from threading import Lock

from dao.init_db import init_db
from service.account_service import list_accounts


task_lock = Lock()
DEFAULT_TASKS = {'collect': True, 'backfill_personal': False, 'cleanup': True}


def get_account_tasks(account_key):
    db = init_db()
    try:
        db.executeCommit('''CREATE TABLE IF NOT EXISTS t_account_tasks (
            account_key TEXT PRIMARY KEY, collect INTEGER NOT NULL DEFAULT 1,
            backfill_personal INTEGER NOT NULL DEFAULT 0, cleanup INTEGER NOT NULL DEFAULT 1)''')
        row = db.cur.execute('SELECT * FROM t_account_tasks WHERE account_key=?', (str(account_key),)).fetchone()
        return {key: bool(row[key]) for key in DEFAULT_TASKS} if row else dict(DEFAULT_TASKS)
    finally:
        db.close()


def set_account_tasks(account_key, tasks):
    if set(tasks) != set(DEFAULT_TASKS) or any(type(value) is not bool for value in tasks.values()):
        raise ValueError('任务设置必须包含三种任务的布尔值')
    get_account_tasks(account_key)
    db = init_db()
    try:
        db.cur.execute('''INSERT INTO t_account_tasks(account_key,collect,backfill_personal,cleanup)
            VALUES(?,?,?,?) ON CONFLICT(account_key) DO UPDATE SET
            collect=excluded.collect,backfill_personal=excluded.backfill_personal,cleanup=excluded.cleanup''',
            (str(account_key), tasks['collect'], tasks['backfill_personal'], tasks['cleanup']))
    finally:
        db.close()
    return dict(tasks)


def allowed_accounts(task, requested=None):
    task = 'collect' if task in ('reidentify_expired', 'reidentify_one', 'retry') else task
    requested = None if requested is None else {str(value) for value in requested}
    return [str(row['account_key']) for row in list_accounts(enabled=True)
            if (requested is None or str(row['account_key']) in requested)
            and get_account_tasks(row['account_key'])[task]]


def run_reserved(target, *args):
    try:
        target(*args)
    finally:
        task_lock.release()
