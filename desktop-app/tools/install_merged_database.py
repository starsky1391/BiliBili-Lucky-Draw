import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3


def fingerprint(connection):
    result = []
    for name, sql in connection.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name"):
        quoted = '"' + name.replace('"', '""') + '"'
        rows = connection.execute('SELECT * FROM ' + quoted).fetchall()
        encoded = sorted(json.dumps(row, ensure_ascii=False, default=lambda value: value.hex()) for row in rows)
        result.append((name, sql, encoded))
    return hashlib.sha256(json.dumps(result, ensure_ascii=False).encode('utf-8')).hexdigest()


def install(merged_path, expected_current_path):
    merged = Path(merged_path).resolve()
    expected = Path(expected_current_path).resolve()
    current = Path(os.environ['LOCALAPPDATA']) / 'BiliBiliLuckyDraw' / 'luckybili.sqlite3'
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = current.with_name('luckybili.before-merge-' + stamp + '.sqlite3')
    staging = current.with_name('luckybili.merge-staging-' + stamp + '.sqlite3')
    if backup.exists() or staging.exists():
        raise FileExistsError('Import staging or backup already exists')
    previous = sqlite3.connect(expected.as_uri() + '?mode=ro', uri=True)
    live = sqlite3.connect(current.as_uri() + '?mode=rw', uri=True, timeout=5)
    try:
        if fingerprint(live) != fingerprint(previous):
            raise RuntimeError('Desktop data changed after the snapshot; regenerate the merge before importing')
        live.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        if live.execute('PRAGMA journal_mode=DELETE').fetchone()[0].lower() != 'delete':
            raise RuntimeError('Desktop database is still in use')
    finally:
        live.close()
        previous.close()
    if any(Path(str(current) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
        raise RuntimeError('SQLite sidecar still exists; desktop application must be fully closed')
    with staging.open('xb') as destination, merged.open('rb') as source:
        shutil.copyfileobj(source, destination)
    staged = sqlite3.connect(staging)
    try:
        staged.execute('PRAGMA journal_mode=DELETE')
        if staged.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Staged database integrity check failed')
        expected_merged = fingerprint(staged)
    finally:
        staged.close()
    os.replace(current, backup)
    try:
        os.replace(staging, current)
    except OSError:
        os.replace(backup, current)
        raise
    installed = sqlite3.connect(current.as_uri() + '?mode=ro', uri=True)
    try:
        if fingerprint(installed) != expected_merged:
            raise RuntimeError('Installed database differs from verified merge')
        totals = {
            table: installed.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
            for table in ('t_account_dynamic', 't_draw_dynamic', 't_share_info')
        }
        if installed.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Installed database integrity check failed')
    finally:
        installed.close()
    record = {'database': str(current), 'backup': str(backup), 'tables': totals,
              'fingerprint_verified': True, 'integrity_check': 'ok'}
    merged.with_suffix('.installed.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(record, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--merged', required=True)
    parser.add_argument('--expected-current', required=True)
    args = parser.parse_args()
    install(args.merged, args.expected_current)
