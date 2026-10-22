import os
import sqlite3
from datetime import datetime
from pathlib import Path


sqlite3.register_adapter(datetime, lambda value: value.isoformat(sep=" ", timespec="seconds"))
sqlite3.register_converter("DATETIME", lambda value: datetime.fromisoformat(value.decode()))


class Cursor(sqlite3.Cursor):
    def execute(self, sql, parameters=None):
        if parameters is None:
            return super().execute(sql)
        return super().execute(sql.replace("%s", "?"), parameters)

    def executemany(self, sql, parameters):
        return super().executemany(sql.replace("%s", "?"), parameters)


class Connection(sqlite3.Connection):
    def cursor(self):
        return super().cursor(factory=Cursor)

    def ping(self, reconnect=True):
        self.execute("SELECT 1")


class SQLiteHelper:
    def __init__(self, path):
        self.con = sqlite3.connect(
            path, timeout=30, isolation_level=None,
            detect_types=sqlite3.PARSE_DECLTYPES, factory=Connection,
        )
        self.con.row_factory = lambda cursor, row: {
            column[0]: row[index] for index, column in enumerate(cursor.description)
        }
        self.con.execute("PRAGMA journal_mode=WAL")
        self.cur = self.con.cursor()

    def executeSql(self, sql):
        return self.cur.execute(sql).fetchall()

    def executeCommit(self, sql):
        self.cur.execute(sql)
        self.con.commit()

    select_db = executeSql

    def execute_db(self, sql):
        self.executeCommit(sql)
        return "插入成功"

    def insert(self, table, params):
        fields = ",".join('"%s"' % key for key in params)
        values = ",".join("?" for _ in params)
        self.cur.execute(f'INSERT INTO "{table}" ({fields}) VALUES ({values})', tuple(params.values()))

    def insertMany(self, table, attrs, values):
        fields = ",".join('"%s"' % key for key in attrs)
        placeholders = ",".join("?" for _ in attrs)
        self.cur.executemany(f'INSERT INTO "{table}" ({fields}) VALUES ({placeholders})', values)

    def update(self, table, attrs_dict, cond_dict):
        fields = ",".join('"%s"=?' % key for key in attrs_dict)
        where = " AND ".join('"%s"=?' % key for key in cond_dict)
        self.cur.execute(f'UPDATE "{table}" SET {fields} WHERE {where}',
                         tuple(attrs_dict.values()) + tuple(cond_dict.values()))

    def select(self, table, cond_dict="", order="", fields="*"):
        columns = ",".join('"%s"' % name for name in fields) if isinstance(fields, list) else fields
        conditions = cond_dict or {}
        where = " AND ".join('"%s"=?' % key for key in conditions) or "1=1"
        return self.cur.execute(f'SELECT {columns} FROM "{table}" WHERE {where} {order}',
                                tuple(conditions.values())).fetchall()

    def delete(self, table, cond_dict):
        where = " AND ".join('"%s"=?' % key for key in cond_dict)
        self.cur.execute(f'DELETE FROM "{table}" WHERE {where}', tuple(cond_dict.values()))

    def close(self):
        self.cur.close()
        self.con.close()

    def __del__(self):
        if hasattr(self, "con"):
            self.con.close()


def init_db():
    path = Path(os.getenv("SQLITE_PATH", "luckybili.sqlite3"))
    return SQLiteHelper(path)


def initialize_database():
    db = init_db()
    try:
        schema = Path(__file__).resolve().parents[1] / "schema.sql"
        db.con.executescript(schema.read_text(encoding="utf-8"))
        db.executeCommit("""CREATE TABLE IF NOT EXISTS t_draw_dynamic_up (
            dynamic_id VARCHAR(64) NOT NULL, up_id VARCHAR(50) NOT NULL,
            insert_time DATETIME, update_time DATETIME,
            PRIMARY KEY (dynamic_id, up_id))""")
    finally:
        db.close()
