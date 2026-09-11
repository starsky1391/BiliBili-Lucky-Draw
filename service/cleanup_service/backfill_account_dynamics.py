import re
import random
import time
from datetime import datetime

from selenium.common.exceptions import InvalidSessionIdException, TimeoutException
from selenium.webdriver.common.by import By

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

    def reset_browser_session(self):
        old_browser = self.bro
        self.bro = None
        self.chains = None
        if old_browser is not None:
            try:
                old_browser.quit()
            except Exception:
                pass
        self.bro, self.chains = init_webdriver()
        LoginService(self.bro, self.chains, self.account_key).login_by_cookie()
        mylogger.info('Selenium会话已重建，已使用现有Cookie自动登录')

    @staticmethod
    def is_session_lost_error(exc):
        return isinstance(exc, InvalidSessionIdException) or 'unable to find session with id' in str(exc).lower()

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
                        wait_seconds = random.randint(20, 30)
                        mylogger.info(
                            '历史回填限速等待 %.0f 秒（第 %s/%s 条）',
                            wait_seconds,
                            summary['updated'] + summary['failed'] + 1,
                            summary['checked']
                        )
                        time.sleep(wait_seconds)
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
                    if self.is_session_lost_error(exc):
                        try:
                            mylogger.warning(
                                '历史回填检测到Selenium会话失效，重建会话后重试当前动态：%s',
                                dynamic_id
                            )
                            self.reset_browser_session()
                            self.backfill_detail(
                                dynamic_id,
                                'https://www.bilibili.com/opus/' + dynamic_id
                            )
                            detail_row = self.find_dynamic(dynamic_id)
                            if detail_row and detail_row.get('up_id'):
                                self.account_dao.ensure_up(detail_row['up_id'])
                            if (
                                not detail_row
                                or not detail_row.get('up_id')
                                or not detail_row.get('publish_time')
                                or not detail_row.get('lottery_time')
                            ):
                                summary['failed'] += 1
                            else:
                                summary['updated'] += 1
                            continue
                        except Exception as retry_exc:
                            exc = retry_exc
                    summary['failed'] += 1
                    mylogger.warning('status=1 动态补齐失败 %s：%s', row.get('dynamic_id'), exc)
                finally:
                    try:
                        self.bro.get('about:blank')
                    except Exception:
                        pass
            mylogger.info('status=1 动态元数据回填完成：%s', summary)
            return summary
        finally:
            try:
                self.bro.quit()
            except Exception:
                pass

    def reidentify_expired_dynamics(self, limit=100):
        """重新识别最早入库的过期动态，只更新元数据和状态。"""
        self.bro, self.chains = init_webdriver()
        summary = {
            'checked': 0,
            'updated': 0,
            'still_expired': 0,
            'failed': 0,
            'remaining': 0,
        }
        try:
            LoginService(self.bro, self.chains, self.account_key).login_by_cookie()
            self.db.cur.execute("""
                SELECT dynamic_id, dyn_url
                FROM t_draw_dynamic
                WHERE status='2'
                ORDER BY insert_time ASC, dynamic_id ASC
                LIMIT %s
            """, (int(limit),))
            rows = self.db.cur.fetchall()
            summary['checked'] = len(rows)
            for index, row in enumerate(rows):
                if index > 0:
                    wait_seconds = random.randint(20, 30)
                    mylogger.info(
                        '过期动态重新识别限速等待 %.0f 秒（第 %s/%s 条）',
                        wait_seconds, index + 1, len(rows)
                    )
                    time.sleep(wait_seconds)
                dynamic_id = str(row.get('dynamic_id') or '')
                dyn_url = str(row.get('dyn_url') or '')
                try:
                    if not dynamic_id.isdigit() or not dyn_url:
                        raise RuntimeError('动态链接或动态ID无效')
                    result = self.reidentify_one_readonly(dynamic_id, dyn_url)
                    if result['status'] == '0':
                        summary['updated'] += 1
                        mylogger.info(
                            '过期动态重新识别后恢复待转发：%s，开奖时间=%s',
                            dynamic_id, result['lottery_time']
                        )
                    else:
                        summary['still_expired'] += 1
                        mylogger.info(
                            '过期动态重新识别确认已过期：%s，开奖时间=%s',
                            dynamic_id, result['lottery_time']
                        )
                except Exception as exc:
                    summary['failed'] += 1
                    mylogger.warning(
                        '过期动态重新识别失败 %s：%s',
                        dynamic_id or dyn_url, exc
                    )
                finally:
                    try:
                        self.bro.get('about:blank')
                    except Exception:
                        pass
            self.db.cur.execute(
                "SELECT COUNT(*) AS c FROM t_draw_dynamic WHERE status='2'"
            )
            summary['remaining'] = int(self.db.cur.fetchone()['c'])
            mylogger.info('过期动态重新识别完成：%s', summary)
            return summary
        finally:
            try:
                self.bro.quit()
            except Exception:
                pass

    def reidentify_one_readonly(self, dynamic_id, dyn_url=None):
        """只读取详情并修正状态，不执行任何 B 站写操作。"""
        dynamic_id = str(dynamic_id or '')
        dyn_url = str(dyn_url or '')
        if not dynamic_id.isdigit():
            raise RuntimeError('动态ID无效')
        source_url = (
            dyn_url if '/opus/' in dyn_url
            else 'https://www.bilibili.com/opus/' + dynamic_id
        )
        retry_session = False
        while True:
            try:
                self.backfill_detail(dynamic_id, source_url)
                break
            except Exception as exc:
                if self.is_session_lost_error(exc) and not retry_session:
                    retry_session = True
                    mylogger.warning(
                        '动态重新识别检测到会话失效，重建会话后重试：%s',
                        dynamic_id
                    )
                    self.reset_browser_session()
                    continue
                raise
        detail_row = self.find_dynamic(dynamic_id)
        lottery_time = detail_row.get('lottery_time') if detail_row else None
        publish_time = detail_row.get('publish_time') if detail_row else None
        lottery_source = detail_row.get('lottery_source') if detail_row else None
        if not detail_row or not lottery_time or not publish_time:
            raise RuntimeError('未完整识别动态发布时间或开奖时间')
        new_status = '0' if datetime.now() < lottery_time else '2'
        self.db.cur.execute(
            """UPDATE t_draw_dynamic
               SET status=%s, note=%s, lottery_source=%s
               WHERE dynamic_id=%s""",
            (new_status, '动态重新识别成功', lottery_source, dynamic_id)
        )
        self.db.con.commit()
        return {
            'dynamic_id': dynamic_id,
            'status': new_status,
            'lottery_time': lottery_time,
            'publish_time': publish_time,
        }

    def reidentify_single_expired_dynamic(self, dynamic_id, dyn_url=None):
        """后台单条重新识别入口，负责创建和关闭浏览器会话。"""
        self.bro, self.chains = init_webdriver()
        try:
            LoginService(self.bro, self.chains, self.account_key).login_by_cookie()
            return self.reidentify_one_readonly(dynamic_id, dyn_url)
        finally:
            try:
                self.bro.quit()
            except Exception:
                pass

    def retry_failed_dynamics(self):
        """限速重新识别失败动态；遇到风控立即停止本轮。"""
        self.bro, self.chains = init_webdriver()
        summary = {
            'checked': 0,
            'updated': 0,
            'failed': 0,
            'processed': 0,
            'remaining': 0,
            'paused': False,
            'stop_reason': None,
        }
        try:
            LoginService(self.bro, self.chains, self.account_key).login_by_cookie()
            self.db.cur.execute("""
                SELECT dynamic_id, dyn_url
                FROM t_draw_dynamic
                WHERE status='3'
                ORDER BY insert_time, dynamic_id
            """)
            rows = self.db.cur.fetchall()
            summary['checked'] = len(rows)
            for index, row in enumerate(rows):
                summary['processed'] = index + 1
                if index > 0:
                    wait_seconds = random.randint(20, 30)
                    mylogger.info(
                        '重新识别限速等待 %.0f 秒（第 %s/%s 条）',
                        wait_seconds, index + 1, len(rows)
                    )
                    time.sleep(wait_seconds)
                dynamic_id = str(row.get('dynamic_id') or '')
                dyn_url = str(row.get('dyn_url') or '')
                try:
                    if not dynamic_id.isdigit() or not dyn_url:
                        raise RuntimeError('动态链接或动态ID无效')
                    source_url = (
                        dyn_url if '/opus/' in dyn_url
                        else 'https://www.bilibili.com/opus/' + dynamic_id
                    )
                    risk_retry = False
                    while True:
                        try:
                            self.backfill_detail(dynamic_id, source_url)
                            break
                        except Exception as exc:
                            if self.is_session_lost_error(exc):
                                mylogger.warning(
                                    '重新识别检测到Selenium会话失效，重建会话后重试当前动态：%s',
                                    dynamic_id
                                )
                                self.reset_browser_session()
                                continue
                            if not self.is_risk_control_error(exc) or risk_retry:
                                raise
                            risk_retry = True
                            mylogger.error(
                                '重新识别首次确认 B 站风控，暂停 30 秒后重试当前动态：%s',
                                dynamic_id
                            )
                            time.sleep(30)
                    detail_row = self.find_dynamic(dynamic_id)
                    if not detail_row or not detail_row.get('up_id'):
                        raise RuntimeError('未识别到UP信息')
                    self.db.cur.execute(
                        "UPDATE t_draw_dynamic SET status='1', note=%s WHERE dynamic_id=%s",
                        ('失败动态重新识别成功', dynamic_id)
                    )
                    self.db.con.commit()
                    summary['updated'] += 1
                except Exception as exc:
                    summary['failed'] += 1
                    if self.is_risk_control_error(exc):
                        summary['paused'] = True
                        summary['stop_reason'] = 'B站安全风控（错误号 412）'
                        summary['remaining'] = len(rows) - index - 1
                        mylogger.error(
                            '重新识别检测到 B 站风控，立即停止本轮：错误号 412，'
                            '当前动态=%s，剩余=%s',
                            dynamic_id or dyn_url,
                            summary['remaining']
                        )
                        break
                    mylogger.warning(
                        '重新识别失败动态失败 %s：%s',
                        dynamic_id or dyn_url,
                        exc
                    )
                finally:
                    try:
                        self.bro.get('about:blank')
                    except Exception:
                        pass
                summary['processed'] = index + 1
            if not summary['paused']:
                summary['remaining'] = max(
                    summary['checked'] - summary['processed'], 0
                )
            mylogger.info('失败动态重新识别完成：%s', summary)
            return summary
        finally:
            try:
                self.bro.quit()
            except Exception:
                pass

    def is_risk_control_error(self, exc):
        """识别 B 站 412 风控页或同类拒绝响应。"""
        error_text = str(exc)
        evidence = self.get_risk_control_evidence(error_text)
        if evidence:
            mylogger.error('重新识别确认 B 站风控特征：%s', evidence)
            return True
        try:
            title = self.bro.title or ''
            body_text = self.bro.find_element(By.TAG_NAME, 'body').text or ''
            evidence = self.get_risk_control_evidence(title + '\n' + body_text)
            if evidence:
                mylogger.error('重新识别确认 B 站风控特征：%s', evidence)
            return bool(evidence)
        except Exception:
            return False

    @staticmethod
    def get_risk_control_evidence(text):
        normalized = re.sub(r'\s+', ' ', str(text or '')).strip().lower()
        patterns = (
            ('错误号: 412', r'错误号\s*[:：]\s*412'),
            ('错误号 412', r'错误号\s+412'),
            ('安全风控策略', r'触发哔哩哔哩安全风控策略'),
            ('请求被拒绝', r'该次访问请求被拒绝'),
            ('security control policy', r'security control policy'),
            ('request was rejected', r'request was rejected'),
        )
        for label, pattern in patterns:
            if re.search(pattern, normalized):
                return label
        return None

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
