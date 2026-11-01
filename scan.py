#!/usr/bin/env python3
"""SCAN mode — ONE search, overall scan (Al's spec, 2026-11-01).

ONE query sweeps every public surface at the same time — with OUR OWN
pipeline only. No Bing / Pinterest / Google image engines anywhere
(Al's standing order): candidates come from
  * YouTube search (platform adapter, no login)
  * Wikimedia Commons — a public media library our system reads
    directly, like any website
  * our own crawler over seed sites (--sites), following topic links
  * Facebook / Instagram pages found along the way, harvested through
    the OG crawler route (social.py, no login)
Every candidate is downloaded once, embedded with CLIP and ranked
against the query text (or, with --tag, matched against a reference
set too). Sources that answer with a login wall are reported as
LOGIN_REQUIRED instead of failing silently.

Usage:
  python scan.py "pakistan logo on social media" [--limit 150]
                 [--tag islamic] [--save data/scan/<slug>]
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import requests
from PIL import Image

import core
import crawl
import social

MIN_DIM = 150
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/124.0 Safari/537.36"}


def youtube_search(session: requests.Session, query: str,
                   limit: int = 30) -> list[dict]:
    url = ("https://www.youtube.com/results?search_query="
           + urllib.parse.quote_plus(query))
    try:
        r = session.get(url, timeout=25)
        r.raise_for_status()
    except Exception as exc:
        print(f"  [youtube] search failed: {type(exc).__name__}")
        return []
    ids = list(dict.fromkeys(re.findall(r'"videoId":"([\w-]{11})"',
                                        r.text)))[:limit]
    return [{"image_url": f"https://i.ytimg.com/vi/{v}/hqdefault.jpg",
             "source_url": f"https://www.youtube.com/watch?v={v}",
             "title": "YouTube video", "origin": "youtube"}
            for v in ids]


def commons_search(session: requests.Session, query: str,
                   limit: int = 50) -> list[dict]:
    """Wikimedia Commons — a public media LIBRARY (a content host our
    system reads directly through its own API, like any website).
    source_url is the real Commons file page."""
    params = {
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"{query} filetype:bitmap", "gsrnamespace": 6,
        "gsrlimit": limit, "prop": "imageinfo",
        "iiprop": "url|size", "iiurlwidth": 500,
    }
    try:
        r = session.get("https://commons.wikimedia.org/w/api.php",
                        params=params, timeout=25)
        r.raise_for_status()
        pages = (r.json().get("query") or {}).get("pages") or {}
    except Exception as exc:
        print(f"  [commons] failed: {type(exc).__name__}")
        return []
    out = []
    for p in pages.values():
        ii = (p.get("imageinfo") or [{}])[0]
        if (ii.get("width") or 0) < MIN_DIM or \
                (ii.get("height") or 0) < MIN_DIM:
            continue
        out.append({
            "image_url": ii.get("thumburl") or ii.get("url"),
            "source_url": ii.get("descriptionurl"),
            "title": (p.get("title") or "Commons file").replace(
                "File:", ""),
            "origin": "commons"})
    return out


def crawl_topic(session: requests.Session, query: str,
                sites: list[str], max_pages: int = 8,
                per_page: int = 20) -> list[dict]:
    """Our own crawler over seed sites: harvest page images, follow
    same-site links whose URL carries a topic word (1 level deep)."""
    words = [w for w in re.findall(r"[a-z0-9]+", query.lower())
             if len(w) >= 4]
    out: list[dict] = []
    seen_pages: set[str] = set()

    def visit(url: str, depth: int) -> None:
        if url in seen_pages or len(seen_pages) >= max_pages:
            return
        seen_pages.add(url)
        page = crawl.fetch_page(session, url)
        if page is None:
            return
        soup = page["soup"]
        for img_url, alt in crawl.extract_images(soup, url)[:per_page]:
            out.append({"image_url": img_url, "source_url": url,
                        "title": alt or page["title"], "origin": "web"})
        if depth > 0:
            for link in crawl.extract_links(soup, url,
                                            same_host_only=True):
                if any(w in link.lower() for w in words):
                    visit(link, depth - 1)

    for s in sites:
        visit(s, 1)
    return out


def harvest(query: str, sites: list[str] | None = None) -> list[dict]:
    """Overall sweep — OUR pipeline only (no image-search engines):
    YouTube adapter + Commons library + own crawl of seed sites +
    OG harvest of any social pages found along the way."""
    session = requests.Session()
    session.headers.update(HEADERS)
    found: dict[str, dict] = {}

    def add(item: dict) -> None:
        if item.get("image_url"):
            found.setdefault(item["image_url"], item)

    for item in youtube_search(session, query):
        add(item)
    for item in commons_search(session, query):
        add(item)
    if sites:
        for item in crawl_topic(session, query, sites):
            add(item)

    # Social pages found inside the harvested set -> OG adapters
    social_urls = [i["source_url"] for i in list(found.values())
                   if i.get("source_url") and (
                       "facebook.com" in i["source_url"]
                       or "instagram.com" in i["source_url"])]
    for u in list(dict.fromkeys(social_urls))[:8]:
        kind = "facebook" if "facebook.com" in u else "instagram"
        got = social.ADAPTERS[kind](u)
        if not got:
            print(f"  [LOGIN_REQUIRED] {u[:80]} — public data nahi "
                  f"mila; gehra analysis login se hoga")
        for item in got:
            add(dict(item, origin=kind))
    return list(found.values())


def _download(item: dict) -> dict | None:
    try:
        r = requests.get(item["image_url"], headers=HEADERS,
                         timeout=15)
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content))
        img.load()
        if img.width < MIN_DIM or img.height < MIN_DIM:
            return None
        return dict(item, img=img.convert("RGB"))
    except Exception:
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--limit", type=int, default=150)
    ap.add_argument("--tag", default="",
                    help="also match against references with this tag")
    ap.add_argument("--sites", default="",
                    help="comma-separated seed sites for our own "
                         "crawler (topic-guided, 1 level deep)")
    ap.add_argument("--save", default="",
                    help="folder to save the top results into")
    args = ap.parse_args()

    print(f"SCAN: {args.query!r} — overall sweep shuru")
    sites = [s.strip() for s in args.sites.split(",") if s.strip()]
    cands = harvest(args.query, sites=sites)
    from collections import Counter
    print("harvested by origin:", dict(Counter(
        c["origin"] for c in cands)), "| total:", len(cands))
    cands = cands[: args.limit]

    with ThreadPoolExecutor(max_workers=8) as ex:
        ok = [d for d in ex.map(_download, cands) if d]
    print(f"downloaded + usable: {len(ok)}")

    vecs = core.embed_pil_images([d["img"] for d in ok])
    _q = core.embed_text(args.query)
    qvec = _q[0] if _q.ndim == 2 else _q
    scores = (vecs @ qvec).tolist()

    ref_scores: list[float] | None = None
    if args.tag:
        import reference
        core.init_db()
        conn = core.connect()
        reference.ensure_tables(conn)
        index = reference.load_ref_index()
        tag_ids = {int(r[0]) for r in conn.execute(
            "SELECT id FROM ref_images WHERE tag = ?", (args.tag,))}
        if index is not None and tag_ids:
            s, ids = index.search(vecs, 8)
            ref_scores = []
            for row_s, row_i in zip(s, ids):
                best = -1.0
                for sv, iv in zip(row_s, row_i):
                    if int(iv) in tag_ids:
                        best = float(sv)
                        break
                ref_scores.append(best)

    order = sorted(range(len(ok)), key=lambda i: -scores[i])
    kept: list[int] = []
    kept_vec_idx: list[int] = []
    for i in order:  # near-duplicate suppression at cosine > 0.98
        if kept_vec_idx:
            sims = vecs[kept_vec_idx] @ vecs[i]
            if float(sims.max()) > 0.98:
                continue
        kept.append(i)
        kept_vec_idx.append(i)
        if len(kept) >= 24:
            break

    print(f"\nTOP RESULTS for {args.query!r} (text-match %):")
    results = []
    for rank, i in enumerate(kept, 1):
        d = ok[i]
        ref = (f" | ref-match {ref_scores[i] * 100:.1f}%"
               if ref_scores is not None else "")
        print(f" {rank:2d}. {scores[i] * 100:5.1f}% [{d['origin']:9s}] "
              f"{d['title'][:46]:46s} {ref}")
        print(f"      src: {d['source_url'][:92]}")
        results.append({"rank": rank, "score": round(scores[i] * 100, 1),
                        "origin": d["origin"], "title": d["title"],
                        "source_url": d["source_url"],
                        "image_url": d["image_url"],
                        "ref_score": (round(ref_scores[i] * 100, 1)
                                      if ref_scores is not None else None)})
        if args.save:
            os.makedirs(args.save, exist_ok=True)
            d["img"].save(os.path.join(
                args.save, f"{rank:02d}-{d['origin']}.jpg"), quality=85)
    if args.save:
        with open(os.path.join(args.save, "results.json"), "w") as f:
            json.dump(results, f, indent=1)
        print("saved:", args.save)
    print("scan done")


if __name__ == "__main__":
    main()
