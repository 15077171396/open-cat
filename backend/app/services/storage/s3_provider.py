"""S3 兼容对象存储驱动（AWS S3 / MinIO / 阿里云 OSS / 华为云 OBS 的 S3 兼容端点）。

为什么一个驱动能覆盖四家：OSS / OBS / MinIO 都提供 S3 兼容 API，换端点即换云，
签名、寻址、重试全部由 boto3 处理 —— 这正是本驱动的价值：适配逻辑只有一份。

实现口径与 cos_service 对齐：
- SDK 是同步的，所有 IO 经 `app.core.executors.run_heavy` 移出事件循环（专用线程池，
  不与口令哈希共用小池，见 cos_service 模块注释的 P1-67 教训）；
- 上传后**尝试**补一次对象 ACL=public-read，失败只打日志不阻断（内容已写入）——
  与 COS 的 `_ensure_public_read` 同款兜底。⚠️ 现代桶常把 ACL 整体禁用
  （Bucket owner enforced），这步必然报错属预期；此时匿名可读要靠**桶策略**
  （Allow s3:GetObject on *），配置完用部署体检或上传一张图验证；
- 公开 URL 优先走 `STORAGE_S3_PUBLIC_BASE`（CDN），否则按寻址风格拼默认域名。

异常口径：只捕 SDK 的 `ClientError`（服务端 4xx/5xx，如 403/404/签名错）、
`BotoCoreError`（SDK 侧错误，二者**不互为父子**，需并列）与 `OSError`（裸 socket
失败）—— 配置写错（KeyError/AttributeError 之类）让它直接 500 暴露，不伪装成
「存储不可用」。

与 COS 客户端缓存的差异：boto3 client **官方文档保证线程安全**（cos SDK 实测不是，
见 cos_service._get_client 的踩坑记录），因此全进程共享一个实例即可；但仍按
「配置指纹」缓存 —— 后台改了密钥（runtime_config 热覆盖 settings）自动重建，
不必重启进程。
"""

from __future__ import annotations

from loguru import logger

from app.config import settings
from app.core.executors import run_heavy

from .base import StorageError, generate_key

# 合法的寻址风格（透传给 botocore 的 s3.addressing_style）
_ADDRESSING_STYLES = ("auto", "path", "virtual")

# (配置指纹, 客户端) —— 指纹变了就重建，避免运行期改密钥后继续用旧客户端
_client_cache: tuple[tuple, object] | None = None

# AWS 默认域名模板（未配 endpoint 时按 region 拼虚拟主机风格）
_AWS_HOST_FMT = "s3.{region}.amazonaws.com"


def _cfg(key: str) -> str:
    return (getattr(settings, key) or "").strip()


def _addressing_style() -> str:
    style = (_cfg("STORAGE_S3_ADDRESSING") or "auto").lower()
    if style not in _ADDRESSING_STYLES:
        raise StorageError(
            f"STORAGE_S3_ADDRESSING 只支持 {' / '.join(_ADDRESSING_STYLES)}，当前: {style!r}"
        )
    return style


def is_configured() -> bool:
    """三元组齐了才算配置（endpoint 留空 = AWS 官方 S3）"""
    return bool(_cfg("STORAGE_S3_ACCESS_KEY") and _cfg("STORAGE_S3_SECRET_KEY") and _cfg("STORAGE_S3_BUCKET"))


def _create_client():
    """构造 boto3 S3 客户端；配置缺失 / 依赖缺失 / 构造报错统一抛 StorageError。"""
    if not is_configured():
        raise StorageError(
            "S3 未配置: 请在 .env 或后台「系统设置 → 对象存储」中设置 "
            "STORAGE_S3_ACCESS_KEY / STORAGE_S3_SECRET_KEY / STORAGE_S3_BUCKET"
        )
    try:
        import boto3
        from botocore.config import Config as BotoConfig
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError as exc:
        raise StorageError(
            f"S3 驱动缺少依赖 boto3（pip install -r requirements.txt）: {exc}"
        ) from exc

    style = _addressing_style()
    try:
        return boto3.client(
            "s3",
            # 留空 = AWS 官方端点；OSS/OBS/MinIO 填各自的 S3 兼容端点
            endpoint_url=_cfg("STORAGE_S3_ENDPOINT") or None,
            # botocore 必须有 region；自建端点通常不校验，缺省回退 us-east-1
            region_name=_cfg("STORAGE_S3_REGION") or "us-east-1",
            aws_access_key_id=_cfg("STORAGE_S3_ACCESS_KEY"),
            aws_secret_access_key=_cfg("STORAGE_S3_SECRET_KEY"),
            config=BotoConfig(
                signature_version="s3v4",
                s3={"addressing_style": style},
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=10,
                read_timeout=60,
            ),
        )
    except (BotoCoreError, ClientError, ValueError) as exc:
        raise StorageError(f"S3 客户端初始化失败（{type(exc).__name__}: {exc}）") from exc


def _get_client():
    """按配置指纹惰性初始化客户端（指纹变化自动重建，见模块注释）。"""
    global _client_cache
    fingerprint = tuple(
        _cfg(k)
        for k in (
            "STORAGE_S3_ENDPOINT", "STORAGE_S3_REGION",
            "STORAGE_S3_ACCESS_KEY", "STORAGE_S3_SECRET_KEY",
            "STORAGE_S3_BUCKET", "STORAGE_S3_ADDRESSING",
        )
    )
    if _client_cache is not None and _client_cache[0] == fingerprint:
        return _client_cache[1]
    client = _create_client()
    _client_cache = (fingerprint, client)
    return client


def _reset_client_cache() -> None:
    """仅供测试：清掉客户端缓存，让下一次调用按当前配置重建。"""
    global _client_cache
    _client_cache = None


def _default_public_base(style: str) -> str:
    """按寻址风格拼默认公开域名（未配 CDN 时）。"""
    bucket = _cfg("STORAGE_S3_BUCKET")
    endpoint = _cfg("STORAGE_S3_ENDPOINT")
    if endpoint:
        host = endpoint.split("://", 1)[-1].rstrip("/")
    else:
        host = _AWS_HOST_FMT.format(region=_cfg("STORAGE_S3_REGION") or "us-east-1")
    if style == "path":
        return f"https://{host}/{bucket}"
    return f"https://{bucket}.{host}"


def _build_public_url(key: str) -> str:
    style = _addressing_style()
    base = _cfg("STORAGE_S3_PUBLIC_BASE") or _default_public_base(style)
    return f"{base.rstrip('/')}/{key}"


def public_url_prefixes() -> tuple[str, ...]:
    """本驱动可能产出的公开 URL 前缀（校验用户提交的图片是否来自本站上传）。"""
    try:
        style = _addressing_style()
    except StorageError:
        return ()
    base = _cfg("STORAGE_S3_PUBLIC_BASE") or _default_public_base(style)
    return (base.rstrip("/") + "/",)


def _upload_sync(key: str, data: bytes, content_type: str) -> str:
    from botocore.exceptions import BotoCoreError, ClientError

    client = _get_client()
    bucket = _cfg("STORAGE_S3_BUCKET")
    try:
        client.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)
    except (ClientError, BotoCoreError, OSError) as exc:
        raise StorageError(f"S3 上传失败: {exc}") from exc

    _ensure_public_read(client, bucket, key)
    url = _build_public_url(key)
    logger.info(f"S3 上传成功: key={key}, size={len(data)} bytes, url={url}")
    return url


def _ensure_public_read(client, bucket: str, key: str) -> None:
    """上传后尝试补一次对象 ACL=public-read（幂等，失败不阻断 —— 见模块注释）。"""
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        client.put_object_acl(Bucket=bucket, Key=key, ACL="public-read")
    except (ClientError, BotoCoreError, OSError) as exc:
        logger.warning(
            f"S3 对象 ACL 设为公共读失败（ACL 禁用的桶请改配桶策略，否则匿名 403）: "
            f"key={key} ({type(exc).__name__}: {exc})"
        )


def _download_sync(key: str, max_size: int) -> tuple[bytes, str]:
    from botocore.exceptions import BotoCoreError, ClientError

    client = _get_client()
    bucket = _cfg("STORAGE_S3_BUCKET")
    try:
        resp = client.get_object(Bucket=bucket, Key=key)
        declared = resp.get("ContentLength")
        if declared and declared > max_size:
            raise StorageError(f"对象超过中转上限（{declared} bytes）: {key}")
        data = resp["Body"].read()
    except StorageError:
        raise
    except (ClientError, BotoCoreError, OSError) as exc:
        raise StorageError(f"S3 读取失败: {exc}") from exc
    if len(data) > max_size:
        raise StorageError(f"对象超过中转上限（{len(data)} bytes）: {key}")
    content_type = (resp.get("ContentType") or "").split(";")[0].strip().lower()
    return data, content_type


async def upload_bytes(*, data: bytes, folder: str, ext: str, content_type: str) -> str:
    """上传字节流，返回公开访问 URL（key 规则与 local/cos 驱动一致）。"""
    key = generate_key(folder, ext)
    return await run_heavy(_upload_sync, key, data, content_type)


async def download_object_bytes(key: str, max_size: int) -> tuple[bytes, str]:
    """按对象 key 鉴权读取（图片代理兜底通道），返回 (内容, Content-Type)。"""
    return await run_heavy(_download_sync, key, max_size)
