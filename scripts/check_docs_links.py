#!/usr/bin/env python3
"""文档链接与锚点校验（零依赖）：校验相对文件链接、文内锚点与标题编号重复。

用法：python scripts/check_docs_links.py [--quiet]
退出码：0 = 全部通过；1 = 存在问题
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import unquote

REPO_ROOT = Path(__file__).resolve().parent.parent

# 校验范围
TARGETS = [REPO_ROOT / "docs", REPO_ROOT / "README.md"]

# Markdown 链接：[文本](目标)
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
# 标题行
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$", re.MULTILINE)
# 行内代码中的内容不参与链接解析
CODE_SPAN_RE = re.compile(r"`[^`\n]*`")


def github_slug(text: str) -> str:
    """按 GitHub 规则生成标题锚点：转小写、去 markdown 标记、空格转连字符，保留 CJK。"""
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*_~]", "", text)
    text = text.strip().lower()
    out = []
    for ch in text:
        if ch in (" ", "-", "_"):
            out.append(ch)
            continue
        category = unicodedata.category(ch)
        if category[0] in ("L", "N") or category == "Lo":
            out.append(ch)
    return "".join(out).replace(" ", "-")


def strip_code(text: str) -> str:
    """把围栏代码块与行内代码替换为等长空白（保留行号），避免示例被误判成标题或真链接。"""

    def blank_block(match: re.Match[str]) -> str:
        block = match.group(0)
        # 保留换行数量，让后续报错的行号仍然准确
        return "\n" * block.count("\n")

    text = re.sub(r"```.*?```", blank_block, text, flags=re.DOTALL)
    text = re.sub(r"~~~.*?~~~", blank_block, text, flags=re.DOTALL)
    text = CODE_SPAN_RE.sub(lambda m: " " * len(m.group(0)), text)
    return text


def collect_headings(path: Path) -> list[tuple[int, str, str]]:
    """返回 [(层级, 原文, 锚点)]。"""
    text = strip_code(path.read_text(encoding="utf-8"))
    result = []
    for match in HEADING_RE.finditer(text):
        level = len(match.group(1))
        raw = match.group(2)
        result.append((level, raw, github_slug(raw)))
    return result


def iter_markdown_files() -> list[Path]:
    files: list[Path] = []
    for target in TARGETS:
        if target.is_file():
            files.append(target)
        elif target.is_dir():
            files.extend(sorted(target.rglob("*.md")))
    return files


def main() -> int:
    # Windows 控制台默认 GBK，输出 ✅ 会抛 UnicodeEncodeError，故强制 UTF-8 输出。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser()
    parser.add_argument("--quiet", action="store_true", help="仅输出问题")
    args = parser.parse_args()

    files = iter_markdown_files()
    if not files:
        print("未找到任何 Markdown 文件", file=sys.stderr)
        return 1

    anchors: dict[Path, set[str]] = {f: {a for _, _, a in collect_headings(f)} for f in files}
    problems: list[str] = []
    checked_links = 0
    checked_anchors = 0

    for path in files:
        rel = path.relative_to(REPO_ROOT).as_posix()
        # 剥离代码块后再检查，避免 shell 注释被误判成标题、示例链接被误判成真链接
        raw_text = strip_code(path.read_text(encoding="utf-8"))

        # 标题编号重复检查（章节重排最易踩）：阿拉伯与中文序号两种风格都查
        seen_numbers: dict[str, list[int]] = {}
        for line_no, line in enumerate(raw_text.splitlines(), start=1):
            m = re.match(r"^(#{1,6})\s+(\d+(?:\.\d+)*)\.?\s", line)
            if not m:
                m = re.match(r"^(#{1,6})\s*([一二三四五六七八九十百]+)、", line)
            if m:
                seen_numbers.setdefault(m.group(2), []).append(line_no)
        for number, lines in seen_numbers.items():
            if len(lines) > 1:
                problems.append(
                    f"{rel}: 标题编号重复 -> {number}（行 {', '.join(map(str, lines))}）"
                )

        # --- 链接检查 ---
        for match in LINK_RE.finditer(raw_text):
            target = match.group(1)
            if target.startswith(("http://", "https://", "mailto:", "tel:")):
                continue

            if target.startswith("#"):
                checked_anchors += 1
                slug = github_slug(unquote(target[1:]).replace("-", " "))
                # 锚点可能带连字符原文，两种形式都比对一次
                if target[1:] not in anchors[path] and slug not in anchors[path]:
                    problems.append(f"{rel}: 锚点不存在 -> {target}")
                continue

            checked_links += 1
            file_part, _, frag = target.partition("#")
            dest = (path.parent / unquote(file_part)).resolve()
            if not dest.exists():
                problems.append(f"{rel}: 链接文件不存在 -> {file_part}")
                continue
            if frag and dest.suffix == ".md":
                if frag not in anchors.get(dest, set()):
                    problems.append(
                        f"{rel}: 链接锚点不存在 -> {file_part}#{frag}"
                    )

    if not args.quiet:
        print(f"校验 {len(files)} 个文件：{checked_links} 个文件链接，{checked_anchors} 个文内锚点")

    if problems:
        print(f"\n发现 {len(problems)} 个问题：")
        for item in problems:
            print("  ✗ " + item)
        return 1

    print("✅ 全部链接与锚点有效")
    return 0


if __name__ == "__main__":
    sys.exit(main())
