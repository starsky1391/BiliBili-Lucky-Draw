import os
import random

from dotenv import load_dotenv
from utils.defaults import DEFAULT_COMMENT_CONTENT, DEFAULT_SHARE_CONTENT
from utils.runtime_settings import get_max_checks, get_merged_ups

# 加载 .env 文件
load_dotenv(".env")


def get_env_int(name, default):
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return int(value)


def get_env_bool(name, default):
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().upper() in ("1", "Y", "YES", "TRUE", "ON")


# 读取变量
max_checks = get_max_checks()
home_url = os.getenv("home_url", "https://www.bilibili.com/")
my_user_id = os.getenv("my_user_id")
ignore_link = os.getenv("ignore_link", "")
multi_users = os.getenv("multi_users")
do_type = os.getenv("do_type")
is_remove = os.getenv("is_remove")
remove_cnt = os.getenv("remove_cnt")
cookie_value = os.getenv("cookie_value")
bili_jct = os.getenv("bili_jct")
if bili_jct is None or len(bili_jct) == 0:
    bili_jct = os.getenv("BILI_JCT")
# 读取变量
notify_switch = os.getenv("notify_switch")
FangTang_KEY = os.getenv("FangTang_KEY")
DRIVER_VERSION = os.getenv("DRIVER_VERSION")
schedule_interval_hours = get_env_int("SCHEDULE_INTERVAL_HOURS", 3)
cleanup_interval_hours = get_env_int("CLEANUP_INTERVAL_HOURS", 24)
auto_run_on_start = get_env_bool("AUTO_RUN_ON_START", True)
share_batch_limit = get_env_int("SHARE_BATCH_LIMIT", 0)
share_browser_recycle_every = get_env_int("SHARE_BROWSER_RECYCLE_EVERY", 20)
share_restart_error_limit = get_env_int("SHARE_RESTART_ERROR_LIMIT", 3)
selenium_page_load_timeout = get_env_int("SELENIUM_PAGE_LOAD_TIMEOUT", 60)
scan_cache_retry_hours = get_env_int("SCAN_CACHE_RETRY_HOURS", 24)
cleanup_enabled = get_env_bool("CLEANUP_ENABLED", False)
cleanup_dry_run = get_env_bool("CLEANUP_DRY_RUN", True)
unfollow_enabled = get_env_bool("UNFOLLOW_ENABLED", False)


def get_infos(str):
    users = os.getenv(str)
    return users


def get_random_comment_content():
    comments = get_multi_infos("comment_content")
    return get_random_from_list(comments)


def get_random_share_content():
    shares = get_multi_infos("share_content")
    return get_random_from_list(shares)


def get_multi_infos(str):
    defaults = {"share_content": DEFAULT_SHARE_CONTENT, "comment_content": DEFAULT_COMMENT_CONTENT}
    users = os.getenv(str, defaults.get(str, "")) or ""
    if len(users) != 0:
        return users.split('|')
    return {}


def get_random_from_list(list):
    if len(list) != 0:
        return random.choice(list)
    return "_"


share_content = get_random_share_content()
comment_content = get_random_comment_content()
ups = get_merged_ups()
