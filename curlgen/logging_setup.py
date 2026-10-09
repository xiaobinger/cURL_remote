"""按天分割的日志系统"""
import os
import logging
from logging.handlers import TimedRotatingFileHandler

from .config import LOG_DIR

_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level=logging.INFO):
    """初始化日志：控制台 + 按天滚动文件（logs/curlgen_YYYY-MM-DD.log，保留 30 天）"""
    os.makedirs(LOG_DIR, exist_ok=True)

    root = logging.getLogger()
    if root.handlers:  # 已初始化过
        return root

    root.setLevel(level)
    formatter = logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT)

    # 文件：每天 0 点滚动，文件名带日期后缀
    file_handler = TimedRotatingFileHandler(
        os.path.join(LOG_DIR, "curlgen.log"),
        when="midnight",
        backupCount=30,
        encoding="utf-8",
    )
    file_handler.suffix = "%Y-%m-%d"
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    root.addHandler(file_handler)

    # 控制台
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.setLevel(level)
    root.addHandler(console)

    # paramiko 默认太啰嗦
    logging.getLogger("paramiko").setLevel(logging.WARNING)

    logger = logging.getLogger("curlgen")
    logger.info("日志系统初始化完成，日志目录: %s", LOG_DIR)
    return root
