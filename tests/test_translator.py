"""Tests for the translate-before-inject step."""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from nexusvox.config import TranslatorConfig
from nexusvox.translator import translate


def _serve_once(status: int, body: bytes) -> tuple[HTTPServer, list[dict]]:
    """A local HTTP server that answers one POST with `body` and records what it got."""
    received: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            received.append(json.loads(self.rfile.read(length)))
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()
    return server, received


def _config(server: HTTPServer, enabled: bool = True) -> TranslatorConfig:
    return TranslatorConfig(enabled=enabled, url=f"http://127.0.0.1:{server.server_port}/translate")


def test_translate_returns_server_text():
    server, received = _serve_once(200, b'{"text": "So, I think that is fine.", "source": "de", "ms": 400}')

    result = translate("Also ich glaube, das passt.", _config(server))

    assert result.text == "So, I think that is fine."
    assert (result.translated, result.source, result.ms, result.error) == (True, "de", 400, None)
    assert received == [{"text": "Also ich glaube, das passt."}]
    server.server_close()


def test_translate_disabled_does_not_call_server():
    server, received = _serve_once(200, b'{"text": "unused"}')

    result = translate("Hallo Welt", _config(server, enabled=False))

    assert (result.text, result.translated, result.error) == ("Hallo Welt", False, None)
    assert received == []
    server.server_close()


def test_translate_blank_text_untouched():
    assert translate("   ", TranslatorConfig(enabled=True)).text == "   "


def test_translate_server_error_falls_back_to_original():
    server, _ = _serve_once(502, b'{"error": "Ollama unreachable"}')

    result = translate("Hallo Welt", _config(server))

    assert (result.text, result.translated) == ("Hallo Welt", False)
    assert result.error is not None and "502" in result.error
    server.server_close()


def test_translate_malformed_reply_falls_back_to_original():
    server, _ = _serve_once(200, b"not json")

    result = translate("Hallo Welt", _config(server))

    assert (result.text, result.translated) == ("Hallo Welt", False)
    assert result.error is not None
    server.server_close()


def test_translate_nothing_listening_falls_back_to_original():
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    free_port = probe.getsockname()[1]
    probe.close()
    config = TranslatorConfig(enabled=True, url=f"http://127.0.0.1:{free_port}/translate", timeout_s=0.5)

    result = translate("Hallo Welt", config)

    assert (result.text, result.translated) == ("Hallo Welt", False)
    assert result.error is not None
