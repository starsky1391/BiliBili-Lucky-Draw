import base64
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from selenium.webdriver.common.by import By

from dao.auth_session_dao import AuthSessionDao
from dao.account_dao import AccountDao
from dao.init_db import init_db
from service.login_service.login_service import LoginService
from service.cleanup_service.backfill_account_dynamics import AccountDynamicBackfill
from service.search_draw_dynamic_service.SearchDynamicByUps import SearchDynamicByUps
from service.share_service.multi_users_share import MultiUsersShareService
from service.cleanup_service.expired_share_cleanup import ExpiredShareCleanup
from service.auth_service import AuthenticationRequiredError, is_authenticated
from service.account_service import (
    cookie_path,
    enabled_account_keys,
    ensure_cookie_accounts,
    list_accounts,
)
from utils.runtime_settings import (
    VALID_CLEANUP_MODES,
    get_cleanup_mode,
    get_env_ups,
    get_extra_ups,
    get_merged_ups,
    get_max_checks,
    set_cleanup_mode,
    set_extra_ups,
    set_max_checks,
)
from utils.webdriver_util import init_webdriver

app = FastAPI(title="Bilibili Lucky Draw Console")
ROOT = Path(__file__).resolve().parent
COOKIE_DIR = Path(os.getenv("COOKIE_DIR", "/app/cookie"))
login_lock = threading.Lock()
login_driver_lock = threading.Lock()
login_state = {
    "driver": None,
    "chains": None,
    "started_at": None,
    "error": None,
    "thread": None,
    "account_key": None,
    "username": None,
}
retry_state = {"running": False, "summary": None, "thread": None, "user_ids": []}
manual_task_state = {
    "collect": {"running": False, "summary": None, "error": None, "user_ids": []},
    "backfill_personal": {"running": False, "summary": None, "error": None, "user_ids": []},
    "cleanup": {"running": False, "summary": None, "error": None, "user_ids": []},
    "reidentify_expired": {"running": False, "summary": None, "error": None, "user_ids": []},
    "reidentify_one": {"running": False, "summary": None, "error": None, "user_ids": []},
}
manual_task_lock = threading.Lock()


def auth_dao():
    dao = AuthSessionDao(init_db())
    dao.ensure_table()
    return dao


def set_auth(account_key, status, uid=None, error=None,
             verified=False, cookie_saved=False):
    auth_dao().upsert(
        str(account_key), status, uid=uid, error=error,
        verified=verified, cookie_saved=cookie_saved
    )


def account_dao():
    return AccountDao(init_db())


def public_auth(account_key=None):
    ensure_cookie_accounts()
    if account_key:
        row = auth_dao().get(str(account_key))
        row = row or {"account_key": str(account_key), "status": "UNKNOWN"}
    else:
        rows = list_accounts()
        row = next(
            (auth_dao().get(str(item["account_key"])) for item in rows
             if auth_dao().get(str(item["account_key"]))),
            None,
        )
        row = row or {"account_key": None, "status": "UNKNOWN"}
    return {
        "account_key": row.get("account_key"),
        "status": row.get("status"),
        "uid": row.get("uid"),
        "last_verified_at": row.get("last_verified_at"),
        "cookie_saved_at": row.get("cookie_saved_at"),
        "last_error": row.get("last_error"),
        "login_started_at": login_state["started_at"],
        "login_account_key": login_state.get("account_key"),
    }


def user_public(row):
    account_key = str(row["account_key"])
    auth = auth_dao().get(account_key) or {}
    path = cookie_path(account_key)
    return {
        "uid": account_key,
        "username": row.get("username"),
        "remark": row.get("remark") or row.get("username") or account_key,
        "display_name": (
            account_key + "  " + (row.get("remark") or row.get("username"))
            if row.get("remark") or row.get("username") else account_key
        ),
        "enabled": bool(row.get("enabled")),
        "cookie_saved": path.is_file(),
        "status": auth.get("status", "UNKNOWN"),
        "last_verified_at": auth.get("last_verified_at"),
        "last_error": auth.get("last_error"),
    }


def find_qr_element(driver):
    selectors = [
        "img[src*='qrcode']",
        "img[src*='qr']",
        "[class*='qrcode'] img",
        "[class*='qr-code'] img",
        "canvas",
    ]
    for selector in selectors:
        elements = driver.find_elements(By.CSS_SELECTOR, selector)
        for element in elements:
            if element.is_displayed():
                return element
    return None


def find_login_uid(driver):
    cookies = {item["name"]: item["value"] for item in driver.get_cookies()}
    uid = cookies.get("DedeUserID")
    if uid:
        return str(uid)
    return None


def save_cookies(driver, account_key):
    COOKIE_DIR.mkdir(parents=True, exist_ok=True)
    target = COOKIE_DIR / (str(account_key) + ".json")
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(driver.get_cookies(), ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)
    return target


def close_login_driver():
    driver = login_state.get("driver")
    login_state["driver"] = None
    login_state["chains"] = None
    login_state["thread"] = None
    if driver is not None:
        try:
            with login_driver_lock:
                driver.quit()
        except Exception:
            pass


def vnc_url():
    driver = login_state.get("driver")
    session_id = getattr(driver, "session_id", None) if driver else None
    if not session_id:
        return None
    return "http://127.0.0.1:17900/vnc.html?autoconnect=true&host=127.0.0.1&port=5555&path=session/%s/se/vnc&resize=scale" % session_id


def get_login_username(driver):
    try:
        result = driver.execute_async_script("""
const done = arguments[0];
fetch('https://api.bilibili.com/x/web-interface/nav', {credentials: 'include'})
  .then(response => response.json())
  .then(data => done(data.data || {}))
  .catch(() => done({}));
""")
        return result.get("uname") if isinstance(result, dict) else None
    except Exception:
        return None


def login_worker(driver, chains):
    try:
        deadline = time.time() + 300
        while time.time() < deadline:
            with login_driver_lock:
                uid = find_login_uid(driver)
                cookies = driver.get_cookies()
                if uid and any(item["name"] == "SESSDATA" for item in cookies):
                    expected_uid = login_state.get("account_key")
                    if expected_uid and str(expected_uid) != uid:
                        raise RuntimeError("扫码账号 UID 与要重新登录的用户不一致")
                    driver.get("https://t.bilibili.com/")
                    username = get_login_username(driver)
                    save_cookies(driver, uid)
                    driver.get("https://t.bilibili.com/")
                    if not LoginService(driver, chains, uid).wait_logged_in(timeout=10):
                        raise RuntimeError("扫码完成，但 B 站登录状态验证失败")
                    current = account_dao().get(uid)
                    account_dao().ensure(
                        uid, bili_uid=uid, username=username,
                        config_file=uid + ".json",
                        remark=current.get("remark") if current else None,
                    )
                    set_auth(uid, "AUTHENTICATED", uid=uid,
                             verified=True, cookie_saved=True)
                    login_state["account_key"] = uid
                    login_state["username"] = username
                    return
                # Selenium removes idle sessions after SE_NODE_SESSION_TIMEOUT.
                driver.execute_script("return document.readyState")
            time.sleep(2)
        if login_state.get("account_key"):
            set_auth(login_state["account_key"], "LOGIN_FAILED", error="二维码登录超时")
    except Exception as exc:
        account_key = login_state.get("account_key")
        if account_key:
            set_auth(account_key, "LOGIN_FAILED", error=str(exc))
        login_state["error"] = str(exc)
    finally:
        close_login_driver()


def start_login_session(account_key=None):
    with login_lock:
        current = login_state.get("driver")
        if current is not None and getattr(current, "session_id", None):
            raise RuntimeError("已有扫码登录正在进行，请完成或关闭当前流程")
        close_login_driver()
        driver, chains = init_webdriver()
        driver.get("https://passport.bilibili.com/login")
        if not getattr(driver, "session_id", None):
            driver.quit()
            raise RuntimeError("Selenium 登录会话创建失败")
        login_state["driver"] = driver
        login_state["chains"] = chains
        login_state["started_at"] = datetime.now().isoformat(timespec="seconds")
        login_state["error"] = None
        login_state["account_key"] = str(account_key) if account_key else None
        login_state["username"] = None
        worker = threading.Thread(
            target=login_worker,
            args=(driver, chains),
            daemon=True,
        )
        login_state["thread"] = worker
        worker.start()
        return driver


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/assets/{name}")
def assets(name: str):
    path = ROOT / "static" / name
    if not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path)


@app.get("/api/auth/status")
def auth_status():
    active_uid = login_state.get("account_key")
    if login_state.get("driver") is not None:
        return {
            "account_key": active_uid,
            "status": "LOGIN_IN_PROGRESS",
            "uid": active_uid,
            "username": login_state.get("username"),
            "error": login_state.get("error"),
        }
    if login_state.get("error"):
        return {
            "account_key": active_uid,
            "status": "LOGIN_FAILED",
            "uid": active_uid,
            "username": login_state.get("username"),
            "error": login_state.get("error"),
        }
    if active_uid:
        row = auth_dao().get(active_uid) or {}
        return {
            "account_key": active_uid,
            "status": row.get("status", "UNKNOWN"),
            "uid": active_uid,
            "username": login_state.get("username"),
            "error": row.get("last_error"),
        }
    return public_auth()


@app.get("/api/users")
def users():
    return {"items": [user_public(row) for row in list_accounts()]}


@app.patch("/api/users/{uid}")
def update_user(uid: str, payload: dict):
    row = account_dao().get(uid)
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")
    if "remark" not in payload:
        raise HTTPException(status_code=400, detail="缺少 remark")
    remark = str(payload.get("remark") or "").strip()
    if not remark:
        remark = row.get("username") or uid
    return user_public(account_dao().update_profile(uid, remark=remark))


@app.post("/api/users/{uid}/enable")
def enable_user(uid: str):
    if not account_dao().get(uid):
        raise HTTPException(status_code=404, detail="用户不存在")
    return user_public(account_dao().set_enabled(uid, True))


@app.post("/api/users/{uid}/disable")
def disable_user(uid: str):
    if not account_dao().get(uid):
        raise HTTPException(status_code=404, detail="用户不存在")
    return user_public(account_dao().set_enabled(uid, False))


@app.post("/api/users/{uid}/verify")
def verify_user(uid: str):
    if not account_dao().get(uid):
        raise HTTPException(status_code=404, detail="用户不存在")
    driver = None
    try:
        driver, chains = init_webdriver()
        LoginService(driver, chains, uid).login_by_cookie()
        return user_public(account_dao().get(uid))
    except AuthenticationRequiredError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass


@app.get("/api/settings/cleanup")
def cleanup_settings():
    mode = get_cleanup_mode()
    return {
        "mode": mode,
        "options": [
            {"value": "disabled", "label": "关闭过期清理"},
            {"value": "delete_only", "label": "只删除本人过期动态"},
            {"value": "delete_and_unfollow", "label": "删除动态并取关相关 UP"},
        ],
    }


@app.post("/api/settings/cleanup")
def update_cleanup_settings(payload: dict):
    mode = payload.get("mode")
    if mode not in VALID_CLEANUP_MODES:
        raise HTTPException(status_code=400, detail="无效的清理模式")
    return {"mode": set_cleanup_mode(mode)}


@app.get("/api/settings/max-checks")
def max_checks_settings():
    return {"max_checks": get_max_checks()}


@app.post("/api/settings/max-checks")
def update_max_checks_settings(payload: dict):
    try:
        value = set_max_checks(payload.get("max_checks"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"max_checks": value}


@app.get("/api/settings/ups")
def ups_settings():
    return {
        "env_ups": get_env_ups(),
        "extra_ups": get_extra_ups(),
        "merged_ups": get_merged_ups(),
    }


@app.post("/api/settings/ups")
def update_ups_settings(payload: dict):
    extra_ups = set_extra_ups(payload.get("extra_ups", ""))
    return {
        "env_ups": get_env_ups(),
        "extra_ups": extra_ups,
        "merged_ups": get_merged_ups(),
    }


@app.post("/api/auth/qrcode/start")
def start_qrcode():
    try:
        driver = start_login_session()
        return {
            "started": True,
            "status": "LOGIN_IN_PROGRESS",
            "session_id": driver.session_id,
            "vnc_url": vnc_url(),
        }
    except Exception as exc:
        login_state["error"] = str(exc)
        close_login_driver()
        raise HTTPException(status_code=503, detail="登录会话创建失败")


@app.post("/api/users/login/start")
def start_user_login(payload: dict = None):
    try:
        target_uid = (payload or {}).get("uid")
        if target_uid and not account_dao().get(str(target_uid)):
            raise HTTPException(status_code=404, detail="用户不存在")
        if target_uid:
            set_auth(str(target_uid), "LOGIN_IN_PROGRESS", error=None)
        driver = start_login_session(target_uid)
        return {
            "started": True,
            "status": "LOGIN_IN_PROGRESS",
            "session_id": driver.session_id,
            "vnc_url": vnc_url(),
        }
    except HTTPException:
        raise
    except Exception:
        close_login_driver()
        raise HTTPException(status_code=503, detail="登录会话创建失败")


@app.get("/api/auth/qrcode/image")
def qrcode_image():
    driver = login_state.get("driver")
    if driver is None:
        return {"ready": False}
    with login_driver_lock:
        element = find_qr_element(driver)
        if element is None:
            return {"ready": False}
        png = element.screenshot_as_png
    return {"ready": True, "image": "data:image/png;base64," + base64.b64encode(png).decode()}


@app.get("/api/auth/vnc")
def auth_vnc():
    return {"ready": vnc_url() is not None, "url": vnc_url()}


def retry_failed_worker(user_ids):
    summaries = []
    try:
        for user in user_ids:
            try:
                summaries.append({
                    "account": user,
                    "status": "completed",
                    "summary": AccountDynamicBackfill(user).retry_failed_dynamics(),
                })
            except AuthenticationRequiredError as exc:
                summaries.append({"account": user, "status": "paused", "error": str(exc)})
            except Exception as exc:
                summaries.append({"account": user, "status": "failed", "error": str(exc)})
        retry_state["summary"] = summaries
    finally:
        retry_state["running"] = False


@app.get("/api/dynamics/retry-status")
def retry_status():
    return {
        "running": retry_state["running"],
        "summary": retry_state["summary"],
    }


@app.post("/api/dynamics/retry-failures")
def retry_failures(payload: dict = None):
    with manual_task_lock:
        if retry_state["running"] or any(item["running"] for item in manual_task_state.values()):
            return {"started": False, "running": True, "reason": "已有任务正在使用浏览器"}
        available = {
            str(row["account_key"])
            for row in list_accounts(enabled=True)
        }
        user_ids = [
            str(value) for value in ((payload or {}).get("user_ids") or [])
            if str(value) in available
        ]
        if not user_ids:
            return {"started": False, "running": False, "reason": "请至少选择一个已启用用户"}
        retry_state["running"] = True
        retry_state["summary"] = None
        retry_state["user_ids"] = user_ids
        worker = threading.Thread(
            target=retry_failed_worker, args=(user_ids,), daemon=True
        )
        retry_state["thread"] = worker
        worker.start()
    return {"started": True, "running": True}


def run_manual_collect(user_ids):
    summaries = []
    try:
        searched = False
        for search_user in user_ids:
            if not cookie_path(search_user).is_file():
                continue
            try:
                SearchDynamicByUps(search_user).init_search()
                searched = True
                break
            except AuthenticationRequiredError as exc:
                summaries.append({"account": search_user, "status": "paused", "error": str(exc)})
            except Exception as exc:
                summaries.append({"account": search_user, "status": "failed", "error": str(exc)})
        if not searched and not summaries:
            summaries.append({"status": "paused", "error": "没有所选用户的 Cookie 文件"})
        summaries.extend(MultiUsersShareService().do_multi_uses_share(user_ids))
        manual_task_state["collect"]["summary"] = summaries
        manual_task_state["collect"]["error"] = None
    except Exception as exc:
        manual_task_state["collect"]["summary"] = summaries
        manual_task_state["collect"]["error"] = str(exc)
    finally:
        manual_task_state["collect"]["running"] = False


def run_manual_backfill_personal(user_ids):
    summaries = []
    try:
        for user in user_ids:
            try:
                summaries.append({"account": user, "status": "completed",
                                  "summary": AccountDynamicBackfill(user).run()})
            except AuthenticationRequiredError as exc:
                summaries.append({"account": user, "status": "paused", "error": str(exc)})
            except Exception as exc:
                summaries.append({"account": user, "status": "failed", "error": str(exc)})
        manual_task_state["backfill_personal"]["summary"] = summaries
        manual_task_state["backfill_personal"]["error"] = None
    finally:
        manual_task_state["backfill_personal"]["running"] = False


def run_manual_cleanup(user_ids):
    summaries = []
    try:
        for user in user_ids:
            try:
                sync_error = None
                try:
                    sync_summary = AccountDynamicBackfill(user).sync_new_personal_forwards()
                except AuthenticationRequiredError:
                    raise
                except Exception as exc:
                    sync_summary = None
                    sync_error = str(exc)
                    print("用户 %s 个人动态增量同步失败，继续清理已有关联记录：%s"
                          % (user, exc), flush=True)
                cleanup_summary = ExpiredShareCleanup(user).run()
                summaries.append({"account": user, "status": "completed",
                                  "sync": sync_summary, "sync_error": sync_error,
                                  "cleanup": cleanup_summary})
            except AuthenticationRequiredError as exc:
                summaries.append({"account": user, "status": "paused", "error": str(exc)})
            except Exception as exc:
                summaries.append({"account": user, "status": "failed", "error": str(exc)})
        manual_task_state["cleanup"]["summary"] = summaries
        manual_task_state["cleanup"]["error"] = None
    finally:
        manual_task_state["cleanup"]["running"] = False


def run_reidentify_expired(user_ids):
    summaries = []
    try:
        for user in user_ids:
            try:
                summaries.append({"account": user, "status": "completed",
                                  "summary": AccountDynamicBackfill(user).reidentify_expired_dynamics(100)})
            except AuthenticationRequiredError as exc:
                summaries.append({"account": user, "status": "paused", "error": str(exc)})
            except Exception as exc:
                summaries.append({"account": user, "status": "failed", "error": str(exc)})
        manual_task_state["reidentify_expired"]["summary"] = summaries
        manual_task_state["reidentify_expired"]["error"] = None
    finally:
        manual_task_state["reidentify_expired"]["running"] = False


def run_reidentify_one(dynamic_id, user_id):
    try:
        db = init_db()
        value = dynamic_id.replace("'", "''")
        rows = db.executeSql(
            "SELECT dynamic_id, dyn_url FROM t_draw_dynamic "
            "WHERE dynamic_id='%s' AND status='2' LIMIT 1" % value
        ) or []
        if not rows:
            raise RuntimeError("动态不存在或当前不是过期状态")
        manual_task_state["reidentify_one"]["summary"] = {
            "account": user_id,
            "status": "completed",
            "summary": AccountDynamicBackfill(user_id).reidentify_single_expired_dynamic(
                dynamic_id, rows[0].get("dyn_url")
            ),
        }
        manual_task_state["reidentify_one"]["error"] = None
    except AuthenticationRequiredError as exc:
        manual_task_state["reidentify_one"]["summary"] = {
            "account": user_id, "status": "paused", "error": str(exc)
        }
        manual_task_state["reidentify_one"]["error"] = None
    except Exception as exc:
        manual_task_state["reidentify_one"]["summary"] = None
        manual_task_state["reidentify_one"]["error"] = str(exc)
    finally:
        manual_task_state["reidentify_one"]["running"] = False


@app.get("/api/tasks/manual-status")
def manual_task_status():
    return manual_task_state


@app.post("/api/tasks/manual/{task_name}")
def start_manual_task(task_name: str, payload: dict = None):
    if task_name not in ("collect", "backfill_personal", "cleanup", "reidentify_expired"):
        raise HTTPException(status_code=404, detail="unknown task")
    state = manual_task_state[task_name]
    with manual_task_lock:
        if retry_state["running"] or any(item["running"] for item in manual_task_state.values()):
            return {"started": False, "running": True, "reason": "已有任务正在使用浏览器"}
        if state["running"]:
            return {"started": False, "running": True}
        requested = (payload or {}).get("user_ids") or []
        available = {
            str(row["account_key"]): row
            for row in list_accounts(enabled=True)
        }
        user_ids = [str(value) for value in requested if str(value) in available]
        if not user_ids:
            return {"started": False, "running": False, "reason": "请至少选择一个已启用用户"}
        state["running"] = True
        state["summary"] = None
        state["error"] = None
        state["user_ids"] = user_ids
        target = {
            "collect": lambda: run_manual_collect(user_ids),
            "backfill_personal": lambda: run_manual_backfill_personal(user_ids),
            "cleanup": lambda: run_manual_cleanup(user_ids),
            "reidentify_expired": lambda: run_reidentify_expired(user_ids),
        }[task_name]
        threading.Thread(target=target, daemon=True).start()
    return {"started": True, "running": True}


@app.post("/api/dynamics/{dynamic_id}/reidentify")
def reidentify_dynamic(dynamic_id: str, payload: dict = None):
    with manual_task_lock:
        if any(item["running"] for item in manual_task_state.values()):
            return {"started": False, "running": True, "reason": "已有任务正在使用浏览器"}
        state = manual_task_state["reidentify_one"]
        state["running"] = True
        state["summary"] = None
        state["error"] = None
        user_id = str((payload or {}).get("user_id") or "")
        if not user_id:
            user_id = next(iter(enabled_account_keys()), "")
        if not user_id:
            state["running"] = False
            return {"started": False, "running": False, "reason": "没有可用用户"}
        state["user_ids"] = [user_id]
        threading.Thread(
            target=run_reidentify_one,
            args=(dynamic_id, user_id),
            daemon=True,
        ).start()
    return {"started": True, "running": True, "dynamic_id": dynamic_id}


@app.post("/api/auth/refresh")
def refresh_auth_page():
    driver = login_state.get("driver")
    if driver is None:
        try:
            driver = start_login_session()
        except Exception:
            raise HTTPException(status_code=503, detail="登录会话创建失败，请稍后重试")
    with login_driver_lock:
        driver.refresh()
    return {"refreshed": True, "vnc_url": vnc_url()}


@app.get("/api/users/login/status")
def user_login_status():
    return auth_status()


@app.get("/api/overview")
def overview():
    db = init_db()
    accounts = list_accounts(enabled=True)
    account_keys = [str(row["account_key"]) for row in accounts]
    account_filter = ",".join(
        "'%s'" % value.replace("'", "''") for value in account_keys
    ) or "''"
    auth_rows = [auth_dao().get(key) for key in account_keys]
    authenticated = any(
        row and row.get("status") == "AUTHENTICATED" for row in auth_rows
    )
    has_accounts = bool(account_keys)
    draw_rows = db.executeSql(
        "SELECT ad.account_key, "
        "COUNT(DISTINCT CASE WHEN (CASE WHEN dd.lottery_source='explicit' "
        "THEN DATE_ADD(dd.lottery_time, INTERVAL 10 DAY) "
        "ELSE dd.lottery_time END) > NOW() "
        "THEN ad.dynamic_id END) AS pending_draws, "
        "COUNT(DISTINCT CASE WHEN (CASE WHEN dd.lottery_source='explicit' "
        "THEN DATE_ADD(dd.lottery_time, INTERVAL 10 DAY) "
        "ELSE dd.lottery_time END) <= NOW() "
        "THEN ad.dynamic_id END) AS completed_draws "
        "FROM t_account_dynamic ad "
        "JOIN t_draw_dynamic dd ON dd.dynamic_id = ad.dynamic_id "
        "WHERE ad.account_key IN (%s) "
        "AND ad.share_status=1 "
        "AND ad.cleanup_status<>1 "
        "AND dd.lottery_time IS NOT NULL "
        "GROUP BY ad.account_key" % account_filter
    ) or []
    draw_by_account = {
        str(row["account_key"]): row for row in draw_rows
    }
    user_draw_stats = []
    for account in accounts:
        account_key = str(account["account_key"])
        row = draw_by_account.get(account_key, {})
        remark = account.get("remark") or account.get("username")
        user_draw_stats.append({
            "uid": account_key,
            "remark": remark if remark and remark != account_key else None,
            "pending_draws": int(row.get("pending_draws") or 0),
            "completed_draws": int(row.get("completed_draws") or 0),
        })
    aggregate_auth = {
        "account_key": None,
        "status": "AUTHENTICATED" if authenticated else (
            "UNKNOWN" if not has_accounts else "LOGIN_REQUIRED"
        ),
        "last_error": next(
            (row.get("last_error") for row in auth_rows
             if row and row.get("last_error")), None
        ),
    }
    return {
        "auth": aggregate_auth,
        "user_count": len(account_keys),
        "selenium": "connected",
        "cleanup_mode": get_cleanup_mode(),
        "user_draw_stats": user_draw_stats,
        "tasks": {
            "collect": (
                "scheduled" if authenticated
                else "pending_verification" if not has_accounts
                else "paused"
            ),
            "cleanup": (
                "scheduled" if authenticated
                else "pending_verification" if not has_accounts
                else "paused"
            ),
        },
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }


@app.get("/api/dynamics/failures")
def failures():
    db = init_db()
    rows = db.executeSql("""
        SELECT dyn_url, dynamic_id, up_id, insert_time, publish_time,
               lottery_time, lottery_source, status
        FROM t_draw_dynamic
        WHERE status='3'
        ORDER BY insert_time ASC, dynamic_id ASC
        LIMIT 100
    """) or []
    return {"items": rows}


@app.get("/api/dynamics/expired")
def expired_dynamics():
    db = init_db()
    rows = db.executeSql("""
        SELECT dyn_url, dynamic_id, up_id, insert_time, publish_time,
               lottery_time, lottery_source, status
        FROM t_draw_dynamic
        WHERE status='2'
        ORDER BY insert_time ASC, dynamic_id ASC
        LIMIT 100
    """) or []
    count_rows = db.executeSql(
        "SELECT COUNT(*) AS count FROM t_draw_dynamic WHERE status='2'"
    ) or [{"count": 0}]
    return {"items": rows, "count": int(count_rows[0].get("count") or 0)}


@app.post("/api/dynamics/{dynamic_id}/mark-missing")
def mark_dynamic_missing(dynamic_id: str):
    db = init_db()
    value = dynamic_id.replace("'", "''")
    rows = db.executeSql(
        "SELECT dynamic_id FROM t_draw_dynamic "
        "WHERE dynamic_id='%s' AND status='3' LIMIT 1" % value
    ) or []
    if not rows:
        raise HTTPException(status_code=404, detail="dynamic not found")
    db.executeCommit(
        "UPDATE t_draw_dynamic SET status='4', note='人工标记页面丢失' "
        "WHERE dynamic_id='%s'" % value
    )
    return {"marked": True, "dynamic_id": dynamic_id, "status": "4"}


@app.post("/api/dynamics/mark-all-missing")
def mark_all_dynamics_missing():
    db = init_db()
    rows = db.executeSql(
        "SELECT COUNT(*) AS count FROM t_draw_dynamic WHERE status='3'"
    ) or [{"count": 0}]
    count = int(rows[0].get("count") or 0)
    if count:
        db.executeCommit(
            "UPDATE t_draw_dynamic SET status='4', note='人工批量标记页面丢失' "
            "WHERE status='3'"
        )
    return {"marked": count, "status": "4"}


@app.get("/api/dynamics/{dynamic_id}")
def dynamic_detail(dynamic_id: str):
    db = init_db()
    value = dynamic_id.replace("'", "''")
    rows = db.executeSql(
        "SELECT * FROM t_draw_dynamic WHERE dynamic_id='%s' LIMIT 1" % value
    ) or []
    if not rows:
        raise HTTPException(status_code=404, detail="dynamic not found")
    return rows[0]


@app.get("/api/logs")
def logs(
    limit: int = Query(200, ge=100, le=500),
    errors_only: bool = Query(False),
):
    log_dir = Path("/app/Log")
    files = sorted(log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return {"items": [], "file": None, "limit": limit, "errors_only": errors_only}
    path = files[0]
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    if errors_only:
        lines = [
            line for line in lines
            if " - ERROR - " in line or " - CRITICAL - " in line
        ]
    return {
        "items": lines[-limit:],
        "file": path.name,
        "limit": limit,
        "errors_only": errors_only,
    }
