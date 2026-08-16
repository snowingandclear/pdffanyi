import os
import subprocess
import sys
import tempfile
from shutil import which

from PIL import Image
import numpy as np

from pdf_translate.layout import group_lines
from pdf_translate.ocr_engine import OCRRegion, TesseractOCR
from pdf_translate.page_filter import PageFilter
from pdf_translate.pdfwriter import make_pdf
from pdf_translate.renderer import Renderer
from pdf_translate.translator import Translator


def _prompt_engine(config):
    """stdin 为 TTY 时交互式选择翻译引擎, 并输入各 AI 引擎的 key。"""
    while True:
        print("选择翻译引擎:")
        print("  1) google      免费, 无需 key")
        print("  2) youdao      免费, 无需 key")
        print("  3) llm         OpenAI 兼容 API (如 DeepSeek)")
        print("  4) opencode    本机 opencode serve")
        try:
            choice = input("请输入编号 (回车默认 3): ").strip()
        except EOFError:
            return {}
        if choice == "":
            choice = "3"
        if choice in ("1", "2", "3", "4"):
            break
        print("无效输入, 请重试")
    kwargs = {"engine": {"1": "google", "2": "youdao", "3": "llm", "4": "opencode"}[choice]}
    if choice in ("1", "2"):
        return kwargs
    if choice == "3":
        default_key = getattr(config, "LLM_API_KEY", "")
        if default_key:
            print(f"检测到 .env.local 已有 key (……{default_key[-4:]})")
        key = input("AI API Key (回车使用已有配置, 留空随后回退 google): ").strip()
        if not key and default_key:
            key = default_key
        base = input(
            f"base_url (回车: {getattr(config, 'LLM_BASE_URL', 'https://api.deepseek.com')}): "
        ).strip()
        model = input(
            f"model (回车: {getattr(config, 'LLM_MODEL', 'deepseek-v4-flash')}): "
        ).strip()
        kwargs["api_key"] = key
        kwargs["base_url"] = base or getattr(config, "LLM_BASE_URL", "")
        kwargs["model"] = model or getattr(config, "LLM_MODEL", "")
    elif choice == "4":
        port = input("opencode serve 地址 (回车: 127.0.0.1:4096): ").strip()
        if port:
            kwargs["opencode_url"] = (
                port if port.startswith("http") else f"http://{port}"
            )
    return kwargs


class Pipeline:
    def __init__(self, config, ocr=None, translator=None, page_filter=None):
        self.config = config
        self.ocr = ocr or TesseractOCR()
        if translator is None:
            engine = getattr(config, "TRANSLATE_ENGINE", "google")
            kwargs = {
                "engine": engine,
                "api_key": getattr(config, "LLM_API_KEY", ""),
                "base_url": getattr(config, "LLM_BASE_URL", ""),
                "model": getattr(config, "LLM_MODEL", ""),
                "temperature": getattr(config, "LLM_TEMPERATURE", 0.2),
                "fallback_engine": getattr(config, "LLM_FALLBACK_ENGINE", ""),
                "opencode_url": getattr(config, "OPENCODE_URL", ""),
                "opencode_user": getattr(config, "OPENCODE_USER", ""),
                "opencode_pass": getattr(config, "OPENCODE_PASS", ""),
            }
            if engine == "llm":
                kwargs["max_batch_chars"] = getattr(
                    config, "LLM_MAX_BATCH_CHARS", None
                )
                kwargs["max_batch_lines"] = getattr(
                    config, "LLM_MAX_BATCH_LINES", None
                )
            self.translator = Translator(**kwargs)
        else:
            self.translator = translator
        self.page_filter = page_filter or PageFilter(config)

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
                img = Image.open(png_path).convert("RGB")
                regions = self.ocr.recognize(png_path, dpi=dpi)
                regions = self._rescue_low_conf(regions, img, dpi)
                img_arr = np.asarray(img)
                decision = self.page_filter.apply(img_arr, regions, page_no + 1)
                kept = decision.kept
                stats = decision.stats
                stats_str = ", ".join(
                    f"{k}={v[0]}/{v[1]}" for k, v in stats.items()
                )
                print(
                    f"[page {page_no + 1}] {len(regions)} boxes, "
                    f"kept {len(kept)} (插画页={decision.is_illustration_page}, "
                    f"{stats_str})",
                    flush=True,
                )
                lines = group_lines(kept)
                if lines:
                    print(f"[page {page_no + 1}] translating {len(lines)} lines...", flush=True)
                    translated = self.translator.translate_lines([line.text for line in lines])
                    missing = sum(1 for zh in translated if not zh)
                    if missing:
                        print(
                            f"[page {page_no + 1}] 警告: {missing}/{len(lines)} 行翻译失败, "
                            f"该页将保留原文",
                            flush=True,
                        )
                    renderer = Renderer(img)
                    for line, zh in zip(lines, translated):
                        if not zh:
                            continue
                        if debug:
                            print(f"    {line.text}  =>  {zh}")
                        fill = renderer.text_color(
                            line.x_min, line.y_min, line.x_max, line.y_max
                        )
                        text_height = line.text_height
                        measured = renderer.measure_text_size(
                            line.x_min, line.y_min, line.x_max, line.y_max,
                            vertical=line.vertical,
                        )
                        if measured:
                            text_height = measured
                        words = [
                            w for r in line.regions for w in r.words
                        ]
                        renderer.erase(
                            line.x_min, line.y_min, line.x_max, line.y_max,
                            words=words,
                        )
                        if line.vertical:
                            renderer.draw_vertical(zh, line.x_min, line.y_min, line.x_max, line.y_max, fill=fill, text_height=text_height)
                        else:
                            renderer.draw_horizontal(zh, line.x_min, line.y_min, line.x_max, line.y_max, fill=fill, text_height=text_height)
                    renderer.sync()
                    img = renderer.image
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

    def _rescue_low_conf(self, regions, img, dpi):
        """低置信度长 CJK 行兜底: 裁出放大 2 倍重识别, 文本更可信则替换。

        整行分数因个别单字被拉低时, 重识别常能救回该行
        (沿用原框几何, 只替换文本与分数)。
        """
        import re as _re

        cjk_re = _re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
        out = []
        rescued = 0
        for r in regions:
            if r.score >= self.config.OCR_CONFIDENCE_THRESHOLD:
                out.append(r)
                continue
            text = r.text.strip()
            if not text or len(text) < 8:
                out.append(r)
                continue
            if len(cjk_re.findall(text)) / len(text) < 0.5:
                out.append(r)
                continue
            crop = img.crop((r.x_min, r.y_min, r.x_max, r.y_max))
            crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                crop_path = f.name
            crop.save(crop_path)
            try:
                resc = self.ocr.recognize(crop_path, dpi=dpi * 2)
            finally:
                os.remove(crop_path)
            better = [
                x for x in resc
                if len(x.text) >= len(text) * 0.8
                and x.score > r.score
            ]
            if better:
                best = max(better, key=lambda x: x.score)
                new_region = OCRRegion(
                    best.text, r.poly.tolist(), best.score, words=best.words
                )
                new_region.min_word_conf = min(r.min_word_conf, best.min_word_conf)
                out.append(new_region)
                rescued += 1
                print(
                    f"[page] 重识别救回 1 行: {best.score:.0f}: {best.text[:30]}",
                    flush=True,
                )
            else:
                out.append(r)
        if rescued:
            print(f"[page] 低分行重识别救回 {rescued} 行", flush=True)
        return out

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
    parser.add_argument(
        "--engine",
        default=None,
        choices=["google", "youdao", "llm", "opencode"],
        help="翻译引擎 (覆盖 config.TRANSLATE_ENGINE)",
    )
    parser.add_argument(
        "--api-key", default=None, help="AI 引擎 API Key (覆盖 .env.local)"
    )
    parser.add_argument(
        "--base-url", default=None, help="AI 引擎 OpenAI 兼容接口地址"
    )
    parser.add_argument("--model", default=None, help="AI 引擎模型名")
    parser.add_argument(
        "--fallback",
        default=None,
        choices=["opencode", "none"],
        help="主引擎失败后的兜底引擎 (默认 opencode)",
    )
    parser.add_argument(
        "--opencode-url", default=None, help="opencode serve 地址"
    )
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"input not found: {args.input}")
        sys.exit(1)
    output = args.output or os.path.splitext(args.input)[0] + "_translated.pdf"
    if os.path.isdir(output):
        output = os.path.join(
            output, os.path.splitext(os.path.basename(args.input))[0] + "_translated.pdf"
        )
    pages = None
    if args.start or args.end:
        pages = (args.start or 1, args.end or 10 ** 9)
    ocr = TesseractOCR(lang=args.lang, psm=args.psm)
    kwargs = {}
    interactive = False
    try:
        interactive = sys.stdin.isatty()
    except Exception:
        interactive = False
    if interactive and not (
        args.engine
        or args.api_key
        or args.base_url
        or args.model
        or args.fallback
        or args.opencode_url
    ):
        kwargs.update(_prompt_engine(config) or {})
    if args.engine:
        kwargs["engine"] = args.engine
    if args.api_key:
        kwargs["api_key"] = args.api_key
        kwargs.setdefault("engine", getattr(config, "TRANSLATE_ENGINE", "llm"))
    if args.base_url or (args.api_key and getattr(config, "LLM_BASE_URL", "")):
        kwargs["base_url"] = args.base_url or getattr(config, "LLM_BASE_URL", "")
    if args.model:
        kwargs["model"] = args.model
    if args.fallback:
        kwargs["fallback_engine"] = (
            args.fallback if args.fallback != "none" else ""
        )
    if args.opencode_url:
        kwargs["opencode_url"] = args.opencode_url
    if kwargs.get("engine") == "llm":
        kwargs["max_batch_chars"] = getattr(config, "LLM_MAX_BATCH_CHARS", None)
        kwargs["max_batch_lines"] = getattr(config, "LLM_MAX_BATCH_LINES", None)
    translator = Translator(**kwargs) if kwargs else None
    pipeline = Pipeline(config, ocr, translator=translator)
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