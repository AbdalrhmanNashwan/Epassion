# app.py
import sys
from pathlib import Path
from PySide6 import QtWidgets, QtGui
from ui.splash import SplashScreen

# <<< set your API base here >>>
SERVER_BASE_URL = "https://epassion.pythonanywhere.com"

def resource_path(rel: str) -> str:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return str(Path(base) / rel)
    return str(Path(__file__).resolve().parent / rel)

main_win = None
splash = None

def _pick_professional_font() -> QtGui.QFont:
    """
    Prefer IBM Plex Sans (excellent legibility), with modern fallbacks.
    """
    db = QtGui.QFontDatabase()
    families = set(db.families())
    preferred = [
        "IBM Plex Sans",
        "Segoe UI Variable",
        "Segoe UI",
        "Inter",
        "Roboto",
        "Noto Sans",
        "Ubuntu",
        "Helvetica Neue",
        "Arial",
    ]
    for fam in preferred:
        if fam in families:
            f = QtGui.QFont(fam)
            f.setPointSize(11)            # a bit larger for readability
            f.setHintingPreference(QtGui.QFont.PreferFullHinting)
            return f
    f = QtGui.QFont()
    f.setPointSize(11)
    return f

def boot_main():
    global main_win
    try:
        from ui.main_window import MainWindow
        main_win = MainWindow(
            server_base_url=SERVER_BASE_URL,
            company_logo_path=resource_path("assets/logo.png"),
        )
        main_win.show()
    except Exception as e:
        msg = QtWidgets.QMessageBox()
        msg.setIcon(QtWidgets.QMessageBox.Critical)
        msg.setWindowTitle("Epassion")
        msg.setText("Failed to start the player.")
        msg.setInformativeText(str(e))
        msg.exec()

def on_splash_finished():
    global splash
    if splash is not None:
        splash.deleteLater()
        splash = None
    boot_main()

def main():
    app = QtWidgets.QApplication(sys.argv)

    app.setWindowIcon(QtGui.QIcon(resource_path("assets/logo.png")))

    # Professional, consistent font
    app.setFont(_pick_professional_font())

    # Load theme (light)
    try:
        with open(resource_path("ui/theme.qss"), "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())
    except Exception:
        pass

    # Splash
    global splash
    try:
        splash = SplashScreen(
            logo_path=resource_path("assets/logo.png"),
            brand_name="Epassion",
            duration_ms=2000,
            fade_ms=300,
        )
        splash.finished.connect(on_splash_finished)
        splash.show()
    except Exception:
        boot_main()

    sys.exit(app.exec())

if __name__ == "__main__":
    main()
