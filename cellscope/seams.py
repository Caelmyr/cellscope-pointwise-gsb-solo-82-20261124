"""Recommend vertical cell boundaries from dark seams running through every row."""

import numpy as np

from image_lab.validation import Invalid

from .inspection import line

MIN_DEPTH = 0.25    # continuation depth below which a column is not seam evidence
ON_SEAM = 0.80      # continuation depth that counts as sitting on a real gap
PLAUSIBLE = 0.30    # evidence needed to mention an alternative in the notes
PITCH_WEIGHT = 0.8  # penalty per fractional pitch of column-width distortion


def _darkness(view, y_edges):
    """Column darkness per row band, normalized so each band's floor reads as 1."""
    x0, _, x1, _ = view.region
    bands = []
    for top, bottom in zip(y_edges, y_edges[1:]):
        inset = max(1, (bottom - top) // 10)
        band = view.values[top + inset:bottom - inset, x0:x1]
        bands.append(np.median(band, axis=0))
    profiles = np.vstack(bands)
    interior = np.median(profiles, axis=1, keepdims=True)
    floor = np.quantile(profiles, 0.02, axis=1, keepdims=True)
    span = np.maximum(interior - floor, 1e-6)
    return np.clip((interior - profiles) / span, 0, 1)


def _current(view, settings):
    """The user's present boundary positions relative to the roi, or None for equal."""
    saved = settings.get("column_edges")
    x0, _, x1, _ = view.region
    width = view.size[0]
    if saved and len(saved) == settings["columns"] + 1:
        edges = [value * width - x0 for value in saved]
        if abs(edges[0]) <= 2 and abs(edges[-1] - (x1 - x0)) <= 2 and \
                all(stop - start >= 3 for start, stop in zip(edges, edges[1:])):
            return edges
    return None


def _candidates(cont, evidence, center, half, limit=5):
    """Runs of row-continuing darkness near one boundary, as scored candidates."""
    lo = max(1, int(round(center - half)))
    hi = min(len(cont) - 1, int(round(center + half)))
    segment = cont[lo:hi]
    found = []
    if len(segment) and float(np.max(segment)) >= MIN_DEPTH:
        level = max(MIN_DEPTH, 0.5 * float(np.max(segment)))
        hot = segment >= level
        changes = np.diff(np.r_[False, hot, False].astype(np.int8))
        for start, stop in zip(np.flatnonzero(changes == 1), np.flatnonzero(changes == -1)):
            run = segment[start:stop]
            weights = np.maximum(run - level + 0.05, 0.01)
            pos = lo + start + float(np.average(np.arange(stop - start), weights=weights))
            x = int(round(pos))
            found.append({"pos": pos, "ev": float(evidence[x]), "depth": float(cont[x]),
                          "span": (lo + int(start), lo + int(stop))})
        found.sort(key=lambda item: -item["ev"])
        found = found[:limit]
    return found


def _choose(boundaries, pitch, width):
    """Pick one candidate per boundary, balancing seam evidence against even spacing."""
    scores = [[cand["ev"] - PITCH_WEIGHT * abs(cand["pos"] - pitch) / pitch
               for cand in boundaries[0]]]
    parents = [[0] * len(boundaries[0])]
    for index in range(1, len(boundaries)):
        row, origin = [], []
        for cand in boundaries[index]:
            best, source = max(
                ((scores[index - 1][k] - PITCH_WEIGHT * abs(cand["pos"] - prev["pos"] - pitch) / pitch, k)
                 for k, prev in enumerate(boundaries[index - 1])), key=lambda item: item[0])
            row.append(cand["ev"] + best)
            origin.append(source)
        scores.append(row)
        parents.append(origin)
    tail = [score - PITCH_WEIGHT * abs(width - cand["pos"] - pitch) / pitch
            for score, cand in zip(scores[-1], boundaries[-1])]
    pick = int(np.argmax(tail))
    chosen = []
    for index in range(len(boundaries) - 1, -1, -1):
        chosen.append(pick)
        pick = parents[index][pick]
    return chosen[::-1]


def _notes(boundaries, chosen, current, pitch, x0, mm, rows):
    """Explain each boundary, including which of two plausible seams was taken and why."""

    def px(pos):
        return int(round(x0 + pos))

    notes = []
    for index, (options, pick) in enumerate(zip(boundaries, chosen), 1):
        got = options[pick]
        shift = got["pos"] - current[index]
        where = "正在当前分位" if abs(shift) < max(3, 0.03 * pitch) \
            else f"距当前分位 {shift:+.0f} px（{abs(shift) * mm:.1f} mm）"
        others = sorted((cand for cand in options if cand is not got
                         and abs(cand["pos"] - got["pos"]) > max(3, 0.03 * pitch)
                         and cand["ev"] >= PLAUSIBLE), key=lambda cand: -cand["ev"])
        if got.get("fallback"):
            if got["depth"] >= ON_SEAM and abs(shift) < 2:
                notes.append(f"第 {index} 道界：当前位置 x={px(got['pos'])} 已在贯通暗缝上，保持不变。")
            elif got["depth"] >= ON_SEAM:
                notes.append(f"第 {index} 道界：当前位置在暗缝内，对齐到暗缝中心 "
                             f"x={px(got['pos'])}（{shift:+.0f} px）。")
            elif others:
                alt = others[0]
                reason = "未贯通各行" if alt["depth"] < 0.5 else "偏离列距过远，更像焊带"
                notes.append(f"第 {index} 道界：x={px(alt['pos'])} 的暗纹{reason}，"
                             f"保留当前位置 x={px(got['pos'])}。")
            else:
                notes.append(f"第 {index} 道界：附近未见贯通 {rows} 行的片缝证据，"
                             f"保留当前位置 x={px(got['pos'])}。")
            continue
        if not others:
            notes.append(f"第 {index} 道界：取 x={px(got['pos'])} 的贯通暗缝（{where}）。")
            continue
        alt = others[0]
        if got["ev"] >= alt["ev"] and abs(got["pos"] - current[index]) <= abs(alt["pos"] - current[index]):
            notes.append(f"第 {index} 道界：取 x={px(got['pos'])}，原片清楚且贴合列距（{where}）。")
        elif got["ev"] >= alt["ev"]:
            notes.append(f"第 {index} 道界：x={px(alt['pos'])} 更贴合当前列距，但 x={px(got['pos'])} "
                         f"的暗缝贯通 {rows} 行、在原片上更清楚（证据 {got['ev']:.2f} 对 {alt['ev']:.2f}），"
                         f"取 x={px(got['pos'])}。")
        else:
            notes.append(f"第 {index} 道界：x={px(alt['pos'])} 的暗纹更清楚，但偏离当前列距较多"
                         f"（疑为焊带），取更贴合列距的 x={px(got['pos'])}"
                         f"（证据 {got['ev']:.2f} 对 {alt['ev']:.2f}）。")
    return notes


def recommend(view, settings):
    """One set of vertical boundaries for the circled board; row edges stay equal."""
    x0, y0, x1, y1 = view.region
    rows, cols = settings["rows"], settings["columns"]
    width = x1 - x0
    pitch = width / cols
    if pitch < 8:
        raise Invalid("检查框过窄，无法推荐竖向分界。")
    current = _current(view, settings) or [pitch * index for index in range(cols + 1)]
    y_edges = np.rint(np.linspace(y0, y1, rows + 1)).astype(int)
    dark = _darkness(view, y_edges)
    cont = np.min(dark, axis=0)
    evidence = 0.65 * cont ** 2 + 0.35 * np.mean(dark, axis=0)
    half = max(4.0, 0.3 * pitch)
    boundaries = []
    for index in range(1, cols):
        found = _candidates(cont, evidence, current[index], half)
        here = float(current[index])
        host = next((cand for cand in found
                     if cand["span"][0] - 1 <= here <= cand["span"][1] + 1
                     and cand["span"][1] - cand["span"][0] <= 0.25 * pitch
                     and abs(cand["pos"] - here) <= 0.1 * pitch), None)
        if host is not None:  # current position sits inside a gap-like run: snap to its center
            found.remove(host)
            here = host["pos"]
        x = int(round(here))
        found.append({"pos": here, "ev": float(evidence[x]), "depth": float(cont[x]),
                      "fallback": True})
        boundaries.append(found)
    chosen = _choose(boundaries, pitch, width) if boundaries else []
    notes = _notes(boundaries, chosen, current, pitch, x0, view.mm, rows)
    edges = [x0] + [int(round(x0 + boundaries[i][pick]["pos"])) for i, pick in enumerate(chosen)] + [x1]
    kept = sum(1 for index, edge in enumerate(edges[1:-1], 1)
               if abs(edge - x0 - current[index]) < 2)
    marks = [line([[edge, y0], [edge, y1]], "#7fd8f7", f"界 {index}")
             for index, edge in enumerate(edges[1:-1], 1)]
    if not boundaries:
        notes = ["单列无需竖向分界。"]
    return {"edges": edges, "normalized": [edge / view.size[0] for edge in edges],
            "notes": notes, "marks": marks, "kept": kept,
            "moved": len(chosen) - kept, "boundaries": len(chosen)}
