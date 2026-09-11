import re
import random
import time
from datetime import datetime

from dao.draw_dynamic_dao import DrawDynamicDao
from dao.follow_up_dao import FollowUpInfoDao
from dao.init_db import init_db
from dao.share_info_dao import ShareInfoDao
from dao.account_dynamic_dao import AccountDynamicDao
from dao.statistics_dao import StatisticsDao
from service.log_service.log_printer_service import MyLogger
from service.login_service.login_service import LoginService
from service.notify_service.notify_service import NotifyService
from service.remove_msg import RemoveMsgService
from service.share_service.share_one_dynamic import DynamicShareBase
from utils import globals
from utils.globals import get_random_comment_content, get_random_share_content
from utils.ip_util import remove_query_string
from utils.webdriver_util import init_webdriver
from service.auth_service import require_authenticated

mylogger = MyLogger('share_from_biliLick.py').getLogger()


class BiliLinkShare(object):
    def __init__(self, user_id, bro=None, chains=None):
        db = init_db()
        self.share_note = ""
        self.user_id = user_id
        self.owns_browser = bro is None
        self.bro = bro
        self.chains = chains
        self.share_info_dao = ShareInfoDao(db)
        self.follow_up_dao = FollowUpInfoDao(db)
        self.draw_dynamic_dao = DrawDynamicDao(db)
        self.statistics_dao = StatisticsDao(db)
        self.account_dynamic_dao = AccountDynamicDao(db)
        mylogger.error("启动：根据B站up主的分享链接进行抽奖动态转发!")

    def do_share_by_links(self):
        require_authenticated()
        do_share_cnt = 0
        success_share_cnt = 0
        expired_cnt = 0
        failed_cnt = 0
        break_flag = 0
        processed_since_restart = 0
        consecutive_errors = 0
        processed_share_attempts = 0
        try:
            self.login_browser()
            datas = self.get_pending_dynamic_links()
            ignore_links = self.get_ignore_link()
            for data in datas:
                lucky_dynamic_url = remove_query_string(data['dyn_url'])
                if not self.is_valid_dynamic_url(lucky_dynamic_url):
                    mylogger.warning("无效动态URL，标记跳过：" + str(lucky_dynamic_url))
                    self.draw_dynamic_dao.update_sharedUrl(url=lucky_dynamic_url, status=2)
                    continue
                # lucky_dynamic_url = 'https://www.bilibili.com/opus/886341319897645089'
                for ign_lnk in ignore_links:
                    if ign_lnk in lucky_dynamic_url:
                        break_flag = 1
                # 跳过已经转发过的
                if break_flag == 1:
                    break_flag = 0
                    continue
                break_flag = 0
                shared = self.share_info_dao.query_shareInfo_by_shareUrl(lucky_dynamic_url, self.user_id)
                if len(shared) != 0:
                    self.draw_dynamic_dao.update_sharedUrl(url=lucky_dynamic_url, status=1)
                    continue
                do_share_cnt = do_share_cnt + 1
                if processed_share_attempts > 0:
                    wait_seconds = random.randint(20, 30)
                    mylogger.info(
                        "正常转发限速等待 %.0f 秒（准备处理第 %s 条）",
                        wait_seconds, processed_share_attempts + 1
                    )
                    time.sleep(wait_seconds)
                risk_retry = False
                while True:
                    dyn = DynamicShareBase()
                    dyn.user_id = self.user_id
                    dyn.share_one(
                        self.bro,
                        self.chains,
                        lucky_dynamic_url,
                        get_random_share_content(),
                        get_random_comment_content()
                    )
                    evidence = self.get_risk_control_evidence(dyn.last_error)
                    if not evidence or risk_retry:
                        break
                    risk_retry = True
                    mylogger.error(
                        "正常转发首次确认 B 站风控特征：%s，暂停 30 秒后重试当前动态：%s",
                        evidence,
                        lucky_dynamic_url
                    )
                    time.sleep(30)
                processed_share_attempts = processed_share_attempts + 1
                if dyn.lottery_time is not None:
                    self.draw_dynamic_dao.update_lottery_metadata(
                        lucky_dynamic_url,
                        dyn.lottery_time,
                        dyn.lottery_source
                    )
                # 保存转发状态和关注的up主信息
                if dyn.share_status == 0:
                    self.draw_dynamic_dao.update_sharedUrl(url=lucky_dynamic_url, status=1)
                    self.share_info_dao.insert_shareInfo(dyn)
                    dynamic_id = self.extract_dynamic_id(lucky_dynamic_url)
                    if dynamic_id:
                        self.account_dynamic_dao.upsert_shared(
                            dynamic_id, self.user_id, dyn.share_time
                        )
                        for up_id, up_url in zip(dyn.upIds, dyn.upUrls):
                            self.draw_dynamic_dao.ensure_dynamic_up(dynamic_id, up_id)
                            self.account_dynamic_dao.ensure_up(up_id)
                            self.follow_up_dao.saverUpdate(up_id, up_url, self.user_id)
                    success_share_cnt = success_share_cnt + 1
                    consecutive_errors = 0
                elif dyn.status == 2:
                    self.draw_dynamic_dao.update_sharedUrl(url=lucky_dynamic_url, status=2)
                    expired_cnt = expired_cnt + 1
                    consecutive_errors = 0
                else:
                    failed_cnt = failed_cnt + 1
                    consecutive_errors = consecutive_errors + 1
                    self.draw_dynamic_dao.update_sharedUrl(url=lucky_dynamic_url, status=3)
                    evidence = self.get_risk_control_evidence(dyn.last_error)
                    if evidence:
                        mylogger.error(
                            "正常转发确认 B 站风控特征：%s，立即停止本轮，当前动态=%s",
                            evidence, lucky_dynamic_url
                        )
                        raise RuntimeError("B站安全风控（错误号 412）")

                processed_since_restart = processed_since_restart + 1
                self.cleanup_browser_page()
                if consecutive_errors >= globals.share_restart_error_limit:
                    self.recycle_browser("连续失败%s条" % consecutive_errors)
                    processed_since_restart = 0
                    consecutive_errors = 0
                elif processed_since_restart >= globals.share_browser_recycle_every:
                    self.recycle_browser("已处理%s条动态" % processed_since_restart)
                    processed_since_restart = 0
        except Exception as e:
            mylogger.error("[do_share_by_links 根据url转发动态 出错 %s]" % e, exc_info=True)
        finally:
            if do_share_cnt == 0:
                percentage = 0
            else:
                percentage = (success_share_cnt / do_share_cnt) * 100
            succ_percentage = f"成功率为 : {percentage:.2f}%"
            content = ("成功的转发条数为：" + str(success_share_cnt)
                       + ";过期跳过条数为：" + str(expired_cnt)
                       + ";失败条数为：" + str(failed_cnt)
                       + ";" + succ_percentage)
            if 50 > percentage > 0:
                NotifyService().fangtang_msg_push_by_content(title="程序预警，需要处理！", content=content)
            self.statistics_dao.insert(self.user_id, content, "")
            # 暂时停止移除
            # RemoveMsgService(self.user_id, success_share_cnt, bro=self.bro, chains=self.chains).do_remove()
            if self.owns_browser:
                self.close_browser()

    @staticmethod
    def get_risk_control_evidence(error):
        normalized = re.sub(r"\s+", " ", str(error or "")).strip().lower()
        patterns = (
            ("错误号: 412", r"错误号\s*[:：]\s*412"),
            ("错误号 412", r"错误号\s+412"),
            ("安全风控策略", r"触发哔哩哔哩安全风控策略"),
            ("请求被拒绝", r"该次访问请求被拒绝"),
            ("security control policy", r"security control policy"),
            ("request was rejected", r"request was rejected"),
        )
        for label, pattern in patterns:
            if re.search(pattern, normalized):
                return label
        return None

    def login_browser(self):
        self.ensure_browser()
        LoginService(self.bro, self.chains, self.user_id).login_by_cookie()

    def ensure_browser(self):
        if self.bro is None:
            self.bro, self.chains = init_webdriver()

    def close_browser(self):
        if self.bro is None:
            return
        try:
            self.bro.quit()
        except Exception as e:
            mylogger.warning("关闭Selenium会话失败：%s" % e)
        finally:
            self.bro = None
            self.chains = None

    def recycle_browser(self, reason):
        if not self.owns_browser:
            return
        mylogger.warning("重启Selenium会话：" + reason)
        self.close_browser()
        self.login_browser()

    def cleanup_browser_page(self):
        if self.bro is None:
            return
        try:
            self.bro.get("about:blank")
        except Exception:
            pass

    def is_valid_dynamic_url(self, url):
        if url is None:
            return False
        return re.search(r'(t\.bilibili\.com|bilibili\.com/opus)/\d+', str(url)) is not None

    @staticmethod
    def extract_dynamic_id(url):
        match = re.search(r'/(?:opus/|)(\d+)(?:[/?#]|$)', str(url or ''))
        return match.group(1) if match else None

    def get_ignore_link(self):
        links = globals.ignore_link
        if len(links) != 0:
            return links.split('|')
        return {}

    def get_today_dynamic_links(self):
        """
        从数据库中获取当天的分享链接
        :return:
        """
        today = str(datetime.now().strftime("%Y-%m-%d"))
        return self.draw_dynamic_dao.query_by_time(today, limit=60, status=0)

    def get_pending_dynamic_links(self):
        """
        从数据库中获取所有待转发链接
        :return:
        """
        return self.draw_dynamic_dao.query_by_status(
            status=0,
            limit=globals.share_batch_limit,
            newest_first=True
        )

