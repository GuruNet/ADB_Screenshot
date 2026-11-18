#!/bin/sh
set -eu
cd "$(dirname "$0")"

if [ "$(uname -s)" != "Darwin" ]; then
    echo "macOS에서 실행해 주세요." >&2
    exit 1
fi
if [ ! -x .venv/bin/python ]; then
    echo "먼저 python3 -m venv .venv 로 가상환경을 만들어 주세요." >&2
    exit 1
fi
if ! .venv/bin/python -c 'import PyInstaller' >/dev/null 2>&1; then
    echo ".venv/bin/python -m pip install -r requirements-build.txt 를 실행해 주세요." >&2
    exit 1
fi
if [ ! -f gps_helper/gps-helper.apk ]; then
    echo "gps_helper/gps-helper.apk가 필요합니다. gps_helper/build.sh로 먼저 빌드해 주세요." >&2
    exit 1
fi

icon_source='assets/adb-screenshot-icon.png'
iconset='build/native/adb-screenshot.iconset'
mkdir -p "$iconset"
for size in 16 32 128 256 512; do
    retina=$((size * 2))
    icon_name="$iconset/icon_${size}x${size}"
    sips -z "$size" "$size" "$icon_source" --out "$icon_name.png" >/dev/null
    sips -z "$retina" "$retina" "$icon_source" --out "$icon_name@2x.png" >/dev/null
done
iconutil -c icns "$iconset" -o build/native/adb-screenshot.icns

xcrun swiftc -O -target "$(uname -m)-apple-macosx26.0" vision_ocr.swift -o build/native/vision_ocr
.venv/bin/python -m PyInstaller --noconfirm --clean --workpath build/pyinstaller ADB_Screenshot.spec
app_bundle='dist/ADB Screenshot.app'
codesign --verify --deep --strict "$app_bundle"
echo "빌드 완료: $(pwd)/$app_bundle"
