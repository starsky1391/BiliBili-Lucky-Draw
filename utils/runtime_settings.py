import json
import os
from pathlib import Path
from threading import Lock


SETTINGS_PATH = Path(os.getenv("RUNTIME_SETTINGS_PATH", "/app/runtime_settings.json"))
VALID_CLEANUP_MODES = ("disabled", "delete_only", "delete_and_unfollow")
_lock = Lock()


def cleanup_mode_from_env():
    cleanup_enabled = os.getenv("CLEANUP_ENABLED", "").strip().upper() in (
        "1", "Y", "YES", "TRUE", "ON"
    )
    unfollow_enabled = os.getenv("UNFOLLOW_ENABLED", "").strip().upper() in (
        "1", "Y", "YES", "TRUE", "ON"
    )
    if not cleanup_enabled:
        return "disabled"
    return "delete_and_unfollow" if unfollow_enabled else "delete_only"


def get_cleanup_mode():
    with _lock:
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            mode = data.get("cleanup_mode")
            if mode in VALID_CLEANUP_MODES:
                return mode
        except (FileNotFoundError, OSError, ValueError):
            pass
        return cleanup_mode_from_env()


def set_cleanup_mode(mode):
    if mode not in VALID_CLEANUP_MODES:
        raise ValueError("无效的清理模式")
    with _lock:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = SETTINGS_PATH.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps({"cleanup_mode": mode}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(SETTINGS_PATH)
    return mode
