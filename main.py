import time
import threading
import schedule
from service.search_draw_dynamic_service.SearchDynamicByUps import SearchDynamicByUps
from service.share_service.multi_users_share import MultiUsersShareService
from service.log_service.log_printer_service import MyLogger
from utils import globals
from service.cleanup_service.expired_share_cleanup import ExpiredShareCleanup
from service.cleanup_service.backfill_account_dynamics import AccountDynamicBackfill

mylogger = MyLogger('main.py').getLogger()
task_lock = threading.Lock()


def do_search():
    SearchDynamicByUps(globals.my_user_id).init_search()
def do_share():
    MultiUsersShareService().do_multi_uses_share()


def collect_and_share():
    do_search()
    do_share()


def cleanup_expired():
    for user in MultiUsersShareService().get_multi_uses():
        try:
            sync_summary = AccountDynamicBackfill(user).sync_new_personal_forwards()
            mylogger.info("个人动态增量同步完成：%s", sync_summary)
        except Exception as e:
            mylogger.error("个人动态同步失败，跳过本次删除和取关：%s", e, exc_info=True)
            continue
        ExpiredShareCleanup(user).run()


def run_job(job_name, job):
    if not task_lock.acquire(blocking=False):
        mylogger.warning("任务正在执行，跳过本次调度：" + job_name)
        return
    try:
        mylogger.info("任务开始：" + job_name)
        job()
        mylogger.info("任务结束：" + job_name)
    except Exception as e:
        mylogger.error("[任务执行出错 %s: %s]" % (job_name, e), exc_info=True)
    finally:
        task_lock.release()


def run_job_background(job_name, job):
    thread = threading.Thread(target=run_job, args=(job_name, job), daemon=True)
    thread.start()
    return thread


if __name__ == '__main__':
    time.sleep(15)
    schedule.every(globals.schedule_interval_hours).hours.do(
        run_job_background,
        "scheduled_collect_and_share",
        collect_and_share
    )
    schedule.every(globals.cleanup_interval_hours).hours.do(
        run_job_background, "scheduled_cleanup_expired", cleanup_expired
    ).tag("scheduled_cleanup_expired")
    mylogger.info("收集转发任务每%s小时执行一次，删除动态和取关任务每%s小时执行一次"
                  % (globals.schedule_interval_hours, globals.cleanup_interval_hours))
    if globals.auto_run_on_start:
        run_job_background("startup_collect_and_share", collect_and_share)
    while True:
        try:
            schedule.run_pending()
            time.sleep(1)
        except Exception as e:
            mylogger.error("[调度循环出错 %s]" % e, exc_info=True)
            time.sleep(1)



