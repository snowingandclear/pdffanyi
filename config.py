import os


def _load_env_local(path=".env.local"):
    """本地密钥文件 (.env.local, 已 gitignore), 仅读取 LLM_ 开头的变量。"""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key.startswith("LLM_") and key not in os.environ:
                os.environ[key] = value.strip()


_load_env_local()

# tesseract TSV 的 conf 是 0-100, 但低置信度行由 rescue/垃圾框规则
# 兜底处理, 这里保持宽松只挡纯噪声
OCR_CONFIDENCE_THRESHOLD = 0.55
RENDER_DPI = 216
FONT_SIZE_MIN = 10
FONT_SIZE_MAX = 96
LINE_PADDING = 2
MIN_TEXT_COLOR_GAP = 40

ART_REGION_ENABLED = True
ART_REGION_STEP = 8
ART_REGION_SAT = 0.22
ART_REGION_MIN_MX = 0.12
ART_REGION_DILATE = 1
ART_REGION_MIN_AREA = 300000
ART_REGION_MAX_RATIO = 0.55
ART_REGIONS_MANUAL = {
    # 12: 右側の途中経過イラスト（「ジジ」など原画内文字が誤読される）
    12: [(2880, 640, 3520, 1640)],
    # 13: 右下の完成イラスト（「信十/」「(し」など原画内文字が誤読される）
    13: [(2552, 2544, 3872, 3680)],
}

# 软件界面截图内的面板/按钮短标签是否翻译。
# False = 截图内文本一律保留日文（只翻书籍正文与插画旁标注）
UI_LABEL_TRANSLATE = False

# 彩色横条上的章节大标题（白字/亮字非白底）翻译条件
BAND_TITLE_MIN_W_RATIO = 0.22
BAND_TITLE_MIN_H = 100
BAND_TITLE_MIN_BRIGHT = 0.18
BAND_TITLE_MIN_CJK_SCORE = 0.45

# 页面背景上的步序/圈注（插画与截图旁边的说明性短标注）翻译条件。
# 行框位于页面白底上、中等宽度，带圈序号（①②…）或动词收尾。
STEP_ANNOTATION_BRIGHT_MIN = 0.72

# 人物对话气泡过滤（插画/线稿区内的对话短句不翻译）。
# 判定：短行(<= DIALOGUE_MAX_LEN)且行宽窄（非正文宽行），
# 且文字周围背景复杂（唯一颜色数 > DIALOGUE_BG_UNIQUE = 插画/照片）。
DIALOGUE_MAX_LEN = 14
DIALOGUE_BG_UNIQUE = 180

# 版权水印过滤（原文中固定的站点水印文字不翻译）。
# 判定：行文本匹配 WATERMARK_TEXT_PATTERNS（正则，部分匹配即跳过），
# 如「素材工坊 www.cgartist.net」。透明描边水印的 OCR 文本通常
# 是汉字+网址混排，文本特征比像素特征可靠（正文浅灰印刷与
# 半透明水印像素特征几乎无法区分，像素法已被否决）。
# 乱码形态（如 WwWW,cdariststcoT1 / .Cgartistnet）由区域跳过
# （见 SKIP_REGIONS_MANUAL 水印带注释）兜底。
WATERMARK_TEXT_PATTERNS = [
    "素材.{0,3}工[房坊]",
    "cgart",
    "z-lib",
    "libgen",
    "www\\.",
]

# 指定区域内的文字一律不翻（手动精确框，行框完整包含于区域内才丢弃）。
# 用于自动/插画区检测覆盖不到的情况，例如截图窗口顶部菜单栏、
# 被正文规则误放行的面板整行。按页码配置，无需改代码。
SKIP_REGIONS_MANUAL = {
    12: [(653, 3837, 2769, 4155)],   # 底部 SAI 窗口顶部菜单栏误读行
    13: [(2360, 2099, 3134, 2200)],  # 面板整行误读「符圧:較濃度図サイズ較混色」
}

# 「素材工坊 www.cgartist.net」站点水印带（216dpi 坐标，全书扫描定位）。
# 水印按页出现：顶部带 y≈290-400、中部装饰带 y≈1560-1700、
# 底部带 y≈2835-2960；行框完整包含于带内即整条跳过。
# 页 3/9/129 三带齐全；页 27/105 仅顶部；页 117 中部+底部。
# （页 130/133 是书内真实网址：作者 YouTube 频道 / KADOKAWA 出版社，
#  不是水印，不跳过。）
WATERMARK_BAND_PAGES = {
    3: [(180, 240, 2140, 470), (180, 1500, 2140, 1730),
        (180, 2760, 2140, 3000)],
    9: [(180, 240, 2140, 470), (180, 1500, 2140, 1730),
        (180, 2760, 2140, 3000)],
    27: [(180, 240, 2140, 470)],
    105: [(180, 240, 2140, 470)],
    117: [(180, 1500, 2140, 1730), (180, 2760, 2140, 3000)],
    129: [(180, 240, 2140, 470), (180, 1500, 2140, 1730),
          (180, 2760, 2140, 3000)],
}

FONT_CANDIDATES = [
    "/system/fonts/NotoSansCJK-Regular.ttc",
    "/system/fonts/NotoSansSC-Regular.otf",
    "/system/fonts/HarmonyOS_Sans.ttf",
    "/system/fonts/HarmonyOS_Sans_Condensed.ttf",
    "/system/fonts/DroidSansFallback.ttf",
    "/data/data/com.termux/files/usr/share/fonts/TTF/DejaVuSans.ttf",
]

# 翻译引擎: google / youdao / llm。
# llm 使用 OpenAI 兼容接口 (DeepSeek、智谱 GLM、阿里百炼等均可),
# 默认指向智谱 GLM-4.7-Flash (免费), 需在 https://open.bigmodel.cn 注册
# 获取 API Key 后通过环境变量 LLM_API_KEY 或此处填入。
TRANSLATE_ENGINE = "llm"
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get(
    "LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"
)
LLM_MODEL = os.environ.get("LLM_MODEL", "glm-4.7-flash")
LLM_TEMPERATURE = 0.2
# LLM 上下文远大于免费接口, 批量可加大: 更大的批量 = 更好的上下文连贯性
LLM_MAX_BATCH_CHARS = 2000
LLM_MAX_BATCH_LINES = 40

# 兜底引擎: 主引擎(如 llm)连续失败后自动切换。默认 opencode (本机 serve)。
# 需先运行 opencode serve (可选 OPENCODE_SERVER_PASSWORD 设密码),
# 翻译用 opencode 配置的默认模型。
LLM_FALLBACK_ENGINE = os.environ.get("LLM_FALLBACK_ENGINE", "opencode")
OPENCODE_URL = os.environ.get("OPENCODE_URL", "http://127.0.0.1:4096")
OPENCODE_USER = os.environ.get("OPENCODE_USER", "opencode")
OPENCODE_PASS = os.environ.get("OPENCODE_SERVER_PASSWORD", "")