from dao.auth_session_dao import AuthSessionDao
from dao.init_db import init_db


class AuthenticationRequiredError(RuntimeError):
    pass


def get_auth_dao():
    db = init_db()
    dao = AuthSessionDao(db)
    dao.ensure_table()
    return dao


def account_key(value=None):
    return str(value or "default")


def is_authenticated(account=None):
    try:
        dao = get_auth_dao()
        row = dao.get(account_key(account))
        if row and row.get("status") == "AUTHENTICATED":
            return True
        return False
    except Exception:
        return False


def require_authenticated(account=None):
    if not is_authenticated(account):
        raise AuthenticationRequiredError("Bilibili login is required; task paused")


def mark_authenticated(account=None, uid=None):
    get_auth_dao().upsert(account_key(account), "AUTHENTICATED", uid=uid, verified=True)


def mark_login_required(account=None, error=None):
    get_auth_dao().upsert(account_key(account), "LOGIN_REQUIRED", error=error)
