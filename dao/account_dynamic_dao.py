from datetime import datetime


class AccountDynamicDao:
    def __init__(self, db):
        self.db = db
        self.ensure_tables()

    def ensure_tables(self):
        self.db.executeCommit("""
        CREATE TABLE IF NOT EXISTS t_account
        (id BIGINT AUTO_INCREMENT PRIMARY KEY, account_key VARCHAR(100) NOT NULL UNIQUE,
         bili_uid VARCHAR(50) NULL, enabled TINYINT NOT NULL DEFAULT 1,
         config_file VARCHAR(255) NULL, insert_time DATETIME NULL, update_time DATETIME NULL)
        ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        self.db.executeCommit("""
        CREATE TABLE IF NOT EXISTS t_up_info
        (up_id VARCHAR(50) PRIMARY KEY, is_managed TINYINT NOT NULL DEFAULT 1,
         insert_time DATETIME NULL, update_time DATETIME NULL)
        ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        self.db.executeCommit("""
        CREATE TABLE IF NOT EXISTS t_account_dynamic
        (id BIGINT AUTO_INCREMENT PRIMARY KEY, dynamic_id VARCHAR(64) NOT NULL,
         account_key VARCHAR(100) NOT NULL, share_status TINYINT NOT NULL DEFAULT 0,
         share_time DATETIME NULL, own_dynamic_id VARCHAR(64) NULL,
         own_dynamic_url VARCHAR(255) NULL, cleanup_status TINYINT NOT NULL DEFAULT 0,
         error_message VARCHAR(500) NULL, insert_time DATETIME NULL, update_time DATETIME NULL,
         UNIQUE KEY uk_account_dynamic(dynamic_id, account_key))
        ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

    def ensure_account(self, account_key):
        now = datetime.now()
        self.db.cur.execute("""INSERT INTO t_account(account_key, enabled, insert_time, update_time)
            VALUES(%s, 1, %s, %s) ON DUPLICATE KEY UPDATE update_time=VALUES(update_time)""",
                           (account_key, now, now))
        self.db.con.commit()

    def ensure_up(self, up_id):
        if not up_id:
            return
        now = datetime.now()
        self.db.cur.execute("""INSERT INTO t_up_info(up_id, is_managed, insert_time, update_time)
            VALUES(%s, 1, %s, %s) ON DUPLICATE KEY UPDATE is_managed=1, update_time=VALUES(update_time)""",
                           (str(up_id), now, now))
        self.db.con.commit()

    def upsert_shared(self, dynamic_id, account_key, share_time=None):
        now = datetime.now()
        self.ensure_account(account_key)
        self.db.cur.execute("""INSERT INTO t_account_dynamic
            (dynamic_id, account_key, share_status, share_time, insert_time, update_time)
            VALUES(%s, %s, 1, %s, %s, %s)
            ON DUPLICATE KEY UPDATE share_status=1, share_time=VALUES(share_time), update_time=VALUES(update_time)""",
                           (str(dynamic_id), account_key, share_time or now, now, now))
        self.db.con.commit()

    def update_own_dynamic(self, dynamic_id, account_key, own_dynamic_id, own_dynamic_url):
        now = datetime.now()
        self.ensure_account(account_key)
        self.db.cur.execute("""INSERT INTO t_account_dynamic
            (dynamic_id, account_key, share_status, own_dynamic_id, own_dynamic_url,
             insert_time, update_time)
            VALUES(%s, %s, 1, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE own_dynamic_id=VALUES(own_dynamic_id),
              own_dynamic_url=VALUES(own_dynamic_url), share_status=1, update_time=VALUES(update_time)""",
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
