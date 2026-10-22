import gc
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.previous_cwd = Path.cwd()
        cls.previous_env = os.environ.copy()
        cls.data = Path(cls.temporary.name)
        os.chdir(cls.data)
        (cls.data / "Log").mkdir()
        (cls.data / "cookie").mkdir()
        os.environ["COOKIE_DIR"] = str(cls.data / "cookie")
        os.environ["RUNTIME_SETTINGS_PATH"] = str(cls.data / "runtime_settings.json")
        os.environ["SQLITE_PATH"] = str(cls.data / "test.sqlite3")
        from dao.init_db import initialize_database
        initialize_database()

    @classmethod
    def tearDownClass(cls):
        import logging
        logging.shutdown()
        gc.collect()
        os.chdir(cls.previous_cwd)
        os.environ.clear()
        os.environ.update(cls.previous_env)
        cls.temporary.cleanup()

    def setUp(self):
        from dao.init_db import init_db
        self.db = init_db()
        for row in self.db.executeSql("SELECT name FROM sqlite_master WHERE type='table'"):
            self.db.executeCommit('DELETE FROM "%s"' % row['name'])

    def tearDown(self):
        self.db.close()
        for path in (self.data / "cookie").glob("*.json"):
            path.unlink()

    def test_account_quotes_and_auth_upsert(self):
        from dao.account_dao import AccountDao
        from dao.auth_session_dao import AuthSessionDao
        accounts = AccountDao(self.db)
        accounts.ensure("123", username="测试'用户")
        self.assertEqual(accounts.get("123")["username"], "测试'用户")
        auth = AuthSessionDao(self.db)
        auth.ensure_table()
        auth.upsert("123", "AUTHENTICATED", uid="123", verified=True, cookie_saved=True)
        verified_at = auth.get("123")["last_verified_at"]
        auth.upsert("123", "LOGIN_REQUIRED", error="expired ' cookie")
        row = auth.get("123")
        self.assertEqual(row["uid"], "123")
        self.assertEqual(row["last_verified_at"], verified_at)
        self.assertIsInstance(verified_at, datetime)

    def test_dynamic_and_relationship_upserts(self):
        from dao.draw_dynamic_dao import DrawDynamicDao
        from dao.account_dynamic_dao import AccountDynamicDao
        draws = DrawDynamicDao(self.db)
        self.assertTrue(draws.insert("https://www.bilibili.com/opus/12345", "source", "note"))
        self.assertTrue(draws.insert("https://www.bilibili.com/opus/12345", "updated", "note"))
        draws.ensure_dynamic("12345", "https://www.bilibili.com/opus/12345", up_id="99")
        draws.ensure_dynamic_up("12345", "99")
        draws.ensure_dynamic_up("12345", "99")
        self.assertEqual(draws.query_dynamic_up_ids("12345"), ["99"])
        dao = AccountDynamicDao(self.db)
        dao.ensure_up("99")
        dao.ensure_up("99")
        dao.upsert_shared("12345", "123")
        dao.upsert_shared("12345", "123")
        dao.update_own_dynamic("12345", "123", "56789", "https://t.bilibili.com/56789")
        self.assertEqual(dao.query_own_dynamic_ids("123"), {"56789"})
        self.assertEqual(len(self.db.executeSql("SELECT * FROM t_account_dynamic")), 1)

    def test_scan_cache_datetime_and_upsert(self):
        from dao.scan_cache_dao import ScanCacheDao
        cache = ScanCacheDao(self.db)
        cache.save_failure("up", "https://example.invalid", "source", "note", "failed")
        self.assertTrue(cache.has_recent_failure("up", "https://example.invalid", 24))
        cache.save_success("up", "https://example.invalid", "source", "note", 3)
        self.assertTrue(cache.has_success("up", "https://example.invalid"))
        self.assertFalse(cache.has_recent_failure("up", "https://example.invalid", 24))

    def test_pending_unfollow_claim_and_retry(self):
        from dao.pending_unfollow_dao import PendingUnfollowDao
        dao = PendingUnfollowDao(self.db)
        dao.enqueue("123", "99", "12345", "failed")
        dao.enqueue("123", "99", "12345", "retry")
        rows = dao.query_pending("123")
        self.assertEqual(len(rows), 1)
        record_id = rows[0]["id"]
        self.assertTrue(dao.mark_processing(record_id))
        self.assertFalse(dao.mark_processing(record_id))
        dao.mark_failed(record_id, "failed again")
        dao.delete(record_id)
        self.assertEqual(dao.query_pending("123"), [])

    def test_cookie_uid_migration_preserves_existing_relationships(self):
        from dao.account_dynamic_dao import AccountDynamicDao
        from service.account_service import ensure_cookie_accounts
        dao = AccountDynamicDao(self.db)
        dao.upsert_shared("1", "legacy")
        dao.upsert_shared("2", "legacy")
        dao.upsert_shared("2", "123")
        (self.data / "cookie" / "legacy.json").write_text(json.dumps([
            {"name": "DedeUserID", "value": "123"},
        ]), encoding="utf-8")
        ensure_cookie_accounts()
        rows = self.db.executeSql("SELECT * FROM t_account_dynamic WHERE account_key='123'")
        self.assertEqual({row["dynamic_id"] for row in rows}, {"1", "2"})

    def test_cleanup_grace_period(self):
        from dao.draw_dynamic_dao import DrawDynamicDao
        from dao.account_dynamic_dao import AccountDynamicDao
        from service.cleanup_service.expired_share_cleanup import ExpiredShareCleanup
        draws = DrawDynamicDao(self.db)
        accounts = AccountDynamicDao(self.db)
        for dynamic_id, days, source in (("1", 11, "explicit"), ("2", 9, "explicit"),
                                         ("3", 1, "inferred")):
            draws.ensure_dynamic(dynamic_id, "https://t.bilibili.com/" + dynamic_id,
                                 lottery_time=datetime.now() - timedelta(days=days), lottery_source=source)
            accounts.update_own_dynamic(dynamic_id, "123", "own" + dynamic_id, "https://t.bilibili.com/own")
        cleanup = ExpiredShareCleanup.__new__(ExpiredShareCleanup)
        cleanup.db = self.db
        cleanup.account_key = "123"
        self.assertEqual({row["dynamic_id"] for row in cleanup.load_expired_rows()}, {"1", "3"})

    def test_parallel_database_connections(self):
        from dao.init_db import init_db

        def write(index):
            db = init_db()
            try:
                db.insert("t_statistics", {"content": str(index), "insert_time": datetime.now()})
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(write, range(20)))
        self.assertEqual(self.db.executeSql("SELECT COUNT(*) AS n FROM t_statistics")[0]["n"], 20)

    def test_ignore_links_without_configuration(self):
        import subprocess
        environment = os.environ.copy()
        environment.pop("ignore_link", None)
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        result = subprocess.run([sys.executable, "-c", """
from utils import globals
from service.search_draw_dynamic_service.SearchDynamicByUps import SearchDynamicByUps
from service.share_service.share_from_biliLink import BiliLinkShare
assert globals.ignore_link == ''
for service in (SearchDynamicByUps, BiliLinkShare):
    assert not service.get_ignore_link(None)
globals.ignore_link = 'https://example.invalid/1|https://example.invalid/2'
for service in (SearchDynamicByUps, BiliLinkShare):
    assert service.get_ignore_link(None) == globals.ignore_link.split('|')
"""], env=environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_local_ui_and_api(self):
        from fastapi.testclient import TestClient
        from admin_web.main import app
        with TestClient(app) as client:
            for route in ("/", "/assets/app.js", "/assets/style.css", "/api/users",
                          "/api/auth/status", "/api/overview", "/api/dynamics/failures",
                          "/api/dynamics/expired", "/api/logs", "/api/settings/cleanup",
                          "/api/settings/ups", "/api/settings/max-checks"):
                with self.subTest(route=route):
                    response = client.get(route)
                    self.assertEqual(response.status_code, 200, response.text[:300])

    def test_account_permissions_apply_to_scheduled_and_manual_tasks(self):
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        from dao.account_dao import AccountDao
        from service.task_control import get_account_tasks, set_account_tasks
        import main
        from admin_web.main import app
        accounts = AccountDao(self.db)
        for uid in ('123', '456'):
            accounts.ensure(uid)
            (self.data / 'cookie' / (uid + '.json')).write_text('[]', encoding='utf-8')
        self.assertEqual(get_account_tasks('123'), {'collect': True, 'backfill_personal': False, 'cleanup': True})
        set_account_tasks('123', {'collect': False, 'backfill_personal': True, 'cleanup': False})
        with patch.object(main, 'SearchDynamicByUps') as search, patch.object(main, 'MultiUsersShareService') as share:
            main.collect_and_share()
            search.assert_called_once_with('456')
            share.return_value.do_multi_uses_share.assert_called_once_with(['456'])
        with patch.object(main, 'is_authenticated', return_value=True), \
             patch.object(main, 'AccountDynamicBackfill') as sync, patch.object(main, 'ExpiredShareCleanup') as cleanup:
            main.cleanup_expired()
            cleanup.assert_called_once_with('456')
            sync.reset_mock()
            main.backfill_personal_forwards()
            sync.assert_called_once_with('123')
        with TestClient(app) as client:
            response = client.post('/api/tasks/manual/collect', json={'user_ids': ['123']})
            self.assertFalse(response.json()['started'])
            self.assertFalse(client.post('/api/tasks/manual/backfill_personal', json={'user_ids': ['456']}).json()['started'])
            self.assertFalse(client.post('/api/dynamics/retry-failures', json={'user_ids': ['123']}).json()['started'])
            self.assertFalse(client.post('/api/dynamics/1/reidentify', json={'user_id': '123'}).json()['started'])
            tasks = {'collect': False, 'backfill_personal': False, 'cleanup': False}
            self.assertEqual(client.put('/api/users/456/tasks', json=tasks).status_code, 200)
            self.assertEqual(client.put('/api/users/456/tasks', json={'collect': 'yes'}).status_code, 400)
            self.assertEqual(client.get('/api/users').json()['items'][1]['tasks'], tasks)

    def test_backup_preview_merge_and_no_cookie_export(self):
        import io
        import zipfile
        from fastapi.testclient import TestClient
        from dao.account_dao import AccountDao
        from dao.account_dynamic_dao import AccountDynamicDao
        from dao.auth_session_dao import AuthSessionDao
        from dao.draw_dynamic_dao import DrawDynamicDao
        from service.task_control import get_account_tasks, set_account_tasks
        from utils import runtime_settings
        from admin_web.main import app
        accounts = AccountDao(self.db)
        accounts.ensure('123')
        accounts.ensure('789')
        auth = AuthSessionDao(self.db)
        auth.ensure_table()
        auth.upsert('789', 'AUTHENTICATED', verified=True)
        relations = AccountDynamicDao(self.db)
        relations.upsert_shared('1', '123')
        relations.update_own_dynamic('1', '123', 'own1', 'https://t.bilibili.com/own1')
        DrawDynamicDao(self.db).ensure_dynamic('1', 'https://www.bilibili.com/opus/1')
        set_account_tasks('123', {'collect': True, 'backfill_personal': False, 'cleanup': True})
        runtime_settings._write_settings({'max_checks': 100, 'extra_ups': 'old-UP'})
        (self.data / 'Log' / 'sample.log').write_text('historical log', encoding='utf-8')
        (self.data / 'cookie' / '123.json').write_text('[{"name":"SESSDATA","value":"SECRET-TEST-COOKIE"}]', encoding='utf-8')
        with TestClient(app) as client:
            response = client.post('/api/backups/export')
            self.assertEqual(response.status_code, 200, response.text if response.status_code != 200 else '')
            content = response.content
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                self.assertIn('database.sqlite3', archive.namelist())
                self.assertIn('logs/sample.log', archive.namelist())
                self.assertFalse(any('cookie' in name.lower() for name in archive.namelist()))
                self.assertFalse(any(b'SECRET-TEST-COOKIE' in archive.read(name) for name in archive.namelist()))
            preview = client.post('/api/backups/preview', content=content)
            self.assertEqual(preview.status_code, 200)
            token = preview.json()['token']
            self.db.executeCommit("DELETE FROM t_account WHERE account_key='789'")
            self.db.executeCommit("DELETE FROM t_auth_session WHERE account_key='789'")
            self.db.executeCommit("UPDATE t_account_dynamic SET cleanup_status=1 WHERE dynamic_id='1'")
            accounts.ensure('456')
            relations.upsert_shared('2', '456')
            set_account_tasks('123', {'collect': False, 'backfill_personal': True, 'cleanup': False})
            runtime_settings._write_settings({'max_checks': 999, 'extra_ups': 'new-UP'})
            imported = client.post('/api/backups/import', json={'token': token})
            self.assertEqual(imported.status_code, 200, imported.text)
            self.assertEqual(imported.json()['counts']['t_account_dynamic'], 2)
            self.assertTrue(Path(imported.json()['rollback_path']).is_file())
            self.assertEqual(get_account_tasks('123')['collect'], False)
            self.assertEqual(self.db.executeSql("SELECT cleanup_status FROM t_account_dynamic WHERE dynamic_id='1'")[0]['cleanup_status'], 1)
            self.assertEqual(auth.get('789')['status'], 'LOGIN_REQUIRED')
            self.assertEqual(runtime_settings.get_max_checks(), 999)
            self.assertEqual(set(runtime_settings.get_extra_ups()), {'old-UP', 'new-UP'})
            self.assertTrue(list((self.data / 'Log').glob('imported-*/sample.log')))
            self.assertEqual(client.post('/api/backups/import', json={'token': token}).status_code, 400)

    def test_backup_busy_and_invalid_archive(self):
        import io
        import zipfile
        from fastapi.testclient import TestClient
        from admin_web.main import app
        from service.task_control import task_lock
        with TestClient(app) as client:
            task_lock.acquire()
            try:
                self.assertEqual(client.post('/api/backups/export').status_code, 409)
                self.assertEqual(client.post('/api/backups/import', json={'token': '0' * 32}).status_code, 409)
            finally:
                task_lock.release()
            self.assertEqual(client.post('/api/backups/preview', content=b'not a zip').status_code, 400)
            data = io.BytesIO()
            with zipfile.ZipFile(data, 'w') as archive:
                archive.writestr('../escape.log', 'bad')
            self.assertEqual(client.post('/api/backups/preview', content=data.getvalue()).status_code, 400)

    def test_failed_import_restores_current_database(self):
        from unittest.mock import patch
        from dao.account_dao import AccountDao
        from dao.account_dynamic_dao import AccountDynamicDao
        from service import backup_service
        from utils import runtime_settings
        AccountDao(self.db).ensure('123')
        relations = AccountDynamicDao(self.db)
        relations.upsert_shared('source-only', '123')
        archive, _ = backup_service.export_backup()
        preview = backup_service.preview_backup(archive.read_bytes())
        self.db.executeCommit('DELETE FROM t_account_dynamic')
        relations.upsert_shared('current-only', '123')
        with patch.object(runtime_settings, '_write_settings', side_effect=[OSError('simulated write failure'), None]):
            with self.assertRaises(OSError):
                backup_service.apply_backup(preview['token'])
        rows = self.db.executeSql('SELECT dynamic_id FROM t_account_dynamic')
        self.assertEqual([row['dynamic_id'] for row in rows], ['current-only'])
        self.assertFalse((backup_service.backup_root() / ('preview-' + preview['token']) / 'applied.json').exists())


if __name__ == "__main__":
    unittest.main()
