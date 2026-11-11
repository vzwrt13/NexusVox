"""Regression tests for the settings, models, dev and shared-core scripts: no browser dialogs, only defined style
tokens, a cancelled hotkey recording, the date filter, a refused dictionary entry and the sample list. Run under
Node with the stand-in DOM of tests/fake_dom.js."""

import re

from dashboard_js import STATIC, dashboard_source
from test_dashboard_fixes_js import FAKE_DOM, _fn, _source, run

CSS = (STATIC / "style.css").read_text(encoding="utf-8")


def test_no_script_opens_a_browser_dialog():
    assert not re.search(r"(?<![\w.])(alert|confirm|prompt)\(", dashboard_source())


def test_only_defined_style_tokens_are_used():
    defined = set(re.findall(r"(--[a-z0-9-]+)\s*:", CSS))
    used = set(re.findall(r"var\((--[a-z0-9-]+)", CSS + dashboard_source()))
    assert used - defined == set()


def test_the_denied_banner_hard_codes_no_colour():
    core = _source("dashboard-core.js")
    banner = core[core.index("// ── Access denied") : core.index("// ── Status colour rule")]
    assert "#" not in re.sub(r"//.*", "", banner)
    assert "cssText" not in banner
    assert re.search(r'setStatus\(\s*banner,\s*"down"', banner)


def test_the_date_filter_goes_only_onto_analytics_requests():
    core = _source("dashboard-core.js")
    script = (
        FAKE_DOM.replace("const fetch =", "var fetch =")
        + "page([['filter-start', 'input'], ['filter-end', 'input']]);"
        + "document.getElementById('filter-start').value = '2026-01-01';"
        + "const urls = []; fetch = async (u) => { urls.push(u); return { json: async () => ({}) }; };"
        + _fn(core, "dateParams")
        + _fn(core, "api", True)
        + _fn(core, "analyticsApi", True)
        + """
    (async () => {
      await api("/api/settings/llm-status");
      await analyticsApi("/api/overview");
      await analyticsApi("/api/top-words?n=20");
      out(urls);
    })();
    """
    )
    assert run(script) == [
        "/api/settings/llm-status",
        "/api/overview?start=2026-01-01",
        "/api/top-words?n=20&start=2026-01-01",
    ]


def test_analytics_requests_use_the_dated_helper():
    assert not re.search(r"(?<!analytics)\bapi\(", _source("dashboard-analytics.js").replace("analyticsApi(", ""))


# -- hotkey recording ---------------------------------------------------------------------------------


def _recording(tail: str):
    settings = _source("dashboard-settings.js")
    script = (
        FAKE_DOM
        + "page([['rec', 'button'], ['elsewhere']]);"
        + "const hotkeyMods = []; const hotkeyKeySelect = {};"
        + "const hotkeyRecordBtn = document.getElementById('rec'); const showHotkeyStatus = () => {};"
        + "const saveHotkey = async () => {};"
        + _fn(settings, "hotkeyKeyFromEvent")
        + "let activeHotkeyRecording = null;"
        + _fn(settings, "cancelHotkeyRecording")
        + _fn(settings, "startHotkeyRecording")
        + tail
    )
    return run(script)


def test_leaving_the_tab_cancels_the_hotkey_recording_and_frees_the_keys():
    result = _recording(
        """
    startHotkeyRecording();
    const during = hotkeyRecordBtn.classList.contains("recording");
    cancelHotkeyRecording();
    out([during, hotkeyRecordBtn.classList.contains("recording")]);
    """
    )
    assert result == [True, False]


def test_a_click_elsewhere_cancels_the_hotkey_recording():
    result = _recording(
        """
    startHotkeyRecording();
    docListeners.pointerdown.forEach((fn) => fn({ target: hotkeyRecordBtn }));
    const stillOn = hotkeyRecordBtn.classList.contains("recording");
    docListeners.pointerdown.forEach((fn) => fn({ target: document.getElementById("elsewhere") }));
    out([stillOn, hotkeyRecordBtn.classList.contains("recording")]);
    """
    )
    assert result == [True, False]


def test_tab_switch_and_setup_end_cancel_the_recording():
    assert "cancelHotkeyRecording()" in _fn(_source("dashboard-core.js"), "activateTab")
    assert "cancelHotkeyRecording()" in _fn(_source("dashboard-setup.js"), "closeSetup")


# -- shared helpers -----------------------------------------------------------------------------------


def test_the_toggles_and_home_use_the_shared_helpers():
    assert _source("dashboard-settings.js").count('method: "POST"') == 1  # the log folder, which needs the status
    home = _source("dashboard-home.js")
    assert "openUploadDialog()" in home
    assert "showModal" not in home


# -- dictionary ---------------------------------------------------------------------------------------


def test_a_refused_dictionary_entry_shows_its_error():
    core = _source("dashboard-core.js")
    notice = re.search(r"const noticeLines.*?^}\n", core, re.S | re.M).group(0)
    script = (
        FAKE_DOM
        + "page([['dict-form', 'form']]);"
        + "const vocabReveal = () => {}; const loadDictionary = () => {};"
        + "const postSetting = async () => ({ json: async () => ({ ok: false, error: 'Already in the dictionary' }) });"
        + "const STATUS_STATES = ['ok', 'warn', 'down'];"
        + _fn(core, "setStatus")
        + notice
        + _fn(_source("dashboard-settings-vocabulary.js"), "addDictionaryEntry", True)
        + """
    El.prototype.insertAdjacentElement = function (_, el) { document.body.append(el); return el; };
    (async () => {
      const ok = await addDictionaryEntry("a", "b");
      const line = document.body.children.find((c) => c.tagName === "P");
      out([ok, line.textContent, line.hidden, line.classList.contains("down")]);
    })();
    """
    )
    assert run(script) == [False, "Already in the dictionary", False, True]


# -- Max WER and the sample list ----------------------------------------------------------------------


def _explorer(wer_value: str):
    samples = (
        '[{wer: 0, index: 1, id: 1, reference: "a", hypothesis: "a", audio_duration_s: null, latency_s: null},'
        ' {wer: 0.4, index: 2, id: 2, reference: "a", hypothesis: "b", audio_duration_s: 2, latency_s: 0.1}]'
    )
    script = (
        FAKE_DOM
        + "page([['dev-sample-list'], ['dev-sample-count'], ['dev-wer-filter', 'input']]);"
        + "page([['dev-sample-search', 'input']]);"
        + f"document.getElementById('dev-wer-filter').value = '{wer_value}';"
        + "const wordDiff = (a, b) => b;"
        + _fn(_source("dashboard-dev.js"), "renderSampleExplorer")
        + f"const samples = {samples};"
        + """
    renderSampleExplorer(samples);
    renderSampleExplorer(samples);
    const w = document.getElementById("dev-wer-filter");
    out([document.getElementById("dev-sample-count").textContent, Object.keys(w.listeners).length]);
    """
    )
    return run(script)


def test_a_max_wer_of_zero_shows_only_perfect_samples_and_null_durations_render():
    assert _explorer("0")[0] == "1 / 2 samples"


def test_an_empty_max_wer_shows_everything_and_opening_twice_adds_no_listener():
    assert _explorer("") == ["2 / 2 samples", 0]  # handlers are assigned (oninput), never stacked


# -- models card --------------------------------------------------------------------------------------

MODELS = _source("dashboard-settings-models.js")


def test_the_model_switcher_knows_the_model_in_use_before_the_first_switch():
    assert "modelSelect.dataset.prev = current.model" in _fn(MODELS, "loadModelSwitcher", True)


def test_a_refused_switch_shows_the_servers_error():
    handler = MODELS[MODELS.index('modelSelect.addEventListener("change"') : MODELS.index("function showModelStatus")]
    assert "data.error" in handler


def test_rendering_the_llm_card_does_not_refetch_the_status():
    assert "loadLlmStatus" not in _fn(MODELS, "renderLlmModels")


def test_a_new_pull_keeps_its_status_box_when_the_old_hide_timer_fires():
    pull = _fn(MODELS, "pollLlmPull")
    assert "llmPullTimer = null" in pull
    assert "clearTimeout(llmPullHideTimer)" in pull
    assert "if (!llmPullTimer)" in _fn(MODELS, "hideLlmPullLater")
