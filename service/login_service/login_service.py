import json
import os
import time
from time import sleep
from urllib.parse import urlparse
from selenium.common.exceptions import InvalidCookieDomainException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.wait import WebDriverWait
from service.log_service.log_printer_service import MyLogger
from utils import globals
from utils.file_util import append_data_to_env
from utils.time_util import random_sleep
from utils.webdriver_util import ElementUtil, init_webdriver
from service.auth_service import (
    AuthenticationRequiredError,
    mark_authenticated,
    mark_login_required,
)
from dao.account_dao import AccountDao
from dao.init_db import init_db
from service.account_service import cookie_path

mylogger = MyLogger('login_service.py').getLogger()


class LoginService(object):
    def __init__(self, bro, chains, my_user_id='0'):
        self.bro = bro
        self.chains = chains
        self.my_user_id = my_user_id
        mylogger.info('启动登录模块')

    def login_manual(self):
        self.bro.get(globals.home_url)
        while ElementUtil.is_xpath_exist(self.bro, self.chains,
                                         '//*[@id="i_cecream"]/div[2]/div[1]/div[1]/ul[2]/li[1]/li/div') is True:
            random_sleep()
        dict_cookies = self.bro.get_cookies()
        json_cookies = json.dumps(dict_cookies)
        try:
            url = self.bro.find_element(By.XPATH,
                                        '//*[@id="i_cecream"]/div[2]/div[1]/div[1]/ul[2]/li[1]/div[1]/a[1]').get_attribute(
                "href")
        except Exception as e:
            print(e)
        result = urlparse(url)
        id = str(result[2])[1:]
        cookie_path = './cookie/' + id + '.txt'
        with open(cookie_path, 'w') as f:
            f.write(json_cookies)

    def login_by_cookie(self):
        """
        根据保存的Cookie信息进行登录
        :param bro:
        :return:
        """
        try:
            self.bro.get(globals.home_url)
            current_host = urlparse(self.bro.current_url).hostname or ""
            account_cookie_path = cookie_path(self.my_user_id)
            if account_cookie_path.is_file():
                with account_cookie_path.open("r", encoding="utf-8") as file:
                    cookies = json.load(file)
                for cookie in cookies:
                    if not cookie.get("name") or not cookie.get("value"):
                        continue
                    domain = str(cookie.get("domain") or "").lstrip(".").lower()
                    if domain and current_host.lower() != domain and not current_host.lower().endswith("." + domain):
                        mylogger.info("跳过与当前域名不匹配的 Cookie：%s", cookie.get("name"))
                        continue
                    if cookie.get("expiry") and cookie["expiry"] <= time.time():
                        mylogger.info("跳过已过期的 Cookie：%s", cookie.get("name"))
                        continue
                    try:
                        self.bro.add_cookie(cookie)
                    except InvalidCookieDomainException:
                        self.bro.get("https://www.bilibili.com/")
                        current_host = urlparse(self.bro.current_url).hostname or ""
                        if current_host.lower() != domain and not current_host.lower().endswith("." + domain):
                            mylogger.info("当前页面域名不匹配，跳过 Cookie：%s", cookie.get("name"))
                            continue
                        try:
                            self.bro.add_cookie(cookie)
                        except InvalidCookieDomainException:
                            mylogger.info("Cookie 域名校验失败，跳过：%s", cookie.get("name"))
            else:
                mark_login_required(self.my_user_id, "未找到该 UID 对应的 Cookie 文件")
                mylogger.warning("未找到用户 %s 的 Cookie 文件，B 站任务已暂停", self.my_user_id)
                raise AuthenticationRequiredError(
                    "Bilibili login is required; task paused"
                )
            self.bro.get("https://t.bilibili.com/")
            random_sleep(start=1, end=2)
            if not self.wait_logged_in():
                error = "Cookie登录失败，请检查SESSDATA是否有效"
                mark_login_required(self.my_user_id, error)
                mylogger.warning("B 站登录校验失败，任务已暂停")
                raise AuthenticationRequiredError("Bilibili login is required; task paused")
            uid = next((cookie.get('value') for cookie in self.bro.get_cookies()
                        if cookie.get('name') == 'DedeUserID'), None)
            if uid and str(uid) != str(self.my_user_id):
                error = "Cookie UID 与目标账号不一致"
                mark_login_required(self.my_user_id, error)
                raise AuthenticationRequiredError("Bilibili login is required; task paused")
            username = self.get_current_user_name()
            AccountDao(init_db()).ensure(
                str(self.my_user_id), bili_uid=uid or self.my_user_id,
                username=username,
                config_file=account_cookie_path.name if account_cookie_path.is_file() else None,
            )
            mark_authenticated(self.my_user_id, uid=uid or self.my_user_id)
            mylogger.info('使用cookie自动登录成功！')
        except AuthenticationRequiredError:
            raise
        except Exception:
            mylogger.exception("Cookie 登录流程异常")
            raise

    def get_current_user_name(self):
        try:
            result = self.bro.execute_async_script("""
const done = arguments[0];
fetch('https://api.bilibili.com/x/web-interface/nav', {credentials: 'include'})
  .then(response => response.json())
  .then(data => done(data.data || {}))
  .catch(() => done({}));
""")
            return result.get("uname") if isinstance(result, dict) else None
        except Exception:
            return None

    def wait_logged_in(self, timeout=15):
        try:
            WebDriverWait(self.bro, timeout).until(lambda driver: self.is_logged_in())
            return True
        except Exception:
            return False

    def is_logged_in(self):
        body_text = self.bro.find_element(By.TAG_NAME, 'body').text
        if "请先 登录 后发表评论" in body_text or "立即登录" in body_text:
            return False
        return len(self.bro.find_elements(By.CSS_SELECTOR, '.header-entry-avatar, .right-entry-avatar, a[href*="space.bilibili.com/"]')) != 0

    def login_by_cookie2(self):
        """
        根据保存的Cookie信息进行登录
        :param bro:
        :return:
        """
        try:
            cookie_path = './cookie/' + self.my_user_id + '.txt'
            self.bro.get(globals.home_url)
            with open(cookie_path, 'r', encoding='utf-8') as f:
                cookies = f.readlines()
            for cookie in cookies:
                cookie = cookie.replace(r'\n', '')
                cookie_li = json.loads(cookie)
                random_sleep(start=1, end=2)
                for cookie in cookie_li:
                    self.bro.add_cookie(cookie)
                self.bro.refresh()
            mylogger.info('使用cookie自动登录成功！')
            random_sleep(start=1, end=2)
        except Exception as e:
            mylogger.error('登录失败')
            mylogger.error("[出错原因为：%s]" % e)
