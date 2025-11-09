# EpassionPlayer/ui/activate_dialog.py
from __future__ import annotations
from PySide6 import QtCore, QtGui, QtWidgets
import json, os

from core.server_api import activate_v2, license_v2, save_license_response
from core.device import get_device_fingerprint

PRIMARY = "#22ABE1"

_FAKE_CLIENT_PUB = (
    "-----BEGIN PUBLIC KEY-----\n"
    "MIIB...PLACE_YOUR_CLIENT_PUB_KEY_HERE...IDAQAB\n"
    "-----END PUBLIC KEY-----\n"
)

class _ActivateWorker(QtCore.QObject):
    finished = QtCore.Signal(bool, str)   # ok, message
    step = QtCore.Signal(str)             # status text
    result = QtCore.Signal(dict)          # license payload on success

    def __init__(self, server: str, code: str, pkg: str, parent=None):
        super().__init__(parent)
        self.server = server
        self.code = code
        self.pkg = pkg

    @QtCore.Slot()
    def run(self):
        try:
            self.step.emit("Binding your activation code…")
            r1 = activate_v2(self.server, self.code, self.pkg)
            if r1.get("status") != "bound":
                self.finished.emit(False, f"Activation failed:\n{r1.get('status')} – {r1.get('message')}")
                return

            self.step.emit("Requesting license from server…")
            r2 = license_v2(self.server, self.code, self.pkg, _FAKE_CLIENT_PUB)
            if r2.get("status") != "ok":
                self.finished.emit(False, f"License failed:\n{r2.get('status')} – {r2.get('message')}")
                return

            self.step.emit("Saving license…")
            save_license_response(r2)

            self.result.emit(r2)
            self.finished.emit(True, "Activation successful.")
        except Exception as e:
            self.finished.emit(False, str(e))

class ActivateDialog(QtWidgets.QDialog):
    def __init__(self, server_base_url: str, logo_path: str, parent=None):
        super().__init__(parent)
        self.server = server_base_url
        self.secure_path = None

        self.setWindowTitle("Epassion • Activate")
        self.setModal(True)
        self.setFixedSize(600, 460)
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowContextHelpButtonHint)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(24,24,24,24); root.setSpacing(16)

        # Header
        top = QtWidgets.QHBoxLayout()
        logo = QtWidgets.QLabel()
        pm = QtGui.QPixmap(logo_path)
        if not pm.isNull():
            pm = pm.scaled(56,56, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        logo.setPixmap(pm)
        title = QtWidgets.QLabel("Activate your device")
        title.setStyleSheet("font-size:20px; font-weight:800; color:#0B1221;")
        top.addWidget(logo); top.addSpacing(10); top.addWidget(title, 1, QtCore.Qt.AlignVCenter)
        root.addLayout(top)

        # Form
        form = QtWidgets.QFormLayout()
        self.edit_package = QtWidgets.QLineEdit()
        self.edit_package.setPlaceholderText("Browse USB to detect Package ID")
        self.edit_package.setReadOnly(True)
        browse_btn = QtWidgets.QPushButton("Browse USB…")
        browse_btn.clicked.connect(self._browse_usb)
        usb_layout = QtWidgets.QHBoxLayout()
        usb_layout.addWidget(self.edit_package, 1)
        usb_layout.addWidget(browse_btn)
        form.addRow("Package ID", usb_layout)

        self.edit_code = QtWidgets.QLineEdit()
        self.edit_code.setPlaceholderText("Activation code")
        self.edit_code.setMaxLength(32)
        form.addRow("Activation Code", self.edit_code)
        root.addLayout(form)

        # Device info
        fp = get_device_fingerprint(hash_hex=True)
        self.lbl_info = QtWidgets.QLabel(f"Device fingerprint: {fp[:12]}…")
        self.lbl_info.setStyleSheet("color:#67728A; font-size:12px;")
        root.addWidget(self.lbl_info)

        # Status + spinner
        self.lbl_status = QtWidgets.QLabel("")
        self.lbl_status.setStyleSheet("color:#67728A; font-size:12px;")
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 0)  # indeterminate while busy
        self.progress.setVisible(False)
        root.addWidget(self.lbl_status)
        root.addWidget(self.progress)

        # Buttons
        btns = QtWidgets.QHBoxLayout(); btns.addStretch(1)
        self.btn_cancel = QtWidgets.QPushButton("Cancel")
        self.btn_ok = QtWidgets.QPushButton("Activate"); self.btn_ok.setProperty("primary", True)
        btns.addWidget(self.btn_cancel); btns.addWidget(self.btn_ok)
        root.addLayout(btns)

        self.btn_cancel.clicked.connect(self.reject)
        self.btn_ok.clicked.connect(self._do_activate)

        # Thread placeholders
        self._thread: QtCore.QThread | None = None
        self._worker: _ActivateWorker | None = None

    def _browse_usb(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Select USB Drive")
        if not path:
            return
        manifest_path = os.path.join(path, "SecureContent", "manifest.json")
        if not os.path.exists(manifest_path):
            QtWidgets.QMessageBox.warning(self, "Epassion",
                "Selected drive does not contain SecureContent/manifest.json")
            return
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                j = json.load(f)
            pkgid = j.get("package_id")
            if not pkgid:
                raise ValueError("No package_id in manifest.json")
            self.edit_package.setText(pkgid)
            self.secure_path = path
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Epassion",
                f"Failed to read manifest.json:\n{e}")

    # ----------------
    # Busy / idle UI
    # ----------------
    def _set_busy(self, busy: bool, status: str = ""):
        self.btn_ok.setEnabled(not busy)
        self.btn_cancel.setEnabled(not busy)
        self.progress.setVisible(busy)
        self.lbl_status.setText(status)
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor if busy else QtCore.Qt.ArrowCursor)

    # ----------------
    # Start activation
    # ----------------
    def _do_activate(self):
        pkg = self.edit_package.text().strip()
        code = self.edit_code.text().strip()
        if not pkg or not code:
            QtWidgets.QMessageBox.warning(self, "Epassion",
                "Please select USB and enter activation code.")
            return

        self._set_busy(True, "Starting…")

        self._thread = QtCore.QThread(self)
        self._worker = _ActivateWorker(self.server, code, pkg)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.step.connect(lambda s: self._set_busy(True, s))

        def _finish(ok: bool, msg: str):
            self._thread.quit(); self._thread.wait()
            self._thread = None
            self._worker = None
            self._set_busy(False, "")
            if ok:
                QtWidgets.QMessageBox.information(self, "Epassion", msg)
                self.accept()
            else:
                QtWidgets.QMessageBox.critical(self, "Epassion", msg)

        self._worker.finished.connect(_finish)
        self._thread.start()
