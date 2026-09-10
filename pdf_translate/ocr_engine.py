import os
import subprocess
import tempfile
from shutil import which

import numpy as np


class OCRRegion:
    """OCR识别结果区域: 包含文本、位置、置信度等信息

    每个OCRRegion代表Tesseract识别出的一个文本区域,
    包含识别的文本内容、在图片中的位置坐标、置信度分数等。
    """

    def __init__(self, text, poly, score, words=None):
        """初始化OCR区域

        Args:
            text: 识别出的文本内容
            poly: 多边形坐标 [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
                  (四边形的四个顶点, 通常是左上、右上、右下、左下)
            score: 置信度分数 (0-100, 越高越可信)
            words: 单词列表 [(text, box, conf), ...]
        """
        self.text = text  # 识别出的文本
        self.poly = np.asarray(poly, dtype=np.float32)  # 多边形坐标
        self.score = float(score)  # 置信度分数
        self.words = words or []  # 单词列表

        # 从多边形坐标计算边界框
        xs = self.poly[:, 0]  # 所有x坐标
        ys = self.poly[:, 1]  # 所有y坐标
        self.x_min = int(xs.min())  # 左边界
        self.y_min = int(ys.min())  # 上边界
        self.x_max = int(xs.max())  # 右边界
        self.y_max = int(ys.max())  # 下边界
        self.width = self.x_max - self.x_min  # 宽度
        self.height = self.y_max - self.y_min  # 高度
        self.center_y = (self.y_min + self.y_max) // 2  # 垂直中心点

    def is_vertical(self):
        """判断是否为竖排文本

        竖排文本的特征: 高度明显大于宽度 (高度 > 宽度 * 1.5)
        """
        return self.height > self.width * 1.5

    def area(self):
        """计算区域面积 (宽度 * 高度)"""
        return self.width * self.height

    def bg_unique_colors(self, img_arr, pad=8):
        """统计文字框周围背景的唯一颜色数

        用于判断文字是否在插画/照片区域内:
        - 颜色数少 (< 50): 可能是纯色背景 (正文区域)
        - 颜色数多 (> 150): 可能是插画/照片区域

        Args:
            img_arr: 图片numpy数组 (H, W, 3)
            pad: 向外扩展的像素数

        Returns:
            唯一颜色数量
        """
        h, w = img_arr.shape[:2]
        # 计算扩展后的区域 (但不超出图片边界)
        x0 = max(self.x_min - pad, 0)
        x1 = min(self.x_max + pad, w)
        y0 = max(self.y_min - pad, 0)
        y1 = min(self.y_max + pad, h)
        # 提取区域内的所有像素
        win = img_arr[y0:y1, x0:x1].reshape(-1, 3)
        # 像素太少则返回0
        if win.shape[0] < 16:
            return 0
        # 统计唯一颜色数
        return int(np.unique(win, axis=0).shape[0])


class TesseractOCR:
    """Tesseract OCR引擎: 识别图片中的文字

    使用Tesseract-OCR引擎识别图片中的日文/中文文本,
    输出TSV格式结果, 包含每个单词的位置和置信度。
    """

    def __init__(self, lang="jpn+chi_sim", psm=11):
        """初始化OCR引擎

        Args:
            lang: 语言包 (jpn=日文, chi_sim=简体中文, 多语言用+连接)
            psm: 页面分割模式
                 11 = 稀疏文本 (无特定排列, 适合画册)
                 3 = 全自动分割
                 6 = 假设为统一的文本块
        """
        self.lang = lang
        self.psm = psm

    def recognize(self, image_path, dpi=216):
        """识别图片中的文字

        调用Tesseract命令行工具进行OCR识别,
        输出TSV格式结果后解析为OCRRegion列表。

        Args:
            image_path: 图片路径 (PNG/JPG)
            dpi: 图片分辨率 (影响识别精度)

        Returns:
            OCRRegion 列表 (识别出的所有文本区域)
        """
        # 检查tesseract是否安装
        if not which("tesseract"):
            raise RuntimeError("tesseract not installed: pkg install tesseract")

        # 创建临时目录存放TSV输出
        with tempfile.TemporaryDirectory() as tmp:
            base = os.path.join(tmp, "out")
            # 调用tesseract命令行
            subprocess.run(
                [
                    "tesseract", image_path, base,  # 输入图片, 输出基础路径
                    "-l", self.lang,  # 语言包
                    "--psm", str(self.psm),  # 页面分割模式
                    "--dpi", str(dpi),  # 分辨率
                    "tsv",  # 输出格式 (TSV)
                ],
                check=False,
                capture_output=True,
            )
            # 检查TSV文件是否生成
            tsv_path = base + ".tsv"
            if not os.path.exists(tsv_path):
                return []
            # 解析TSV文件
            return self._parse_tsv(tsv_path)

    @staticmethod
    def _parse_tsv(tsv_path):
        """解析Tesseract TSV输出文件

        TSV格式包含以下列:
        - level: 层级 (5=单词级别)
        - block_num: 文本块编号
        - line_num: 行编号
        - word_num: 单词编号
        - text: 识别出的文本
        - conf: 置信度 (-1表示未识别)
        - left, top, width, height: 位置和尺寸

        Args:
            tsv_path: TSV文件路径

        Returns:
            OCRRegion 列表
        """
        regions = []  # 结果列表
        pending_key = None  # 当前行的标识 (block_num, line_num)
        builder = None  # 当前行的构建器

        with open(tsv_path, encoding="utf-8") as f:
            # 读取表头
            header = f.readline().rstrip("\n").split("\t")
            for row in f:
                cols = row.rstrip("\n").split("\t")
                # 跳过列数不足的行
                if len(cols) < 12:
                    continue
                # 将列数据转换为字典
                record = dict(zip(header, cols))
                # 只处理单词级别 (level=5) 的数据
                if record.get("level") != "5":
                    continue
                text = record.get("text", "")
                # 解析数值字段
                try:
                    conf = float(record.get("conf", "-1"))  # 置信度
                    left = int(float(record.get("left", 0)))  # 左边界
                    top = int(float(record.get("top", 0)))  # 上边界
                    width = int(float(record.get("width", 0)))  # 宽度
                    height = int(float(record.get("height", 0)))  # 高度
                except ValueError:
                    continue
                # 跳过无效数据 (空文本/低置信度/太小)
                if not text.strip() or conf < 0 or width < 2 or height < 2:
                    continue

                box = (left, top, width, height)
                # 行标识: (块编号, 行编号)
                key = (record.get("block_num"), record.get("line_num"))

                # 新行: 保存上一行, 创建新构建器
                if builder is None or key != pending_key:
                    if builder is not None:
                        regions.append(builder.to_region())
                    builder = _RegionBuilder(text, box, conf)
                    pending_key = key
                else:
                    # 同一行: 添加到当前构建器
                    builder.add(text, box, conf)
                # 记录单词信息
                builder.words.append((text, box, conf))

        # 保存最后一行
        if builder is not None:
            regions.append(builder.to_region())
        return regions


class _RegionBuilder:
    """区域构建器: 将同一行的多个单词合并为一个OCRRegion

    Tesseract输出的是单个单词级别的识别结果,
    _RegionBuilder将同一行的单词合并, 形成完整的文本行。
    """

    def __init__(self, text, box, conf):
        """初始化区域构建器

        Args:
            text: 第一个单词的文本
            box: 位置 (left, top, width, height)
            conf: 置信度
        """
        self.parts = [(text, box, conf)]  # 所有单词的列表
        self.words = []  # 单词详情 (用于后续处理)

    def add(self, text, box, conf):
        """添加一个单词到当前行"""
        self.parts.append((text, box, conf))

    def to_region(self):
        """将构建器转换为OCRRegion对象

        合并所有单词的:
        - 文本: 按顺序拼接
        - 位置: 计算包含所有单词的最小矩形
        - 置信度: 按文本长度加权平均

        Returns:
            OCRRegion 对象
        """
        xs = []  # 所有x坐标
        ys = []  # 所有y坐标
        text = ""  # 拼接后的文本
        total_w = 0  # 总字符数 (用于加权)
        conf_sum = 0.0  # 加权置信度总和
        min_conf = 1.0  # 最小置信度

        for t, (left, top, width, height), c in self.parts:
            # 收集坐标
            xs.extend([left, left + width])
            ys.extend([top, top + height])
            # 拼接文本
            text += t
            # 计算加权置信度 (长单词权重更高)
            w = max(len(t), 1)
            total_w += w
            conf_sum += c * w
            min_conf = min(min_conf, c)

        # 构建四边形坐标 (左上, 右上, 右下, 左下)
        poly = [
            [min(xs), min(ys)],  # 左上
            [max(xs), min(ys)],  # 右上
            [max(xs), max(ys)],  # 右下
            [min(xs), max(ys)],  # 左下
        ]

        # 计算加权平均置信度
        score = conf_sum / total_w if total_w else min_conf

        # 创建OCRRegion对象
        region = OCRRegion(text, poly, score, words=self.words)
        region.min_word_conf = min_conf  # 记录最小单词置信度
        return region