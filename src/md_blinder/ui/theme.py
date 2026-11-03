"""Единые цвета, шрифты, размеры и таблица стилей всех экранов.

Виджеты не задают собственных стилей: вид определяется именем объекта (objectName)
и свойствами вроде tone и small, правила для них собраны здесь.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QWidget

FONTS_DIR = Path(__file__).parent / "assets" / "fonts"

SANS = "IBM Plex Sans"
SERIF = "IBM Plex Serif"
MONO = "IBM Plex Mono"

# --- цвета ---
PAPER = "#f4f3ee"  # фон окна
SURFACE = "#fffffe"  # карточки, панели, меню, окна
SURFACE_2 = "#faf9f5"  # полоса номеров строк, заголовки групп
SUNK = "#eceae2"  # наведение, счетчики, метки замен
INK = "#181b22"
INK_2 = "#474d5a"
INK_3 = "#646a78"  # подписи; темнее прежнего #7b8291, чтобы мелкий текст читался (контраст 4,9)
LINE = "#e4e1d8"
LINE_2 = "#d6d2c7"
ACCENT = "#3a3d8f"
ACCENT_2 = "#4d50a8"
ACCENT_WASH = "#ecebf6"
ACCENT_LINE = "#c9c8ea"
ACCENT_INK = "#ffffff"
INFO_INK = "#2e3170"  # текст на плашке-сведении
# «Внимание» и «не проверено» — один оранжевый тон.
WARN = "#8a4b06"
WARN_WASH = "#fff1d6"
WARN_LINE = "#e7c48b"
PENDING = WARN
PENDING_WASH = WARN_WASH
PENDING_LINE = "#c9821a"  # подчеркивание непроверенного фрагмента в тексте
DANGER = "#b23a45"
DANGER_INK = "#8f2833"
DANGER_WASH = "#f6e7e8"
DANGER_LINE = "#e3b7bb"
READY = "#1f7a45"
READY_WASH = "#eef8f1"
READY_LINE = "#b9dcc6"
GOOD = READY
REDACT_BG = "#17191e"
REDACT_INK = "#f3f3f0"
MARK = "#ffe2a8"  # подсветка совпадения в контексте кандидата

# Скругления: кнопки и поля, карточки и окна. Чипы и счетчики — полукругом.
RADIUS = 10
RADIUS_SM = 6
RADIUS_XS = 4

# Шесть размеров шрифта на весь интерфейс; текст документа — Serif 15.
FS_CAPTION = 12  # подписи, счетчики, даты
FS_SMALL = 13  # пояснения, плашки, подсказки в меню
FS_BODY = 14  # основной текст, кнопки, пункты меню
FS_SECTION = 16  # заголовок раздела
FS_TITLE = 20  # заголовок окна и карточки
FS_SCREEN = 28  # заголовок экрана
FS_DOC = 15

# Высота кнопок и полей: обычная и внутри строк списков.
CONTROL_H = 32
CONTROL_H_SM = 26

# Тона плашек: фон, рамка, текст, значок.
TONES: dict[str, tuple[str, str, str, str]] = {
    "info": (ACCENT_WASH, ACCENT_LINE, INFO_INK, "info"),
    "warn": (WARN_WASH, WARN_LINE, WARN, "warn"),
    "success": (READY_WASH, READY_LINE, READY, "check"),
    "danger": (DANGER_WASH, DANGER_LINE, DANGER_INK, "error"),
}

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


def _tone_rules() -> str:
    """Плашки (Notice) и полоса обновления: фон, рамка и цвет текста по тону."""
    rules = []
    for tone, (bg, line, ink, _icon) in TONES.items():
        rules.append(
            f'QFrame#notice[tone="{tone}"] {{ background: {bg}; border: 1px solid {line}; border-radius: {RADIUS}px; }}'
            f'QFrame#notice[tone="{tone}"] QLabel {{ color: {ink}; background: transparent; font-size: {FS_SMALL}px; }}'
            f'QFrame#notice[tone="{tone}"] QPushButton {{ color: {ink}; border-color: {ink}; background: transparent; }}'
            f'QFrame#notice[tone="{tone}"] QPushButton:hover {{ background: {SURFACE}; }}'
            f'QFrame#notice[tone="{tone}"] QPushButton#ghost {{ border-color: transparent; }}'
            f'QFrame#notice[tone="{tone}"] QPushButton#primary {{ background: {ink}; border-color: {ink}; color: {SURFACE}; }}'
            f'QFrame#notice[tone="{tone}"] QPushButton#primary:hover {{ background: {ink}; }}'
        )
    return "\n".join(rules)


STYLESHEET = f"""
* {{ font-family: "{SANS}"; color: {INK}; }}
QWidget {{ font-size: {FS_BODY}px; }}
QMainWindow, QWidget#page, QScrollArea#page > QWidget > QWidget {{ background: {PAPER}; }}
QToolTip {{ background: {SURFACE}; color: {INK}; border: 1px solid {LINE_2}; padding: 4px 6px; font-size: {FS_SMALL}px; }}

/* Текст */
QLabel {{ background: transparent; }}
QLabel#screenTitle {{ font-size: {FS_SCREEN}px; font-weight: 600; }}
QLabel#dialogTitle {{ font-size: {FS_TITLE}px; font-weight: 600; }}
QLabel#sectionTitle {{ font-size: {FS_SECTION}px; font-weight: 600; }}
QLabel#subtitle {{ color: {INK_2}; font-size: {FS_BODY}px; }}
QLabel#body {{ font-size: {FS_BODY}px; }}
QLabel#secondary {{ color: {INK_2}; font-size: {FS_SMALL}px; }}
QLabel#muted {{ color: {INK_3}; font-size: {FS_SMALL}px; }}
QLabel#hint {{ color: {INK_3}; font-size: {FS_CAPTION}px; }}
QLabel#strong {{ font-size: {FS_BODY}px; font-weight: 600; }}
QLabel#strongSmall {{ font-size: {FS_SMALL}px; font-weight: 600; }}
QLabel#historyName {{ font-size: {FS_BODY}px; font-weight: 500; }}
QLabel#paneTitle {{ font-size: {FS_SMALL}px; font-weight: 600; }}
QLabel#sectionHead {{ color: {INK_3}; font-size: {FS_CAPTION}px; font-weight: 600; letter-spacing: 0.4px; }}
QLabel#warnText {{ color: {WARN}; font-size: {FS_CAPTION}px; }}
QLabel#errorText {{ color: {DANGER}; font-size: {FS_SMALL}px; }}
QLabel#saveState {{ font-size: {FS_CAPTION}px; color: {INK_3}; }}
QLabel#saveState[state="saved"] {{ color: {READY}; }}
QLabel#saveState[state="error"] {{ color: {DANGER}; }}
QLabel#brand {{ font-size: {FS_SECTION}px; font-weight: 600; }}
QLabel#crumb {{ font-size: {FS_BODY}px; font-weight: 600; }}
QLabel#kbd {{ font-family: "{MONO}"; font-size: 11px; border: 1px solid {INK_3}; border-radius: {RADIUS_XS}px; padding: 0 4px; color: {INK_2}; }}
QLabel#kbdOnPrimary {{ font-family: "{MONO}"; font-size: 11px; border: 1px solid rgba(255,255,255,170); border-radius: {RADIUS_XS}px; padding: 0 4px; color: {ACCENT_INK}; }}
QLabel#onPrimary {{ color: {ACCENT_INK}; font-size: {FS_BODY}px; font-weight: 500; }}
QLabel#buttonText {{ font-size: {FS_BODY}px; font-weight: 500; }}

/* Кнопки: обычная, основная (#primary), тихая (#ghost), опасная (#danger, #dangerPrimary).
   Высота 32 px, с small="true" — 26 px. */
QPushButton, QToolButton {{
  font-size: {FS_BODY}px; font-weight: 500; color: {INK}; background: {SURFACE};
  border: 1px solid {LINE_2}; border-radius: {RADIUS_SM}px; padding: 0 12px; min-height: {CONTROL_H - 2}px;
}}
QPushButton:hover, QToolButton:hover {{ border-color: {ACCENT}; color: {ACCENT}; background: {ACCENT_WASH}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {INK_3}; background: {SURFACE}; border-color: {LINE}; }}
QPushButton[small="true"], QPushButton#small {{ padding: 0 9px; min-height: {CONTROL_H_SM - 2}px; font-size: {FS_SMALL}px; }}
QPushButton#primary, QPushButton#splitMain {{ background: {ACCENT}; border-color: {ACCENT}; color: {ACCENT_INK}; }}
QPushButton#primary:hover, QPushButton#splitMain:hover {{ background: {ACCENT_2}; border-color: {ACCENT_2}; color: {ACCENT_INK}; }}
QPushButton#primary:disabled {{ background: {mix(ACCENT, SURFACE, 0.45).name()}; border-color: {mix(ACCENT, SURFACE, 0.45).name()}; color: {ACCENT_INK}; }}
QPushButton#ghost {{ border-color: transparent; background: transparent; color: {INK_2}; }}
QPushButton#ghost:hover {{ background: {SUNK}; color: {INK}; border-color: transparent; }}
QPushButton#ghost:disabled {{ background: transparent; border-color: transparent; color: {LINE_2}; }}
QPushButton#danger {{ color: {DANGER}; }}
QPushButton#danger:hover {{ border-color: {DANGER}; background: {DANGER_WASH}; color: {DANGER}; }}
QPushButton#dangerPrimary {{ background: {DANGER}; border-color: {DANGER}; color: {ACCENT_INK}; }}
QPushButton#dangerPrimary:hover {{ background: {DANGER_INK}; border-color: {DANGER_INK}; color: {ACCENT_INK}; }}
QPushButton#iconButton {{
  border: none; background: transparent; padding: 0;
  min-width: {CONTROL_H}px; max-width: {CONTROL_H}px; min-height: {CONTROL_H}px; max-height: {CONTROL_H}px;
}}
QPushButton#iconButton[small="true"] {{
  min-width: {CONTROL_H_SM}px; max-width: {CONTROL_H_SM}px; min-height: {CONTROL_H_SM}px; max-height: {CONTROL_H_SM}px;
}}
QPushButton#iconButton:hover {{ background: {SUNK}; }}
QPushButton#link {{
  border: none; background: transparent; color: {ACCENT}; padding: 0; min-height: 0; font-size: {FS_SMALL}px;
  text-decoration: underline; text-align: left;
}}
QPushButton#link:hover {{ color: {ACCENT_2}; background: transparent; }}
QPushButton#textAction {{ border: none; background: transparent; color: {ACCENT}; padding: 0 4px; font-size: {FS_SMALL}px; text-align: left; }}
QPushButton#textAction:hover {{ background: {ACCENT_WASH}; color: {ACCENT_2}; }}
QPushButton#splitMain {{ border-top-right-radius: 0; border-bottom-right-radius: 0; }}
QPushButton#splitToggle {{
  background: {ACCENT}; border-color: {ACCENT}; color: {ACCENT_INK};
  border-top-left-radius: 0; border-bottom-left-radius: 0; border-left: 1px solid rgba(255,255,255,90);
  padding: 0; min-width: {CONTROL_H}px; max-width: {CONTROL_H}px;
}}
QPushButton#splitToggle:hover {{ background: {ACCENT_2}; border-color: {ACCENT_2}; }}

/* Чипы и счетчики */
QPushButton#chipPending, QLabel#chipPending {{
  background: {WARN_WASH}; border: 1px solid {WARN_LINE}; color: {WARN};
  border-radius: 12px; padding: 0 10px; min-height: 22px; max-height: 22px; font-size: {FS_CAPTION}px; font-weight: 500;
}}
QPushButton#chipPending:hover {{ border-color: {WARN}; color: {WARN}; background: {WARN_WASH}; }}
QPushButton#chipReady, QLabel#chipReady {{
  background: {READY_WASH}; border: 1px solid {READY_LINE}; color: {READY};
  border-radius: 12px; padding: 0 10px; min-height: 22px; max-height: 22px; font-size: {FS_CAPTION}px; font-weight: 500;
}}
QLabel#badge, QLabel#badgePending, QLabel#badgeDone {{
  border-radius: 10px; padding: 0 7px; min-height: 20px; max-height: 20px; font-size: {FS_CAPTION}px; font-weight: 600;
}}
QLabel#badge {{ background: {SUNK}; color: {INK_2}; }}
QLabel#badgePending {{ background: {WARN_WASH}; color: {WARN}; }}
QLabel#badgeDone {{ background: {READY_WASH}; color: {READY}; }}
QLabel#placeholder, QLabel#cellPlaceholder {{
  background: {SUNK}; color: {INK_2}; font-family: "{MONO}"; font-size: {FS_CAPTION}px; font-weight: 500;
  border-radius: {RADIUS_XS}px; padding: 1px 6px;
}}
QLabel#fileType {{
  font-family: "{MONO}"; font-size: 10px; font-weight: 600; border-radius: {RADIUS_SM}px;
  min-width: 30px; max-width: 30px; min-height: 28px; max-height: 28px; qproperty-alignment: AlignCenter;
}}
QLabel#fileType[kind="docx"] {{ background: #e3ecfa; color: #2a5fa8; }}
QLabel#fileType[kind="pdf"] {{ background: {DANGER_WASH}; color: {DANGER_INK}; }}
QLabel#fileType[kind="xlsx"] {{ background: #e4f3e8; color: {READY}; }}

/* Поля ввода и списки */
QLineEdit {{
  font-size: {FS_BODY}px; color: {INK}; background: {SURFACE}; border: 1px solid {LINE_2};
  border-radius: {RADIUS_SM}px; padding: 0 8px; min-height: {CONTROL_H - 2}px;
}}
QLineEdit:focus {{ border-color: {ACCENT}; }}
QComboBox QLineEdit {{ border: none; padding: 0; min-height: 0; background: transparent; }}
QComboBox {{
  font-size: {FS_BODY}px; color: {INK}; background: {SURFACE}; border: 1px solid {LINE_2};
  border-radius: {RADIUS_SM}px; padding: 0 28px 0 10px; min-height: {CONTROL_H - 2}px; combobox-popup: 0;
}}
QComboBox:hover {{ border-color: {INK_3}; }}
QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox:disabled {{ color: {INK_3}; border-color: {LINE}; }}
QComboBox#mono {{ font-family: "{MONO}"; font-size: {FS_CAPTION}px; padding: 0 26px 0 8px; min-height: {CONTROL_H_SM - 2}px; }}
QComboBox#compact {{ font-size: {FS_SMALL}px; padding: 0 24px 0 8px; min-height: {CONTROL_H_SM - 2}px; }}
/* Стрелку рисует Select.paintEvent значком chevron-down. */
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: none; width: 0; height: 0; }}
QComboBoxPrivateContainer {{ background: {SURFACE}; border: none; }}
QListView#selectList {{
  background: {SURFACE}; border: 1px solid {LINE_2}; border-radius: {RADIUS_SM}px; outline: 0; padding: 4px; color: {INK};
}}
QListView#selectList::item {{ padding: 6px 10px; border-radius: {RADIUS_XS}px; border: none; }}
QListView#selectList::item:hover {{ background: {SUNK}; }}
QListView#selectList::item:selected {{ background: {ACCENT_WASH}; color: {INK}; }}
QListView#selectList QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QListView#selectList QScrollBar::handle:vertical {{ background: {LINE_2}; border-radius: 3px; min-height: 24px; }}
QListView#selectList QScrollBar::add-line:vertical, QListView#selectList QScrollBar::sub-line:vertical {{ height: 0; }}
QListView#selectList QScrollBar::add-page:vertical, QListView#selectList QScrollBar::sub-page:vertical {{ background: transparent; }}
QCheckBox {{ font-size: {FS_BODY}px; spacing: 8px; }}
QCheckBox#small {{ font-size: {FS_SMALL}px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; }}

/* Шапка окна */
QFrame#appHeader {{ background: {SURFACE}; border: none; border-bottom: 1px solid {LINE}; }}
QFrame#appHeader[dragging="true"] {{ background: {ACCENT_WASH}; }}
QPushButton#brandButton {{ border: none; background: transparent; padding: 0 6px; }}
QPushButton#brandButton:hover {{ background: {SUNK}; }}
QPushButton#crumbLink {{ border: none; background: transparent; color: {INK_2}; padding: 0 6px; font-weight: 400; }}
QPushButton#crumbLink:hover {{ background: {SUNK}; color: {INK}; }}
QPushButton#menuButton {{ padding: 0; }}
QFrame#divider {{ background: {LINE}; }}

/* Плашки */
{_tone_rules()}

/* Карточки и панели */
QFrame#card, QFrame#listCard, QFrame#pane, QFrame#entityPane {{
  background: {SURFACE}; border: 1px solid {LINE}; border-radius: {RADIUS}px;
}}
QFrame#paneHead {{ background: transparent; border: none; border-bottom: 1px solid {LINE}; }}
QFrame#segmented {{ background: {SUNK}; border: none; border-radius: 8px; }}
QPushButton#segment {{
  border: none; background: transparent; color: {INK_2}; padding: 0 12px; min-height: {CONTROL_H_SM}px;
  max-height: {CONTROL_H_SM}px; font-size: {FS_SMALL}px; border-radius: {RADIUS_SM}px;
}}
QPushButton#segment:hover {{ color: {INK}; background: transparent; }}
QPushButton#segment:checked {{ background: {SURFACE}; color: {INK}; }}
QFrame#upload {{ background: {SURFACE}; border: 1.5px dashed {LINE_2}; border-radius: {RADIUS}px; }}
QFrame#upload[dragging="true"], QFrame#upload:hover {{ border-color: {ACCENT}; }}
QFrame#upload[dragging="true"] {{ background: {ACCENT_WASH}; }}
QLabel#dropIcon {{ background: {ACCENT_WASH}; border-radius: 20px; }}
QFrame#historyRow {{ border: none; border-top: 1px solid {LINE}; background: transparent; }}
QFrame#historyRow:hover {{ background: {SURFACE_2}; }}
QFrame#historyGroup {{ border: none; border-top: 1px solid {LINE}; background: {SURFACE_2}; }}
QFrame#listFooter {{ border: none; border-top: 1px solid {LINE}; background: transparent; }}
QFrame#progressCard {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: {RADIUS}px; }}
QProgressBar {{ background: {SUNK}; border: none; border-radius: 2px; max-height: 4px; min-height: 4px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}

/* Меню */
QFrame#menuPanel {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: {RADIUS}px; }}
QPushButton#menuItem {{ border: none; background: transparent; border-radius: {RADIUS_SM}px; padding: 0; min-height: 0; text-align: left; }}
QPushButton#menuItem:hover {{ background: {SUNK}; }}
QPushButton#menuItem[current="true"] {{ background: {SUNK}; }}
QLabel#menuTitle {{ font-size: {FS_BODY}px; font-weight: 500; color: {INK}; }}
QLabel#menuHint {{ font-size: {FS_CAPTION}px; color: {INK_3}; }}
QLabel#menuHintWarn {{ font-size: {FS_CAPTION}px; color: {WARN}; }}
QLabel#menuSection {{ font-size: {FS_CAPTION}px; font-weight: 600; color: {INK_3}; letter-spacing: 0.4px; }}
QLabel#menuShortcut {{ font-family: "{MONO}"; font-size: {FS_CAPTION}px; color: {INK_3}; }}
QScrollArea#menuList, QWidget#menuListBody {{ background: transparent; border: none; }}

/* Окна */
QDialog#dialog {{ background: {SURFACE}; }}
QFrame#dialogFooter {{ background: transparent; border: none; border-top: 1px solid {LINE}; }}
QScrollArea#dialogBody {{ background: {SURFACE}; border: none; border-top: 1px solid {LINE}; }}
QMessageBox, QProgressDialog {{ background: {SURFACE}; }}
QMessageBox QLabel {{ font-size: {FS_BODY}px; }}

/* Уведомление внизу окна */
QFrame#toast {{ background: {INK}; border-radius: {RADIUS}px; }}
QFrame#toast QLabel {{ color: {PAPER}; font-size: {FS_SMALL}px; }}
QFrame#toast QPushButton {{
  background: transparent; color: {ACCENT_INK}; border: 1px solid rgba(244,243,238,90);
  padding: 0 10px; min-height: {CONTROL_H_SM - 2}px; font-size: {FS_SMALL}px;
}}
QFrame#toast QPushButton:hover {{ background: rgba(244,243,238,36); border-color: {PAPER}; color: {ACCENT_INK}; }}
QFrame#toast QPushButton#iconButton {{ border: none; }}

/* Редактор и результат */
QTextEdit#sourceEditor, QTextBrowser#resultPreview {{ font-family: "{SERIF}"; font-size: {FS_DOC}px; }}
QWidget#panelBody {{ background: {SURFACE}; }}
QWidget#clickable {{ background: transparent; border-radius: {RADIUS_SM}px; }}
QWidget#clickable[hover="true"] {{ background: {SUNK}; }}
QPlainTextEdit, QTextEdit, QTextBrowser {{ background: transparent; border: none; selection-background-color: {mix(ACCENT, SURFACE, 0.28).name()}; selection-color: {INK}; }}
QLabel#redactSample {{
  background: {REDACT_BG}; color: {REDACT_INK}; font-family: "{MONO}"; font-size: {FS_CAPTION - 1}px; font-weight: 500;
  padding: 1px 6px; border-radius: {RADIUS_XS}px;
}}
QLabel#keepSample {{
  background: {PENDING_WASH}; font-size: {FS_CAPTION}px; padding: 0 1px; border-bottom: 1px dotted {PENDING_LINE};
}}
QFrame#popup {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: {RADIUS}px; }}
QPushButton#mergeOpen {{ font-size: {FS_SMALL}px; padding: 0 9px; min-height: {CONTROL_H_SM}px; border: 1px solid transparent; background: {ACCENT_WASH}; color: {ACCENT}; border-radius: {RADIUS_SM}px; }}
QPushButton#mergeOpen:hover {{ background: {ACCENT}; color: {ACCENT_INK}; }}

/* Боковая панель */
QFrame#sideTabs {{ background: transparent; border: none; border-bottom: 1px solid {LINE}; }}
QPushButton#sideTab {{ border: none; border-bottom: 2px solid transparent; border-radius: 0; background: transparent; color: {INK_2}; padding: 0 8px; min-height: 45px; max-height: 45px; }}
QPushButton#sideTab:hover {{ border-bottom-color: {LINE_2}; background: transparent; }}
QPushButton#sideTab[selected="true"] {{ border-bottom-color: {ACCENT}; }}
QLabel#tabTitle {{ font-size: {FS_BODY}px; color: {INK_2}; }}
QLabel#tabTitle[selected="true"] {{ color: {INK}; font-weight: 600; }}

QFrame#candidateCard {{ background: {mix(WARN_WASH, SURFACE, 0.45).name()}; border: 1px solid {PENDING_LINE}; border-radius: {RADIUS}px; }}
QFrame#candidateCard[focused="true"] {{ border: 2px solid {ACCENT}; }}
QLabel#pairTitle {{ color: {INFO_INK}; font-size: {FS_SMALL}px; font-weight: 600; }}
QLabel#pairNote {{ color: {INFO_INK}; font-size: {FS_CAPTION}px; }}
QPushButton#labelChip {{
  background: {SUNK}; border: 1px solid transparent; border-radius: {RADIUS_XS}px; padding: 0; min-height: 0;
}}
QPushButton#labelChip:hover {{ background: {SURFACE}; border-color: {LINE_2}; }}
QLabel#labelChipText {{ color: {INK_2}; font-family: "{MONO}"; font-size: {FS_CAPTION}px; font-weight: 500; }}
QFrame#pairCard {{ background: {ACCENT_WASH}; border: 1px solid {ACCENT_LINE}; border-radius: {RADIUS}px; }}
QLabel#cardCount {{ color: {WARN}; font-size: {FS_CAPTION}px; font-weight: 600; }}
QLabel#cardValue {{ font-size: {FS_TITLE}px; font-weight: 600; }}
QLabel#cardContext {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: {RADIUS_SM}px; padding: 9px 11px; font-family: "{SERIF}"; font-size: {FS_BODY}px; color: {INK_2}; }}
QLabel#cardReason {{ font-size: {FS_SMALL}px; color: {INK_2}; }}
QLabel#cardLink {{ font-size: {FS_SMALL}px; }}
QLabel#fieldLabel {{ color: {INK_3}; font-size: {FS_SMALL}px; }}
QLabel#doneTitle {{ color: {READY}; font-weight: 600; font-size: {FS_BODY}px; }}
QFrame#candidateItem {{ border-radius: {RADIUS_SM}px; background: transparent; }}
QFrame#candidateItem[current="true"] {{ background: {ACCENT_WASH}; }}
QLabel#itemNum {{ background: {WARN_WASH}; color: {WARN}; border-radius: 11px; font-size: 11px; font-weight: 600; }}
QLabel#itemValue {{ font-size: {FS_BODY}px; font-weight: 500; }}
QLabel#itemReason {{ font-size: {FS_CAPTION}px; color: {INK_3}; }}
QLabel#itemReasonCurrent {{ font-size: {FS_CAPTION}px; color: {ACCENT}; }}
QPushButton#disclosure {{ border: none; background: transparent; color: {INK_2}; font-size: {FS_SMALL}px; font-weight: 500; padding: 0 4px; text-align: left; }}
QPushButton#disclosure:hover {{ background: {SUNK}; color: {INK}; }}

QFrame#groupHeader {{ background: {SURFACE}; border-radius: {RADIUS_SM}px; }}
QFrame#groupHeader[highlighted="true"] {{ border: 2px solid {ACCENT}; }}
QFrame#groupSep {{ background: {LINE}; }}
QLabel#groupName {{ font-family: "{MONO}"; font-size: {FS_CAPTION}px; font-weight: 600; }}
QLabel#groupCount {{ color: {INK_3}; font-size: {FS_CAPTION}px; }}
QLabel#typeDot {{ border-radius: 4px; min-width: 8px; max-width: 8px; min-height: 8px; max-height: 8px; }}
QFrame#entityRow {{ border-radius: {RADIUS_SM}px; background: transparent; }}
QFrame#entityRow[selected="true"] {{ background: {ACCENT_WASH}; }}
QFrame#entityRow[highlighted="true"] {{ background: {ACCENT_WASH}; border-left: 3px solid {ACCENT}; }}
QFrame#overviewRow {{ border: none; border-top: 1px solid {LINE}; border-radius: 0; background: transparent; }}
QLabel#cellValue {{ font-size: {FS_BODY}px; font-weight: 500; }}
QLabel#cellValueOff {{ font-size: {FS_BODY}px; font-weight: 500; color: {INK_3}; text-decoration: line-through; }}
QLabel#cellCount {{ color: {INK_3}; font-size: {FS_CAPTION}px; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {LINE_2}; border-radius: 4px; min-height: 30px; margin: 2px; }}
QScrollBar::handle:vertical:hover {{ background: {INK_3}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: {LINE_2}; border-radius: 4px; min-width: 30px; margin: 2px; }}
QSplitter::handle {{ background: transparent; }}
"""
