from selenium.webdriver.support.wait import WebDriverWait
from selenium import webdriver
from selenium.webdriver import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from utils import globals



class ElementUtil(object):
    def get_element_by_xpath(bro, chains, path):
        """
        获取element的通用方法
        :param bro:
        :param chains:
        :param path:
        :return:
        """
        return WebDriverWait(bro, 10).until(EC.element_to_be_clickable((By.XPATH, path)))

    def get_elementArr_by_xpath(bro, chains, path):
        return WebDriverWait(bro, 10).until(EC.presence_of_all_elements_located((By.XPATH, path)))

    def is_xpath_exist(bro, chains, path):
        try:
            WebDriverWait(bro, 5).until(EC.element_to_be_clickable((By.XPATH, path)))
            return True
        except:
            return False

    def wait_to_go(bro, timeout=10):
        WebDriverWait(bro, timeout).until(EC.new_window_is_opened)

_drivers = set()
_driver_lock = __import__("threading").Lock()
_stopping = False


def init_webdriver():
    import os
    import subprocess
    from selenium.webdriver.edge.service import Service as EdgeService

    with _driver_lock:
        if _stopping:
            raise RuntimeError("桌面程序正在关闭")
        options = webdriver.EdgeOptions()
        options.page_load_strategy = "eager"
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1440,1000")
        options.add_argument("--lang=zh-CN")
        driver_path = os.getenv("EDGE_DRIVER_PATH")
        service = EdgeService(
            executable_path=driver_path,
            popen_kw={"creation_flags": subprocess.CREATE_NO_WINDOW},
        )
        driver = webdriver.Edge(service=service, options=options)
        driver.set_page_load_timeout(globals.selenium_page_load_timeout)
        _drivers.add(driver)
    return driver, ActionChains(driver)


def close_all_drivers():
    global _stopping
    with _driver_lock:
        _stopping = True
        drivers = list(_drivers)
        _drivers.clear()
    for driver in drivers:
        try:
            driver.quit()
        except Exception:
            pass
