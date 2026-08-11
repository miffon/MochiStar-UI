import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QItemSelectionModel, QMimeData, QPoint, QUrl, Qt
from PySide6.QtWidgets import (
    QFormLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
)

from models import (
    ConversionPreset,
    DownloadOptions,
    FormatInfo,
    MediaInfo,
    SubtitleOptions,
    SubtitleSelection,
    SubtitleTrack,
    TaskKind,
    TaskRecord,
    TaskStatus,
)
from i18n import set_language, tr, translate_widget_tree
from panels import (
    AnalyzePanel,
    BottomStatusBar,
    ConversionPanel,
    FileAnalysisPanel,
    FileDropListWidget,
    LogPanel,
    NoWheelComboBox,
    QueuePanel,
    ReplacementPanel,
    RoundedProgressBar,
    SettingsPanel,
    SubtitlePanel,
    TaskTableModel,
)
from storage import Settings
from theme import load_theme_stylesheet, theme_color

TEST_PATH = Path("test-data")


@pytest.fixture(autouse=True)
def english_ui():
    set_language("en")


def make_media(media_id: str = "video-1", title: str = "Example") -> MediaInfo:
    return MediaInfo(
        media_id=media_id,
        title=title,
        uploader="Uploader",
        duration=125,
        site="YouTube",
        webpage_url=f"https://example.test/{media_id}",
        thumbnail="https://example.test/thumb.jpg",
        formats=[
            FormatInfo("137", "mp4", "1080p", 30, "avc1", "none", 10_000_000, 1920, 1080),
            FormatInfo("140", "m4a", "audio only", None, "none", "mp4a", 1_000_000, None, None),
        ],
        entries=[],
    )


def make_task(task_id: str = "task-1", progress: float | None = 0.25) -> TaskRecord:
    now = datetime.now(UTC)
    return TaskRecord(
        id=task_id,
        kind=TaskKind.DOWNLOAD,
        title="Example",
        status=TaskStatus.PENDING,
        progress=progress,
        output_path="C:/Downloads/example.mp4",
        error="",
        created_at=now,
        updated_at=now,
        download_options=DownloadOptions(url="https://example.test/video"),
    )


def test_queue_error_translates_youtube_block_summary(app) -> None:
    task = make_task()
    task.error = "YouTube blocked this request. Configure browser or Cookie file authentication in Settings, then retry."
    model = TaskTableModel([task])
    set_language("zh_TW")

    index = model.index(0, 6)

    assert model.data(index) == "YouTube 已擋下這個下載請求. 請在設定中使用瀏覽器或 Cookie 檔案驗證後重試"
    assert model.data(index, Qt.ItemDataRole.ToolTipRole) == model.data(index)


def test_analyze_panel_emits_payload_and_displays_media(app):
    panel = AnalyzePanel()
    assert all(
        layout.fieldGrowthPolicy() == QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        for layout in panel.findChildren(QFormLayout)
    )
    assert panel.metadata_text.text() == ""
    assert panel.thumbnail_label.text() == ""
    analyze_width = panel.analyze_button.minimumWidth()
    cancelled = []
    panel.cancel_analysis_requested.connect(lambda: cancelled.append(True))
    panel.set_analyzing(True)
    assert panel.analyze_button.text() == "Analyzing..."
    assert not panel.analyze_button.isEnabled()
    assert not panel.cancel_analysis_button.isHidden()
    assert panel.analyze_button.minimumWidth() == analyze_width
    panel.set_analysis_progress(1, 300)
    assert panel.analysis_progress_label.text() == "1/300"
    panel.cancel_analysis_button.click()
    assert cancelled == [True]
    panel.set_analyzing(False)
    assert panel.cancel_analysis_button.isHidden()
    assert panel.analysis_progress_label.text() == ""
    assert panel.analyze_button.property("role") == "default"
    assert panel.add_button.property("role") == "primary"

    requested = []
    panel.analyze_requested.connect(requested.append)
    panel.url_edit.setText("https://example.test/video")
    panel.analyze_button.click()
    assert requested[0]["url"] == "https://example.test/video"
    assert requested[0]["cookie"]["source"] == "none"

    panel.set_media(make_media())
    assert panel.metadata_text.text() == (
        "Title: Example\n"
        "Uploader: Uploader\n"
        "Duration: 2:05\n"
        "Site: YouTube\n"
        "URL: https://example.test/video-1"
    )
    assert panel.video_format_combo.findData("137") >= 0
    assert panel.audio_format_combo.findData("140") >= 0
    assert panel.container_combo.findData("mov") >= 0
    assert panel.video_format_combo.itemText(1) == "137\tMP4\t1080p\t30 FPS\tV: avc1\tA: none\t9.5 MiB"
    assert panel.audio_format_combo.itemText(1) == "140\tM4A\taudio only\t-\tV: none\tA: mp4a\t976.6 KiB"
    assert panel.add_button.isEnabled()
    assert panel.layout().itemAt(0).layout().itemAtPosition(0, 1).widget() is panel.add_button
    assert panel.metadata_group.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Expanding
    assert panel.advanced_group.layout().count() == 1


def test_subtitle_panel_fields_expand_across_available_width(app):
    panel = SubtitlePanel()
    forms = panel.findChildren(QFormLayout)

    assert forms
    assert all(
        layout.fieldGrowthPolicy() == QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow for layout in forms
    )


def test_advanced_format_summary_limits_decimal_places():
    fractional = FormatInfo("303", "webm", "1080p", 29.970029, "vp9", "none", 12_345_678)
    precise = FormatInfo("399", "mp4", "2160p", 23.976, "av01", "none", 12_345_678)
    assert AnalyzePanel._format_option_text(fractional) == (
        "303\tWEBM\t1080p\t29.97 FPS\tV: vp9\tA: none\t11.8 MiB"
    )
    assert AnalyzePanel._format_option_text(precise) == (
        "399\tMP4\t2160p\t23.976 FPS\tV: av01\tA: none\t11.8 MiB"
    )


def test_advanced_formats_sort_by_estimated_size_descending(app):
    panel = AnalyzePanel()
    media = make_media()
    media.formats = [
        FormatInfo("small", "mp4", "720p", 30, "avc1", "none", 1_000),
        FormatInfo("unknown", "mp4", "1080p", 30, "avc1", "none", None),
        FormatInfo("large", "mp4", "2160p", 30, "av01", "none", 10_000),
    ]
    panel.set_media(media)
    assert [panel.video_format_combo.itemData(index) for index in range(1, 4)] == ["large", "small", "unknown"]


def test_analyze_panel_playlist_checkboxes(app):
    panel = AnalyzePanel()
    playlist = make_media("playlist", "Playlist")
    playlist.entries = [make_media("one", "One"), make_media("two", "Two")]
    panel.set_media(playlist)
    assert panel.playlist_table.rowCount() == 2
    assert panel.selected_entry_ids() == ["one", "two"]
    panel.set_all_entries_checked(False)
    assert panel.selected_entry_ids() == []


def test_analysis_tables_sort_without_losing_row_controls(app):
    analyze = AnalyzePanel()
    playlist = make_media("playlist", "Playlist")
    first, second = make_media("one", "Zulu"), make_media("two", "Alpha")
    first.duration, second.duration = 600, 120
    playlist.entries = [first, second]
    analyze.set_media(playlist)
    analyze.playlist_table.sortItems(2, Qt.SortOrder.AscendingOrder)
    assert analyze.playlist_table.item(0, 1).text() == "Alpha"
    assert analyze.selected_entry_ids() == ["two", "one"]
    analyze.playlist_table.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
    assert [analyze.playlist_table.item(row, 1).text() for row in range(2)] == ["Zulu", "Alpha"]

    subtitles = SubtitlePanel()
    media = make_media()
    media.subtitles = [
        SubtitleTrack("en", "English", "manual", ["vtt"]),
        SubtitleTrack("zh-Hant", "Chinese", "automatic", ["vtt", "srt"]),
    ]
    subtitles.set_media(media)
    subtitles.subtitle_table.sortItems(2, Qt.SortOrder.DescendingOrder)
    assert subtitles.subtitle_table.item(0, 2).text() == "zh-Hant"
    assert subtitles.subtitle_table.cellWidget(0, 5).findData("srt") >= 0
    assert subtitles.subtitle_table.rowHeight(0) >= subtitles.subtitle_table.cellWidget(0, 5).sizeHint().height() + 4
    subtitles.subtitle_table.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
    assert [subtitles.subtitle_table.item(row, 2).text() for row in range(2)] == ["en", "zh-Hant"]
    assert subtitles.subtitle_table.cellWidget(1, 5).findData("srt") >= 0


def test_tables_use_interactive_columns_without_horizontal_scrolling(app):
    analyze, subtitles, queue = AnalyzePanel(), SubtitlePanel(), QueuePanel()
    playlist = make_media("playlist", "Playlist")
    playlist.entries = [make_media("one", "One"), make_media("two", "Two")]
    analyze.set_media(playlist)
    media = make_media()
    media.subtitles = [SubtitleTrack("en", "English", "manual", ["vtt"])]
    subtitles.set_media(media)
    format_combo = subtitles.subtitle_table.cellWidget(0, 5)
    assert format_combo.property("role") == "tableCell"

    for panel, table in (
        (analyze, analyze.playlist_table), (subtitles, subtitles.subtitle_table), (queue, queue.table),
    ):
        header = table.horizontalHeader()
        assert header.isSortIndicatorClearable()
        assert all(
            header.sectionResizeMode(column) == QHeaderView.ResizeMode.Interactive
            for column in range(header.count())
        )
        assert header.defaultAlignment() == Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        assert not header.stretchLastSection()
        assert table.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        panel.resize(720, 640)
        panel.show()
        app.processEvents()
        table._width_controller.fit()
        assert header.length() <= table.viewport().width()
        assert header.sectionSize(0) == header.minimumSectionSize()
        regular_width = header.sectionSize(2)
        assert header.sectionSize(1) == pytest.approx(regular_width * 1.5, abs=2)
        original_title, original_right = header.sectionSize(1), header.sectionSize(2)
        header.resizeSection(1, original_title + 30)
        assert header.sectionSize(1) == original_title + 30
        assert header.sectionSize(2) == original_right - 30
        title_width = header.sectionSize(1)
        other_widths = [header.sectionSize(column) for column in range(header.count()) if column != 1]
        panel.resize(max(panel.width(), table.width()) + 200, 640)
        app.processEvents()
        assert header.length() <= table.viewport().width()
        assert header.sectionSize(1) > title_width
        assert [header.sectionSize(column) for column in range(header.count()) if column != 1] == other_widths
        panel.close()


def test_table_drag_cascades_only_into_right_columns_and_stops_at_minimum(app):
    panel = AnalyzePanel()
    playlist = make_media("playlist", "Playlist")
    playlist.entries = [make_media("one", "One")]
    panel.set_media(playlist)
    panel.resize(720, 640)
    panel.show()
    app.processEvents()
    try:
        header = panel.playlist_table.horizontalHeader()
        panel.playlist_table._width_controller.fit()
        minimum = header.minimumSectionSize()
        left_width = header.sectionSize(0)
        title_width = header.sectionSize(1)
        right_widths = [header.sectionSize(column) for column in range(2, header.count())]
        first_capacity = right_widths[0] - minimum

        header.resizeSection(1, title_width + first_capacity + 10)

        assert header.sectionSize(0) == left_width
        assert header.sectionSize(2) == minimum
        assert header.sectionSize(3) == right_widths[1] - 10

        remaining_capacity = sum(
            header.sectionSize(column) - minimum for column in range(2, header.count())
        )
        current_title = header.sectionSize(1)
        header.resizeSection(1, current_title + remaining_capacity + 50)

        assert all(header.sectionSize(column) == minimum for column in range(2, header.count()))
        assert header.sectionSize(1) == current_title + remaining_capacity
        assert header.sectionSize(0) == left_width
        assert header.length() <= panel.playlist_table.viewport().width()
    finally:
        panel.close()


def test_analysis_tables_sort_by_checked_state(app):
    analyze = AnalyzePanel()
    playlist = make_media("playlist", "Playlist")
    playlist.entries = [make_media("one", "One"), make_media("two", "Two"), make_media("three", "Three")]
    analyze.set_media(playlist)
    analyze.playlist_table.item(1, 0).setCheckState(Qt.CheckState.Unchecked)

    analyze.playlist_table.sortItems(0, Qt.SortOrder.AscendingOrder)
    assert analyze.playlist_table.item(0, 0).checkState() == Qt.CheckState.Unchecked
    analyze.playlist_table.sortItems(0, Qt.SortOrder.DescendingOrder)
    assert analyze.playlist_table.item(0, 0).checkState() == Qt.CheckState.Checked

    analyze.set_all_entries_checked(False)
    assert analyze.selected_entry_ids() == []
    analyze.set_all_entries_checked(True)
    assert set(analyze.selected_entry_ids()) == {"one", "two", "three"}

    subtitles = SubtitlePanel()
    media = make_media()
    media.subtitles = [
        SubtitleTrack("en", "English", "manual", ["vtt"]),
        SubtitleTrack("zh-Hant", "Chinese", "automatic", ["vtt"]),
    ]
    subtitles.set_media(media)
    subtitles.subtitle_table.item(1, 0).setCheckState(Qt.CheckState.Checked)
    subtitles.subtitle_table.sortItems(0, Qt.SortOrder.DescendingOrder)
    assert subtitles.subtitle_table.item(0, 0).checkState() == Qt.CheckState.Checked
    subtitles.set_all_checked(True)
    assert len(subtitles.selected_tracks()) == 2


def test_subtitle_panel_lists_tracks_and_emits_per_track_formats(app):
    panel = SubtitlePanel()
    requested = []
    panel.analyze_requested.connect(requested.append)
    panel.url_edit.setText("https://example.test/video")
    panel.include_automatic_checkbox.setChecked(False)
    panel.analyze_button.click()
    assert requested[-1]["include_automatic_subtitles"] is False
    panel.set_analyzing(True)
    assert not panel.include_automatic_checkbox.isChecked()
    assert panel.include_automatic_checkbox.isEnabled()
    panel.include_automatic_checkbox.setChecked(True)
    assert len(requested) == 1
    panel.set_analyzing(False)
    assert panel.include_automatic_checkbox.isChecked()
    panel.analyze_button.click()
    assert requested[-1]["include_automatic_subtitles"] is True
    media = make_media()
    media.subtitles = [
        SubtitleTrack("en", "English", "manual", ["vtt", "srt"]),
        SubtitleTrack("zh-Hant", "Chinese", "automatic", ["vtt"]),
    ]
    panel.set_media(media)

    assert panel.subtitle_table.rowCount() == 2
    assert panel.selected_tracks() == []
    assert not panel.add_button.isEnabled()
    first_formats = panel.subtitle_table.cellWidget(0, 5)
    assert [first_formats.itemData(index) for index in range(first_formats.count())] == ["best", "vtt", "srt"]
    first_formats.setCurrentIndex(first_formats.findData("srt"))
    panel.subtitle_table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    panel.output_directory_edit.setText("C:/Downloads")
    assert panel.add_button.isEnabled()
    assert panel.selected_tracks() == [{
        "media_id": "video-1",
        "title": "Example",
        "url": "https://example.test/video-1",
        "language": "en",
        "source": "manual",
        "format": "srt",
    }]


def test_settings_panel_manual_paths_are_locked_by_default(app):
    panel = SettingsPanel()
    panel.set_settings(Settings())
    assert panel.theme_combo.currentData() == "cute_light"
    assert panel.theme_combo.itemText(panel.theme_combo.findData("starlit_night")) == "Starlit Night"
    assert not panel.custom_title_bar_checkbox.isChecked()
    assert panel.settings_scroll.widgetResizable()
    assert panel.updates_group.isAncestorOf(panel.open_app_data_button)
    assert panel.updates_group.isAncestorOf(panel.factory_reset_button)
    assert not panel.ffmpeg_directory_edit.isEnabled()
    assert not panel.js_directory_edit.isEnabled()
    assert not panel.reset_dependency_reminders_button.isEnabled()
    assert panel.theme_combo.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding
    assert panel.language_combo.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding
    panel.set_dependency_reminders_ignored(True)
    assert panel.reset_dependency_reminders_button.isEnabled()
    panel.manual_ffmpeg_checkbox.setChecked(True)
    panel.manual_js_checkbox.setChecked(True)
    assert panel.ffmpeg_directory_edit.isEnabled()
    assert panel.js_directory_edit.isEnabled()
    assert panel.theme_combo.findData("cute_light") >= 0


def test_settings_panel_emits_experimental_title_bar_changes(app):
    panel = SettingsPanel()
    changes = []
    panel.custom_title_bar_changed.connect(changes.append)
    panel.set_settings(Settings(experimental_custom_title_bar=True))
    assert panel.custom_title_bar_checkbox.isChecked()
    assert changes == []
    panel.custom_title_bar_checkbox.setChecked(False)
    assert changes == [False]


def test_settings_panel_shows_compact_tool_status_with_paths_in_tooltips(app):
    panel = SettingsPanel()
    panel.set_tool_status(
        "FFmpeg: OK", "JavaScript runtime: OK",
        "FFmpeg: C:/tools/ffmpeg.exe\nFFprobe: C:/tools/ffprobe.exe", "node: C:/tools/node.exe",
    )
    assert panel.ffmpeg_status_label.text() == "FFmpeg: OK"
    assert panel.ffmpeg_status_label.toolTip().endswith("FFprobe: C:/tools/ffprobe.exe")
    assert panel.js_status_label.text() == "JavaScript runtime: OK"
    assert panel.js_status_label.toolTip() == "node: C:/tools/node.exe"
    assert "not plugins" in panel.tools_group.toolTip()


def test_settings_panel_switches_between_english_and_traditional_chinese(app):
    panel = SettingsPanel()
    set_language("zh_TW")
    translate_widget_tree(panel)
    assert panel.apply_button.text() == "套用工具路徑"
    assert panel.updates_group.title() == "更新通知與下載"
    assert panel.check_updates_button.text() == "立即檢查更新"
    assert panel.auto_check_updates_checkbox.text() == "自動檢查更新"
    assert panel.custom_title_bar_checkbox.text() == "下次啟動時使用整合式標題列 (實驗性)"
    assert panel.open_app_data_button.text() == "開啟應用程式資料夾"
    assert panel.factory_reset_button.text() == "恢復原廠設定"
    assert panel.reset_dependency_reminders_button.text() == "重設依賴提醒"
    assert panel.manual_ffmpeg_checkbox.text() == "手動設定 FFmpeg bin 資料夾"
    assert "不是外掛" in panel.tools_group.toolTip()
    set_language("en")
    translate_widget_tree(panel)
    assert panel.apply_button.text() == "Apply Tool Paths"
    assert panel.updates_group.title() == "Update Notifications and Downloads"


def test_subtitle_analyze_button_uses_current_language(app):
    set_language("zh_TW")
    panel = SubtitlePanel()
    assert panel.analyze_button.text() == "分析"
    panel.set_analyzing(True)
    assert panel.analyze_button.text() == "分析中..."
    assert panel.cancel_analysis_button.text() == "取消"
    assert not panel.cancel_analysis_button.isHidden()


def test_traditional_chinese_uses_taiwan_queue_and_ui_terms():
    set_language("zh_TW")
    assert tr("Queue") == "列隊"
    assert tr("Add to Queue") == "加入列隊"
    assert tr("Duration") == "片長"
    assert tr("Browser Profile") == "瀏覽器設定檔"
    assert tr("Workers") == "同時執行數"
    assert tr("Sel.") == "選取"


def test_selection_table_headers_use_short_english_label(app) -> None:
    set_language("en")
    analyze, subtitles = AnalyzePanel(), SubtitlePanel()

    assert analyze.playlist_table.horizontalHeaderItem(0).text() == "Sel."
    assert subtitles.subtitle_table.horizontalHeaderItem(0).text() == "Sel."


def test_replacement_panel_builds_single_source_payload_and_persists_no_paths(app, sample_media) -> None:
    panel = ReplacementPanel()
    visual, audio = sample_media("picture.png", "music.wav")
    panel.output_directory_edit.setText(str(visual.parent))
    panel.visual_card.set_path(str(visual))
    panel.audio_card.set_path(str(audio))
    panel.set_source_probe(str(visual), {
        "duration": None, "streams": [{"codec_type": "video", "codec_name": "png", "width": 800, "height": 600}],
    })
    panel.set_source_probe(str(audio), {
        "duration": 12.0, "streams": [{"codec_type": "audio", "codec_name": "pcm_s16le"}],
    })
    panel.duration_mode_combo.setCurrentIndex(panel.duration_mode_combo.findData("custom"))
    panel.custom_duration_edit.setText("00:00:10.500")
    panel.visual_card.delay_spin.setValue(0.25)
    panel.audio_card.delay_spin.setValue(-0.5)
    panel.trim_start_spin.setValue(1)
    panel.aspect_ratio_combo.setCurrentIndex(panel.aspect_ratio_combo.findData("9:16"))

    payload = panel.request_payload()
    saved = panel.persistent_settings()

    assert payload["visual_path"] == str(visual) and payload["audio_path"] == str(audio)
    assert payload["custom_duration"] == 10.5
    assert payload["visual_delay"] == 0.25 and payload["audio_delay"] == -0.5
    assert payload["trim_start"] == 1 and payload["aspect_ratio"] == "9:16"
    assert "visual_path" not in saved and "audio_path" not in saved
    assert panel.splitter.count() == 2
    assert isinstance(panel.splitter.widget(1), QScrollArea)
    assert panel.settings_scroll.widgetResizable()
    assert panel.advanced_stack.parentWidget() is panel._unused_advanced_widget
    assert not panel.option_labels["video_preset"].isHidden()
    assert not panel.save_preset_button.isHidden()


@pytest.mark.parametrize(
    "text,seconds",
    [("10.5", 10.5), ("2000", 2000), ("1:30", 90), ("1:1:30.3", 3690.3)],
)
def test_replacement_duration_parses_components_from_seconds_to_hours(text: str, seconds: float) -> None:
    assert ReplacementPanel.parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["", "1:", ":30", "1:60", "1:1:60", "1:2:3:4", "-1", "nan"])
def test_replacement_duration_rejects_incomplete_or_out_of_range_values(text: str) -> None:
    assert ReplacementPanel.parse_duration(text) is None


def test_replacement_duration_placeholder_follows_language(app) -> None:
    panel = ReplacementPanel()
    set_language("zh_TW")
    translate_widget_tree(panel)
    assert panel.custom_duration_edit.placeholderText() == "小時:分鐘:秒.毫秒"
    set_language("en")
    translate_widget_tree(panel)
    assert panel.custom_duration_edit.placeholderText() == "Hours:Minutes:Seconds.ms"


def test_queue_model_and_action_signals(app):
    panel = QueuePanel()
    assert panel.table.horizontalHeader().sortIndicatorSection() == -1
    assert panel.proxy_model.sortColumn() == -1
    task = make_task()
    panel.set_tasks([task])
    assert panel.model.index(0, 0).data() == "Download"
    assert panel.model.index(0, 2).data() == "https://example.test/video"
    assert panel.model.index(0, 2).data(Qt.ItemDataRole.ToolTipRole) == "https://example.test/video"
    assert panel.model.index(0, 4).data() == "25.0%"
    assert panel.model.headerData(0, Qt.Orientation.Horizontal, Qt.ItemDataRole.TextAlignmentRole) == int(
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
    )
    assert panel.model.index(0, 4).data(Qt.ItemDataRole.TextAlignmentRole) & Qt.AlignmentFlag.AlignLeft
    assert panel.model.index(0, 1).data(Qt.ItemDataRole.ToolTipRole) == "Example"
    assert all(
        panel.table.horizontalHeader().sectionResizeMode(section) == QHeaderView.ResizeMode.Interactive
        for section in range(panel.model.columnCount())
    )
    panel.table.horizontalHeader().resizeSection(0, 120)
    assert panel.table.horizontalHeader().sectionSize(0) == 120
    widths = [80, 210, 260, 100, 90, 230, 190]
    panel.set_column_widths(widths)
    assert panel.column_widths() == widths

    panel.table.selectRow(0)
    cancelled = []
    moved = []
    panel.cancel_requested.connect(cancelled.append)
    panel.move_requested.connect(lambda ids, direction: moved.append((ids, direction)))
    opened = []
    panel.open_output_requested.connect(opened.append)
    panel.cancel_button.click()
    panel.up_button.click()
    panel.table.doubleClicked.emit(panel.proxy_model.index(0, 1))
    assert cancelled == [["task-1"]]
    assert moved == [(["task-1"], -1)]
    assert Path(opened[-1]) == Path("C:/Downloads")

    workers = []
    panel.concurrency_changed.connect(workers.append)
    panel.concurrency_combo.setCurrentIndex(panel.concurrency_combo.findData(3))
    assert [panel.concurrency_combo.itemData(index) for index in range(panel.concurrency_combo.count())] == [1, 2, 3, 4]
    assert workers[-1] == 3
    default_buttons = (
        panel.cancel_button,
        panel.retry_button,
        panel.remove_button,
    )
    assert all(button.property("role") == "default" for button in default_buttons)
    assert panel.up_button.property("role") == "ghost"
    assert panel.down_button.property("role") == "ghost"

    first, second = make_task("first"), make_task("second")
    first.title, second.title = "Zulu", "Alpha"
    panel.set_tasks([first, second])
    panel.table.sortByColumn(1, Qt.SortOrder.AscendingOrder)
    assert panel.proxy_model.index(0, 1).data() == "Alpha"
    panel.table.selectRow(0)
    assert panel.selected_task_ids() == ["second"]
    panel.table.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
    assert [panel.proxy_model.index(row, 1).data() for row in range(2)] == ["Zulu", "Alpha"]

    conversion = make_task("conversion")
    conversion.kind = TaskKind.CONVERSION
    conversion.download_options = None
    panel.set_tasks([task, conversion])
    selection = panel.table.selectionModel()
    selection.select(panel.proxy_model.index(0, 0), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
    selection.select(panel.proxy_model.index(1, 0), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
    assert panel.model.index(1, 2).data() == ""

    subtitle = TaskRecord(
        kind=TaskKind.SUBTITLE,
        subtitle_options=SubtitleOptions(
            url="https://example.test/subtitles",
            selections=[SubtitleSelection("en")],
        ),
    )
    panel.set_tasks([subtitle])
    assert panel.model.index(0, 0).data() == "Subtitle"
    assert panel.model.index(0, 2).data() == "https://example.test/subtitles"


def test_queue_start_button_toggles_between_start_and_pause(app):
    panel = QueuePanel()
    started = []
    paused = []
    panel.start_requested.connect(lambda: started.append(True))
    panel.pause_requested.connect(lambda: paused.append(True))

    assert panel.start_button.isEnabled()
    assert panel.start_button.text() == "Pause Queue"
    assert panel.start_button.property("role") == "default"
    panel.set_dispatch_paused(True)
    assert panel.start_button.text() == "Start Queue"
    assert panel.start_button.property("role") == "primary"
    panel.start_button.click()
    panel.set_dispatch_paused(False)
    assert panel.start_button.text() == "Pause Queue"
    assert "Running tasks will finish normally" in panel.start_button.toolTip()
    assert "Cancel" in panel.start_button.toolTip()
    assert "selected" in panel.cancel_button.toolTip()
    panel.start_button.click()
    panel.set_dispatch_paused(True)
    assert panel.start_button.isEnabled()
    assert panel.start_button.text() == "Start Queue"
    assert started == [True]
    assert paused == [True]


def test_queue_page_summary_counts_pending_completed_and_failed(app):
    load_theme_stylesheet("starlit_night")
    panel = QueuePanel()
    tasks = [make_task(str(index)) for index in range(4)]
    tasks[1].status = TaskStatus.COMPLETED
    tasks[2].status = TaskStatus.COMPLETED
    tasks[3].status = TaskStatus.FAILED
    panel.set_tasks(tasks)
    assert panel.summary_label.text() == "Pending: 1  Completed: 2  Failed: 1"
    assert panel.model.data(panel.model.index(1, 3), Qt.ItemDataRole.ForegroundRole).name() == "#50c878"
    assert panel.model.data(panel.model.index(3, 3), Qt.ItemDataRole.ForegroundRole).name() == "#f36b7f"
    assert panel.model.data(panel.model.index(0, 3), Qt.ItemDataRole.ForegroundRole) is None
    panel.resize(900, 640)
    panel.show()
    app.processEvents()
    assert panel.summary_label.mapTo(panel, QPoint()).y() > panel.table.mapTo(panel, QPoint()).y()
    panel.close()
    set_language("zh_TW")
    panel.refresh_summary()
    assert panel.summary_label.text() == "等待: 1  完成: 2  錯誤: 1"


def test_combo_boxes_ignore_mouse_wheel(app):
    class FakeWheelEvent:
        ignored = False

        def ignore(self) -> None:
            self.ignored = True

    combo = NoWheelComboBox()
    combo.addItems(["One", "Two"])
    event = FakeWheelEvent()
    combo.wheelEvent(event)  # type: ignore[arg-type]
    assert event.ignored
    assert combo.currentIndex() == 0


def test_combo_popup_shows_all_items_without_covering_control(app):
    combo = NoWheelComboBox()
    combo.addItems(["First", "Second", "Third"])
    combo.resize(240, 30)
    combo.move(100, 100)
    combo.show()
    combo.showPopup()
    app.processEvents()
    app.processEvents()
    popup = combo.view().window()
    combo_rect = combo.geometry()
    popup_rect = popup.geometry()
    try:
        expected_height = sum(combo.view().sizeHintForRow(row) for row in range(combo.count()))
        assert expected_height <= popup.height() <= expected_height + 24
        assert not combo.view().verticalScrollBar().isVisible()
        intersection = popup_rect.intersected(combo_rect)
        assert not intersection.isValid() or intersection.height() <= 1
    finally:
        combo.hidePopup()
        combo.close()


def test_editable_combo_applies_native_font_alignment_compensation(app):
    previous_stylesheet = app.styleSheet()
    app.setStyleSheet(load_theme_stylesheet())
    try:
        line_edit = QLineEdit("B")
        combo = NoWheelComboBox()
        combo.addItem("B")
        editable_combo = NoWheelComboBox()
        editable_combo.setEditable(True)
        editable_combo.addItem("B")
        widgets = (line_edit, combo, editable_combo)
        for widget in widgets:
            widget.resize(300, 30)
            widget.show()
            widget.clearFocus()
        app.processEvents()

        # 使用相同文字掃描實際 glyph, 避免 Qt content rect 和繪製位置不一致
        text_starts = []
        for widget in widgets:
            image = widget.grab().toImage()
            bright_pixels = [
                x
                for y in range(5, image.height() - 5)
                for x in range(2, 120)
                if all(value > 120 for value in image.pixelColor(x, y).getRgb()[:3])
            ]
            text_starts.append(min(bright_pixels))
        assert text_starts[0] == text_starts[1]
        assert text_starts[2] == text_starts[0] - 2
    finally:
        for widget in widgets: widget.close()
        app.setStyleSheet(previous_stylesheet)


def test_conversion_panel_builds_request_and_emits_changes(app):
    panel = ConversionPanel()
    changes = []
    requests = []
    panel.files_changed.connect(changes.append)
    panel.add_requested.connect(requests.append)
    panel.add_files([TEST_PATH / "one.mov", TEST_PATH / "one.mov", TEST_PATH / "two.mp4"])
    panel.output_directory_edit.setText(str(TEST_PATH))
    panel.set_available_encoders(["h264_nvenc", "hevc_amf", "av1_qsv"])
    assert len(panel.file_paths()) == 2
    assert panel.target_format_combo.findData("mov") >= 0
    assert panel.encoder_combo.findData("nvidia") >= 0
    assert panel.encoder_combo.findData("amd") < 0
    assert panel.encoder_combo.findData("intel") < 0
    assert panel.splitter.orientation() == Qt.Orientation.Horizontal
    assert panel.add_button.isEnabled()
    assert panel.layout().itemAt(0).layout().itemAtPosition(0, 1).widget() is panel.add_button
    panel.add_button.click()
    assert requests[0]["input_paths"] == panel.file_paths()
    assert requests[0]["video_codec"] == "auto"
    assert requests[0]["resolution_height"] is None
    assert requests[0]["fps"] == "source"
    assert requests[0]["quality_mode"] == "vbr"
    assert requests[0]["quality_value"] == 7.5
    assert requests[0]["acceleration"] == "auto"
    assert requests[0]["pixel_format"] == "auto"
    assert not hasattr(panel, "pixel_format_combo")
    assert changes[-1] == panel.file_paths()


def test_conversion_file_list_accepts_only_local_file_drops(app, sample_media):
    first, second = sample_media("first.mp4", "second.wav")
    folder = first.parent / "folder"
    folder.mkdir(exist_ok=True)
    mime_data = QMimeData()
    mime_data.setUrls([
        QUrl.fromLocalFile(str(first)), QUrl("https://example.test/video.mp4"),
        QUrl.fromLocalFile(str(folder)), QUrl.fromLocalFile(str(first)), QUrl.fromLocalFile(str(second)),
    ])

    assert FileDropListWidget.local_file_paths(mime_data) == [str(first), str(second)]

    class DropEvent:
        def __init__(self): self.accepted = False
        def mimeData(self): return mime_data
        def acceptProposedAction(self): self.accepted = True
        def ignore(self): self.accepted = False

    panel = ConversionPanel()
    event = DropEvent()
    panel.files_list.dragEnterEvent(event)  # type: ignore[arg-type]
    assert event.accepted and panel.files_list.property("dragActive") is True
    panel.files_list.dropEvent(event)  # type: ignore[arg-type]
    assert event.accepted and panel.files_list.property("dragActive") is False
    assert panel.file_paths() == [str(first), str(second)]


def test_file_analysis_panel_manages_multiple_formatted_reports(app, sample_media):
    first, second = sample_media("clip.mp4", "song.flac")
    panel = FileAnalysisPanel()
    requests = []
    panel.analyze_requested.connect(requests.append)

    panel.analyze_files([first, second])

    assert panel.report_paths() == [str(first), str(second)]
    assert requests == [[str(first), str(second)]]
    assert panel.clear_button.isEnabled()
    metadata = {
        "format": {
            "format_long_name": "QuickTime / MOV", "duration": "65.2",
            "size": "10485760", "bit_rate": "7500000",
        },
        "streams": [
            {
                "codec_type": "video", "codec_long_name": "H.264", "width": 1920, "height": 1080,
                "avg_frame_rate": "30000/1001", "pix_fmt": "yuv420p", "bit_rate": "7000000",
            },
            {
                "codec_type": "audio", "codec_name": "aac", "sample_rate": "48000",
                "channels": 2, "channel_layout": "stereo", "bit_rate": "192000",
            },
        ],
    }
    panel.set_result(str(first), metadata)
    card = panel._cards[str(first)]
    gop_requests = []
    panel.gop_analysis_requested.connect(gop_requests.append)
    assert card.gop_button.isVisibleTo(panel) and card.gop_button.isEnabled()
    assert card.gop_button.toolTip() == "Scan video frames to measure the keyframe interval"
    assert card.remove_button.toolTip() == "Close Analysis Card"
    card.gop_button.click()
    assert gop_requests == [str(first)]
    assert not card.gop_button.isEnabled() and card.gop_button.text() == "Analyzing GOP..."
    panel.set_result(str(first), {
        **metadata,
        "gop_analysis": {
            "value": 60, "average": 70, "minimum": 60, "maximum": 90,
            "keyframes": 4, "frames_scanned": 240,
        },
    })
    report = card.report_label.text()
    assert "H.264" in report and "1920 x 1080" in report and "48" in report
    assert "7.500 Mbps" in report
    assert "60 frames" in report and "range 60-90" in report
    assert "padding-right: 16px" in report
    assert not card.gop_button.isEnabled() and card.gop_button.text() == "GOP Analyzed"

    set_language("zh_TW")
    panel.retranslate_reports()
    assert card.gop_button.toolTip() == "需要時掃描影片影格並計算關鍵影格間隔"
    assert card.remove_button.toolTip() == "關閉分析卡片"
    assert card.remove_button.accessibleName() == "關閉分析卡片"
    set_language("en")
    panel.retranslate_reports()

    panel.set_result(str(second), None, "Unsupported media data")
    assert "Analysis Failed" in panel._cards[str(second)].report_label.text()
    assert "Unsupported media data" in panel._cards[str(second)].report_label.text()

    panel.remove_report(str(first))
    assert panel.report_paths() == [str(second)]
    panel.clear_all()
    assert panel.report_paths() == []
    assert not panel.clear_button.isEnabled()


def test_file_analysis_cards_use_stable_numbers_and_keep_full_paths(app, sample_media):
    path, second = sample_media(f"{'very-long-file-name-' * 8}.mp4", "second.webm")
    panel = FileAnalysisPanel()
    panel.analyze_files([path, second])
    panel.set_result(str(path), {"format": {"format_name": "mp4"}, "streams": []})
    card = panel._cards[str(path)]

    assert card.title_label.text() == "#1"
    assert panel._cards[str(second)].title_label.text() == "#2"
    assert str(path) in card.report_label.text()
    assert panel._cards[str(second)].remove_button.property("controlType") == "close"

    panel.remove_report(str(path))

    assert panel._cards[str(second)].title_label.text() == "#1"


def test_conversion_selected_files_can_be_sent_for_analysis(app, sample_media):
    first, second = sample_media("first.mp4", "second.webm")
    panel = ConversionPanel()
    requested = []
    panel.analyze_selected_requested.connect(requested.append)
    file_buttons = panel.file_actions.layout()
    assert [file_buttons.itemAt(index).widget() for index in range(4)] == [
        panel.browse_files_button, panel.remove_files_button, panel.clear_files_button, panel.analyze_files_button,
    ]
    panel.add_files([first, second])
    panel.files_list.item(1).setSelected(True)

    assert panel.analyze_files_button.isEnabled()
    set_language("zh_TW")
    translate_widget_tree(panel)
    assert panel.analyze_files_button.text() == "分析所選項目"
    panel.analyze_files_button.click()
    assert requested == [[str(second)]]


def test_conversion_analysis_does_nothing_without_selection(app, sample_media):
    first, second = sample_media("first.webm", "unknown.media")
    panel = ConversionPanel()
    requested = []
    panel.analyze_selected_requested.connect(requested.append)

    panel.add_files([first, second])

    assert panel.analyze_files_button.isEnabled()
    panel.analyze_files_button.click()
    assert requested == []

    panel.clear_files()
    assert panel.analyze_files_button.isEnabled()


def test_conversion_panel_applies_preset_and_marks_overrides(app):
    panel = ConversionPanel()
    preset = ConversionPreset(
        id="proxy", name="Proxy Work", target_format="mov", video_codec="prores",
        prores_profile="lt", resolution_height=1080, fps="24000/1001", audio_codec="pcm_s24le",
    )

    panel.set_presets([preset], "proxy")
    payload = panel.request_payload()

    assert panel.current_preset_id() == "proxy"
    assert payload["target_format"] == "mov"
    assert payload["video_codec"] == "prores"
    assert payload["prores_profile"] == "lt"
    assert payload["resolution_height"] == 1080
    assert not panel.target_format_combo.isEnabled()
    assert panel.prores_profile_combo.isEnabled()
    assert not panel.gop_combo.isEnabled()
    assert not panel.update_preset_button.isEnabled()

    panel.prores_profile_combo.setCurrentIndex(panel.prores_profile_combo.findData("hq"))
    assert panel.preset_combo.currentIndex() == -1
    assert panel.preset_combo.placeholderText() == "Proxy Work*"
    assert panel.update_preset_button.isEnabled()

    panel.preset_combo.setCurrentIndex(panel.preset_combo.findData("default:video"))
    assert panel.request_payload()["target_format"] == "mp4"
    assert panel.request_payload()["video_codec"] == "auto"
    assert panel.preset_combo.currentData() == "default:video"

    panel.video_codec_combo.setCurrentIndex(panel.video_codec_combo.findData("auto"))
    panel.target_format_combo.setCurrentIndex(panel.target_format_combo.findData("webm"))
    assert not panel.video_codec_combo.isEnabled()
    assert not panel.resolution_combo.isEnabled()
    assert not panel.audio_codec_combo.isEnabled()


def test_conversion_panel_manages_custom_presets(app, monkeypatch):
    panel = ConversionPanel()
    emitted = []
    panel.presets_changed.connect(emitted.append)
    names = iter([("Work", True), ("Work Renamed", True)])
    monkeypatch.setattr("panels.QInputDialog.getText", lambda *_args, **_kwargs: next(names))
    monkeypatch.setattr(
        "panels.QMessageBox.question", lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes
    )

    panel._save_preset()
    assert panel.preset_combo.count() == 2
    assert panel.current_preset_id() != "default:video"
    assert emitted[-1][0].name == "Work"

    panel.target_format_combo.setCurrentIndex(panel.target_format_combo.findData("mov"))
    assert panel.preset_combo.placeholderText() == "Work*"
    panel._update_preset()
    assert panel.preset_combo.currentText() == "Work"
    assert not panel.preset_combo.placeholderText()
    panel._rename_preset()
    assert panel.preset_combo.currentText() == "Work Renamed"
    panel._delete_preset()
    assert panel.preset_combo.count() == 1
    assert panel.current_preset_id() == "default:video"


def test_conversion_and_replacement_presets_share_catalog_without_syncing_values(app, monkeypatch):
    conversion, replacement = ConversionPanel(), ReplacementPanel()
    preset = ConversionPreset(id="shared", name="Shared", quality_value=7.5)
    conversion.set_presets([preset], "shared")
    replacement.set_presets([preset], "shared")
    conversion.presets_changed.connect(replacement.refresh_presets)
    replacement.presets_changed.connect(conversion.refresh_presets)
    monkeypatch.setattr(
        "panels.QMessageBox.question", lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes
    )

    replacement.duration_mode_combo.setCurrentIndex(replacement.duration_mode_combo.findData("custom"))
    replacement.custom_duration_edit.setText("00:00:12.500")
    replacement.audio_card.loop_checkbox.setChecked(True)
    conversion.quality_value_spin.setValue(10)
    conversion._update_preset()

    assert conversion.preset_combo.currentText() == "Shared"
    assert replacement.quality_value_spin.value() == 7.5
    assert replacement.preset_combo.currentIndex() == -1
    assert replacement.preset_combo.placeholderText() == "Shared*"
    assert replacement.update_preset_button.isEnabled()
    assert replacement.custom_duration_edit.text() == "00:00:12.500"
    assert replacement.audio_card.loop_checkbox.isChecked()

    replacement.preset_combo.setCurrentIndex(replacement.preset_combo.findData("shared"))
    assert replacement.quality_value_spin.value() == 10
    assert replacement.preset_combo.currentText() == "Shared"
    assert replacement.custom_duration_edit.text() == "00:00:12.500"
    assert replacement.audio_card.loop_checkbox.isChecked()

    conversion.quality_value_spin.setValue(12)
    conversion._update_preset()
    replacement._update_preset()
    assert replacement.preset_combo.currentText() == "Shared"
    assert conversion.quality_value_spin.value() == 12
    assert conversion.preset_combo.placeholderText() == "Shared*"

    replacement._delete_preset()
    assert conversion.quality_value_spin.value() == 12
    assert conversion.preset_combo.placeholderText() == "Custom*"
    assert not conversion.update_preset_button.isEnabled()
    assert not conversion.rename_preset_button.isEnabled()
    assert not conversion.delete_preset_button.isEnabled()


def test_replacement_can_create_and_rename_shared_presets_without_saving_replacement_options(app, monkeypatch):
    conversion, replacement = ConversionPanel(), ReplacementPanel()
    replacement.presets_changed.connect(conversion.refresh_presets)
    names = iter([("Replacement", True), ("Shared Rename", True)])
    monkeypatch.setattr("panels.QInputDialog.getText", lambda *_args, **_kwargs: next(names))

    replacement.duration_mode_combo.setCurrentIndex(replacement.duration_mode_combo.findData("custom"))
    replacement.custom_duration_edit.setText("00:00:20.000")
    replacement.visual_card.loop_checkbox.setChecked(True)
    replacement.quality_value_spin.setValue(9)
    replacement._save_preset()

    preset = replacement.custom_presets()[0]
    assert conversion.preset_combo.findData(preset.id) >= 0
    assert preset.quality_value == 9
    assert "duration_mode" not in preset.to_dict()
    assert "visual_loop" not in preset.to_dict()
    conversion.preset_combo.setCurrentIndex(conversion.preset_combo.findData(preset.id))
    replacement._rename_preset()
    assert conversion.preset_combo.currentText() == "Shared Rename"
    assert not conversion.preset_combo.placeholderText()
    assert replacement.custom_duration_edit.text() == "00:00:20.000"
    assert replacement.visual_card.loop_checkbox.isChecked()


def test_conversion_preset_dirty_labels_and_incremented_save_names(app, monkeypatch):
    set_language("zh_TW")
    panel = ConversionPanel()
    presets = [ConversionPreset(id="named-custom", name="自訂")]
    panel.set_presets(presets, "default:video")

    assert panel.preset_combo.findData("custom") < 0
    assert panel.preset_combo.findText("自訂") >= 0
    panel.target_format_combo.setCurrentIndex(panel.target_format_combo.findData("mov"))
    assert panel.preset_combo.currentIndex() == -1
    assert panel.preset_combo.placeholderText() == "自訂*"

    suggestions = []

    def accept_suggestion(*_args, **kwargs):
        suggestions.append(kwargs["text"])
        return kwargs["text"], True

    monkeypatch.setattr("panels.QInputDialog.getText", accept_suggestion)
    panel._save_preset()
    assert suggestions == ["自訂-1"]
    assert panel.preset_combo.currentText() == "自訂-1"
    assert not panel.preset_combo.placeholderText()

    numbered = [
        ConversionPreset(id="mp4", name="MP4"), ConversionPreset(id="mp4-1", name="MP4-1"),
        ConversionPreset(id="work-2", name="Work-2"), ConversionPreset(id="work-3", name="Work-3"),
    ]
    panel.set_presets(numbered, "mp4")
    assert panel._next_preset_name("video") == "MP4-2"
    panel.set_presets(numbered, "work-2")
    assert panel._next_preset_name("video") == "Work-4"


def test_conversion_panel_switches_category_pages_without_audio_presets(app):
    panel = ConversionPanel()
    presets = [
        ConversionPreset(id="video-work", name="Work", media_type="video", target_format="mov"),
        ConversionPreset(id="audio-work", name="Work", media_type="audio", target_format="mp3", audio_bitrate=256),
        ConversionPreset(id="subtitle-work", name="Caption", media_type="subtitle", target_format="ass"),
    ]
    panel.set_presets(presets, {
        "video": "video-work", "audio": "audio-work", "subtitle": "subtitle-work",
    })

    assert panel.advanced_stack.currentIndex() == 0
    assert panel.preset_combo.findData("video-work") >= 0
    assert panel.preset_combo.findData("audio-work") < 0
    assert set(panel._preset_controls) == {"video"}
    assert [preset.id for preset in panel.custom_presets()] == ["video-work"]
    panel.output_type_combo.setCurrentIndex(panel.output_type_combo.findData("audio"))
    assert panel.advanced_stack.currentIndex() == 1
    assert panel.request_payload()["media_type"] == "audio"
    assert panel.request_payload()["target_format"] == "mp3"
    panel.audio_quality_combo.setCurrentIndex(panel.audio_quality_combo.findData(256))
    assert panel.request_payload()["audio_bitrate"] == 256
    panel.audio_format_combo.setCurrentIndex(panel.audio_format_combo.findData("m4a"))
    assert panel.request_payload()["target_format"] == "m4a"

    panel.output_type_combo.setCurrentIndex(panel.output_type_combo.findData("subtitle"))
    assert panel.advanced_stack.currentIndex() == 2
    assert panel.request_payload()["media_type"] == "subtitle"
    assert panel.request_payload()["target_format"] == "srt"
    assert "subtitle" not in panel._preset_controls
    assert panel.splitter.handleWidth() == 1


def test_conversion_output_types_translate_to_traditional_chinese(app):
    panel = ConversionPanel()
    set_language("zh_TW")
    translate_widget_tree(panel)
    panel.set_available_backends(("nvidia",))

    assert panel.output_type_combo.itemText(panel.output_type_combo.findData("video")) == "影片"
    assert panel.output_type_combo.itemText(panel.output_type_combo.findData("audio")) == "音訊"
    assert panel.output_type_combo.itemText(panel.output_type_combo.findData("subtitle")) == "字幕"
    assert panel.option_labels["video_bitrate"].text() == "影片位元率"
    assert panel.option_labels["video_format"].text() == "輸出格式"
    assert panel.option_labels["video_codec"].text() == "影片轉碼器"
    assert panel.option_labels["profile"].text() == "轉碼器設定"
    assert panel.option_labels["frame_rate"].text() == "每秒影格數"
    assert panel.option_labels["video_audio_codec"].text() == "音訊轉碼器"
    assert panel.option_labels["video_audio_bitrate"].text() == "音訊位元率"
    assert panel.option_labels["video_audio_sample_rate"].text() == "音訊採樣率"
    assert panel.option_labels["audio_quality"].text() == "音訊位元率"
    assert panel.option_labels["audio_sample_rate"].text() == "音訊採樣率"
    assert "檔案可能越大" in panel.option_labels["frame_rate"].toolTip()
    assert [panel.quality_mode_combo.itemText(index) for index in range(panel.quality_mode_combo.count())] == [
        "固定位元率(CBR)", "固定速率(CRF)", "平均位元率(VBR)", "平均位元率(VBR 2-Pass)",
    ]
    assert panel.mute_audio_checkbox.text() == "靜音"
    assert panel.save_preset_button.text() == "另存預設"
    assert panel.files_list.toolTip() == "將本機檔案拖放到這裡"
    assert panel.option_labels["acceleration"].toolTip() == "優先使用支援的硬體轉碼加速"
    assert panel.encoder_combo.itemText(panel.encoder_combo.findData("auto")) == "自動"


def test_conversion_options_are_interactive_and_have_tooltips(app):
    panel = ConversionPanel()

    no_tooltip = {"output_folder", "output_type", "video_preset"}
    assert all(not panel.option_labels[key].toolTip() for key in no_tooltip)
    assert all(label.toolTip() for key, label in panel.option_labels.items() if key not in no_tooltip)
    assert [panel.target_format_combo.itemText(index) for index in range(panel.target_format_combo.count())] == [
        "MP4", "MOV", "MKV", "WebM",
    ]
    assert panel.encoder_combo.parentWidget().title() == "General Settings"
    assert panel.profile_stack.currentWidget() is panel.h264_profile_combo
    panel.video_codec_combo.setCurrentIndex(panel.video_codec_combo.findData("prores"))
    assert panel.profile_stack.currentWidget() is panel.prores_profile_combo
    assert panel.target_format_combo.currentData() == "mov"
    assert not panel.encoder_combo.isEnabled()

    panel.video_codec_combo.setCurrentIndex(panel.video_codec_combo.findData("h264"))
    panel.target_format_combo.setCurrentIndex(panel.target_format_combo.findData("mp4"))
    assert not panel.audio_codec_combo.model().item(panel.audio_codec_combo.findData("pcm_s24le")).isEnabled()
    panel.target_format_combo.setCurrentIndex(panel.target_format_combo.findData("mov"))
    assert panel.audio_codec_combo.model().item(panel.audio_codec_combo.findData("pcm_s24le")).isEnabled()

    assert not panel.maximum_bitrate_spin.isEnabled()
    assert panel.quality_value_spin.decimals() == 3
    assert panel.quality_value_spin.singleStep() == 0.1
    panel.quality_mode_combo.setCurrentIndex(panel.quality_mode_combo.findData("vbr_2pass"))
    assert panel.maximum_bitrate_spin.isEnabled()
    assert panel.maximum_bitrate_spin.decimals() == 3
    assert panel.maximum_bitrate_spin.singleStep() == 0.1
    panel.quality_value_spin.setValue(12)
    panel.maximum_bitrate_spin.setValue(20)
    assert panel.request_payload()["quality_mode"] == "vbr_2pass"
    assert panel.request_payload()["maximum_bitrate"] == 20


def test_conversion_audio_quality_only_applies_to_lossy_encoding(app):
    panel = ConversionPanel()
    panel.output_type_combo.setCurrentIndex(panel.output_type_combo.findData("audio"))
    panel.audio_quality_combo.setCurrentIndex(panel.audio_quality_combo.findData(256))
    panel.audio_sample_rate_combo.setCurrentIndex(panel.audio_sample_rate_combo.findData(96000))

    assert panel.audio_quality_combo.isEnabled()
    assert panel.request_payload()["audio_bitrate"] == 256
    assert panel.request_payload()["audio_sample_rate"] == 96000
    assert not panel.encoder_combo.isEnabled()

    panel.audio_format_combo.setCurrentIndex(panel.audio_format_combo.findData("flac"))
    assert not panel.audio_quality_combo.isEnabled()
    assert panel.request_payload()["audio_bitrate"] is None
    panel.audio_format_combo.setCurrentIndex(panel.audio_format_combo.findData("mp3"))
    panel.remux_checkbox.setChecked(True)
    assert not panel.audio_quality_combo.isEnabled()
    assert panel.request_payload()["audio_bitrate"] is None
    assert panel.request_payload()["audio_sample_rate"] is None


def test_conversion_remux_checkbox_is_general_and_locked_for_subtitles(app):
    panel = ConversionPanel()
    assert panel.remux_checkbox.parentWidget().title() == "General Settings"
    assert "without encoding" in panel.remux_checkbox.toolTip()
    panel.remux_checkbox.setChecked(True)
    assert panel.request_payload()["stream_copy"]
    assert not panel.video_codec_combo.isEnabled()

    panel.output_type_combo.setCurrentIndex(panel.output_type_combo.findData("audio"))
    assert panel.remux_checkbox.isEnabled()
    assert not panel.remux_checkbox.isChecked()
    panel.remux_checkbox.setChecked(True)
    assert panel.request_payload()["stream_copy"]

    panel.output_type_combo.setCurrentIndex(panel.output_type_combo.findData("subtitle"))
    assert not panel.remux_checkbox.isEnabled()
    assert not panel.remux_checkbox.isChecked()
    assert not panel.request_payload()["stream_copy"]
    panel.output_type_combo.setCurrentIndex(panel.output_type_combo.findData("video"))
    assert panel.remux_checkbox.isEnabled()
    assert panel.remux_checkbox.isChecked()


def test_conversion_video_mute_disables_audio_settings_and_presets_none(app):
    panel = ConversionPanel()
    panel.audio_codec_combo.setCurrentIndex(panel.audio_codec_combo.findData("aac"))
    panel.audio_bitrate_combo.setCurrentIndex(panel.audio_bitrate_combo.findData(256))
    panel.mute_audio_checkbox.setChecked(True)

    payload = panel.request_payload()
    assert payload["audio_codec"] == "none"
    assert payload["audio_bitrate"] is None
    assert payload["audio_sample_rate"] is None
    assert not panel.audio_codec_combo.isEnabled()
    assert not panel.audio_bitrate_combo.isEnabled()

    panel.mute_audio_checkbox.setChecked(False)
    assert panel.audio_codec_combo.currentData() == "aac"
    assert panel.request_payload()["audio_bitrate"] == 256
    panel.video_audio_sample_rate_combo.setCurrentIndex(panel.video_audio_sample_rate_combo.findData(48000))
    assert panel.request_payload()["audio_sample_rate"] == 48000
    panel.set_presets([ConversionPreset(id="silent", name="Silent", audio_codec="none")], "silent")
    assert panel.mute_audio_checkbox.isChecked()
    assert panel.request_payload()["audio_codec"] == "none"


def test_conversion_panel_layout_alignment_and_initial_split(app):
    panel = ConversionPanel()
    panel.resize(1180, 760)
    panel.show()
    app.processEvents()
    try:
        left, right = panel.splitter_sizes()
        assert abs(left - right) <= 1
        assert panel.general_container.layout().contentsMargins().right() == 5
        assert panel.advanced_container.layout().contentsMargins().left() == 5
        assert panel.preset_combo.width() == panel.target_format_combo.width()
        assert all(row.spacing() == 5 for row in panel._setting_row_layouts)
        buttons = [
            panel.save_preset_button, panel.update_preset_button,
            panel.rename_preset_button, panel.delete_preset_button,
        ]
        assert len({button.geometry().top() for button in buttons}) == 1
        spin_positions = {
            "resolution": panel.resolution_spin.mapTo(panel, QPoint()).x(),
            "fps": panel.fps_spin.mapTo(panel, QPoint()).x(),
            "bitrate": panel.quality_value_spin.mapTo(panel, QPoint()).x(),
        }
        assert len(set(spin_positions.values())) == 1, spin_positions
        assert len({spin.width() for spin in (
            panel.resolution_spin, panel.fps_spin, panel.quality_value_spin,
        )}) == 1
        assert panel.quality_value_spin.width() == 140
        assert panel.maximum_bitrate_spin.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding
        assert panel.maximum_bitrate_spin.width() == panel.target_format_combo.width()
        assert panel.maximum_bitrate_spin.width() > 140
        assert abs(
            panel.allow_upscale_checkbox.mapTo(panel, panel.allow_upscale_checkbox.rect().center()).y()
            - panel.resolution_spin.mapTo(panel, panel.resolution_spin.rect().center()).y()
        ) <= 1
        assert panel.allow_upscale_checkbox.width() >= panel.allow_upscale_checkbox.sizeHint().width()
        assert abs(
            panel.mute_audio_checkbox.mapTo(panel, panel.mute_audio_checkbox.rect().center()).y()
            - panel.audio_codec_combo.mapTo(panel, panel.audio_codec_combo.rect().center()).y()
        ) <= 1
    finally:
        panel.close()


def test_log_panel_is_readonly_and_limits_blocks(app):
    panel = LogPanel(maximum_blocks=2)
    panel.append("one")
    panel.append_log("two")
    panel.append("three")
    assert panel.text_edit.isReadOnly()
    assert panel.text_edit.document().blockCount() == 2
    assert panel.text_edit.toPlainText() == "two\nthree"
    buttons = panel.layout().itemAt(2).layout()
    assert not hasattr(panel, "copy_button")
    assert buttons.itemAt(0).widget() is panel.error_filter_button
    assert buttons.itemAt(1).widget() is panel.clear_button
    assert buttons.itemAt(2).spacerItem() is not None
    assert panel.layout().itemAt(3).widget() is panel.text_edit
    panel.text_edit.selectAll()
    panel.text_edit.copy()
    assert app.clipboard().text() == "two\nthree"
    panel.clear_button.click()
    assert panel.text_edit.toPlainText() == ""


def test_log_panel_filters_errors_and_colors_complete_records(app):
    panel = LogPanel(maximum_blocks=20)
    panel.append("info", logging.INFO)
    panel.append("warning", logging.WARNING)
    panel.append("error heading\ntraceback line", logging.ERROR)
    panel.append("critical", logging.CRITICAL)

    document = panel.text_edit.document()
    assert document.findBlockByNumber(1).blockFormat().background().color().name() == theme_color("warning_soft")
    assert document.findBlockByNumber(2).blockFormat().background().color().name() == theme_color("error_soft")
    assert document.findBlockByNumber(3).blockFormat().background().color().name() == theme_color("error_soft")
    assert document.findBlockByNumber(2).begin().fragment().charFormat().foreground().color().name() == theme_color("error")

    panel.error_filter_button.click()
    assert panel.text_edit.toPlainText() == "error heading\ntraceback line\ncritical"
    panel.clear_button.click()
    panel.error_filter_button.click()
    assert panel.text_edit.toPlainText() == ""


def test_log_panel_bounds_a_single_multiline_record(app):
    panel = LogPanel(maximum_blocks=3)
    panel.append("heading\ndetail one\ndetail two\nfinal error", logging.ERROR)
    assert panel.text_edit.document().blockCount() == 3
    assert panel.text_edit.toPlainText() == "heading\n... 2 earlier lines omitted ...\nfinal error"


def test_log_panel_only_follows_output_when_already_at_bottom(app):
    panel = LogPanel(maximum_blocks=200)
    panel.resize(320, 120)
    panel.show()
    for index in range(80): panel.append(f"line {index}")
    app.processEvents()
    scrollbar = panel.text_edit.verticalScrollBar()
    assert scrollbar.maximum() > 0
    scrollbar.setValue(0)
    panel.append("stay here")
    assert scrollbar.value() == 0
    scrollbar.setValue(scrollbar.maximum())
    panel.append("follow this")
    assert scrollbar.value() == scrollbar.maximum()
    panel.close()


def test_page_subtitles_describe_user_tasks_in_both_languages(app):
    panels = [
        AnalyzePanel(), SubtitlePanel(), QueuePanel(), FileAnalysisPanel(),
        ConversionPanel(), ReplacementPanel(), LogPanel(), SettingsPanel(),
    ]
    english = [
        "Review the media, then choose what you want to download and in which format",
        "Find and download subtitles available for a video",
        "View and manage tasks that are waiting, running, or completed",
        "View format, video, audio, and other details about media files",
        "Convert video, audio, or subtitle files to the format you need",
        "Replace a video's audio or combine visual media with new audio",
        "Review application activity and find errors when something goes wrong",
        "Change appearance, updates, and how the application works",
    ]
    traditional_chinese = [
        "先查看媒體內容，再選擇要下載的項目與格式",
        "尋找並下載影片提供的字幕",
        "查看並管理等待中、執行中與已完成的任務",
        "查看影音檔案的格式、畫面、音訊與其他詳細資訊",
        "將影片、音訊或字幕轉換成需要的格式",
        "替換影片音訊，或將畫面素材搭配新的音訊",
        "查看程式執行情況，發生問題時可在這裡尋找錯誤",
        "調整外觀、更新與程式運作設定",
    ]

    def subtitles() -> list[str]:
        return [
            next(label.text() for label in panel.findChildren(QLabel) if label.property("role") == "pageSubtitle")
            for panel in panels
        ]

    assert subtitles() == english
    set_language("zh_TW")
    for panel in panels: translate_widget_tree(panel)
    assert subtitles() == traditional_chinese
    for panel in panels: panel.close()


def test_bottom_status_bar_supports_progress_and_indeterminate(app):
    status = BottomStatusBar()
    first = make_task("first", 0.424)
    first.status = TaskStatus.RUNNING
    second = make_task("second", -1)
    second.status = TaskStatus.RUNNING
    status.set_tasks([first])
    one_row_height = status.sizeHint().height()
    status.set_tasks([first, second])
    rows = status.activity_rows()
    two_row_height = status.sizeHint().height()
    assert len(rows) == 2
    assert rows[0].summary_label.text() == "Download: Example"
    assert rows[0].progress_label.text() == "42%"
    assert rows[0].progress_bar.value() == 42
    assert rows[1].progress_label.text() == "--"
    assert rows[1].progress_bar.minimum() == rows[1].progress_bar.maximum() == 0
    assert two_row_height > one_row_height

    first.status = TaskStatus.COMPLETED
    status.update_task(first)
    assert len(status.activity_rows()) == 1
    second.status = TaskStatus.COMPLETED
    status.update_task(second)
    assert not status.activity_rows()
    assert status.message_label.isVisibleTo(status)
    assert status.sizeHint().height() <= one_row_height


def test_rounded_progress_bar_draws_curved_chunk_ends(app):
    previous_stylesheet = app.styleSheet()
    app.setStyleSheet(load_theme_stylesheet())
    try:
        progress = RoundedProgressBar()
        progress.setTextVisible(False)
        progress.setRange(0, 100)
        progress.setValue(42)
        progress.resize(320, 10)
        progress.show()
        app.processEvents()
        image = progress.grab().toImage()
        accent = theme_color("accent")
        rows = [
            [x for x in range(image.width()) if image.pixelColor(x, y).name() == accent]
            for y in range(image.height())
        ]
        top, middle = rows[2], rows[4]
        assert min(top) > min(middle)
        assert max(top) < max(middle)
    finally:
        app.setStyleSheet(previous_stylesheet)
