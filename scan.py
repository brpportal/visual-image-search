#!/usr/bin/env python3
"""SCAN mode — ONE search, overall scan (Al's spec, 2026-11-01).

One query sweeps every public surface at the same time:
  * web image results (Bing + Pinterest public pages — discovery only,
    used to FIND public images/pages; extraction + matching is ours)
  * YouTube search results (thumbnails, no login)
  * Facebook / Instagram URLs discovered in the results, harvested
    through the OG crawler route (social.py, no login)
  * Instagram hashtag pages (crawler UA)
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
import social
import webfetch

MIN_DIM = 150


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


def discover_social(session: requests.Session,
                    query: str) -> dict[str, list[str]]:
    """Find public Facebook/Instagram page/post URLs for the query in
    public WEB search results (the phone book) — the pages themselves
    are then harvested by our own OG adapters. Bing wraps outbound
    links as bing.com/ck/a?...&u=a1<base64url-of-target>, so decode
    those too."""
    import base64
    urls: dict[str, list[str]] = {"facebook": [], "instagram": []}
    for kind in ("facebook", "instagram"):
        try:
            r = session.get(
                "https://www.bing.com/search?q="
                + urllib.parse.quote_plus(f"{query} {kind}"), timeout=20)
            raw = re.findall(r'href="(https?://[^"&]+)', r.text)
            raw += re.findall(r'[?&]u=a1([A-Za-z0-9_-]+)', r.text)
            for u in raw:
                if "bing.com" in u and "a1" not in u:
                    continue
                if not u.startswith("http"):
                    try:
                        pad = "=" * (-len(u) % 4)
                        u = base64.urlsafe_b64decode(u + pad).decode(
                            "utf-8", "ignore")
                    except Exception:
                        continue
                u = u.replace("&amp;", "&")
                if f"{kind}.com" not in u:
                    continue
                if any(b in u for b in ("/sharer", "/dialog", "/plugins",
                                        "/tr?", "login")):
                    continue
                if u not in urls[kind]:
                    urls[kind].append(u)
        except Exception:
            pass
    return urls


def harvest(query: str) -> list[dict]:
    session = requests.Session()
    session.headers.update(webfetch.HEADERS)
    found: dict[str, dict] = {}

    def add(item: dict) -> None:
        found.setdefault(item["image_url"], item)

    for item in webfetch.bing_search(session, query, want=80):
        item = dict(item, origin="web")
        add(item)
    for item in webfetch.pinterest_search(session, query):
        add(dict(item, origin="web"))
    for item in youtube_search(session, query):
        add(item)

    # Targeted discovery passes: social pages rarely surface in plain
    # image results, so look for them by name — only their page URLs
    # are used (routed to the OG adapters below), images stay "web".
    for suffix in ("facebook", "instagram"):
        for item in webfetch.bing_search(session, f"{query} {suffix}",
                                         want=30):
            add(dict(item, origin="web"))

    # Social discovery: URLs inside image results + a web-search pass
    social_urls = [i["source_url"] for i in list(found.values())
                   if "facebook.com" in i["source_url"]
                   or "instagram.com" in i["source_url"]]
    disc = discover_social(session, query)
    social_urls += disc["facebook"][:4] + disc["instagram"][:4]
    print("social urls discovered:",
          {k: len(v) for k, v in disc.items()})
    for u in list(dict.fromkeys(social_urls))[:8]:
        kind = "facebook" if "facebook.com" in u else "instagram"
        got = social.ADAPTERS[kind](u)
        if not got:
            print(f"  [LOGIN_REQUIRED] {u[:80]} — public data nahi mila; "
                  f"gehra analysis login se hoga")
        for item in got:
            add(dict(item, origin=kind))

    # Instagram hashtag pages for the query's words (crawler UA)
    words = [w for w in re.findall(r"[a-z0-9]+", query.lower())
             if len(w) >= 4]
    tags = list(dict.fromkeys(words))[:2]
    for tag in tags:
        got = social.instagram_images(
            f"https://www.instagram.com/explore/tags/{tag}/")
        for item in got:
            add(dict(item, origin="instagram"))
    return list(found.values())


def _download(item: dict) -> dict | None:
    try:
        r = requests.get(item["image_url"], headers=webfetch.HEADERS,
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
    ap.add_argument("--save", default="",
                    help="folder to save the top results into")
    args = ap.parse_args()

    print(f"SCAN: {args.query!r} — overall sweep shuru")
    cands = harvest(args.query)
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
