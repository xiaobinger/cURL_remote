"""左侧快捷请求面板：分组折叠展示 + 右键管理"""
import json
import logging

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QFrame, QMenu, QInputDialog, QMessageBox,
)
from PyQt5.QtCore import Qt, QPoint, pyqtSignal
from PyQt5.QtGui import QFont

from .config import load_saved_requests, save_saved_requests

logger = logging.getLogger("curlgen.saved_panel")


class SavedRequestsPanel(QWidget):
    """左侧快捷请求面板，分组折叠展示"""
    request_clicked = pyqtSignal(dict)  # 点击请求时发射完整请求数据
    new_request_requested = pyqtSignal()  # 右键"新建请求"（清空表单，避免覆盖已保存请求）

    def __init__(self, theme):
        super().__init__()
        self.theme = theme
        self.data = load_saved_requests()
        self._group_widgets = {}  # group_name -> {"header": btn, "container": widget, "items": {req_name: widget}}
        self.setFixedWidth(220)
        self.init_ui()
        self._rebuild()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 标题栏
        header = QWidget()
        header.setFixedHeight(44)
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(12, 0, 8, 0)

        title = QLabel("快捷请求")
        title.setFont(QFont("Segoe UI", 12, QFont.Bold))
        h_layout.addWidget(title)
        h_layout.addStretch()

        # 新建分组按钮
        self.add_group_btn = QPushButton("+")
        self.add_group_btn.setFixedSize(28, 28)
        self.add_group_btn.setToolTip("新建分组")
        self.add_group_btn.setCursor(Qt.PointingHandCursor)
        self.add_group_btn.clicked.connect(self._add_group)
        h_layout.addWidget(self.add_group_btn)

        layout.addWidget(header)
        layout.addWidget(self._hline())

        # 滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.container = QWidget()
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setContentsMargins(4, 4, 4, 4)
        self.container_layout.setSpacing(2)
        self.container_layout.addStretch()

        # 空白处右键：新建请求 / 新建分组
        self.container.setContextMenuPolicy(Qt.CustomContextMenu)
        self.container.customContextMenuRequested.connect(self._blank_context_menu)

        scroll.setWidget(self.container)
        layout.addWidget(scroll, 1)

    def _hline(self):
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFixedHeight(1)
        return line

    def _rebuild(self):
        """重建整个面板"""
        # 清除旧的
        for w in list(self._group_widgets.values()):
            w["header"].deleteLater()
            w["container"].deleteLater()
        self._group_widgets.clear()

        for group in self.data.get("groups", []):
            self._add_group_widget(group)

    def _add_group_widget(self, group):
        g_name = group["name"]
        collapsed = group.get("collapsed", False)

        # 分组头
        header_btn = QPushButton(f"{'▸' if collapsed else '▾'} {g_name}")
        header_btn.setFont(QFont("Segoe UI", 10, QFont.Bold))
        header_btn.setFixedHeight(32)
        header_btn.setCursor(Qt.PointingHandCursor)
        header_btn.setCheckable(False)
        header_btn.setProperty("group_name", g_name)

        # 分组容器
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(8, 0, 0, 0)
        container_layout.setSpacing(1)

        items = {}
        for req in group.get("requests", []):
            req_btn = self._make_req_btn(req, g_name)
            container_layout.addWidget(req_btn)
            items[req["name"]] = req_btn

        container_layout.addStretch()
        container.setVisible(not collapsed)

        # 点击折叠
        def toggle():
            vis = not container.isVisible()
            container.setVisible(vis)
            g = next((gg for gg in self.data["groups"] if gg["name"] == g_name), None)
            if g:
                g["collapsed"] = not vis
                save_saved_requests(self.data)
            header_btn.setText(f"{'▸' if not vis else '▾'} {g_name}")

        header_btn.clicked.connect(toggle)

        # 右键菜单
        header_btn.setContextMenuPolicy(Qt.CustomContextMenu)
        header_btn.customContextMenuRequested.connect(
            lambda pos, gn=g_name: self._group_context_menu(gn, pos)
        )

        # 插入到 stretch 之前
        self.container_layout.insertWidget(self.container_layout.count() - 1, header_btn)
        self.container_layout.insertWidget(self.container_layout.count() - 1, container)

        self._group_widgets[g_name] = {"header": header_btn, "container": container, "items": items}
        self._apply_group_theme(header_btn)

    def _make_req_btn(self, req, group_name):
        btn = QPushButton(req["name"])
        btn.setFont(QFont("Segoe UI", 9))
        btn.setFixedHeight(30)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet("padding-left: 12px; border: none; border-radius: 4px; text-align: left;")
        btn.setProperty("req_data", json.dumps(req, ensure_ascii=False))
        btn.setProperty("group_name", group_name)

        btn.clicked.connect(lambda checked, r=req, gn=group_name: self.request_clicked.emit({**r, "_group_name": gn}))

        # 右键菜单
        btn.setContextMenuPolicy(Qt.CustomContextMenu)
        btn.customContextMenuRequested.connect(
            lambda pos, r=req, gn=group_name: self._req_context_menu(r, gn, pos)
        )

        return btn

    def _group_context_menu(self, group_name, pos):
        menu = QMenu(self)
        new_req_action = menu.addAction("新建请求")
        menu.addSeparator()
        rename_action = menu.addAction("重命名分组")
        delete_action = menu.addAction("删除分组")
        action = menu.exec_(self._group_widgets[group_name]["header"].mapToGlobal(pos))
        if action == new_req_action:
            self.new_request_requested.emit()
        elif action == rename_action:
            self._rename_group(group_name)
        elif action == delete_action:
            self._delete_group(group_name)

    def _blank_context_menu(self, pos):
        """面板空白处右键菜单"""
        menu = QMenu(self)
        new_req_action = menu.addAction("新建请求")
        new_group_action = menu.addAction("新建分组")
        action = menu.exec_(self.container.mapToGlobal(pos))
        if action == new_req_action:
            self.new_request_requested.emit()
        elif action == new_group_action:
            self._add_group()

    def _req_context_menu(self, req, group_name, pos):
        menu = QMenu(self)
        new_req_action = menu.addAction("新建请求")
        menu.addSeparator()
        delete_action = menu.addAction("删除")
        rename_action = menu.addAction("重命名")
        items = self._group_widgets.get(group_name, {}).get("items", {})
        widget = items.get(req["name"])
        if widget:
            action = menu.exec_(widget.mapToGlobal(QPoint(0, 0)))
            if action == new_req_action:
                self.new_request_requested.emit()
            elif action == delete_action:
                self._delete_request(group_name, req["name"])
            elif action == rename_action:
                self._rename_request(group_name, req["name"])

    def _add_group(self):
        name, ok = QInputDialog.getText(self, "新建分组", "分组名称:")
        if ok and name.strip():
            name = name.strip()
            if any(g["name"] == name for g in self.data["groups"]):
                return
            self.data.setdefault("groups", []).append({"name": name, "collapsed": False, "requests": []})
            logger.info("新建快捷请求分组: %s", name)
            save_saved_requests(self.data)
            self._rebuild()
            self.apply_theme(self.theme)

    def _rename_group(self, old_name):
        new_name, ok = QInputDialog.getText(self, "重命名分组", "新名称:", text=old_name)
        if ok and new_name.strip() and new_name.strip() != old_name:
            new_name = new_name.strip()
            if any(g["name"] == new_name for g in self.data["groups"]):
                return
            for g in self.data["groups"]:
                if g["name"] == old_name:
                    g["name"] = new_name
                    break
            logger.info("重命名分组: %s → %s", old_name, new_name)
            save_saved_requests(self.data)
            self._rebuild()
            self.apply_theme(self.theme)

    def _delete_group(self, group_name):
        reply = QMessageBox.question(self, "确认", f"确定删除分组 \"{group_name}\" 及其所有请求？",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.data["groups"] = [g for g in self.data["groups"] if g["name"] != group_name]
            logger.info("删除分组: %s", group_name)
            save_saved_requests(self.data)
            self._rebuild()

    def _rename_request(self, group_name, old_name):
        new_name, ok = QInputDialog.getText(self, "重命名请求", "新名称:", text=old_name)
        if ok and new_name.strip() and new_name.strip() != old_name:
            new_name = new_name.strip()
            for g in self.data["groups"]:
                if g["name"] == group_name:
                    for r in g["requests"]:
                        if r["name"] == old_name:
                            r["name"] = new_name
                            break
                    break
            logger.info("重命名快捷请求: %s/%s → %s", group_name, old_name, new_name)
            save_saved_requests(self.data)
            self._rebuild()
            self.apply_theme(self.theme)

    def _delete_request(self, group_name, req_name):
        for g in self.data["groups"]:
            if g["name"] == group_name:
                g["requests"] = [r for r in g["requests"] if r["name"] != req_name]
                break
        logger.info("删除快捷请求: %s/%s", group_name, req_name)
        save_saved_requests(self.data)
        self._rebuild()

    def save_request(self, req_data, group_name, req_name, update_old_name=None):
        """保存或更新请求。update_old_name 非空时表示重命名更新"""
        # 找到或创建分组
        group = None
        for g in self.data["groups"]:
            if g["name"] == group_name:
                group = g
                break
        if group is None:
            group = {"name": group_name, "collapsed": False, "requests": []}
            self.data.setdefault("groups", []).append(group)

        req_data["name"] = req_name

        if update_old_name:
            # 更新已有请求（可能改名）
            for r in group["requests"]:
                if r["name"] == update_old_name:
                    group["requests"].remove(r)
                    break

        # 检查是否已存在同名
        group["requests"] = [r for r in group["requests"] if r["name"] != req_name]
        group["requests"].append(req_data)
        group["collapsed"] = False

        logger.info("保存快捷请求: %s/%s (method=%s url=%s)",
                    group_name, req_name, req_data.get("method", ""), req_data.get("url", ""))
        save_saved_requests(self.data)
        self._rebuild()
        self.apply_theme(self.theme)

    def _apply_group_theme(self, btn):
        theme = self.theme
        btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {theme.TEXT_SECONDARY};
                text-align: left;
                padding-left: 10px;
                border: none;
                border-radius: 6px;
                font-size: 11px;
            }}
            QPushButton:hover {{
                background: {theme.BG_HOVER};
                color: {theme.ACCENT};
            }}
        """)

    def apply_theme(self, theme):
        self.theme = theme
        bg = theme.BG_SECONDARY
        fg = theme.TEXT_PRIMARY
        surface = theme.BG_SURFACE
        border = theme.BORDER
        hover = theme.BG_HOVER

        self.setStyleSheet(f"""
            SavedRequestsPanel {{ background: {bg}; }}
            QScrollArea {{ border: none; background: {bg}; }}
            QWidget#container {{ background: {bg}; }}
        """)
        self.container.setStyleSheet(f"background: {bg};")

        # 标题区
        for lbl in self.findChildren(QLabel):
            lbl.setStyleSheet(f"color: {fg};")

        self.add_group_btn.setStyleSheet(f"""
            QPushButton {{
                background: {surface}; color: {fg}; border: 1px solid {border};
                border-radius: 8px; font-weight: bold; font-size: 13px;
            }}
            QPushButton:hover {{ background: {theme.ACCENT}; color: {theme.CHECKER}; border-color: {theme.ACCENT}; }}
        """)

        # 分组头
        for gw in self._group_widgets.values():
            self._apply_group_theme(gw["header"])

        # 请求按钮（悬停时左侧亮起强调色条）
        for btn in self.findChildren(QPushButton):
            if btn.property("req_data"):
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: transparent; color: {fg}; text-align: left;
                        padding-left: 12px; border: none; border-left: 3px solid transparent;
                        border-radius: 6px;
                    }}
                    QPushButton:hover {{
                        background: {hover};
                        border-left: 3px solid {theme.ACCENT};
                        color: {theme.ACCENT};
                    }}
                """)
