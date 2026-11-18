# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

project = Path(SPECPATH)
app_name = "ADB Screenshot"

a = Analysis(
    [str(project / "app.py")],
    pathex=[str(project)],
    binaries=[(str(project / "build/native/vision_ocr"), ".")],
    datas=[
        (str(project / "gps_helper/gps-helper.apk"), "gps_helper"),
        (str(project / "assets/adb-screenshot-icon.png"), "assets"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ADB_Screenshot",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    argv_emulation=False,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=app_name)
app = BUNDLE(
    coll,
    name=app_name + ".app",
    icon=str(project / "build/native/adb-screenshot.icns"),
    bundle_identifier="io.github.adbscreenshot.collector",
    info_plist={
        "CFBundleDisplayName": app_name,
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "10",
        "LSMultipleInstancesProhibited": True,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "26.0",
        "NSLocalNetworkUsageDescription": "같은 네트워크의 Android 기기를 찾아 무선 ADB로 화면과 GPS 기록을 수집합니다.",
        "NSBonjourServices": ["_adb-tls-connect._tcp", "_adb-tls-pairing._tcp"],
    },
)
