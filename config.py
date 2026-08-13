import os

OCR_CONFIDENCE_THRESHOLD = 0.55
RENDER_DPI = 216
FONT_SIZE_MIN = 10
FONT_SIZE_MAX = 96
LINE_PADDING = 2
MIN_TEXT_COLOR_GAP = 40

FONT_CANDIDATES = [
    "/system/fonts/HarmonyOS_Sans.ttf",
    "/system/fonts/HarmonyOS_Sans_Condensed.ttf",
    "/system/fonts/DroidSansFallback.ttf",
    "/system/fonts/NotoSansCJK-Regular.ttc",
    "/system/fonts/NotoSansSC-Regular.otf",
    "/data/data/com.termux/files/usr/share/fonts/TTF/DejaVuSans.ttf",
]