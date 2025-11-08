# core/recorder_watch.py
from __future__ import annotations

import sys
from typing import Set

import psutil

# Process name patterns (lowercase)
RECORDER_PROCS: Set[str] = {
    # common
    "obs64.exe", "obs32.exe", "obs", "streamlabs obs", "streamlabs.exe",
    "xsplit.core.exe", "xsplit", "bandicam.exe", "bandicam",
    "camtasia.exe", "camtasiastudio.exe", "snagit32.exe", "snagit64.exe",
    "dxtory.exe", "action.exe",
    # linux/mac names (cross-OS)
    "simple-screen-recorder", "simplescreenrecorder", "kazam",
    "vokoscreen", "peek", "recordmydesktop", "screenflow",
    # conferencing/sharing (best effort)
    "zoom.exe", "zoom", "teams.exe", "ms-teams", "ms-teams.exe",
    "meet", "webex", "webexmta.exe", "webexmta",
}

# Common capture hook/injected DLLs (OBS & friends)
# We only scan *our own* process modules (reliable and cheap);
# many “Game Capture” modes inject into target window’s process.
HOOK_DLL_HINTS: Set[str] = {
    "graphics-hook32.dll", "graphics-hook64.dll",
    "obs-graphics-hook32.dll", "obs-graphics-hook64.dll",
    "win-capture.dll", "capture.dll", "d3d9.dll", "dxgi.dll",
}

_is_windows = sys.platform.startswith("win")

if _is_windows:
    import ctypes
    from ctypes import wintypes

    Psapi = ctypes.WinDLL("Psapi.dll")
    Kernel32 = ctypes.WinDLL("kernel32.dll")

    GetCurrentProcess = Kernel32.GetCurrentProcess
    GetCurrentProcess.restype = wintypes.HANDLE

    EnumProcessModules = Psapi.EnumProcessModules
    EnumProcessModules.argtypes = [wintypes.HANDLE,
                                   ctypes.POINTER(wintypes.HMODULE),
                                   wintypes.DWORD,
                                   ctypes.POINTER(wintypes.DWORD)]
    EnumProcessModules.restype = wintypes.BOOL

    GetModuleFileNameExW = Psapi.GetModuleFileNameExW
    GetModuleFileNameExW.argtypes = [wintypes.HANDLE,
                                     wintypes.HMODULE,
                                     wintypes.LPWSTR,
                                     wintypes.DWORD]
    GetModuleFileNameExW.restype = wintypes.DWORD


def _any_process_matches() -> bool:
    """Scan running processes for well-known recorder names (best effort)."""
    try:
        for proc in psutil.process_iter(["name", "exe", "cmdline"]):
            name = (proc.info.get("name") or "").lower()
            exe = (proc.info.get("exe") or "").lower()
            cmd = " ".join(proc.info.get("cmdline") or []).lower()
            blob = f"{name} {exe} {cmd}"
            for tag in RECORDER_PROCS:
                if tag in blob:
                    return True
    except Exception:
        # If we cannot enumerate, fail-open (don’t block everything)
        return False
    return False


def _self_has_hook_dll() -> bool:
    """Check our own process modules for known capture DLLs (Windows only)."""
    if not _is_windows:
        return False
    try:
        h_proc = GetCurrentProcess()
        needed = wintypes.DWORD(0)
        # First call to get required size
        EnumProcessModules(h_proc, None, 0, ctypes.byref(needed))
        count = needed.value // ctypes.sizeof(wintypes.HMODULE)
        arr = (wintypes.HMODULE * count)()
        if not EnumProcessModules(h_proc, arr, needed, ctypes.byref(needed)):
            return False
        buf = ctypes.create_unicode_buffer(260)
        for i in range(count):
            GetModuleFileNameExW(h_proc, arr[i], buf, 260)
            path = buf.value.lower()
            for k in HOOK_DLL_HINTS:
                if k in path:
                    return True
    except Exception:
        return False
    return False


def recorder_or_hook_present() -> bool:
    """High-level signal used by UI to lock sensitive actions."""
    return _any_process_matches() or _self_has_hook_dll()
