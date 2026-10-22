import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["SE_CACHE_PATH"] = str(ROOT / ".browser-cache")
os.environ["SE_AVOID_BROWSER_DOWNLOAD"] = "true"

from utils import webdriver_util

driver, _ = webdriver_util.init_webdriver()
try:
    driver.get("data:text/html,<title>Desktop smoke test</title><canvas width='180' height='180'></canvas>")
    assert driver.title == "Desktop smoke test"
    arguments = driver.execute_cdp_cmd("Browser.getBrowserCommandLine", {})["arguments"]
    assert "--headless=new" in arguments
    assert driver.find_element("tag name", "canvas").screenshot_as_png.startswith(b"\x89PNG\r\n\x1a\n")
    print("Headless Edge startup, navigation and QR element screenshot OK")
finally:
    webdriver_util.close_all_drivers()
