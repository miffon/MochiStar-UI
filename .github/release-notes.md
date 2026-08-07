<!-- 每次 release 前直接修改這個檔案, workflow 會自動加上版本標題 -->

下載:
- `MochiStar-Windows-portable.zip`: 關閉 MochiStar, 解壓縮後用新的 `MochiStar` 資料夾取代舊版
- `MochiStar-macOS-installer.dmg`: 關閉 MochiStar, 開啟 DMG 後將 `MochiStar.app` 拖入「應用程式」並取代舊版
- `MochiStar-Linux.tar.gz`: 關閉 MochiStar, 解壓縮後用新的 `MochiStar` 資料夾取代舊版, archive 會保留執行權限

使用前請依需要安裝 FFmpeg 與 JavaScript Runtime, 或在「設定」指定工具目錄

MochiStar 不會自動執行、解壓縮或覆蓋更新檔。設定、列隊與紀錄存放在系統 application data 目錄, 替換程式不會清除這些資料

macOS 版本使用 ad-hoc signing, 沒有 Apple notarization。若系統阻擋啟動, 請到「系統設定」>「隱私權與安全性」確認後選擇仍要打開
