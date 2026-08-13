class TextLine:
    def __init__(self, regions):
        self.regions = regions
        self.text = "".join(r.text for r in regions)
        self.x_min = min(r.x_min for r in regions)
        self.y_min = min(r.y_min for r in regions)
        self.x_max = max(r.x_max for r in regions)
        self.y_max = max(r.y_max for r in regions)
        self.center_y = (self.y_min + self.y_max) // 2
        self.vertical = regions[0].is_vertical() if regions else False

    def union_with(self, region):
        self.regions.append(region)
        self.text += region.text
        self.x_min = min(self.x_min, region.x_min)
        self.y_min = min(self.y_min, region.y_min)
        self.x_max = max(self.x_max, region.x_max)
        self.y_max = max(self.y_max, region.y_max)
        self.center_y = (self.y_min + self.y_max) // 2

    def size(self):
        return self.x_max - self.x_min, self.y_max - self.y_min


def _overlap_ratio(a_y_min, a_y_max, b_y_min, b_y_max):
    lo = max(a_y_min, b_y_min)
    hi = min(a_y_max, b_y_max)
    if hi <= lo:
        return 0.0
    a_h = max(a_y_max - a_y_min, 1)
    b_h = max(b_y_max - b_y_min, 1)
    return (hi - lo) / max(min(a_h, b_h), 1)


def group_lines(regions):
    regions = sorted(regions, key=lambda r: (r.center_y, r.x_min))
    lines = []
    for region in regions:
        placed = False
        for line in lines:
            if line.vertical != region.is_vertical():
                continue
            ratio = _overlap_ratio(
                region.y_min, region.y_max, line.y_min, line.y_max
            )
            if ratio >= 0.6:
                line.union_with(region)
                placed = True
                break
        if not placed:
            lines.append(TextLine([region]))
    for line in lines:
        line.regions.sort(key=lambda r: (r.y_min, r.x_min))
        line.text = "".join(r.text for r in line.regions)
    vertical_lines = [line for line in lines if line.vertical]
    horizontal_lines = [line for line in lines if not line.vertical]
    vertical_lines.sort(key=lambda l: (l.x_min, l.y_min))
    horizontal_lines.sort(key=lambda l: (l.y_min, l.x_min))
    return horizontal_lines + vertical_lines