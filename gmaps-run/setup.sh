#!/usr/bin/env bash
# Lead Finder - one-time setup for macOS / Linux.
# Downloads the Google Maps scraper (checksum-verified) and the browser it uses.
set -euo pipefail
cd "$(dirname "$0")"

VERSION=1.20.0
case "$(uname -s)-$(uname -m)" in
  Darwin-arm64)               ASSET="darwin-arm64"; SHA=8c0be7897b84d6a9e17caa22db7f04eb98ef9250ec27f7f43bde85b2824ebbb7 ;;
  Darwin-x86_64)              ASSET="darwin-amd64"; SHA=82468bfdedcef20cbddfe8a064414354397673c0f18263a0b8c6f33d806f3144 ;;
  Linux-x86_64)               ASSET="linux-amd64";  SHA=1838984cd77e6a6e94501d733566544b9ea954544649da4fe6af8774e14b9adc ;;
  Linux-aarch64|Linux-arm64)  ASSET="linux-arm64";  SHA=735f5cb82c183b6b741a6b5410e3ff39a01e4289b0a2f5daaa21ca0b6d2006fd ;;
  *) echo "Unsupported system: $(uname -s) $(uname -m)"; exit 1 ;;
esac
URL="https://github.com/gosom/google-maps-scraper/releases/download/v${VERSION}/google_maps_scraper-${VERSION}-${ASSET}"

echo "[1/3] Checking Python..."
command -v python3 >/dev/null || { echo "Python 3 is not installed. Install it, then run this again."; exit 1; }

echo "[2/3] Getting the Google Maps scraper v${VERSION} (about 60 MB)..."
if [ -f google-maps-scraper ]; then
  echo "      already downloaded"
else
  tmp="$(mktemp)"
  curl -fL --progress-bar -o "$tmp" "$URL"
  if command -v sha256sum >/dev/null; then actual=$(sha256sum "$tmp" | cut -d' ' -f1); else actual=$(shasum -a 256 "$tmp" | cut -d' ' -f1); fi
  if [ "$actual" != "$SHA" ]; then rm -f "$tmp"; echo "Checksum mismatch: not the official file. Nothing was installed."; exit 1; fi
  mv "$tmp" google-maps-scraper && chmod +x google-maps-scraper
  echo "      downloaded and verified"
fi

echo "[3/3] Installing the browser the scraper uses (one time, a few hundred MB)..."
PLAYWRIGHT_INSTALL_ONLY=1 ./google-maps-scraper
mkdir -p webdata
chmod +x start.sh
echo
echo "Setup finished. Start it with:  ./start.sh"
