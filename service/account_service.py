import json
import os
from pathlib import Path

from dao.account_dao import AccountDao
from dao.init_db import init_db


def cookie_dir():
    return Path(os.getenv("COOKIE_DIR", "/app/cookie"))


def read_cookie_file(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    return data if isinstance(data, list) else []


def cookie_map(cookies):
    return {
        str(item.get("name")): str(item.get("value"))
        for item in cookies
        if item.get("name") and item.get("value")
    }


def cookie_uid(cookies):
    uid = cookie_map(cookies).get("DedeUserID")
    return str(uid) if uid and str(uid).isdigit() else None


def migrate_legacy_account_dynamics(db, legacy_key, account_key):
    legacy_key = str(legacy_key)
    account_key = str(account_key)
    if legacy_key == account_key:
        return
    db.cur.execute(
        """UPDATE t_account_dynamic legacy
           LEFT JOIN t_account_dynamic current
             ON current.dynamic_id=legacy.dynamic_id
            AND current.account_key=%s
           SET legacy.account_key=%s
           WHERE legacy.account_key=%s
             AND current.id IS NULL""",
        (account_key, account_key, legacy_key),
    )
    db.con.commit()


def cookie_path(account_key):
    directory = cookie_dir()
    canonical = directory / (str(account_key) + ".json")
    if canonical.is_file():
        return canonical
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        if cookie_uid(read_cookie_file(path)) == str(account_key):
            return path
    return canonical


def ensure_cookie_accounts():
    db = init_db()
    dao = AccountDao(db)
    directory = cookie_dir()
    if not directory.is_dir():
        return dao.list()
    for path in sorted(directory.glob("*.json")):
        cookies = read_cookie_file(path)
        uid = cookie_uid(cookies)
        if not uid:
            continue
        migrate_legacy_account_dynamics(db, path.stem, uid)
        dao.ensure(uid, bili_uid=uid, config_file=path.name)
    return dao.list()


def list_accounts(enabled=None):
    rows = ensure_cookie_accounts()
    rows = [row for row in rows if str(row.get("account_key") or "").isdigit()]
    if enabled is None:
        return rows
    return [row for row in rows if bool(row.get("enabled")) == bool(enabled)]


def enabled_account_keys():
    return [str(row["account_key"]) for row in list_accounts(enabled=True)]
