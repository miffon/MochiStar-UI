from __future__ import annotations

import sys
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QAbstractButton, QComboBox, QGroupBox, QLabel, QLineEdit, QTableWidget, QWidget


LANGUAGES = (("English", "en"), ("繁體中文", "zh_TW"))
_current_language = "en"
_ZH_TW = {
    "Media toolkit": "影音工具箱",
    "Video/Audio": "影音",
    "Media": "影音",
    "Subtitle": "字幕",
    "Convert": "轉檔",
    "Replace": "替換",
    "Replacement": "替換",
    "File Analysis": "檔案分析",
    "Queue": "列隊",
    "Log": "紀錄",
    "Settings": "設定",
    "View format, video, audio, and other details about media files": "查看影音檔案的格式、畫面、音訊與其他詳細資訊",
    "Analyze GOP": "分析 GOP",
    "Analyzing GOP...": "正在分析 GOP...",
    "GOP Analyzed": "GOP 已分析",
    "Retry GOP Analysis": "重新分析 GOP",
    "Scan video frames to measure the keyframe interval": "需要時掃描影片影格並計算關鍵影格間隔",
    "Close Analysis Card": "關閉分析卡片",
    "Browse Files": "瀏覽檔案",
    "Clear All": "全部清空",
    "Drop files here or browse for files to inspect": "將檔案拖放到這裡, 或瀏覽檔案進行分析",
    "Analyze Items": "分析所選項目",
    "Remove": "移除",
    "File": "檔案",
    "Container": "容器格式",
    "File Size": "檔案大小",
    "Overall Bitrate": "整體位元率",
    "Video Stream": "影片串流",
    "Audio Stream": "音訊串流",
    "Subtitle Stream": "字幕串流",
    "Other Stream": "其他串流",
    "Codec": "轉碼器",
    "Bitrate": "位元率",
    "Color Space": "色彩空間",
    "Color Transfer": "色彩轉換",
    "Sample Rate": "採樣率",
    "Channels": "聲道數",
    "Channel Layout": "聲道配置",
    "Stream Title": "串流標題",
    "Chapters": "章節",
    "{value} frames": "{value} 個影格",
    "{value} frames (average {average}, range {minimum}-{maximum})": "{value} 個影格 (平均 {average}, 範圍 {minimum}-{maximum})",
    "File analysis disabled: FFprobe is unavailable": "無法使用檔案分析: 找不到 FFprobe",
    "Paste one media or playlist URL": "貼上單一媒體或播放清單網址",
    "Analyze": "分析",
    "Analyzing...": "分析中...",
    "Selected": "選取",
    "Sel.": "選取",
    "Title": "標題",
    "Duration": "片長",
    "Uploader": "上傳者",
    "Site": "網站",
    "Select All": "全選",
    "Select None": "取消全選",
    "Best Video + Audio": "最佳影片 + 音訊",
    "Video Only": "僅影片",
    "Audio Only": "僅音訊",
    "Best": "最佳",
    "Auto": "自動",
    "Original": "原始格式",
    "MP4 Video": "MP4 影片",
    "MOV Video": "MOV 影片",
    "MKV Video": "MKV 影片",
    "WebM Video": "WebM 影片",
    "MP3 Audio": "MP3 音訊",
    "M4A Audio": "M4A 音訊",
    "Opus Audio": "Opus 音訊",
    "FLAC Audio": "FLAC 音訊",
    "WAV Audio": "WAV 音訊",
    "SRT Subtitle": "SRT 字幕",
    "WebVTT Subtitle": "WebVTT 字幕",
    "ASS Subtitle": "ASS 字幕",
    "Automatic\tUse download preset": "自動\t使用下載預設",
    "Choose an output folder": "選擇輸出資料夾",
    "Browse": "瀏覽",
    "None": "無",
    "Browser": "瀏覽器",
    "Netscape Cookie File": "Netscape Cookie 檔案",
    "Optional browser profile": "選填的瀏覽器設定檔",
    "Add to Queue": "加入列隊",
    "Metadata": "媒體資訊",
    "Playlist Items": "播放清單項目",
    "Download Options": "下載選項",
    "Output and Cookies": "輸出與 Cookie",
    "Advanced Formats": "進階格式",
    "Preset": "預設",
    "Resolution": "解析度",
    "Video Container": "影片容器",
    "Audio Output": "音訊輸出",
    "Output Folder": "輸出資料夾",
    "Cookie Source": "Cookie 來源",
    "Browser Profile": "瀏覽器設定檔",
    "Cookie File": "Cookie 檔案",
    "Video Format": "影片格式",
    "Audio Format": "音訊格式",
    "Download Media": "下載媒體",
    "Review the media, then choose what you want to download and in which format": "先查看媒體內容，再選擇要下載的項目與格式",
    "Video": "影片",
    "Audio": "音訊",
    "Language": "語言",
    "Name": "名稱",
    "Source": "來源",
    "Format": "格式",
    "Analyze a URL to list available subtitles": "分析網址以列出可用字幕",
    "No manual subtitles or auto captions were found": "找不到人工字幕或自動產生字幕",
    "No manual subtitles were found": "找不到人工字幕",
    "Include auto-generated subtitles": "包含自動產生的字幕",
    "Include automatically generated captions when analyzing the URL": "分析網址時一併列出自動產生的字幕",
    "Download Subtitles": "下載字幕",
    "Find and download subtitles available for a video": "尋找並下載影片提供的字幕",
    "Manual": "人工",
    "Type": "類型",
    "URL": "網址",
    "Status": "狀態",
    "Progress": "進度",
    "Output": "輸出",
    "Error": "錯誤",
    "Download": "下載",
    "Conversion": "轉檔",
    "Pending": "等待中",
    "Running": "執行中",
    "Paused": "已暫停",
    "Completed": "已完成",
    "Failed": "失敗",
    "Cancelled": "已取消",
    "Pause Queue": "暫停列隊",
    "Start Queue": "啟動列隊",
    "Cancel": "取消",
    "Retry / Resume": "重試 / 繼續",
    "Remove": "移除",
    "Move Up": "上移",
    "Move Down": "下移",
    "Workers": "同時執行數",
    "Task Queue": "任務列隊",
    "Run, reorder, retry, or cancel download, subtitle, and conversion tasks": "執行、調整順序、重試或取消下載、字幕與轉檔任務",
    "View and manage tasks that are waiting, running, or completed": "查看並管理等待中、執行中與已完成的任務",
    "Replace Audio": "替換音訊",
    "Replace a video's audio or combine visual media with new audio": "替換影片音訊，或將畫面素材搭配新的音訊",
    "Visual": "畫面",
    "Drop one file here or browse": "拖放一個檔案到這裡, 或瀏覽檔案",
    "No file selected": "尚未選擇檔案",
    "Static image": "靜態圖片",
    "Loop": "循環",
    "Delay": "延遲",
    "Positive values delay the source with black video or silence; negative values skip the beginning": "正值會延後素材並補黑畫面或靜音, 負值會略過素材開頭",
    "Drop exactly one file into this card": "每張卡片一次只能拖放一個檔案",
    "Common Settings": "共同設定",
    "Longest Source": "較長來源",
    "Shortest Source": "較短來源",
    "Total Duration": "總時間",
    "Custom Duration": "自訂時間",
    "Aspect Ratio": "畫面比例",
    "Image Fit": "畫面配置",
    "Fit with Black Bars": "完整顯示並留黑邊",
    "Fill and Crop": "填滿並裁切",
    "Cut Head": "切除開頭",
    "Cut Tail": "切除結尾",
    "Force Re-encoding": "強制重新編碼",
    "Choose the finished timeline length": "選擇成品時間軸的總長度",
    "Hours:Minutes:Seconds.ms": "小時:分鐘:秒.毫秒",
    "Enter seconds, MM:SS, or HH:MM:SS.mmm": "輸入秒數、MM:SS 或 HH:MM:SS.mmm",
    "Keep the source shape or use a common video canvas": "維持來源比例, 或使用常見的影片畫面比例",
    "Show the whole image with black bars or crop it to fill the canvas": "使用黑邊完整顯示畫面, 或裁切成滿版",
    "Remove this amount from the beginning of the finished timeline": "從完成的時間軸開頭切除指定秒數",
    "Remove this amount from the end of the finished timeline": "從完成的時間軸結尾切除指定秒數",
    "Encode both video and audio even when compatible streams could be copied": "即使串流可以直接保留, 仍重新編碼畫面與音訊",
    "Choose Visual Source": "選擇畫面來源",
    "Choose Audio Source": "選擇音訊來源",
    "Choose both a visual source and an audio source": "請同時選擇畫面與音訊來源",
    "The visual source does not contain a video stream": "畫面來源沒有可用的影片或圖片串流",
    "The audio source does not contain an audio stream": "音訊來源沒有可用的音訊串流",
    "Custom duration must be greater than zero": "自訂時間必須大於 0",
    "The head and tail cuts remove the entire output": "切除開頭與結尾後沒有剩餘的輸出內容",
    "Audio Stream Copy cannot be used when the audio timeline or format must be changed": "音訊需要調整時間軸或格式時無法使用 Stream Copy",
    "Choose existing visual and audio source files": "請選擇存在的畫面與音訊來源檔案",
    "Video: {video}; Audio: {audio}; Duration: {duration}s": "畫面: {video}; 音訊: {audio}; 時間: {duration} 秒",
    "Add Files": "加入檔案",
    "Drag and drop local files here": "將本機檔案拖放到這裡",
    "Remove Items": "移除所選項目",
    "Clear": "清除",
    "Encode": "編碼",
    "Remux / Stream Copy": "重新封裝 / Stream Copy",
    "CPU (Software)": "CPU 軟體編碼",
    "Conversion Options": "轉檔選項",
    "Target Format": "目標格式",
    "Output Format": "輸出格式",
    "Mode": "模式",
    "Encoder": "編碼器",
    "Simple Settings": "簡易設定",
    "General Settings": "一般設定",
    "Advanced Settings": "進階設定",
    "Output Type": "輸出類型",
    "Default": "預設值",
    "Save As": "另存預設",
    "Update": "更新",
    "Rename": "重新命名",
    "Delete": "刪除",
    "Modified": "已修改",
    "Acceleration": "硬體加速",
    "Apple ProRes": "Apple ProRes",
    "Custom": "自訂",
    "Allow upscale": "允許放大",
    "Upscale": "允許放大",
    "Frame Rate": "每秒影格數",
    "Frames Per Second": "每秒影格數",
    "Quality": "品質",
    "Video Bitrate": "影片位元率",
    "Maximum Bitrate": "最大位元率",
    "Audio Quality": "音訊位元率",
    "Average Bitrate": "平均位元率",
    "Constant Bitrate (CBR)": "固定位元率(CBR)",
    "Constant Rate Factor (CRF)": "固定速率(CRF)",
    "Average Bitrate (VBR)": "平均位元率(VBR)",
    "Average Bitrate (VBR 2-Pass)": "平均位元率(VBR 2-Pass)",
    "Low - 30": "低 - 30",
    "Medium - 60": "中 - 60",
    "High - 120": "高 - 120",
    "H.264 Profile": "H.264 Profile",
    "ProRes Profile": "ProRes Profile",
    "Profile": "Profile",
    "Transcoder Settings": "轉碼器設定",
    "Pixel Format": "Pixel Format",
    "Video Codec": "影片編碼格式",
    "Audio Codec": "音訊編碼格式",
    "Video Transcoder": "影片轉碼器",
    "Audio Transcoder": "音訊轉碼器",
    "Audio Bitrate": "音訊位元率",
    "Audio Sample Rate": "音訊採樣率",
    "Stream Copy": "Stream Copy",
    "PCM 16-bit": "PCM 16-bit",
    "PCM 24-bit": "PCM 24-bit",
    "No Audio": "無音訊",
    "Mute": "靜音",
    "Preset name": "Preset 名稱",
    "Invalid Preset Name": "Preset 名稱無效",
    "Preset name must be unique and cannot be Default": "Preset 名稱不可重複，也不可使用 Default",
    "Save Conversion Preset": "另存預設",
    "Update Preset": "更新 Preset",
    "Rename Conversion Preset": "重新命名轉檔 Preset",
    "Delete Preset": "刪除 Preset",
    "Replace {name} with the current settings?": "要用目前設定覆寫 {name} 嗎?",
    "Delete {name}?": "要刪除 {name} 嗎?",
    "ProRes output requires MOV": "ProRes 輸出必須使用 MOV",
    "Unsupported ProRes profile": "不支援此 ProRes profile",
    "Resolution height must be an even number from 2 to 8192": "解析度高度必須是 2 到 8192 之間的偶數",
    "FPS must be between 1 and 240": "FPS 必須介於 1 到 240",
    "CRF must be between 0 and 51": "CRF 必須介於 0 到 51",
    "Video bitrate must be greater than zero": "Video bitrate 必須大於 0",
    "Maximum bitrate must be greater than zero": "最大位元率必須大於 0",
    "Maximum bitrate must not be lower than the average bitrate": "最大位元率不可低於平均位元率",
    "Audio bitrate must be greater than zero": "Audio bitrate 必須大於 0",
    "Audio bitrate is unavailable for lossless output": "無損音訊輸出不使用 bitrate",
    "Unsupported audio sample rate": "不支援這個音訊採樣率",
    "MP3 bitrate must not exceed 320 kbps": "MP3 bitrate 不可超過 320 kbps",
    "Opus bitrate must not exceed 512 kbps": "Opus bitrate 不可超過 512 kbps",
    "PCM audio requires MOV output": "PCM 音訊必須使用 MOV 輸出",
    "Choose where converted files are saved": "選擇轉檔完成後的檔案儲存位置",
    "Choose whether to create a video, audio file, or subtitle file": "選擇輸出影片、音訊或字幕檔案",
    "Video copies compatible video and audio streams without re-encoding. Audio extracts the source audio unchanged; MP3 output requires an MP3 source track. Incompatible formats are reported before queuing. Subtitle conversion does not support this mode": "影片會直接複製相容的影像與音訊串流, 不重新編碼. 音訊會原樣抽出來源音軌; 輸出 MP3 時來源音軌必須已是 MP3. 不相容格式會在加入列隊前提示. 字幕轉檔不支援此模式",
    "Prefer an available H.264 hardware encoder; unsupported settings fall back to CPU": "優先使用可用的 H.264 硬體編碼器, 不支援目前設定時改用 CPU",
    "Load or manage a complete reusable video specification": "載入或管理可重複使用的完整影片規格",
    "Choose the output container; ProRes automatically uses MOV": "選擇輸出容器, ProRes 會自動使用 MOV",
    "Encode applies the settings below; Remux copies compatible streams without re-encoding": "編碼會套用下方設定, Remux 則不重新編碼並複製相容的串流",
    "Choose H.264, Apple ProRes, or the safe default for the selected container": "選擇 H.264、Apple ProRes 或目前容器的安全預設編碼格式",
    "Profile options change with the codec: H.264 controls compatibility, while ProRes controls its editing profile": "Profile 選項會跟著 codec 切換: H.264 控制相容性, ProRes 控制剪輯規格",
    "Set output height while FFmpeg calculates an even width and preserves display aspect ratio": "設定輸出高度, FFmpeg 會維持顯示比例並計算偶數寬度",
    "Allow output larger than the source; otherwise the selected resolution only scales down": "允許輸出大於來源, 否則指定解析度只會縮小、不會放大",
    "Keep the source frame rate or generate a constant output frame rate": "保留來源幀率或產生固定輸出幀率",
    "Choose automatic rate control, quality-based CRF, or an average bitrate in Mbps": "選擇自動控制、依品質決定的 CRF 或 Mbps 平均位元率",
    "Set the fixed keyframe interval; All-I makes every frame a keyframe": "設定固定關鍵幀間隔, All-I 會讓每一幀都是關鍵幀",
    "Choose how the audio track is encoded, copied, or removed from the video": "選擇影片音軌要重新編碼、直接複製或移除",
    "Remove the audio track from the output video": "移除輸出影片中的音軌",
    "Set AAC bitrate; other audio codecs ignore this setting": "設定 AAC bitrate, 其他音訊編碼格式會忽略此選項",
    "Load or manage a reusable audio output specification": "載入或管理可重複使用的音訊輸出規格",
    "Choose the audio codec and output container": "選擇音訊編碼格式與輸出容器",
    "Encode creates the selected format; Remux only works when the source audio is compatible": "編碼會建立選定格式, Remux 只適用於來源音訊相容時",
    "Set bitrate for MP3, M4A, or Opus; lossless FLAC and WAV do not use this setting": "設定 MP3、M4A 或 Opus bitrate, 無損 FLAC 與 WAV 不使用此選項",
    "Choose the subtitle file format": "選擇字幕檔案格式",
    "Convert the file format without encoding. This is faster, but only works with compatible source and output formats": "不經過編碼直接轉換格式, 速度較快但需要符合特定格式條件",
    "Prefer supported hardware transcoding acceleration": "優先使用支援的硬體轉碼加速",
    "Choose the output file format. ProRes always uses MOV": "選擇輸出的檔案格式, ProRes 固定使用 MOV",
    "Choose how the video is compressed. Auto selects a suitable option for the output format": "選擇影片的壓縮方式, 自動會依輸出格式使用合適的設定",
    "Adjust compatibility for H.264, or choose the editing quality level for ProRes": "調整 H.264 的相容性, 或選擇 ProRes 的剪輯品質等級",
    "Choose the output height. The width is calculated automatically to keep the original proportions": "選擇輸出高度, 寬度會自動維持原始比例",
    "Keep the source frame rate, or convert it to a fixed value. A higher frame rate may increase the file size": "保留來源的每秒影格數, 或轉換成固定值. 每秒影格數越高, 檔案可能越大",
    "Control video quality and file size. A higher bitrate usually produces a larger file": "控制影片畫質與檔案大小, 位元率越高通常檔案越大",
    "Limit the highest bitrate used during VBR 2-Pass encoding": "限制 VBR 2-Pass 轉碼時使用的最高位元率",
    "Choose the keyframe interval. Shorter intervals are easier to edit, but usually create larger files": "選擇關鍵畫面的間隔, 間隔越短越方便剪輯, 但檔案通常越大",
    "Choose how the audio track is compressed, or copy the source track without encoding": "選擇音軌的壓縮方式, 也可以不經編碼直接複製來源音軌",
    "Set the AAC audio quality. A higher bitrate usually produces a larger file": "設定 AAC 音質, 位元率越高通常檔案越大",
    "Choose how many audio samples are used per second. Auto keeps a suitable setting": "選擇每秒的音訊採樣次數, 自動會保留合適的設定",
    "Choose the output audio file format": "選擇輸出的音訊檔案格式",
    "Set audio quality for MP3, M4A, or Opus. A higher bitrate usually produces a larger file": "設定 MP3、M4A 或 Opus 音質, 位元率越高通常檔案越大",
    "Choose the output subtitle file format": "選擇輸出的字幕檔案格式",
    "Convert Media": "影音與字幕轉檔",
    "Convert video, audio, or subtitle files to the format you need": "將影片、音訊或字幕轉換成需要的格式",
    "Use a manual FFmpeg bin directory": "手動設定 FFmpeg bin 資料夾",
    "Directory containing ffmpeg and ffprobe": "包含 ffmpeg 與 ffprobe 的資料夾",
    "Use a manual JavaScript runtime bin directory": "手動設定 JavaScript runtime bin 資料夾",
    "Directory containing deno, node, qjs, or bun": "包含 deno、node、qjs 或 bun 的資料夾",
    "Apply Tool Paths": "套用工具路徑",
    "Reset Dependency Reminders": "重設依賴提醒",
    "Application Updates": "應用程式更新",
    "Update Notifications and Downloads": "更新通知與下載",
    "Current Version: {version}": "目前版本: {version}",
    "Automatically check for updates": "自動檢查更新",
    "Check for Updates": "立即檢查更新",
    "Ready to check for updates": "可以檢查更新",
    "Update service is not configured": "尚未設定更新服務",
    "Checking for updates...": "正在檢查更新...",
    "Application is up to date": "目前已是最新版",
    "No stable release is available yet": "目前尚無正式版本",
    "This test build is newer than the latest stable release": "目前測試版比最新正式版更新",
    "Update available: {version}": "有可用更新: {version}",
    "Update check failed: {error}": "更新檢查失敗: {error}",
    "Update Check Failed": "更新檢查失敗",
    "Update Available": "有可用更新",
    "Version {version} is available": "版本 {version} 已可下載",
    "No release notes were provided": "這個版本沒有提供 release notes",
    "A verified update asset is unavailable. Use the Release page to download manually.": "找不到可驗證的更新檔, 請改從 Release 頁面手動下載",
    "Download Update File": "下載更新檔",
    "Open Release Page": "開啟 Release 頁面",
    "Remind Me Later": "稍後提醒",
    "Downloading version {version}...": "正在下載版本 {version}...",
    "Downloading update file...": "正在下載更新檔...",
    "Update downloaded: {version}": "更新已下載: {version}",
    "Update File Downloaded": "更新檔已下載",
    "The update file was downloaded and verified.": "更新檔已下載並通過驗證。",
    "Open Download Folder": "開啟下載資料夾",
    "Later": "稍後處理",
    "Close MochiStar, extract the downloaded ZIP file, then replace the old MochiStar folder with the extracted folder. Your settings and queue are stored separately.": "請關閉 MochiStar、解壓縮下載的 ZIP，然後用解壓後的 MochiStar 資料夾取代舊版。設定與列隊資料存放在其他位置，不會被清除。",
    "Close MochiStar, open the downloaded DMG, then drag MochiStar.app to Applications and replace the old version. If macOS blocks it, use Open Anyway in Privacy & Security.": "請關閉 MochiStar、開啟下載的 DMG，然後將 MochiStar.app 拖入「應用程式」並取代舊版。若 macOS 阻擋開啟，請到「隱私權與安全性」選擇「仍要打開」。",
    "Close MochiStar, extract the downloaded archive, then replace the old MochiStar folder. The archive preserves the executable permission.": "請關閉 MochiStar、解壓縮下載的封存檔，然後取代舊的 MochiStar 資料夾。封存檔會保留執行權限。",
    "Update download failed: {error}": "更新下載失敗: {error}",
    "Update File Download Failed": "更新檔下載失敗",
    "Update download cancelled": "已取消更新下載",
    "Open Application Data Folder": "開啟應用程式資料夾",
    "Restore Factory Settings": "恢復原廠設定",
    "Minimize": "最小化",
    "Maximize": "最大化",
    "Restore": "還原",
    "Close": "關閉",
    "Appearance": "外觀",
    "Theme": "主題",
    "Use integrated title bar after restart (Experimental)": "下次啟動時使用整合式標題列 (實驗性)",
    "Replaces the system frame with themed window controls the next time the application starts.": "下次啟動程式時, 使用跟隨主題的視窗控制按鈕取代系統外框.",
    "External Tools": "外部工具",
    "Local command-line programs required for downloading and conversion; these are dependencies, not plugins": "下載與轉檔所需的本機命令列程式, 屬於執行依賴而不是外掛",
    "Change appearance, updates, and how the application works": "調整外觀、更新與程式運作設定",
    "Only Errors": "只顯示錯誤",
    "Application Log": "應用程式紀錄",
    "Review application activity and find errors when something goes wrong": "查看程式執行情況，發生問題時可在這裡尋找錯誤",
    "Ready": "就緒",
    "Active: {active}  Pending: {pending}": "執行中: {active}  等待中: {pending}",
    "Pending: {pending}  Completed: {completed}": "等待中: {pending}  已完成: {completed}",
    "Workers: {workers}": "同時執行數: {workers}",
    "Pending: {pending}  Completed: {completed}  Failed: {failed}": "等待: {pending}  完成: {completed}  錯誤: {failed}",
    "Available": "可用",
    "Unavailable": "無法使用",
    "Start pending queue tasks": "開始執行等待中的列隊任務",
    "Pause new task dispatch. Running tasks will finish normally; use Cancel on selected tasks to stop them immediately": "暫停分派新任務，執行中的任務會正常完成；若要立即停止，請選取任務後按取消",
    "Stop selected running or pending tasks": "停止選取的執行中或等待中任務",
    "Modern Dark": "星夜",
    "Analysis Failed": "分析失敗",
    "Subtitle Analysis Failed": "字幕分析失敗",
    "Analysis failed. Try again or check the Application Log.": "分析失敗, 請再試一次或查看應用程式紀錄",
    "Invalid Cookie File": "無效的 Cookie 檔案",
    "Choose an existing Netscape cookies.txt file": "請選擇現有的 Netscape cookies.txt 檔案",
    "No Playlist Items": "未選擇播放清單項目",
    "Select at least one playlist item": "請至少選擇一個播放清單項目",
    "No accessible playlist items were found": "播放清單中沒有可存取的項目",
    "FFmpeg Required": "需要 FFmpeg",
    "This download preset requires FFmpeg. Configure it in Settings or PATH": "此下載預設需要 FFmpeg，請在設定中指定路徑或加入 PATH",
    "Choose Media Files": "選擇影音或字幕檔案",
    "ffprobe is unavailable. Configure FFmpeg in Settings or PATH": "ffprobe 無法使用，請在設定中指定 FFmpeg 路徑或加入 PATH",
    "The input does not contain a video stream": "輸入檔案不包含影片串流",
    "The input does not contain an audio stream": "輸入檔案不包含音訊串流",
    "The input does not contain a subtitle stream": "輸入檔案不包含字幕串流",
    "Some files are incompatible with the selected output:": "部分檔案不符合選擇的輸出類型:",
    "Input file does not exist: {path}": "輸入檔案不存在: {path}",
    "Input file does not exist:\n{path}": "輸入檔案不存在:\n{path}",
    "Missing Input": "找不到輸入檔案",
    "Choose Output Folder": "選擇輸出資料夾",
    "Choose Netscape Cookie File": "選擇 Netscape Cookie 檔案",
    "Text files (*.txt);;All files (*)": "文字檔案 (*.txt);;所有檔案 (*)",
    "Choose Tool Bin Directory": "選擇工具 Bin 資料夾",
    "Invalid FFmpeg Directory": "FFmpeg 資料夾無效",
    "The selected directory does not contain: {missing}": "選取的資料夾不包含: {missing}",
    "Invalid JavaScript Runtime Directory": "JavaScript runtime 資料夾無效",
    "The selected directory does not contain a supported deno, node, qjs, or bun version": "選取的資料夾不包含版本受支援的 deno、node、qjs 或 bun",
    "Unable to Apply Theme": "無法套用主題",
    "Unable to Open Application Data Folder": "無法開啟應用程式資料夾",
    "Unable to Open Output Folder": "無法開啟輸出資料夾",
    "Restore Factory Settings?": "要恢復原廠設定嗎?",
    "All application preferences will be reset. Queue tasks and downloaded files will not be deleted.": "所有應用程式偏好都會重設, 但不會刪除列隊任務與已下載檔案",
    "Output Folder Required": "尚未選擇輸出資料夾",
    "Invalid Output Folder": "無效的輸出資料夾",
    "Conversion disabled: {missing} is unavailable": "轉檔功能已停用，{missing} 無法使用",
    "FFmpeg: {value}": "FFmpeg: {value}",
    "JavaScript runtime: {value}": "JavaScript runtime: {value}",
    "No supported JavaScript runtime was found; some websites may fail JavaScript challenges": "找不到支援的 JavaScript runtime，部分網站可能無法通過 JavaScript challenge",
    "External Dependencies Required": "需要外部依賴",
    "Command Copied - External Dependencies Required": "已複製命令 - 需要外部依賴",
    "The following dependencies were not detected and may affect the full experience:": "偵測到系統尚未安裝以下依賴, 可能影響完整體驗:",
    "FFmpeg and FFprobe": "FFmpeg 與 FFprobe",
    "Media conversion, file analysis, and some downloads are unavailable": "影音轉檔、檔案分析與部分下載功能目前無法使用",
    "JavaScript runtime": "JavaScript runtime",
    "Some websites may fail JavaScript challenges. Deno is recommended": "部分網站可能無法通過 JavaScript challenge. 建議安裝 Deno",
    "Do not remind me about these items again": "不要再提醒這些項目",
    "Copy Command": "複製命令",
    "This command uses sudo and may ask for an administrator password": "此命令使用 sudo, 可能會要求輸入管理員密碼",
    "No supported package manager was found. Follow the official instructions": "找不到支援的套件管理器, 請依官方說明安裝",
    "Open Official Installation Guide": "開啟官方安裝說明",
    "Open Homebrew Website": "開啟 Homebrew 網站",
    "Command copied. Run it in Terminal, then restart MochiStar": "已複製命令. 請在 Terminal 執行後重新啟動 MochiStar",
    "{name}: not found": "{name}: 找不到執行檔",
    "{name}: version {version} is unsupported; required {minimum}": "{name}: 不支援版本 {version}, 需要 {minimum}",
    "{name}: unable to run or read version": "{name}: 無法執行或讀取版本",
    "Unknown": "未知",
    "Added {count} task(s)": "已加入 {count} 個任務",
    "Concurrency set to {count}": "同時執行數已設為 {count}",
    "Queue paused": "列隊已暫停",
    "Cancelling selected task(s)": "正在取消選取的任務",
    "Selected task(s) ready": "選取的任務已可執行",
    "Removed selected task(s)": "已移除選取的任務",
    "Running tasks": "正在執行任務",
    "Queue idle": "列隊閒置",
    "Processing {title}": "正在處理 {title}",
    "Download completed": "下載完成",
    "Subtitle download completed": "字幕下載完成",
    "Conversion completed": "轉檔完成",
    "Duration unavailable": "無法取得片長",
    "YouTube blocked this request. Configure browser or Cookie file authentication in Settings, then retry.": "YouTube 已擋下這個下載請求. 請在設定中使用瀏覽器或 Cookie 檔案驗證後重試",
}
_REVERSE_ZH_TW = {value: key for key, value in _ZH_TW.items()}
_SOURCE_ROLE = int(Qt.ItemDataRole.UserRole) + 100


def set_language(language: str) -> None:
    """設定目前 UI 語言"""
    global _current_language
    _current_language = language if language in {"en", "zh_TW"} else "zh_TW"


def current_language() -> str:
    """取得目前 UI 語言"""
    return _current_language


def system_ui_font() -> QFont:
    """選擇各系統已安裝且支援繁中的 UI font"""
    base_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    preferred = (
        ("Microsoft JhengHei UI", "Microsoft JhengHei", "Segoe UI")
        if sys.platform == "win32"
        else ("PingFang TC", "Heiti TC", ".AppleSystemUIFont")
        if sys.platform == "darwin"
        else ("Noto Sans CJK TC", "Noto Sans CJK", "WenQuanYi Zen Hei", "DejaVu Sans")
    )
    installed = {family.casefold(): family for family in QFontDatabase.families()}
    family = next((installed[name.casefold()] for name in preferred if name.casefold() in installed), "")
    if not family: return base_font
    font = QFont(base_font)
    font.setFamily(family)
    return font


def tr(source: str, **values: Any) -> str:
    """翻譯固定 UI 文字並代入格式值"""
    text = _ZH_TW.get(source, source) if _current_language == "zh_TW" else source
    return text.format(**values) if values else text


def translate_text(text: str) -> str:
    """將現有英文或中文固定文字切換到目前語言"""
    source = _REVERSE_ZH_TW.get(text, text)
    return _ZH_TW.get(source, source) if _current_language == "zh_TW" else source


def _source_text(text: str) -> str:
    """取得可供後續切換語言的英文 source"""
    return _REVERSE_ZH_TW.get(text, text)


def translate_widget_tree(root: QWidget) -> None:
    """立即翻譯 widget tree 內的固定 UI 文字"""
    widgets = [root, *root.findChildren(QWidget)]
    translated_models: set[int] = set()
    for widget in widgets:
        if widget.windowTitle():
            source = widget.property("i18nWindowTitle") or _source_text(widget.windowTitle())
            widget.setProperty("i18nWindowTitle", source)
            widget.setWindowTitle(tr(source))
        dynamic = widget.property("i18nDynamic") is True
        if widget.toolTip() and not dynamic:
            source = widget.property("i18nToolTip") or _source_text(widget.toolTip())
            widget.setProperty("i18nToolTip", source)
            widget.setToolTip(tr(source))
        if isinstance(widget, QAbstractButton) and not dynamic:
            source = widget.property("i18nText") or _source_text(widget.text())
            widget.setProperty("i18nText", source)
            widget.setText(tr(source))
        if isinstance(widget, QLabel) and not dynamic:
            source = widget.property("i18nText") or _source_text(widget.text())
            widget.setProperty("i18nText", source)
            widget.setText(tr(source))
        if isinstance(widget, QGroupBox):
            source = widget.property("i18nTitle") or _source_text(widget.title())
            widget.setProperty("i18nTitle", source)
            widget.setTitle(tr(source))
        if isinstance(widget, QLineEdit):
            source = widget.property("i18nPlaceholder") or _source_text(widget.placeholderText())
            widget.setProperty("i18nPlaceholder", source)
            widget.setPlaceholderText(tr(source))
        if isinstance(widget, QComboBox) and not dynamic:
            for index in range(widget.count()):
                source = widget.itemData(index, _SOURCE_ROLE) or _source_text(widget.itemText(index))
                widget.setItemData(index, source, _SOURCE_ROLE)
                widget.setItemText(index, tr(source))
        if isinstance(widget, QTableWidget):
            for column in range(widget.columnCount()):
                item = widget.horizontalHeaderItem(column)
                if item:
                    source = item.data(_SOURCE_ROLE) or _source_text(item.text())
                    item.setData(_SOURCE_ROLE, source)
                    item.setText(tr(source))
            for row in range(widget.rowCount()):
                for column in range(widget.columnCount()):
                    item = widget.item(row, column)
                    if item:
                        source = item.data(_SOURCE_ROLE) or _source_text(item.text())
                        item.setData(_SOURCE_ROLE, source)
                        item.setText(tr(source))
        model_value = getattr(widget, "model", None)
        model = model_value() if callable(model_value) else model_value
        if model is not None and id(model) not in translated_models and hasattr(model, "retranslate"):
            translated_models.add(id(model))
            model.retranslate()
