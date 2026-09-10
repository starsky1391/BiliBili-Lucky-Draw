import hashlib
from datetime import datetime, timedelta


class ScanCacheDao(object):
    def __init__(self, db):
        self.table_name = 't_scan_cache'
        self.db = db
        self.ensure_table()

    def ensure_table(self):
        sql = """
        CREATE TABLE IF NOT EXISTS t_scan_cache
        (
            scan_key    varchar(64)  not null
                primary key,
            scan_type   varchar(50)  not null,
            scan_url    varchar(1024) not null,
            source      varchar(255) null,
            note        varchar(255) null,
            status      varchar(20)  null,
            link_count  int          null,
            last_error  varchar(500) null,
            insert_time datetime     null,
            update_time datetime     null
        )
            DEFAULT CHARSET = utf8mb4
            COLLATE = utf8mb4_general_ci
        """
        self.db.executeCommit(sql)

    def build_scan_key(self, scan_type, scan_url):
        raw_key = str(scan_type) + ':' + str(scan_url)
        return hashlib.md5(raw_key.encode('utf-8')).hexdigest()

    def query(self, scan_type, scan_url):
        try:
            scan_key = self.build_scan_key(scan_type, scan_url)
            self.db.con.ping(reconnect=True)
            self.db.cur.execute(
                "SELECT * FROM " + self.table_name + " WHERE scan_key = %s",
                (scan_key,)
            )
            return self.db.cur.fetchall()
        except Exception:
            return {}

    def has_success(self, scan_type, scan_url):
        data = self.query(scan_type, scan_url)
        return len(data) != 0 and str(data[0].get('status')) == '1'

    def has_recent_failure(self, scan_type, scan_url, retry_hours):
        if retry_hours is None or int(retry_hours) <= 0:
            return False
        data = self.query(scan_type, scan_url)
        if len(data) == 0 or str(data[0].get('status')) == '1':
            return False
        update_time = data[0].get('update_time') or data[0].get('insert_time')
        if update_time is None:
            return False
        return datetime.now() - update_time < timedelta(hours=int(retry_hours))

    def save_success(self, scan_type, scan_url, source, note, link_count):
        self.upsert(scan_type, scan_url, source, note, '1', link_count, '')

    def save_failure(self, scan_type, scan_url, source, note, error):
        self.upsert(scan_type, scan_url, source, note, '3', 0, str(error)[:500])

    def upsert(self, scan_type, scan_url, source, note, status, link_count, last_error):
        try:
            scan_key = self.build_scan_key(scan_type, scan_url)
            now = datetime.now()
            self.db.con.ping(reconnect=True)
            self.db.cur.execute(
                """
                INSERT INTO t_scan_cache
                    (scan_key, scan_type, scan_url, source, note, status, link_count, last_error, insert_time, update_time)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    source = VALUES(source),
                    note = VALUES(note),
                    status = VALUES(status),
                    link_count = VALUES(link_count),
                    last_error = VALUES(last_error),
                    update_time = VALUES(update_time)
                """,
                (scan_key, scan_type, scan_url, source, note, status, link_count, last_error, now, now)
            )
            self.db.con.commit()
        except Exception as e:
            print(e)
