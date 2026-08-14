import re

import numpy as np

from pdf_translate.layout import group_lines

CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")


class PageDecision:
    """页面过滤结果"""

    def __init__(self, is_illustration_page, kept, stats):
        self.is_illustration_page = is_illustration_page
        self.kept = kept
        self.stats = stats

    def should_translate(self):
        return self.kept

    def __repr__(self):
        return (
            f"PageDecision(插画页={self.is_illustration_page}, "
            f"翻译块数={len(self.kept)}, stats={self.stats})"
        )


class PageFilter:
    """页面级文本过滤策略。

    可扩展点：
    - 自定义页面分类器（实现 classify(img_arr, regions) -> bool），
      通过 add_classifier 追加，任一命中即视为插画页
    - 默认使用背景复杂度 + 日文比例的分类器

    行为：
    - 插画页：整页跳过翻译（图中文字不作处理）
    - 正文页：过滤艺术字（拉丁/符号混排装饰文字）、
      插图/软件界面等嵌入图片中的文字后翻译
    """

    def __init__(self, config, bg_unique_threshold=800,
                 complex_ratio=0.7, cjk_ratio=0.85, long_ratio=0.175,
                 long_block_ratio=0.07, art_text=True,
                 text_width_ratio=0.11, font_ratio_min=0.85,
                 font_ratio_max=1.6, bright_threshold=0.78,
                 group_align_tol=60, group_font_min=0.88,
                 group_bright_min=0.78, tail_bright_min=0.65,
                 vertical_span_min=0.15):
        self.config = config
        self.bg_unique_threshold = bg_unique_threshold
        self.complex_ratio = complex_ratio
        self.cjk_ratio = cjk_ratio
        self.long_ratio = long_ratio
        self.long_block_ratio = long_block_ratio
        self.art_text = art_text
        self.text_width_ratio = text_width_ratio
        self.font_ratio_min = font_ratio_min
        self.font_ratio_max = font_ratio_max
        self.bright_threshold = bright_threshold
        self.group_align_tol = group_align_tol
        self.group_font_min = group_font_min
        self.group_bright_min = group_bright_min
        self.tail_bright_min = tail_bright_min
        self.vertical_span_min = vertical_span_min
        self._classifiers = [self._default_classifier]

    def add_classifier(self, fn):
        """追加页面分类器 fn(img_arr, regions) -> bool"""
        self._classifiers.append(fn)
        return self

    def apply(self, img_arr, regions):
        basic = [
            r for r in regions
            if r.score >= self.config.OCR_CONFIDENCE_THRESHOLD
            and r.width >= 4 and r.height >= 4
        ]
        stats = {}
        if not basic:
            return PageDecision(False, [], stats)
        bg_uniqs = [r.bg_unique_colors(img_arr) for r in basic]
        cjk_blocks = sum(1 for r in basic if CJK_RE.search(r.text))
        complex_blocks = sum(1 for u in bg_uniqs if u > self.bg_unique_threshold)
        total = len(basic)
        stats["complex_background"] = (complex_blocks, total)
        stats["cjk_blocks"] = (cjk_blocks, total)
        page_dim = max(float(img_arr.shape[0]), float(img_arr.shape[1]))
        long_blocks = sum(
            1 for r in basic
            if max(r.width, r.height) > self.long_ratio * page_dim
            and len(r.text) >= 8
        )
        stats["long_blocks"] = (long_blocks, total)

        for classifier in self._classifiers:
            if classifier(img_arr, basic, stats):
                return PageDecision(True, [], stats)

        kept = [
            r for r in basic
            if not self._is_art_text(r.text)
            and len(r.text) >= 3
        ]
        stats["skipped_illustration_text"] = (total - len(kept), total)
        lines = self.filter_lines(group_lines(kept), img_arr)
        kept = [r for line in lines for r in line.regions]
        stats["skipped_embedded_text"] = (
            total - len(kept), total
        )
        return PageDecision(False, kept, stats)

    def filter_lines(self, lines, img_arr):
        """过滤嵌入图片中的文字（插画标注/软件界面/面板标签等）。

        判定依据（正文特征，命中任一即保留）：
        - 横排：
          1) 行宽 >= 页面宽度 text_width_ratio（11%）——长句正文
          2) 属于对齐读列（x_min 对齐且 y 相邻 >= 3 行、
             字号与亮度均达标）
          3) 字号在正文中位字号 font_ratio_min..max 且背景为亮纸
             （所在读列字号不偏小）
          4) 段首/段中延续短行（字号与亮度达标）
          5) 段末短行（亮度与读列字号达标，或以助词/词尾收束）
        - 竖排：纵向跨度 >= 页面高度 vertical_span_min（15%）
        软件界面/插画中的文字短小、密排、背景偏灰多色，
        与白纸黑字的正文长句在以上特征上可区分。
        """
        if not lines:
            return lines
        h_med = sorted(l.text_height for l in lines)[len(lines) // 2]
        page_h, page_w = img_arr.shape[:2]
        groups = self._column_groups(
            [l for l in lines if not l.vertical], img_arr
        )
        out = []
        for line in lines:
            if not self._is_body_line(line, h_med, img_arr, page_h, page_w,
                                      groups):
                continue
            out.append(line)
        return out

    def _column_groups(self, lines, img_arr):
        """按 x_min 容差与 y 邻近把横排行聚成读列（正文段落/面板列）"""
        groups = []
        for line in sorted(lines, key=lambda l: (l.x_min, l.y_min)):
            placed = False
            for g in groups:
                if abs(line.x_min - g["x_ref"]) > self.group_align_tol:
                    continue
                gap_tol = 2.5 * max(
                    line.text_height, g["h_med"] or line.text_height
                )
                if line.y_min <= g["y_max"] and line.y_max >= g["y_min"]:
                    near = True
                else:
                    below_gap = line.y_min - g["y_max"]
                    above_gap = g["y_min"] - line.y_max
                    near = (0 <= below_gap <= gap_tol
                            or 0 <= above_gap <= gap_tol)
                if near:
                    g["lines"].append(line)
                    g["y_min"] = min(g["y_min"], line.y_min)
                    g["y_max"] = max(g["y_max"], line.y_max)
                    placed = True
                    break
            if not placed:
                groups.append({
                    "x_ref": line.x_min,
                    "lines": [line],
                    "h_med": 0,
                    "y_min": line.y_min,
                    "y_max": line.y_max,
                })
        for g in groups:
            hs = sorted(l.text_height for l in g["lines"])
            g["h_med"] = hs[len(hs) // 2]
            g["bright_med"] = sorted(
                self._line_brightness(l, img_arr) for l in g["lines"]
            )[len(g["lines"]) // 2]
            g["y_min"] = min(l.y_min for l in g["lines"])
            g["y_max"] = max(l.y_max for l in g["lines"])
        return groups

    def _find_group(self, line, groups):
        for g in groups:
            if any(member is line for member in g["lines"]):
                return g
        return None

    def _is_body_line(self, line, h_med, img_arr, page_h, page_w, groups):
        if line.vertical:
            v_span = (line.y_max - line.y_min) / max(page_h, 1)
            return v_span >= self.vertical_span_min
        line_w = line.x_max - line.x_min
        w_ratio = line_w / max(page_w, 1)
        h_ratio = line.text_height / max(h_med, 1)
        if w_ratio >= self.text_width_ratio:
            if self._line_brightness(line, img_arr) >= 0.70:
                return True
        bright = self._line_brightness(line, img_arr)
        group = self._find_group(line, groups)
        g_h_ratio = None
        if group is not None:
            g_h_ratio = group["h_med"] / max(h_med, 1)
            if (len(group["lines"]) >= 3
                    and g_h_ratio >= self.group_font_min
                    and group["bright_med"] >= self.group_bright_min):
                return True
            idx = group["lines"].index(line)
            last = len(group["lines"]) - 1
            prev = group["lines"][idx - 1] if idx > 0 else None
            gap = line.y_min - prev.y_max if prev else None
            if 0 <= idx <= last - 1:
                if (h_ratio >= 0.70
                        and bright >= self.bright_threshold
                        and (prev is None or gap < 1.6 * line.text_height)):
                    return True
            elif idx == last and len(group["lines"]) > 1:
                if (h_ratio >= 0.55
                        and bright >= self.tail_bright_min
                        and g_h_ratio >= 0.7
                        and (gap is None or gap > 1.5 * line.text_height
                             or self._ends_body_suffix(line.text))):
                    return True
        if (self.font_ratio_min <= h_ratio <= self.font_ratio_max
                and bright >= self.bright_threshold):
            if len(group["lines"]) >= 2:
                if g_h_ratio is None or g_h_ratio >= 0.7:
                    return True
            elif 0.9 <= h_ratio <= 1.25 and bright >= 0.80:
                return True
        return False

    @staticmethod
    def _ends_body_suffix(text):
        return bool(re.search(
            r"(しました|ました|します|です|ます|した|なさい|ください"
            r"|。|、|を|に|て|で|た|は|が)$",
            text,
        ))

    @staticmethod
    def _line_brightness(line, img_arr):
        h, w = img_arr.shape[:2]
        x0 = max(line.x_min, 0)
        x1 = min(line.x_max, w)
        y0 = max(line.y_min, 0)
        y1 = min(line.y_max, h)
        if x1 <= x0 or y1 <= y0:
            return 0.0
        win = img_arr[y0:y1, x0:x1]
        return float((win.mean(axis=2) > 200).mean())

    def _default_classifier(self, img_arr, basic, stats):
        complex_blocks, total = stats["complex_background"]
        cjk_blocks, _ = stats["cjk_blocks"]
        long_blocks, _ = stats["long_blocks"]
        return (
            complex_blocks / total > self.complex_ratio
            and cjk_blocks / total < self.cjk_ratio
            and long_blocks / total < self.long_block_ratio
        )

    @staticmethod
    def _is_art_text(text):
        """装饰/艺术字特征：以拉丁字母或符号为主、日文占比过低的文本。

        正文行可能夹带少量拉丁/数字（如「B5/350dpi」「OK」），
        只要日文（汉字+假名）占比较足即可视为正文。
        """
        if not text:
            return True
        cjk = len(CJK_RE.findall(text))
        kana = len(re.findall(r"[\u3040-\u30ff]", text))
        latin = len(re.findall(r"[A-Za-z]", text))
        total = max(len(text), 1)
        if cjk / total >= 0.5:
            return False
        if kana / total >= 0.5:
            return False
        if (cjk + kana) / total >= 0.35 and latin / total < 0.4:
            return False
        if latin / total >= 0.4:
            return True
        return True