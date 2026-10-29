"""AI 模型抽象基类"""

from abc import ABC, abstractmethod


class AIProvider(ABC):
    """AI 模型提供者抽象基类"""

    # 配置指纹：由工厂在创建实例时写入。工厂据此判断「配置是否变了」，
    # 变了就重建实例 —— 这是「后台改 Key 后无需重启进程即可生效」的关键。
    config_fingerprint: tuple = ()

    @abstractmethod
    async def chat(self, messages: list[dict], **kwargs) -> str:
        """
        对话接口
        :param messages: [{"role": "user", "content": "..."}]
        :return: AI 回复文本
        """
        ...

    async def stream_chat(self, messages: list[dict], **kwargs):
        """默认退化为一次性返回：新 Provider 不实现流式也能工作。"""
        yield await self.chat(messages, **kwargs)

    @abstractmethod
    async def close(self):
        """关闭连接"""
        ...
