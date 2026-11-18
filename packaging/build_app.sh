#!/bin/bash
# Build dist/DiskReport.app with PyInstaller (--onedir --windowed; never --onefile, which
# unpacks to a temp directory on every launch).
#
#   packaging/build_app.sh            uses .venv/bin/python, or $PYTHON if set
#
# Needs: PyQt6 (requirements.txt) and PyInstaller (requirements-build.txt).
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-.venv/bin/python}"
VERSION="$("$PY" -c 'import diskreport; print(diskreport.__version__)')"
BUNDLE_ID="$("$PY" -c 'from diskreport.app import BUNDLE_ID; print(BUNDLE_ID)')"

"$PY" packaging/make_icon.py build/icon

rm -rf dist/DiskReport.app build/pyinstaller
"$PY" -m PyInstaller --noconfirm --clean \
    --onedir --windowed \
    --name DiskReport \
    --icon "$PWD/build/icon/DiskReport.icns" \
    --osx-bundle-identifier "$BUNDLE_ID" \
    --paths "$PWD" \
    --distpath "$PWD/dist" --workpath "$PWD/build/pyinstaller" --specpath "$PWD/build" \
    "$PWD/packaging/run_app.py"

# Menu bar app: no Dock icon (LSUIElement), plus version strings.
"$PY" - "$VERSION" <<'EOF'
import plistlib, sys
path = "dist/DiskReport.app/Contents/Info.plist"
with open(path, "rb") as f:
    info = plistlib.load(f)
info["LSUIElement"] = True
info["CFBundleShortVersionString"] = sys.argv[1]
info["CFBundleVersion"] = sys.argv[1]
info["CFBundleDisplayName"] = "DiskReport"
info["NSHumanReadableCopyright"] = "Never erases files. Moves items to the Trash only when you confirm."
with open(path, "wb") as f:
    plistlib.dump(info, f)
EOF

# Editing Info.plist breaks the bundle's ad-hoc signature; sign it again (still unsigned
# as far as Gatekeeper is concerned: there is no Developer ID).
codesign --force --deep --sign - dist/DiskReport.app
echo "Built dist/DiskReport.app (version $VERSION)"
