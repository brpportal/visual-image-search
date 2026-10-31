#!/usr/bin/env bash
# Visual Image Search — installer for the INSIDE of proot-distro Ubuntu.
#
# Run order (full steps in README.md):
#   [Termux]  pkg update && pkg install proot-distro git
#   [Termux]  proot-distro install ubuntu
#   [Termux]  proot-distro login ubuntu
#   [Ubuntu]  git clone <this repo> && cd visual-image-search && bash setup.sh
#
# Why this split: PyTorch and friends do not install in native Termux;
# inside proot Ubuntu they install normally from wheels.
set -e
cd "$(dirname "$0")"

echo "=== [1/4] System packages ==="
apt update
apt install -y python3 python3-pip python3-venv git

echo "=== [2/4] Python virtual environment ==="
python3 -m venv venv
# shellcheck disable=SC1091
source venv/bin/activate
pip install --upgrade pip

echo "=== [3/4] PyTorch (CPU wheels — much smaller than the CUDA build) ==="
# torch AND torchvision together from the CPU index: if torchvision comes
# from plain PyPI later (as an open_clip dependency) it is built against a
# different torch ABI and fails with "operator torchvision::nms does not
# exist". Installing the +cpu pair first prevents that.
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

echo "=== [4/4] Project dependencies ==="
pip install -r requirements.txt

mkdir -p data/images data/thumbs static

cat <<'MSG'

=== Setup complete ===
Next steps:
  1) Put public page URLs in urls.txt (one per line)
  2) source venv/bin/activate
     python crawl.py        # download images + metadata
     python index.py        # build the FAISS index (first run downloads the CLIP model, ~350 MB)
     uvicorn app:app --host 0.0.0.0 --port 8000
  3) Open http://localhost:8000 in the phone browser

Quick self-test with 20 built-in sample images:
     python make_samples.py && python index.py
MSG
