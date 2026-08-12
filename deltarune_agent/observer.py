import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from typing import Optional

import pyautogui
from PIL import Image


user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
PW_CLIENTONLY = 0x00000001
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
BI_RGB = 0


def frame_is_usable(frame: Image.Image) -> bool:
    """Reject blank bitmaps returned as successful Windows captures."""
    sample = frame.convert("RGB").resize((32, 24), Image.Resampling.BILINEAR)
    pixels = list(sample.getdata())
    almost_white = sum(min(pixel) >= 248 for pixel in pixels) / len(pixels)
    almost_black = sum(max(pixel) <= 3 for pixel in pixels) / len(pixels)
    return almost_white < 0.985 and almost_black < 0.995


class BitmapInfoHeader(ctypes.Structure):
    _fields_ = (
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    )


class BitmapInfo(ctypes.Structure):
    _fields_ = (("bmiHeader", BitmapInfoHeader),)


user32.GetDC.argtypes = (wintypes.HWND,)
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
user32.ReleaseDC.restype = ctypes.c_int
user32.GetClientRect.argtypes = (wintypes.HWND, ctypes.c_void_p)
user32.GetClientRect.restype = wintypes.BOOL
user32.PrintWindow.argtypes = (wintypes.HWND, wintypes.HDC, wintypes.UINT)
user32.PrintWindow.restype = wintypes.BOOL
gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = (
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
)
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.DeleteDC.argtypes = (wintypes.HDC,)
gdi32.DeleteDC.restype = wintypes.BOOL
gdi32.BitBlt.argtypes = (
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.DWORD,
)
gdi32.BitBlt.restype = wintypes.BOOL
gdi32.GetDIBits.argtypes = (
    wintypes.HDC,
    wintypes.HBITMAP,
    wintypes.UINT,
    wintypes.UINT,
    ctypes.c_void_p,
    ctypes.POINTER(BitmapInfo),
    wintypes.UINT,
)
gdi32.GetDIBits.restype = ctypes.c_int


def _capture_window_client(hwnd: int, *, bitblt: bool) -> Image.Image:
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        raise ctypes.WinError(ctypes.get_last_error())
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        raise OSError("Deltarune client area has no drawable size")
    window_dc = user32.GetDC(hwnd)
    if not window_dc:
        raise ctypes.WinError(ctypes.get_last_error())
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    if not memory_dc or not bitmap:
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(hwnd, window_dc)
        raise ctypes.WinError(ctypes.get_last_error())
    previous = gdi32.SelectObject(memory_dc, bitmap)
    try:
        if bitblt:
            # GetDC(hwnd) is the client-area DC. BitBlt gives us a second
            # Windows capture route when a GameMaker/DirectX window declines
            # PrintWindow while unfocused. It is deliberately attempted only
            # after PrintWindow fails and only for a background game window.
            drawn = gdi32.BitBlt(
                memory_dc,
                0,
                0,
                width,
                height,
                window_dc,
                0,
                0,
                SRCCOPY,
            )
        else:
            drawn = user32.PrintWindow(hwnd, memory_dc, PW_CLIENTONLY)
        if not drawn:
            raise ctypes.WinError(ctypes.get_last_error())
        info = BitmapInfo(
            BitmapInfoHeader(
                ctypes.sizeof(BitmapInfoHeader),
                width,
                -height,
                1,
                32,
                BI_RGB,
                width * height * 4,
                0,
                0,
                0,
                0,
            )
        )
        pixels = ctypes.create_string_buffer(width * height * 4)
        copied = gdi32.GetDIBits(
            memory_dc,
            bitmap,
            0,
            height,
            pixels,
            ctypes.byref(info),
            DIB_RGB_COLORS,
        )
        if copied != height:
            raise ctypes.WinError(ctypes.get_last_error())
        return Image.frombytes("RGB", (width, height), pixels.raw, "raw", "BGRX")
    finally:
        if previous:
            gdi32.SelectObject(memory_dc, previous)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(hwnd, window_dc)


def capture_window_client(hwnd: int) -> Image.Image:
    """Capture a client area through PrintWindow."""
    return _capture_window_client(hwnd, bitblt=False)


def capture_window_client_bitblt(hwnd: int) -> Image.Image:
    """Capture the client DC with BitBlt as a background fallback."""
    return _capture_window_client(hwnd, bitblt=True)


@dataclass(frozen=True)
class Observation:
    frame: Image.Image
    step: int
    visual_valid: bool = True


class ScreenObserver:
    def __init__(self, region: Optional[tuple[int, int, int, int]] = None):
        self.region = region
        self.window_hwnd: int | None = None
        self._last_window_frame: Image.Image | None = None
        self._capture_counts = {
            "print_window_successes": 0,
            "print_window_errors": 0,
            "print_window_unusable": 0,
            "bitblt_successes": 0,
            "bitblt_errors": 0,
            "bitblt_unusable": 0,
            "desktop_successes": 0,
            "desktop_unusable": 0,
            "stale_frame_reuses": 0,
            "blank_fallbacks": 0,
            "valid_frames": 0,
            "invalid_frames": 0,
        }

    def _accept(self, frame: Image.Image, step: int, method: str) -> Observation | None:
        if frame_is_usable(frame):
            self._capture_counts[f"{method}_successes"] += 1
            self._capture_counts["valid_frames"] += 1
            self._last_window_frame = frame
            return Observation(frame=frame, step=step)
        self._capture_counts[f"{method}_unusable"] += 1
        return None

    def observe(self, step: int) -> Observation:
        if self.window_hwnd is not None:
            frame: Image.Image | None = None
            try:
                frame = capture_window_client(self.window_hwnd)
            except OSError:
                self._capture_counts["print_window_errors"] += 1
            else:
                accepted = self._accept(frame, step, "print_window")
                if accepted is not None:
                    return accepted

            foreground = user32.GetForegroundWindow() == self.window_hwnd
            if foreground:
                desktop = pyautogui.screenshot(region=self.region)
                accepted = self._accept(desktop, step, "desktop")
                if accepted is not None:
                    return accepted
            else:
                # PrintWindow failed for long stretches in the 2026-08-12 live
                # run while telemetry proved that Kris kept moving. Try the
                # client DC before giving up and reusing a stale screenshot.
                try:
                    background = capture_window_client_bitblt(self.window_hwnd)
                except OSError:
                    self._capture_counts["bitblt_errors"] += 1
                else:
                    accepted = self._accept(background, step, "bitblt")
                    if accepted is not None:
                        return accepted
                    if frame is None:
                        frame = background

            self._capture_counts["invalid_frames"] += 1
            if self._last_window_frame is not None:
                self._capture_counts["stale_frame_reuses"] += 1
                return Observation(
                    frame=self._last_window_frame.copy(),
                    step=step,
                    visual_valid=False,
                )
            self._capture_counts["blank_fallbacks"] += 1
            size = frame.size if frame is not None else (
                (self.region[2], self.region[3]) if self.region else (320, 240)
            )
            return Observation(
                frame=Image.new("RGB", size),
                step=step,
                visual_valid=False,
            )
        return Observation(frame=pyautogui.screenshot(region=self.region), step=step)

    def diagnostics(self) -> dict[str, object]:
        counts = dict(self._capture_counts)
        total = int(counts["valid_frames"]) + int(counts["invalid_frames"])
        counts.update(
            {
                "window_capture_configured": self.window_hwnd is not None,
                "observed_window_frames": total,
                "visual_valid_ratio": (
                    float(counts["valid_frames"]) / total if total else None
                ),
            }
        )
        return counts

    def set_region(self, region: tuple[int, int, int, int]) -> None:
        self.region = region

    def set_window(self, hwnd: int, region: tuple[int, int, int, int]) -> None:
        self.window_hwnd = hwnd
        self.region = region
        if user32.GetForegroundWindow() == hwnd:
            frame = pyautogui.screenshot(region=region)
            if frame_is_usable(frame):
                self._last_window_frame = frame
