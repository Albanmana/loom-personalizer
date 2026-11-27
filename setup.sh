#!/bin/bash
# Installs what the workflow needs. Safe to run again. Everything except Homebrew packages stays in this folder.
set -euo pipefail
cd "$(dirname "$0")"

command -v brew >/dev/null || { echo "Install Homebrew first: https://brew.sh (it asks for your Mac password)"; exit 1; }
for formula in ffmpeg node@22 whisper-cpp python@3.12; do
  brew list --versions "$formula" >/dev/null 2>&1 || brew install "$formula"
done

PYTHON="$(brew --prefix python@3.12)/bin/python3.12"
[ -x .venv/bin/python ] || "$PYTHON" -m venv .venv
.venv/bin/pip install --quiet -r requirements.txt

NODE_BIN="$(brew --prefix node@22)/bin"
if [ ! -f data/tools/node_modules/n8n/bin/n8n ]; then
  mkdir -p data/tools
  PATH="$NODE_BIN:$PATH" "$NODE_BIN/npm" install --prefix data/tools n8n@2.22.6
fi

# n8n needs sqlite3's install script. `ignore-scripts=true` in ~/.npmrc skips it, so run that one package's only.
if ! (cd data/tools 2>/dev/null && "$NODE_BIN/node" -e "require('sqlite3')" 2>/dev/null); then
  echo "Building n8n's database module (sqlite3)"
  PATH="$NODE_BIN:$PATH" "$NODE_BIN/npm" rebuild --prefix data/tools --ignore-scripts=false sqlite3
fi

MODEL=data/models/ggml-small.en.bin
if [ ! -f "$MODEL" ]; then
  mkdir -p data/models
  echo "Downloading the speech model (about 490 MB) from huggingface.co/ggerganov/whisper.cpp"
  curl -fL --retry 3 -o "$MODEL.part" https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin
  if ! echo "c6138d6d58ecc8322097e0f987c32f1be8bb0a18532a3f88f734d1bbf9c41e5d  $MODEL.part" | shasum -a 256 -c - >/dev/null 2>&1; then
    rm -f "$MODEL.part"
    echo "The speech model does not match the expected file, so it was not installed. Run ./setup.sh again."
    exit 1
  fi
  mv "$MODEL.part" "$MODEL"
fi

.venv/bin/python studio.py init
echo "Installed. Next: python3 studio.py doctor"
