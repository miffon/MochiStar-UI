# MochiStar (麻糬星)

繁體中文 | [English](https://github.com/miffon/MochiStar-UI/blob/main/README.en.md)

<img src="src/assets/logo.svg" width="128">

跨平台的影音下載與轉檔 GUI, 使用 PySide6、yt-dlp 與 FFmpeg

## 下載

### 最新正式版

[![Windows](https://img.shields.io/github/v/release/miffon/MochiStar-UI?display_name=tag&label=Windows&logo=windows)](https://github.com/miffon/MochiStar-UI/releases/latest/download/MochiStar-Windows-portable.zip)
[![macOS](https://img.shields.io/github/v/release/miffon/MochiStar-UI?display_name=tag&label=macOS&logo=apple)](https://github.com/miffon/MochiStar-UI/releases/latest/download/MochiStar-macOS-installer.dmg)
[![Ubuntu/Linux](https://img.shields.io/github/v/release/miffon/MochiStar-UI?display_name=tag&label=Ubuntu%2FLinux&logo=linux)](https://github.com/miffon/MochiStar-UI/releases/latest/download/MochiStar-Linux.tar.gz)

### 最新測試版

前往 [Build MochiStar Actions 頁面](https://github.com/miffon/MochiStar-UI/actions/workflows/build.yml), 開啟最新的 `Test Build 版本號-test`, 再從 Artifacts 下載對應平台檔案

下載 Actions artifacts 前需要先登入 GitHub

### macOS 獨立系統測試

維護者可前往 [macOS System Test Actions 頁面](https://github.com/miffon/MochiStar-UI/actions/workflows/macos-system-test.yml) 手動執行測試

測試會在 Apple Silicon 與 Intel runner 驗證封裝程式、網路、yt-dlp、外部工具與權限，並提供下列 artifacts:

- `macos-ui-arm64-*`: 雙主題、雙語的逐頁截圖與 HTML 圖集
- `macos-system-arm64-*`: Apple Silicon packaged application 診斷
- `macos-system-intel-*`: Intel packaged application 診斷

UI 只顯示簡短錯誤，完整 traceback 與 yt-dlp 診斷保存在 system test artifact

## 後續更新
MochiStar 預設自動檢查更新, 有新的更新時會通知你, 協助你下載更新:

- Windows: 關閉 MochiStar、解壓縮 ZIP，然後用新的 `MochiStar` 資料夾取代舊版
- macOS: 關閉 MochiStar、開啟 DMG，然後將 `MochiStar.app` 拖入「應用程式」並取代舊版。若系統阻擋開啟，請到「系統設定」>「隱私權與安全性」選擇「仍要打開」
- Ubuntu/Linux: 關閉 MochiStar、解壓縮 tar.gz，然後用新的 `MochiStar` 資料夾取代舊版。發布的 archive 會保留執行權限

若程式下載失敗，可改用更新視窗的「開啟 Release 頁面」透過瀏覽器下載
