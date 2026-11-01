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
  GET  /health          status: metadata count, index size, model state

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
            pass  # /health reports model state; first search retries the load

    threading.Thread(target=_load, daemon=True).start()


@app.get("/health")
def health() -> dict:
    core.init_db()
    with core.connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
        indexed = conn.execute(
            "SELECT COUNT(*) FROM images WHERE indexed = 1").fetchone()[0]
    index = get_index()
    return {
        "status": "ok",
        "images_in_db": total,
        "images_indexed": indexed,
        "index_vectors": int(index.ntotal) if index is not None else 0,
        "model_loaded": _model_ready.is_set(),
        "model": f"{core.MODEL_NAME} ({core.PRETRAINED})",
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


@app.get("/live")
def live_web_search(q: str = Query(..., min_length=1),
                    k: int = Query(24, ge=1, le=50)) -> JSONResponse:
    """Realtime web image search — the Google/Bing/Pinterest behaviour.

    Engines are queried at this moment (Bing Images + Pinterest, via the
    reverse-engineered routes in webfetch.py); the photos they return are
    embedded and ranked live. NOTHING is read from the local index.
    Results are cached per query for a few minutes (see livesearch.py).
    """
    import livesearch
    return JSONResponse(livesearch.live_search(q, k))


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


@app.get("/scan")
def scan(q: str, limit: int = 60):
    """SCAN mode (scan.py): ONE query -> overall sweep of web + YouTube
    + public Facebook/Instagram via OG adapters, ranked by our CLIP."""
    import scan as scanmod
    from concurrent.futures import ThreadPoolExecutor as _TPE
    q = (q or "").strip()
    if not q:
        return {"error": "empty query"}
    try:
        cands = scanmod.harvest(q)
    except Exception as exc:
        return {"error": f"harvest failed: {type(exc).__name__}"}
    from collections import Counter
    counts = dict(Counter(c["origin"] for c in cands))
    cands = cands[: max(10, min(limit, 120))]
    with _TPE(max_workers=8) as ex:
        ok = [d for d in ex.map(scanmod._download, cands) if d]
    if not ok:
        return {"query": q, "counts": counts, "total": 0, "results": []}
    vecs = core.embed_pil_images([d["img"] for d in ok])
    _q = core.embed_text(q)
    qv = _q[0] if _q.ndim == 2 else _q
    scores = (vecs @ qv).tolist()
    order = sorted(range(len(ok)), key=lambda i: -scores[i])
    kept, kept_idx = [], []
    for i in order:
        if kept_idx and float((vecs[kept_idx] @ vecs[i]).max()) > 0.98:
            continue
        kept.append(i)
        kept_idx.append(i)
        if len(kept) >= 24:
            break
    return {"query": q, "counts": counts, "total": len(ok),
            "results": [{"score": round(scores[i] * 100, 1),
                         "origin": ok[i]["origin"],
                         "thumb_url": ok[i]["image_url"],
                         "source_url": ok[i]["source_url"],
                         "title": ok[i]["title"]} for i in kept]}


app.mount("/images", StaticFiles(directory=core.IMAGES_DIR), name="images")
app.mount("/", StaticFiles(directory=os.path.join(core.BASE_DIR, "static"),
                           html=True), name="static")
