# pdfTran — 高精度 PDF 翻译工具

基于 pixiv_illust-illustrationCatch-master/others/pdfTranslate 开源原型重构，用于把日文绘画教程 PDF 翻译成简体中文，保留版式与图片（描图级覆盖重绘）。

## 特性（相比原版的高精度改进）

- OCR：Tesseract 5 + tessdata_best 高精度模型（jpn/chi_sim，以及 jpn_vert 竖排）
- 置信度过滤：低置信度文本块自动丢弃，避免"翻译乱码"
- 同行合并：按行分组后整体翻译，避免逐块翻译导致的断句
- 批量翻译：有道翻译免费接口（无需 API key）+ 批量调用 + 编号对位解析
- 背景采样擦除：按文本框边缘采样背景色填充，白字彩底也能干净覆盖，并自动检测文字颜色（白字/黑字）与背景匹配
- 智能排版：自动字号适配、自动换行、垂直文本竖排渲染（译文过长自动缩字号防越界）
- 翻页断点：`--start/--end` 只翻译指定页；`--debug` 输出调试图

## 依赖安装

```bash
pkg install -y poppler tesseract python-numpy freetype
# tesseract 语言包（已实测 jpn、chi_sim、jpn_vert、chi_sim_vert 均为 tessdata_best 版）：
#  放至 $PREFIX/share/tessdata/，来源 https://github.com/tesseract-ocr/tessdata_best
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple pillow openai requests
```

## 使用

```bash
cd ~/projects/AiSmallTools/pdffanyi
python3 -m pdf_translate.pipeline 输入.pdf -o 输出.pdf --debug
python3 -m pdf_translate.pipeline 输入.pdf --start 5 --end 10
```

参数：

| 参数 | 说明 |
|---|---|
| `-o` | 输出路径，默认 `输入名_translated.pdf` |
| `--start/--end` | 页码范围（从 1 开始），先试 1~2 页 |
| `--dpi` | 渲染分辨率，默认 216，越高 OCR 越准但越慢 |
| `--lang` | OCR 语言，默认 `jpn+chi_sim`，中文书用 `chi_sim+jpn` |
| `--psm` | tesseract 版面模式，默认 11（稀疏文本，适合画册） |
| `--debug` | 保存 `_debug_N.png` 检查覆盖效果 |

## 配置

- 翻译：有道免费接口 `https://aidemo.youdao.com/trans`，无需 API key，国内直连可用
- `OCR_CONFIDENCE_THRESHOLD`：0.55，识别置信度门槛

## 实测结果

- ✅ 《数字插画瞳绘画方法》（日文排版书）：第 3 页识别+翻译质量良好（见 tests/）
- ⚠️ 花体装饰字/手绘标注类画集（如《メルヘン…衣装デザインカタログ》）识别率低——该类文字连商用 OCR 也难以处理，属预期限制

## 目录结构

```
pdffanyi/
├── config.py                    # 配置
├── requirements.txt
├── tests/                       # 测试产物（_debug_N.png 调试图、test_output.pdf）
└── pdf_translate/
    ├── ocr_engine.py            # tesseract TSV 解析 OCR 引擎
    ├── layout.py                # 行分组
    ├── translator.py            # DeepSeek 翻译
    ├── renderer.py              # 背景采样擦除 + 排版渲染
    └── pipeline.py              # 主流程 + CLI
```