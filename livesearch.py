#!/usr/bin/env python3
"""Live web image search — the Google/Bing/Pinterest behaviour.

A query arrives, engines are asked AT THAT MOMENT (nothing is read from
the local index), the returned photos are embedded with CLIP, ranked by
cosine similarity to the query, near-duplicates (>0.98) suppressed, and
the top results are returned with score + original URL + source page.

Engines: Bing Images + Pinterest (reverse-engineered public pages /
guest internal API — see webfetch.py). Google Images serves only a JS
shell to non-browser clients, so it cannot be a live source here.

Results are cached per query for CACHE_TTL seconds (repeat searches are
instant and do not hammer the engines).

Used by app.py's GET /live endpoint; also runnable standalone:
  python livesearch.py "biryani" [k]
"""
from __future__ import annotations

import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import requests
from PIL import Image

import core
import webfetch

CACHE_TTL = 600          # seconds per query
MAX_CANDIDATES = 40      # photos downloaded + embedded per live search
DOWNLOAD_TIMEOUT = 6
MAX_BYTES = 4 * 1024 * 1024
NEAR_DUP_THRESHOLD = 0.98

_cache: dict[str, tuple[float, list[dict]]] = {}
_cache_lock = threading.Lock()
_embed_lock = threading.Lock()   # CLIP inference is serialized


def _download_pil(url: str) -> Image.Image | None:
    try:
        resp = requests.get(url, headers=webfetch.HEADERS,
                            timeout=DOWNLOAD_TIMEOUT, stream=True)
        resp.raise_for_status()
        raw = b""
        for chunk in resp.iter_content(64 * 1024):
            raw += chunk
            if len(raw) > MAX_BYTES:
                return None
        img = Image.open(io.BytesIO(raw))
        img.load()
        return img.convert("RGB")
    except Exception:
        return None


def _candidates(query: str) -> list[dict]:
    """Ask every working engine, tag the source, dedupe by image URL."""
    out: list[dict] = []
    seen: set[str] = set()

    def add(items: list[dict], engine: str) -> None:
        for item in items:
            url = item.get("image_url")
            if not url or url in seen:
                continue
            seen.add(url)
            out.append({
                "image_url": url,
                "display_url": item.get("thumb_url") or url,
                "source_url": item.get("source_url", ""),
                "title": item.get("title", "") or query,
                "engine": engine,
            })

    session = requests.Session()
    session.headers.update(webfetch.HEADERS)
    try:
        add(webfetch.bing_search(session, query, want=48), "bing")
    except Exception as exc:
        print(f"[live] bing failed for {query!r}: {exc}")
    try:
        add(webfetch.pinterest_search(session, query), "pinterest")
    except Exception as exc:
        print(f"[live] pinterest failed for {query!r}: {exc}")
    return out[:MAX_CANDIDATES]


def live_search(query: str, k: int = 24) -> list[dict]:
    key = query.strip().lower()
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1][:k]

    candidates = _candidates(query)
    if not candidates:
        return []

    with ThreadPoolExecutor(max_workers=10) as pool:
        images = list(pool.map(lambda c: _download_pil(c["display_url"]),
                               candidates))
    pairs = [(c, img) for c, img in zip(candidates, images) if img is not None]
    if not pairs:
        return []

    with _embed_lock:
        vectors = core.embed_pil_images([img for _, img in pairs])
        query_vec = core.embed_text(query)
    scores = vectors @ query_vec

    order = np.argsort(-scores)
    results: list[dict] = []
    kept_vecs: list[np.ndarray] = []
    for idx in order:
        if len(results) >= k:
            break
        vec = vectors[idx]
        if kept_vecs and float(np.max(np.array(kept_vecs) @ vec)) \
                > NEAR_DUP_THRESHOLD:
            continue
        kept_vecs.append(vec)
        cand = pairs[idx][0]
        results.append({
            "score": round(max(0.0, float(scores[idx])) * 100, 1),
            "thumb_url": cand["display_url"],
            "image_url": cand["image_url"],
            "source_url": cand["source_url"],
            "title": cand["title"],
            "engine": cand["engine"],
        })

    with _cache_lock:
        _cache[key] = (now, results)
    return results


if __name__ == "__main__":
    import json
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "cat"
    k = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    t0 = time.time()
    for r in live_search(q, k):
        print(f"{r['score']:5.1f}  [{r['engine']}]  {r['title'][:60]}")
        print(f"       img: {r['image_url'][:100]}")
    print(f"\n{time.time() - t0:.1f}s")
