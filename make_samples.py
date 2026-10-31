#!/usr/bin/env python3
"""Generate the 20-image acceptance-test set and seed the database.

Creates simple, clearly different drawn images (480x360) in data/images,
plus:
  - sample 19: a near-duplicate of sample 03 (the same image re-saved at
    JPEG quality 85 — measured CLIP cosine 0.9898 vs the original) —
    search results must collapse it via the >0.98 near-duplicate rule.
    (A resized copy measures 0.9624 and is correctly NOT collapsed:
    the rule targets same-photo re-hosts, not merely similar images.)
  - sample 20: a corrupt "image" (text bytes) — the indexer must skip it
    gracefully instead of crashing.

The seeded rows pretend the images were crawled from example.org gallery
pages, so source_url/title/platform behave exactly like real crawl output.

Usage:  python make_samples.py   (then: python index.py)
"""
from __future__ import annotations

import os
import time
from io import BytesIO

from PIL import Image, ImageDraw

import core

W, H = 480, 360


def gradient(top, bottom):
    img = Image.new("RGB", (W, H))
    px = img.load()
    for y in range(H):
        t = y / H
        px_row = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        for x in range(0, W, 8):  # blocky fill is fine for samples
            for dx in range(8):
                px[x + dx, y] = px_row
    return img


def label(img, text):
    d = ImageDraw.Draw(img)
    d.rectangle([8, H - 34, 8 + 11 * len(text) + 12, H - 8], fill=(0, 0, 0))
    d.text((14, H - 28), text, fill=(255, 255, 255))
    return img


def scene(kind: str) -> Image.Image:
    if kind == "sunset_mountains":
        img = gradient((255, 140, 60), (60, 20, 90))
        d = ImageDraw.Draw(img)
        d.ellipse([330, 40, 420, 130], fill=(255, 220, 120))
        d.polygon([(0, 360), (150, 170), (300, 360)], fill=(40, 40, 70))
        d.polygon([(180, 360), (330, 140), (480, 360)], fill=(25, 25, 55))
        return img
    if kind == "red_sun_blue":
        img = gradient((40, 90, 200), (10, 30, 80))
        d = ImageDraw.Draw(img)
        d.ellipse([165, 80, 315, 230], fill=(230, 40, 40))
        return img
    if kind == "green_field_tree":
        img = gradient((140, 200, 255), (90, 180, 90))
        d = ImageDraw.Draw(img)
        d.rectangle([0, 250, W, H], fill=(50, 150, 60))
        d.rectangle([225, 170, 245, 260], fill=(110, 70, 30))
        d.ellipse([170, 70, 300, 190], fill=(30, 130, 40))
        return img
    if kind == "stripes_red_white":
        img = Image.new("RGB", (W, H), (255, 255, 255))
        d = ImageDraw.Draw(img)
        for i in range(0, W, 60):
            d.rectangle([i, 0, i + 30, H], fill=(220, 30, 30))
        return img
    if kind == "stripes_blue_white":
        img = Image.new("RGB", (W, H), (255, 255, 255))
        d = ImageDraw.Draw(img)
        for i in range(0, H, 60):
            d.rectangle([0, i, W, i + 30], fill=(30, 60, 220))
        return img
    if kind == "checkerboard":
        img = Image.new("RGB", (W, H))
        d = ImageDraw.Draw(img)
        s = 45
        for y in range(0, H, s):
            for x in range(0, W, s):
                c = (20, 20, 20) if (x // s + y // s) % 2 == 0 else (240, 240, 240)
                d.rectangle([x, y, x + s, y + s], fill=c)
        return img
    if kind == "house":
        img = gradient((150, 210, 255), (230, 240, 255))
        d = ImageDraw.Draw(img)
        d.rectangle([0, 290, W, H], fill=(90, 170, 80))
        d.rectangle([150, 180, 330, 300], fill=(240, 230, 200))
        d.polygon([(130, 180), (240, 90), (350, 180)], fill=(180, 60, 40))
        d.rectangle([215, 230, 265, 300], fill=(120, 70, 30))
        return img
    if kind == "car_red":
        img = gradient((200, 220, 240), (120, 130, 140))
        d = ImageDraw.Draw(img)
        d.rectangle([0, 280, W, H], fill=(70, 70, 70))
        d.rectangle([90, 210, 390, 270], fill=(210, 30, 30))
        d.rectangle([150, 160, 320, 215], fill=(190, 25, 25))
        d.ellipse([120, 255, 180, 315], fill=(15, 15, 15))
        d.ellipse([300, 255, 360, 315], fill=(15, 15, 15))
        return img
    if kind == "boat_sea":
        img = gradient((120, 190, 255), (20, 90, 180))
        d = ImageDraw.Draw(img)
        d.rectangle([0, 240, W, H], fill=(15, 70, 160))
        d.polygon([(180, 240), (300, 240), (270, 275), (205, 275)], fill=(140, 90, 40))
        d.polygon([(238, 130), (238, 235), (300, 235)], fill=(250, 250, 240))
        return img
    if kind == "night_stars":
        img = gradient((5, 5, 30), (20, 20, 60))
        d = ImageDraw.Draw(img)
        import random

        random.seed(7)
        for _ in range(120):
            x, y = random.randint(0, W - 3), random.randint(0, 240)
            d.ellipse([x, y, x + 3, y + 3], fill=(255, 255, 230))
        d.ellipse([360, 40, 430, 110], fill=(240, 240, 210))
        d.ellipse([335, 30, 405, 100], fill=(10, 10, 45))
        return img
    if kind == "yellow_circle_black":
        img = Image.new("RGB", (W, H), (10, 10, 10))
        d = ImageDraw.Draw(img)
        d.ellipse([140, 55, 340, 255], fill=(250, 210, 30))
        return img
    if kind == "pink_hearts":
        img = gradient((255, 190, 220), (255, 120, 170))
        d = ImageDraw.Draw(img)
        for cx, cy, s in [(120, 110, 46), (300, 200, 60), (390, 90, 34)]:
            d.ellipse([cx - s, cy - s, cx, cy + 6], fill=(230, 30, 90))
            d.ellipse([cx, cy - s, cx + s, cy + 6], fill=(230, 30, 90))
            d.polygon([(cx - s + 4, cy), (cx + s - 4, cy), (cx, cy + s + 16)],
                      fill=(230, 30, 90))
        return img
    if kind == "orange_cat_face":
        img = gradient((255, 235, 200), (255, 210, 150))
        d = ImageDraw.Draw(img)
        d.polygon([(140, 90), (180, 30), (215, 85)], fill=(245, 150, 40))
        d.polygon([(265, 85), (300, 30), (340, 90)], fill=(245, 150, 40))
        d.ellipse([130, 70, 350, 300], fill=(250, 165, 50))
        d.ellipse([185, 150, 215, 185], fill=(20, 20, 20))
        d.ellipse([265, 150, 295, 185], fill=(20, 20, 20))
        d.ellipse([228, 205, 252, 222], fill=(230, 110, 130))
        return img
    if kind == "snow_peak":
        img = gradient((170, 210, 255), (240, 248, 255))
        d = ImageDraw.Draw(img)
        d.polygon([(40, 360), (240, 90), (440, 360)], fill=(110, 130, 160))
        d.polygon([(185, 165), (240, 90), (295, 165), (262, 150), (240, 172),
                   (218, 150)], fill=(255, 255, 255))
        return img
    if kind == "desert_dune":
        img = gradient((255, 190, 90), (250, 160, 60))
        d = ImageDraw.Draw(img)
        d.ellipse([30, 25, 105, 100], fill=(255, 240, 180))
        d.polygon([(0, 250), (240, 180), (480, 250), (480, 360), (0, 360)],
                  fill=(232, 175, 95))
        d.polygon([(0, 300), (200, 240), (480, 300), (480, 360), (0, 360)],
                  fill=(214, 150, 70))
        return img
    if kind == "purple_abstract":
        img = gradient((70, 20, 110), (30, 5, 60))
        d = ImageDraw.Draw(img)
        for i, c in enumerate([(150, 60, 200), (110, 40, 170), (190, 90, 230),
                               (90, 30, 140)]):
            d.ellipse([60 + i * 60, 60 + i * 30, 260 + i * 40, 220 + i * 25],
                      outline=c, width=10)
        return img
    if kind == "green_apple":
        img = gradient((245, 245, 240), (220, 220, 210))
        d = ImageDraw.Draw(img)
        d.ellipse([165, 110, 315, 290], fill=(90, 190, 60))
        d.rectangle([235, 70, 245, 115], fill=(110, 70, 30))
        d.ellipse([240, 55, 305, 95], fill=(60, 160, 60))
        return img
    raise ValueError(kind)


SAMPLES = [
    ("sunset_mountains", "Sample 01 - Sunset over purple mountains"),
    ("red_sun_blue", "Sample 02 - Big red sun on blue sky"),
    ("green_field_tree", "Sample 03 - Green tree in a field"),
    ("stripes_red_white", "Sample 04 - Red and white vertical stripes"),
    ("stripes_blue_white", "Sample 05 - Blue and white horizontal stripes"),
    ("checkerboard", "Sample 06 - Black and white checkerboard"),
    ("house", "Sample 07 - House with red roof"),
    ("car_red", "Sample 08 - Red car on a road"),
    ("boat_sea", "Sample 09 - Sailboat on the sea"),
    ("night_stars", "Sample 10 - Night sky with stars and moon"),
    ("yellow_circle_black", "Sample 11 - Yellow circle on black"),
    ("pink_hearts", "Sample 12 - Pink hearts"),
    ("orange_cat_face", "Sample 13 - Orange cat face"),
    ("snow_peak", "Sample 14 - Snowy mountain peak"),
    ("desert_dune", "Sample 15 - Desert dunes at noon"),
    ("purple_abstract", "Sample 16 - Purple abstract circles"),
    ("green_apple", "Sample 17 - Green apple"),
    ("sunset_mountains", "Sample 18 - Mountain sunset (warm)"),
]


def main() -> None:
    core.init_db()
    conn = core.connect()
    conn.execute("DELETE FROM images WHERE source_url LIKE 'https://example.org/%'")
    conn.commit()

    made = []
    for i, (kind, title) in enumerate(SAMPLES, start=1):
        img = scene(kind)
        if i == 18:  # warm-shifted variant of 01 (still a different image)
            img = gradient((255, 170, 80), (90, 30, 60))
            d = ImageDraw.Draw(img)
            d.ellipse([60, 35, 140, 115], fill=(255, 225, 140))
            d.polygon([(0, 360), (170, 180), (340, 360)], fill=(50, 35, 70))
        label(img, f"SAMPLE {i:02d}")
        name = f"sample-{i:02d}.jpg"
        path = os.path.join(core.IMAGES_DIR, name)
        img.save(path, "JPEG", quality=90)
        made.append((name, title, i))

    # 19 = near-duplicate of 03 (same pixels, re-saved at lower JPEG quality)
    dup_src = Image.open(os.path.join(core.IMAGES_DIR, "sample-03.jpg"))
    buf = BytesIO()
    dup_src.save(buf, "JPEG", quality=85)
    with open(os.path.join(core.IMAGES_DIR, "sample-19.jpg"), "wb") as fh:
        fh.write(buf.getvalue())
    made.append(("sample-19.jpg", "Sample 19 - Green tree in a field (copy)", 19))

    # 20 = corrupt file
    with open(os.path.join(core.IMAGES_DIR, "sample-20.jpg"), "wb") as fh:
        fh.write(b"this is not a real jpeg file at all" * 8)
    made.append(("sample-20.jpg", "Sample 20 - Corrupt file (indexer must skip)", 20))

    for name, title, i in made:
        rel = os.path.join("data", "images", name)
        conn.execute(
            "INSERT OR REPLACE INTO images (image_path, image_url, source_url,"
            " page_title, platform, phash, width, height, indexed, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,0,?)",
            (rel, f"https://example.org/files/{name}",
             f"https://example.org/gallery/sample-{i:02d}", title,
             "example.org", "", W, H, time.time()))
    conn.commit()
    print(f"Seeded {len(made)} sample rows. Now run: python index.py")


if __name__ == "__main__":
    main()
