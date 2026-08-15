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
                 text_width_ratio=0.11, anchor_width_ratio=0.22,
                 font_ratio_min=0.85,
                 font_ratio_max=1.6, bright_threshold=0.78,
                 group_align_tol=60, group_font_min=0.88,
                 group_bright_min=0.78, tail_bright_min=0.65,
                 group_span_min=0.08, vertical_span_min=0.15):
        self.config = config
        self.bg_unique_threshold = bg_unique_threshold
        self.complex_ratio = complex_ratio
        self.cjk_ratio = cjk_ratio
        self.long_ratio = long_ratio
        self.long_block_ratio = long_block_ratio
        self.art_text = art_text
        self.text_width_ratio = text_width_ratio
        self.anchor_width_ratio = anchor_width_ratio
        self.font_ratio_min = font_ratio_min
        self.font_ratio_max = font_ratio_max
        self.bright_threshold = bright_threshold
        self.group_align_tol = group_align_tol
        self.group_font_min = group_font_min
        self.group_bright_min = group_bright_min
        self.tail_bright_min = tail_bright_min
        self.group_span_min = group_span_min
        self.vertical_span_min = vertical_span_min
        self._classifiers = [self._default_classifier]
        self._art_regions_cache = {}

    def add_classifier(self, fn):
        """追加页面分类器 fn(img_arr, regions) -> bool"""
        self._classifiers.append(fn)
        return self

    def apply(self, img_arr, regions, page_no=None):
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
            and len(r.text) >= 2
        ]
        for r in kept:
            stripped = self._strip_repeat_tail(r.text)
            if stripped:
                r.text = stripped
            if r.text and self._is_pure_kana_junk(r.text):
                r.text = ""
        kept = [r for r in kept if r.text]
        stats["skipped_illustration_text"] = (total - len(kept), total)
        lines = self.filter_lines(group_lines(kept), img_arr, page_no)
        kept = [r for line in lines for r in line.regions]
        stats["skipped_embedded_text"] = (
            total - len(kept), total
        )
        return PageDecision(False, kept, stats)

    def filter_lines(self, lines, img_arr, page_no=None):
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

        针对截图旁/插图上的孤立的标注（如 SAI 界面步骤标注
        「①「ファイル」メニュー選択《」），追加正文锚规则：
        中等宽度行（text_width_ratio ~ anchor_width_ratio）必须
        与同栈中的正文宽行（>= anchor_width_ratio）相连，或属于
        >= 3 行且纵向跨幅 >= group_span_min 的真实读列；
        孤立的短行不得因单行字号规则或「ます」结尾而被放行。

        最后一关：彩色插画区域（原画/大图）内的文字一律不翻。
        """
        if not lines:
            return lines
        h_med = sorted(l.text_height for l in lines)[len(lines) // 2]
        page_h, page_w = img_arr.shape[:2]
        stacks = self._column_stacks(lines, page_w)
        groups = self._column_groups(
            [l for l in lines if not l.vertical], img_arr
        )
        out = []
        for line in lines:
            if not self._is_body_line(line, h_med, img_arr, page_h, page_w,
                                      groups, stacks):
                if (getattr(self.config, "UI_LABEL_TRANSLATE", False)
                        and self._is_ui_label(line, img_arr, out)):
                    out.append(line)
                continue
            out.append(line)
        art = self._art_regions(img_arr, page_no)
        if art:
            out = [l for l in out
                   if not self._inside_art_region(l, art)]
        skip = getattr(self.config, "SKIP_REGIONS_MANUAL", {}).get(page_no, [])
        if skip:
            out = [l for l in out
                   if not self._inside_skip_region(l, skip)]
        return out

    def _is_ui_label(self, line, img_arr, kept):
        """界面标签兜底: 插画/截图旁的按钮、面板短标签原位保留翻译。

        要求: 纯假名/汉字(无拉丁数字符号)、可读字号、对比度尚可,
        且与保留的正文行无重叠(避免盖住正文)。
        """
        text = line.text
        if not text or len(text) < 2 or len(text) > 12:
            return False
        if re.search(
            r"[^\u3040-\u30ff\u3400-\u9fff\u30fc|・/〔〕【】{}()（）]",
            text,
        ):
            return False
        h = line.text_height
        if h < 22 or h > 160:
            return False
        if line.x_max - line.x_min > 900:
            return False
        if self._line_brightness(line, img_arr) < 0.50:
            return False
        if self._ends_body_suffix(text) and h < 60:
            return False
        if self._overlaps_kept(line, kept):
            return False
        return True

    def _art_regions(self, img_arr, page_no=None):
        """彩色插画区域检测（原画/大图），缓存按页。

        流程: 降采样 -> 饱和度掩码 -> 二值膨胀 -> 连通域 ->
        面积 >= ART_REGION_MIN_AREA 的组件即插画区。
        阈值/开关在 config（ART_REGION_*）；若某个页在
        ART_REGIONS_MANUAL 中配置了区域，则手动区优先
        （替换自动检测），新页面只需改 config 无需改代码。

        手动区先优先，避免把大截图/工具条误判为插画。
        """
        if not getattr(self.config, "ART_REGION_ENABLED", True):
            return []
        if page_no in self._art_regions_cache:
            return self._art_regions_cache[page_no]
        manual = getattr(self.config, "ART_REGIONS_MANUAL", {}).get(
            page_no)
        regions = (
            list(manual)
            if manual is not None
            else self._detect_art_regions(img_arr)
        )
        self._art_regions_cache[page_no] = regions
        return regions

    def _detect_art_regions(self, img_arr):
        step = getattr(self.config, "ART_REGION_STEP", 8)
        sat_th = getattr(self.config, "ART_REGION_SAT", 0.22)
        min_mx = getattr(self.config, "ART_REGION_MIN_MX", 0.12)
        dilate = getattr(self.config, "ART_REGION_DILATE", 1)
        min_area = getattr(self.config, "ART_REGION_MIN_AREA", 300000)
        rgb = img_arr[::step, ::step].astype(np.float32) / 255.0
        mx = rgb.max(axis=2)
        mn = rgb.min(axis=2)
        sat = np.where(mx > min_mx, (mx - mn) / np.maximum(mx, 1e-6), 0.0)
        mask = sat > sat_th
        for _ in range(dilate):
            n = np.zeros_like(mask)
            n[1:, :] |= mask[:-1, :]
            n[:-1, :] |= mask[1:, :]
            n[:, 1:] |= mask[:, :-1]
            n[:, :-1] |= mask[:, 1:]
            mask |= n
        h, w = mask.shape
        lab = np.zeros((h, w), dtype=np.int32)
        cur = 0
        for y in range(h):
            for x in range(w):
                if not mask[y, x] or lab[y, x]:
                    continue
                cur += 1
                lab[y, x] = cur
                stack = [(y, x)]
                while stack:
                    cy, cx = stack.pop()
                    if cy > 0 and mask[cy - 1, cx] and not lab[cy - 1, cx]:
                        lab[cy - 1, cx] = cur
                        stack.append((cy - 1, cx))
                    if cy < h - 1 and mask[cy + 1, cx] and not lab[cy + 1, cx]:
                        lab[cy + 1, cx] = cur
                        stack.append((cy + 1, cx))
                    if cx > 0 and mask[cy, cx - 1] and not lab[cy, cx - 1]:
                        lab[cy, cx - 1] = cur
                        stack.append((cy, cx - 1))
                    if cx < w - 1 and mask[cy, cx + 1] and not lab[cy, cx + 1]:
                        lab[cy, cx + 1] = cur
                        stack.append((cy, cx + 1))
        regions = []
        page_area = max(h * w * step * step, 1)
        for i in range(1, cur + 1):
            ys, xs = np.where(lab == i)
            area = len(xs) * step * step
            if area < min_area:
                continue
            r = (int(xs.min() * step), int(ys.min() * step),
                 int(xs.max() * step + step), int(ys.max() * step + step))
            if ((r[2] - r[0]) * (r[3] - r[1])
                    > page_area * getattr(self.config, "ART_REGION_MAX_RATIO", 0.55)):
                continue
            regions.append(r)
        return regions

    def _inside_art_region(self, line, art_regions):
        cx = (line.x_min + line.x_max) // 2
        cy = (line.y_min + line.y_max) // 2
        return any(
            x0 <= cx <= x1 and y0 <= cy <= y1
            for x0, y0, x1, y1 in art_regions
        )

    @staticmethod
    def _inside_skip_region(line, skip_regions):
        """行框完整包含于手动跳过区内才丢弃（区框较紧，避免误伤边缘正文）"""
        return any(
            x0 <= line.x_min and y0 <= line.y_min
            and line.x_max <= x1 and line.y_max <= y1
            for x0, y0, x1, y1 in skip_regions
        )

    @staticmethod
    def _overlaps_kept(line, kept):
        for k in kept:
            x_lo = max(line.x_min, k.x_min)
            x_hi = min(line.x_max, k.x_max)
            y_lo = max(line.y_min, k.y_min)
            y_hi = min(line.y_max, k.y_max)
            if x_lo < x_hi and y_lo < y_hi:
                inter = (x_hi - x_lo) * (y_hi - y_lo)
                area = max((line.x_max - line.x_min) * (line.y_max - line.y_min), 1)
                if inter / area > 0.4:
                    return True
        return False

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

    def _column_stacks(self, lines, page_w):
        """按纵向邻接把横排行聚成文栈（同一段落/标注的连续行）。

        相邻判定：x 范围重叠 >= 较短行宽的一半，且纵向间距
        <= 3 倍行高（允许轻微行框交叠）。用于判断某行是否
        与正文宽行（>= anchor_width_ratio）处于同一段文字流。
        """
        stacks = []
        for line in sorted(lines, key=lambda l: l.y_min):
            if line.vertical:
                continue
            placed = None
            for s in stacks:
                prev = s["lines"][-1]
                x_lo = max(line.x_min, prev.x_min)
                x_hi = min(line.x_max, prev.x_max)
                if x_hi <= x_lo:
                    continue
                short_w = min(line.x_max - line.x_min,
                              prev.x_max - prev.x_min)
                if (x_hi - x_lo) / max(short_w, 1) < 0.5:
                    continue
                gap = line.y_min - prev.y_max
                if -0.5 * line.text_height <= gap <= 3.0 * max(
                        line.text_height, prev.text_height):
                    placed = s
                    break
            if placed is not None:
                placed["lines"].append(line)
            else:
                stacks.append({"lines": [line]})
        for s in stacks:
            s_max = max(l.x_max - l.x_min for l in s["lines"])
            s["max_w_ratio"] = s_max / max(page_w, 1)
        return stacks

    def _find_stack(self, line, stacks):
        for s in stacks:
            if any(member is line for member in s["lines"]):
                return s
        return None

    def _is_body_line(self, line, h_med, img_arr, page_h, page_w, groups,
                      stacks):
        if self._is_repeat_text(line.text):
            return False
        if line.vertical:
            v_span = (line.y_max - line.y_min) / max(page_h, 1)
            return v_span >= self.vertical_span_min
        line_w = line.x_max - line.x_min
        w_ratio = line_w / max(page_w, 1)
        h_ratio = line.text_height / max(h_med, 1)
        bright = self._line_brightness(line, img_arr)
        group = self._find_group(line, groups)
        stack = self._find_stack(line, stacks)
        anchored = stack is not None and stack["max_w_ratio"] >= self.anchor_width_ratio
        if w_ratio >= self.text_width_ratio:
            if bright >= 0.70:
                if (w_ratio >= self.anchor_width_ratio
                        or anchored
                        or self._is_read_column(group, page_h)):
                    return True
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
                        and ((prev is not None and gap < 1.6 * line.text_height)
                             or (prev is None
                                 and (anchored
                                      or self._is_read_column(group, page_h))))):
                    return True
            elif idx == last and len(group["lines"]) > 1:
                span_ok = ((group["y_max"] - group["y_min"]) / max(page_h, 1)
                           >= self.group_span_min)
                if (h_ratio >= 0.55
                        and bright >= self.tail_bright_min
                        and g_h_ratio >= 0.7
                        and span_ok
                        and (gap is None or gap > 1.5 * line.text_height
                             or self._ends_body_suffix(line.text))):
                    return True
        if (self.font_ratio_min <= h_ratio <= self.font_ratio_max
                and bright >= self.bright_threshold):
            if len(group["lines"]) >= 2:
                if (g_h_ratio is None or g_h_ratio >= 0.7) and (
                        anchored or self._is_read_column(group, page_h)):
                    return True
            elif 0.9 <= h_ratio <= 1.25 and bright >= 0.80:
                if anchored:
                    return True
        return False

    def _is_read_column(self, group, page_h):
        if group is None or len(group["lines"]) < 3:
            return False
        return ((group["y_max"] - group["y_min"]) / max(page_h, 1)
                >= self.group_span_min)

    @staticmethod
    def _strip_repeat_tail(text, min_len=6):
        """剥离 OCR 把目录点线/装饰线误识的幻影尾部。

        点线幻影的特征: 尾部从真条目结束位置起连续无汉字,
        由片假名/重复假名组成(如「線画の作成コーニュレアアルにアニュー...」
        →「線画の作成」)。真实正文常以汉字、句读或常见动词收尾,
        带这些结尾的尾部不剥离。
        """
        if len(text) < min_len + 2:
            return text
        tail = ""
        for ch in reversed(text):
            if ch.isspace():
                continue
            if ch in "。！？!?、，,.;;:：（）()「」『』【】［］":
                break
            if "\u4e00" <= ch <= "\u9fff":
                break
            tail = ch + tail
        if len(tail) < min_len:
            return text
        if re.search(
            r"(ます|です|ました|して|した|している|していた|たい|て|た|ね|よ|か|な|ん|る|い)$",
            tail,
        ):
            return text
        prefix = text[: len(text) - len(tail)]
        if not prefix or prefix[-1] < "\u4e00" or prefix[-1] > "\u9fff":
            return text
        kat = len(re.findall(r"[\u30a0-\u30ff\u30fc]", tail))
        bigrams = [tail[i:i + 2] for i in range(len(tail) - 1)]
        repetitive = bool(bigrams) and len(set(bigrams)) / len(bigrams) <= 0.85
        if kat >= 2 or repetitive:
            return prefix
        return text

    @staticmethod
    def _is_repeat_text(text, min_len=15, max_unique_ratio=0.75):
        """装饰边框/插图文字 OCR 幻觉：长行中重复假名/符号二元组占比过高。

        目录缩略图边框、插画装饰线等被 OCR 识别为大量重复字符
        （如「にににだどどここ」），正文长句的二元组几乎不重复。
        """
        if len(text) < min_len:
            return False
        bigrams = [text[i:i + 2] for i in range(len(text) - 1)]
        if not bigrams:
            return False
        return len(set(bigrams)) / len(bigrams) <= max_unique_ratio

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
        if not (
            complex_blocks / total > self.complex_ratio
            and cjk_blocks / total < self.cjk_ratio
            and long_blocks / total < self.long_block_ratio
        ):
            return False
        page_w = max(img_arr.shape[1], 1)
        body_candidates = [
            r for r in basic
            if (r.x_max - r.x_min) >= self.text_width_ratio * page_w
            and len(CJK_RE.findall(r.text)) >= 8
        ]
        if body_candidates:
            for r in body_candidates:
                r_ratio = (r.y_max - r.y_min) / max(img_arr.shape[0], 1)
                if (r_ratio <= 0.08
                        and self._line_brightness(r, img_arr) >= 0.70):
                    return False
        return True

    @staticmethod
    def _is_pure_kana_junk(text, min_len=8):
        """整段纯假名且无汉字/拉丁/数字的长串 = 目录点线等 OCR 幻影。"""
        if len(text) < min_len:
            return False
        for ch in text:
            if not ("\u3040" <= ch <= "\u30ff" or ch == "\u30fc"):
                return False
        return True

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
            runs = re.findall(r"[\u3040-\u30ff\u3400-\u9fff]+", text)
            if runs and len(max(runs, key=len)) >= 4:
                return False
            return True
        return True