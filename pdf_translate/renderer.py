import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import config


class FontManager:
    """字体管理器: 自动查找并缓存中文字体

    在Android系统上自动查找可用的中文字体,
    支持Noto Sans CJK、HarmonyOS Sans等常见字体。
    """

    def __init__(self):
        """初始化字体管理器, 自动查找可用的中文字体

        遍历config.FONT_CANDIDATES中列出的字体路径,
        测试是否能正常渲染中文字符, 找到第一个可用的即停止。
        """
        self.font_path = None  # 找到的字体路径
        for candidate in config.FONT_CANDIDATES:
            # 检查字体文件是否存在
            if not os.path.exists(candidate):
                continue
            try:
                # 尝试加载字体并测试中文渲染
                font = ImageFont.truetype(candidate, 32)
                # 测试"测中"两个字的宽度是否大于0 (证明能渲染)
                if font.getbbox("测中")[2] - font.getbbox("测中")[0] > 0:
                    self.font_path = candidate
                    break
            except Exception:
                continue
        self._cache = {}  # 字体缓存 {size: Font}

    def get_font(self, size):
        """获取指定大小的字体 (带缓存)

        Args:
            size: 字号大小

        Returns:
            ImageFont 对象
        """
        if size not in self._cache:
            try:
                self._cache[size] = ImageFont.truetype(self.font_path, size)
            except Exception:
                # 字体加载失败, 使用默认字体
                self._cache[size] = ImageFont.load_default()
        return self._cache[size]


class Renderer:
    """渲染器: 负责背景擦除和译文绘制

    主要功能:
    1. 采样背景色: 取文本框边缘像素的中位数作为背景色
    2. 擦除原文: 将文字像素替换为背景色
    3. 检测文字颜色: 根据背景亮度决定使用白色或黑色文字
    4. 绘制译文: 支持横排和竖排文字, 自动调整字号
    """

    def __init__(self, image):
        """初始化渲染器

        Args:
            image: PIL Image 对象 (RGB模式)
        """
        self.image = image
        # 将图片转换为numpy数组, 方便像素级操作
        self.arr = np.array(image.convert("RGB"), dtype=np.uint8)
        self.fonts = FontManager()  # 字体管理器

    def sync(self):
        """同步numpy数组到PIL Image

        在修改self.arr后调用, 更新self.image。
        """
        self.arr = np.asarray(self.arr, dtype=np.uint8)
        self.image = Image.fromarray(self.arr)

    @staticmethod
    def _sample_bg(arr, x_min, y_min, x_max, y_max, pad=6):
        """采样背景色: 取文本框边缘像素的中位数

        通过采样文本框周围的像素来推断背景颜色,
        使用中位数而非平均值, 可以避免文字像素的干扰。

        Args:
            arr: 图片numpy数组
            x_min, y_min, x_max, y_max: 文本框边界
            pad: 向外扩展的像素数

        Returns:
            RGB元组 (R, G, B)
        """
        h, w = arr.shape[:2]
        samples = []  # 采样像素列表
        # 计算扩展后的边界
        left = max(x_min - pad, 0)
        right = min(x_max + pad, w - 1)
        top = max(y_min - pad, 0)
        bottom = min(y_max + pad, h - 1)

        # 采样左右两条边的像素
        for x in (left, right):
            for y in range(top, min(bottom, y_min + max(y_max - y_min, 1) + pad)):
                if 0 <= x < w and 0 <= y < h:
                    samples.append(arr[y, x])
        # 采样上下两条边的像素
        for y in (top, bottom):
            for x in range(left, min(right, x_min + max(x_max - x_min, 1) + pad)):
                if 0 <= x < w and 0 <= y < h:
                    samples.append(arr[y, x])

        if not samples:
            return (255, 255, 255)  # 默认白色
        # 返回中位数作为背景色
        return tuple(int(np.median(samples, axis=0)[i]) for i in range(3))

    def erase(self, x_min, y_min, x_max, y_max, words=None):
        """擦除文字区域: 用背景色填充

        Args:
            x_min, y_min, x_max, y_max: 文本框边界
            words: 单词列表 (未使用, 保留接口)
        """
        # 采样背景色
        bg = self._sample_bg(self.arr, x_min, y_min, x_max, y_max)
        # 扩展1像素确保完全覆盖
        pad = 1
        bx0 = max(x_min - pad, 0)
        by0 = max(y_min - pad, 0)
        bx1 = min(x_max + pad, self.arr.shape[1] - 1)
        by1 = min(y_max + pad, self.arr.shape[0] - 1)
        # 擦除前景
        self._erase_foreground(bx0, by0, bx1, by1, bg)

    def _is_text_box(self, x0, y0, x1, y1, bg, max_fore_ratio=0.35):
        """判断区域是否为文字框

        通过计算前景占比来判断:
        - 前景占比低 (<= 35%): 可能是背景区域
        - 前景占比高 (> 35%): 可能是文字区域

        Args:
            x0, y0, x1, y1: 区域边界
            bg: 背景色RGB元组
            max_fore_ratio: 最大前景占比阈值

        Returns:
            True 表示是背景区域
        """
        # 提取区域像素
        window = self.arr[y0:y1, x0:x1].astype(np.int32)
        # 计算每个像素与背景色的距离 (RGB差值绝对值之和)
        dist = np.abs(window - np.asarray(bg, dtype=np.int32)).sum(axis=2)
        # 前景占比 = 距离大于60的像素比例
        return (dist > 60).mean() <= max_fore_ratio

    def _erase_foreground(self, x0, y0, x1, y1, bg, diff_thresh=60):
        """擦除前景: 将与背景色差异大的像素替换为背景色

        Args:
            x0, y0, x1, y1: 区域边界
            bg: 背景色RGB元组
            diff_thresh: 颜色差异阈值 (RGB差值之和)
        """
        # 提取区域像素
        window = self.arr[y0:y1, x0:x1].astype(np.int32)
        # 计算每个像素与背景色的距离
        dist = np.abs(window - np.asarray(bg, dtype=np.int32)).sum(axis=2)
        # 创建前景掩码 (距离大于阈值的像素)
        mask = dist > diff_thresh
        if not mask.any():
            return  # 没有前景像素
        # 将前景像素替换为背景色
        window[mask] = np.asarray(bg, dtype=np.int32)
        self.arr[y0:y1, x0:x1] = window.astype(np.uint8)

    def text_color(self, x_min, y_min, x_max, y_max):
        """检测文字颜色: 根据背景亮度决定使用白色或黑色文字

        检测逻辑:
        1. 计算背景亮度 (加权平均: 0.299R + 0.587G + 0.114B)
        2. 计算原文区域最暗像素的亮度
        3. 如果背景暗 (亮度<128) 且文字亮, 使用白色文字
        4. 如果背景亮且文字暗, 使用黑色文字
        5. 如果差异小于阈值, 根据背景亮度选择

        Args:
            x_min, y_min, x_max, y_max: 文本框边界

        Returns:
            RGB元组 (白色或黑色)
        """
        import numpy as np

        # 采样背景色并计算亮度
        bg = self._sample_bg(self.arr, x_min, y_min, x_max, y_max)
        bg_lum = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]

        # 提取原文区域像素
        arr = self.arr.astype(np.int32)
        interior = arr[y_min:y_max, x_min:x_max]
        if interior.size == 0:
            # 空区域: 暗背景用白字, 亮背景用黑字
            return (255, 255, 255) if bg_lum < 128 else (0, 0, 0)

        # 计算区域内每个像素的亮度
        lum = (
            0.299 * interior[:, :, 0]
            + 0.587 * interior[:, :, 1]
            + 0.114 * interior[:, :, 2]
        )
        # 取最暗3%像素的亮度 (排除噪声)
        darkest = float(np.percentile(lum, 3))

        # 判断逻辑
        if darkest > 160 and bg_lum < 128:
            # 文字很亮且背景暗: 用白字
            return (255, 255, 255)
        if bg_lum - darkest < config.MIN_TEXT_COLOR_GAP:
            # 亮度差异小: 暗背景用白字, 亮背景用黑字
            return (255, 255, 255) if bg_lum < 128 else (0, 0, 0)
        # 默认黑字
        return (0, 0, 0)

    def measure_text_size(self, x_min, y_min, x_max, y_max, vertical=False):
        """测量原文墨迹范围: 用于确定译文字号

        通过检测文字像素来确定原文的实际大小,
        作为译文字号的参考基准。

        Args:
            x_min, y_min, x_max, y_max: 文本框边界
            vertical: 是否为竖排文本

        Returns:
            横排返回高度, 竖排返回单列宽度, 失败返回None
        """
        """测量原文本墨迹范围: 横排=高度, 竖排=单列宽度, 作为译文字号基准."""
        # 采样背景色
        bg = self._sample_bg(self.arr, x_min, y_min, x_max, y_max)
        # 提取区域像素
        window = self.arr[y_min:y_max, x_min:x_max].astype(np.int32)
        if window.size == 0:
            return None
        # 计算像素与背景色的距离
        dist = np.abs(window - np.asarray(bg, dtype=np.int32)).sum(axis=2)
        # 创建文字掩码 (距离大于60的像素)
        mask = dist > 60

        if vertical:
            # 竖排: 统计每列的文字像素数
            colcnt = mask.sum(axis=0)
            # 找到有文字的列
            cols = np.where(colcnt >= 2)[0]
            if not len(cols):
                return None
            # 将连续的列分组, 取最宽的一组
            runs = np.split(cols, np.where(np.diff(cols) > 1)[0] + 1)
            w = max(run[-1] - run[0] + 1 for run in runs)
            return w if w <= x_max - x_min else None
        else:
            # 横排: 统计每行的文字像素数
            rowcnt = mask.sum(axis=1)
            # 找到有文字的行
            rows = np.where(rowcnt >= 2)[0]
            if not len(rows):
                return None
            # 返回文字区域的高度
            h = rows[-1] - rows[0] + 1
            return h if h <= y_max - y_min else None

    def _ink_metrics(self, font):
        """获取字体的墨迹尺寸 (高度和宽度)

        通过渲染"测"字来测量字体的实际显示尺寸。

        Args:
            font: ImageFont 对象

        Returns:
            (高度, 宽度) 元组
        """
        # 获取字体的边界框 (anchor="la" 表示左上角对齐)
        bbox = font.getbbox("测", anchor="la")
        return max(bbox[3] - bbox[1], 1), max(bbox[2] - bbox[0], 1)

    def _fit_font(self, text, box_w, box_h, text_height=None):
        """自动调整字号使文字适应框大小

        从参考字号开始, 逐步减小字号直到文字能放入框中。

        Args:
            text: 待绘制的文字
            box_w: 框宽度
            box_h: 框高度
            text_height: 参考字号 (从原文测量)

        Returns:
            (font, lines) 元组
        """
        start = text_height or box_h  # 初始字号
        font_size = max(min(int(start), config.FONT_SIZE_MAX), config.FONT_SIZE_MIN)

        while True:
            font = self.fonts.get_font(font_size)
            ink_h, _ = self._ink_metrics(font)

            # 测试是否需要换行
            lines = [text]
            if font.getlength(text) > box_w:
                lines = self._wrap(text, font, box_w)

            # 计算总高度
            total_h = len(lines) * ink_h + max(len(lines) - 1, 0) * 2
            # 检查是否适应
            fits = total_h <= box_h and font.getlength(text) <= box_w
            if fits or font_size <= config.FONT_SIZE_MIN:
                return font, lines
            # 字号太大, 减小1
            font_size -= 1

    def draw_horizontal(self, text, x_min, y_min, x_max, y_max, fill=(0, 0, 0), text_height=None):
        """绘制横排文字

        Args:
            text: 待绘制的文字
            x_min, y_min, x_max, y_max: 目标区域边界
            fill: 文字颜色 (RGB元组)
            text_height: 参考字号
        """
        box_w = max(x_max - x_min, 1)  # 框宽度
        box_h = max(y_max - y_min, 1)  # 框高度

        # 自动调整字号
        font, lines = self._fit_font(text, box_w, box_h, text_height)
        ink_h, _ = self._ink_metrics(font)
        line_h = ink_h + 2  # 行高 (墨迹高度 + 2像素间距)

        # 计算总高度并垂直居中
        total_h = len(lines) * ink_h + max(len(lines) - 1, 0) * 2
        y = y_min + max((box_h - total_h) // 2, 0)

        # 复制区域像素到临时图片
        tile = self.arr[y_min:y_max, x_min:x_max].copy()
        tmp = Image.fromarray(tile)
        d = ImageDraw.Draw(tmp)

        # 逐行绘制
        for line in lines:
            # 获取文字边界框 (用于定位)
            lb = font.getbbox(line, anchor="la")
            d.text(
                (-lb[0], y - y_min - lb[1]),  # 相对于临时图片的坐标
                line, fill=fill, font=font, anchor="la",
            )
            y += line_h

        # 写回原图
        self.arr[y_min:y_max, x_min:x_max] = np.asarray(tmp)

    def draw_vertical(self, text, x_min, y_min, x_max, y_max, fill=(0, 0, 0), text_height=None):
        """绘制竖排文字

        竖排文字从上到下、从右到左排列,
        自动调整字号和列数以适应框大小。

        Args:
            text: 待绘制的文字
            x_min, y_min, x_max, y_max: 目标区域边界
            fill: 文字颜色 (RGB元组)
            text_height: 参考字号
        """
        box_w = max(x_max - x_min, 1)  # 框宽度
        box_h = max(y_max - y_min, 1)  # 框高度
        chars = list(text)  # 将文字拆分为单个字符

        start = text_height or box_w  # 初始字号
        font_size = max(min(int(start), config.FONT_SIZE_MAX), config.FONT_SIZE_MIN)

        # 自动调整字号
        while True:
            font = self.fonts.get_font(font_size)
            ink_h, ink_w = self._ink_metrics(font)
            col_h = ink_h + 2  # 列高 (墨迹高度 + 2像素间距)
            cols = max(int(box_h // col_h), 1)  # 每列字符数
            n_cols = (len(chars) + cols - 1) // cols  # 总列数
            fits = n_cols * (ink_w + 2) <= box_w  # 检查宽度是否适应
            if fits or font_size <= config.FONT_SIZE_MIN:
                break
            font_size -= 1

        # 获取最终字体参数
        font = self.fonts.get_font(font_size)
        ink_h, ink_w = self._ink_metrics(font)
        col_h = ink_h + 2
        cols = max(int(box_h // col_h), 1)
        n_cols = (len(chars) + cols - 1) // cols
        total_w = n_cols * (ink_w + 2) - 2

        # 从左到右逐列绘制
        col_x = x_min
        tile = self.arr[y_min:y_max, x_min:x_max].copy()
        tmp = Image.fromarray(tile)
        d = ImageDraw.Draw(tmp)

        for start_idx in range(0, len(chars), cols):
            column = chars[start_idx : start_idx + cols]  # 当前列的字符
            # 计算列的总高度并垂直居中
            total_h = len(column) * ink_h + max(len(column) - 1, 0) * 2
            y = y_min + max((box_h - total_h) // 2, 0)

            # 从上到下逐字符绘制
            for ch in column:
                cb = font.getbbox(ch, anchor="la")
                d.text(
                    (col_x - x_min - cb[0], y - y_min - cb[1]),
                    ch, fill=fill, font=font, anchor="la",
                )
                y += col_h

            col_x += ink_w + 2  # 移动到下一列

        # 写回原图
        self.arr[y_min:y_max, x_min:x_max] = np.asarray(tmp)

    def _wrap(self, text, font, box_w):
        """文字自动换行

        逐字符检测宽度, 超过框宽度时换行。

        Args:
            text: 待换行的文字
            font: ImageFont 对象
            box_w: 框宽度

        Returns:
            换行后的文字列表
        """
        lines = []
        current = ""  # 当前行
        for ch in text:
            probe = current + ch
            # 如果加上新字符后宽度超限, 且当前行不为空
            if font.getlength(probe) <= box_w or not current:
                current = probe
            else:
                lines.append(current)
                current = ch
        if current:
            lines.append(current)
        return lines