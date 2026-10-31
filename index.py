#!/usr/bin/env python3
"""Indexer: embeds every not-yet-indexed image with CLIP and adds it to FAISS.

- Batches of 16, CPU only.
- The FAISS index is IndexIDMap2(IndexFlatIP) keyed by the SQLite row id,
  so the id mapping can never drift from the metadata.
- The index is saved after every batch (a phone dying mid-run loses at
  most one batch).
- Corrupt/unreadable images are reported and skipped, never fatal.

Usage:  python index.py [--batch 16] [--rebuild]
"""
from __future__ import annotations

import argparse
import os

import numpy as np
from PIL import Image

import core


def main() -> None:
    ap = argparse.ArgumentParser(description="Build/update the FAISS index")
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--rebuild", action="store_true",
                    help="drop the existing index and re-embed everything")
    args = ap.parse_args()

    core.init_db()
    conn = core.connect()

    if args.rebuild and os.path.isfile(core.INDEX_PATH):
        os.remove(core.INDEX_PATH)
        conn.execute("UPDATE images SET indexed = 0")
        conn.commit()

    index = core.load_index()
    if index is None:
        index = core.new_index()
        # No index file => nothing can be marked as indexed truthfully.
        conn.execute("UPDATE images SET indexed = 0")
        conn.commit()

    rows = conn.execute(
        "SELECT id, image_path FROM images WHERE indexed = 0 ORDER BY id"
    ).fetchall()
    print(f"Images to index: {len(rows)} (already in index: {index.ntotal})")
    if not rows:
        print("Nothing to do.")
        return

    done = skipped = 0
    for start in range(0, len(rows), args.batch):
        batch = rows[start:start + args.batch]
        images, ids = [], []
        for row in batch:
            path = os.path.join(core.BASE_DIR, row["image_path"])
            try:
                img = Image.open(path)
                img.load()
                images.append(img)
                ids.append(row["id"])
            except Exception as exc:
                print(f"[skip corrupt] {row['image_path']}: {exc}")
                skipped += 1
        if not images:
            continue
        vectors = core.embed_pil_images(images)
        index.add_with_ids(vectors, np.array(ids, dtype=np.int64))
        core.save_index(index)
        conn.executemany("UPDATE images SET indexed = 1 WHERE id = ?",
                         [(i,) for i in ids])
        conn.commit()
        done += len(ids)
        print(f"  indexed {done}/{len(rows)} (index total: {index.ntotal})")

    print(f"Done. New vectors: {done}, skipped corrupt: {skipped}, "
          f"index size: {index.ntotal} -> {core.INDEX_PATH}")


if __name__ == "__main__":
    main()
