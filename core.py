"""Shared core for the visual image search system.

Used by crawl.py (DB + dedupe helpers), index.py (embeddings + FAISS)
and app.py (search). Everything is local: no paid APIs, no cloud.

Model: OpenCLIP ViT-B-32 (laion2b_s34b_b79k), CPU only.
Vectors are L2-normalized, so FAISS IndexFlatIP == cosine similarity.
"""
from __future__ import annotations

import os
import sqlite3
import threading

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
IMAGES_DIR = os.path.join(DATA_DIR, "images")
THUMBS_DIR = os.path.join(DATA_DIR, "thumbs")
DB_PATH = os.path.join(DATA_DIR, "meta.db")
INDEX_PATH = os.path.join(DATA_DIR, "index.faiss")

MODEL_NAME = "ViT-B-32"
PRETRAINED = "laion2b_s34b_b79k"
EMBED_DIM = 512  # ViT-B-32 embedding size

os.makedirs(IMAGES_DIR, exist_ok=True)
os.makedirs(THUMBS_DIR, exist_ok=True)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    image_path  TEXT NOT NULL UNIQUE,   -- path relative to project root
    image_url   TEXT DEFAULT '',        -- direct URL the file was downloaded from
    source_url  TEXT DEFAULT '',        -- page/post URL the image was found on
    page_title  TEXT DEFAULT '',
    platform    TEXT DEFAULT '',        -- host of source_url, e.g. facebook.com
    phash       TEXT DEFAULT '',        -- perceptual hash (hex) for dedupe
    width       INTEGER DEFAULT 0,
    height      INTEGER DEFAULT 0,
    indexed     INTEGER DEFAULT 0,      -- 1 once its vector is in the FAISS index
    created_at  REAL DEFAULT 0
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.execute(_SCHEMA)
        conn.commit()


def platform_of(url: str) -> str:
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


# ---------------------------------------------------------------------------
# CLIP model (lazy singleton — loading takes a few seconds and ~600 MB RAM)
# ---------------------------------------------------------------------------
_model_lock = threading.Lock()
_model = None
_preprocess = None
_tokenizer = None


def get_model():
    """Load (once) and return (model, preprocess, tokenizer)."""
    global _model, _preprocess, _tokenizer
    if _model is None:
        with _model_lock:
            if _model is None:
                import open_clip
                import torch

                model, _, preprocess = open_clip.create_model_and_transforms(
                    MODEL_NAME, pretrained=PRETRAINED
                )
                model.eval()
                _model = model
                _preprocess = preprocess
                _tokenizer = open_clip.get_tokenizer(MODEL_NAME)
    return _model, _preprocess, _tokenizer


def embed_pil_images(pil_images) -> "object":
    """Embed a list of PIL images -> float32 numpy array (n, 512),
    L2-normalized. Preprocess parallel (8 threads) + encode 32 ke
    chunks me — bari lists par pehle se kai guna tez."""
    import numpy as np
    import torch
    from concurrent.futures import ThreadPoolExecutor as _TPE

    model, preprocess, _ = get_model()
    imgs = [img.convert("RGB") for img in pil_images]
    with _TPE(max_workers=8) as ex:
        tensors = list(ex.map(preprocess, imgs))
    outs = []
    with torch.no_grad():
        for i in range(0, len(tensors), 32):
            batch = torch.stack(tensors[i:i + 32])
            feats = model.encode_image(batch)
            feats = feats / feats.norm(dim=-1, keepdim=True)
            outs.append(feats.cpu().numpy().astype(np.float32))
    if not outs:
        return np.zeros((0, 512), dtype=np.float32)
    return np.concatenate(outs, axis=0)


def embed_text(text: str) -> "object":
    """Embed one text query -> float32 numpy array (512,), L2-normalized."""
    import numpy as np
    import torch

    model, _, tokenizer = get_model()
    tokens = tokenizer([text])
    with torch.no_grad():
        feats = model.encode_text(tokens)
    feats = feats / feats.norm(dim=-1, keepdim=True)
    return feats.cpu().numpy()[0].astype(np.float32)


# ---------------------------------------------------------------------------
# FAISS helpers
# ---------------------------------------------------------------------------
def load_index():
    """Load the FAISS index (IndexIDMap2 over IndexFlatIP) or return None."""
    import faiss

    if not os.path.isfile(INDEX_PATH):
        return None
    return faiss.read_index(INDEX_PATH)


def new_index():
    import faiss

    return faiss.IndexIDMap2(faiss.IndexFlatIP(EMBED_DIM))


def save_index(index) -> None:
    import faiss

    faiss.write_index(index, INDEX_PATH)
