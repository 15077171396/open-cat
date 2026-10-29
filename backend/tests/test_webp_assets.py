"""前端静态图的格式守卫（PERF-10 回归）。

## 背景

`PERF-10` 把 6 张 hero 图 + 认证二维码从 PNG 转成 WebP，压掉了约一半体积。
但转换脚本用了 `Image.open(src).convert('RGB')` —— **这一步把透明通道丢了**。

这些图的背景本来就是**全透明**的（hero 是插画、二维码是白底挖空），
转成 RGB 后透明区被填成了调色板里的第一个颜色：hero 变成**一大块深绿**、
二维码变成黑底。表现是「每个页面右侧的 hero 图都带着一个背景色块」，
而 `convert('RGB')` 不报错、体积还更小，所以只有肉眼看才会发现。

## 守卫

对 `frontend/public/` 下每一对 `<name>.png` / `<name>.webp`：

- 源 PNG **有**透明像素（alpha 最小值 < 255）→ 目标 WebP **必须也有** alpha 通道
  且不是全不透明；
- 源 PNG 完全不透明 → WebP 不要求 alpha（不做多余约束）。

这样以后谁再用 `convert('RGB')` 批量转图，测试直接红。
"""

import os
import unittest

from PIL import Image

def _find_public_dir() -> str:
    """向上找包含 frontend/public 的仓库根。

    不用「__file__ 往上两层」：pytest 在不同 rootdir/import 模式下 `__file__`
    可能是相对路径，拼出来的目录会指到别处（实测会指到兄弟目录）。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    cur = here
    for _ in range(6):
        candidate = os.path.join(cur, "frontend", "public")
        if os.path.isdir(candidate):
            return candidate
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    raise RuntimeError(f"从 {here} 向上找不到 frontend/public")


PUBLIC = _find_public_dir()


def _alpha_extrema(path: str):
    with Image.open(path) as im:
        return im.convert("RGBA").getchannel("A").getextrema()


class WebpKeepsTransparencyTests(unittest.TestCase):
    def test_pairs_are_present(self):
        """至少要找到一对同名 png/webp，否则这个守卫在空转。"""
        names = [
            f[:-5]
            for f in os.listdir(PUBLIC)
            if f.endswith(".webp")
            and os.path.exists(os.path.join(PUBLIC, f[:-5] + ".png"))
        ]
        self.assertGreaterEqual(len(names), 1, "frontend/public 下没有 png/webp 同名对，守卫失效")

    def test_transparent_png_maps_to_transparent_webp(self):
        problems = []
        for f in sorted(os.listdir(PUBLIC)):
            if not f.endswith(".webp"):
                continue
            webp = os.path.join(PUBLIC, f)
            png = os.path.join(PUBLIC, f[:-5] + ".png")
            if not os.path.exists(png):
                continue
            src_min, _ = _alpha_extrema(png)
            dst_min, _ = _alpha_extrema(webp)
            if src_min < 255 and dst_min == 255:
                problems.append(
                    f"{f}：源 PNG 有透明像素（alpha 最小 {src_min}），WebP 却完全不透明 —— "
                    "透明区被填成了实色（多半是 convert('RGB') 丢了 alpha）"
                )
        self.assertEqual(problems, [], "\n".join(problems))


def _transparent_rgb(path: str):
    """返回全透明像素里出现的「非中性」RGB 集合（白/黑视为中性）。"""
    with Image.open(path) as im:
        rgba = im.convert("RGBA")
    # 用 getcolors 而不是 getdata：后者在 Pillow 14 会弃用，而 pytest 配置把
    # DeprecationWarning 当错误（filterwarnings = error），会直接把守卫判红
    colors = rgba.getcolors(maxcolors=1 << 24) or []
    bad = set()
    for _count, (r, g, b, a) in colors:
        if a == 0 and (r, g, b) not in ((0, 0, 0), (255, 255, 255)):
            bad.add((r, g, b))
    return bad


class TransparentAreaIsNeutralTests(unittest.TestCase):
    """透明区的 RGB 必须是白或黑，不能带色。

    背景：6 张 hero PNG 的透明像素曾经存成 rgb(71,112,76)（橄榄绿）。
    alpha 为 0 时看不见，但只要某个环节丢了 alpha（COS 转码、CDN 图片处理、
    浏览器首帧、导出截图），就会露出一块**棕绿色底** —— 2026-10-22 线上
    「首页 hero 刷新时闪一下棕绿」就是这个。PNG 调色板里刷成白色即可根除。
    """

    # 透明色种类超过这个数，说明是「真正的 alpha 渐变」（如 shenqing.png 的 447 色），
    # 不是「整块纯色背景被抠掉」，不适用本规则
    FLAT_BACKDROP_MAX_COLORS = 4
    # 通道值在此范围内视为中性（纯黑 / 纯白及其压缩噪声）
    NEUTRAL_TOLERANCE = 8

    def _is_neutral(self, rgb) -> bool:
        lo, hi = self.NEUTRAL_TOLERANCE, 255 - self.NEUTRAL_TOLERANCE
        return all(c <= lo or c >= hi for c in rgb)

    def test_flat_transparent_backdrop_is_neutral(self):
        problems = []
        for f in sorted(os.listdir(PUBLIC)):
            if not f.endswith(".png"):
                continue
            bad = _transparent_rgb(os.path.join(PUBLIC, f))
            # 多种透明色 → alpha 渐变图，跳过
            if not bad or len(bad) > self.FLAT_BACKDROP_MAX_COLORS:
                continue
            colored = {c for c in bad if not self._is_neutral(c)}
            if colored:
                sample = sorted(colored)[:3]
                problems.append(f"{f}：透明底色带色，例如 {sample}（共 {len(colored)} 种）")
        self.assertEqual(
            problems,
            [],
            "透明区的 RGB 必须刷成白色（否则丢 alpha 时会露出色块）：\n" + "\n".join(problems),
        )


if __name__ == "__main__":
    unittest.main()
