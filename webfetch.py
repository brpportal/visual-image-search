#!/usr/bin/env python3
"""Realtime web image fetch via reverse-engineered public search pages.

Engines (no paid APIs, no logins, no CAPTCHA bypass — a blocked engine is
reported and skipped):
- Bing Images: result data sits in HTML-entity-escaped JSON attributes
  (m="{&quot;murl&quot;:...}") on /images/search + /images/async/search.
  Unescape, then read murl (original), purl (source page), t (title).
- Pinterest: guest session cookie (csrftoken) from the search page, then
  the site's own internal resource endpoint
  /resource/BaseSearchResource/get/ returns pin JSON incl. image URLs.
- Google Images: modern pages need JS; the basic variants return an
  empty mobile shell from most networks. One attempt is made and the
  outcome reported honestly.

Fetched images go through the exact pipeline crawl.py uses: >=200x200,
perceptual-hash dedupe, SQLite metadata, files under data/images/.
Then run index.py to embed them.

Usage:
  python webfetch.py --queries "cat,dog,red car" [--per-query 12]
                     [--engines bing,pinterest,google] [--delay 1.2]
"""
from __future__ import annotations

import argparse
import hashlib
import html as ihtml
import json
import os
import random
import re
import time
from urllib.parse import quote_plus

import requests

import core
from crawl import MIN_SIZE, download_image, is_near_duplicate, phash_of

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0 Safari/537.36")
HEADERS = {"User-Agent": BROWSER_UA, "Accept-Language": "en-US,en;q=0.9"}


# ---------------------------------------------------------------- engines

def bing_search(session: requests.Session, query: str, want: int) -> list[dict]:
    """Return [{image_url, source_url, title}] from Bing Images pages."""
    out, seen = [], set()

    def harvest(doc: str) -> None:
        doc = ihtml.unescape(doc).replace("\\/", "/")
        for m in re.finditer(r'"murl":"(https?://[^"]+)"', doc):
            url = m.group(1)
            if url in seen:
                continue
            window = doc[m.start():m.start() + 1600]
            purl = re.search(r'"purl":"(https?://[^"]+)"', window)
            turl = re.search(r'"turl":"(https?://[^"]+)"', window)
            title = re.search(r'"t":"([^"]{1,160})"', window)
            seen.add(url)
            out.append({
                "image_url": url,
                "thumb_url": turl.group(1) if turl else url,
                "source_url": purl.group(1) if purl else
                              f"https://www.bing.com/images/search?q={quote_plus(query)}",
                "title": (title.group(1) if title else query).strip(),
            })

    try:
        r = session.get("https://www.bing.com/images/search",
                        params={"q": query}, timeout=25)
        harvest(r.text)
        if len(out) < want * 2:  # async page = the "load more" fragment
            time.sleep(1.0)
            r = session.get("https://www.bing.com/images/async/search",
                            params={"q": query, "first": 35, "count": 60,
                                    "mmasync": 1, "scenario": "ImageBasicHover"},
                            timeout=25)
            harvest(r.text)
    except Exception as exc:
        print(f"  [bing:error] {query}: {type(exc).__name__} {str(exc)[:120]}")
    return out


def _pin_walk(node, found: list) -> None:
    if isinstance(node, dict):
        images = node.get("images")
        if isinstance(images, dict):
            best = None
            for key in ("orig", "736x", "600x315", "474x", "236x"):
                entry = images.get(key)
                if isinstance(entry, dict) and entry.get("url"):
                    best = entry["url"]
                    break
            if best:
                pin_id = node.get("id")
                title = (node.get("title") or node.get("grid_title")
                         or node.get("rich_summary", {}).get("display_name")
                         or "")
                found.append({
                    "image_url": best,
                    "source_url": (f"https://www.pinterest.com/pin/{pin_id}/"
                                   if pin_id else
                                   "https://www.pinterest.com/search/pins/"),
                    "title": str(title).strip(),
                })
        for value in node.values():
            _pin_walk(value, found)
    elif isinstance(node, list):
        for item in node:
            _pin_walk(item, found)


def pinterest_search(session: requests.Session, query: str) -> list[dict]:
    """Guest internal-API search; returns [] when Pinterest refuses."""
    page_url = f"https://www.pinterest.com/search/pins/?q={quote_plus(query)}"
    try:
        session.get(page_url, timeout=25)
    except Exception as exc:
        print(f"  [pinterest:error] page: {type(exc).__name__} {str(exc)[:100]}")
        return []
    csrf = session.cookies.get("csrftoken")
    if not csrf:
        print("  [pinterest] no csrftoken cookie — guest session refused")
        return []
    payload = {"options": {"query": query, "scope": "pins", "page_size": 25},
               "context": {}}
    headers = {"X-Requested-With": "XMLHttpRequest", "X-CSRFToken": csrf,
               "Referer": page_url,
               "Accept": "application/json, text/javascript, */*, q=0.01"}
    url = "https://www.pinterest.com/resource/BaseSearchResource/get/"
    form = {"source_url": f"/search/pins/?q={quote_plus(query)}",
            "data": json.dumps(payload)}
    resp = None
    for attempt in range(2):
        try:
            resp = session.post(url, data=form, headers=headers, timeout=25)
            break
        except Exception as exc:
            print(f"  [pinterest:error] api try {attempt + 1}: "
                  f"{type(exc).__name__} {str(exc)[:100]}")
            time.sleep(2)
    if resp is None:
        return []
    try:
        data = resp.json()
    except Exception:
        print(f"  [pinterest] API HTTP {resp.status_code}, non-JSON reply "
              f"({len(resp.text)} bytes) — treated as blocked")
        return []
    found: list[dict] = []
    _pin_walk(data, found)
    dedup, seen = [], set()
    for item in found:
        if item["image_url"] not in seen:
            seen.add(item["image_url"])
            if not item["title"]:
                item["title"] = f"Pinterest — {query}"
            dedup.append(item)
    return dedup


def google_search(session: requests.Session, query: str) -> list[dict]:
    """Best-effort: modern Google Images needs JS; basic variants serve an
    empty mobile shell from most networks. Returns what is findable."""
    try:
        r = session.get("https://www.google.com/search",
                        params={"q": query, "udm": "2", "hl": "en", "gl": "us"},
                        timeout=25)
    except Exception as exc:
        print(f"  [google:error] {type(exc).__name__} {str(exc)[:100]}")
        return []
    doc = ihtml.unescape(r.text)
    hits = re.findall(r'\["(https?://[^"]+\.(?:jpg|jpeg|png|webp))",\d+,\d+\]', doc)
    out = [{"image_url": u,
            "source_url": f"https://www.google.com/search?q={quote_plus(query)}&udm=2",
            "title": query}
           for u in dict.fromkeys(hits)]
    if not out:
        print(f"  [google] page served ({len(doc)} bytes) but no image data "
              f"in it — JS shell, treated as unavailable")
    return out


ENGINES = {"bing": lambda s, q, w: bing_search(s, q, w),
           "pinterest": lambda s, q, w: pinterest_search(s, q),
           "google": lambda s, q, w: google_search(s, q)}


# ---------------------------------------------------------------- storage

def store(session, conn, known_hashes, known_urls, item, delay) -> bool:
    """Same rules as crawl.py: known-URL skip, >=200px, phash dedupe."""
    url = item["image_url"]
    if url in known_urls:
        return False
    img, raw = download_image(session, url)
    time.sleep(delay + random.uniform(0, 0.5))
    if img is None or img.width < MIN_SIZE or img.height < MIN_SIZE:
        return False
    ph = phash_of(img.convert("RGB"))
    if is_near_duplicate(ph, known_hashes):
        return False
    ext = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp",
           "GIF": ".gif", "BMP": ".bmp"}.get(img.format or "JPEG", ".jpg")
    name = hashlib.sha1(url.encode()).hexdigest()[:16] + ext
    rel_path = os.path.join("data", "images", name)
    with open(os.path.join(core.BASE_DIR, rel_path), "wb") as fh:
        fh.write(raw)
    conn.execute(
        "INSERT OR IGNORE INTO images (image_path, image_url, source_url,"
        " page_title, platform, phash, width, height, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (rel_path, url, item["source_url"], item["title"],
         core.platform_of(item["source_url"]), ph,
         img.width, img.height, time.time()))
    conn.commit()
    known_hashes.append(ph)
    known_urls.add(url)
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch realtime images from "
                                             "web image search engines")
    ap.add_argument("--queries", required=True,
                    help="comma-separated search queries")
    ap.add_argument("--per-query", type=int, default=12,
                    help="max NEW images stored per query per engine")
    ap.add_argument("--engines", default="bing,pinterest,google")
    ap.add_argument("--delay", type=float, default=1.2)
    args = ap.parse_args()

    core.init_db()
    conn = core.connect()
    known_hashes = [r["phash"] for r in conn.execute(
        "SELECT phash FROM images WHERE phash != ''")]
    known_urls = {r["image_url"] for r in conn.execute(
        "SELECT image_url FROM images")}

    dl_session = requests.Session()  # image downloads, default UA like crawl
    totals: dict[str, int] = {}
    for engine_name in [e.strip() for e in args.engines.split(",") if e.strip()]:
        fetcher = ENGINES.get(engine_name)
        if fetcher is None:
            print(f"[skip] unknown engine {engine_name}")
            continue
        session = requests.Session()
        session.headers.update(HEADERS)
        stored_engine = 0
        for query in [q.strip() for q in args.queries.split(",") if q.strip()]:
            print(f"[{engine_name}] query: {query}")
            try:
                candidates = fetcher(session, query, args.per_query)
            except Exception as exc:
                print(f"  [error] {type(exc).__name__} {str(exc)[:120]}")
                candidates = []
            print(f"  candidates: {len(candidates)}")
            stored = 0
            for item in candidates:
                if stored >= args.per_query:
                    break
                if store(dl_session, conn, known_hashes, known_urls,
                         item, args.delay):
                    stored += 1
                    print(f"  [ok] {item['image_url'][:100]}")
            print(f"  stored: {stored}")
            stored_engine += stored
            time.sleep(args.delay + random.uniform(0, 0.5))
        totals[engine_name] = stored_engine

    print("\n=== WebFetch summary ===")
    for name, count in totals.items():
        print(f"{name}: {count} new images stored")
    print("Next step: python index.py")


if __name__ == "__main__":
    main()
