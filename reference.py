#!/usr/bin/env python3
"""Reference set builder — the system's own 'what to look for' index.

Drop reference images into a folder (default: references/) and run:

  python reference.py build [--folder references/]

Each new image is perceptual-hash de-duplicated against the existing
set, embedded with CLIP, and added to data/references.faiss
(IndexIDMap2 over IndexFlatIP, ids = ref_images row ids) with its
metadata in SQLite. Incremental: re-running only adds what is new.
Thousands of references are fine — FAISS holds 512-d vectors compactly
(10,000 refs ≈ 20 MB) and searches them in milliseconds.

The hunter (hunter.py) compares every image it finds on the web
against this index.
"""
from __future__ import annotations

import argparse
import os
import time

import numpy as np
from PIL import Image

import core
from crawl import is_near_duplicate, phash_of

REF_INDEX_PATH = os.path.join(core.DATA_DIR, "references.faiss")
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")


def ensure_tables(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ref_images ("
        " id INTEGER PRIMARY KEY, file_path TEXT, phash TEXT DEFAULT '',"
        " width INTEGER DEFAULT 0, height INTEGER DEFAULT 0,"
        " created_at REAL DEFAULT 0, tag TEXT DEFAULT '')")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(ref_images)")}
    if "tag" not in cols:  # migrate databases created before tags existed
        conn.execute(
            "ALTER TABLE ref_images ADD COLUMN tag TEXT DEFAULT ''")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS matches ("
        " id INTEGER PRIMARY KEY, found_url TEXT UNIQUE,"
        " source_page TEXT DEFAULT '', page_title TEXT DEFAULT '',"
        " matched_ref_id INTEGER DEFAULT 0, score REAL DEFAULT 0,"
        " found_path TEXT DEFAULT '', width INTEGER DEFAULT 0,"
        " height INTEGER DEFAULT 0, found_at REAL DEFAULT 0)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS scanned ("
        " found_url TEXT PRIMARY KEY, phash TEXT DEFAULT '',"
        " best_score REAL DEFAULT 0, best_ref_id INTEGER DEFAULT 0,"
        " scanned_at REAL DEFAULT 0)")
    conn.commit()


def load_ref_index():
    import faiss
    if not os.path.isfile(REF_INDEX_PATH):
        return None
    return faiss.read_index(REF_INDEX_PATH)


def new_ref_index():
    import faiss
    return faiss.IndexIDMap2(faiss.IndexFlatIP(core.EMBED_DIM))


def save_ref_index(index) -> None:
    import faiss
    faiss.write_index(index, REF_INDEX_PATH)


def build(folder: str, batch_size: int = 16, tag: str = "") -> None:
    core.init_db()
    conn = core.connect()
    ensure_tables(conn)

    index = load_ref_index()
    if index is None:
        index = new_ref_index()

    known_hashes = [r["phash"] for r in conn.execute(
        "SELECT phash FROM ref_images WHERE phash != ''")]
    known_paths = {r["file_path"] for r in conn.execute(
        "SELECT file_path FROM ref_images")}

    files = []
    for name in sorted(os.listdir(folder)):
        if name.lower().endswith(IMAGE_EXTS):
            rel = os.path.join(os.path.basename(folder.rstrip("/")), name) \
                if not folder.startswith("data") else name
            files.append((name, os.path.join(folder, name)))
    print(f"Reference folder: {folder} — {len(files)} image files, "
          f"{index.ntotal} already in reference index")

    added = skipped = 0
    pending: list[tuple[int, Image.Image]] = []

    def flush() -> None:
        nonlocal added
        if not pending:
            return
        vectors = core.embed_pil_images([img for _, img in pending])
        index.add_with_ids(vectors,
                           np.array([rid for rid, _ in pending],
                                    dtype=np.int64))
        save_ref_index(index)
        conn.commit()
        added += len(pending)
        print(f"  embedded + indexed: {added} new references "
              f"(index total: {index.ntotal})")
        pending.clear()

    for name, path in files:
        rel_path = os.path.relpath(path, core.BASE_DIR)
        if rel_path in known_paths:
            skipped += 1
            continue
        try:
            img = Image.open(path)
            img.load()
        except Exception as exc:
            print(f"  [skip unreadable] {name}: {exc}")
            skipped += 1
            continue
        ph = phash_of(img.convert("RGB"))
        if is_near_duplicate(ph, known_hashes):
            print(f"  [dup] {name}")
            skipped += 1
            continue
        cur = conn.execute(
            "INSERT INTO ref_images (file_path, phash, width, height,"
            " created_at, tag) VALUES (?,?,?,?,?,?)",
            (rel_path, ph, img.width, img.height, time.time(), tag))
        known_hashes.append(ph)
        known_paths.add(rel_path)
        pending.append((int(cur.lastrowid), img))
        if len(pending) >= batch_size:
            flush()
    flush()
    print(f"Done. New references: {added}, skipped: {skipped}, "
          f"reference index size: {index.ntotal}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build the reference index")
    ap.add_argument("command", choices=["build"])
    ap.add_argument("--folder", default="references")
    ap.add_argument("--tag", default="",
                    help="label stored with every reference from this "
                         "folder (e.g. islamic) so hunts can target one set")
    args = ap.parse_args()
    if args.command == "build":
        build(args.folder, tag=args.tag)
