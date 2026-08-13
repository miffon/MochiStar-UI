#!/usr/bin/env bash
set -euo pipefail

artifact_name="${1:?DMG output path is required}"
volume_version="${2:-$(uv run python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')}"
volume_name="MochiStar $volume_version Installer"

# 建立原生 macOS application bundle
uv run --with nuitka==4.1.3 --with ordered-set --with zstandard python -m nuitka \
  --standalone \
  --enable-plugin=pyside6 \
  --include-package=yt_dlp \
  --include-package-data=yt_dlp \
  --nofollow-import-to=yt_dlp.extractor.lazy_extractors \
  --include-data-dir=src/assets=assets \
  --macos-create-app-bundle \
  --macos-app-name=MochiStar \
  --macos-app-icon=src/assets/logo.icns \
  --assume-yes-for-downloads \
  --output-dir=dist \
  --output-filename=MochiStar \
  src/main.py

app_path="dist/main.app"
if [ ! -d "$app_path" ]; then
  echo "main.app was not found under dist"
  find dist -maxdepth 4 -print
  exit 1
fi

# 建立和 release 相同的 DMG
mkdir -p artifact/dmg-root
cp -R "$app_path" artifact/dmg-root/MochiStar.app
ln -s /Applications artifact/dmg-root/Applications
chmod +x artifact/dmg-root/MochiStar.app/Contents/MacOS/*
xattr -cr artifact/dmg-root/MochiStar.app
codesign --force --deep --sign - artifact/dmg-root/MochiStar.app
hdiutil create -volname "$volume_name" -srcfolder artifact/dmg-root -ov -format UDZO "$artifact_name"
