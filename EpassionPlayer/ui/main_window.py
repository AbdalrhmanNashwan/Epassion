from __future__ import annotations
import json
from pathlib import Path
from typing import Optional, List, Dict, Set

from PySide6 import QtCore, QtGui, QtWidgets
import psutil  # recorder detection

from core.device import simple_fingerprint, read_package_id_from_usb
from core.server_api import activate_v2, license_v2, save_license_response
from core.content import find_secure_root, load_package, PackageView, FileEntry
from core.crypto import decrypt_file_to_temp, open_with_default_app, safe_delete
from core import secure_store
from core import keys

from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature

# Brand palette (light)
PRIMARY = "#22ABE1"
TEXT_DARK = "#0B1221"
TEXT_MUTED = "#536077"
BG = "#FFFFFF"
BORDER = "#E7ECF2"
SUCCESS = "#14A44D"
DANGER = "#E53935"
HOVER_BG = "#F5FAFF"
SELECT_BG = "#E8F6FF"

# ---------- Recorder detection (improved normalization) ----------
def _stem(name: str) -> str:
    n = (name or "").strip().lower()
    if n.endswith(".exe"):
        n = n[:-4]
    return n

def _squash(s: str) -> str:
    return s.replace(" ", "").replace("-", "").replace("_", "")

# Normalized recorder “stems”
STRICT_RECORDERS: Set[str] = {
    "obs64", "obs32", "obs",
    "xsplit.core", "xsplit", "xsplit broadcaster", "xsplit gamecaster",
    "bandicam", "bdcam",
    "camtasia", "camtasia studio", "camtasiastudio",
    "streamlabs", "slobs", "streamlabs obs",
    "dxtory", "action",
    "snagit32", "snagit64", "snagit",
    "screenflow", "kazam",
    "simplescreenrecorder", "simple screen recorder",
    "vokoscreen", "recordmydesktop", "peek",
    "sharex", "screen2gif",
    "loilo", "flashbackexpress", "flashback pro", "flashbackpro",
    "icecream screen recorder", "icecreamscreenrecorder",
    "nvidia share", "shadowplay", "geforce experience",
    "amddvr", "amddvr64", "amddvruser", "relive",
    "arccontrol", "intel-gpu-capture", "intel gpu capture", "intel capture service",
}

GBAR_PRIMARY = {"gamebarui", "gamebarft"}
GBAR_HINTS   = {"broadcastdvrserver", "gamebarftserver", "recordingindicatorsvc", "recordingindicator"}

IGNORE_ALWAYS = {"nvcontainer", "nvsphelper64", "nvsphelper", "gamingservices", "gamingservicesnet", "explorer"}

def _match_name(name: str, normalized_set: Set[str]) -> bool:
    s = _stem(name)
    if s in normalized_set:
        return True
    if _squash(s) in {_squash(x) for x in normalized_set}:
        return True
    return False


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, server_base_url: str, company_logo_path: Optional[str] = None):
        super().__init__()
        self.setWindowTitle("Epassion Player")
        self.resize(1240, 820)

        # Ensure device RSA keypair: private key in secure store, public key on disk
        keys.ensure_keypair()

        self.server_base = server_base_url
        self.secure_root: Optional[Path] = None
        self.package: Optional[PackageView] = None
        self.drive_key: Optional[bytes] = None
        self.temp_paths: list[str] = []

        # quiz gating for videos (only videos appear here after being opened)
        self.opened_entries: Set[str] = set()

        # visited progress (all file kinds) — PERSISTED per package
        self._visited_keys: Set[str] = set()
        self._item_index: Dict[str, QtWidgets.QTreeWidgetItem] = {}
        self._total_files: int = 0
        self._current_pkg_id: Optional[str] = None

        # UI refs
        self._toolbar_frame: Optional[QtWidgets.QFrame] = None
        self._toolbar_toggle_btn: Optional[QtWidgets.QToolButton] = None
        self._toolbar_collapsed = False
        self._progress: Optional[QtWidgets.QProgressBar] = None

        # Recorder detection state
        self._rec_timer: Optional[QtCore.QTimer] = None
        self.recorder_detected: bool = False
        self._rec_hits = 0
        self._rec_misses = 0
        self._rec_matches: List[str] = []

        # Build/wire UI, start monitor, restore session
        self._build_ui(company_logo_path)
        self._wire()
        self._setup_recorder_monitor()
        self._restore_last_session()
        self._refresh_activation_ui()

    # ---------- UI ----------
    def _build_ui(self, company_logo_path: Optional[str]):
        central = QtWidgets.QWidget(); self.setCentralWidget(central)
        outer = QtWidgets.QVBoxLayout(central)
        outer.setContentsMargins(18, 12, 18, 12); outer.setSpacing(12)

        # Header row with brand + PROGRESS + toolbar toggle
        header = QtWidgets.QHBoxLayout()
        if company_logo_path and Path(company_logo_path).exists():
            logo = QtWidgets.QLabel()
            pix = QtGui.QPixmap(company_logo_path).scaledToHeight(60, QtCore.Qt.SmoothTransformation)
            logo.setPixmap(pix)
            header.addWidget(logo)
        title = QtWidgets.QLabel("Epassion Player")
        title.setStyleSheet(f"color:{TEXT_DARK}; font-size:20px; font-weight:900;")
        header.addWidget(title)

        # Progress bar
        self._progress = QtWidgets.QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        self._progress.setFormat("Progress: %p%")
        self._progress.setFixedHeight(18)
        self._progress.setStyleSheet(
            "QProgressBar {"
            f"  background: #F3F7FB; border: 1px solid {BORDER}; border-radius: 9px;"
            f"  color: {TEXT_MUTED}; font-weight: 600; padding: 0 6px;"
            "}"
            "QProgressBar::chunk {"
            f"  background: {PRIMARY}; border-radius: 9px;"
            "}"
        )
        header.addWidget(self._progress, 1)

        self._toolbar_toggle_btn = QtWidgets.QToolButton()
        self._toolbar_toggle_btn.setText("Controls")
        self._toolbar_toggle_btn.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        self._toolbar_toggle_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._toolbar_toggle_btn.setCheckable(True)
        self._toolbar_toggle_btn.setChecked(True)
        self._toolbar_toggle_btn.setStyleSheet(
            f"QToolButton{{border:1px solid {BORDER}; border-radius:8px; padding:6px 10px; background:{BG}; color:{TEXT_DARK}; font-weight:700;}}"
            f"QToolButton:hover{{background:{HOVER_BG}; border-color:{PRIMARY};}}"
        )
        header.addWidget(self._toolbar_toggle_btn, 0, QtCore.Qt.AlignRight)
        outer.addLayout(header)

        # Recorder banner
        self._rec_banner = QtWidgets.QLabel("")
        self._rec_banner.setVisible(False)
        self._rec_banner.setStyleSheet(
            f"background:{BG}; border:1px solid {DANGER}; color:{DANGER};"
            "padding:8px 10px; border-radius:8px; font-weight:700;"
        )
        outer.addWidget(self._rec_banner)

        # Collapsible toolbar (path / activation / load)
        self._toolbar_frame = QtWidgets.QFrame(objectName="toolbar")
        tl = QtWidgets.QGridLayout(self._toolbar_frame)
        tl.setContentsMargins(12, 10, 12, 10); tl.setHorizontalSpacing(10); tl.setVerticalSpacing(8)

        self.btn_choose = QtWidgets.QPushButton("Choose Folder"); self._as_button(self.btn_choose)
        self.ed_folder = QtWidgets.QLineEdit(); self._as_line(self.ed_folder, "Select a folder that contains SecureContent"); self.ed_folder.setReadOnly(True)
        self.ed_code = QtWidgets.QLineEdit(); self._as_line(self.ed_code, "Activation code")

        self.btn_activate = QtWidgets.QPushButton("Activate"); self._as_primary(self.btn_activate)
        self.btn_load = QtWidgets.QPushButton("Load Content"); self._as_button(self.btn_load)

        self.btn_start_quiz = QtWidgets.QPushButton("Start Quiz"); self._as_primary(self.btn_start_quiz); self.btn_start_quiz.setEnabled(False)
        self.lbl_quiz_target = QtWidgets.QLabel("No quiz selected"); self.lbl_quiz_target.setStyleSheet(f"color:{TEXT_MUTED};")

        tl.addWidget(self.btn_choose,      0, 0)
        tl.addWidget(self.ed_folder,       0, 1, 1, 3)
        tl.addWidget(self.ed_code,         1, 0)
        tl.addWidget(self.btn_activate,    1, 1)
        tl.addWidget(self.btn_load,        1, 2)
        tl.addWidget(self.btn_start_quiz,  1, 3)
        tl.addWidget(self.lbl_quiz_target, 2, 0, 1, 4)

        outer.addWidget(self._toolbar_frame)

        # Content card (tree)
        content_card = QtWidgets.QFrame(objectName="card")
        cl = QtWidgets.QVBoxLayout(content_card); cl.setContentsMargins(12, 12, 12, 12); cl.setSpacing(8)

        hint = QtWidgets.QLabel("Double-click a file to open. Videos show a badge ▣. PDF, Image, and Video files have distinct icons.")
        hint.setStyleSheet(f"color:{TEXT_MUTED};")
        cl.addWidget(hint)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderHidden(True); self.tree.setColumnCount(1)
        self.tree.setStyleSheet(
            f"QTreeWidget{{border:1px solid {BORDER}; border-radius:10px; background:{BG}; color:{TEXT_DARK};}}"
            f"QTreeWidget::item{{padding:6px 8px;}}"
            f"QTreeWidget::item:selected{{background:{SELECT_BG}; color:{TEXT_DARK};}}"
            f"QTreeWidget::item:hover{{background:{HOVER_BG};}}"
        )
        cl.addWidget(self.tree, 1)

        outer.addWidget(content_card, 1)

        # Status bar
        sb = QtWidgets.QStatusBar()
        sb.setStyleSheet(f"QStatusBar{{background:{BG}; border-top:1px solid {BORDER}; color:{TEXT_MUTED};}}")
        self.setStatusBar(sb)

    def _as_line(self, w: QtWidgets.QLineEdit, ph: str):
        w.setPlaceholderText(ph)
        w.setMinimumHeight(36)

    def _as_button(self, b: QtWidgets.QPushButton):
        b.setMinimumHeight(36); b.setCursor(QtCore.Qt.PointingHandCursor)

    def _as_primary(self, b: QtWidgets.QPushButton):
        b.setMinimumHeight(36); b.setCursor(QtCore.Qt.PointingHandCursor)
        b.setProperty("primary", True)
        b.style().unpolish(b); b.style().polish(b)

    # ---------- Wiring ----------
    def _wire(self):
        self.btn_choose.clicked.connect(self.on_choose)
        self.btn_activate.clicked.connect(self.on_activate)
        self.btn_load.clicked.connect(self.on_load)
        self.btn_start_quiz.clicked.connect(self.on_start_quiz)
        self.tree.itemSelectionChanged.connect(self.on_selection_changed)
        self.tree.itemDoubleClicked.connect(self.on_item_double)
        self._toolbar_toggle_btn.toggled.connect(self._set_toolbar_visible)

    # ---------- Recorder monitoring for Main Window ----------
    def _setup_recorder_monitor(self):
        self._rec_timer = QtCore.QTimer(self)
        self._rec_timer.setInterval(2000)
        self._rec_timer.timeout.connect(self._poll_recorders_main)
        self._rec_timer.start()

    def _find_recorders(self) -> List[str]:
        raw_matches: List[str] = []
        present_stems: Set[str] = set()
        present_squashed: Set[str] = set()
        try:
            for proc in psutil.process_iter(["name"]):
                raw = (proc.info.get("name") or "").strip()
                if not raw:
                    continue
                s = _stem(raw)
                if not s:
                    continue
                if s in IGNORE_ALWAYS or _squash(s) in {_squash(x) for x in IGNORE_ALWAYS}:
                    continue
                present_stems.add(s)
                present_squashed.add(_squash(s))
                if _match_name(raw, STRICT_RECORDERS):
                    raw_matches.append(raw)
        except Exception:
            pass

        has_gbar = any(x in present_stems or _squash(x) in present_squashed for x in GBAR_PRIMARY)
        has_hint = any(x in present_stems or _squash(x) in present_squashed for x in GBAR_HINTS)
        if has_gbar and has_hint:
            raw_matches.append("Xbox Game Bar (recording)")

        seen = set()
        out: List[str] = []
        for n in raw_matches:
            k = _squash(_stem(n))
            if k not in seen:
                seen.add(k)
                out.append(n)
        return out[:6]

    def _poll_recorders_main(self):
        matches = self._find_recorders()
        found = bool(matches)

        if found:
            self._rec_hits += 1
            self._rec_misses = 0
        else:
            self._rec_misses += 1
            self._rec_hits = 0

        new_state = self.recorder_detected
        if not self.recorder_detected and self._rec_hits >= 2:
            new_state = True
        elif self.recorder_detected and self._rec_misses >= 2:
            new_state = False

        if new_state != self.recorder_detected:
            self.recorder_detected = new_state
            self._rec_matches = matches if self.recorder_detected else []
            self._apply_recorder_state(self._rec_matches)
        elif self.recorder_detected:
            self._rec_matches = matches
            self._apply_recorder_state(self._rec_matches)

    def _apply_recorder_state(self, matches: List[str]):
        if self.recorder_detected:
            detail = f" ({', '.join(matches)})" if matches else ""
            self._rec_banner.setText(f"Screen recorder detected{detail}. All actions are disabled.")
            self._rec_banner.setVisible(True)
            self._set_interactive_enabled(False)
            self.statusBar().showMessage("Security: recording detected — blocked", 2000)
        else:
            self._rec_banner.setVisible(False)
            self._set_interactive_enabled(True)
            self.statusBar().showMessage("Security: clear", 1500)

    def _set_interactive_enabled(self, enabled: bool):
        for w in (self.btn_choose, self.ed_folder, self.ed_code, self.btn_activate, self.btn_load,
                  self.btn_start_quiz, self._toolbar_toggle_btn, self.tree):
            if w is not None:
                w.setEnabled(enabled)

    # ---------- Toolbar visibility ----------
    def _set_toolbar_visible(self, visible: bool):
        if not self._toolbar_frame:
            return
        self._toolbar_frame.setVisible(visible)
        self._toolbar_collapsed = not visible
        if not visible:
            self._auto_load_if_activated()

    def _refresh_activation_ui(self):
        activated = self._is_activated_for_current_root()
        self._toolbar_toggle_btn.blockSignals(True)
        self._toolbar_toggle_btn.setChecked(not activated)
        self._toolbar_toggle_btn.blockSignals(False)
        self._set_toolbar_visible(not activated)
        self.btn_activate.setEnabled(not activated and not self.recorder_detected)
        if activated:
            self.statusBar().showMessage("Activated — controls hidden; content loading…", 3000)
        else:
            self.statusBar().showMessage("Not activated — enter code to activate.", 3000)

    def _auto_load_if_activated(self):
        if self._is_activated_for_current_root():
            # Always trigger a fresh load when the toolbar is hidden due to activation
            self._load_content(show_dialogs=False)

    def _is_activated_for_current_root(self) -> bool:
        if not self.secure_root:
            return False
        pkg_id = read_package_id_from_usb(self.secure_root)
        if not pkg_id:
            return False
        lic_file = Path(self.secure_root) / "license_response.json"
        if lic_file.exists():
            try:
                data = json.loads(lic_file.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "license" in data and "enc_drive_key_b64" in data["license"]:
                    return True
            except Exception:
                pass
        payload = secure_store.load_json(f"license_{pkg_id}")
        if payload and isinstance(payload, dict) and "license" in payload and "enc_drive_key_b64" in payload["license"]:
            return True
        return False

    # ---------- Session helpers ----------
    def _restore_last_session(self):
        last = secure_store.load_json("last_pkg")
        if not last or not isinstance(last, dict):
            return
        folder = last.get("folder") or ""
        p = Path(folder)
        root = find_secure_root(p)
        if not root:
            return
        # Reset everything to avoid stale keys/files, then set root
        self._reset_package_state_for_new_root(root)
        self.ed_folder.setText(str(root))

        pkg_id = read_package_id_from_usb(self.secure_root)
        if pkg_id:
            saved = secure_store.load_json(f"code_{pkg_id}")
            if saved and isinstance(saved, dict) and saved.get("code"):
                self.ed_code.setText(saved["code"])

        # If activated, load content immediately
        self._refresh_activation_ui()
        if self._is_activated_for_current_root():
            self._load_content(show_dialogs=False)

    def _save_last_session(self):
        if not self.secure_root:
            return
        pkg_id = read_package_id_from_usb(self.secure_root) or ""
        try:
            secure_store.save_json("last_pkg", {"pkg_id": pkg_id, "folder": str(self.secure_root)})
        except Exception:
            pass

    # ---------- Visited persistence (per package) ----------
    def _pkg_id(self) -> Optional[str]:
        if not self.secure_root:
            return None
        return read_package_id_from_usb(self.secure_root)

    def _load_visited_for_pkg(self, pkg_id: Optional[str]) -> Set[str]:
        if not pkg_id:
            return set()
        data = secure_store.load_json(f"visited_{pkg_id}")
        if data and isinstance(data, dict):
            items = data.get("visited")
            if isinstance(items, list):
                return set(str(x) for x in items)
        return set()

    def _save_visited_for_pkg(self):
        """Persist current visited set for current package."""
        pkg_id = self._pkg_id()
        if not pkg_id:
            return
        try:
            secure_store.save_json(f"visited_{pkg_id}", {"visited": sorted(self._visited_keys)})
        except Exception:
            pass

    # Auto-refresh visited for current package (replaces the old button)
    def _refresh_progress_for_current_pkg(self):
        """Re-sync visited state from storage for the current package and reapply styling."""
        if not self.package:
            return
        pkg_id = self._pkg_id()
        loaded = self._load_visited_for_pkg(pkg_id)

        # Determine keys present in this package
        present_keys: Set[str] = set()
        for f in self.package.files:
            present_keys.add(self._entry_key(f))

        # Replace visited with (loaded ∩ present)
        self._visited_keys = loaded & present_keys

        # Clear styling on all items, then reapply visited look
        root_count = self.tree.topLevelItemCount()
        for i in range(root_count):
            root_it = self.tree.topLevelItem(i)
            self._clear_visit_style_recursive(root_it)

        for key in self._visited_keys:
            it = self._item_index.get(key)
            if it:
                self._style_item_as_visited(it)

        self._update_progress()

    def _clear_visit_style_recursive(self, item: QtWidgets.QTreeWidgetItem):
        if item is None:
            return
        # reset to default style
        item.setForeground(0, QtGui.QBrush(QtGui.QColor(TEXT_DARK)))
        f = item.font(0)
        f.setItalic(False)
        item.setFont(0, f)
        for i in range(item.childCount()):
            self._clear_visit_style_recursive(item.child(i))

    # ---------- Icon helpers ----------
    def _quiz_badge_icon(self, base: QtGui.QIcon) -> QtGui.QIcon:
        pm = base.pixmap(24, 24)
        out = QtGui.QPixmap(pm.size()); out.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(out); p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.drawPixmap(0, 0, pm)
        p.setBrush(QtGui.QBrush(QtGui.QColor(PRIMARY)))
        p.setPen(QtGui.QPen(QtGui.QColor("#FFFFFF")))
        r = QtCore.QRect(out.width()-12, 0, 12, 12)
        p.drawRoundedRect(r, 2, 2)
        p.end()
        return QtGui.QIcon(out)

    def _kind_icon(self, kind: str, has_quiz: bool) -> QtGui.QIcon:
        style = self.style()
        base = style.standardIcon(QtWidgets.QStyle.SP_FileIcon).pixmap(28, 28)
        tag_color = {
            "video": QtGui.QColor("#22ABE1"),
            "pdf":   QtGui.QColor("#D32F2F"),
            "image": QtGui.QColor("#2E7D32"),
        }.get(kind, QtGui.QColor("#607D8B"))
        label = {"video": "VID", "pdf": "PDF", "image": "IMG"}.get(kind, "FILE")

        pm = QtGui.QPixmap(base.size()); pm.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(pm); p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.drawPixmap(0, 0, base)
        rect = QtCore.QRectF(pm.width()-18, pm.height()-12, 18, 12)
        path = QtGui.QPainterPath(); path.addRoundedRect(rect, 3, 3)
        p.fillPath(path, tag_color)
        f = p.font(); f.setPointSize(6); f.setBold(True); p.setFont(f)
        p.setPen(QtGui.QPen(QtCore.Qt.white))
        p.drawText(rect.adjusted(1, -1, -1, -1), QtCore.Qt.AlignCenter, label)
        p.end()

        icon = QtGui.QIcon(pm)
        if has_quiz and kind == "video":
            icon = self._quiz_badge_icon(icon)
        return icon

    # ---------- Actions ----------
    def _reset_package_state_for_new_root(self, new_root: Path):
        """Hard reset when switching paths to avoid stale keys or package state."""
        # wipe temps from previous session
        for p in self.temp_paths:
            safe_delete(p)
        self.temp_paths.clear()

        # reset crypto + content state
        self.drive_key = None
        self.package = None
        self.secure_root = new_root

        # load visited for the new package id
        self._current_pkg_id = read_package_id_from_usb(self.secure_root) or None
        self._visited_keys = self._load_visited_for_pkg(self._current_pkg_id)

        # reset UI list and progress immediately
        self.tree.clear()
        self._item_index.clear()
        self._total_files = 0
        self._update_progress()

    def on_choose(self):
        if self.recorder_detected:
            QtWidgets.QMessageBox.warning(self, "Epassion Player", "Recording detected. Close the recorder first.")
            return
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose folder")
        if not d:
            return
        picked = Path(d)
        root = find_secure_root(picked)
        if not root:
            QtWidgets.QMessageBox.warning(self, "Epassion Player", "This folder does not contain SecureContent/manifest.enc.")
            return

        # FULL reset and set new root (prevents decrypt errors from stale states)
        self._reset_package_state_for_new_root(root)

        self.ed_folder.setText(str(root))
        self.statusBar().showMessage(f"Selected: {root}", 3500)
        self._save_last_session()

        pkg_id = read_package_id_from_usb(self.secure_root)
        if pkg_id:
            saved = secure_store.load_json(f"code_{pkg_id}")
            if saved and isinstance(saved, dict) and saved.get("code"):
                self.ed_code.setText(saved["code"])
            else:
                self.ed_code.clear()

        # Update toolbar visibility and, if activated, auto-load content NOW
        self._refresh_activation_ui()
        if self._is_activated_for_current_root():
            self._load_content(show_dialogs=False)

    def on_activate(self):
        if self.recorder_detected:
            QtWidgets.QMessageBox.warning(self, "Epassion Player", "Recording detected. Close the recorder first.")
            return
        if not self.secure_root:
            QtWidgets.QMessageBox.warning(self, "Epassion Player", "Choose a folder first."); return
        code = self.ed_code.text().strip()
        if not code:
            QtWidgets.QMessageBox.warning(self, "Epassion Player", "Enter an activation code."); return
        pkg_id = read_package_id_from_usb(self.secure_root)
        if not pkg_id:
            QtWidgets.QMessageBox.critical(self, "Epassion Player", "package_id.txt not found inside SecureContent."); return
        fp = simple_fingerprint()

        client_pub_pem = keys.get_public_pem_text()
        if not client_pub_pem:
            QtWidgets.QMessageBox.critical(self, "Epassion Player", "Device public key unavailable."); return

        ok, msg = activate_v2(self.server_base, code, pkg_id, fp)
        if not ok and "bound" not in msg:
            QtWidgets.QMessageBox.critical(self, "Epassion Player", f"Activation failed: {msg}"); return

        ok, payload, err = license_v2(self.server_base, code, pkg_id, fp, client_pub_pem)
        if not ok or payload is None:
            QtWidgets.QMessageBox.critical(self, "Epassion Player", f"License failed: {err}"); return
        try:
            self._verify_sig(payload)
        except InvalidSignature:
            QtWidgets.QMessageBox.critical(self, "Epassion Player", "Server signature invalid."); return

        save_license_response(self.secure_root, payload)
        try:
            secure_store.save_json(f"license_{pkg_id}", payload)
            secure_store.save_json(f"code_{pkg_id}", {"code": code})
        except Exception:
            pass
        self._save_last_session()

        QtWidgets.QMessageBox.information(self, "Epassion Player", "Activated & license saved.\nLoading content…")
        self.statusBar().showMessage("Activation OK — loading content…", 5000)

        # Hide controls and immediately load content
        self._refresh_activation_ui()
        self._load_content(show_dialogs=False)

    def _verify_sig(self, payload: dict):
        from cryptography.hazmat.primitives.asymmetric import ed25519
        lic = payload["license"]
        body = json.dumps(lic, separators=(",", ":"), sort_keys=True).encode("utf-8")
        import base64
        sig = base64.b64decode(payload["sig_b64"])
        server_pub_pem = payload["server_sign_pub_pem"].encode("utf-8")
        pub = serialization.load_pem_public_key(server_pub_pem)
        if isinstance(pub, ed25519.Ed25519PublicKey):
            pub.verify(sig, body)

    # ---------- Content loading helpers ----------
    def _load_drive_key(self) -> Optional[bytes]:
        if not self.secure_root:
            return None
        pkg_id = read_package_id_from_usb(self.secure_root)
        if not pkg_id:
            QtWidgets.QMessageBox.critical(self, "Epassion Player", "package_id.txt not found inside SecureContent."); return None

        lic_file = Path(self.secure_root) / "license_response.json"
        payload: Optional[dict] = None
        if lic_file.exists():
            try:
                payload = json.loads(lic_file.read_text(encoding="utf-8"))
            except Exception:
                payload = None
        if payload is None:
            payload = secure_store.load_json(f"license_{pkg_id}")
        if payload is None:
            QtWidgets.QMessageBox.warning(self, "Epassion Player", "No license found. Activate first."); return None

        import base64
        enc = base64.b64decode(payload["license"]["enc_drive_key_b64"])
        return keys.rsa_decrypt_oaep_sha256(enc)

    def _load_content(self, show_dialogs: bool = True):
        if self.recorder_detected:
            if show_dialogs:
                QtWidgets.QMessageBox.warning(self, "Epassion Player", "Recording detected. Close the recorder first.")
            return

        if not self.secure_root:
            if show_dialogs:
                QtWidgets.QMessageBox.warning(self, "Epassion Player", "Choose a folder first.")
            return

        try:
            # Always reload drive key fresh for the current root (fixes stale-key decrypt issue)
            self.drive_key = self._load_drive_key()
        except Exception as e:
            self.drive_key = None
            if show_dialogs:
                QtWidgets.QMessageBox.critical(self, "Epassion Player", f"License error: {e}")
            else:
                self.statusBar().showMessage("License error.", 4000)
            return

        if not self.drive_key:
            if show_dialogs:
                self._set_toolbar_visible(True)
                self._toolbar_toggle_btn.setChecked(True)
            else:
                self.statusBar().showMessage("No license available for this path.", 4000)
            return

        try:
            # Resolve current pkg id and load its saved visited set
            new_pkg_id = read_package_id_from_usb(self.secure_root) or None
            if self._current_pkg_id != new_pkg_id:
                self._current_pkg_id = new_pkg_id
                self._visited_keys = self._load_visited_for_pkg(new_pkg_id)

            # Load package, populate tree
            self.package = load_package(self.secure_root, self.drive_key)
            self._fill_tree()

            # Auto-refresh visited/progress from store (no button)
            self._refresh_progress_for_current_pkg()

            self.opened_entries.clear()
            self.btn_start_quiz.setEnabled(False)
            self.lbl_quiz_target.setText("No quiz selected")
            self.statusBar().showMessage("Content loaded", 5000)
            self._update_progress()
        except Exception as e:
            self.package = None
            if show_dialogs:
                QtWidgets.QMessageBox.critical(self, "Epassion Player", f"Failed to load content: {e}")
            else:
                self.statusBar().showMessage("Failed to load content.", 4000)

    def on_load(self):
        self._load_content(show_dialogs=True)
        self._refresh_activation_ui()

    # ---------- Tree population ----------
    def _fill_tree(self):
        # While rebuilding, pause updates for smoothness
        self.tree.setUpdatesEnabled(False)
        try:
            self.tree.clear()
            self._item_index.clear()
            self._total_files = 0

            if not self.package:
                return

            roots: dict[tuple[int, str], QtWidgets.QTreeWidgetItem] = {}

            # Create roots
            for r in self.package.roots or []:
                name = r.get("name", f"Root {r.get('index', 0)}")
                it = QtWidgets.QTreeWidgetItem([name])
                it.setData(0, QtCore.Qt.UserRole, {"kind": "root"})
                it.setIcon(0, self.style().standardIcon(QtWidgets.QStyle.SP_DirIcon))
                self.tree.addTopLevelItem(it)
                roots[(r.get("index", 0), name)] = it

            # Collect keys present in this package (to prune stale visited later)
            present_keys: Set[str] = set()

            for f in self.package.files:
                parent = roots.get((f.root_index, f.root_name))
                if parent is None:
                    parent = QtWidgets.QTreeWidgetItem([f.root_name or "Root"])
                    parent.setData(0, QtCore.Qt.UserRole, {"kind": "root"})
                    parent.setIcon(0, self.style().standardIcon(QtWidgets.QStyle.SP_DirIcon))
                    self.tree.addTopLevelItem(parent)

                self._total_files += 1
                txt = f.relpath
                leaf = QtWidgets.QTreeWidgetItem([txt])
                key = self._entry_key(f)
                present_keys.add(key)

                leaf.setData(0, QtCore.Qt.UserRole, {"kind": "file", "entry": f, "key": key})
                leaf.setIcon(0, self._kind_icon(f.kind, bool(f.quiz)))
                parent.addChild(leaf)
                self._item_index[key] = leaf

            # Prune visited set to only existing files in this package and persist if changed
            before = len(self._visited_keys)
            self._visited_keys &= present_keys
            if len(self._visited_keys) != before:
                self._save_visited_for_pkg()

            # Apply visited styling
            for key in self._visited_keys:
                it = self._item_index.get(key)
                if it:
                    self._style_item_as_visited(it)

            self.tree.expandAll()
        finally:
            self.tree.setUpdatesEnabled(True)

    def _entry_key(self, entry: FileEntry) -> str:
        return f"{entry.root_index}|{entry.root_name}|{entry.relpath}"

    # ---------- Visited styling & progress ----------
    def _style_item_as_visited(self, item: QtWidgets.QTreeWidgetItem):
        item.setForeground(0, QtGui.QBrush(QtGui.QColor(TEXT_MUTED)))
        font = item.font(0)
        font.setItalic(True)
        item.setFont(0, font)

    def _update_progress(self):
        if not self._progress:
            return
        total = max(0, self._total_files)
        done = min(len(self._visited_keys), total) if total > 0 else 0
        pct = int(round((done / total) * 100)) if total else 0
        self._progress.setValue(pct)
        self._progress.setFormat(f"  Progress: {pct}%")

    # ---------- Interactions ----------
    def on_item_double(self, item: QtWidgets.QTreeWidgetItem, col: int):
        if self.recorder_detected:
            QtWidgets.QMessageBox.warning(self, "Epassion Player", "Recording detected. Close the recorder first.")
            return

        data = item.data(0, QtCore.Qt.UserRole)
        if not data or data.get("kind") != "file":
            return
        if not self.package or not self.drive_key:
            QtWidgets.QMessageBox.warning(self, "Epassion Player", "Load content first.")
            return

        entry: FileEntry = data["entry"]
        key = data.get("key") or self._entry_key(entry)
        try:
            tmp = decrypt_file_to_temp(self.secure_root, entry.desc, self.drive_key)
            if entry.kind == "video":
                try:
                    from ui.video_player import VideoPlayerWindow
                    w = VideoPlayerWindow(self)
                    w.play_temp_video(tmp, title=Path(entry.relpath).name)
                    w.show()
                except Exception:
                    open_with_default_app(tmp)
            else:
                open_with_default_app(tmp)
            self.temp_paths.append(tmp)
            self.statusBar().showMessage("Opened", 3000)

            # Mark visited (persist immediately)
            if key not in self._visited_keys:
                self._visited_keys.add(key)
                self._style_item_as_visited(item)
                self._update_progress()
                self._save_visited_for_pkg()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Epassion Player", f"Open failed: {e}")
            return

        # Quiz gating for videos only
        if entry.kind == "video" and entry.quiz:
            self.opened_entries.add(self._entry_key(entry))
            self.on_selection_changed()

    def on_selection_changed(self):
        item = self.tree.currentItem()
        if not item:
            self.btn_start_quiz.setEnabled(False)
            self.lbl_quiz_target.setText("No quiz selected")
            return
        data = item.data(0, QtCore.Qt.UserRole) or {}
        if data.get("kind") != "file":
            self.btn_start_quiz.setEnabled(False)
            self.lbl_quiz_target.setText("No quiz selected")
            return
        entry: FileEntry = data["entry"]
        key = self._entry_key(entry)
        can_quiz = (entry.kind == "video" and entry.quiz and key in self.opened_entries)
        self.btn_start_quiz.setEnabled(bool(can_quiz) and not self.recorder_detected)
        self.lbl_quiz_target.setText(f"Quiz {'ready' if can_quiz else 'not ready'} for: {entry.relpath}")

    def on_start_quiz(self):
        if self.recorder_detected:
            QtWidgets.QMessageBox.warning(self, "Epassion Player", "Recording detected. Close the recorder first.")
            return
        item = self.tree.currentItem()
        if not item:
            QtWidgets.QMessageBox.warning(self, "Epassion Player", "Select a video with a quiz.")
            return
        data = item.data(0, QtCore.Qt.UserRole) or {}
        if data.get("kind") != "file":
            QtWidgets.QMessageBox.warning(self, "Epassion Player", "Select a video with a quiz.")
            return

        entry: FileEntry = data["entry"]
        key = self._entry_key(entry)
        if not (entry.kind == "video" and entry.quiz):
            QtWidgets.QMessageBox.warning(self, "Epassion Player", "This file has no quiz.")
            return
        if key not in self.opened_entries:
            QtWidgets.QMessageBox.warning(self, "Epassion Player", "Open the video first, then start the quiz.")
            return

        dlg = self._quiz_dialog(entry.quiz, self)
        dlg.exec()

    # Minimal self-contained quiz dialog
    def _quiz_dialog(self, quiz_items: List[Dict], parent):
        class QuizDialog(QtWidgets.QDialog):
            def __init__(self, items, parent=None):
                super().__init__(parent)
                self.setWindowTitle("Quiz")
                self.setModal(True)
                self.resize(640, 540)
                self.items = list(items or [])
                self.idx = 0
                self.answers: List[bool] = []
                root = QtWidgets.QVBoxLayout(self); root.setContentsMargins(18,18,18,18); root.setSpacing(14)
                header = QtWidgets.QHBoxLayout()
                title = QtWidgets.QLabel("Answer the questions"); title.setStyleSheet(f"color:{TEXT_DARK}; font-size:18px; font-weight:700;")
                self.lbl_prog = QtWidgets.QLabel(""); self.lbl_prog.setStyleSheet(f"color:{TEXT_MUTED};")
                header.addWidget(title); header.addStretch(1); header.addWidget(self.lbl_prog); root.addLayout(header)
                card = QtWidgets.QFrame(objectName="card"); card.setStyleSheet(f"QFrame#card{{background:{BG}; border:1px solid {BORDER}; border-radius:12px;}}")
                cl = QtWidgets.QVBoxLayout(card); cl.setContentsMargins(16,16,16,16); cl.setSpacing(12)
                self.lbl_q = QtWidgets.QLabel(""); self.lbl_q.setStyleSheet(f"color:{TEXT_DARK}; font-size:16px; font-weight:600;"); self.lbl_q.setWordWrap(True)
                cl.addWidget(self.lbl_q)
                self.btns: List[QtWidgets.QPushButton] = []
                def mk_btn():
                    b = QtWidgets.QPushButton(""); b.setMinimumHeight(48)
                    b.setCursor(QtCore.Qt.PointingHandCursor)
                    b.setStyleSheet(f"QPushButton{{border:1px solid {BORDER}; border-radius:10px; padding:10px 12px; background:{BG}; color:{TEXT_DARK}; font-size:14px;}} QPushButton:hover{{ background:{HOVER_BG}; border-color:{PRIMARY}; }}")
                    return b
                for _ in range(4):
                    b = mk_btn(); self.btns.append(b); cl.addWidget(b)
                root.addWidget(card)
                self.lbl_feedback = QtWidgets.QLabel(""); self.lbl_feedback.setStyleSheet(f"color:{TEXT_DARK};"); root.addWidget(self.lbl_feedback)
                nav = QtWidgets.QHBoxLayout(); nav.addStretch(1)
                self.btn_close = QtWidgets.QPushButton("Close"); self.btn_next = QtWidgets.QPushButton("Next")
                for b in (self.btn_close, self.btn_next):
                    b.setMinimumHeight(38)
                    b.setStyleSheet(f"QPushButton{{border:1px solid {BORDER}; border-radius:10px; padding:8px 14px; background:{BG}; color:{TEXT_DARK}; font-weight:600;}} QPushButton:hover{{ background:{HOVER_BG}; border-color:{PRIMARY}; }}")
                self.btn_close.clicked.connect(self.reject); self.btn_next.clicked.connect(self._next)
                nav.addWidget(self.btn_close); nav.addWidget(self.btn_next); root.addLayout(nav)
                if not self.items:
                    self.lbl_q.setText("No questions."); [b.setEnabled(False) for b in self.btns]; return
                for i, b in enumerate(self.btns):
                    b.clicked.connect(lambda _, k=i: self._pick(k))
                self._load(0)
            def _reset(self):
                for b in self.btns:
                    b.setEnabled(True)
                    b.setStyleSheet(f"QPushButton{{border:1px solid {BORDER}; border-radius:10px; padding:10px 12px; background:{BG}; color:{TEXT_DARK}; font-size:14px;}} QPushButton:hover{{ background:{HOVER_BG}; border-color:{PRIMARY}; }}")
            def _load(self, idx:int):
                self.idx = idx; total = len(self.items)
                self.lbl_prog.setText(f"{idx+1}/{total}"); self.lbl_feedback.setText(""); self.btn_next.setEnabled(False)
                q = self.items[idx]; self.lbl_q.setText(q.get("q","")); opts = list(q.get("options", []))
                while len(opts) < 4: opts.append("")
                for i, b in enumerate(self.btns): b.setText(opts[i])
                self._reset(); self.btn_next.setText("Finish" if idx == total-1 else "Next")
            def _pick(self, picked:int):
                q = self.items[self.idx]; correct = int(q.get("correct_index", -1))
                for b in self.btns: b.setEnabled(False)
                ok = (picked == correct)
                self.lbl_feedback.setText("Correct ✅" if ok else "Wrong ❌")
                if len(self.answers) == self.idx: self.answers.append(ok)
                else: self.answers[self.idx] = ok
                self.btn_next.setEnabled(True)
            def _next(self):
                if self.idx + 1 < len(self.items): self._load(self.idx+1); return
                score = sum(1 for x in self.answers if x)
                QtWidgets.QMessageBox.information(self, "Quiz", f"Your score: {score}/{len(self.items)}")
                self.accept()
        return QuizDialog(quiz_items, parent)

    # ---------- Close cleanup ----------
    def closeEvent(self, ev: QtGui.QCloseEvent) -> None:
        # Persist visited on close
        self._save_visited_for_pkg()
        for p in self.temp_paths:
            safe_delete(p)
        super().closeEvent(ev)
