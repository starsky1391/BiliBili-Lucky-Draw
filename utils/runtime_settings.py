import json
import os
from pathlib import Path
from threading import Lock


SETTINGS_PATH = Path(os.getenv("RUNTIME_SETTINGS_PATH", "/app/runtime_settings.json"))
VALID_CLEANUP_MODES = ("disabled", "delete_only", "delete_and_unfollow")
DEFAULT_MAX_CHECKS = 300
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
        data = _read_settings()
        mode = data.get("cleanup_mode")
        if mode in VALID_CLEANUP_MODES:
            return mode
        return cleanup_mode_from_env()


def set_cleanup_mode(mode):
    if mode not in VALID_CLEANUP_MODES:
        raise ValueError("无效的清理模式")
    with _lock:
        data = _read_settings()
        data["cleanup_mode"] = mode
        _write_settings(data)
    return mode


def get_max_checks():
    with _lock:
        data = _read_settings()
        value = data.get("max_checks")
        if isinstance(value, int) and value > 0:
            return value
        try:
            value = int(os.getenv("max_checks", DEFAULT_MAX_CHECKS))
        except (TypeError, ValueError):
            value = DEFAULT_MAX_CHECKS
        return value if value > 0 else DEFAULT_MAX_CHECKS


def set_max_checks(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ValueError("max_checks 必须是正整数")
    if value <= 0:
        raise ValueError("max_checks 必须是正整数")
    with _lock:
        data = _read_settings()
        data["max_checks"] = value
        _write_settings(data)
    return value


def _read_settings():
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, OSError, ValueError):
        return {}


def _write_settings(data):
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = SETTINGS_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(SETTINGS_PATH)
