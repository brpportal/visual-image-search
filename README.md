# Visual Image Search — self-hosted, on your phone

**▶ Live public demo: https://brpportal.github.io/visual-image-search/**
(19 sample images; embeddings precomputed by the real model, scores computed
live in your browser with the same ranking rule as the server. The full
version below adds photo upload, free-text queries and your own crawl.)

Upload a reference image (or type words) and get the **top 50 most similar
images from your own index**, each with a similarity score (0–100%), a
thumbnail, and a link back to the page/post it came from.

- 100% local: **no paid APIs, no cloud.** Everything runs inside
  proot-distro Ubuntu on Termux.
- Embeddings: **OpenCLIP ViT-B-32** (`laion2b_s34b_b79k`), CPU only.
- Vector search: **FAISS** `IndexFlatIP` on L2-normalized vectors (= cosine).
- Metadata: **SQLite**. Crawler dedupes by **perceptual hash**.
- Server: **FastAPI + Uvicorn**, plain HTML/JS frontend, mobile-first.
- Public pages only — robots.txt respected, 1–2 s crawl delay, login pages
  skipped. **No face recognition / person identification, by design.**

> Why proot Ubuntu? PyTorch does not install in native Termux. Inside
> proot-distro Ubuntu it installs from normal wheels — that's the whole trick.

---

## Install (Termux → proot Ubuntu)

**Step 1 — Termux side:**

```bash
pkg update && pkg install proot-distro git
proot-distro install ubuntu
proot-distro login ubuntu
```

**Step 2 — inside Ubuntu:**

```bash
git clone https://github.com/brpportal/visual-image-search.git
cd visual-image-search
bash setup.sh
```

`setup.sh` installs Python + venv, PyTorch from the **CPU wheel index**
(much smaller than the default CUDA build), then the project requirements.

## Build your index

```bash
source venv/bin/activate

# 1) Edit urls.txt — one public page URL per line
# 2) Crawl (downloads images >= 200x200, skips duplicates + login pages)
python crawl.py

# 3) Index (first run downloads the CLIP model, ~350 MB)
python index.py
```

Re-running `crawl.py` / `index.py` later is incremental: only new images
are downloaded and embedded.

## Run the server

```bash
bash run.sh
# or: uvicorn app:app --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000** in the phone browser. Type a description or
upload a reference image — results appear as a grid with score badges and
"Open source ↗" links.

Keep the Termux session alive while serving (Termux: `termux-wake-lock`
helps). To use it again tomorrow: `proot-distro login ubuntu`, `cd
visual-image-search`, `bash run.sh`.

## Live web search (no index needed)

The server's default mode is **Live Web**: type any query and the engines
are asked *at that moment* — Bing Images and Pinterest, via the
reverse-engineered routes in `webfetch.py` — then the returned photos are
embedded with CLIP, ranked, de-duplicated and shown with scores and source
links. Nothing is read from the local index; every search is fresh web
results, like Google Images. First search for a query takes ~20–35 s
(engine fetch + downloads + embedding); repeats are cached for 10 minutes.

- Endpoint: `GET /live?q=biryani&k=24`
- Standalone: `python livesearch.py "purple sports car"`
- Google Images is not a live source: it serves only a JS shell to
  non-browser clients (verified directly and with REA static analysis).
- The **My Index** tab in the UI is the original offline mode over your
  own crawled/indexed images (`/search`).

## Reference hunter — our own system (no one else's backend)

This is the Google/Bing/Pinterest *technology* rebuilt as our own
system: give it reference images, and it scans the web itself to find
those images and ones like them.

1. **Reference set:** drop images into `references/` (thousands are
   fine) and build the reference index:
   `python reference.py build`
2. **Seeds:** list starting pages in `sources.txt`. The hunter crawls
   them politely (robots.txt, delays, login pages skipped) and follows
   same-host links. Social sources work without login via adapters —
   prefix a line: `youtube: https://…`, `instagram: …`, `facebook: …`
   (YouTube thumbnails are fully public; Instagram/Facebook expose only
   what they serve to logged-out visitors — walls are reported, never
   bypassed).
3. **Hunt:** `python hunter.py --threshold 0.80` — every image found is
   embedded with CLIP and compared to the reference index; matches are
   saved to `data/found/` and recorded in SQLite with score + the
   reference matched + source page. Scanned URLs are remembered, so
   repeat runs only check NEW images.
4. **Automate:** `--watch 60` repeats hourly, or run it from cron.

## Quick self-test (20 sample images)

```bash
source venv/bin/activate
python make_samples.py     # 20 drawn samples: 19 valid + 1 near-duplicate + 1 corrupt
python index.py            # expect: 19 indexed, 1 skipped corrupt
uvicorn app:app --host 0.0.0.0 --port 8000 &
python test_search.py      # acceptance checks against the running server
```

Expected: text search *"green tree in a field"* → the tree image on top
(sample 03, or its re-saved copy 19 — exactly one of them is shown, the
other is collapsed as a near-duplicate); image
search with `sample-03.jpg` → sample 03 first at ~100%, and its
near-duplicate (sample 19, the same image re-saved at JPEG q85, measured
cosine 0.9898) **absent** — the > 0.98 near-duplicate rule collapses it.
A merely resized copy (cosine 0.9624) is intentionally NOT collapsed: the
rule targets same-photo re-hosts, not just similar images.

## How it works

| File | Job |
|---|---|
| `setup.sh` | Ubuntu-side installer (venv, torch-CPU, requirements) |
| `crawl.py` | urls.txt → public images into `data/images` + SQLite metadata (source page URL, title, platform), robots.txt + delay + login-skip + phash dedupe |
| `index.py` | CLIP-embeds new images in batches of 16 → `data/index.faiss` (FAISS ids = SQLite ids, so the mapping can't drift) |
| `app.py` | FastAPI: `POST /search` (image), `GET /search?q=` (text), `GET /thumb/{id}`, `GET /health`; suppresses near-duplicates (> 0.98) in results |
| `core.py` | Shared paths, SQLite schema, CLIP loader, FAISS helpers |
| `static/index.html` | Mobile-first UI: upload box, text box, 50-result grid, score badges, source links, loading state |
| `make_samples.py` / `test_search.py` | The 20-image fixture set + acceptance test |

## Performance targets

- Index size: 5k–20k images. IndexFlatIP scans exactly, so search stays
  well under 2 s at that scale on a phone CPU (embedding the query image
  is the slow part, ~0.5–1.5 s).
- Storage: the FAISS index is ~2 KB per image (512 floats); thumbnails are
  generated once and cached in `data/thumbs`.

## Limits & notes

- Crawling JavaScript-heavy pages: the crawler is `requests` +
  BeautifulSoup (no browser). Pages that only render images via JS will
  yield few images. Playwright is intentionally optional — if you need it,
  run it inside the same proot Ubuntu (`pip install playwright &&
  playwright install chromium`) and feed its downloaded HTML to the same
  SQLite schema.
- Qdrant is not needed at this scale; FAISS is faster and simpler on-device.
  The FAISS id = SQLite id design keeps a later migration trivial.
- `VERIFIED_ACCESSIBLE`-style link checking is out of scope: source links
  are stored as found at crawl time.
- Python 3.11+ (Ubuntu's stock python3 in proot is fine).

## Verified status (honest)

✅ Built and acceptance-tested by the author on a Linux machine with the
exact commands above (venv install, 20-sample index build, live server,
`test_search.py` all passing — see repo history/notes).
⚠️ The Termux/proot on-phone run itself is your step: performance depends
on your phone; a mid-range phone embeds ~2–5 images/sec in batches.

License: MIT
