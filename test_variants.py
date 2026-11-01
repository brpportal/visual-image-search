#!/usr/bin/env python3
"""Variant lab: prove the matcher catches the forms Al listed —
same-to-same copies, EDITED versions, and logos hidden inside GROUP
(collage) images — and measure what tile (deep) matching adds.

Takes real logos from the islamic-tagged reference set, crafts edited
variants locally, and scores each against the reference index twice:
whole-image only vs whole-image + tile views (hunter.views_of).

Usage: python test_variants.py
"""
from __future__ import annotations

import os

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance

import core
import reference
from hunter import views_of

OUT = os.path.join("demo", "variants")
os.makedirs(OUT, exist_ok=True)


def main() -> None:
    core.init_db()
    conn = core.connect()
    reference.ensure_tables(conn)
    index = reference.load_ref_index()
    tag_ids = {int(r[0]) for r in conn.execute(
        "SELECT id FROM ref_images WHERE tag = 'islamic'")}
    assert index is not None and tag_ids, "build islamic references first"
    refs = conn.execute(
        "SELECT id, file_path FROM ref_images WHERE tag = 'islamic' "
        "ORDER BY id LIMIT 3").fetchall()

    def best(vecs) -> tuple[float, int]:
        scores, ids = index.search(vecs, 8)
        best_s, best_id = -1.0, -1
        for row_s, row_i in zip(scores, ids):
            for s, i in zip(row_s, row_i):
                if int(i) in tag_ids and float(s) > best_s:
                    best_s, best_id = float(s), int(i)
        return best_s, best_id

    def score(img: Image.Image, tiles: bool) -> tuple[float, int]:
        views = [img.convert("RGB")]
        if tiles:
            views += views_of(img.convert("RGB"))
        return best(core.embed_pil_images(views))

    # a real photo to build collages on (from the crawled corpus)
    photo_row = conn.execute(
        "SELECT image_path FROM images WHERE indexed = 1 AND "
        "lower(page_title) LIKE '%mountain%' LIMIT 1").fetchone()
    photo = Image.open(os.path.join(
        core.BASE_DIR, photo_row["image_path"])).convert("RGB")
    photo = photo.resize((1000, 700))

    logo_id, logo_path = refs[0]["id"], refs[0]["file_path"]
    logo = Image.open(os.path.join(core.BASE_DIR, logo_path)).convert("RGB")

    variants: dict[str, Image.Image] = {}
    # 1) same-to-same re-save
    p = os.path.join(OUT, "v1-resave.jpg")
    logo.save(p, quality=85)
    variants["1 exact re-save (same-to-same)"] = Image.open(p)
    # 2) recolor (hue shift)
    hsv = logo.convert("HSV")
    h, s_, v = hsv.split()
    h = h.point(lambda x: (x + 45) % 256)
    variants["2 edited: rang badla (hue shift)"] = \
        Image.merge("HSV", (h, s_, v)).convert("RGB")
    # 3) crop to center 55%
    w, hgt = logo.size
    cw, ch = int(w * 0.55), int(hgt * 0.55)
    variants["3 edited: crop 55%"] = logo.crop(
        ((w - cw) // 2, (hgt - ch) // 2, (w + cw) // 2, (hgt + ch) // 2))
    # 4) text banner added (typical edited share)
    canvas = Image.new("RGB", (logo.width, logo.height + 90), "white")
    canvas.paste(logo, (0, 0))
    d = ImageDraw.Draw(canvas)
    d.rectangle([0, logo.height, logo.width, logo.height + 90],
                fill=(20, 90, 50))
    d.text((16, logo.height + 30), "ISLAMIC SCHOOL", fill="white")
    variants["4 edited: text banner joda"] = canvas
    # 5) group/collage: small logo inside a big photo
    collage = photo.copy()
    small = logo.copy()
    small.thumbnail((240, 240))
    collage.paste(small, (30, 30))
    d = ImageDraw.Draw(collage)
    d.rectangle([0, 620, 1000, 700], fill=(0, 0, 0))
    d.text((20, 650), "GROUP PHOTO — EVENT 2026", fill="white")
    variants["5 group image: photo ke andar chhota logo"] = collage
    # 6) faded watermark over photo
    wm = photo.copy()
    faded = ImageEnhance.Brightness(logo.copy()).enhance(1.6)
    faded.thumbnail((420, 420))
    wm.paste(faded, (290, 140))
    variants["6 edited: photo par faded logo"] = wm

    print(f"Reference logo under test: ref#{logo_id} ({logo_path})")
    print(f"{'variant':46s} {'global':>8s} {'+tiles':>8s}  matched")
    for name, img in variants.items():
        path = os.path.join(OUT, name.split()[0] + ".jpg")
        img.save(path, quality=88)
        g_s, g_id = score(img, tiles=False)
        t_s, t_id = score(img, tiles=True)
        print(f"{name:46s} {g_s * 100:7.1f}% {t_s * 100:7.1f}%  "
              f"ref#{t_id}")


if __name__ == "__main__":
    main()
