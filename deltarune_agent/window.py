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
CONTROLLER_EXECUTABLES = {"python.exe", "pythonw.exe", "py.exe"}
GAME_EXECUTABLE_HINTS = ("deltarune", "survey_program", "survey-program")
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
user32.PostMessageW.argtypes = (
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)
user32.PostMessageW.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = (
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
)
user32.SetWindowPos.restype = wintypes.BOOL
user32.SystemParametersInfoW.argtypes = (
    wintypes.UINT,
    wintypes.UINT,
    wintypes.LPVOID,
    wintypes.UINT,
)
user32.SystemParametersInfoW.restype = wintypes.BOOL

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CLOSE = 0x0010
MAPVK_VK_TO_VSC = 0
VIRTUAL_KEYS = {
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "z": 0x5A,
    "x": 0x58,
    "c": 0x43,
    "f8": 0x77,
    "f9": 0x78,
    "f10": 0x79,
}
EXTENDED_KEYS = {"left", "up", "right", "down"}


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    executable: str
    process_id: int = 0


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
    """Return visible top-level windows, including windows with blank titles."""
    windows: list[WindowInfo] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        title = _title(hwnd).strip()
        executable = _executable(hwnd).strip()
        # Untitled GameMaker windows are valid targets. Keep them when their
        # executable can identify the process; discard only truly anonymous
        # handles that cannot be selected safely.
        if title or executable:
            windows.append(WindowInfo(hwnd, title, executable, _process_id(hwnd)))
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


def _candidate_windows() -> list[WindowInfo]:
    return [
        window
        for window in visible_windows()
        if window.title.casefold() not in CONTROLLER_TITLES
        and not (
            not window.title
            and window.executable.casefold() in CONTROLLER_EXECUTABLES
        )
    ]


def _find_known_window(
    windows: list[WindowInfo],
    known_path: Path | None,
    *,
    identifier: str | None = None,
) -> WindowInfo | None:
    needle = identifier.casefold() if identifier else None
    records = load_known_windows(known_path)
    for record in reversed(records):
        known_title = str(record.get("title", "")).casefold()
        known_executable = str(record.get("executable", "")).casefold()
        if needle is not None and not (
            needle in known_title or needle in known_executable
        ):
            continue
        for window in windows:
            if (
                known_executable
                and window.executable.casefold() == known_executable
            ) or (known_title and window.title.casefold() == known_title):
                return window
    return None


def _auto_detect_window(
    windows: list[WindowInfo],
    known_path: Path | None,
) -> WindowInfo | None:
    known = _find_known_window(windows, known_path)
    if known is not None:
        return known

    hinted = [
        window
        for window in windows
        if any(
            hint in window.executable.casefold()
            or hint in window.title.casefold()
            for hint in GAME_EXECUTABLE_HINTS
        )
    ]
    if len(hinted) == 1:
        return hinted[0]
    if hinted:
        return max(
            hinted,
            key=lambda window: (
                "deltarune" in window.executable.casefold(),
                bool(window.executable),
                bool(window.title),
            ),
        )

    # Do not guess among unrelated applications. A single remaining untitled
    # non-controller window is safe enough to select by process identity.
    untitled = [window for window in windows if not window.title and window.executable]
    return untitled[0] if len(untitled) == 1 else None


def find_window(identifier: str, known_path: Path | None = None) -> WindowInfo | None:
    needle = identifier.strip().casefold()
    windows = _candidate_windows()
    if not needle:
        return _auto_detect_window(windows, known_path)

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
    return _find_known_window(windows, known_path, identifier=needle)


def find_window_by_process_id(process_id: int) -> WindowInfo | None:
    """Select the visible top-level window owned by exactly one game process."""

    process_id = int(process_id)
    if process_id <= 0:
        raise ValueError("process_id must be positive")
    matches = [
        window
        for window in _candidate_windows()
        if window.process_id == process_id
    ]
    if not matches:
        return None
    return max(matches, key=lambda window: (bool(window.title), bool(window.executable)))


def wait_for_process_window(
    process_id: int,
    *,
    timeout: float = 15.0,
    poll_interval: float = 0.05,
) -> WindowInfo:
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        window = find_window_by_process_id(process_id)
        if window is not None:
            return window
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"Process {process_id} did not create a visible Deltarune window "
                f"within {max(0.0, float(timeout)):.1f} seconds."
            )
        time.sleep(max(0.01, float(poll_interval)))


def _window_label(window: WindowInfo) -> str:
    return f'{window.executable or "unknown"}: {window.title or "<untitled>"}'


def focus_window(identifier: str, known_path: Path | None = None) -> WindowInfo:
    window = find_window(identifier, known_path)
    if window is None:
        choices = "\n".join(f"  {_window_label(item)}" for item in visible_windows())
        requested = identifier.strip()
        target = f'containing "{requested}"' if requested else "by auto-detection"
        raise RuntimeError(
            f"No visible game window could be selected {target}.\n"
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


def post_window_key(hwnd: int, key: str, pressed: bool) -> None:
    """Post a key state directly to one window without typing elsewhere."""
    virtual_key = VIRTUAL_KEYS.get(key.casefold())
    if virtual_key is None:
        raise ValueError(f"unsupported targeted key {key!r}")
    scan_code = int(user32.MapVirtualKeyW(virtual_key, MAPVK_VK_TO_VSC))
    lparam = 1 | (scan_code << 16)
    if key.casefold() in EXTENDED_KEYS:
        lparam |= 1 << 24
    if not pressed:
        lparam |= (1 << 30) | (1 << 31)
    if not user32.PostMessageW(
        hwnd,
        WM_KEYDOWN if pressed else WM_KEYUP,
        virtual_key,
        lparam,
    ):
        raise ctypes.WinError(ctypes.get_last_error())


def close_window(hwnd: int) -> None:
    if not user32.PostMessageW(hwnd, WM_CLOSE, 0, 0):
        raise ctypes.WinError(ctypes.get_last_error())


def set_window_bounds(
    hwnd: int,
    left: int,
    top: int,
    width: int,
    height: int,
) -> None:
    """Move and size one game window without activating another AI's window."""

    SWP_NOACTIVATE = 0x0010
    SWP_SHOWWINDOW = 0x0040
    if width < 1 or height < 1:
        raise ValueError("window width and height must be positive")
    if not user32.SetWindowPos(
        hwnd,
        0,
        int(left),
        int(top),
        int(width),
        int(height),
        SWP_NOACTIVATE | SWP_SHOWWINDOW,
    ):
        raise ctypes.WinError(ctypes.get_last_error())


def tile_windows(windows: list[WindowInfo]) -> None:
    """Tile independent game windows across the usable primary desktop."""

    if not windows:
        return
    import math

    work = Rect()
    SPI_GETWORKAREA = 0x0030
    if not user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(work), 0):
        raise ctypes.WinError(ctypes.get_last_error())
    count = len(windows)
    columns = max(1, math.ceil(math.sqrt(count)))
    rows = max(1, math.ceil(count / columns))
    width = max(240, (work.right - work.left) // columns)
    height = max(180, (work.bottom - work.top) // rows)
    for index, window in enumerate(windows):
        column = index % columns
        row = index // columns
        set_window_bounds(
            window.hwnd,
            work.left + column * width,
            work.top + row * height,
            width,
            height,
        )


def client_region(hwnd: int) -> tuple[int, int, int, int]:
    rect = Rect()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        raise ctypes.WinError(ctypes.get_last_error())
    origin = Point(rect.left, rect.top)
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        raise ctypes.WinError(ctypes.get_last_error())
    return origin.x, origin.y, rect.right - rect.left, rect.bottom - rect.top
