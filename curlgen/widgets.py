"""可复用 UI 组件：语法高亮、下拉选择、键值对编辑器"""
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QMenu,
    QWidgetAction, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView,
)
from PyQt5.QtCore import Qt, QRegExp, QPoint, pyqtSignal
from PyQt5.QtGui import QFont, QColor, QTextCharFormat, QSyntaxHighlighter

from .theme import DarkTheme, lighten, darken, ghost_button_qss


# ──────────────────────────── 语法高亮 ────────────────────────────
class CurlHighlighter(QSyntaxHighlighter):
    def __init__(self, parent, theme):
        super().__init__(parent)
        self.theme = theme
        self._build_rules()

    def _build_rules(self):
        rules = [
            (QRegExp(r'\bcurl\b'), self.theme.ACCENT, True),
            (QRegExp(r'-[a-zA-Z]+|--[a-zA-Z-]+'), self.theme.MAUVE, False),
            (QRegExp(r'"[^"]*"'), self.theme.GREEN, False),
            (QRegExp(r"'[^']*'"), self.theme.GREEN, False),
            (QRegExp(r'https?://[^\s"\'\\]+'), self.theme.TEAL, False),
            (QRegExp(r'#.*'), self.theme.TEXT_MUTED, False),
        ]
        self.rules = []
        for pattern, color, bold in rules:
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            if bold:
                fmt.setFontWeight(QFont.Bold)
            self.rules.append((pattern, fmt))

    def highlightBlock(self, text):
        for pattern, fmt in self.rules:
            idx = pattern.indexIn(text, 0)
            while idx >= 0:
                length = pattern.matchedLength()
                self.setFormat(idx, length, fmt)
                idx = pattern.indexIn(text, idx + length)


# ──────────────────────────── 通用下拉组件 ────────────────────────────
class StyledDropdown(QPushButton):
    """统一美化的下拉选择框：按钮 + 弹出菜单"""

    currentTextChanged = pyqtSignal(str)

    def __init__(self, items=None, parent=None):
        super().__init__(parent)
        self._items = items or []
        self._current = self._items[0] if self._items else ""
        self.setFont(QFont("Segoe UI", 11))
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)

        self.menu = QMenu(self)
        self.menu.setAttribute(Qt.WA_TranslucentBackground)
        self._actions = {}
        for item_text in self._items:
            action = QWidgetAction(self.menu)
            w = DropdownMenuItem(item_text, self.menu)
            action.setDefaultWidget(w)
            action.setData(item_text)
            self.menu.addAction(action)
            self._actions[item_text] = (action, w)
        self._update_text()

    def _update_text(self):
        self.setText(f"  {self._current}  ▾")

    def currentText(self):
        return self._current

    def setCurrentText(self, text):
        if text in self._items:
            self._current = text
            self._update_text()

    def set_items(self, items):
        self._items = items
        self.menu.clear()
        self._actions.clear()
        for item_text in self._items:
            action = QWidgetAction(self.menu)
            w = DropdownMenuItem(item_text, self.menu)
            action.setDefaultWidget(w)
            action.setData(item_text)
            self.menu.addAction(action)
            self._actions[item_text] = (action, w)
        if self._current not in self._items:
            self._current = self._items[0] if self._items else ""
        self._update_text()

    def _select(self, text):
        self._current = text
        self._update_text()
        self.menu.hide()
        self.currentTextChanged.emit(text)

    def mousePressEvent(self, event):
        self.apply_theme(self._find_theme())
        pos = self.mapToGlobal(QPoint(0, self.height() + 4))
        self.menu.popup(pos)

    def _find_theme(self):
        parent = self.parent()
        while parent:
            if hasattr(parent, 'theme'):
                return parent.theme
            parent = parent.parent()
        return DarkTheme()

    def apply_theme(self, theme):
        t = theme
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {t.BG_SURFACE};
                color: {t.TEXT_PRIMARY};
                border: 1px solid {t.BORDER};
                border-radius: 8px;
                padding: 6px 14px;
                font-size: 12px;
                text-align: left;
            }}
            QPushButton:hover {{
                background-color: {t.BG_HOVER};
                border-color: {t.ACCENT};
            }}
        """)
        self.menu.setStyleSheet(f"""
            QMenu {{
                background-color: {t.BG_SURFACE};
                border: 1px solid {t.BORDER};
                border-radius: 10px;
                padding: 4px;
            }}
            QMenu::item {{
                background-color: transparent;
                padding: 0px;
                border-radius: 6px;
            }}
            QMenu::item:selected {{
                background-color: transparent;
            }}
        """)
        for action, w in self._actions.values():
            w.apply_theme(t)


class DropdownMenuItem(QWidget):
    """下拉菜单项"""
    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.text = text
        self.setCursor(Qt.PointingHandCursor)
        self._base_style = "background-color: transparent; border-radius: 6px;"
        self._hover_style = "background-color: rgba(128,128,128,0.15); border-radius: 6px;"
        self.setStyleSheet(self._base_style)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 18, 8)
        layout.setSpacing(8)

        self.label = QLabel(text)
        self.label.setFont(QFont("Segoe UI", 11))
        layout.addWidget(self.label)
        layout.addStretch()

    def apply_theme(self, theme):
        self.label.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; background: transparent;")

    def mousePressEvent(self, event):
        parent = self.parent()
        while parent:
            if isinstance(parent, StyledDropdown):
                parent._select(self.text)
                return
            parent = parent.parent()

    def enterEvent(self, event):
        self.setStyleSheet(self._hover_style)

    def leaveEvent(self, event):
        self.setStyleSheet(self._base_style)


# ──────────────────────────── HTTP 方法下拉 ────────────────────────────
METHOD_COLORS = {
    "GET":     "#16b981",
    "POST":    "#3b82f6",
    "PUT":     "#e8930c",
    "DELETE":  "#e23d5a",
    "PATCH":   "#8b5cf6",
    "HEAD":    "#0aa2b8",
    "OPTIONS": "#7c8aa0",
}

METHODS_LIST = ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]


class MethodMenuItem(QWidget):
    """下拉菜单中的单个方法项：彩色圆点 + 方法名 + hover 效果"""
    def __init__(self, method, parent=None):
        super().__init__(parent)
        self.method = method
        color = METHOD_COLORS.get(method, "#888")
        self.setCursor(Qt.PointingHandCursor)
        self._base_style = "background-color: transparent; border-radius: 6px;"
        self._hover_style = "background-color: rgba(128,128,128,0.15); border-radius: 6px;"
        self.setStyleSheet(self._base_style)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 7, 16, 7)
        layout.setSpacing(10)

        dot = QLabel()
        dot.setFixedSize(12, 12)
        dot.setStyleSheet(f"""
            QLabel {{
                background-color: {color};
                border-radius: 6px;
                min-width: 12px;
                min-height: 12px;
            }}
        """)
        layout.addWidget(dot)

        name = QLabel(method)
        name.setFont(QFont("Segoe UI", 11, QFont.Bold))
        name.setStyleSheet(f"color: {color}; background: transparent;")
        layout.addWidget(name)
        layout.addStretch()

    def mousePressEvent(self, event):
        parent = self.parent()
        while parent:
            if isinstance(parent, MethodDropdown):
                parent._select(self.method)
                return
            parent = parent.parent()

    def enterEvent(self, event):
        self.setStyleSheet(self._hover_style)

    def leaveEvent(self, event):
        self.setStyleSheet(self._base_style)


class MethodDropdown(QPushButton):
    """自定义方法下拉按钮：彩色按钮 + ▾ 箭头 + 弹出菜单"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._current = "GET"
        self.setFont(QFont("Segoe UI", 11, QFont.Bold))
        self.setFixedWidth(115)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)

        self.menu = QMenu(self)
        self.menu.setAttribute(Qt.WA_TranslucentBackground)

        self._actions = {}
        for method in METHODS_LIST:
            action = QWidgetAction(self.menu)
            item = MethodMenuItem(method, self.menu)
            action.setDefaultWidget(item)
            action.setData(method)
            self.menu.addAction(action)
            self._actions[method] = (action, item)

        self._update_text()

    def _select(self, method):
        self._current = method
        self._update_text()
        self.menu.hide()
        parent = self.parent()
        while parent:
            if hasattr(parent, '_on_method_change'):
                parent._on_method_change(method)
                break
            parent = parent.parent()

    def _update_text(self):
        self.setText(f"  {self._current}  ▾")

    def currentText(self):
        return self._current

    def apply_theme(self, theme):
        t = theme
        color = METHOD_COLORS.get(self._current, t.ACCENT)
        self._apply_color(color, t)
        self.menu.setStyleSheet(f"""
            QMenu {{
                background-color: {t.BG_SURFACE};
                border: 1px solid {t.BORDER};
                border-radius: 10px;
                padding: 4px;
            }}
            QMenu::item {{
                background-color: transparent;
                padding: 0px;
                border-radius: 6px;
            }}
            QMenu::item:selected {{
                background-color: transparent;
            }}
        """)

    def _apply_color(self, color, t):
        self.setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {lighten(color, 14)}, stop:1 {color});
                color: #ffffff;
                border: none;
                border-radius: 8px;
                padding: 0 10px;
                font-weight: bold;
                font-size: 12px;
                min-height: 32px;
                text-align: center;
            }}
            QPushButton:hover {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {lighten(color, 28)}, stop:1 {lighten(color, 14)});
            }}
            QPushButton:pressed {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {color}, stop:1 {darken(color, 15)});
            }}
        """)

    def mousePressEvent(self, event):
        self._apply_color(METHOD_COLORS.get(self._current, "#888"), self._find_theme())
        pos = self.mapToGlobal(QPoint(0, self.height() + 4))
        self.menu.popup(pos)

    def _find_theme(self):
        parent = self.parent()
        while parent:
            if hasattr(parent, 'theme'):
                return parent.theme
            parent = parent.parent()
        return DarkTheme()


# ──────────────────────────── 动态键值对编辑器 ────────────────────────────
class KeyValueEditor(QWidget):
    def __init__(self, placeholder_key="Key", placeholder_val="Value", parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(4)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels([placeholder_key, placeholder_val])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)

        btn_layout = QHBoxLayout()
        self.add_btn = QPushButton("+  添加")
        self.add_btn.setFixedWidth(80)
        self.add_btn.clicked.connect(self.add_row)

        self.remove_btn = QPushButton("-  删除")
        self.remove_btn.setFixedWidth(80)
        self.remove_btn.clicked.connect(self.remove_row)

        self.clear_btn = QPushButton("清空")
        self.clear_btn.setFixedWidth(60)
        self.clear_btn.clicked.connect(self.clear_all)

        btn_layout.addWidget(self.add_btn)
        btn_layout.addWidget(self.remove_btn)
        btn_layout.addWidget(self.clear_btn)
        btn_layout.addStretch()

        self.layout.addWidget(self.table)
        self.layout.addLayout(btn_layout)

    def apply_theme(self, theme):
        self.table.setStyleSheet("QTableWidget { border-radius: 8px; }")
        self.add_btn.setStyleSheet(ghost_button_qss(theme, theme.GREEN))
        self.remove_btn.setStyleSheet(ghost_button_qss(theme, theme.RED))
        self.clear_btn.setStyleSheet(ghost_button_qss(theme, theme.YELLOW))

    def add_row(self, key="", val=""):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(key))
        self.table.setItem(row, 1, QTableWidgetItem(val))
        self.table.setCurrentCell(row, 0)
        self.table.editItem(self.table.item(row, 0))

    def remove_row(self):
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)

    def clear_all(self):
        self.table.setRowCount(0)

    def get_pairs(self):
        pairs = []
        for r in range(self.table.rowCount()):
            k = self.table.item(r, 0)
            v = self.table.item(r, 1)
            key = k.text().strip() if k else ""
            val = v.text().strip() if v else ""
            if key:
                pairs.append((key, val))
        return pairs

    def set_pairs(self, pairs):
        self.table.setRowCount(0)
        for k, v in pairs:
            self.add_row(k, v)
