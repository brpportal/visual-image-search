#!/data/data/com.termux/files/usr/bin/bash
# Image Scan — Termux backend environment setup (ek dafa chalao).
# App ke Setup screen se copy kiya gaya command isay chalata hai.
set -e
cd "$HOME/vis"

echo "== [1/4] Python packages (torch samet — pehli dafa waqt lage ga) =="
pip install --upgrade pip
pip install -r requirements-termux.txt

echo "== [2/4] Backend start script bana raha hoon =="
cat > "$HOME/vis/start-backend.sh" <<'EOS'
#!/data/data/com.termux/files/usr/bin/bash
cd "$HOME/vis"
export PYTHONUNBUFFERED=1
echo "Image Scan backend: http://127.0.0.1:8000/scan.html"
exec python -m uvicorn app:app --host 127.0.0.1 --port 8000
EOS
chmod +x "$HOME/vis/start-backend.sh"

echo "== [3/4] App ko Termux chalane ki ijazat (RUN_COMMAND) =="
mkdir -p "$HOME/.termux"
if ! grep -q "allow-external-apps" "$HOME/.termux/termux.properties" 2>/dev/null; then
  echo "allow-external-apps = true" >> "$HOME/.termux/termux.properties"
fi
termux-reload-settings || true

echo "== [4/4] Tayyar! =="
echo "Ab Image Scan app kholo — 'Backend Start Karo' dabao, ya yahan chalao:"
echo "  bash ~/vis/start-backend.sh"
