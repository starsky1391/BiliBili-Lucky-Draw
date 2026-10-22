import argparse
from datetime import date, datetime, time
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pymysql


def quote(name):
    return '`' + name.replace('`', '``') + '`'


def sqlite_type(mysql_type):
    kind = mysql_type.lower().split('(')[0]
    if kind in ('datetime', 'timestamp'):
        return 'DATETIME'
    if 'int' in kind:
        return 'INTEGER'
    if kind in ('double', 'float', 'real'):
        return 'REAL'
    if 'blob' in kind or kind in ('binary', 'varbinary', 'bit'):
        return 'BLOB'
    return 'TEXT'


def value_for_sqlite(value):
    if isinstance(value, (datetime, date, time)):
        return value.isoformat(sep=' ') if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def row_hash(row):
    normalized = [None if value is None else
                  value.hex() if isinstance(value, bytes) else str(value) for value in row]
    return hashlib.sha256(json.dumps(normalized, ensure_ascii=False).encode('utf-8')).hexdigest()


def convert(host, port, database, user, output):
    output = Path(output).resolve()
    partial = output.with_name(output.name + '.partial')
    if output.exists() or partial.exists():
        raise FileExistsError('Destination already exists; refusing to overwrite')
    output.parent.mkdir(parents=True, exist_ok=True)
    source = pymysql.connect(
        host=host, port=port, user=user,
        password=os.getenv('MIGRATION_MYSQL_PASSWORD', ''), database=database,
        charset='utf8mb4', connect_timeout=10, read_timeout=120,
        cursorclass=pymysql.cursors.DictCursor,
    )
    report = {}
    try:
        with source.cursor() as cursor:
            cursor.execute('SET SESSION TRANSACTION READ ONLY')
            cursor.execute('START TRANSACTION WITH CONSISTENT SNAPSHOT')
            cursor.execute("SELECT TABLE_NAME FROM information_schema.tables "
                           "WHERE TABLE_SCHEMA=%s AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME",
                           (database,))
            tables = [row['TABLE_NAME'] for row in cursor.fetchall()]
            partial.open('xb').close()
            with sqlite3.connect(partial) as target:
                target.executescript((Path(__file__).resolve().parents[1] / 'schema.sql').read_text(encoding='utf-8'))
                target.execute('''CREATE TABLE IF NOT EXISTS t_draw_dynamic_up (
                    dynamic_id VARCHAR(64) NOT NULL, up_id VARCHAR(50) NOT NULL,
                    insert_time DATETIME, update_time DATETIME,
                    PRIMARY KEY(dynamic_id, up_id))''')
                target.execute('BEGIN')
                for table in tables:
                    cursor.execute('SHOW COLUMNS FROM ' + quote(table))
                    fields = cursor.fetchall()
                    columns = [field['Field'] for field in fields]
                    existing = {row[1] for row in target.execute('PRAGMA table_info(' + quote(table) + ')')}
                    if not existing:
                        definitions = [quote(field['Field']) + ' ' + sqlite_type(field['Type']) for field in fields]
                        keys = [quote(field['Field']) for field in fields if field['Key'] == 'PRI']
                        if keys:
                            definitions.append('PRIMARY KEY (' + ','.join(keys) + ')')
                        target.execute('CREATE TABLE ' + quote(table) + ' (' + ','.join(definitions) + ')')
                    else:
                        for field in fields:
                            if field['Field'] not in existing:
                                target.execute('ALTER TABLE ' + quote(table) + ' ADD COLUMN ' +
                                               quote(field['Field']) + ' ' + sqlite_type(field['Type']))
                    cursor.execute('SELECT * FROM ' + quote(table))
                    rows = [tuple(value_for_sqlite(row[name]) for name in columns) for row in cursor.fetchall()]
                    field_sql = ','.join(quote(name) for name in columns)
                    target.executemany('INSERT INTO ' + quote(table) + ' (' + field_sql + ') VALUES (' +
                                       ','.join('?' for _ in columns) + ')', rows)
                    actual = target.execute('SELECT ' + field_sql + ' FROM ' + quote(table)).fetchall()
                    if sorted(map(row_hash, rows)) != sorted(map(row_hash, actual)):
                        raise ValueError('Row verification failed for ' + table)
                    report[table] = len(rows)
                    print(table + ': ' + str(len(rows)) + ' rows verified')
                target.commit()
                integrity = target.execute('PRAGMA integrity_check').fetchone()[0]
                if integrity != 'ok':
                    raise ValueError('SQLite integrity check failed')
            # Close the file before renaming it on Windows.
            target.close()
            partial.rename(output)
    finally:
        source.rollback()
        source.close()
    report_path = output.with_suffix('.verification.json')
    report_path.write_text(json.dumps({'tables': report, 'integrity_check': 'ok',
                                       'all_rows_verified': True}, indent=2), encoding='utf-8')
    print('SQLite conversion complete: ' + str(output))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--database', default='luckybili')
    parser.add_argument('--user', default='root')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    convert(args.host, args.port, args.database, args.user, args.output)
