#!/usr/bin/env python3
"""Hunter — our own web scanner that finds images matching the
reference set. This is the Google/Bing/Pinterest *technology* rebuilt
as our own system: crawl → fingerprint (CLIP embedding) → compare
against the reference index → keep what matches.

How it works:
- Seed pages come from sources.txt. The hunter crawls them politely
  (robots.txt respected, 1–2 s delays, login pages skipped) and follows
  same-host links up to --depth, like a small search-engine crawler.
- Every image found (>= 200x200) is embedded and compared with the
  reference index (reference.py). Best score >= --threshold means a
  MATCH: the photo is saved to data/found/, and the match is recorded
  with its score, the reference it matched, and the source page.
- Every scanned image URL is remembered (SQLite `scanned`), so repeat
  runs only process NEW images — that is what makes automation cheap.
- Run it on a schedule (cron) or loop it: --watch 60 repeats hourly.

Usage:
  python hunter.py [--sources sources.txt] [--depth 1] [--max-pages 30]
                   [--threshold 0.80] [--delay 1.2] [--watch 0]
"""
from __future__ import annotations

import argparse
import hashlib
import os
import random
import time
from urllib.parse import urljoin, urlparse

import numpy as np
import requests
from bs4 import BeautifulSoup

import core
import reference
import social
from crawl import (MIN_SIZE, USER_AGENT, candidate_image_urls,
                   download_image, is_near_duplicate, looks_like_login_page,
                   phash_of, robots_allowed)

BATCH = 16


def views_of(img) -> list:
    """Extra views for deep matching: a logo can hide inside a bigger
    picture (collage/group image), where the whole-image embedding
    dilutes it. Center crop + 4 overlapping quadrants let a small logo
    fill the frame in at least one view."""
    w, h = img.size
    views = []
    cw, ch = int(w * 0.7), int(h * 0.7)
    x0, y0 = (w - cw) // 2, (h - ch) // 2
    views.append(img.crop((x0, y0, x0 + cw, y0 + ch)))
    hw, hh = w // 2, h // 2
    ox, oy = int(w * 0.08), int(h * 0.08)
    views.append(img.crop((0, 0, hw + ox, hh + oy)))
    views.append(img.crop((hw - ox, 0, w, hh + oy)))
    views.append(img.crop((0, hh - oy, hw + ox, h)))
    views.append(img.crop((hw - ox, hh - oy, w, h)))
    return views


def page_links(soup: BeautifulSoup, page_url: str) -> list[str]:
    host = urlparse(page_url).netloc
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        absolute = urljoin(page_url, a["href"]).split("#")[0]
        parsed = urlparse(absolute)
        if parsed.netloc != host or not parsed.scheme.startswith("http"):
            continue
        if any(absolute.lower().endswith(ext) for ext in
               (".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf", ".zip")):
            continue
        if absolute not in seen:
            seen.add(absolute)
            out.append(absolute)
    return out


def hunt(args) -> dict:
    core.init_db()
    conn = core.connect()
    reference.ensure_tables(conn)
    ref_index = reference.load_ref_index()
    if ref_index is None or ref_index.ntotal == 0:
        raise SystemExit("Reference index is empty — first run: "
                         "python reference.py build")
    tag_ids: set[int] | None = None
    if args.tag:
        tag_ids = {int(r[0]) for r in conn.execute(
            "SELECT id FROM ref_images WHERE tag = ?", (args.tag,))}
        if not tag_ids:
            raise SystemExit(f"No references with tag {args.tag!r} — "
                             f"build them first (reference.py --tag)")
        print(f"Target set {args.tag!r}: {len(tag_ids)} references")
    print(f"Reference index: {ref_index.ntotal} images | "
          f"threshold: {args.threshold:.2f} | tiles: {args.tiles}")

    scanned_urls = {r["found_url"] for r in conn.execute(
        "SELECT found_url FROM scanned")}
    match_hashes = [r["phash"] for r in conn.execute(
        "SELECT phash FROM ref_images WHERE phash != ''")]
    found_hashes = [r[0] for r in conn.execute(
        "SELECT s.phash FROM scanned s JOIN matches m "
        "ON s.found_url = m.found_url WHERE s.phash != ''")]

    with open(args.sources, encoding="utf-8") as fh:
        all_seeds = [ln.strip() for ln in fh
                     if ln.strip() and not ln.strip().startswith("#")]
    adapter_specs = [s for s in all_seeds if s.lower().startswith(
        ("youtube:", "instagram:", "facebook:"))]
    seeds = [s for s in all_seeds if s not in adapter_specs]

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    robots_cache: dict = {}
    stats = {"pages": 0, "images_seen": 0, "images_new": 0, "matches": 0}

    queue: list[tuple[str, int]] = [(url, 0) for url in seeds]
    visited: set[str] = set()
    pending: list[dict] = []

    def best_in_tag(scores_row, ids_row):
        """Best (score, ref_id) in one search row, honoring --tag."""
        if tag_ids is None:
            return float(scores_row[0]), int(ids_row[0])
        for s, i in zip(scores_row, ids_row):
            if int(i) in tag_ids:
                return float(s), int(i)
        return -1.0, -1

    def flush() -> None:
        if not pending:
            return
        topk = 8 if tag_ids is not None else 1
        vectors = core.embed_pil_images([p["img"] for p in pending])
        scores, ids = ref_index.search(vectors, topk)
        best = [best_in_tag(scores[i], ids[i]) for i in range(len(pending))]
        # Stage 2 — deep match: tile views rescue a logo diluted inside
        # a bigger picture (group/collage images). Only run for items
        # not already matched globally but plausibly related (>= 0.25),
        # which keeps automated hunts fast.
        if args.tiles:
            todo = [i for i, (s, _) in enumerate(best)
                    if 0.25 <= s < args.threshold]
            if todo:
                views, owners = [], []
                for i in todo:
                    for v in views_of(pending[i]["img"]):
                        views.append(v)
                        owners.append(i)
                vvecs = core.embed_pil_images(views)
                vscores, vids = ref_index.search(vvecs, topk)
                for j, i in enumerate(owners):
                    s2, r2 = best_in_tag(vscores[j], vids[j])
                    if s2 > best[i][0]:
                        best[i] = (s2, r2)
        for item, (score, ref_id) in zip(pending, best):
            conn.execute(
                "INSERT OR REPLACE INTO scanned (found_url, phash,"
                " best_score, best_ref_id, scanned_at) VALUES (?,?,?,?,?)",
                (item["url"], item["phash"], score, int(ref_id),
                 time.time()))
            if score >= args.threshold and ref_id >= 0:
                ext = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp",
                       "GIF": ".gif", "BMP": ".bmp"}.get(
                           item["img"].format or "JPEG", ".jpg")
                name = hashlib.sha1(
                    item["url"].encode()).hexdigest()[:16] + ext
                rel_path = os.path.join("data", "found", name)
                with open(os.path.join(core.BASE_DIR, rel_path),
                          "wb") as fh:
                    fh.write(item["raw"])
                cur = conn.execute(
                    "INSERT OR IGNORE INTO matches (found_url, source_page,"
                    " page_title, matched_ref_id, score, found_path, width,"
                    " height, found_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (item["url"], item["page"], item["page_title"],
                     int(ref_id), round(score, 4), rel_path,
                     item["img"].width, item["img"].height, time.time()))
                if cur.rowcount:
                    stats["matches"] += 1
                    print(f"  [MATCH {score * 100:.1f}%] ref#{int(ref_id)} "
                          f"<- {item['url'][:90]}")
        conn.commit()
        pending.clear()

    # Social adapter sources (youtube:/instagram:/facebook: lines):
    # public images pulled without login, same match pipeline.
    for spec in adapter_specs:
        got = social.candidates_for(spec)
        if got is None:
            print(f"[social] bad source line: {spec}")
            continue
        page_url, candidates = got
        stats["pages"] += 1
        print(f"[social page {stats['pages']}] {page_url}")
        for item in candidates[:args.per_page * 2]:
            image_url = item["image_url"]
            stats["images_seen"] += 1
            if image_url in scanned_urls:
                continue
            img, raw = download_image(session, image_url)
            time.sleep(args.delay + random.uniform(0, 0.5))
            if img is None or img.width < MIN_SIZE or img.height < MIN_SIZE:
                scanned_urls.add(image_url)
                conn.execute(
                    "INSERT OR IGNORE INTO scanned (found_url, scanned_at)"
                    " VALUES (?,?)", (image_url, time.time()))
                continue
            ph = phash_of(img.convert("RGB"))
            if is_near_duplicate(ph, found_hashes):
                scanned_urls.add(image_url)
                continue
            scanned_urls.add(image_url)
            stats["images_new"] += 1
            pending.append({"url": image_url, "page": page_url,
                            "page_title": item.get("title", ""),
                            "img": img, "raw": raw, "phash": ph})
            if len(pending) >= BATCH:
                flush()
        conn.commit()

    while queue and stats["pages"] < args.max_pages:
        page_url, depth = queue.pop(0)
        if page_url in visited:
            continue
        visited.add(page_url)
        if not robots_allowed(robots_cache, page_url):
            print(f"[robots] skip {page_url}")
            continue
        try:
            resp = session.get(page_url, timeout=20)
            resp.raise_for_status()
        except Exception as exc:
            print(f"[page-error] {page_url}: {str(exc)[:90]}")
            continue
        if looks_like_login_page(resp.url, resp.text):
            print(f"[login-protected] skip {resp.url}")
            continue
        stats["pages"] += 1
        soup = BeautifulSoup(resp.text, "html.parser")
        title = (soup.title.string or "").strip() if soup.title else ""
        print(f"[page {stats['pages']}] {resp.url} — {title[:60]}")

        if depth < args.depth:
            for link in page_links(soup, resp.url):
                if link not in visited:
                    queue.append((link, depth + 1))

        candidates = candidate_image_urls(soup, resp.url)[:args.per_page]
        for image_url in candidates:
            stats["images_seen"] += 1
            if image_url in scanned_urls:
                continue
            img, raw = download_image(session, image_url)
            time.sleep(args.delay + random.uniform(0, 0.5))
            if img is None or img.width < MIN_SIZE or img.height < MIN_SIZE:
                scanned_urls.add(image_url)
                conn.execute(
                    "INSERT OR IGNORE INTO scanned (found_url, scanned_at)"
                    " VALUES (?,?)", (image_url, time.time()))
                continue
            ph = phash_of(img.convert("RGB"))
            if is_near_duplicate(ph, found_hashes):
                scanned_urls.add(image_url)
                continue
            scanned_urls.add(image_url)
            stats["images_new"] += 1
            pending.append({"url": image_url, "page": resp.url,
                            "page_title": title, "img": img, "raw": raw,
                            "phash": ph})
            if len(pending) >= BATCH:
                flush()
        conn.commit()
        time.sleep(args.delay + random.uniform(0, 0.5))
    flush()

    total_matches = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    print(f"\nHunt done. Pages crawled: {stats['pages']}, "
          f"images seen: {stats['images_seen']}, "
          f"new images checked: {stats['images_new']}, "
          f"NEW matches: {stats['matches']} "
          f"(all-time matches: {total_matches})")
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description="Hunt the web for images "
                                                 "matching the reference set")
    ap.add_argument("--sources", default="sources.txt")
    ap.add_argument("--depth", type=int, default=1,
                    help="follow same-host links this deep from seeds")
    ap.add_argument("--max-pages", type=int, default=30)
    ap.add_argument("--per-page", type=int, default=30,
                    help="max candidate images per page")
    ap.add_argument("--threshold", type=float, default=0.80,
                    help="min cosine similarity to call it a match")
    ap.add_argument("--tag", default="",
                    help="only match references built with this tag "
                         "(e.g. islamic); empty = whole reference set")
    ap.add_argument("--tiles", action="store_true",
                    help="deep match: also compare center/quadrant "
                         "crops, rescuing logos inside group/collage "
                         "images")
    ap.add_argument("--delay", type=float, default=1.2)
    ap.add_argument("--watch", type=int, default=0,
                    help="repeat every N minutes (0 = run once)")
    args = ap.parse_args()

    while True:
        hunt(args)
        if not args.watch:
            break
        print(f"\n[watch] sleeping {args.watch} min…")
        time.sleep(args.watch * 60)


if __name__ == "__main__":
    main()
