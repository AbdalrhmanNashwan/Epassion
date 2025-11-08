# ui/splash.py
from __future__ import annotations
from PySide6 import QtCore, QtGui, QtWidgets

PRIMARY    = "#22ABE1"
TEXT_DARK  = "#0B1221"
TEXT_MUTED = "#536077"
BG         = "#FFFFFF"
BORDER     = "#E7ECF2"

class SplashScreen(QtWidgets.QWidget):
    """
    Light splash screen:
      - White background
      - Rounded card with logo, title, and progress
      - Smooth fade-in/out
      - Stays visible ~2.5s by default
      - Esc/click to skip
    """
    finished = QtCore.Signal()

    def __init__(
        self,
        logo_path: str,
        brand_name: str = "Epassion",
        duration_ms: int = 2500,   # how long splash stays visible
        fade_ms: int = 450,        # fade in/out duration
        parent=None
    ):
        super().__init__(parent)
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.SplashScreen
            | QtCore.Qt.WindowStaysOnTopHint
        )
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)

        self._duration_ms = duration_ms
        self._fade_ms = fade_ms

        self._build_ui(logo_path, brand_name)
        self._build_animations()

        scr = QtWidgets.QApplication.primaryScreen().availableGeometry()
        w = max(720, int(scr.width() * 0.42))
        h = max(420, int(scr.height() * 0.38))
        self.resize(w, h)
        self.move(scr.center() - self.rect().center())

        QtCore.QTimer.singleShot(50, self._anim_in.start)

    # --- UI ---
    def _build_ui(self, logo_path: str, brand_name: str):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)

        # Card
        self.card = QtWidgets.QFrame(objectName="card")
        self.card.setStyleSheet(
            f"QFrame#card {{ background:{BG}; border:1px solid {BORDER}; border-radius:16px; }}"
        )
        cl = QtWidgets.QVBoxLayout(self.card)
        cl.setContentsMargins(28, 28, 28, 24)
        cl.setSpacing(16)

        # Drop shadow
        shadow = QtWidgets.QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(36)
        shadow.setColor(QtGui.QColor(0, 0, 0, 40))
        shadow.setOffset(0, 8)
        self.card.setGraphicsEffect(shadow)

        # Logo
        logo = QtWidgets.QLabel(alignment=QtCore.Qt.AlignCenter)
        pm = QtGui.QPixmap(logo_path)
        if not pm.isNull():
            pm = pm.scaled(140, 140, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        logo.setPixmap(pm)
        cl.addWidget(logo)

        # Title
        title = QtWidgets.QLabel(brand_name, alignment=QtCore.Qt.AlignCenter)
        title.setStyleSheet(f"color:{PRIMARY}; font-size:36px; font-weight:900;")
        cl.addWidget(title)

        # Subtext
        sub = QtWidgets.QLabel("Starting up…", alignment=QtCore.Qt.AlignCenter)
        sub.setStyleSheet(f"color:{TEXT_MUTED}; font-size:14px;")
        cl.addWidget(sub)

        cl.addStretch(1)

        # Progress
        progress = QtWidgets.QProgressBar()
        progress.setRange(0, 0)  # indeterminate
        progress.setTextVisible(False)
        progress.setFixedHeight(8)
        progress.setStyleSheet(
            f"""
            QProgressBar {{
              background: #EDF3F9;
              border: 1px solid {BORDER};
              border-radius: 6px;
            }}
            QProgressBar::chunk {{
              background: {PRIMARY};
              border-radius: 6px;
            }}
            """
        )
        cl.addWidget(progress)

        root.addWidget(self.card, 1, QtCore.Qt.AlignCenter)

        # Opacity effect
        self._op = QtWidgets.QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._op)
        self._op.setOpacity(0.0)

    # --- Animations ---
    def _build_animations(self):
        self._anim_in = QtCore.QPropertyAnimation(self._op, b"opacity", self)
        self._anim_in.setDuration(self._fade_ms)
        self._anim_in.setStartValue(0.0)
        self._anim_in.setEndValue(1.0)
        self._anim_in.setEasingCurve(QtCore.QEasingCurve.InOutCubic)
        self._anim_in.finished.connect(self._hold_then_out)

        self._anim_out = QtCore.QPropertyAnimation(self._op, b"opacity", self)
        self._anim_out.setDuration(self._fade_ms)
        self._anim_out.setStartValue(1.0)
        self._anim_out.setEndValue(0.0)
        self._anim_out.setEasingCurve(QtCore.QEasingCurve.InOutCubic)
        self._anim_out.finished.connect(self._finish)

    def _hold_then_out(self):
        QtCore.QTimer.singleShot(self._duration_ms, self._anim_out.start)

    def _finish(self):
        self.hide()
        self.finished.emit()

    # --- Skip ---
    def mousePressEvent(self, e: QtGui.QMouseEvent) -> None:
        self._anim_in.stop(); self._anim_out.stop(); self._finish()

    def keyPressEvent(self, e: QtGui.QKeyEvent) -> None:
        if e.key() in (QtCore.Qt.Key_Escape, QtCore.Qt.Key_Space, QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            self._anim_in.stop(); self._anim_out.stop(); self._finish()
        else:
            super().keyPressEvent(e)
