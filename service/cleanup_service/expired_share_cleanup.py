import json
import os
import random
import time
from pathlib import Path

import requests
from datetime import datetime

from dao.init_db import init_db
from service.log_service.log_printer_service import MyLogger
from utils import globals
from utils.runtime_settings import get_cleanup_mode

mylogger = MyLogger('expired_share_cleanup.py').getLogger()


class ExpiredShareCleanup:
    def __init__(self, account_key):
        self.account_key = account_key
        self.db = init_db()
        self.cookie_value, self.bili_jct = self.load_auth_cookies()

    def load_auth_cookies(self):
        cookie_dir = Path(os.getenv("COOKIE_DIR", "/app/cookie"))
        cookie_path = cookie_dir / (str(self.account_key) + ".json")
        cookies = {}
        if cookie_path.is_file():
            try:
                data = json.loads(cookie_path.read_text(encoding="utf-8"))
                cookies = {
                    str(item.get("name")): str(item.get("value"))
                    for item in data
                    if item.get("name") and item.get("value")
                }
            except (OSError, ValueError, TypeError):
                mylogger.warning("读取本地登录 Cookie 失败：%s", cookie_path.name)
        cookie_value = cookies.get("SESSDATA") or globals.cookie_value
        bili_jct = cookies.get("bili_jct") or globals.bili_jct
        if not cookie_value or not bili_jct:
            raise RuntimeError("缺少有效的 SESSDATA 或 bili_jct，请先在管理端完成登录")
        if any(ord(char) > 127 for char in cookie_value + bili_jct):
            raise RuntimeError("Cookie 包含非法字符，请先在管理端重新登录")
        return cookie_value, bili_jct

    def run(self):
        cleanup_mode = get_cleanup_mode()
        if cleanup_mode == "disabled":
            mylogger.info("过期转发清理未启用")
            return {'checked': 0, 'deleted': 0, 'skipped': 0, 'failed': 0, 'unfollowed': 0}
        rows = self.load_expired_rows()
        unfollowed_up_ids = set()
        summary = {
            'checked': len(rows), 'deleted': 0, 'skipped': 0,
            'failed': 0, 'unfollowed': 0,
        }
        for index, row in enumerate(rows):
            if index > 0:
                wait_seconds = random.randint(20, 30)
                mylogger.info(
                    "清理操作限速等待 %.0f 秒（第 %s/%s 条）",
                    wait_seconds, index + 1, len(rows)
                )
                time.sleep(wait_seconds)
            relation_up_ids = self.query_dynamic_up_ids(row['dynamic_id'])
            if not relation_up_ids and row.get('up_id'):
                relation_up_ids = [str(row['up_id'])]
            own_id = row.get('own_dynamic_id')
            if not own_id:
                summary['skipped'] += 1
                mylogger.warning("缺少本人转发动态ID，暂不删除原动态：%s", row.get('dynamic_id'))
                continue
            try:
                if not self.mark_processing(row['id']):
                    summary['skipped'] += 1
                    mylogger.info("清理记录已被其他任务处理，跳过：%s", row['dynamic_id'])
                    continue
                self.remove_dynamic(own_id)
                self.update_deleted(row['id'])
                summary['deleted'] += 1
                if cleanup_mode == "delete_and_unfollow":
                    for up_id in relation_up_ids:
                        if up_id in unfollowed_up_ids:
                            mylogger.info("本批次已完成取关，跳过重复取关 UP：%s", up_id)
                            continue
                        if not self.can_unfollow(up_id):
                            mylogger.info(
                                "当前账号仍有未清理动态，暂不取关 UP：%s", up_id
                            )
                            continue
                        try:
                            self.unfollow(up_id)
                            unfollowed_up_ids.add(up_id)
                            summary['unfollowed'] += 1
                            mylogger.info(
                                "逐条清理完成并已取关 UP：%s（关联动态=%s）",
                                up_id, row['dynamic_id']
                            )
                        except Exception as exc:
                            summary['failed'] += 1
                            mylogger.error("取关 UP 失败 %s: %s", up_id, exc)
            except Exception as exc:
                self.update_failed(row['id'], str(exc))
                summary['failed'] += 1
                mylogger.error("删除本人动态失败 %s: %s", own_id, exc)
        mylogger.info("过期清理完成：%s", summary)
        return summary

    def load_expired_rows(self):
        self.db.cur.execute("""SELECT ad.id, ad.own_dynamic_id, dd.dynamic_id, dd.up_id,
                CASE WHEN dd.lottery_source='explicit'
                     THEN DATE_ADD(dd.lottery_time, INTERVAL 10 DAY)
                     ELSE dd.lottery_time END AS cleanup_at
            FROM t_account_dynamic ad JOIN t_draw_dynamic dd ON dd.dynamic_id = ad.dynamic_id
            WHERE ad.account_key=%s AND ad.share_status=1 AND ad.cleanup_status IN (0, 2)
              AND dd.lottery_time IS NOT NULL
              AND (CASE WHEN dd.lottery_source='explicit'
                        THEN DATE_ADD(dd.lottery_time, INTERVAL 10 DAY)
                        ELSE dd.lottery_time END) <= NOW()""", (self.account_key,))
        return self.db.cur.fetchall()

    def remove_dynamic(self, dynamic_id):
        response = requests.post(
            'https://api.bilibili.com/x/dynamic/feed/operate/remove',
            params={'csrf': self.bili_jct},
            json={'dyn_id_str': str(dynamic_id)},
            headers=self.api_headers(), timeout=20)
        data = self.parse_api_response(response, '删除动态')
        if data.get('code') != 0:
            raise RuntimeError('B站删除接口返回错误码：%s' % data.get('code'))

    def api_headers(self, json_body=True):
        headers = {
            'Cookie': 'SESSDATA=%s; bili_jct=%s' % (
                self.cookie_value, self.bili_jct),
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://www.bilibili.com/',
            'Origin': 'https://www.bilibili.com',
            'Accept': 'application/json, text/plain, */*',
        }
        if json_body:
            headers['Content-Type'] = 'application/json'
        else:
            headers['Content-Type'] = 'application/x-www-form-urlencoded; charset=UTF-8'
        return headers

    @staticmethod
    def parse_api_response(response, operation):
        content_type = response.headers.get('Content-Type', '')
        try:
            return response.json()
        except ValueError:
            body = (response.text or '').strip().replace('\n', ' ')
            mylogger.error(
                '%s接口返回非JSON：status=%s content_type=%s body=%s',
                operation, response.status_code, content_type, body[:500])
            raise RuntimeError('%s接口返回非JSON响应：HTTP %s' %
                               (operation, response.status_code))

    def update_deleted(self, record_id):
        self.db.cur.execute("""UPDATE t_account_dynamic
            SET cleanup_status=1, update_time=%s, error_message=NULL WHERE id=%s""",
                           (datetime.now(), record_id))
        self.db.con.commit()

    def mark_processing(self, record_id):
        self.db.cur.execute("""UPDATE t_account_dynamic
            SET cleanup_status=3, update_time=%s, error_message=NULL
            WHERE id=%s AND cleanup_status IN (0, 2)""",
                           (datetime.now(), record_id))
        self.db.con.commit()
        return self.db.cur.rowcount == 1

    def update_failed(self, record_id, message):
        self.db.cur.execute("""UPDATE t_account_dynamic
            SET cleanup_status=2, update_time=%s, error_message=%s WHERE id=%s""",
                           (datetime.now(), message[:500], record_id))
        self.db.con.commit()

    def can_unfollow(self, up_id):
        self.db.cur.execute("""SELECT COUNT(*) AS c FROM t_draw_dynamic dd
            JOIN t_account_dynamic ad ON ad.dynamic_id=dd.dynamic_id
            WHERE (dd.up_id=%s OR EXISTS
              (SELECT 1 FROM t_draw_dynamic_up ddu
               WHERE ddu.dynamic_id=dd.dynamic_id AND ddu.up_id=%s))
              AND ad.account_key=%s
              AND ad.share_status=1
              AND ad.cleanup_status<>1""",
                           (up_id, up_id, self.account_key))
        return int(self.db.cur.fetchone()['c']) == 0

    def query_dynamic_up_ids(self, dynamic_id):
        self.db.cur.execute(
            'SELECT up_id FROM t_draw_dynamic_up WHERE dynamic_id=%s',
            (str(dynamic_id),)
        )
        return [str(row['up_id']) for row in self.db.cur.fetchall()]

    def unfollow(self, up_id):
        response = requests.post(
            'https://api.bilibili.com/x/relation/modify',
            params={'csrf': self.bili_jct},
            data={'fid': str(up_id), 'act': 2, 're_src': 11,
                  'spmid': '333.999.0.0', 'csrf': self.bili_jct},
            headers=self.api_headers(json_body=False), timeout=20)
        data = self.parse_api_response(response, '取关')
        if data.get('code') != 0:
            raise RuntimeError('B站取关接口返回错误码：%s' % data.get('code'))
