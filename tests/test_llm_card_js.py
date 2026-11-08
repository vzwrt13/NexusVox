"""The Language Model card's refresh and overview helpers of the dashboard scripts, run under Node."""

import json
import shutil
import subprocess

import pytest
from dashboard_js import dashboard_source

START = "// ── LLM card refresh"
END = "// ── end of LLM card refresh"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")


def run_js(body: str):
    source = dashboard_source()
    helpers = source[source.index(START) : source.index(END)]
    out = subprocess.run(
        ["node", "-e", f"{helpers}\n(async () => {{ {body} }})()"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=30,
    ).stdout
    return json.loads(out)


def test_under_reported_size_is_not_shown_as_real():
    out = run_js(
        """
        const data = {reachable: true, protocol: "ollama", device: "gpu",
                      loaded: {plausible: false, on_gpu: true}};
        const m = {size_gb: 4.6};
        console.log(JSON.stringify([llmRunsOn(data), llmMemory(data, m)]));"""
    )
    assert out[0] == {"text": "GPU", "warn": False}
    assert out[1] == "~4.6 GB VRAM (estimate)"


def test_plausible_size_shows_real_figures_and_the_split():
    out = run_js(
        """
        const base = {reachable: true, protocol: "ollama", device: "gpu"};
        const full = {...base, loaded: {plausible: true, vram_gb: 7.7, ram_gb: 0}};
        const split = {...base, loaded: {plausible: true, vram_gb: 5, ram_gb: 3}};
        console.log(JSON.stringify([llmRunsOn(full), llmMemory(full, null),
                                    llmRunsOn(split), llmMemory(split, null)]));"""
    )
    assert out[0] == {"text": "GPU (fully in VRAM)", "warn": False}
    assert out[1] == "7.7 GB VRAM"
    assert out[2]["warn"] is True and out[2]["text"].startswith("GPU + CPU")
    assert out[3] == "5 GB VRAM + 3 GB RAM"


def test_no_figures_when_ollama_is_down_or_not_ollama():
    out = run_js(
        """
        const m = {size_gb: 4.6};
        console.log(JSON.stringify([llmMemory({reachable: false, protocol: "ollama"}, m),
                                    llmMemory({reachable: true, protocol: "openai"}, m)]));"""
    )
    assert out == ["—", "—"]


REFRESHER = """
    let hidden = false, active = true, payload = {reachable: true, selected: "a", recommended: [], other: []};
    const renders = [], timers = [];
    const r = createLlmRefresher({
      fetchData: async () => JSON.parse(JSON.stringify(payload)),
      render: (d, rebuild) => renders.push(rebuild),
      isActive: () => !hidden && active,
      schedule: (fn, ms) => { timers.push(fn); return timers.length; },
      cancel: () => {},
    });
"""


def test_no_render_without_change_and_options_stay_when_only_the_overview_changes():
    out = run_js(
        REFRESHER
        + """
        await r.refresh(true);
        await r.refresh();
        payload.loaded = {plausible: true, vram_gb: 1, ram_gb: 0};
        await r.refresh();
        console.log(JSON.stringify(renders));"""
    )
    # first draw rebuilds; the identical poll draws nothing; a loaded-only change keeps the select
    assert out == [True, False]


def test_recovery_from_unreachable_rebuilds_the_dropdown():
    out = run_js(
        REFRESHER
        + """
        payload.reachable = false;
        await r.refresh(true);
        payload = {reachable: true, selected: "a", recommended: [{name: "a"}], other: [{name: "b"}]};
        await r.refresh();
        console.log(JSON.stringify(renders));"""
    )
    assert out == [True, True]


def test_polling_only_while_visible():
    out = run_js(
        REFRESHER
        + """
        r.start();
        hidden = true;
        timers[0]();   // a tick while hidden does nothing
        await Promise.resolve();
        const hiddenRenders = renders.length;
        hidden = false;
        r.wake();      // visible again: one refresh
        await new Promise((res) => setTimeout(res, 0));
        console.log(JSON.stringify([hiddenRenders, renders.length, r.running]));"""
    )
    assert out == [0, 1, True]


# -- models per task -------------------------------------------------------------------------------

_TWO_SERVERS = """
    const data = {reachable: true, protocol: "openai", url: "http://127.0.0.1:8083/v1", selected: "big.gguf",
        recommended: [], other: [{name: "big.gguf", size_gb: 0, installed: true}],
        ollama: {active: false, reachable: true, url: "http://127.0.0.1:11434",
                 models: [{name: "gemma4:e2b"}]},
        servers: [{active: true, name: "llama", url: "http://127.0.0.1:8083/v1", reachable: true,
                   models: [{name: "big.gguf"}]}],
        roles: {enabled: true, main_server: "llama", fast: null, tasks: [
            {id: "dictation_cleanup", label: "Dictation cleanup", role: "fast"},
            {id: "polish", label: "Polish", role: "main"},
            {id: "file_cleanup", label: "Cleanup of uploaded files", role: "main"}]}};
"""


def test_the_fast_dropdown_names_the_server_of_every_model():
    out = run_js(
        _TWO_SERVERS
        + """
        const groups = llmFastOptionGroups(data);
        console.log(JSON.stringify(groups.map((g) => [g.label, g.options.map((o) => [o.value, !!o.disabled])])));"""
    )
    assert out[0] == [None, [["", True]]]  # nothing picked yet
    assert out[1] == ["llama — Models on this server", [['@@["http://127.0.0.1:8083/v1","big.gguf"]', False]]]
    assert out[2] == ["Ollama", [['@@["http://127.0.0.1:11434","gemma4:e2b"]', False]]]


def test_a_saved_fast_model_its_server_does_not_list_stays_selectable():
    out = run_js(
        _TWO_SERVERS
        + """
        data.roles.fast = {url: "http://10.0.0.5:11434", protocol: "ollama", model: "tiny:1b", server: "Box"};
        const groups = llmFastOptionGroups(data);
        console.log(JSON.stringify([groups[0], llmFastValue(data)]));"""
    )
    assert out[0] == {
        "label": "Box",
        "options": [{"value": '@@["http://10.0.0.5:11434","tiny:1b"]', "label": "tiny:1b"}],
    }
    assert out[1] == '@@["http://10.0.0.5:11434","tiny:1b"]'


def test_a_model_that_is_not_installed_cannot_be_the_fast_model():
    out = run_js(
        """
        const data = {reachable: true, protocol: "ollama", url: "http://127.0.0.1:11434", selected: "gemma4:e4b",
            recommended: [{name: "gemma4:e2b", size_gb: 4.6, installed: false}], other: [],
            ollama: {active: true}, servers: [],
            roles: {enabled: true, main_server: "Ollama", fast: null, tasks: []}};
        console.log(JSON.stringify(llmFastOptionGroups(data)[1]));"""
    )
    assert out["label"] == "Ollama — Recommended"
    assert out["options"][0]["disabled"] is True


def test_the_overview_says_what_the_main_model_does():
    out = run_js(
        _TWO_SERVERS
        + """
        const before = llmMainUsedFor(data);
        data.roles.fast = {url: "http://127.0.0.1:11434", protocol: "ollama", model: "gemma4:e2b", server: "Ollama"};
        console.log(JSON.stringify([before, llmMainUsedFor(data)]));"""
    )
    assert out == ["Cleanup, Polish, upload cleanup, extensions", "Polish, Cleanup of uploaded files, extensions"]
