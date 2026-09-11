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
from dao.init_db import init_db
from service.login_service.login_service import LoginService
from service.cleanup_service.backfill_account_dynamics import AccountDynamicBackfill
from service.search_draw_dynamic_service.SearchDynamicByUps import SearchDynamicByUps
from service.share_service.multi_users_share import MultiUsersShareService
from service.cleanup_service.expired_share_cleanup import ExpiredShareCleanup
from service.auth_service import require_authenticated
from utils import globals
from utils.runtime_settings import (
    VALID_CLEANUP_MODES,
    get_cleanup_mode,
    get_max_checks,
    set_cleanup_mode,
    set_max_checks,
)
from utils.webdriver_util import init_webdriver

app = FastAPI(title="Bilibili Lucky Draw Console")
ROOT = Path(__file__).resolve().parent
COOKIE_DIR = Path(os.getenv("COOKIE_DIR", "/app/cookie"))
ACCOUNT_KEY = str(globals.my_user_id or "default")
login_lock = threading.Lock()
login_state = {
    "driver": None,
    "chains": None,
    "started_at": None,
    "error": None,
    "thread": None,
}
retry_state = {"running": False, "summary": None, "thread": None}
manual_task_state = {
    "collect": {"running": False, "summary": None, "error": None},
    "cleanup": {"running": False, "summary": None, "error": None},
    "reidentify_expired": {"running": False, "summary": None, "error": None},
    "reidentify_one": {"running": False, "summary": None, "error": None},
}
manual_task_lock = threading.Lock()


def auth_dao():
    dao = AuthSessionDao(init_db())
    dao.ensure_table()
    return dao


def set_auth(status, uid=None, error=None, verified=False, cookie_saved=False):
    auth_dao().upsert(
        ACCOUNT_KEY, status, uid=uid, error=error,
        verified=verified, cookie_saved=cookie_saved
    )


def public_auth():
    row = auth_dao().get(ACCOUNT_KEY)
    if row is None and globals.cookie_value:
        row = {
            "account_key": ACCOUNT_KEY,
            "status": "UNKNOWN",
            "last_error": "已配置旧版 Cookie，等待任务首次验证",
        }
    row = row or {"account_key": ACCOUNT_KEY, "status": "UNKNOWN"}
    return {
        "account_key": row.get("account_key"),
        "status": row.get("status"),
        "uid": row.get("uid"),
        "last_verified_at": row.get("last_verified_at"),
        "cookie_saved_at": row.get("cookie_saved_at"),
        "last_error": row.get("last_error"),
        "login_started_at": login_state["started_at"],
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
        if elements:
            return elements[0]
    return None


def find_login_uid(driver):
    cookies = {item["name"]: item["value"] for item in driver.get_cookies()}
    uid = cookies.get("DedeUserID")
    if uid:
        return str(uid)
    return None


def save_cookies(driver):
    COOKIE_DIR.mkdir(parents=True, exist_ok=True)
    target = COOKIE_DIR / (ACCOUNT_KEY + ".json")
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
            driver.quit()
        except Exception:
            pass


def vnc_url():
    driver = login_state.get("driver")
    session_id = getattr(driver, "session_id", None) if driver else None
    if not session_id:
        return None
    return "http://127.0.0.1:7900/vnc.html?autoconnect=true&host=127.0.0.1&port=5555&path=session/%s/se/vnc&resize=scale" % session_id


def login_worker(driver, chains):
    try:
        deadline = time.time() + 300
        while time.time() < deadline:
            uid = find_login_uid(driver)
            if uid and any(item["name"] == "SESSDATA" for item in driver.get_cookies()):
                save_cookies(driver)
                driver.get("https://t.bilibili.com/")
                if not LoginService(driver, chains, ACCOUNT_KEY).wait_logged_in(timeout=10):
                    raise RuntimeError("扫码完成，但 B 站登录状态验证失败")
                set_auth("AUTHENTICATED", uid=uid, verified=True, cookie_saved=True)
                return
            # Selenium removes idle sessions after SE_NODE_SESSION_TIMEOUT.
            driver.execute_script("return document.readyState")
            time.sleep(2)
        set_auth("LOGIN_FAILED", error="二维码登录超时")
    except Exception as exc:
        set_auth("LOGIN_FAILED", error=str(exc))
    finally:
        close_login_driver()


def start_login_session():
    with login_lock:
        current = login_state.get("driver")
        if current is not None and getattr(current, "session_id", None):
            return current
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
    return public_auth()


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


@app.post("/api/auth/qrcode/start")
def start_qrcode():
    try:
        set_auth("LOGIN_IN_PROGRESS", error=None)
        driver = start_login_session()
        return {
            "started": True,
            "status": "LOGIN_IN_PROGRESS",
            "session_id": driver.session_id,
            "vnc_url": vnc_url(),
        }
    except Exception as exc:
        login_state["error"] = str(exc)
        set_auth("LOGIN_FAILED", error=str(exc))
        close_login_driver()
        raise HTTPException(status_code=503, detail="登录会话创建失败")


@app.get("/api/auth/qrcode/image")
def qrcode_image():
    driver = login_state.get("driver")
    if driver is None:
        return {"ready": False}
    element = find_qr_element(driver)
    if element is None:
        return {"ready": False}
    png = element.screenshot_as_png
    return {"ready": True, "image": "data:image/png;base64," + base64.b64encode(png).decode()}


@app.get("/api/auth/vnc")
def auth_vnc():
    return {"ready": vnc_url() is not None, "url": vnc_url()}


def retry_failed_worker():
    try:
        retry_state["summary"] = AccountDynamicBackfill(ACCOUNT_KEY).retry_failed_dynamics()
    except Exception as exc:
        retry_state["summary"] = {
            "checked": 0,
            "updated": 0,
            "failed": 0,
            "error": str(exc),
        }
    finally:
        retry_state["running"] = False


@app.get("/api/dynamics/retry-status")
def retry_status():
    return {
        "running": retry_state["running"],
        "summary": retry_state["summary"],
    }


@app.post("/api/dynamics/retry-failures")
def retry_failures():
    with manual_task_lock:
        if retry_state["running"] or any(item["running"] for item in manual_task_state.values()):
            return {"started": False, "running": True, "reason": "已有任务正在使用浏览器"}
        retry_state["running"] = True
        retry_state["summary"] = None
        worker = threading.Thread(target=retry_failed_worker, daemon=True)
        retry_state["thread"] = worker
        worker.start()
    return {"started": True, "running": True}


def run_manual_collect():
    try:
        require_authenticated()
        SearchDynamicByUps(ACCOUNT_KEY).init_search()
        MultiUsersShareService().do_multi_uses_share()
        manual_task_state["collect"]["summary"] = "收集任务执行完成"
        manual_task_state["collect"]["error"] = None
    except Exception as exc:
        manual_task_state["collect"]["summary"] = None
        manual_task_state["collect"]["error"] = str(exc)
    finally:
        manual_task_state["collect"]["running"] = False


def run_manual_cleanup():
    try:
        require_authenticated()
        summaries = []
        for user in MultiUsersShareService().get_multi_uses():
            sync_summary = AccountDynamicBackfill(user).sync_new_personal_forwards()
            ExpiredShareCleanup(user).run()
            summaries.append({"account": user, "sync": sync_summary})
        manual_task_state["cleanup"]["summary"] = summaries
        manual_task_state["cleanup"]["error"] = None
    except Exception as exc:
        manual_task_state["cleanup"]["summary"] = None
        manual_task_state["cleanup"]["error"] = str(exc)
    finally:
        manual_task_state["cleanup"]["running"] = False


def run_reidentify_expired():
    try:
        require_authenticated()
        summary = AccountDynamicBackfill(ACCOUNT_KEY).reidentify_expired_dynamics(100)
        manual_task_state["reidentify_expired"]["summary"] = summary
        manual_task_state["reidentify_expired"]["error"] = None
    except Exception as exc:
        manual_task_state["reidentify_expired"]["summary"] = None
        manual_task_state["reidentify_expired"]["error"] = str(exc)
    finally:
        manual_task_state["reidentify_expired"]["running"] = False


def run_reidentify_one(dynamic_id):
    try:
        require_authenticated()
        db = init_db()
        value = dynamic_id.replace("'", "''")
        rows = db.executeSql(
            "SELECT dynamic_id, dyn_url FROM t_draw_dynamic "
            "WHERE dynamic_id='%s' AND status='2' LIMIT 1" % value
        ) or []
        if not rows:
            raise RuntimeError("动态不存在或当前不是过期状态")
        summary = AccountDynamicBackfill(ACCOUNT_KEY).reidentify_single_expired_dynamic(
            dynamic_id, rows[0].get("dyn_url")
        )
        manual_task_state["reidentify_one"]["summary"] = summary
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
def start_manual_task(task_name: str):
    if task_name not in ("collect", "cleanup", "reidentify_expired"):
        raise HTTPException(status_code=404, detail="unknown task")
    state = manual_task_state[task_name]
    with manual_task_lock:
        if retry_state["running"] or any(item["running"] for item in manual_task_state.values()):
            return {"started": False, "running": True, "reason": "已有任务正在使用浏览器"}
        if state["running"]:
            return {"started": False, "running": True}
        state["running"] = True
        state["summary"] = None
        state["error"] = None
        target = {
            "collect": run_manual_collect,
            "cleanup": run_manual_cleanup,
            "reidentify_expired": run_reidentify_expired,
        }[task_name]
        threading.Thread(target=target, daemon=True).start()
    return {"started": True, "running": True}


@app.post("/api/dynamics/{dynamic_id}/reidentify")
def reidentify_dynamic(dynamic_id: str):
    with manual_task_lock:
        if any(item["running"] for item in manual_task_state.values()):
            return {"started": False, "running": True, "reason": "已有任务正在使用浏览器"}
        state = manual_task_state["reidentify_one"]
        state["running"] = True
        state["summary"] = None
        state["error"] = None
        threading.Thread(
            target=run_reidentify_one,
            args=(dynamic_id,),
            daemon=True,
        ).start()
    return {"started": True, "running": True, "dynamic_id": dynamic_id}


@app.post("/api/auth/refresh")
def refresh_auth_page():
    driver = login_state.get("driver")
    if driver is None:
        try:
            set_auth("LOGIN_IN_PROGRESS", error=None)
            driver = start_login_session()
        except Exception:
            raise HTTPException(status_code=503, detail="登录会话创建失败，请稍后重试")
    driver.refresh()
    return {"refreshed": True, "vnc_url": vnc_url()}


@app.get("/api/overview")
def overview():
    db = init_db()
    failure_rows = db.executeSql(
        "SELECT COUNT(*) AS count FROM t_draw_dynamic WHERE status='3'"
    ) or [{"count": 0}]
    pending_rows = db.executeSql(
        "SELECT COUNT(DISTINCT ad.dynamic_id) AS count "
        "FROM t_account_dynamic ad "
        "JOIN t_draw_dynamic dd ON dd.dynamic_id = ad.dynamic_id "
        "WHERE ad.account_key=%s "
        "AND ad.share_status=1 "
        "AND ad.cleanup_status<>1 "
        "AND dd.lottery_time IS NOT NULL "
        "AND (CASE WHEN dd.lottery_source='explicit' "
        "THEN DATE_ADD(dd.lottery_time, INTERVAL 10 DAY) "
        "ELSE dd.lottery_time END) > NOW()" % repr(ACCOUNT_KEY)
    ) or [{"count": 0}]
    expired_rows = db.executeSql(
        "SELECT COUNT(DISTINCT ad.dynamic_id) AS count "
        "FROM t_account_dynamic ad "
        "JOIN t_draw_dynamic dd ON dd.dynamic_id = ad.dynamic_id "
        "WHERE ad.account_key=%s "
        "AND ad.share_status=1 "
        "AND ad.cleanup_status<>1 "
        "AND dd.lottery_time IS NOT NULL "
        "AND (CASE WHEN dd.lottery_source='explicit' "
        "THEN DATE_ADD(dd.lottery_time, INTERVAL 10 DAY) "
        "ELSE dd.lottery_time END) <= NOW()" % repr(ACCOUNT_KEY)
    ) or [{"count": 0}]
    return {
        "auth": public_auth(),
        "selenium": "connected",
        "failures": int(failure_rows[0].get("count") or 0),
        "cleanup_mode": get_cleanup_mode(),
        "pending_draws": int(pending_rows[0].get("count") or 0),
        "expired_draws": int(expired_rows[0].get("count") or 0),
        "tasks": {
            "collect": (
                "scheduled" if public_auth()["status"] == "AUTHENTICATED"
                else "pending_verification" if public_auth()["status"] == "UNKNOWN"
                else "paused"
            ),
            "cleanup": (
                "scheduled" if public_auth()["status"] == "AUTHENTICATED"
                else "pending_verification" if public_auth()["status"] == "UNKNOWN"
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
