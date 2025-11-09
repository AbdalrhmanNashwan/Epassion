# AdminEncryptor/encrypt_worker.py
from __future__ import annotations
from PySide6 import QtCore
from .crypto_utils import encrypt_file_aesgcm_stream

class EncryptWorker(QtCore.QObject):
    """
    Runs streaming AES-GCM encryption in a background thread.
    Emits progress (0..100), finished(ok, msg), and result(key, nonce, tag, size).
    """
    progress = QtCore.Signal(int)
    finished = QtCore.Signal(bool, str)
    result = QtCore.Signal(str, str, str, int)

    def __init__(self, src_path: str, dst_path: str, parent=None):
        super().__init__(parent)
        self._src = src_path
        self._dst = dst_path

    @QtCore.Slot()
    def run(self):
        try:
            def on_prog(p): self.progress.emit(p)
            key_b64, nonce_b64, tag_b64, size = encrypt_file_aesgcm_stream(
                self._src, self._dst, on_progress=on_prog
            )
            self.result.emit(key_b64, nonce_b64, tag_b64, size)
            self.finished.emit(True, "Encryption completed")
        except Exception as e:
            self.finished.emit(False, str(e))
