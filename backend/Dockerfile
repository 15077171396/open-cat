# 后端 Dockerfile
FROM python:3.11-slim

WORKDIR /app

# 切换 apt 为清华源 (加速系统依赖安装, python:3.11-slim 基于 Debian 12)
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources || true

# 安装系统依赖
# fonts-noto-cjk：邀请海报用 Pillow 画中文，slim 镜像默认没有中文字体
# （缺字体会把中文渲染成方块，且不会报错——只能靠肉眼看图发现）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖 (清华源加速)
COPY requirements.txt .
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

# 复制项目代码
COPY . .

# 非 root 运行（第二轮增量审查 2026-10-10 P0-3）：
# 容器逃逸/漏洞利用的起点不该是 root。应用只需写 logs/，其余保持只读属主。
RUN useradd -r -u 10001 -s /sbin/nologin -d /app appuser \
    && mkdir -p /app/logs \
    && chown -R appuser:appuser /app

USER appuser

# 暴露端口
EXPOSE 8000

# 启动命令
#
# --timeout 必须显式给：gunicorn 默认 30s，而 UvicornWorker 的 `timeout_notify` 就等于这个值
# （心跳周期 == 判死阈值）。任何一次事件循环停顿（GC、大 JSON、同步 CPU 段）超过 30s
# 都会让 arbiter 直接 SIGKILL 整个 worker，该 worker 上的在途请求一起丢。
# 后端自己的预算更宽：AI_TOTAL_BUDGET_SECONDS=45、单次 30s×2 次重试，压线就会被打死。
# 取 120s：既高于 AI/第三方抓取的最长预算，又能在真死锁时于 2 分钟内自愈。
CMD ["gunicorn", "-k", "uvicorn.workers.UvicornWorker", "-w", "2", "-b", "0.0.0.0:8000", "--timeout", "120", "--graceful-timeout", "30", "app.main:app"]
