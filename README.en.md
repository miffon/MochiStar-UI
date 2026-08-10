# MochiStar

[繁體中文](https://github.com/miffon/MochiStar-UI/blob/main/README.md) | English

<img src="src/assets/logo.svg" width="128">

A cross-platform GUI for downloading and converting media, built with PySide6, yt-dlp, and FFmpeg

## Downloads

### Latest stable release

[![Windows](https://img.shields.io/github/v/release/miffon/MochiStar-UI?display_name=tag&label=Windows&logo=windows)](https://github.com/miffon/MochiStar-UI/releases/latest/download/MochiStar-Windows-portable.zip)
[![macOS](https://img.shields.io/github/v/release/miffon/MochiStar-UI?display_name=tag&label=macOS&logo=apple)](https://github.com/miffon/MochiStar-UI/releases/latest/download/MochiStar-macOS-installer.dmg)
[![Ubuntu/Linux](https://img.shields.io/github/v/release/miffon/MochiStar-UI?display_name=tag&label=Ubuntu%2FLinux&logo=linux)](https://github.com/miffon/MochiStar-UI/releases/latest/download/MochiStar-Linux.tar.gz)

### Latest test build

Open the [Build MochiStar Actions page](https://github.com/miffon/MochiStar-UI/actions/workflows/build.yml), select the latest `Test Build version-test`, then download the artifact for your platform

You must be signed in to GitHub to download Actions artifacts

### Independent macOS system test

Maintainers can manually run the [macOS System Test workflow](https://github.com/miffon/MochiStar-UI/actions/workflows/macos-system-test.yml)

The workflow checks packaging, networking, yt-dlp, external tools, and permissions on Apple Silicon and Intel runners. It provides these artifacts:

- `macos-ui-arm64-*`: Per-page screenshots and an HTML gallery for both themes and languages
- `macos-system-arm64-*`: Apple Silicon packaged application diagnostics
- `macos-system-intel-*`: Intel packaged application diagnostics

The UI keeps error feedback concise. Full tracebacks and yt-dlp diagnostics are stored in the system test artifact

## Updating MochiStar

MochiStar checks for updates automatically and notifies you when a newer version is available:

- Windows: Close MochiStar, extract the ZIP, then replace the old `MochiStar` folder with the new one
- macOS: Close MochiStar, open the DMG, then drag `MochiStar.app` into Applications and replace the existing app. If macOS blocks the app, open System Settings > Privacy & Security and select Open Anyway
- Ubuntu/Linux: Close MochiStar, extract the tar.gz, then replace the old `MochiStar` folder with the new one. The release archive preserves executable permissions

If the in-app download fails, select Open Release Page in the update window and download the update with your browser
