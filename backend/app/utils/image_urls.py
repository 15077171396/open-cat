"""用户提交的图片 URL 校验（评论配图 / 反馈配图共用）。

为什么必须校验域名：`images` 由客户端提交，不校验等于把站内任意 UGC 入口开放成外部图床 ——
攻击者可以填任意第三方地址，让站点在别人页面里加载/代理不属于自己的资源。

抽成公共函数的原因：评论与反馈是同一类「上传后把 URL 随正文一起提交」的链路，
校验规则（数量上限 / 必须本站 COS·CDN 前缀 / 去重）必须完全一致，
各写一份迟早会漂移成「评论能过、反馈被拒」这种说不清的行为差异。
"""

from app.core.exceptions import AppException
from app.services import cos_service


def validate_uploaded_image_urls(images, *, max_count: int) -> list[str]:
    """校验并清洗图片 URL 列表；返回去重后的结果（空输入返回空列表）。

    错误码统一 400 + 可直接展示的中文文案：这类失败都来自客户端提交了不合规的值，
    不是服务端故障，前端 toast 会原样显示 message。
    """
    if not images:
        return []
    if not isinstance(images, list):
        raise AppException(code=400, message="图片参数格式不正确")

    prefixes = cos_service.public_url_prefixes()
    cleaned: list[str] = []
    for url in images:
        if not isinstance(url, str):
            raise AppException(code=400, message="图片参数格式不正确")
        url = url.strip()
        if not url:
            continue
        if not url.startswith(prefixes):
            raise AppException(code=400, message="图片地址无效，请重新上传")
        if url not in cleaned:  # 同一张图重复提交没有意义，去重而非报错
            cleaned.append(url)

    if len(cleaned) > max_count:
        raise AppException(code=400, message=f"最多只能上传 {max_count} 张图片")
    return cleaned
