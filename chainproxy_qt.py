#!/usr/bin/env python3
"""
ChainProxy — PyQt6 GUI.

Apple System-Settings-grade design:
    • Sidebar + canvas with section headers and a live status dot
    • Hero overview: big status pill, one prominent action, visual chain
    • Inline, direct manipulation everywhere (no select-then-action)
    • Auto-save on commit; tiny toast confirms writes
    • Apple-style rows on Settings (label/sub on the left, control on the right)
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from typing import Callable, Optional

from PyQt6.QtCore import (QEasingCurve, QPoint, QPropertyAnimation, QRect,
                          QSize, Qt, QTimer, pyqtSignal)
from PyQt6.QtGui import (QAction, QColor, QFont, QFontMetrics, QIcon,
                          QKeySequence, QPainter, QPainterPath, QPalette,
                          QPen, QShortcut)
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFrame, QGridLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QSizePolicy, QStackedWidget, QStyle, QStyledItemDelegate, QSystemTrayIcon,
    QTableWidget, QTableWidgetItem, QTextEdit, QToolButton, QVBoxLayout,
    QWidget,
)

import chainproxy_core as core


# ─── Color palette (Apple-inspired, semantic) ──────────────────────────────

LIGHT = {
    "window":      "#ffffff",
    "sidebar":     "#f4f4f6",
    "alt":         "#f9f9fb",
    "card":        "#ffffff",
    "raise":       "#fafafc",   # raised tile inside card
    "border":      "#dcdce0",
    "border_soft": "#ececef",
    "text":        "#1d1d1f",
    "text_dim":    "#6e6e73",
    "text_mute":   "#a1a1a6",
    "accent":      "#007aff",
    "accent_hi":   "#3395ff",
    "accent_lo":   "#005ec1",
    "accent_soft": "rgba(0,122,255,0.10)",
    "ok":          "#30b34a",
    "ok_soft":     "rgba(48,179,74,0.12)",
    "err":         "#ff3b30",
    "err_soft":    "rgba(255,59,48,0.12)",
    "warn":        "#ff9500",
    "warn_soft":   "rgba(255,149,0,0.14)",
    "input_bg":    "#ffffff",
    # Light-mode log: white card, dim text — matches the rest of the
    # surface. Older versions used a black "terminal" background here, but
    # that broke the consistency of light mode.
    "log_bg":      "#fafafc",
    "log_fg":      "#3a3a3f",
    "shadow":      "rgba(0,0,0,0.04)",
    "hover":       "rgba(0,0,0,0.04)",
    "press":       "rgba(0,0,0,0.07)",
}

DARK = {
    "window":      "#1c1c1e",
    "sidebar":     "#252527",
    "alt":         "#2c2c2e",
    "card":        "#1c1c1e",
    "raise":       "#242427",
    "border":      "#3a3a3c",
    "border_soft": "#2c2c2e",
    "text":        "#f2f2f7",
    "text_dim":    "#a1a1a6",
    "text_mute":   "#6e6e73",
    "accent":      "#0a84ff",
    "accent_hi":   "#3a9bff",
    "accent_lo":   "#0066cc",
    "accent_soft": "rgba(10,132,255,0.18)",
    "ok":          "#30d158",
    "ok_soft":     "rgba(48,209,88,0.18)",
    "err":         "#ff453a",
    "err_soft":    "rgba(255,69,58,0.18)",
    "warn":        "#ff9f0a",
    "warn_soft":   "rgba(255,159,10,0.20)",
    "input_bg":    "#2c2c2e",
    "log_bg":      "#0a0b0d",
    "log_fg":      "#dde0e5",
    "shadow":      "rgba(0,0,0,0.18)",
    "hover":       "rgba(255,255,255,0.06)",
    "press":       "rgba(255,255,255,0.10)",
}


# Per-platform font stacks. Listing fonts that don't exist on the current OS
# triggers Qt's "Replace uses of missing font family X" warning every paint.
# Qt QSS does NOT recognize CSS generic family names (sans-serif/monospace)
# — it treats them as literal font names — so we list only real installed
# families and let Qt's own default font kick in if all of them are missing.
if sys.platform == "darwin":
    _UI_FONTS = '".AppleSystemUIFont", "Helvetica Neue"'
    _MONO_FONTS = '"Menlo"'
elif sys.platform == "win32":
    _UI_FONTS = '"Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei"'
    _MONO_FONTS = '"Consolas", "Cascadia Mono"'
else:
    _UI_FONTS = '"DejaVu Sans", "Liberation Sans"'
    _MONO_FONTS = '"DejaVu Sans Mono", "Liberation Mono"'


def stylesheet(c: dict) -> str:
    return f"""
* {{ font-family: {_UI_FONTS}; }}

QMainWindow, QDialog {{ background: {c['window']}; }}
QWidget#root, QWidget#page {{ background: {c['window']}; color: {c['text']}; }}

/* ── Typography ── */
QLabel              {{ color: {c['text']}; font-size: 13px; }}
QLabel.pageTitle    {{ font-size: 22px; font-weight: 700; }}
QLabel.pageSub      {{ font-size: 13px; color: {c['text_dim']}; }}
QLabel.section      {{ font-size: 11px; font-weight: 600;
                       letter-spacing: 0.6px; color: {c['text_dim']};
                       text-transform: uppercase; }}
QLabel.cardTitle    {{ font-size: 15px; font-weight: 600; color: {c['text']}; }}
QLabel.rowLabel     {{ font-size: 13px; font-weight: 500; color: {c['text']}; }}
QLabel.rowSub       {{ font-size: 12px; color: {c['text_dim']}; }}
QLabel.dim          {{ font-size: 12px; color: {c['text_dim']}; }}
QLabel.mute         {{ font-size: 12px; color: {c['text_mute']}; }}
QLabel.mono         {{ font-family: {_MONO_FONTS}; font-size: 12px; color: {c['text']}; }}
QLabel.statHero     {{ font-size: 24px; font-weight: 700; }}
QLabel.statValue    {{ font-size: 16px; font-weight: 600; }}
QLabel.statLabel    {{ font-size: 11px; font-weight: 600;
                       letter-spacing: 0.4px; color: {c['text_dim']};
                       text-transform: uppercase; }}
QLabel.toast        {{ background: {c['ok_soft']}; color: {c['ok']};
                       border-radius: 12px; padding: 5px 12px;
                       font-size: 12px; font-weight: 600; }}

/* ── Frames / dividers ── */
QFrame.divider      {{ background: {c['border']};
                       max-height: 1px; min-height: 1px; border: none; }}
QFrame.divider_soft {{ background: {c['border_soft']};
                       max-height: 1px; min-height: 1px; border: none; }}

QFrame.card {{
    background: {c['card']};
    border: 1px solid {c['border']};
    border-radius: 12px;
}}

QFrame.tile {{
    background: {c['raise']};
    border: 1px solid {c['border_soft']};
    border-radius: 10px;
}}

QFrame.chainNode {{
    background: {c['raise']};
    border: 1px solid {c['border']};
    border-radius: 10px;
    padding: 12px 16px;
}}
QFrame.chainNode[active="true"] {{
    background: {c['accent_soft']};
    border-color: {c['accent']};
}}
QLabel.chainNodeName {{ font-size: 13px; font-weight: 600; color: {c['text']}; }}
QLabel.chainNodeMeta {{ font-size: 11px; color: {c['text_dim']}; }}

/* ── Status pill ── */
QLabel.pillRunning {{
    background: {c['ok_soft']}; color: {c['ok']};
    border-radius: 10px; padding: 3px 10px;
    font-size: 11px; font-weight: 700; letter-spacing: 0.4px;
}}
QLabel.pillStopped {{
    background: rgba(127,127,127,0.14); color: {c['text_dim']};
    border-radius: 10px; padding: 3px 10px;
    font-size: 11px; font-weight: 700; letter-spacing: 0.4px;
}}

/* ── Sidebar ── */
QFrame#sidebarChrome {{ background: {c['sidebar']}; border: none;
                         border-right: 1px solid {c['border_soft']}; }}
QLabel#brandTitle    {{ font-size: 16px; font-weight: 700; color: {c['text']}; }}
QLabel#brandStatus   {{ font-size: 11px; color: {c['text_dim']}; }}
QListWidget#sidebar {{
    background: {c['sidebar']};
    border: none; outline: 0;
    padding: 0 8px;
    font-size: 13.5px; color: {c['text']};
}}
QListWidget#sidebar::item {{
    padding: 8px 12px;
    border-radius: 7px;
    margin: 1px 0;
}}
QListWidget#sidebar::item:selected {{
    background: {c['accent']}; color: white;
}}
QListWidget#sidebar::item:hover:!selected:!disabled {{
    background: {c['hover']};
}}
QListWidget#sidebar::item:disabled {{
    color: {c['text_mute']};
    font-size: 10px; font-weight: 700;
    letter-spacing: 0.7px;
    padding: 12px 12px 4px;
    margin-top: 6px;
    background: transparent;
}}

/* ── Buttons ── */
QPushButton {{
    background: transparent; color: {c['text']};
    border: 1px solid {c['border']}; border-radius: 7px;
    padding: 6px 14px; font-size: 13px;
}}
QPushButton:hover    {{ background: {c['hover']}; }}
QPushButton:pressed  {{ background: {c['press']}; }}
QPushButton:disabled {{ color: {c['text_mute']}; border-color: {c['border_soft']}; }}

QPushButton[kind="primary"] {{
    background: {c['accent']}; color: white;
    border: 1px solid {c['accent']}; font-weight: 600;
}}
QPushButton[kind="primary"]:hover    {{ background: {c['accent_hi']}; border-color: {c['accent_hi']}; }}
QPushButton[kind="primary"]:pressed  {{ background: {c['accent_lo']}; border-color: {c['accent_lo']}; }}
QPushButton[kind="primary"]:disabled {{ background: {c['border']}; border-color: {c['border']}; color: {c['text_mute']}; }}

QPushButton[kind="success"] {{
    background: {c['ok']}; color: white;
    border: 1px solid {c['ok']}; font-weight: 600;
    padding: 9px 22px; font-size: 14px; min-width: 120px;
}}
QPushButton[kind="success"]:hover {{ background: #2a9d40; border-color: #2a9d40; }}

QPushButton[kind="danger"] {{
    background: {c['err']}; color: white;
    border: 1px solid {c['err']}; font-weight: 600;
    padding: 9px 22px; font-size: 14px; min-width: 120px;
}}
QPushButton[kind="danger"]:hover {{ background: #d8312a; border-color: #d8312a; }}

QPushButton[kind="ghost"] {{
    background: transparent; color: {c['text_dim']};
    border: 1px solid {c['border_soft']};
    padding: 5px 11px; font-size: 12px;
}}
QPushButton[kind="ghost"]:hover {{ background: {c['hover']}; color: {c['text']}; }}

QPushButton[kind="ghostDanger"] {{
    background: transparent; color: {c['err']};
    border: 1px solid {c['border']};
}}
QPushButton[kind="ghostDanger"]:hover {{
    background: {c['err_soft']}; border-color: {c['err']};
}}

QPushButton[kind="link"] {{
    background: transparent; border: none;
    color: {c['accent']}; padding: 2px 0;
    text-align: left;
}}
QPushButton[kind="link"]:hover {{ color: {c['accent_hi']}; }}

QPushButton[kind="chip"] {{
    background: {c['raise']}; color: {c['text']};
    border: 1px solid {c['border_soft']};
    border-radius: 14px; padding: 5px 14px;
    font-size: 12px;
}}
QPushButton[kind="chip"]:hover {{ background: {c['hover']}; border-color: {c['border']}; }}

/* ── Inputs ── */
QLineEdit, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {c['input_bg']}; color: {c['text']};
    border: 1px solid {c['border']}; border-radius: 7px;
    padding: 6px 9px;
    selection-background-color: {c['accent']}; selection-color: white;
}}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border-color: {c['accent']};
}}
QPlainTextEdit, QTextEdit {{ font-family: {_MONO_FONTS}; font-size: 12.5px; }}
QComboBox::drop-down {{
    subcontrol-origin: padding; subcontrol-position: top right;
    width: 22px; border: none;
}}
QComboBox QAbstractItemView {{
    background: {c['card']}; color: {c['text']};
    border: 1px solid {c['border']}; border-radius: 7px;
    selection-background-color: {c['accent']}; selection-color: white;
    outline: 0; padding: 4px;
}}

/* ── Checkbox ── */
QCheckBox {{ color: {c['text']}; spacing: 8px; padding: 2px 0; }}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {c['border']};
    background: {c['input_bg']}; border-radius: 4px;
}}
QCheckBox::indicator:checked {{
    background: {c['accent']}; border-color: {c['accent']};
}}

/* ── Switch (a checkbox styled as iOS toggle) ── */
QCheckBox.switch {{ spacing: 0; }}
QCheckBox.switch::indicator {{
    width: 38px; height: 22px;
    border-radius: 11px; border: none;
    background: rgba(127,127,127,0.30);
}}
QCheckBox.switch::indicator:checked {{
    background: {c['ok']};
}}

/* ── Table ── */
QTableWidget {{
    background: {c['card']};
    alternate-background-color: {c['alt']};
    color: {c['text']};
    gridline-color: {c['border_soft']};
    border: 1px solid {c['border']};
    border-radius: 10px;
    selection-background-color: {c['accent_soft']};
    selection-color: {c['text']};
}}
QTableWidget::item {{ padding: 6px; border: none; }}
QHeaderView::section {{
    background: {c['sidebar']}; color: {c['text_dim']};
    border: none; border-bottom: 1px solid {c['border']};
    padding: 8px 10px; font-weight: 600; font-size: 11.5px;
    text-transform: uppercase; letter-spacing: 0.4px;
}}
QHeaderView {{ background: {c['sidebar']}; }}

/* ── Scroll ── */
QScrollBar:vertical, QScrollBar:horizontal {{ background: transparent; margin: 0; border: none; }}
QScrollBar:vertical   {{ width: 10px; }}
QScrollBar:horizontal {{ height: 10px; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {c['border']}; border-radius: 5px; min-height: 30px; min-width: 30px;
}}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{
    background: {c['text_dim']};
}}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; border: none; background: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QScrollArea {{ background: {c['window']}; border: none; }}
QScrollArea > QWidget > QWidget {{ background: {c['window']}; }}

/* ── Log box ── */
QPlainTextEdit#log {{
    background: {c['log_bg']}; color: {c['log_fg']};
    border: 1px solid {c['border']}; border-radius: 10px;
    font-family: {_MONO_FONTS}; font-size: 12px;
    padding: 10px 12px;
}}
QTextEdit#result {{
    background: {c['raise']}; color: {c['text']};
    border: 1px solid {c['border_soft']}; border-radius: 10px;
    padding: 12px 14px;
}}

/* ── Brand status dot in sidebar ── */
QLabel#brandDot[state="on"]  {{ color: {c['ok']}; font-size: 12px; }}
QLabel#brandDot[state="off"] {{ color: {c['text_mute']}; font-size: 12px; }}
"""


def detect_dark_mode(app) -> bool:
    try:
        return app.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        pass
    # Fallback for Windows 10 (where Qt's colorScheme() may not detect AppsUseLightTheme):
    # read the registry directly. Cheap and robust.
    if sys.platform == "win32":
        try:
            import winreg
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as k:
                v, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")
                return int(v) == 0
        except OSError:
            pass
    bg = app.palette().color(QPalette.ColorRole.Window)
    return bg.lightness() < 128


# ─── Cosmetic key labels by platform ─────────────────────────────────────
# Display modifier shows in tooltips / footer hints. Qt's QKeySequence
# already maps Ctrl→Cmd internally on macOS, so the bindings work as-is on
# both platforms — only the label changes.
MOD_LABEL = "⌘" if sys.platform == "darwin" else "Ctrl"


# ─── Tiny widget helpers ───────────────────────────────────────────────────

def L(text: str, klass: str = "") -> QLabel:
    lbl = QLabel(text)
    if klass:
        lbl.setProperty("class", klass)
    return lbl


def B(text: str, kind: str = "", on_click=None,
      tooltip: str = "") -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    if on_click:
        b.clicked.connect(on_click)
    if tooltip:
        b.setToolTip(tooltip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def divider(soft: bool = False) -> QFrame:
    d = QFrame()
    d.setProperty("class", "divider_soft" if soft else "divider")
    d.setFrameShape(QFrame.Shape.NoFrame)
    return d


def hbox(*items, spacing: int = 8, margins=(0, 0, 0, 0)) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for it in items:
        if it == "stretch":
            lay.addStretch()
        elif isinstance(it, QWidget):
            lay.addWidget(it)
        elif isinstance(it, int):
            lay.addSpacing(it)
        else:
            lay.addLayout(it)
    return lay


def vbox(*items, spacing: int = 8, margins=(0, 0, 0, 0)) -> QVBoxLayout:
    lay = QVBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for it in items:
        if it == "stretch":
            lay.addStretch()
        elif isinstance(it, QWidget):
            lay.addWidget(it)
        elif isinstance(it, int):
            lay.addSpacing(it)
        else:
            lay.addLayout(it)
    return lay


def card(*children, padding=(22, 18, 22, 18), spacing: int = 12,
         klass: str = "card") -> QFrame:
    f = QFrame()
    f.setProperty("class", klass)
    lay = QVBoxLayout(f)
    lay.setContentsMargins(*padding)
    lay.setSpacing(spacing)
    for ch in children:
        if ch == "stretch":
            lay.addStretch()
        elif isinstance(ch, QWidget):
            lay.addWidget(ch)
        elif isinstance(ch, int):
            lay.addSpacing(ch)
        else:
            lay.addLayout(ch)
    return f


def settings_row(label: str, control: QWidget, sub: str = "",
                 trailing: Optional[QWidget] = None) -> QHBoxLayout:
    """Apple System-Settings-style row: label/sub on the left, control(s) right."""
    left = vbox(L(label, "rowLabel"), spacing=2)
    if sub:
        left.addWidget(L(sub, "rowSub"))
    right_items = [control] if trailing is None else [control, trailing]
    return hbox(left, "stretch", *right_items, spacing=12)


# ─── Custom widgets ────────────────────────────────────────────────────────

class NameDialog(QDialog):
    """Mac-styled single-field input dialog."""
    def __init__(self, parent, title: str, label_text: str, default: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(380)
        if parent:
            self.setStyleSheet(parent.styleSheet())

        self.edit = QLineEdit(default)
        self.edit.selectAll()
        self.edit.setMinimumHeight(28)

        ok = B("确定", "primary", self.accept)
        ok.setDefault(True)
        cancel = B("取消", on_click=self.reject)

        lay = vbox(
            L(label_text, "rowLabel"),
            self.edit,
            hbox("stretch", cancel, ok),
            spacing=12, margins=(20, 18, 20, 16),
        )
        self.setLayout(lay)

    @staticmethod
    def get(parent, title, label_text, default=""):
        d = NameDialog(parent, title, label_text, default)
        if d.exec() == QDialog.DialogCode.Accepted:
            return d.edit.text().strip(), True
        return "", False


class ChainNode(QFrame):
    """Single rounded box in the chain visualization."""
    def __init__(self, name: str, meta: str = "", active: bool = True):
        super().__init__()
        self.setProperty("class", "chainNode")
        self.setProperty("active", "true" if active else "false")
        self.title_lbl = L(name, "chainNodeName")
        self.title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.meta_lbl = L(meta, "chainNodeMeta")
        self.meta_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay = vbox(self.title_lbl, self.meta_lbl,
                    spacing=2, margins=(0, 0, 0, 0))
        self.setLayout(lay)
        self.setMinimumWidth(120)
        self.setSizePolicy(QSizePolicy.Policy.Preferred,
                            QSizePolicy.Policy.Fixed)

    def setActive(self, active: bool):
        self.setProperty("active", "true" if active else "false")
        self.style().unpolish(self); self.style().polish(self)

    def setLabels(self, name: str, meta: str):
        self.title_lbl.setText(name)
        self.meta_lbl.setText(meta)


class Arrow(QWidget):
    """A simple horizontal arrow drawn with QPainter."""
    def __init__(self, color: str = "#a1a1a6"):
        super().__init__()
        self._color = QColor(color)
        self.setFixedHeight(28)
        self.setMinimumWidth(28)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)

    def setColor(self, color: str):
        self._color = QColor(color)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(self._color, 1.4)
        p.setPen(pen)
        h = self.height() // 2
        w = self.width()
        # main line
        p.drawLine(8, h, w - 10, h)
        # arrowhead
        p.drawLine(w - 14, h - 4, w - 8, h)
        p.drawLine(w - 14, h + 4, w - 8, h)


class ChainView(QWidget):
    """Visual representation of 本机 → 第一跳 → 第二跳 → 目标."""
    def __init__(self):
        super().__init__()
        self.local = ChainNode("本机", core.PLATFORM_LABEL)
        self.first = ChainNode("—", "第一跳")
        self.second = ChainNode("—", "第二跳")
        self.target = ChainNode("目标", "互联网")

        self.a1 = Arrow()
        self.a2 = Arrow()
        self.a3 = Arrow()

        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 4, 2, 4)
        lay.setSpacing(6)
        for w in (self.local, self.a1, self.first,
                  self.a2, self.second, self.a3, self.target):
            lay.addWidget(w, 1 if isinstance(w, Arrow) else 0)

    def setChain(self, first_name: str, first_meta: str,
                 second_name: str, second_meta: str,
                 color: str, active: bool):
        self.first.setLabels(first_name or "—", first_meta)
        self.second.setLabels(second_name or "—", second_meta)
        self.first.setActive(active and bool(first_name))
        self.second.setActive(active and bool(second_name))
        self.local.setActive(active)
        self.target.setActive(active)
        for a in (self.a1, self.a2, self.a3):
            a.setColor(color)


class ToastLabel(QLabel):
    """Tiny floating toast that fades in/out at the top-right."""
    def __init__(self, parent):
        super().__init__(parent)
        self.setProperty("class", "toast")
        self.hide()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_text(self, text: str, msec: int = 1500):
        self.setText(text)
        self.adjustSize()
        if self.parent() is not None:
            pw = self.parent().width()
            self.move(pw - self.width() - 28, 18)
        self.show()
        self.raise_()
        self._timer.start(msec)


# ─── Pages ─────────────────────────────────────────────────────────────────

class OverviewPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setObjectName("page")

        # ── Hero card: status + chain + primary action ──
        self.status_pill = L("未运行", "pillStopped")
        self.status_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_text = L("代理未启动", "statHero")
        self.status_meta = L("点击右侧按钮开始接管系统流量", "dim")
        head = vbox(
            hbox(self.status_pill, "stretch"),
            self.status_text,
            self.status_meta,
            spacing=6,
        )

        self.chain = ChainView()

        self.action_btn = B("启动", "success", self._toggle)
        recover_tip = ("清系统代理 / 杀残留 mihomo / 删 TUN 路由"
                       if sys.platform == "darwin"
                       else "清系统代理 / 杀残留 mihomo.exe")
        self.recover_btn = B("网络急救", "ghost", app.panic_recover,
                              tooltip=recover_tip)
        actions = vbox(self.action_btn, self.recover_btn,
                       spacing=8)
        actions.setAlignment(Qt.AlignmentFlag.AlignTop |
                              Qt.AlignmentFlag.AlignRight)

        hero_top = hbox(head, "stretch", actions)

        hero = card(
            hero_top,
            divider(soft=True),
            self.chain,
            spacing=14,
            padding=(24, 22, 24, 22),
        )

        # ── Stats grid ──
        self.tile_port = self._tile("本地端口", "—")
        self.tile_mode = self._tile("运行模式", "—")
        self.tile_first = self._tile("第一跳", "—")
        self.tile_second = self._tile("第二跳", "—")

        stats_grid = QGridLayout()
        stats_grid.setHorizontalSpacing(12)
        stats_grid.setVerticalSpacing(12)
        for i, t in enumerate([self.tile_port, self.tile_mode,
                                self.tile_first, self.tile_second]):
            stats_grid.addWidget(t, 0, i)
            stats_grid.setColumnStretch(i, 1)

        # ── Quick test ──
        self.quick_url = QLineEdit("https://www.google.com")
        self.quick_url.setPlaceholderText("输入域名或 URL，按回车测试")
        self.quick_url.returnPressed.connect(self._quick_test)
        self.quick_url.setMinimumHeight(32)
        self.quick_btn = B("测试", "primary", self._quick_test)
        self.quick_result = L("尚未测试", "dim")
        self.quick_result.setWordWrap(True)
        quick_card = card(
            hbox(L("快速测试", "cardTitle"), "stretch",
                 L(f"{MOD_LABEL}T 聚焦", "mute")),
            hbox(self.quick_url, self.quick_btn, spacing=8),
            self.quick_result,
            spacing=12,
        )

        # ── Recent log ──
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("log")
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(180)
        clear_log = B("清空", "ghost",
                      lambda: self.log_view.clear())
        log_card = card(
            hbox(L("最近日志", "cardTitle"), "stretch", clear_log),
            self.log_view,
            spacing=10,
        )

        self.setLayout(vbox(
            hero,
            stats_grid,
            quick_card,
            log_card,
            "stretch",
            spacing=14, margins=(28, 22, 28, 22),
        ))

    def _tile(self, label: str, value: str) -> QFrame:
        lbl = L(label, "statLabel")
        val = L(value, "statValue")
        val.setObjectName("tileValue")
        f = QFrame()
        f.setProperty("class", "tile")
        lay = vbox(lbl, val, spacing=4, margins=(14, 12, 14, 12))
        f.setLayout(lay)
        f._value = val
        return f

    def _toggle(self):
        if self.app.runner.is_running():
            self.app.stop()
        else:
            self.app.start()

    def update_state(self):
        c = self.app.cfg
        col = self.app.colors
        running = self.app.runner.is_running()
        state_changed = getattr(self, "_last_running", None) != running
        self._last_running = running

        if running:
            self.status_pill.setText("运行中")
            self.status_text.setText("链式代理已接管")
            mode = "TUN 模式" if c.get("tun_mode") else "系统代理模式"
            self.status_meta.setText(
                f"{mode}  ·  本地端口 {c['local_port']}")
            self.action_btn.setText("停止")
            chain_color = col["accent"]
        else:
            self.status_pill.setText("未运行")
            self.status_text.setText("代理未启动")
            self.status_meta.setText("点击右侧按钮开始接管系统流量")
            self.action_btn.setText("启动")
            chain_color = col["text_mute"]

        # Only re-polish when running state actually flipped (or theme changed).
        if state_changed:
            self.status_pill.setProperty(
                "class", "pillRunning" if running else "pillStopped")
            self.action_btn.setProperty(
                "kind", "danger" if running else "success")
            self.status_pill.style().unpolish(self.status_pill)
            self.status_pill.style().polish(self.status_pill)
            self.action_btn.style().unpolish(self.action_btn)
            self.action_btn.style().polish(self.action_btn)

        # Chain view
        fh_name = c.get("active_first_hop") or ""
        sh_name = c.get("active_second_hop") or ""
        fh = next((p for p in c["first_hops"] if p["name"] == fh_name), None)
        sh = next((p for p in c["second_hops"] if p["name"] == sh_name), None)
        fh_meta = f"{fh['type']} · {fh['server']}:{fh['port']}" if fh else "未配置"
        sh_meta = f"{sh['type']} · {sh['server']}:{sh['port']}" if sh else "未配置"
        self.chain.setChain(fh_name, fh_meta, sh_name, sh_meta,
                             chain_color, running)

        # Stats tiles
        rules = "启用" if c.get("rules_enabled") else "禁用"
        mode_str = "TUN" if c.get("tun_mode") else "系统代理"
        self.tile_port._value.setText(str(c.get("local_port", "—")))
        self.tile_mode._value.setText(f"{mode_str}  ·  规则{rules}")
        self.tile_first._value.setText(fh_name or "—")
        self.tile_second._value.setText(sh_name or "—")

    def _quick_test(self):
        url = self.quick_url.text().strip()
        if not url:
            return
        if not self.app.runner.is_running():
            self.quick_result.setText("请先启动链式代理")
            self.quick_result.setStyleSheet(
                f"color: {self.app.colors['warn']};")
            return
        port = self.app.cfg["local_port"]
        self.quick_result.setText("测试中…")
        self.quick_result.setStyleSheet(
            f"color: {self.app.colors['text_dim']};")

        cfg = self.app.cfg
        ctl_port = int(cfg.get("controller_port", 9999))
        ctl_secret = cfg.get("controller_secret", "")

        def worker():
            res = core.test_url_through_proxy(
                url, port, core.MIHOMO_LOG,
                controller_port=ctl_port, controller_secret=ctl_secret)
            def render():
                col = self.app.colors
                if res.get("ok"):
                    msg = (f"HTTP {res['http_code']}  ·  "
                           f"{res['time_total_ms']} ms  ·  "
                           f"{res.get('rule', '?')} → {res.get('proxy', '?')}")
                    self.quick_result.setText(msg)
                    self.quick_result.setStyleSheet(f"color: {col['ok']};")
                else:
                    self.quick_result.setText(
                        f"失败：{res.get('error', '未知错误')}")
                    self.quick_result.setStyleSheet(f"color: {col['err']};")
            self.app.qt_invoke(render)

        threading.Thread(target=worker, daemon=True).start()

    def append_log(self, line: str):
        self.log_view.appendPlainText(line)
        if self.log_view.blockCount() > 250:
            cur = self.log_view.textCursor()
            cur.movePosition(cur.MoveOperation.Start)
            cur.select(cur.SelectionType.LineUnderCursor)
            cur.removeSelectedText()
            cur.deleteChar()


class NodeEditor(QFrame):
    """One stacked card with selector + form. Auto-saves on commit."""
    def __init__(self, app, kind: str, title: str, sub: str):
        super().__init__()
        self.setProperty("class", "card")
        self.app = app
        self.kind = kind
        self._loading = False

        # Header: title + sub on left, segmented selector + actions on right
        head_left = vbox(L(title, "cardTitle"), L(sub, "rowSub"), spacing=2)

        self.combo = QComboBox()
        self.combo.setMinimumWidth(180)
        self.combo.setMinimumHeight(28)
        self.combo.currentTextChanged.connect(self._on_select)

        self.add_btn = B("新增", "ghost", self._add)
        self.dup_btn = B("复制", "ghost", self._dup)
        self.rename_btn = B("重命名", "ghost", self._rename)
        self.del_btn = B("删除", "ghostDanger", self._del)

        head = hbox(head_left, "stretch", self.combo,
                    self.add_btn, self.dup_btn, self.rename_btn, self.del_btn,
                    spacing=8)

        # Form fields
        self.f_proto = QComboBox(); self.f_proto.addItems(core.PROTOCOLS)
        self.f_udp = QCheckBox("UDP")
        self.f_tls = QCheckBox("TLS")
        self.f_skip = QCheckBox("跳过证书验证")
        self.f_server = QLineEdit()
        self.f_port = QLineEdit()
        self.f_user = QLineEdit()
        self.f_pass = QLineEdit()
        self.f_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self.f_cipher = QLineEdit()
        self.f_sni = QLineEdit()

        for w in (self.f_server, self.f_port, self.f_user, self.f_pass,
                  self.f_cipher, self.f_sni, self.f_proto):
            w.setMinimumHeight(28)

        self.f_server.setPlaceholderText("hostname 或 IP")
        self.f_port.setPlaceholderText("1080")
        self.f_user.setPlaceholderText("可空")
        self.f_pass.setPlaceholderText("可空")
        self.f_cipher.setPlaceholderText("ss/vmess 时填")
        self.f_sni.setPlaceholderText("trojan/hysteria2 时填")

        # Auto-save on commit
        for w in (self.f_server, self.f_port, self.f_user, self.f_pass,
                  self.f_cipher, self.f_sni):
            w.editingFinished.connect(self._save)
        self.f_proto.currentTextChanged.connect(lambda _: self._save())
        for chk in (self.f_udp, self.f_tls, self.f_skip):
            chk.toggled.connect(lambda _: self._save())

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(11)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        # row 0: protocol + flags
        grid.addWidget(L("协议", "rowLabel"), 0, 0)
        grid.addWidget(self.f_proto, 0, 1)
        grid.addLayout(hbox(self.f_udp, self.f_tls, self.f_skip,
                             "stretch", spacing=14), 0, 2, 1, 2)
        # row 1: server + port
        grid.addWidget(L("服务器", "rowLabel"), 1, 0)
        grid.addWidget(self.f_server, 1, 1)
        grid.addWidget(L("端口", "rowLabel"), 1, 2)
        grid.addWidget(self.f_port, 1, 3)
        # row 2: user + pass
        grid.addWidget(L("用户名", "rowLabel"), 2, 0)
        grid.addWidget(self.f_user, 2, 1)
        grid.addWidget(L("密码", "rowLabel"), 2, 2)
        grid.addWidget(self.f_pass, 2, 3)
        # row 3: cipher + sni
        grid.addWidget(L("加密", "rowLabel"), 3, 0)
        grid.addWidget(self.f_cipher, 3, 1)
        grid.addWidget(L("SNI", "rowLabel"), 3, 2)
        grid.addWidget(self.f_sni, 3, 3)

        # Empty state hint
        self.empty_hint = L("还没有节点。点击「新增」创建第一个。", "dim")
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint.setMinimumHeight(80)
        self.empty_hint.hide()

        body = vbox(head, divider(soft=True), grid, self.empty_hint,
                    spacing=14, margins=(20, 18, 20, 18))
        self.setLayout(body)

    def refresh(self):
        names = [p["name"] for p in self.app.cfg[f"{self.kind}_hops"]]
        active = self.app.cfg.get(f"active_{self.kind}_hop", "")
        if active not in names and names:
            active = names[0]
            self.app.cfg[f"active_{self.kind}_hop"] = active
            core.save_config(self.app.cfg)

        self._loading = True
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItems(names)
        if active:
            self.combo.setCurrentText(active)
        self.combo.blockSignals(False)
        self._loading = False

        has_any = bool(names)
        self.empty_hint.setVisible(not has_any)
        for w in (self.f_proto, self.f_udp, self.f_tls, self.f_skip,
                  self.f_server, self.f_port, self.f_user, self.f_pass,
                  self.f_cipher, self.f_sni, self.dup_btn,
                  self.rename_btn, self.del_btn, self.combo):
            w.setEnabled(has_any)

        if has_any:
            self._load(active)
        else:
            self._clear_form()

    def _on_select(self, name):
        if not name or self._loading:
            return
        self.app.cfg[f"active_{self.kind}_hop"] = name
        core.save_config(self.app.cfg)
        self._load(name)
        self.app.on_config_change()

    def _load(self, name):
        self._loading = True
        try:
            node = next((p for p in self.app.cfg[f"{self.kind}_hops"]
                         if p["name"] == name), None)
            if not node:
                self._clear_form()
                return
            self.f_proto.setCurrentText(node.get("type", "socks5"))
            self.f_udp.setChecked(bool(node.get("udp", False)))
            self.f_tls.setChecked(bool(node.get("tls", False)))
            self.f_skip.setChecked(bool(node.get("skip_cert_verify", False)))
            self.f_server.setText(str(node.get("server", "")))
            self.f_port.setText(str(node.get("port", "")))
            self.f_user.setText(node.get("username", ""))
            self.f_pass.setText(node.get("password", ""))
            self.f_cipher.setText(node.get("cipher", ""))
            self.f_sni.setText(node.get("sni", ""))
        finally:
            self._loading = False

    def _clear_form(self):
        self._loading = True
        try:
            for w in (self.f_server, self.f_port, self.f_user, self.f_pass,
                      self.f_cipher, self.f_sni):
                w.clear()
            self.f_proto.setCurrentText("socks5")
            self.f_udp.setChecked(True)
            self.f_tls.setChecked(False)
            self.f_skip.setChecked(False)
        finally:
            self._loading = False

    def _save(self):
        if self._loading:
            return
        name = self.combo.currentText().strip()
        if not name:
            return
        node = next((p for p in self.app.cfg[f"{self.kind}_hops"]
                     if p["name"] == name), None)
        if not node:
            return
        try:
            port = int(self.f_port.text()) if self.f_port.text().strip() else 0
        except ValueError:
            self.app.toast("端口必须是整数", error=True)
            return
        node.update({
            "type": self.f_proto.currentText(),
            "udp": self.f_udp.isChecked(),
            "tls": self.f_tls.isChecked(),
            "skip_cert_verify": self.f_skip.isChecked(),
            "server": self.f_server.text().strip(),
            "port": port,
            "username": self.f_user.text(),
            "password": self.f_pass.text(),
            "cipher": self.f_cipher.text(),
            "sni": self.f_sni.text(),
        })
        core.save_config(self.app.cfg)
        self.app.toast("已自动保存")
        self.app.on_config_change()
        self.app.maybe_restart_for_config_change(silent=True)

    def _add(self):
        name, ok = NameDialog.get(self, "新增节点", "节点名称")
        if not ok or not name:
            return
        if any(p["name"] == name for p in self.app.cfg[f"{self.kind}_hops"]):
            QMessageBox.warning(self, "重名", "已存在同名节点")
            return
        self.app.cfg[f"{self.kind}_hops"].append({
            "name": name, "type": "socks5", "server": "127.0.0.1", "port": 1080,
            "username": "", "password": "", "udp": True,
            "tls": False, "skip_cert_verify": False, "cipher": "", "sni": "",
        })
        self.app.cfg[f"active_{self.kind}_hop"] = name
        core.save_config(self.app.cfg)
        self.refresh()
        self.app.on_config_change()
        self.app.toast(f"已新增节点：{name}")

    def _dup(self):
        cur = self.combo.currentText()
        if not cur:
            return
        name, ok = NameDialog.get(self, "复制节点", "新名称", cur + " 副本")
        if not ok or not name:
            return
        if any(p["name"] == name for p in self.app.cfg[f"{self.kind}_hops"]):
            QMessageBox.warning(self, "重名", "已存在同名节点")
            return
        src = next(p for p in self.app.cfg[f"{self.kind}_hops"]
                    if p["name"] == cur)
        new = dict(src); new["name"] = name
        self.app.cfg[f"{self.kind}_hops"].append(new)
        self.app.cfg[f"active_{self.kind}_hop"] = name
        core.save_config(self.app.cfg)
        self.refresh()
        self.app.on_config_change()
        self.app.toast(f"已复制为：{name}")

    def _rename(self):
        cur = self.combo.currentText()
        if not cur:
            return
        name, ok = NameDialog.get(self, "重命名", "新名称", cur)
        if not ok or not name or name == cur:
            return
        if any(p["name"] == name for p in self.app.cfg[f"{self.kind}_hops"]):
            QMessageBox.warning(self, "重名", "已存在同名节点")
            return
        for p in self.app.cfg[f"{self.kind}_hops"]:
            if p["name"] == cur:
                p["name"] = name
        if self.app.cfg.get(f"active_{self.kind}_hop") == cur:
            self.app.cfg[f"active_{self.kind}_hop"] = name
        core.save_config(self.app.cfg)
        self.refresh()
        self.app.on_config_change()
        self.app.toast(f"已重命名为：{name}")

    def _del(self):
        cur = self.combo.currentText()
        if not cur:
            return
        if QMessageBox.question(
                self, "删除节点", f"确认删除节点「{cur}」？此操作无法撤销。"
        ) != QMessageBox.StandardButton.Yes:
            return
        self.app.cfg[f"{self.kind}_hops"] = [
            p for p in self.app.cfg[f"{self.kind}_hops"] if p["name"] != cur
        ]
        if self.app.cfg.get(f"active_{self.kind}_hop") == cur:
            self.app.cfg[f"active_{self.kind}_hop"] = ""
        core.save_config(self.app.cfg)
        self.refresh()
        self.app.on_config_change()
        self.app.toast(f"已删除：{cur}")


class NodesPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setObjectName("page")
        self.first = NodeEditor(
            app, "first", "第一跳",
            "通常是机场客户端在本机暴露的 SOCKS5 入口（127.0.0.1）")
        self.second = NodeEditor(
            app, "second", "第二跳",
            "链路终点节点。所有走 Chain 的流量在第一跳之后再次跳到这里")
        self.setLayout(vbox(
            self._header(),
            self.first,
            self.second,
            "stretch",
            spacing=14, margins=(28, 22, 28, 22),
        ))

    def _header(self) -> QVBoxLayout:
        return vbox(
            L("节点", "pageTitle"),
            L("配置链路上的两个跳点。修改字段后会自动保存。", "pageSub"),
            spacing=4,
        )

    def refresh(self):
        self.first.refresh()
        self.second.refresh()


# Inline rule-set table delegate kept for theming consistency; not strictly needed.
class RulesPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setObjectName("page")

        # ── Header strip: enable + final fallback (auto-save) ──
        self.enable_chk = QCheckBox()
        self.enable_chk.setProperty("class", "switch")
        self.enable_chk.setChecked(bool(app.cfg.get("rules_enabled")))
        self.enable_chk.toggled.connect(self._on_enable_toggle)

        self.final_combo = QComboBox()
        self.final_combo.addItems([t for t in core.RULE_TARGETS if t != "REJECT"])
        self.final_combo.setCurrentText(app.cfg.get("final_target", "FirstHopOnly"))
        self.final_combo.setMinimumWidth(160)
        self.final_combo.setMinimumHeight(28)
        self.final_combo.currentTextChanged.connect(self._on_final_change)

        master = card(
            settings_row("启用分流规则",
                         self.enable_chk,
                         "关闭后所有流量直接走兜底"),
            divider(soft=True),
            settings_row("未匹配兜底",
                         self.final_combo,
                         "没匹配上任何规则的流量去哪里"),
            spacing=8,
        )

        # ── Custom rules ──
        self.pre_edit = QPlainTextEdit()
        self.pre_edit.setPlainText("\n".join(app.cfg.get("custom_rules_pre", [])))
        self.pre_edit.setMinimumHeight(170)
        self.pre_edit.setPlaceholderText(
            "DOMAIN-SUFFIX,example.com,Chain\n"
            "DOMAIN-KEYWORD,openai,Chain\n"
            "IP-CIDR,1.2.3.0/24,DIRECT,no-resolve")

        self.post_edit = QPlainTextEdit()
        self.post_edit.setPlainText("\n".join(app.cfg.get("custom_rules_post", [])))
        self.post_edit.setMinimumHeight(80)
        self.post_edit.setPlaceholderText("一般留空。仅在内置规则之后还想兜底时填。")

        self.pre_edit.focusOutEvent = self._wrap_focus_out(
            self.pre_edit, self._save_custom_rules)
        self.post_edit.focusOutEvent = self._wrap_focus_out(
            self.post_edit, self._save_custom_rules)

        custom_card = card(
            hbox(L("自定义规则", "cardTitle"), "stretch",
                 L("失焦自动保存", "mute")),
            L("一行一条，优先级最高。语法：DOMAIN-SUFFIX,host,目标   ·   "
              "目标可填 Chain / FirstHopOnly / DIRECT / REJECT", "rowSub"),
            self.pre_edit,
            divider(soft=True),
            L("后置规则（极少用）", "rowLabel"),
            L("内置规则之后再兜底，通常留空。", "rowSub"),
            self.post_edit,
            spacing=10,
        )

        # ── Built-in rule sets table (inline editing) ──
        self.last_update_lbl = L("", "mute")
        update_btn = B("更新规则", "primary", self._update_rules)

        self.tbl = QTableWidget(0, 5)
        self.tbl.setHorizontalHeaderLabels(
            ["启用", "名称", "命中后", "类型", "说明"])
        h = self.tbl.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        h.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.tbl.setColumnWidth(0, 56)
        self.tbl.setColumnWidth(1, 130)
        self.tbl.setColumnWidth(2, 140)
        self.tbl.setColumnWidth(3, 80)
        self.tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setAlternatingRowColors(True)
        self.tbl.setShowGrid(False)
        self.tbl.setMinimumHeight(280)
        self.tbl.verticalHeader().setDefaultSectionSize(36)

        builtin_card = card(
            hbox(L("内置规则集", "cardTitle"), "stretch",
                 self.last_update_lbl, update_btn, spacing=10),
            L("每行可直接点开关启用/禁用，下拉切换命中后流向。", "rowSub"),
            self.tbl,
            spacing=10,
        )

        self.setLayout(vbox(
            self._header(),
            master,
            custom_card,
            builtin_card,
            "stretch",
            spacing=14, margins=(28, 22, 28, 22),
        ))
        self._refresh_table()

    def _header(self) -> QVBoxLayout:
        return vbox(
            L("分流规则", "pageTitle"),
            L("决定不同流量走哪一跳。自定义规则优先级最高。", "pageSub"),
            spacing=4,
        )

    def _wrap_focus_out(self, widget, callback):
        original = widget.focusOutEvent
        def wrapper(ev):
            original(ev)
            callback()
        return wrapper

    def _on_enable_toggle(self, on):
        self.app.cfg["rules_enabled"] = bool(on)
        core.save_config(self.app.cfg)
        self.app.on_config_change()
        self.app.toast("已自动保存")
        self.app.maybe_restart_for_config_change(silent=True)

    def _on_final_change(self, text):
        self.app.cfg["final_target"] = text
        core.save_config(self.app.cfg)
        self.app.toast("已自动保存")
        self.app.maybe_restart_for_config_change(silent=True)

    def _save_custom_rules(self):
        pre = [ln.rstrip() for ln in self.pre_edit.toPlainText().splitlines()]
        post = [ln.rstrip() for ln in self.post_edit.toPlainText().splitlines()]
        if (pre == self.app.cfg.get("custom_rules_pre", []) and
                post == self.app.cfg.get("custom_rules_post", [])):
            return
        self.app.cfg["custom_rules_pre"] = pre
        self.app.cfg["custom_rules_post"] = post
        core.save_config(self.app.cfg)
        self.app.toast("已自动保存")
        self.app.maybe_restart_for_config_change(silent=True)

    def _update_rules(self):
        def worker():
            ok, total = core.update_all_rule_sets(self.app.cfg, self.app.log)
            self.app.qt_invoke(self._refresh_table)
            self.app.qt_invoke(
                lambda: self.app.toast(f"已更新 {ok}/{total} 个规则集"))
            if self.app.runner.is_running() and self.enable_chk.isChecked():
                self.app.qt_invoke(
                    lambda: self.app.maybe_restart_for_config_change(silent=True))
        threading.Thread(target=worker, daemon=True).start()

    def _refresh_table(self):
        self.last_update_lbl.setText(
            f"上次更新：{self.app.cfg.get('rule_sets_last_update') or '从未'}")
        sets = self.app.cfg.get("rule_sets", [])
        self.tbl.setRowCount(len(sets))
        for i, rs in enumerate(sets):
            self._populate_row(i, rs)

    def _populate_row(self, i, rs):
        # Column 0: a real toggle (checkbox in a centered cell widget)
        chk = QCheckBox()
        chk.setProperty("class", "switch")
        chk.setChecked(bool(rs.get("enabled")))
        chk.toggled.connect(lambda on, idx=i: self._on_row_enable(idx, on))
        cell = QWidget()
        clay = QHBoxLayout(cell)
        clay.setContentsMargins(0, 0, 0, 0)
        clay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        clay.addWidget(chk)
        self.tbl.setCellWidget(i, 0, cell)

        # Column 1: name (read-only)
        name_item = QTableWidgetItem(rs["name"])
        name_item.setFlags(Qt.ItemFlag.ItemIsEnabled |
                           Qt.ItemFlag.ItemIsSelectable)
        mono_font = QFont()
        mono_font.setFamilies(["Menlo", "Consolas", "Cascadia Mono"])
        mono_font.setPointSize(11)
        name_item.setFont(mono_font)
        self.tbl.setItem(i, 1, name_item)

        # Column 2: target combo (inline editable)
        combo = QComboBox()
        combo.addItems(core.RULE_TARGETS)
        combo.setCurrentText(rs.get("target", "Chain"))
        combo.currentTextChanged.connect(
            lambda txt, idx=i: self._on_row_target(idx, txt))
        wrap = QWidget()
        wlay = QHBoxLayout(wrap)
        wlay.setContentsMargins(4, 2, 4, 2)
        wlay.addWidget(combo)
        self.tbl.setCellWidget(i, 2, wrap)

        # Column 3: behavior
        beh_item = QTableWidgetItem(rs.get("behavior", "?"))
        beh_item.setFlags(Qt.ItemFlag.ItemIsEnabled |
                          Qt.ItemFlag.ItemIsSelectable)
        beh_item.setForeground(QColor(self.app.colors["text_dim"]))
        self.tbl.setItem(i, 3, beh_item)

        # Column 4: description (+ status hint)
        local = "" if core.rule_set_local_path_exists(rs["name"]) \
                  else "  ·  未下载"
        desc_item = QTableWidgetItem(rs.get("desc", "") + local)
        desc_item.setFlags(Qt.ItemFlag.ItemIsEnabled |
                           Qt.ItemFlag.ItemIsSelectable)
        desc_item.setForeground(QColor(self.app.colors["text_dim"]))
        self.tbl.setItem(i, 4, desc_item)

    def _on_row_enable(self, idx, on):
        self.app.cfg["rule_sets"][idx]["enabled"] = bool(on)
        core.save_config(self.app.cfg)
        self.app.toast("已自动保存")
        self.app.maybe_restart_for_config_change(silent=True)

    def _on_row_target(self, idx, target):
        if self.app.cfg["rule_sets"][idx].get("target") == target:
            return
        self.app.cfg["rule_sets"][idx]["target"] = target
        core.save_config(self.app.cfg)
        self.app.toast("已自动保存")
        self.app.maybe_restart_for_config_change(silent=True)


class TestPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setObjectName("page")
        self._history = []

        self.url_edit = QLineEdit("https://www.google.com")
        self.url_edit.setPlaceholderText("输入域名或 URL")
        self.url_edit.returnPressed.connect(self._run)
        self.url_edit.setMinimumHeight(32)
        run_btn = B("测试", "primary", self._run)

        chips = hbox(
            L("快速：", "rowSub"),
            B("百度", "chip", lambda: self._set_run("https://www.baidu.com")),
            B("Google", "chip", lambda: self._set_run("https://www.google.com")),
            B("YouTube", "chip", lambda: self._set_run("https://www.youtube.com")),
            B("Telegram", "chip", lambda: self._set_run("https://web.telegram.org")),
            B("ChatGPT", "chip", lambda: self._set_run("https://chat.openai.com")),
            B("ipinfo.io", "chip", lambda: self._set_run("https://ipinfo.io")),
            "stretch",
            spacing=6,
        )

        input_card = card(
            L("规则测试", "cardTitle"),
            L("发起一个 HTTP 请求，看它走了哪一条规则、最终用了哪个出口。",
              "rowSub"),
            hbox(self.url_edit, run_btn, spacing=8),
            chips,
            spacing=10,
        )

        self.result = QTextEdit()
        self.result.setObjectName("result")
        self.result.setReadOnly(True)
        self.result.setMinimumHeight(220)
        result_card = card(L("最近一次结果", "cardTitle"), self.result,
                           spacing=10)

        self.hist_tbl = QTableWidget(0, 6)
        self.hist_tbl.setHorizontalHeaderLabels(
            ["时间", "主机", "HTTP", "延迟ms", "命中规则", "代理链"])
        h = self.hist_tbl.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        h.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        for i, w in enumerate([80, 220, 60, 70]):
            self.hist_tbl.setColumnWidth(i, w)
        self.hist_tbl.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self.hist_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.hist_tbl.itemSelectionChanged.connect(self._on_hist_select)
        self.hist_tbl.verticalHeader().setVisible(False)
        self.hist_tbl.setMinimumHeight(180)
        self.hist_tbl.setAlternatingRowColors(True)
        self.hist_tbl.setShowGrid(False)
        self.hist_tbl.verticalHeader().setDefaultSectionSize(30)
        history_card = card(L("历史", "cardTitle"), self.hist_tbl, spacing=10)

        self.setLayout(vbox(
            self._header(),
            input_card,
            result_card,
            history_card,
            "stretch",
            spacing=14, margins=(28, 22, 28, 22),
        ))

    def _header(self) -> QVBoxLayout:
        return vbox(
            L("规则测试", "pageTitle"),
            L("验证你的规则是否生效，并能看到完整的代理链。", "pageSub"),
            spacing=4,
        )

    def _set_run(self, url):
        self.url_edit.setText(url)
        self._run()

    def _run(self):
        url = self.url_edit.text().strip()
        if not url:
            return
        if not self.app.runner.is_running():
            QMessageBox.information(self, "未运行", "请先在「概览」启动链式代理")
            return
        port = self.app.cfg["local_port"]
        c = self.app.colors
        self.result.setHtml(
            f"<span style='color:{c['text_dim']}'>测试中…</span>")

        cfg = self.app.cfg
        ctl_port = int(cfg.get("controller_port", 9999))
        ctl_secret = cfg.get("controller_secret", "")

        def worker():
            res = core.test_url_through_proxy(
                url, port, core.MIHOMO_LOG,
                controller_port=ctl_port, controller_secret=ctl_secret)
            self.app.qt_invoke(lambda: self._render(res))
            self.app.qt_invoke(lambda: self._add_history(res))
        threading.Thread(target=worker, daemon=True).start()

    def _render(self, res):
        c = self.app.colors
        rows = []
        rows.append(
            f"<div style='color:{c['text_dim']};font-size:11px;"
            f"text-transform:uppercase;letter-spacing:0.5px'>URL</div>"
            f"<div style='font-family:Menlo,Consolas,monospace;font-size:12.5px;margin-bottom:10px'>"
            f"{res.get('url','?')}</div>")
        if res.get("ok"):
            ttls = res.get('time_tls_ms', 0)
            tcon = res.get('time_connect_ms', 0)
            tdns = res.get('time_dns_ms', 0)
            rows.append(
                f"<div style='display:inline-block;background:{c['ok_soft']};"
                f"color:{c['ok']};padding:3px 10px;border-radius:8px;"
                f"font-weight:600;font-size:11.5px'>HTTP {res['http_code']} · "
                f"{res.get('time_total_ms','?')} ms</div>"
                f"<div style='color:{c['text_dim']};margin-top:6px;font-size:11.5px'>"
                f"DNS {tdns}ms · 连接 {max(0,tcon-tdns)}ms · "
                f"TLS {max(0,ttls-tcon)}ms</div>")
        else:
            rows.append(
                f"<div style='display:inline-block;background:{c['err_soft']};"
                f"color:{c['err']};padding:3px 10px;border-radius:8px;"
                f"font-weight:600;font-size:11.5px'>失败</div>"
                f"<div style='color:{c['err']};margin-top:6px'>"
                f"{res.get('error','未知错误')}</div>")
        rows.append("<div style='height:14px'></div>")
        rows.append(
            f"<table cellpadding='4' style='font-size:12.5px'>"
            f"<tr><td style='color:{c['text_dim']};padding-right:14px'>命中规则</td>"
            f"<td style='color:{c['accent']};font-weight:600'>"
            f"{res.get('rule','?')}</td></tr>"
            f"<tr><td style='color:{c['text_dim']};padding-right:14px'>代理组</td>"
            f"<td style='color:{c['accent']};font-weight:600'>"
            f"{res.get('chain','?')}</td></tr>"
            f"<tr><td style='color:{c['text_dim']};padding-right:14px'>实际节点</td>"
            f"<td style='color:{c['accent']};font-weight:600'>"
            f"{res.get('proxy','?')}</td></tr>"
            f"</table>")
        if res.get("log_lines"):
            rows.append(
                f"<div style='color:{c['text_dim']};margin-top:14px;"
                f"font-size:11px;text-transform:uppercase;letter-spacing:0.5px'>"
                f"mihomo 原始日志</div>")
            for ln in res["log_lines"][-3:]:
                rows.append(
                    f"<pre style='color:{c['text_dim']};margin:2px 0;"
                    f"font-family:Menlo,Consolas,monospace;font-size:11.5px'>{ln}</pre>")
        self.result.setHtml("".join(rows))

    def _add_history(self, res):
        self._history.append(res)
        ts = time.strftime("%H:%M:%S")
        code = res.get("http_code", "ERR" if not res.get("ok") else "?")
        lat = res.get("time_total_ms", "")
        rule = res.get("rule", "?")
        chain = res.get("chain", "?")
        if (res.get("chain") and res.get("proxy") and
                res["chain"] != res["proxy"]):
            chain = f"{res['chain']} → {res['proxy']}"
        self.hist_tbl.insertRow(0)
        for col, val in enumerate(
                [ts, res.get("host", "?"), code, str(lat), rule, chain]):
            item = QTableWidgetItem(str(val))
            if col == 2:
                col_color = self.app.colors["ok"] if res.get("ok") \
                            else self.app.colors["err"]
                item.setForeground(QColor(col_color))
                font = item.font(); font.setBold(True); item.setFont(font)
            self.hist_tbl.setItem(0, col, item)

    def _on_hist_select(self):
        rows = self.hist_tbl.selectionModel().selectedRows()
        if not rows:
            return
        idx = rows[0].row()
        rev_idx = len(self._history) - 1 - idx
        if 0 <= rev_idx < len(self._history):
            self._render(self._history[rev_idx])


class SettingsPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setObjectName("page")

        # Local port — auto-save on commit
        self.port_edit = QLineEdit(str(app.cfg["local_port"]))
        self.port_edit.setMaximumWidth(120)
        self.port_edit.setMinimumHeight(28)
        self.port_edit.editingFinished.connect(self._save_port)

        # Auto system proxy
        self.sysproxy_chk = QCheckBox()
        self.sysproxy_chk.setProperty("class", "switch")
        self.sysproxy_chk.setChecked(
            bool(app.cfg.get("set_system_proxy_on_start")))
        self.sysproxy_chk.toggled.connect(self._on_sysproxy_toggle)

        sp_actions = hbox(
            B("立即设为系统代理", "ghost",
              lambda: app.toggle_system_proxy(True)),
            B("清除系统代理", "ghost",
              lambda: app.toggle_system_proxy(False)),
            "stretch",
            spacing=8,
        )

        # TUN
        self.tun_chk = QCheckBox()
        self.tun_chk.setProperty("class", "switch")
        self.tun_chk.setChecked(bool(app.cfg.get("tun_mode", False)))
        self.tun_chk.toggled.connect(self._on_tun_toggle)

        # Theme
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["跟随系统", "浅色", "深色"])
        self.theme_combo.setCurrentText({
            "auto": "跟随系统", "light": "浅色", "dark": "深色"
        }.get(app.cfg.get("theme", "auto"), "跟随系统"))
        self.theme_combo.setMinimumWidth(140)
        self.theme_combo.currentTextChanged.connect(self._on_theme)

        # Cards
        network_card = card(
            L("网络", "cardTitle"),
            divider(soft=True),
            settings_row(
                "本地代理端口", self.port_edit,
                "浏览器/应用配置代理时填这个端口（HTTP / SOCKS5）"),
            divider(soft=True),
            settings_row(
                "启动时设为系统代理", self.sysproxy_chk,
                "启动链式代理时自动接管系统代理设置"),
            sp_actions,
            spacing=10,
        )

        tun_sub = ("接管系统所有 IPv4 流量。第一次启用会要求管理员密码。"
                   if sys.platform == "darwin"
                   else "接管系统所有 IPv4 流量。每次启动都需通过 UAC 授权。")
        tun_extra = ("• 开启后无需配置 SOCKS5 / 系统代理\n"
                     "• 你的机场客户端自身的 TUN 必须关闭\n"
                     "• 异常断网时回到「概览」点「网络急救」"
                     if sys.platform == "darwin"
                     else "• 开启后无需配置 SOCKS5 / 系统代理\n"
                          "• 你的机场客户端自身的 TUN 必须关闭\n"
                          "• 每次启动 mihomo 时会弹 UAC 提权（Windows 限制）\n"
                          "• 异常断网时回到「概览」点「网络急救」")
        tun_card = card(
            L("高级", "cardTitle"),
            divider(soft=True),
            settings_row("TUN 模式", self.tun_chk, tun_sub),
            L(tun_extra, "rowSub"),
            spacing=10,
        )

        appearance_card = card(
            L("外观", "cardTitle"),
            divider(soft=True),
            settings_row("主题", self.theme_combo, "选择浅色 / 深色 / 跟随系统"),
            spacing=10,
        )

        reveal_label = ("在 Finder 中显示" if sys.platform == "darwin"
                        else "在文件管理器中显示")
        cfg_actions = hbox(
            B("在编辑器中打开", "ghost", self._open_config),
            B(reveal_label, "ghost", self._reveal_config),
            "stretch",
            spacing=8,
        )
        cfg_card = card(
            L("配置文件", "cardTitle"),
            divider(soft=True),
            L("config.json 是软件唯一的配置源。GUI 写入它；你也可以直接编辑。",
              "rowSub"),
            L(str(core.CONFIG_PATH), "mono"),
            cfg_actions,
            spacing=10,
        )

        self.setLayout(vbox(
            self._header(),
            network_card,
            tun_card,
            appearance_card,
            cfg_card,
            "stretch",
            spacing=14, margins=(28, 22, 28, 22),
        ))

    def _header(self) -> QVBoxLayout:
        return vbox(
            L("设置", "pageTitle"),
            L("所有改动会立刻生效并自动保存。", "pageSub"),
            spacing=4,
        )

    def _save_port(self):
        try:
            new = int(self.port_edit.text())
        except ValueError:
            self.app.toast("端口必须是整数", error=True)
            return
        if new == self.app.cfg.get("local_port"):
            return
        self.app.cfg["local_port"] = new
        core.save_config(self.app.cfg)
        self.app.toast("已自动保存")
        self.app.on_config_change()
        self.app.maybe_restart_for_config_change(silent=True)

    def _on_sysproxy_toggle(self, on):
        self.app.cfg["set_system_proxy_on_start"] = bool(on)
        core.save_config(self.app.cfg)
        self.app.toast("已自动保存")

    def _on_tun_toggle(self, on):
        self.app.cfg["tun_mode"] = bool(on)
        core.save_config(self.app.cfg)
        self.app.toast("已自动保存")
        if on:
            if sys.platform == "darwin":
                msg = ("已启用 TUN 模式。\n\n"
                       "• 第一次启动时需输入管理员密码（之后免密）\n"
                       "• 异常断网时点「网络急救」恢复\n"
                       "• 你的机场客户端自身的 TUN 必须关闭")
            else:
                msg = ("已启用 TUN 模式。\n\n"
                       "• 每次启动 mihomo 都会弹 UAC 授权（Windows 限制）\n"
                       "• 异常断网时点「网络急救」恢复\n"
                       "• 你的机场客户端自身的 TUN 必须关闭\n"
                       "• 务必在「first_hop_process_names」里填上机场客户端进程名")
            QMessageBox.information(self, "TUN 模式", msg)
        self.app.on_config_change()
        if self.app.runner.is_running():
            self.app.maybe_restart_for_config_change(silent=True)

    def _on_theme(self, text):
        self.app.cfg["theme"] = {"跟随系统": "auto",
                                  "浅色": "light",
                                  "深色": "dark"}[text]
        core.save_config(self.app.cfg)
        self.app.refresh_theme()

    def _open_config(self):
        path = str(core.CONFIG_PATH)
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-t", path])
        elif sys.platform == "win32":
            # os.startfile launches the default editor (Notepad / VSCode etc.)
            os.startfile(path)
        else:
            subprocess.Popen(["xdg-open", path])

    def _reveal_config(self):
        path = str(core.CONFIG_PATH)
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        elif sys.platform == "win32":
            # /select, highlights the file in Explorer instead of opening it.
            subprocess.Popen(["explorer", f"/select,{path}"])
        else:
            subprocess.Popen(["xdg-open", str(core.CONFIG_PATH.parent)])


# ─── Main window ───────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    log_signal = pyqtSignal(str)
    invoke_signal = pyqtSignal(object)

    def __init__(self, app: QApplication):
        super().__init__()
        self.q_app = app
        self.cfg = core.load_config()
        self.runner = core.MihomoRunner(self.log)
        # When mihomo dies on its own (panic / OOM / segfault), the runner
        # fires this from its tail thread. We bounce to the Qt main thread
        # so state mutations + restart scheduling stay serialized.
        self.runner.on_unexpected_exit = lambda: self.qt_invoke(
            self._on_mihomo_unexpected_exit)
        self.mihomo_bin = core.find_mihomo()
        self._we_set_proxy = False
        self.colors = LIGHT

        self.setWindowTitle("ChainProxy")
        self.resize(1100, 780)
        self.setMinimumSize(940, 640)

        self.invoke_signal.connect(lambda fn: fn())
        self.log_signal.connect(self._on_log_signal)

        self._mihomo_started_at = 0.0
        self._watchdog_failures = 0
        self._watchdog_last_restart = 0.0
        self._watchdog_bounced = False
        self._last_watchdog_tick = 0.0
        # Cleanup gate: _final_cleanup is reachable from closeEvent,
        # aboutToQuit, atexit, and signal handlers — guard against running
        # the (potentially slow) sudo-helper invocations more than once.
        self._final_cleanup_done = False
        # Distinguishes user-initiated stop from mihomo dying on its own.
        # Used by MihomoRunner's death callback to decide auto-restart.
        self._intentional_stop = False

        self._build()
        self.refresh_theme()
        self.runner.attach_existing(self.log)
        # Startup cleanup: if a previous GUI session was killed (task manager,
        # crash, OS restart) while system proxy was enabled, the registry now
        # points at a dead port and the user has no internet. We detect this
        # by checking for the proxy_backup file we leave during enable. If
        # present AND no orphan mihomo was adopted, restore the snapshot now.
        if sys.platform == "win32" and not self.runner.is_running():
            self._cleanup_orphan_proxy_state()
        if self.runner.is_running():
            # Adopted orphan: it's been alive for unknown time; skip warm-up
            # so the watchdog can act immediately if it's already wedged.
            self._mihomo_started_at = time.time() - 120

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)
        self._tick()

        # Debounced restart timer: rapid edits coalesce into one restart.
        self._restart_timer = QTimer(self)
        self._restart_timer.setSingleShot(True)
        self._restart_timer.timeout.connect(self._do_debounced_restart)

        # Outbound health watchdog: catches the "process alive but every dial
        # times out" failure mode (sleep/wake, route conflict with another
        # local TUN proxy, zombie utun). 30s cadence × 2 strikes ≈ 1 minute
        # before auto-restart, which beats the user reaching for 网络急救
        # (which only stops, doesn't restart) and then a reboot.
        self._watchdog_timer = QTimer(self)
        self._watchdog_timer.timeout.connect(self._watchdog_tick)
        self._watchdog_timer.start(30000)

        try:
            app.styleHints().colorSchemeChanged.connect(
                lambda _: self.refresh_theme())
        except Exception:
            pass

        self._install_shortcuts()
        self._build_tray()

    def _build(self):
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Sidebar ──
        sidebar_chrome = QFrame()
        sidebar_chrome.setObjectName("sidebarChrome")
        sidebar_chrome.setFixedWidth(208)
        sb_lay = QVBoxLayout(sidebar_chrome)
        sb_lay.setContentsMargins(0, 18, 0, 16)
        sb_lay.setSpacing(0)

        # Brand block
        self.brand_dot = QLabel("●")
        self.brand_dot.setObjectName("brandDot")
        self.brand_dot.setProperty("state", "off")
        title = QLabel("ChainProxy")
        title.setObjectName("brandTitle")
        self.brand_status = QLabel("未运行")
        self.brand_status.setObjectName("brandStatus")
        brand_top = hbox(self.brand_dot, title, "stretch", spacing=6)
        brand = vbox(brand_top, self.brand_status,
                      spacing=2, margins=(16, 0, 16, 12))
        sb_lay.addLayout(brand)

        # Nav: single list with disabled section headers.
        # list_idx → stack_idx mapping (None for non-selectable headers).
        self._nav_items = [
            ("导航",    None),
            ("概览",    0),
            ("节点",    1),
            ("分流规则", 2),
            ("规则测试", 3),
            ("应用",    None),
            ("设置",    4),
        ]
        self.sidebar = QListWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFrameShape(QFrame.Shape.NoFrame)
        for text, stack_idx in self._nav_items:
            it = QListWidgetItem(text, self.sidebar)
            if stack_idx is None:
                it.setFlags(Qt.ItemFlag.NoItemFlags)
        self.sidebar.currentRowChanged.connect(self._on_nav)
        sb_lay.addWidget(self.sidebar, 1)

        sb_lay.addStretch(1)

        # Footer hint
        version = QLabel(f"v1.1  ·  {MOD_LABEL}1–5 切换页面")
        version.setStyleSheet(
            f"color: {self.colors['text_mute']}; font-size: 11px;"
            f"padding: 0 16px;")
        version.setWordWrap(True)
        self.version_lbl = version
        sb_lay.addWidget(version)

        layout.addWidget(sidebar_chrome)

        # ── Main canvas ──
        self.stack = QStackedWidget()
        self.overview = OverviewPage(self)
        self.nodes_page = NodesPage(self)
        self.rules_page = RulesPage(self)
        self.test_page = TestPage(self)
        self.settings_page = SettingsPage(self)
        for p in (self.overview, self.nodes_page, self.rules_page,
                  self.test_page, self.settings_page):
            self.stack.addWidget(self._scrollable(p))
        layout.addWidget(self.stack, 1)

        # Toast lives in this window
        self._toast = ToastLabel(self)

        self.sidebar.setCurrentRow(self._stack_to_list(0))
        self.nodes_page.refresh()

    def _scrollable(self, w: QWidget) -> QScrollArea:
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        sc.setWidget(w)
        return sc

    def _stack_to_list(self, stack_idx: int) -> int:
        for i, (_, sidx) in enumerate(self._nav_items):
            if sidx == stack_idx:
                return i
        return 1

    def _on_nav(self, list_idx: int):
        if list_idx < 0 or list_idx >= len(self._nav_items):
            return
        stack_idx = self._nav_items[list_idx][1]
        if stack_idx is None:
            # User somehow landed on a header — bounce to nearest selectable.
            for i in range(list_idx + 1, len(self._nav_items)):
                if self._nav_items[i][1] is not None:
                    self.sidebar.setCurrentRow(i)
                    return
            return
        self.stack.setCurrentIndex(stack_idx)
        if stack_idx == 1:
            self.nodes_page.refresh()

    def _install_shortcuts(self):
        defs = [
            ("Ctrl+1", lambda: self._goto(0)),
            ("Ctrl+2", lambda: self._goto(1)),
            ("Ctrl+3", lambda: self._goto(2)),
            ("Ctrl+4", lambda: self._goto(3)),
            ("Ctrl+5", lambda: self._goto(4)),
            ("Ctrl+R", self._toggle_run),
            ("Ctrl+T", self._focus_quick_test),
        ]
        for seq, fn in defs:
            sc = QShortcut(QKeySequence(seq), self)
            sc.activated.connect(fn)

    def _goto(self, stack_idx: int):
        self.sidebar.setCurrentRow(self._stack_to_list(stack_idx))

    def _toggle_run(self):
        if self.runner.is_running():
            self.stop()
        else:
            self.start()

    def _focus_quick_test(self):
        self._goto(0)
        self.overview.quick_url.setFocus()
        self.overview.quick_url.selectAll()

    def refresh_theme(self):
        pref = self.cfg.get("theme", "auto")
        if pref == "dark":
            dark = True
        elif pref == "light":
            dark = False
        else:
            dark = detect_dark_mode(self.q_app)
        self.colors = DARK if dark else LIGHT
        self.q_app.setStyleSheet(stylesheet(self.colors))
        if hasattr(self, "version_lbl"):
            self.version_lbl.setStyleSheet(
                f"color: {self.colors['text_mute']}; font-size: 11px;"
                f"padding: 0 16px;")
        if hasattr(self, "overview"):
            self.overview.update_state()
        if hasattr(self, "rules_page"):
            self.rules_page._refresh_table()

    def _tick(self):
        self.overview.update_state()
        running = self.runner.is_running()
        if getattr(self, "_brand_running", None) != running:
            self._brand_running = running
            self.brand_dot.setProperty("state", "on" if running else "off")
            self.brand_dot.style().unpolish(self.brand_dot)
            self.brand_dot.style().polish(self.brand_dot)
        self.brand_status.setText(
            f"运行中  ·  端口 {self.cfg['local_port']}" if running
            else "未运行")

    def log(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        self.log_signal.emit(f"[{ts}] {msg}")

    def _on_log_signal(self, line):
        self.overview.append_log(line)

    def qt_invoke(self, fn):
        self.invoke_signal.emit(fn)

    def toast(self, text: str, error: bool = False):
        if error:
            c = self.colors
            self._toast.setStyleSheet(
                f"background:{c['err_soft']};color:{c['err']};"
                f"border-radius:12px;padding:5px 12px;"
                f"font-size:12px;font-weight:600;")
        else:
            self._toast.setStyleSheet("")  # falls back to .toast class
            self._toast.setProperty("class", "toast")
        self._toast.show_text(text)

    def on_config_change(self):
        self.overview.update_state()

    def maybe_restart_for_config_change(self, silent: bool = False):
        """Debounced: coalesces a burst of edits into a single restart."""
        if not self.runner.is_running():
            return
        # Wait until the user has been quiet for 1.5s, then restart once.
        self._restart_timer.start(1500)

    def _do_debounced_restart(self):
        if not self.runner.is_running():
            return
        self.log("配置已更改，重启 mihomo…")
        # Don't cancel ourselves; we ARE the pending restart.
        self.stop(cancel_pending_restart=False)
        QTimer.singleShot(500, lambda: self.start(silent=True))

    def _watchdog_tick(self):
        """Schedule a connectivity probe on a worker thread. The actual
        probes (HTTP/SOCKS5 to local mixed-port) can each block up to 5s;
        running them on the Qt main thread froze the GUI for up to 10s
        every 30s during outages. We dispatch off-thread and post results
        back via qt_invoke."""
        now = time.time()
        last_tick = getattr(self, "_last_watchdog_tick", 0.0)
        self._last_watchdog_tick = now

        # macOS: detect a foreign mihomo TUN that came up AFTER us. Preflight
        # only runs once at start; if the user later opens FastLink/Mihomo
        # Party/ClashX and turns on its TUN, both clients fight over
        # 198.18.0.1 and routes flap. We can't fix it for them, but we can
        # tell them clearly instead of leaving them debugging. Throttle the
        # warning so we don't spam every 30s.
        if (sys.platform == "darwin"
                and self.runner.is_running()
                and self.cfg.get("tun_mode")):
            self._check_for_foreign_tun()
        # 30s tick cadence; jitter is sub-second. Tier the response by gap:
        #   <35s : normal tick, fall through to probe
        #   35-90s : brief sleep (clamshell flicker, lid-close <60s). The
        #     OS-level interface usually survives but mihomo's connection
        #     table holds dead-on-the-server-side TCP connections — apps
        #     reusing them sit on retransmits for 10-30s before reconnecting.
        #     Symptom: "WeChat sends slow for a minute after wake."
        #     Fix: DELETE /connections on mihomo controller, forces apps
        #     to drop stale FDs and dial fresh. No process restart, no
        #     Wi-Fi bounce, no perceptible interruption.
        #   >90s : long sleep, mihomo's auto-detect-interface may be stuck
        #     on pre-sleep socket state. Schedule a full restart at +8s
        #     so Wi-Fi can re-associate before mihomo binds again.
        if last_tick > 0 and self.runner.is_running():
            gap = now - last_tick
            if gap > 90:
                self.log(f"⌁ 检测到系统长时间休眠（间隔 {gap:.0f}s），"
                         f"8s 后重启 mihomo 刷新出站状态")
                QTimer.singleShot(8000, self._post_wake_recover)
                return
            if gap > 35:
                self.log(f"⌁ 检测到系统短暂休眠（间隔 {gap:.0f}s），"
                         f"清理 mihomo 陈旧连接（apps 自动重连）")
                threading.Thread(target=self._evict_connections_worker,
                                 daemon=True).start()
                # Fall through to probe — eviction is fire-and-forget; if
                # something is also broken at the dial level we still want
                # to detect it.

        if not self.runner.is_running():
            self._watchdog_failures = 0
            self._watchdog_bounced = False
            return
        started = self._mihomo_started_at
        if not started or (now - started) < 60:
            # Warm-up: rule sets, GeoIP DB, fakeip cache loading from disk.
            return
        # Reentrancy guard: if a previous probe is still in flight (slow
        # mihomo, network hung), don't pile up workers.
        if getattr(self, "_watchdog_probe_inflight", False):
            return
        self._watchdog_probe_inflight = True
        threading.Thread(target=self._watchdog_probe_worker,
                         args=(now,), daemon=True).start()

    def _watchdog_probe_worker(self, sample_time: float):
        """Off-thread: run probes, post outcome back to Qt main thread for
        decision-making (state mutations + restart scheduling must run on
        the main thread to keep them serialized with start/stop clicks)."""
        try:
            chain_ok = self._probe_chain()
            direct_ok = self._probe_direct()
        except Exception:
            chain_ok = direct_ok = False
        self.qt_invoke(lambda: self._on_watchdog_probe_result(
            sample_time, chain_ok, direct_ok))

    def _on_watchdog_probe_result(self, sample_time: float,
                                   chain_ok: bool, direct_ok: bool):
        """Main-thread continuation of _watchdog_tick. Sees the probe
        outcomes and decides: ignore / log / restart / bounce-iface."""
        self._watchdog_probe_inflight = False
        # Defensive: mihomo may have died while probes were in flight.
        if not self.runner.is_running():
            self._watchdog_failures = 0
            self._watchdog_bounced = False
            return

        if chain_ok and direct_ok:
            if self._watchdog_failures > 0:
                self.log("✓ 网络出站已恢复")
            self._watchdog_failures = 0
            self._watchdog_bounced = False
            return

        # Single-path failure is usually upstream-specific: Chain fails when
        # the first-hop client is closed; DIRECT fails when ISP RSTs. mihomo
        # restart can't fix either — only log, don't act.
        if chain_ok or direct_ok:
            failing = "DIRECT" if not direct_ok else "Chain"
            self.log(f"⚠ {failing} 路径异常（单路径，不重启）")
            self._watchdog_failures = 0
            return

        # Both paths down → system-level state breakage.
        self._watchdog_failures += 1
        self.log(f"⚠ 双路径出站不通 ({self._watchdog_failures}/2)")
        if self._watchdog_failures < 2:
            return

        now = time.time()
        # If we already restarted recently and BOTH probes are STILL failing,
        # the kernel-level interface state is wedged — restart can't recover
        # it. Bounce the primary interface (sudo helper). Only try this once
        # per failure cycle; if it ALSO doesn't help, surface to the user.
        if now - self._watchdog_last_restart < 180:
            if not getattr(self, "_watchdog_bounced", False):
                self._watchdog_bounced = True
                self.log("⚠ 重启 mihomo 后仍不通，触发底层网卡重置…")
                self.toast("尝试重置网卡…")
                threading.Thread(target=self._deep_recover_worker,
                                 daemon=True).start()
                return
            self.toast("网络持续异常，请检查 WiFi/上游代理", error=True)
            return

        self._watchdog_last_restart = now
        self._watchdog_failures = 0
        self._watchdog_bounced = False
        self.log("⚠ mihomo 出站持续不通，自动重启…")
        self.toast("网络异常，自动重启 mihomo")
        self._intentional_stop = True
        self.stop(cancel_pending_restart=False)
        QTimer.singleShot(500, lambda: self.start(silent=True))

    def _evict_connections_worker(self):
        """Off-thread: ask mihomo to close all open connections via its
        REST controller. Used after sleep/wake to force apps reusing
        pre-sleep TCP connections (whose remote end has long since timed
        out) to dial fresh. The DELETE returns immediately; mihomo closes
        each conn server-side, the apps see EOF/RST on their next read,
        retry, and reconnect via mihomo's outbound — all within a second.
        Without this, WeChat / Slack / browsers sit on dead sockets for
        10-30s before TCP-keepalive or app-level retry kicks them."""
        import urllib.request, urllib.error
        try:
            port = int(self.cfg.get("controller_port", 9999))
            secret = self.cfg.get("controller_secret", "")
        except (TypeError, ValueError):
            return
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/connections", method="DELETE")
            if secret:
                req.add_header("Authorization", f"Bearer {secret}")
            with urllib.request.urlopen(req, timeout=3) as r:
                r.read()
            self.qt_invoke(lambda: self.log("✓ 已清理 mihomo 陈旧连接"))
        except (urllib.error.URLError, OSError, ValueError) as e:
            self.qt_invoke(lambda: self.log(f"⚠ 清理陈旧连接失败: {e}"))

    def _check_for_foreign_tun(self):
        """If the kernel routes 198.18.0.1 to a utun other than ours, another
        mihomo-based client (FastLink, Mihomo Party, ClashX, …) opened its
        TUN after we did. We detect this by counting utun interfaces that
        currently have 198.18.0.1 configured. If >1, conflict.

        We don't know our own utun ifname directly (helper-spawned mihomo,
        no shared state). Fallback: any 198.18.0.1-bearing utun beyond the
        first counts as foreign. Throttled to once per 5 min per warning,
        otherwise the toast spams the user."""
        now = time.time()
        last = getattr(self, "_foreign_tun_warned_at", 0.0)
        if now - last < 300:
            return
        try:
            r = subprocess.run(
                ["/sbin/ifconfig", "-l"],
                capture_output=True, text=True, timeout=2,
            )
            utun_names = [n for n in r.stdout.split() if n.startswith("utun")]
        except Exception:
            return
        owners = []
        for u in utun_names:
            try:
                r = subprocess.run(
                    ["/sbin/ifconfig", u],
                    capture_output=True, text=True, timeout=1,
                )
                if "198.18.0.1" in r.stdout:
                    owners.append(u)
            except Exception:
                continue
        if len(owners) > 1:
            self._foreign_tun_warned_at = now
            others = ", ".join(owners[1:])
            self.log(f"⚠ 检测到另一个 mihomo TUN 占用 198.18.0.1（{others}）。"
                     "请关闭 FastLink/Mihomo Party/ClashX 等的 TUN 模式，"
                     "否则路由会互相破坏。")
            self.toast("检测到另一个代理客户端的 TUN 冲突", error=True)

    def _post_wake_recover(self):
        """Called 8s after a detected sleep→wake transition. Force a clean
        mihomo restart to refresh interface bindings and route monitor."""
        if not self.runner.is_running():
            return
        self.log("⌁ 系统已唤醒，重启 mihomo")
        self._watchdog_last_restart = time.time()
        self._watchdog_failures = 0
        self._watchdog_bounced = False
        self.stop(cancel_pending_restart=False)
        QTimer.singleShot(500, lambda: self.start(silent=True))

    def _deep_recover_worker(self):
        """Off-thread: bounce the primary OS interface, then restart mihomo.
        Used when post-restart probes still fail — i.e. mihomo's outbound
        socket is fine but the kernel route through Wi-Fi/Ethernet is dead
        (typical after sleep/wake or DHCP lease expiry)."""
        try:
            core.bounce_primary_interface(self.log)
        except Exception as e:
            self.qt_invoke(lambda: self.log(f"⚠ 网卡重置失败: {e}"))
            self.qt_invoke(lambda: self.toast("网卡重置失败", error=True))
            return
        self.qt_invoke(lambda: self.toast("网卡已重置，等待链路恢复…"))
        # Give the interface 3s to come back up + DHCP, then restart mihomo
        # so its outbound binds to the fresh route table.
        def restart():
            self._watchdog_last_restart = time.time()
            self._watchdog_failures = 0
            self.stop(cancel_pending_restart=False)
            QTimer.singleShot(500, lambda: self.start(silent=True))
        self.qt_invoke(lambda: QTimer.singleShot(3000, restart))

    def _socks5_connect_probe(self, target_ip: str, target_port: int) -> bool:
        """SOCKS5 CONNECT to a literal IP via the local mixed-port. We use IP
        (not hostname) on purpose — the rule engine then dispatches by
        IP-CIDR / GEOIP / MATCH without DNS, giving us deterministic routing
        even if fakeip is broken. Mihomo only sends rep=0 once the outbound
        TCP handshake succeeds, so a failed dial = failed probe."""
        import socket, struct
        try:
            port = int(self.cfg.get("local_port", 7890))
        except (TypeError, ValueError):
            return True  # don't false-alarm on bad config
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(5)
            s.connect(("127.0.0.1", port))
            s.sendall(b"\x05\x01\x00")  # SOCKS5, 1 method, no-auth
            resp = s.recv(2)
            if len(resp) != 2 or resp[0] != 0x05 or resp[1] != 0x00:
                return False
            req = (b"\x05\x01\x00\x01"
                   + socket.inet_aton(target_ip)
                   + struct.pack(">H", target_port))
            s.sendall(req)
            resp = s.recv(10)
            return len(resp) >= 2 and resp[0] == 0x05 and resp[1] == 0x00
        except Exception:
            return False
        finally:
            if s is not None:
                try:
                    s.close()
                except Exception:
                    pass

    def _probe_chain(self) -> bool:
        """SOCKS5 CONNECT to 8.8.8.8:443. Non-CN IP not in any cncidr/lan
        ruleset, falls through to MATCH (which is FirstHopOnly or Chain in
        all user configs we've seen) — so this exercises the upstream-proxy
        outbound path. If the user's first-hop client is closed, this fails
        (and the watchdog logs without restarting, since single-path
        failure is upstream-specific)."""
        return self._socks5_connect_probe("8.8.8.8", 443)

    def _probe_direct(self) -> bool:
        """SOCKS5 CONNECT to 223.5.5.5:443 via the mixed-port. AliDNS is in
        GEOIP CN → DIRECT, so this exercises the DIRECT outbound path —
        the path that breaks after sleep/wake when Chain (going through
        the upstream proxy at 127.0.0.1) still appears to work."""
        return self._socks5_connect_probe("223.5.5.5", 443)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "_toast") and self._toast.isVisible():
            self._toast.move(self.width() - self._toast.width() - 28, 18)

    def start(self, silent: bool = False):
        """Start mihomo. With silent=True, errors go to log/toast instead of modals
        — used by the debounced auto-restart."""
        def fail(title: str, msg: str, critical: bool = False):
            if silent:
                self.log(f"✗ {title}: {msg}")
                self.toast(f"{title}", error=True)
            elif critical:
                QMessageBox.critical(self, title, msg)
            else:
                QMessageBox.warning(self, title, msg)

        # Re-probe each click — user may have just installed mihomo without
        # restarting the GUI.
        if not self.mihomo_bin:
            self.mihomo_bin = core.find_mihomo()
        if not self.mihomo_bin:
            if sys.platform == "darwin":
                hint = "请运行 brew install mihomo"
            elif sys.platform == "win32":
                hint = ("请把 mihomo.exe 放到下列任一位置：\n\n"
                        f"  • {core.SUPPORT_DIR}\\mihomo.exe（推荐）\n"
                        "  • C:\\Program Files\\mihomo\\mihomo.exe\n"
                        "  • 任何 PATH 中的目录\n\n"
                        "下载：https://github.com/MetaCubeX/mihomo/releases")
            else:
                hint = "请安装 mihomo"
            fail("未找到 mihomo", hint, critical=True)
            return
        fh = next((x for x in self.cfg["first_hops"]
                   if x["name"] == self.cfg["active_first_hop"]), None)
        if not fh:
            fail("未选第一跳", "请先在「节点」里新增并选择第一跳")
            return
        if not core.tcp_reachable(fh["server"], fh["port"], timeout=2):
            fail("第一跳不可达",
                 f"无法连接 {fh['server']}:{fh['port']}（{fh['name']}）。\n\n"
                 "请先打开你的第一跳机场客户端，并关闭它的「系统代理」「TUN」开关。")
            return
        # TUN-mode loop-prevention guard: if the first hop is on 127.0.0.1
        # (i.e. it's another local proxy client whose own outbound traffic
        # would be re-captured by our TUN), require the user to list that
        # client's process names. Without this, every dial through the
        # first hop deadlocks and TLS times out.
        if (self.cfg.get("tun_mode")
                and fh["server"] in ("127.0.0.1", "localhost", "::1")
                and not (self.cfg.get("first_hop_process_names") or [])):
            fail("TUN 模式配置不完整",
                 "你启用了 TUN 模式，且第一跳指向本机 "
                 f"({fh['server']}:{fh['port']})。\n\n"
                 "TUN 会捕获**所有** IPv4 流量，包括你机场客户端自己的拨号 — "
                 "这会让客户端连不上它的远程服务器，TLS 握手永远失败。\n\n"
                 "解决方案任选其一：\n"
                 "  ① 关闭 TUN 模式（设置页），改用「系统代理模式」（推荐）\n"
                 "  ② 在 config.json 的 first_hop_process_names 里填上你机场\n"
                 "     客户端的所有 .exe 进程名（含 GUI 主进程和它派生的代理引擎）\n\n"
                 f"配置文件：{core.CONFIG_PATH}",
                 critical=True)
            return
        try:
            yaml = core.build_mihomo_yaml(self.cfg)
        except Exception as e:
            fail("配置错误", str(e), critical=True)
            return
        # Atomic write so a crash mid-write can't leave mihomo with a
        # half-truncated YAML on next start.
        core.atomic_write_text(core.MIHOMO_YAML, yaml)
        use_sudo = bool(self.cfg.get("tun_mode"))
        try:
            self.runner.start(self.mihomo_bin, use_sudo=use_sudo)
        except Exception as e:
            fail("启动失败", str(e), critical=True)
            return
        # CRITICAL: confirm mihomo actually came up before touching system
        # proxy. If it spawned and then crashed (bad config, port in use,
        # missing rule-set file) we must NOT point Windows at a dead port.
        # We poll the controller socket up to 2s — that's the most
        # authoritative liveness check available without async I/O.
        if not self._wait_until_listening(int(self.cfg["controller_port"]),
                                          timeout_s=2.0):
            self._intentional_stop = True  # we're killing it deliberately
            try:
                self.runner.stop()
            except Exception:
                pass
            tail = self._tail_mihomo_log(20)
            fail("mihomo 启动后立即崩溃",
                 f"端口 {self.cfg['controller_port']} 在 2 秒内未监听。\n\n"
                 f"最近日志：\n{tail or '(空)'}",
                 critical=True)
            return
        if self.cfg.get("set_system_proxy_on_start") and not use_sudo:
            self.toggle_system_proxy(True)
        elif use_sudo:
            self.log("TUN 模式已启用，无需设系统代理")
        self._mihomo_started_at = time.time()
        self._watchdog_failures = 0
        self._watchdog_bounced = False
        # New incarnation: any death from here on is unexpected (until the
        # user clicks stop or another deliberate-stop path runs).
        self._intentional_stop = False
        self._tick()

    def _cleanup_orphan_proxy_state(self):
        """Windows-only: a previous GUI session crashed/was killed while it
        owned the system proxy. The snapshot file is the marker.

        Two cases:
          1. Registry STILL points at our 127.0.0.1:<port> → restore the
             snapshot so the user has working internet again.
          2. Registry has been changed by something else (third-party proxy
             client, manual edit) since our crash → DON'T restore, the
             snapshot is now stale and would clobber whatever is currently
             working. Just drop the backup file so future enable/disable
             cycles aren't confused by it.
        """
        try:
            backup_path = core.SUPPORT_DIR / ".proxy_backup.json"
            if not backup_path.exists():
                return
            # Decide whether we still own the registry by looking at our
            # signature ProxyOverride string + a 127.0.0.1 server (don't pin
            # to a specific port — user may have changed `local_port` between
            # crash and restart).
            cur = core._read_proxy_state()
            registry_is_ours = (
                str(cur.get("ProxyOverride", "")) == core._DEFAULT_BYPASS
                and str(cur.get("ProxyServer", "")).startswith("127.0.0.1:")
            )
            if registry_is_ours:
                self.log("⚠ 检测到上次 GUI 异常退出时未还原系统代理，自动清理…")
                ok, info = core.set_system_proxy(0, enable=False)
                self.log(f"  系统代理状态：{info if ok else 'error: ' + info}")
            else:
                # Someone else has taken over the registry — leave it alone,
                # just drop our stale snapshot so we don't try to restore it
                # over a third-party client's working settings later.
                try:
                    backup_path.unlink()
                except OSError:
                    pass
                self.log("启动检查：检测到系统代理已被其他程序接管，跳过自动还原。")
        except Exception as e:
            self.log(f"启动清理失败：{e}")

    @staticmethod
    def _wait_until_listening(port: int, timeout_s: float = 2.0,
                              host: str = "127.0.0.1") -> bool:
        import socket as _s
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                with _s.create_connection((host, port), timeout=0.3):
                    return True
            except OSError:
                time.sleep(0.1)
        return False

    def _tail_mihomo_log(self, n: int = 20) -> str:
        try:
            txt = core.MIHOMO_LOG.read_text(encoding="utf-8", errors="replace")
            return "\n".join(txt.splitlines()[-n:])
        except OSError:
            return ""

    def stop(self, cancel_pending_restart: bool = True):
        if cancel_pending_restart and hasattr(self, "_restart_timer"):
            self._restart_timer.stop()
        # Mark this stop as user-initiated. The runner's tail thread checks
        # this flag (via _intentional_stop) to decide whether to fire
        # on_unexpected_exit when it sees mihomo gone. Without this, every
        # stop click would race with the death detector and either trigger
        # a phantom auto-restart or, in the other order, silently swallow
        # a real crash that happened to land during a stop.
        self._intentional_stop = True
        try:
            self.runner.stop()
        except Exception as e:
            self.log(f"stop error: {e}")
        if self._we_set_proxy:
            self.toggle_system_proxy(False)
        self._mihomo_started_at = 0.0
        self._watchdog_failures = 0
        self._watchdog_bounced = False
        self._tick()

    def toggle_system_proxy(self, on):
        try:
            port = int(self.cfg["local_port"])
        except ValueError:
            QMessageBox.warning(self, "格式错误", "本地端口无效")
            return
        ok, info = core.set_system_proxy(port, on)
        if ok:
            self._we_set_proxy = on
            self.log(f"{'已设为' if on else '已清除'}系统代理（{info}）")
            self.toast(f"{'已设为' if on else '已清除'}系统代理")
        else:
            if sys.platform == "darwin":
                detail = f"networksetup 出错：{info}\n你可能需要 sudo 权限。"
            else:
                detail = f"写入注册表失败：{info}"
            QMessageBox.warning(self, "失败", detail)

    def panic_recover(self):
        if sys.platform == "darwin":
            prompt = ("执行：清系统代理 + 杀残留 mihomo + 删 TUN 路由 + 关 utun "
                      "+ 重置主网卡（Wi-Fi/有线 down/up + DHCP 续约），"
                      "然后用全新状态重启 mihomo。\n\n"
                      "网卡会断开 2-3 秒。继续？")
        else:
            prompt = ("执行：清系统代理 + 杀残留 mihomo.exe，然后用全新状态重启。\n\n继续？")
        if QMessageBox.question(self, "网络急救", prompt) != QMessageBox.StandardButton.Yes:
            return
        # If mihomo was running before rescue, restart it after cleanup so
        # the user lands back in a working proxy state (fresh fakeip table,
        # fresh routes, fresh DNS) — not a "rescued but proxy off" limbo.
        # Without this, prior users found 网络急救 "useless" because it left
        # them with no proxy AND any other-process state still poisoned.
        # We also bounce the primary interface on macOS — sleep/wake leaves
        # the kernel route table wedged at L3, and mihomo restart alone
        # can't fix that. Bouncing forces ARP/route refresh + DHCP renew.
        was_running = self.runner.is_running()
        self._intentional_stop = True  # rescue is user-initiated
        def worker():
            try: self.runner.stop()
            except Exception: pass
            try:
                core.panic_recover(self.log)
            except Exception as e:
                self.qt_invoke(lambda: self.log(f"⚠ 清理失败: {e}"))
            if sys.platform == "darwin":
                try:
                    core.bounce_primary_interface(self.log)
                except Exception as e:
                    self.qt_invoke(lambda: self.log(f"⚠ 网卡重置失败: {e}"))
            if was_running:
                self.qt_invoke(lambda: self.toast("清理完成，3s 后重启 mihomo…"))
                # Give the bounced interface a moment to come back up + DHCP
                # before mihomo's auto-detect-interface samples the route.
                self.qt_invoke(lambda: QTimer.singleShot(
                    3000, lambda: self.start(silent=True)))
            else:
                self.qt_invoke(lambda: self.toast("已恢复网络"))
        threading.Thread(target=worker, daemon=True).start()

    def _on_mihomo_unexpected_exit(self):
        """mihomo died without us asking. Symptoms: panic on bad rule-set,
        OOM, segfault, SIGKILL from outside. The watchdog wouldn't catch
        this because is_running()=False after death — its outbound probe
        only triggers when alive-but-stuck. Auto-restart silently with
        the same backoff cap as the watchdog (180s window) to keep a
        crashloop from masking a real bug."""
        if self._intentional_stop:
            self._intentional_stop = False
            return
        now = time.time()
        if now - self._watchdog_last_restart < 180:
            self.log("⚠ mihomo 进程意外退出，但最近刚重启过 — 跳过自动拉起，避免 crashloop")
            self.toast("mihomo 反复崩溃，请检查规则/配置", error=True)
            return
        self._watchdog_last_restart = now
        self.log("⚠ mihomo 进程意外退出，自动拉起…")
        self.toast("mihomo 已崩溃，自动重启")
        QTimer.singleShot(500, lambda: self.start(silent=True))

    # ── system tray ───────────────────────────────────────────────────────
    def _build_tray(self):
        """Create the tray icon. Closing the window minimizes to tray instead
        of quitting; quit happens only via the tray menu's '退出' or Ctrl+Q."""
        self._tray_quit = False  # set by tray-quit / Ctrl+Q to allow real exit
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = None
            return

        icon = self.windowIcon()
        if icon.isNull():
            icon = self.q_app.windowIcon()
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("ChainProxy")

        menu = QMenu(self)
        act_show = QAction("打开主窗口", self)
        act_show.triggered.connect(self._show_window)
        act_toggle = QAction("启动 / 停止", self)
        act_toggle.triggered.connect(self._toggle_run)
        act_quit = QAction("退出 ChainProxy", self)
        act_quit.triggered.connect(self._quit_via_tray)
        menu.addAction(act_show)
        menu.addAction(act_toggle)
        menu.addSeparator()
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

        # Ctrl+Q: explicit quit shortcut (tray menu also has it)
        sc_quit = QShortcut(QKeySequence("Ctrl+Q"), self)
        sc_quit.activated.connect(self._quit_via_tray)

    def _on_tray_activated(self, reason):
        # Trigger = double-click on Windows / single-click on macOS by default
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self._show_window()

    def _show_window(self):
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def _quit_via_tray(self):
        self._tray_quit = True
        self.close()

    def _final_cleanup(self):
        """Synchronous cleanup invoked from any process-exit path:
        closeEvent, app.aboutToQuit, atexit, SIGTERM/SIGHUP. Without this,
        force-quitting the GUI (Cmd-Opt-Esc, OS shutdown, kill -TERM)
        leaves mihomo running and TUN routes hijacking 198.18.0.1 — user
        finds Wi-Fi 'broken' until they reboot or manually kill mihomo.
        Idempotent: the gate flag guards against running twice when more
        than one exit hook fires."""
        if self._final_cleanup_done:
            return
        self._final_cleanup_done = True
        self._intentional_stop = True
        try:
            if self.runner.is_running():
                self.runner.stop()
        except Exception:
            pass
        try:
            if getattr(self, "_we_set_proxy", False):
                core.set_system_proxy(0, enable=False)
        except Exception:
            pass
        # macOS TUN: helper recover wipes routes/utun even if runner.stop
        # already did so (defensive — we don't want a dead utun4 to outlive
        # us). Pass a no-op log so we don't try to write to a Qt UI that's
        # in the middle of tearing down.
        if sys.platform == "darwin" and self.cfg.get("tun_mode"):
            try:
                core.panic_recover(lambda *_a, **_kw: None)
            except Exception:
                pass

    def closeEvent(self, e):
        # Closing the window without an explicit quit minimizes to the tray
        # so the proxy keeps running. Hide rather than terminate.
        if self.tray and self.tray.isVisible() and not self._tray_quit:
            e.ignore()
            self.hide()
            # First time: tell the user where the app went, otherwise people
            # think it crashed. The flag is per-process — fine for our needs.
            if not getattr(self, "_tray_hint_shown", False):
                self._tray_hint_shown = True
                try:
                    self.tray.showMessage(
                        "ChainProxy 仍在运行",
                        "已最小化到通知区。点击图标打开，或右键选「退出 ChainProxy」彻底关闭。",
                        QSystemTrayIcon.MessageIcon.Information, 4000)
                except Exception:
                    pass
            return

        # Real quit: stop mihomo, restore proxy, close.
        if self.runner.is_running():
            r = QMessageBox.question(self, "退出",
                "代理仍在运行。退出会停止代理。继续？")
            if r != QMessageBox.StandardButton.Yes:
                e.ignore()
                self._tray_quit = False  # cancel: don't quit, but don't hide either
                return
            self._intentional_stop = True
            try: self.runner.stop()
            except Exception: pass
            if self._we_set_proxy:
                self.toggle_system_proxy(False)
            if self.cfg.get("tun_mode"):
                core.panic_recover(self.log)
        if self.tray:
            self.tray.hide()
        e.accept()


# ─── Entry ─────────────────────────────────────────────────────────────────

def _resource_path(relative: str) -> str:
    """Return an absolute path to a bundled resource that works both when
    running from source and from a PyInstaller --onedir build."""
    if getattr(sys, "frozen", False):
        # PyInstaller: resources live under sys._MEIPASS (one-file) or
        # alongside the exe (--onedir).
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relative)


def _find_app_icon() -> str:
    """Pick the best icon file for QApplication.setWindowIcon.
    macOS bundle ships ChainProxy.icns inside Resources/; the dev tree has
    icon.png at repo root. Windows uses icon.ico (multi-resolution)."""
    if sys.platform == "darwin":
        candidates = ("ChainProxy.icns", "icon.png", "icon.ico")
    elif sys.platform == "win32":
        candidates = ("icon.ico", "icon.png")
    else:
        candidates = ("icon.png", "icon.ico")
    for name in candidates:
        p = _resource_path(name)
        if os.path.exists(p):
            return p
    return ""


def main():
    # Windows + TUN mode: re-launch self elevated. This trades a single UAC
    # prompt at startup for zero UAC prompts on every start/stop of mihomo.
    # (Without this, every start AND every stop of TUN-mode mihomo pops UAC
    # — a worse UX than other Windows proxy clients.)
    if sys.platform == "win32":
        try:
            cfg_peek = core.load_config()
            need_elevation = bool(cfg_peek.get("tun_mode"))
        except Exception:
            need_elevation = False
        if need_elevation and not core.is_elevated():
            # Spawn elevated copy and exit. The elevated copy will acquire
            # the single-instance lock; this unelevated copy never reaches
            # that point so they don't fight each other.
            ok = core.relaunch_elevated()
            if ok:
                sys.exit(0)
            # If the user cancelled UAC, fall through to a non-elevated GUI.
            # mihomo TUN start will then fail with a clear error — better
            # than silently doing nothing.

    lock = core.acquire_single_instance_lock()
    if lock is None:
        core.activate_existing_window()
        sys.exit(0)

    QApplication.setApplicationName(core.APP_NAME)
    QApplication.setOrganizationName("ChainProxy")
    # Windows: tell Windows this is a distinct app for taskbar grouping
    # purposes, otherwise it groups under "python.exe" / "ChainProxy" and
    # reuses python's icon. AppUserModelID needs to match the .exe file.
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "Laogeyouge.ChainProxy.GUI.1")
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # consistent base, themed via QSS
    icon_path = _find_app_icon()
    if icon_path:
        app.setWindowIcon(QIcon(icon_path))

    w = MainWindow(app)
    if icon_path:
        w.setWindowIcon(QIcon(icon_path))

    # Catch every "the GUI is going away" path so mihomo doesn't outlive us.
    # closeEvent handles user-initiated window close. The hooks below cover:
    #   - app.aboutToQuit : Cmd-Q, tray-quit, programmatic QApplication.quit()
    #   - SIGTERM / SIGHUP : kill, OS shutdown handlers (Unix)
    #   - atexit : last-line-of-defense for unhandled paths
    # Without these, force-quit / kill -TERM / system reboot leave mihomo
    # running and the TUN routes hijacking 198.18.0.1 — the user reboots
    # and the network is "broken" until manual intervention.
    import atexit
    app.aboutToQuit.connect(w._final_cleanup)
    atexit.register(w._final_cleanup)
    if sys.platform != "win32":
        import signal
        def _on_term_signal(_signum, _frame):
            QApplication.quit()
        try:
            signal.signal(signal.SIGTERM, _on_term_signal)
            signal.signal(signal.SIGHUP, _on_term_signal)
        except (OSError, ValueError):
            # Not on main thread (we are, but be safe) or signal not
            # available — silently skip; aboutToQuit + atexit still cover us.
            pass

    w.show()
    w.raise_()
    w.activateWindow()
    try:
        sys.exit(app.exec())
    finally:
        try: lock.close()
        except Exception: pass


if __name__ == "__main__":
    main()
