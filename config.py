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
ART_REGIONS_MANUAL = {
    # 12: 右側の途中経過イラスト（「ジジ」など原画内文字が誤読される）
    12: [(2880, 640, 3520, 1640)],
    # 13: 右下の完成イラスト（「信十/」「(し」など原画内文字が誤読される）
    13: [(2552, 2544, 3872, 3680)],
}

FONT_CANDIDATES = [
    "/system/fonts/NotoSansCJK-Regular.ttc",
    "/system/fonts/NotoSansSC-Regular.otf",
    "/system/fonts/HarmonyOS_Sans.ttf",
    "/system/fonts/HarmonyOS_Sans_Condensed.ttf",
    "/system/fonts/DroidSansFallback.ttf",
    "/data/data/com.termux/files/usr/share/fonts/TTF/DejaVuSans.ttf",
]