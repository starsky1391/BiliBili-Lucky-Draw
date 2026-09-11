import re
import time
from datetime import datetime, timedelta

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

    def extract_detail_entries_from_cards(self, bro, cards):
        detail_entries = []
        for card in cards:
            card_text = card.text or ''
            if '投稿了文章' not in card_text and '抽奖' not in card_text and '福利' not in card_text:
                continue
            publish_time = self.extract_publish_time_from_card(bro, card)
            for element in card.find_elements(By.CSS_SELECTOR, '[data-url*="/opus/"], [data-url*="t.bilibili.com/"]'):
                url = self.normalize_dynamic_url(element.get_attribute('data-url'))
                if not url or any(item['url'] == url for item in detail_entries):
                    continue
                detail_entries.append({'url': url, 'publish_time': publish_time})
        return detail_entries

    def extract_publish_time_from_card(self, bro, card):
        values = bro.execute_script("""
const card = arguments[0];
const values = [];
const nodes = [card].concat(Array.from(card.querySelectorAll('*')));
for (const node of nodes) {
  for (const attr of Array.from(node.attributes || [])) {
    const value = String(attr.value || '').trim();
    if (/(time|date|pub|timestamp)/i.test(attr.name) || /^\\d{10,13}$/.test(value)) {
      values.push(value);
    }
  }
  if (node.tagName === 'TIME' || /time|date/i.test(String(node.className || ''))) {
    const text = String(node.innerText || node.textContent || '').trim();
    if (text) values.push(text);
  }
}
return values;
""", card) or []
        values.append(card.text or '')
        now = datetime.now()
        for value in values:
            parsed = self.parse_card_time(value, now)
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def parse_card_time(value, now):
        text = re.sub(r'\s+', ' ', str(value or '')).strip()
        if not text:
            return None
        if re.fullmatch(r'\d{10,13}', text):
            timestamp = int(text)
            if len(text) == 13:
                timestamp //= 1000
            try:
                return datetime.fromtimestamp(timestamp)
            except (OverflowError, OSError, ValueError):
                return None
        match = re.search(r'(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})日?(?:\s+|T)(\d{1,2}):(\d{2})', text)
        if match:
            return datetime(*[int(value) for value in match.groups()])
        match = re.search(r'(?<!\d)(\d{1,2})月(\d{1,2})日?(?:\s+|T)(\d{1,2}):(\d{2})', text)
        if match:
            month, day, hour, minute = map(int, match.groups())
            try:
                return datetime(now.year, month, day, hour, minute)
            except ValueError:
                return None
        match = re.search(r'(?<!\d)(\d{1,2})[-/](\d{1,2})(?:\s+|T)(\d{1,2}):(\d{2})', text)
        if match:
            month, day, hour, minute = map(int, match.groups())
            try:
                return datetime(now.year, month, day, hour, minute)
            except ValueError:
                return None
        match = re.search(r'昨天\s*(\d{1,2}):(\d{2})', text)
        if match:
            hour, minute = map(int, match.groups())
            return (now - timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        match = re.search(r'(\d+)\s*分钟前', text)
        if match:
            return now - timedelta(minutes=int(match.group(1)))
        if '刚刚' in text:
            return now
        return None

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
        if self.is_missing_dynamic_page(bro):
            mylogger.warning('动态详情页不存在或已删除，跳过待转发入库：' + str(detail_url))
            return None
        time.sleep(2)
        links = self.extract_links_from_current_page(bro)
        detail_url = self.normalize_dynamic_url(detail_url)
        return [link for link in links if link != detail_url]

    @staticmethod
    def is_missing_dynamic_page(bro):
        title = (bro.title or '').strip()
        if '404' in title or '出错啦' in title:
            return True
        try:
            body_text = bro.find_element(By.TAG_NAME, 'body').text or ''
        except Exception:
            return False
        markers = ('页面不存在', '动态不存在', '内容不存在', '已被删除', '返回上一页')
        return any(marker in body_text for marker in markers) and (
            '换一张' in body_text or '刷新' in body_text or '返回上一页' in body_text
        )

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
        detail_entries = self.extract_detail_entries_from_cards(bro, cards)
        cutoff_time = datetime.now() - timedelta(days=365)
        for entry in detail_entries:
            detail_url = entry['url']
            publish_time = entry['publish_time']
            if publish_time is not None and publish_time < cutoff_time:
                mylogger.info(
                    '动态发布时间早于过去一年，停止当前UP扫描：%s，发布时间=%s，边界=%s',
                    detail_url, publish_time, cutoff_time
                )
                break
            if not self.should_scan_detail_page(detail_url):
                continue
            detail_links = self.extract_links_from_detail_page(bro, detail_url)
            if detail_links is None:
                links = [
                    link for link in links
                    if link != self.normalize_dynamic_url(detail_url)
                ]
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
        except Exception:
            mylogger.error("[搜索抽奖动态列表主流程 出错]")
            raise
        finally:
            if bro is not None:
                bro.quit()


if __name__ == '__main__':
    SearchDynamicByUps().init_search()
