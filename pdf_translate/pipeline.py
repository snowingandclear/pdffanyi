import os
import subprocess
import sys
import tempfile
from shutil import which

from PIL import Image

from pdf_translate.layout import group_lines
from pdf_translate.ocr_engine import TesseractOCR
from pdf_translate.pdfwriter import make_pdf
from pdf_translate.renderer import Renderer
from pdf_translate.translator import Translator


class Pipeline:
    def __init__(self, config, ocr=None, translator=None):
        self.config = config
        self.ocr = ocr or TesseractOCR()
        self.translator = translator or Translator()

    def run(self, pdf_path, output_path, pages=None, dpi=None, debug=False, workdir=None):
        dpi = dpi or self.config.RENDER_DPI
        if not which("pdftoppm"):
            raise RuntimeError("poppler not installed: pkg install poppler")
        total = self._page_count(pdf_path)
        page_indexes = list(range(total))
        if pages:
            start, end = pages
            page_indexes = [i for i in page_indexes if start - 1 <= i <= end - 1]
        workdir = workdir or os.path.dirname(os.path.abspath(output_path)) or "."
        os.makedirs(workdir, exist_ok=True)
        pages_dir = tempfile.mkdtemp(prefix="pdffanyi_", dir=workdir)
        jpegs = []
        try:
            for page_no in page_indexes:
                print(f"\n[page {page_no + 1}/{total}] rendering...", flush=True)
                png_path = self._render_page(pdf_path, page_no + 1, dpi, pages_dir)
                print(f"[page {page_no + 1}] OCR...", flush=True)
                regions = self.ocr.recognize(png_path, dpi=dpi)
                kept = [
                    r for r in regions
                    if r.score >= self.config.OCR_CONFIDENCE_THRESHOLD
                    and r.width >= 4 and r.height >= 4
                    and not r.is_illustration_text()
                ]
                print(f"[page {page_no + 1}] {len(regions)} boxes, kept {len(kept)}", flush=True)
                lines = group_lines(kept)
                img = Image.open(png_path).convert("RGB")
                if lines:
                    print(f"[page {page_no + 1}] translating {len(lines)} lines...", flush=True)
                    translated = self.translator.translate_lines([line.text for line in lines])
                    renderer = Renderer(img)
                    for line, zh in zip(lines, translated):
                        if not zh:
                            continue
                        if debug:
                            print(f"    {line.text}  =>  {zh}")
                        fill = renderer.text_color(
                            line.x_min, line.y_min, line.x_max, line.y_max
                        )
                        renderer.erase(line.x_min, line.y_min, line.x_max, line.y_max)
                        if line.vertical:
                            renderer.draw_vertical(zh, line.x_min, line.y_min, line.x_max, line.y_max, fill=fill)
                        else:
                            renderer.draw_horizontal(zh, line.x_min, line.y_min, line.x_max, line.y_max, fill=fill)
                    if debug:
                        dbg = os.path.join(workdir, f"_debug_{page_no + 1}.png")
                        img.save(dbg)
                        print(f"[page {page_no + 1}] debug: {dbg}")
                jpeg_path = os.path.join(pages_dir, f"p{page_no + 1:04d}.jpg")
                img.save(jpeg_path, format="JPEG", quality=88, dpi=(dpi, dpi))
                jpegs.append(jpeg_path)
                os.remove(png_path)
            make_pdf(jpegs, output_path)
        finally:
            if not debug:
                for jpg in jpegs:
                    try:
                        os.remove(jpg)
                    except OSError:
                        pass
            try:
                os.rmdir(pages_dir)
            except OSError:
                pass
        print(f"\nDONE: {output_path} ({len(jpegs)} pages)")

    @staticmethod
    def _page_count(pdf_path):
        result = subprocess.run(
            ["pdfinfo", pdf_path], capture_output=True, text=True
        )
        for line in result.stdout.splitlines():
            if line.startswith("Pages:"):
                return int(line.split(":")[1].strip())
        return 0

    @staticmethod
    def _render_page(pdf_path, page_num, dpi, out_dir):
        prefix = os.path.join(out_dir, f"p{page_num:04d}")
        subprocess.run(
            [
                "pdftoppm", "-f", str(page_num), "-l", str(page_num),
                "-r", str(dpi), "-png", "-singlefile",
                pdf_path, prefix,
            ],
            check=True,
            capture_output=True,
        )
        return prefix + ".png"


def main():
    import argparse

    import config

    parser = argparse.ArgumentParser(description="High-precision PDF translator")
    parser.add_argument("input", help="input PDF path")
    parser.add_argument("-o", "--output", default=None, help="output PDF path")
    parser.add_argument("--start", type=int, default=None, help="start page (1-based)")
    parser.add_argument("--end", type=int, default=None, help="end page (1-based)")
    parser.add_argument("--dpi", type=int, default=config.RENDER_DPI)
    parser.add_argument("--lang", default="jpn+chi_sim")
    parser.add_argument("--psm", type=int, default=11)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"input not found: {args.input}")
        sys.exit(1)
    output = args.output or os.path.splitext(args.input)[0] + "_translated.pdf"
    pages = None
    if args.start or args.end:
        pages = (args.start or 1, args.end or 10 ** 9)
    pipeline = Pipeline(config, TesseractOCR(lang=args.lang, psm=args.psm))
    pipeline.run(
        args.input,
        output,
        pages=pages,
        dpi=args.dpi,
        debug=args.debug,
        workdir=os.path.dirname(os.path.abspath(output)),
    )


if __name__ == "__main__":
    main()