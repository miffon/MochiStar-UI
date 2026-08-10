# macOS System Tests

這個目錄保存 macOS script-based 和 packaged application 綜合測試, 不屬於 pytest test suite

- `capture_ui.py` 建立固定 UI 狀態並擷取每個主頁
- `run_platform_checks.py` 驗證 subprocess, Unicode path 和 filesystem permission
- `run_source_probes.py` 從 source 執行 update, local media 和外部 media probes
- `preflight_media_url.py` 在啟動 architecture matrix 前確認外部 media URL 可分析
- GitHub Actions workflow 位於 `.github/workflows/macos-system-test.yml`

測試由 `workflow_dispatch` 手動執行, 預設只跑 ARM64 和 Intel script system tests
勾選 `run_packaged` 才會在 script tests 通過後建立並測試兩個 architecture 的 DMG

`media_url` 有預設測試連結, 每次執行也可以注入其他 yt-dlp 支援的 URL
URL preflight 會將錯誤分成 `url_content`, `permission`, `network` 或 `unknown`, 失敗時不啟動後續 macOS jobs

正常 artifact 只包含根目錄的 `README.md`, `report.log` 和 `ui/` screenshot 資料夾
下載後可以直接閱讀或整包上傳給 LLM, 不需要整理原始工作目錄
需要檢查原始 PNG, JSON 和 log 時才勾選 `include_raw_diagnostics`, 預設不會上傳中間檔案

Artifacts 使用下列固定名稱, 詳細 log 和 traceback 不顯示在 application UI

- `macos-url-preflight`
- `macos-script-arm64` 和 `macos-script-intel`
- `macos-package-arm64` 和 `macos-package-intel`, 只有 `run_packaged` 開啟時產生

勾選 `include_raw_diagnostics` 時才會另外產生對應的 `macos-debug-*` artifacts
