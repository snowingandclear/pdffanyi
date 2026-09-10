class TextLine:
    """文本行: 由多个OCR区域合并而成

    OCR识别结果通常是单个词或短语, TextLine将同一行的区域合并,
    形成完整的文本行, 便于后续翻译和绘制。
    """

    def __init__(self, regions):
        """初始化文本行

        Args:
            regions: OCRRegion 列表 (同一行的多个识别区域)
        """
        self.regions = regions  # 组成该行的所有OCR区域
        # 合并所有区域的文本 (按顺序拼接)
        self.text = "".join(r.text for r in regions)
        # 计算整行的边界框 (取所有区域的最小/最大坐标)
        self.x_min = min(r.x_min for r in regions)  # 左边界
        self.y_min = min(r.y_min for r in regions)  # 上边界
        self.x_max = max(r.x_max for r in regions)  # 右边界
        self.y_max = max(r.y_max for r in regions)  # 下边界
        # 行的垂直中心点 (用于排序和合并判断)
        self.center_y = (self.y_min + self.y_max) // 2
        # 判断是否为竖排文本 (取第一个区域的方向)
        self.vertical = regions[0].is_vertical() if regions else False

    def union_with(self, region):
        """合并一个OCR区域到当前行

        当新区域与当前行纵向重叠足够大且水平间距足够小时,
        将其合并到当前行。
        """
        self.regions.append(region)  # 添加新区域
        self.text += region.text  # 追加文本
        # 更新边界框 (扩展到包含新区域)
        self.x_min = min(self.x_min, region.x_min)
        self.y_min = min(self.y_min, region.y_min)
        self.x_max = max(self.x_max, region.x_max)
        self.y_max = max(self.y_max, region.y_max)
        # 重新计算垂直中心点
        self.center_y = (self.y_min + self.y_max) // 2

    def size(self):
        """获取行的宽高 (宽度, 高度)"""
        return self.x_max - self.x_min, self.y_max - self.y_min

    @property
    def text_height(self):
        """获取文本高度

        横排文本: 返回行高 (字符高度)
        竖排文本: 返回列宽 (字符宽度)
        用于确定译文的字号大小。
        """
        if self.vertical:
            # 竖排: 取所有区域宽度的最大值 (即字符宽度)
            return max(r.width for r in self.regions)
        # 横排: 取所有区域高度的最大值 (即字符高度)
        return max(r.height for r in self.regions)


def _overlap_ratio(a_y_min, a_y_max, b_y_min, b_y_max):
    """计算两个区域的纵向重叠比例

    用于判断两个OCR区域是否在同一行:
    - 重叠比例 >= 0.6 表示很可能在同一行
    - 重叠比例 < 0.6 表示可能在不同行

    Args:
        a_y_min, a_y_max: 区域A的纵向范围
        b_y_min, b_y_max: 区域B的纵向范围

    Returns:
        重叠比例 (0.0 ~ 1.0)
    """
    # 计算重叠区间
    lo = max(a_y_min, b_y_min)  # 重叠区上界
    hi = min(a_y_max, b_y_max)  # 重叠区下界
    # 无重叠
    if hi <= lo:
        return 0.0
    # 计算两个区域的高度
    a_h = max(a_y_max - a_y_min, 1)  # 区域A高度 (至少1像素)
    b_h = max(b_y_max - b_y_min, 1)  # 区域B高度 (至少1像素)
    # 重叠比例 = 重叠高度 / 较小区域的高度
    return (hi - lo) / max(min(a_h, b_h), 1)


def group_lines(regions):
    """将OCR区域合并为文本行

    合并规则:
    1. 两个区域必须同为横排或同为竖排
    2. 纵向重叠比例 >= 0.6 (在同一水平线上)
    3. 水平间距 <= 0.5倍最大高度 (距离足够近)

    Args:
        regions: OCRRegion 列表

    Returns:
        TextLine 列表 (横排在前, 竖排在后, 各自按位置排序)
    """
    # 按垂直中心点排序 (从上到下), 同一行按水平位置排序 (从左到右)
    regions = sorted(regions, key=lambda r: (r.center_y, r.x_min))
    lines = []  # 已合并的行列表

    for region in regions:
        placed = False  # 当前区域是否已合并到某行
        # 尝试合并到已有的行
        for line in lines:
            # 跳过方向不同的行 (横排 vs 竖排)
            if line.vertical != region.is_vertical():
                continue
            # 计算纵向重叠比例
            ratio = _overlap_ratio(
                region.y_min, region.y_max, line.y_min, line.y_max
            )
            # 计算水平间距
            gap = _horizontal_gap(region, line)
            # 取两个区域中较大的高度作为参考
            max_h = max(region.height, line.y_max - line.y_min)
            # 判断是否满足合并条件:
            # - 重叠比例 >= 0.6 (在同一水平线上)
            # - 水平间距 <= 0.5倍最大高度 (距离足够近, 至少24像素)
            if ratio >= 0.6 and gap <= max(int(0.5 * max_h), 24):
                line.union_with(region)  # 合并到该行
                placed = True
                break
        # 如果没有匹配的行, 创建新行
        if not placed:
            lines.append(TextLine([region]))

    # 重新排序每行内的区域 (按垂直位置, 再按水平位置)
    for line in lines:
        line.regions.sort(key=lambda r: (r.y_min, r.x_min))
        # 重新拼接文本 (确保顺序正确)
        line.text = "".join(r.text for r in line.regions)

    # 分离横排和竖排行, 分别排序后合并
    vertical_lines = [line for line in lines if line.vertical]
    horizontal_lines = [line for line in lines if not line.vertical]
    # 竖排: 从左到右, 从上到下
    vertical_lines.sort(key=lambda l: (l.x_min, l.y_min))
    # 横排: 从上到下, 从左到右
    horizontal_lines.sort(key=lambda l: (l.y_min, l.x_min))

    return horizontal_lines + vertical_lines


def _horizontal_gap(a, b):
    """计算两个区域的水平间距

    对于横排文本: 返回水平方向的距离
    对于竖排文本: 返回垂直方向的距离

    Args:
        a, b: 两个OCR区域 (需有 x_min, x_max, y_min, y_max 属性)

    Returns:
        间距 (像素), 如果有重叠则返回0
    """
    # 计算水平重叠区间
    left = max(a.x_min, b.x_min)
    right = min(a.x_max, b.x_max)
    # 如果有重叠, 间距为0
    if right >= left:
        return 0
    # 竖排文本: 计算垂直间距
    if a.is_vertical():
        return max(a.y_min - b.y_max, b.y_min - a.y_max, 0)
    # 横排文本: 计算水平间距
    return max(a.x_min - b.x_max, b.x_min - a.x_max, 0)