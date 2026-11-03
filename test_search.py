#!/usr/bin/env python3
"""Acceptance test against a RUNNING server (uvicorn app:app --port 8000).

Run make_samples.py + index.py first, start the server, then:
    python test_search.py [base_url]

Checks:
  1. /health reports status "ok" and indexed_images count (null if unloaded)
  2. Text search "green tree in a field" -> sample 03 on top
  3. Image search with sample-03.jpg -> sample 03 first with score ~100
  4. Near-duplicate sample 19 must NOT appear next to sample 03 (>0.98 rule)
  5. Uploading a corrupt file returns HTTP 400, not a crash
  6. Every result has exactly the contract fields with a 0-100 score
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
HERE = os.path.dirname(os.path.abspath(__file__))
failures = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=120) as resp:
        return json.loads(resp.read())


def post_image(path: str, filename: str):
    import urllib.error
    import uuid

    boundary = uuid.uuid4().hex
    with open(path, "rb") as fh:
        payload = fh.read()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: image/jpeg\r\n\r\n"
    ).encode() + payload + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        BASE + "/search?k=50", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


health = get("/health")
print("health:", health)
check("health.status == 'ok'", health.get("status") == "ok",
      f"got {health.get('status')}")
check("health contract (exactly status and indexed_images)",
      set(health.keys()) == {"status", "indexed_images"})

import urllib.parse

text_results = get("/search?q=" + urllib.parse.quote("green tree in a field") + "&k=50")
check("text search returns results", len(text_results) > 0, f"{len(text_results)} results")
# Sample 19 is a re-saved copy of sample 03: for a text query either copy may
# rank first; the other must then be collapsed by the >0.98 rule.
tree_hits = [r for r in text_results
             if "sample-03" in r["source_url"] or "sample-19" in r["source_url"]]
check("text search top = the tree image (sample 03 or its copy 19)",
      bool(text_results) and text_results[0] in tree_hits,
      text_results[0]["source_url"] if text_results else "none")
check("text search shows exactly one copy of the tree image",
      len(tree_hits) == 1, f"{len(tree_hits)} copies")

post_health = get("/health")
check("post_health.indexed_images == 19", post_health.get("indexed_images") == 19,
      f"got {post_health.get('indexed_images')}")

status, img_results = post_image(os.path.join(HERE, "data", "images", "sample-03.jpg"),
                                 "sample-03.jpg")
check("image search HTTP 200", status == 200, f"got {status}")
check("image search top = sample 03, score > 98",
      bool(img_results) and "sample-03" in img_results[0]["source_url"]
      and img_results[0]["score"] > 98,
      f"{img_results[0] if img_results else 'none'}")
check("near-duplicate sample 19 collapsed",
      all("sample-19" not in r["source_url"] for r in img_results),
      "sample-19 present!" if any("sample-19" in r["source_url"] for r in img_results)
      else "absent as required")

bad = os.path.join(HERE, "data", "images", "sample-20.jpg")
status, _ = post_image(bad, "sample-20.jpg")
check("corrupt upload -> HTTP 400", status == 400, f"got {status}")

contract_ok = all(
    set(r.keys()) == {"score", "thumb_url", "source_url", "title"}
    and 0 <= r["score"] <= 100
    for r in img_results + text_results)
check("result contract fields + score range", contract_ok)

scores = [r["score"] for r in img_results]
check("scores sorted descending", scores == sorted(scores, reverse=True))

print("\n" + ("ALL CHECKS PASSED" if not failures else f"FAILURES: {failures}"))
sys.exit(1 if failures else 0)
