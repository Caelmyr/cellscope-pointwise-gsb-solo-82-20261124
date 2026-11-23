"""Inspectability limits and the persisted review input contract."""

from math import isfinite

from image_lab.validation import Invalid

MAX_UPLOAD = 22 * 1024**2
MAX_PIXELS = 14_000_000
MODES = {
    "cells": "电池片亮度",
    "dark": "暗区面积",
    "trace": "裂纹剖线",
    "rails": "焊带位置",
    "edge": "边沿缺口",
    "uniform": "行列均匀度",
    "hot": "亮斑定位",
    "levels": "灰度分布",
}
DEFAULTS = {
    "mm_per_pixel": 0.19,
    "rows": 3,
    "columns": 4,
    "dark_cutoff": 0.40,
    "min_area": 18,
    "hot_cutoff": 0.94,
    "band": 7,
    "roi": [0.05, 0.08, 0.95, 0.92],
    "trace": [[0.087, 0.17], [0.149, 0.221]],
    "pins": [],
    "rail_window": [0.137, 0.132, 0.179, 0.341],
}


def object_value(value):
    if not isinstance(value, dict):
        raise Invalid("检查请求应为对象。")
    return value


def text_value(value, field="照片名称"):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 100:
        raise Invalid(f"{field}应有 1 至 100 个字符。")
    return " ".join(value.split())


def numeric(value, key, start, stop, integer=False):
    if isinstance(value, bool):
        raise Invalid(f"{key}超出可用范围。")
    try:
        number = float(value)
    except (ValueError, TypeError):
        raise Invalid(f"{key}需要填写数字。") from None
    if not isfinite(number) or not start <= number <= stop or (integer and int(number) != number):
        raise Invalid(f"{key}超出可用范围。")
    return int(number) if integer else number


def points(value):
    if not isinstance(value, list) or len(value) != 2:
        raise Invalid("在原照片上指定两个剖线端点。")
    parsed = []
    for point in value:
        if not isinstance(point, list) or len(point) != 2:
            raise Invalid("在原照片上指定两个剖线端点。")
        parsed.append([numeric(item, "像素位置", 0, 1) for item in point])
    if parsed[0] == parsed[1]:
        raise Invalid("两个剖线端点应有可见间距。")
    return parsed


def rectangle(value, label):
    if not isinstance(value, list) or len(value) != 4:
        raise Invalid(f"请重新框选{label}。")
    x0, y0, x1, y1 = [numeric(item, label, 0, 1) for item in value]
    if x1 - x0 < 0.01 or y1 - y0 < 0.01:
        raise Invalid(f"{label}太小，请重新框选。")
    return [x0, y0, x1, y1]


def column_edges(value, columns):
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != columns + 1:
        return None
    edges = [numeric(item, "竖向分界", 0, 1) for item in value]
    if any(stop - start < 1e-4 for start, stop in zip(edges, edges[1:])):
        raise Invalid("竖向分界应严格递增。")
    return edges


def settings(value, fallback):
    chosen = {**fallback, **object_value(value)}
    limits = {
        "mm_per_pixel": (0.001, 100, False),
        "rows": (1, 24, True), "columns": (1, 32, True),
        "dark_cutoff": (0.01, 0.99, False), "hot_cutoff": (0.01, 1.0, False),
        "min_area": (2, 200_000, True), "band": (1, 30, True),
    }
    result = {key: numeric(chosen.get(key), key, *limit) for key, limit in limits.items()}
    result["roi"] = rectangle(chosen["roi"], "检查框")
    result["rail_window"] = rectangle(chosen["rail_window"], "焊带定位窗")
    result["trace"] = points(chosen["trace"])
    pins = chosen["pins"]
    if not isinstance(pins, list) or len(pins) > 12:
        raise Invalid("一张照片最多暂留 12 段剖线。")
    result["pins"] = [points(pin) for pin in pins]
    edges = column_edges(chosen.get("column_edges"), result["columns"])
    if edges is not None:
        result["column_edges"] = edges
    return result


def mode(value):
    if not isinstance(value, str) or value not in MODES:
        raise Invalid("请选择一种检查。")
    return value
