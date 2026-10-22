from datetime import datetime


class AccountDynamicDao:
    def __init__(self, db):
        self.db = db
        self.ensure_tables()

    def ensure_tables(self):
        self.db.executeCommit("""
        CREATE TABLE IF NOT EXISTS t_account
        (id INTEGER PRIMARY KEY AUTOINCREMENT, account_key VARCHAR(100) NOT NULL UNIQUE,
         bili_uid VARCHAR(50) NULL, username VARCHAR(255) NULL,
         remark VARCHAR(255) NULL, enabled TINYINT NOT NULL DEFAULT 1,
         config_file VARCHAR(255) NULL, insert_time DATETIME NULL, update_time DATETIME NULL)
        """)
        columns = {
            row.get("name")
            for row in (self.db.executeSql("PRAGMA table_info(t_account)") or [])
        }
        for name, definition in (
            ("username", "VARCHAR(255) NULL"),
            ("remark", "VARCHAR(255) NULL"),
        ):
            if name not in columns:
                self.db.executeCommit(
                    "ALTER TABLE t_account ADD COLUMN %s %s" % (name, definition)
                )
        self.db.executeCommit("""
        CREATE TABLE IF NOT EXISTS t_up_info
        (up_id VARCHAR(50) PRIMARY KEY, is_managed TINYINT NOT NULL DEFAULT 1,
         insert_time DATETIME NULL, update_time DATETIME NULL)
        """)
        self.db.executeCommit("""
        CREATE TABLE IF NOT EXISTS t_account_dynamic
        (id INTEGER PRIMARY KEY AUTOINCREMENT, dynamic_id VARCHAR(64) NOT NULL,
         account_key VARCHAR(100) NOT NULL, share_status TINYINT NOT NULL DEFAULT 0,
         share_time DATETIME NULL, own_dynamic_id VARCHAR(64) NULL,
         own_dynamic_url VARCHAR(255) NULL, cleanup_status TINYINT NOT NULL DEFAULT 0,
         error_message VARCHAR(500) NULL, insert_time DATETIME NULL, update_time DATETIME NULL,
         UNIQUE (dynamic_id, account_key))
        """)

    def ensure_account(self, account_key):
        now = datetime.now()
        self.db.cur.execute("""INSERT INTO t_account(account_key, enabled, insert_time, update_time)
            VALUES(%s, 1, %s, %s) ON CONFLICT DO UPDATE SET update_time=excluded.update_time""",
                           (account_key, now, now))
        self.db.con.commit()

    def ensure_up(self, up_id):
        if not up_id:
            return
        now = datetime.now()
        self.db.cur.execute("""INSERT INTO t_up_info(up_id, is_managed, insert_time, update_time)
            VALUES(%s, 1, %s, %s) ON CONFLICT DO UPDATE SET is_managed=1, update_time=excluded.update_time""",
                           (str(up_id), now, now))
        self.db.con.commit()

    def upsert_shared(self, dynamic_id, account_key, share_time=None):
        now = datetime.now()
        self.ensure_account(account_key)
        self.db.cur.execute("""INSERT INTO t_account_dynamic
            (dynamic_id, account_key, share_status, share_time, insert_time, update_time)
            VALUES(%s, %s, 1, %s, %s, %s)
            ON CONFLICT DO UPDATE SET share_status=1, share_time=excluded.share_time, update_time=excluded.update_time""",
                           (str(dynamic_id), account_key, share_time or now, now, now))
        self.db.con.commit()

    def update_own_dynamic(self, dynamic_id, account_key, own_dynamic_id, own_dynamic_url):
        now = datetime.now()
        self.ensure_account(account_key)
        self.db.cur.execute("""INSERT INTO t_account_dynamic
            (dynamic_id, account_key, share_status, own_dynamic_id, own_dynamic_url,
             insert_time, update_time)
            VALUES(%s, %s, 1, %s, %s, %s, %s)
            ON CONFLICT DO UPDATE SET own_dynamic_id=excluded.own_dynamic_id,
              own_dynamic_url=excluded.own_dynamic_url, share_status=1, update_time=excluded.update_time""",
                           (str(dynamic_id), account_key, str(own_dynamic_id), own_dynamic_url, now, now))
        self.db.con.commit()

    def query_without_own_dynamic(self, account_key):
        self.db.cur.execute("""SELECT * FROM t_account_dynamic
            WHERE account_key=%s AND share_status=1
              AND (own_dynamic_id IS NULL OR own_dynamic_id='')""", (account_key,))
        return self.db.cur.fetchall()

    def query_own_dynamic_ids(self, account_key):
        self.db.cur.execute("""SELECT own_dynamic_id FROM t_account_dynamic
            WHERE account_key=%s AND own_dynamic_id IS NOT NULL AND own_dynamic_id<>''""",
                           (account_key,))
        return {str(row['own_dynamic_id']) for row in self.db.cur.fetchall()}
