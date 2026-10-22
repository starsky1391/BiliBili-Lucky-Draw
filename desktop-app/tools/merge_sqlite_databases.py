import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3


KEYS = {
    't_account': ('account_key',),
    't_account_tasks': ('account_key',),
    't_account_dynamic': ('dynamic_id', 'account_key'),
    't_auth_session': ('account_key',),
    't_draw_dynamic': ('dyn_url',),
    't_draw_dynamic_up': ('dynamic_id', 'up_id'),
    't_pending_unfollow': ('account_key', 'up_id'),
    't_scan_cache': ('scan_key',),
    't_shared_urls': ('user_id', 'dyn_url'),
    't_up_info': ('up_id',),
}


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


def readonly(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def combine(table, desktop, source):
    result = dict(desktop)
    for name, value in source.items():
        if result.get(name) in (None, ''):
            result[name] = value
    if table == 't_scan_cache' and (source.get('update_time') or '') > (desktop.get('update_time') or ''):
        result.update(source)
        result['scan_key'] = desktop['scan_key']
    if table == 't_account_dynamic':
        if desktop.get('share_status') == 1 or source.get('share_status') == 1:
            result['share_status'] = 1
        if desktop.get('cleanup_status') == 1 or source.get('cleanup_status') == 1:
            result['cleanup_status'] = 1
        old_own = desktop.get('own_dynamic_id')
        new_own = source.get('own_dynamic_id')
        if old_own and new_own and old_own != new_own:
            raise ValueError('Conflicting own dynamic IDs require manual review')
    if table == 't_draw_dynamic':
        if str(desktop.get('status')) == '1' or str(source.get('status')) == '1':
            result['status'] = '1'
        if source.get('lottery_source') == 'explicit' and desktop.get('lottery_source') != 'explicit':
            result['lottery_source'] = source['lottery_source']
            result['lottery_time'] = source.get('lottery_time')
    if 'insert_time' in result:
        times = [row.get('insert_time') for row in (desktop, source) if row.get('insert_time')]
        if times:
            result['insert_time'] = min(times)
    if 'update_time' in result:
        times = [row.get('update_time') for row in (desktop, source) if row.get('update_time')]
        if times:
            result['update_time'] = max(times)
    return result


def merge(desktop_path, source_path, output_path):
    output = Path(output_path).resolve()
    if output.exists():
        raise FileExistsError('Merge output already exists; refusing to overwrite')
    desktop = readonly(desktop_path)
    source = readonly(source_path)
    output.open('xb').close()
    target = sqlite3.connect(output)
    target.row_factory = sqlite3.Row
    report = {}
    try:
        desktop.backup(target)
        tables = source.execute("SELECT name,sql FROM sqlite_master WHERE type='table' "
                                "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
        target.execute('BEGIN')
        for table_info in tables:
            table = table_info['name']
            exists = target.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            fields = source.execute('PRAGMA table_info(' + quoted(table) + ')').fetchall()
            if not exists:
                definitions = []
                for field in fields:
                    kind = field['type'].upper()
                    kind = kind if kind in ('INTEGER', 'REAL', 'TEXT', 'BLOB', 'DATETIME') else 'TEXT'
                    definitions.append(quoted(field['name']) + ' ' + kind)
                primary = sorted((field for field in fields if field['pk']), key=lambda field: field['pk'])
                if primary:
                    definitions.append('PRIMARY KEY (' + ','.join(quoted(field['name']) for field in primary) + ')')
                target.execute('CREATE TABLE ' + quoted(table) + ' (' + ','.join(definitions) + ')')
            target_columns = {row['name'] for row in target.execute('PRAGMA table_info(' + quoted(table) + ')')}
            for field in fields:
                if field['name'] not in target_columns:
                    kind = field['type'].upper()
                    kind = kind if kind in ('INTEGER', 'REAL', 'TEXT', 'BLOB', 'DATETIME') else 'TEXT'
                    target.execute('ALTER TABLE ' + quoted(table) + ' ADD COLUMN ' +
                                   quoted(field['name']) + ' ' + kind)
            all_columns = [row['name'] for row in target.execute('PRAGMA table_info(' + quoted(table) + ')')]
            columns = [field['name'] for field in fields]
            source_rows = [dict(row) for row in source.execute('SELECT * FROM ' + quoted(table))]
            before = target.execute('SELECT COUNT(*) FROM ' + quoted(table)).fetchone()[0]
            keys = KEYS.get(table)
            if keys:
                existing = {tuple(row[key] for key in keys): dict(row)
                            for row in target.execute('SELECT * FROM ' + quoted(table))}
                for row in source_rows:
                    key = tuple(row[name] for name in keys)
                    current = existing.get(key)
                    if current is not None:
                        merged = combine(table, current, row)
                        update_columns = [name for name in all_columns if name != 'id' and name not in keys]
                        target.execute('UPDATE ' + quoted(table) + ' SET ' +
                                       ','.join(quoted(name) + '=?' for name in update_columns) + ' WHERE ' +
                                       ' AND '.join(quoted(name) + '=?' for name in keys),
                                       tuple(merged.get(name) for name in update_columns) + key)
                        existing[key] = merged
                    else:
                        insert_columns = [name for name in columns if name != 'id']
                        target.execute('INSERT INTO ' + quoted(table) + ' (' +
                                       ','.join(map(quoted, insert_columns)) + ') VALUES (' +
                                       ','.join('?' for _ in insert_columns) + ')',
                                       tuple(row[name] for name in insert_columns))
                        existing[key] = row
            else:
                # Surrogate IDs from independent databases can collide; retain log multiplicity.
                content_columns = [name for name in columns if name != 'id']
                existing_counts = Counter(tuple(row[name] for name in content_columns)
                                          for row in target.execute('SELECT * FROM ' + quoted(table)))
                source_counts = Counter()
                for row in source_rows:
                    content = tuple(row[name] for name in content_columns)
                    source_counts[content] += 1
                    if source_counts[content] <= existing_counts[content]:
                        continue
                    insert_columns = columns if not exists else content_columns
                    target.execute('INSERT INTO ' + quoted(table) + ' (' +
                                   ','.join(map(quoted, insert_columns)) + ') VALUES (' +
                                   ','.join('?' for _ in insert_columns) + ')',
                                   tuple(row[name] for name in insert_columns))
            after = target.execute('SELECT COUNT(*) FROM ' + quoted(table)).fetchone()[0]
            report[table] = {'desktop_before': before, 'source': len(source_rows), 'merged': after}
            print(table + ': ' + str(before) + ' + ' + str(len(source_rows)) + ' -> ' + str(after))
        target.commit()
        if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Merged SQLite integrity check failed')
        target.execute('ATTACH DATABASE ? AS original_desktop', (str(Path(desktop_path).resolve()),))
        target.execute('ATTACH DATABASE ? AS original_source', (str(Path(source_path).resolve()),))
        for database in ('original_desktop', 'original_source'):
            missing = target.execute('''SELECT COUNT(*) FROM ''' + database + '''.t_account_dynamic original
                WHERE original.share_status=1 AND NOT EXISTS (
                    SELECT 1 FROM main.t_account_dynamic merged
                    WHERE merged.account_key=original.account_key
                      AND merged.dynamic_id=original.dynamic_id AND merged.share_status=1)''').fetchone()[0]
            lost_cleanup = target.execute('''SELECT COUNT(*) FROM ''' + database + '''.t_account_dynamic original
                WHERE original.cleanup_status=1 AND NOT EXISTS (
                    SELECT 1 FROM main.t_account_dynamic merged
                    WHERE merged.account_key=original.account_key
                      AND merged.dynamic_id=original.dynamic_id AND merged.cleanup_status=1)''').fetchone()[0]
            if missing or lost_cleanup:
                raise ValueError('Transfer or cleanup history verification failed')
        output.with_suffix('.verification.json').write_text(json.dumps({
            'tables': report, 'integrity_check': 'ok', 'all_shared_and_deleted_states_preserved': True,
        }, indent=2), encoding='utf-8')
        print('Merged database verified; all shared and deleted states preserved')
    finally:
        target.close()
        desktop.close()
        source.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--desktop', required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    merge(args.desktop, args.source, args.output)
