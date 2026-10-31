#!/usr/bin/env python3
"""Crawler: reads page URLs from urls.txt, downloads public images.

Rules (by design):
- Public pages only. Login-protected pages are skipped (login URL or a
  password form in the HTML). No credential use, no bypassing anything.
- robots.txt is respected per host.
- Polite: 1-2s delay between requests.
- Images must be at least MIN_SIZE px on BOTH sides (default 200).
- Dedupe by perceptual hash (near-identical images are stored once).
- Metadata (source page URL + title + platform) goes to SQLite.

Usage:  python crawl.py [--urls urls.txt] [--delay 1.5] [--max-pages 50]
"""
from __future__ import annotations

import argparse
import hashlib
import os
import random
import time
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import imagehash
import requests
from bs4 import BeautifulSoup
from PIL import Image

import core

MIN_SIZE = 200                 # minimum width AND height in pixels
MAX_IMAGE_BYTES = 10 * 1024 * 1024
PHASH_NEAR_DUP_DISTANCE = 4    # Hamming distance <= this => same image
USER_AGENT = "VisualSearchBot/1.0 (self-hosted personal image index; respects robots.txt)"
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")


def robots_allowed(robots_cache: dict, url: str) -> bool:
    host = urlparse(url).netloc
    if host not in robots_cache:
        parser = RobotFileParser()
        parser.set_url(f"{urlparse(url).scheme}://{host}/robots.txt")
        try:
            parser.read()
        except Exception:
            pass  # unreadable robots.txt -> parser defaults apply
        robots_cache[host] = parser
    try:
        return robots_cache[host].can_fetch(USER_AGENT, url)
    except Exception:
        return True


def looks_like_login_page(final_url: str, html: str) -> bool:
    path = urlparse(final_url).path.lower()
    if "login" in path or "signin" in path or "sign-in" in path:
        return True
    return 'type="password"' in html.lower()


def candidate_image_urls(soup: BeautifulSoup, page_url: str) -> list[str]:
    found: list[str] = []
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        found.append(og["content"])
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
        if src:
            found.append(src)
        srcset = img.get("srcset") or img.get("data-srcset")
        if srcset:
            parts = [p.strip().split(" ")[0] for p in srcset.split(",") if p.strip()]
            if parts:
                found.append(parts[-1])  # largest candidate is usually last
    for a in soup.find_all("a", href=True):
        if a["href"].lower().split("?")[0].endswith(IMAGE_EXTS):
            found.append(a["href"])
    # Resolve + dedupe, keep order
    seen, out = set(), []
    for item in found:
        absolute = urljoin(page_url, item)
        if absolute.startswith(("http://", "https://")) and absolute not in seen:
            seen.add(absolute)
            out.append(absolute)
    return out


def phash_of(img: Image.Image) -> str:
    return str(imagehash.phash(img))


def is_near_duplicate(phash_hex: str, existing_hashes: list[str]) -> bool:
    try:
        new = imagehash.hex_to_hash(phash_hex)
    except Exception:
        return False
    for old in existing_hashes:
        try:
            if new - imagehash.hex_to_hash(old) <= PHASH_NEAR_DUP_DISTANCE:
                return True
        except Exception:
            continue
    return False


def download_image(session: requests.Session, image_url: str):
    """Return (PIL image, raw bytes) or (None, None) on any problem."""
    try:
        resp = session.get(image_url, timeout=20, stream=True)
        resp.raise_for_status()
        raw = b""
        for chunk in resp.iter_content(64 * 1024):
            raw += chunk
            if len(raw) > MAX_IMAGE_BYTES:
                return None, None
        from io import BytesIO

        img = Image.open(BytesIO(raw))
        img.load()
        return img, raw
    except Exception:
        return None, None


def crawl(urls_file: str, delay: float, max_pages: int) -> None:
    core.init_db()
    conn = core.connect()
    known_hashes = [r["phash"] for r in conn.execute(
        "SELECT phash FROM images WHERE phash != ''")]
    known_image_urls = {r["image_url"] for r in conn.execute(
        "SELECT image_url FROM images")}

    with open(urls_file, encoding="utf-8") as fh:
        page_urls = [ln.strip() for ln in fh
                     if ln.strip() and not ln.strip().startswith("#")]

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    robots_cache: dict = {}
    stats = {"pages": 0, "downloaded": 0, "skipped": 0}

    for page_url in page_urls[:max_pages]:
        if not robots_allowed(robots_cache, page_url):
            print(f"[robots] skip {page_url}")
            continue
        try:
            resp = session.get(page_url, timeout=20)
            resp.raise_for_status()
        except Exception as exc:
            print(f"[page-error] {page_url}: {exc}")
            continue
        if looks_like_login_page(resp.url, resp.text):
            print(f"[login-protected] skip {page_url}")
            continue
        stats["pages"] += 1
        soup = BeautifulSoup(resp.text, "html.parser")
        title = (soup.title.string or "").strip() if soup.title else ""
        platform = core.platform_of(resp.url)

        for image_url in candidate_image_urls(soup, resp.url):
            if image_url in known_image_urls:
                stats["skipped"] += 1
                continue
            img, raw = download_image(session, image_url)
            time.sleep(delay + random.uniform(0, 0.5))
            if img is None:
                stats["skipped"] += 1
                continue
            if img.width < MIN_SIZE or img.height < MIN_SIZE:
                stats["skipped"] += 1
                continue
            ph = phash_of(img.convert("RGB"))
            if is_near_duplicate(ph, known_hashes):
                print(f"[dup] {image_url}")
                stats["skipped"] += 1
                continue
            ext = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp",
                   "GIF": ".gif", "BMP": ".bmp"}.get(img.format or "JPEG", ".jpg")
            name = hashlib.sha1(image_url.encode()).hexdigest()[:16] + ext
            rel_path = os.path.join("data", "images", name)
            with open(os.path.join(core.BASE_DIR, rel_path), "wb") as fh:
                fh.write(raw)
            conn.execute(
                "INSERT OR IGNORE INTO images (image_path, image_url, source_url,"
                " page_title, platform, phash, width, height, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (rel_path, image_url, resp.url, title, platform, ph,
                 img.width, img.height, time.time()))
            conn.commit()
            known_hashes.append(ph)
            known_image_urls.add(image_url)
            stats["downloaded"] += 1
            print(f"[ok] {image_url}  ({img.width}x{img.height})")
        time.sleep(delay + random.uniform(0, 0.5))

    print(f"\nDone. Pages crawled: {stats['pages']}, "
          f"images stored: {stats['downloaded']}, skipped: {stats['skipped']}")
    print("Next step: python index.py")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Crawl public pages for images")
    ap.add_argument("--urls", default="urls.txt")
    ap.add_argument("--delay", type=float, default=1.5,
                    help="base delay between requests in seconds (1-2 recommended)")
    ap.add_argument("--max-pages", type=int, default=50)
    args = ap.parse_args()
    crawl(args.urls, args.delay, args.max_pages)
