"""Цвета и стили. Палитра типов сущностей взята из App.css браузерной версии."""

from __future__ import annotations

from PySide6.QtGui import QColor

# (r, g, b, alpha) — как --ent-* в App.css.
ENTITY_RGBA: dict[str, tuple[int, int, int, float]] = {
    "PERSON": (42, 120, 214, 0.30),
    "COMPANY": (237, 161, 0, 0.30),
    "ADDRESS": (0, 131, 0, 0.30),
    "REG_NUMBER": (74, 58, 167, 0.32),
    "ID_DOC": (176, 52, 196, 0.26),
    "IBAN": (27, 175, 122, 0.30),
    "SWIFT": (232, 123, 164, 0.34),
    "ACCOUNT": (235, 104, 52, 0.30),
    "CARD": (227, 73, 72, 0.30),
    "EMAIL": (14, 155, 184, 0.30),
    "PHONE": (138, 109, 59, 0.34),
    "URL": (122, 150, 0, 0.32),
    "OTHER": (110, 118, 135, 0.30),
}

ACCENT = "#1f5fbf"
WARN = "#a15c00"
ERROR = "#b42318"
MUTED = "#5b6474"


def entity_fill(type_: str, alpha_scale: float = 1.0) -> QColor:
    r, g, b, a = ENTITY_RGBA.get(type_, ENTITY_RGBA["OTHER"])
    color = QColor(r, g, b)
    color.setAlphaF(min(1.0, a * alpha_scale))
    return color


def entity_solid(type_: str) -> QColor:
    r, g, b, _ = ENTITY_RGBA.get(type_, ENTITY_RGBA["OTHER"])
    return QColor(r, g, b)


STYLESHEET = f"""
QWidget {{ font-size: 10pt; }}
QMainWindow, QWidget#page {{ background: #f7f8fa; }}
QLabel#title {{ font-size: 20pt; font-weight: 600; }}
QLabel#muted, QLabel.muted {{ color: {MUTED}; }}
QFrame#dropzone {{ border: 2px dashed #9aa4b5; border-radius: 10px; background: #ffffff; }}
QFrame#dropzone[dragging="true"] {{ border-color: {ACCENT}; background: #eef4fd; }}
QFrame#card, QFrame#fact, QFrame#candidateCard {{ background: #ffffff; border: 1px solid #dde1e8; border-radius: 8px; }}
QFrame#candidateCard:focus {{ border: 2px solid {ACCENT}; }}
QFrame#notice {{ background: #fff8e6; border: 1px solid #f0d58a; border-radius: 6px; }}
QFrame#noticeError {{ background: #fdecea; border: 1px solid #f3b1aa; border-radius: 6px; }}
QPushButton {{ padding: 5px 12px; border: 1px solid #c4cad4; border-radius: 6px; background: #ffffff; }}
QPushButton:hover {{ background: #f0f3f7; }}
QPushButton:disabled {{ color: #9aa4b5; }}
QPushButton#primary, QToolButton#primary {{ background: {ACCENT}; color: white; border-color: {ACCENT}; }}
QPushButton#primary:hover, QToolButton#primary:hover {{ background: #184f9f; }}
QPushButton#link {{ border: none; background: transparent; color: {ACCENT}; text-decoration: underline; padding: 2px; }}
QPushButton#chipPending {{ background: #fff4e0; color: {WARN}; border: 1px solid #f0c27a; border-radius: 12px; }}
QLabel#chipReady {{ background: #e7f6ec; color: #1a7f37; border: 1px solid #9fd8b0; border-radius: 12px; padding: 4px 10px; }}
QToolButton#primary {{ padding: 5px 12px; border-radius: 6px; }}
QPlainTextEdit, QTextBrowser {{ background: #ffffff; border: 1px solid #dde1e8; border-radius: 6px; }}
QTabWidget::pane {{ border: 1px solid #dde1e8; border-radius: 6px; background: #ffffff; }}
QTabBar::tab {{ padding: 6px 12px; }}
QTabBar::tab:selected {{ font-weight: 600; }}
"""
