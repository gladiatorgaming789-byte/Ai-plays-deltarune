import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from pathlib import PureWindowsPath
import time


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
CONTROLLER_TITLES = {"deltarune ai controller"}
kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = (
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
)
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel32.CloseHandle.restype = wintypes.BOOL


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    executable: str


class Point(ctypes.Structure):
    _fields_ = (("x", wintypes.LONG), ("y", wintypes.LONG))


class Rect(ctypes.Structure):
    _fields_ = (
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    )


def _title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value


def _executable(hwnd: int) -> str:
    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, process_id.value)
    if not process:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
            return PureWindowsPath(buffer.value).name
        return ""
    finally:
        kernel32.CloseHandle(process)


def _process_id(hwnd: int) -> int:
    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    return int(process_id.value)


def visible_windows() -> list[WindowInfo]:
    windows: list[WindowInfo] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd: int, _lparam: int) -> bool:
        title = _title(hwnd).strip()
        if user32.IsWindowVisible(hwnd) and title:
            windows.append(WindowInfo(hwnd, title, _executable(hwnd)))
        return True

    user32.EnumWindows(callback, 0)
    return windows


def load_known_windows(path: Path | None) -> list[dict[str, object]]:
    if path is None or not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != 1 or not isinstance(data.get("windows"), list):
            return []
        return [item for item in data["windows"] if isinstance(item, dict)]
    except (OSError, json.JSONDecodeError, TypeError):
        return []


def remember_window(path: Path | None, window: WindowInfo) -> None:
    if path is None:
        return
    records = load_known_windows(path)
    title_key = window.title.casefold()
    executable_key = window.executable.casefold()
    match = next(
        (
            item
            for item in records
            if str(item.get("title", "")).casefold() == title_key
            and str(item.get("executable", "")).casefold() == executable_key
        ),
        None,
    )
    now = datetime.now(timezone.utc).isoformat()
    if match is None:
        records.append(
            {
                "title": window.title,
                "executable": window.executable,
                "first_seen": now,
                "last_seen": now,
                "seen_count": 1,
            }
        )
    else:
        match["last_seen"] = now
        match["seen_count"] = int(match.get("seen_count", 0)) + 1
    data = {"version": 1, "windows": records}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temporary.replace(path)


def find_window(identifier: str, known_path: Path | None = None) -> WindowInfo | None:
    needle = identifier.casefold()
    windows = [
        window
        for window in visible_windows()
        if window.title.casefold() not in CONTROLLER_TITLES
    ]
    direct_matches: list[tuple[int, WindowInfo]] = []
    for window in windows:
        title = window.title.casefold()
        executable = window.executable.casefold()
        score = 0
        if executable == needle:
            score = 400
        elif needle in executable:
            score = 300
        elif title == needle:
            score = 200
        elif needle in title:
            score = 100
        if score:
            direct_matches.append((score, window))
    if direct_matches:
        return max(direct_matches, key=lambda item: item[0])[1]
    matching_records = [
        item
        for item in load_known_windows(known_path)
        if needle in str(item.get("title", "")).casefold()
        or needle in str(item.get("executable", "")).casefold()
    ]
    for record in reversed(matching_records):
        known_title = str(record.get("title", "")).casefold()
        known_executable = str(record.get("executable", "")).casefold()
        for window in windows:
            if (
                known_executable
                and window.executable.casefold() == known_executable
            ) or (known_title and window.title.casefold() == known_title):
                return window
    return None


def focus_window(identifier: str, known_path: Path | None = None) -> WindowInfo:
    window = find_window(identifier, known_path)
    if window is None:
        choices = "\n".join(
            f'  {item.executable or "unknown"}: {item.title}' for item in visible_windows()
        )
        raise RuntimeError(
            f'No visible window or executable containing "{identifier}" was found.\n'
            f"Available windows:\n{choices}"
        )
    user32.ShowWindow(window.hwnd, 9)  # SW_RESTORE
    for _attempt in range(8):
        user32.BringWindowToTop(window.hwnd)
        user32.SetForegroundWindow(window.hwnd)
        if is_window_foreground(window):
            break
        time.sleep(0.05)
    return window


def foreground_title() -> str:
    return _title(user32.GetForegroundWindow())


def foreground_handle() -> int:
    return user32.GetForegroundWindow()


def is_window_foreground(window: WindowInfo) -> bool:
    foreground = foreground_handle()
    return foreground == window.hwnd or (
        foreground != 0 and _process_id(foreground) == _process_id(window.hwnd)
    )


def foreground_description() -> str:
    foreground = foreground_handle()
    if not foreground:
        return "no foreground window"
    return f'{_executable(foreground) or "unknown"}: {_title(foreground) or "untitled"}'


def client_region(hwnd: int) -> tuple[int, int, int, int]:
    rect = Rect()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        raise ctypes.WinError(ctypes.get_last_error())
    origin = Point(rect.left, rect.top)
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        raise ctypes.WinError(ctypes.get_last_error())
    return origin.x, origin.y, rect.right - rect.left, rect.bottom - rect.top
