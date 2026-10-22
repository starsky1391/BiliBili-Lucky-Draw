import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time


def write_signal(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def prepare_runtime():
    data = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "BiliBiliLuckyDraw"
    data.mkdir(parents=True, exist_ok=True)
    for folder in ("cookie", "Log"):
        (data / folder).mkdir(exist_ok=True)
    os.chdir(data)
    os.environ["COOKIE_DIR"] = str(data / "cookie")
    os.environ["RUNTIME_SETTINGS_PATH"] = str(data / "runtime_settings.json")
    os.environ["SQLITE_PATH"] = str(data / "luckybili.sqlite3")
    os.environ.setdefault("AUTO_RUN_ON_START", "false")
    if sys.stdout is None:
        sys.stdout = (data / "Log" / "desktop.log").open("a", encoding="utf-8", buffering=1)
    if sys.stderr is None:
        sys.stderr = sys.stdout
    return data


def run_backend(ready_file):
    # File IPC also works in a windowed executable without stdin/stdout handles.
    command_file = Path(ready_file).with_suffix(".command")
    deadline = time.monotonic() + 30
    while not command_file.is_file():
        if time.monotonic() >= deadline:
            return
        time.sleep(0.1)
    if command_file.read_text(encoding="utf-8") != "START":
        return
    prepare_runtime()
    import socket
    import schedule
    import uvicorn
    from dao.init_db import initialize_database
    initialize_database()
    from admin_web.main import app
    import main as tasks

    stop = threading.Event()
    scheduler = schedule.Scheduler()
    scheduler.every(tasks.globals.schedule_interval_hours).hours.do(
        tasks.run_job_background, "scheduled_collect_and_share", tasks.collect_and_share)
    scheduler.every(tasks.globals.cleanup_interval_hours).hours.do(
        tasks.run_job_background, "scheduled_cleanup_expired", tasks.cleanup_expired)

    def schedule_loop():
        while not stop.wait(1):
            scheduler.run_pending()

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="info", access_log=False))

    def signal_ready():
        while not server.started and not stop.wait(0.05):
            pass
        if not stop.is_set():
            write_signal(Path(ready_file), json.dumps({"port": port}))

    def wait_for_shutdown():
        while not stop.wait(0.2):
            if command_file.read_text(encoding="utf-8") == "STOP":
                break
        stop.set()
        server.should_exit = True

    threading.Thread(target=signal_ready, daemon=True).start()
    threading.Thread(target=wait_for_shutdown, daemon=True).start()
    threading.Thread(target=schedule_loop, daemon=True).start()
    try:
        server.run(sockets=[sock])
    finally:
        stop.set()
        from utils.webdriver_util import close_all_drivers
        close_all_drivers()
        sock.close()


def launch(smoke_test=False):
    from windows_job import ProcessJob
    import webview

    webview.settings['ALLOW_DOWNLOADS'] = True

    data = prepare_runtime()
    command = [sys.executable]
    if not getattr(sys, "frozen", False):
        command.append(str(Path(__file__).resolve()))
    with tempfile.TemporaryDirectory(prefix="desktop-", dir=data) as temporary:
        ready = Path(temporary) / "ready.json"
        with (data / "Log" / "desktop.log").open("a", encoding="utf-8") as output:
            process = subprocess.Popen(
                command + ["--backend", str(ready)],
                stdout=output, stderr=output, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            job = None
            try:
                job = ProcessJob()
                job.assign(process)
                write_signal(ready.with_suffix(".command"), "START")
                deadline = time.monotonic() + 45
                while not ready.exists():
                    if process.poll() is not None:
                        raise RuntimeError(f"后台启动失败，请查看 {data / 'Log' / 'desktop.log'}")
                    if time.monotonic() >= deadline:
                        raise RuntimeError("后台启动超时，请查看桌面端日志")
                    time.sleep(0.1)
                port = json.loads(ready.read_text(encoding="utf-8"))["port"]
                window = webview.create_window(
                    "Bilibili 抽奖助手", f"http://127.0.0.1:{port}",
                    width=1280, height=860, min_size=(960, 640), hidden=smoke_test,
                    text_select=True)
                smoke_result = {}

                def check_window():
                    try:
                        if not window.events.loaded.wait(20):
                            raise RuntimeError("WebView2 页面加载超时")
                        if not window.evaluate_js("document.querySelector('#userList') !== null"):
                            raise RuntimeError("桌面管理页面未加载")
                        logs_result = {}
                        logs_done = threading.Event()

                        def receive_logs_result(result):
                            logs_result["value"] = result
                            logs_done.set()

                        window.evaluate_js("""(async () => {
                            showTab('users');
                            await refreshUsers();
                            const group = document.querySelector('.user-tasks[data-uid="123"]');
                            const defaults = group && group.querySelectorAll('input:checked').length === 2 &&
                                group.querySelector('[data-task="collect"]').checked &&
                                !group.querySelector('[data-task="backfill_personal"]').checked &&
                                group.querySelector('[data-task="cleanup"]').checked;
                            if (!defaults) return {error: 'task defaults'};
                            const checkbox = group.querySelector('[data-task="collect"]');
                            checkbox.checked = false;
                            checkbox.dispatchEvent(new Event('change'));
                            await refreshUsers();
                            const unsaved = !group.querySelector('[data-task="collect"]').checked;
                            await saveUserTasks('123');
                            const users = await getJson('/api/users');
                            const tasks = unsaved && users.items[0].tasks.collect === false;
                            const download = await fetch('/api/backups/export', {method: 'POST'});
                            const blob = await download.blob();
                            const transfer = new DataTransfer();
                            transfer.items.add(new File([blob], 'smoke.bilidraw'));
                            document.querySelector('#backupFile').files = transfer.files;
                            await document.querySelector('#backupFile').onchange({target: document.querySelector('#backupFile')});
                            const previewed = !document.querySelector('#importBackup').disabled;
                            const originalConfirm = window.confirm;
                            window.confirm = () => true;
                            try { await document.querySelector('#importBackup').onclick(); }
                            finally { window.confirm = originalConfirm; }
                            const backup = previewed && document.querySelector('#backupStatus').textContent.includes('导入完成');
                            showTab('logs');
                            while (logsLoading) await new Promise(resolve => setTimeout(resolve, 20));
                            const box = document.querySelector('#logBox');
                            box.innerHTML = logMarkup('Log selection smoke test');
                            const selection = window.getSelection();
                            const range = document.createRange();
                            range.selectNodeContents(box);
                            selection.removeAllRanges();
                            selection.addRange(range);
                            const selected = selection.toString();
                            await refreshLogs();
                            const preserved = selected.length > 0 && selection.toString() === selected;
                            const selectable = getComputedStyle(box).userSelect !== 'none';
                            selection.removeAllRanges();
                            await refreshLogs();
                            const resumed = document.querySelector('#logRefreshState').textContent.includes('实时刷新中');
                            return {tasks, backup, preserved, selectable, resumed};
                        })()""", callback=receive_logs_result)
                        logs_done.wait(20)
                        logs_ok = logs_result.get("value")
                        if not isinstance(logs_ok, dict) or not all(
                            logs_ok.get(key) is True for key in ("tasks", "backup", "preserved", "selectable", "resumed")
                        ):
                            raise RuntimeError(f"日志选择或刷新恢复验证失败：{logs_ok}")
                        smoke_result["loaded"] = True
                    except Exception as error:
                        smoke_result["error"] = str(error)
                    finally:
                        window.destroy()

                webview.start(check_window if smoke_test else None, gui="edgechromium",
                              storage_path=str(data / "webview"))
                if smoke_test and not smoke_result.get("loaded"):
                    raise RuntimeError(smoke_result.get("error", "桌面窗口验证失败"))
            finally:
                if process.poll() is None:
                    try:
                        write_signal(ready.with_suffix(".command"), "STOP")
                        process.wait(timeout=5)
                    except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                        pass
                if job is not None:
                    job.close()
                if process.poll() is None:
                    process.kill()
                process.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.backend:
        run_backend(args.backend)
    else:
        try:
            launch(smoke_test=args.smoke_test)
        except Exception as error:
            if not args.smoke_test:
                import ctypes
                ctypes.windll.user32.MessageBoxW(None, str(error), "Bilibili 抽奖助手启动失败", 0x10)
            raise
