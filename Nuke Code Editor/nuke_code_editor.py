# -*- coding: utf-8 -*-
"""
NukeCodeEditor 2.0  -  a PyCharm-style IDE for Nuke  (PySide2 + PySide6)
=========================================================================
Author : Nitin Kashyap

Runs inside Nuke 13-15 (PySide2) and Nuke 16+ (PySide6), or standalone:
    python nuke_code_editor.py

INSTALL  (menu.py)
------------------
    import nuke, nuke_code_editor
    nuke.menu('Nuke').addCommand('TD/Nuke Code Editor',
                                 'nuke_code_editor.show_window()', 'alt+e')
    nuke_code_editor.register_panel()          # optional dockable panel

Tip: use the floating window (show_window) - it avoids shortcut clashes with
Nuke's own Ctrl+S / Ctrl+N / Ctrl+O when the editor is docked in the main window.

WHAT'S INSIDE
-------------
Project tool window, Structure view, breadcrumbs, tabs, recent files
Editor: syntax highlight (Darcula / Light), code folding, bracket matching,
        auto-pairs, smart Enter/Backspace/Home, live templates, occurrence
        highlight, right margin, word wrap, whitespace, multi-line indent,
        move/duplicate/delete/join line, extend selection, toggle case
Inspections: syntax errors, unresolved references, unused imports/variables,
        bare except, mutable defaults, long lines  (squiggles + gutter + Problems)
Code:   completion (jedi if installed, else runtime dir() aware), quick
        documentation (F1), go to declaration (Ctrl+B / Ctrl+click) incl. Nuke
        library sources, find usages, rename (Shift+F6), surround with,
        reformat (black / autopep8 / built-in), optimize imports
Search: Find/Replace bar, Find in Files (+replace), Search Everywhere (double
        Shift), Go to File / Symbol / Line, recent files, bookmarks, back/forward
Run:    inside Nuke (with undo grouping), external Python, `nuke -t`,
        stop button, Python Console (REPL), Terminal, TODO panel
Debug:  real debugger - gutter breakpoints, step over/into/out, resume, call
        stack, variables, evaluate expression (built on bdb, runs in Nuke)
TD:     node browser, knob inspector, snippets, scene utilities
"""

__author__ = "Nitin Kashyap"
__version__ = "2.0"

import os
import re
import io
import sys
import ast
import bdb
import json
import math
import time
import code
import shutil
import fnmatch
import inspect
import keyword
import weakref
import builtins
import importlib
import linecache
import pkgutil
import tokenize
import traceback
from string import Template
from collections import Counter, namedtuple

# --------------------------------------------------------------------------- #
# Qt compatibility (PySide6 first, PySide2 fallback)
# --------------------------------------------------------------------------- #
try:
    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtGui import QAction
    QFileSystemModel = getattr(QtGui, "QFileSystemModel", None) or QtWidgets.QFileSystemModel
    PYSIDE = 6
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets
    from PySide2.QtWidgets import QAction
    QFileSystemModel = QtWidgets.QFileSystemModel
    PYSIDE = 2

Qt = QtCore.Qt
Signal = QtCore.Signal
QPoint, QPointF, QRect = QtCore.QPoint, QtCore.QPointF, QtCore.QRect
QColor = QtGui.QColor

try:
    import nuke
except ImportError:
    nuke = None

APP_NAME = "Nuke Code Editor"
# history lives in <NK_NukeCodeEditor>/history  (one level above td_tools), wherever the package is installed
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "history")
SESSION_FILE = os.path.join(DATA_DIR, "session.json")
SNIPPET_FILE = os.path.join(DATA_DIR, "snippets.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
THIS_FILE = os.path.normcase(os.path.abspath(__file__))


def _ensure_dir(path=None):
    try:
        os.makedirs(path or DATA_DIR)
    except OSError:
        pass


def _exec(obj, *args):
    """QDialog/QMenu/QApplication exec - works on PySide2 and PySide6."""
    fn = getattr(obj, "exec", None) or getattr(obj, "exec_")
    return fn(*args)


def _char_width(fm, ch="9"):
    return fm.horizontalAdvance(ch) if hasattr(fm, "horizontalAdvance") else fm.width(ch)


def read_text(path):
    with io.open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
        return f.read().replace("\r\n", "\n").replace("\r", "\n")


def write_text(path, text):
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def nuke_main_window():
    app = QtWidgets.QApplication.instance()
    if not app:
        return None
    for w in app.topLevelWidgets():
        try:
            if w.metaObject().className() == "Foundry::UI::DockMainWindow":
                return w
        except Exception:
            pass
    return None


def default_project_dir():
    if nuke:
        try:
            name = nuke.root().name()
            if name and name != "Root":
                return os.path.dirname(name)
        except Exception:
            pass
    nd = os.path.join(os.path.expanduser("~"), ".nuke")
    return nd if os.path.isdir(nd) else os.getcwd()


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
class Settings(object):
    DEFAULTS = {
        "theme": "Darcula",
        "font_family": "",
        "font_size": 10,
        "tab_width": 4,
        "margin": 120,
        "wrap": False,
        "whitespace": False,
        "run_mode": "Inside Nuke",
        "interpreter": "",
        "project": "",
        "recent_files": [],
        "recent_projects": [],
        "geometry": "",
    }

    def __init__(self):
        self.data = dict(self.DEFAULTS)
        self.load()

    def load(self):
        try:
            with open(SETTINGS_FILE, "r") as f:
                self.data.update(json.load(f))
        except Exception:
            pass

    def save(self):
        _ensure_dir()
        try:
            with open(SETTINGS_FILE, "w") as f:
                json.dump(self.data, f, indent=2)
        except Exception:
            pass

    def get(self, key):
        return self.data.get(key, self.DEFAULTS.get(key))

    def set(self, key, value, save=True):
        self.data[key] = value
        if save:
            self.save()

    def push_recent(self, key, value, limit=15):
        lst = [v for v in self.get(key) if v != value]
        lst.insert(0, value)
        self.set(key, lst[:limit])


SETTINGS = Settings()

# --------------------------------------------------------------------------- #
# Themes
# --------------------------------------------------------------------------- #
THEMES = {
    "Darcula": dict(
        bg="#2b2b2b", fg="#a9b7c6", gutter="#313335", gutter_fg="#606366", gutter_cur="#a4a3a3",
        line="#323232", sel="#214283", keyword="#cc7832", builtin="#8888c6", string="#6a8759",
        number="#6897bb", comment="#808080", deco="#bbb529", defname="#ffc66d", selfc="#94558d",
        panel="#3c3f41", border="#4a4a4a", accent="#4b6eaf", err="#ff6b68", warn="#f0a732",
        weak="#8a8a8a", match="#3b514d", occur="#344134", debug="#2d6099", bp_line="#5b3030",
        popup="#3c3f41", dim="#787878", btn="#4c5052", btn_hover="#5c6164", console_ok="#629755"),
    "Light": dict(
        bg="#ffffff", fg="#080808", gutter="#f0f0f0", gutter_fg="#999999", gutter_cur="#1a1a1a",
        line="#fcfaed", sel="#a6d2ff", keyword="#0033b3", builtin="#871094", string="#067d17",
        number="#1750eb", comment="#8c8c8c", deco="#9e880d", defname="#00627a", selfc="#94558d",
        panel="#f2f2f2", border="#c9c9c9", accent="#3574f0", err="#d5313b", warn="#c58a00",
        weak="#8a8a8a", match="#b8d6ff", occur="#e8f2fe", debug="#a6d2ff", bp_line="#f7c9c9",
        popup="#f7f7f7", dim="#8a8a8a", btn="#e6e6e6", btn_hover="#d8d8d8", console_ok="#067d17"),
}

T = {}


def set_theme(name):
    T.clear()
    T.update(THEMES.get(name) or THEMES["Darcula"])


set_theme(SETTINGS.get("theme"))

QSS = Template("""
QMainWindow, QDialog { background: $panel; }
QWidget { color: $fg; }
QToolTip { background: $popup; color: $fg; border: 1px solid $border; }
QPlainTextEdit, QLineEdit, QTreeView, QTreeWidget, QListWidget, QComboBox, QSpinBox, QTextEdit {
    background: $bg; color: $fg; border: 1px solid $border;
    selection-background-color: $sel; selection-color: $fg; }
QTreeView::item:selected, QListWidget::item:selected, QTreeWidget::item:selected { background: $sel; color: $fg; }
QTabWidget::pane { border: 1px solid $border; top: -1px; }
QTabBar::tab { background: $panel; padding: 4px 11px; border: 1px solid $border; border-bottom: none; }
QTabBar::tab:selected { background: $bg; border-bottom: 2px solid $accent; }
QToolBar { background: $panel; border: 0; spacing: 3px; }
QToolButton { padding: 3px 5px; border: 1px solid transparent; border-radius: 3px; }
QToolButton:hover { background: $btn_hover; }
QPushButton { background: $btn; border: 1px solid $border; padding: 3px 9px; border-radius: 3px; }
QPushButton:hover { background: $btn_hover; }
QPushButton:disabled { color: $dim; }
QMenuBar, QMenu { background: $panel; color: $fg; }
QMenu { border: 1px solid $border; }
QMenu::item:selected, QMenuBar::item:selected { background: $accent; color: #ffffff; }
QDockWidget::title { background: $panel; padding: 4px; }
QStatusBar { background: $panel; color: $fg; }
QHeaderView::section { background: $panel; border: 0; padding: 3px; }
QSplitter::handle { background: $border; }
QScrollBar:vertical { background: $bg; width: 12px; margin: 0; }
QScrollBar::handle:vertical { background: $btn; min-height: 24px; border-radius: 4px; margin: 1px; }
QScrollBar:horizontal { background: $bg; height: 12px; margin: 0; }
QScrollBar::handle:horizontal { background: $btn; min-width: 24px; border-radius: 4px; margin: 1px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QLabel#crumbs { padding: 2px 8px; color: $dim; background: $panel; }
""")


def build_style():
    return QSS.safe_substitute(**T)


# =========================================================================== #
#  ICONS  -  vector icons drawn with QPainter (no image files, theme-aware,
#            sharp at any DPI).  icon("run")  ->  QIcon
# =========================================================================== #
QRectF = QtCore.QRectF
ICON_SIZES = (16, 20, 24, 32, 48)
_ICON_DRAWERS = {}
_ICON_CACHE = {}
_ICON_USERS = []          # (object, name, tab_index)  re-skinned on theme change

_ICON_ALIAS = {
    "save_as": "save", "open_folder": "open", "replace_all": "replace", "rerun": "refresh",
    "clear": "trash", "usages": "find", "file_json": "file_txt",
}


def _icon_palette(mono=False):
    dark = QColor(T["bg"]).lightness() < 128
    if mono:
        d = QColor(T["dim"])
        d.setAlphaF(0.55)
        return dict(fg=d, accent=d, green=d, red=d, amber=d, blue=d, bg=QColor(T["bg"]))
    return dict(fg=QColor(T["fg"]),
                accent=QColor("#589df6" if dark else T["accent"]),
                green=QColor("#59a869" if dark else T["console_ok"]),
                red=QColor(T["err"]), amber=QColor(T["warn"]),
                blue=QColor(T["number"]), bg=QColor(T["bg"]))


def _ic(*names):
    def deco(fn):
        for n in names:
            _ICON_DRAWERS[n] = fn
        return fn
    return deco


def _poly(*pts):
    return QtGui.QPolygonF([QPointF(x, y) for x, y in pts])


def _stroke(p, col, w=1.8):
    p.setPen(QtGui.QPen(col, w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)


def _fill(p, col):
    p.setPen(Qt.NoPen)
    p.setBrush(QtGui.QBrush(col))


def _line(p, *pts):
    p.drawPolyline(_poly(*pts))


def _dot(p, x, y, r=1.7):
    p.drawEllipse(QPointF(x, y), r, r)


def _page(p, col, fold=True):
    """document outline (6..19 x 3..21) with folded corner"""
    _stroke(p, col)
    _line(p, (14, 3), (6, 3), (6, 21), (19, 21), (19, 8), (14, 3))
    if fold:
        _line(p, (14, 3), (14, 8), (19, 8))


def _arrow_h(p, x1, x2, y, d=3.2):
    _line(p, (x1, y), (x2, y))
    s = 1 if x2 > x1 else -1
    _line(p, (x2 - s * d, y - d), (x2, y), (x2 - s * d, y + d))


def _arrow_v(p, y1, y2, x, d=3.2):
    _line(p, (x, y1), (x, y2))
    s = 1 if y2 > y1 else -1
    _line(p, (x - d, y2 - s * d), (x, y2), (x + d, y2 - s * d))


def _badge(p, P, col, letter):
    _fill(p, col)
    p.drawEllipse(QRectF(2.5, 2.5, 19, 19))
    f = QtGui.QFont()
    f.setBold(True)
    f.setPixelSize(13)
    p.setFont(f)
    p.setPen(P["bg"])
    p.drawText(QRectF(2.5, 2.2, 19, 19), letter, QtGui.QTextOption(Qt.AlignCenter))


# ---- run / debug ---------------------------------------------------------- #
@_ic("run")
def _i_run(p, P):
    _fill(p, P["green"])
    p.drawPolygon(_poly((7, 4.5), (19.5, 12), (7, 19.5)))


@_ic("debug")
def _i_debug(p, P):
    c = P["green"]
    _stroke(p, c, 1.6)
    for s in (1, -1):
        def X(v, s=s):
            return 12 + s * (v - 12)
        _line(p, (X(8.5), 12), (X(4), 10))
        _line(p, (X(8.5), 15), (X(3.5), 15))
        _line(p, (X(8.5), 18), (X(4.5), 20.5))
        _line(p, (X(10.3), 5.2), (X(8.3), 2.8))
    _fill(p, c)
    p.drawEllipse(QRectF(8, 9, 8, 11.5))
    p.drawEllipse(QRectF(9.4, 4.6, 5.2, 4.8))


@_ic("stop")
def _i_stop(p, P):
    _fill(p, P["red"])
    p.drawRoundedRect(QRectF(6, 6, 12, 12), 2, 2)


@_ic("resume")
def _i_resume(p, P):
    _stroke(p, P["green"], 2.2)
    _line(p, (6.5, 5), (6.5, 19))
    _fill(p, P["green"])
    p.drawPolygon(_poly((10.5, 5), (19.5, 12), (10.5, 19)))


@_ic("breakpoint")
def _i_breakpoint(p, P):
    _fill(p, P["red"])
    p.drawEllipse(QRectF(5, 5, 14, 14))


@_ic("step_over")
def _i_step_over(p, P):
    _stroke(p, P["accent"])
    path = QtGui.QPainterPath()
    path.moveTo(3.5, 14.5)
    path.cubicTo(4, 4.5, 17, 4.5, 17.5, 13.5)
    p.drawPath(path)
    _line(p, (14.8, 11.6), (17.5, 14.6), (20.2, 11.6))
    _fill(p, P["accent"])
    _dot(p, 11, 19.5, 1.9)


@_ic("step_into")
def _i_step_into(p, P):
    _stroke(p, P["accent"])
    _arrow_v(p, 3.5, 14.5, 12, 4.5)
    _fill(p, P["accent"])
    _dot(p, 12, 19.5, 1.9)


@_ic("step_out")
def _i_step_out(p, P):
    _stroke(p, P["accent"])
    _arrow_v(p, 15, 3.5, 12, 4.5)
    _fill(p, P["accent"])
    _dot(p, 12, 19.5, 1.9)


@_ic("refresh")
def _i_refresh(p, P):
    _stroke(p, P["fg"])
    p.drawArc(QRectF(4.5, 4.5, 15, 15), 40 * 16, 280 * 16)
    # arrow head at the 40deg end of the arc
    _line(p, (13.8, 3.6), (17.4, 5.6), (15.8, 9.6))


# ---- file / project ------------------------------------------------------- #
@_ic("new")
def _i_new(p, P):
    _page(p, P["fg"])
    _stroke(p, P["accent"])
    _line(p, (9.5, 15), (15.5, 15))
    _line(p, (12.5, 12), (12.5, 18))


@_ic("open", "recent_files")
def _i_open(p, P):
    _stroke(p, P["fg"])
    _line(p, (3, 19.5), (3, 5.5), (9, 5.5), (11, 8), (19.5, 8), (19.5, 11))
    _line(p, (3, 19.5), (6, 11), (22, 11), (19, 19.5), (3, 19.5))


@_ic("folder")
def _i_folder(p, P):
    _stroke(p, P["fg"])
    _line(p, (3, 6), (9, 6), (11, 8.5), (21, 8.5), (21, 19), (3, 19), (3, 6))


@_ic("folder_fixed")
def _i_folder_fixed(p, P):
    c = QColor("#d6a645")
    _fill(p, c.darker(130))
    p.drawRoundedRect(QRectF(2.5, 4.5, 8, 5), 1.5, 1.5)
    _fill(p, c)
    p.drawRoundedRect(QRectF(2.5, 7.5, 19, 12.5), 2, 2)


@_ic("save")
def _i_save(p, P):
    _stroke(p, P["fg"])
    p.drawRoundedRect(QRectF(4, 4, 16, 16), 2, 2)
    _line(p, (8, 4), (8, 9), (16, 9), (16, 4))
    _line(p, (7.5, 20), (7.5, 14), (16.5, 14), (16.5, 20))


@_ic("save_all")
def _i_save_all(p, P):
    _stroke(p, P["fg"])
    _line(p, (7, 4), (7, 2.8), (20.8, 2.8), (20.8, 16.5), (19.5, 16.5))
    p.drawRoundedRect(QRectF(3.5, 6, 14.5, 14.5), 2, 2)
    _line(p, (7, 6), (7, 10.5), (14.5, 10.5), (14.5, 6))
    _line(p, (6.5, 20.5), (6.5, 15), (15, 15), (15, 20.5))


@_ic("close")
def _i_close(p, P):
    _stroke(p, P["fg"], 2)
    _line(p, (6.5, 6.5), (17.5, 17.5))
    _line(p, (17.5, 6.5), (6.5, 17.5))


@_ic("file")
def _i_file(p, P):
    _page(p, P["fg"])


@_ic("file_txt")
def _i_file_txt(p, P):
    c = QColor("#9aa3ad")
    _page(p, c)
    _stroke(p, c, 1.5)
    _line(p, (9, 12.5), (16, 12.5))
    _line(p, (9, 16), (16, 16))


@_ic("file_py")
def _i_file_py(p, P):
    c = QColor("#5a9fd4")
    _page(p, c)
    _fill(p, QColor("#f2c94c"))
    p.drawEllipse(QRectF(9, 14, 3.4, 3.4))
    _fill(p, c)
    p.drawEllipse(QRectF(12.8, 11, 3.4, 3.4))


@_ic("file_nk")
def _i_file_nk(p, P):
    c = QColor("#e8913a")
    _page(p, c)
    _stroke(p, c, 1.6)
    p.drawRoundedRect(QRectF(8.5, 12, 3.3, 3), 0.6, 0.6)
    p.drawRoundedRect(QRectF(13.3, 16, 3.3, 3), 0.6, 0.6)
    _line(p, (10.2, 15), (10.2, 17.5), (13.3, 17.5))


@_ic("structure")
def _i_structure(p, P):
    _stroke(p, P["fg"])
    _line(p, (6, 7), (6, 18), (11, 18))
    _line(p, (6, 12.5), (11, 12.5))
    _fill(p, P["accent"])
    p.drawRoundedRect(QRectF(3.5, 3.5, 5, 4), 1, 1)
    p.drawRoundedRect(QRectF(11, 10.5, 8, 4), 1, 1)
    p.drawRoundedRect(QRectF(11, 16, 8, 4), 1, 1)


@_ic("toolbox")
def _i_toolbox(p, P):
    _stroke(p, P["fg"])
    p.drawRoundedRect(QRectF(3, 8, 18, 12), 2, 2)
    _line(p, (9, 8), (9, 5), (15, 5), (15, 8))
    _line(p, (3, 13.5), (21, 13.5))
    _fill(p, P["accent"])
    p.drawRoundedRect(QRectF(10.5, 12, 3, 3.5), 0.8, 0.8)


@_ic("gear")
def _i_gear(p, P):
    _stroke(p, P["fg"], 1.7)
    pts = []
    for k in range(8):
        a = k * math.pi / 4
        for da, r in ((-0.30, 6.6), (-0.19, 9.4), (0.19, 9.4), (0.30, 6.6)):
            pts.append((12 + r * math.cos(a + da), 12 + r * math.sin(a + da)))
    p.drawPolygon(_poly(*pts))
    p.drawEllipse(QRectF(9.2, 9.2, 5.6, 5.6))


@_ic("app")
def _i_app(p, P):
    g = QtGui.QLinearGradient(0, 0, 0, 24)
    g.setColorAt(0, QColor("#4f8df0"))
    g.setColorAt(1, QColor("#2a4fa0"))
    p.setPen(Qt.NoPen)
    p.setBrush(QtGui.QBrush(g))
    p.drawRoundedRect(QRectF(1, 1, 22, 22), 5.5, 5.5)
    _stroke(p, QColor("#ffffff"), 2.3)
    _line(p, (6.8, 7.8), (11.3, 12), (6.8, 16.2))
    _line(p, (13.2, 16.6), (17.6, 16.6))
    _fill(p, QColor("#ffb54d"))
    _dot(p, 17.6, 7.4, 1.9)


# ---- search / navigate ---------------------------------------------------- #
def _magnifier(p, col, ox=0.0, oy=0.0, k=1.0):
    _stroke(p, col)
    p.drawEllipse(QRectF(4 + ox, 4 + oy, 10 * k, 10 * k))
    _line(p, (ox + 4 + 8.6 * k, oy + 4 + 8.6 * k), (ox + 19.5, oy + 19.5))


@_ic("find")
def _i_find(p, P):
    _magnifier(p, P["fg"])


@_ic("search_all")
def _i_search_all(p, P):
    _magnifier(p, P["fg"], -0.5, 1.5, 0.95)
    _stroke(p, P["accent"], 1.6)
    _line(p, (18, 2.8), (18, 8.8))
    _line(p, (15, 5.8), (21, 5.8))


@_ic("find_files")
def _i_find_files(p, P):
    _stroke(p, P["fg"], 1.6)
    _line(p, (13.5, 4.5), (21, 4.5))
    _line(p, (15.5, 8.5), (21, 8.5))
    _line(p, (17.5, 12.5), (21, 12.5))
    _line(p, (3.5, 20), (8, 20))
    _stroke(p, P["accent"], 1.8)
    p.drawEllipse(QRectF(3.5, 4.5, 8, 8))
    _line(p, (9.8, 10.8), (13.5, 14.5))


@_ic("replace")
def _i_replace(p, P):
    _stroke(p, P["fg"])
    _arrow_h(p, 4, 19, 8, 3.2)
    _arrow_h(p, 20, 5, 16, 3.2)


@_ic("back")
def _i_back(p, P):
    _stroke(p, P["fg"])
    _arrow_h(p, 19, 5, 12, 4.5)


@_ic("forward")
def _i_forward(p, P):
    _stroke(p, P["fg"])
    _arrow_h(p, 5, 19, 12, 4.5)


@_ic("up")
def _i_up(p, P):
    _stroke(p, P["fg"])
    _arrow_v(p, 19, 5, 12, 4.5)


@_ic("down")
def _i_down(p, P):
    _stroke(p, P["fg"])
    _arrow_v(p, 5, 19, 12, 4.5)


@_ic("goto")
def _i_goto(p, P):
    _stroke(p, P["fg"])
    p.drawEllipse(QRectF(6.5, 6.5, 11, 11))
    _line(p, (12, 2.5), (12, 6))
    _line(p, (12, 18), (12, 21.5))
    _line(p, (2.5, 12), (6, 12))
    _line(p, (18, 12), (21.5, 12))
    _fill(p, P["accent"])
    _dot(p, 12, 12, 2)


@_ic("recent")
def _i_recent(p, P):
    _stroke(p, P["fg"])
    p.drawEllipse(QRectF(3.5, 3.5, 17, 17))
    _line(p, (12, 7.5), (12, 12), (15.5, 14))


@_ic("bookmark")
def _i_bookmark(p, P):
    _stroke(p, P["amber"])
    _line(p, (7, 3.5), (17, 3.5), (17, 20.5), (12, 16), (7, 20.5), (7, 3.5))


@_ic("bolt")
def _i_bolt(p, P):
    _fill(p, P["amber"])
    p.drawPolygon(_poly((13.5, 2.5), (5.5, 13.5), (11, 13.5), (9.5, 21.5), (18.5, 9.5), (12.8, 9.5)))


# ---- edit / code ---------------------------------------------------------- #
@_ic("hash")
def _i_hash(p, P):
    _stroke(p, P["fg"], 1.7)
    _line(p, (9.6, 4), (8, 20))
    _line(p, (16, 4), (14.4, 20))
    _line(p, (4.5, 9), (20, 9))
    _line(p, (4, 15), (19.5, 15))


@_ic("duplicate")
def _i_duplicate(p, P):
    _stroke(p, P["fg"])
    p.drawRoundedRect(QRectF(8.5, 8.5, 12, 12), 2, 2)
    _line(p, (5.5, 15.5), (3.5, 15.5), (3.5, 3.5), (15.5, 3.5), (15.5, 5.5))


@_ic("trash")
def _i_trash(p, P):
    _stroke(p, P["fg"])
    _line(p, (4.5, 7), (19.5, 7))
    _line(p, (9.5, 7), (9.5, 4.5), (14.5, 4.5), (14.5, 7))
    _line(p, (6.5, 7), (7.7, 20), (16.3, 20), (17.5, 7))
    _line(p, (10.5, 10.5), (10.8, 16.5))
    _line(p, (13.5, 10.5), (13.2, 16.5))


@_ic("join")
def _i_join(p, P):
    _stroke(p, P["fg"])
    _line(p, (4, 5), (20, 5))
    _line(p, (4, 19), (20, 19))
    _arrow_v(p, 8.5, 15.5, 12, 3.2)


@_ic("extend")
def _i_extend(p, P):
    _stroke(p, P["fg"])
    _line(p, (4, 9), (4, 4), (9, 4))
    _line(p, (15, 4), (20, 4), (20, 9))
    _line(p, (20, 15), (20, 20), (15, 20))
    _line(p, (9, 20), (4, 20), (4, 15))
    _fill(p, P["accent"])
    _dot(p, 12, 12, 2.2)


@_ic("fold")
def _i_fold(p, P):
    _stroke(p, P["fg"])
    _line(p, (7, 4), (12, 9), (17, 4))
    _line(p, (7, 20), (12, 15), (17, 20))
    _line(p, (4.5, 12), (19.5, 12))


@_ic("unfold")
def _i_unfold(p, P):
    _stroke(p, P["fg"])
    _line(p, (7, 9), (12, 4), (17, 9))
    _line(p, (7, 15), (12, 20), (17, 15))
    _line(p, (4.5, 12), (6.5, 12))
    _line(p, (10.5, 12), (13.5, 12))
    _line(p, (17.5, 12), (19.5, 12))


@_ic("complete")
def _i_complete(p, P):
    _stroke(p, P["fg"], 1.6)
    p.drawRoundedRect(QRectF(3.5, 4.5, 17, 15), 2, 2)
    _fill(p, P["accent"])
    p.drawRoundedRect(QRectF(5.5, 6.7, 13, 3.2), 1, 1)
    _stroke(p, P["fg"], 1.6)
    _line(p, (6.5, 13.2), (14, 13.2))
    _line(p, (6.5, 16.4), (11, 16.4))


@_ic("info")
def _i_info(p, P):
    _stroke(p, P["accent"])
    p.drawEllipse(QRectF(3.5, 3.5, 17, 17))
    _line(p, (12, 11), (12, 16.5))
    _fill(p, P["accent"])
    _dot(p, 12, 7.7, 1.2)


@_ic("rename")
def _i_rename(p, P):
    _stroke(p, P["fg"])
    p.drawPolygon(_poly((4, 20), (4.9, 15.8), (16.4, 4.3), (19.7, 7.6), (8.2, 19.1)))
    _line(p, (14, 6.7), (17.3, 10))


@_ic("brackets")
def _i_brackets(p, P):
    _stroke(p, P["fg"])
    _line(p, (9, 4), (5, 4), (5, 20), (9, 20))
    _line(p, (15, 4), (19, 4), (19, 20), (15, 20))
    _fill(p, P["accent"])
    _dot(p, 12, 12, 2)


@_ic("reformat")
def _i_reformat(p, P):
    _stroke(p, P["fg"])
    _line(p, (4, 5.5), (20, 5.5))
    _line(p, (4, 10), (14, 10))
    _line(p, (4, 14.5), (20, 14.5))
    _line(p, (4, 19), (12, 19))


@_ic("imports")
def _i_imports(p, P):
    _stroke(p, P["fg"])
    _line(p, (5, 14), (5, 20), (19, 20), (19, 14))
    _stroke(p, P["accent"])
    _arrow_v(p, 3.5, 15, 12, 4)


@_ic("wrap")
def _i_wrap(p, P):
    _stroke(p, P["fg"])
    _line(p, (4, 6), (20, 6))
    path = QtGui.QPainterPath()
    path.moveTo(4, 12)
    path.lineTo(17, 12)
    path.cubicTo(21.5, 12, 21.5, 18, 17, 18)
    p.drawPath(path)
    _line(p, (7, 18), (12, 18))
    _line(p, (9.5, 15.5), (7, 18), (9.5, 20.5))


@_ic("whitespace")
def _i_whitespace(p, P):
    _fill(p, P["fg"])
    for x in (5, 10, 15, 20):
        _dot(p, x, 12, 1.5)
    _stroke(p, P["accent"], 1.6)
    _line(p, (4, 18.5), (4, 20), (20, 20), (20, 18.5))


@_ic("zoom_in")
def _i_zoom_in(p, P):
    _magnifier(p, P["fg"])
    _stroke(p, P["accent"], 1.6)
    _line(p, (6.8, 9), (11.2, 9))
    _line(p, (9, 6.8), (9, 11.2))


@_ic("zoom_out")
def _i_zoom_out(p, P):
    _magnifier(p, P["fg"])
    _stroke(p, P["accent"], 1.6)
    _line(p, (6.8, 9), (11.2, 9))


@_ic("keyboard")
def _i_keyboard(p, P):
    _stroke(p, P["fg"], 1.6)
    p.drawRoundedRect(QRectF(2.5, 6, 19, 12), 2, 2)
    _fill(p, P["fg"])
    for x in (6.5, 10.2, 13.9, 17.6):
        _dot(p, x, 10, 0.9)
    _dot(p, 8.2, 14, 0.9)
    _dot(p, 15.8, 14, 0.9)
    _stroke(p, P["fg"], 1.4)
    _line(p, (10, 14), (14, 14))


# ---- tool windows --------------------------------------------------------- #
@_ic("terminal")
def _i_terminal(p, P):
    _stroke(p, P["fg"])
    p.drawRoundedRect(QRectF(3, 4.5, 18, 15), 2, 2)
    _stroke(p, P["accent"])
    _line(p, (7, 9.3), (10.6, 12), (7, 14.7))
    _line(p, (12.5, 15), (17, 15))


@_ic("pyconsole")
def _i_pyconsole(p, P):
    _stroke(p, P["accent"], 1.9)
    _line(p, (4.5, 7.5), (9, 12), (4.5, 16.5))
    _line(p, (11, 7.5), (15.5, 12), (11, 16.5))
    _stroke(p, P["fg"], 1.9)
    _line(p, (17.5, 16.5), (21, 16.5))


@_ic("problems")
def _i_problems(p, P):
    _stroke(p, P["amber"])
    _line(p, (12, 3.6), (21.2, 19.8), (2.8, 19.8), (12, 3.6))
    _line(p, (12, 9.5), (12, 14))
    _fill(p, P["amber"])
    _dot(p, 12, 16.9, 1.1)


@_ic("error")
def _i_error(p, P):
    _fill(p, P["red"])
    p.drawEllipse(QRectF(3.5, 3.5, 17, 17))
    _stroke(p, P["bg"], 2)
    _line(p, (8.5, 8.5), (15.5, 15.5))
    _line(p, (15.5, 8.5), (8.5, 15.5))


@_ic("warning")
def _i_warning(p, P):
    _fill(p, P["amber"])
    p.drawPolygon(_poly((12, 3.5), (21.5, 20), (2.5, 20)))
    _stroke(p, P["bg"], 2)
    _line(p, (12, 9.5), (12, 14.2))
    _fill(p, P["bg"])
    _dot(p, 12, 17.1, 1.15)


@_ic("weak")
def _i_weak(p, P):
    _fill(p, QColor(T["weak"]))
    _dot(p, 12, 12, 3)


@_ic("todo")
def _i_todo(p, P):
    _stroke(p, P["fg"])
    p.drawRoundedRect(QRectF(4, 4, 16, 16), 3, 3)
    _stroke(p, P["green"], 2)
    _line(p, (8, 12.2), (11, 15.2), (16.5, 8.8))


@_ic("nodes")
def _i_nodes(p, P):
    _stroke(p, P["fg"])
    _line(p, (12, 8), (12, 12), (6, 12), (6, 16))
    _line(p, (12, 12), (18, 12), (18, 16))
    _stroke(p, P["accent"])
    p.drawRoundedRect(QRectF(8.5, 3.5, 7, 4.5), 1, 1)
    p.drawRoundedRect(QRectF(2.8, 16, 6.4, 4.5), 1, 1)
    p.drawRoundedRect(QRectF(14.8, 16, 6.4, 4.5), 1, 1)


@_ic("eye")
def _i_eye(p, P):
    _stroke(p, P["fg"])
    path = QtGui.QPainterPath()
    path.moveTo(2.5, 12)
    path.cubicTo(6, 5.8, 18, 5.8, 21.5, 12)
    path.cubicTo(18, 18.2, 6, 18.2, 2.5, 12)
    p.drawPath(path)
    _fill(p, P["accent"])
    _dot(p, 12, 12, 3.1)


@_ic("snippets")
def _i_snippets(p, P):
    _stroke(p, P["fg"])
    _line(p, (8, 7), (3, 12), (8, 17))
    _line(p, (16, 7), (21, 12), (16, 17))
    _stroke(p, P["accent"])
    _line(p, (13.5, 5), (10.5, 19))


@_ic("sliders")
def _i_sliders(p, P):
    _stroke(p, P["fg"], 1.6)
    for y in (6.5, 12, 17.5):
        _line(p, (4, y), (20, y))
    _fill(p, P["accent"])
    _dot(p, 9, 6.5, 2.3)
    _dot(p, 15.5, 12, 2.3)
    _dot(p, 8, 17.5, 2.3)


# ---- symbol badges (Structure view) ---------------------------------------- #
@_ic("sym_class")
def _i_sym_class(p, P):
    _badge(p, P, P["green"], "C")


@_ic("sym_function")
def _i_sym_function(p, P):
    _badge(p, P, P["accent"], "f")


@_ic("sym_method")
def _i_sym_method(p, P):
    _badge(p, P, P["accent"], "m")


@_ic("sym_var")
def _i_sym_var(p, P):
    _badge(p, P, P["amber"], "v")


def _render_icon(name, size, pal):
    pm = QtGui.QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    p.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
    p.scale(size / 24.0, size / 24.0)
    try:
        _ICON_DRAWERS[name](p, pal)
    finally:
        p.end()
    return pm


def icon(name):
    """Themed QIcon for `name` (cached; empty QIcon when the name is unknown)."""
    name = _ICON_ALIAS.get(name, name)
    if name not in _ICON_DRAWERS:
        return QtGui.QIcon()
    key = (name, T["bg"], T["fg"], T["accent"])
    ic = _ICON_CACHE.get(key)
    if ic is None:
        ic = QtGui.QIcon()
        try:
            normal, dis = _icon_palette(), _icon_palette(True)
            for s in ICON_SIZES:
                ic.addPixmap(_render_icon(name, s, normal), QtGui.QIcon.Normal, QtGui.QIcon.Off)
                ic.addPixmap(_render_icon(name, s, dis), QtGui.QIcon.Disabled, QtGui.QIcon.Off)
        except Exception:
            ic = QtGui.QIcon()
        _ICON_CACHE[key] = ic
    return ic


def _apply_icon(obj, name, index):
    ic = icon(name)
    if index is not None:
        obj.setTabIcon(index, ic)
    else:
        obj.setIcon(ic)


def use_icon(obj, name, index=None):
    """Give `obj` (QAction / button / tab of a QTabWidget) an icon that follows the theme."""
    _ICON_USERS.append((obj, name, index))
    try:
        _apply_icon(obj, name, index)
    except Exception:
        pass


def refresh_icons():
    """Re-skin every registered icon (called after a theme switch)."""
    alive = []
    for obj, name, index in _ICON_USERS:
        try:
            _apply_icon(obj, name, index)
            alive.append((obj, name, index))
        except RuntimeError:               # underlying C++ object is gone
            pass
    _ICON_USERS[:] = alive


# toolbar / menu action  ->  icon name
ACTION_ICONS = {
    "Run": "run", "Run (Shift+F10)": "run", "Run (F5)": "run", "Run Selection / Line": "run",
    "Debug": "debug", "Stop": "stop", "Toggle Breakpoint": "breakpoint", "Resume": "resume",
    "Step Over": "step_over", "Step Into": "step_into", "Step Out": "step_out", "Clear Output": "trash",
    "New": "new", "Open...": "open", "Open Folder as Project...": "folder", "Save": "save",
    "Save As...": "save", "Save All": "save_all", "Settings...": "gear", "Close Tab": "close",
    "Find...": "find", "Find Next": "down", "Find Previous": "up", "Replace...": "replace",
    "Find in Files...": "find_files", "Toggle Comment": "hash", "Duplicate Line": "duplicate",
    "Delete Line": "trash", "Move Line Up": "up", "Move Line Down": "down", "Join Lines": "join",
    "Extend Selection": "extend", "Fold All": "fold", "Unfold All": "unfold",
    "Search Everywhere": "search_all", "Find Action...": "bolt", "Go to File...": "file",
    "Go to Symbol...": "sym_class", "Recent Files...": "recent", "Go to Line...": "goto",
    "Go to Declaration": "goto", "Find Usages": "find", "Back": "back", "Forward": "forward",
    "Next Problem": "down", "Previous Problem": "up", "Toggle Bookmark": "bookmark",
    "Show Bookmarks": "bookmark", "Complete": "complete", "Quick Documentation": "info",
    "Rename Symbol...": "rename", "Surround With...": "brackets", "Reformat Code": "reformat",
    "Optimize Imports": "imports", "Soft Wrap": "wrap", "Show Whitespace": "whitespace",
    "Font Larger": "zoom_in", "Font Smaller": "zoom_out", "Focus Terminal": "terminal",
    "Focus Python Console": "pyconsole", "Keyboard Shortcuts": "keyboard", "About": "info",
}

# =========================================================================== #
#  PURE-PYTHON LOGIC  (no Qt - unit-testable)
# =========================================================================== #
Problem = namedtuple("Problem", "line col end sev msg")          # line 1-based, col 0-based
Symbol = namedtuple("Symbol", "kind name line end depth sig")

_STDLIB_FALLBACK = frozenset("""abc argparse array ast asyncio atexit base64 bisect builtins bz2 calendar cgi cmath cmd
code codecs collections colorsys concurrent configparser contextlib copy csv ctypes dataclasses datetime decimal difflib
dis email enum errno fnmatch fractions functools gc getpass gettext glob gzip hashlib heapq hmac html http imp importlib
inspect io itertools json keyword linecache locale logging lzma math mimetypes multiprocessing numbers operator os
pathlib pdb pickle pkgutil platform plistlib pprint profile pstats queue random re sched secrets select shelve shlex
shutil signal site smtplib socket sqlite3 ssl stat statistics string struct subprocess sys sysconfig tarfile tempfile
textwrap threading time timeit tkinter token tokenize traceback types typing unicodedata unittest urllib uuid warnings
weakref webbrowser xml zipfile zlib __future__ ntpath posixpath""".split())
STDLIB = getattr(sys, "stdlib_module_names", None) or _STDLIB_FALLBACK


def _is_str_const(node):
    return isinstance(getattr(node, "value", getattr(node, "s", None)), str)


def _str_value(node):
    return getattr(node, "value", getattr(node, "s", None))


def _used_names(tree):
    used = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            used.add(n.id)
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == "__all__" and isinstance(n.value, (ast.List, ast.Tuple)):
                    for el in n.value.elts:
                        if _is_str_const(el):
                            used.add(_str_value(el))
    return used


def _stmt_end(node, lines):
    """1-based last line of a statement (works on Python 3.7 too)."""
    e = getattr(node, "end_lineno", None)
    if e:
        return e
    i, depth = node.lineno - 1, 0
    while i < len(lines):
        s = lines[i]
        depth += s.count("(") - s.count(")")
        if depth <= 0 and not s.rstrip().endswith("\\"):
            break
        i += 1
    return min(i + 1, len(lines))


def analyze(text, known=(), max_len=0):
    """Lightweight inspections -> list[Problem]."""
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        line = e.lineno or 1
        col = max(0, (e.offset or 1) - 1)
        end = col + 1
        if getattr(e, "end_offset", None) and (getattr(e, "end_lineno", line) or line) == line:
            end = max(col + 1, e.end_offset - 1)
        return [Problem(line, col, end, "error", e.msg or "Syntax error")]
    except (ValueError, RecursionError, MemoryError) as e:
        return [Problem(1, 0, 1, "error", str(e))]

    lines = text.split("\n")
    probs = []

    def add(node, sev, msg, length=None):
        col = getattr(node, "col_offset", 0)
        end = getattr(node, "end_col_offset", None)
        if length is not None:
            end = col + length
        if end is None or getattr(node, "end_lineno", node.lineno) != node.lineno:
            end = max(col + 1, len(lines[node.lineno - 1].rstrip()) if node.lineno <= len(lines) else col + 1)
        probs.append(Problem(node.lineno, col, max(end, col + 1), sev, msg))

    star = any(isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names) for n in ast.walk(tree))
    used = _used_names(tree)
    has_all = any(isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__"
                                                    for t in n.targets) for n in ast.walk(tree))

    # ---- unused imports
    if not has_all:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    nm = a.asname or a.name.split(".")[0]
                    if nm not in used:
                        add(node, "warning", "Unused import statement '%s'" % a.name)
            elif isinstance(node, ast.ImportFrom) and node.module != "__future__":
                for a in node.names:
                    if a.name != "*" and (a.asname or a.name) not in used:
                        add(node, "warning", "Unused import statement '%s'" % a.name)

    # ---- unresolved references
    defined = set(dir(builtins)) | set(known) | {
        "__file__", "__name__", "__doc__", "__builtins__", "__spec__", "__loader__",
        "__package__", "__path__", "__class__", "__qualname__", "__module__"}
    for n in ast.walk(tree):
        nm = getattr(n, "name", None)
        if isinstance(nm, str):
            defined.add(nm)
        if isinstance(getattr(n, "rest", None), str):
            defined.add(n.rest)
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            defined.add(n.id)
        elif isinstance(n, ast.arg):
            defined.add(n.arg)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                defined.add(a.asname or a.name.split(".")[0])
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            defined.update(n.names)
    if not star and "exec(" not in text and "globals()" not in text:
        seen = 0
        for n in ast.walk(tree):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in defined:
                add(n, "warning", "Unresolved reference '%s'" % n.id, len(n.id))
                seen += 1
                if seen > 150:
                    break

    # ---- unused locals, mutable defaults, bare except
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            loads, declared, stores = set(), set(), {}
            for n in ast.walk(fn):
                if isinstance(n, (ast.Global, ast.Nonlocal)):
                    declared.update(n.names)
                elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                    loads.add(n.id)
                elif isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name):
                    loads.add(n.target.id)
                elif isinstance(n, ast.Assign):
                    for t in n.targets:
                        if isinstance(t, ast.Name):
                            stores.setdefault(t.id, t)
            if "locals" not in loads:
                for name, node in stores.items():
                    if name not in loads and name not in declared and not name.startswith("_"):
                        add(node, "warning", "Local variable '%s' value is not used" % name, len(name))
            defaults = list(fn.args.defaults) + [d for d in fn.args.kw_defaults if d is not None]
            for d in defaults:
                if isinstance(d, (ast.List, ast.Dict, ast.Set)):
                    add(d, "warning", "Default argument value is mutable")
        elif isinstance(fn, ast.ExceptHandler) and fn.type is None:
            add(fn, "warning", "Too broad exception clause", 6)

    # ---- long lines
    if max_len:
        cnt = 0
        for i, l in enumerate(lines):
            if len(l) > max_len:
                probs.append(Problem(i + 1, max_len, len(l), "weak", "Line too long (%d > %d)" % (len(l), max_len)))
                cnt += 1
                if cnt > 60:
                    break
    probs.sort(key=lambda p: (p.line, p.col))
    return probs


def outline(text):
    """Symbols for the Structure view. Returns None if the text does not parse."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return None
    out = []

    def end_of(n):
        e = getattr(n, "end_lineno", None)
        if e:
            return e
        return max(getattr(x, "lineno", n.lineno) for x in ast.walk(n))

    def visit(body, depth, in_class):
        for n in body:
            if isinstance(n, ast.ClassDef):
                out.append(Symbol("class", n.name, n.lineno, end_of(n), depth, n.name))
                visit(n.body, depth + 1, True)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = [a.arg for a in n.args.args]
                sig = "%s(%s)" % (n.name, ", ".join(args))
                out.append(Symbol("method" if in_class else "function", n.name, n.lineno, end_of(n), depth, sig))
                visit(n.body, depth + 1, False)
            elif isinstance(n, ast.Assign) and depth == 0:
                for t in n.targets:
                    if isinstance(t, ast.Name) and t.id.isupper():
                        out.append(Symbol("var", t.id, n.lineno, n.lineno, depth, t.id))
            elif isinstance(n, (ast.If, ast.Try, ast.With)) and depth == 0:
                visit(getattr(n, "body", []), depth, in_class)
                for h in getattr(n, "handlers", []):
                    visit(h.body, depth, in_class)
                visit(getattr(n, "orelse", []), depth, in_class)

    visit(tree.body, 0, False)
    return out


def symbol_chain(symbols, line):
    """Symbols (outermost first) that contain 1-based *line*."""
    chain = [s for s in (symbols or []) if s.kind != "var" and s.line <= line <= s.end]
    chain.sort(key=lambda s: s.depth)
    return chain


# --------------------------------------------------------------------------- #
# Refactoring helpers
# --------------------------------------------------------------------------- #
def rename_tokens(text, old, new):
    """Rename every NAME token *old* -> *new* (strings/comments untouched)."""
    lines = text.split("\n")
    hits = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.NAME and tok.string == old:
            hits.append(tok.start)
    for row, col in sorted(hits, reverse=True):
        l = lines[row - 1]
        lines[row - 1] = l[:col] + new + l[col + len(old):]
    return "\n".join(lines), len(hits)


def _import_entries(node):
    out = []
    if isinstance(node, ast.Import):
        for a in node.names:
            out.append(("import", a.name, 0, None, a.asname))
    else:
        for a in node.names:
            out.append(("from", node.module or "", node.level or 0, a.name, a.asname))
    return out


def _entry_binding(e):
    kind, module, level, name, asname = e
    if kind == "import":
        return asname or module.split(".")[0]
    return asname or name


def _render_import_run(entries):
    def group_of(e):
        kind, module, level, name, asname = e
        if kind == "from" and module == "__future__":
            return 0
        if level:
            return 3
        return 1 if module.split(".")[0] in STDLIB else 2

    groups = {}
    for e in entries:
        groups.setdefault(group_of(e), []).append(e)
    blocks = []
    for g in sorted(groups):
        es = groups[g]
        lines = []
        plain = sorted({(e[1], e[4]) for e in es if e[0] == "import"}, key=lambda x: x[0].lower())
        for m, a in plain:
            lines.append("import %s%s" % (m, (" as " + a) if a else ""))
        froms = {}
        for e in es:
            if e[0] == "from":
                froms.setdefault((e[2], e[1]), set()).add((e[3], e[4]))
        for (level, mod), names in sorted(froms.items(), key=lambda kv: (kv[0][0], kv[0][1].lower())):
            items = sorted(names, key=lambda x: x[0].lower())
            parts = ["%s as %s" % (n, a) if a else n for n, a in items]
            stmt = "from %s%s import %s" % ("." * level, mod, ", ".join(parts))
            if len(stmt) > 100:
                stmt = "from %s%s import (\n%s\n)" % ("." * level, mod, "".join("    %s,\n" % p for p in parts).rstrip("\n"))
            lines.extend(stmt.split("\n"))
        blocks.append(lines)
    out = []
    for i, b in enumerate(blocks):
        if i:
            out.append("")
        out.extend(b)
    return out


def optimize_imports(text):
    """Remove unused top-level imports and sort/group the rest. -> (new_text, removed_count)."""
    tree = ast.parse(text)
    lines = text.split("\n")
    used = _used_names(tree)
    star = any(isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names) for n in tree.body)
    runs, cur = [], []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            cur.append(node)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    removed = 0
    for run in reversed(runs):
        first, last = run[0].lineno, _stmt_end(run[-1], lines)
        covered = set()
        for n in run:
            covered.update(range(n.lineno, _stmt_end(n, lines) + 1))
        pure = all((i in covered) or not lines[i - 1].strip() for i in range(first, last + 1))
        entries, keep_entries = [], []
        for n in run:
            for e in _import_entries(n):
                entries.append(e)
                if star or (e[0] == "from" and e[1] == "__future__") or _entry_binding(e) in used or e[3] == "*":
                    keep_entries.append(e)
        removed += len(entries) - len(keep_entries)
        if pure:
            new_lines = _render_import_run(keep_entries)
            lines[first - 1:last] = new_lines
        elif len(entries) != len(keep_entries):
            for n in reversed(run):        # in-place removal, statement by statement
                es = _import_entries(n)
                ke = [e for e in es if e in keep_entries]
                if len(ke) == len(es):
                    continue
                a, b = n.lineno, _stmt_end(n, lines)
                lines[a - 1:b] = _render_import_run(ke) if ke else []
    return "\n".join(lines), removed


def basic_reformat(text):
    lines = [l.rstrip() for l in text.expandtabs(4).split("\n")]
    out, blanks = [], 0
    for l in lines:
        if l:
            blanks = 0
            out.append(l)
        else:
            blanks += 1
            if blanks <= 2:
                out.append(l)
    while out and not out[-1]:
        out.pop()
    return "\n".join(out) + "\n"


def reformat_source(text):
    """black -> autopep8 -> built-in. Returns (text, engine)."""
    try:
        import black
        return black.format_str(text, mode=black.Mode(line_length=SETTINGS.get("margin") or 88)), "black"
    except ImportError:
        pass
    except Exception as exc:
        raise ValueError("black: %s" % exc)
    try:
        import autopep8
        return autopep8.fix_code(text), "autopep8"
    except ImportError:
        pass
    return basic_reformat(text), "built-in"


SURROUND = {
    "if": (["if condition:"], []),
    "if / else": (["if condition:"], ["else:", "    pass"]),
    "for": (["for item in items:"], []),
    "while": (["while condition:"], []),
    "try / except": (["try:"], ["except Exception as exc:", "    raise"]),
    "try / finally": (["try:"], ["finally:", "    pass"]),
    "with": (["with open(path) as f:"], []),
    "def": (["def function():"], []),
    "nuke Undo group": (["_undo = nuke.Undo", "_undo.begin('Undo group')", "try:"], ["finally:", "    _undo.end()"]),
}


def surround_lines(lines, kind):
    head, tail = SURROUND[kind]
    base = min((len(l) - len(l.lstrip()) for l in lines if l.strip()), default=0)
    pad = " " * base
    out = [pad + h for h in head]
    body_indent = "    " if head[-1].endswith(":") else ""
    out += [(body_indent + l) if l.strip() else l for l in lines]
    out += [pad + t for t in tail]
    return out


LIVE_TEMPLATES = {
    "main": "if __name__ == '__main__':\n    $END$",
    "def": "def $END$():\n    pass",
    "defs": "def $END$(self):\n    pass",
    "class": "class $END$(object):\n    def __init__(self):\n        pass",
    "for": "for i in range($END$):\n    pass",
    "fori": "for i, item in enumerate($END$):\n    pass",
    "if": "if $END$:\n    pass",
    "ife": "if $END$:\n    pass\nelse:\n    pass",
    "try": "try:\n    $END$\nexcept Exception as exc:\n    raise",
    "with": "with open($END$) as f:\n    pass",
    "prn": "print($END$)",
    "prop": "@property\ndef $END$(self):\n    return self._",
    "log": "import logging\nlog = logging.getLogger(__name__)\n$END$",
    "nsel": "for n in nuke.selectedNodes():\n    $END$",
    "nall": "for n in nuke.allNodes():\n    $END$",
    "nread": "for n in nuke.allNodes('Read'):\n    $END$",
    "nwrite": "for n in nuke.allNodes('Write'):\n    $END$",
    "ntn": "nuke.toNode('$END$')",
    "ncn": "n = nuke.createNode('$END$')",
    "nundo": "_undo = nuke.Undo\n_undo.begin('$END$')\ntry:\n    pass\nfinally:\n    _undo.end()",
    "ncb": "def callback():\n    $END$\n\nnuke.addOnScriptLoad(callback)",
    "nkc": "def on_knob_changed():\n    n, k = nuke.thisNode(), nuke.thisKnob()\n    $END$\n\nnuke.addKnobChanged(on_knob_changed, nodeClass='Read')",
    "nprog": "task = nuke.ProgressTask('$END$')\nfor i in range(100):\n    if task.isCancelled():\n        break\n    task.setProgress(i)\n    task.setMessage('step %d' % i)",
}


def expand_template(key, indent=""):
    """-> (text, cursor_offset_from_start) or None."""
    tpl = LIVE_TEMPLATES.get(key)
    if tpl is None:
        return None
    text = ("\n" + indent).join(tpl.split("\n"))
    idx = text.find("$END$")
    if idx < 0:
        return text, len(text)
    return text.replace("$END$", "", 1), idx


def fuzzy_score(query, text):
    """Lower is better; None = no match."""
    q, t = query.lower(), text.lower()
    if not q:
        return 0
    i = t.find(q)
    if i >= 0:
        return i + (0 if i == 0 or not t[i - 1].isalnum() else 5)
    pos, first = 0, None
    for ch in q:
        pos = t.find(ch, pos)
        if pos < 0:
            return None
        first = pos if first is None else first
        pos += 1
    return 1000 + pos - (first or 0)


def dotted_expression_at(line, col):
    """The dotted name ending at the word under *col*: 'nuke.toNode' for the cursor in 'toNode'."""
    for m in re.finditer(r"[A-Za-z_]\w*(?:\s*\.\s*[A-Za-z_]\w*)*", line):
        if m.start() <= col <= m.end():
            expr = ""
            for part in re.finditer(r"[A-Za-z_]\w*", m.group()):
                expr = m.group()[:part.end()].replace(" ", "")
                if m.start() + part.end() >= col:
                    break
            return expr
    return ""


def quick_doc(obj, name):
    """Plain-text documentation for an object."""
    head = name
    try:
        head += str(inspect.signature(obj))
    except (TypeError, ValueError):
        pass
    doc = inspect.getdoc(obj) or "(no documentation)"
    kind = type(obj).__name__
    text = "%s   <%s>\n\n%s" % (head, kind, doc)
    return text if len(text) < 2500 else text[:2500] + "\n..."


def locate_object(obj):
    """-> (path, line) of an object's Python source, or None."""
    try:
        target = obj
        if not (inspect.ismodule(obj) or inspect.isclass(obj) or inspect.isroutine(obj) or inspect.isframe(obj)):
            target = type(obj)
        path = inspect.getsourcefile(target)
        if not path or not os.path.isfile(path):
            return None
        try:
            line = inspect.getsourcelines(target)[1]
        except (OSError, TypeError):
            line = 1
        return path, max(1, line)
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Project file helpers
# --------------------------------------------------------------------------- #
EXCLUDE_DIRS = {"__pycache__", "node_modules", "venv", "build", "dist", "site-packages"}


def iter_files(root, masks=("*.py",), limit=20000):
    n = 0
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if d not in EXCLUDE_DIRS and not d.startswith("."))
        for fn in sorted(fns):
            if any(fnmatch.fnmatch(fn, m) for m in masks):
                yield os.path.join(dp, fn)
                n += 1
                if n >= limit:
                    return


def make_pattern(query, regex=False, case=False, whole=False):
    src = query if regex else re.escape(query)
    if whole:
        src = r"\b(?:%s)\b" % src
    return re.compile(src, 0 if case else re.IGNORECASE)


def search_text(root, pattern, masks=("*.py",), limit=5000, overrides=None):
    """-> list[(path, line, col, text)]"""
    overrides = overrides or {}
    res = []
    files = list(iter_files(root, masks))
    for p in overrides:
        if p not in files and os.path.normcase(p).startswith(os.path.normcase(root)):
            files.append(p)
    for path in files:
        try:
            if path in overrides:
                text = overrides[path]
            else:
                if os.path.getsize(path) > 2 * 1024 * 1024:
                    continue
                text = read_text(path)
        except Exception:
            continue
        for i, line in enumerate(text.split("\n")):
            m = pattern.search(line)
            if m:
                res.append((path, i + 1, m.start(), line.strip()[:240]))
                if len(res) >= limit:
                    return res
    return res


def find_definitions(root, name, overrides=None):
    """Regex-based declaration search -> list[(path, line)]"""
    rx = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+%s\b|^%s\s*(?::[^=]+)?=" % (re.escape(name), re.escape(name)))
    out = []
    overrides = overrides or {}
    files = list(iter_files(root))
    for p in overrides:
        if p not in files:
            files.append(p)
    for path in files:
        try:
            text = overrides[path] if path in overrides else read_text(path)
        except Exception:
            continue
        for i, line in enumerate(text.split("\n")):
            if rx.match(line):
                out.append((path, i + 1))
    return out


def scan_symbols(root, limit=4000):
    rx = re.compile(r"^\s*(?:async\s+)?(def|class)\s+(\w+)")
    out = []
    for n, path in enumerate(iter_files(root, limit=limit)):
        try:
            text = read_text(path)
        except Exception:
            continue
        for i, line in enumerate(text.split("\n")):
            m = rx.match(line)
            if m:
                out.append((m.group(2), m.group(1), path, i + 1))
    return out


# --------------------------------------------------------------------------- #
# Debugger core (bdb based; UI supplies stop_cb)
# --------------------------------------------------------------------------- #
class Debugger(bdb.Bdb):
    """stop_cb(frame) must return one of: resume, over, into, out, stop."""

    def __init__(self, stop_cb, is_known):
        bdb.Bdb.__init__(self)
        self.stop_cb = stop_cb
        self.is_known = is_known
        self._first = True

    def user_line(self, frame):
        if self._first:                 # bdb stops on the very first line: run on to breakpoints
            self._first = False
            self.set_continue()
            return
        fn = self.canonic(frame.f_code.co_filename)
        if fn == THIS_FILE or not self.is_known(fn):
            self.set_return(frame)      # foreign frame: run until it returns
            return
        act = self.stop_cb(frame)
        if act == "over":
            self.set_next(frame)
        elif act == "into":
            self.set_step()
        elif act == "out":
            self.set_return(frame)
        elif act == "stop":
            self.set_quit()
        else:
            self.set_continue()


def register_source(label, text):
    """Make linecache aware of unsaved editor text (tracebacks + breakpoints need it)."""
    linecache.cache[label] = (len(text), None, text.splitlines(True) or ["\n"], label)


# =========================================================================== #
#  EDITOR WIDGETS
# =========================================================================== #
def _fmt(color, bold=False, italic=False):
    f = QtGui.QTextCharFormat()
    f.setForeground(QtGui.QBrush(QColor(color)))
    if bold:
        f.setFontWeight(QtGui.QFont.Bold)
    f.setFontItalic(italic)
    return f


class PyHighlighter(QtGui.QSyntaxHighlighter):
    TRIPLE = re.compile(r'"""|\'\'\'')

    def __init__(self, doc):
        super().__init__(doc)
        self.kw = re.compile(r"\b(?:%s)\b" % "|".join(keyword.kwlist))
        self.bi = re.compile(r"\b(?:%s)\b" % "|".join(
            n for n in dir(builtins) if not n.startswith("_") and n[0].islower()))
        self.rebuild(rehighlight=False)

    def rebuild(self, rehighlight=True):
        self.rules = [
            (self.bi, _fmt(T["builtin"])),
            (self.kw, _fmt(T["keyword"], bold=True)),
            (re.compile(r"\b(?:self|cls)\b"), _fmt(T["selfc"], italic=True)),
            (re.compile(r"\b(?:True|False|None)\b"), _fmt(T["keyword"])),
            (re.compile(r"\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b|\b0[xX][0-9a-fA-F]+\b"), _fmt(T["number"])),
            (re.compile(r"(?<=def )\w+|(?<=class )\w+"), _fmt(T["defname"], bold=True)),
            (re.compile(r"^\s*@[\w\.]+"), _fmt(T["deco"])),
            (re.compile(r"\"[^\"\\\n]*(?:\\.[^\"\\\n]*)*\"|'[^'\\\n]*(?:\\.[^'\\\n]*)*'"), _fmt(T["string"])),
            (re.compile(r"#[^\n]*"), _fmt(T["comment"], italic=True)),
        ]
        self.ml_fmt = _fmt(T["string"])
        if rehighlight:
            self.rehighlight()

    def highlightBlock(self, text):
        for rx, fmt in self.rules:
            for m in rx.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)
        state = self.previousBlockState()
        state = state if state in (1, 2) else 0
        pos, start = 0, 0
        while True:
            if state == 0:
                m = self.TRIPLE.search(text, pos)
                if not m:
                    break
                state = 1 if m.group() == '"""' else 2
                start, pos = m.start(), m.end()
            delim = '"""' if state == 1 else "'''"
            end = text.find(delim, pos)
            if end == -1:
                self.setFormat(start, len(text) - start, self.ml_fmt)
                break
            self.setFormat(start, end + 3 - start, self.ml_fmt)
            pos, state = end + 3, 0
        self.setCurrentBlockState(state)


class BlockData(QtGui.QTextBlockUserData):
    """Per-line state that travels with the line: fold / bookmark / breakpoint."""

    def __init__(self):
        super().__init__()
        self.folded = False
        self.bookmark = False
        self.bp = False


def bdata(block, create=False):
    d = block.userData()
    if d is not None and not isinstance(d, BlockData):
        d = None
    if d is None and create:
        d = BlockData()
        block.setUserData(d)
    return d


class Gutter(QtWidgets.QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.ed = editor

    def sizeHint(self):
        return QtCore.QSize(self.ed.gutter_width(), 0)

    def paintEvent(self, event):
        self.ed.paint_gutter(event)

    def mousePressEvent(self, event):
        self.ed.gutter_click(event.pos())


OPENERS = {"(": ")", "[": "]", "{": "}"}
CLOSERS = {v: k for k, v in OPENERS.items()}
BLOCK_OPEN = re.compile(r"^\s*(?:async\s+)?(?:def|class|if|elif|else|for|while|try|except|finally|with)\b.*:\s*(?:#.*)?$")
KEYWORD_SET = set(keyword.kwlist)
_IMPORT_CACHE = {}
SAFE_CALL_NAMES = {"selectedNode", "selectedNodes", "thisNode", "thisGroup", "thisKnob", "root", "activeViewer",
                   "toNode", "allNodes", "input", "Class", "knobs", "name", "fullName", "node", "dependencies",
                   "dependent", "inputs", "format", "value", "getValue", "x", "y", "xpos", "ypos"}


def _module_names():
    if "names" not in _IMPORT_CACHE:
        names = set(sys.builtin_module_names)
        try:
            names |= {m.name for m in pkgutil.iter_modules()}
        except Exception:
            pass
        _IMPORT_CACHE["names"] = sorted(n for n in names if not n.startswith("_"))
    return _IMPORT_CACHE["names"]


class CodeEditor(QtWidgets.QPlainTextEdit):
    problemsChanged = Signal()
    outlineChanged = Signal()
    gotoDeclaration = Signal()
    breakpointToggled = Signal(int, bool)          # 1-based line, enabled
    MARK_W, FOLD_W = 16, 14

    def __init__(self, ns_getter, parent=None):
        super().__init__(parent)
        self.path = None
        self.title = "untitled"
        self.get_ns = ns_getter
        self.problems = []
        self.symbols = []
        self._problem_lines = {}
        self.debug_line = -1
        self._sel_cache = None

        self.highlighter = PyHighlighter(self.document())
        self.gutter = Gutter(self)
        self.apply_settings()

        self.blockCountChanged.connect(self._update_margin)
        self.updateRequest.connect(self._update_area)
        self.cursorPositionChanged.connect(self.refresh_selections)
        self._update_margin()

        # completion
        self.model = QtCore.QStringListModel(self)
        self.completer = QtWidgets.QCompleter(self)
        self.completer.setWidget(self)
        self.completer.setModel(self.model)
        self.completer.setCompletionMode(QtWidgets.QCompleter.PopupCompletion)
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.activated.connect(self._insert_completion)

        # background inspections
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(600)
        self._timer.timeout.connect(self.run_analysis)
        self.textChanged.connect(self._timer.start)
        self.refresh_selections()

    # ------------------------------------------------------------------ #
    # settings / theme
    # ------------------------------------------------------------------ #
    def apply_settings(self):
        fam = SETTINGS.get("font_family")
        font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        if fam:
            font.setFamily(fam)
        font.setPointSize(int(SETTINGS.get("font_size")))
        self.setFont(font)
        self.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth if SETTINGS.get("wrap")
                             else QtWidgets.QPlainTextEdit.NoWrap)
        self.setTabStopDistance(self.tab_width() * _char_width(self.fontMetrics()))
        opt = self.document().defaultTextOption()
        flags = opt.flags()
        ws = QtGui.QTextOption.ShowTabsAndSpaces
        if bool(flags & ws) != bool(SETTINGS.get("whitespace")):
            flags = flags ^ ws
        opt.setFlags(flags)
        self.document().setDefaultTextOption(opt)
        self.setStyleSheet("QPlainTextEdit { background:%s; color:%s; border:0; "
                           "selection-background-color:%s; }" % (T["bg"], T["fg"], T["sel"]))
        self.highlighter.rebuild(rehighlight=False)
        self.highlighter.rehighlight()
        self._update_margin()
        self.gutter.update()
        self.refresh_selections()

    def tab_width(self):
        return int(SETTINGS.get("tab_width") or 4)

    @property
    def indent_str(self):
        return " " * self.tab_width()

    def run_label(self):
        return self.path if self.path else "<%s>" % self.title

    # ------------------------------------------------------------------ #
    # gutter
    # ------------------------------------------------------------------ #
    def gutter_width(self):
        digits = max(3, len(str(max(1, self.blockCount()))))
        return self.MARK_W + _char_width(self.fontMetrics()) * digits + 10 + self.FOLD_W

    def _update_margin(self, *_):
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _update_area(self, rect, dy):
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_margin()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.gutter.setGeometry(QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))

    def is_fold_start(self, block):
        if not BLOCK_OPEN.match(block.text()):
            return False
        ind = self._indent(block.text())
        b = block.next()
        while b.isValid():
            t = b.text()
            if t.strip():
                return self._indent(t) > ind
            b = b.next()
        return False

    @staticmethod
    def _indent(text):
        return len(text) - len(text.lstrip())

    def fold_end(self, block):
        """Last block of the fold region that starts at *block* (or None)."""
        if not self.is_fold_start(block):
            return None
        ind, last, b = self._indent(block.text()), None, block.next()
        while b.isValid():
            t = b.text()
            if t.strip():
                if self._indent(t) <= ind:
                    break
                last = b
            b = b.next()
        return last

    def paint_gutter(self, event):
        p = QtGui.QPainter(self.gutter)
        p.fillRect(event.rect(), QColor(T["gutter"]))
        block = self.firstVisibleBlock()
        n = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        cur = self.textCursor().blockNumber()
        fh = self.fontMetrics().height()
        w = self.gutter.width()
        sev_col = {"error": T["err"], "warning": T["warn"], "weak": T["weak"]}
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                d = bdata(block)
                p.setPen(QColor(T["gutter_cur"] if n == cur else T["gutter_fg"]))
                p.drawText(self.MARK_W, top, w - self.MARK_W - self.FOLD_W - 4, fh, Qt.AlignRight, str(n + 1))
                cy = top + fh // 2
                p.setPen(Qt.NoPen)
                if d is not None and d.bp:
                    p.setBrush(QColor("#c75450"))
                    p.drawEllipse(QPoint(self.MARK_W // 2, cy), 5, 5)
                elif d is not None and d.bookmark:
                    p.setBrush(QColor("#e0a030"))
                    p.drawRoundedRect(QRect(3, cy - 5, 10, 10), 2, 2)
                else:
                    sev = self._problem_lines.get(n + 1)
                    if sev:
                        p.setBrush(QColor(sev_col[sev]))
                        p.drawRect(QRect(5, cy - 3, 6, 6))
                if n == self.debug_line:
                    p.setBrush(QColor("#4b8bd8"))
                    p.drawPolygon(QtGui.QPolygonF([QPointF(2, cy - 5), QPointF(13, cy), QPointF(2, cy + 5)]))
                if self.is_fold_start(block):
                    folded = d is not None and d.folded
                    p.setBrush(QColor(T["gutter_fg"]))
                    x = w - self.FOLD_W + 3
                    if folded:
                        poly = [QPointF(x, cy - 4), QPointF(x + 6, cy), QPointF(x, cy + 4)]
                    else:
                        poly = [QPointF(x - 1, cy - 3), QPointF(x + 7, cy - 3), QPointF(x + 3, cy + 4)]
                    p.drawPolygon(QtGui.QPolygonF(poly))
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            n += 1

    def gutter_click(self, pos):
        c = self.cursorForPosition(QPoint(2, pos.y()))
        block = c.block()
        if pos.x() < self.MARK_W:
            self.toggle_breakpoint(block)
        elif pos.x() > self.gutter.width() - self.FOLD_W:
            self.toggle_fold(block)
        else:
            c.select(QtGui.QTextCursor.LineUnderCursor)
            self.setTextCursor(c)

    # ------------------------------------------------------------------ #
    # breakpoints / bookmarks
    # ------------------------------------------------------------------ #
    def toggle_breakpoint(self, block=None):
        block = block or self.textCursor().block()
        d = bdata(block, True)
        d.bp = not d.bp
        self.gutter.update()
        self.breakpointToggled.emit(block.blockNumber() + 1, d.bp)

    def toggle_bookmark(self, block=None):
        block = block or self.textCursor().block()
        d = bdata(block, True)
        d.bookmark = not d.bookmark
        self.gutter.update()

    def _flagged_lines(self, attr):
        out, b = [], self.document().firstBlock()
        while b.isValid():
            d = bdata(b)
            if d is not None and getattr(d, attr, False):
                out.append(b.blockNumber() + 1)
            b = b.next()
        return out

    def breakpoint_lines(self):
        return self._flagged_lines("bp")

    def bookmark_lines(self):
        return self._flagged_lines("bookmark")

    def set_debug_line(self, line0):
        self.debug_line = line0
        self.gutter.update()
        self.refresh_selections()

    # ------------------------------------------------------------------ #
    # folding
    # ------------------------------------------------------------------ #
    def _set_region_visible(self, start, end, visible):
        endn = end.blockNumber()
        b = start.next()
        while b.isValid() and b.blockNumber() <= endn:
            b.setVisible(visible)
            b.setLineCount(1 if visible else 0)
            if visible:
                d = bdata(b)
                if d is not None and d.folded:            # keep nested folds collapsed
                    e2 = self.fold_end(b)
                    if e2 is not None:
                        b = b.next()
                        while b.isValid() and b.blockNumber() <= e2.blockNumber():
                            b.setVisible(False)
                            b.setLineCount(0)
                            b = b.next()
                        continue
            b = b.next()
        self._relayout(start, end)

    def _relayout(self, start, end):
        doc = self.document()
        doc.markContentsDirty(start.position(), end.position() + end.length() - start.position())
        lay = doc.documentLayout()
        if hasattr(lay, "requestUpdate"):
            lay.requestUpdate()
        self.viewport().update()
        self.gutter.update()
        self._update_margin()

    def toggle_fold(self, block):
        end = self.fold_end(block)
        if end is None:
            return
        d = bdata(block, True)
        d.folded = not d.folded
        self._set_region_visible(block, end, not d.folded)
        if d.folded:
            c = self.textCursor()
            if c.block().blockNumber() > block.blockNumber() and c.block().blockNumber() <= end.blockNumber():
                c.setPosition(block.position() + len(block.text()))
                self.setTextCursor(c)

    def fold_all(self):
        b = self.document().lastBlock()
        while b.isValid():
            end = self.fold_end(b)
            if end is not None:
                d = bdata(b, True)
                d.folded = True
                self._set_region_visible(b, end, False)
            b = b.previous()

    def unfold_all(self):
        b = self.document().firstBlock()
        while b.isValid():
            d = bdata(b)
            if d is not None:
                d.folded = False
            b.setVisible(True)
            b.setLineCount(1)
            b = b.next()
        doc = self.document()
        doc.markContentsDirty(0, doc.characterCount())
        self.viewport().update()
        self.gutter.update()

    def reveal_line(self, line0):
        """Unfold whatever hides 0-based *line0*."""
        block = self.document().findBlockByNumber(line0)
        if not block.isValid() or block.isVisible():
            return
        b = block.previous()
        while b.isValid():
            d = bdata(b)
            if d is not None and d.folded:
                end = self.fold_end(b)
                if end is not None and end.blockNumber() >= line0:
                    d.folded = False
                    self._set_region_visible(b, end, True)
            b = b.previous()

    # ------------------------------------------------------------------ #
    # text helpers
    # ------------------------------------------------------------------ #
    def _ch(self, pos):
        if pos < 0 or pos >= self.document().characterCount() - 1:
            return ""
        ch = self.document().characterAt(pos)
        return "" if ch in ("\x00", "\u2029") else ch

    def word_at(self, pos=None):
        """-> (word, start_pos, end_pos) at pos (or at the cursor)."""
        c = self.textCursor()
        if pos is not None:
            c.setPosition(pos)
        block = c.block()
        col = c.positionInBlock()
        for m in re.finditer(r"\w+", block.text()):
            if m.start() <= col <= m.end():
                return m.group(), block.position() + m.start(), block.position() + m.end()
        return "", c.position(), c.position()

    def _line_range(self):
        c = self.textCursor()
        s, e = c.selectionStart(), c.selectionEnd()
        doc = self.document()
        b1, b2 = doc.findBlock(s), doc.findBlock(e)
        if e > s and b2.position() == e:
            b2 = b2.previous()
        return b1.blockNumber(), b2.blockNumber()

    def replace_lines(self, a, b, new_lines):
        """Replace whole lines a..b (0-based, inclusive) with new_lines."""
        doc = self.document()
        first, last = doc.findBlockByNumber(a), doc.findBlockByNumber(b)
        c = QtGui.QTextCursor(doc)
        c.setPosition(first.position())
        c.setPosition(last.position() + len(last.text()), QtGui.QTextCursor.KeepAnchor)
        c.insertText("\n".join(new_lines))

    def indent_lines(self, add=True):
        a, b = self._line_range()
        cur = self.textCursor()
        cur.beginEditBlock()
        for n in range(a, b + 1):
            block = self.document().findBlockByNumber(n)
            c = QtGui.QTextCursor(block)
            if add:
                if block.text().strip():
                    c.insertText(self.indent_str)
            else:
                t = block.text()
                k = min(self.tab_width(), len(t) - len(t.lstrip(" ")))
                if k:
                    c.movePosition(QtGui.QTextCursor.Right, QtGui.QTextCursor.KeepAnchor, k)
                    c.removeSelectedText()
        cur.endEditBlock()

    def toggle_comment(self):
        a, b = self._line_range()
        doc = self.document()
        blocks = [doc.findBlockByNumber(n) for n in range(a, b + 1)]
        nonempty = [l for l in blocks if l.text().strip()]
        if not nonempty:
            return
        all_c = all(l.text().lstrip().startswith("#") for l in nonempty)
        min_ind = min(self._indent(l.text()) for l in nonempty)
        cur = self.textCursor()
        cur.beginEditBlock()
        for block in nonempty:
            c = QtGui.QTextCursor(block)
            text = block.text()
            if all_c:
                idx = text.index("#")
                c.setPosition(block.position() + idx)
                k = 2 if text[idx:idx + 2] == "# " else 1
                c.movePosition(QtGui.QTextCursor.Right, QtGui.QTextCursor.KeepAnchor, k)
                c.removeSelectedText()
            else:
                c.setPosition(block.position() + min_ind)
                c.insertText("# ")
        cur.endEditBlock()

    def duplicate_line(self):
        c = self.textCursor()
        if c.hasSelection():
            txt = c.selectedText()
            c.setPosition(c.selectionEnd())
            c.insertText(txt)
        else:
            line = c.block().text()
            c.movePosition(QtGui.QTextCursor.EndOfBlock)
            c.insertText("\n" + line)
        self.setTextCursor(c)

    def delete_line(self):
        a, b = self._line_range()
        doc = self.document()
        first, last = doc.findBlockByNumber(a), doc.findBlockByNumber(b)
        start = first.position()
        end = last.position() + last.length()
        total = doc.characterCount() - 1
        if end > total:
            end = total
            if start > 0:
                start -= 1
        c = QtGui.QTextCursor(doc)
        c.setPosition(start)
        c.setPosition(end, QtGui.QTextCursor.KeepAnchor)
        c.removeSelectedText()

    def move_lines(self, up):
        a, b = self._line_range()
        doc = self.document()
        if (up and a == 0) or (not up and b >= doc.blockCount() - 1):
            return
        mine = [doc.findBlockByNumber(n).text() for n in range(a, b + 1)]
        if up:
            other = doc.findBlockByNumber(a - 1).text()
            new, ra, rb = mine + [other], a - 1, b
            sel_a, sel_b = a - 1, b - 1
        else:
            other = doc.findBlockByNumber(b + 1).text()
            new, ra, rb = [other] + mine, a, b + 1
            sel_a, sel_b = a + 1, b + 1
        cur = self.textCursor()
        cur.beginEditBlock()
        self.replace_lines(ra, rb, new)
        cur.endEditBlock()
        c = QtGui.QTextCursor(doc)
        c.setPosition(doc.findBlockByNumber(sel_a).position())
        lb = doc.findBlockByNumber(sel_b)
        c.setPosition(lb.position() + len(lb.text()), QtGui.QTextCursor.KeepAnchor)
        self.setTextCursor(c)

    def join_lines(self):
        a, b = self._line_range()
        doc = self.document()
        if b == a:
            b = min(a + 1, doc.blockCount() - 1)
        if b == a:
            return
        parts = [doc.findBlockByNumber(a).text().rstrip()]
        parts += [doc.findBlockByNumber(n).text().strip() for n in range(a + 1, b + 1)]
        cur = self.textCursor()
        cur.beginEditBlock()
        self.replace_lines(a, b, [" ".join(p for p in parts if p)])
        cur.endEditBlock()

    def extend_selection(self):
        c = self.textCursor()
        s, e = c.selectionStart(), c.selectionEnd()
        block = self.document().findBlock(s)
        text, base = block.text(), block.position()
        cands = []
        w, ws, we = self.word_at(s)
        if w:
            cands.append((ws, we))
        stripped = text.strip()
        if stripped:
            lo = base + text.index(stripped)
            cands.append((lo, lo + len(stripped)))
        cands.append((base, base + len(text)))
        cands.append((0, self.document().characterCount() - 1))
        for a, b in cands:
            if a <= s and e <= b and (a, b) != (s, e):
                c.setPosition(a)
                c.setPosition(b, QtGui.QTextCursor.KeepAnchor)
                self.setTextCursor(c)
                return

    def toggle_case(self):
        c = self.textCursor()
        if not c.hasSelection():
            w, ws, we = self.word_at()
            if not w:
                return
            c.setPosition(ws)
            c.setPosition(we, QtGui.QTextCursor.KeepAnchor)
        t = c.selectedText()
        c.insertText(t.upper() if t.islower() else t.lower())
        self.setTextCursor(c)

    def surround_with(self, kind):
        a, b = self._line_range()
        doc = self.document()
        lines = [doc.findBlockByNumber(n).text() for n in range(a, b + 1)]
        new = surround_lines(lines, kind)
        cur = self.textCursor()
        cur.beginEditBlock()
        self.replace_lines(a, b, new)
        cur.endEditBlock()

    def replace_all_text(self, new_text):
        """Replace the document but keep undo history and (roughly) the caret."""
        if new_text == self.toPlainText():
            return False
        line = self.textCursor().blockNumber()
        c = QtGui.QTextCursor(self.document())
        c.beginEditBlock()
        c.select(QtGui.QTextCursor.Document)
        c.insertText(new_text)
        c.endEditBlock()
        self.goto_line(line + 1)
        return True

    def goto_line(self, line, col=0):
        self.reveal_line(line - 1)
        block = self.document().findBlockByNumber(max(0, line - 1))
        if not block.isValid():
            block = self.document().lastBlock()
        c = self.textCursor()
        c.setPosition(block.position() + min(col, len(block.text())))
        self.setTextCursor(c)
        self.centerCursor()
        self.setFocus()

    # ------------------------------------------------------------------ #
    # inspections
    # ------------------------------------------------------------------ #
    def run_analysis(self):
        text = self.toPlainText()
        if len(text) > 500000:
            self.problems = []
        else:
            try:
                known = list(self.get_ns().keys())
            except Exception:
                known = []
            self.problems = analyze(text, known, int(SETTINGS.get("margin") or 0))
            syms = outline(text)
            if syms is not None:
                self.symbols = syms
                self.outlineChanged.emit()
        rank = {"error": 3, "warning": 2, "weak": 1}
        lines = {}
        for p in self.problems:
            if rank[p.sev] > rank.get(lines.get(p.line), 0):
                lines[p.line] = p.sev
        self._problem_lines = lines
        self.gutter.update()
        self.refresh_selections()
        self.problemsChanged.emit()

    def problem_at(self, line, col):
        hits = [p for p in self.problems if p.line == line and p.col <= col <= max(p.end, p.col + 1)]
        return hits or [p for p in self.problems if p.line == line]

    def viewportEvent(self, e):
        if e.type() == QtCore.QEvent.ToolTip:
            c = self.cursorForPosition(e.pos())
            msgs = [p.msg for p in self.problem_at(c.blockNumber() + 1, c.positionInBlock())]
            if msgs:
                QtWidgets.QToolTip.showText(e.globalPos(), "\n".join(msgs), self)
            else:
                QtWidgets.QToolTip.hideText()
            return True
        return super().viewportEvent(e)

    # ------------------------------------------------------------------ #
    # extra selections (current line, occurrences, brackets, debug, squiggles)
    # ------------------------------------------------------------------ #
    def _sel(self, start, end, bg=None, underline=None, full=False):
        s = QtWidgets.QTextEdit.ExtraSelection()
        fmt = QtGui.QTextCharFormat()
        if bg:
            fmt.setBackground(QtGui.QBrush(QColor(bg)))
        if underline:
            fmt.setUnderlineStyle(QtGui.QTextCharFormat.WaveUnderline)
            fmt.setUnderlineColor(QColor(underline))
        if full:
            fmt.setProperty(QtGui.QTextFormat.FullWidthSelection, True)
        s.format = fmt
        c = QtGui.QTextCursor(self.document())
        c.setPosition(start)
        c.setPosition(end, QtGui.QTextCursor.KeepAnchor)
        s.cursor = c
        return s

    def refresh_selections(self):
        doc = self.document()
        sels = []
        cur = self.textCursor()
        block = cur.block()
        sels.append(self._sel(block.position(), block.position(), T["line"], full=True))

        for b in ([doc.findBlockByNumber(self.debug_line)] if self.debug_line >= 0 else []):
            if b.isValid():
                sels.append(self._sel(b.position(), b.position(), T["debug"], full=True))

        text = None
        # occurrences of the word under the caret
        if not cur.hasSelection() and doc.characterCount() < 300000:
            w, ws, we = self.word_at()
            if len(w) >= 2 and w not in KEYWORD_SET:
                text = self.toPlainText()
                n = 0
                for m in re.finditer(r"\b%s\b" % re.escape(w), text):
                    if m.start() != ws:
                        sels.append(self._sel(m.start(), m.end(), T["occur"]))
                    n += 1
                    if n > 300:
                        break

        # bracket matching
        pos = cur.position()
        for p in (pos - 1, pos):
            ch = self._ch(p)
            if ch in OPENERS or ch in CLOSERS:
                text = text if text is not None else self.toPlainText()
                q = self._find_partner(text, p, ch)
                if q is None:
                    sels.append(self._sel(p, p + 1, T["err"]))
                else:
                    sels.append(self._sel(p, p + 1, T["match"]))
                    sels.append(self._sel(q, q + 1, T["match"]))
                break

        # squiggles
        col = {"error": T["err"], "warning": T["warn"], "weak": T["weak"]}
        for pr in self.problems[:400]:
            blk = doc.findBlockByNumber(pr.line - 1)
            if not blk.isValid():
                continue
            a = blk.position() + min(pr.col, len(blk.text()))
            z = blk.position() + min(max(pr.end, pr.col + 1), len(blk.text()))
            if z > a:
                sels.append(self._sel(a, z, underline=col[pr.sev]))
        self.setExtraSelections(sels)
        self.gutter.update()

    @staticmethod
    def _find_partner(text, pos, ch):
        if ch in OPENERS:
            want, step, depth, i = OPENERS[ch], 1, 0, pos
        else:
            want, step, depth, i = CLOSERS[ch], -1, 0, pos
        limit = 20000
        n = len(text)
        while 0 <= i < n and limit:
            c = text[i]
            if c == ch:
                depth += 1
            elif c == want:
                depth -= 1
                if depth == 0:
                    return i
            i += step
            limit -= 1
        return None

    def paintEvent(self, event):
        super().paintEvent(event)
        m = int(SETTINGS.get("margin") or 0)
        if m:
            x = round(_char_width(self.fontMetrics()) * m + self.document().documentMargin()
                      + self.contentOffset().x())
            p = QtGui.QPainter(self.viewport())
            p.setPen(QColor(T["border"]))
            p.drawLine(x, event.rect().top(), x, event.rect().bottom())

    # ------------------------------------------------------------------ #
    # completion
    # ------------------------------------------------------------------ #
    def _collect_completions(self, force):
        c = self.textCursor()
        line = c.block().text()[:c.positionInBlock()]
        ns = self.get_ns()
        m = re.match(r"^\s*from\s+([\w\.]+)\s+import\s+(?:[\w\s,]*,\s*)?(\w*)$", line)
        if m:
            try:
                mod = importlib.import_module(m.group(1))
                return [n for n in dir(mod) if not n.startswith("_")], m.group(2)
            except Exception:
                return [], m.group(2)
        m = re.match(r"^\s*(?:import|from)\s+([\w\.]*)$", line)
        if m:
            return _module_names(), m.group(1).split(".")[-1]
        try:                                   # jedi gives the best answers when present
            import jedi
            script = jedi.Interpreter(self.toPlainText(), [ns])
            comps = script.complete(c.blockNumber() + 1, c.positionInBlock())
            if comps:
                return [x.name for x in comps], (re.search(r"(\w*)$", line).group(1))
        except Exception:
            pass
        m = re.search(r"([A-Za-z_][\w\.]*(?:\([^()]*\))?[\w\.]*)\.(\w*)$", line)
        if m:
            expr = m.group(1)
            try:
                # only evaluate plain dotted names and a whitelist of side-effect-free calls
                ok = re.match(r"^[\w\.]+(?:\(\s*(?:'[^']*'|\"[^\"]*\")?\s*\)[\w\.]*)*$", expr)
                if not ok or any(cn not in SAFE_CALL_NAMES for cn in re.findall(r"(\w+)\s*\(", expr)):
                    raise ValueError("unsafe expression")
                names = [n for n in dir(eval(expr, ns)) if not n.startswith("__")]
            except Exception:
                names = []
            return names, m.group(2)
        m = re.search(r"(\w+)$", line)
        prefix = m.group(1) if m else ""
        if len(prefix) < 2 and not force:
            return [], prefix
        names = set(keyword.kwlist) | set(dir(builtins)) | set(ns.keys()) | set(LIVE_TEMPLATES)
        if len(self.toPlainText()) < 400000:
            names |= set(re.findall(r"\b[A-Za-z_]\w{2,}\b", self.toPlainText()))
        return list(names), prefix

    def show_completions(self, force=False):
        popup = self.completer.popup()
        names, prefix = self._collect_completions(force)
        if not names:
            popup.hide()
            return
        self.model.setStringList(sorted(set(names), key=lambda s: s.lower()))
        self.completer.setCompletionPrefix(prefix)
        cnt = self.completer.completionCount()
        if cnt == 0 or (cnt == 1 and self.completer.currentCompletion() == prefix):
            popup.hide()
            return
        popup.setCurrentIndex(self.completer.completionModel().index(0, 0))
        rect = self.cursorRect()
        rect.setWidth(popup.sizeHintForColumn(0) + popup.verticalScrollBar().sizeHint().width() + 12)
        self.completer.complete(rect)

    def _insert_completion(self, text):
        prefix = self.completer.completionPrefix()
        c = self.textCursor()
        c.movePosition(QtGui.QTextCursor.Left, QtGui.QTextCursor.KeepAnchor, len(prefix))
        c.insertText(text)
        self.setTextCursor(c)

    # ------------------------------------------------------------------ #
    # keyboard / mouse
    # ------------------------------------------------------------------ #
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and (e.modifiers() & Qt.ControlModifier):
            self.setTextCursor(self.cursorForPosition(e.pos()))
            self.gotoDeclaration.emit()
            return
        super().mousePressEvent(e)

    def _try_template(self):
        c = self.textCursor()
        if c.hasSelection():
            return False
        line = c.block().text()[:c.positionInBlock()]
        m = re.search(r"(?<![\w\.])([A-Za-z_]\w*)$", line)
        if not m or m.group(1) not in LIVE_TEMPLATES:
            return False
        indent = re.match(r"\s*", line).group()
        res = expand_template(m.group(1), indent)
        if not res:
            return False
        text, offset = res
        c.beginEditBlock()
        c.movePosition(QtGui.QTextCursor.Left, QtGui.QTextCursor.KeepAnchor, len(m.group(1)))
        start = c.selectionStart()
        c.insertText(text)
        c.endEditBlock()
        c.setPosition(start + offset)
        self.setTextCursor(c)
        return True

    def keyPressEvent(self, e):
        key, mods = e.key(), e.modifiers()
        popup = self.completer.popup()
        if popup.isVisible() and key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Escape, Qt.Key_Tab, Qt.Key_Backtab):
            e.ignore()
            return
        ctrl = bool(mods & Qt.ControlModifier)
        shift = bool(mods & Qt.ShiftModifier)
        alt = bool(mods & Qt.AltModifier)

        if ctrl and not alt:
            if key == Qt.Key_Space:
                self.show_completions(force=True)
                return
            if key == Qt.Key_Slash:
                self.toggle_comment()
                return
            if key == Qt.Key_D and not shift:
                self.duplicate_line()
                return
            if key == Qt.Key_Y:
                self.delete_line()
                return
            if key == Qt.Key_W and not shift:
                self.extend_selection()
                return
            if key == Qt.Key_U and shift:
                self.toggle_case()
                return
            if key == Qt.Key_J and shift:
                self.join_lines()
                return
        if alt and shift and key in (Qt.Key_Up, Qt.Key_Down):
            self.move_lines(key == Qt.Key_Up)
            return
        if key == Qt.Key_Backtab:
            self.indent_lines(False)
            return
        if key == Qt.Key_Tab and not ctrl:
            c = self.textCursor()
            doc = c.document()
            if c.hasSelection() and doc.findBlock(c.selectionStart()) != doc.findBlock(c.selectionEnd()):
                self.indent_lines(True)
                return
            if self._try_template():
                return
            col = c.positionInBlock()
            c.insertText(" " * (self.tab_width() - col % self.tab_width()))
            self.setTextCursor(c)
            return
        if key in (Qt.Key_Return, Qt.Key_Enter) and not ctrl:
            self._smart_enter()
            return
        if key == Qt.Key_Home and not ctrl:
            c = self.textCursor()
            text = c.block().text()
            first = len(text) - len(text.lstrip())
            target = c.block().position() + (0 if c.positionInBlock() == first and first else first)
            c.setPosition(target, QtGui.QTextCursor.KeepAnchor if shift else QtGui.QTextCursor.MoveAnchor)
            self.setTextCursor(c)
            return
        if key == Qt.Key_Backspace and not mods & (Qt.ControlModifier | Qt.AltModifier):
            if self._smart_backspace():
                return

        txt = e.text()
        if len(txt) == 1 and not ctrl and not alt and self._handle_pairs(txt):
            return

        super().keyPressEvent(e)
        if txt and (txt.isalnum() or txt in "_."):
            self.show_completions()
        elif popup.isVisible() and key not in (Qt.Key_Shift, Qt.Key_Control, Qt.Key_Alt):
            popup.hide()

    def _smart_enter(self):
        c = self.textCursor()
        line = c.block().text()
        indent = re.match(r"\s*", line).group()
        before = line[:c.positionInBlock()]
        prev, nxt = self._ch(c.position() - 1), self._ch(c.position())
        if prev in OPENERS and nxt == OPENERS[prev]:
            c.beginEditBlock()
            c.insertText("\n" + indent + self.indent_str + "\n" + indent)
            c.movePosition(QtGui.QTextCursor.PreviousBlock)
            c.movePosition(QtGui.QTextCursor.EndOfBlock)
            c.endEditBlock()
        else:
            extra = self.indent_str if before.rstrip().endswith((":", "(", "[", "{")) and not before.strip().startswith("#") else ""
            c.insertText("\n" + indent + extra)
        self.setTextCursor(c)
        self.ensureCursorVisible()

    def _smart_backspace(self):
        c = self.textCursor()
        if c.hasSelection():
            return False
        pos = c.position()
        prev, nxt = self._ch(pos - 1), self._ch(pos)
        if prev in OPENERS and nxt == OPENERS[prev] or (prev in "'\"" and prev == nxt and prev):
            c.setPosition(pos - 1)
            c.setPosition(pos + 1, QtGui.QTextCursor.KeepAnchor)
            c.removeSelectedText()
            self.setTextCursor(c)
            return True
        before = c.block().text()[:c.positionInBlock()]
        if before and not before.strip() and len(before) >= 1:
            k = len(before) % self.tab_width() or self.tab_width()
            k = min(k, len(before))
            c.movePosition(QtGui.QTextCursor.Left, QtGui.QTextCursor.KeepAnchor, k)
            c.removeSelectedText()
            self.setTextCursor(c)
            return True
        return False

    def _handle_pairs(self, t):
        c = self.textCursor()
        pos = c.position()
        prev, nxt = self._ch(pos - 1), self._ch(pos)
        if t in OPENERS:
            if c.hasSelection():
                sel = c.selectedText()
                c.insertText(t + sel + OPENERS[t])
                self.setTextCursor(c)
                return True
            if nxt == "" or nxt.isspace() or nxt in ")]},:;":
                c.insertText(t + OPENERS[t])
                c.movePosition(QtGui.QTextCursor.Left)
                self.setTextCursor(c)
                return True
        elif t in CLOSERS:
            if nxt == t:
                c.movePosition(QtGui.QTextCursor.Right)
                self.setTextCursor(c)
                return True
        elif t in ("'", '"'):
            if c.hasSelection():
                c.insertText(t + c.selectedText() + t)
                self.setTextCursor(c)
                return True
            if nxt == t:
                c.movePosition(QtGui.QTextCursor.Right)
                self.setTextCursor(c)
                return True
            prev_blocks = prev != "" and (prev.isalnum() or prev in "'\"\\_")
            if not prev_blocks and (nxt == "" or nxt.isspace() or nxt in ")]},:;"):
                c.insertText(t + t)
                c.movePosition(QtGui.QTextCursor.Left)
                self.setTextCursor(c)
                return True
        return False


# =========================================================================== #
#  PANELS & DIALOGS
# =========================================================================== #
class Console(QtWidgets.QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        font.setPointSize(max(8, int(SETTINGS.get("font_size")) - 1))
        self.setFont(font)
        self.setMaximumBlockCount(20000)

    @staticmethod
    def color(kind):
        return {"out": T["fg"], "err": T["err"], "info": T["comment"],
                "result": T["console_ok"], "prompt": T["defname"]}.get(kind, T["fg"])

    def write(self, text, kind="out"):
        c = self.textCursor()
        c.movePosition(QtGui.QTextCursor.End)
        fmt = QtGui.QTextCharFormat()
        fmt.setForeground(QColor(self.color(kind)))
        c.insertText(text, fmt)
        self.setTextCursor(c)
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())


class Stream(object):
    def __init__(self, fn, kind):
        self.fn, self.kind = fn, kind

    def write(self, s):
        self.fn(s, self.kind)
        return len(s)

    def flush(self):
        pass

    def isatty(self):
        return False


class HistoryLineEdit(QtWidgets.QLineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.history, self.idx = [], 0

    def push(self, text):
        if text.strip() and (not self.history or self.history[-1] != text):
            self.history.append(text)
        self.idx = len(self.history)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Up and self.history:
            self.idx = max(0, self.idx - 1)
            self.setText(self.history[self.idx])
        elif e.key() == Qt.Key_Down and self.history:
            self.idx = min(len(self.history), self.idx + 1)
            self.setText(self.history[self.idx] if self.idx < len(self.history) else "")
        elif e.key() == Qt.Key_Tab:
            self.insert("    ")
        else:
            super().keyPressEvent(e)


# --------------------------------------------------------------------------- #
class FindBar(QtWidgets.QWidget):
    def __init__(self, editor_getter, parent=None):
        super().__init__(parent)
        self.editor = editor_getter
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        self.find = QtWidgets.QLineEdit()
        self.find.setPlaceholderText("Find")
        self.repl = QtWidgets.QLineEdit()
        self.repl.setPlaceholderText("Replace")
        self.regex = QtWidgets.QCheckBox("Regex")
        self.case = QtWidgets.QCheckBox("Case")
        self.whole = QtWidgets.QCheckBox("Word")
        self.status = QtWidgets.QLabel("")
        b_prev, b_next = QtWidgets.QPushButton("Prev"), QtWidgets.QPushButton("Next")
        b_rep, b_all = QtWidgets.QPushButton("Replace"), QtWidgets.QPushButton("All")
        b_close = QtWidgets.QPushButton()
        b_close.setFixedWidth(28)
        b_close.setToolTip("Close (Esc)")
        for b, nm in ((b_prev, "up"), (b_next, "down"), (b_rep, "replace"), (b_all, "replace"), (b_close, "close")):
            use_icon(b, nm)
        for w in (self.find, self.repl, self.regex, self.case, self.whole, b_prev, b_next,
                  b_rep, b_all, self.status, b_close):
            lay.addWidget(w)
        self.find.returnPressed.connect(lambda: self.search(True))
        b_next.clicked.connect(lambda: self.search(True))
        b_prev.clicked.connect(lambda: self.search(False))
        b_rep.clicked.connect(self.replace_one)
        b_all.clicked.connect(self.replace_all)
        b_close.clicked.connect(self._close)
        self.hide()

    def _close(self):
        self.hide()
        ed = self.editor()
        if ed:
            ed.setFocus()

    def open(self, with_replace=False):
        ed = self.editor()
        if ed and ed.textCursor().hasSelection():
            self.find.setText(ed.textCursor().selectedText().replace("\u2029", "\n"))
        self.repl.setVisible(True)
        self.show()
        self.find.setFocus()
        self.find.selectAll()

    def _pattern(self):
        txt = self.find.text()
        if not txt:
            return None
        try:
            pat = make_pattern(txt, self.regex.isChecked(), self.case.isChecked(), self.whole.isChecked())
            self.status.setText("")
            return pat
        except re.error as exc:
            self.status.setText("<span style='color:%s'>%s</span>" % (T["err"], exc))
            return None

    def search(self, forward=True):
        ed, pat = self.editor(), self._pattern()
        if not ed or not pat:
            return False
        text = ed.toPlainText()
        cur = ed.textCursor()
        if forward:
            m = pat.search(text, cur.selectionEnd()) or pat.search(text, 0)
        else:
            allm = list(pat.finditer(text))
            before = [x for x in allm if x.end() <= cur.selectionStart()]
            m = before[-1] if before else (allm[-1] if allm else None)
        if not m:
            self.status.setText("not found")
            return False
        ed.reveal_line(ed.document().findBlock(m.start()).blockNumber())
        c = ed.textCursor()
        c.setPosition(m.start())
        c.setPosition(m.end(), QtGui.QTextCursor.KeepAnchor)
        ed.setTextCursor(c)
        ed.ensureCursorVisible()
        return True

    def _sub(self, pat, text, count=0):
        if self.regex.isChecked():
            return pat.subn(self.repl.text(), text, count=count)
        r = self.repl.text()
        return pat.subn(lambda m: r, text, count=count)

    def replace_one(self):
        ed, pat = self.editor(), self._pattern()
        if not ed or not pat:
            return
        c = ed.textCursor()
        sel = c.selectedText().replace("\u2029", "\n")
        if c.hasSelection() and pat.fullmatch(sel):
            new, _ = self._sub(pat, sel, 1)
            c.insertText(new)
            ed.setTextCursor(c)
        self.search(True)

    def replace_all(self):
        ed, pat = self.editor(), self._pattern()
        if not ed or not pat:
            return
        new, n = self._sub(pat, ed.toPlainText())
        ed.replace_all_text(new)
        self.status.setText("%d replaced" % n)


# --------------------------------------------------------------------------- #
class FileIcons(QtWidgets.QFileIconProvider):
    """Project-tree icons: folders, Python, Nuke scripts, everything else."""
    NK = (".nk", ".nknc", ".gizmo", ".tcl")
    PY = (".py", ".pyw", ".pyi")

    def icon(self, arg):
        try:
            if isinstance(arg, QtCore.QFileInfo):
                if arg.isDir():
                    return icon("folder_fixed")
                name = arg.fileName().lower()
                if name.endswith(self.PY):
                    return icon("file_py")
                if name.endswith(self.NK):
                    return icon("file_nk")
                return icon("file_txt")
            if arg == QtWidgets.QFileIconProvider.Folder:
                return icon("folder_fixed")
            if arg == QtWidgets.QFileIconProvider.File:
                return icon("file_txt")
        except Exception:
            pass
        return super().icon(arg)


class ProjectPanel(QtWidgets.QWidget):
    fileActivated = Signal(str)
    MASKS = ["*.py", "*.pyw", "*.json", "*.txt", "*.md", "*.nk", "*.nknc", "*.gizmo", "*.ui", "*.yml",
             "*.yaml", "*.cfg", "*.ini", "*.toml", "*.tcl", "*.xml", "*.csv", "*.sh", "*.bat"]

    def __init__(self, root, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.label = QtWidgets.QLabel()
        self.label.setObjectName("crumbs")
        self.tree = QtWidgets.QTreeView()
        self.model = QFileSystemModel(self)
        self._icons = FileIcons()
        self.model.setIconProvider(self._icons)
        self.model.setNameFilters(self.MASKS)
        self.model.setNameFilterDisables(False)
        self.tree.setModel(self.model)
        self.tree.setHeaderHidden(True)
        for c in (1, 2, 3):
            self.tree.hideColumn(c)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.doubleClicked.connect(self._open)
        lay.addWidget(self.label)
        lay.addWidget(self.tree)
        self.root = root
        self.set_root(root)

    def set_root(self, root):
        self.root = root
        self.model.setRootPath(root)
        self.tree.setRootIndex(self.model.index(root))
        self.label.setText(os.path.basename(root.rstrip("/\\")) or root)
        self.label.setToolTip(root)

    def _path(self, index=None):
        idx = index if index is not None else self.tree.currentIndex()
        return self.model.filePath(idx) if idx.isValid() else self.root

    def _open(self, index):
        p = self.model.filePath(index)
        if os.path.isfile(p):
            self.fileActivated.emit(p)

    def _dir_of(self, p):
        return p if os.path.isdir(p) else os.path.dirname(p)

    def _menu(self, pos):
        idx = self.tree.indexAt(pos)
        path = self._path(idx) if idx.isValid() else self.root
        m = QtWidgets.QMenu(self)
        items = [("New Python File...", lambda: self._new_file(path)),
                 ("New Folder...", lambda: self._new_folder(path)),
                 ("Rename...", lambda: self._rename(path)),
                 ("Delete", lambda: self._delete(path)),
                 ("Copy Path", lambda: QtWidgets.QApplication.clipboard().setText(path)),
                 ("Reveal in File Manager", lambda: QtGui.QDesktopServices.openUrl(
                     QtCore.QUrl.fromLocalFile(self._dir_of(path))))]
        for text, fn in items:
            a = m.addAction(text)
            a.triggered.connect(lambda checked=False, f=fn: f())
        _exec(m, self.tree.viewport().mapToGlobal(pos))

    def _new_file(self, base):
        name, ok = QtWidgets.QInputDialog.getText(self, "New Python File", "File name:")
        if ok and name.strip():
            name = name.strip()
            if not os.path.splitext(name)[1]:
                name += ".py"
            p = os.path.join(self._dir_of(base), name)
            if not os.path.exists(p):
                write_text(p, "")
            self.fileActivated.emit(p)

    def _new_folder(self, base):
        name, ok = QtWidgets.QInputDialog.getText(self, "New Folder", "Folder name:")
        if ok and name.strip():
            _ensure_dir(os.path.join(self._dir_of(base), name.strip()))

    def _rename(self, path):
        old = os.path.basename(path)
        name, ok = QtWidgets.QInputDialog.getText(self, "Rename", "New name:", text=old)
        if ok and name.strip() and name != old:
            try:
                os.rename(path, os.path.join(os.path.dirname(path), name.strip()))
            except OSError as exc:
                QtWidgets.QMessageBox.warning(self, APP_NAME, str(exc))

    def _delete(self, path):
        B = QtWidgets.QMessageBox
        if path == self.root:
            return
        if B.question(self, APP_NAME, "Delete %s ?" % os.path.basename(path), B.Yes | B.No) != B.Yes:
            return
        try:
            shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
        except OSError as exc:
            B.warning(self, APP_NAME, str(exc))


class StructurePanel(QtWidgets.QTreeWidget):
    gotoLine = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.itemClicked.connect(self._click)

    def set_symbols(self, syms):
        self.clear()
        stack = []
        for s in syms or []:
            label = s.sig if s.kind in ("function", "method") else s.name
            item = QtWidgets.QTreeWidgetItem([label])
            item.setIcon(0, icon("sym_" + s.kind))
            item.setData(0, Qt.UserRole, s.line)
            item.setForeground(0, QtGui.QBrush(QColor(T["defname"] if s.kind == "class" else T["fg"])))
            while stack and stack[-1][0] >= s.depth:
                stack.pop()
            if stack:
                stack[-1][1].addChild(item)
            else:
                self.addTopLevelItem(item)
            stack.append((s.depth, item))
        self.expandAll()

    def _click(self, item, _col):
        line = item.data(0, Qt.UserRole)
        if line:
            self.gotoLine.emit(int(line))


class ProblemsPanel(QtWidgets.QTreeWidget):
    problemActivated = Signal(object, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self._entries = []
        self.itemDoubleClicked.connect(self._activate)

    def refresh(self, editors):
        self.clear()
        self._entries = []
        errs = warns = 0
        col = {"error": T["err"], "warning": T["warn"], "weak": T["weak"]}
        for ed in editors:
            if not ed.problems:
                continue
            parent = QtWidgets.QTreeWidgetItem(["%s  (%d)" % (ed.title, len(ed.problems))])
            parent.setIcon(0, icon(NukeCodeEditor.file_icon_name(ed)))
            self.addTopLevelItem(parent)
            for p in ed.problems:
                errs += p.sev == "error"
                warns += p.sev == "warning"
                it = QtWidgets.QTreeWidgetItem(parent, ["%s    :%d" % (p.msg, p.line)])
                it.setIcon(0, icon(p.sev))
                it.setForeground(0, QtGui.QBrush(QColor(col[p.sev])))
                it.setData(0, Qt.UserRole, len(self._entries))
                self._entries.append((ed, p.line, p.col))
            parent.setExpanded(True)
        return errs, warns

    def _activate(self, item, _col):
        idx = item.data(0, Qt.UserRole)
        if idx is not None:
            ed, line, col = self._entries[int(idx)]
            self.problemActivated.emit(ed, line, col)


class ResultsPanel(QtWidgets.QWidget):
    activated = Signal(str, int, int)

    def __init__(self, root_getter, parent=None):
        super().__init__(parent)
        self.root_getter = root_getter
        self._entries = []
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.label = QtWidgets.QLabel("No results")
        self.label.setObjectName("crumbs")
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderHidden(True)
        lay.addWidget(self.label)
        lay.addWidget(self.tree)
        self.tree.itemDoubleClicked.connect(self._activate)

    def show_results(self, title, results):
        self.tree.clear()
        self._entries = []
        root = self.root_getter()
        groups = {}
        for path, line, col, text in results:
            groups.setdefault(path, []).append((line, col, text))
        for path, hits in groups.items():
            try:
                rel = os.path.relpath(path, root)
            except ValueError:
                rel = path
            parent = QtWidgets.QTreeWidgetItem(["%s  (%d)" % (rel, len(hits))])
            self.tree.addTopLevelItem(parent)
            for line, col, text in hits:
                it = QtWidgets.QTreeWidgetItem(parent, ["%d:  %s" % (line, text)])
                it.setData(0, Qt.UserRole, len(self._entries))
                self._entries.append((path, line, col))
            parent.setExpanded(len(groups) < 25)
        self.label.setText("%s  -  %d match(es) in %d file(s)" % (title, len(results), len(groups)))

    def _activate(self, item, _col):
        idx = item.data(0, Qt.UserRole)
        if idx is not None:
            p, l, c = self._entries[int(idx)]
            self.activated.emit(p, l, c)


# --------------------------------------------------------------------------- #
class Terminal(QtWidgets.QWidget):
    def __init__(self, cwd_getter, parent=None):
        super().__init__(parent)
        self.cwd = cwd_getter()
        self.proc = None
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.out = Console()
        row = QtWidgets.QHBoxLayout()
        self.prompt = QtWidgets.QLabel()
        self.edit = HistoryLineEdit()
        self.edit.setPlaceholderText("Shell command  (cd works; running commands receive stdin)")
        b_stop = QtWidgets.QPushButton("Stop")
        use_icon(b_stop, "stop")
        row.addWidget(self.prompt)
        row.addWidget(self.edit, 1)
        row.addWidget(b_stop)
        lay.addWidget(self.out, 1)
        lay.addLayout(row)
        self.edit.returnPressed.connect(self._submit)
        b_stop.clicked.connect(self.stop)
        self._update_prompt()

    def set_cwd(self, cwd):
        self.cwd = cwd
        self._update_prompt()

    def _update_prompt(self):
        self.prompt.setText((os.path.basename(self.cwd.rstrip("/\\")) or self.cwd) + " $")

    def running(self):
        return self.proc is not None and self.proc.state() != QtCore.QProcess.NotRunning

    def stop(self):
        if self.running():
            self.proc.kill()

    def _submit(self):
        cmd = self.edit.text()
        self.edit.clear()
        if self.running():
            self.out.write(cmd + "\n", "prompt")
            self.proc.write((cmd + "\n").encode("utf-8"))
            return
        cmd = cmd.strip()
        if not cmd:
            return
        self.edit.push(cmd)
        self.out.write("%s %s\n" % (self.prompt.text(), cmd), "prompt")
        if cmd in ("clear", "cls"):
            self.out.clear()
            return
        m = re.match(r"^cd(?:\s+(.*))?$", cmd)
        if m:
            target = os.path.expanduser((m.group(1) or "~").strip().strip('"'))
            newp = os.path.normpath(os.path.join(self.cwd, target))
            if os.path.isdir(newp):
                self.set_cwd(newp)
            else:
                self.out.write("cd: no such directory: %s\n" % newp, "err")
            return
        self.proc = QtCore.QProcess(self)
        self.proc.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        self.proc.setWorkingDirectory(self.cwd)
        self.proc.readyRead.connect(self._read)
        self.proc.finished.connect(lambda *a: self.out.write("[exit %s]\n" % (a[0] if a else "?"), "info"))
        if os.name == "nt":
            self.proc.start("cmd.exe", ["/c", cmd])
        else:
            self.proc.start("/bin/sh", ["-c", cmd])

    def _read(self):
        self.out.write(self.proc.readAll().data().decode("utf-8", "replace"))


class PyConsole(QtWidgets.QWidget):
    """Interactive REPL sharing the editor's namespace."""

    def __init__(self, runner, parent=None):
        super().__init__(parent)
        self.runner = runner            # runner(compiled, write_fn)
        self.buf = []
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.out = Console()
        row = QtWidgets.QHBoxLayout()
        self.prompt = QtWidgets.QLabel(">>>")
        self.edit = HistoryLineEdit()
        row.addWidget(self.prompt)
        row.addWidget(self.edit, 1)
        lay.addWidget(self.out, 1)
        lay.addLayout(row)
        self.edit.returnPressed.connect(self._submit)
        self.out.write("Python %s\n" % sys.version.split()[0], "info")

    def _submit(self):
        line = self.edit.text()
        self.edit.clear()
        self.edit.push(line)
        self.out.write("%s %s\n" % (self.prompt.text(), line), "prompt")
        self.buf.append(line)
        src = "\n".join(self.buf)
        try:
            cc = code.compile_command(src, "<console>", "single")
        except (SyntaxError, OverflowError, ValueError) as exc:
            self.buf = []
            self.prompt.setText(">>>")
            self.out.write("".join(traceback.format_exception_only(type(exc), exc)), "err")
            return
        if cc is None:
            self.prompt.setText("...")
            return
        self.buf = []
        self.prompt.setText(">>>")
        self.runner(cc, self.out.write)


# --------------------------------------------------------------------------- #
def safe_repr(v, limit=200):
    try:
        r = repr(v)
    except Exception as exc:
        r = "<repr failed: %s>" % exc
    return r if len(r) <= limit else r[:limit] + "..."


class DebugPanel(QtWidgets.QWidget):
    action = Signal(str)
    frameSelected = Signal(int)
    evalRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        row = QtWidgets.QHBoxLayout()
        self.buttons = []
        for label, act, tip, ico in [("Resume", "resume", "F9", "resume"), ("Step Over", "over", "F8", "step_over"),
                                     ("Step Into", "into", "F7", "step_into"), ("Step Out", "out", "Shift+F8", "step_out"),
                                     ("Stop", "stop", "Ctrl+F2", "stop")]:
            b = QtWidgets.QPushButton(label)
            use_icon(b, ico)
            b.setToolTip(tip)
            b.clicked.connect(lambda checked=False, a=act: self.action.emit(a))
            row.addWidget(b)
            self.buttons.append(b)
        self.state = QtWidgets.QLabel("Not debugging")
        row.addWidget(self.state, 1)
        lay.addLayout(row)
        split = QtWidgets.QSplitter(Qt.Horizontal)
        self.frames = QtWidgets.QListWidget()
        self.vars = QtWidgets.QTreeWidget()
        self.vars.setHeaderLabels(["Name", "Type", "Value"])
        self.vars.setColumnWidth(0, 150)
        self.vars.setColumnWidth(1, 110)
        split.addWidget(self.frames)
        split.addWidget(self.vars)
        split.setSizes([260, 700])
        lay.addWidget(split, 1)
        self.expr = HistoryLineEdit()
        self.expr.setPlaceholderText("Evaluate expression in the selected frame and press Enter")
        lay.addWidget(self.expr)
        self.frames.currentRowChanged.connect(lambda r: self.frameSelected.emit(r) if r >= 0 else None)
        self.expr.returnPressed.connect(self._eval)
        self.set_active(False)

    def _eval(self):
        text = self.expr.text().strip()
        if text:
            self.expr.push(text)
            self.expr.clear()
            self.evalRequested.emit(text)

    def set_active(self, on):
        for b in self.buttons:
            b.setEnabled(on)
        self.expr.setEnabled(on)
        self.state.setText("Paused" if on else "Not debugging")
        if not on:
            self.frames.clear()
            self.vars.clear()

    def show_frames(self, infos):
        self.frames.blockSignals(True)
        self.frames.clear()
        for name, line, func in infos:
            self.frames.addItem("%s()  %s:%d" % (func, name, line))
        self.frames.setCurrentRow(0)
        self.frames.blockSignals(False)

    def show_vars(self, mapping):
        self.vars.clear()
        for k in sorted(mapping, key=lambda s: str(s).lower()):
            if str(k).startswith("__"):
                continue
            v = mapping[k]
            self.vars.addTopLevelItem(QtWidgets.QTreeWidgetItem([str(k), type(v).__name__, safe_repr(v)]))


# --------------------------------------------------------------------------- #
class QuickList(QtWidgets.QDialog):
    """Search-Everywhere style popup. provider(query) -> [(label, payload)]"""

    def __init__(self, parent, title, provider, on_pick, initial=""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(700, 460)
        self.provider, self.on_pick = provider, on_pick
        self._payloads = []
        lay = QtWidgets.QVBoxLayout(self)
        self.edit = QtWidgets.QLineEdit()
        self.edit.setPlaceholderText(title)
        self.list = QtWidgets.QListWidget()
        lay.addWidget(self.edit)
        lay.addWidget(self.list, 1)
        self.edit.textChanged.connect(self.refill)
        self.edit.returnPressed.connect(self.accept_current)
        self.list.itemActivated.connect(lambda *_: self.accept_current())
        self.edit.installEventFilter(self)
        self.edit.setText(initial)
        self.refill(initial)
        self.edit.setFocus()

    def eventFilter(self, obj, ev):
        if obj is self.edit and ev.type() == QtCore.QEvent.KeyPress and \
                ev.key() in (Qt.Key_Up, Qt.Key_Down, Qt.Key_PageUp, Qt.Key_PageDown):
            QtWidgets.QApplication.sendEvent(self.list, ev)
            return True
        return super().eventFilter(obj, ev)

    def refill(self, q):
        items = self.provider(q.strip())
        self.list.clear()
        self._payloads = []
        for label, payload in items[:120]:
            self.list.addItem(label)
            self._payloads.append(payload)
        if self._payloads:
            self.list.setCurrentRow(0)

    def accept_current(self):
        row = self.list.currentRow()
        if 0 <= row < len(self._payloads):
            payload = self._payloads[row]
            self.accept()
            QtCore.QTimer.singleShot(0, lambda: self.on_pick(payload))


class FindInFilesDialog(QtWidgets.QDialog):
    def __init__(self, parent, root, query=""):
        super().__init__(parent)
        self.setWindowTitle("Find / Replace in Files")
        self.resize(560, 260)
        form = QtWidgets.QFormLayout(self)
        self.query = QtWidgets.QLineEdit(query)
        self.repl = QtWidgets.QLineEdit()
        self.dir = QtWidgets.QLineEdit(root)
        self.mask = QtWidgets.QLineEdit("*.py")
        self.regex = QtWidgets.QCheckBox("Regex")
        self.case = QtWidgets.QCheckBox("Match case")
        self.whole = QtWidgets.QCheckBox("Whole word")
        checks = QtWidgets.QHBoxLayout()
        for c in (self.regex, self.case, self.whole):
            checks.addWidget(c)
        form.addRow("Find:", self.query)
        form.addRow("Replace with:", self.repl)
        form.addRow("Directory:", self.dir)
        form.addRow("File masks:", self.mask)
        form.addRow("", checks)
        row = QtWidgets.QHBoxLayout()
        self.b_find, self.b_repl, b_close = (QtWidgets.QPushButton("Find"),
                                             QtWidgets.QPushButton("Replace All"), QtWidgets.QPushButton("Cancel"))
        for b in (self.b_find, self.b_repl, b_close):
            row.addWidget(b)
        form.addRow(row)
        self.mode = None
        self.b_find.clicked.connect(lambda: self._done("find"))
        self.b_repl.clicked.connect(lambda: self._done("replace"))
        b_close.clicked.connect(self.reject)
        self.query.returnPressed.connect(lambda: self._done("find"))

    def _done(self, mode):
        self.mode = mode
        self.accept()

    def params(self):
        return dict(query=self.query.text(), repl=self.repl.text(), root=self.dir.text() or ".",
                    masks=[m.strip() for m in self.mask.text().replace(";", ",").split(",") if m.strip()] or ["*.py"],
                    regex=self.regex.isChecked(), case=self.case.isChecked(), whole=self.whole.isChecked())


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(460, 360)
        form = QtWidgets.QFormLayout(self)
        self.theme = QtWidgets.QComboBox()
        self.theme.addItems(sorted(THEMES))
        self.theme.setCurrentText(SETTINGS.get("theme"))
        self.font = QtWidgets.QLineEdit(SETTINGS.get("font_family"))
        self.font.setPlaceholderText("(system monospace)")
        self.size = QtWidgets.QSpinBox()
        self.size.setRange(6, 32)
        self.size.setValue(int(SETTINGS.get("font_size")))
        self.tab = QtWidgets.QSpinBox()
        self.tab.setRange(1, 8)
        self.tab.setValue(int(SETTINGS.get("tab_width")))
        self.margin = QtWidgets.QSpinBox()
        self.margin.setRange(0, 400)
        self.margin.setValue(int(SETTINGS.get("margin")))
        self.margin.setToolTip("0 disables the right margin line and the long-line inspection")
        self.wrap = QtWidgets.QCheckBox("Soft-wrap long lines")
        self.wrap.setChecked(bool(SETTINGS.get("wrap")))
        self.ws = QtWidgets.QCheckBox("Show whitespace")
        self.ws.setChecked(bool(SETTINGS.get("whitespace")))
        self.interp = QtWidgets.QLineEdit(SETTINGS.get("interpreter"))
        self.interp.setPlaceholderText("python / python3 / full path  (external runs)")
        form.addRow("Theme:", self.theme)
        form.addRow("Font family:", self.font)
        form.addRow("Font size:", self.size)
        form.addRow("Tab width:", self.tab)
        form.addRow("Right margin (columns):", self.margin)
        form.addRow("", self.wrap)
        form.addRow("", self.ws)
        form.addRow("Python interpreter:", self.interp)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def apply(self):
        S = SETTINGS
        S.set("theme", self.theme.currentText(), False)
        S.set("font_family", self.font.text().strip(), False)
        S.set("font_size", self.size.value(), False)
        S.set("tab_width", self.tab.value(), False)
        S.set("margin", self.margin.value(), False)
        S.set("wrap", self.wrap.isChecked(), False)
        S.set("whitespace", self.ws.isChecked(), False)
        S.set("interpreter", self.interp.text().strip(), False)
        S.save()


# --------------------------------------------------------------------------- #
# TD tools dock (node browser / inspector / snippets / utilities)
# --------------------------------------------------------------------------- #
BUILTIN_SNIPPETS = {
    "Selected nodes: info": '''for n in nuke.selectedNodes():
    print("%-25s %-15s pos=(%d, %d) inputs=%d" % (n.name(), n.Class(), n.xpos(), n.ypos(), n.inputs()))
''',
    "Reads: list paths + range": '''for n in nuke.allNodes("Read"):
    print("%s: %s [%s-%s] cs=%s" % (n.name(), n["file"].value(), n["first"].value(), n["last"].value(), n["colorspace"].value()))
''',
    "Reads: replace in path": '''old, new = "/old/path", "/new/path"
for n in nuke.allNodes("Read"):
    p = n["file"].value()
    if old in p:
        n["file"].setValue(p.replace(old, new))
        print("fixed", n.name())
''',
    "Reads: set colorspace on all": '''for n in nuke.allNodes("Read"):
    n["colorspace"].setValue("raw")
''',
    "Set project range from Reads": '''reads = nuke.allNodes("Read")
first = min(int(n["first"].value()) for n in reads)
last = max(int(n["last"].value()) for n in reads)
nuke.root()["first_frame"].setValue(first)
nuke.root()["last_frame"].setValue(last)
print("range", first, last)
''',
    "Disable / enable by class": '''cls, state = "Blur", True   # True = disable
for n in nuke.allNodes(cls):
    n["disable"].setValue(state)
''',
    "Writes: list paths": '''for n in nuke.allNodes("Write"):
    print(n.name(), n["file"].value(), "disabled" if n["disable"].value() else "")
''',
    "Render all Writes": '''first = int(nuke.root()["first_frame"].value())
last = int(nuke.root()["last_frame"].value())
for n in nuke.allNodes("Write"):
    if not n["disable"].value():
        nuke.execute(n, first, last)
''',
    "Dependencies of selected": '''n = nuke.selectedNode()
print("UP  :", [x.name() for x in nuke.dependencies(n)])
print("DOWN:", [x.name() for x in nuke.dependentNodes(nuke.INPUTS | nuke.HIDDEN_INPUTS | nuke.EXPRESSIONS, [n])])
''',
    "Backdrop around selection": '''import nukescripts
nukescripts.autoBackdrop()
''',
    "Link knob with expression": '''src = nuke.toNode("Grade1")
for n in nuke.selectedNodes("Grade"):
    n["gain"].setExpression("%s.gain" % src.name())
''',
    "Knob-changed callback": '''def on_changed():
    n, k = nuke.thisNode(), nuke.thisKnob()
    if k.name() == "file":
        print("file changed on", n.name())

nuke.addKnobChanged(on_changed, nodeClass="Read")
''',
    "Find nodes with errors": '''for n in nuke.allNodes(recurseGroups=True):
    if n.hasError():
        print("ERROR:", n.fullName())
''',
    "Remove all viewers": '''for n in nuke.allNodes("Viewer"):
    nuke.delete(n)
''',
}


class TDTools(QtWidgets.QTabWidget):
    def __init__(self, editor_getter, out, run_code, parent=None):
        super().__init__(parent)
        self.editor = editor_getter
        self.out = out                     # out(text, kind)
        self.run_code = run_code           # run_code(source, label)
        self._last_sel = ()
        self.user_snippets = self._load_user_snippets()
        self._build_nodes_tab()
        self._build_inspector_tab()
        self._build_snippets_tab()
        self._build_utils_tab()
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._poll)
        self.timer.start(700)

    def _need_nuke(self):
        if nuke is None:
            self.out("Nuke is not available (standalone mode).\n", "err")
            return False
        return True

    def _insert(self, text):
        ed = self.editor()
        if ed:
            ed.insertPlainText(text)
            ed.setFocus()

    # ---- Nodes ---------------------------------------------------------- #
    def _build_nodes_tab(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        self.node_filter = QtWidgets.QLineEdit()
        self.node_filter.setPlaceholderText("Filter by name / class")
        self.node_tree = QtWidgets.QTreeWidget()
        self.node_tree.setHeaderLabels(["Name", "Class", "State"])
        self.node_tree.setRootIsDecorated(False)
        self.node_tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.node_tree.setSortingEnabled(True)
        lay.addWidget(self.node_filter)
        lay.addWidget(self.node_tree)
        row = QtWidgets.QGridLayout()
        for i, (label, fn) in enumerate([
                ("Refresh", self.refresh_nodes),
                ("Sel -> toNode()", self.sel_to_tonode),
                ("Sel -> names list", self.sel_to_names),
                ("Sel -> Python rebuild", self.sel_to_python)]):
            b = QtWidgets.QPushButton(label)
            if label == "Refresh":
                use_icon(b, "refresh")
            b.clicked.connect(lambda checked=False, f=fn: f())
            row.addWidget(b, i // 2, i % 2)
        lay.addLayout(row)
        self.node_filter.textChanged.connect(self._filter_nodes)
        self.node_tree.itemSelectionChanged.connect(self._tree_select)
        self.node_tree.itemDoubleClicked.connect(self._tree_focus)
        self.addTab(w, "Nodes")
        use_icon(self, "nodes", self.count() - 1)

    def refresh_nodes(self):
        self.node_tree.blockSignals(True)
        self.node_tree.clear()
        if nuke:
            try:
                for n in nuke.allNodes():
                    state = "disabled" if ("disable" in n.knobs() and n["disable"].value()) else ""
                    if n.hasError():
                        state = "ERROR"
                    it = QtWidgets.QTreeWidgetItem([n.name(), n.Class(), state])
                    if state == "ERROR":
                        it.setForeground(2, QtGui.QBrush(QColor(T["err"])))
                    self.node_tree.addTopLevelItem(it)
            except Exception as exc:
                self.out("refresh failed: %s\n" % exc, "err")
        self.node_tree.blockSignals(False)
        self._filter_nodes()

    def _filter_nodes(self, *_):
        t = self.node_filter.text().lower()
        for i in range(self.node_tree.topLevelItemCount()):
            it = self.node_tree.topLevelItem(i)
            it.setHidden(bool(t) and t not in it.text(0).lower() and t not in it.text(1).lower())

    def _tree_select(self):
        if not nuke:
            return
        names = set(i.text(0) for i in self.node_tree.selectedItems())
        try:
            for n in nuke.allNodes():
                n.setSelected(n.name() in names)
        except Exception:
            pass

    def _tree_focus(self, item, _col):
        if nuke:
            n = nuke.toNode(item.text(0))
            if n:
                nuke.zoom(1.5, [n.xpos(), n.ypos()])

    def sel_to_tonode(self):
        if self._need_nuke():
            self._insert("\n".join("nuke.toNode(%r)" % n.name() for n in nuke.selectedNodes()) + "\n")

    def sel_to_names(self):
        if self._need_nuke():
            self._insert(repr([n.name() for n in nuke.selectedNodes()]) + "\n")

    def sel_to_python(self):
        if not self._need_nuke():
            return
        nodes = nuke.selectedNodes()
        if not nodes:
            self.out("Select some nodes first.\n", "err")
            return
        names = set(n.name() for n in nodes)
        L = ["import nuke", "",
             "def _make(cls, knobs):",
             "    for _n in nuke.selectedNodes():",
             "        _n.setSelected(False)",
             "    return nuke.createNode(cls, knobs, inpanel=False)", "",
             "created = {}"]
        for n in nodes:
            knobs = n.writeKnobs(nuke.TO_SCRIPT | nuke.WRITE_USER_KNOB_DEFS)
            L.append("created[%r] = _make(%r, %r)" % (n.name(), n.Class(), knobs))
        L.append("")
        for n in nodes:
            for i in range(n.inputs()):
                inp = n.input(i)
                if inp is None:
                    continue
                src = ("created[%r]" % inp.name()) if inp.name() in names else ("nuke.toNode(%r)" % inp.name())
                L.append("created[%r].setInput(%d, %s)" % (n.name(), i, src))
        self._insert("\n".join(L) + "\n")

    # ---- Inspector ------------------------------------------------------ #
    def _build_inspector_tab(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        top = QtWidgets.QHBoxLayout()
        self.insp_label = QtWidgets.QLabel("No node")
        self.insp_auto = QtWidgets.QCheckBox("Auto")
        self.insp_auto.setChecked(True)
        self.insp_mod = QtWidgets.QCheckBox("Only changed")
        b = QtWidgets.QPushButton("Refresh")
        use_icon(b, "refresh")
        b.clicked.connect(lambda: self.refresh_inspector())
        for x in (self.insp_label, self.insp_auto, self.insp_mod, b):
            top.addWidget(x)
        self.insp_filter = QtWidgets.QLineEdit()
        self.insp_filter.setPlaceholderText("Filter knobs")
        self.insp_tree = QtWidgets.QTreeWidget()
        self.insp_tree.setHeaderLabels(["Knob", "Type", "Value"])
        self.insp_tree.setRootIsDecorated(False)
        self.insp_tree.setColumnWidth(0, 130)
        lay.addLayout(top)
        lay.addWidget(self.insp_filter)
        lay.addWidget(self.insp_tree)
        lay.addWidget(QtWidgets.QLabel("Double-click a knob to insert its accessor."))
        self.insp_filter.textChanged.connect(self.refresh_inspector)
        self.insp_mod.toggled.connect(self.refresh_inspector)
        self.insp_tree.itemDoubleClicked.connect(self._insp_insert)
        self.addTab(w, "Inspector")
        use_icon(self, "eye", self.count() - 1)
        self._insp_node = None

    def refresh_inspector(self, *_):
        self.insp_tree.clear()
        if not nuke:
            self.insp_label.setText("Nuke not available")
            return
        try:
            sel = nuke.selectedNodes()
        except Exception:
            return
        if not sel:
            self.insp_label.setText("No node selected")
            self._insp_node = None
            return
        n = sel[0]
        self._insp_node = n.name()
        self.insp_label.setText("%s (%s)" % (n.name(), n.Class()))
        t = self.insp_filter.text().lower()
        only_mod = self.insp_mod.isChecked()
        for name, k in sorted(n.knobs().items()):
            if t and t not in name.lower():
                continue
            try:
                if only_mod and not k.notDefault():
                    continue
                val = k.toScript()
            except Exception:
                val = ""
            val = val.replace("\n", " ")
            self.insp_tree.addTopLevelItem(QtWidgets.QTreeWidgetItem(
                [name, k.Class(), val[:140] + ("..." if len(val) > 140 else "")]))

    def _insp_insert(self, item, _col):
        if self._insp_node:
            self._insert("nuke.toNode(%r)[%r].value()" % (self._insp_node, item.text(0)))

    def _poll(self):
        if not nuke or not self.insp_auto.isChecked() or self.currentIndex() != 1 or not self.isVisible():
            return
        try:
            sel = tuple(n.name() for n in nuke.selectedNodes())
        except Exception:
            return
        if sel != self._last_sel:
            self._last_sel = sel
            self.refresh_inspector()

    # ---- Snippets ------------------------------------------------------- #
    def _load_user_snippets(self):
        try:
            with open(SNIPPET_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_user_snippets(self):
        _ensure_dir()
        try:
            with open(SNIPPET_FILE, "w") as f:
                json.dump(self.user_snippets, f, indent=2)
        except Exception as exc:
            self.out("could not save snippets: %s\n" % exc, "err")

    def _build_snippets_tab(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        split = QtWidgets.QSplitter(Qt.Vertical)
        self.snip_list = QtWidgets.QListWidget()
        self.snip_preview = QtWidgets.QPlainTextEdit()
        font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        font.setPointSize(9)
        self.snip_preview.setFont(font)
        split.addWidget(self.snip_list)
        split.addWidget(self.snip_preview)
        lay.addWidget(split)
        row = QtWidgets.QHBoxLayout()
        for label, fn in [("Insert", self.snip_insert), ("Run", self.snip_run),
                          ("Save as...", self.snip_save), ("Delete", self.snip_delete)]:
            b = QtWidgets.QPushButton(label)
            use_icon(b, {"Insert": "snippets", "Run": "run", "Save as...": "save", "Delete": "trash"}[label])
            b.clicked.connect(lambda checked=False, f=fn: f())
            row.addWidget(b)
        lay.addLayout(row)
        self.snip_list.currentTextChanged.connect(self._snip_show)
        self.snip_list.itemDoubleClicked.connect(lambda *_: self.snip_insert())
        self._fill_snippets()
        self.addTab(w, "Snippets")
        use_icon(self, "snippets", self.count() - 1)

    def _fill_snippets(self):
        self.snip_list.clear()
        for name in sorted(BUILTIN_SNIPPETS):
            self.snip_list.addItem(name)
        for name in sorted(self.user_snippets):
            self.snip_list.addItem("* " + name)

    def _snip_show(self, name):
        if name.startswith("* "):
            self.snip_preview.setPlainText(self.user_snippets.get(name[2:], ""))
        else:
            self.snip_preview.setPlainText(BUILTIN_SNIPPETS.get(name, ""))

    def snip_insert(self):
        self._insert(self.snip_preview.toPlainText() + "\n")

    def snip_run(self):
        self.run_code(self.snip_preview.toPlainText(), "<snippet>")

    def snip_save(self):
        text = self.snip_preview.toPlainText()
        ed = self.editor()
        if ed and ed.textCursor().hasSelection() and not text.strip():
            text = ed.textCursor().selectedText().replace("\u2029", "\n")
        if not text.strip():
            return
        name, ok = QtWidgets.QInputDialog.getText(self, "Save snippet", "Snippet name:")
        if ok and name.strip():
            self.user_snippets[name.strip()] = text
            self._save_user_snippets()
            self._fill_snippets()

    def snip_delete(self):
        it = self.snip_list.currentItem()
        if it and it.text().startswith("* "):
            self.user_snippets.pop(it.text()[2:], None)
            self._save_user_snippets()
            self._fill_snippets()

    # ---- Utilities ------------------------------------------------------ #
    def _build_utils_tab(self):
        w = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(w)
        tools = [
            ("Script info", self.u_script_info), ("Class counts", self.u_class_counts),
            ("Missing files", self.u_missing_files), ("Error nodes", self.u_error_nodes),
            ("Unused nodes", self.u_unused_nodes), ("Write paths", self.u_write_paths),
            ("Select by class...", self.u_select_class), ("Toggle disable (sel)", self.u_toggle_disable),
            ("Reload all Reads", self.u_reload_reads), ("Select unused", self.u_select_unused),
        ]
        for i, (label, fn) in enumerate(tools):
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(lambda checked=False, f=fn: f())
            grid.addWidget(b, i // 2, i % 2)
        grid.setRowStretch(len(tools) // 2 + 1, 1)
        self.addTab(w, "Utils")
        use_icon(self, "sliders", self.count() - 1)

    def _p(self, s, kind="out"):
        self.out(s + "\n", kind)

    def u_script_info(self):
        if not self._need_nuke():
            return
        r = nuke.root()
        self._p("Script : %s" % (r.name() or "<unsaved>"), "info")
        for k in ("first_frame", "last_frame", "fps", "colorManagement", "format", "proxy"):
            try:
                self._p("  %-16s %s" % (k, r[k].value()))
            except Exception:
                pass
        self._p("  nodes            %d (top-level)  / %d (recursive)" % (
            len(nuke.allNodes()), len(nuke.allNodes(recurseGroups=True))))
        self._p("  Nuke version     %s" % nuke.NUKE_VERSION_STRING)

    def u_class_counts(self):
        if not self._need_nuke():
            return
        for cls, cnt in Counter(n.Class() for n in nuke.allNodes(recurseGroups=True)).most_common():
            self._p("%5d  %s" % (cnt, cls))

    @staticmethod
    def _frame_path(n):
        p = n["file"].evaluate()
        try:
            first = int(n["first"].value())
        except Exception:
            first = 1
        try:
            return p % first
        except Exception:
            return re.sub(r"#+", lambda m: str(first).zfill(len(m.group())), p)

    def u_missing_files(self):
        if not self._need_nuke():
            return
        bad = 0
        for n in nuke.allNodes("Read", recurseGroups=True):
            try:
                path = self._frame_path(n)
            except Exception:
                continue
            if path and not os.path.exists(path):
                bad += 1
                self._p("MISSING  %-20s %s" % (n.fullName(), path), "err")
        self._p("%d missing Read file(s)." % bad, "info")

    def u_error_nodes(self):
        if not self._need_nuke():
            return
        errs = [n for n in nuke.allNodes(recurseGroups=True) if n.hasError()]
        for n in errs:
            self._p("ERROR  %s (%s)" % (n.fullName(), n.Class()), "err")
        self._p("%d node(s) with errors." % len(errs), "info")

    def _unused(self):
        skip = ("Viewer", "Write", "BackdropNode", "StickyNote", "Root", "NoOp")
        return [n for n in nuke.allNodes() if n.Class() not in skip and not n.dependent()]

    def u_unused_nodes(self):
        if not self._need_nuke():
            return
        un = self._unused()
        for n in un:
            self._p("%-25s %s" % (n.name(), n.Class()))
        self._p("%d unused node(s)." % len(un), "info")

    def u_select_unused(self):
        if not self._need_nuke():
            return
        un = set(n.name() for n in self._unused())
        for n in nuke.allNodes():
            n.setSelected(n.name() in un)

    def u_write_paths(self):
        if not self._need_nuke():
            return
        for n in nuke.allNodes("Write", recurseGroups=True):
            self._p("%-20s %s %s" % (n.fullName(), n["file"].value(), "[disabled]" if n["disable"].value() else ""))

    def u_select_class(self):
        if not self._need_nuke():
            return
        cls, ok = QtWidgets.QInputDialog.getText(self, "Select by class", "Node class:")
        if ok and cls:
            hits = nuke.allNodes(cls.strip())
            for n in nuke.allNodes():
                n.setSelected(False)
            for n in hits:
                n.setSelected(True)
            self._p("Selected %d %s node(s)." % (len(hits), cls), "info")

    def u_toggle_disable(self):
        if not self._need_nuke():
            return
        for n in nuke.selectedNodes():
            if "disable" in n.knobs():
                n["disable"].setValue(not n["disable"].value())

    def u_reload_reads(self):
        if not self._need_nuke():
            return
        cnt = 0
        for n in nuke.allNodes("Read", recurseGroups=True):
            try:
                n["reload"].execute()
                cnt += 1
            except Exception:
                pass
        self._p("Reloaded %d Read(s)." % cnt, "info")


# =========================================================================== #
#  MAIN WINDOW
# =========================================================================== #
class DoubleShiftFilter(QtCore.QObject):
    """Application-wide filter: tapping Shift twice opens Search Everywhere."""

    def __init__(self, win):
        super().__init__()
        self.ref = weakref.ref(win)
        self.last = 0.0

    def eventFilter(self, obj, ev):
        if ev.type() != QtCore.QEvent.KeyPress:
            return False
        win = self.ref()
        app = QtWidgets.QApplication.instance()
        if win is None:
            if app:
                app.removeEventFilter(self)
            return False
        if ev.key() == Qt.Key_Shift and not ev.isAutoRepeat():
            fw = app.focusWidget()
            if fw is not None and win.isAncestorOf(fw):
                now = time.time()
                if now - self.last < 0.4:
                    self.last = 0.0
                    QtCore.QTimer.singleShot(0, lambda: win.search_everywhere("all"))
                else:
                    self.last = now
        elif ev.key() not in (Qt.Key_Control, Qt.Key_Alt, Qt.Key_Meta):
            self.last = 0.0
        return False


SHORTCUTS_HELP = """<pre>
RUN / DEBUG                              NAVIGATE
Ctrl+Return / Shift+F10  Run             Ctrl+Alt+F5    Search Everywhere
Ctrl+Shift+Return        Run selection   Ctrl+Shift+N   Go to file
Shift+F9                 Debug           Ctrl+Alt+Shift+N  Go to symbol
Ctrl+F2                  Stop            Ctrl+E         Recent files
F9 / F8 / F7 / Shift+F8  Resume/Over/    Ctrl+G         Go to line
                         Into/Out        Ctrl+B / Ctrl+Click  Declaration
Ctrl+F8  (or gutter)     Breakpoint      Alt+F7         Find usages
Ctrl+L                   Clear output    Ctrl+Alt+Left/Right  Back/Forward
                                         F2 / Shift+F2  Next/prev problem
EDIT                                     F11 / Shift+F11  Bookmark / list
Ctrl+/           Comment                 Ctrl+F / F3    Find / next
Ctrl+D           Duplicate line          Ctrl+R         Replace
Ctrl+Y           Delete line             Ctrl+Shift+F   Find in files
Alt+Shift+Up/Dn  Move line               Ctrl+Shift+R   Replace in files
Ctrl+Shift+J     Join lines
Ctrl+W           Extend selection        CODE
Ctrl+Shift+U     Toggle case             Ctrl+Space     Complete
Tab (after abbr) Live template           F1             Quick documentation
Ctrl+Shift+-/=   Fold / unfold all       Shift+F6       Rename
                                         Ctrl+Alt+T     Surround with
Templates: main def defs class for fori  Ctrl+Alt+L     Reformat code
if ife try with prn prop log nsel nall   Ctrl+Alt+O     Optimize imports
nread nwrite ntn ncn nundo ncb nkc nprog
</pre>"""


class NukeCodeEditor(QtWidgets.QMainWindow):
    RUN_MODES = ["Inside Nuke", "Python (external)", "Nuke -t (terminal)"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("%s %s  -  by %s" % (APP_NAME, __version__, __author__))
        self.resize(1500, 920)
        set_theme(SETTINGS.get("theme"))
        self.setWindowIcon(icon("app"))
        self._ns = {"__name__": "__main__"}
        self._untitled = 0
        self._actions = {}
        self._nav_back, self._nav_fwd, self._navigating = [], [], False
        self._debugging, self._dbg, self._dbg_loop = False, None, None
        self._dbg_action, self._dbg_frames = "resume", []
        self._proc = None
        self._file_cache = (None, 0.0, [])
        self._sym_cache = (None, 0.0, [])
        self._doc_dlg = None
        proj = SETTINGS.get("project")
        self.project = os.path.abspath(proj if proj and os.path.isdir(proj) else default_project_dir())

        self._build_ui()
        self._build_actions()
        self._build_status()
        self.apply_theme()
        self._restore_session()
        if self.tabs.count() == 0:
            self.new_tab()
        self.tools.refresh_nodes()

        self.autosave = QtCore.QTimer(self)
        self.autosave.timeout.connect(self.save_session)
        self.autosave.start(20000)
        self._shift_filter = DoubleShiftFilter(self)
        app = QtWidgets.QApplication.instance()
        if app:
            app.installEventFilter(self._shift_filter)
        self.console.write("%s %s by %s ready - Shift Shift = Search Everywhere, Shift+F9 = Debug, F1 = quick docs.\n" % (APP_NAME, __version__, __author__), "info")

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.crumbs = QtWidgets.QLabel("")
        self.crumbs.setObjectName("crumbs")
        self.findbar = FindBar(self.current_editor)

        area = QtWidgets.QWidget()
        al = QtWidgets.QVBoxLayout(area)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(0)
        al.addWidget(self.crumbs)
        al.addWidget(self.tabs, 1)
        al.addWidget(self.findbar)

        # bottom tool windows
        self.console = Console()
        run_page = QtWidgets.QWidget()
        rl = QtWidgets.QVBoxLayout(run_page)
        rl.setContentsMargins(0, 0, 0, 0)
        row = QtWidgets.QHBoxLayout()
        for label, fn, ico in (("Rerun", self.run_all, "refresh"), ("Stop", self.stop_all, "stop"),
                               ("Clear", lambda: self.console.clear(), "trash")):
            b = QtWidgets.QPushButton(label)
            use_icon(b, ico)
            b.clicked.connect(lambda checked=False, f=fn: f())
            row.addWidget(b)
        row.addStretch(1)
        rl.addLayout(row)
        rl.addWidget(self.console, 1)

        self.terminal = Terminal(lambda: self.project)
        self.pyconsole = PyConsole(self._console_run)
        self.debug_panel = DebugPanel()
        self.problems_panel = ProblemsPanel()
        self.results = ResultsPanel(lambda: self.project)
        self.todo = ResultsPanel(lambda: self.project)
        todo_page = QtWidgets.QWidget()
        tl = QtWidgets.QVBoxLayout(todo_page)
        tl.setContentsMargins(0, 0, 0, 0)
        b_scan = QtWidgets.QPushButton("Scan project for TODO / FIXME")
        use_icon(b_scan, "todo")
        b_scan.clicked.connect(lambda: self.scan_todos())
        tl.addWidget(b_scan)
        tl.addWidget(self.todo, 1)

        self.bottom = QtWidgets.QTabWidget()
        self.page_run = run_page
        for w, name in ((run_page, "Run"), (self.terminal, "Terminal"), (self.pyconsole, "Python Console"),
                        (self.debug_panel, "Debug"), (self.problems_panel, "Problems"),
                        (self.results, "Find"), (todo_page, "TODO")):
            self.bottom.addTab(w, name)
            use_icon(self.bottom, {"Run": "run", "Terminal": "terminal", "Python Console": "pyconsole",
                                   "Debug": "debug", "Problems": "problems", "Find": "find",
                                   "TODO": "todo"}[name], self.bottom.count() - 1)

        split = QtWidgets.QSplitter(Qt.Vertical)
        split.addWidget(area)
        split.addWidget(self.bottom)
        split.setSizes([640, 250])
        self.setCentralWidget(split)

        # docks
        self.project_panel = ProjectPanel(self.project)
        self.structure = StructurePanel()
        self.tools = TDTools(self.current_editor, self.console.write, self.run_code)
        self.dock_project = self._dock("Project", self.project_panel, Qt.LeftDockWidgetArea)
        self.dock_structure = self._dock("Structure", self.structure, Qt.LeftDockWidgetArea)
        self.tabifyDockWidget(self.dock_project, self.dock_structure)
        self.dock_project.raise_()
        self.dock_tools = self._dock("TD Tools", self.tools, Qt.RightDockWidgetArea)
        for d, nm in ((self.dock_project, "folder"), (self.dock_structure, "structure"), (self.dock_tools, "toolbox")):
            use_icon(d.toggleViewAction(), nm)

        self.project_panel.fileActivated.connect(lambda p: self.open_path(p))
        self.structure.gotoLine.connect(lambda l: self._goto_current(l))
        self.problems_panel.problemActivated.connect(lambda ed, l, c: self.goto(ed, l, c))
        self.results.activated.connect(lambda p, l, c: self.open_path(p, l, c))
        self.todo.activated.connect(lambda p, l, c: self.open_path(p, l, c))
        self.debug_panel.action.connect(self._dbg_do)
        self.debug_panel.frameSelected.connect(self._dbg_show_frame)
        self.debug_panel.evalRequested.connect(self._dbg_eval)

    def _dock(self, title, widget, area):
        d = QtWidgets.QDockWidget(title, self)
        d.setObjectName(title.replace(" ", "") + "Dock")
        d.setWidget(widget)
        self.addDockWidget(area, d)
        return d

    def _build_status(self):
        sb = self.statusBar()
        self.lbl_msg = QtWidgets.QLabel("")
        self.lbl_prob = QtWidgets.QLabel("")
        self.lbl_pos = QtWidgets.QLabel("Ln 1, Col 1")
        self.lbl_git = QtWidgets.QLabel(self._git_branch())
        self.lbl_env = QtWidgets.QLabel("Nuke %s | PySide%d | Py %d.%d" % (
            nuke.NUKE_VERSION_STRING if nuke else "n/a", PYSIDE, sys.version_info[0], sys.version_info[1]))
        for w in (self.lbl_prob, self.lbl_git, self.lbl_pos, self.lbl_env):
            sb.addPermanentWidget(w)

    def msg(self, text, ms=4000):
        self.statusBar().showMessage(text, ms)

    def _git_branch(self):
        d = self.project
        for _ in range(8):
            h = os.path.join(d, ".git", "HEAD")
            if os.path.isfile(h):
                try:
                    txt = read_text(h).strip()
                    m = re.match(r"ref: refs/heads/(.+)", txt)
                    return "git: " + (m.group(1) if m else txt[:7])
                except Exception:
                    return ""
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
        return ""

    # ------------------------------------------------------------------ #
    # actions / menus
    # ------------------------------------------------------------------ #
    def _act(self, text, slot, shortcut=None, menu=None, toolbar=None, register=True):
        a = QAction(text, self)
        clean = text.split("\t")[0].replace("&", "")
        hint = shortcut or (text.split("\t")[1] if "\t" in text else "")
        a.setToolTip("%s  (%s)" % (clean, hint) if hint else clean)
        if clean in ACTION_ICONS:
            use_icon(a, ACTION_ICONS[clean])
        if shortcut:
            a.setShortcut(QtGui.QKeySequence(shortcut))
            a.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        a.triggered.connect(lambda checked=False, s=slot: s())
        self.addAction(a)
        if menu is not None:
            menu.addAction(a)
        if toolbar is not None:
            toolbar.addAction(a)
        if register:
            self._actions[text] = a
        return a

    def _ed(self, fn):
        ed = self.current_editor()
        if ed:
            fn(ed)

    def _build_actions(self):
        mb = self.menuBar()
        m_file, m_edit, m_nav = mb.addMenu("&File"), mb.addMenu("&Edit"), mb.addMenu("&Navigate")
        m_code, m_run, m_view, m_help = mb.addMenu("&Code"), mb.addMenu("&Run"), mb.addMenu("&View"), mb.addMenu("&Help")
        tb = self.addToolBar("Main")
        tb.setObjectName("MainToolbar")
        tb.setMovable(False)
        tb.setIconSize(QtCore.QSize(18, 18))
        tb.setToolButtonStyle(Qt.ToolButtonIconOnly)

        # run configuration selector (PyCharm style)
        self.run_combo = QtWidgets.QComboBox()
        self.run_combo.addItems(self.RUN_MODES)
        cur = SETTINGS.get("run_mode")
        self.run_combo.setCurrentIndex(self.RUN_MODES.index(cur) if cur in self.RUN_MODES else 0)
        self.run_combo.currentTextChanged.connect(lambda t: SETTINGS.set("run_mode", t))
        tb.addWidget(self.run_combo)
        A = self._act
        A("Run", self.run_all, "Ctrl+Return", m_run, tb)
        A("Run (Shift+F10)", self.run_all, "Shift+F10", m_run, register=False)
        A("Run (F5)", self.run_all, "F5", None, register=False)
        A("Debug", self.start_debug, "Shift+F9", m_run, tb)
        A("Stop", self.stop_all, "Ctrl+F2", m_run, tb)
        tb.addSeparator()
        A("Run Selection / Line", self.run_selection, "Ctrl+Shift+Return", m_run)
        A("Toggle Breakpoint", lambda: self._ed(lambda e: e.toggle_breakpoint()), "Ctrl+F8", m_run)
        m_run.addSeparator()
        A("Resume", lambda: self._dbg_do("resume"), "F9", m_run)
        A("Step Over", lambda: self._dbg_do("over"), "F8", m_run)
        A("Step Into", lambda: self._dbg_do("into"), "F7", m_run)
        A("Step Out", lambda: self._dbg_do("out"), "Shift+F8", m_run)
        m_run.addSeparator()
        A("Clear Output", lambda: self.console.clear(), "Ctrl+L", m_run)

        A("New", self.new_tab, "Ctrl+N", m_file, tb)
        A("Open...", self.open_file_dialog, "Ctrl+O", m_file, tb)
        A("Open Folder as Project...", self.open_folder, "Ctrl+Shift+O", m_file)
        self.recent_menu = m_file.addMenu("Recent Files")
        use_icon(self.recent_menu.menuAction(), "recent")
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        self.recent_proj_menu = m_file.addMenu("Recent Projects")
        use_icon(self.recent_proj_menu.menuAction(), "folder")
        self.recent_proj_menu.aboutToShow.connect(self._fill_recent_projects)
        m_file.addSeparator()
        A("Save", self.save_file, "Ctrl+S", m_file, tb)
        A("Save As...", lambda: self.save_file(True), "Ctrl+Shift+S", m_file)
        A("Save All", self.save_all, None, m_file)
        m_file.addSeparator()
        A("Settings...", self.open_settings, "Ctrl+Alt+S", m_file)
        A("Close Tab", lambda: self.close_tab(self.tabs.currentIndex()), "Ctrl+F4", m_file)
        tb.addSeparator()

        A("Find...", self.findbar.open, "Ctrl+F", m_edit, tb)
        A("Find Next", lambda: self.findbar.search(True), "F3", m_edit)
        A("Find Previous", lambda: self.findbar.search(False), "Shift+F3", m_edit)
        A("Replace...", self.findbar.open, "Ctrl+R", m_edit)
        A("Find in Files...", lambda: self.find_in_files(), "Ctrl+Shift+F", m_edit)
        m_edit.addSeparator()
        A("Toggle Comment\tCtrl+/", lambda: self._ed(lambda e: e.toggle_comment()), None, m_edit)
        A("Duplicate Line\tCtrl+D", lambda: self._ed(lambda e: e.duplicate_line()), None, m_edit)
        A("Delete Line\tCtrl+Y", lambda: self._ed(lambda e: e.delete_line()), None, m_edit)
        A("Move Line Up\tAlt+Shift+Up", lambda: self._ed(lambda e: e.move_lines(True)), None, m_edit)
        A("Move Line Down\tAlt+Shift+Down", lambda: self._ed(lambda e: e.move_lines(False)), None, m_edit)
        A("Join Lines\tCtrl+Shift+J", lambda: self._ed(lambda e: e.join_lines()), None, m_edit)
        A("Extend Selection\tCtrl+W", lambda: self._ed(lambda e: e.extend_selection()), None, m_edit)
        A("Toggle Case\tCtrl+Shift+U", lambda: self._ed(lambda e: e.toggle_case()), None, m_edit)
        m_edit.addSeparator()
        A("Fold All", lambda: self._ed(lambda e: e.fold_all()), "Ctrl+Shift+-", m_edit)
        A("Unfold All", lambda: self._ed(lambda e: e.unfold_all()), "Ctrl+Shift+=", m_edit)

        A("Search Everywhere\tCtrl+Alt+F5", lambda: self.search_everywhere("all"), None, m_nav, tb)
        A("Find Action...", lambda: self.search_everywhere("actions"), "Ctrl+Shift+A", m_nav)
        A("Go to File...", lambda: self.search_everywhere("files"), "Ctrl+Shift+N", m_nav)
        A("Go to Symbol...", lambda: self.search_everywhere("symbols"), "Ctrl+Alt+Shift+N", m_nav)
        A("Recent Files...", lambda: self.search_everywhere("recent"), "Ctrl+E", m_nav)
        A("Go to Line...", self.goto_line_dialog, "Ctrl+G", m_nav)
        m_nav.addSeparator()
        A("Go to Declaration", self.goto_declaration, "Ctrl+B", m_nav)
        A("Find Usages", self.find_usages, "Alt+F7", m_nav)
        A("Back", self.nav_back, "Ctrl+Alt+Left", m_nav)
        A("Forward", self.nav_forward, "Ctrl+Alt+Right", m_nav)
        m_nav.addSeparator()
        A("Next Problem", lambda: self.jump_problem(True), "F2", m_nav)
        A("Previous Problem", lambda: self.jump_problem(False), "Shift+F2", m_nav)
        A("Toggle Bookmark", lambda: self._ed(lambda e: e.toggle_bookmark()), "F11", m_nav)
        A("Show Bookmarks", self.show_bookmarks, "Shift+F11", m_nav)

        A("Complete\tCtrl+Space", lambda: self._ed(lambda e: e.show_completions(True)), None, m_code)
        A("Quick Documentation", self.quick_doc, "F1", m_code)
        A("Rename Symbol...", self.rename_symbol, "Shift+F6", m_code)
        A("Surround With...", self.surround_with, "Ctrl+Alt+T", m_code)
        A("Reformat Code", self.reformat_code, "Ctrl+Alt+L", m_code)
        A("Optimize Imports", self.optimize_imports_action, "Ctrl+Alt+O", m_code)

        for d in (self.dock_project, self.dock_structure, self.dock_tools):
            m_view.addAction(d.toggleViewAction())
        m_view.addSeparator()
        m_theme = m_view.addMenu("Theme")
        for name in sorted(THEMES):
            a = m_theme.addAction(name)
            a.triggered.connect(lambda checked=False, n=name: self._set_theme(n))
        a = A("Soft Wrap", lambda: self._set_flag("wrap", self._actions["Soft Wrap"].isChecked()), None, m_view)
        a.setCheckable(True)
        a.setChecked(bool(SETTINGS.get("wrap")))
        a = A("Show Whitespace", lambda: self._set_flag("whitespace", self._actions["Show Whitespace"].isChecked()), None, m_view)
        a.setCheckable(True)
        a.setChecked(bool(SETTINGS.get("whitespace")))
        A("Font Larger", lambda: self._zoom(1), "Ctrl+=", m_view)
        A("Font Smaller", lambda: self._zoom(-1), "Ctrl+-", m_view)
        A("Focus Terminal", lambda: (self.bottom.setCurrentWidget(self.terminal), self.terminal.edit.setFocus()), "Alt+F12", m_view)
        A("Focus Python Console", lambda: (self.bottom.setCurrentWidget(self.pyconsole), self.pyconsole.edit.setFocus()), "Alt+F11", m_view)

        A("Keyboard Shortcuts", self.show_help, "Ctrl+Shift+F1", m_help)
        A("About", self.show_about, None, m_help)

    def show_about(self):
        QtWidgets.QMessageBox.about(
            self, "About " + APP_NAME,
            "<h3>%s %s</h3><p>PyCharm-style Python IDE for Nuke<br>PySide2 / PySide6</p>"
            "<p><b>Author:</b> %s</p>" % (APP_NAME, __version__, __author__))

    def show_help(self):
        B = QtWidgets.QMessageBox(self)
        B.setWindowTitle("Keyboard Shortcuts")
        B.setTextFormat(Qt.RichText)
        B.setText(SHORTCUTS_HELP)
        _exec(B)

    # ------------------------------------------------------------------ #
    # theme / settings
    # ------------------------------------------------------------------ #
    def apply_theme(self):
        set_theme(SETTINGS.get("theme"))
        self.setStyleSheet(build_style())
        for ed in self.all_editors():
            ed.apply_settings()
        refresh_icons()
        self.setWindowIcon(icon("app"))
        if hasattr(self, "tabs"):
            self._refresh_titles()
        cur = self.current_editor() if self.tabs.count() else None
        if cur is not None:
            self.structure.set_symbols(cur.symbols)

    def _set_theme(self, name):
        SETTINGS.set("theme", name)
        self.apply_theme()

    def _set_flag(self, key, value):
        SETTINGS.set(key, bool(value))
        for ed in self.all_editors():
            ed.apply_settings()

    def _zoom(self, d):
        SETTINGS.set("font_size", max(6, int(SETTINGS.get("font_size")) + d))
        for ed in self.all_editors():
            ed.apply_settings()

    def open_settings(self):
        dlg = SettingsDialog(self)
        if _exec(dlg):
            dlg.apply()
            self.apply_theme()

    # ------------------------------------------------------------------ #
    # editors / tabs
    # ------------------------------------------------------------------ #
    def namespace(self):
        if nuke:
            import __main__
            return __main__.__dict__
        return self._ns

    def current_editor(self):
        w = self.tabs.currentWidget()
        return w if isinstance(w, CodeEditor) else None

    def all_editors(self):
        return [self.tabs.widget(i) for i in range(self.tabs.count())]

    def editor_for_path(self, path):
        n = os.path.normcase(os.path.abspath(path))
        for ed in self.all_editors():
            if ed.path and os.path.normcase(os.path.abspath(ed.path)) == n:
                return ed
        return None

    def editor_for_label(self, label):
        for ed in self.all_editors():
            rl = ed.run_label()
            if rl == label or (ed.path and os.path.normcase(os.path.abspath(rl)) == os.path.normcase(label)):
                return ed
        return None

    def _overrides(self):
        return {os.path.abspath(e.path): e.toPlainText() for e in self.all_editors()
                if e.path and e.document().isModified()}

    def new_tab(self, text="", path=None, title=None):
        ed = CodeEditor(self.namespace)
        if path:
            ed.path = path
            ed.title = os.path.basename(path)
        else:
            self._untitled += 1
            ed.title = title or "scratch_%d.py" % self._untitled
        ed.setPlainText(text)
        ed.document().setModified(False)
        ed.document().modificationChanged.connect(lambda *_: self._refresh_titles())
        ed.cursorPositionChanged.connect(self._cursor_moved)
        ed.problemsChanged.connect(self._problems_changed)
        ed.outlineChanged.connect(lambda e=ed: self._outline_changed(e))
        ed.gotoDeclaration.connect(self.goto_declaration)
        ed.breakpointToggled.connect(lambda line, on, e=ed: self._bp_toggled(e, line, on))
        i = self.tabs.addTab(ed, icon(self.file_icon_name(ed)), ed.title)
        self.tabs.setTabToolTip(i, path or ed.title)
        self.tabs.setCurrentIndex(i)
        ed.run_analysis()
        ed.setFocus()
        return ed

    @staticmethod
    def file_icon_name(ed):
        name = (getattr(ed, "path", None) or getattr(ed, "title", "") or "").lower()
        if name.endswith(FileIcons.PY):
            return "file_py"
        if name.endswith(FileIcons.NK):
            return "file_nk"
        return "file_txt"

    def _refresh_titles(self):
        for i in range(self.tabs.count()):
            ed = self.tabs.widget(i)
            self.tabs.setTabText(i, ed.title + (" \u2022" if ed.document().isModified() else ""))
            self.tabs.setTabIcon(i, icon(self.file_icon_name(ed)))

    def _tab_changed(self, *_):
        ed = self.current_editor()
        if ed:
            self.structure.set_symbols(ed.symbols)
        self._cursor_moved()

    def _cursor_moved(self):
        ed = self.current_editor()
        if not ed:
            return
        c = ed.textCursor()
        line = c.blockNumber() + 1
        self.lbl_pos.setText("Ln %d, Col %d" % (line, c.positionInBlock() + 1))
        parts = [os.path.relpath(ed.path, self.project) if ed.path and ed.path.startswith(self.project) else ed.title]
        parts += [s.name for s in symbol_chain(ed.symbols, line)]
        self.crumbs.setText("  \u203A  ".join(parts))

    def _problems_changed(self):
        errs, warns = self.problems_panel.refresh(self.all_editors())
        self.lbl_prob.setText("\u2716 %d   \u25B2 %d" % (errs, warns))
        idx = self.bottom.indexOf(self.problems_panel)
        self.bottom.setTabText(idx, "Problems (%d)" % (errs + warns) if errs + warns else "Problems")

    def _outline_changed(self, ed):
        if ed is self.current_editor():
            self.structure.set_symbols(ed.symbols)
            self._cursor_moved()

    # ------------------------------------------------------------------ #
    # files
    # ------------------------------------------------------------------ #
    def open_file_dialog(self):
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "Open", self.project,
                                                          "Python (*.py);;All files (*)")
        for p in paths:
            self.open_path(p)

    def open_path(self, path, line=None, col=0):
        path = os.path.abspath(path)
        ed = self.editor_for_path(path)
        if ed is None:
            try:
                text = read_text(path)
            except Exception as exc:
                self.console.write("Cannot open %s: %s\n" % (path, exc), "err")
                return None
            ed0 = self.current_editor()
            if ed0 and ed0.path is None and not ed0.toPlainText() and self.tabs.count() == 1:
                self.tabs.removeTab(0)
            ed = self.new_tab(text, path)
            SETTINGS.push_recent("recent_files", path)
        if line:
            self.goto(ed, line, col)
        else:
            self.tabs.setCurrentWidget(ed)
        return ed

    def save_file(self, save_as=False):
        ed = self.current_editor()
        if not ed:
            return False
        path = ed.path
        if save_as or not path:
            start = ed.path or os.path.join(self.project, ed.title)
            path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save", start, "Python (*.py);;All files (*)")
            if not path:
                return False
        try:
            write_text(path, ed.toPlainText())
        except Exception as exc:
            self.console.write("Cannot save: %s\n" % exc, "err")
            return False
        ed.path, ed.title = os.path.abspath(path), os.path.basename(path)
        ed.document().setModified(False)
        self._refresh_titles()
        SETTINGS.push_recent("recent_files", ed.path)
        self.msg("Saved %s" % path)
        return True

    def save_all(self):
        cur = self.tabs.currentIndex()
        for i in range(self.tabs.count()):
            ed = self.tabs.widget(i)
            if ed.path and ed.document().isModified():
                self.tabs.setCurrentIndex(i)
                self.save_file()
        self.tabs.setCurrentIndex(cur)

    def close_tab(self, i):
        if i < 0:
            return
        ed = self.tabs.widget(i)
        if ed.document().isModified():
            B = QtWidgets.QMessageBox
            r = B.question(self, APP_NAME, "Save changes to %s?" % ed.title, B.Save | B.Discard | B.Cancel)
            if r == B.Cancel:
                return
            if r == B.Save:
                self.tabs.setCurrentIndex(i)
                if not self.save_file():
                    return
        self.tabs.removeTab(i)
        ed.deleteLater()
        if self.tabs.count() == 0:
            self.new_tab()
        self._problems_changed()

    def _fill_recent(self):
        self.recent_menu.clear()
        for p in SETTINGS.get("recent_files"):
            if os.path.isfile(p):
                a = self.recent_menu.addAction(p)
                a.triggered.connect(lambda checked=False, path=p: self.open_path(path))

    def _fill_recent_projects(self):
        self.recent_proj_menu.clear()
        for p in SETTINGS.get("recent_projects"):
            if os.path.isdir(p):
                a = self.recent_proj_menu.addAction(p)
                a.triggered.connect(lambda checked=False, path=p: self.set_project(path))

    def open_folder(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Open Folder as Project", self.project)
        if d:
            self.set_project(d)

    def set_project(self, path):
        self.project = os.path.abspath(path)
        SETTINGS.set("project", self.project)
        SETTINGS.push_recent("recent_projects", self.project)
        self.project_panel.set_root(self.project)
        self.terminal.set_cwd(self.project)
        self._file_cache = (None, 0.0, [])
        self._sym_cache = (None, 0.0, [])
        self.lbl_git.setText(self._git_branch())
        self.dock_project.raise_()
        self.msg("Project: %s" % self.project)

    # ------------------------------------------------------------------ #
    # navigation
    # ------------------------------------------------------------------ #
    def _here(self):
        ed = self.current_editor()
        return (ed, ed.textCursor().blockNumber() + 1) if ed else None

    def _push_nav(self):
        if self._navigating:
            return
        h = self._here()
        if h and (not self._nav_back or self._nav_back[-1] != h):
            self._nav_back.append(h)
            del self._nav_back[:-50]
            self._nav_fwd = []

    def goto(self, ed, line, col=0, push=True):
        if push:
            self._push_nav()
        self.tabs.setCurrentWidget(ed)
        ed.goto_line(line, col)

    def _goto_current(self, line):
        ed = self.current_editor()
        if ed:
            self.goto(ed, line)

    def _nav_step(self, src, dst):
        h = self._here()
        while src:
            ed, line = src.pop()
            try:
                if self.tabs.indexOf(ed) < 0:
                    continue
            except RuntimeError:
                continue
            if h:
                dst.append(h)
            self._navigating = True
            try:
                self.goto(ed, line, push=False)
            finally:
                self._navigating = False
            return

    def nav_back(self):
        self._nav_step(self._nav_back, self._nav_fwd)

    def nav_forward(self):
        self._nav_step(self._nav_fwd, self._nav_back)

    def goto_line_dialog(self):
        ed = self.current_editor()
        if not ed:
            return
        text, ok = QtWidgets.QInputDialog.getText(self, "Go to Line", "Line[:column]  (1 - %d):" % ed.blockCount())
        if ok:
            m = re.match(r"^\s*(\d+)(?:\s*[:,]\s*(\d+))?\s*$", text)
            if m:
                self.goto(ed, int(m.group(1)), max(0, int(m.group(2) or 1) - 1))

    def jump_problem(self, forward=True):
        ed = self.current_editor()
        if not ed or not ed.problems:
            self.msg("No problems")
            return
        line, col = ed.textCursor().blockNumber() + 1, ed.textCursor().positionInBlock()
        pos = sorted(ed.problems, key=lambda p: (p.line, p.col))
        if forward:
            nxt = [p for p in pos if (p.line, p.col) > (line, col)] or pos
            p = nxt[0]
        else:
            prv = [p for p in pos if (p.line, p.col) < (line, col)] or pos
            p = prv[-1]
        self.goto(ed, p.line, p.col)
        self.msg("%s: %s" % (p.sev, p.msg), 6000)

    def show_bookmarks(self):
        items = []
        for ed in self.all_editors():
            for ln in ed.bookmark_lines():
                text = ed.document().findBlockByNumber(ln - 1).text().strip()
                items.append(("%s:%d   %s" % (ed.title, ln, text), (ed, ln)))
        if not items:
            self.msg("No bookmarks (F11 toggles one)")
            return
        dlg = QuickList(self, "Bookmarks", lambda q: [i for i in items if fuzzy_score(q, i[0]) is not None],
                        lambda payload: self.goto(payload[0], payload[1]))
        _exec(dlg)

    # ------------------------------------------------------------------ #
    # code actions
    # ------------------------------------------------------------------ #
    def reformat_code(self):
        ed = self.current_editor()
        if not ed:
            return
        try:
            new, engine = reformat_source(ed.toPlainText())
        except ValueError as exc:
            self.console.write("Reformat failed: %s\n" % exc, "err")
            return
        self.msg("Reformatted with %s" % engine if ed.replace_all_text(new) else "Already formatted (%s)" % engine)

    def optimize_imports_action(self):
        ed = self.current_editor()
        if not ed:
            return
        try:
            new, n = optimize_imports(ed.toPlainText())
        except SyntaxError as exc:
            self.console.write("Cannot optimize imports: %s\n" % exc, "err")
            return
        ed.replace_all_text(new)
        self.msg("Optimize imports: removed %d unused" % n)

    def surround_with(self):
        ed = self.current_editor()
        if not ed:
            return
        kind, ok = QtWidgets.QInputDialog.getItem(self, "Surround With", "Template:", list(SURROUND), 0, False)
        if ok:
            ed.surround_with(kind)

    def rename_symbol(self):
        ed = self.current_editor()
        if not ed:
            return
        old, _, _ = ed.word_at()
        if not old or not (old.isidentifier()):
            self.msg("Put the caret on an identifier")
            return
        new, ok = QtWidgets.QInputDialog.getText(self, "Rename", "Rename '%s' to:" % old, text=old)
        if not ok or new == old:
            return
        if not new.isidentifier() or keyword.iskeyword(new):
            self.msg("'%s' is not a valid identifier" % new)
            return
        try:
            text, n = rename_tokens(ed.toPlainText(), old, new)
        except (tokenize.TokenError, IndentationError, SyntaxError) as exc:
            self.console.write("Rename failed: %s\n" % exc, "err")
            return
        ed.replace_all_text(text)
        self.msg("Renamed %d occurrence(s) in this file (strings/comments untouched)" % n, 6000)

    def quick_doc(self):
        ed = self.current_editor()
        if not ed:
            return
        c = ed.textCursor()
        expr = dotted_expression_at(c.block().text(), c.positionInBlock())
        if not expr:
            return
        info = None
        try:
            info = quick_doc(eval(expr, self.namespace()), expr)
        except Exception:
            for s in ed.symbols:                         # fall back to a declaration in this file
                if s.name == expr.split(".")[-1]:
                    info = "%s   <%s, line %d>" % (s.sig, s.kind, s.line)
                    break
        if not info:
            self.msg("No documentation for '%s'" % expr)
            return
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Quick Documentation")
        dlg.resize(620, 340)
        lay = QtWidgets.QVBoxLayout(dlg)
        view = QtWidgets.QPlainTextEdit(info)
        view.setReadOnly(True)
        lay.addWidget(view)
        dlg.show()
        self._doc_dlg = dlg

    def goto_declaration(self):
        ed = self.current_editor()
        if not ed:
            return
        word, _, _ = ed.word_at()
        if not word:
            return
        c = ed.textCursor()
        cands, seen = [], set()

        def add(target, line):
            key = (os.path.normcase(target) if isinstance(target, str) else id(target), line)
            if key not in seen:
                seen.add(key)
                cands.append((target, line))

        for s in ed.symbols:
            if s.name == word:
                add(ed, s.line)
        for i, l in enumerate(ed.toPlainText().split("\n")):
            if re.match(r"^%s\s*(?::[^=]+)?=" % re.escape(word), l):
                add(ed, i + 1)
        try:
            expr = dotted_expression_at(c.block().text(), c.positionInBlock())
            loc = locate_object(eval(expr, self.namespace()))
            if loc:
                add(loc[0], loc[1])
        except Exception:
            pass
        for path, line in find_definitions(self.project, word, self._overrides()):
            tgt = self.editor_for_path(path)
            add(tgt if tgt is not None else path, line)
        if not cands:
            self.msg("No declaration found for '%s'" % word)
            return
        if len(cands) == 1:
            self._open_candidate(cands[0])
            return
        m = QtWidgets.QMenu(self)
        for target, line in cands:
            name = target.title if isinstance(target, CodeEditor) else os.path.relpath(target, self.project) \
                if target.startswith(self.project) else target
            a = m.addAction("%s:%d" % (name, line))
            a.triggered.connect(lambda checked=False, t=(target, line): self._open_candidate(t))
        _exec(m, ed.mapToGlobal(ed.cursorRect().bottomLeft()))

    def _open_candidate(self, cand):
        target, line = cand
        if isinstance(target, CodeEditor):
            self.goto(target, line)
        else:
            self._push_nav()
            self.open_path(target, line)

    def find_usages(self):
        ed = self.current_editor()
        if not ed:
            return
        word, _, _ = ed.word_at()
        if not word:
            return
        res = search_text(self.project, make_pattern(word, False, True, True), overrides=self._overrides())
        if ed.path is None:                                        # unsaved scratch tab
            for i, l in enumerate(ed.toPlainText().split("\n")):
                m = re.search(r"\b%s\b" % re.escape(word), l)
                if m:
                    res.append((ed.title, i + 1, m.start(), l.strip()))
        self.results.show_results("Usages of '%s'" % word, res)
        self.bottom.setCurrentWidget(self.results)

    # ------------------------------------------------------------------ #
    # search
    # ------------------------------------------------------------------ #
    FILE_MASKS = ("*.py", "*.json", "*.txt", "*.md", "*.nk", "*.nknc", "*.gizmo", "*.tcl", "*.ui",
                  "*.yml", "*.yaml", "*.cfg", "*.ini", "*.toml")

    def project_files(self):
        root, t, files = self._file_cache
        if root != self.project or time.time() - t > 20:
            files = [(os.path.relpath(p, self.project), p) for p in iter_files(self.project, self.FILE_MASKS)]
            self._file_cache = (self.project, time.time(), files)
        return files

    def project_symbols(self):
        root, t, syms = self._sym_cache
        if root != self.project or time.time() - t > 60:
            syms = scan_symbols(self.project)
            self._sym_cache = (self.project, time.time(), syms)
        return syms

    def search_everywhere(self, mode="all"):
        titles = {"all": "Search Everywhere", "actions": "Find Action", "files": "Go to File",
                  "symbols": "Go to Symbol", "recent": "Recent Files"}

        def provider(q):
            res = []
            if mode in ("all", "actions"):
                for name, act in self._actions.items():
                    label = name.split("\t")[0].replace("&", "")
                    s = fuzzy_score(q, label)
                    if s is not None and (q or mode == "actions"):
                        sc = act.shortcut().toString()
                        res.append((s + 2, "Action   %s%s" % (label, ("     [%s]" % sc) if sc else ""), ("action", act)))
            if mode in ("all", "files"):
                for rel, path in self.project_files():
                    s = fuzzy_score(q, os.path.basename(rel)) if q else 0
                    if s is None:
                        s2 = fuzzy_score(q, rel)
                        s = None if s2 is None else s2 + 50
                    if s is not None and (q or mode == "files"):
                        res.append((s, "File   %s" % rel, ("file", path, 1)))
            if mode in ("all", "symbols") and (len(q) >= 2 or mode == "symbols"):
                for name, kind, path, line in self.project_symbols():
                    s = fuzzy_score(q, name)
                    if s is not None:
                        res.append((s + 10, "%s   %s    %s:%d" % (kind, name, os.path.basename(path), line),
                                    ("file", path, line)))
            if mode == "recent" or (mode == "all" and not q):
                for p in SETTINGS.get("recent_files"):
                    if os.path.isfile(p) and (not q or fuzzy_score(q, os.path.basename(p)) is not None):
                        res.append((-1, "Recent   %s" % p, ("file", p, 1)))
            res.sort(key=lambda r: (r[0], r[1].lower()))
            return [(label, payload) for _, label, payload in res]

        ed = self.current_editor()
        initial = ed.textCursor().selectedText() if ed and ed.textCursor().hasSelection() and mode != "actions" else ""
        dlg = QuickList(self, titles[mode], provider, self._pick, initial)
        _exec(dlg)

    def _pick(self, payload):
        if payload[0] == "action":
            payload[1].trigger()
        elif payload[0] == "file":
            self._push_nav()
            self.open_path(payload[1], payload[2] if payload[2] > 1 else None)

    def find_in_files(self):
        ed = self.current_editor()
        q = ""
        if ed:
            q = ed.textCursor().selectedText().replace("\u2029", " ") if ed.textCursor().hasSelection() else ed.word_at()[0]
        dlg = FindInFilesDialog(self, self.project, q)
        if not _exec(dlg) or not dlg.params()["query"]:
            return
        p = dlg.params()
        try:
            pat = make_pattern(p["query"], p["regex"], p["case"], p["whole"])
        except re.error as exc:
            self.msg("Bad pattern: %s" % exc, 6000)
            return
        res = search_text(p["root"], pat, p["masks"], overrides=self._overrides())
        if dlg.mode == "find":
            self.results.show_results("'%s'" % p["query"], res)
            self.bottom.setCurrentWidget(self.results)
            return
        files = len(set(r[0] for r in res))
        B = QtWidgets.QMessageBox
        if not res or B.question(self, APP_NAME, "Replace in %d line(s) across %d file(s)?" % (len(res), files),
                                 B.Yes | B.No) != B.Yes:
            return
        repl = p["repl"]
        rep = repl if p["regex"] else (lambda m: repl)
        total = nfiles = 0
        handled = set()
        for e in self.all_editors():
            if e.path and os.path.normcase(os.path.abspath(e.path)).startswith(os.path.normcase(os.path.abspath(p["root"]))) \
                    and any(fnmatch.fnmatch(os.path.basename(e.path), m) for m in p["masks"]):
                new, n = pat.subn(rep, e.toPlainText())
                handled.add(os.path.normcase(os.path.abspath(e.path)))
                if n:
                    e.replace_all_text(new)
                    total += n
                    nfiles += 1
        for path in iter_files(p["root"], p["masks"]):
            if os.path.normcase(os.path.abspath(path)) in handled:
                continue
            try:
                new, n = pat.subn(rep, read_text(path))
                if n:
                    write_text(path, new)
                    total += n
                    nfiles += 1
            except Exception as exc:
                self.console.write("Replace failed in %s: %s\n" % (path, exc), "err")
        self.msg("Replaced %d occurrence(s) in %d file(s)" % (total, nfiles), 6000)

    def scan_todos(self):
        pat = re.compile(r"#.*\b(TODO|FIXME|XXX|HACK)\b")
        res = search_text(self.project, pat, overrides=self._overrides())
        self.todo.show_results("TODO", res)
        self.bottom.setTabText(self.bottom.count() - 1, "TODO (%d)" % len(res) if res else "TODO")

    # ------------------------------------------------------------------ #
    # running
    # ------------------------------------------------------------------ #
    def _print_exc(self, write):
        et, ev, tb = sys.exc_info()
        while tb is not None and os.path.normcase(os.path.abspath(tb.tb_frame.f_code.co_filename)) == THIS_FILE:
            tb = tb.tb_next
        write("".join(traceback.format_exception(et, ev, tb)), "err")

    def _run_guarded(self, fn, label, write=None, quiet=False):
        w = write or self.console.write
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = Stream(w, "out"), Stream(w, "err")
        undo = False
        if nuke:
            try:
                nuke.Undo.begin(APP_NAME)
                undo = True
            except Exception:
                pass
        t0 = time.time()
        try:
            fn()
        except SystemExit:
            w("SystemExit ignored.\n", "err")
        except BaseException:
            self._print_exc(w)
        finally:
            sys.stdout, sys.stderr = old_out, old_err
            if undo:
                try:
                    nuke.Undo.end()
                except Exception:
                    pass
        if not quiet:
            w("# %s - %.3fs\n" % (label, time.time() - t0), "info")

    def run_code(self, code_text, label="<editor>"):
        if not code_text.strip():
            return
        if self._debugging:
            self.msg("Finish or stop the debug session first")
            return
        ns = self.namespace()
        register_source(label, code_text)

        def go():
            try:
                compiled, is_eval = compile(code_text.strip(), label, "eval"), True
            except SyntaxError:
                compiled, is_eval = compile(code_text, label, "exec"), False
            if is_eval:
                res = eval(compiled, ns)
                if res is not None:
                    ns["_"] = res
                    self.console.write(repr(res) + "\n", "result")
            else:
                exec(compiled, ns)

        self.bottom.setCurrentWidget(self.page_run)
        self._run_guarded(go, label)

    def _console_run(self, compiled, write):
        ns = self.namespace()
        old = sys.displayhook

        def hook(v):
            if v is not None:
                ns["_"] = v
                write(repr(v) + "\n", "result")

        sys.displayhook = hook
        try:
            self._run_guarded(lambda: exec(compiled, ns), "<console>", write=write, quiet=True)
        finally:
            sys.displayhook = old

    def run_all(self):
        ed = self.current_editor()
        if not ed:
            return
        mode = self.run_combo.currentText()
        if mode == self.RUN_MODES[0]:
            self.run_code(ed.toPlainText(), ed.run_label())
        else:
            self.run_external(ed, mode)

    def run_selection(self):
        ed = self.current_editor()
        if not ed:
            return
        c = ed.textCursor()
        code_text = c.selectedText().replace("\u2029", "\n") if c.hasSelection() else c.block().text()
        self.run_code(code_text, "<%s selection>" % ed.title)

    def _script_for_external(self, ed):
        if ed.path:
            if ed.document().isModified():
                self.tabs.setCurrentWidget(ed)
                self.save_file()
            return ed.path
        tmp = os.path.join(DATA_DIR, "tmp")
        _ensure_dir(tmp)
        name = re.sub(r"[^\w\.-]", "_", ed.title)
        p = os.path.join(tmp, name if name.endswith(".py") else name + ".py")
        write_text(p, ed.toPlainText())
        return p

    def _proc_running(self):
        return self._proc is not None and self._proc.state() != QtCore.QProcess.NotRunning

    def run_external(self, ed, mode):
        if self._proc_running():
            self.console.write("A process is already running - press Stop first.\n", "err")
            return
        path = self._script_for_external(ed)
        if mode.startswith("Nuke"):
            prog = (nuke.EXE_PATH if nuke else None) or shutil.which("nuke")
            args = ["-t", path]
        else:
            prog = SETTINGS.get("interpreter") or shutil.which("python3") or shutil.which("python")
            args = ["-u", path]
        if not prog:
            self.console.write("No interpreter found. Set one in Settings.\n", "err")
            return
        proc = QtCore.QProcess(self)
        proc.setWorkingDirectory(os.path.dirname(path) if ed.path else self.project)
        env = QtCore.QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        if mode.startswith("Python"):
            env.remove("PYTHONHOME")
        proc.setProcessEnvironment(env)
        proc.readyReadStandardOutput.connect(
            lambda: self.console.write(proc.readAllStandardOutput().data().decode("utf-8", "replace")))
        proc.readyReadStandardError.connect(
            lambda: self.console.write(proc.readAllStandardError().data().decode("utf-8", "replace"), "err"))
        t0 = time.time()
        proc.finished.connect(lambda code=0, status=None: self.console.write(
            "# process finished with exit code %s (%.2fs)\n" % (code, time.time() - t0), "info"))
        proc.errorOccurred.connect(lambda err: self.console.write("# process error: %s\n" % proc.errorString(), "err"))
        self._proc = proc
        self.bottom.setCurrentWidget(self.page_run)
        self.console.write("$ %s %s\n" % (prog, " ".join(args)), "prompt")
        proc.start(prog, args)

    def stop_all(self):
        if self._dbg_loop is not None:
            self._dbg_do("stop")
        if self._proc_running():
            self._proc.kill()
            self.console.write("# process killed\n", "err")
        self.terminal.stop()

    # ------------------------------------------------------------------ #
    # debugger
    # ------------------------------------------------------------------ #
    def _is_known_file(self, fn):
        return self.editor_for_label(fn) is not None or (os.path.isfile(fn) and fn != THIS_FILE)

    def _bp_toggled(self, ed, line, on):
        if self._debugging and self._dbg is not None:
            label = ed.run_label()
            if on:
                self._dbg.set_break(label, line)
            else:
                self._dbg.clear_break(label, line)

    def start_debug(self):
        if self._debugging:
            self.msg("Already debugging")
            return
        ed = self.current_editor()
        if not ed:
            return
        text, label = ed.toPlainText(), ed.run_label()
        try:
            compiled = compile(text, label, "exec")
        except SyntaxError:
            self._run_guarded(lambda: compile(text, label, "exec"), label)
            return
        register_source(label, text)
        dbg = Debugger(self._dbg_stopped, self._is_known_file)
        n = 0
        for e in self.all_editors():
            if e is not ed and not e.path:
                continue
            for ln in e.breakpoint_lines():
                err = dbg.set_break(e.run_label(), ln)
                if err:
                    self.console.write("Breakpoint: %s\n" % err, "err")
                else:
                    n += 1
        self.console.write("# debugging %s  (%d breakpoint%s)\n" % (label, n, "" if n == 1 else "s"), "info")
        self._dbg, self._debugging = dbg, True
        self.bottom.setCurrentWidget(self.page_run)
        ns = self.namespace()
        try:
            self._run_guarded(lambda: dbg.run(compiled, ns), label + " [debug]")
        finally:
            self._debugging, self._dbg = False, None
            self.debug_panel.set_active(False)
            for e in self.all_editors():
                e.set_debug_line(-1)

    def _dbg_stopped(self, frame):
        frames, f = [], frame
        while f is not None and f is not self._dbg.botframe:
            frames.append(f)
            f = f.f_back
        self._dbg_frames = frames
        self.debug_panel.set_active(True)
        self.debug_panel.show_frames([(os.path.basename(x.f_code.co_filename), x.f_lineno, x.f_code.co_name)
                                      for x in frames])
        self._dbg_show_frame(0)
        self.bottom.setCurrentWidget(self.debug_panel)
        self.raise_()
        self.activateWindow()
        self._dbg_action = "resume"
        loop = QtCore.QEventLoop()
        self._dbg_loop = loop
        _exec(loop)
        self._dbg_loop = None
        self.debug_panel.set_active(False)
        for e in self.all_editors():
            e.set_debug_line(-1)
        return self._dbg_action

    def _dbg_show_frame(self, i):
        if not (0 <= i < len(self._dbg_frames)):
            return
        fr = self._dbg_frames[i]
        fn = fr.f_code.co_filename
        ed = self.editor_for_label(os.path.normcase(os.path.abspath(fn)) if not fn.startswith("<") else fn)
        if ed is None and os.path.isfile(fn):
            ed = self.open_path(fn)
        for e in self.all_editors():
            if e is not ed:
                e.set_debug_line(-1)
        if ed is not None:
            ed.set_debug_line(fr.f_lineno - 1)
            self.tabs.setCurrentWidget(ed)
            ed.reveal_line(fr.f_lineno - 1)
            block = ed.document().findBlockByNumber(fr.f_lineno - 1)
            c = ed.textCursor()
            c.setPosition(block.position())
            ed.setTextCursor(c)
            ed.centerCursor()
        loc = dict(fr.f_locals)
        if fr.f_locals is fr.f_globals:
            loc = {k: v for k, v in loc.items() if not k.startswith("_") and not (
                inspect.ismodule(v) or inspect.isfunction(v) or inspect.isclass(v))}
        self.debug_panel.show_vars(loc)

    def _dbg_do(self, action):
        if self._dbg_loop is not None:
            self._dbg_action = action
            self._dbg_loop.quit()

    def _dbg_eval(self, text):
        row = self.debug_panel.frames.currentRow()
        if not (0 <= row < len(self._dbg_frames)):
            return
        fr = self._dbg_frames[row]
        try:
            try:
                res = eval(text, fr.f_globals, fr.f_locals)
                val, typ = safe_repr(res), type(res).__name__
            except SyntaxError:
                exec(text, fr.f_globals, fr.f_locals)
                val, typ = "(executed)", "stmt"
        except Exception as exc:
            val, typ = "%s: %s" % (type(exc).__name__, exc), "error"
        self.debug_panel.vars.insertTopLevelItem(0, QtWidgets.QTreeWidgetItem([text, typ, val]))

    # ------------------------------------------------------------------ #
    # session
    # ------------------------------------------------------------------ #
    def save_session(self):
        try:
            _ensure_dir()
            tabs = []
            for ed in self.all_editors():
                tabs.append({"path": ed.path, "title": ed.title, "text": ed.toPlainText(),
                             "line": ed.textCursor().blockNumber() + 1,
                             "bp": ed.breakpoint_lines(), "bm": ed.bookmark_lines()})
            with open(SESSION_FILE, "w") as f:
                json.dump({"current": self.tabs.currentIndex(), "tabs": tabs}, f)
            SETTINGS.set("geometry", bytes(self.saveGeometry().toBase64().data()).decode("ascii"))
        except Exception:
            pass

    def _restore_session(self):
        try:
            g = SETTINGS.get("geometry")
            if g:
                self.restoreGeometry(QtCore.QByteArray.fromBase64(g.encode("ascii")))
        except Exception:
            pass
        try:
            with open(SESSION_FILE, "r") as f:
                data = json.load(f)
        except Exception:
            return
        for t in data.get("tabs", []):
            ed = self.new_tab(t.get("text", ""), t.get("path"), t.get("title"))
            if t.get("path"):
                try:
                    if read_text(t["path"]) != t.get("text", ""):
                        ed.document().setModified(True)
                except Exception:
                    ed.document().setModified(True)
            for ln in t.get("bp", []):
                b = ed.document().findBlockByNumber(ln - 1)
                if b.isValid():
                    bdata(b, True).bp = True
            for ln in t.get("bm", []):
                b = ed.document().findBlockByNumber(ln - 1)
                if b.isValid():
                    bdata(b, True).bookmark = True
            ed.gutter.update()
            ed.refresh_selections()
            if t.get("line", 1) > 1:
                ed.goto_line(t["line"])
        self._refresh_titles()
        if 0 <= data.get("current", 0) < self.tabs.count():
            self.tabs.setCurrentIndex(data["current"])

    def closeEvent(self, event):
        if self._dbg_loop is not None:
            self._dbg_do("stop")
        if self._proc_running():
            self._proc.kill()
        self.terminal.stop()
        self.save_session()
        app = QtWidgets.QApplication.instance()
        if app and getattr(self, "_shift_filter", None):
            app.removeEventFilter(self._shift_filter)
        super().closeEvent(event)


# =========================================================================== #
#  ENTRY POINTS
# =========================================================================== #
_window = None


def show_window():
    """Open (or raise) the floating editor window inside Nuke."""
    global _window
    if _window is None:
        _window = NukeCodeEditor(nuke_main_window())
    _window.show()
    _window.raise_()
    _window.activateWindow()
    return _window


def register_panel():
    """Register as a dockable Nuke panel (Windows > Custom > Nuke Code Editor)."""
    try:
        import nukescripts
        nukescripts.panels.registerWidgetAsPanel(
            "nuke_code_editor.NukeCodeEditor", APP_NAME, "com.td.NukeCodeEditor")
    except Exception as exc:
        print("NukeCodeEditor: panel registration failed: %s" % exc)


if __name__ == "__main__":
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = NukeCodeEditor()
    win.show()
    sys.exit(_exec(app))
