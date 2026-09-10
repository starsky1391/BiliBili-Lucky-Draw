import json
from time import sleep
from urllib.parse import urlparse
from selenium.webdriver.common.by import By
from selenium.webdriver.support.wait import WebDriverWait
from service.log_service.log_printer_service import MyLogger
from utils import globals
from utils.file_util import append_data_to_env
from utils.time_util import random_sleep
from utils.webdriver_util import ElementUtil, init_webdriver
from service.auth_service import mark_authenticated, mark_login_required

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
            cookie_value = globals.cookie_value
            cookie = {"domain": ".bilibili.com", "name": "SESSDATA", "path": "/", "sameSite": "Lax", "value": cookie_value}
            self.bro.add_cookie(cookie)
            if globals.bili_jct:
                csrf_cookie = {"domain": ".bilibili.com", "name": "bili_jct", "path": "/", "sameSite": "Lax", "value": globals.bili_jct}
                self.bro.add_cookie(csrf_cookie)
            self.bro.get("https://t.bilibili.com/")
            random_sleep(start=1, end=2)
            if not self.wait_logged_in():
                raise Exception("Cookie登录失败，请检查SESSDATA是否有效")
            uid = next((cookie.get('value') for cookie in self.bro.get_cookies()
                        if cookie.get('name') == 'DedeUserID'), None)
            mark_authenticated(uid)
            mylogger.info('使用cookie自动登录成功！')
        except Exception as e:
            mark_login_required(str(e))
            mylogger.error('登录失败')
            mylogger.error("[出错原因为：%s]" % e)
            raise

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
