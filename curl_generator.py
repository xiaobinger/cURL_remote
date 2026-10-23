"""
Curl Generator - 现代化 cURL 命令生成工具
支持签名密钥配置、主题切换、签名别名管理、快捷请求、本地/SSH 执行

模块结构:
    curlgen/
        config.py         路径常量与 JSON 持久化
        logging_setup.py  按天滚动日志
        signing.py        签名计算
        ssh_executor.py   本地/SSH 执行器（流式输出、超时、可停止）
        http_executor.py  本地直发 HTTP 执行器
        ai_client.py      AI 脚本生成（OpenAI 兼容协议）
        code_gen.py       多语言调用代码生成
        web_ui.py         主窗口（星轨控制台，Web 引擎界面）
        curl_builder.py   cURL 命令构建与结构化请求解析

运行依赖：PyQt5 + PyQtWebEngine（界面由 Web 引擎渲染）
"""
import sys

from curlgen.logging_setup import setup_logging


def main():
    setup_logging()

    try:
        import PyQt5.QtWebEngineWidgets  # noqa: F401
    except ImportError:
        print("缺少 PyQtWebEngine，无法启动界面：pip install PyQtWebEngine")
        sys.exit(1)

    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QApplication

    # 允许运行时加载 QtWebEngine
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    from curlgen.web_ui import WebCurlGenerator
    window = WebCurlGenerator()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
