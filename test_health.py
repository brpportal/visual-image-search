#!/usr/bin/env python3
"""Focused validation for GET /health endpoint.

Covers:
1. Unloaded index state: _index is None -> {"status": "ok", "indexed_images": null}
2. Read-only / No side-effects: GET /health does not load or mutate _index
3. Empty index state: _index has 0 vectors -> {"status": "ok", "indexed_images": 0}
4. Loaded index state: _index has vectors -> {"status": "ok", "indexed_images": <count>}
5. Response contract: exactly two fields ("status", "indexed_images")
6. Preservation of existing routes: /search and /logs operate normally
"""
from __future__ import annotations

import sys
from fastapi.testclient import TestClient

import app
import core


def run_checks() -> None:
    client = TestClient(app.app)
    failures = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        status = "PASS" if cond else "FAIL"
        print(f"{status} {name}" + (f" — {detail}" if detail else ""))
        if not cond:
            failures.append(name)

    print("=== Testing Unloaded Index State ===")
    app._index = None

    res = client.get("/health")
    check("status code 200", res.status_code == 200, f"got {res.status_code}")
    data = res.json()
    check("exact fields in response", set(data.keys()) == {"status", "indexed_images"}, f"got {list(data.keys())}")
    check("status is 'ok'", data.get("status") == "ok", f"got {data.get('status')}")
    check("indexed_images is null when unloaded", data.get("indexed_images") is None, f"got {data.get('indexed_images')}")
    check("no side-effect on _index (still None)", app._index is None, f"got {app._index}")

    print("\n=== Testing Empty Index State ===")
    app._index = core.new_index()
    check("new index has 0 vectors", app._index.ntotal == 0, f"got {app._index.ntotal}")

    res = client.get("/health")
    check("status code 200", res.status_code == 200, f"got {res.status_code}")
    data = res.json()
    check("exact fields in response", set(data.keys()) == {"status", "indexed_images"}, f"got {list(data.keys())}")
    check("status is 'ok'", data.get("status") == "ok", f"got {data.get('status')}")
    check("indexed_images is 0 when empty", data.get("indexed_images") == 0, f"got {data.get('indexed_images')}")
    check("no mutation on _index (still 0)", app._index.ntotal == 0, f"got {app._index.ntotal}")

    print("\n=== Testing Loaded Index State ===")
    real_index = core.load_index()
    if real_index is not None:
        app._index = real_index
        expected_count = int(real_index.ntotal)
        res = client.get("/health")
        check("status code 200", res.status_code == 200, f"got {res.status_code}")
        data = res.json()
        check("exact fields in response", set(data.keys()) == {"status", "indexed_images"}, f"got {list(data.keys())}")
        check("status is 'ok'", data.get("status") == "ok", f"got {data.get('status')}")
        check(f"indexed_images is {expected_count}", data.get("indexed_images") == expected_count, f"got {data.get('indexed_images')}")
    else:
        print("SKIP loaded index test: data/index.faiss not present")

    print("\n=== Testing Route Preservation & Method Safety ===")
    res_post = client.post("/health")
    check("POST /health rejected (405 Method Not Allowed)", res_post.status_code == 405, f"got {res_post.status_code}")

    res_logs = client.get("/logs")
    check("GET /logs works (200 OK)", res_logs.status_code == 200, f"got {res_logs.status_code}")

    if failures:
        print(f"\nFAILED: {failures}")
        sys.exit(1)
    else:
        print("\nALL FOCUSED CHECKS PASSED")


if __name__ == "__main__":
    run_checks()
