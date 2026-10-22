import os
from pathlib import Path
import subprocess
import sys
import tempfile
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
executable = os.getenv("DESKTOP_EXECUTABLE")
command = [executable] if executable else [sys.executable, str(ROOT / "desktop.py")]
with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
    data = Path(temporary) / 'BiliBiliLuckyDraw'
    data.mkdir()
    db = sqlite3.connect(data / 'luckybili.sqlite3')
    db.executescript((ROOT / 'schema.sql').read_text(encoding='utf-8'))
    db.execute("INSERT INTO t_account(account_key,enabled) VALUES('123',1)")
    db.commit()
    db.close()
    result = subprocess.run(
        command + ["--smoke-test"],
        env=dict(os.environ, LOCALAPPDATA=temporary),
        capture_output=True, text=True, timeout=60,
    )
    print("WebView2 smoke exit:", result.returncode)
    if result.stdout:
        print(result.stdout[-1000:])
    if result.stderr:
        print(result.stderr[-2000:])
    if result.returncode:
        for path in (Path(temporary) / "BiliBiliLuckyDraw" / "Log").glob("*.log"):
            print(path.name, path.read_text(encoding="utf-8", errors="replace")[-2000:])
        raise SystemExit(result.returncode)
