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
        r = session.get(url, timeout=10)
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
                sites: list[str], max_pages: int = 10,
                per_page: int = 40, seed_per_page: int = 80) -> list[dict]:
    """Our own crawler over seed sites: jo page user ne diya hai wo
    INDEX page hai — us ki SARI images (photos + video posters +
    embedded-video thumbnails) harvest hoti hain; phir same-site
    links jin ke URL me topic word ho (1 level deep) bhi visit hote
    hain."""
    words = [w for w in re.findall(r"[a-z0-9]+", query.lower())
             if len(w) >= 4]
    out: list[dict] = []
    seen_pages: set[str] = set()

    def visit(url: str, depth: int, cap: int) -> None:
        if url in seen_pages or len(seen_pages) >= max_pages:
            return
        seen_pages.add(url)
        got = _fetch_soup(session, url)
        if got is None:
            return
        soup, title = got
        for img_url in crawl.candidate_image_urls(soup, url)[:cap]:
            out.append({"image_url": img_url, "source_url": url,
                        "title": title, "origin": "web"})
        if depth > 0:
            for link in huntermod.page_links(soup, url):
                if any(w in link.lower() for w in words):
                    visit(link, depth - 1, per_page)

    for s in sites:
        visit(s, 1, seed_per_page)
    return out


def bing_links(session: requests.Session, query: str,
               limit: int = 12) -> list[str]:
    """Bing WEB results -> sirf page links (u=a1<base64> decode).
    Engine ki IMAGES kabhi nahi — sirf links."""
    import base64
    try:
        r = session.get("https://www.bing.com/search?q="
                        + urllib.parse.quote_plus(query), timeout=12)
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
                        + urllib.parse.quote_plus(query), timeout=12)
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


def _external_links(soup, own_hosts: tuple, limit: int) -> list[str]:
    """Generic results page se external page/post links (engine ke
    apne domains + utility paths chor kar)."""
    out: list[str] = []
    seen: set[str] = set()
    junk = ("/search?", "login", "signup", "sign-in", "settings",
            "preferences", "/help", "/about", "privacy", "terms",
            "cookies", "support.", "account.")
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("//"):
            href = "https:" + href
        if not href.startswith(("http://", "https://")):
            continue
        host = urllib.parse.urlparse(href).netloc.lower()
        if any(h in host for h in own_hosts):
            continue
        if any(x in href.lower() for x in junk):
            continue
        key = href.split("#")[0]
        if key not in seen:
            seen.add(key)
            out.append(key)
        if len(out) >= limit:
            break
    return out


def _engine_get(session, url: str, name: str):
    from bs4 import BeautifulSoup
    try:
        r = session.get(url, timeout=10)
        return BeautifulSoup(r.text, "html.parser")
    except Exception as exc:
        print(f"  [links:{name}] {type(exc).__name__}")
        return None


def google_links(session, query: str, limit: int = 10) -> list[str]:
    """Google WEB results -> /url?q= wrappers se asal page links.
    Datacenter IPs par aksar block hota hai — 0 aye to koi nuqsan
    nahi, baqi engines chalte hain."""
    soup = _engine_get(session, "https://www.google.com/search?q="
                       + urllib.parse.quote_plus(query)
                       + "&num=20&hl=en", "google")
    if soup is None:
        return []
    out: list[str] = []
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if h.startswith("/url?"):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(h).query)
            u = (qs.get("q") or [""])[0]
            if u.startswith("http") and "google." not in u:
                out.append(u)
    if not out:
        out = _external_links(soup, ("google.", "gstatic."), limit)
    return list(dict.fromkeys(out))[:limit]


def brave_links(session, query: str, limit: int = 10) -> list[str]:
    soup = _engine_get(session, "https://search.brave.com/search?q="
                       + urllib.parse.quote_plus(query), "brave")
    return [] if soup is None else _external_links(
        soup, ("brave.com",), limit)


def yahoo_links(session, query: str, limit: int = 10) -> list[str]:
    soup = _engine_get(session, "https://search.yahoo.com/search?p="
                       + urllib.parse.quote_plus(query), "yahoo")
    if soup is None:
        return []
    out: list[str] = []
    for a in soup.find_all("a", href=True):
        h = a["href"]
        m = re.search(r"RU=([^/]+)", h)
        if m:
            u = urllib.parse.unquote(m.group(1))
            if u.startswith("http") and "yahoo." not in u:
                out.append(u)
    if not out:
        out = _external_links(soup, ("yahoo.",), limit)
    return list(dict.fromkeys(out))[:limit]


def mojeek_links(session, query: str, limit: int = 10) -> list[str]:
    soup = _engine_get(session, "https://www.mojeek.com/search?q="
                       + urllib.parse.quote_plus(query), "mojeek")
    return [] if soup is None else _external_links(
        soup, ("mojeek.com",), limit)


def ecosia_links(session, query: str, limit: int = 10) -> list[str]:
    soup = _engine_get(session, "https://www.ecosia.org/search?q="
                       + urllib.parse.quote_plus(query), "ecosia")
    return [] if soup is None else _external_links(
        soup, ("ecosia.org",), limit)


ENGINES = [("Bing", bing_links), ("DuckDuckGo", ddg_links),
           ("Google", google_links), ("Brave", brave_links),
           ("Yahoo", yahoo_links), ("Mojeek", mojeek_links),
           ("Ecosia", ecosia_links)]


def discover_links(session: requests.Session, query: str,
                   per_engine: int = 10) -> list[dict]:
    """SEARCH ON SEARCH: SAARE engines se sirf LINKS uthao (query +
    facebook + instagram variants taake posts bhi ayen). Images kisi
    engine ki kabhi nahi — har link ko hamara engine khud visit
    karega. Har link ke saath record hota hai ke kis ENGINE ne wo
    link diya; per-engine ginati me 0 wale engines bhi dikhte hain
    (konsa engine chala, konsa block hua — sab saaf)."""
    from concurrent.futures import ThreadPoolExecutor as _TPE
    links: list[dict] = []
    seen: set[str] = set()
    per: dict[str, int] = {name: 0 for name, _ in ENGINES}
    queries = (query, f"{query} facebook", f"{query} instagram")

    def run_engine(args):
        name, fn, q = args
        local = requests.Session()
        local.headers.update(HEADERS)
        try:
            return name, fn(local, q, per_engine)
        except Exception:
            return name, []

    tasks = [(name, fn, q) for q in queries for name, fn in ENGINES]
    with _TPE(max_workers=7) as ex:
        for name, urls in ex.map(run_engine, tasks):
            for u in urls:
                key = u.split("#")[0]
                if key not in seen and "duckduckgo.com" not in key:
                    seen.add(key)
                    links.append({"url": u, "engine": name})
                    per[name] += 1
    print(f"  [search-on-search] {len(links)} links mile {per} "
          f"(sirf links — images hamara engine nikale ga)")
    links_per = per
    for item in links:
        item.setdefault("engine", "")
    discover_links.last_per_engine = links_per
    return links[:60]


def visit_links(session: requests.Session, links: list,
                per_page: int = 40, max_pages: int = 24) -> list[dict]:
    """Har link ko hamara engine KHUD visit karta hai: aam websites se
    page ki SARI images (ek link = kai images), social posts/pages se
    OG adapters ke zariye. Visits parallel hain (6 workers) taake
    overall scan tez ho. Har item par 'via' = wo engine jis ne link
    diya tha."""
    from concurrent.futures import ThreadPoolExecutor as _TPE

    def one(link) -> list[dict]:
        if isinstance(link, dict):
            u, via = link["url"], link.get("engine", "")
        else:
            u, via = link, ""

        def tag(items: list[dict]) -> list[dict]:
            return [dict(it, via=via) for it in items]

        if "facebook.com" in u or "instagram.com" in u:
            kind = "facebook" if "facebook.com" in u else "instagram"
            got = social.ADAPTERS[kind](u)
            if not got:
                print(f"  [LOGIN_REQUIRED] {u[:80]} — public data nahi "
                      f"mila; gehra analysis login se hoga")
            return tag([dict(item, origin=kind) for item in got])
        if "youtube.com/watch" in u:
            m = re.search(r"v=([\w-]{11})", u)
            if m:
                return tag([{
                    "image_url": f"https://i.ytimg.com/vi/{m.group(1)}"
                                 f"/hqdefault.jpg",
                    "source_url": u, "title": "YouTube video",
                    "origin": "youtube"}])
            return []
        if "youtube.com/" in u:
            return tag([dict(item, origin="youtube")
                        for item in social.youtube_images(u)])
        local = requests.Session()
        local.headers.update(HEADERS)
        got = _fetch_soup(local, u)
        if got is None:
            return []
        soup, title = got
        return tag([{"image_url": img_url, "source_url": u,
                     "title": title, "origin": "web"}
                    for img_url in
                    crawl.candidate_image_urls(soup, u)[:per_page]])

    with _TPE(max_workers=6) as ex:
        parts = list(ex.map(one, links[:max_pages]))
    return [item for part in parts for item in part]


def harvest(query: str, sites: list[str] | None = None,
            on_event=None) -> list[dict]:
    """Overall sweep — SEARCH ON SEARCH upar, baqi sources neeche:
    engine links -> hamara visit; YouTube adapter; Commons library;
    --sites par apna crawler. on_event(kind, data) progress ke liye:
    ("links", {total, per_engine}) aur ("harvested", {origin: n})."""
    session = requests.Session()
    session.headers.update(HEADERS)
    found: dict[str, dict] = {}

    def add(item: dict) -> None:
        if item.get("image_url"):
            found.setdefault(item["image_url"], item)

    links = discover_links(session, query)
    if on_event:
        per_eng = getattr(discover_links, "last_per_engine", None)
        if not per_eng:
            from collections import Counter
            per_eng = dict(Counter(l["engine"] for l in links))
        on_event("links", {"total": len(links), "per_engine": per_eng})
    for item in visit_links(session, links):
        add(item)
    for item in youtube_search(session, query):
        add(dict(item, via="Direct"))
    for item in commons_search(session, query):
        add(dict(item, via="Direct"))
    if sites:
        for item in crawl_topic(session, query, sites):
            add(dict(item, via="Direct"))
    if on_event:
        from collections import Counter
        on_event("harvested", dict(Counter(
            i["origin"] for i in found.values())))
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
