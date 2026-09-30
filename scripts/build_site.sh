#!/usr/bin/env bash
# Copy the demo assets next to site/index.html (kept out of git to avoid duplicating binaries).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p site/assets
cp submission/settle_demo.mp4 submission/thumbnail_16x9.png docs/architecture.png media/rcs/logo.png site/assets/
cp docs/screenshots/rcs-*.jpg site/assets/
# SRT -> WebVTT for the <track> element
{ echo "WEBVTT"; echo; sed -E 's/([0-9]{2}:[0-9]{2}:[0-9]{2}),([0-9]{3})/\1.\2/g' submission/settle_demo.srt; } > site/assets/settle_demo.vtt
echo "site/assets ready: $(ls site/assets | wc -l | tr -d ' ') files"
