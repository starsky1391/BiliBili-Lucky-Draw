from service.log_service.log_printer_service import MyLogger
from service.search_draw_dynamic_service.SearchDynamicByUps import SearchDynamicByUps
from service.share_service.share_from_biliLink import BiliLinkShare
from service.auth_service import AuthenticationRequiredError
from service.account_service import enabled_account_keys

mylogger = MyLogger('multi_users_share.py').getLogger()


class MultiUsersShareService(object):
    """
    多用户转发模式
    """

    def __init__(self):
        mylogger.info('启动多用户转发模式!')


    def do_multi_uses_share(self, users=None):
        summaries = []
        users = users if users is not None else self.get_multi_uses()
        for user in users:
            try:
                mylogger.info('用户: ' + user + '开始转发动态.')
                BiliLinkShare(user).do_share_by_links()
                summaries.append({"account": user, "status": "completed"})
            except AuthenticationRequiredError as exc:
                mylogger.info("用户 %s 登录失效，暂停该用户任务：%s", user, exc)
                summaries.append({"account": user, "status": "paused", "error": str(exc)})
            except Exception as e:
                mylogger.error("[do_multi_uses_share 用户 %s 出错 %s]", user, e, exc_info=True)
                summaries.append({"account": user, "status": "failed", "error": str(e)})
        return summaries

    def get_multi_uses(self):
        return enabled_account_keys()
