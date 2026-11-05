"""The Ollama install hint of dashboard.js (unreachable, no model, recovery), run under Node."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_STATIC = Path(__file__).resolve().parents[1] / "src" / "nexusvox" / "dashboard" / "static"
JS = _STATIC / "dashboard.js"
HTML = (_STATIC / "index.html").read_text(encoding="utf-8")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")

FAKE = (
    "function el() { const e = {hidden: true, textContent: '', children: [], _c: new Set(),"
    " append(c) { this.children.push(c); },"
    " classList: {add(c){e._c.add(c)}, remove(...c){c.forEach(x=>e._c.delete(x))}}};"
    " return e; }\n"
    "const document = {createElement: () => ({})};\n"
)
DOWN = "{reachable: false, protocol: 'ollama', url: 'http://127.0.0.1:11434', model: 'm', model_installed: null}"
NO_MODEL = "{reachable: true, protocol: 'ollama', url: 'u', model: 'gemma4:e4b', model_installed: false}"
READY = "{reachable: true, protocol: 'ollama', url: 'u', model: 'm', model_installed: true}"


def run_js(body: str):
    source = JS.read_text(encoding="utf-8")
    status = re.search(r"const STATUS_STATES.*?\n\}\n", source, re.S).group(0)
    start = source.index("// ── Ollama install hint")
    helpers = source[start : source.index("// ── end of Ollama install hint")]
    out = subprocess.run(
        ["node", "-e", f"{FAKE}{status}\n{helpers}\n{body}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=30,
    ).stdout
    return json.loads(out)


def test_unreachable_shows_red_hint_with_download_link():
    out = run_js(
        f"const e = el(); renderOllamaHint(e, {DOWN});"
        "console.log(JSON.stringify([e.hidden, e._c.has('down'), e.textContent, e.children]));"
    )
    assert out[0] is False and out[1] is True
    assert "Ollama is not running" in out[2]
    assert out[3] == [
        {
            "href": "https://ollama.com/download",
            "target": "_blank",
            "rel": "noopener noreferrer",
            "textContent": "ollama.com/download",
        }
    ]


def test_reachable_without_model_points_to_settings_models():
    out = run_js(
        f"const e = el(); renderOllamaHint(e, {NO_MODEL});"
        "console.log(JSON.stringify([e.hidden, e.textContent, e.children.length]));"
    )
    assert out[0] is False
    assert "gemma4:e4b" in out[1] and "Settings > Models" in out[1]
    assert out[2] == 0


def test_hint_disappears_when_ollama_comes_up():
    out = run_js(
        f"const e = el(); renderOllamaHint(e, {DOWN}); renderOllamaHint(e, {READY});"
        "console.log(JSON.stringify([e.hidden, e.textContent, e._c.has('down')]));"
    )
    assert out == [True, "", False]


def test_hidden_when_the_feature_is_not_wanted():
    out = run_js(f"const e = el(); renderOllamaHint(e, {DOWN}, false); console.log(JSON.stringify(e.hidden));")
    assert out is True


def test_other_servers_get_no_ollama_link():
    out = run_js(
        "const e = el(); renderOllamaHint(e, {reachable: false, protocol: 'openai', url: 'http://x'});"
        "console.log(JSON.stringify([e.textContent, e.children.length]));"
    )
    assert "ollama" not in out[0].lower() and out[1] == 0


@pytest.mark.parametrize("hint_id", ["cleanup-llm-hint", "upload-llm-hint", "llm-status", "llm-model-hint"])
def test_every_area_has_a_hint_element(hint_id):
    assert f'id="{hint_id}"' in HTML
