"""应用配置管理 - 基于 pydantic-settings 读取环境变量"""

from typing import List
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict

# 生产环境弱密钥检测
_DEFAULT_SECRET_KEY = "ai-cat-secret-key"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    APP_ENV: str = "development"
    APP_DEBUG: bool = True
    SECRET_KEY: str = _DEFAULT_SECRET_KEY

    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_EXPIRE_MINUTES: int = 120
    JWT_REFRESH_EXPIRE_DAYS: int = 7

    # 数据库 (MySQL)
    DB_HOST: str = "127.0.0.1"
    DB_PORT: int = 3306
    DB_NAME: str = "ai_cat"
    DB_USER: str = "root"
    DB_PASSWORD: str = "root"

    REDIS_HOST: str = "127.0.0.1"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = ""
    REDIS_DB: int = 0

    # AI 模型：三个 Provider 均走 OpenAI 兼容协议；ai_config.model 填「模型管理」里的 code，
    # 由 services/ai/factory 路由（旧供应商名继续兼容）；未配置 Key 时返回明确错误，不静默回退
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"
    DEEPSEEK_MODEL: str = "deepseek-chat"

    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_MODEL: str = "gpt-4o-mini"

    # 通义千问走 DashScope 的 OpenAI 兼容端点（不是原生 dashscope 协议）
    QWEN_API_KEY: str = ""
    QWEN_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    QWEN_MODEL: str = "qwen-plus"

    # 通义万相走 DashScope 原生异步任务 API，复用 Qwen 的 Key 时可只填 QWEN_API_KEY
    DASHSCOPE_IMAGE_API_KEY: str = ""
    DASHSCOPE_IMAGE_BASE_URL: str = "https://dashscope.aliyuncs.com"
    DASHSCOPE_IMAGE_MODEL: str = "wanx2.1-t2i-turbo"

    AI_WRITING_ENABLED: bool = False
    AI_WRITING_IMAGE_DAILY_LIMIT: int = 30

    # 编辑器图片搜索：密钥在后台加密保存，环境变量只作为未配置时的兜底
    IMAGE_SEARCH_ENABLED: bool = False
    IMAGE_SEARCH_PEXELS_KEY: str = ""
    IMAGE_SEARCH_PIXABAY_KEY: str = ""
    IMAGE_SEARCH_GIPHY_KEY: str = ""
    IMAGE_SEARCH_MEMECUB_KEY: str = ""
    IMAGE_SEARCH_PEXELS_ENABLED: bool = True
    IMAGE_SEARCH_PIXABAY_ENABLED: bool = True
    IMAGE_SEARCH_GIPHY_ENABLED: bool = True
    IMAGE_SEARCH_TENOR_ENABLED: bool = False
    IMAGE_SEARCH_BAIDU_ENABLED: bool = False
    IMAGE_SEARCH_SOGOU_ENABLED: bool = False
    IMAGE_SEARCH_DOUTUPK_ENABLED: bool = False
    IMAGE_SEARCH_EMOJI_ENABLED: bool = False
    IMAGE_SEARCH_PAGE_SIZE: int = 24
    IMAGE_SEARCH_ADMIN_HOURLY_LIMIT: int = 120
    IMAGE_SEARCH_DAILY_IMPORT_LIMIT: int = 200

    # 自定义供应商（OpenAI 兼容协议）：Moonshot/Kimi、智谱 GLM、SiliconFlow、Ollama、vLLM、
    # OneAPI 网关等都能接。**Base URL 与上游模型名按模型逐条填在「模型管理」里**，
    # 这里只保留同名环境变量兜底（自建网关不建库记录也能跑）；三者都为空时
    # 建模型会被拒（没有 base_url 无从拼 /chat/completions）。
    CUSTOM_API_KEY: str = ""
    CUSTOM_BASE_URL: str = ""
    CUSTOM_MODEL: str = ""

    DEFAULT_AI_MODEL: str = "deepseek"
    AI_DAILY_LIMIT_PER_USER: int = 50
    AI_TIMEOUT_SECONDS: int = 30
    AI_RETRY_COUNT: int = 2
    # AI 调用总耗时预算：重试叠加超时最坏约 93s，2 worker 下两个慢请求即可让全站排队，超预算即停止重试、快速失败
    AI_TOTAL_BUDGET_SECONDS: int = 45

    # 第三方抓取总耗时预算：多环节超时叠加可达分钟级，防止慢源站长期占住仅有的 2 个 worker
    THIRD_PARTY_FETCH_BUDGET_SECONDS: int = 60

    # LIKE 兜底计数的 MySQL 服务端语句超时（MAX_EXECUTION_TIME）：应用侧取消后查询仍占着连接，命中即返回 400 而非静默 0 结果
    SEARCH_FALLBACK_COUNT_TIMEOUT_MS: int = 1000

    # AI Provider base_url 主机白名单（逗号分隔，按点分后缀匹配）：base_url 直接决定 API Key 与 prompt 发往哪，
    # 仅校验 http(s) 前缀挡不住 http://deepseek.com@evil.com 形态；内网/环回/链路本地地址一律拒绝
    AI_BASE_URL_ALLOWED_HOSTS: str = "deepseek.com,openai.com,aliyuncs.com"

    # 全站 AI 成本熔断（单用户日限额防不住批量刷号）；默认 0 = 不限制，看板始终统计用量，建议日 5000 / 月 80000
    AI_GLOBAL_DAILY_LIMIT: int = 0
    AI_GLOBAL_MONTHLY_LIMIT: int = 0
    # 费用估算兜底单价（元/百万 tokens），默认 0 = 不估算金额，看板只展示调用次数与 tokens
    AI_PRICE_INPUT_PER_MTOK: float = 0.0
    AI_PRICE_OUTPUT_PER_MTOK: float = 0.0
    # 按模型单列单价 JSON（形如 {"deepseek": {"input": 1.0, "output": 2.0}}，键为 Provider 名），未列出回落兜底价；解析失败仅打日志不影响业务
    AI_MODEL_PRICES: str = ""

    # 邮件 (SMTP) - 用于注册验证码发送
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""  # 授权码 (非邮箱登录密码)
    SMTP_USE_TLS: bool = True  # 587 端口使用 STARTTLS; 465 端口应设为 False 并改用 SSL
    SMTP_FROM_EMAIL: str = ""  # 留空则使用 SMTP_USERNAME
    SMTP_SENDER_NAME: str = "AI学习猫"

    CORS_ORIGINS: str = "http://localhost:5173"

    # SEO
    # 对外站点根地址（sitemap 生成与百度推送 URL 拼接用）
    SITE_BASE_URL: str = "https://www.wdmao.cn"
    # 百度搜索资源平台「普通收录-API推送」token，留空则每日推送任务自动跳过
    BAIDU_PUSH_TOKEN: str = ""
    # Bing IndexNow 即时收录密钥（openssl rand -hex 16），留空则推送任务与密钥文件路由同时停用
    INDEXNOW_KEY: str = ""

    # 错误监控 (Sentry / GlitchTip)：留空 = 完全不启用；自建 GlitchTip 填其项目 DSN（协议兼容）
    SENTRY_DSN: str = ""
    # 上报环境名，留空则取 APP_ENV
    SENTRY_ENVIRONMENT: str = ""
    # 发布版本号（建议填 git short sha）
    APP_RELEASE: str = ""
    # 升级检查用的 GitHub 仓库（owner/repo）；留空 = 不检查最新 release（离线/自建镜像也能用）
    APP_UPDATE_REPO: str = ""
    # 性能追踪采样率：0 = 只上报错误（默认）；0.1 = 额外采样 10% 请求做链路追踪
    SENTRY_TRACES_SAMPLE_RATE: float = 0.0

    # 深度健康检查 (/api/v1/health/deep) 结果缓存秒数，吸收探活高频请求
    HEALTH_DEEP_CACHE_SECONDS: int = 5

    # ---- 运维状态（后台「系统状态」卡）----
    # 备份目录：容器内把宿主机 ./backups 只读挂载到此路径；看不到目录时该检查项
    # 只标「不可见」不告警（本地开发 / 未挂载属正常），避免用假告警消磨信任
    BACKUP_DIR: str = "/app/backups"
    # 备份超过该小时数判定「过旧」，与 scripts/watchdog.sh 的默认阈值保持一致
    BACKUP_STALE_HOURS: int = 48
    # 系统状态接口结果缓存秒数（聚合里含一次 MySQL 探测，避免刷新页面就打库）
    SYSTEM_STATUS_CACHE_SECONDS: int = 30
    # 告警 webhook 地址：后端**不发送**告警（由 scripts/watchdog.sh 在宿主机发送），
    # 这里只用来判断「通道到底配没配」，让后台能把「不会有人收到通知」显示出来
    WATCHDOG_WEBHOOK: str = ""

    # 仅当直连对端命中该 CIDR 列表才信任 X-Forwarded-For / X-Real-IP，防外网伪造代理头绕过限流；
    # 留空 = 默认信任回环 + RFC1918 私网（容器部署时 nginx 位于 docker 私网）
    TRUSTED_PROXIES: str = ""

    # 免费时长上限 (3小时 = 10800秒)
    FREE_TIME_LIMIT: int = 10800

    # 微信小程序登录；未配置时开发环境用 mock openid 联调，生产环境登录接口返回 503
    WECHAT_MINI_APPID: str = ""
    WECHAT_MINI_SECRET: str = ""
    # 小程序内嵌 H5 / 图片素材的 CDN 域名（选填）
    MINI_ASSET_BASE_URL: str = ""

    # 限流 (固定窗口计数器)
    RATE_LIMIT_GUEST: int = 120  # 游客每分钟请求上限
    RATE_LIMIT_USER: int = 300   # 登录用户每分钟请求上限
    RATE_LIMIT_WINDOW: int = 60  # 时间窗口 (秒)

    # 文件上传（全部走腾讯云 COS）
    AVATAR_MAX_SIZE: int = 2 * 1024 * 1024  # 2MB
    AVATAR_ALLOWED_TYPES: str = "image/jpeg,image/png,image/webp,image/gif"

    # UGC 内容审核
    # 总开关：关闭后所有敏感词检查跳过（排障用，生产不建议关）
    MODERATION_ENABLED: bool = True
    # 命中处置：reject=拒绝提交(默认) / mask=打码后入库 / review=入库待人工复核
    MODERATION_ACTION: str = "reject"
    # 追加敏感词（逗号分隔，在内置基线词库之上追加，调整词库无需发版）
    MODERATION_EXTRA_WORDS: str = ""

    # GitHub (管理后台扩展编辑拉取 README 用)
    # 可选 Personal Access Token，配置后可提升 API 匿名速率上限 (60次/小时)
    GITHUB_TOKEN: str = ""
    # 可选 GitHub 出站代理（仅对 GitHub 域名请求生效），适用于 SSH 反向隧道借用本地代理：
    # 本地 ssh -R 172.17.0.1:7890:127.0.0.1:7890 后填 http://172.17.0.1:7890（docker0 网桥地址，容器可达且不暴露公网）
    GITHUB_PROXY: str = ""
    # 可选 GitHub 加速镜像前缀（需以 / 结尾）；拉取顺序: jsDelivr → 官方 API → 官方 API(经代理) → 本镜像
    GITHUB_MIRROR_PREFIX: str = ""

    # 可选 HuggingFace Token（匿名拉公开资源已够用，配置后可拉受限 Gated 资源并提升速率限制）；
    # 拉取顺序: hf-mirror.com 镜像 → 官方直连 → 官方 API(经 GITHUB_PROXY)
    HF_TOKEN: str = ""

    # 可选国外文章平台出站代理，仅对国内无法直连的国外社区平台 (X/Twitter、Reddit、Medium、Hacker News) 生效，格式同 GITHUB_PROXY
    ARTICLE_PROXY: str = ""

    # ---- 模块开关 ----
    # 逗号分隔的模块 key；留空 = 按各模块默认（支付/积分/实名为默认关闭）。
    # 非空时**只启用列出的模块**（核心模块始终启用），用于给不同站点做裁剪。
    # 可选值见 app/module_registry.py 的 MODULES。
    ENABLED_MODULES: str = ""

    # ---- 文件存储驱动 ----
    # local = 落本机磁盘（开源默认，不需要任何云账号）；cos = 腾讯云对象存储（生产/CDN）
    STORAGE_DRIVER: str = "local"
    # local 驱动的存放根目录。容器内是挂卷的 /app/uploads；本地开发默认 ./uploads
    STORAGE_LOCAL_DIR: str = "./uploads"
    # local 驱动对外访问前缀，留空则用 SITE_BASE_URL；产出形如 {base}/uploads/{key}
    STORAGE_PUBLIC_BASE: str = ""

    # 对象存储 (腾讯云 COS)
    COS_SECRET_ID: str = ""
    COS_SECRET_KEY: str = ""
    COS_BUCKET: str = ""  # 形如 cat-1309118228
    COS_REGION: str = "ap-guangzhou"
    # 可选自定义访问域名 (CDN/自定义域名), 留空则使用默认 COS 域名
    COS_CDN_DOMAIN: str = ""
    # 资源文件大小上限 (20MB), 超出则拒绝并提示使用网盘
    COS_MAX_FILE_SIZE: int = 20 * 1024 * 1024
    # 编辑器图片大小上限 (5MB)
    COS_IMAGE_MAX_SIZE: int = 5 * 1024 * 1024
    # 编辑器 PNG 超该大小时服务端转 WebP 再入 COS (0 = 关闭转码)
    COS_IMAGE_WEBP_THRESHOLD: int = 500 * 1024
    # 服务端 WebP 转码质量 (与前端 imageMogr2 展示参数保持一致)
    COS_IMAGE_WEBP_QUALITY: int = 85

    # 中文全文检索（MySQL 8 ngram 全文索引）
    # 关闭开关：全文检索出问题时可一键退回改造前的 LIKE 行为，无需改代码发版
    SEARCH_FULLTEXT_ENABLED: bool = True
    # ngram 分词粒度，必须与 MySQL 服务端 ngram_token_size 一致；低于该长度的词查不到，自动退回 LIKE
    SEARCH_NGRAM_TOKEN_SIZE: int = 2
    # 超过该长度的关键词直接走 LIKE（查询串过长会被 MySQL errno 191 拒绝）
    SEARCH_MAX_KEYWORD_LEN: int = 64

    @property
    def cors_origins_list(self) -> List[str]:
        """CORS 白名单列表"""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def database_url(self) -> str:
        """异步 MySQL 连接 URL（凭据做 URL 编码，防密码特殊字符破坏解析）"""
        return (
            f"mysql+aiomysql://{quote_plus(self.DB_USER)}:{quote_plus(self.DB_PASSWORD)}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}?charset=utf8mb4"
        )

    @property
    def database_url_sync(self) -> str:
        """同步 MySQL 连接 URL (Alembic 用)"""
        return (
            f"mysql+pymysql://{quote_plus(self.DB_USER)}:{quote_plus(self.DB_PASSWORD)}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}?charset=utf8mb4"
        )

    @property
    def redis_url(self) -> str:
        """Redis 连接 URL"""
        if self.REDIS_PASSWORD:
            return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @property
    def is_dev(self) -> bool:
        return self.APP_ENV == "development"


settings = Settings()

# 生产环境使用默认弱密钥或过短密钥时直接拒绝启动（独立于 APP_ENV 检查，防环境变量误配时放行）
if not settings.is_dev and settings.SECRET_KEY == _DEFAULT_SECRET_KEY:
    raise RuntimeError("生产环境必须设置 SECRET_KEY 环境变量（不能使用默认值）")
if len(settings.SECRET_KEY) < 32 and not settings.is_dev:
    raise RuntimeError("SECRET_KEY 长度不足 32 字符，存在被暴力破解风险")
# CORS 校验：allow_credentials=True 时通配符 origin 会被浏览器拒绝
if "*" in settings.cors_origins_list:
    raise RuntimeError("CORS_ORIGINS 不能配置为 *（与 allow_credentials=True 不兼容，跨域带 cookie 请求将静默失败）")

# 生产环境凭据 fail-fast：弱 DB 密码或无鉴权 Redis 属部署漏配，与其带着可爆破的凭据运行不如启动即报错
if not settings.is_dev:
    if settings.DB_PASSWORD in ("", "root"):
        raise RuntimeError(
            "生产环境必须设置强 DB_PASSWORD（当前为空或默认值 root）。"
            "请在 .env 中设置 MYSQL_ROOT_PASSWORD/DB_PASSWORD 为强口令后重启"
        )
    if not settings.REDIS_PASSWORD:
        raise RuntimeError(
            "生产环境必须设置 REDIS_PASSWORD。Redis 存有**安全状态**"
            "（令牌黑名单 / 令牌版本号 / refresh_token / RBAC 权限缓存），"
            "无鉴权时任何能进入容器网络的进程都可读改它们（改权限缓存即可提权）。"
            "请在 .env 中设置 REDIS_PASSWORD 后重启"
        )
