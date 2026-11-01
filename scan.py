#!/usr/bin/env python3
"""SCAN mode — ONE search, overall scan (Al's spec, 2026-11-01).

ONE query sweeps every public surface at the same time — with OUR OWN
pipeline. The technique (Al's spec) is SEARCH ON SEARCH:
  * the tool asks several search engines for the query — but takes
    ONLY page/post LINKS from them, never their image results;
  * OUR engine then VISITS each link itself and extracts the images
    from the actual website/post (social posts via OG adapters);
  * plus platform/library sources read directly: YouTube search
    adapter and the Wikimedia Commons media library;
  * plus our own crawler over seed sites (--sites).
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
import hunter as huntermod
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


def _fetch_soup(session: requests.Session, url: str):
    """Visit a page OURSELVES (the Search-on-Search rule: engines give
    links, we do the visiting). Returns (soup, title) or None."""
    from bs4 import BeautifulSoup
    try:
        r = session.get(url, timeout=25)
        r.raise_for_status()
    except Exception:
        return None
    if "text/html" not in r.headers.get("Content-Type", "text/html"):
        return None
    if crawl.looks_like_login_page(r.url, r.text[:4000]):
        return None
    soup = BeautifulSoup(r.text, "html.parser")
    title = (soup.title.string.strip()
             if soup.title and soup.title.string else url)
    return soup, title


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
        got = _fetch_soup(session, url)
        if got is None:
            return
        soup, title = got
        for img_url in crawl.candidate_image_urls(soup, url)[:per_page]:
            out.append({"image_url": img_url, "source_url": url,
                        "title": title, "origin": "web"})
        if depth > 0:
            for link in huntermod.page_links(soup, url):
                if any(w in link.lower() for w in words):
                    visit(link, depth - 1)

    for s in sites:
        visit(s, 1)
    return out


def bing_links(session: requests.Session, query: str,
               limit: int = 12) -> list[str]:
    """Bing WEB results -> sirf page links (u=a1<base64> decode).
    Engine ki IMAGES kabhi nahi — sirf links."""
    import base64
    try:
        r = session.get("https://www.bing.com/search?q="
                        + urllib.parse.quote_plus(query), timeout=25)
        r.raise_for_status()
    except Exception as exc:
        print(f"  [links:bing] {type(exc).__name__}")
        return []
    doc = r.text.replace("&amp;", "&")
    out = []
    for m in re.findall(r'[?&]u=a1([A-Za-z0-9_-]+)', doc):
        try:
            pad = "=" * (-len(m) % 4)
            u = base64.urlsafe_b64decode(m + pad).decode("utf-8",
                                                        "ignore")
        except Exception:
            continue
        if u.startswith("http") and "bing.com" not in u \
                and "microsoft.com" not in u:
            out.append(u)
    return list(dict.fromkeys(out))[:limit]


def ddg_links(session: requests.Session, query: str,
              limit: int = 12) -> list[str]:
    """DuckDuckGo HTML results -> sirf page/post links (uddg decode)."""
    try:
        r = session.get("https://html.duckduckgo.com/html/?q="
                        + urllib.parse.quote_plus(query), timeout=25)
        r.raise_for_status()
    except Exception as exc:
        print(f"  [links:ddg] {type(exc).__name__}")
        return []
    out = []
    for href in re.findall(r'class="result__a" href="([^"]+)"', r.text):
        m = re.search(r'uddg=([^&]+)', href)
        u = urllib.parse.unquote(m.group(1)) if m else href
        if u.startswith("http"):
            out.append(u)
    return list(dict.fromkeys(out))[:limit]


def discover_links(session: requests.Session, query: str,
                   per_engine: int = 10) -> list[str]:
    """SEARCH ON SEARCH: engines se sirf LINKS uthao (query + facebook
    + instagram variants taake posts bhi ayen). Images engine ki kabhi
    nahi — har link ko hamara engine khud visit karega."""
    links: list[str] = []
    seen: set[str] = set()
    for q in (query, f"{query} facebook", f"{query} instagram"):
        for fn in (ddg_links, bing_links):
            for u in fn(session, q, per_engine):
                key = u.split("#")[0]
                if key not in seen and "duckduckgo.com" not in key:
                    seen.add(key)
                    links.append(u)
    print(f"  [search-on-search] {len(links)} links mile "
          f"(sirf links — images hamara engine nikale ga)")
    return links[:36]


def visit_links(session: requests.Session, links: list[str],
                per_page: int = 20, max_pages: int = 24) -> list[dict]:
    """Har link ko hamara engine KHUD visit karta hai: aam websites se
    page ki images, social posts/pages se OG adapters ke zariye."""
    import time as _time
    out: list[dict] = []
    for u in links[:max_pages]:
        _time.sleep(0.4)
        if "facebook.com" in u or "instagram.com" in u:
            kind = "facebook" if "facebook.com" in u else "instagram"
            got = social.ADAPTERS[kind](u)
            if not got:
                print(f"  [LOGIN_REQUIRED] {u[:80]} — public data nahi "
                      f"mila; gehra analysis login se hoga")
            for item in got:
                out.append(dict(item, origin=kind))
            continue
        if "youtube.com/watch" in u:
            m = re.search(r"v=([\w-]{11})", u)
            if m:
                out.append({
                    "image_url": f"https://i.ytimg.com/vi/{m.group(1)}"
                                 f"/hqdefault.jpg",
                    "source_url": u, "title": "YouTube video",
                    "origin": "youtube"})
            continue
        if "youtube.com/" in u:
            for item in social.youtube_images(u):
                out.append(dict(item, origin="youtube"))
            continue
        got = _fetch_soup(session, u)
        if got is None:
            continue
        soup, title = got
        for img_url in crawl.candidate_image_urls(soup, u)[:per_page]:
            out.append({"image_url": img_url, "source_url": u,
                        "title": title, "origin": "web"})
    return out


def harvest(query: str, sites: list[str] | None = None) -> list[dict]:
    """Overall sweep — SEARCH ON SEARCH upar, baqi sources neeche:
    engine links -> hamara visit; YouTube adapter; Commons library;
    --sites par apna crawler."""
    session = requests.Session()
    session.headers.update(HEADERS)
    found: dict[str, dict] = {}

    def add(item: dict) -> None:
        if item.get("image_url"):
            found.setdefault(item["image_url"], item)

    links = discover_links(session, query)
    for item in visit_links(session, links):
        add(item)
    for item in youtube_search(session, query):
        add(item)
    for item in commons_search(session, query):
        add(item)
    if sites:
        for item in crawl_topic(session, query, sites):
            add(item)
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
