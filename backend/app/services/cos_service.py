"""腾讯云 COS 对象存储服务

负责将上传的二进制内容写入 COS 并返回可访问的 URL。
SDK (cos-python-sdk-v5) 为同步实现，所有 IO 通过 `app.core.executors.run_heavy`
移出事件循环，避免阻塞 FastAPI 的协程。走专用池而不是默认的 `asyncio.to_thread`：
后者与口令哈希共用一个容量很小的池（2 核机器上只有 6 条线程），一次批量图片转存
就能把 `/auth/login` 挤到无声排队（第四轮全量审查 P1-67）。
"""

from __future__ import annotations

import threading
import uuid
from typing import Optional

from loguru import logger

from app.config import settings
from app.core.executors import run_heavy


# COS 默认访问域名模板: https://<bucket>.cos.<region>.myqcloud.com
_DEFAULT_DOMAIN_FMT = "https://{bucket}.cos.{region}.myqcloud.com"

# 主线程的客户端缓存（事件循环 / 健康检查 / 单测走这里）
_client = None
# 工作线程的客户端缓存：cos-python-sdk-v5 的 CosS3Client **不是线程安全的**
_tls = threading.local()


class CosError(Exception):
    """COS 上传失败"""

def is_configured() -> bool:
    """COS 凭证是否已配置（供可选功能降级判断，如 README 图片转存）"""
    return bool(settings.COS_SECRET_ID and settings.COS_SECRET_KEY and settings.COS_BUCKET)


def _create_client():
    """构造一个 COS 客户端；配置缺失 / SDK 缺失 / 构造报错统一抛 CosError"""
    if not settings.COS_SECRET_ID or not settings.COS_SECRET_KEY or not settings.COS_BUCKET:
        raise CosError("COS 未配置: 请在 .env 中设置 COS_SECRET_ID / COS_SECRET_KEY / COS_BUCKET")

    # 延迟导入，避免未安装依赖时影响应用启动
    try:
        from qcloud_cos import CosConfig, CosS3Client

        config = CosConfig(
            Region=settings.COS_REGION,
            SecretId=settings.COS_SECRET_ID,
            SecretKey=settings.COS_SECRET_KEY,
            Scheme="https",
        )
        return CosS3Client(config)
    except Exception as exc:  # noqa: BLE001
        # 必须收进 CosError：调用方只映射这一种异常，穿透出去就是兜底 500「服务器内部错误」，
        # 日志里既看不出是 COS 用不了，也分不出缺依赖与配置错——所以带上底层异常类型名
        raise CosError(f"COS 客户端初始化失败（{type(exc).__name__}: {exc}）") from exc


def _get_client():
    """惰性初始化 COS 客户端（**每个线程一个实例**）。

    ⚠️ 为什么不能全局共享一个客户端（2026-10-19 实测）：`cos-python-sdk-v5` 的
    `CosS3Client` 不是线程安全的。README / 文章配图转存是「5 路并发」跑在
    `run_heavy` 线程池里的，多线程共用同一实例时，`put_object` 请求会被互相
    篡改 —— 现象极隐蔽：**上传不报错、对象也存在、大小内容都对，但
    `ACL: public-read` 头偶发丢失**，对象变成私有，浏览器匿名 GET 拿到
    `403 AccessDenied`，用户端表现为「正文配图随机裂图」（图越大、并发越高越容易命中）。
    实测对照：4 路并发共享客户端 → 3/4 不可读；每线程独立客户端 → 4/4 可读。
    """
    global _client
    is_main = threading.current_thread() is threading.main_thread()
    cached = _client if is_main else getattr(_tls, "client", None)
    if cached is not None:
        return cached

    client = _create_client()
    if is_main:
        _client = client
    else:
        _tls.client = client
    logger.info(
        f"COS 客户端已初始化（线程 {threading.current_thread().name}）: "
        f"bucket={settings.COS_BUCKET}, region={settings.COS_REGION}"
    )
    return client


def _build_public_url(key: str) -> str:
    """根据 object key 拼拼可公开访问的 URL。

    优先使用自定义 CDN 域名，否则回退到默认 COS 域名。
    """
    if settings.COS_CDN_DOMAIN:
        domain = settings.COS_CDN_DOMAIN.rstrip("/")
        return f"{domain}/{key}"
    domain = _DEFAULT_DOMAIN_FMT.format(bucket=settings.COS_BUCKET, region=settings.COS_REGION)
    return f"{domain}/{key}"


def public_url_prefixes() -> tuple[str, ...]:
    """本服务可能产出的公开 URL 前缀，用于校验「用户提交的图片确实来自本站上传」。

    ⚠️ 同时给出 CDN 与默认 COS 域名两种：
    ``COS_CDN_DOMAIN`` 是后来才支持的，历史图片仍可能指向默认 COS 域名，
    只认 CDN 会把老图判为非法（表现为「明明是自己上传的图却被拒绝」）。
    """
    prefixes = []
    if settings.COS_CDN_DOMAIN:
        prefixes.append(settings.COS_CDN_DOMAIN.rstrip("/") + "/")
    prefixes.append(
        _DEFAULT_DOMAIN_FMT.format(bucket=settings.COS_BUCKET, region=settings.COS_REGION) + "/"
    )
    return tuple(prefixes)


def _generate_key(folder: str, ext: str) -> str:
    """生成按日期分目录、UUID 命名的对象 key。

    例: images/2026/08/01/a1b2c3d4e5f6.jpg

    日期取 UTC (ARCH-006)，避免服务器时区变化导致同一天文件落入不同目录。
    """
    from app.utils.time import utcnow

    date_path = utcnow().strftime("%Y/%m/%d")
    filename = f"{uuid.uuid4().hex[:16]}{ext}"
    return f"{folder}/{date_path}/{filename}"


def _upload_sync(key: str, data: bytes, content_type: str) -> str:
    """同步上传字节流到 COS，返回公开访问 URL。"""
    client = _get_client()
    try:
        client.put_object(
            Bucket=settings.COS_BUCKET,
            Body=data,
            Key=key,
            ContentType=content_type,
            EnableMD5=True,
            ACL="public-read",
        )
    except Exception as exc:  # noqa: BLE001 - SDK 抛多种异常，统一转换为 CosError
        raise CosError(f"COS 上传失败: {exc}") from exc

    _ensure_public_read(client, key)
    url = _build_public_url(key)
    logger.info(f"COS 上传成功: key={key}, size={len(data)} bytes, url={url}")
    return url


def _ensure_public_read(client, key: str) -> None:
    """上传后再显式设一次对象 ACL=public-read（幂等）

    ⚠️ 为什么 put_object 里的 `ACL="public-read"` 还不够（2026-10-19 实测）：
    只靠该参数时，**刚上传的那几分钟匿名 GET 是 200，之后稳定变成
    `403 AccessDenied`**（对象其实没丢，鉴权 GET 正常，`get_object_acl`
    也显示 AllUsers 有 READ）。同一份数据改用 `put_object_acl` 显式设置后，
    20 分钟以上仍保持 200。两种调用最终都会命中同一次 ACL 写入，但
    `put_object` 的 ACL 头在本桶上不持久 —— 与其去猜服务端实现，
    不如上传后补一次幂等的 `put_object_acl`。

    本服务返回的 URL 是给浏览器 `<img>` 直接访问的，一旦 403 就是
    用户看到的「正文配图随机裂图」。这一步失败**不阻断上传**（内容已入库，
    只是可能不可匿名访问），但必须打日志，否则又是一次静默劣化。
    """
    try:
        client.put_object_acl(
            Bucket=settings.COS_BUCKET, Key=key, ACL="public-read"
        )
    except Exception as exc:  # noqa: BLE001 - ACL 设置失败不影响对象已写入
        logger.error(
            f"COS 对象 ACL 设为公共读失败（该图可能匿名 403）: key={key} "
            f"({type(exc).__name__}: {exc})"
        )


async def upload_bytes(
    data: bytes,
    *,
    folder: str,
    ext: str,
    content_type: str,
) -> str:
    """上传字节流到 COS。

    :param data: 文件二进制内容
    :param folder: COS 目录前缀 (如 images / files / avatars)
    :param ext: 文件扩展名 (含点, 如 .jpg)
    :param content_type: MIME 类型
    :return: 可访问的公开 URL
    """
    key = _generate_key(folder, ext)
    return await run_heavy(_upload_sync, key, data, content_type)


def _download_sync(key: str, max_size: int) -> tuple[bytes, str]:
    """同步按 key 读取对象（服务端凭证），返回 (内容, Content-Type)"""
    client = _get_client()
    try:
        resp = client.get_object(Bucket=settings.COS_BUCKET, Key=key)
        declared = resp.get("Content-Length")
        if declared and str(declared).isdigit() and int(declared) > max_size:
            raise CosError(f"对象超过中转上限（{declared} bytes）: {key}")
        data = resp["Body"].get_raw_stream().read()
    except CosError:
        raise
    except Exception as exc:  # noqa: BLE001 - SDK 异常类型多，统一收口
        raise CosError(f"COS 读取失败: {exc}") from exc
    if len(data) > max_size:
        raise CosError(f"对象超过中转上限（{len(data)} bytes）: {key}")
    content_type = (resp.get("Content-Type") or "").split(";")[0].strip().lower()
    return data, content_type


async def download_object_bytes(key: str, max_size: int) -> tuple[bytes, str]:
    """按对象 key 读取内容（**鉴权**），返回 (内容, Content-Type)。

    用途：图片代理的兜底通道。存储桶侧的匿名访问可能被内容审核等策略拦成
    `403 AccessDenied`（对象本身完好、鉴权读取正常），此时由服务端凭证读出再回传，
    前台就不会裂图。读取量受 `max_size` 约束，避免把大文件整包读进内存。
    """
    return await run_heavy(_download_sync, key, max_size)


def get_file_ext(filename: Optional[str], content_type: str) -> str:
    """从文件名或 content_type 推断扩展名 (含点)。

    实现已移到 `app/services/storage/base.py`（各存储驱动共用同一份白名单）；
    这里保留同名函数是为了不破坏既有调用点。
    """
    from app.services.storage.base import get_file_ext as _impl

    return _impl(filename, content_type)
