"""对话框：环境配置管理、SSH 配置、保存快捷请求"""
import logging
import threading

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QGroupBox, QCheckBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QMessageBox, QComboBox,
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont

from .config import load_env_configs, save_env_configs, save_ssh_config
from .theme import solid_button_qss, ghost_button_qss
from .widgets import StyledDropdown, KeyValueEditor
from .ssh_executor import SSHRemoteExecutor

logger = logging.getLogger("curlgen.dialogs")


# ──────────────────────────── 环境配置管理对话框 ────────────────────────────
class EnvManageDialog(QDialog):
    def __init__(self, parent, theme):
        super().__init__(parent)
        self.parent_win = parent
        self.theme = theme
        self.configs = load_env_configs()
        self._editing_index = -1  # -1 = 新增模式, >=0 = 编辑模式
        self.setWindowTitle("环境配置管理")
        self.setFixedSize(560, 600)
        self.init_ui()
        self.apply_theme(theme)

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        title = QLabel("环境配置管理")
        title.setFont(QFont("Segoe UI", 15, QFont.Bold))
        layout.addWidget(title)

        # 列表
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["别名", "签名", "算法", "参数名"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.cellDoubleClicked.connect(self._on_row_double_clicked)
        layout.addWidget(self.table, 1)

        # 双击提示
        dbl_hint = QLabel("双击表格行可加载到编辑区进行修改")
        dbl_hint.setFont(QFont("Segoe UI", 9))
        layout.addWidget(dbl_hint)

        self._refresh_table()

        # 编辑区域
        self.edit_group = QGroupBox("编辑环境配置")
        self.edit_group.setFont(QFont("Segoe UI", 10, QFont.Bold))
        edit_layout = QVBoxLayout(self.edit_group)

        # 别名
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("环境别名:"))
        self.alias_input = QLineEdit()
        self.alias_input.setPlaceholderText("唯一别名，如: 测试环境、生产环境")
        row1.addWidget(self.alias_input, 1)
        edit_layout.addLayout(row1)

        # 启用签名
        self.enable_sign_check = QCheckBox("启用签名")
        self.enable_sign_check.setChecked(True)
        self.enable_sign_check.stateChanged.connect(self._on_sign_toggle)
        edit_layout.addWidget(self.enable_sign_check)

        # 签名密钥
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("签名密钥:"))
        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText("签名密钥 (keySecret)")
        self.key_input.setEchoMode(QLineEdit.Password)
        row2.addWidget(self.key_input, 1)
        edit_layout.addLayout(row2)

        # 算法 + 参数名
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("签名算法:"))
        self.algo_input = StyledDropdown(["MD5", "SHA256"])
        self.algo_input.setFixedWidth(100)
        row3.addWidget(self.algo_input)
        row3.addSpacing(16)
        row3.addWidget(QLabel("参数名:"))
        self.param_name_input = QLineEdit("sign")
        self.param_name_input.setFixedWidth(100)
        row3.addWidget(self.param_name_input)
        row3.addStretch()
        edit_layout.addLayout(row3)

        # 自定义环境参数
        params_label = QLabel("自定义环境变量 (可在其他字段中用 {{变量名}} 引用):")
        params_label.setFont(QFont("Segoe UI", 10))
        edit_layout.addWidget(params_label)

        self.custom_params_editor = KeyValueEditor("参数名", "参数值")
        edit_layout.addWidget(self.custom_params_editor)

        layout.addWidget(self.edit_group)

        # 按钮行
        btn_row = QHBoxLayout()
        self.save_btn = QPushButton("+ 添加")
        self.save_btn.setFixedHeight(32)
        self.save_btn.setCursor(Qt.PointingHandCursor)
        self.save_btn.clicked.connect(self._save_config)
        btn_row.addWidget(self.save_btn)

        self.cancel_edit_btn = QPushButton("取消编辑")
        self.cancel_edit_btn.setFixedHeight(32)
        self.cancel_edit_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_edit_btn.setVisible(False)
        self.cancel_edit_btn.clicked.connect(self._cancel_edit)
        btn_row.addWidget(self.cancel_edit_btn)

        del_btn = QPushButton("- 删除选中")
        del_btn.setFixedHeight(32)
        del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.clicked.connect(self._del_config)
        btn_row.addWidget(del_btn)

        btn_row.addStretch()

        close_btn = QPushButton("关闭")
        close_btn.setFixedHeight(32)
        close_btn.setFixedWidth(80)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)

    def _on_sign_toggle(self, state):
        enabled = state == Qt.Checked
        self.key_input.setEnabled(enabled)
        self.algo_input.setEnabled(enabled)
        self.param_name_input.setEnabled(enabled)

    def _refresh_table(self):
        self.table.setRowCount(0)
        for cfg in self.configs:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(cfg.get("alias", "")))
            self.table.setItem(row, 1, QTableWidgetItem("✓" if cfg.get("enable_sign", True) else "✗"))
            self.table.setItem(row, 2, QTableWidgetItem(cfg.get("algo", "MD5")))
            self.table.setItem(row, 3, QTableWidgetItem(cfg.get("param_name", "sign")))

    def _on_row_double_clicked(self, row, col):
        """双击表格行 → 加载到编辑区"""
        if row < 0 or row >= len(self.configs):
            return
        cfg = self.configs[row]
        self._editing_index = row
        self.alias_input.setText(cfg.get("alias", ""))
        self.enable_sign_check.setChecked(cfg.get("enable_sign", True))
        self.key_input.setText(cfg.get("key", ""))
        self.algo_input.setCurrentText(cfg.get("algo", "MD5"))
        self.param_name_input.setText(cfg.get("param_name", "sign"))
        custom_params = cfg.get("custom_params", [])
        self.custom_params_editor.set_pairs([(k, v) for k, v in custom_params])
        self._set_edit_mode(True)

    def _set_edit_mode(self, editing):
        """切换 新增/编辑 模式的 UI 状态"""
        if editing:
            self.save_btn.setText("✓ 保存修改")
            self.cancel_edit_btn.setVisible(True)
            self.alias_input.setEnabled(False)
            self.edit_group.setTitle("编辑环境配置 — 修改模式")
        else:
            self._editing_index = -1
            self.save_btn.setText("+ 添加")
            self.cancel_edit_btn.setVisible(False)
            self.alias_input.setEnabled(True)
            self.alias_input.clear()
            self.enable_sign_check.setChecked(True)
            self.key_input.clear()
            self.algo_input.setCurrentText("MD5")
            self.param_name_input.setText("sign")
            self.custom_params_editor.clear_all()
            self.edit_group.setTitle("编辑环境配置")
            # 恢复签名字段启用状态
            self.key_input.setEnabled(True)
            self.algo_input.setEnabled(True)
            self.param_name_input.setEnabled(True)

    def _cancel_edit(self):
        self._set_edit_mode(False)

    def _save_config(self):
        alias = self.alias_input.text().strip()
        enable_sign = self.enable_sign_check.isChecked()
        key = self.key_input.text().strip()
        algo = self.algo_input.currentText()
        param_name = self.param_name_input.text().strip() or "sign"
        custom_params = self.custom_params_editor.get_pairs()

        if not alias:
            QMessageBox.warning(self, "提示", "请输入环境别名")
            return
        if enable_sign and not key:
            QMessageBox.warning(self, "提示", "启用签名时请输入签名密钥")
            return

        env_data = {
            "alias": alias,
            "enable_sign": enable_sign,
            "key": key,
            "algo": algo,
            "param_name": param_name,
            "custom_params": custom_params,
        }

        if self._editing_index >= 0:
            # 编辑模式 → 更新现有配置
            self.configs[self._editing_index].update(env_data)
            logger.info("更新环境配置: %s", alias)
        else:
            # 新增模式 → 检查别名唯一性
            for cfg in self.configs:
                if cfg["alias"] == alias:
                    QMessageBox.warning(self, "提示", f"别名 '{alias}' 已存在，请使用其他名称")
                    return
            self.configs.append(env_data)
            logger.info("新增环境配置: %s (签名=%s, 算法=%s)", alias, enable_sign, algo)

        save_env_configs(self.configs)
        self._refresh_table()
        self._set_edit_mode(False)
        self.parent_win._refresh_env_dropdown()

    def _del_config(self):
        row = self.table.currentRow()
        if row < 0:
            return
        alias = self.configs[row]["alias"]
        if QMessageBox.question(self, "确认删除", f"确定删除环境配置 '{alias}' 吗？") == QMessageBox.Yes:
            self.configs.pop(row)
            logger.info("删除环境配置: %s", alias)
            save_env_configs(self.configs)
            self._refresh_table()
            if self._editing_index == row:
                self._set_edit_mode(False)
            elif self._editing_index > row:
                self._editing_index -= 1
            self.parent_win._refresh_env_dropdown()

    def apply_theme(self, theme):
        t = theme
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {t.BG_PRIMARY};
            }}
            QLabel {{
                color: {t.TEXT_PRIMARY};
                background: transparent;
            }}
            QGroupBox {{
                color: {t.TEXT_PRIMARY};
                border: 1px solid {t.BORDER};
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 16px;
                font-weight: bold;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 8px;
                color: {t.ACCENT};
            }}
            QLineEdit {{
                background-color: {t.BG_PRIMARY};
                color: {t.TEXT_PRIMARY};
                border: 1px solid {t.BORDER};
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 13px;
            }}
            QLineEdit:focus {{
                border-color: {t.ACCENT};
            }}
            QTableWidget {{
                background-color: {t.BG_PRIMARY};
                color: {t.TEXT_PRIMARY};
                border: 1px solid {t.BORDER};
                border-radius: 6px;
                gridline-color: {t.BORDER};
                font-size: 12px;
            }}
            QTableWidget::item:selected {{
                background-color: {t.BG_HOVER};
            }}
            QHeaderView::section {{
                background-color: {t.BG_SURFACE};
                color: {t.TEXT_SECONDARY};
                padding: 6px;
                border: none;
                border-bottom: 1px solid {t.BORDER};
                font-weight: bold;
            }}
        """)
        self.algo_input.apply_theme(t)
        self.custom_params_editor.apply_theme(t)
        # Style buttons
        for btn in self.findChildren(QPushButton):
            text = btn.text()
            if text in ("+ 添加", "✓ 保存修改"):
                btn.setStyleSheet(ghost_button_qss(t, t.GREEN))
            elif text == "取消编辑":
                btn.setStyleSheet(ghost_button_qss(t, t.YELLOW))
            elif text == "- 删除选中":
                btn.setStyleSheet(ghost_button_qss(t, t.RED))
            elif text == "关闭":
                btn.setStyleSheet(solid_button_qss(t, t.ACCENT, radius=8))


# ──────────────────────────── SSH 配置对话框 ────────────────────────────
class SSHConfigDialog(QDialog):
    # 连接测试结果：工作线程 → 主线程（is_target, ok, message）
    _sig_test_done = pyqtSignal(bool, bool, str)
    # 授权机器列表：工作线程 → 主线程（ok, hosts_or_message）
    _sig_hosts_done = pyqtSignal(bool, object)

    def __init__(self, parent, theme, ssh_data):
        super().__init__(parent)
        self.theme = theme
        self.ssh_data = ssh_data  # reference to parent's dict
        self._hosts = []
        self.setWindowTitle("SSH 配置")
        self.setFixedSize(460, 580)
        self.init_ui()
        self._load_data()
        self.apply_theme(theme)
        self._sig_test_done.connect(self._on_test_done)
        self._sig_hosts_done.connect(self._on_hosts_done)

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        title = QLabel("SSH 远程执行配置")
        title.setFont(QFont("Segoe UI", 14, QFont.Bold))
        layout.addWidget(title)

        # 跳板机
        jump_label = QLabel("跳板机 (Jump Host)")
        jump_label.setFont(QFont("Segoe UI", 10, QFont.Bold))
        layout.addWidget(jump_label)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("主机:"))
        self.jump_host = QLineEdit()
        self.jump_host.setPlaceholderText("IP / 域名")
        row1.addWidget(self.jump_host, 2)
        row1.addWidget(QLabel("端口:"))
        self.jump_port = QLineEdit("22")
        self.jump_port.setFixedWidth(60)
        row1.addWidget(self.jump_port)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("用户:"))
        self.jump_user = QLineEdit()
        self.jump_user.setPlaceholderText("用户名")
        row2.addWidget(self.jump_user, 1)
        row2.addWidget(QLabel("密码:"))
        self.jump_pass = QLineEdit()
        self.jump_pass.setPlaceholderText("密码")
        self.jump_pass.setEchoMode(QLineEdit.Password)
        row2.addWidget(self.jump_pass, 1)
        layout.addLayout(row2)

        # 目标机
        target_label = QLabel("目标机 (Target Host)")
        target_label.setFont(QFont("Segoe UI", 10, QFont.Bold))
        target_label.setStyleSheet("margin-top: 8px;")
        layout.addWidget(target_label)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("主机:"))
        self.target_host = QLineEdit()
        self.target_host.setPlaceholderText("IP / 域名")
        row3.addWidget(self.target_host, 2)
        row3.addWidget(QLabel("端口:"))
        self.target_port = QLineEdit("22")
        self.target_port.setFixedWidth(60)
        row3.addWidget(self.target_port)
        layout.addLayout(row3)

        row4 = QHBoxLayout()
        row4.addWidget(QLabel("用户:"))
        self.target_user = QLineEdit()
        self.target_user.setPlaceholderText("登录用户名（由堡垒机授权决定，可留空）")
        row4.addWidget(self.target_user, 1)
        self.load_hosts_btn = QPushButton("加载授权机器")
        self.load_hosts_btn.setFixedSize(112, 30)
        self.load_hosts_btn.setCursor(Qt.PointingHandCursor)
        self.load_hosts_btn.setToolTip("连接堡垒机，读取当前账号被授权的机器列表")
        self.load_hosts_btn.clicked.connect(self._start_load_hosts)
        row4.addWidget(self.load_hosts_btn)
        layout.addLayout(row4)

        # 授权机器下拉：堡垒机（GateShell）唯一入口就是它的菜单
        row5 = QHBoxLayout()
        row5.addWidget(QLabel("授权列表:"))
        self.host_combo = QComboBox()
        self.host_combo.setToolTip("选择后自动回填上方主机/端口/用户")
        self.host_combo.activated.connect(self._on_host_picked)
        row5.addWidget(self.host_combo, 1)
        layout.addLayout(row5)

        # 连接测试
        test_row = QHBoxLayout()
        self.test_jump_btn = QPushButton("测试跳板机连接")
        self.test_jump_btn.setFixedHeight(32)
        self.test_jump_btn.setCursor(Qt.PointingHandCursor)
        self.test_jump_btn.clicked.connect(lambda: self._start_test(False))
        test_row.addWidget(self.test_jump_btn)

        self.test_target_btn = QPushButton("测试目标机连接")
        self.test_target_btn.setFixedHeight(32)
        self.test_target_btn.setCursor(Qt.PointingHandCursor)
        self.test_target_btn.setToolTip("连接堡垒机 → 在授权菜单中选择该机器 → 进入其 shell")
        self.test_target_btn.clicked.connect(lambda: self._start_test(True))
        test_row.addWidget(self.test_target_btn)
        layout.addLayout(test_row)

        self.test_status = QLabel("")
        self.test_status.setWordWrap(True)
        layout.addWidget(self.test_status)

        layout.addStretch()

        # 按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.setFixedSize(80, 34)
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        save_btn = QPushButton("保存")
        save_btn.setFixedSize(80, 34)
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def _load_data(self):
        self.jump_host.setText(self.ssh_data.get("jump_host", ""))
        self.jump_port.setText(self.ssh_data.get("jump_port", "22"))
        self.jump_user.setText(self.ssh_data.get("jump_user", ""))
        self.jump_pass.setText(self.ssh_data.get("jump_pass", ""))
        self.target_host.setText(self.ssh_data.get("target_host", ""))
        self.target_port.setText(self.ssh_data.get("target_port", "22"))
        self.target_user.setText(self.ssh_data.get("target_user", ""))

    def _save(self):
        self.ssh_data["jump_host"] = self.jump_host.text().strip()
        self.ssh_data["jump_port"] = self.jump_port.text().strip()
        self.ssh_data["jump_user"] = self.jump_user.text().strip()
        self.ssh_data["jump_pass"] = self.jump_pass.text().strip()
        self.ssh_data["target_host"] = self.target_host.text().strip()
        self.ssh_data["target_port"] = self.target_port.text().strip()
        self.ssh_data["target_user"] = self.target_user.text().strip()
        # 目标机密码已废弃：GateShell 菜单进入目标机由堡垒机代管认证
        self.ssh_data.pop("target_pass", None)
        save_ssh_config(self.ssh_data)
        self.accept()

    # ──────────────────── 授权机器列表 ────────────────────
    def _start_load_hosts(self):
        if not self.load_hosts_btn.isEnabled():
            return
        cfg = self._collect_fields()
        self.load_hosts_btn.setEnabled(False)
        self.test_jump_btn.setEnabled(False)
        self.test_target_btn.setEnabled(False)
        self.host_combo.clear()
        self.test_status.setText("正在读取堡垒机授权机器列表 ...")
        self.test_status.setStyleSheet(f"color: {self.theme.YELLOW};")
        threading.Thread(target=self._run_load_hosts, args=(cfg,), daemon=True).start()

    def _run_load_hosts(self, cfg):
        try:
            ok, result = SSHRemoteExecutor.list_authorized_hosts(cfg)
        except Exception as e:
            logger.exception("加载授权机器列表异常")
            ok, result = False, f"加载授权机器列表异常: {e}"
        self._sig_hosts_done.emit(ok, result)

    def _on_hosts_done(self, ok, result):
        self.load_hosts_btn.setEnabled(True)
        self.test_jump_btn.setEnabled(True)
        self.test_target_btn.setEnabled(True)
        if not ok:
            self.test_status.setText(str(result))
            self.test_status.setStyleSheet(f"color: {self.theme.RED};")
            logger.info("加载授权机器列表失败: %s", result)
            return
        self._hosts = result
        current = self.target_host.text().strip()
        cur_port = self.target_port.text().strip() or "22"
        for h in result:
            self.host_combo.addItem(f"{h['index']}  {h['name']}  ({h['ip']}:{h['port']})", h)
        # 已有配置若在授权列表内，默认选中它
        for i, h in enumerate(result):
            if h["ip"] == current and str(h["port"]) == cur_port:
                self.host_combo.setCurrentIndex(i)
                break
        msg = f"已加载 {len(result)} 台授权机器"
        self.test_status.setText(msg)
        self.test_status.setStyleSheet(f"color: {self.theme.GREEN};")
        logger.info("%s", msg)

    def _on_host_picked(self, index):
        h = self.host_combo.itemData(index)
        if not h:
            return
        self.target_host.setText(h["ip"])
        self.target_port.setText(str(h["port"]))
        if h.get("user"):
            self.target_user.setText(h["user"])
        logger.info("已选择授权机器: %s (%s)", h["name"], h["ip"])

    # ──────────────────── 连接测试 ────────────────────
    def _collect_fields(self):
        """读取对话框当前输入（未保存也生效）"""
        return {
            "jump_host": self.jump_host.text().strip(),
            "jump_port": self.jump_port.text().strip(),
            "jump_user": self.jump_user.text().strip(),
            "jump_pass": self.jump_pass.text().strip(),
            "target_host": self.target_host.text().strip(),
            "target_port": self.target_port.text().strip(),
            "target_user": self.target_user.text().strip(),
        }

    def _start_test(self, is_target):
        if not self.test_jump_btn.isEnabled():
            return
        cfg = self._collect_fields()
        self.test_jump_btn.setEnabled(False)
        self.test_target_btn.setEnabled(False)
        label = "目标机（跳板机 → 目标机）" if is_target else "跳板机"
        self.test_status.setText(f"正在测试{label}连接 ...")
        self.test_status.setStyleSheet(f"color: {self.theme.YELLOW};")
        threading.Thread(target=self._run_test, args=(cfg, is_target), daemon=True).start()

    def _run_test(self, cfg, is_target):
        try:
            ok, msg = SSHRemoteExecutor.test_connection(cfg, include_target=is_target)
        except Exception as e:
            logger.exception("连接测试异常")
            ok, msg = False, f"连接测试异常: {e}"
        self._sig_test_done.emit(is_target, ok, msg)

    def _on_test_done(self, is_target, ok, msg):
        self.test_jump_btn.setEnabled(True)
        self.test_target_btn.setEnabled(True)
        self.load_hosts_btn.setEnabled(True)
        color = self.theme.GREEN if ok else self.theme.RED
        self.test_status.setText(msg)
        self.test_status.setStyleSheet(f"color: {color};")
        logger.info("连接测试完成: target=%s ok=%s msg=%s", is_target, ok, msg)

    def apply_theme(self, theme):
        t = theme
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {t.BG_PRIMARY};
            }}
            QLabel {{
                color: {t.TEXT_PRIMARY};
                background: transparent;
                font-size: 13px;
            }}
            QLineEdit {{
                background-color: {t.BG_PRIMARY};
                color: {t.TEXT_PRIMARY};
                border: 1px solid {t.BORDER};
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 13px;
            }}
            QLineEdit:focus {{
                border-color: {t.ACCENT};
            }}
            QComboBox {{
                background-color: {t.BG_PRIMARY};
                color: {t.TEXT_PRIMARY};
                border: 1px solid {t.BORDER};
                border-radius: 6px;
                padding: 5px 8px;
                font-size: 12px;
                min-height: 22px;
            }}
            QComboBox:focus {{ border-color: {t.ACCENT}; }}
            QComboBox QAbstractItemView {{
                background-color: {t.BG_SURFACE};
                color: {t.TEXT_PRIMARY};
                border: 1px solid {t.BORDER};
                selection-background-color: {t.ACCENT};
                selection-color: {t.BG_PRIMARY};
                outline: none;
            }}
            QComboBox::drop-down {{ border: none; width: 18px; }}
        """)
        for btn in self.findChildren(QPushButton):
            if btn.text() == "保存":
                btn.setStyleSheet(solid_button_qss(t, t.ACCENT, radius=8))
            elif btn.text() == "取消":
                btn.setStyleSheet(ghost_button_qss(t, t.TEXT_SECONDARY))


# ──────────────────────────── 保存请求对话框 ────────────────────────────
class SaveRequestDialog(QDialog):
    """保存/更新快捷请求的对话框"""
    def __init__(self, parent, theme, groups, current_name=""):
        super().__init__(parent)
        self.theme = theme
        self.groups = groups
        self.result_name = ""
        self.result_group = ""
        self.init_ui(current_name)
        self.apply_theme(theme)

    def init_ui(self, current_name):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 16, 20, 16)

        title = QLabel("保存快捷请求")
        title.setFont(QFont("Segoe UI", 14, QFont.Bold))
        layout.addWidget(title)

        # 请求名称
        layout.addWidget(QLabel("请求名称:"))
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("例如: 获取用户信息")
        self.name_input.setFont(QFont("Consolas", 11))
        self.name_input.setFixedHeight(34)
        if current_name:
            self.name_input.setText(current_name)
        layout.addWidget(self.name_input)

        # 选择分组
        layout.addWidget(QLabel("保存到分组:"))
        self.group_combo = StyledDropdown(["(新建分组)"] + [g["name"] for g in self.groups])
        self.group_combo.setFixedHeight(34)
        layout.addWidget(self.group_combo)

        # 新建分组名称（仅在选择"新建分组"时显示）
        self.new_group_input = QLineEdit()
        self.new_group_input.setPlaceholderText("输入新分组名称")
        self.new_group_input.setFont(QFont("Consolas", 11))
        self.new_group_input.setFixedHeight(34)
        self.new_group_input.setVisible(False)
        layout.addWidget(self.new_group_input)

        self.group_combo.currentTextChanged.connect(
            lambda t: self.new_group_input.setVisible(t == "(新建分组)")
        )

        # 按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.setFixedHeight(34)
        cancel_btn.setFixedWidth(80)
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        save_btn = QPushButton("保存")
        save_btn.setFixedHeight(34)
        save_btn.setFixedWidth(80)
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.setFont(QFont("Segoe UI", 10, QFont.Bold))
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)

        layout.addLayout(btn_row)

        if current_name:
            self.name_input.selectAll()

    def _on_save(self):
        name = self.name_input.text().strip()
        if not name:
            self.name_input.setFocus()
            return
        group_text = self.group_combo.currentText()
        if group_text == "(新建分组)":
            group_name = self.new_group_input.text().strip()
            if not group_name:
                self.new_group_input.setFocus()
                return
        else:
            group_name = group_text
        self.result_name = name
        self.result_group = group_name
        self.accept()

    def apply_theme(self, theme):
        self.setStyleSheet(f"""
            QDialog {{ background: {theme.BG_PRIMARY}; color: {theme.TEXT_PRIMARY}; }}
        """)
        self.group_combo.apply_theme(theme)
        for btn in self.findChildren(QPushButton):
            if btn.text() == "保存":
                btn.setStyleSheet(solid_button_qss(theme, theme.ACCENT, radius=8))
            else:
                btn.setStyleSheet(ghost_button_qss(theme, theme.TEXT_SECONDARY))
