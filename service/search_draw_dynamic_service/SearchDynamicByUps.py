import time

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.wait import WebDriverWait
from dao.draw_dynamic_dao import DrawDynamicDao
from dao.init_db import init_db
from dao.scan_cache_dao import ScanCacheDao
from dao.statistics_dao import StatisticsDao
from service.log_service.log_printer_service import MyLogger
from service.login_service.login_service import LoginService
from service.notify_service.notify_service import NotifyService
from utils.ip_util import remove_query_string
from utils.webdriver_util import init_webdriver
from utils import globals
mylogger = MyLogger('SearchDynamicByUps.py').getLogger()


class SearchDynamicByUps(object):

    def __init__(self, user_id):
        self.count = 0
        self.user_id = user_id
        self.search_note = ""
        db = init_db()
        self.draw_dynamic_dao = DrawDynamicDao(db)
        self.scan_cache_dao = ScanCacheDao(db)
        self.statistics_dao = StatisticsDao(db)
        self.scan_cache_skip_count = 0
        self.scan_cache_retry_skip_count = 0

    def wait_dynamic_cards(self, bro):
        try:
            return WebDriverWait(bro, 20).until(
                lambda driver: driver.find_elements(By.CSS_SELECTOR, '.bili-dyn-list__item > .bili-dyn-item, .bili-dyn-item')
            )
        except TimeoutException:
            return []

    def normalize_dynamic_url(self, url):
        if not url:
            return None
        url = str(url).strip()
        if url.startswith('//'):
            url = 'https:' + url
        if url.startswith('http://'):
            url = 'https://' + url[len('http://'):]
        return remove_query_string(url)

    def is_dynamic_url(self, url):
        url = self.normalize_dynamic_url(url)
        if not url:
            return False
        return (
            '://t.bilibili.com/' in url
            or '://www.bilibili.com/opus/' in url
        )

    def append_unique_link(self, links, url):
        url = self.normalize_dynamic_url(url)
        if url and self.is_dynamic_url(url) and url not in links:
            links.append(url)

    def extract_links_from_current_page(self, bro):
        links = []
        selectors = [
            'a.opus-text-rich-hl[href]',
            'a[href*="t.bilibili.com/"]',
            'a[href*="/opus/"]',
        ]
        for selector in selectors:
            for element in bro.find_elements(By.CSS_SELECTOR, selector):
                self.append_unique_link(links, element.get_attribute('href'))
        for element in bro.find_elements(By.CSS_SELECTOR, '[data-url*="/opus/"], [data-url*="t.bilibili.com/"]'):
            self.append_unique_link(links, element.get_attribute('data-url'))
        return links

    def extract_detail_urls_from_cards(self, cards):
        detail_urls = []
        for card in cards:
            card_text = card.text or ''
            if '投稿了文章' not in card_text and '抽奖' not in card_text and '福利' not in card_text:
                continue
            for element in card.find_elements(By.CSS_SELECTOR, '[data-url*="/opus/"], [data-url*="t.bilibili.com/"]'):
                self.append_unique_link(detail_urls, element.get_attribute('data-url'))
        return detail_urls

    def extract_links_from_detail_page(self, bro, detail_url):
        try:
            bro.get(detail_url)
            WebDriverWait(bro, 15).until(lambda driver: driver.find_elements(By.TAG_NAME, 'body'))
        except TimeoutException:
            mylogger.warning('详情页加载超时，跳过：' + str(detail_url))
            return None
        except Exception as e:
            mylogger.warning('详情页加载失败，跳过：' + str(detail_url) + '，原因：' + str(e))
            return None
        time.sleep(2)
        links = self.extract_links_from_current_page(bro)
        detail_url = self.normalize_dynamic_url(detail_url)
        return [link for link in links if link != detail_url]

    def should_scan_detail_page(self, detail_url):
        detail_url = self.normalize_dynamic_url(detail_url)
        if self.scan_cache_dao.has_success('detail', detail_url):
            self.scan_cache_skip_count = self.scan_cache_skip_count + 1
            mylogger.info('详情页已扫描，跳过：' + str(detail_url))
            return False
        if self.scan_cache_dao.has_recent_failure('detail', detail_url, globals.scan_cache_retry_hours):
            self.scan_cache_retry_skip_count = self.scan_cache_retry_skip_count + 1
            mylogger.info('详情页近期扫描失败，暂不重试：' + str(detail_url))
            return False
        return True

    def search_dynamic_links_by_up(self, bro, base_url, note):
        cards = []
        for attempt in range(3):
            if attempt == 0:
                bro.get(base_url)
            else:
                bro.refresh()
            time.sleep(2)
            bro.execute_script("window.scrollTo(0, 600)")
            cards = self.wait_dynamic_cards(bro)
            if len(cards) != 0:
                break
        if len(cards) == 0:
            mylogger.warning('未找到动态卡片，跳过：' + note + '，页面标题：' + str(bro.title))
            return

        links = self.extract_links_from_current_page(bro)
        detail_urls = self.extract_detail_urls_from_cards(cards)
        for detail_url in detail_urls:
            detail_url = self.normalize_dynamic_url(detail_url)
            if not self.should_scan_detail_page(detail_url):
                continue
            detail_links = self.extract_links_from_detail_page(bro, detail_url)
            if detail_links is None:
                self.scan_cache_dao.save_failure('detail', detail_url, remove_query_string(base_url), note, '详情页加载超时')
                continue
            self.scan_cache_dao.save_success('detail', detail_url, remove_query_string(base_url), note, len(detail_links))
            if len(detail_links) == 0:
                self.append_unique_link(links, detail_url)
            else:
                for link in detail_links:
                    self.append_unique_link(links, link)

        self.dynLinks_to_db(links, remove_query_string(base_url), note)

    def searchFromFiftyUps(self, bro, chains):
        """
        up主：你的工具人老公
        up主链接：https://space.bilibili.com/100680137/dynamic
        来源：https://space.bilibili.com/100680137/dynamic
        :return:
        """
        base_url = 'https://space.bilibili.com/100680137/dynamic'
        try:
            self.search_dynamic_links_by_up(bro, base_url, "你的工具人老公")
        except Exception as e:
            mylogger.error("[searchFromFiftyUps 从“你的工具人老公”查找抽奖动态 出错 %s]" % e, exc_info=True)
            NotifyService().fangtang_msg_push_by_content(title="“你的工具人老公”查找抽奖动态出错", content='从“你的工具人老公”查找抽奖动态 出错')

    def searchFromBigFish(self, bro, chains):
        """
        up主：_大锦鲤_
        up主链接：https://space.bilibili.com/226257459/dynamic
        :return:
        """
        base_url = 'https://space.bilibili.com/226257459/dynamic'
        try:
            self.search_dynamic_links_by_up(bro, base_url, "_大锦鲤_")
        except Exception as e:
            mylogger.error("[searchFromBigFish 从“_大锦鲤_”查找抽奖动态 出错 %s]" % e, exc_info=True)
            NotifyService().fangtang_msg_push_by_content(title="“_大锦鲤_”查找抽奖动态出错", content='从“_大锦鲤_”查找抽奖动态 出错')

    def searchFromCarcinus_(self, bro, chains):
        """
        up主：Carcinus_
        up主链接：https://space.bilibili.com/27332255/dynamic
        :return:
        """
        base_url = 'https://space.bilibili.com/27332255/dynamic'
        try:
            self.search_dynamic_links_by_up(bro, base_url, "Carcinus_")
        except Exception as e:
            mylogger.error("[searchFromCarcinus_ 从“Carcinus_”查找抽奖动态 出错 %s]" % e, exc_info=True)
            NotifyService().fangtang_msg_push_by_content(title="“Carcinus_”查找抽奖动态出错", content='从“Carcinus_”查找抽奖动态 出错')

    def searchFromSmile(self, bro, chains):
        """
        up主：闻不着味
        up主链接：https://space.bilibili.com/280025263/dynamic
        :return:
        """
        base_url = 'https://space.bilibili.com/280025263/dynamic'
        try:
            self.search_dynamic_links_by_up(bro, base_url, "闻不着味")
        except Exception as e:
            mylogger.error("[searchFromSmile 从“闻不着味”查找抽奖动态 出错 %s]" % e, exc_info=True)
            NotifyService().fangtang_msg_push_by_content(title="“闻不着味”查找抽奖动态出错", content='从“闻不着味”查找抽奖动态 出错')

    def dynLinks_to_db(self, dynLinks, source, note):
        cnt = 0
        break_flag = 0
        ignore_links = self.get_ignore_link()
        for link in dynLinks:
            try:
                if not link:
                    continue
                link = self.normalize_dynamic_url(link)
                # 跳过非抽奖动态
                for ign_lnk in ignore_links:
                    if ign_lnk in link:
                        break_flag = 1
                if break_flag == 1:
                    break_flag = 0
                    continue
                break_flag = 0
                # 跳过已经入库的
                if len(self.draw_dynamic_dao.query_by_dyn_url(remove_query_string(link))) == 0:
                    self.draw_dynamic_dao.insert(remove_query_string(link), source, note)
                    cnt = cnt + 1
            except Exception as e:
                mylogger.error("[dynLinks_to_db 动态插入数据库 出错 %s]" % e, exc_info=True)
        self.count = self.count + cnt
        self.search_note = self.search_note + note + ":" + str(cnt) + ";  "

    def get_ignore_link(self):
        links = globals.ignore_link
        if len(links) != 0:
            return links.split('|')
        return {}

    def init_search(self):
        bro = None
        try:
            bro, chains = init_webdriver()
            LoginService(bro, chains, self.user_id).login_by_cookie()
            if "你的工具人老公" in globals.ups:
                self.searchFromFiftyUps(bro, chains)
            if "_大锦鲤_" in globals.ups:
                self.searchFromBigFish(bro, chains)
            if "Carcinus_" in globals.ups:
                self.searchFromCarcinus_(bro, chains)
            if "闻不着味" in globals.ups:
                self.searchFromSmile(bro, chains)
            # 统计入库
            self.search_note = (
                self.search_note
                + "详情页缓存跳过:" + str(self.scan_cache_skip_count)
                + ";  详情页失败冷却跳过:" + str(self.scan_cache_retry_skip_count)
                + ";  "
            )
            self.statistics_dao.insert("", "搜索到的抽奖动态条数为: " + str(self.count), self.search_note)
        except:
            mylogger.error("[搜索抽奖动态列表主流程 出错]")
        finally:
            bro.quit()


if __name__ == '__main__':
    SearchDynamicByUps().init_search()
