from dao.auth_session_dao import AuthSessionDao
from dao.init_db import init_db
from utils import globals


def get_auth_dao():
    db = init_db()
    dao = AuthSessionDao(db)
    dao.ensure_table()
    return dao


def account_key():
    return str(globals.my_user_id or "default")


def is_authenticated():
    try:
        dao = get_auth_dao()
        row = dao.get(account_key())
        if row and row.get("status") == "AUTHENTICATED":
            return True
        return not row and bool(globals.cookie_value)
    except Exception:
        return False


def require_authenticated():
    if not is_authenticated():
        raise RuntimeError("Bilibili login is required; task paused")


def mark_authenticated(uid=None):
    get_auth_dao().upsert(account_key(), "AUTHENTICATED", uid=uid, verified=True)


def mark_login_required(error=None):
    get_auth_dao().upsert(account_key(), "LOGIN_REQUIRED", error=error)
