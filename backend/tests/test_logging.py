"""日志配置单测 (ARCH-020)。

验证文件 sink 能正确创建日志文件并按 JSON 行写入。
"""

import os
import tempfile
import unittest

from loguru import logger

from app import logging as app_logging


class LoggingSetupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="ai-cat-logs-")
        self._orig_log_dir = app_logging.LOG_DIR
        self._orig_log_file = app_logging.LOG_FILE
        # LOG_DIR/LOG_FILE 在模块加载时已读取, 此处直接改属性后再调用 setup
        app_logging.LOG_DIR = self._tmpdir
        app_logging.LOG_FILE = os.path.join(self._tmpdir, "app.log")

    async def asyncTearDown(self):
        app_logging.LOG_DIR = self._orig_log_dir
        app_logging.LOG_FILE = self._orig_log_file
        # 恢复默认 sink, 避免污染其它测试
        app_logging.setup_logging()

    async def test_file_sink_writes_log(self):
        app_logging.setup_logging()
        logger.bind(event="test_event").info("a structured test message")
        # enqueue=True 需等待异步写入完成
        await logger.complete()
        self.assertTrue(os.path.isfile(app_logging.LOG_FILE))
        with open(app_logging.LOG_FILE, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("a structured test message", content)
        # serialize=True 输出 JSON, 应包含 record/extra 字段
        self.assertIn("test_event", content)

    def test_setup_logging_is_idempotent(self):
        # 重复调用不应抛错
        app_logging.setup_logging()
        app_logging.setup_logging()


if __name__ == "__main__":
    unittest.main()
