import base64
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from selenium.webdriver.common.by import By

from dao.auth_session_dao import AuthSessionDao
from dao.init_db import init_db
from service.login_service.login_service import LoginService
from utils import globals
from utils.webdriver_util import init_webdriver

app = FastAPI(title="Bilibili Lucky Draw Console")
ROOT = Path(__file__).resolve().parent
COOKIE_DIR = Path(os.getenv("COOKIE_DIR", "/app/cookie"))
ACCOUNT_KEY = str(globals.my_user_id or "default")
login_lock = threading.Lock()
login_state = {"driver": None, "chains": None, "started_at": None, "error": None}


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
    if driver is not None:
        try:
            driver.quit()
        except Exception:
            pass


def login_worker():
    try:
        driver, chains = init_webdriver()
        login_state["driver"] = driver
        login_state["chains"] = chains
        driver.get("https://passport.bilibili.com/login")
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
            time.sleep(2)
        set_auth("LOGIN_FAILED", error="二维码登录超时")
    except Exception as exc:
        set_auth("LOGIN_FAILED", error=str(exc))
    finally:
        close_login_driver()


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


@app.post("/api/auth/qrcode/start")
def start_qrcode():
    if login_state["driver"] is not None:
        return {"started": True, "status": "LOGIN_IN_PROGRESS"}
    set_auth("LOGIN_IN_PROGRESS", error=None)
    login_state["started_at"] = datetime.now().isoformat(timespec="seconds")
    login_state["error"] = None
    threading.Thread(target=login_worker, daemon=True).start()
    return {"started": True, "status": "LOGIN_IN_PROGRESS"}


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


@app.get("/api/overview")
def overview():
    db = init_db()
    failure_rows = db.executeSql(
        "SELECT COUNT(*) AS count FROM t_draw_dynamic WHERE status='3'"
    ) or [{"count": 0}]
    return {
        "auth": public_auth(),
        "selenium": "connected",
        "failures": int(failure_rows[0].get("count") or 0),
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
        SELECT dyn_url, dynamic_id, up_id, publish_time, lottery_time, status
        FROM t_draw_dynamic
        WHERE status='3'
        ORDER BY insert_time DESC
        LIMIT 100
    """) or []
    return {"items": rows}


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
def logs():
    log_dir = Path("/app/Log")
    files = sorted(log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    lines = []
    for path in files[:5]:
        try:
            lines.extend(path.read_text(encoding="utf-8", errors="replace").splitlines()[-80:])
        except OSError:
            continue
    return {"items": lines[-300:]}
