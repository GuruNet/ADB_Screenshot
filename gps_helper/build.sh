#!/bin/sh
set -eu

sdk_root=${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}
if [ -z "$sdk_root" ]; then
    printf '%s\n' 'ANDROID_HOME 또는 ANDROID_SDK_ROOT에 Android SDK 경로를 설정하세요.' >&2
    exit 1
fi
tools_dir=${GPS_BUILD_TOOLS:-"$sdk_root/build-tools/35.0.0"}
android_jar="$sdk_root/platforms/android-35/android.jar"
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
build_dir=$(mktemp -d "${TMPDIR:-/tmp}/adb-gps-build.XXXXXX")
trap 'rm -rf "$build_dir"' EXIT
mkdir -p "$build_dir/classes" "$build_dir/dex" "${HOME}/.android"
keystore="${HOME}/.android/debug.keystore"
if [ ! -f "$keystore" ]; then
    keytool -genkeypair -keystore "$keystore" -storepass android -keypass android \
        -alias androiddebugkey -dname 'CN=Android Debug,O=Android,C=US' \
        -keyalg RSA -keysize 2048 -validity 10000 >/dev/null 2>&1
fi
"$tools_dir/aapt" package -f -M "$here/AndroidManifest.xml" -I "$android_jar" -F "$build_dir/unsigned.apk"
javac -source 8 -target 8 -bootclasspath "$android_jar" -d "$build_dir/classes" \
    "$here"/src/io/github/adbscreenshot/gpstracker/*.java
"$tools_dir/d8" --min-api 26 --lib "$android_jar" --output "$build_dir/dex" "$build_dir"/classes/io/github/adbscreenshot/gpstracker/*.class
(cd "$build_dir/dex" && zip -q "$build_dir/unsigned.apk" classes.dex)
"$tools_dir/zipalign" -f 4 "$build_dir/unsigned.apk" "$build_dir/aligned.apk"
"$tools_dir/apksigner" sign --ks "$keystore" --ks-key-alias androiddebugkey \
    --ks-pass pass:android --key-pass pass:android --out "$here/gps-helper.apk" "$build_dir/aligned.apk"
"$tools_dir/apksigner" verify "$here/gps-helper.apk"
printf '%s\n' "빌드 완료: $here/gps-helper.apk"
