#!/usr/bin/env python3
"""docker-compose.yml 折叠标量自检：`command: >` / `|` 块里不许出现 `#`。

## 为什么需要这个检查（2026-10-20 线上事故）

MySQL 服务是这么写的：

```yaml
    command: >
      --character-set-server=utf8mb4
      # binlog 默认保留 30 天……        ← 以为这是 YAML 注释
      --binlog-expire-logs-seconds=259200
```

`command: >` 是 YAML 的**折叠标量（folded scalar）**：它下面所有缩进更深的行
都是**字面文本**，`#` 在这里不是注释，会被原样拼进命令行：

    mysqld --character-set-server=utf8mb4 # binlog 默认保留 30 天…… --binlog-…

结果是 mysqld 启动即报

    [ERROR] [MY-010147] [Server] Too many arguments (first extra is '#').
    [ERROR] [MY-010119] [Server] Aborting

mysql 永远不 healthy → `app` 因 `depends_on: mysql: service_healthy` 起不来 →
nginx 起不来 → **整站 502**。而 `docker compose config` 不报错、CI 也不跑 compose，
所以这条只能靠静态检查兜住。

判定：在 `command:`/`entrypoint:` 的 `>`/`|` 块内（缩进比该行更深、非空），
任何以 `#` 开头的行都是**会被当成参数**的，直接失败。

零依赖（只用标准库），可在 CI 里跑。
"""

import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPOSE = os.path.join(ROOT, "docker-compose.yml")

SCALAR_KEYS = ("command:", "entrypoint:")


def main() -> int:
    if not os.path.exists(COMPOSE):
        print("找不到 docker-compose.yml")
        return 1

    lines = io.open(COMPOSE, encoding="utf-8").read().split("\n")
    problems = []
    checked = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        # 只认 `command: >` / `command: |` / `entrypoint: >` 这类块标量
        if not any(stripped.startswith(k) for k in SCALAR_KEYS):
            continue
        if not (stripped.endswith(">") or stripped.endswith("|")):
            continue
        checked += 1
        base = len(line) - len(line.lstrip())
        for j in range(i + 1, len(lines)):
            nxt = lines[j]
            if nxt.strip() == "":
                continue
            if len(nxt) - len(nxt.lstrip()) <= base:
                break
            if nxt.strip().startswith("#"):
                problems.append(
                    f"  第 {j + 1} 行：{nxt.strip()[:60]}"
                )

    if problems:
        print("❌ docker-compose.yml 的折叠标量里出现了 `#` —— 它会被当成命令行参数传给容器")
        print("   （2026-10-20 事故：mysqld 报 Too many arguments (first extra is '#') 后 abort）")
        print("\n".join(problems))
        print("   修法：把说明文字移到 `command: >` 这一行的**外面**（同级或更浅的缩进）。")
        return 1

    print(f"✅ compose 折叠标量自检通过（检查了 {checked} 个块，无 `#` 混入）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
