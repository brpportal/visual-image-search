#!/usr/bin/env python3
"""Social media source adapters — pull PUBLIC images from social pages
WITHOUT any login, the same way search engines' crawlers see them.

- YouTube: fully public. Video/channel pages carry every thumbnail on
  the public CDN (i.ytimg.com); video ids + titles are read from the
  page's ytInitialData. No login, no API key.
- Instagram: a public post/page exposes its main image as og:image and
  often CDN (scontent) images in the HTML. Profile grids are login
  gated by Instagram itself — whatever is public is returned, the rest
  is reported, never bypassed.
- Facebook: public Pages often expose og:image + fbcdn images in the
  HTML; many surfaces answer with a login wall instead. Same rule:
  take what is public, report the wall, never bypass it.

Each adapter returns candidate dicts {image_url, source_url, title}
that hunter.py feeds into the normal match pipeline. In sources.txt,
prefix a line with the platform:  youtube: https://…  /  instagram: …
/  facebook: …
"""
from __future__ import annotations

import html as ihtml
import re
from urllib.parse import parse_qs, urlparse

import requests

import webfetch

MAX_PER_SOURCE = 60

# Social platforms serve OpenGraph data (og:image / og:title — the link
# preview mechanism) to CRAWLER user agents without any login, while a
# browser UA gets a login wall. Verified live 2026-11-01: Facebook page
# + posts and Instagram profiles return OG + content images to the
# Googlebot UA. So adapters ask as a crawler first, browser second.
CRAWLER_UA = ("Mozilla/5.0 (compatible; Googlebot/2.1; "
              "+http://www.google.com/bot.html)")
CRAWLER_HEADERS = {"User-Agent": CRAWLER_UA,
                   "Accept-Language": "en-US,en;q=0.9"}


def _session(headers: dict | None = None) -> requests.Session:
    s = requests.Session()
    s.headers.update(headers or webfetch.HEADERS)
    return s


def _fetch(url: str, headers: dict | None = None,
           timeout: int = 25) -> str | None:
    try:
        r = _session(headers).get(url, timeout=timeout)
        return r.text
    except Exception as exc:
        print(f"  [social:error] {url[:70]}: {type(exc).__name__} "
              f"{str(exc)[:90]}")
        return None


def _extract_fb(doc: str, url: str) -> list[dict]:
    doc = doc.replace("\\u0026", "&").replace("\\/", "/")
    urls = list(dict.fromkeys(re.findall(
        r'https://[^"\' ]*?fbcdn\.net[^"\' ]*?\.(?:jpg|jpeg|png|webp)'
        r'(?:\?[^"\' ]*)?', doc)))
    urls = [u for u in urls if "/rsrc.php/" not in u]
    urls += [u for u in dict.fromkeys(re.findall(
        r'https://lookaside\.fbsbx\.com/[^"\' ]+', doc))
        if u not in urls]
    out = []
    og = _og(doc, "image")
    title = _og(doc, "title") or "Facebook"
    if og:
        out.append({"image_url": og, "source_url": url, "title": title})
    for u in urls:
        if u != og:
            out.append({"image_url": u, "source_url": url,
                        "title": title})
        if len(out) >= MAX_PER_SOURCE:
            break
    return out


def _extract_ig(doc: str, url: str) -> list[dict]:
    doc = doc.replace("\\u0026", "&").replace("\\/", "/")
    # Asli photo CDN URLs (scontent / cdninstagram) — static interface
    # graphics nahi.
    urls = list(dict.fromkeys(re.findall(
        r'https://[^"\' ]*?(?:scontent|cdninstagram)[^"\' ]*?'
        r'\.(?:jpg|jpeg|webp)(?:\?[^"\' ]*)?', doc)))
    urls = [u for u in urls if "static.cdninstagram" not in u
            and "/rsrc" not in u]
    out = []
    og = _og(doc, "image")
    title = _og(doc, "title") or "Instagram"
    if og:
        out.append({"image_url": og, "source_url": url, "title": title})
    for u in urls:
        if u != og:
            out.append({"image_url": u, "source_url": url,
                        "title": title})
        if len(out) >= MAX_PER_SOURCE:
            break
    return out


def _fetch_best(url: str, variants: list[str], extractor,
                label: str) -> list[dict]:
    """Strategy chain: pehle crawler UA, phir browser UA, phir host
    variants (m./mbasic./embed) — jo pehle KAFI images de, wahi jeeta.
    Kisi profile par images ayen aur kisi par na ayen wala masla isi
    chain se hal hota hai: har profile ek hi shakl me nahi khulta."""
    best: list[dict] = []
    strategies: list[tuple[str, dict | None]] = []
    for v in variants:
        strategies.append((v, CRAWLER_HEADERS))
        strategies.append((v, None))
    for v, headers in strategies:
        html = _fetch(v, headers, timeout=15)
        if html is None:
            continue
        got = extractor(html, url)
        if len(got) > len(best):
            best = got
        if len(best) >= 8:
            break
    if not best:
        print(f"  [{label}] no public images at {url[:70]} "
              f"(login wall or JS-only page)")
    else:
        print(f"  [{label}] {len(best)} public images from {url[:70]}")
    return best


def _og(html: str, prop: str) -> str:
    m = re.search(rf'<meta[^>]+property="og:{prop}"[^>]+content="([^"]+)"',
                  html)
    if not m:
        m = re.search(rf'<meta[^>]+content="([^"]+)"[^>]+property="og:{prop}"',
                      html)
    return ihtml.unescape(m.group(1)) if m else ""


def youtube_images(url: str) -> list[dict]:
    """Thumbnails for a watch page, channel, playlist or search page."""
    parsed = urlparse(url)
    vid = parse_qs(parsed.query).get("v", [None])[0]
    if vid:  # single video — its own thumbnails, best quality first
        return [{"image_url": f"https://i.ytimg.com/vi/{vid}/maxresdefault.jpg",
                 "source_url": url, "title": "YouTube video"}]
    html = _fetch(url)
    if html is None:
        return []
    out, seen = [], set()
    for m in re.finditer(r'"videoId":"([A-Za-z0-9_-]{11})"', html):
        vid = m.group(1)
        if vid in seen:
            continue
        seen.add(vid)
        window = html[m.start():m.start() + 900]
        t = re.search(r'"title":\{"runs":\[\{"text":"([^"]{1,120})"', window)
        if not t:
            t = re.search(r'"title":\{"simpleText":"([^"]{1,120})"', window)
        out.append({
            "image_url": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
            "source_url": f"https://www.youtube.com/watch?v={vid}",
            "title": ihtml.unescape(t.group(1)) if t else "YouTube video",
        })
        if len(out) >= MAX_PER_SOURCE:
            break
    og = _og(html, "image")
    if og and all(o["image_url"] != og for o in out):
        out.insert(0, {"image_url": og, "source_url": url,
                       "title": _og(html, "title") or "YouTube"})
    print(f"  [youtube] {len(out)} thumbnails from {url[:70]}")
    return out


def instagram_images(url: str) -> list[dict]:
    # Strategy chain: crawler UA -> browser UA -> post embed page.
    # Profiles link-preview (crawler) ko OG + scontent dete hain;
    # kuch profiles sirf doosri shakl me khulte hain.
    variants = [url]
    m = re.search(r"instagram\.com/(p|reel)/([\w-]+)", url)
    if m:
        variants.append(
            f"https://www.instagram.com/{m.group(1)}/{m.group(2)}/embed/")
    elif "?" not in url:
        variants.append(url.rstrip("/") + "/?hl=en")
    return _fetch_best(url, variants, _extract_ig, "instagram")


def facebook_images(url: str) -> list[dict]:
    # Strategy chain: crawler UA -> browser UA -> m.facebook ->
    # mbasic.facebook (mbasic purani saadgi wali HTML deta hai jisme
    # images seedhi mil jati hain).
    variants = [url]
    p = urlparse(url)
    if p.netloc.endswith("facebook.com") and \
            not p.netloc.startswith(("m.", "mbasic.")):
        variants.append(f"https://m.facebook.com{p.path}")
        variants.append(f"https://mbasic.facebook.com{p.path}")
    return _fetch_best(url, variants, _extract_fb, "facebook")


ADAPTERS = {"youtube": youtube_images,
            "instagram": instagram_images,
            "facebook": facebook_images}


def candidates_for(spec: str) -> tuple[str, list[dict]] | None:
    """'youtube: https://…' -> (page_url, candidates) or None."""
    platform, _, url = spec.partition(":")
    platform = platform.strip().lower()
    url = url.strip()
    fn = ADAPTERS.get(platform)
    if fn is None or not url.startswith("http"):
        return None
    return url, fn(url)
