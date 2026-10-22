import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@unittest.skipUnless(sys.platform == "win32", "Windows desktop process lifecycle")
class BackendTests(unittest.TestCase):
    def test_backend_startup_api_and_shutdown(self):
        from desktop import write_signal
        from windows_job import ProcessJob
        executable = os.getenv("DESKTOP_EXECUTABLE")
        command = [executable] if executable else [sys.executable, str(ROOT / "desktop.py")]
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            ready = data / "ready.json"
            environment = dict(os.environ, LOCALAPPDATA=str(data))
            with (data / "backend.log").open("w", encoding="utf-8") as output:
                process = subprocess.Popen(command + ["--backend", str(ready)],
                                           env=environment, stdout=output, stderr=output,
                                           creationflags=subprocess.CREATE_NO_WINDOW)
                job = ProcessJob()
                try:
                    job.assign(process)
                    write_signal(ready.with_suffix(".command"), "START")
                    deadline = time.monotonic() + 40
                    while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.1)
                    self.assertTrue(ready.exists(), (data / "backend.log").read_text(encoding="utf-8"))
                    port = json.loads(ready.read_text(encoding="utf-8"))["port"]
                    with urlopen(f"http://127.0.0.1:{port}/api/overview", timeout=5) as response:
                        self.assertEqual(json.load(response)["user_count"], 0)
                    with urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
                        self.assertIn(b"<html", response.read().lower())
                    self.assertTrue((data / "BiliBiliLuckyDraw" / "luckybili.sqlite3").is_file())
                    write_signal(ready.with_suffix(".command"), "STOP")
                    process.wait(timeout=10)
                    self.assertEqual(process.returncode, 0)
                finally:
                    job.close()
                    if process.poll() is None:
                        process.kill()
                    process.wait()

    def test_job_closes_owned_child(self):
        from windows_job import ProcessJob
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        job = ProcessJob()
        try:
            job.assign(process)
            job.close()
            process.wait(timeout=5)
            self.assertIsNotNone(process.returncode)
        finally:
            job.close()
            if process.poll() is None:
                process.kill()
                process.wait()


if __name__ == "__main__":
    unittest.main()
