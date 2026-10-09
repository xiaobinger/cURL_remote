"""主窗口：请求编辑、cURL 生成、执行与快捷请求"""
import base64
import ctypes
import json
import logging
import os
import subprocess
import sys
import threading
from datetime import datetime

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QTextEdit, QPushButton, QTabWidget,
    QGroupBox, QCheckBox, QSplitter, QFrame, QScrollArea,
    QShortcut, QRadioButton, QDialog, QMessageBox,
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QIcon, QFont, QColor, QPalette, QPixmap, QKeySequence

from .config import (
    SCRIPT_DIR, LOG_DIR, load_env_configs, load_ssh_config,
    resolve_ssh_cfg, env_ssh_target_alias,
)
from .theme import (
    DarkTheme, LightTheme, build_app_stylesheet,
    primary_button_qss, outline_button_qss, solid_button_qss, ghost_button_qss,
    lighten, darken,
)
from .signing import compute_sign
from .widgets import CurlHighlighter, StyledDropdown, MethodDropdown, KeyValueEditor
from .dialogs import EnvManageDialog, SSHConfigDialog, SaveRequestDialog
from .saved_panel import SavedRequestsPanel
from .ssh_executor import (
    LocalExecutor, SSHRemoteExecutor,
    ExecutionError, ExecutionTimeout, ExecutorStop,
)

logger = logging.getLogger("curlgen.main")


class CurlGenerator(QMainWindow):
    # 工作线程 → 主线程 的信号桥（工作线程里 QTimer.singleShot 不会触发，必须用信号）
    _sig_progress = pyqtSignal(str, str)
    _sig_response = pyqtSignal(int, str, bool)
    _sig_done = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.theme = DarkTheme()
        self.history = []
        self.max_history = 50
        self._exec_process = None
        self._logo_pixmap = None
        self._logo_path = os.path.join(SCRIPT_DIR, "logo.png")
        self.ssh_data = load_ssh_config()
        self._current_req_name = None  # 当前加载的快捷请求名（用于更新模式）
        self._current_req_group = None
        self._sig_progress.connect(self._apply_progress)
        self._sig_response.connect(self._apply_response)
        self._sig_done.connect(self._exec_done_ui)
        self.init_ui()
        self.load_logo()
        self.apply_theme(self.theme)
        self._fix_taskbar_icon()
        self._refresh_env_dropdown()
        logger.info("应用启动完成")

    # ──────────────────── UI 构建 ────────────────────
    def init_ui(self):
        self.setWindowTitle("cURL Generator")
        self.setMinimumSize(1100, 750)
        self.resize(1200, 800)

        # ── 菜单栏 ──
        self._build_menubar()

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(16, 12, 16, 12)
        main_layout.setSpacing(10)

        # ── 标题栏 ──
        self._accent_strip = QFrame()
        self._accent_strip.setFixedHeight(3)
        self._accent_strip.setObjectName("accentStrip")
        main_layout.addWidget(self._accent_strip)

        title_bar = QHBoxLayout()
        title_bar.setSpacing(12)

        self.logo_label = QLabel()
        self.logo_label.setFixedSize(44, 44)
        self.logo_label.setScaledContents(True)
        self.logo_label.setObjectName("logoChip")
        title_bar.addWidget(self.logo_label)

        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_label = QLabel("cURL Generator")
        title_label.setFont(QFont("Segoe UI", 21, QFont.Bold))
        title_label.setObjectName("appTitle")
        title_col.addWidget(title_label)

        subtitle = QLabel("HTTP 请求构建器 · 签名生成 · 远程执行")
        subtitle.setFont(QFont("Segoe UI", 10))
        subtitle.setObjectName("appSubtitle")
        title_col.addWidget(subtitle)
        title_bar.addLayout(title_col)
        title_bar.addStretch()

        self._chip_labels = []
        for text in ("Ctrl+Enter  生成", "Ctrl+Shift+Enter  执行"):
            chip = QLabel(text)
            chip.setFont(QFont("Segoe UI", 9))
            chip.setObjectName("keyChip")
            chip.setFixedHeight(26)
            self._chip_labels.append(chip)
            title_bar.addWidget(chip)

        main_layout.addLayout(title_bar)
        main_layout.addWidget(self._hline())

        # ── 主体分割 ──
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(4)

        # 折叠切换按钮
        self.panel_toggle = QPushButton("◀")
        self.panel_toggle.setFixedWidth(20)
        self.panel_toggle.setCursor(Qt.PointingHandCursor)
        self.panel_toggle.setToolTip("折叠/展开快捷请求面板")
        self.panel_toggle.clicked.connect(self._toggle_saved_panel)
        splitter.addWidget(self.panel_toggle)

        # 左侧快捷请求面板
        self.saved_panel = SavedRequestsPanel(self.theme)
        self.saved_panel.request_clicked.connect(self._load_request)
        splitter.addWidget(self.saved_panel)

        input_panel = self._build_input_panel()
        splitter.addWidget(input_panel)

        right_panel = self._build_preview_panel()
        splitter.addWidget(right_panel)

        splitter.setSizes([20, 220, 500, 440])
        self._panel_expanded = True
        main_layout.addWidget(splitter, 1)

        # ── 底部操作栏 ──
        bottom_bar = QHBoxLayout()

        self.generate_btn = QPushButton("⚡ 生成 cURL")
        self.generate_btn.setFixedHeight(42)
        self.generate_btn.setFont(QFont("Segoe UI", 12, QFont.Bold))
        self.generate_btn.clicked.connect(self.generate_curl)
        bottom_bar.addWidget(self.generate_btn)

        self.copy_btn = QPushButton(" 复制到剪贴板")
        self.copy_btn.setFixedHeight(42)
        self.copy_btn.setFont(QFont("Segoe UI", 11, QFont.Bold))
        self.copy_btn.clicked.connect(self.copy_to_clipboard)
        bottom_bar.addWidget(self.copy_btn)

        self.save_btn = QPushButton("💾 保存请求")
        self.save_btn.setFixedHeight(42)
        self.save_btn.setFont(QFont("Segoe UI", 11))
        self.save_btn.clicked.connect(self._open_save_request_dialog)
        bottom_bar.addWidget(self.save_btn)

        self.clear_btn = QPushButton(" 重置")
        self.clear_btn.setFixedHeight(42)
        self.clear_btn.setFont(QFont("Segoe UI", 11))
        self.clear_btn.clicked.connect(self.reset_all)
        bottom_bar.addWidget(self.clear_btn)

        self.exec_btn = QPushButton("▶ 执行")
        self.exec_btn.setFixedHeight(42)
        self.exec_btn.setFont(QFont("Segoe UI", 12, QFont.Bold))
        self.exec_btn.setCursor(Qt.PointingHandCursor)
        self.exec_btn.clicked.connect(self._show_exec_dialog)
        bottom_bar.addWidget(self.exec_btn)

        self.exec_stop_btn = QPushButton("停止")
        self.exec_stop_btn.setFixedHeight(42)
        self.exec_stop_btn.setFixedWidth(70)
        self.exec_stop_btn.setFont(QFont("Segoe UI", 11))
        self.exec_stop_btn.setEnabled(False)
        self.exec_stop_btn.clicked.connect(self._stop_execution)
        bottom_bar.addWidget(self.exec_stop_btn)

        bottom_bar.addStretch()
        main_layout.addLayout(bottom_bar)

        # 快捷键
        QShortcut(QKeySequence("Ctrl+Return"), self, self.generate_curl)
        QShortcut(QKeySequence("Ctrl+Shift+Return"), self, lambda: self._show_exec_dialog())

    def _build_menubar(self):
        menubar = self.menuBar()

        # 视图菜单：新旧界面切换
        view_menu = menubar.addMenu("  视图  ")
        new_ui_action = view_menu.addAction("🚀 切换到新版界面（星轨控制台）")
        new_ui_action.triggered.connect(self._open_new_ui)

        # 设置菜单
        settings_menu = menubar.addMenu("  设置  ")

        self.theme_action = settings_menu.addAction(
            "☀️ 切换亮色主题" if isinstance(self.theme, DarkTheme) else "🌙 切换暗色主题"
        )
        self.theme_action.triggered.connect(self._toggle_theme)

        settings_menu.addSeparator()

        ssh_action = settings_menu.addAction("🖧 SSH 配置...")
        ssh_action.triggered.connect(self._open_ssh_config)

        logs_action = settings_menu.addAction("📄 打开日志目录")
        logs_action.triggered.connect(self._open_log_dir)

        # 环境配置菜单
        env_menu = menubar.addMenu("  环境配置  ")

        manage_env_action = env_menu.addAction("🌐 环境配置管理...")
        manage_env_action.triggered.connect(self._open_env_manage)

    def _open_new_ui(self):
        """切换到新版星轨控制台界面"""
        if getattr(self, "_new_ui_opened", False):
            return
        try:
            import PyQt5.QtWebEngineWidgets  # noqa: F401
        except ImportError:
            self._show_status("未安装 PyQtWebEngine，无法启用新版界面", self.theme.RED)
            return
        self._new_ui_opened = True
        from .web_ui import WebCurlGenerator
        logger.info("切换到新版界面")
        self._new_ui_win = WebCurlGenerator()
        self._new_ui_win.show()
        self.close()

    def _open_ssh_config(self):
        dlg = SSHConfigDialog(self, self.theme, self.ssh_data)
        dlg.exec_()

    def _open_env_manage(self):
        dlg = EnvManageDialog(self, self.theme)
        dlg.exec_()

    def _open_log_dir(self):
        os.makedirs(LOG_DIR, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(LOG_DIR)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", LOG_DIR])
            else:
                subprocess.Popen(["xdg-open", LOG_DIR])
        except Exception as e:
            logger.warning("打开日志目录失败: %s", e)

    def _refresh_env_dropdown(self):
        configs = load_env_configs()
        aliases = [c["alias"] for c in configs]
        self.env_dropdown.set_items(["(不选择环境)"] + aliases)

    def load_logo(self):
        if os.path.exists(self._logo_path):
            self._logo_pixmap = QPixmap(self._logo_path).scaled(
                36, 36, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self.logo_label.setPixmap(self._logo_pixmap)
            self.setWindowIcon(QIcon(self._logo_path))

    def _fix_taskbar_icon(self):
        if sys.platform == "win32":
            try:
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    "curl.generator.app"
                )
            except Exception:
                pass
        if os.path.exists(self._logo_path):
            self.setWindowIcon(QIcon(self._logo_path))

    # ──────────────────── 输入面板 ────────────────────
    def _build_input_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(12, 12, 12, 12)
        inner_layout.setSpacing(12)

        # ── 请求方法 + URL ──
        url_group = self._group("请求信息")
        url_layout_inner = QVBoxLayout()

        row_method_url = QHBoxLayout()
        self.method_combo = MethodDropdown()
        self.method_combo.apply_theme(self.theme)
        row_method_url.addWidget(self.method_combo)

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://api.example.com/v1/resource")
        self.url_input.setFont(QFont("Consolas", 12))
        row_method_url.addWidget(self.url_input, 1)
        url_layout_inner.addLayout(row_method_url)

        # 环境选择行
        env_row = QHBoxLayout()
        env_row.addWidget(QLabel("环境:"))
        self.env_dropdown = StyledDropdown(["(不选择环境)"])
        self.env_dropdown.setFixedWidth(200)
        env_row.addWidget(self.env_dropdown)
        env_row.addSpacing(8)
        env_manage_btn = QPushButton("管理环境...")
        env_manage_btn.setFixedHeight(30)
        env_manage_btn.setCursor(Qt.PointingHandCursor)
        env_manage_btn.clicked.connect(self._open_env_manage)
        env_row.addWidget(env_manage_btn)

        # 环境签名预览
        self.env_sign_preview = QLabel("")
        self.env_sign_preview.setFont(QFont("Consolas", 10))
        env_row.addWidget(self.env_sign_preview)
        env_row.addStretch()
        url_layout_inner.addLayout(env_row)

        url_group.setLayout(url_layout_inner)
        inner_layout.addWidget(url_group)

        # ── Tab 区域 ──
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setUsesScrollButtons(True)

        # Headers
        self.headers_editor = KeyValueEditor("Header Name", "Header Value")
        self.tabs.addTab(self.headers_editor, "Headers")

        # Query Params
        self.params_editor = KeyValueEditor("Parameter", "Value")
        self.tabs.addTab(self.params_editor, "Query Params")

        # Body
        body_widget = QWidget()
        body_layout = QVBoxLayout(body_widget)
        body_layout.setContentsMargins(8, 8, 8, 8)

        body_top_row = QHBoxLayout()
        self.body_type = StyledDropdown(["JSON", "Form Data", "Raw Text", "None"])
        self.body_type.setFixedWidth(130)
        body_top_row.addWidget(self.body_type)

        body_top_row.addSpacing(8)

        self.json_format_btn = QPushButton("美化")
        self.json_format_btn.setFixedHeight(30)
        self.json_format_btn.setFixedWidth(56)
        self.json_format_btn.setCursor(Qt.PointingHandCursor)
        self.json_format_btn.setToolTip("格式化 JSON (Pretty Print)")
        self.json_format_btn.clicked.connect(self._format_json)
        body_top_row.addWidget(self.json_format_btn)

        self.json_compress_btn = QPushButton("压缩")
        self.json_compress_btn.setFixedHeight(30)
        self.json_compress_btn.setFixedWidth(56)
        self.json_compress_btn.setCursor(Qt.PointingHandCursor)
        self.json_compress_btn.setToolTip("压缩 JSON (Minify)")
        self.json_compress_btn.clicked.connect(self._compress_json)
        body_top_row.addWidget(self.json_compress_btn)

        body_top_row.addStretch()
        body_layout.addLayout(body_top_row)

        self.body_editor = QTextEdit()
        self.body_editor.setPlaceholderText('{\n  "key": "value"\n}')
        self.body_editor.setFont(QFont("Consolas", 12))
        body_layout.addWidget(self.body_editor)

        self.tabs.addTab(body_widget, "Body")

        # Auth
        auth_widget = QWidget()
        auth_layout = QVBoxLayout(auth_widget)
        auth_layout.setContentsMargins(8, 8, 8, 8)

        self.auth_type = StyledDropdown(["None", "Bearer Token", "Basic Auth", "API Key"])
        self.auth_type.setFixedWidth(170)
        self.auth_type.setFixedHeight(34)
        self.auth_type.currentTextChanged.connect(self._on_auth_change)
        auth_layout.addWidget(self.auth_type)

        self.auth_fields = QWidget()
        self.auth_fields_layout = QVBoxLayout(self.auth_fields)
        self.auth_fields_layout.setContentsMargins(0, 8, 0, 0)
        auth_layout.addWidget(self.auth_fields)

        self.tabs.addTab(auth_widget, "Auth")

        # Options
        opts_widget = QWidget()
        opts_layout = QVBoxLayout(opts_widget)
        opts_layout.setContentsMargins(8, 8, 8, 8)

        self.ssl_check = QCheckBox("跳过 SSL 验证 (-k)")
        opts_layout.addWidget(self.ssl_check)

        self.silent_check = QCheckBox("静默模式 (-s)")
        opts_layout.addWidget(self.silent_check)

        proxy_layout = QHBoxLayout()
        proxy_layout.addWidget(QLabel("代理:"))
        self.proxy_input = QLineEdit()
        self.proxy_input.setPlaceholderText("http://proxy:8080")
        proxy_layout.addWidget(self.proxy_input)
        opts_layout.addLayout(proxy_layout)

        timeout_layout = QHBoxLayout()
        timeout_layout.addWidget(QLabel("超时(秒):"))
        self.timeout_input = QLineEdit()
        self.timeout_input.setPlaceholderText("30")
        self.timeout_input.setFixedWidth(80)
        timeout_layout.addWidget(self.timeout_input)
        timeout_layout.addStretch()
        opts_layout.addLayout(timeout_layout)

        opts_layout.addStretch()
        self.tabs.addTab(opts_widget, "Options")

        inner_layout.addWidget(self.tabs)
        scroll.setWidget(inner)
        layout.addWidget(scroll)

        return panel

    # ─────────────────── 预览面板 ────────────────────
    def _build_preview_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        header = QHBoxLayout()
        preview_label = QLabel("预览")
        preview_label.setFont(QFont("Segoe UI", 13, QFont.Bold))
        header.addWidget(preview_label)
        header.addStretch()

        self.status_label = QLabel("就绪")
        self.status_label.setFont(QFont("Segoe UI", 10))
        header.addWidget(self.status_label)
        layout.addLayout(header)

        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(QFont("Consolas", 12))
        layout.addWidget(self.preview, 1)

        # 底部 Tab: 响应结果 / 历史记录
        self.bottom_tabs = QTabWidget()
        self.bottom_tabs.setDocumentMode(True)

        # 响应结果 tab
        resp_widget = QWidget()
        resp_layout = QVBoxLayout(resp_widget)
        resp_layout.setContentsMargins(4, 4, 4, 4)

        self.response_info = QLabel("状态: 未执行")
        self.response_info.setFont(QFont("Segoe UI", 10))
        resp_layout.addWidget(self.response_info)

        self.response_view = QTextEdit()
        self.response_view.setReadOnly(True)
        self.response_view.setFont(QFont("Consolas", 11))
        self.response_view.setPlaceholderText("执行后响应内容将展示在这里...")
        resp_layout.addWidget(self.response_view, 1)

        self.bottom_tabs.addTab(resp_widget, "响应结果")

        # 历史记录 tab
        history_widget = QWidget()
        history_layout = QVBoxLayout(history_widget)
        history_layout.setContentsMargins(4, 4, 4, 4)

        history_header = QHBoxLayout()
        history_label = QLabel("历史记录")
        history_label.setFont(QFont("Segoe UI", 11, QFont.Bold))
        history_header.addWidget(history_label)
        history_header.addStretch()

        self.clear_history_btn = QPushButton("清空历史")
        self.clear_history_btn.setFixedHeight(26)
        self.clear_history_btn.setCursor(Qt.PointingHandCursor)
        self.clear_history_btn.clicked.connect(self.clear_history)
        history_header.addWidget(self.clear_history_btn)
        history_layout.addLayout(history_header)

        self.history_list = QTextEdit()
        self.history_list.setReadOnly(True)
        self.history_list.setFont(QFont("Consolas", 10))
        history_layout.addWidget(self.history_list)

        self.bottom_tabs.addTab(history_widget, "历史记录")

        layout.addWidget(self.bottom_tabs, 1)

        return panel

    # ──────────────────── 主题系统 ────────────────────
    def _toggle_theme(self):
        if isinstance(self.theme, LightTheme):
            self.theme = DarkTheme()
            self.theme_action.setText("☀️ 切换亮色主题")
        else:
            self.theme = LightTheme()
            self.theme_action.setText("🌙 切换暗色主题")
        logger.info("切换主题: %s", self.theme.NAME)
        self.apply_theme(self.theme)

    def apply_theme(self, theme):
        t = theme
        # 全局设计令牌 QSS + 标题栏专属样式
        self.setStyleSheet(build_app_stylesheet(t) + f"""
            QFrame#accentStrip {{
                background: {t.GRADIENT_HEADER};
                border: none;
                max-height: 3px;
            }}
            QLabel#appTitle {{ color: {t.TEXT_PRIMARY}; }}
            QLabel#appSubtitle {{ color: {t.TEXT_MUTED}; }}
            QLabel#keyChip {{
                background: {t.BG_SURFACE};
                color: {t.TEXT_SECONDARY};
                border: 1px solid {t.BORDER};
                border-radius: 13px;
                padding: 0 14px;
            }}
            QLabel#logoChip {{
                background: {t.BG_SURFACE};
                border: 1px solid {t.BORDER};
                border-radius: 12px;
                padding: 4px;
            }}
        """)

        # ── 操作按钮 ──
        self.generate_btn.setStyleSheet(primary_button_qss(t))
        self.exec_btn.setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {t.GREEN}, stop:1 {t.TEAL});
                color: {t.CHECKER};
                border: none;
                border-radius: 10px;
                padding: 0 26px;
                font-weight: bold;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {lighten(t.GREEN, 15)}, stop:1 {lighten(t.TEAL, 15)});
            }}
            QPushButton:pressed {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {darken(t.GREEN, 15)}, stop:1 {darken(t.TEAL, 15)});
            }}
            QPushButton:disabled {{
                background: {t.BG_SECONDARY};
                color: {t.TEXT_MUTED};
                border: 1px solid {t.BORDER};
            }}
        """)
        self.copy_btn.setStyleSheet(outline_button_qss(t, t.TEAL))
        self.save_btn.setStyleSheet(outline_button_qss(t, t.PEACH))
        self.clear_btn.setStyleSheet(outline_button_qss(t, t.RED))
        self.exec_stop_btn.setStyleSheet(ghost_button_qss(t, t.RED))
        self.clear_history_btn.setStyleSheet(ghost_button_qss(t, t.RED, radius=4))
        self.panel_toggle.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {t.TEXT_MUTED};
                border: none;
                border-radius: 0px;
                padding: 0px;
                font-size: 11px;
            }}
            QPushButton:hover {{
                background-color: {t.BG_HOVER};
                color: {t.ACCENT};
            }}
        """)
        # 分割线
        self._hline_widget.setStyleSheet(f"background-color: {t.BORDER}; max-height: 1px;")

        # 子组件主题
        self.headers_editor.apply_theme(t)
        self.params_editor.apply_theme(t)
        self.method_combo.apply_theme(t)
        self.body_type.apply_theme(t)
        self.auth_type.apply_theme(t)
        self.env_dropdown.apply_theme(t)
        self.saved_panel.apply_theme(t)

        # JSON 美化/压缩按钮
        self.json_format_btn.setStyleSheet(ghost_button_qss(t, t.TEAL))
        self.json_compress_btn.setStyleSheet(ghost_button_qss(t, t.PEACH))

        # 环境签名预览
        self.env_sign_preview.setStyleSheet(f"color: {t.MAUVE};")

        # 重新应用语法高亮
        self.highlighter = CurlHighlighter(self.preview.document(), t)

        # palette
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor(t.BG_PRIMARY))
        palette.setColor(QPalette.WindowText, QColor(t.TEXT_PRIMARY))
        palette.setColor(QPalette.Base, QColor(t.BG_PRIMARY))
        palette.setColor(QPalette.AlternateBase, QColor(t.BG_SECONDARY))
        palette.setColor(QPalette.ToolTipBase, QColor(t.BG_SURFACE))
        palette.setColor(QPalette.ToolTipText, QColor(t.TEXT_PRIMARY))
        palette.setColor(QPalette.Text, QColor(t.TEXT_PRIMARY))
        palette.setColor(QPalette.Button, QColor(t.BG_SURFACE))
        palette.setColor(QPalette.ButtonText, QColor(t.TEXT_PRIMARY))
        palette.setColor(QPalette.Highlight, QColor(t.ACCENT))
        palette.setColor(QPalette.HighlightedText, QColor(t.CHECKER))
        QApplication.instance().setPalette(palette)

    # ─────────────────── 辅助组件 ────────────────────
    def _group(self, title):
        g = QGroupBox(title)
        g.setFont(QFont("Segoe UI", 11, QFont.Bold))
        return g

    def _hline(self):
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        self._hline_widget = line
        return line

    def _on_method_change(self, method):
        self.method_combo.apply_theme(self.theme)
        if method in ("GET", "HEAD", "OPTIONS"):
            self.tabs.setTabEnabled(2, False)
            self.tabs.setTabText(2, "Body (禁用)")
        else:
            self.tabs.setTabEnabled(2, True)
            self.tabs.setTabText(2, "Body")

    def _on_auth_change(self, auth_type):
        while self.auth_fields_layout.count():
            item = self.auth_fields_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if auth_type == "Bearer Token":
            self._auth_input("Token", "eyJhbGciOiJIUzI1NiIs...")
        elif auth_type == "Basic Auth":
            row = QHBoxLayout()
            lbl1 = QLabel("用户:")
            row.addWidget(lbl1)
            self.auth_user = QLineEdit()
            self.auth_user.setPlaceholderText("username")
            row.addWidget(self.auth_user)
            lbl2 = QLabel("密码:")
            row.addWidget(lbl2)
            self.auth_pass = QLineEdit()
            self.auth_pass.setPlaceholderText("password")
            self.auth_pass.setEchoMode(QLineEdit.Password)
            row.addWidget(self.auth_pass)
            self.auth_fields_layout.addLayout(row)
        elif auth_type == "API Key":
            row1 = QHBoxLayout()
            lbl1 = QLabel("Header:")
            row1.addWidget(lbl1)
            self.auth_header_name = QLineEdit("X-API-Key")
            row1.addWidget(self.auth_header_name)
            self.auth_fields_layout.addLayout(row1)
            self._auth_input("API Key", "your-api-key-here")

    def _auth_input(self, label, placeholder):
        row = QHBoxLayout()
        lbl = QLabel(f"{label}:")
        row.addWidget(lbl)
        inp = QLineEdit()
        inp.setPlaceholderText(placeholder)
        inp.setFont(QFont("Consolas", 11))
        row.addWidget(inp)
        self.auth_fields_layout.addLayout(row)
        setattr(self, "_auth_value_input", inp)

    # ──────────────────── JSON 格式化 / 压缩 ────────────────────
    def _format_json(self):
        body = self.body_editor.toPlainText().strip()
        if not body:
            self._show_status("Body 为空", self.theme.YELLOW)
            return
        try:
            obj = json.loads(body)
            self.body_editor.setPlainText(json.dumps(obj, indent=2, ensure_ascii=False))
            self._show_status("JSON 已美化 ✓", self.theme.GREEN)
        except json.JSONDecodeError as e:
            self._show_status(f"JSON 格式错误: {e}", self.theme.RED)

    def _compress_json(self):
        body = self.body_editor.toPlainText().strip()
        if not body:
            self._show_status("Body 为空", self.theme.YELLOW)
            return
        try:
            obj = json.loads(body)
            self.body_editor.setPlainText(json.dumps(obj, separators=(",", ":"), ensure_ascii=False))
            self._show_status("JSON 已压缩 ✓", self.theme.GREEN)
        except json.JSONDecodeError as e:
            self._show_status(f"JSON 格式错误: {e}", self.theme.RED)

    # ──────────────────── 变量替换 ────────────────────
    def _resolve_vars(self, text, var_map):
        """将 {{key}} 替换为环境变量值"""
        if not var_map or not text:
            return text
        for k, v in var_map.items():
            text = text.replace("{{" + k + "}}", v)
        return text

    # ──────────────────── 生成逻辑 ────────────────────
    def generate_curl(self):
        parts = ["curl"]
        method = self.method_combo.currentText()
        url = self.url_input.text().strip()

        if not url:
            self._show_status("请输入 URL", self.theme.RED)
            self.url_input.setFocus()
            return

        # ── 加载环境配置 & 构建变量映射 ──
        var_map = {}
        env_cfg = None
        selected_env = self.env_dropdown.currentText()
        if selected_env and selected_env != "(不选择环境)":
            configs = load_env_configs()
            env_cfg = next((c for c in configs if c["alias"] == selected_env), None)
        if env_cfg:
            for ck, cv in env_cfg.get("custom_params", []):
                if ck:
                    var_map[ck] = cv

        # 变量替换 URL
        url = self._resolve_vars(url, var_map)

        # Method
        if method != "GET":
            parts.append(f"-X {method}")

        # Options
        if self.ssl_check.isChecked():
            parts.append("-k")
        if self.silent_check.isChecked():
            parts.append("-s")

        proxy = self._resolve_vars(self.proxy_input.text().strip(), var_map)
        if proxy:
            parts.append(f'-x "{proxy}"')

        timeout = self.timeout_input.text().strip()
        if timeout:
            parts.append(f"--max-time {timeout}")

        # Auth
        auth_type = self.auth_type.currentText()
        if auth_type == "Bearer Token":
            inp = getattr(self, "_auth_value_input", None)
            token = self._resolve_vars(inp.text().strip(), var_map) if inp else ""
            if token:
                parts.append(f'-H "Authorization: Bearer {token}"')
        elif auth_type == "Basic Auth":
            user = getattr(self, "auth_user", None)
            pwd = getattr(self, "auth_pass", None)
            u = self._resolve_vars(user.text().strip(), var_map) if user else ""
            p = self._resolve_vars(pwd.text().strip(), var_map) if pwd else ""
            if u:
                creds = base64.b64encode(f"{u}:{p}".encode()).decode()
                parts.append(f'-H "Authorization: Basic {creds}"')
        elif auth_type == "API Key":
            header_name = getattr(self, "auth_header_name", None)
            inp = getattr(self, "_auth_value_input", None)
            h = header_name.text().strip() if header_name else "X-API-Key"
            v = self._resolve_vars(inp.text().strip(), var_map) if inp else ""
            if v:
                parts.append(f'-H "{h}: {v}"')

        # Headers
        for k, v in self.headers_editor.get_pairs():
            k = self._resolve_vars(k, var_map)
            v = self._resolve_vars(v, var_map)
            parts.append(f'-H "{k}: {v}"')

        # Query params
        params = [(self._resolve_vars(k, var_map), self._resolve_vars(v, var_map))
                  for k, v in self.params_editor.get_pairs()]

        # ── 签名计算（env_cfg 和 var_map 已在上方加载） ──
        sign_value = None
        sign_param_name = "sign"

        if env_cfg:
            if env_cfg.get("enable_sign", True):
                secret_key = env_cfg.get("key", "")
                sign_algo = env_cfg.get("algo", "MD5")
                sign_param_name = env_cfg.get("param_name", "sign")
                # 收集所有参数用于签名计算（all_params 以 dict 类型传入）
                all_params = dict(params)
                body_type = self.body_type.currentText()
                if method not in ("GET", "HEAD", "OPTIONS") and body_type != "None":
                    body_text = self._resolve_vars(
                        self.body_editor.toPlainText().strip(), var_map)
                    if body_text:
                        if body_type == "JSON":
                            try:
                                obj = json.loads(body_text)
                            except json.JSONDecodeError:
                                obj = None
                            if isinstance(obj, dict):
                                for k, v in obj.items():
                                    if isinstance(v, bool):
                                        # Java 风格：布尔转小写 true/false
                                        all_params[str(k)] = "true" if v else "false"
                                    elif isinstance(v, (dict, list)):
                                        all_params[str(k)] = json.dumps(
                                            v, separators=(",", ":"), ensure_ascii=False)
                                    elif v is not None:
                                        all_params[str(k)] = v
                            else:
                                logger.debug("JSON body 非对象或解析失败，不并入签名: %s", body_text)
                        elif body_type == "Form Data":
                            for line in body_text.splitlines():
                                line = line.strip()
                                if "=" in line:
                                    k, v = line.split("=", 1)
                                    all_params[k.strip()] = v.strip()
                        else:  # Raw Text: 按 k=v 或 k=v&k2=v2 解析
                            for seg in body_text.splitlines():
                                for pair in seg.split("&"):
                                    pair = pair.strip()
                                    if "=" in pair:
                                        k, v = pair.split("=", 1)
                                        all_params[k.strip()] = v.strip()
                sign_value = compute_sign(all_params, secret_key, sign_algo, sign_param_name)
                if sign_value:
                    self.env_sign_preview.setText(f"签名: {sign_value}")
                    logger.info("签名参数(all_params, dict): %s, 签名值: %s", all_params, sign_value)
                else:
                    self.env_sign_preview.setText("")
            else:
                self.env_sign_preview.setText("(签名已禁用)")
        else:
            self.env_sign_preview.setText("")

        # 拼接 URL 参数
        if params:
            param_str = "&".join(f"{k}={v}" for k, v in params)
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{param_str}"

        # GET/HEAD/OPTIONS 没有 body，签名放到 URL 参数里
        if sign_value and method in ("GET", "HEAD", "OPTIONS"):
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{sign_param_name}={sign_value}"

        # Body
        body_type = self.body_type.currentText()
        if body_type != "None" and method not in ("GET", "HEAD", "OPTIONS"):
            body = self._resolve_vars(self.body_editor.toPlainText().strip(), var_map)
            if body:
                if body_type == "JSON":
                    parts.append('-H "Content-Type: application/json"')
                    try:
                        obj = json.loads(body)
                        if sign_value:
                            obj[sign_param_name] = sign_value
                        body = json.dumps(obj, indent=2, ensure_ascii=False)
                    except json.JSONDecodeError:
                        # JSON 解析失败，尝试在末尾 } 前插入签名
                        if sign_value and body.rstrip().endswith("}"):
                            insert = f', "{sign_param_name}": "{sign_value}"'
                            body = body.rstrip()[:-1] + insert + "\n}"
                    parts.append(f"-d '{body}'")
                elif body_type == "Form Data":
                    form_lines = body.strip().splitlines()
                    if sign_value:
                        form_lines.append(f"{sign_param_name}={sign_value}")
                    for line in form_lines:
                        line = line.strip()
                        if "=" in line:
                            k, v = line.split("=", 1)
                            parts.append(f'-d "{k.strip()}={v.strip()}"')
                else:
                    if sign_value:
                        body += f"&{sign_param_name}={sign_value}"
                    parts.append(f"-d '{body}'")
            else:
                # body 为空但有签名时，构造 body
                if sign_value:
                    if body_type == "JSON":
                        parts.append('-H "Content-Type: application/json"')
                        obj = {sign_param_name: sign_value}
                        parts.append(f"-d '{json.dumps(obj)}'")
                    elif body_type == "Form Data":
                        parts.append(f'-d "{sign_param_name}={sign_value}"')
                    else:
                        parts.append(f"-d \'{sign_param_name}={sign_value}\'")

        parts.append(f'"{url}"')

        # 格式化输出
        curl_cmd = " \\\n  ".join(parts)
        self.preview.setPlainText(curl_cmd)
        self.save_to_history()
        self._show_status("已生成 ✓", self.theme.GREEN)
        logger.info("生成 cURL: %s %s", method, url)

    def copy_to_clipboard(self):
        text = self.preview.toPlainText().strip()
        if not text:
            self._show_status("没有可复制的内容", self.theme.RED)
            return
        cb = QApplication.clipboard()
        cb.setText(text)
        self._show_status("已复制到剪贴板 ✓", self.theme.GREEN)
        logger.info("已复制 cURL 到剪贴板 (%d 字符)", len(text))
        QTimer.singleShot(2000, lambda: self._show_status("就绪", self.theme.TEXT_MUTED))

    def save_to_history(self):
        text = self.preview.toPlainText().strip()
        if not text:
            return
        ts = datetime.now().strftime("%H:%M:%S")
        method = self.method_combo.currentText()
        url = self.url_input.text().strip()
        entry = f"[{ts}] {method} {url}\n{text}\n{'─' * 60}"
        self.history.insert(0, entry)
        if len(self.history) > self.max_history:
            self.history.pop()
        self.history_list.setPlainText("\n\n".join(self.history))
        self.history_list.verticalScrollBar().setValue(0)
        self._show_status("已保存 ✓", self.theme.MAUVE)

    def clear_history(self):
        self.history.clear()
        self.history_list.clear()

    # ──────────────────── 快捷请求 ────────────────────
    def _collect_request_data(self):
        """收集当前所有请求参数为一个 dict"""
        data = {
            "method": self.method_combo.currentText(),
            "url": self.url_input.text().strip(),
            "env": self.env_dropdown.currentText(),
            "headers": self.headers_editor.get_pairs(),
            "params": self.params_editor.get_pairs(),
            "body_type": self.body_type.currentText(),
            "body": self.body_editor.toPlainText(),
            "auth_type": self.auth_type.currentText(),
            "ssl": self.ssl_check.isChecked(),
            "silent": self.silent_check.isChecked(),
            "proxy": self.proxy_input.text().strip(),
            "timeout": self.timeout_input.text().strip(),
        }
        # Auth 值
        if data["auth_type"] == "Bearer Token":
            inp = getattr(self, "_auth_value_input", None)
            data["auth_value"] = inp.text().strip() if inp else ""
        elif data["auth_type"] == "Basic Auth":
            u = getattr(self, "auth_user", None)
            p = getattr(self, "auth_pass", None)
            data["auth_user"] = u.text().strip() if u else ""
            data["auth_pass"] = p.text().strip() if p else ""
        elif data["auth_type"] == "API Key":
            h = getattr(self, "auth_header_name", None)
            inp = getattr(self, "_auth_value_input", None)
            data["auth_header_name"] = h.text().strip() if h else "X-API-Key"
            data["auth_value"] = inp.text().strip() if inp else ""
        return data

    def _load_request(self, req_data):
        """从保存的请求数据恢复到当前界面"""
        self.method_combo._select(req_data.get("method", "GET"))
        self.url_input.setText(req_data.get("url", ""))

        # 环境
        env_name = req_data.get("env", "(不选择环境)")
        self.env_dropdown.setCurrentText(env_name)

        # Headers & Params
        self.headers_editor.clear_all()
        for k, v in req_data.get("headers", []):
            self.headers_editor.add_row(k, v)
        self.params_editor.clear_all()
        for k, v in req_data.get("params", []):
            self.params_editor.add_row(k, v)

        # Body
        self.body_type.setCurrentText(req_data.get("body_type", "JSON"))
        self.body_editor.setPlainText(req_data.get("body", ""))

        # Auth
        auth_type = req_data.get("auth_type", "None")
        self.auth_type.setCurrentText(auth_type)
        self._on_auth_change(auth_type)
        if auth_type == "Bearer Token":
            inp = getattr(self, "_auth_value_input", None)
            if inp:
                inp.setText(req_data.get("auth_value", ""))
        elif auth_type == "Basic Auth":
            u = getattr(self, "auth_user", None)
            p = getattr(self, "auth_pass", None)
            if u:
                u.setText(req_data.get("auth_user", ""))
            if p:
                p.setText(req_data.get("auth_pass", ""))
        elif auth_type == "API Key":
            h = getattr(self, "auth_header_name", None)
            inp = getattr(self, "_auth_value_input", None)
            if h:
                h.setText(req_data.get("auth_header_name", "X-API-Key"))
            if inp:
                inp.setText(req_data.get("auth_value", ""))

        # Options
        self.ssl_check.setChecked(req_data.get("ssl", False))
        self.silent_check.setChecked(req_data.get("silent", False))
        self.proxy_input.setText(req_data.get("proxy", ""))
        self.timeout_input.setText(req_data.get("timeout", ""))

        # 记录当前加载的请求名
        self._current_req_name = req_data.get("name")
        self._current_req_group = req_data.get("_group_name")

        logger.info("加载快捷请求: %s/%s (%s %s)",
                    self._current_req_group, self._current_req_name,
                    req_data.get("method", ""), req_data.get("url", ""))
        self._show_status(f"已加载: {req_data.get('name', '?')}", self.theme.GREEN)

    def _toggle_saved_panel(self):
        """折叠/展开左侧快捷请求面板"""
        self._panel_expanded = not self._panel_expanded
        self.saved_panel.setVisible(self._panel_expanded)
        self.panel_toggle.setText("▶" if not self._panel_expanded else "◀")
        # 调整 splitter sizes
        if self._panel_expanded:
            self.panel_toggle.parent().setSizes([20, 220, 500, 440])
        else:
            self.panel_toggle.parent().setSizes([20, 0, 540, 440])

    def _open_save_request_dialog(self):
        """打开保存快捷请求对话框"""
        url = self.url_input.text().strip()
        if not url:
            self._show_status("请先输入 URL", self.theme.RED)
            return

        data = self._collect_request_data()
        dlg = SaveRequestDialog(
            self, self.theme,
            self.saved_panel.data.get("groups", []),
            current_name=self._current_req_name or ""
        )
        if dlg.exec_() == QDialog.Accepted:
            req_data = self._collect_request_data()
            old_name = self._current_req_name if self._current_req_name != dlg.result_name else None
            self.saved_panel.save_request(req_data, dlg.result_group, dlg.result_name, update_old_name=old_name)
            self._current_req_name = dlg.result_name
            self._current_req_group = dlg.result_group
            self._show_status(f"已保存: {dlg.result_name}", self.theme.GREEN)

    def reset_all(self):
        self.method_combo._select("GET")
        self.url_input.clear()
        self.headers_editor.clear_all()
        self.params_editor.clear_all()
        self.body_editor.clear()
        self.body_type.setCurrentText("JSON")
        self.auth_type.setCurrentText("None")
        self.ssl_check.setChecked(False)
        self.silent_check.setChecked(False)
        self.proxy_input.clear()
        self.timeout_input.clear()
        self.env_dropdown.setCurrentText("(不选择环境)")
        self.env_sign_preview.setText("")
        self.preview.clear()
        self.response_view.clear()
        self.response_info.setText("状态: 未执行")
        self._current_req_name = None
        self._current_req_group = None
        self._show_status("已重置", self.theme.TEXT_MUTED)
        logger.info("已重置表单")

    def _show_status(self, text, color):
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: bold;")

    # ──────────────────── 执行功能 ────────────────────
    def _get_exec_timeout(self):
        """解析用户填写的超时时间，默认 30s"""
        t = self.timeout_input.text().strip()
        try:
            return max(1, int(t)) if t else 30
        except ValueError:
            return 30

    def _on_exec_progress(self, tag, text):
        """执行过程中的实时输出（工作线程 → 主线程，经信号排队）"""
        self._sig_progress.emit(tag, text if isinstance(text, str) else str(text))

    def _apply_progress(self, tag, text):
        """主线程：追加实时输出到响应面板"""
        if tag == "info":
            self.response_view.append(f"[{text.strip()}]")
        else:
            self.response_view.append(text.rstrip("\n"))

    def _show_exec_dialog(self):
        """弹窗选择执行模式 + 二次确认后执行"""
        self.generate_curl()
        curl_cmd = self.preview.toPlainText()
        if not curl_cmd.strip():
            self._show_status("请先生成 cURL 命令", self.theme.RED)
            return

        t = self.theme
        dlg = QDialog(self)
        dlg.setWindowTitle("选择执行模式")
        dlg.setFixedSize(380, 210)

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(12)

        title = QLabel("选择执行模式")
        title.setFont(QFont("Segoe UI", 14, QFont.Bold))
        layout.addWidget(title)

        radio_local = QRadioButton("  本地执行")
        radio_ssh = QRadioButton("  SSH 远程执行（跳板机 → 目标机）")
        # 当前环境启用 SSH 时默认选中远程执行
        env_alias = self.env_dropdown.currentText()
        env_target = env_ssh_target_alias(env_alias)
        if env_target is not None:
            radio_ssh.setChecked(True)
        else:
            radio_local.setChecked(True)
        layout.addWidget(radio_local)
        layout.addWidget(radio_ssh)
        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.setFixedSize(80, 34)
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(cancel_btn)

        ok_btn = QPushButton("确认")
        ok_btn.setFixedSize(80, 34)
        ok_btn.setCursor(Qt.PointingHandCursor)
        ok_btn.setStyleSheet(solid_button_qss(t, t.ACCENT, radius=8))
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

        def on_ok():
            dlg.accept()
            mode = "SSH" if radio_ssh.isChecked() else "本地"
            msg = QMessageBox(self)
            msg.setWindowTitle("确认执行")
            msg.setText(f"即将以 【{mode}】 模式执行请求，是否继续？")
            msg.setIcon(QMessageBox.Question)
            msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            msg.setDefaultButton(QMessageBox.Yes)
            msg.button(QMessageBox.Yes).setText("执行")
            msg.button(QMessageBox.No).setText("取消")
            msg.setStyleSheet(f"""
                QMessageBox {{
                    background-color: {t.BG_PRIMARY};
                }}
                QMessageBox QLabel {{
                    color: {t.TEXT_PRIMARY};
                    font-size: 13px;
                    min-width: 280px;
                }}
            """)
            if msg.exec_() == QMessageBox.Yes:
                self._execute_request(mode="ssh" if radio_ssh.isChecked() else "local")

        ok_btn.clicked.connect(on_ok)
        dlg.exec_()

    def _execute_request(self, mode="local"):
        curl_cmd = self.preview.toPlainText()
        if not curl_cmd.strip():
            self._show_status("请先生成 cURL 命令", self.theme.RED)
            return

        self._exec_process = None
        self.exec_btn.setEnabled(False)
        self.exec_stop_btn.setEnabled(True)
        self.response_view.clear()
        self._show_status("执行中...", self.theme.YELLOW)
        self.response_info.setText("状态: 执行中...")
        # 切换到响应结果 tab
        self.bottom_tabs.setCurrentIndex(0)
        logger.info("开始执行请求 (mode=%s)", mode)

        if mode == "local":
            threading.Thread(target=self._run_local, args=(curl_cmd,), daemon=True).start()
        else:
            # 按当前环境解析 SSH 目标机（环境启用 SSH 时用其绑定标签，否则用第一个目标机）
            env_alias = self.env_dropdown.currentText()
            target_label = env_ssh_target_alias(env_alias)
            ssh_cfg = resolve_ssh_cfg(self.ssh_data, target_label)
            # 校验 SSH 配置：堡垒机 + 目标机地址即可
            #（目标机认证由堡垒机菜单代管，无需目标机密码）
            config_ok = all([
                (ssh_cfg.get("jump_host") or "").strip(),
                (ssh_cfg.get("jump_user") or "").strip(),
                (ssh_cfg.get("jump_pass") or "").strip(),
                (ssh_cfg.get("target_host") or "").strip(),
            ])
            if not config_ok:
                logger.warning("SSH 配置不完整: jump_host=%s target=%s(%s)",
                               ssh_cfg.get("jump_host") or "(空)",
                               ssh_cfg.get("target_label") or "(空)", ssh_cfg.get("target_host") or "(空)")
                msg = "SSH 配置不完整，请在菜单「设置 → SSH 配置」中设置跳板机与目标机"
                self._show_status(msg, self.theme.RED)
                self.response_info.setText("状态: 配置不完整")
                self.response_view.append(f"──── 状态: 配置不完整 ────")
                self.response_view.append(f"[错误] {msg}")
                self.exec_btn.setEnabled(True)
                self.exec_stop_btn.setEnabled(False)
                return
            threading.Thread(target=self._run_ssh, args=(curl_cmd, ssh_cfg), daemon=True).start()

    def _stop_execution(self):
        proc = self._exec_process
        if proc is not None:
            try:
                proc.stop()
                logger.info("已请求停止执行")
            except Exception as e:
                logger.warning("停止执行失败: %s", e)
        self._show_status("正在停止...", self.theme.RED)
        self.response_info.setText("状态: 正在停止...")
        # 按钮由工作线程结束时统一恢复

    def _run_local(self, curl_cmd):
        timeout = self._get_exec_timeout()
        executor = LocalExecutor(on_progress=self._on_exec_progress)
        self._exec_process = executor
        try:
            result = executor.run(curl_cmd, timeout=timeout)
            body = result["stdout"] if result["stdout"] else result["stderr"]
            self._update_response(result["exit_code"], body, streamed=True)
        except ExecutorStop as e:
            self._update_response(-2, f"执行已停止: {e}", streamed=True)
        except ExecutionTimeout as e:
            self._update_response(-3, str(e), streamed=True)
        except ExecutionError as e:
            self._update_response(-1, f"执行失败: {e}", streamed=True)
        except Exception as e:
            logger.exception("本地执行异常")
            self._update_response(-1, f"执行异常: {e}", streamed=True)
        finally:
            self._exec_process = None
            self._exec_done()

    def _run_ssh(self, curl_cmd, ssh_cfg=None):
        timeout = self._get_exec_timeout()
        cfg = ssh_cfg or resolve_ssh_cfg(self.ssh_data)
        executor = SSHRemoteExecutor(cfg, on_progress=self._on_exec_progress)
        self._exec_process = executor
        try:
            result = executor.run(curl_cmd, timeout=timeout)
            body = result["stdout"] if result["stdout"] else result["stderr"]
            self._update_response(result["exit_code"], body, streamed=True)
        except ExecutorStop as e:
            self._update_response(-2, f"执行已停止: {e}", streamed=True)
        except ExecutionTimeout as e:
            self._update_response(-3, str(e), streamed=True)
        except ExecutionError as e:
            self._update_response(-1, f"SSH 失败: {e}", streamed=True)
        except Exception as e:
            logger.exception("SSH 执行异常")
            self._update_response(-1, f"SSH 执行异常: {e}", streamed=True)
        finally:
            executor.close()
            self._exec_process = None
            self._exec_done()

    def _update_response(self, code, body, streamed=False):
        """工作线程 → 主线程：经信号把执行结果带回 UI"""
        self._sig_response.emit(code, body, streamed)

    def _apply_response(self, code, body, streamed):
        """主线程：渲染执行结果。
        code: 0=成功, -1=失败, -2=已停止, -3=超时, 其余为远端 exit code
        """
        if code == 0:
            status_text = "状态: 成功 (exit 0)"
            color = self.theme.GREEN
            bar_text = "执行完成"
        elif code == -1:
            status_text = "状态: 失败"
            color = self.theme.RED
            bar_text = "执行失败"
        elif code == -2:
            status_text = "状态: 已停止"
            color = self.theme.YELLOW
            bar_text = "已停止"
        elif code == -3:
            status_text = "状态: 超时"
            color = self.theme.RED
            bar_text = "执行超时"
        else:
            status_text = f"状态: exit {code}"
            color = self.theme.YELLOW
            bar_text = f"执行结束 (exit {code})"

        if streamed:
            # 实时输出已在面板中；失败/停止/超时的错误信息未流式展示，补到面板里
            if code < 0 and body and body.strip():
                self.response_view.append("")
                self.response_view.append(f"[错误] {body.strip()}")
            self.response_view.append("")
            self.response_view.append(f"──── {status_text} ────")
        else:
            self.response_view.setPlainText(body.strip() if body else "(空响应)")
        self.response_info.setText(status_text)
        self.response_info.setStyleSheet(f"color: {color}; font-weight: bold;")
        self._show_status(bar_text, color)

    def _exec_done(self):
        """工作线程 → 主线程：恢复按钮状态"""
        self._sig_done.emit()

    def _exec_done_ui(self):
        self.exec_btn.setEnabled(True)
        self.exec_stop_btn.setEnabled(False)
