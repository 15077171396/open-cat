"""通用响应 Schema 与跨路由共享的请求参数约束"""

from typing import Generic, Optional, TypeVar
from pydantic import BaseModel

# 列表接口 `keyword` 的唯一上界（第四轮审查 P2-50⑧）。
# 原先三套口径：公开 5 处 100、admin 2 处 200、其余 11 处**完全没有上界** ——
# 无界者直入 `MATCH AGAINST` / `LIKE ESCAPE`，而 `services/search.py` 只转义通配符
# 不限长度，一条超长关键词就是一次昂贵的全表模式匹配。
# 取 200（现存最宽的一档）：公开侧放宽不会有调用方受影响，收紧侧一刀切掉无界输入。
KEYWORD_MAX_LENGTH = 200

T = TypeVar('T')


class ApiResponse(BaseModel, Generic[T]):
    """统一 API 响应格式"""
    code: int = 0
    message: str = "success"
    data: Optional[T] = None
