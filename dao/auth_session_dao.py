from datetime import datetime


class AuthSessionDao:
    def __init__(self, db):
        self.db = db

    def ensure_table(self):
        self.db.executeCommit("""
            CREATE TABLE IF NOT EXISTS t_auth_session (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                account_key VARCHAR(100) NOT NULL UNIQUE,
                status VARCHAR(30) NOT NULL DEFAULT 'UNKNOWN',
                uid VARCHAR(50) NULL,
                last_verified_at DATETIME NULL,
                cookie_saved_at DATETIME NULL,
                last_error VARCHAR(500) NULL,
                update_time DATETIME NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

    def get(self, account_key):
        rows = self.db.executeSql(
            "SELECT * FROM t_auth_session WHERE account_key='%s' LIMIT 1"
            % account_key.replace("'", "''")
        ) or []
        return rows[0] if rows else None

    def upsert(self, account_key, status, uid=None, error=None,
               verified=False, cookie_saved=False):
        current = self.get(account_key)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        verified_sql = "last_verified_at=VALUES(last_verified_at)" if verified else "last_verified_at=last_verified_at"
        cookie_sql = "cookie_saved_at=VALUES(cookie_saved_at)" if cookie_saved else "cookie_saved_at=cookie_saved_at"
        uid_value = "NULL" if uid is None else "'%s'" % str(uid).replace("'", "''")
        error_value = "NULL" if error is None else "'%s'" % str(error)[:500].replace("'", "''")
        sql = """
            INSERT INTO t_auth_session
                (account_key, status, uid, last_verified_at, cookie_saved_at, last_error, update_time)
            VALUES ('%s', '%s', %s, %s, %s, %s, '%s')
            ON DUPLICATE KEY UPDATE
                status=VALUES(status),
                uid=COALESCE(VALUES(uid), uid),
                %s,
                %s,
                last_error=VALUES(last_error),
                update_time=VALUES(update_time)
        """ % (
            account_key.replace("'", "''"), status, uid_value,
            "'%s'" % now if verified else "NULL",
            "'%s'" % now if cookie_saved else "NULL",
            error_value, now, verified_sql, cookie_sql
        )
        self.db.executeCommit(sql)

    def is_authenticated(self, account_key):
        row = self.get(account_key)
        return bool(row and row.get("status") == "AUTHENTICATED")
