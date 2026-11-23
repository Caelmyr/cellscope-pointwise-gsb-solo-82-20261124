"""EL brightness, contour, grid, busbar and one-segment trace measurements."""

from math import hypot

import cv2
import numpy as np

from image_lab.validation import Invalid

from . import cutline, model, photo


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
    return stats, rows, marks, None, None


def regions(view, settings, bright=False):
    pixels = view.roi
    mask = pixels >= settings["hot_cutoff"] if bright else pixels < settings["dark_cutoff"]
    kept = None if bright else cutline.retained(view, settings.get("cut"))
    if kept is not None:
        mask = mask & kept
    count, labels, stats, centers = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    x0, y0, _, _ = view.region
    minimum = settings["min_area"]
    accepted = sorted((index for index in range(1, count) if stats[index, cv2.CC_STAT_AREA] >= minimum),
                      key=lambda index: (-stats[index, cv2.CC_STAT_AREA], index))
    total = sum(int(stats[index, cv2.CC_STAT_AREA]) for index in accepted)
    area = int(kept.sum()) if kept is not None else mask.size
    rows, marks = [], []
    color = "#f6d17d" if bright else "#ff896f"
    for ordinal, index in enumerate(accepted[:80], 1):
        left, top, width, height, area_px = map(int, stats[index])
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
        rows.append(record(label, compact(area_px * view.mm**2, 2), f"mm² · {area_px}px", center))
    metrics = [metric("达最小面积的区域", len(accepted)), metric("总面积", compact(total * view.mm**2, 2), "mm²"),
               metric("占剩余片区" if kept is not None else "占检查框",
                      compact(total * 100 / area, 2), "%")]
    if kept is not None:
        metrics.append(metric("剩余片区", compact(area * view.mm**2, 2), "mm²"))
    return metrics, rows, marks, None, None


def dark_map(view, settings):
    return regions(view, settings)


def hot_map(view, settings):
    return regions(view, settings, bright=True)


def measure_segment(view, endpoints, band):
    """Read along one uninterrupted straight segment and its neighboring bands."""
    width, height = view.size
    (x0, y0), (x1, y1) = [photo.point(item, width, height) for item in endpoints]
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
            "contrast": compact(np.median(contrast))}


def crack_trace(view, settings):
    main = measure_segment(view, settings["trace"], settings["band"])
    pins = [[photo.point(p, *view.size) for p in pin] for pin in settings["pins"]]
    marks = [line(main["ends"], "#4fe6d0", "当前剖线")]
    marks.extend(line(pin, "#dbbd73", f"暂留 {index}") for index, pin in enumerate(pins, 1))
    rows = [record("起点", main["ends"][0], "原片像素", main["ends"][0]),
            record("终点", main["ends"][1], "原片像素", main["ends"][1])]
    metrics = [metric("剖线长度", main["length"], "mm"), metric("中位对比", main["contrast"]),
               metric("暂留剖线", len(pins))]
    graph = {**plot("沿剖线明暗差", main["samples"]), "trace_ends": main["ends"]}
    return metrics, rows, marks, graph, None


def rail_positions(view, settings):
    x0, y0, x1, y1 = photo.box(settings["rail_window"], *view.size)
    pixels = view.values[y0:y1, x0:x1]
    profile = np.median(pixels, axis=0)
    if len(profile) < 4:
        raise Invalid("定位窗内至少需要四列。")
    cutoff = np.quantile(profile, .75) * .72
    dark = profile < cutoff
    changes = np.diff(np.r_[False, dark, False].astype(np.int8))
    spans = [(int(left + x0), int(right + x0))
             for left, right in zip(np.flatnonzero(changes == 1), np.flatnonzero(changes == -1))
             if 1 <= right - left <= max(6, (x1 - x0) // 12)]
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
    return stats, rows, marks, graph, None


def edges(view, settings):
    result, metrics, rows, marks = cutline.report(view, settings)
    return metrics, rows, marks, None, result


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
    return stats, rows, marks, graph, None


def levels(view, settings):
    pixels = view.roi
    counts, edges = np.histogram(pixels, bins=32, range=(0, 1))
    quantiles = [(rank, compact(np.quantile(pixels, rank))) for rank in (.05, .25, .50, .75, .95)]
    rows = [record(f"P{rank * 100:g}", value, "原片线性灰度") for rank, value in quantiles]
    stats = [metric("像素数", pixels.size), metric("中位灰度", quantiles[2][1]),
             metric("近黑像素", int(np.sum(pixels <= .02)))]
    graph = plot("灰度分布", [{"x": compact((left + right) / 2), "y": int(count)}
                              for left, right, count in zip(edges, edges[1:], counts)])
    return stats, rows, [], graph, None


CHECKS = {
    "cells": cell_map, "dark": dark_map, "trace": crack_trace, "rails": rail_positions,
    "edge": edges, "uniform": uniformity, "hot": hot_map, "levels": levels,
}


def report(view, mode, settings):
    stats, rows, marks, graph, recommendation = CHECKS[mode](view, settings)
    x0, y0, x1, y1 = view.region
    extra = [cutline.line_mark(view, settings["cut"], cutline.CUT_COLOR, "采用试裁线")] if settings.get("cut") else []
    return {"mode": mode, "heading": model.MODES[mode], "settings": settings, "size": list(view.size),
            "metrics": stats, "rows": rows,
            "marks": [rectangle([[x0, y0], [x1, y1]], "#8cbaee"), *marks, *extra], "plot": graph,
            "recommendation": recommendation if recommendation and recommendation["endpoints"] else None}
