# AdminEncryptor/encrypt_dialog.py
from __future__ import annotations
from pathlib import Path
from PySide6 import QtCore, QtWidgets
from .encrypt_worker import EncryptWorker

class EncryptDialog(QtWidgets.QDialog):
    """
    Ready-to-use dialog:
      - pick source & destination
      - run streaming encryption in a QThread
      - live progress
      - shows success/error messages
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Encrypt Content")
        self.setModal(True)
        self.setFixedSize(560, 260)

        root = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()

        # Source
        self.src_edit = QtWidgets.QLineEdit()
        self.src_btn  = QtWidgets.QPushButton("Browse…")
        self.src_btn.clicked.connect(self._pick_src)
        h1 = QtWidgets.QHBoxLayout()
        h1.addWidget(self.src_edit, 1); h1.addWidget(self.src_btn)
        form.addRow("Source file", h1)

        # Destination
        self.dst_edit = QtWidgets.QLineEdit()
        self.dst_btn  = QtWidgets.QPushButton("Browse…")
        self.dst_btn.clicked.connect(self._pick_dst)
        h2 = QtWidgets.QHBoxLayout()
        h2.addWidget(self.dst_edit, 1); h2.addWidget(self.dst_btn)
        form.addRow("Encrypted output", h2)

        root.addLayout(form)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        btns = QtWidgets.QHBoxLayout(); btns.addStretch(1)
        self.btn_close = QtWidgets.QPushButton("Close")
        self.btn_go    = QtWidgets.QPushButton("Encrypt")
        self.btn_go.setDefault(True)
        btns.addWidget(self.btn_close); btns.addWidget(self.btn_go)
        root.addLayout(btns)

        self.btn_close.clicked.connect(self.reject)
        self.btn_go.clicked.connect(self._start)

        self._thread: QtCore.QThread | None = None
        self._worker: EncryptWorker | None = None
        self.encryption_result = None  # (key_b64, nonce_b64, tag_b64, size)

    # --- pickers ---
    def _pick_src(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select source file")
        if path:
            self.src_edit.setText(path)
            p = Path(path)
            self.dst_edit.setText(str(p.with_suffix(p.suffix + ".enc")))

    def _pick_dst(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Select output file")
        if path:
            self.dst_edit.setText(path)

    # --- start encryption ---
    def _start(self):
        src = self.src_edit.text().strip()
        dst = self.dst_edit.text().strip()
        if not src or not dst:
            QtWidgets.QMessageBox.warning(self, "Encrypt", "Please choose source and destination.")
            return

        # Busy UI
        self._set_busy(True)

        # Thread + worker
        self._thread = QtCore.QThread(self)
        self._worker = EncryptWorker(src, dst)
        self._worker.moveToThread(self._thread)

        # Wiring
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self.progress.setValue)
        self._worker.result.connect(self._capture)

        def _finish(ok: bool, msg: str):
            self._thread.quit(); self._thread.wait()
            self._thread = None
            self._worker = None
            self._set_busy(False)
            if ok:
                QtWidgets.QMessageBox.information(self, "Encrypt", msg)
                # You can now use self.encryption_result for packaging JSON, etc.
            else:
                QtWidgets.QMessageBox.critical(self, "Encrypt", msg)

        self._worker.finished.connect(_finish)
        self._thread.start()

    def _set_busy(self, busy: bool):
        self.btn_go.setEnabled(not busy)
        self.btn_close.setEnabled(not busy)
        self.progress.setVisible(busy)
        if busy:
            self.progress.setValue(0)
        QtWidgets.QApplication.setOverrideCursor(
            QtCore.Qt.WaitCursor if busy else QtCore.Qt.ArrowCursor
        )

    def _capture(self, key_b64: str, nonce_b64: str, tag_b64: str, size: int):
        self.encryption_result = (key_b64, nonce_b64, tag_b64, size)
