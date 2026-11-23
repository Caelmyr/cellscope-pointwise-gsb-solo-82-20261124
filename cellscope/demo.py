"""Create an attributable synthetic panel with visible inspection features."""

import cv2
import numpy as np
from PIL import Image

from . import model, photo

WIDTH, HEIGHT = 1000, 660


def panel_photo():
    rng = np.random.default_rng(213)
    y, x = np.indices((HEIGHT, WIDTH))
    canvas = 10 + 2 * rng.normal(size=(HEIGHT, WIDTH))
    for row in range(3):
        for col in range(4):
            left, top = 48 + col * 226, 50 + row * 189
            right, bottom = left + 212, top + 175
            zone = (x >= left) & (x < right) & (y >= top) & (y < bottom)
            base = 145 + 8 * col - 5 * row + 6 * np.sin(y[zone] / 45)
            texture = 4 * np.sin(y[zone] * 1.64) + 1.5 * rng.normal(size=zone.sum())
            canvas[zone] = base + texture
            for offset in (51, 104, 157):
                rail = zone & (abs(x - left - offset) <= 2)
                canvas[rail] *= .31
            canvas[top:bottom, left:left + 4] *= .72
            canvas[bottom - 3:bottom, left:right] *= .72
    render = np.clip(canvas, 0, 255).astype(np.uint8)
    cv2.polylines(render, [np.array([[87, 112], [121, 128], [149, 145]])],
                  False, 43, 3, cv2.LINE_AA)
    cv2.polylines(render, [np.array([[156, 149], [185, 177], [204, 195]])],
                  False, 55, 2, cv2.LINE_AA)
    cv2.polylines(render, [np.array([[160, 145], [193, 164], [218, 178]])],
                  False, 55, 2, cv2.LINE_AA)
    cv2.polylines(render, [np.array([[277, 336], [303, 358], [321, 365], [338, 383]])],
                  False, 60, 2, cv2.LINE_AA)
    cv2.ellipse(render, (397, 332), (62, 26), -20, 0, 360, 63, -1, cv2.LINE_AA)
    cv2.ellipse(render, (861, 523), (34, 55), 35, 0, 360, 81, -1, cv2.LINE_AA)
    cv2.fillPoly(render, [np.array([[500, 50], [524, 51], [519, 65], [505, 59]])], 15)
    for center, radius in (((224, 200), 3), ((616, 495), 5), ((458, 120), 4), ((779, 360), 3)):
        cv2.circle(render, center, radius, 250, -1, cv2.LINE_AA)
    return Image.fromarray(render)


def create(library):
    image = panel_photo()
    settings = {**model.DEFAULTS, "roi": [0.048, 0.076, 0.938, 0.914],
                "pins": [[[.156, .226], [.204, .296]], [[.160, .220], [.218, .270]]]}
    return library.create("参考练习 · EL-042", "EL-042-synthetic.png", photo.png(image),
                          image.size, settings, {"kind": "synthetic"})
