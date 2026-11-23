"""Straight trial-cut recommendation from the visible upper notch and its retained mask.

The upper edge is read column by column as the first sustained run of material
pixels.  A robust straight line through those readings is the panel's upper
edge; a notch is a stretch that sits visibly below it.  A trial cut has to stay
below every notch, may not remove more than the allowed depth from that edge,
and may lean slightly so that more of the panel is kept.
"""

from math import atan, degrees, tan

import numpy as np

from image_lab.validation import Invalid

from . import photo

RUN_ROWS = 4             # consecutive material rows that make a column readable
TAIL_ROWS = 30           # rows inspected to reject busbars and cell gaps
TAIL_SUPPORT = 0.60      # share of those rows that must be material
GAP_FILL = 8             # unreadable columns bridged while grouping a notch
NOISE_FLOOR_PX = 2.0     # smallest elevation accepted as a visible notch
CLEARANCE_MM = 1.0       # material kept between the cut and the notch
MAX_REMOVAL_MM = 12.0    # deepest cut allowed below the panel's upper edge
MAX_TILT_DEG = 5.0       # "slightly tilted" limit relative to that edge
GRID = 401               # candidate tilts across the allowed range
MATERIAL_GAIN_MM = 0.25  # tilt only when it saves at least this much on average
CUT_COLOR = "#ffb347"
IDEA_COLOR = "#6fe39b"
NOTCH_COLOR = "#ff896f"
CLEARANCE_COLOR = "#f6d17d"


def scan(roi, cutoff):
    """First sustained material row and readability for every inspection column."""
    height, width = roi.shape
    if height < RUN_ROWS + 2 or width < 2:
        raise Invalid("检查框太小，无法读取上边缘。")
    bright = roi >= cutoff
    run = bright[:height - RUN_ROWS + 1].copy()
    for offset in range(1, RUN_ROWS):
        run &= bright[offset:offset + height - RUN_ROWS + 1]
    present = run.any(axis=0)
    first = np.where(present, run.argmax(axis=0), 0)
    cumulative = np.vstack([np.zeros((1, width), np.int32), np.cumsum(bright, axis=0)])
    columns = np.arange(width)
    start = np.clip(first, 0, height)
    stop = np.minimum(height, start + TAIL_ROWS)
    support = np.where(stop > start, (cumulative[stop, columns] - cumulative[start, columns])
                       / np.maximum(stop - start, 1), 0.0)
    readable = present & (support >= TAIL_SUPPORT) & (first < height * .75)
    depth = np.where(readable, first, np.nan).astype(float)
    return depth, readable


def _fit(depth, readable):
    """Least-squares straight upper edge over the readable columns."""
    columns = np.arange(len(depth), dtype=float)
    middle = (len(depth) - 1) / 2
    if readable.sum() < 2:
        return None, 0.0
    design = np.vstack([np.ones(int(readable.sum())), columns[readable] - middle]).T
    intercept, slope = np.linalg.lstsq(design, depth[readable], rcond=None)[0]
    return intercept + slope * (columns - middle), float(slope)


def _baseline(depth, readable):
    """Robust straight fit of the panel's upper edge, ignoring notches and gaps."""
    inliers = readable.copy()
    edge, slope = _fit(depth, inliers)
    if edge is None:
        return None, 0.0, inliers
    for _ in range(5):
        residual = depth - edge
        values = residual[inliers]
        spread = 1.4826 * np.median(np.abs(values - np.median(values)))
        keep = readable & (residual <= max(1.5, 3.0 * spread))
        if keep.sum() < 3 or keep.sum() == inliers.sum():
            break
        inliers = keep
        edge, slope = _fit(depth, inliers)
        if edge is None:
            return None, 0.0, readable
    edge, slope = _fit(depth, inliers)
    return edge, slope, inliers


def _bridge(values, cells):
    """Interpolate short unreadable gaps, leaving long gaps unusable."""
    known = np.isfinite(values)
    if not known.any():
        return values.copy(), known
    filled = np.interp(np.arange(len(values)), np.flatnonzero(known), values[known])
    bridge = np.zeros(len(values), bool)
    index = 0
    while index < len(values):
        if known[index]:
            index += 1
            continue
        start = index
        while index < len(values) and not known[index]:
            index += 1
        if index - start <= cells:
            bridge[start:index] = True
    return filled, known | bridge


def _notches(depth, edge, readable):
    """Column stretches that sit visibly below the fitted upper edge."""
    elevation = depth - edge
    filled, usable = _bridge(elevation, GAP_FILL)
    inliers = readable & np.isfinite(elevation)
    spread = 0.0
    if inliers.any():
        values = elevation[inliers]
        spread = 1.4826 * np.median(np.abs(values - np.median(values)))
    threshold = max(NOISE_FLOOR_PX, 4.0 * spread)
    raised = usable & (filled > threshold)
    closed = raised.copy()
    closed[1:-1] |= raised[:-2] & raised[2:]
    # An unreadable stretch inside a notch (cell gap, busbar) cannot prove material,
    # so a notch continues over it instead of splitting in two.
    index = 0
    while index < len(closed):
        if closed[index] or usable[index]:
            index += 1
            continue
        start = index
        while index < len(closed) and not closed[index] and not usable[index]:
            index += 1
        if (start > 0 and index < len(closed) and closed[start - 1] and closed[index]
                and index - start <= GAP_FILL * 2):
            closed[start:index] = True
    groups = []
    for start, stop in _spans(closed):
        visible = [column for column in range(start, stop + 1) if raised[column]]
        left, right = visible[0], visible[-1]
        segment = np.where(raised[left:right + 1], filled[left:right + 1], -np.inf)
        peak = float(segment.max())
        if peak <= threshold:
            continue
        groups.append((left, right, left + int(segment.argmax()), peak))
    return np.where(usable, filled, np.nan), closed & usable, groups, threshold


def _spans(mask):
    spans, index = [], 0
    while index < len(mask):
        if not mask[index]:
            index += 1
            continue
        start = index
        while index < len(mask) and mask[index]:
            index += 1
        spans.append((start, index - 1))
    return spans


def _candidate(edge, signal, inside, tilt, limit, height, middle):
    """Shallowest cut at this tilt inside the material and below every notch."""
    columns = np.arange(len(signal), dtype=float)
    lift = tilt * (columns - middle)
    offset = float((signal - lift)[inside].max())
    removal = offset + lift
    top = edge + removal
    if removal.min() < 0 or removal.max() > limit:
        return None
    if top.min() < 0 or top.max() > height - 1:
        return None
    return offset, removal, float(removal.mean())


def _clean(value, digits=2):
    rounded = round(float(value), digits)
    return 0.0 if rounded == 0 else rounded


def recommend(view, settings):
    """One straight cut, or the reason the twelve millimetre limit rules it out."""
    depth, readable = scan(view.roi, settings["dark_cutoff"])
    mm = view.mm
    result = {"endpoints": None, "angle": 0.0, "edge_angle": 0.0, "mean_removal": 0.0,
              "max_removal": 0.0, "min_clearance": 0.0, "clearance_at": None,
              "level_mean": 0.0, "gain": 0.0, "preference": "none", "reason": "",
              "notches": [], "limit": MAX_REMOVAL_MM, "tolerance": NOISE_FLOOR_PX * mm}
    if readable.sum() < max(8, len(depth) * .15):
        result["reason"] = "上沿可读段太少，无法定位缺口与上沿。"
        return result
    edge, slope, _ = _baseline(depth, readable)
    if edge is None:
        result["reason"] = "上沿可读段太少，无法定位缺口与上沿。"
        return result
    result["edge_angle"] = _clean(degrees(atan(slope)))
    elevation, notch_mask, groups, threshold = _notches(depth, edge, readable)
    result["tolerance"] = _clean(max(NOISE_FLOOR_PX, threshold) * mm)
    width = len(depth)
    height = view.roi.shape[0]
    middle = (width - 1) / 2
    clearance = max(2.0, CLEARANCE_MM / mm) if mm > 0 else 2.0
    limit = MAX_REMOVAL_MM / mm if mm > 0 else MAX_REMOVAL_MM
    inside = (readable | notch_mask) & np.isfinite(elevation)
    signal = np.where(inside, np.maximum(elevation, clearance), 0.0)
    signal = np.where(notch_mask & np.isfinite(elevation), elevation + clearance, signal)

    notches = []
    for left, right, peak_column, peak in groups:
        top = int(round(np.nanmin(edge[left:right + 1])))
        bottom = int(round(edge[peak_column] + peak)) + 1
        notches.append({"left": int(left), "right": int(right), "peak_column": int(peak_column),
                        "depth_mm": round(peak * mm, 2), "width_mm": round((right - left + 1) * mm, 2),
                        "box": [int(view.region[0] + left), int(view.region[1] + top),
                                int(view.region[0] + right + 1), int(view.region[1] + bottom)],
                        "at": [int(view.region[0] + (left + right) // 2),
                               int(view.region[1] + edge[(left + right) // 2] + peak / 2)]})
    result["notches"] = notches
    if not notches:
        result["reason"] = "检查框上沿没有看到可见缺口，这一趟不需要试裁。"
        return result

    deepest = max(notch["depth_mm"] for notch in notches)
    if deepest + CLEARANCE_MM > MAX_REMOVAL_MM:
        result["reason"] = (f"缺口最深 {deepest:.2f} mm，裁到缺口内侧至少超过 "
                            f"{MAX_REMOVAL_MM:g} mm 上限，先不推荐。")
        return result

    level = _candidate(edge, signal, inside, 0.0, limit, height, middle)
    if level is None:
        result["reason"] = "缺口太深，裁到缺口内侧会超过 12 mm 上限，先不推荐。"
        return result
    cap = tan(MAX_TILT_DEG * np.pi / 180)
    best = None
    for tilt in np.linspace(-cap, cap, GRID):
        candidate = _candidate(edge, signal, inside, tilt, limit, height, middle)
        if candidate is None:
            continue
        key = (round(candidate[2], 9), round(abs(float(tilt)), 9))
        if best is None or key < best[0]:
            best = (key, float(tilt), candidate)
    if best is None:
        result["reason"] = ("检查框里找不到既落在全部缺口内侧、又不超过 "
                            f"{MAX_REMOVAL_MM:g} mm 的直线，先不推荐。")
        return result

    _, tilt, (offset, removal, mean_removal) = best
    _, _, level_mean = level
    gain = (level_mean - mean_removal) * mm
    if gain < MATERIAL_GAIN_MM:
        tilt, (offset, removal, mean_removal), gain = 0.0, level, 0.0
    notch_columns = np.flatnonzero(notch_mask)
    gaps = (edge + removal - depth)[notch_columns]
    nearest = int(notch_columns[int(np.nanargmin(gaps))])
    minimum_clearance = float(np.nanmin(gaps))
    deepest_column = int(np.argmax(removal))
    left, top, right, _ = view.region
    cut_top = edge + removal + top
    stands = [float(cut_top[0]), float(cut_top[-1])]
    size = view.size
    endpoints = [[round(left / (size[0] - 1), 6), round(stands[0] / (size[1] - 1), 6)],
                 [round((right - 1) / (size[0] - 1), 6), round(stands[1] / (size[1] - 1), 6)]]
    angle = _cut_angle(view, endpoints)
    result.update({
        "endpoints": endpoints, "angle": _clean(angle), "mean_removal": _clean(mean_removal * mm),
        "max_removal": _clean(float(removal.max()) * mm),
        "min_clearance": _clean(minimum_clearance * mm),
        "clearance_at": [int(left + nearest), int(round(cut_top[nearest]))],
        "level_mean": _clean(level_mean * mm), "gain": _clean(gain),
        "deepest_at": int(left + deepest_column),
        "preference": "material" if tilt else "clearance",
    })
    if tilt:
        side = "左端" if deepest_column < width / 2 else "右端"
        result["reason"] = (f"偏向多留料：相对上沿斜 {abs(angle):.1f}°，平均少去掉 {gain:.2f} mm；"
                            f"缺口近旁余量压到 {minimum_clearance * mm:.2f} mm，最深处在{side}。")
    else:
        result["reason"] = (f"偏向缺口余量：与上沿平行，缺口近旁留 {minimum_clearance * mm:.2f} mm，"
                            f"最深处 {float(removal.max()) * mm:.2f} mm；斜裁省料不足 "
                            f"{MATERIAL_GAIN_MM:g} mm，故不倾斜。")
    return result


def _cut_columns(view, cut):
    """Per-inspection-column cut row, used for both display and the retained mask."""
    width, height = view.size
    (ax, ay), (bx, by) = [photo.point(point, width, height) for point in cut]
    if ax == bx:
        raise Invalid("试裁线需要横向跨过检查框。")
    left, _, right, _ = view.region
    columns = np.arange(left, right)
    return ay + (by - ay) * (columns - ax) / (bx - ax)


def _cut_angle(view, cut):
    """The cut's tilt in image geometry, degrees clockwise from horizontal."""
    width, height = view.size
    (ax, ay), (bx, by) = [photo.point(point, width, height) for point in cut]
    if ax == bx:
        raise Invalid("试裁线需要横向跨过检查框。")
    return degrees(atan((by - ay) / (bx - ax)))


def retained(view, cut):
    """Boolean mask of the inspection pixels kept below an adopted cut."""
    if not cut:
        return None
    _, top, _, bottom = view.region
    line = _cut_columns(view, cut)
    rows = np.arange(top, bottom)
    kept = rows[:, None] >= line[None, :]
    if not kept.any():
        raise Invalid("试裁线压在检查框下沿，已没有剩余片区。")
    return kept


def evaluate(view, cut, cutoff):
    """Removal, tilt and clearance of an adopted cut, for re-opened readings."""
    depth, readable = scan(view.roi, cutoff)
    edge, slope, _ = _baseline(depth, readable)
    mm = view.mm
    line = _cut_columns(view, cut)
    angle = _cut_angle(view, cut)
    _, top, _, _ = view.region
    if edge is None:
        return {"angle": _clean(angle), "mean": None, "max": None, "clearance": None}
    removal = line - (edge + top)
    _, notch_mask, _, _ = _notches(depth, edge, readable)
    gaps = (line - (depth + top))[notch_mask & np.isfinite(depth)]
    return {"angle": _clean(angle - degrees(atan(slope))),
            "mean": _clean(float(np.mean(removal)) * mm),
            "max": _clean(float(np.max(removal)) * mm),
            "clearance": _clean(float(np.min(gaps)) * mm) if gaps.size else None}


def line_mark(view, cut, color, label):
    """Original-pixel endpoints of a cut line for the overlay."""
    width, height = view.size
    return {"type": "line", "points": [list(photo.point(point, width, height)) for point in cut],
            "color": color, "label": label}


def report(view, settings):
    """Cut recommendation plus the metrics, rows and marks the edge check shows."""
    result = recommend(view, settings)
    return result, edge_metrics(result), edge_rows(view, result, settings), edge_marks(view, result, settings)


def edge_metrics(result):
    metrics = [{"label": "可见缺口", "value": len(result["notches"]), "units": ""}]
    if result["endpoints"] is None:
        metrics.append({"label": "试裁建议", "value": "暂不推荐", "units": ""})
        return metrics
    metrics.extend([
        {"label": "建议倾角", "value": result["angle"], "units": "°"},
        {"label": "平均去掉", "value": result["mean_removal"], "units": "mm"},
        {"label": "最深处", "value": result["max_removal"], "units": "mm"},
        {"label": "缺口最小余量", "value": result["min_clearance"], "units": "mm"},
    ])
    return metrics


def edge_rows(view, result, settings):
    rows = []
    left, _, _, _ = view.region
    for index, notch in enumerate(result["notches"], 1):
        x0, _, x1, _ = notch["box"]
        rows.append({"label": f"缺口 {index}",
                     "value": f"深 {notch['depth_mm']} mm · 宽 {notch['width_mm']} mm",
                     "detail": f"原片列 {x0}–{x1 - 1} · 最深列 {left + notch['peak_column']}",
                     "at": notch["at"]})
    if result["endpoints"] is None:
        rows.append({"label": "试裁建议", "value": "暂不推荐", "detail": result["reason"], "at": None})
    else:
        rows.append({"label": "建议试裁线",
                     "value": f"倾角 {result['angle']}° · 平均去掉 {result['mean_removal']} mm",
                     "detail": result["reason"], "at": result["clearance_at"]})
    adopted = settings.get("cut")
    if adopted:
        measured = evaluate(view, adopted, settings["dark_cutoff"])
        rows.append({"label": "已采用裁线",
                     "value": f"相对上沿倾角 {measured['angle']}°",
                     "detail": "暗区面积按剩余片区计" if measured["mean"] is None else
                               f"平均去掉 {measured['mean']} mm · 最深 {measured['max']} mm · "
                               f"缺口近旁余量 {measured['clearance']} mm",
                     "at": None})
    return rows


def edge_marks(view, result, settings):
    marks = []
    for index, notch in enumerate(result["notches"], 1):
        x0, y0, x1, y1 = notch["box"]
        marks.append({"type": "box", "points": [[x0, y0], [x1, y1]], "color": NOTCH_COLOR,
                      "label": f"缺口 {index}"})
    if result["endpoints"] is not None:
        marks.append(line_mark(view, result["endpoints"], IDEA_COLOR, "建议试裁线"))
        if result["clearance_at"]:
            column, cut_row = result["clearance_at"]
            notch_row = min(notch["box"][3] - 1 for notch in result["notches"]
                            if notch["box"][0] <= column < notch["box"][2])
            marks.append({"type": "line", "points": [[column, notch_row], [column, cut_row]],
                          "color": CLEARANCE_COLOR, "label": "缺口余量"})
    return marks
