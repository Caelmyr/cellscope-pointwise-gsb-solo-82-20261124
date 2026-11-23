"""EL brightness, contour, grid, busbar and one-segment trace measurements."""

from math import atan2, degrees, hypot, pi

import cv2
import numpy as np

from image_lab.validation import Invalid

from . import model, photo

TURN_LIMIT = 45.0   # 续接候选允许的最大转向（度）
TURN_WEIGHT = 0.12  # 综合代价中每度转向折算的毫米数


def metric(label, value, units=""):
    return {"label": label, "value": value, "units": units}


def record(label, value, detail="", at=None):
    return {"label": label, "value": value, "detail": detail, "at": at}


def plot(label, samples):
    return {"label": label, "samples": samples}


def line(coords, color, label=""):
    return {"type": "line", "points": coords, "color": color, "label": label}


def rectangle(coords, color, label=""):
    return {"type": "box", "points": coords, "color": color, "label": label}


def polygon(coords, color, label=""):
    return {"type": "polygon", "points": coords, "color": color, "label": label}


def compact(value, digits=3):
    return round(float(value), digits)


def grid(view, settings):
    x0, y0, x1, y1 = view.region
    x_edges = np.rint(np.linspace(x0, x1, settings["columns"] + 1)).astype(int)
    y_edges = np.rint(np.linspace(y0, y1, settings["rows"] + 1)).astype(int)
    if min(np.min(np.diff(x_edges)), np.min(np.diff(y_edges))) < 3:
        raise Invalid("检查框过窄，无法按当前行列数拆分。")
    return [([int(left), int(top), int(right), int(bottom)], f"R{r + 1} · C{c + 1}")
            for r, (top, bottom) in enumerate(zip(y_edges, y_edges[1:]))
            for c, (left, right) in enumerate(zip(x_edges, x_edges[1:]))]


def cell_map(view, settings):
    rows, marks, means = [], [], []
    for (x0, y0, x1, y1), name in grid(view, settings):
        inset = max(1, min(x1 - x0, y1 - y0) // 18)
        pixels = view.values[y0 + inset:y1 - inset, x0 + inset:x1 - inset]
        median = compact(np.median(pixels))
        dim = compact(np.mean(pixels < settings["dark_cutoff"]) * 100, 1)
        means.append(median)
        rows.append(record(name, median, f"暗像素 {dim}% · 检查框内缩 {inset}px",
                           [(x0 + x1) // 2, (y0 + y1) // 2]))
        marks.append(rectangle([[x0, y0], [x1, y1]], "#dbbd73", name))
    stats = [metric("电池片", len(rows)), metric("最低中位灰度", min(means)),
             metric("最高中位灰度", max(means))]
    return stats, rows, marks, None


def regions(view, settings, bright=False):
    pixels = view.roi
    mask = pixels >= settings["hot_cutoff"] if bright else pixels < settings["dark_cutoff"]
    count, labels, stats, centers = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    x0, y0, _, _ = view.region
    minimum = settings["min_area"]
    accepted = sorted((index for index in range(1, count) if stats[index, cv2.CC_STAT_AREA] >= minimum),
                      key=lambda index: (-stats[index, cv2.CC_STAT_AREA], index))
    total = sum(int(stats[index, cv2.CC_STAT_AREA]) for index in accepted)
    rows, marks = [], []
    color = "#f6d17d" if bright else "#ff896f"
    for ordinal, index in enumerate(accepted[:80], 1):
        left, top, width, height, area = map(int, stats[index])
        x, y = left + x0, top + y0
        center = [round(float(centers[index, 0])) + x0, round(float(centers[index, 1])) + y0]
        local_labels = labels[top:top + height, left:left + width]
        eligible = (local_labels == index).astype(np.uint8)
        contours, _ = cv2.findContours(eligible, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        outline = max(contours, key=cv2.contourArea).reshape(-1, 2)
        reduce = max(1, (len(outline) + 259) // 260)
        coords = [[int(px) + x, int(py) + y] for px, py in outline[::reduce]]
        label = f"{'亮斑' if bright else '暗区'} {ordinal}"
        marks.append(polygon(coords, color, label) if len(coords) > 2
                     else rectangle([[x, y], [x + width, y + height]], color, label))
        rows.append(record(label, compact(area * view.mm**2, 2), f"mm² · {area}px", center))
    metrics = [metric("达最小面积的区域", len(accepted)), metric("总面积", compact(total * view.mm**2, 2), "mm²"),
               metric("占检查框", compact(total * 100 / mask.size, 2), "%")]
    return metrics, rows, marks, None


def dark_map(view, settings):
    return regions(view, settings)


def hot_map(view, settings):
    return regions(view, settings, bright=True)


def sample_line(view, p0, p1, band):
    """Read along one uninterrupted straight segment and its neighboring bands."""
    width, height = view.size
    (x0, y0), (x1, y1) = p0, p1
    length = hypot(x1 - x0, y1 - y0)
    if length < 2:
        raise Invalid("剖线端点至少需要相距两个像素。")
    count = min(650, int(length) + 1)
    fx = np.linspace(x0, x1, count)
    fy = np.linspace(y0, y1, count)
    nx, ny = -(y1 - y0) / length, (x1 - x0) / length

    def values(offset):
        px = np.clip(np.rint(fx + offset * nx).astype(int), 0, width - 1)
        py = np.clip(np.rint(fy + offset * ny).astype(int), 0, height - 1)
        return view.values[py, px]

    center = values(0)
    around = np.median(np.vstack([values(offset) for offset in (-band, -band / 2, band / 2, band)]), axis=0)
    contrast = around - center
    samples = [{"x": compact(distance * view.mm), "y": compact(value)} for distance, value in
               zip(np.linspace(0, length, count), contrast)]
    return {"ends": [[x0, y0], [x1, y1]], "samples": samples, "length": compact(length * view.mm, 2),
            "pixels": length, "contrast": compact(np.median(contrast))}


def chain_parts(main, links):
    """Orient the main segment and adopted links into one continuous chain."""
    parts = [[main[0], main[1]]]
    if links:
        def distance(a, b):
            return hypot(a[0] - b[0], a[1] - b[1])

        if min(distance(main[0], p) for p in links[0]) < min(distance(main[1], p) for p in links[0]):
            parts[0] = [main[1], main[0]]
        for link in links:
            tail = parts[-1][1]
            parts.append([link[1], link[0]] if distance(tail, link[1]) < distance(tail, link[0])
                         else [link[0], link[1]])
    gaps = [hypot(parts[i + 1][0][0] - parts[i][1][0], parts[i + 1][0][1] - parts[i][1][1])
            for i in range(len(parts) - 1)]
    return parts, gaps


def rail_spans(view, window):
    """Locate the narrow dark rail bands inside the circled positioning window."""
    x0, y0, x1, y1 = photo.box(window, *view.size)
    pixels = view.values[y0:y1, x0:x1]
    profile = np.median(pixels, axis=0)
    if len(profile) < 4:
        return None
    cutoff = np.quantile(profile, .75) * .72
    dark = profile < cutoff
    changes = np.diff(np.r_[False, dark, False].astype(np.int8))
    spans = [(int(left + x0), int(right + x0))
             for left, right in zip(np.flatnonzero(changes == 1), np.flatnonzero(changes == -1))
             if 1 <= right - left <= max(6, (x1 - x0) // 12)]
    return (x0, y0, x1, y1), spans, profile, cutoff


def recommend_continuation(view, settings, parts):
    """Pick the most credible parked segment continuing the chain across the circled rail."""
    if not settings["pins"]:
        return None, None
    located = rail_spans(view, settings["rail_window"])
    if located is None or not located[1]:
        return None, "焊带定位窗内没有识别出窄暗带，无法推荐续接。"
    (x0, y0, x1, y1), spans = located[0], located[1]
    ends = parts[-1]["ends"]
    if len(parts) == 1:
        def clearance(point):
            return min(min(abs(point[0] - left), abs(point[0] - right)) for left, right in spans)
        tip, base = (ends[1], ends[0]) if clearance(ends[1]) <= clearance(ends[0]) else (ends[0], ends[1])
    else:
        tip, base = ends[1], ends[0]
    rail_left, rail_right = min(spans, key=lambda span: min(abs(tip[0] - span[0]), abs(tip[0] - span[1])))
    gap_limit = max(3 * (rail_right - rail_left) * view.mm, 1.5)
    heading = atan2(tip[1] - base[1], tip[0] - base[0])
    candidates = []
    for index, pin in enumerate(settings["pins"]):
        head, tail = sorted((photo.point(p, *view.size) for p in pin),
                            key=lambda p: hypot(p[0] - tip[0], p[1] - tip[1]))
        across = head[0] >= rail_right if tip[0] < (rail_left + rail_right) / 2 else head[0] < rail_left
        if not across or not (y0 - 6 <= tip[1] <= y1 + 6 and y0 - 6 <= head[1] <= y1 + 6):
            continue
        gap_mm = hypot(head[0] - tip[0], head[1] - tip[1]) * view.mm
        if gap_mm > gap_limit:
            continue
        turn = abs(degrees((atan2(tail[1] - head[1], tail[0] - head[0]) - heading + pi) % (2 * pi) - pi))
        if turn > TURN_LIMIT:
            continue
        candidates.append({"pin": index, "head": head, "gap_mm": gap_mm, "turn": turn,
                           "cost": gap_mm + TURN_WEIGHT * turn})
    if not candidates:
        return None, "附近没有同一条焊带两侧、端头够近且方向接得上的暂留段，保持当前剖线。"
    best = min(candidates, key=lambda c: (c["cost"], c["gap_mm"]))
    shown = [{"pin": c["pin"], "gap_mm": compact(c["gap_mm"], 2), "turn_deg": compact(c["turn"], 1),
              "cost": compact(c["cost"], 2)} for c in candidates]
    best_shown = next(item for item in shown if item["pin"] == best["pin"])
    reason = [f"暂留 {best['pin'] + 1} 最可信：间隔 {best_shown['gap_mm']} mm、"
              f"转向 {best_shown['turn_deg']}°，综合代价 {best_shown['cost']} 最低"]
    for other in shown:
        if other["pin"] == best["pin"]:
            continue
        if other["gap_mm"] < best_shown["gap_mm"] and other["turn_deg"] > best_shown["turn_deg"]:
            reason.append(f"暂留 {other['pin'] + 1} 端头更近（{other['gap_mm']} mm）"
                          f"但转向 {other['turn_deg']}° 更急")
        elif other["turn_deg"] < best_shown["turn_deg"] and other["gap_mm"] > best_shown["gap_mm"]:
            reason.append(f"暂留 {other['pin'] + 1} 方向更顺（{other['turn_deg']}°）"
                          f"但间隔 {other['gap_mm']} mm 更长")
        else:
            reason.append(f"暂留 {other['pin'] + 1} 间隔 {other['gap_mm']} mm、"
                          f"转向 {other['turn_deg']}°，代价 {other['cost']} 更高")
    reason = "；".join(reason) + f"。门槛：同一焊带两侧、间隔 ≤ {compact(gap_limit, 2)} mm、转向 ≤ {TURN_LIMIT:g}°"
    continuation = {"pin": best["pin"], "label": f"暂留 {best['pin'] + 1}", "reason": reason,
                    "gap_mm": best_shown["gap_mm"], "turn_deg": best_shown["turn_deg"],
                    "cost": best_shown["cost"], "joint": list(tip), "head": list(best["head"])}
    return continuation, None


def crack_trace(view, settings):
    band = settings["band"]
    main_ends = [photo.point(p, *view.size) for p in settings["trace"]]
    links = [[photo.point(p, *view.size) for p in link] for link in settings.get("links") or []]
    pins = [[photo.point(p, *view.size) for p in pin] for pin in settings["pins"]]
    chain, gaps = chain_parts(main_ends, links)
    parts = [sample_line(view, p0, p1, band) for p0, p1 in chain]

    samples, trace_parts, covered = [], [], []
    offset = 0.0
    for index, part in enumerate(parts):
        length_mm = part["pixels"] * view.mm
        samples.extend({"x": compact(sample["x"] + offset), "y": sample["y"]} for sample in part["samples"])
        trace_parts.append({"ends": part["ends"], "from": compact(offset), "to": compact(offset + length_mm)})
        offset += length_mm
        if index < len(gaps):
            gap_mm = gaps[index] * view.mm
            covered.append({"from": compact(offset), "to": compact(offset + gap_mm), "mm": compact(gap_mm, 2)})
            samples.append({"x": compact(offset + gap_mm / 2), "y": None})
            offset += gap_mm

    continuation, note = recommend_continuation(view, settings, parts)

    marks = [line(parts[0]["ends"], "#4fe6d0", "当前剖线")]
    marks.extend(line(part["ends"], "#8ce99a", f"续接 {index}") for index, part in enumerate(parts[1:], 1))
    for index, span in enumerate(covered):
        marks.append({**line([parts[index]["ends"][1], parts[index + 1]["ends"][0]], "#9aa8a1",
                             f"遮住 {span['mm']} mm"), "dashed": True})
    for index, pin in enumerate(pins, 1):
        chosen = continuation and continuation["pin"] == index - 1
        marks.append(line(pin, "#ecff96" if chosen else "#dbbd73",
                          f"推荐 · 暂留 {index}" if chosen else f"暂留 {index}"))
    if continuation:
        marks.append({**line([continuation["joint"], continuation["head"]], "#ecff96",
                             f"遮住约 {continuation['gap_mm']} mm"), "dashed": True})

    rows = [record("起点", parts[0]["ends"][0], "原片像素", parts[0]["ends"][0])]
    for index, span in enumerate(covered, 1):
        rows.append(record(f"遮住 {index}", f"{span['mm']} mm", "焊带遮住 · 不计入可见长度 · 原片无落点"))
        rows.append(record(f"续接 {index}", f"{parts[index]['length']} mm",
                           f"距起点 {trace_parts[index]['from']}–{trace_parts[index]['to']} mm",
                           parts[index]["ends"][0]))
    rows.append(record("终点", parts[-1]["ends"][1], "原片像素", parts[-1]["ends"][1]))
    if note:
        rows.append(record("续接推荐", "无合格段", note))

    visible = [sample["y"] for sample in samples if sample["y"] is not None]
    metrics = [metric("中位对比", compact(np.median(visible))), metric("暂留剖线", len(pins))]
    if covered:
        metrics.insert(0, metric("遮住段", compact(sum(span["mm"] for span in covered), 2), "mm"))
        metrics.insert(0, metric("可见裂纹长度", compact(sum(part["pixels"] for part in parts) * view.mm, 2), "mm"))
    else:
        metrics.insert(0, metric("剖线长度", parts[0]["length"], "mm"))
    graph = {**plot("沿剖线明暗差", samples), "trace_ends": main_ends}
    if covered:
        graph["trace_parts"] = trace_parts
        graph["covered"] = covered
    extra = {"continuation": continuation} if continuation else {}
    return metrics, rows, marks, graph, extra


def rail_positions(view, settings):
    located = rail_spans(view, settings["rail_window"])
    if located is None:
        raise Invalid("定位窗内至少需要四列。")
    (x0, y0, x1, y1), spans, profile, cutoff = located
    rows, marks = [], [rectangle([[x0, y0], [x1, y1]], "#dbbd73", "定位窗")]
    for index, (left, right) in enumerate(spans[:32], 1):
        label = f"焊带 {index}"
        marks.append(rectangle([[left, y0], [right, y1]], "#d299ef", label))
        rows.append(record(label, [left, right - 1], f"列坐标 · {compact((right - left) * view.mm, 2)} mm",
                           [(left + right) // 2, (y0 + y1) // 2]))
    stats = [metric("窗口宽度", x1 - x0, "px"), metric("窄暗带", len(spans)), metric("列中位阈值", compact(cutoff))]
    stride = max(1, len(profile) // 500)
    graph = plot("穿过焊带时的列灰度", [{"x": x + x0, "y": compact(value)}
                                        for x, value in zip(range(0, len(profile), stride), profile[::stride])])
    return stats, rows, marks, graph


def edges(view, settings):
    roi = view.roi >= settings["dark_cutoff"]
    x0, y0, x1, y1 = view.region
    count = min(36, roi.shape[1])
    starts = np.linspace(0, roi.shape[1], count + 1, dtype=int)
    rows, marks, samples = [], [], []
    for start, stop in zip(starts, starts[1:]):
        support = np.mean(roi[:, start:stop], axis=1) >= .30
        eligible = np.flatnonzero(support)
        if not len(eligible):
            continue
        depth = int(eligible[0])
        x = int(x0 + (start + stop) // 2)
        samples.append({"x": x, "y": compact(depth * view.mm, 2)})
        if depth > 1:
            endpoint = [x, y0 + depth]
            name = f"原片列 {x}"
            rows.append(record(name, compact(depth * view.mm, 2), "mm · 到第一条有效行", endpoint))
            marks.append(line([[x, y0], endpoint], "#ff896f", name))
    return [metric("扫描段", count), metric("可读段", len(samples)),
            metric("最大内缩", max((s["y"] for s in samples), default=0), "mm")], rows, marks, plot("上边缘内缩", samples)


def uniformity(view, settings):
    pixels = view.roi
    along_rows = np.median(pixels, axis=1)
    along_cols = np.median(pixels, axis=0)
    x0, y0, x1, y1 = view.region
    mid = (x0 + x1) // 2
    stride = max(1, len(along_rows) // 450)
    graph = plot("逐行中位灰度", [{"x": y + y0, "y": compact(value)}
                                    for y, value in zip(range(0, len(along_rows), stride), along_rows[::stride])])
    bands = (("亮度最高行", along_rows, int(np.argmax(along_rows)), "row"),
             ("亮度最低行", along_rows, int(np.argmin(along_rows)), "row"),
             ("亮度最低列", along_cols, int(np.argmin(along_cols)), "column"))
    rows, marks = [], []
    for title, series, index, axis in bands:
        if axis == "row":
            at = [mid, index + y0]
            ends = [[x0, at[1]], [x1, at[1]]]
        else:
            at = [index + x0, (y0 + y1) // 2]
            ends = [[at[0], y0], [at[0], y1]]
        rows.append(record(title, compact(series[index]), f"原片位置 {at}", at))
        marks.append(line(ends, "#8cbaee", title))
    stats = [metric("中位灰度", compact(np.median(pixels))),
             metric("行亮度极差", compact(np.ptp(along_rows))), metric("列亮度极差", compact(np.ptp(along_cols)))]
    return stats, rows, marks, graph


def levels(view, settings):
    pixels = view.roi
    counts, edges = np.histogram(pixels, bins=32, range=(0, 1))
    quantiles = [(rank, compact(np.quantile(pixels, rank))) for rank in (.05, .25, .50, .75, .95)]
    rows = [record(f"P{rank * 100:g}", value, "原片线性灰度") for rank, value in quantiles]
    stats = [metric("像素数", pixels.size), metric("中位灰度", quantiles[2][1]),
             metric("近黑像素", int(np.sum(pixels <= .02)))]
    graph = plot("灰度分布", [{"x": compact((left + right) / 2), "y": int(count)}
                              for left, right, count in zip(edges, edges[1:], counts)])
    return stats, rows, [], graph


CHECKS = {
    "cells": cell_map, "dark": dark_map, "trace": crack_trace, "rails": rail_positions,
    "edge": edges, "uniform": uniformity, "hot": hot_map, "levels": levels,
}


def report(view, mode, settings):
    outcome = CHECKS[mode](view, settings)
    stats, rows, marks, graph = outcome[:4]
    x0, y0, x1, y1 = view.region
    result = {"mode": mode, "heading": model.MODES[mode], "settings": settings, "size": list(view.size),
              "metrics": stats, "rows": rows,
              "marks": [rectangle([[x0, y0], [x1, y1]], "#8cbaee"), *marks],
              "plot": graph}
    if len(outcome) > 4:
        result.update(outcome[4])
    return result
