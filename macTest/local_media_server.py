from __future__ import annotations

import argparse
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


MEDIA_BYTES = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"


class _MediaHandler(BaseHTTPRequestHandler):
    """提供 yt-dlp Generic extractor 使用的固定 media response"""

    def do_HEAD(self) -> None:
        self._send_media(False)

    def do_GET(self) -> None:
        self._send_media(True)

    def _send_media(self, include_body: bool) -> None:
        if self.path != "/fixture.mp4":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(MEDIA_BYTES)))
        self.end_headers()
        if include_body: self.wfile.write(MEDIA_BYTES)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def start_local_media_server(port: int = 0) -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    """啟動 local media fixture server 並回傳測試 URL"""
    server = ThreadingHTTPServer(("127.0.0.1", port), _MediaHandler)
    thread = threading.Thread(target=server.serve_forever, name="media-fixture", daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_port}/fixture.mp4"


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the macTest media fixture")
    parser.add_argument("--port", type=int, default=38473)
    arguments = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", arguments.port), _MediaHandler)
    print(f"http://127.0.0.1:{server.server_port}/fixture.mp4", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__": raise SystemExit(main())
