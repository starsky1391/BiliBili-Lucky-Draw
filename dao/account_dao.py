from datetime import datetime


class AccountDao:
    def __init__(self, db):
        self.db = db
        self.ensure_table()

    def ensure_table(self):
        self.db.executeCommit("""
            CREATE TABLE IF NOT EXISTS t_account
            (id BIGINT AUTO_INCREMENT PRIMARY KEY,
             account_key VARCHAR(100) NOT NULL UNIQUE,
             bili_uid VARCHAR(50) NULL,
             username VARCHAR(255) NULL,
             remark VARCHAR(255) NULL,
             enabled TINYINT NOT NULL DEFAULT 1,
             config_file VARCHAR(255) NULL,
             insert_time DATETIME NULL,
             update_time DATETIME NULL)
            ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        columns = {
            row.get("Field")
            for row in (self.db.executeSql("SHOW COLUMNS FROM t_account") or [])
        }
        for name, definition in (
            ("username", "VARCHAR(255) NULL"),
            ("remark", "VARCHAR(255) NULL"),
        ):
            if name not in columns:
                self.db.executeCommit(
                    "ALTER TABLE t_account ADD COLUMN %s %s" % (name, definition)
                )

    def get(self, account_key):
        value = str(account_key).replace("'", "''")
        rows = self.db.executeSql(
            "SELECT * FROM t_account WHERE account_key='%s' LIMIT 1" % value
        ) or []
        return rows[0] if rows else None

    def list(self, enabled=None):
        where = ""
        if enabled is not None:
            where = " WHERE enabled=%s" % (1 if enabled else 0)
        return self.db.executeSql(
            "SELECT * FROM t_account%s ORDER BY enabled DESC, account_key ASC" % where
        ) or []

    def ensure(self, account_key, bili_uid=None, username=None,
               config_file=None, remark=None):
        account_key = str(account_key)
        current = self.get(account_key)
        now = datetime.now()
        if current:
            next_username = username or current.get("username")
            current_remark = current.get("remark")
            next_remark = (
                next_username
                if next_username and current_remark == account_key
                else current_remark or remark or next_username
            )
            self.db.cur.execute(
                """UPDATE t_account
                   SET bili_uid=COALESCE(%s, bili_uid),
                       username=%s,
                       remark=%s,
                       config_file=COALESCE(%s, config_file),
                       update_time=%s
                   WHERE account_key=%s""",
                (
                    bili_uid, next_username, next_remark, config_file,
                    now, account_key,
                ),
            )
        else:
            self.db.cur.execute(
                """INSERT INTO t_account
                   (account_key, bili_uid, username, remark, enabled,
                    config_file, insert_time, update_time)
                   VALUES(%s, %s, %s, %s, 1, %s, %s, %s)""",
                (
                    account_key, bili_uid or account_key, username,
                    remark or username or account_key, config_file, now, now,
                ),
            )
        self.db.con.commit()
        return self.get(account_key)

    def update_profile(self, account_key, remark=None, username=None):
        current = self.get(account_key)
        if not current:
            return None
        values = {
            "remark": current.get("remark") if remark is None else str(remark).strip(),
            "username": current.get("username") if username is None else username,
        }
        self.db.cur.execute(
            """UPDATE t_account
               SET remark=%s, username=%s, update_time=%s
               WHERE account_key=%s""",
            (values["remark"], values["username"], datetime.now(), str(account_key)),
        )
        self.db.con.commit()
        return self.get(account_key)

    def set_enabled(self, account_key, enabled):
        self.db.cur.execute(
            "UPDATE t_account SET enabled=%s, update_time=%s WHERE account_key=%s",
            (1 if enabled else 0, datetime.now(), str(account_key)),
        )
        self.db.con.commit()
        return self.get(account_key)
