"""AI Provider 注册表——唯一事实来源：Provider 名 / 工厂类 / 设置键前缀 / 环境变量前缀一次声明：

    @register_provider("deepseek", settings_prefix="ai_deepseek", env_prefix="DEEPSEEK")

模块发现用 pkgutil 自动扫描 *_provider.py（手写清单曾造成漏注册：只登记一份映射时会
静默回退到 DeepSeek 的配置构造实例，不报错）。漏注册由 tests/test_provider_registry.py 守卫。
刻意保持的边界：SETTING_DEFINITIONS 与 config.Settings 仍各自声明、由测试守卫一致；
get_provider_config 对未注册的名字抛错而非回退（no silent fallback）。
"""

import importlib
import pkgutil
from dataclasses import dataclass

from loguru import logger

# Provider 模块命名约定：`<name>_provider.py`（自动扫描据此发现）
_MODULE_SUFFIX = "_provider"


@dataclass(frozen=True)
class ProviderMeta:
    """Provider 的配置定位信息（注册时一并声明）"""

    settings_prefix: str  # 旧 system_settings 键前缀（迁移前数据 / 回滚兜底），如 ai_deepseek
    env_prefix: str  # 环境变量前缀，如 DEEPSEEK
    # 协议覆盖的输入输出形态；同一个名字只服务一种模态，
    # 文本模型不能误选图像 Provider，反之亦然
    modality: str = "text"


_provider_factories: dict[str, type] = {}
_provider_meta: dict[str, ProviderMeta] = {}
_loaded = False


def register_provider(
    name: str, *, settings_prefix: str, env_prefix: str, modality: str = "text"
):
    """Provider 注册装饰器（模块级 ``@register_provider(...)``）

    重复注册直接抛错：两份注册表并存的时代已经过去，同名注册只可能是
    复制粘贴事故，静默覆盖会让「哪个实现生效」无法解释。
    """

    def decorator(cls):
        if name in _provider_factories:
            raise RuntimeError(
                f"Provider 重复注册: {name}（{cls.__module__} 与 "
                f"{_provider_factories[name].__module__}）"
            )
        _provider_factories[name] = cls
        _provider_meta[name] = ProviderMeta(
            settings_prefix=settings_prefix, env_prefix=env_prefix, modality=modality
        )
        return cls

    return decorator


def ensure_providers_loaded() -> None:
    """导入全部 ``*_provider.py`` 模块（幂等；导入即执行注册装饰器）"""
    global _loaded
    if _loaded:
        return
    # 先置位再导入：Provider 模块内部若再次触发本函数不会递归
    _loaded = True

    import app.services.ai as pkg

    for info in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name):
        if info.name.endswith(_MODULE_SUFFIX):
            importlib.import_module(f"app.services.ai.{info.name}")
            logger.debug(f"AI Provider 模块已加载: {info.name}")


def registered_provider_names(modality: str | None = None) -> list[str]:
    """已注册的 Provider 名（排序后，供测试与后台选项使用）

    `modality=None` 返回全部；指定模态时只返回同一模态的实现。
    """
    ensure_providers_loaded()
    names = sorted(_provider_factories)
    if modality is None:
        return names
    return [name for name in names if _provider_meta[name].modality == modality]


def provider_constants() -> tuple[tuple[str, type, ProviderMeta], ...]:
    """(名称, 工厂类, 配置定位) 三元组，供守卫测试遍历"""
    ensure_providers_loaded()
    return tuple(
        (name, _provider_factories[name], _provider_meta[name])
        for name in sorted(_provider_factories)
    )


def provider_meta(name: str) -> ProviderMeta | None:
    """取 Provider 的配置定位；未注册返回 None（调用方决定报错还是回退）"""
    ensure_providers_loaded()
    return _provider_meta.get(name)


def provider_factory(name: str, modality: str = "text") -> type | None:
    """取 Provider 工厂类；未注册或模态不匹配返回 None"""
    ensure_providers_loaded()
    factory = _provider_factories.get(name)
    if factory is None:
        return None
    return factory if _provider_meta[name].modality == modality else None