"""Токены, шрифты и стили. Источник — src/index.css и src/App.css веб-версии.

Названия токенов совпадают с CSS-переменными (--paper → PAPER и т. д.), чтобы
правку в веб-версии было легко перенести сюда.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QWidget

FONTS_DIR = Path(__file__).parent / "assets" / "fonts"

SANS = "IBM Plex Sans"
SERIF = "IBM Plex Serif"
MONO = "IBM Plex Mono"

# --- токены из src/index.css ---
PAPER = "#f4f3ee"
SURFACE = "#fffffe"
SUNK = "#eceae2"
INK = "#181b22"
INK_2 = "#474d5a"
INK_3 = "#7b8291"
LINE = "#e4e1d8"
LINE_2 = "#d6d2c7"
ACCENT = "#3a3d8f"
ACCENT_2 = "#4d50a8"
ACCENT_WASH = "#ecebf6"
ACCENT_INK = "#ffffff"
GOOD = "#2c7a58"
WARN = "#a2691a"
WARN_WASH = "#f6eddf"
DANGER = "#b23a45"
DANGER_WASH = "#f6e7e8"
REDACT_BG = "#17191e"
REDACT_INK = "#f3f3f0"
PENDING = "#8a4b06"
PENDING_WASH = "#fff1d6"
PENDING_LINE = "#c9821a"
READY = "#1f7a45"
READY_WASH = "#eef8f1"
READY_LINE = "#b9dcc6"
MARK = "#ffe2a8"  # подсветка совпадения в контексте кандидата

RADIUS = 12
RADIUS_SM = 8
RADIUS_XS = 5

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


def entity_fill(type_: str, alpha_scale: float = 1.0) -> QColor:
    r, g, b, a = ENTITY_RGBA.get(type_, ENTITY_RGBA["OTHER"])
    color = QColor(r, g, b)
    color.setAlphaF(min(1.0, a * alpha_scale))
    return color


def entity_solid(type_: str) -> QColor:
    r, g, b, _ = ENTITY_RGBA.get(type_, ENTITY_RGBA["OTHER"])
    return QColor(r, g, b)


def mix(a: str, b: str, share_a: float) -> QColor:
    """Аналог color-mix(in srgb, a share_a%, b)."""
    ca, cb = QColor(a), QColor(b)
    return QColor(
        round(ca.red() * share_a + cb.red() * (1 - share_a)),
        round(ca.green() * share_a + cb.green() * (1 - share_a)),
        round(ca.blue() * share_a + cb.blue() * (1 - share_a)),
    )


def register_fonts() -> list[str]:
    """Регистрирует IBM Plex из сборки. Возвращает зарегистрированные семейства."""
    families: set[str] = set()
    for path in sorted(FONTS_DIR.glob("*.ttf")):
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id >= 0:
            families.update(QFontDatabase.applicationFontFamilies(font_id))
    return sorted(families)


def font(family: str = SANS, px: float = 15, weight: int = 400, italic: bool = False) -> QFont:
    f = QFont(family)
    f.setPixelSize(round(px))
    f.setWeight(QFont.Weight(weight))
    f.setItalic(italic)
    f.setStyleHint(QFont.StyleHint.Serif if family == SERIF else
                   QFont.StyleHint.Monospace if family == MONO else QFont.StyleHint.SansSerif)
    return f


def add_shadow(widget: QWidget, pop: bool = False) -> None:
    """--shadow-sm или --shadow-pop."""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(30 if pop else 6)
    effect.setOffset(0, 10 if pop else 1)
    effect.setColor(QColor(24, 27, 34, 40 if pop else 22))
    widget.setGraphicsEffect(effect)


STYLESHEET = f"""
* {{ font-family: "{SANS}"; color: {INK}; }}
QWidget {{ font-size: 15px; }}
QMainWindow, QWidget#page, QScrollArea#page > QWidget > QWidget {{ background: {PAPER}; }}
QToolTip {{ background: {SURFACE}; color: {INK}; border: 1px solid {LINE_2}; padding: 4px 6px; font-size: 13px; }}

QLabel#muted {{ color: {INK_3}; font-size: 13px; }}
QLabel#hint {{ color: {INK_3}; font-size: 12px; }}
QLabel#title {{ font-size: 31px; font-weight: 700; }}
QLabel#subtitle {{ color: {INK_2}; font-size: 15px; }}
QLabel#paneTitle {{ font-size: 13px; font-weight: 600; }}
QLabel#sectionHead {{ color: {INK_3}; font-size: 12px; font-weight: 600; letter-spacing: 0.5px; }}
QLabel#warnText {{ color: {WARN}; font-size: 12px; }}
QLabel#errorText {{ color: {DANGER}; font-size: 13px; }}

QPushButton, QToolButton {{
  font-size: 14px; font-weight: 500; color: {INK}; background: {SURFACE};
  border: 1px solid {LINE_2}; border-radius: {RADIUS_SM}px; padding: 7px 13px;
}}
QPushButton:hover, QToolButton:hover {{ border-color: {ACCENT}; color: {ACCENT}; background: {ACCENT_WASH}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {INK_3}; background: {SURFACE}; border-color: {LINE}; }}
QPushButton#primary, QToolButton#primary {{ background: {ACCENT}; border-color: {ACCENT}; color: {ACCENT_INK}; }}
QPushButton#primary:hover, QToolButton#primary:hover {{ background: {ACCENT_2}; border-color: {ACCENT_2}; color: {ACCENT_INK}; }}
QPushButton#small {{ padding: 3px 10px; font-size: 13px; border-radius: {RADIUS_XS}px; }}
QPushButton#ghost {{ border: none; background: transparent; color: {INK_3}; padding: 2px 6px; }}
QPushButton#ghost:hover {{ background: {SUNK}; color: {INK}; }}
QPushButton#iconButton {{ border: none; background: transparent; padding: 0; min-width: 32px; min-height: 32px; max-width: 32px; max-height: 32px; }}
QPushButton#iconButton:hover {{ background: {SUNK}; }}
QPushButton#link {{
  border: none; background: transparent; color: {ACCENT}; padding: 0; font-size: 13px;
  text-decoration: underline; text-align: left;
}}
QPushButton#link:hover {{ color: {ACCENT_2}; background: transparent; }}
QPushButton#danger {{ color: {DANGER}; padding: 3px 10px; font-size: 13px; border-radius: {RADIUS_XS}px; }}
QPushButton#danger:hover {{ border-color: {DANGER}; background: {DANGER_WASH}; color: {DANGER}; }}
QPushButton#chipPending {{
  background: {PENDING_WASH}; border: 1px solid {PENDING_LINE}; color: {PENDING};
  border-radius: 13px; padding: 4px 12px; font-size: 13px; font-weight: 500;
}}
QPushButton#chipPending:hover {{ border-color: {PENDING}; color: {INK}; background: {PENDING_WASH}; }}
QLabel#chipReady {{
  background: {READY_WASH}; border: 1px solid {READY_LINE}; color: {READY};
  border-radius: 13px; padding: 4px 12px; font-size: 13px; font-weight: 500;
}}
QPushButton#splitMain {{ border-top-right-radius: 0; border-bottom-right-radius: 0; }}
QPushButton#splitToggle {{
  border-top-left-radius: 0; border-bottom-left-radius: 0; border-left: 1px solid rgba(255,255,255,90);
  padding-left: 9px; padding-right: 9px;
}}
QPushButton#viewToggle {{ border: none; border-radius: 0; background: transparent; color: {INK_3}; padding: 6px 14px; font-size: 13px; }}
QPushButton#viewToggle:checked {{ background: {ACCENT}; color: {ACCENT_INK}; }}
QFrame#viewToggleBox {{ border: 1px solid {LINE}; border-radius: {RADIUS_SM}px; background: {SURFACE}; }}

QComboBox {{
  font-size: 14px; color: {INK}; background: {SURFACE}; border: 1px solid {LINE_2};
  border-radius: {RADIUS_SM}px; padding: 5px 28px 5px 8px; combobox-popup: 0;
}}
QComboBox:hover {{ border-color: {INK_3}; }}
QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox:disabled {{ color: {INK_3}; border-color: {LINE}; }}
QComboBox#mono {{ font-family: "{MONO}"; font-size: 12px; padding: 3px 26px 3px 6px; }}
QComboBox#compact {{ font-size: 12px; padding: 2px 24px 2px 6px; }}
/* Стрелку рисует Select.paintEvent значком chevron-down. */
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: none; width: 0; height: 0; }}
QComboBoxPrivateContainer {{ background: {SURFACE}; border: none; }}
QListView#selectList {{
  background: {SURFACE}; border: 1px solid {LINE_2}; outline: 0; padding: 4px; color: {INK};
}}
QListView#selectList::item {{ padding: 6px 10px; border-radius: {RADIUS_XS}px; border: none; }}
QListView#selectList::item:hover {{ background: {SUNK}; }}
QListView#selectList::item:selected {{ background: {ACCENT_WASH}; color: {INK}; }}
QListView#selectList QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QListView#selectList QScrollBar::handle:vertical {{ background: {LINE_2}; border-radius: 3px; min-height: 24px; }}
QListView#selectList QScrollBar::add-line:vertical, QListView#selectList QScrollBar::sub-line:vertical {{ height: 0; }}
QListView#selectList QScrollBar::add-page:vertical, QListView#selectList QScrollBar::sub-page:vertical {{ background: transparent; }}
QCheckBox {{ font-size: 13px; spacing: 8px; }}
QCheckBox::indicator {{ width: 15px; height: 15px; }}

QFrame#card {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: {RADIUS}px; }}
QFrame#topbar {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: {RADIUS}px; }}
QFrame#topbar[dragging="true"] {{ background: {ACCENT_WASH}; border-color: {ACCENT}; }}
QFrame#divider {{ background: {LINE}; }}
QFrame#fact {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: {RADIUS_SM}px; }}
QFrame#factPrivacy {{ background: {READY_WASH}; border: 1px solid {READY_LINE}; border-radius: {RADIUS_SM}px; }}
QFrame#fact QLabel, QFrame#factPrivacy QLabel {{ color: {INK_2}; font-size: 14px; }}
QFrame#upload {{ background: {SURFACE}; border: 1.5px dashed {LINE_2}; border-radius: {RADIUS}px; }}
QFrame#upload[dragging="true"], QFrame#upload:hover {{ border-color: {ACCENT}; }}
QFrame#upload[dragging="true"] {{ background: {ACCENT_WASH}; }}
QLabel#dropIcon {{ background: {ACCENT_WASH}; border-radius: 22px; }}
QFrame#historyRow {{ border: none; border-bottom: 1px solid {LINE}; background: transparent; }}
QFrame#notice {{ background: {WARN_WASH}; border: 1px solid {WARN}; border-radius: 6px; }}
QFrame#notice QLabel {{ color: {WARN}; font-size: 13px; }}
QFrame#noticeError {{ background: {DANGER_WASH}; border: 1px solid {DANGER}; border-radius: 6px; }}
QFrame#noticeError QLabel {{ color: {DANGER}; font-size: 13px; }}
QFrame#plainNotice QLabel {{ color: {INK_2}; font-size: 13px; }}

QFrame#menuPanel {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: {RADIUS_SM}px; }}
QPushButton#menuItem {{ border: none; background: transparent; border-radius: {RADIUS_XS}px; padding: 0; text-align: left; }}
QPushButton#menuItem:hover {{ background: {SUNK}; }}
QPushButton#menuItem[current="true"] {{ background: {SUNK}; }}
QLabel#menuTitle {{ font-size: 14px; font-weight: 500; color: {INK}; background: transparent; }}
QLabel#menuHint {{ font-size: 12px; color: {INK_3}; background: transparent; }}
QLabel#menuHintWarn {{ font-size: 12px; color: {WARN}; background: transparent; }}

QFrame#editorBox, QFrame#previewBox {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: {RADIUS}px; }}
QTextEdit#sourceEditor, QTextBrowser#resultPreview {{ font-family: "{SERIF}"; font-size: 15px; }}
QWidget#panelBody {{ background: {SURFACE}; }}
QWidget#clickable {{ background: transparent; border-radius: {RADIUS_XS}px; }}
QWidget#clickable[hover="true"] {{ background: {SUNK}; }}
QPlainTextEdit, QTextEdit, QTextBrowser {{ background: transparent; border: none; selection-background-color: {mix(ACCENT, SURFACE, 0.28).name()}; selection-color: {INK}; }}
QFrame#popup {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: {RADIUS_SM}px; }}
QPushButton#mergeOpen {{ font-size: 12px; padding: 3px 9px; border: 1px solid transparent; background: {ACCENT_WASH}; color: {ACCENT}; border-radius: {RADIUS_XS}px; }}
QPushButton#mergeOpen:hover {{ background: {ACCENT}; color: {ACCENT_INK}; }}

QFrame#entityPane {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: {RADIUS}px; }}
QFrame#sideTabs {{ background: transparent; border: none; border-bottom: 1px solid {LINE}; }}
QPushButton#sideTab {{ border: none; border-bottom: 2px solid transparent; border-radius: 0; background: transparent; color: {INK_3}; font-size: 14px; padding: 0 8px; min-height: 42px; }}
QPushButton#sideTab:hover {{ border-bottom-color: {LINE_2}; color: {INK}; background: transparent; }}
QPushButton#sideTab[selected="true"] {{ border-bottom-color: {ACCENT}; color: {INK}; font-weight: 600; }}
QLabel#badge {{ background: {SUNK}; color: {INK_2}; border-radius: 9px; padding: 0 7px; min-height: 18px; max-height: 18px; font-size: 12px; font-weight: 600; }}
QLabel#badgePending {{ background: {PENDING_WASH}; color: {PENDING}; border-radius: 9px; padding: 0 7px; min-height: 18px; max-height: 18px; font-size: 12px; font-weight: 600; }}
QLabel#badgeDone {{ background: {READY_WASH}; color: {READY}; border-radius: 9px; padding: 0 7px; min-height: 18px; max-height: 18px; font-size: 12px; font-weight: 600; }}

QFrame#candidateCard {{ background: {mix(PENDING_WASH, SURFACE, 0.55).name()}; border: 1px solid {PENDING_LINE}; border-radius: {RADIUS_SM}px; }}
QFrame#candidateCard[focused="true"] {{ border: 2px solid {ACCENT}; }}
QLabel#cardCount {{ color: {PENDING}; font-size: 13px; font-weight: 500; }}
QLabel#cardValue {{ font-size: 19px; font-weight: 600; }}
QLabel#cardContext {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: {RADIUS_XS}px; padding: 9px 11px; font-family: "{SERIF}"; font-size: 14px; color: {INK_2}; }}
QLabel#cardReason {{ font-size: 13px; font-weight: 500; }}
QLabel#fieldLabel {{ color: {INK_3}; font-size: 13px; }}
QFrame#reviewDone {{ background: {READY_WASH}; border: 1px solid {READY_LINE}; border-radius: {RADIUS_SM}px; }}
QLabel#doneTitle {{ color: {READY}; font-weight: 600; font-size: 14px; }}
QFrame#candidateItem {{ border-radius: {RADIUS_XS}px; background: transparent; }}
QFrame#candidateItem[current="true"] {{ background: {ACCENT_WASH}; }}
QLabel#itemNum {{ background: {PENDING_WASH}; color: {PENDING}; border-radius: 11px; font-size: 11px; font-weight: 600; }}
QLabel#itemValue {{ font-size: 13px; font-weight: 500; }}
QLabel#itemReason {{ font-size: 12px; color: {INK_3}; }}
QLabel#itemReasonCurrent {{ font-size: 12px; color: {ACCENT}; }}
QPushButton#disclosure {{ border: none; background: transparent; color: {INK_2}; font-size: 13px; font-weight: 500; padding: 4px; text-align: left; }}

QFrame#groupHeader {{ background: {SURFACE}; border-radius: {RADIUS_XS}px; }}
QFrame#groupHeader[highlighted="true"] {{ border: 2px solid {ACCENT}; }}
QFrame#groupSep {{ background: {LINE}; }}
QLabel#groupName {{ font-family: "{MONO}"; font-size: 12px; font-weight: 500; }}
QLabel#groupCount {{ color: {INK_3}; font-size: 12px; }}
QFrame#entityRow {{ border-radius: {RADIUS_XS}px; background: transparent; }}
QFrame#entityRow[selected="true"] {{ background: {ACCENT_WASH}; }}
QFrame#entityRow[highlighted="true"] {{ background: {ACCENT_WASH}; border-left: 3px solid {ACCENT}; }}
QLabel#cellValue {{ font-size: 13px; font-weight: 500; }}
QLabel#cellValueOff {{ font-size: 13px; font-weight: 500; color: {INK_3}; text-decoration: line-through; }}
QLabel#cellPlaceholder {{ background: {SUNK}; color: {INK_2}; font-family: "{MONO}"; font-size: 11px; border-radius: 4px; padding: 1px 6px; }}
QLabel#cellCount {{ color: {INK_3}; font-size: 12px; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {LINE_2}; border-radius: 4px; min-height: 30px; margin: 2px; }}
QScrollBar::handle:vertical:hover {{ background: {INK_3}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: {LINE_2}; border-radius: 4px; min-width: 30px; margin: 2px; }}
QSplitter::handle {{ background: transparent; }}
QMessageBox QLabel {{ font-size: 14px; }}
"""
