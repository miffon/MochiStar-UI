# macTest

這個目錄保存 macOS script-based 和 packaged application 綜合測試, 不屬於 pytest test suite

- `capture_ui.py` 建立固定 UI 狀態並擷取每個主頁
- `run_platform_checks.py` 驗證 subprocess, Unicode path 和 filesystem permission
- `run_source_probes.py` 從 source 執行 update, local media 和外部 media probes
- `preflight_media_url.py` 在啟動 architecture matrix 前確認外部 media URL 可分析
- GitHub Actions workflow 位於 `.github/workflows/macos-system-test.yml`

測試由 `workflow_dispatch` 手動執行, 預設只跑 ARM64 和 Intel script tests
勾選 `run_packaged` 才會在 script tests 通過後建立並測試兩個 architecture 的 DMG

`media_url` 有預設測試連結, 每次執行也可以注入其他 yt-dlp 支援的 URL
URL preflight 會將錯誤分成 `url_content`, `permission`, `network` 或 `unknown`, 失敗時不啟動後續 macOS jobs

URL preflight 只負責在使用兩台 architecture runner 前排除失效連結、網站權限與網路問題
結果會寫入 Actions summary, 預設不會放進 artifact, 只有 `include_raw_diagnostics` 開啟時才上傳詳細資料

正常執行只產生一個 `macTest-results` artifact, GitHub 下載檔名為 `macTest-results.zip`
解壓後依 `script-arm64`, `script-intel` 和選配的 `package-*` 分類, 每個資料夾包含 `README.md`, `report.log` 和 `ui/`
下載後可以直接閱讀或整包上傳給 LLM, 不需要整理原始工作目錄
需要檢查原始 PNG, JSON 和 log 時才勾選 `include_raw_diagnostics`, 預設不會上傳中間檔案

勾選 `include_raw_diagnostics` 時才會另外產生對應的 `debug-*` artifacts
