# core/screenguard.py
from __future__ import annotations
import sys
from typing import Optional

if sys.platform.startswith("win"):
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32

    # SetWindowDisplayAffinity constants
    WDA_NONE = 0x00000000
    WDA_MONITOR = 0x00000001                   # basic protection (Win7+)
    WDA_EXCLUDEFROMCAPTURE = 0x00000011        # real capture-block (Win10 2004+)

    # Define the EnumWindows callback prototype ourselves (this is the fix)
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    SetWindowDisplayAffinity = user32.SetWindowDisplayAffinity
    SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    SetWindowDisplayAffinity.restype = wintypes.BOOL

    IsWindow = user32.IsWindow
    IsWindow.argtypes = [wintypes.HWND]
    IsWindow.restype = wintypes.BOOL

    IsWindowVisible = user32.IsWindowVisible
    IsWindowVisible.argtypes = [wintypes.HWND]
    IsWindowVisible.restype = wintypes.BOOL

    GetWindowThreadProcessId = user32.GetWindowThreadProcessId
    GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    GetWindowThreadProcessId.restype = wintypes.DWORD

    EnumWindows = user32.EnumWindows
    EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    EnumWindows.restype = wintypes.BOOL

    def _each_toplevel_hwnd():
        def _collector():
            lst = []

            @WNDENUMPROC
            def enum_proc(hwnd, lparam):
                if IsWindow(hwnd) and IsWindowVisible(hwnd):
                    lst.append(hwnd)
                return True

            EnumWindows(enum_proc, 0)
            return lst

        return _collector()

    def _set_affinity(hwnd, mode: int) -> bool:
        try:
            return bool(SetWindowDisplayAffinity(hwnd, mode))
        except Exception:
            return False

    def _hwnd_from_qwindow(qwindow) -> Optional[int]:
        try:
            wid = int(qwindow.winId())
            return wid if wid else None
        except Exception:
            return None

else:
    # Non-Windows stubs
    def _each_toplevel_hwnd(): return []
    def _set_affinity(hwnd, mode: int) -> bool: return False
    def _hwnd_from_qwindow(qwindow) -> Optional[int]: return None
    WDA_NONE = 0
    WDA_MONITOR = 0x1
    WDA_EXCLUDEFROMCAPTURE = 0x11


def enable_guard(widget_or_window) -> bool:
    """Mark a given Qt top-level window as not capturable by OS capture APIs."""
    hwnd = _hwnd_from_qwindow(widget_or_window)
    if hwnd is None:
        return False
    # Try strongest; fall back to MONITOR if unavailable
    if not _set_affinity(hwnd, WDA_EXCLUDEFROMCAPTURE):
        _set_affinity(hwnd, WDA_MONITOR)
    return True


def disable_guard(widget_or_window) -> bool:
    hwnd = _hwnd_from_qwindow(widget_or_window)
    if hwnd is None:
        return False
    return _set_affinity(hwnd, WDA_NONE)


def enable_guard_for_all_toplevels(qapp) -> int:
    """Apply capture protection to every visible top-level window in this process."""
    count = 0
    for widget in qapp.topLevelWidgets():
        try:
            if enable_guard(widget):
                count += 1
        except Exception:
            pass
    return count
