from PySide6 import QtCore, QtGui, QtWidgets
from core.server_api import activate_v2, license_v2, save_license_response
from core.device import get_device_fingerprint
import json
import os

PRIMARY = "#22ABE1"

_FAKE_CLIENT_PUB = (
    "-----BEGIN PUBLIC KEY-----\n"
    "MIIB...PLACE_YOUR_CLIENT_PUB_KEY_HERE...IDAQAB\n"
    "-----END PUBLIC KEY-----\n"
)

class ActivateDialog(QtWidgets.QDialog):
    def __init__(self, server_base_url: str, logo_path: str, parent=None):
        super().__init__(parent)
        self.server = server_base_url
        self.secure_path = None

        self.setWindowTitle("Epassion • Activate")
        self.setModal(True)
        self.setFixedSize(600, 420)
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

        form = QtWidgets.QFormLayout()
        # Package (auto after browse)
        self.edit_package = QtWidgets.QLineEdit()
        self.edit_package.setPlaceholderText("Browse USB to detect Package ID")
        self.edit_package.setReadOnly(True)
        browse_btn = QtWidgets.QPushButton("Browse USB…")
        browse_btn.clicked.connect(self._browse_usb)
        usb_layout = QtWidgets.QHBoxLayout()
        usb_layout.addWidget(self.edit_package, 1)
        usb_layout.addWidget(browse_btn)
        form.addRow("Package ID", usb_layout)

        # Code input
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

        # Buttons
        btns = QtWidgets.QHBoxLayout(); btns.addStretch(1)
        self.btn_cancel = QtWidgets.QPushButton("Cancel")
        self.btn_ok = QtWidgets.QPushButton("Activate")
        self.btn_ok.setProperty("primary", True)
        btns.addWidget(self.btn_cancel); btns.addWidget(self.btn_ok)
        root.addLayout(btns)

        self.btn_cancel.clicked.connect(self.reject)
        self.btn_ok.clicked.connect(self._do_activate)

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

    def _do_activate(self):
        pkg = self.edit_package.text().strip()
        code = self.edit_code.text().strip()
        if not pkg or not code:
            QtWidgets.QMessageBox.warning(self, "Epassion",
                "Please select USB and enter activation code.")
            return

        self.btn_ok.setEnabled(False)
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            # 1. Bind code
            r1 = activate_v2(self.server, code, pkg)
            if r1.get("status") != "bound":
                QtWidgets.QMessageBox.critical(self, "Epassion",
                    f"Activation failed:\n{r1.get('status')} – {r1.get('message')}")
                return

            # 2. Get license
            r2 = license_v2(self.server, code, pkg, _FAKE_CLIENT_PUB)
            if r2.get("status") != "ok":
                QtWidgets.QMessageBox.critical(self, "Epassion",
                    f"License failed:\n{r2.get('status')} – {r2.get('message')}")
                return

            save_license_response(r2)
            QtWidgets.QMessageBox.information(self, "Epassion", "Activation successful.")
            self.accept()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Epassion", str(e))
        finally:
            self.btn_ok.setEnabled(True)
            QtWidgets.QApplication.restoreOverrideCursor()
