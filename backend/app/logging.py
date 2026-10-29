"""日志配置 - loguru 控制台与文件 sink（按大小滚动、保留 14 天）"""

import os
import sys
from loguru import logger

from app.config import settings
from app.core.request_context import get_request_id

LOG_DIR = os.environ.get("LOG_DIR", "logs")
LOG_FILE = os.path.join(LOG_DIR, "app.log")

ROTATION = os.environ.get("LOG_ROTATION", "10 MB")
# loguru retention 支持整数(保留文件数)或时间字符串(如 "14 days"); 默认保留 14 天
RETENTION = os.environ.get("LOG_RETENTION", "14 days")

LOG_LEVEL = os.environ.get("LOG_LEVEL", "DEBUG" if settings.is_dev else "INFO")


def _inject_request_id(record) -> bool:
    """在 filter 里统一注入请求关联 ID：任何一条日志都自动带上不会漏绑；
    无请求上下文填 -，setdefault 让调用方显式 bind 时以调用方为准。"""
    record["extra"].setdefault("request_id", get_request_id() or "-")
    return True


def setup_logging():
    """配置 loguru sink: 控制台 + 文件（应在应用启动早期调用一次，重复调用先移除旧 sink）"""
    logger.remove()
    # 控制台 sink (彩色, 简洁格式)
    logger.add(
        sys.stderr,
        level=LOG_LEVEL,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{extra[request_id]}</cyan> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
        filter=_inject_request_id,
        backtrace=settings.is_dev,
        diagnose=settings.is_dev,
    )

    # 文件 sink (JSON 行, 便于采集; 滚动 + 保留)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        logger.add(
            LOG_FILE,
            level=LOG_LEVEL,
            rotation=ROTATION,
            retention=RETENTION,
            compression="zip",
            enqueue=True,  # 进程安全, 避免多 worker 写冲突
            serialize=True,  # 结构化 JSON 输出, 含 extra 字段 (logger.bind 的内容)
            filter=_inject_request_id,
            backtrace=True,
            # 文件 sink 的 diagnose 恒为 False：diagnose 会把异常帧局部变量渲染进落盘日志
            # 且保留 14 天，明文口令一旦进文件就很难收回；控制台仍跟随 is_dev（不落盘）
            diagnose=False,
            delay=True,
        )
    except OSError:
        # 日志目录不可写时仅退化为控制台输出, 不阻塞启动
        logger.warning(f"无法初始化文件日志 sink: {LOG_FILE} (仅控制台输出)")


# 模块导入时即完成基础配置, 保证早期日志也可被文件 sink 捕获
setup_logging()
