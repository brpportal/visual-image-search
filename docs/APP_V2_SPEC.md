# Image Scan App v2 — LOCKED SPEC (Al, 2026-11-02)

Fully on-device Android app. Koi server, koi Termux, koi tunnel nahi.
Internet sirf data lene ke liye (websites/social pages); saara hisaab
(matching) phone ke andar.

## 1. On-device engine (core)
- CLIP ViT-B-32 (wahi model jo server par hai) ONNX Runtime se phone par.
- Model app ke andar nahi — pehli dafa **download** hota hai (~154 MB INT8
  default; chahe to FP32 high-quality ~606 MB), phir phone me rehta hai.
- Verified 2026-11-02: FP32 ONNX ki embeddings server (torch) se cosine
  1.00000; INT8 ~0.94-0.95 torch ke muqable me lekin app ke andar tamam
  embeddings ek hi model se banti hain is liye ranking self-consistent.
- Models GitHub Release `models-v1` par hain (repo brpportal/visual-image-search).
- Modes: Overall Scan (engines se sirf LINKS, khud visit, images extract),
  Image Scan (upload + topic; tiles wali gehri matching), URL Scan
  (ek URL ki tamam images + OG/meta deeply). Search-on-search qanoon
  barqarar: engine ke image results kabhi nahi, sirf links.

## 2. References (Settings/References page)
- User apni images upload kare ga — yehi **references** hain jin ko
  system internet par dhundhe ga.
- Har reference add hone par us ki embedding phone par ban kar save
  hoti hai. List, delete, count — sab app ke andar.

## 3. AI (optional — bagher AI ke bhi system chalega)
- Settings me **custom Base URL + API Key + Model** — koi bhi platform
  jo OpenAI-compatible ho (OpenRouter, Apmix, OpenAI, waghera).
- Key phone par encrypted save hoti hai; paste ki ghaltiyon se bachne
  ke liye key auto-sanitize (spaces/ghair-ASCII strip) hoti hai.
- AI ka kaam: **deep research** — scan/hunt results ka AI analysis,
  summary, behtar query tajweez. Core matching AI ke bagher mukammal.

## 4. Tasks page (automation)
- User **multiple tasks** bana sakta hai, **time ke hisaab** se
  (rozana makhsoos waqt / ek dafa makhsoos waqt par).
- Task types: References Hunt (references × target sources),
  Overall Scan (query), URL Watch (ek URL ki images me tabdeeli).
- Tasks **background me auto** chalte hain (WorkManager), results
  app me save + notification. Task list me last-run status/results.

## 5. Jo pehle se hai, barqarar
- Scan UI (bottom bar 3 modes, lightbox + download + similar images,
  Load More 50), Logs page, remote backend ka option (jis ke paas
  server ho) Settings me rehta hai — default ab on-device engine hai.

## Build staging
- **v2.0 foundation**: model download + on-device CLIP embedder +
  References page + AI settings/client. ✅ (2026-11-02)
- **v2.1**: in-app scan engine (Kotlin: discovery, adapters, extraction,
  matching) — Scan tab bagher kisi backend ke chalega.
- **v2.2**: Tasks page + background worker + notifications +
  AI deep-research results par.
