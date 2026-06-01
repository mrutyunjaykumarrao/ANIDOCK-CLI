import ctypes
import hashlib
import json
import os
import sys
import threading
import time

from core.input_parsing import sanitize_file_part


def make_manifest_path(output_dir: str, anime_title: str) -> str:
    safe_title = sanitize_file_part(anime_title)
    if os.name == "nt" and getattr(sys, "frozen", False):
        base_dir = os.getenv("LOCALAPPDATA") or os.path.join(
            os.path.expanduser("~"),
            "AppData",
            "Local",
        )
        output_key = os.path.abspath(output_dir).encode("utf-8")
        output_hash = hashlib.sha1(output_key).hexdigest()[:8]
        preferred_dir = os.path.join(base_dir, "AniDock", "manifests")
        manifest_dir = preferred_dir if _is_directory_writable(preferred_dir) else output_dir
        return os.path.join(manifest_dir, "{0}.{1}.json".format(safe_title, output_hash))
    return os.path.join(output_dir, ".{0}.download_manifest.json".format(safe_title))


def default_manifest(anime_title: str) -> dict:
    return {
        "version": 1,
        "anime_title": anime_title,
        "updated_at": int(time.time()),
        "queue": [],
        "status_by_episode": {},
        "completed_files": {},
        "failed_errors": {},
        "completed_segments": {},
        "settings": {},
    }


def load_manifest(manifest_path: str, anime_title: str) -> dict:
    if not os.path.exists(manifest_path):
        return default_manifest(anime_title)
    with open(manifest_path, "r", encoding="utf-8") as manifest_file:
        payload = json.load(manifest_file)
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("Unsupported manifest format at {0}".format(manifest_path))
    payload.setdefault("anime_title", anime_title)
    payload.setdefault("queue", [])
    payload.setdefault("status_by_episode", {})
    payload.setdefault("completed_files", {})
    payload.setdefault("failed_errors", {})
    payload.setdefault("completed_segments", {})
    payload.setdefault("settings", {})
    return payload


def save_manifest(manifest_path: str, manifest_data: dict) -> None:
    manifest_data["updated_at"] = int(time.time())
    os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
    _clear_readonly_on_windows(manifest_path)
    with open(manifest_path, "w", encoding="utf-8") as manifest_file:
        json.dump(manifest_data, manifest_file, indent=2, sort_keys=True)
    _hide_manifest_on_windows(manifest_path)


def _hide_manifest_on_windows(manifest_path: str) -> None:
    if os.name != "nt":
        return
    get_attributes = ctypes.windll.kernel32.GetFileAttributesW
    get_attributes.argtypes = [ctypes.c_wchar_p]
    get_attributes.restype = ctypes.c_uint32
    set_attributes = ctypes.windll.kernel32.SetFileAttributesW
    set_attributes.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
    set_attributes.restype = ctypes.c_int
    attributes = get_attributes(manifest_path)
    if attributes == 0xFFFFFFFF:
        print("Warning: could not read manifest attributes for hiding: {0}".format(manifest_path))
        return
    hidden_flag = 0x2
    if attributes & hidden_flag:
        return
    if not set_attributes(manifest_path, attributes | hidden_flag):
        print("Warning: could not hide manifest file: {0}".format(manifest_path))


def _clear_readonly_on_windows(manifest_path: str) -> None:
    if os.name != "nt" or not os.path.exists(manifest_path):
        return
    get_attributes = ctypes.windll.kernel32.GetFileAttributesW
    get_attributes.argtypes = [ctypes.c_wchar_p]
    get_attributes.restype = ctypes.c_uint32
    set_attributes = ctypes.windll.kernel32.SetFileAttributesW
    set_attributes.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
    set_attributes.restype = ctypes.c_int
    attributes = get_attributes(manifest_path)
    if attributes == 0xFFFFFFFF:
        return
    readonly_flag = 0x1
    if not (attributes & readonly_flag):
        return
    if not set_attributes(manifest_path, attributes & ~readonly_flag):
        print("Warning: could not clear manifest read-only attribute: {0}".format(manifest_path))


def _is_directory_writable(directory_path: str) -> bool:
    try:
        os.makedirs(directory_path, exist_ok=True)
        probe_path = os.path.join(directory_path, ".anidock_write_probe")
        with open(probe_path, "w", encoding="utf-8") as probe_file:
            probe_file.write("ok")
        os.remove(probe_path)
        return True
    except OSError:
        return False


def set_manifest_episode_status(
    manifest_path: str,
    manifest_data: dict,
    manifest_lock: threading.Lock,
    episode_number: int,
    status: str,
    error_message: str | None = None,
    final_file: str | None = None,
) -> None:
    with manifest_lock:
        manifest_data["status_by_episode"][str(episode_number)] = status
        if error_message:
            manifest_data["failed_errors"][str(episode_number)] = error_message
        else:
            manifest_data["failed_errors"].pop(str(episode_number), None)
        if final_file:
            manifest_data["completed_files"][str(episode_number)] = final_file
        save_manifest(manifest_path, manifest_data)


def mark_segment_complete(
    manifest_path: str,
    manifest_data: dict,
    manifest_lock: threading.Lock,
    episode_number: int,
    segment_index: int,
) -> None:
    with manifest_lock:
        key = str(episode_number)
        segment_values = manifest_data["completed_segments"].setdefault(key, [])
        if segment_index not in segment_values:
            segment_values.append(segment_index)
            segment_values.sort()
        save_manifest(manifest_path, manifest_data)


def get_manifest_completed_segments(manifest_data: dict, episode_number: int) -> set[int]:
    values = manifest_data.get("completed_segments", {}).get(str(episode_number), [])
    return {int(item) for item in values if isinstance(item, int)}
