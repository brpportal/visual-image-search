#!/usr/bin/env python3
"""Collect a reference set of Islamic logos from public image search
pages (Bing + Pinterest routes from webfetch.py) into a folder, ready
for reference.py. One-time data collection for OUR OWN reference set —
the hunter itself never uses these engines.

- >= 120 px on both sides (references may be smaller than crawl finds)
- transparent logos are flattened onto WHITE (CLIP embeds them far
  better than alpha), everything saved as JPEG
- perceptual-hash dedupe; stops at --target images

Usage: python collect_logos.py [--target 100] [--out references-islamic]
"""
from __future__ import annotations

import argparse
import io
import os
import time

import requests
from PIL import Image

import webfetch
from crawl import is_near_duplicate, phash_of

QUERIES = [
    "islamic logo",
    "islamic logo design",
    "mosque logo",
    "crescent moon and star logo",
    "islamic calligraphy logo",
    "madrasa logo design",
    "islamic geometric logo",
    "quran logo",
    "muslim charity logo",
    "islamic school logo",
]
MIN_DIM = 120


def flatten(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and
                                      "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(bg, rgba).convert("RGB")
    return img.convert("RGB")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=100)
    ap.add_argument("--out", default="references-islamic")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    session = requests.Session()
    session.headers.update(webfetch.HEADERS)
    pool: list[str] = []
    seen: set[str] = set()
    for q in QUERIES:
        try:
            for item in webfetch.bing_search(session, q, want=40):
                if item["image_url"] not in seen:
                    seen.add(item["image_url"])
                    pool.append(item["image_url"])
        except Exception as exc:
            print(f"[bing skip] {q}: {str(exc)[:80]}")
        try:
            for item in webfetch.pinterest_search(session, q):
                if item["image_url"] not in seen:
                    seen.add(item["image_url"])
                    pool.append(item["image_url"])
        except Exception as exc:
            print(f"[pinterest skip] {q}: {str(exc)[:80]}")
        print(f"pool after {q!r}: {len(pool)} candidates")

    hashes: list[str] = []
    saved = tried = 0
    for url in pool:
        if saved >= args.target:
            break
        tried += 1
        try:
            resp = requests.get(url, headers=webfetch.HEADERS, timeout=15)
            resp.raise_for_status()
            img = Image.open(io.BytesIO(resp.content))
            img.load()
        except Exception:
            continue
        if img.width < MIN_DIM or img.height < MIN_DIM:
            continue
        img = flatten(img)
        ph = phash_of(img)
        if is_near_duplicate(ph, hashes):
            continue
        hashes.append(ph)
        saved += 1
        img.save(os.path.join(args.out, f"logo-{saved:03d}.jpg"),
                 "JPEG", quality=90)
        if saved % 10 == 0:
            print(f"  saved {saved}/{args.target} (tried {tried})")
        time.sleep(0.4)
    print(f"Done. {saved} reference logos in {args.out}/ "
          f"(from {tried} downloads, pool {len(pool)})")


if __name__ == "__main__":
    main()
