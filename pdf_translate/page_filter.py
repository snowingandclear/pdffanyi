import re

import numpy as np

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
    - 正文页：过滤艺术字（拉丁/符号混排装饰文字）后翻译
    """

    def __init__(self, config, bg_unique_threshold=800,
                 complex_ratio=0.7, cjk_ratio=0.85, art_text=True):
        self.config = config
        self.bg_unique_threshold = bg_unique_threshold
        self.complex_ratio = complex_ratio
        self.cjk_ratio = cjk_ratio
        self.art_text = art_text
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

        for classifier in self._classifiers:
            if classifier(img_arr, basic, stats):
                return PageDecision(True, [], stats)

        kept = [r for r in basic if not self._is_art_text(r.text)]
        return PageDecision(False, kept, stats)

    def _default_classifier(self, img_arr, basic, stats):
        complex_blocks, total = stats["complex_background"]
        cjk_blocks, _ = stats["cjk_blocks"]
        return (
            complex_blocks / total > self.complex_ratio
            and cjk_blocks / total < self.cjk_ratio
        )

    @staticmethod
    def _is_art_text(text):
        """装饰/艺术字特征：非日文为主的文本（拉丁字母或符号混杂）"""
        if not text:
            return True
        cjk = len(CJK_RE.findall(text))
        kana = len(re.findall(r"[\u3040-\u30ff]", text))
        latin = len(re.findall(r"[A-Za-z]", text))
        total = max(len(text), 1)
        if cjk / total >= 0.5:
            return False
        if latin / total >= 0.4:
            return True
        if kana / total >= 0.5:
            return False
        return True