# ui/guard_service.py
from __future__ import annotations
import sys
from typing import Set, Dict, List, Tuple

from PySide6 import QtCore
import psutil

if sys.platform.startswith("win"):
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    GetWindowTextW = user32.GetWindowTextW
    GetWindowTextLengthW = user32.GetWindowTextLengthW
    IsWindowVisible = user32.IsWindowVisible
    GetWindowThreadProcessId = user32.GetWindowThreadProcessId
    EnumWindows = user32.EnumWindows

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

else:
    # Stubs for non-Windows; we fall back to simple process-name matching
    ctypes = None
    WNDENUMPROC = None


# --- Watch lists (lowercase) ---

# Things we DO consider recorders (UI/front-end processes)
WATCH_PROCESSES: Set[str] = {
    # Popular desktop recorders
    "obs64.exe", "obs32.exe", "obs",
    "streamlabs obs.exe", "streamlabs.exe", "slobs",
    "xsplit.core.exe", "xsplit",
    "bandicam.exe", "camtasia.exe", "camtasiastudio.exe",
    "dxtory.exe", "action.exe", "snagit32.exe", "snagit64.exe",
    "sharex.exe", "screen2gif.exe",
    "screenflow", "kazam", "vokoscreen", "peek", "recordmydesktop",
    "simplescreenrecorder", "simple-screen-recorder",

    # Windows Game Bar frontends
    "gamebar.exe", "xboxgamebar.exe",

    # Nvidia / AMD / Intel UI components (overlay/frontends)
    "nvidia share.exe", "shadowplay.exe", "geforceexperience.exe",
    "amddvr.exe", "relive.exe", "amddvruser.exe",
    "arccontrol.exe", "intel-gpu-capture.exe",
}

# Background helpers we EXCLUDE to reduce false positives
EXCLUDE_BACKGROUND: Set[str] = {
    "nvcontainer.exe", "gamingservices.exe", "gamingservicesnet.exe",
    "system", "idle",
}

# Window title hints (secondary heuristic)
TITLE_HINTS: Set[str] = {
    "obs", "xsplit", "streamlabs", "game bar", "gamebar",
    "bandicam", "camtasia", "shadowplay", "geforce", "relive", "arc control",
}


class GuardService(QtCore.QObject):
    """
    Singleton that detects active screen recorders with low false positives.
    Emits:
      - recorderActiveChanged(bool)
      - recorderListChanged(list[str])  # readable process names that triggered lock
    """
    recorderActiveChanged = QtCore.Signal(bool)
    recorderListChanged = QtCore.Signal(list)

    _instance = None

    @classmethod
    def instance(cls) -> "GuardService":
        if cls._instance is None:
            cls._instance = GuardService()
        return cls._instance

    def __init__(self):
        super().__init__()
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(1500)  # light
        self._timer.timeout.connect(self._poll)
        self._active = False
        self._offenders: List[str] = []
        self._timer.start()

    @property
    def active(self) -> bool:
        return self._active

    def offenders(self) -> List[str]:
        return list(self._offenders)

    # ------------- core polling -------------
    def _poll(self):
        active, offenders = self._detect()
        changed = (active != self._active)
        list_changed = (offenders != self._offenders)

        if changed:
            self._active = active
            self.recorderActiveChanged.emit(active)

        if list_changed or changed:
            self._offenders = offenders
            self.recorderListChanged.emit(list(offenders))

    # ------------- detection -------------
    def _detect(self) -> Tuple[bool, List[str]]:
        """
        Returns (active, offenders).
        active=True only if we find a known recorder process AND it has at least one visible top-level window
        (or a top-level window title matches known hints). On non-Windows, we fall back to simple process-name check.
        """
        try:
            # Collect candidate PIDs by process name (excluding background helpers)
            candidates: Dict[int, str] = {}
            for p in psutil.process_iter(["pid", "name"]):
                name = (p.info.get("name") or "").lower()
                if not name or name in EXCLUDE_BACKGROUND:
                    continue
                if name in WATCH_PROCESSES:
                    candidates[p.info["pid"]] = name

            # No candidates → maybe a generic recorder with a telltale title?
            if not candidates:
                if sys.platform.startswith("win"):
                    titles = self._visible_titles()
                    joined = " | ".join(titles)
                    if any(h in joined for h in TITLE_HINTS):
                        return True, ["Unknown recorder (window title match)"]
                return False, []

            # Windows: confirm they actually have a visible window
            if sys.platform.startswith("win"):
                pid_with_window = self._pids_with_visible_windows()
                offenders = [n for pid, n in candidates.items() if pid in pid_with_window]
                if offenders:
                    return True, sorted(set(offenders))
                # Fallback: if no windows, try title hints
                titles = self._visible_titles()
                joined = " | ".join(titles)
                if any(h in joined for h in TITLE_HINTS):
                    return True, sorted(set(candidates.values()))
                return False, []

            # Non-Windows fallback: if process exists, treat as active
            return True, sorted(set(candidates.values()))

        except Exception:
            # Fail closed for security
            return True, ["Detector error"]

    # ---------- Windows helpers ----------
    def _visible_titles(self) -> List[str]:
        if not sys.platform.startswith("win"):
            return []
        titles: List[str] = []

        @WNDENUMPROC
        def enum_proc(hwnd, _lparam):
            if IsWindowVisible(hwnd):
                length = GetWindowTextLengthW(hwnd)
                if length > 0:
                    buff = ctypes.create_unicode_buffer(length + 1)
                    GetWindowTextW(hwnd, buff, length + 1)
                    t = (buff.value or "").strip()
                    if t:
                        titles.append(t.lower())
            return True

        try:
            EnumWindows(enum_proc, 0)
        except Exception:
            pass
        return titles

    def _pids_with_visible_windows(self) -> Set[int]:
        if not sys.platform.startswith("win"):
            return set()
        pids: Set[int] = set()

        @WNDENUMPROC
        def enum_proc(hwnd, _lparam):
            if IsWindowVisible(hwnd):
                pid = wintypes.DWORD(0)
                GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value:
                    pids.add(int(pid.value))
            return True

        try:
            EnumWindows(enum_proc, 0)
        except Exception:
            pass
        return pids
