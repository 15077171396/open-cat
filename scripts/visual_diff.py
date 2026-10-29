"""视觉回归对比：逐页比较 before/after 截图，输出差异比例与差异图。

用法：backend/venv/Scripts/python.exe scripts/visual_diff.py
判据：差异像素占比 < 0.3% 视为「渲染一致」（页面里的相对时间、随机推荐位会造成
少量差异，纯像素级 0 差异不可强求）。
"""

import io
import os
import sys

from PIL import Image, ImageChops

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 目录名可用环境变量覆盖：管理端用 admin-before / admin-after
BEFORE = os.path.join(ROOT, ".visual", os.environ.get("VISUAL_BEFORE", "before"))
AFTER = os.path.join(ROOT, ".visual", os.environ.get("VISUAL_AFTER", "after"))
DIFF = os.path.join(ROOT, ".visual", os.environ.get("VISUAL_DIFF", "diff"))

# 允许的差异像素占比（0.003 = 0.3%）
TOLERANCE = 0.003


def compare(name: str) -> tuple[float, str]:
    b_path = os.path.join(BEFORE, f"{name}.png")
    a_path = os.path.join(AFTER, f"{name}.png")
    if not (os.path.exists(b_path) and os.path.exists(a_path)):
        return -1.0, "缺文件"

    b = Image.open(b_path).convert("RGB")
    a = Image.open(a_path).convert("RGB")

    # 尺寸不同：先按「短边对齐」判断是否为整体布局变化
    if b.size != a.size:
        return 1.0, f"尺寸变化 {b.size} -> {a.size}"

    diff = ImageChops.difference(a, b)
    # 转灰度后按阈值二值化，滤掉抗锯齿/亚像素渲染噪声
    gray = diff.convert("L")
    pixels = gray.load()
    w, h = gray.size
    changed = 0
    for y in range(h):
        for x in range(w):
            if pixels[x, y] > 12:
                changed += 1
    ratio = changed / float(w * h)

    if changed:
        os.makedirs(DIFF, exist_ok=True)
        # 差异处标红，便于肉眼定位
        overlay = a.copy()
        op = overlay.load()
        gp = gray.load()
        for y in range(h):
            for x in range(w):
                if gp[x, y] > 12:
                    op[x, y] = (255, 0, 0)
        overlay.save(os.path.join(DIFF, f"{name}.png"))

    return ratio, f"{changed} px 变化"


def main() -> int:
    names = sorted(
        os.path.splitext(f)[0] for f in os.listdir(BEFORE) if f.endswith(".png")
    )
    if not names:
        print("没有 before 截图，先跑 scripts/shoot.sh before")
        return 1

    bad = []
    for name in names:
        ratio, note = compare(name)
        if ratio < 0:
            print(f"SKIP {name:16s} {note}")
            bad.append(name)
            continue
        flag = "OK  " if ratio <= TOLERANCE else "DIFF"
        print(f"{flag} {name:16s} {ratio*100:6.2f}%  {note}")
        if ratio > TOLERANCE:
            bad.append(name)

    print()
    if bad:
        print(f"{len(bad)} 个页面渲染不一致：{bad}")
        return 2
    print(f"全部 {len(names)} 个页面渲染一致（阈值 {TOLERANCE*100:.1f}%）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
