import re
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import time as datetime_time
from urllib.parse import urlparse

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait

from service.log_service.log_printer_service import MyLogger
from utils.ip_util import get_host_ip
from utils.time_util import random_sleep

mylogger = MyLogger('share_one_dynamic.py').getLogger()


class DynamicShareBase(object):
    PAGE_READY_CSS = '.bili-dyn-item, .bili-dyn-content, .side-toolbar'
    FOLLOW_CSS = '.space-head-follow, .follow-btn, .space-follow-btn'
    LIKE_CSS = '.side-toolbar__action.like, .bili-dyn-action.like, [class*="side-toolbar__action"][class*="like"]'
    FORWARD_CSS = '.side-toolbar__action.forward, .bili-dyn-action.forward, [class*="side-toolbar__action"][class*="forward"]'
    SHARE_EDITOR_CSS = '.bili-dyn-share-publishing .bili-rich-textarea__inner[contenteditable="true"]'
    SHARE_PUBLISH_CSS = '.bili-dyn-share-publishing__action, .bili-dyn-share-publishing button'
    DYNAMIC_TEXT_CSS = (
        '.bili-dyn-item, .bili-dyn-card, .bili-dyn-content, '
        '.bili-dyn-content__orig, .bili-dyn-share-publishing__reference'
    )
    UP_TITLE_CSS = '.bili-dyn-title__text, .bili-dyn-title'
    UP_AT_CSS = '.bili-dyn-item [data-type="at"][data-oid], .bili-dyn-content [data-type="at"][data-oid]'

    def __init__(self):
        # 公有属性，可以在类外部访问
        self.upId = None
        self.upUrl = None
        self.upIds = []
        self.upUrls = []
        self.share_url = None
        self.status = 1
        self.machine_ip = None
        self.share_time = None
        self.share_status = 1
        self.user_id = None
        self.expire_date = None
        self.lottery_time = None
        self.publish_time = None
        self.current_user_name = None

    def share_one(self, bro, chains, lucky_dynamic_url, share_content, comment_content):
        """
        提供需要转发的抽奖动态URL，然后“执行状态、up主的ID、up主的主页URL”等信息
        :param bro:
        :param chains:
        :param lucky_dynamic_url: 需要进行转发的抽奖动态的URL
        :return:
        """
        try:
            bro.get(lucky_dynamic_url)
            self.wait_page_ready(bro)
            self.share_url = lucky_dynamic_url
            self.resolve_lottery_time(bro)
            if self.is_expired_dynamic(bro):
                self.status = 2
                self.share_status = 2
                mylogger.info("动态已过期，跳过转发url : " + lucky_dynamic_url)
                return
            self.ensure_publish_cookie(bro)
            # 点击关注
            self.click_follow_all(bro, chains)
            random_sleep()
            # 点赞
            self.click_like(bro, chains)
            random_sleep()
            # 预约抽奖
            self.click_reserve(bro, chains)
            random_sleep()
            # 评论
            if self.has_current_user_comment(bro):
                mylogger.info("当前账号已评论，跳过评论")
            else:
                self.commit_comment(bro, chains, comment_content)
            random_sleep()
            # 移动到“分享”按钮, 点击“转发”
            if self.is_already_forwarded(bro):
                mylogger.info("当前账号已转发，跳过转发")
            else:
                self.click_share(bro, chains, share_content)
            random_sleep()
            # 回填状态
            self.share_status = 0
            self.status = 0
        except Exception as e:
            mylogger.error("share_one 转发单条动态主流程 出错url : " + lucky_dynamic_url)
            mylogger.error("[share_one 出错原因为：%s]" % e, exc_info=True)
        finally:
            self.share_time = str(datetime.now())
            self.machine_ip = get_host_ip()
            mylogger.info('单条动态转发--执行结束')

    def wait_page_ready(self, bro, timeout=20):
        WebDriverWait(bro, timeout).until(EC.presence_of_element_located((By.CSS_SELECTOR, 'body')))
        WebDriverWait(bro, timeout).until(lambda driver: self.is_dynamic_page_ready(driver))

    def is_dynamic_page_ready(self, bro):
        if self.is_bili_error_page(bro):
            raise Exception("B站错误页：" + str(bro.current_url))
        return len(bro.find_elements(By.CSS_SELECTOR, self.PAGE_READY_CSS)) > 0

    def is_bili_error_page(self, bro):
        title = bro.title or ''
        if '出错啦' in title:
            return True
        try:
            body_text = bro.find_element(By.TAG_NAME, 'body').text or ''
        except Exception:
            return False
        return '返回上一页' in body_text and '换一张' in body_text

    def wait_css(self, bro, selector, timeout=10):
        return WebDriverWait(bro, timeout).until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))

    def wait_clickable_css(self, bro, selector, timeout=10):
        return WebDriverWait(bro, timeout).until(EC.element_to_be_clickable((By.CSS_SELECTOR, selector)))

    def click_by_js(self, bro, ele):
        bro.execute_script("arguments[0].scrollIntoView({block:'center'});", ele)
        bro.execute_script("arguments[0].click();", ele)

    def click_first_by_text(self, bro, texts, timeout=3):
        xpath = "//*[self::button or self::div or self::span or self::a][" + " or ".join(
            ["contains(normalize-space(.), '%s')" % text for text in texts]
        ) + "]"
        ele = WebDriverWait(bro, timeout).until(EC.element_to_be_clickable((By.XPATH, xpath)))
        self.click_by_js(bro, ele)
        return ele

    def get_visible_elements(self, bro, selector):
        return [ele for ele in bro.find_elements(By.CSS_SELECTOR, selector) if ele.is_displayed()]

    def get_up_info(self, bro):
        self.upId = None
        self.upUrl = None
        self.upIds = []
        self.upUrls = []
        up_id = self.get_up_id_from_initial_state(bro)
        if up_id:
            self.add_up_id(up_id)
        else:
            up_id = self.get_up_id_from_dynamic_api(bro)
            if up_id:
                self.add_up_id(up_id)
        for link in self.get_visible_elements(bro, self.UP_AT_CSS):
            up_id = (link.get_attribute('data-oid') or '').strip()
            if up_id.isdigit():
                self.add_up_id(up_id)
        if self.upIds:
            self.upId = self.upIds[0]
            self.upUrl = self.upUrls[0]
            return
        raise Exception("未能从动态标题解析UP主UID")

    def add_up_id(self, up_id):
        up_id = str(up_id)
        if up_id.isdigit() and up_id not in self.upIds:
            self.upIds.append(up_id)
            self.upUrls.append('https://space.bilibili.com/' + up_id)

    def get_up_id_from_initial_state(self, bro):
        try:
            up_id = bro.execute_script("""
const state = window.__INITIAL_STATE__ || {};
const basicUid = state.detail && state.detail.basic && state.detail.basic.uid;
if (basicUid) return String(basicUid);
const modules = state.detail && Array.isArray(state.detail.modules) ? state.detail.modules : [];
for (const module of modules) {
  const author = module.module_author || {};
  const mid = author.mid || author.uid;
  if (mid) return String(mid);
}
return null;
""")
            if up_id and str(up_id).isdigit():
                return str(up_id)
        except Exception:
            return None
        return None

    def get_up_id_from_dynamic_api(self, bro):
        dynamic_id = self.get_dynamic_id_from_url(bro.current_url)
        if not dynamic_id:
            return None
        try:
            up_id = bro.execute_async_script("""
const dynamicId = arguments[0];
const done = arguments[1];
fetch('https://api.bilibili.com/x/polymer/web-dynamic/v1/detail?id=' + encodeURIComponent(dynamicId), {credentials: 'include'})
  .then(response => response.json())
  .then(data => {
    const item = data.data && data.data.item;
    const author = item && item.modules && item.modules.module_author;
    const mid = author && (author.mid || author.uid);
    done(mid ? String(mid) : null);
  })
  .catch(() => done(null));
""", dynamic_id)
            if up_id and str(up_id).isdigit():
                return str(up_id)
        except Exception:
            return None
        return None

    def get_dynamic_id_from_url(self, url):
        path = urlparse(url).path.strip('/')
        match = re.search(r'(\d+)$', path)
        if match:
            return match.group(1)
        return None

    def get_dynamic_author_name(self, bro):
        for ele in self.get_visible_elements(bro, self.UP_TITLE_CSS):
            text = self.get_element_text(ele).replace('\u200b', '').strip()
            if text:
                return text
        raise Exception("未能从动态标题获取UP主名称")

    def parse_up_id(self, href):
        if href is None:
            return None
        path = urlparse(href).path.strip('/')
        if path.isdigit():
            return path
        return None

    def is_done_text(self, ele, done_words):
        text = (ele.get_attribute('innerText') or ele.text or '').strip()
        cls = ele.get_attribute('class') or ''
        return any(word in text for word in done_words) or any(word in cls for word in done_words)

    def has_text(self, ele, words):
        text = (ele.get_attribute('innerText') or ele.text or '').strip()
        return any(word in text for word in words)

    def is_expired_dynamic(self, bro):
        if self.lottery_time is None:
            return False
        self.expire_date = str(self.lottery_time)
        return datetime.now() >= self.lottery_time

    def resolve_lottery_time(self, bro):
        metadata = self.get_dynamic_time_metadata(bro)
        publish_ts = metadata.get('publish_ts')
        lottery_end_ts = metadata.get('lottery_end_ts')

        if publish_ts is not None:
            self.publish_time = datetime.fromtimestamp(publish_ts)

        if lottery_end_ts is not None:
            self.lottery_time = datetime.fromtimestamp(lottery_end_ts)
            return

        text_date = self.parse_expire_date(self.get_dynamic_text(bro))
        if text_date is not None:
            self.lottery_time = datetime.combine(text_date, datetime_time(23, 59, 59))
            return

        if self.publish_time is not None:
            self.lottery_time = self.publish_time + timedelta(days=120)
            mylogger.info("未找到明确开奖时间，使用动态发布时间+120天：%s" % self.lottery_time)
        else:
            mylogger.warning("未获取到动态发布时间，无法计算120天保底时间")

    def get_dynamic_time_metadata(self, bro):
        dynamic_id = self.get_dynamic_id_from_url(bro.current_url)
        if not dynamic_id:
            return {}
        try:
            result = bro.execute_async_script("""
const dynamicId = arguments[0];
const done = arguments[1];
const timeKeys = [
  'lottery_time', 'lottery_end_time', 'end_time', 'end_ts',
  'draw_time', 'award_time', 'deadline'
];

function toTimestamp(value) {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value > 100000000000 ? Math.floor(value / 1000) : Math.floor(value);
  }
  if (typeof value !== 'string' || value.trim() === '') return null;
  const numberValue = Number(value);
  if (Number.isFinite(numberValue)) {
    return numberValue > 100000000000 ? Math.floor(numberValue / 1000) : Math.floor(numberValue);
  }
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? Math.floor(parsed / 1000) : null;
}

function findLotteryTimestamp(value, visited) {
  if (!value || typeof value !== 'object' || visited.has(value)) return null;
  visited.add(value);
  for (const key of timeKeys) {
    if (value[key] != null) {
      const timestamp = toTimestamp(value[key]);
      if (timestamp != null) return timestamp;
    }
  }
  for (const key of Object.keys(value)) {
    const timestamp = findLotteryTimestamp(value[key], visited);
    if (timestamp != null) return timestamp;
  }
  return null;
}

Promise.all([
  fetch('https://api.bilibili.com/x/polymer/web-dynamic/v1/detail?id='
    + encodeURIComponent(dynamicId), {credentials: 'include'})
    .then(response => response.json())
    .catch(() => ({})),
  fetch('https://api.vc.bilibili.com/lottery_svr/v1/lottery_svr/lottery_notice'
    + '?business_type=4&business_id=' + encodeURIComponent(dynamicId),
    {credentials: 'include'})
    .then(response => response.json())
    .catch(() => ({}))
]).then(results => {
  const detail = results[0] || {};
  const lottery = results[1] || {};
  const item = detail.data && (detail.data.item || detail.data);
  const author = item && item.modules && item.modules.module_author;
  const publishTs = author && Number(author.pub_ts);
  const lotteryData = lottery.data || {};
  const lotteryEndTs = findLotteryTimestamp(lotteryData, new Set());
  done({
    publish_ts: Number.isFinite(publishTs) && publishTs > 0 ? publishTs : null,
    lottery_end_ts: lotteryEndTs
  });
}).catch(() => done({}));
""", dynamic_id)
            return result if isinstance(result, dict) else {}
        except Exception as e:
            mylogger.warning("获取动态发布时间/开奖时间失败：%s" % e)
            return {}

    def get_dynamic_text(self, bro):
        texts = []
        for ele in self.get_visible_elements(bro, self.DYNAMIC_TEXT_CSS):
            text = (ele.get_attribute('innerText') or ele.text or '').strip()
            if text:
                texts.append(text)
        if len(texts) == 0:
            body = bro.find_element(By.TAG_NAME, 'body')
            texts.append((body.get_attribute('innerText') or body.text or '').strip())
        return "\n".join(texts)

    def parse_expire_date(self, text):
        keyword_pattern = (
            r'(开奖|开獎|抽奖结果|抽獎結果|中奖名单|中獎名單|公布名单|公布名單|'
            r'公布结果|公布結果|截止|截至|报名截止|報名截止|参与截止|參與截止|'
            r'结束时间|結束時間|结束|結束)'
        )
        keyword_candidates = []
        all_dates = []
        current_year = date.today().year
        for match in re.finditer(
                r'((?:20\d{2})年)?(\d{1,2})月(\d{1,2})[日号]?', text):
            year = int(match.group(1)[:-1]) if match.group(1) else current_year
            self.append_valid_date(all_dates, year, int(match.group(2)), int(match.group(3)))
        for match in re.finditer(r'(?<!\d)(\d{1,2})[./](\d{1,2})(?!\d)', text):
            self.append_valid_date(all_dates, current_year, int(match.group(1)), int(match.group(2)))
        for keyword in re.finditer(keyword_pattern, text, re.IGNORECASE):
            start = max(0, keyword.start() - 40)
            end = min(len(text), keyword.end() + 60)
            context = text[start:end]
            for match in re.finditer(
                    r'((?:20\d{2})年)?(\d{1,2})月(\d{1,2})[日号]?', context):
                year = int(match.group(1)[:-1]) if match.group(1) else current_year
                self.append_valid_date(
                    keyword_candidates, year, int(match.group(2)), int(match.group(3)))
            for match in re.finditer(r'(?<!\d)(\d{1,2})[./](\d{1,2})(?!\d)', context):
                self.append_valid_date(
                    keyword_candidates, current_year, int(match.group(1)), int(match.group(2)))
        if keyword_candidates:
            return max(keyword_candidates)
        if all_dates:
            return max(all_dates)
        return None

    def append_valid_date(self, dates, year, month, day):
        try:
            dates.append(date(year, month, day))
        except ValueError:
            return

    def ensure_publish_cookie(self, bro):
        names = [cookie.get('name') for cookie in bro.get_cookies()]
        if 'bili_jct' not in names:
            raise Exception("缺少 bili_jct Cookie，无法提交评论/转发，已停止执行防止假成功")

    def get_current_user_id(self, bro):
        for avatar in self.get_visible_elements(
                bro, 'a.header-entry-mini, .header-entry-avatar, .right-entry-avatar'):
            href = avatar.get_attribute('href')
            if not href:
                try:
                    href = avatar.find_element(By.XPATH, './ancestor-or-self::a[1]').get_attribute('href')
                except Exception:
                    href = None
            user_id = self.parse_up_id(href)
            if user_id:
                return user_id
        return None

    def get_current_user_name(self, bro):
        if self.current_user_name:
            return self.current_user_name
        try:
            result = bro.execute_async_script("""
const done = arguments[0];
fetch('https://api.bilibili.com/x/web-interface/nav', {credentials: 'include'})
  .then(response => response.json())
  .then(data => done(data.data || {}))
  .catch(() => done({}));
""")
            self.current_user_name = result.get('uname')
        except Exception:
            self.current_user_name = None
        return self.current_user_name

    def has_current_user_comment(self, bro):
        user_name = self.get_current_user_name(bro)
        if not user_name:
            return False
        bro.execute_script("""
const comments = document.querySelector('.comment-wrap, .bili-comment-container, bili-comments');
if (comments) comments.scrollIntoView({block: 'center'});
""")
        body = bro.find_element(By.TAG_NAME, 'body').text or ''
        editor_marker = body.rfind('请输入内容')
        if editor_marker >= 0:
            body = body[:editor_marker]
        comment_marker = re.search(r'评论\s*[\d.万]+', body)
        if comment_marker:
            body = body[comment_marker.start():]
        return any(line.strip() == user_name for line in body.splitlines())

    def is_already_forwarded(self, bro):
        buttons = self.get_visible_elements(bro, self.FORWARD_CSS)
        for button in buttons:
            text = (button.get_attribute('innerText') or button.text or '').strip()
            cls = (button.get_attribute('class') or '').lower()
            aria_pressed = button.get_attribute('aria-pressed')
            if (
                any(word in text for word in ["已转发", "已分享"])
                or "forwarded" in cls
                or "active" in cls
                or aria_pressed == "true"
            ):
                return True
        return False

    def click_follow_all(self, bro, chains):
        dynamic_url = bro.current_url
        self.get_up_info(bro)
        for up_id, up_url in zip(self.upIds, self.upUrls):
            self.upId = up_id
            self.upUrl = up_url
            self.click_follow_one(bro, chains, dynamic_url)
        self.upId = self.upIds[0]
        self.upUrl = self.upUrls[0]

    def click_follow_one(self, bro, chains, dynamic_url):
        """
        点击关注，并且返回相关的up主信息
        :param bro:
        :param chains:
        :return:
        """
        try:
            bro.get(self.upUrl)
            self.wait_css(bro, self.FOLLOW_CSS, timeout=20)
            if self.is_followed_by_relation_api(bro):
                mylogger.info("UP主已关注，跳过关注")
                return
            follow_btn = self.get_space_follow_button(bro)
            if follow_btn is None:
                mylogger.info("未找到关注按钮，跳过关注")
                return
            if self.has_text(follow_btn, ["已关注", "已互粉"]):
                mylogger.info("UP主已关注，跳过关注")
                return
            if not self.has_text(follow_btn, ["关注"]):
                raise Exception("主页关注按钮状态未知：" + self.get_element_text(follow_btn))
            self.click_by_js(bro, follow_btn)
            random_sleep(start=1, end=2)
            WebDriverWait(bro, 10, poll_frequency=2).until(
                lambda driver: self.is_followed_on_space(driver) or self.is_followed_by_relation_api(driver)
            )
        except Exception as e:
            mylogger.error("[click_follow 点击“关注” 出错 %s]" % e, exc_info=True)
            raise
        finally:
            try:
                if dynamic_url and bro.current_url != dynamic_url:
                    bro.get(dynamic_url)
                    self.wait_page_ready(bro)
            except Exception:
                pass

    def get_element_text(self, ele):
        return (ele.get_attribute('innerText') or ele.text or '').strip()

    def get_space_follow_button(self, bro):
        for selector in ['.space-follow-btn', '.follow-btn', '.space-head-follow']:
            for button in self.get_visible_elements(bro, selector):
                if self.has_text(button, ["关注", "已关注", "已互粉"]):
                    return button
        return None

    def is_followed_on_space(self, bro):
        button = self.get_space_follow_button(bro)
        return button is not None and self.has_text(button, ["已关注", "已互粉"])

    def is_followed_by_relation_api(self, bro):
        if not self.upId:
            return False
        try:
            result = bro.execute_async_script("""
const fid = arguments[0];
const done = arguments[1];
fetch('https://api.bilibili.com/x/relation?fid=' + encodeURIComponent(fid), {credentials: 'include'})
  .then(response => response.json())
  .then(data => done(data))
  .catch(() => done({}));
""", self.upId)
            if result.get('code') != 0:
                return False
            attribute = int(result.get('data', {}).get('attribute') or 0)
            return (attribute & 2) == 2
        except Exception:
            return False

    def click_like(self, bro, chains):
        """
        点赞
        :param bro:
        :param chains:
        :return:
        """
        try:
            like_btn = self.wait_clickable_css(bro, self.LIKE_CSS)
            if self.is_done_text(like_btn, ["active", "liked", "已赞"]):
                mylogger.info("动态已点赞，跳过点赞")
                return
            self.click_by_js(bro, like_btn)
        except Exception as e:
            mylogger.error("[click_like 点击“点赞” 出错 %s]" % e, exc_info=True)
            raise

    def click_reserve(self, bro, chains):
        """
        预约抽奖
        :param bro:
        :param chains:
        :return:
        """
        try:
            try:
                self.click_first_by_text(bro, ["预约", "参与"], timeout=3)
            except TimeoutException:
                mylogger.info("未找到预约/参与按钮，跳过预约")
        except Exception as e:
            mylogger.error("[click_like 点击“预约抽奖” 出错 %s]" % e, exc_info=True)
            raise

    def commit_comment(self, bro, chains, comment_content):
        """
        评论
        :param comment_content:
        :param bro:
        :param chains:
        :return:
        """
        try:
            self.wait_css(bro, 'bili-comments', timeout=20)
            editor = WebDriverWait(bro, 10).until(lambda driver: self.find_comment_editor(driver))
            self.click_by_js(bro, editor)
            editor.send_keys(str(comment_content))
            random_sleep(start=1, end=2)
            ok = bro.execute_script("""
function visible(el) {
  const r = el.getBoundingClientRect();
  const s = getComputedStyle(el);
  return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
}
function walk(root) {
  const buttons = root.querySelectorAll ? root.querySelectorAll('button') : [];
  for (const btn of buttons) {
    const text = (btn.innerText || btn.textContent || '').trim();
    const cls = String(btn.className || '');
    if (visible(btn) && text === '发布' && !btn.disabled && !cls.includes('disabled')) {
      btn.click();
      return true;
    }
  }
  const all = root.querySelectorAll ? root.querySelectorAll('*') : [];
  for (const el of all) {
    if (el.shadowRoot && walk(el.shadowRoot)) return true;
  }
  return false;
}
return walk(document);
""")
            if not ok:
                raise Exception("未找到评论发布按钮")
            try:
                WebDriverWait(bro, 3).until(lambda driver: self.has_current_user_comment(driver))
            except TimeoutException:
                mylogger.info("评论已提交，评论列表尚未刷新，继续转发")
        except Exception as e:
            mylogger.error("[commit_comment 点击“评论” 出错 %s]" % e, exc_info=True)
            raise

    def find_comment_editor(self, bro):
        return bro.execute_script("""
function visible(el) {
  const r = el.getBoundingClientRect();
  const s = getComputedStyle(el);
  return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
}
function walk(root) {
  const editor = root.querySelector && root.querySelector('.brt-editor[contenteditable="true"], [contenteditable="true"].brt-editor');
  if (editor && visible(editor)) return editor;
  const all = root.querySelectorAll ? root.querySelectorAll('*') : [];
  for (const el of all) {
    if (el.shadowRoot) {
      const found = walk(el.shadowRoot);
      if (found) return found;
    }
  }
  return null;
}
return walk(document);
""")

    def click_share(self, bro, chains, share_content):
        """
        执行转发动态
        :param bro:
        :param chains:
        :return:
        """
        try:
            share_btn = self.wait_clickable_css(bro, self.FORWARD_CSS)
            self.click_by_js(bro, share_btn)
            share_text = self.wait_css(bro, self.SHARE_EDITOR_CSS, timeout=10)
            self.focus_contenteditable_start(bro, share_text)
            share_text.send_keys(str(share_content))
            random_sleep(start=1, end=2)
            publish_btn = WebDriverWait(bro, 10).until(lambda driver: self.find_share_publish_btn(driver))
            self.click_by_js(bro, publish_btn)
            self.wait_share_success(bro)
        except Exception as e:
            mylogger.error("[click_share 点击“分享” 出错 %s]" % e, exc_info=True)
            raise

    def focus_contenteditable_start(self, bro, ele):
        bro.execute_script("""
const ele = arguments[0];
ele.focus();
const range = document.createRange();
range.selectNodeContents(ele);
range.collapse(true);
const selection = window.getSelection();
selection.removeAllRanges();
selection.addRange(range);
""", ele)

    def wait_share_success(self, bro, timeout=20):
        def result(driver):
            modal = self.get_visible_elements(driver, '.bili-dyn-share-publishing')
            modal_text = " ".join(
                (element.get_attribute('innerText') or element.text or '').strip()
                for element in modal
            )
            fail_words = ["发布失败", "转发失败", "操作失败", "csrf", "验证码", "频繁"]
            if any(word in modal_text for word in fail_words):
                raise Exception("转发提交失败，页面提示：" + self.get_page_tip(modal_text))
            if "发布成功" in modal_text or "转发成功" in modal_text:
                return True
            if len(modal) == 0:
                message_text = self.get_visible_message_text(driver)
                if any(word in message_text for word in fail_words):
                    raise Exception("转发提交失败，页面提示：" + self.get_page_tip(message_text))
                return True
            return False

        WebDriverWait(bro, timeout).until(result)

    def get_visible_message_text(self, bro):
        messages = bro.find_elements(
            By.CSS_SELECTOR,
            '[role="alert"], [class*="toast"], [class*="Toast"], '
            '[class*="notification"], [class*="Notification"]'
        )
        return " ".join(
            (element.get_attribute('innerText') or element.text or '').strip()
            for element in messages
            if element.is_displayed()
        )

    def get_page_tip(self, body):
        text = body.replace('\n', ' ').strip()
        if len(text) > 200:
            return text[-200:]
        return text

    def find_share_publish_btn(self, bro):
        buttons = self.get_visible_elements(bro, self.SHARE_PUBLISH_CSS)
        for button in buttons:
            text = (button.get_attribute('innerText') or button.text or '').strip()
            cls = button.get_attribute('class') or ''
            if "发布" in text and "disabled" not in cls:
                return button
        return False

    def to_old_version(self, bro, chains):
        """
        新版转旧版
        :param bro:
        :param chains:
        :return:
        """
        mylogger.info("当前已使用新版动态页结构，跳过旧版切换")
