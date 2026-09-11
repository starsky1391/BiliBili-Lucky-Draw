from datetime import datetime


class PendingUnfollowDao:
    def __init__(self, db):
        self.db = db
        self.ensure_table()

    def ensure_table(self):
        self.db.executeCommit("""
        CREATE TABLE IF NOT EXISTS t_pending_unfollow
        (id BIGINT AUTO_INCREMENT PRIMARY KEY,
         account_key VARCHAR(100) NOT NULL,
         up_id VARCHAR(50) NOT NULL,
         source_dynamic_id VARCHAR(64) NULL,
         status TINYINT NOT NULL DEFAULT 0,
         error_message VARCHAR(500) NULL,
         retry_time DATETIME NULL,
         insert_time DATETIME NOT NULL,
         update_time DATETIME NOT NULL,
         UNIQUE KEY uk_account_up(account_key, up_id),
         KEY idx_pending_unfollow(account_key, status, retry_time))
        ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

    def enqueue(self, account_key, up_id, source_dynamic_id=None, message=None):
        now = datetime.now()
        self.db.cur.execute("""
            INSERT INTO t_pending_unfollow
                (account_key, up_id, source_dynamic_id, status, error_message,
                 retry_time, insert_time, update_time)
            VALUES(%s, %s, %s, 2, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                source_dynamic_id=COALESCE(VALUES(source_dynamic_id), source_dynamic_id),
                status=2,
                error_message=VALUES(error_message),
                retry_time=VALUES(retry_time),
                update_time=VALUES(update_time)
        """, (account_key, str(up_id), source_dynamic_id, message[:500] if message else None,
              now, now, now))
        self.db.con.commit()

    def query_pending(self, account_key):
        self.db.cur.execute("""
            SELECT * FROM t_pending_unfollow
            WHERE account_key=%s AND status IN (0, 2)
              AND (retry_time IS NULL OR retry_time <= NOW())
            ORDER BY insert_time, id
        """, (account_key,))
        return self.db.cur.fetchall()

    def mark_processing(self, record_id):
        now = datetime.now()
        self.db.cur.execute("""
            UPDATE t_pending_unfollow
            SET status=3, update_time=%s
            WHERE id=%s AND status IN (0, 2)
        """, (now, record_id))
        self.db.con.commit()
        return self.db.cur.rowcount == 1

    def delete(self, record_id):
        self.db.cur.execute("""
            DELETE FROM t_pending_unfollow WHERE id=%s
        """, (record_id,))
        self.db.con.commit()

    def mark_failed(self, record_id, message):
        self.db.cur.execute("""
            UPDATE t_pending_unfollow
            SET status=2, error_message=%s, retry_time=%s, update_time=%s
            WHERE id=%s
        """, (message[:500], datetime.now(), datetime.now(), record_id))
        self.db.con.commit()
