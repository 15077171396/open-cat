"""公开列表/统计接口的响应形状一致性（第三轮增量审查 P1-48 ①②）。

为什么值得钉：同一个项目里，`/plugins/featured` 返回 `{"items": [...]}`，而
`/resources/hot` 与 `/resources/latest` 原先返回**裸数组**；`/resources/stats` 用
`type` 键、`/nodes/stats` 却用 `key`。前端（或小程序）每次对接都要先猜形状，
而这两种不一致**不会报错** —— 只是让每个消费方多写一份兼容代码。

已核查这些端点当前**没有消费方**（Web 端不调用；小程序 `utils/api.js` 定义了
helper 但从未被页面调用），所以统一形状是安全的。本文件防止它们再漂回去。
"""

import unittest
from contextlib import asynccontextmanager
from unittest import mock

import httpx

from tests.conftest import FakeRedis
from tests.fake_db import MemorySession

if __name__ == "__main__":
    unittest.main()
