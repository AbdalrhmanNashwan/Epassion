import sys, os
from pathlib import Path
from typing import Dict, List, Optional
from PySide6 import QtGui
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QFileDialog,
    QLabel, QTableWidget, QTableWidgetItem, QMessageBox, QHeaderView, QLineEdit, QCheckBox
)
from packager import PackageBuilder, detect_kind
from usb_utils import list_removable_drives, get_drive_id
from quiz_dialog import QuizDialog

SERVER_URL = "https://epassion.pythonanywhere.com"


# --- Helpers for icon paths & Windows taskbar identity ---
def resource_path(rel: str) -> str:
    """Return correct path both for dev and PyInstaller bundles."""
    base = getattr(sys, "_MEIPASS", None)  # set by PyInstaller
    if base:
        return str(Path(base) / rel)
    return str(Path(__file__).resolve().parent / rel)

def _set_appusermodel_id():
    """Makes Windows taskbar use our EXE icon + consistent grouping."""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Epassion.AdminTool")
    except Exception:
        pass
# ---------------------------------------------------------


def human_type(path: Path) -> str:
    if path.is_dir():
        return "Folder"
    k = detect_kind(path)
    return {"video": "Video", "pdf": "PDF", "image": "Image", "other": "File"}[k]


class AdminTool(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Admin Encryption Tool — Multi-Folder Browser Mode")
        self.setWindowIcon(QtGui.QIcon(resource_path("assets/logo.ico")))  # <-- sets window icon
        self.resize(1280, 780)

        # Session state
        self.usb_root: Optional[Path] = None
        self.roots: List[Path] = []  # selected root folders
        self.current_folder: Optional[Path] = None
        self.video_quiz_map: Dict[str, List[dict]] = {}

        root = QVBoxLayout()

        # --- USB Target ---
        row_usb = QHBoxLayout()
        self.usb_edit = QLineEdit()
        self.usb_edit.setPlaceholderText("USB root (e.g., E:\\ or any target directory)")
        btn_usb = QPushButton("Choose USB…")
        btn_usb.clicked.connect(self.choose_usb)
        row_usb.addWidget(QLabel("Target:"))
        row_usb.addWidget(self.usb_edit)
        row_usb.addWidget(btn_usb)
        root.addLayout(row_usb)

        # --- ROOT Folders Controls ---
        row_src = QHBoxLayout()
        self.roots_label = QLabel("Roots: 0")
        btn_add_root = QPushButton("Add ROOT Folder…")
        btn_add_root.clicked.connect(self.add_root_folder)
        btn_clear_roots = QPushButton("Clear All Roots")
        btn_clear_roots.clicked.connect(self.clear_roots)
        btn_up = QPushButton("⬅ Back")
        btn_up.clicked.connect(self.go_up)
        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self.refresh)
        row_src.addWidget(self.roots_label)
        row_src.addStretch(1)
        row_src.addWidget(btn_add_root)
        row_src.addWidget(btn_clear_roots)
        row_src.addWidget(btn_up)
        row_src.addWidget(btn_refresh)
        root.addLayout(row_src)

        # --- Table ---
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Name", "Type", "Quiz"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.cellDoubleClicked.connect(self.on_double_click)
        root.addWidget(self.table)

        # --- Server Auth ---
        row_srv = QHBoxLayout()
        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText("ADMIN_USERNAME")
        self.pass_edit = QLineEdit()
        self.pass_edit.setPlaceholderText("ADMIN_PASSWORD")
        self.pass_edit.setEchoMode(QLineEdit.Password)
        self.chk_upload = QCheckBox("Upload package to server automatically")
        row_srv.addWidget(QLabel("User:"))
        row_srv.addWidget(self.user_edit)
        row_srv.addWidget(QLabel("Pass:"))
        row_srv.addWidget(self.pass_edit)
        row_srv.addWidget(self.chk_upload)
        root.addLayout(row_srv)

        # --- Actions ---
        row_actions = QHBoxLayout()
        b_quiz = QPushButton("Add/Edit Quiz to Selected Video…")
        b_quiz.clicked.connect(self.add_edit_quiz_selected)
        b_build = QPushButton("Build Package")
        b_build.clicked.connect(self.build_package)
        row_actions.addWidget(b_quiz)
        row_actions.addStretch(1)
        row_actions.addWidget(b_build)
        root.addLayout(row_actions)

        self.setLayout(root)
        self.refresh()

    # ===== Helpers =====
    def alert(self, msg):
        QMessageBox.warning(self, "Admin Tool", msg)
    def info(self, msg):
        QMessageBox.information(self, "Admin Tool", msg)

    def choose_usb(self):
        drives = list_removable_drives()
        start_dir = drives[0] if drives else str(Path.home())
        d = QFileDialog.getExistingDirectory(self, "Select Target Output (USB root)", start_dir)
        if d:
            self.usb_root = Path(d)
            self.usb_edit.setText(str(self.usb_root))

    def add_root_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Select ROOT Folder", str(Path.home()))
        if not d:
            return
        p = Path(d)
        if p not in self.roots:
            self.roots.append(p)
        self.refresh()

    def clear_roots(self):
        self.roots = []
        self.current_folder = None
        self.refresh()

    def go_up(self):
        if self.current_folder is None:
            return
        if any(self.current_folder == r for r in self.roots):
            self.current_folder = None
        else:
            self.current_folder = self.current_folder.parent
        self.refresh()

    def list_roots(self) -> List[Path]:
        return self.roots

    def list_dir(self, folder: Path) -> List[Path]:
        if not folder.exists():
            return []
        entries = sorted(
            [p for p in folder.iterdir() if not p.name.startswith(".")],
            key=lambda x: (0 if x.is_dir() else 1, x.name.lower()),
        )
        return entries

    def refresh(self):
        self.roots_label.setText(f"Roots: {len(self.roots)}")
        self.table.setRowCount(0)
        if self.current_folder is None:
            for p in self.list_roots():
                r = self.table.rowCount()
                self.table.insertRow(r)
                name_item = QTableWidgetItem(p.name)
                name_item.setData(Qt.UserRole, str(p))
                self.table.setItem(r, 0, name_item)
                self.table.setItem(r, 1, QTableWidgetItem("Folder"))
                self.table.setItem(r, 2, QTableWidgetItem(""))
        else:
            for p in self.list_dir(self.current_folder):
                r = self.table.rowCount()
                self.table.insertRow(r)
                name_item = QTableWidgetItem(p.name)
                name_item.setData(Qt.UserRole, str(p))
                self.table.setItem(r, 0, name_item)
                self.table.setItem(r, 1, QTableWidgetItem(human_type(p)))
                quiz_text = ""
                if p.is_file() and detect_kind(p) == "video":
                    q = self.video_quiz_map.get(p.resolve().as_posix()) or []
                    quiz_text = f"{len(q)} question(s)" if q else "0"
                self.table.setItem(r, 2, QTableWidgetItem(quiz_text))

    def selected_path(self) -> Optional[Path]:
        r = self.table.currentRow()
        if r < 0:
            return None
        item = self.table.item(r, 0)
        if not item:
            return None
        val = item.data(Qt.UserRole)
        return Path(val) if val else None

    # ===== Interactions =====
    def on_double_click(self, row: int, col: int):
        p = self.selected_path()
        if not p:
            return
        if p.is_dir():
            self.current_folder = p
            self.refresh()
            return
        if detect_kind(p) == "video":
            self.open_quiz_for_video(p)

    def add_edit_quiz_selected(self):
        p = self.selected_path()
        if not p:
            return self.alert("اختر عنصرًا أولاً.")
        if not p.is_file() or detect_kind(p) != "video":
            return self.alert("الكويز متاح للفيديو فقط.")
        self.open_quiz_for_video(p)

    def open_quiz_for_video(self, video_path: Path):
        abs_key = video_path.resolve().as_posix()
        existing = self.video_quiz_map.get(abs_key, [])
        dlg = QuizDialog(self, existing=existing)
        if dlg.exec():
            self.video_quiz_map[abs_key] = dlg.get_quiz_items()
            self.refresh()

    # ===== Build =====
    def build_package(self):
        if not self.usb_root:
            t = self.usb_edit.text().strip()
            if t:
                self.usb_root = Path(t)
        if not self.usb_root or not self.usb_root.exists():
            return self.alert("اختر مسار إخراج صالح (USB).")
        if not self.roots:
            return self.alert("أضف مجلدًا واحدًا على الأقل (Roots).")

        drive_id = get_drive_id(str(self.usb_root)) or "UNKNOWN"
        try:
            builder = PackageBuilder(self.usb_root, drive_id)
            builder.set_roots_and_quizzes(self.roots, self.video_quiz_map)
            out_dir, key_path, package_id = builder.build(write_package_id_txt=True)

            if self.chk_upload.isChecked():
                user = self.user_edit.text().strip()
                pw = self.pass_edit.text().strip()
                if not (user and pw):
                    return self.alert("أدخل User/Pass أو ألغِ خيار الرفع التلقائي.")
                ok, msg = builder.upload_to_server(server_base=SERVER_URL, admin_user=user, admin_pass=pw)
                if not ok:
                    return self.alert(f"تم الإنشاء لكن الرفع فشل:\n{msg}")
                self.info(f"تم الإنشاء والرفع.\n\nUSB: {out_dir}\npackage_id: {package_id}\nKey: {key_path}\nServer: {SERVER_URL}\nنتيجة: {msg}")
            else:
                self.info(f"تم الإنشاء.\n\nUSB: {out_dir}\npackage_id: {package_id}\nKey: {key_path}\n(الرفع التلقائي غير مُفعّل)")
        except Exception as e:
            self.alert(f"فشل: {e}")


def main():
    _set_appusermodel_id()
    app = QApplication(sys.argv)
    app.setWindowIcon(QtGui.QIcon(resource_path("assets/logo.ico")))
    w = AdminTool()
    w.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
