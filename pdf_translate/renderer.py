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
        self._ref = Image.fromarray(self.arr)
        self.draw = ImageDraw.Draw(self._ref)
        self.fonts = FontManager()

    def sync(self):
        self.image.paste(self._ref.convert("RGB"), (0, 0))

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
        boxes = words or [(None, (x_min, y_min, x_max - x_min, y_max - y_min), None)]
        for _w, (left, top, width, height), _c in boxes:
            pad = 1 if words else 0
            bx0 = max(left - pad, 0)
            by0 = max(top - pad, 0)
            bx1 = min(left + width + pad, self.image.width - 1)
            by1 = min(top + height + pad, self.image.height - 1)
            if bx1 <= bx0 or by1 <= by0:
                continue
            if not self._is_text_box(bx0, by0, bx1, by1, bg):
                continue
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

    def draw_horizontal(self, text, x_min, y_min, x_max, y_max, fill=(0, 0, 0)):
        box_w = max(x_max - x_min - config.LINE_PADDING * 2, 1)
        box_h = max(y_max - y_min - config.LINE_PADDING * 2, 1)
        font_size = min(int(box_h), config.FONT_SIZE_MAX)
        font = self.fonts.get_font(font_size)
        while font_size > config.FONT_SIZE_MIN:
            tw = self.draw.textlength(text, font=font)
            if tw <= box_w:
                break
            font_size -= 1
            font = self.fonts.get_font(font_size)
        if self.draw.textlength(text, font=font) > box_w:
            lines = self._wrap(text, font, box_w)
        else:
            lines = [text]
        line_h = max(font_size, 8) + 2
        total_h = len(lines) * line_h
        y = y_min + config.LINE_PADDING + max((box_h - total_h) // 2, 0)
        for line in lines:
            tw = self.draw.textlength(line, font=font)
            x = x_min + config.LINE_PADDING + max((box_w - tw) // 2, 0)
            self.draw.text((x, y), line, fill=fill, font=font)
            y += line_h

    def draw_vertical(self, text, x_min, y_min, x_max, y_max, fill=(0, 0, 0)):
        box_w = max(x_max - x_min - config.LINE_PADDING * 2, 1)
        box_h = max(y_max - y_min - config.LINE_PADDING * 2, 1)
        chars = list(text)
        font_size = int(box_w)
        while font_size > config.FONT_SIZE_MIN:
            font = self.fonts.get_font(font_size)
            col_h = font_size + 4
            cols = max(int(box_h // col_h), 1)
            n_cols = (len(chars) + cols - 1) // cols
            if n_cols * (font_size + 2) - 2 <= box_w:
                break
            font_size -= 1
        font = self.fonts.get_font(font_size)
        col_h = font_size + 4
        cols = max(int(box_h // col_h), 1)
        col_x = x_min + config.LINE_PADDING + (box_w - font_size) // 2
        for start in range(0, len(chars), cols):
            column = chars[start : start + cols]
            y = y_min + config.LINE_PADDING + max((box_h - len(column) * col_h) // 2, 0)
            for ch in column:
                self.draw.text((col_x, y), ch, fill=fill, font=font)
                y += col_h
            col_x += font_size + 2

    def _wrap(self, text, font, box_w):
        lines = []
        current = ""
        for ch in text:
            probe = current + ch
            if self.draw.textlength(probe, font=font) <= box_w or not current:
                current = probe
            else:
                lines.append(current)
                current = ch
        if current:
            lines.append(current)
        return lines