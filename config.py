import os

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

# 指定区域内的文字一律不翻（手动精确框，行框完整包含于区域内才丢弃）。
# 用于自动/插画区检测覆盖不到的情况，例如截图窗口顶部菜单栏、
# 被正文规则误放行的面板整行。按页码配置，无需改代码。
SKIP_REGIONS_MANUAL = {
    12: [(653, 3837, 2769, 4155)],   # 底部 SAI 窗口顶部菜单栏误读行
    13: [(2360, 2099, 3134, 2200)],  # 面板整行误读「符圧:較濃度図サイズ較混色」
}

FONT_CANDIDATES = [
    "/system/fonts/NotoSansCJK-Regular.ttc",
    "/system/fonts/NotoSansSC-Regular.otf",
    "/system/fonts/HarmonyOS_Sans.ttf",
    "/system/fonts/HarmonyOS_Sans_Condensed.ttf",
    "/system/fonts/DroidSansFallback.ttf",
    "/data/data/com.termux/files/usr/share/fonts/TTF/DejaVuSans.ttf",
]