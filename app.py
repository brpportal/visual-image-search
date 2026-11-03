#!/usr/bin/env python3
"""FastAPI server for the visual image search system.

Endpoints:
  POST /search          multipart form, field "file" = query image
  GET  /search?q=text   text query
  Both accept ?k=50 (default 50) and return:
  [{"score": 97.4, "thumb_url": "/thumb/12", "source_url": "...", "title": "..."}]

  GET  /thumb/{id}      cached 320px JPEG thumbnail
  GET  /images/...      original files (StaticFiles over data/images)
  GET  /                the search frontend
  GET  /health          status: "ok" and indexed_images count (null if unloaded)

Near-duplicate rule: after ranking, a result is dropped if its embedding is
> 0.98 cosine-similar to an already-kept result (the same photo re-hosted
should not fill the grid). No face recognition, no person identification —
search is by overall visual/text similarity only.

Run:  uvicorn app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import io
import os
import threading

import numpy as np
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image

import core

NEAR_DUP_THRESHOLD = 0.98
DEFAULT_K = 50

app = FastAPI(title="Visual Image Search (self-hosted)")

# ---- Logs buffer: server ke prints yahan bhi jama hote hain taake
# Android app / web Settings se /logs par kabhi bhi dekhe ja saken.
import sys as _sys

LOG_BUFFER: list[str] = []


class _Tee:
    def __init__(self, orig):
        self._orig = orig

    def write(self, s):
        try:
            self._orig.write(s)
        except Exception:
            pass
        for line in str(s).splitlines():
            if line.strip():
                LOG_BUFFER.append(line.rstrip())
                if len(LOG_BUFFER) > 400:
                    del LOG_BUFFER[:-400]
        return len(s)

    def flush(self):
        try:
            self._orig.flush()
        except Exception:
            pass


_sys.stdout = _Tee(_sys.stdout)


@app.get("/logs")
def logs(n: int = 200):
    """Aakhri server log lines (app ke Settings > Logs ke liye)."""
    return {"lines": LOG_BUFFER[-max(1, min(n, 400)):]}


_index = None
_index_lock = threading.Lock()
_model_ready = threading.Event()


def get_index():
    """Load the FAISS index once; return None if it has not been built."""
    global _index
    if _index is None:
        with _index_lock:
            if _index is None:
                _index = core.load_index()
    return _index


def search_vectors(query_vec: np.ndarray, k: int) -> list[dict]:
    index = get_index()
    if index is None or index.ntotal == 0:
        raise HTTPException(status_code=503,
                            detail="Index is empty. Run crawl.py then index.py first.")
    # Over-fetch: dedupe + missing rows can only shrink the list.
    fetch = min(index.ntotal, max(k * 3, k + 10))
    scores, ids = index.search(query_vec.reshape(1, -1), fetch)
    scores, ids = scores[0], ids[0]

    conn = core.connect()
    results: list[dict] = []
    kept_vectors: list[np.ndarray] = []
    for score, image_id in zip(scores, ids):
        if image_id < 0 or len(results) >= k:
            continue
        row = conn.execute("SELECT * FROM images WHERE id = ?",
                           (int(image_id),)).fetchone()
        if row is None:
            continue
        if not os.path.isfile(os.path.join(core.BASE_DIR, row["image_path"])):
            continue
        # Near-duplicate suppression against already-kept results.
        vec = index.reconstruct(int(image_id))
        if kept_vectors:
            sims = np.array(kept_vectors) @ vec
            if float(sims.max()) > NEAR_DUP_THRESHOLD:
                continue
        kept_vectors.append(vec)
        results.append({
            "score": round(max(0.0, float(score)) * 100, 1),
            "thumb_url": f"/thumb/{row['id']}",
            "source_url": row["source_url"] or row["image_url"],
            "title": row["page_title"] or "",
        })
    conn.close()
    return results


@app.on_event("startup")
def _warm_model() -> None:
    """Warm the CLIP model in the background so the first search is fast."""
    def _load():
        try:
            core.get_model()
            _model_ready.set()
        except Exception:
            pass  # first search retries the load

    threading.Thread(target=_load, daemon=True).start()


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "indexed_images": int(_index.ntotal) if _index is not None else None,
    }


@app.post("/search")
async def search_by_image(file: UploadFile = File(...),
                          k: int = Query(DEFAULT_K, ge=1, le=200)) -> JSONResponse:
    raw = await file.read()
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:
        raise HTTPException(status_code=400,
                            detail="Could not read that file as an image.")
    vec = core.embed_pil_images([img])[0]
    return JSONResponse(search_vectors(vec, k))


@app.get("/search")
def search_by_text(q: str = Query(..., min_length=1),
                   k: int = Query(DEFAULT_K, ge=1, le=200)) -> JSONResponse:
    vec = core.embed_text(q)
    return JSONResponse(search_vectors(vec, k))


# NOTE (2026-11-01, Al's order): the old /live route — realtime search
# riding Bing Images + Pinterest via webfetch/livesearch — is REMOVED.
# Kisi aur ka image engine backend nahi chalega; overall search ke liye
# hamara apna SCAN system hai (/scan, /imagescan, scan.py).


@app.get("/thumb/{image_id}")
def thumb(image_id: int) -> FileResponse:
    thumb_path = os.path.join(core.THUMBS_DIR, f"{image_id}.jpg")
    if not os.path.isfile(thumb_path):
        with core.connect() as conn:
            row = conn.execute("SELECT image_path FROM images WHERE id = ?",
                               (image_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="image not found")
        src = os.path.join(core.BASE_DIR, row["image_path"])
        try:
            img = Image.open(src)
            img.load()
            img = img.convert("RGB")
            img.thumbnail((320, 320))
            img.save(thumb_path, "JPEG", quality=80)
        except Exception:
            raise HTTPException(status_code=404, detail="image file unreadable")
    return FileResponse(thumb_path, media_type="image/jpeg")


TOP_RESULTS = 200

_JUNK_URL = ("emoji", "sprite", "favicon", "1x1", "/tr?", "tr?id=",
             "pixel", "blank.", "spacer", "s32x32", "32x32", "36x36",
             "/icons/", "loading.", "gravatar")


def _download_enough(cands, want_ok: int = 130, max_try: int = 340,
                     budget_s: int = 32):
    """Waves me download karo jab tak KAFI achhi images na mil jayen
    (pehle sirf pehli 200 try hoti theen — 27% success par funnel
    bhookha reh jata tha). TIME BUDGET bhi hai: budget khatam to jo
    mila usi par aage ka kaam — scan latakta nahi. Junk URLs pehle
    skip; aakhir me bari images (pixel area) pehle; embed set cap 84."""
    import scan as scanmod
    import time as _time
    from concurrent.futures import ThreadPoolExecutor as _TPE
    pool = [c for c in cands
            if not any(j in c["image_url"].lower() for j in _JUNK_URL)]
    ok: list = []
    idx = 0
    t0 = _time.time()
    while idx < min(len(pool), max_try) and len(ok) < want_ok:
        if idx and _time.time() - t0 > budget_s:
            break
        wave = pool[idx: idx + 56]
        idx += len(wave)
        with _TPE(max_workers=16) as ex:
            ok.extend(d for d in ex.map(scanmod._download, wave) if d)
    ok.sort(key=lambda d: -(d["img"].width * d["img"].height))
    return ok[:84]


_SCAN_CACHE: dict = {}


def _cache_get(key):
    hit = _SCAN_CACHE.get(key)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    return None


def _result_item(d: dict, score: float) -> dict:
    return {"score": round(score * 100, 1), "origin": d["origin"],
            "via": d.get("via", ""), "thumb_url": d["image_url"],
            "source_url": d["source_url"], "title": d["title"]}


def _attach_similars(results: list[dict], vecs_kept) -> list[dict]:
    """Har result ke saath usi result-set ki sab se milti-julti
    images ke index — image kholne par neeche 'Similar Images'
    dikhane ke liye."""
    import numpy as _np
    if len(results) < 2 or vecs_kept is None or len(vecs_kept) < 2:
        for r in results:
            r["similar"] = []
        return results
    sims = vecs_kept @ vecs_kept.T
    for i, r in enumerate(results):
        order = _np.argsort(-sims[i])
        r["similar"] = [int(j) for j in order
                        if j != i and sims[i][j] >= 0.45][:4]
    return results


def _top_kept(ok, vecs, scores, topn=TOP_RESULTS):
    order = sorted(range(len(ok)), key=lambda i: -scores[i])
    kept, kept_idx = [], []
    for i in order:
        if kept_idx and float((vecs[kept_idx] @ vecs[i]).max()) > 0.98:
            continue
        kept.append(i)
        kept_idx.append(i)
        if len(kept) >= topn:
            break
    return kept


def _scan_compute(q: str, limit: int = 200, on_event=None,
                  sites: list | None = None) -> dict:
    """SCAN (Search on Search): engines se sirf links, hamara engine
    visit karke images nikalta hai, CLIP se query ke khilaf ranking.
    sites = user ki di hui target websites — engine unhein lazmi
    visit karta hai (engines ki marzi par depend nahi)."""
    import scan as scanmod
    from collections import Counter
    from concurrent.futures import ThreadPoolExecutor as _TPE
    q = (q or "").strip()
    if not q:
        return {"error": "empty query"}
    key = (q.lower(), tuple(sorted(sites or [])))
    cached = _cache_get(key)
    if cached is not None:
        if on_event:
            on_event("harvested", cached["counts"])
        return dict(cached, cached=True)
    try:
        cands = scanmod.harvest(q, sites=sites, on_event=on_event)
    except Exception as exc:
        return {"error": f"harvest failed: {type(exc).__name__}"}
    counts = dict(Counter(c["origin"] for c in cands))
    ok = _download_enough(cands)
    if not ok:
        return {"query": q, "counts": counts, "total": 0, "results": []}
    vecs = core.embed_pil_images([d["img"] for d in ok])
    _q = core.embed_text(q)
    qv = _q[0] if _q.ndim == 2 else _q
    scores = (vecs @ qv).tolist()
    kept = _top_kept(ok, vecs, scores)
    result = {"query": q, "counts": counts, "total": len(ok),
              "results": _attach_similars(
                  [_result_item(ok[i], scores[i]) for i in kept],
                  vecs[kept])}
    _SCAN_CACHE[key] = (time.time(), result)
    return result


def _imagescan_compute(ref, query: str, limit: int = 180,
                       on_event=None, sites: list | None = None) -> dict:
    """IMAGE SCAN: upload ki hui image ko reference bana kar overall
    sweep ki har image se hamari CLIP + tile deep-match. sites = user
    ki target websites jinhein engine lazmi visit karega."""
    import scan as scanmod
    import hunter as huntermod
    from collections import Counter
    from concurrent.futures import ThreadPoolExecutor as _TPE
    query = (query or "").strip()
    if not query:
        return {"error": "topic (q) required — kis naam/topic par "
                         "overall scan karun?"}
    try:
        cands = scanmod.harvest(query, sites=sites, on_event=on_event)
    except Exception as exc:
        return {"error": f"harvest failed: {type(exc).__name__}"}
    counts = dict(Counter(c["origin"] for c in cands))
    ok = _download_enough(cands)
    if not ok:
        return {"query": query, "counts": counts, "total": 0,
                "results": []}
    ref_vec = core.embed_pil_images([ref])[0]
    vecs = core.embed_pil_images([d["img"] for d in ok])
    scores = (vecs @ ref_vec).tolist()
    todo = [i for i, s in enumerate(scores) if 0.25 <= s < 0.90]
    if todo:  # deep match: tiles rescue edited / in-group copies
        views, owners = [], []
        for i in todo:
            for v in huntermod.views_of(ok[i]["img"]):
                views.append(v)
                owners.append(i)
        vs = (core.embed_pil_images(views) @ ref_vec).tolist()
        for j, i in enumerate(owners):
            if vs[j] > scores[i]:
                scores[i] = vs[j]
    kept = _top_kept(ok, vecs, scores)
    return {"query": query, "counts": counts, "total": len(ok),
            "results": _attach_similars(
                [_result_item(ok[i], scores[i]) for i in kept],
                vecs[kept])}


def _parse_sites(sites: str | None) -> list | None:
    if not sites:
        return None
    out = []
    for s in sites.replace(",", " ").split():
        s = s.strip()
        if s:
            out.append(s if s.startswith("http") else "https://" + s)
    return out or None


@app.get("/scan")
def scan(q: str, limit: int = 200, sites: str = ""):
    return _scan_compute(q, limit, sites=_parse_sites(sites))


def _fetch_meta(url: str) -> dict:
    """Page ka OG/meta data: title, description, og:image, site."""
    import requests as _rq
    import scan as scanmod
    import social as socialmod
    from bs4 import BeautifulSoup
    headers = (socialmod.CRAWLER_HEADERS
               if ("facebook.com" in url or "instagram.com" in url)
               else scanmod.HEADERS)
    try:
        r = _rq.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")

        def og(prop):
            tag = soup.find("meta", property=prop) or \
                soup.find("meta", attrs={"name": prop})
            return (tag.get("content") or "").strip() if tag else ""

        desc_tag = soup.find("meta", attrs={"name": "description"})
        return {
            "title": og("og:title") or (soup.title.string.strip()
                                        if soup.title and
                                        soup.title.string else ""),
            "description": og("og:description") or
                           ((desc_tag.get("content") or "").strip()
                            if desc_tag else ""),
            "og_image": og("og:image"),
            "site_name": og("og:site_name"),
            "type": og("og:type"),
        }
    except Exception:
        return {"title": "", "description": "", "og_image": "",
                "site_name": "", "type": ""}


def _urlscan_compute(url: str, on_event=None) -> dict:
    """ONLY URL SCAN — ek hi URL ki DEEP analysis: page par mojood
    SARI images (photos + video posters + embed thumbnails) + OG/meta
    data. Social URL ho to OG adapters se gehri public harvest."""
    import re as _re
    import scan as scanmod
    import social as socialmod
    url = (url or "").strip()
    if not url:
        return {"error": "URL likho"}
    if not url.startswith("http"):
        url = "https://" + url
    if on_event:
        on_event("stage", {"text": "page visit ho raha hai…"})
    meta = _fetch_meta(url)
    items: list[dict] = []
    if "facebook.com" in url or "instagram.com" in url:
        kind = "facebook" if "facebook.com" in url else "instagram"
        got = socialmod.ADAPTERS[kind](url)
        if not got:
            return {"error": "is social page se public data nahi mila "
                             "(login wall) — LOGIN_REQUIRED",
                    "url": url, "meta": meta}
        items = [dict(it, origin=kind) for it in got]
    elif "youtube.com" in url or "youtu.be" in url:
        m = _re.search(r"(?:v=|youtu\.be/|embed/|shorts/)([\w-]{11})",
                       url)
        if m:
            items = [{"image_url": f"https://i.ytimg.com/vi/"
                                   f"{m.group(1)}/hqdefault.jpg",
                      "source_url": url, "title": meta["title"],
                      "origin": "youtube"}]
        else:
            items = [dict(it, origin="youtube")
                     for it in socialmod.youtube_images(url)]
    else:
        got = scanmod._fetch_soup(_session_for_scan(), url)
        if got is None:
            return {"error": "page khul nahi saka (band, login wall "
                             "ya JS-only site?)", "url": url,
                    "meta": meta}
        soup, title = got
        import crawl as crawlmod
        items = [{"image_url": iu, "source_url": url,
                  "title": title, "origin": "url"}
                 for iu in crawlmod.candidate_image_urls(soup, url)]
        if not meta["title"]:
            meta["title"] = title
    if on_event:
        on_event("stage", {"text": f"{len(items)} images mili — "
                                   f"download + analysis ho raha hai…"})
        from collections import Counter
        on_event("harvested", dict(Counter(i["origin"] for i in items)))
    ok = _download_enough(items, want_ok=len(items) or 1,
                          max_try=min(len(items), 200))
    pairs = [({"score": None, "origin": d["origin"], "via": "",
               "thumb_url": d["image_url"], "source_url": url,
               "title": d.get("title", ""),
               "w": d["img"].width, "h": d["img"].height}, d["img"])
             for d in ok]
    pairs.sort(key=lambda p: -(p[0]["w"] * p[0]["h"]))
    results = [p[0] for p in pairs]
    if len(pairs) >= 2:  # similar images ke liye embeddings
        vecs_all = core.embed_pil_images([p[1] for p in pairs])
        _attach_similars(results, vecs_all)
    else:
        for r in results:
            r["similar"] = []
    return {"url": url, "meta": meta,
            "counts": {items[0]["origin"] if items else "url":
                       len(items)},
            "total": len(results), "results": results}


def _session_for_scan():
    import requests as _rq
    import scan as scanmod
    s = _rq.Session()
    s.headers.update(scanmod.HEADERS)
    return s


@app.get("/urlscan")
def urlscan(url: str):
    return _urlscan_compute(url)


@app.get("/urlscan/start")
def urlscan_start(url: str):
    return {"job_id": _start_job(
        lambda ev: _urlscan_compute(url, ev))}


@app.post("/imagescan")
async def imagescan(file: UploadFile = File(...), q: str = "",
                    limit: int = 180, sites: str = ""):
    raw = await file.read()
    try:
        ref = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:
        return {"error": "uploaded image unreadable"}
    return _imagescan_compute(ref, q, limit, sites=_parse_sites(sites))


# ---- JOB pattern: pehle jawab (job id), phir peeche kaam ----------
# Lambi ek-request wali scans mobile/tunnel par toot jati theen; ab
# page chhoti requests se progress poochta rehta hai (polling).
import threading
import time
import uuid

JOBS: dict = {}


def _start_job(fn) -> str:
    job_id = uuid.uuid4().hex[:10]
    job = {"state": "running", "stage": "shuru ho raha hai…",
           "result": None, "error": None, "ts": time.time(),
           "links": None, "harvested": None}
    JOBS[job_id] = job

    def on_event(kind, data):
        if kind == "stage":
            job["stage"] = data.get("text", job["stage"])
        elif kind == "links":
            job["links"] = data
            job["stage"] = (f"{data['total']} links mile — pages "
                            f"visit ho rahi hain…")
        elif kind == "harvested":
            job["harvested"] = data
            job["stage"] = "images mil gayi — matching chal rahi hai…"

    def run():
        try:
            res = fn(on_event)
            if isinstance(res, dict) and res.get("error"):
                job["state"] = "error"
                job["error"] = res["error"]
            else:
                job["result"] = res
                job["state"] = "done"
        except Exception as exc:
            job["state"] = "error"
            job["error"] = f"{type(exc).__name__}: {exc}"

    threading.Thread(target=run, daemon=True).start()
    return job_id


@app.get("/scan/start")
def scan_start(q: str, limit: int = 200, sites: str = ""):
    parsed = _parse_sites(sites)
    return {"job_id": _start_job(
        lambda ev: _scan_compute(q, limit, ev, sites=parsed))}


@app.post("/imagescan/start")
async def imagescan_start(file: UploadFile = File(...), q: str = "",
                          limit: int = 180, sites: str = ""):
    raw = await file.read()
    try:
        ref = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:
        return {"error": "uploaded image unreadable"}
    parsed = _parse_sites(sites)
    return {"job_id": _start_job(
        lambda ev: _imagescan_compute(ref, q, limit, ev,
                                      sites=parsed))}


@app.get("/job/{job_id}")
def job_status(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        return {"state": "error",
                "error": "job not found (server restart ho gaya?)"}
    out = {"state": job["state"], "stage": job["stage"],
           "links": job["links"], "harvested": job["harvested"]}
    if job["state"] == "done":
        out["result"] = job["result"]
    if job["state"] == "error":
        out["error"] = job["error"]
    return out


app.mount("/images", StaticFiles(directory=core.IMAGES_DIR), name="images")
app.mount("/", StaticFiles(directory=os.path.join(core.BASE_DIR, "static"),
                           html=True), name="static")
