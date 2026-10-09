"""
Curl Generator - 现代化 cURL 命令生成工具
支持签名密钥配置、亮暗主题切换、签名别名管理、快捷请求、本地/SSH 执行

模块结构:
    curlgen/
        theme.py          主题配色
        config.py         路径常量与 JSON 持久化
        logging_setup.py  按天滚动日志
        signing.py        签名计算
        widgets.py        通用 UI 组件（高亮、下拉、键值编辑器）
        ssh_executor.py   本地/SSH 执行器（流式输出、超时、可停止）
        dialogs.py        对话框（环境配置/SSH 配置/保存请求）
        saved_panel.py    左侧快捷请求面板
        main_window.py    旧版主窗口（PyQt 原生界面）
        web_ui.py         新版主窗口（星轨控制台，Web 引擎界面）
        curl_builder.py   cURL 命令构建（新旧 UI 共用）

启动参数:
    --legacy   强制使用旧版 PyQt 界面（新版需要 PyQtWebEngine）
"""
import sys

from curlgen.logging_setup import setup_logging


def main():
    setup_logging()

    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QApplication

    # 允许运行时按需加载 QtWebEngine（新旧界面切换）
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    force_legacy = "--legacy" in sys.argv
    use_web = False
    if not force_legacy:
        try:
            import PyQt5.QtWebEngineWidgets  # noqa: F401
            use_web = True
        except ImportError:
            print("未安装 PyQtWebEngine，使用旧版界面（pip install PyQtWebEngine 可启用新版）")

    if use_web:
        from curlgen.web_ui import WebCurlGenerator
        window = WebCurlGenerator()
    else:
        from curlgen.main_window import CurlGenerator
        window = CurlGenerator()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
