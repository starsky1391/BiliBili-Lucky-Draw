import re
from datetime import datetime


class DrawDynamicDao(object):
    def __init__(self, db):
        self.table_name = 't_draw_dynamic'
        self.db = db
        self.ensure_lottery_time_column()
        self.ensure_schema_columns()
        self.ensure_up_relation_table()

    def ensure_up_relation_table(self):
        self.db.executeCommit("""
        CREATE TABLE IF NOT EXISTS t_draw_dynamic_up
        (dynamic_id VARCHAR(64) NOT NULL, up_id VARCHAR(50) NOT NULL,
         insert_time DATETIME NULL, update_time DATETIME NULL,
         PRIMARY KEY(dynamic_id, up_id))
        ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

    def ensure_schema_columns(self):
        columns = {
            'dynamic_id': 'VARCHAR(64) NULL',
            'up_id': 'VARCHAR(50) NULL',
            'publish_time': 'DATETIME NULL',
            'lottery_source': 'VARCHAR(30) NULL'
        }
        for name, definition in columns.items():
            try:
                self.db.cur.execute(
                    "SELECT COUNT(*) AS c FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = 't_draw_dynamic' "
                    "AND column_name = %s", (name,)
                )
                if int(self.db.cur.fetchone()['c']) == 0:
                    self.db.cur.execute("ALTER TABLE t_draw_dynamic ADD COLUMN %s %s" % (name, definition))
                    self.db.con.commit()
            except Exception as e:
                print(e)
        try:
            self.db.cur.execute("""UPDATE t_draw_dynamic SET dynamic_id =
                CASE
                  WHEN dyn_url REGEXP '/opus/[0-9]+' THEN SUBSTRING_INDEX(dyn_url, '/', -1)
                  WHEN dyn_url REGEXP '/[0-9]+$' THEN SUBSTRING_INDEX(dyn_url, '/', -1)
                  ELSE dynamic_id
                END
                WHERE dynamic_id IS NULL OR dynamic_id = ''""")
            self.db.con.commit()
        except Exception as e:
            print(e)

    def ensure_lottery_time_column(self):
        try:
            self.db.cur.execute(
                """
                SELECT COUNT(*) AS column_count
                FROM information_schema.columns
                WHERE table_schema = DATABASE()
                  AND table_name = 't_draw_dynamic'
                  AND column_name = 'lottery_time'
                """
            )
            result = self.db.cur.fetchone()
            if result and int(result['column_count']) == 0:
                self.db.executeCommit(
                    "ALTER TABLE t_draw_dynamic ADD COLUMN lottery_time DATETIME NULL AFTER insert_time"
                )
        except Exception as e:
            print(e)

    def query_by_time(self, time, limit=2000, status=1):
        try:
            sql = ("SELECT * FROM " + self.table_name
                   + " where status = '" + str(status)
                   + "' and insert_time >= '" + time
                   + "' limit " + str(limit))
            data = self.db.select_db(sql)  # 用mysql_operate文件中的db的select_db方法进行查询
            return data
        except Exception as e:
            return {}

    def query_by_status(self, status=0, limit=2000, newest_first=False):
        try:
            order = "desc" if newest_first else ""
            sql = ("SELECT * FROM " + self.table_name
                   + " where status = '" + str(status)
                   + "' order by insert_time " + order)
            if limit is not None and int(limit) > 0:
                sql = sql + " limit " + str(limit)
            data = self.db.select_db(sql)
            return data
        except Exception as e:
            return {}

    def query_by_dyn_url(self, dyn_url):
        try:
            match = re.search(r'(?:/opus/|/)(\d+)(?:[/?#]|$)', str(dyn_url or ''))
            if not match:
                return {}
            sql = ("SELECT * FROM " + self.table_name
                   + " where dynamic_id = '" + match.group(1) + "'")
            data = self.db.select_db(sql)  # 用mysql_operate文件中的db的select_db方法进行查询
            return data
        except Exception as e:
            return {}

    def insert(self, dyn_url, source, note):
        try:
            match = re.search(r'(?:/opus/|/)(\d+)(?:[/?#]|$)', str(dyn_url or ''))
            dynamic_id = match.group(1) if match else None
            if not dynamic_id:
                return False
            params = {}
            params['dyn_url'] = 'https://www.bilibili.com/opus/' + dynamic_id
            params['dynamic_id'] = dynamic_id
            params['source'] = str(source)
            params['status'] = '0'
            params['note'] = str(note)
            params['insert_time'] = str(datetime.now())
            self.db.cur.execute(
                """INSERT INTO t_draw_dynamic
                   (dynamic_id, dyn_url, source, status, note, insert_time)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON DUPLICATE KEY UPDATE
                     source=COALESCE(NULLIF(VALUES(source), ''), source),
                     note=COALESCE(NULLIF(VALUES(note), ''), note)""",
                (
                    params['dynamic_id'], params['dyn_url'], params['source'],
                    params['status'], params['note'], params['insert_time']
                )
            )
            self.db.con.commit()
            return True
        except Exception as e:
            print(e)
            return False

    def ensure_dynamic(self, dynamic_id, dyn_url, up_id=None, publish_time=None,
                       source=None, note=None, lottery_time=None,
                       lottery_source=None):
        self.db.cur.execute(
            """SELECT dyn_url
               FROM t_draw_dynamic
               WHERE dynamic_id=%s
               ORDER BY
                 (up_id IS NULL OR up_id='') DESC,
                 (publish_time IS NULL) DESC,
                 (lottery_time IS NULL) DESC,
                 (lottery_source IS NULL) DESC,
                 (status='1') DESC
               LIMIT 1""",
            (str(dynamic_id),)
        )
        existing = self.db.cur.fetchone()
        if existing:
            self.db.cur.execute(
                """UPDATE t_draw_dynamic
                   SET up_id=COALESCE(%s, up_id),
                       publish_time=COALESCE(%s, publish_time),
                       lottery_time=COALESCE(%s, lottery_time),
                       lottery_source=COALESCE(%s, lottery_source)
                   WHERE dyn_url=%s""",
                (up_id, publish_time, lottery_time, lottery_source, existing['dyn_url'])
            )
            self.db.con.commit()
            return
        self.db.cur.execute(
            """INSERT INTO t_draw_dynamic
               (dynamic_id, dyn_url, up_id, publish_time, insert_time, lottery_time,
                lottery_source, source, note, status)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, '0')
               ON DUPLICATE KEY UPDATE
                 up_id=COALESCE(VALUES(up_id), up_id),
                 publish_time=COALESCE(VALUES(publish_time), publish_time),
                 lottery_time=COALESCE(VALUES(lottery_time), lottery_time),
                 lottery_source=COALESCE(VALUES(lottery_source), lottery_source)""",
            (str(dynamic_id), dyn_url, up_id, publish_time, datetime.now(), lottery_time,
             lottery_source, source, note)
        )
        self.db.con.commit()

    def update_sharedUrl(self, url, status):
        try:
            match = re.search(r'(?:/opus/|/)(\d+)(?:[/?#]|$)', str(url or ''))
            if not match:
                return
            self.db.cur.execute(
                "UPDATE t_draw_dynamic SET status=%s WHERE dynamic_id=%s",
                (str(status), match.group(1))
            )
            self.db.con.commit()
        except Exception as e:
            print(e)

    def update_lottery_time(self, url, lottery_time):
        try:
            match = re.search(r'(?:/opus/|/)(\d+)(?:[/?#]|$)', str(url or ''))
            if not match:
                return
            self.db.cur.execute(
                "UPDATE t_draw_dynamic SET lottery_time=%s WHERE dynamic_id=%s",
                (lottery_time, match.group(1))
            )
            self.db.con.commit()
        except Exception as e:
            print(e)

    def update_up_info(self, dynamic_id, up_id, publish_time=None,
                       lottery_time=None, lottery_source=None):
        fields = ['up_id = %s']
        values = [str(up_id)]
        if publish_time is not None:
            fields.append('publish_time = %s')
            values.append(publish_time)
        if lottery_time is not None:
            fields.append('lottery_time = %s')
            values.append(lottery_time)
        if lottery_source is not None:
            fields.append('lottery_source = %s')
            values.append(lottery_source)
        values.append(str(dynamic_id))
        self.db.cur.execute(
            'UPDATE t_draw_dynamic SET ' + ', '.join(fields) + ' WHERE dynamic_id = %s',
            tuple(values)
        )
        self.db.con.commit()

    def ensure_dynamic_up(self, dynamic_id, up_id):
        if not dynamic_id or not up_id:
            return
        now = datetime.now()
        self.db.cur.execute(
            """INSERT INTO t_draw_dynamic_up(dynamic_id, up_id, insert_time, update_time)
               VALUES(%s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE update_time=VALUES(update_time)""",
            (str(dynamic_id), str(up_id), now, now)
        )
        self.db.con.commit()

    def query_dynamic_up_ids(self, dynamic_id):
        self.db.cur.execute(
            'SELECT up_id FROM t_draw_dynamic_up WHERE dynamic_id=%s ORDER BY up_id',
            (str(dynamic_id),)
        )
        return [str(row['up_id']) for row in self.db.cur.fetchall()]
