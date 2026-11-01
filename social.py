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


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(webfetch.HEADERS)
    return s


def _fetch(url: str) -> str | None:
    try:
        r = _session().get(url, timeout=25)
        return r.text
    except Exception as exc:
        print(f"  [social:error] {url[:70]}: {type(exc).__name__} "
              f"{str(exc)[:90]}")
        return None


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
    html = _fetch(url)
    if html is None:
        return []
    doc = html.replace("\\u0026", "&").replace("\\/", "/")
    # Only real photo CDN URLs (scontent…) — static.cdninstagram.com/rsrc
    # files are Instagram's own interface graphics, not user photos.
    urls = list(dict.fromkeys(re.findall(
        r'https://scontent[^"\' ]+?\.(?:jpg|jpeg|webp)(?:\?[^"\' ]*)?', doc)))
    out = []
    og = _og(html, "image")
    title = _og(html, "title") or "Instagram"
    if og:
        out.append({"image_url": og, "source_url": url, "title": title})
    for u in urls:
        if u != og:
            out.append({"image_url": u, "source_url": url, "title": title})
        if len(out) >= MAX_PER_SOURCE:
            break
    if not out:
        print(f"  [instagram] no public images at {url[:70]} "
              f"(login wall or JS-only page)")
    else:
        print(f"  [instagram] {len(out)} public images from {url[:70]}")
    return out


def facebook_images(url: str) -> list[dict]:
    html = _fetch(url)
    if html is None:
        return []
    doc = html.replace("\\u0026", "&").replace("\\/", "/")
    urls = list(dict.fromkeys(re.findall(
        r'https://scontent[^"\' ]*?fbcdn\.net[^"\' ]*?\.(?:jpg|jpeg|png)'
        r'(?:\?[^"\' ]*)?', doc)))
    out = []
    og = _og(html, "image")
    title = _og(html, "title") or "Facebook"
    if og:
        out.append({"image_url": og, "source_url": url, "title": title})
    for u in urls:
        if u != og:
            out.append({"image_url": u, "source_url": url, "title": title})
        if len(out) >= MAX_PER_SOURCE:
            break
    if not out:
        print(f"  [facebook] no public images at {url[:70]} "
              f"(login wall or JS-only page)")
    else:
        print(f"  [facebook] {len(out)} public images from {url[:70]}")
    return out


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
