"""The dashboard's Ollama install hints (unreachable, no model, recovery, who sees what), run under Node."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from dashboard_js import dashboard_source

_STATIC = Path(__file__).resolve().parents[1] / "src" / "nexusvox" / "dashboard" / "static"
HTML = (_STATIC / "index.html").read_text(encoding="utf-8")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")

# Like the DOM: assigning textContent drops the children (so a link left behind is a real failure).
FAKE = """
function el() {
  const e = {hidden: true, children: [], _t: '', _c: new Set(),
    append(c) { this.children.push(c); },
    classList: {add(c) { e._c.add(c); }, remove(...c) { c.forEach((x) => e._c.delete(x)); }}};
  Object.defineProperty(e, 'textContent', {get() { return e._t; }, set(v) { e._t = v; e.children = []; }});
  return e;
}
const document = {createElement: () => ({})};
function ui(over = {}) {
  return Object.assign({polish: el(), cleanup: el(), upload: el(), models: el(),
    polishOn: true, cleanupOn: true, uploadOn: true, modelsData: {reachable: false, protocol: 'ollama'}}, over);
}
function view(e) {
  return {hidden: e.hidden, text: e.textContent, links: e.children.length, down: e._c.has('down'), ok: e._c.has('ok')};
}
"""
URL = "http://127.0.0.1:11434"
DOWN = f"{{reachable: false, protocol: 'ollama', url: '{URL}', model: 'm', model_installed: null}}"
NO_MODEL = f"{{reachable: true, protocol: 'ollama', url: '{URL}', model: 'gemma4:e4b', model_installed: false}}"
READY = f"{{reachable: true, protocol: 'ollama', url: '{URL}', model: 'm', model_installed: true}}"
UNKNOWN = f"{{reachable: true, protocol: 'ollama', url: '{URL}', model: 'm', model_installed: null}}"


def run_js(body: str):
    source = dashboard_source()
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


def test_unreachable_shows_red_hint_with_link_and_the_address():
    out = run_js(f"const u = ui(); renderLlmHints({DOWN}, u); console.log(JSON.stringify(view(u.cleanup)));")
    assert out["hidden"] is False and out["down"] is True and out["links"] == 1
    assert "Ollama is not running" in out["text"] and URL in out["text"]


def test_link_points_to_the_ollama_download_page():
    out = run_js(f"const e = el(); renderOllamaHint(e, {DOWN}); console.log(JSON.stringify(e.children));")
    assert out == [
        {
            "href": "https://ollama.com/download",
            "target": "_blank",
            "rel": "noopener noreferrer",
            "textContent": "ollama.com/download",
        }
    ]


def test_remote_ollama_keeps_the_address_and_offers_no_install_link():
    remote = "{reachable: false, protocol: 'ollama', url: 'http://192.168.1.20:11434', model: 'm'}"
    out = run_js(f"const u = ui(); renderLlmHints({remote}, u); console.log(JSON.stringify(view(u.polish)));")
    assert "192.168.1.20:11434" in out["text"] and "[llm].url" in out["text"] and out["links"] == 0


def test_reachable_without_model_points_to_settings_models_and_the_models_card_says_so():
    out = run_js(
        f"const u = ui({{modelsData: {{reachable: true, protocol: 'ollama'}}}});"
        f"renderLlmHints({NO_MODEL}, u); console.log(JSON.stringify([view(u.cleanup), view(u.models)]));"
    )
    assert "gemma4:e4b" in out[0]["text"] and "Settings > Models" in out[0]["text"] and out[0]["links"] == 0
    assert out[1]["hidden"] is False and "button below" in out[1]["text"]


def test_recovery_clears_every_hint_and_the_link():
    out = run_js(
        f"const u = ui(); renderLlmHints({DOWN}, u); u.modelsData = {{reachable: true, protocol: 'ollama'}};"
        f"renderLlmHints({READY}, u);"
        "console.log(JSON.stringify([u.cleanup, u.upload, u.models].map(view).concat([view(u.polish)])));"
    )
    for v in out[:3]:
        assert v == {"hidden": True, "text": "", "links": 0, "down": False, "ok": False}
    assert out[3]["ok"] is True and out[3]["hidden"] is False and out[3]["links"] == 0


def test_no_hint_for_features_the_user_did_not_turn_on():
    out = run_js(
        f"const u = ui({{polishOn: false, cleanupOn: false, uploadOn: false}});"
        f"renderLlmHints({DOWN}, u); console.log(JSON.stringify([u.polish, u.cleanup, u.upload].map(view)));"
    )
    assert all(v["hidden"] is True and v["text"] == "" for v in out)


def test_turning_a_feature_off_hides_its_hint():
    out = run_js(
        f"const u = ui(); renderLlmHints({DOWN}, u); u.uploadOn = false; renderLlmHints({DOWN}, u);"
        "console.log(JSON.stringify([view(u.upload), view(u.cleanup)]));"
    )
    assert out[0]["hidden"] is True and out[1]["hidden"] is False


def test_models_card_does_not_claim_ollama_is_down_when_only_the_list_fails():
    out = run_js(
        f"const u = ui(); renderLlmHints({UNKNOWN}, u); console.log(JSON.stringify([view(u.models), view(u.cleanup)]));"
    )
    assert "cannot be listed" in out[0]["text"] and "saved model is kept" in out[0]["text"] and out[0]["links"] == 0
    assert out[1]["hidden"] is True


def test_other_servers_get_no_ollama_link_and_the_models_card_names_the_server():
    out = run_js(
        "const s = {reachable: false, protocol: 'openai', url: 'http://x'};"
        "const u = ui({modelsData: {reachable: false, protocol: 'openai'}});"
        "renderLlmHints(s, u); console.log(JSON.stringify([view(u.cleanup), view(u.models)]));"
    )
    assert "ollama" not in out[0]["text"].lower() and out[0]["links"] == 0
    assert "No model server at http://x" in out[1]["text"] and out[1]["links"] == 0


@pytest.mark.parametrize(
    "hint_id", ["cleanup-llm-hint", "upload-llm-hint", "llm-status", "llm-model-hint", "llm-pull-selected"]
)
def test_every_area_has_its_element(hint_id):
    assert f'id="{hint_id}"' in HTML


def test_wiring_covers_the_triggers_that_make_a_hint_recover():
    source = dashboard_source()
    assert (
        "loadLlmStatus().catch(() => {}); // the cleanup hint must reflect Ollama as it is now" in source
    )  # Upload tab
    assert "setInterval(() => {" in source and '"tab-upload"' in source  # own poll, not the model list's
    assert source.count("refreshLlmHints();") >= 5  # cleanup (before and after save), polish, upload toggle, models
