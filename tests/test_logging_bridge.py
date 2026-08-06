import logging
from datetime import date, timedelta

from logging_bridge import LOG_RETENTION_COUNT, QtLogBridge, _prune_log_files, _StreamProxy


def test_stream_proxy_accepts_missing_or_non_stream_console():
    logger = logging.getLogger("stream-proxy-test")
    proxy = _StreamProxy("console unavailable", logger, logging.INFO)
    assert proxy.write("message\n") == len("message\n")
    proxy.flush()
    assert not proxy.isatty()


def test_log_bridge_only_appends_errors_to_daily_file(tmp_path):
    log_path = tmp_path / f"mochistar-{date.today().isoformat()}.log"
    log_path.write_text("previous session\n", encoding="utf-8")
    bridge = QtLogBridge(tmp_path)
    bridge.install()
    try:
        logger = logging.getLogger("daily-log-test")
        logger.info("successful result")
        logger.error("failed result")
    finally:
        bridge.restore_streams()

    content = log_path.read_text(encoding="utf-8")
    assert "previous session" in content
    assert "successful result" not in content
    assert "failed result" in content


def test_log_bridge_sends_formatted_messages_with_original_level(tmp_path):
    bridge = QtLogBridge(tmp_path)
    messages = []
    bridge.message.connect(lambda message, level: messages.append((message, level)))
    bridge.install()
    try:
        logging.getLogger("level-test").warning("careful")
        logging.getLogger("level-test").error("failed")
    finally:
        bridge.restore_streams()

    assert [level for _message, level in messages] == [logging.WARNING, logging.ERROR]
    assert "| WARNING | level-test | careful" in messages[0][0]
    assert "| ERROR | level-test | failed" in messages[1][0]


def test_log_bridge_does_not_create_file_without_errors(tmp_path):
    bridge = QtLogBridge(tmp_path)
    bridge.install()
    try:
        logging.getLogger("daily-log-test").info("successful result")
    finally:
        bridge.restore_streams()

    assert not list(tmp_path.glob("*.log"))


def test_prune_log_files_keeps_latest_five_daily_logs(tmp_path):
    today = date.today()
    paths = []
    for days_ago in range(LOG_RETENTION_COUNT + 2):
        path = tmp_path / f"mochistar-{(today - timedelta(days=days_ago)).isoformat()}.log"
        path.write_text("log", encoding="utf-8")
        paths.append(path)
    unrelated = tmp_path / "mochistar-manual.log"
    unrelated.write_text("keep", encoding="utf-8")

    _prune_log_files(tmp_path)

    assert all(path.exists() for path in paths[:LOG_RETENTION_COUNT])
    assert all(not path.exists() for path in paths[LOG_RETENTION_COUNT:])
    assert unrelated.exists()
