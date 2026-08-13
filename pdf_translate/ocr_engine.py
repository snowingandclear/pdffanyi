import os
import subprocess
import tempfile
from shutil import which

import numpy as np


class OCRRegion:
    def __init__(self, text, poly, score):
        self.text = text
        self.poly = np.asarray(poly, dtype=np.float32)
        self.score = float(score)
        xs = self.poly[:, 0]
        ys = self.poly[:, 1]
        self.x_min = int(xs.min())
        self.y_min = int(ys.min())
        self.x_max = int(xs.max())
        self.y_max = int(ys.max())
        self.width = self.x_max - self.x_min
        self.height = self.y_max - self.y_min
        self.center_y = (self.y_min + self.y_max) // 2

    def is_vertical(self):
        return self.height > self.width * 1.5

    def area(self):
        return self.width * self.height

    def is_illustration_text(self):
        """插图/装饰文字特征：日文字符占比低且混入大量拉丁/符号"""
        import re

        text = self.text
        if not text:
            return True
        cjk = len(re.findall(r"[\u3040-\u30ff\u3400-\u9fff]", text))
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


class TesseractOCR:
    def __init__(self, lang="jpn+chi_sim", psm=11):
        self.lang = lang
        self.psm = psm

    def recognize(self, image_path, dpi=216):
        if not which("tesseract"):
            raise RuntimeError("tesseract not installed: pkg install tesseract")
        with tempfile.TemporaryDirectory() as tmp:
            base = os.path.join(tmp, "out")
            subprocess.run(
                [
                    "tesseract", image_path, base,
                    "-l", self.lang,
                    "--psm", str(self.psm),
                    "--dpi", str(dpi),
                    "tsv",
                ],
                check=False,
                capture_output=True,
            )
            tsv_path = base + ".tsv"
            if not os.path.exists(tsv_path):
                return []
            return self._parse_tsv(tsv_path)

    @staticmethod
    def _parse_tsv(tsv_path):
        regions = []
        pending_key = None
        builder = None
        with open(tsv_path, encoding="utf-8") as f:
            header = f.readline().rstrip("\n").split("\t")
            for row in f:
                cols = row.rstrip("\n").split("\t")
                if len(cols) < 12:
                    continue
                record = dict(zip(header, cols))
                if record.get("level") != "5":
                    continue
                text = record.get("text", "")
                try:
                    conf = float(record.get("conf", "-1"))
                    left = int(float(record.get("left", 0)))
                    top = int(float(record.get("top", 0)))
                    width = int(float(record.get("width", 0)))
                    height = int(float(record.get("height", 0)))
                except ValueError:
                    continue
                if not text.strip() or conf < 0 or width < 2 or height < 2:
                    continue
                box = (left, top, width, height)
                key = (record.get("block_num"), record.get("line_num"))
                if builder is None or key != pending_key:
                    if builder is not None:
                        regions.append(builder.to_region())
                    builder = _RegionBuilder(text, box, conf)
                    pending_key = key
                else:
                    builder.add(text, box, conf)
        if builder is not None:
            regions.append(builder.to_region())
        return regions


class _RegionBuilder:
    def __init__(self, text, box, conf):
        self.parts = [(text, box, conf)]

    def add(self, text, box, conf):
        self.parts.append((text, box, conf))

    def to_region(self):
        xs = []
        ys = []
        text = ""
        min_conf = 1.0
        for t, (left, top, width, height), c in self.parts:
            xs.extend([left, left + width])
            ys.extend([top, top + height])
            text += t
            min_conf = min(min_conf, c)
        poly = [
            [min(xs), min(ys)],
            [max(xs), min(ys)],
            [max(xs), max(ys)],
            [min(xs), max(ys)],
        ]
        return OCRRegion(text, poly, min_conf)