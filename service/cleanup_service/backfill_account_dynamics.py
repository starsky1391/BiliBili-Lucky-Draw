import re
import time
from datetime import datetime

from selenium.common.exceptions import TimeoutException

from dao.account_dynamic_dao import AccountDynamicDao
from dao.draw_dynamic_dao import DrawDynamicDao
from dao.init_db import init_db
from service.log_service.log_printer_service import MyLogger
from service.login_service.login_service import LoginService
from service.share_service.share_one_dynamic import DynamicShareBase
from utils.webdriver_util import init_webdriver

mylogger = MyLogger('backfill_account_dynamics.py').getLogger()


class AccountDynamicBackfill:
    FEED_URL = 'https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space'

    def __init__(self, account_key):
        self.account_key = account_key
        self.db = init_db()
        self.account_dao = AccountDynamicDao(self.db)
        self.draw_dao = DrawDynamicDao(self.db)
        self.bro = None
        self.chains = None

    def run(self):
        self.bro, self.chains = init_webdriver()
        try:
            LoginService(self.bro, self.chains, self.account_key).login_by_cookie()
            items = self.read_personal_forwards()
            summary = {'scanned': len(items), 'matched': 0, 'detail_fallback': 0,
                       'inserted': 0, 'skipped': 0, 'failed': 0}
            for item in items:
                try:
                    source_id = self.get_source_id(item)
                    own_id = str(item.get('id_str') or item.get('id') or '')
                    if not source_id or not own_id:
                        summary['skipped'] += 1
                        continue
                    source_url = 'https://www.bilibili.com/opus/' + source_id
                    row = self.find_dynamic(source_id)
                    api_up_id = self.get_author_id(item)
                    if api_up_id:
                        if row:
                            self.draw_dao.update_up_info(source_id, api_up_id)
                        else:
                            self.draw_dao.ensure_dynamic(
                                source_id, source_url, api_up_id,
                                source='personal_forward_backfill',
                                note='个人转发接口回填'
                            )
                        self.account_dao.ensure_up(api_up_id)
                        row = self.find_dynamic(source_id)
                    if not row:
                        row = self.backfill_detail(source_id, source_url)
                        summary['detail_fallback'] += 1
                    if not row:
                        summary['skipped'] += 1
                        continue
                    self.account_dao.update_own_dynamic(
                        source_id, self.account_key, own_id,
                        'https://www.bilibili.com/opus/' + own_id
                    )
                    summary['matched'] += 1
                except Exception as exc:
                    summary['failed'] += 1
                    mylogger.warning('回填单条动态失败：%s', exc)
            mylogger.info('个人转发动态回填完成：%s', summary)
            return summary
        finally:
            try:
                self.bro.quit()
            except Exception:
                pass

    def sync_new_personal_forwards(self):
        """同步个人页最新转发关系，遇到已同步动态后停止翻页。"""
        self.bro, self.chains = init_webdriver()
        summary = {'scanned': 0, 'updated': 0, 'stopped_at_existing': False}
        try:
            LoginService(self.bro, self.chains, self.account_key).login_by_cookie()
            known_own_ids = self.account_dao.query_own_dynamic_ids(self.account_key)
            host_mid = self.get_current_uid()
            if not host_mid:
                raise RuntimeError('无法取得当前账号UID，不能同步个人动态')
            offset = ''
            while True:
                result = self.read_personal_forwards_page(host_mid, offset)
                page = result.get('items') or result.get('cards') or []
                if not page:
                    break
                for item in page:
                    if item.get('type') != 'DYNAMIC_TYPE_FORWARD' or not item.get('orig'):
                        continue
                    summary['scanned'] += 1
                    own_id = str(item.get('id_str') or item.get('id') or '')
                    if not own_id:
                        continue
                    if own_id in known_own_ids:
                        summary['stopped_at_existing'] = True
                        return summary
                    source_id = self.get_source_id(item)
                    if not source_id:
                        continue
                    api_up_id = self.get_author_id(item)
                    if api_up_id:
                        self.draw_dao.ensure_dynamic(
                            source_id, 'https://www.bilibili.com/opus/' + source_id,
                            api_up_id, source='personal_forward_sync',
                            note='个人转发同步'
                        )
                        self.account_dao.ensure_up(api_up_id)
                    if self.find_dynamic(source_id):
                        self.account_dao.update_own_dynamic(
                            source_id, self.account_key, own_id,
                            'https://www.bilibili.com/opus/' + own_id
                        )
                        known_own_ids.add(own_id)
                        summary['updated'] += 1
                next_offset = str(result.get('offset') or '')
                if not next_offset or next_offset == offset:
                    break
                offset = next_offset
            return summary
        finally:
            try:
                self.bro.quit()
            except Exception:
                pass

    def backfill_status_one(self):
        """历史回填：逐条读取全部 status=1 动态详情，不执行B站写操作。"""
        self.bro, self.chains = init_webdriver()
        summary = {'checked': 0, 'updated': 0, 'failed': 0}
        try:
            LoginService(self.bro, self.chains, self.account_key).login_by_cookie()
            self.db.cur.execute("""SELECT dynamic_id, dyn_url FROM t_draw_dynamic
                WHERE status='1'
                  AND (up_id IS NULL OR up_id=''
                    OR publish_time IS NULL
                    OR lottery_time IS NULL
                    OR lottery_source IS NULL)
                ORDER BY insert_time, dynamic_id""")
            rows = self.db.cur.fetchall()
            summary['checked'] = len(rows)
            for row in rows:
                try:
                    if summary['updated'] + summary['failed'] > 0:
                        time.sleep(30)
                    dynamic_id = str(row.get('dynamic_id') or '')
                    if not dynamic_id.isdigit() or int(dynamic_id) <= 0:
                        summary['failed'] += 1
                        continue
                    self.backfill_detail(dynamic_id, 'https://www.bilibili.com/opus/' + dynamic_id)
                    detail_row = self.find_dynamic(dynamic_id)
                    if detail_row and detail_row.get('up_id'):
                        self.account_dao.ensure_up(detail_row['up_id'])
                    if not detail_row or not detail_row.get('up_id') or not detail_row.get('publish_time') or not detail_row.get('lottery_time'):
                        summary['failed'] += 1
                    else:
                        summary['updated'] += 1
                except Exception as exc:
                    summary['failed'] += 1
                    mylogger.warning('status=1 动态补齐失败 %s：%s', row.get('dynamic_id'), exc)
                finally:
                    self.bro.get('about:blank')
            mylogger.info('status=1 动态元数据回填完成：%s', summary)
            return summary
        finally:
            try:
                self.bro.quit()
            except Exception:
                pass

    def read_personal_forwards(self):
        host_mid = self.get_current_uid()
        if not host_mid:
            raise RuntimeError('无法取得当前账号UID，不能扫描个人动态')
        items = []
        offset = ''
        for _ in range(100):
            result = self.read_personal_forwards_page(host_mid, offset)
            page = result.get('items') or result.get('cards') or []
            if not page:
                break
            for item in page:
                if item.get('type') == 'DYNAMIC_TYPE_FORWARD' and item.get('orig'):
                    items.append(item)
            next_offset = str(result.get('offset') or '')
            if not next_offset or next_offset == offset:
                break
            offset = next_offset
        return items

    def read_personal_forwards_page(self, host_mid, offset):
        result = self.bro.execute_async_script("""
const endpoint = arguments[0];
const hostMid = arguments[1];
const offset = arguments[2];
const done = arguments[3];
const url = endpoint + '?host_mid=' + encodeURIComponent(hostMid) +
  '&offset=' + encodeURIComponent(offset || '') + '&timezone_offset=-480';
fetch(url, {credentials: 'include'}).then(r => r.json()).then(done).catch(() => done({}));
""", self.FEED_URL, host_mid, offset)
        if not isinstance(result, dict) or result.get('code') != 0:
            raise RuntimeError('个人动态接口返回异常')
        data = result.get('data') or {}
        return data if isinstance(data, dict) else {}

    def get_current_uid(self):
        result = self.bro.execute_async_script("""
const done = arguments[0];
fetch('https://api.bilibili.com/x/web-interface/nav', {credentials:'include'})
  .then(r => r.json()).then(data => done(data.data && data.data.mid || null)).catch(() => done(null));
""", )
        return str(result) if result else None

    @staticmethod
    def get_source_id(item):
        orig = item.get('orig') or {}
        value = str(orig.get('id_str') or orig.get('id') or '')
        return value if value.isdigit() and int(value) > 0 else None

    @staticmethod
    def get_author_id(item):
        orig = item.get('orig') or {}
        author = (orig.get('modules') or {}).get('module_author') or {}
        value = str(author.get('mid') or author.get('uid') or '')
        return value if value.isdigit() and int(value) > 0 else None

    def find_dynamic(self, dynamic_id):
        self.db.cur.execute('SELECT * FROM t_draw_dynamic WHERE dynamic_id=%s', (str(dynamic_id),))
        return self.db.cur.fetchone()

    def backfill_detail(self, dynamic_id, source_url):
        try:
            self.bro.get(source_url)
        except TimeoutException:
            mylogger.warning('详情页加载超时，继续读取已加载页面：%s', dynamic_id)
        parser = DynamicShareBase()
        parser.wait_page_ready(self.bro, timeout=20)
        parser.resolve_lottery_time(self.bro)
        parser.get_up_info(self.bro)
        publish_time = parser.publish_time
        lottery_source = 'explicit' if parser.lottery_time and publish_time and parser.lottery_time != publish_time else 'fallback_120d'
        self.draw_dao.ensure_dynamic(
            dynamic_id, source_url, parser.upId, publish_time,
            source='personal_forward_backfill', note='个人转发回填',
            lottery_time=parser.lottery_time, lottery_source=lottery_source
        )
        if parser.upId:
            self.account_dao.ensure_up(parser.upId)
        for up_id in parser.upIds:
            self.draw_dao.ensure_dynamic_up(dynamic_id, up_id)
            self.account_dao.ensure_up(up_id)
        return self.find_dynamic(dynamic_id)
