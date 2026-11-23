"""Inspection output uses the exact saved marks and recorded coordinates."""

import csv
from io import StringIO
from math import hypot

from PIL import ImageDraw


def csv_text(result):
    stream = StringIO()
    writer = csv.writer(stream)
    writer.writerow(["field", "value", "details", "original_x", "original_y"])
    writer.writerow(["mode", result["heading"], result.get("title", "")])
    writer.writerow(["scale", result["settings"]["mm_per_pixel"], "mm/px"])
    for metric in result["metrics"]:
        writer.writerow([metric["label"], metric["value"], metric["units"]])
    for row in result["rows"]:
        at = row.get("at") or ["", ""]
        writer.writerow([row["label"], row["value"], row["detail"], *at])
    continuation = result.get("continuation")
    if continuation:
        writer.writerow(["推荐续接", continuation["label"], continuation["reason"]])
    if result.get("plot"):
        writer.writerow(["plot_x", "plot_y", result["plot"]["label"]])
        writer.writerows((point["x"], point["y"]) for point in result["plot"]["samples"])
    return stream.getvalue()


def dashed(draw, ends, color, width, dash=7, gap=5):
    (x0, y0), (x1, y1) = ends
    length = hypot(x1 - x0, y1 - y0)
    if not length:
        return
    dx, dy = (x1 - x0) / length, (y1 - y0) / length
    start = 0.0
    while start < length:
        stop = min(start + dash, length)
        draw.line([(x0 + dx * start, y0 + dy * start), (x0 + dx * stop, y0 + dy * stop)],
                  fill=color, width=width)
        start = stop + gap


def overlay(image, result):
    composed = image.convert("RGB")
    draw = ImageDraw.Draw(composed)
    width = max(1, round(image.width / 650))
    for mark in result["marks"]:
        points = [tuple(point) for point in mark["points"]]
        color = mark["color"]
        if mark["type"] == "box":
            draw.rectangle(points, outline=color, width=width)
        elif mark["type"] == "polygon":
            draw.line(points + [points[0]], fill=color, width=width)
        elif mark.get("dashed"):
            dashed(draw, points, color, width + 1)
        else:
            draw.line(points, fill=color, width=width + 1)
    return composed
