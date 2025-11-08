from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QComboBox, QMessageBox, QSpacerItem, QSizePolicy
)

def _clean(s: str) -> str:
    return (s or "").strip()

class _QuizRow:
    def __init__(self, idx: int):
        self.idx = idx
        self.q_edit = QLineEdit()
        self.opt_edits = [QLineEdit(), QLineEdit(), QLineEdit(), QLineEdit()]
        self.correct_box = QComboBox()
        self.correct_box.addItems(["A (0)", "B (1)", "C (2)", "D (3)"])

    def to_dict_or_none(self):
        q = _clean(self.q_edit.text())
        opts = [_clean(x.text()) for x in self.opt_edits]
        if not q:
            return None
        if any(not o for o in opts):
            return None
        if len(set(opts)) < 4:
            return None
        correct = self.correct_box.currentIndex()
        return {"q": q, "options": opts, "correct_index": correct}

    def load(self, data):
        self.q_edit.setText(data.get("q",""))
        opts = data.get("options") or ["","","",""]
        for i in range(4):
            self.opt_edits[i].setText(opts[i] if i < len(opts) else "")
        ci = data.get("correct_index", 0)
        if 0 <= ci <= 3:
            self.correct_box.setCurrentIndex(ci)

class QuizDialog(QDialog):
    """
    Allows 0..2 questions. Leave rows empty to skip.
    """
    def __init__(self, parent=None, existing=None):
        super().__init__(parent)
        self.setWindowTitle("Add/Edit Quiz (0–2 questions)")
        self.rows = [_QuizRow(1), _QuizRow(2)]
        self._build_ui()
        # preload
        if isinstance(existing, list):
            for i, row in enumerate(self.rows):
                if i < len(existing):
                    row.load(existing[i])

    def _build_ui(self):
        root = QVBoxLayout()

        for i, row in enumerate(self.rows, start=1):
            root.addWidget(QLabel(f"Question {i} (optional)"))
            qline = row.q_edit
            root.addWidget(qline)

            h1 = QHBoxLayout()
            h1.addWidget(QLabel("Option A:")); h1.addWidget(row.opt_edits[0])
            h1.addWidget(QLabel("Option B:")); h1.addWidget(row.opt_edits[1])
            root.addLayout(h1)

            h2 = QHBoxLayout()
            h2.addWidget(QLabel("Option C:")); h2.addWidget(row.opt_edits[2])
            h2.addWidget(QLabel("Option D:")); h2.addWidget(row.opt_edits[3])
            root.addLayout(h2)

            h3 = QHBoxLayout()
            h3.addWidget(QLabel("Correct:")); h3.addWidget(row.correct_box)
            root.addLayout(h3)

            root.addItem(QSpacerItem(0,12, QSizePolicy.Minimum, QSizePolicy.Minimum))

        btns = QHBoxLayout()
        b_ok = QPushButton("OK"); b_ok.clicked.connect(self.accept)
        b_cancel = QPushButton("Cancel"); b_cancel.clicked.connect(self.reject)
        btns.addStretch(1); btns.addWidget(b_ok); btns.addWidget(b_cancel)
        root.addLayout(btns)

        self.setLayout(root)
        self.resize(600, 360)

    def get_quiz_items(self):
        out = []
        for row in self.rows:
            d = row.to_dict_or_none()
            if d: out.append(d)
        # cap at 2, already enforced
        return out
