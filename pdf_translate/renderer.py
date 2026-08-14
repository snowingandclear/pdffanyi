import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import config


class FontManager:
    def __init__(self):
        self.font_path = None
        for candidate in config.FONT_CANDIDATES:
            if not os.path.exists(candidate):
                continue
            try:
                font = ImageFont.truetype(candidate, 32)
                if font.getbbox("测中")[2] - font.getbbox("测中")[0] > 0:
                    self.font_path = candidate
                    break
            except Exception:
                continue
        self._cache = {}

    def get_font(self, size):
        if size not in self._cache:
            try:
                self._cache[size] = ImageFont.truetype(self.font_path, size)
            except Exception:
                self._cache[size] = ImageFont.load_default()
        return self._cache[size]


class Renderer:
    def __init__(self, image):
        self.image = image
        self.arr = np.array(image.convert("RGB"), dtype=np.uint8)
        self.fonts = FontManager()

    def sync(self):
        self.arr = np.asarray(self.arr, dtype=np.uint8)
        self.image = Image.fromarray(self.arr)

    @staticmethod
    def _sample_bg(arr, x_min, y_min, x_max, y_max, pad=6):
        h, w = arr.shape[:2]
        samples = []
        left = max(x_min - pad, 0)
        right = min(x_max + pad, w - 1)
        top = max(y_min - pad, 0)
        bottom = min(y_max + pad, h - 1)
        for x in (left, right):
            for y in range(top, min(bottom, y_min + max(y_max - y_min, 1) + pad)):
                if 0 <= x < w and 0 <= y < h:
                    samples.append(arr[y, x])
        for y in (top, bottom):
            for x in range(left, min(right, x_min + max(x_max - x_min, 1) + pad)):
                if 0 <= x < w and 0 <= y < h:
                    samples.append(arr[y, x])
        if not samples:
            return (255, 255, 255)
        return tuple(int(np.median(samples, axis=0)[i]) for i in range(3))

    def erase(self, x_min, y_min, x_max, y_max, words=None):
        bg = self._sample_bg(self.arr, x_min, y_min, x_max, y_max)
        pad = 1
        bx0 = max(x_min - pad, 0)
        by0 = max(y_min - pad, 0)
        bx1 = min(x_max + pad, self.arr.shape[1] - 1)
        by1 = min(y_max + pad, self.arr.shape[0] - 1)
        self._erase_foreground(bx0, by0, bx1, by1, bg)

    def _is_text_box(self, x0, y0, x1, y1, bg, max_fore_ratio=0.35):
        window = self.arr[y0:y1, x0:x1].astype(np.int32)
        dist = np.abs(window - np.asarray(bg, dtype=np.int32)).sum(axis=2)
        return (dist > 60).mean() <= max_fore_ratio

    def _erase_foreground(self, x0, y0, x1, y1, bg, diff_thresh=60):
        window = self.arr[y0:y1, x0:x1].astype(np.int32)
        dist = np.abs(window - np.asarray(bg, dtype=np.int32)).sum(axis=2)
        mask = dist > diff_thresh
        if not mask.any():
            return
        window[mask] = np.asarray(bg, dtype=np.int32)
        self.arr[y0:y1, x0:x1] = window.astype(np.uint8)

    def text_color(self, x_min, y_min, x_max, y_max):
        import numpy as np

        bg = self._sample_bg(self.arr, x_min, y_min, x_max, y_max)
        bg_lum = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
        arr = self.arr.astype(np.int32)
        interior = arr[y_min:y_max, x_min:x_max]
        if interior.size == 0:
            return (255, 255, 255) if bg_lum < 128 else (0, 0, 0)
        lum = (
            0.299 * interior[:, :, 0]
            + 0.587 * interior[:, :, 1]
            + 0.114 * interior[:, :, 2]
        )
        darkest = float(np.percentile(lum, 3))
        if darkest > 160 and bg_lum < 128:
            return (255, 255, 255)
        if bg_lum - darkest < config.MIN_TEXT_COLOR_GAP:
            return (255, 255, 255) if bg_lum < 128 else (0, 0, 0)
        return (0, 0, 0)

    def measure_text_size(self, x_min, y_min, x_max, y_max, vertical=False):
        """测量原文本墨迹范围: 横排=高度, 竖排=单列宽度, 作为译文字号基准."""
        bg = self._sample_bg(self.arr, x_min, y_min, x_max, y_max)
        window = self.arr[y_min:y_max, x_min:x_max].astype(np.int32)
        if window.size == 0:
            return None
        dist = np.abs(window - np.asarray(bg, dtype=np.int32)).sum(axis=2)
        mask = dist > 60
        if vertical:
            colcnt = mask.sum(axis=0)
            cols = np.where(colcnt >= 2)[0]
            if not len(cols):
                return None
            runs = np.split(cols, np.where(np.diff(cols) > 1)[0] + 1)
            w = max(run[-1] - run[0] + 1 for run in runs)
            return w if w <= x_max - x_min else None
        rowcnt = mask.sum(axis=1)
        rows = np.where(rowcnt >= 2)[0]
        if not len(rows):
            return None
        h = rows[-1] - rows[0] + 1
        return h if h <= y_max - y_min else None

    def _ink_metrics(self, font):
        bbox = font.getbbox("测", anchor="la")
        return max(bbox[3] - bbox[1], 1), max(bbox[2] - bbox[0], 1)

    def _fit_font(self, text, box_w, box_h, text_height=None):
        start = text_height or box_h
        font_size = max(min(int(start), config.FONT_SIZE_MAX), config.FONT_SIZE_MIN)
        while True:
            font = self.fonts.get_font(font_size)
            ink_h, _ = self._ink_metrics(font)
            lines = [text]
            if font.getlength(text) > box_w:
                lines = self._wrap(text, font, box_w)
            total_h = len(lines) * ink_h + max(len(lines) - 1, 0) * 2
            fits = total_h <= box_h and font.getlength(text) <= box_w
            if fits or font_size <= config.FONT_SIZE_MIN:
                return font, lines
            font_size -= 1

    def draw_horizontal(self, text, x_min, y_min, x_max, y_max, fill=(0, 0, 0), text_height=None):
        box_w = max(x_max - x_min, 1)
        box_h = max(y_max - y_min, 1)
        font, lines = self._fit_font(text, box_w, box_h, text_height)
        ink_h, _ = self._ink_metrics(font)
        line_h = ink_h + 2
        total_h = len(lines) * ink_h + max(len(lines) - 1, 0) * 2
        y = y_min + max((box_h - total_h) // 2, 0)
        tile = self.arr[y_min:y_max, x_min:x_max].copy()
        tmp = Image.fromarray(tile)
        d = ImageDraw.Draw(tmp)
        for line in lines:
            lb = font.getbbox(line, anchor="la")
            ink_w = lb[2] - lb[0]
            x = x_min + max((box_w - ink_w) // 2, 0)
            d.text(
                (x - x_min - lb[0], y - y_min - lb[1]),
                line, fill=fill, font=font, anchor="la",
            )
            y += line_h
        self.arr[y_min:y_max, x_min:x_max] = np.asarray(tmp)

    def draw_vertical(self, text, x_min, y_min, x_max, y_max, fill=(0, 0, 0), text_height=None):
        box_w = max(x_max - x_min, 1)
        box_h = max(y_max - y_min, 1)
        chars = list(text)
        start = text_height or box_w
        font_size = max(min(int(start), config.FONT_SIZE_MAX), config.FONT_SIZE_MIN)
        while True:
            font = self.fonts.get_font(font_size)
            ink_h, ink_w = self._ink_metrics(font)
            col_h = ink_h + 2
            cols = max(int(box_h // col_h), 1)
            n_cols = (len(chars) + cols - 1) // cols
            fits = n_cols * (ink_w + 2) <= box_w
            if fits or font_size <= config.FONT_SIZE_MIN:
                break
            font_size -= 1
        font = self.fonts.get_font(font_size)
        ink_h, ink_w = self._ink_metrics(font)
        col_h = ink_h + 2
        cols = max(int(box_h // col_h), 1)
        n_cols = (len(chars) + cols - 1) // cols
        total_w = n_cols * (ink_w + 2) - 2
        col_x = x_min + max((box_w - total_w) // 2, 0)
        tile = self.arr[y_min:y_max, x_min:x_max].copy()
        tmp = Image.fromarray(tile)
        d = ImageDraw.Draw(tmp)
        for start_idx in range(0, len(chars), cols):
            column = chars[start_idx : start_idx + cols]
            total_h = len(column) * ink_h + max(len(column) - 1, 0) * 2
            y = y_min + max((box_h - total_h) // 2, 0)
            for ch in column:
                cb = font.getbbox(ch, anchor="la")
                d.text(
                    (col_x - x_min - cb[0], y - y_min - cb[1]),
                    ch, fill=fill, font=font, anchor="la",
                )
                y += col_h
            col_x += ink_w + 2
        self.arr[y_min:y_max, x_min:x_max] = np.asarray(tmp)

    def _wrap(self, text, font, box_w):
        lines = []
        current = ""
        for ch in text:
            probe = current + ch
            if font.getlength(probe) <= box_w or not current:
                current = probe
            else:
                lines.append(current)
                current = ch
        if current:
            lines.append(current)
        return lines