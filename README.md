# pdffanyi — 高精度 PDF 翻译工具

基于 pixiv_illust-illustrationCatch-master/others/pdfTranslate 开源原型重构，用于把日文绘画教程 PDF 翻译成简体中文，保留版式与图片（描图级覆盖重绘）。

## 功能特性

### 核心功能
- **高精度 OCR**: Tesseract 5 + tessdata_best 高精度模型，支持日文/中文/竖排文字识别
- **智能过滤**: 自动识别并跳过插画区、对话气泡、版权水印、软件界面截图等不需要翻译的内容
- **多引擎翻译**: 支持 Google/Youdao(免费)、LLM/OpenCode(付费高质量) 四种翻译引擎
- **上下文翻译**: LLM/OpenCode 引擎支持保留最近 36 行译文作为上下文，保持前后文连贯
- **背景擦除重绘**: 智能采样背景色擦除原文，自动检测文字颜色(白字/黑字)，在原位置绘制译文
- **断点续传**: 每完成一页自动保存进度，中断后重新运行可跳过已完成页继续翻译

### 智能特性
- **横竖排自动识别**: 自动检测文字方向，横排/竖排文字分别处理
- **字号自动适配**: 根据原文大小自动调整译文字号，过长文字自动换行或缩字号
- **文字颜色检测**: 根据背景亮度自动选择白色或黑色文字，确保可读性
- **低置信度修复**: 对置信度低但包含CJK字符的长行，裁图放大重识别提高准确率
- **兜底机制**: 主引擎连续失败自动切换到备用引擎，确保翻译完成

## 依赖安装

```bash
# 系统依赖 (Termux)
pkg install -y poppler tesseract python-numpy freetype

# tesseract 语言包 (tessdata_best 版, 放至 $PREFIX/share/tessdata/):
#   jpn / jpn_vert / chi_sim / chi_sim_vert
#   来源: https://github.com/tesseract-ocr/tessdata_best

# Python 依赖
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple pillow openai requests
```

## 快速开始

```bash
cd ~/projects/AiSmallTools/pdffanyi

# 交互式运行 (选择引擎和输入key)
python3 -m pdf_translate.pipeline 输入.pdf -o 输出.pdf

# 使用免费 Google 引擎
python3 -m pdf_translate.pipeline 输入.pdf --engine google -o 输出.pdf

# 使用高质量 LLM 引擎 (需要API key)
python3 -m pdf_translate.pipeline 输入.pdf --engine llm \
  --api-key sk-xxxx --base-url https://api.deepseek.com \
  --model deepseek-v4-flash -o 输出.pdf

# 只翻译指定页码范围
python3 -m pdf_translate.pipeline 输入.pdf --start 1 --end 10

# 调试模式 (保存调试图)
python3 -m pdf_translate.pipeline 输入.pdf --debug
```

## 参数说明

| 参数 | 默认值 | 说明 |
|---|---|---|
| `-o, --output` | `输入名_translated.pdf` | 输出PDF路径 |
| `--start / --end` | 全部页 | 页码范围(从1开始) |
| `--dpi` | 216 | 渲染分辨率, 越高OCR越准但越慢 |
| `--lang` | `jpn+chi_sim` | OCR语言包 |
| `--psm` | 11 | tesseract版面模式(11=稀疏文本) |
| `--debug` | 关 | 保存调试图和中间文件 |
| `--fresh` | 关 | 强制重新翻译(忽略断点) |
| `--engine` | `google` | 翻译引擎(google/youdao/llm/opencode) |
| `--api-key` | `.env.local` | LLM API密钥 |
| `--base-url` | `.env.local` | LLM API地址 |
| `--model` | `.env.local` | LLM模型名 |
| `--fallback` | `opencode` | 主引擎失败后的兜底引擎 |

## 配置文件

项目根目录 `.env.local`(已gitignore)保存LLM配置:

```bash
LLM_API_KEY=sk-xxxxxxxx
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-flash
LLM_TEMPERATURE=0.2
LLM_REQUEST_GAP=1.2
LLM_FALLBACK_ENGINE=opencode
```

## 翻译引擎对比

| 引擎 | 费用 | 速度 | 质量 | 上下文 |
|---|---|---|---|---|
| Google | 免费 | 快 | 一般 | 不支持 |
| Youdao | 免费 | 快 | 一般 | 不支持 |
| LLM (DeepSeek) | 付费 | 慢 | 最好 | 支持 |
| OpenCode | 免费 | 慢 | 好 | 支持 |

## 工作流程

```
PDF → 渲染图片 → OCR识别 → 智能过滤 → 翻译 → 背景擦除 → 绘制译文 → 合成PDF
```

1. **渲染**: 使用 poppler 将 PDF 每页转为 PNG 图片
2. **OCR**: 使用 Tesseract 识别文字位置和内容
3. **过滤**: 智能识别插画区/对话气泡/水印等不翻译内容
4. **翻译**: 调用翻译引擎将日文翻译为中文
5. **擦除**: 采样背景色, 将原文像素替换为背景色
6. **绘制**: 在原位置用中文字体绘制译文
7. **合成**: 将所有页面图片合成为 PDF

## 已知限制

- 输出为整页图片PDF(不可选中复制文字,体积较大)
- 花体装饰字/手绘标注类文字OCR识别率低
- 纯图片页无文字框则原样透传
- 免费引擎不支持上下文翻译,一词多义可能翻错

## 目录结构

```
pdffanyi/
├── config.py                    # 配置(阈值/字体/字号/引擎)
├── .env.local                   # AI key(本地,不进git)
├── requirements.txt             # Python依赖
├── 使用文档.md                  # 详细使用文档
├── README.md                    # 本文件
├── tests/                       # 测试
└── pdf_translate/
    ├── __init__.py              # 包初始化
    ├── ocr_engine.py            # OCR引擎(Tesseract TSV识别)
    ├── layout.py                # 文本行布局(区域合并/横竖排分组)
    ├── page_filter.py           # 页面过滤(插画/水印/对话气泡)
    ├── translator.py            # 翻译引擎(google/youdao/llm/opencode)
    ├── renderer.py              # 渲染器(背景擦除/文字颜色检测/译文绘制)
    ├── pdfwriter.py             # PDF生成(图片合成PDF)
    └── pipeline.py              # 主流程+CLI(断点续传/交互式选择)
```

## 代码注释

所有模块的函数都添加了详细的中文注释,可以在 Acode 等代码编辑器中查看:
- `translator.py`: 翻译器类、各引擎实现、批量翻译、上下文翻译
- `pipeline.py`: PDF翻译主流程、断点续传、OCR修复
- `renderer.py`: 背景色采样、前景擦除、文字颜色检测、字号适配、横竖排绘制
- `ocr_engine.py`: OCR区域、Tesseract引擎、TSV解析、单词合并
- `layout.py`: 文本行合并规则、重叠比例计算、水平间距计算
- `pdfwriter.py`: PDF生成原理

## 许可证

MIT License
