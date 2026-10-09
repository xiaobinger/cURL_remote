"""设计令牌（Design Tokens）与全局样式表

视觉方向：「深空座舱」—— 深蓝墨色底 + 电光蓝/青渐变主色，圆角面板、
细边框、轻发光主按钮；亮色主题为「晨雾」浅蓝灰纸感。
"""


# ──────────────────── 通用令牌（跨主题不变） ────────────────────
FONT_UI = '"Segoe UI", "Microsoft YaHei UI", sans-serif'
FONT_MONO = '"Consolas", "Cascadia Mono", "Courier New", monospace'

RADIUS_SM = 6
RADIUS_MD = 10
RADIUS_LG = 14


def lighten(hex_color, amount=20):
    hex_color = hex_color.lstrip("#")
    r = min(255, int(hex_color[0:2], 16) + amount)
    g = min(255, int(hex_color[2:4], 16) + amount)
    b = min(255, int(hex_color[4:6], 16) + amount)
    return f"#{r:02x}{g:02x}{b:02x}"


def darken(hex_color, amount=15):
    hex_color = hex_color.lstrip("#")
    r = max(0, int(hex_color[0:2], 16) - amount)
    g = max(0, int(hex_color[2:4], 16) - amount)
    b = max(0, int(hex_color[4:6], 16) - amount)
    return f"#{r:02x}{g:02x}{b:02x}"


# ──────────────────── 暗色主题：深空座舱 ────────────────────
class DarkTheme:
    NAME = "dark"

    BG_PRIMARY = "#0d1424"      # 深空墨蓝（窗口底）
    BG_SECONDARY = "#0a101d"    # 更深一档（侧栏/面板底）
    BG_SURFACE = "#16213a"      # 抬升面板（卡片/输入框）
    BG_HOVER = "#223154"        # 悬停态
    TEXT_PRIMARY = "#e8eefb"
    TEXT_SECONDARY = "#9db1cf"
    TEXT_MUTED = "#5d6f8c"
    ACCENT = "#4c8dff"          # 电光蓝
    ACCENT_HOVER = "#6ea1ff"
    GREEN = "#2dd4a0"
    RED = "#f4506c"
    YELLOW = "#f7c948"
    MAUVE = "#a78bfa"
    PEACH = "#fb923c"
    TEAL = "#22d3ee"
    PINK = "#f472b6"
    BORDER = "#253552"

    # 主按钮渐变（电光蓝 → 青）
    GRADIENT_PRIMARY = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4c8dff, stop:1 #22d3ee)"
    GRADIENT_PRIMARY_HOVER = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #6ea1ff, stop:1 #4fe0f5)"
    GRADIENT_PRIMARY_DOWN = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3a72e0, stop:1 #17b9d1)"
    GRADIENT_HEADER = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4c8dff, stop:0.55 #7c6cff, stop:1 #22d3ee)"
    CHECKER = "#ffffff"


# ──────────────────── 亮色主题：晨雾 ────────────────────
class LightTheme:
    NAME = "light"

    BG_PRIMARY = "#f5f8fd"      # 晨雾纸白
    BG_SECONDARY = "#eaf0f8"
    BG_SURFACE = "#ffffff"
    BG_HOVER = "#dfe8f5"
    TEXT_PRIMARY = "#15233d"
    TEXT_SECONDARY = "#45587a"
    TEXT_MUTED = "#8494ab"
    ACCENT = "#2f6bff"
    ACCENT_HOVER = "#1d54e8"
    GREEN = "#0fae7e"
    RED = "#e23d5a"
    YELLOW = "#c98a04"
    MAUVE = "#7c5cf0"
    PEACH = "#e8681a"
    TEAL = "#0aa2b8"
    PINK = "#d6429a"
    BORDER = "#d3dfee"

    GRADIENT_PRIMARY = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2f6bff, stop:1 #4f46e5)"
    GRADIENT_PRIMARY_HOVER = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4a80ff, stop:1 #635eea)"
    GRADIENT_PRIMARY_DOWN = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2559d8, stop:1 #3f37c9)"
    GRADIENT_HEADER = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2f6bff, stop:0.55 #6d5df0, stop:1 #0aa2b8)"
    CHECKER = "#15233d"


# ══════════════════════ 样式构建 ══════════════════════
def build_app_stylesheet(t):
    """全局 QSS：菜单 / 表单 / 标签页 / 表格 / 滚动条 / 工具提示 等"""
    return f"""
    /* ── 基底 ── */
    QMainWindow, QDialog {{ background-color: {t.BG_PRIMARY}; }}
    QWidget {{
        background-color: {t.BG_PRIMARY};
        color: {t.TEXT_PRIMARY};
        font-family: {FONT_UI};
        font-size: 13px;
    }}
    QLabel {{ background: transparent; }}
    QToolTip {{
        background-color: {t.BG_SURFACE};
        color: {t.TEXT_PRIMARY};
        border: 1px solid {t.ACCENT};
        border-radius: {RADIUS_SM}px;
        padding: 6px 10px;
        font-size: 12px;
    }}

    /* ── 菜单栏 / 菜单 ── */
    QMenuBar {{
        background-color: {t.BG_SECONDARY};
        color: {t.TEXT_SECONDARY};
        border-bottom: 1px solid {t.BORDER};
        padding: 2px 6px;
        font-size: 13px;
    }}
    QMenuBar::item {{
        background: transparent;
        padding: 7px 18px;
        border-radius: {RADIUS_SM}px;
        font-weight: 600;
    }}
    QMenuBar::item:selected {{
        background-color: {t.BG_HOVER};
        color: {t.TEXT_PRIMARY};
    }}
    QMenuBar::item:pressed {{ background-color: {t.ACCENT}; color: {t.CHECKER}; }}
    QMenu {{
        background-color: {t.BG_SURFACE};
        border: 1px solid {t.BORDER};
        border-radius: {RADIUS_MD}px;
        padding: 6px;
    }}
    QMenu::item {{
        padding: 8px 28px 8px 22px;
        border-radius: {RADIUS_SM}px;
        font-size: 13px;
        color: {t.TEXT_PRIMARY};
    }}
    QMenu::item:selected {{ background-color: {t.BG_HOVER}; color: {t.ACCENT}; }}
    QMenu::separator {{ height: 1px; background-color: {t.BORDER}; margin: 5px 10px; }}

    /* ── 分组框 ── */
    QGroupBox {{
        color: {t.TEXT_PRIMARY};
        background-color: {t.BG_SURFACE};
        border: 1px solid {t.BORDER};
        border-radius: {RADIUS_MD}px;
        margin-top: 14px;
        padding: 16px 12px 12px 12px;
        font-weight: bold;
        font-size: 11px;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top left;
        padding: 0 10px;
        color: {t.ACCENT};
        font-size: 12px;
    }}

    /* ── 输入 ── */
    QLineEdit, QPlainTextEdit, QTextEdit {{
        background-color: {t.BG_SURFACE};
        color: {t.TEXT_PRIMARY};
        border: 1px solid {t.BORDER};
        border-radius: {RADIUS_SM}px;
        padding: 8px 12px;
        font-size: 13px;
        selection-background-color: {t.ACCENT};
        selection-color: {t.CHECKER};
    }}
    QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{ border-color: {t.ACCENT}; }}
    QLineEdit:hover, QPlainTextEdit:hover, QTextEdit:hover {{ border-color: {lighten(t.BORDER, 20)}; }}
    QLineEdit:disabled, QTextEdit:disabled {{
        background-color: {t.BG_SECONDARY};
        color: {t.TEXT_MUTED};
    }}
    QTextEdit, QPlainTextEdit {{
        font-family: {FONT_MONO};
        font-size: 12px;
        padding: 10px;
        border-radius: {RADIUS_MD}px;
    }}

    /* ── 复选框 / 单选框 ── */
    QCheckBox, QRadioButton {{
        color: {t.TEXT_PRIMARY};
        font-size: 13px;
        spacing: 9px;
        padding: 4px 0;
        background: transparent;
    }}
    QCheckBox::indicator {{
        width: 17px; height: 17px;
        border-radius: 5px;
        border: 2px solid {t.BORDER};
        background-color: {t.BG_SURFACE};
    }}
    QCheckBox::indicator:hover {{ border-color: {t.ACCENT}; }}
    QCheckBox::indicator:checked {{
        background-color: {t.ACCENT};
        border-color: {t.ACCENT};
    }}
    QRadioButton::indicator {{
        width: 18px; height: 18px;
        border-radius: 10px;
        border: 2px solid {t.BORDER};
        background-color: {t.BG_SURFACE};
    }}
    QRadioButton::indicator:hover {{ border-color: {t.ACCENT}; }}
    QRadioButton::indicator:checked {{ background-color: {t.ACCENT}; border-color: {t.ACCENT}; }}

    /* ── 标签页 ── */
    QTabWidget::pane {{
        border: 1px solid {t.BORDER};
        border-radius: {RADIUS_MD}px;
        background-color: {t.BG_SECONDARY};
        top: -1px;
    }}
    QTabBar {{ qproperty-drawBase: 0; }}
    QTabBar::tab {{
        background-color: transparent;
        color: {t.TEXT_SECONDARY};
        padding: 10px 26px;
        margin-right: 4px;
        min-width: 92px;
        border-top-left-radius: {RADIUS_MD}px;
        border-top-right-radius: {RADIUS_MD}px;
        font-size: 13px;
        font-weight: 600;
    }}
    QTabBar::tab:selected {{
        background-color: {t.BG_SURFACE};
        color: {t.ACCENT};
        border: 1px solid {t.BORDER};
        border-bottom: 3px solid {t.ACCENT};
    }}
    QTabBar::tab:hover:!selected {{ color: {t.TEXT_PRIMARY}; background-color: {t.BG_HOVER}; }}
    QTabBar::tab:disabled {{ color: {t.TEXT_MUTED}; }}

    /* ── 表格 ── */
    QTableWidget, QTableView {{
        background-color: {t.BG_SURFACE};
        color: {t.TEXT_PRIMARY};
        border: 1px solid {t.BORDER};
        border-radius: {RADIUS_MD}px;
        gridline-color: {t.BORDER};
        font-size: 12px;
    }}
    QTableWidget::item {{ padding: 6px; background: transparent; }}
    QTableWidget::item:selected {{ background-color: {t.BG_HOVER}; color: {t.TEXT_PRIMARY}; }}
    QHeaderView::section {{
        background-color: {t.BG_SECONDARY};
        color: {t.TEXT_SECONDARY};
        padding: 7px 8px;
        border: none;
        border-bottom: 1px solid {t.BORDER};
        border-right: 1px solid {t.BORDER};
        font-weight: bold;
        font-size: 12px;
    }}
    QTableWidget QLineEdit {{
        background: transparent;
        border: none;
        padding: 4px;
    }}

    /* ── 下拉（原生） ── */
    QComboBox {{
        background-color: {t.BG_SURFACE};
        color: {t.TEXT_PRIMARY};
        border: 1px solid {t.BORDER};
        border-radius: {RADIUS_SM}px;
        padding: 5px 10px;
        font-size: 12px;
        min-height: 22px;
    }}
    QComboBox:focus {{ border-color: {t.ACCENT}; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox QAbstractItemView {{
        background-color: {t.BG_SURFACE};
        color: {t.TEXT_PRIMARY};
        border: 1px solid {t.BORDER};
        selection-background-color: {t.ACCENT};
        selection-color: {t.CHECKER};
        outline: none;
        border-radius: {RADIUS_SM}px;
    }}

    /* ── 按钮基底 ── */
    QPushButton {{
        background-color: {t.BG_SURFACE};
        color: {t.TEXT_PRIMARY};
        border: 1px solid {t.BORDER};
        border-radius: {RADIUS_SM}px;
        padding: 7px 16px;
        font-size: 12px;
    }}
    QPushButton:hover {{ background-color: {t.BG_HOVER}; border-color: {lighten(t.BORDER, 20)}; }}
    QPushButton:pressed {{ padding-top: 9px; padding-bottom: 5px; }}
    QPushButton:disabled {{
        background-color: {t.BG_SECONDARY};
        color: {t.TEXT_MUTED};
        border-color: {t.BORDER};
    }}

    /* ── 滚动条 ── */
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {t.BORDER};
        border-radius: 5px;
        min-height: 28px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {t.ACCENT}; }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 10px;
        margin: 2px;
    }}
    QScrollBar::handle:horizontal {{
        background: {t.BORDER};
        border-radius: 5px;
        min-width: 28px;
    }}
    QScrollBar::handle:horizontal:hover {{ background: {t.ACCENT}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page, QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
        background: transparent;
    }}

    /* ── 滚动区 / 分割线 ── */
    QScrollArea {{ background: transparent; border: none; }}
    QSplitter::handle {{ background: {t.BORDER}; }}
    QSplitter::handle:hover {{ background: {t.ACCENT}; }}
    QFrame[frameShape="4"] {{ background-color: {t.BORDER}; max-height: 1px; border: none; }}
    """


# ══════════════════════ 按钮样式助手 ══════════════════════
def primary_button_qss(t, radius=RADIUS_MD):
    """主操作按钮：渐变 + 悬停发光"""
    return f"""
        QPushButton {{
            background: {t.GRADIENT_PRIMARY};
            color: {t.CHECKER};
            border: none;
            border-radius: {radius}px;
            padding: 0 26px;
            font-weight: bold;
            font-size: 13px;
        }}
        QPushButton:hover {{
            background: {t.GRADIENT_PRIMARY_HOVER};
            border: 1px solid {t.ACCENT_HOVER};
        }}
        QPushButton:pressed {{ background: {t.GRADIENT_PRIMARY_DOWN}; }}
        QPushButton:disabled {{
            background: {t.BG_SECONDARY};
            color: {t.TEXT_MUTED};
            border: 1px solid {t.BORDER};
        }}
    """


def outline_button_qss(t, color, radius=RADIUS_MD):
    """描边按钮：常态透明 + 悬停实心"""
    return f"""
        QPushButton {{
            background: transparent;
            color: {color};
            border: 1px solid {color};
            border-radius: {radius}px;
            padding: 0 18px;
            font-weight: 600;
        }}
        QPushButton:hover {{ background: {color}; color: {t.CHECKER}; }}
        QPushButton:pressed {{ padding-top: 2px; padding-bottom: 2px; }}
        QPushButton:disabled {{
            color: {t.TEXT_MUTED};
            border-color: {t.BORDER};
            background: transparent;
        }}
    """


def solid_button_qss(t, color, radius=RADIUS_SM):
    """实心小按钮"""
    return f"""
        QPushButton {{
            background-color: {color};
            color: {t.CHECKER};
            border: none;
            border-radius: {radius}px;
            padding: 5px 14px;
            font-weight: bold;
            font-size: 12px;
        }}
        QPushButton:hover {{ background-color: {lighten(color, 18)}; }}
        QPushButton:pressed {{ background-color: {darken(color, 15)}; }}
        QPushButton:disabled {{ background-color: {t.BG_SECONDARY}; color: {t.TEXT_MUTED}; }}
    """


def ghost_button_qss(t, color, radius=RADIUS_SM):
    """幽灵小按钮：描边细款"""
    return f"""
        QPushButton {{
            background: transparent;
            color: {color};
            border: 1px solid {color};
            border-radius: {radius}px;
            padding: 4px 12px;
            font-weight: bold;
            font-size: 12px;
        }}
        QPushButton:hover {{ background: {color}; color: {t.CHECKER}; }}
        QPushButton:disabled {{ color: {t.TEXT_MUTED}; border-color: {t.BORDER}; background: transparent; }}
    """
