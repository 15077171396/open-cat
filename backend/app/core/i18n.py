"""轻量 i18n：只覆盖**后端下发的展示文案**（侧边栏菜单、权限名）。

**为什么需要它**：这两处文案由后端下发，前端做 i18n 也翻不了侧边栏 ——
所以语言协商必须在后端完成。

**为什么不引入 gettext / babel**：只有两种语言、只有两处文案来源（`admin_menu` 与
`rbac_catalog`），一个字典加一个 header 解析就够。引入完整框架会让「加一条菜单」
从「写一行」变成「写一行 + 跑一遍提取脚本 + 合并 po 文件」。

**默认中文**：`Accept-Language` 缺失、认不出、或只有未支持的语言时一律中文 ——
保证现有中文站点的行为零变化，也保证「没翻译」永远不会变成空白菜单。

⚠️ 当前只翻译菜单与权限名。页面内的业务文案仍是中文（见 README 已知限制），
前端因此**显式声明** `Accept-Language: zh-CN`（见 api/client.ts），
避免「英文浏览器 + 中文页面」的混搭 —— 那种半截语言比全中文更难用。
"""

from fastapi import Request

DEFAULT_LOCALE = "zh"
SUPPORTED_LOCALES = ("zh", "en")

# 主语言标签归一：zh-CN / zh-Hans / zh_CN → zh
_PRIMARY = {"zh": "zh", "en": "en"}


def resolve_locale(accept_language: str | None) -> str:
    """从 `Accept-Language` 解析出受支持的语言。

    按 q 值从高到低取第一个受支持的主标签；一个都不支持则回默认。
    只做「主标签」匹配：`en-GB` 与 `en-US` 共用一份英文文案，不值得为此拆两份。
    """
    if not accept_language:
        return DEFAULT_LOCALE

    parsed: list[tuple[float, str]] = []
    for chunk in accept_language.split(","):
        part = chunk.strip()
        if not part:
            continue
        tag, _, params = part.partition(";")
        quality = 1.0
        for param in params.split(";"):
            key, _, value = param.strip().partition("=")
            if key.strip().lower() == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 1.0
        primary = tag.strip().lower().replace("_", "-").split("-")[0]
        parsed.append((quality, primary))

    # q 相同时保持原顺序（Python 的 sort 稳定）
    for _quality, primary in sorted(parsed, key=lambda item: -item[0]):
        if primary in _PRIMARY:
            return _PRIMARY[primary]
    return DEFAULT_LOCALE


def request_locale(request: Request) -> str:
    """FastAPI 依赖：从请求头取语言（路由层用 `Depends(request_locale)`）"""
    return resolve_locale(request.headers.get("accept-language"))


def pick(locale: str, zh: str, en: str | None) -> str:
    """按语言选文案；英文缺失时回退中文（绝不返回空串）"""
    if locale == "en" and en:
        return en
    return zh
