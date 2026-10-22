from datetime import datetime
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import uuid
import zipfile

from tools.merge_sqlite_databases import merge
from utils import runtime_settings


MAX_ARCHIVE_SIZE = 128 * 1024 * 1024
MAX_EXPANDED_SIZE = 512 * 1024 * 1024


def database_path():
    return Path(os.getenv('SQLITE_PATH', 'luckybili.sqlite3')).resolve()


def backup_root():
    root = database_path().parent / 'backups'
    root.mkdir(exist_ok=True)
    return root


def snapshot(destination):
    source = sqlite3.connect(database_path().as_uri() + '?mode=ro', uri=True)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def summarize(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        db.execute('PRAGMA query_only=ON')
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('数据库完整性检查失败')
        counts = {table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                  for table in ('t_draw_dynamic', 't_account_dynamic', 't_share_info')}
        accounts = [str(row[0]) for row in db.execute('SELECT account_key FROM t_account')
                    if str(row[0]).isdigit()]
        return {'accounts': accounts, 'counts': counts}
    finally:
        db.close()


def export_backup():
    directory = backup_root() / uuid.uuid4().hex
    directory.mkdir()
    db_file = directory / 'database.sqlite3'
    snapshot(db_file)
    settings = runtime_settings._read_settings()
    summary = summarize(db_file)
    manifest = {'format': 'bilibili-lucky-draw', 'version': 1,
                'created_at': datetime.now().isoformat(timespec='seconds'), 'includes_cookies': False}
    archive_path = directory / ('BiliDraw-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.bilidraw')
    with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False))
        archive.writestr('settings.json', json.dumps(settings, ensure_ascii=False))
        archive.write(db_file, 'database.sqlite3')
        logs = database_path().parent / 'Log'
        for file in sorted(logs.rglob('*.log')) if logs.exists() else []:
            archive.write(file, 'logs/' + file.relative_to(logs).as_posix())
    return archive_path, summary


def preview_backup(content):
    if len(content) > MAX_ARCHIVE_SIZE:
        raise ValueError('备份文件不能超过 128 MB')
    token = uuid.uuid4().hex
    directory = backup_root() / ('preview-' + token)
    directory.mkdir()
    archive_path = directory / 'backup.bilidraw'
    archive_path.write_bytes(content)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if len(entries) > 5000 or len(set(names)) != len(names):
                raise ValueError('备份包含过多或重复文件')
            if sum(entry.file_size for entry in entries) > MAX_EXPANDED_SIZE:
                raise ValueError('备份解压后不能超过 512 MB')
            for name in names:
                parts = PurePosixPath(name).parts
                if '\\' in name or ':' in name or '..' in parts or PurePosixPath(name).is_absolute():
                    raise ValueError('备份包含非法路径')
                if name not in ('manifest.json', 'settings.json', 'database.sqlite3') and not (
                    name.startswith('logs/') and name.endswith('.log')
                ):
                    raise ValueError('备份包含不支持的文件')
            manifest = json.loads(archive.read('manifest.json'))
            if not isinstance(manifest, dict) or manifest.get('format') != 'bilibili-lucky-draw' or manifest.get('version') != 1:
                raise ValueError('不支持此备份格式或版本')
            settings = json.loads(archive.read('settings.json'))
            if not isinstance(settings, dict):
                raise ValueError('备份设置格式错误')
            if 'cleanup_mode' in settings and settings['cleanup_mode'] not in runtime_settings.VALID_CLEANUP_MODES:
                raise ValueError('备份清理设置无效')
            if 'max_checks' in settings and (type(settings['max_checks']) is not int or settings['max_checks'] <= 0):
                raise ValueError('备份扫描设置无效')
            with archive.open('database.sqlite3') as source, (directory / 'database.sqlite3').open('xb') as target:
                shutil.copyfileobj(source, target)
            summary = summarize(directory / 'database.sqlite3')
            summary.update({'token': token, 'created_at': manifest.get('created_at'),
                            'log_files': sum(name.startswith('logs/') for name in names),
                            'includes_cookies': False})
            (directory / 'preview.json').write_text(json.dumps(summary), encoding='utf-8')
            return summary
    except (KeyError, zipfile.BadZipFile, json.JSONDecodeError, sqlite3.DatabaseError) as error:
        raise ValueError('备份文件损坏或缺少必要数据') from error


def apply_backup(token):
    if len(token) != 32 or any(char not in '0123456789abcdef' for char in token):
        raise ValueError('无效的备份预览标识')
    directory = backup_root() / ('preview-' + token)
    preview = directory / 'preview.json'
    if not preview.is_file() or (directory / 'applied.json').exists():
        raise ValueError('请重新选择并预览备份文件')
    rollback_archive, _ = export_backup()
    before = rollback_archive.parent / 'database.sqlite3'
    merged = directory / ('merged-' + uuid.uuid4().hex + '.sqlite3')
    merge(before, directory / 'database.sqlite3', merged)
    previous_settings = runtime_settings._read_settings()
    with zipfile.ZipFile(directory / 'backup.bilidraw') as archive:
        incoming_settings = json.loads(archive.read('settings.json'))
        settings = dict(incoming_settings)
        settings.update(previous_settings)
        settings['extra_ups'] = '|'.join(dict.fromkeys(
            runtime_settings.split_ups(previous_settings.get('extra_ups')) +
            runtime_settings.split_ups(incoming_settings.get('extra_ups'))))
        # Keep current sessions; imported accounts need a fresh login without exported Cookies.
        db = sqlite3.connect(merged)
        db.execute('ATTACH DATABASE ? AS before_import', (str(before),))
        db.execute("""UPDATE t_auth_session SET status='LOGIN_REQUIRED', last_verified_at=NULL,
            cookie_saved_at=NULL WHERE account_key NOT IN
            (SELECT account_key FROM before_import.t_auth_session)""")
        db.commit()
        db.close()
        imported_logs = database_path().parent / 'Log' / ('imported-' + token)
        imported_logs.mkdir(parents=True, exist_ok=False)
        try:
            for entry in archive.infolist():
                if entry.filename.startswith('logs/'):
                    relative = PurePosixPath(entry.filename).relative_to('logs')
                    file = imported_logs.joinpath(*relative.parts)
                    file.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(entry) as source, file.open('xb') as destination:
                        shutil.copyfileobj(source, destination)
                    timestamp = datetime(*entry.date_time).timestamp()
                    os.utime(file, (timestamp, timestamp))
            _replace_database(merged)
            runtime_settings._write_settings(settings)
            summary = summarize(database_path())
        except Exception:
            _replace_database(before)
            runtime_settings._write_settings(previous_settings)
            shutil.rmtree(imported_logs)
            raise
    summary['rollback_file'] = rollback_archive.name
    summary['rollback_path'] = str(rollback_archive)
    (directory / 'applied.json').write_text(json.dumps(summary), encoding='utf-8')
    return summary


def _replace_database(source_path):
    source = sqlite3.connect(Path(source_path).resolve().as_uri() + '?mode=ro', uri=True)
    target = sqlite3.connect(database_path(), timeout=10)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
