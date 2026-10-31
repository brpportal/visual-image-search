#!/usr/bin/env python3
"""Export everything the static public demo needs into docs/:
- docs/data.json: image metadata + real CLIP embeddings (from the FAISS
  index) + preset text-query embeddings (from the real model).
- docs/thumbs/<id>.jpg: the same 320px thumbnails the server serves.
The demo page re-implements app.py's scoring in JS (cosine x100, round 1dp,
near-duplicate suppression > 0.98), so results match the live server.
"""
import json
import os

import numpy as np
from PIL import Image

import core

BASE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(BASE, "docs")
THUMBS_OUT = os.path.join(DOCS, "thumbs")
os.makedirs(THUMBS_OUT, exist_ok=True)

QUERIES = [
    "red car on a road",
    "green tree in a field",
    "sunset over purple mountains",
    "an orange cat face",
    "a sailboat on the sea",
    "snowy mountain peak",
    "house with a red roof",
    "night sky with stars and moon",
    "desert dunes at noon",
    "fresh green apple",
    "pink hearts pattern",
    "black and white checkerboard",
    "big red sun on blue sky",
    "purple abstract circles",
    "a yellow circle",
    "red and white stripes",
]


def r5(vec):
    return [round(float(x), 5) for x in vec]


def main():
    index = core.load_index()
    assert index is not None and index.ntotal > 0, "index missing/empty"

    conn = core.connect()
    images = []
    for row in conn.execute(
            "SELECT id, page_title, source_url, image_url, image_path "
            "FROM images WHERE indexed = 1 ORDER BY id"):
        iid = int(row["id"])
        vec = index.reconstruct(iid)
        # thumbnail: same rule as the server's /thumb endpoint
        # (RGB, max 320px, JPEG q80), generated from the stored original.
        dst = os.path.join(THUMBS_OUT, f"{iid}.jpg")
        img = Image.open(os.path.join(core.BASE_DIR, row["image_path"]))
        img.load()
        img.convert("RGB").thumbnail((320, 320))
        img.save(dst, "JPEG", quality=80)
        images.append({
            "id": iid,
            "title": row["page_title"] or "",
            "source_url": row["source_url"] or row["image_url"] or "",
            "thumb": f"thumbs/{iid}.jpg",
            "emb": r5(vec),
        })
    conn.close()

    queries = [{"q": q, "emb": r5(core.embed_text(q))} for q in QUERIES]

    data = {"model": f"{core.MODEL_NAME} ({core.PRETRAINED})",
            "images": images, "queries": queries}
    with open(os.path.join(DOCS, "data.json"), "w") as f:
        json.dump(data, f)
    print(f"exported {len(images)} images, {len(queries)} queries -> {DOCS}")


if __name__ == "__main__":
    main()
