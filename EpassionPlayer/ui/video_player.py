from __future__ import annotations

from pathlib import Path
from typing import Optional, List, Set
import time

from PySide6 import QtCore, QtGui, QtWidgets
import psutil

from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget

from core.crypto import safe_delete
from core.screenguard import enable_guard, disable_guard

# ---- Light theme palette (match app) ----
PRIMARY = "#22ABE1"
TEXT_DARK = "#0B1221"
TEXT_MUTED = "#536077"
BG = "#FFFFFF"
BORDER = "#E7ECF2"
DANGER = "#E53935"
HOVER_BG = "#F5FAFF"

# ---------------- Normalization helpers ----------------
def _stem(name: str) -> str:
    n = (name or "").strip().lower()
    if n.endswith(".exe"):
        n = n[:-4]
    return n

def _squash(s: str) -> str:
    return s.replace(" ", "").replace("-", "").replace("_", "").replace(".", "")

def _norm(s: str) -> str:
    return _squash(_stem(s))

# ---------------- Matcher sets (pre-normalized) ----------------
STRICT_RECORDERS_RAW: Set[str] = {
    # Popular
    "obs64", "obs32", "obs",
    "xsplitcore", "xsplit", "xsplitbroadcaster", "xsplitgamecaster",
    "bandicam", "bdcam",
    "camtasia", "camtasiastudio",
    "streamlabs", "slobs", "streamlabsobs",
    "dxtory", "action",
    "snagit32", "snagit64", "snagit",
    "screenflow", "kazam",
    "simplescreenrecorder", "simplescreenrecorderflatpak",
    "vokoscreen", "recordmydesktop", "peek",
    "sharex", "screen2gif",
    "loilo", "flashbackexpress", "flashbackpro",
    "icecreamscreenrecorder",
    # GPU suites
    "nvidiashare", "shadowplay", "geforceexperience", "nvoverlay",
    "amddvr", "amddvr64", "amddvruser", "relive",
    "arccontrol", "intelgpucapture", "intel-gpu-capture", "intelcaptureservice",
}
STRICT_N = { _norm(x) for x in STRICT_RECORDERS_RAW }

# Helpers that should not trigger by themselves
IGNORE_SOLO_RAW = {
    "nvcontainer", "nvsphelper64", "nvsphelper",
    "gamingservices", "gamingservicesnet",
    "explorer", "runtimebroker",
}
IGNORE_SOLO_N = { _norm(x) for x in IGNORE_SOLO_RAW }

# Xbox Game Bar heuristic
GBAR_PRIMARY_RAW = { "gamebarui", "gamebarft"}
GBAR_HINTS_RAW   = {"broadcastdvrserver", "gamebarftserver", "recordingindicatorsvc", "recordingindicator", "gamerecorder", "dvr"}
GBAR_PRIMARY_N = { _norm(x) for x in GBAR_PRIMARY_RAW }
GBAR_HINTS_N   = { _norm(x) for x in GBAR_HINTS_RAW }

# Pre-filter substrings to decide which processes merit deeper inspection
CANDIDATE_SUBSTRINGS = tuple([
    "obs", "xsplit", "bandi", "camtasia", "stream", "dxtory", "snagit", "screen",
    "record", "capture", "sharex", "gif", "nvidia", "shadow", "amd", "relive",
    "intel", "gamebar", "dvr"
])

# ---------------- Background scanner thread ----------------
class RecorderScanner(QtCore.QThread):
    """
    Runs recorder detection off the GUI thread.
    Emits: found(list[str]) with matched human-readable names (or empty list).
    """
    found = QtCore.Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop = False
        self._interval_playing = 1.2  # seconds when media playing
        self._interval_idle = 2.5     # seconds when idle/paused
        self._is_playing = False

    def set_playing(self, playing: bool):
        self._is_playing = playing

    def stop(self):
        self._stop = True

    # Minimal allocation — keep hot loop tight
    def _collect_proc_tokens(self, proc: psutil.Process) -> List[str]:
        tokens: List[str] = []
        try:
            name = proc.info.get("name") or ""
        except Exception:
            name = ""

        # Quick candidate filter by raw name first
        nlower = name.lower()
        if name and not any(k in nlower for k in CANDIDATE_SUBSTRINGS):
            # Still include Xbox Game Bar core names
            if _norm(name) not in GBAR_PRIMARY_N:
                return []

        # name variants
        if name:
            tokens += [name, _stem(name), _squash(name), _norm(name)]

        # Only dive deeper for potential candidates to avoid heavy syscalls
        try:
            exe = proc.exe()
            if exe:
                p = Path(exe)
                tokens += [p.name, str(p), _stem(p.name), _squash(p.name), _norm(p.name), _norm(str(p))]
        except Exception:
            pass

        try:
            for part in proc.cmdline() or []:
                if not part:
                    continue
                tokens += [part, _stem(part), _squash(part), _norm(part)]
        except Exception:
            pass

        # Dedup
        seen = set()
        out: List[str] = []
        for t in tokens:
            k = t.lower()
            if k not in seen:
                seen.add(k)
                out.append(t)
        return out

    def _looks_like_recorder(self, tokens: List[str]) -> Optional[str]:
        # strict match
        for t in tokens:
            tn = _norm(t)
            if tn in STRICT_N:
                # Return the most readable token (prefer the stemmed name)
                return _stem(t) or "recorder"
        # ignore-only helpers should not trigger by themselves
        for t in tokens:
            if _norm(t) in IGNORE_SOLO_N:
                return None
        return None

    def _xbox_gbar_active(self, present_norms: Set[str]) -> bool:
        has_primary = any(x in present_norms for x in GBAR_PRIMARY_N)
        has_hint = any(x in present_norms for x in GBAR_HINTS_N)
        return has_primary and has_hint

    def run(self):
        while not self._stop:
            matches: List[str] = []
            present_norms: Set[str] = set()
            try:
                for proc in psutil.process_iter(["name"]):
                    name = (proc.info.get("name") or "").strip()
                    if not name:
                        continue

                    # record norms for GBAR heuristic
                    nn = _norm(name)
                    present_norms.add(nn)

                    tokens = self._collect_proc_tokens(proc)
                    hit = self._looks_like_recorder(tokens)
                    if hit:
                        if _norm(hit) in IGNORE_SOLO_N:
                            continue
                        # Show a nicer label if possible
                        label = name if name else hit
                        if label not in matches:
                            matches.append(label)
            except Exception:
                # On any scanning error, just emit what we have
                pass

            # Xbox Game Bar
            if self._xbox_gbar_active(present_norms):
                if "Xbox Game Bar (recording)" not in matches:
                    matches.append("Xbox Game Bar (recording)")

            self.found.emit(matches[:6])

            # Adaptive sleep
            time.sleep(self._interval_playing if self._is_playing else self._interval_idle)


# ---------------- Video Player ----------------
class VideoPlayerWindow(QtWidgets.QMainWindow):
    """Dedicated video player window with Back button and full controls (light theme)."""

    recorderStateChanged = QtCore.Signal(bool, list)

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Epassion Player — Video")
        self.resize(1080, 720)

        # State
        self.recorder_detected = False
        self._last_matches: List[str] = []
        self._hits = 0
        self._misses = 0
        self.current_video_temp: Optional[str] = None

        # Multimedia (created lazily)
        self.player: Optional[QMediaPlayer] = None
        self.audio: Optional[QAudioOutput] = None

        # UI
        self._build_ui()
        self._wire()

        # OS guard (no-op on non-Windows unless implemented)
        try:
            enable_guard(self)
        except Exception:
            pass

        # Start scanner thread
        self._scanner = RecorderScanner(self)
        self._scanner.found.connect(self._on_scanner_result)
        self._scanner.start()

    # ---------- UI ----------
    def _build_ui(self):
        central = QtWidgets.QWidget()
        central.setAutoFillBackground(True)
        pal = central.palette()
        pal.setColor(QtGui.QPalette.Window, QtGui.QColor(BG))
        central.setPalette(pal)
        self.setCentralWidget(central)

        outer = QtWidgets.QVBoxLayout(central)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(10)

        # Top bar: Back + Title (light)
        top = QtWidgets.QHBoxLayout()
        self.btn_back = QtWidgets.QPushButton("← Back")
        self._as_button(self.btn_back)
        self.lbl_title = QtWidgets.QLabel("Video")
        self.lbl_title.setStyleSheet(f"color:{TEXT_DARK}; font-size:18px; font-weight:800;")
        top.addWidget(self.btn_back)
        top.addSpacing(8)
        top.addWidget(self.lbl_title)
        top.addStretch(1)
        outer.addLayout(top)

        # Recorder banner
        self.rec_banner = QtWidgets.QLabel("")
        self.rec_banner.setVisible(False)
        self.rec_banner.setStyleSheet(
            f"background:{BG}; border:1px solid {DANGER}; color:{DANGER};"
            f"padding:8px 10px; border-radius:8px; font-weight:700;"
        )
        outer.addWidget(self.rec_banner)

        # Video surface
        self.video_widget = QVideoWidget(self)
        self.video_widget.setMinimumHeight(360)
        self.video_widget.setStyleSheet(
            f"background:#000; border:1px solid {BORDER}; border-radius:12px;"
        )
        outer.addWidget(self.video_widget, 1)

        # Controls panel (light card)
        controls = QtWidgets.QFrame(objectName="controls")
        controls.setStyleSheet(
            f"QFrame#controls{{background:{BG}; border:1px solid {BORDER}; border-radius:12px;}}"
        )
        cl = QtWidgets.QGridLayout(controls)
        cl.setContentsMargins(12, 10, 12, 10)
        cl.setHorizontalSpacing(10)
        cl.setVerticalSpacing(8)

        # Row 0: timeline
        self.lbl_time = QtWidgets.QLabel("00:00 / 00:00")
        self.lbl_time.setStyleSheet(f"color:{TEXT_MUTED};")
        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.setStyleSheet(
            "QSlider::groove:horizontal{height:6px;background:#DDE6EF;border-radius:3px}"
            f"QSlider::handle:horizontal{{background:{PRIMARY};width:14px;height:14px;border-radius:7px;margin:-4px 0;}}"
            "QSlider::sub-page:horizontal{background:#B9E6FB;border-radius:3px}"
        )
        cl.addWidget(self.lbl_time, 0, 0)
        cl.addWidget(self.slider,   0, 1, 1, 6)

        # Row 1: transport + volume + speed + fullscreen
        self.btn_play = QtWidgets.QPushButton("▶ Play");     self._as_button(self.btn_play)
        self.btn_pause = QtWidgets.QPushButton("⏸ Pause");   self._as_button(self.btn_pause)
        self.btn_stop = QtWidgets.QPushButton("⏹ Stop");     self._as_button(self.btn_stop)
        self.btn_back10 = QtWidgets.QPushButton("⟲ 10s");    self._as_button(self.btn_back10)
        self.btn_fwd10  = QtWidgets.QPushButton("10s ⟳");    self._as_button(self.btn_fwd10)

        self.btn_mute = QtWidgets.QPushButton("🔇");         self._as_button(self.btn_mute)
        self.vol = QtWidgets.QSlider(QtCore.Qt.Horizontal);  self.vol.setRange(0, 100); self.vol.setValue(70)
        self.vol.setFixedWidth(120)
        self.vol.setStyleSheet(
            "QSlider::groove:horizontal{height:6px;background:#DDE6EF;border-radius:3px}"
            f"QSlider::handle:horizontal{{background:{PRIMARY};width:12px;height:12px;border-radius:6px;margin:-4px 0;}}"
            "QSlider::sub-page:horizontal{background:#B9E6FB;border-radius:3px}"
        )

        self.speed = QtWidgets.QComboBox()
        self.speed.addItems(["0.5x","0.75x","1.0x","1.25x","1.5x","1.75x","2.0x"])
        self.speed.setCurrentText("1.0x")
        self._as_combo(self.speed)

        self.btn_full = QtWidgets.QPushButton("⛶ Fullscreen"); self._as_button(self.btn_full)

        cl.addWidget(self.btn_play,   1, 0)
        cl.addWidget(self.btn_pause,  1, 1)
        cl.addWidget(self.btn_stop,   1, 2)
        cl.addWidget(self.btn_back10, 1, 3)
        cl.addWidget(self.btn_fwd10,  1, 4)

        cl.addWidget(QtWidgets.QLabel("Vol:", styleSheet=f"color:{TEXT_MUTED};"), 1, 5, alignment=QtCore.Qt.AlignRight)
        cl.addWidget(self.vol,       1, 6)
        cl.addWidget(self.btn_mute,  1, 7)
        cl.addWidget(QtWidgets.QLabel("Speed:", styleSheet=f"color:{TEXT_MUTED};"), 1, 8, alignment=QtCore.Qt.AlignRight)
        cl.addWidget(self.speed,     1, 9)
        cl.addWidget(self.btn_full,  1, 10)

        outer.addWidget(controls)

        # Status bar
        sb = QtWidgets.QStatusBar()
        sb.setStyleSheet(
            f"QStatusBar{{background:{BG}; border-top:1px solid {BORDER}; color:{TEXT_MUTED};}}"
        )
        self.setStatusBar(sb)

    def _as_button(self, b: QtWidgets.QPushButton):
        b.setMinimumHeight(34)
        b.setCursor(QtCore.Qt.PointingHandCursor)
        b.setStyleSheet(
            f"QPushButton{{border:1px solid {BORDER};border-radius:10px;padding:6px 12px;background:{BG};color:{TEXT_DARK};font-weight:600;}}"
            f"QPushButton:hover{{background:{HOVER_BG};border-color:{PRIMARY};}}"
        )

    def _as_combo(self, c: QtWidgets.QComboBox):
        c.setMinimumHeight(34)
        c.setStyleSheet(
            f"QComboBox{{border:1px solid {BORDER};border-radius:10px;padding:4px 10px;background:{BG};color:{TEXT_DARK};}}"
            f"QComboBox:hover{{border-color:{PRIMARY};}}"
        )

    def _wire(self):
        self.btn_back.clicked.connect(self._on_back)
        self.btn_play.clicked.connect(self._on_play)
        self.btn_pause.clicked.connect(lambda: self.player.pause() if self.player else None)
        self.btn_stop.clicked.connect(lambda: self._stop_video(force=False))
        self.btn_back10.clicked.connect(lambda: self.player.setPosition(max(0, self.player.position()-10000)) if self.player else None)
        self.btn_fwd10.clicked.connect(lambda: self.player.setPosition(min(self.player.duration(), self.player.position()+10000)) if self.player else None)
        self.btn_mute.clicked.connect(self._toggle_mute)
        self.vol.valueChanged.connect(self._on_volume)
        self.speed.currentTextChanged.connect(self._on_speed)
        self.slider.sliderMoved.connect(lambda v: self.player.setPosition(v) if self.player else None)
        self.btn_full.clicked.connect(self._toggle_fullscreen)

    # ---------- scanner results (debounced on GUI thread) ----------
    @QtCore.Slot(list)
    def _on_scanner_result(self, matches: List[str]):
        found = bool(matches)

        # Debounce: 2 hits to set, 2 misses to clear
        if found:
            self._hits += 1
            self._misses = 0
        else:
            self._misses += 1
            self._hits = 0

        new_state = self.recorder_detected
        if not self.recorder_detected and self._hits >= 2:
            new_state = True
        elif self.recorder_detected and self._misses >= 2:
            new_state = False

        if new_state != self.recorder_detected:
            self.recorder_detected = new_state
            self._last_matches = matches if self.recorder_detected else []
            self._apply_recorder_state(self._last_matches)
            self.recorderStateChanged.emit(self.recorder_detected, list(self._last_matches))
        elif self.recorder_detected:
            self._last_matches = matches
            self._apply_recorder_state(self._last_matches)
            self.recorderStateChanged.emit(True, list(self._last_matches))

    def _apply_recorder_state(self, matches: List[str]):
        if self.recorder_detected:
            detail = f" ({', '.join(matches)})" if matches else ""
            self.rec_banner.setText(f"Screen recorder detected{detail}. Playback has been stopped for security.")
            self.rec_banner.setVisible(True)
            self.statusBar().setStyleSheet(
                f"QStatusBar{{background:{BG}; border-top:1px solid {BORDER}; color:{DANGER};}}"
            )
            self._stop_video(force=False)  # keep source so user can resume later
            self.video_widget.setEnabled(False)
        else:
            self.rec_banner.setVisible(False)
            self.statusBar().setStyleSheet(
                f"QStatusBar{{background:{BG}; border-top:1px solid {BORDER}; color:{TEXT_MUTED};}}"
            )
            self.video_widget.setEnabled(True)

    # ---------- player helpers ----------
    def _ensure_player(self):
        if self.player is not None:
            return
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video_widget)

        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)
        self.player.mediaStatusChanged.connect(self._on_media_status)
        self.player.playbackStateChanged.connect(self._on_state_changed)

    # ---------- public API ----------
    def play_temp_video(self, temp_path: str, title: str = "Video"):
        self._ensure_player()
        self._stop_video(force=True)

        self.current_video_temp = temp_path
        self.lbl_title.setText(Path(temp_path).name if not title else title)

        url = QtCore.QUrl.fromLocalFile(temp_path)
        self.player.setSource(url)
        self.player.setPosition(0)
        self.player.play()
        self.statusBar().showMessage("Playing…", 2000)

        # tell scanner to poll a bit faster while playing
        if hasattr(self, "_scanner") and self._scanner.isRunning():
            self._scanner.set_playing(True)

    # ---------- controls ----------
    def _on_play(self):
        if self.recorder_detected:
            self.statusBar().showMessage("Close the screen recorder to continue.", 3000)
            return
        self._ensure_player()
        try:
            if self.player.position() >= max(1, self.player.duration() - 10):
                self.player.setPosition(0)
        except Exception:
            pass
        self.player.play()
        if hasattr(self, "_scanner") and self._scanner.isRunning():
            self._scanner.set_playing(True)

    def _toggle_mute(self):
        if not self.audio:
            return
        self.audio.setMuted(not self.audio.isMuted())
        self.btn_mute.setText("🔊" if not self.audio.isMuted() else "🔇")

    def _on_volume(self, v: int):
        if self.audio:
            self.audio.setVolume(v / 100.0)

    def _on_speed(self, text: str):
        if not self.player:
            return
        try:
            sp = float(text.replace("x", ""))
            self.player.setPlaybackRate(sp)
        except Exception:
            pass

    def _toggle_fullscreen(self):
        self.video_widget.setFullScreen(not self.video_widget.isFullScreen())

    # Allow Esc to exit fullscreen (and F toggles)
    def keyPressEvent(self, e: QtGui.QKeyEvent) -> None:
        if e.key() == QtCore.Qt.Key_Escape and self.video_widget.isFullScreen():
            self.video_widget.setFullScreen(False)
            e.accept()
            return
        if e.key() == QtCore.Qt.Key_F:
            self._toggle_fullscreen()
            e.accept()
            return
        super().keyPressEvent(e)

    # ---------- time/seek ----------
    def _fmt(self, ms: int) -> str:
        ms = max(0, int(ms))
        s = ms // 1000
        m, s = divmod(s, 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def _on_position(self, pos: int):
        self.slider.blockSignals(True)
        self.slider.setValue(pos)
        self.slider.blockSignals(False)
        if self.player:
            self.lbl_time.setText(f"{self._fmt(pos)} / {self._fmt(self.player.duration())}")

    def _on_duration(self, dur: int):
        self.slider.setRange(0, max(0, dur))
        if self.player:
            self.lbl_time.setText(f"{self._fmt(self.player.position())} / {self._fmt(dur)}")

    def _on_state_changed(self, state: QMediaPlayer.PlaybackState):
        # Update scanner pacing
        if hasattr(self, "_scanner") and self._scanner.isRunning():
            self._scanner.set_playing(state == QMediaPlayer.PlayingState)

    def _on_media_status(self, status: QMediaPlayer.MediaStatus):
        if status == QMediaPlayer.EndOfMedia:
            try:
                if self.player:
                    self.player.stop()
                    self.player.setPosition(0)
                self.statusBar().showMessage("Video ended — press Play to restart.", 3000)
            except Exception:
                pass
        elif status == QMediaPlayer.InvalidMedia:
            self._stop_video(force=False)

    # ---------- stop/wipe ----------
    def _stop_video(self, force: bool = False):
        try:
            if self.player:
                self.player.stop()
                if force:
                    self.player.setSource(QtCore.QUrl())
                else:
                    self.player.setPosition(0)
        except Exception:
            pass

        if force and self.current_video_temp:
            safe_delete(self.current_video_temp)
            self.current_video_temp = None

        # tell scanner we’re idle
        if hasattr(self, "_scanner") and self._scanner.isRunning():
            self._scanner.set_playing(False)

    # ---------- navigation ----------
    def _on_back(self):
        self.close()

    # ---------- lifecycle ----------
    def showEvent(self, ev: QtGui.QShowEvent) -> None:
        super().showEvent(ev)
        # thread is started in __init__

    def hideEvent(self, ev: QtGui.QHideEvent) -> None:
        super().hideEvent(ev)

    def closeEvent(self, ev: QtGui.QCloseEvent) -> None:
        try:
            disable_guard(self)
        except Exception:
            pass
        try:
            if hasattr(self, "_scanner"):
                self._scanner.stop()
                self._scanner.wait(1500)
        except Exception:
            pass
        self._stop_video(force=True)
        super().closeEvent(ev)
