"""A chat-completion call to a local language model server the user runs.

NexusVox does not ship or load a language model. It sends one request to the
server configured under `[llm]`: Ollama's own API (default, `protocol = "ollama"`,
which can switch a reasoning model's thinking off and keep the model loaded for
`keep_alive`) or any OpenAI-compatible `/chat/completions` endpoint
(`protocol = "openai"`: LM Studio, llama.cpp server, vLLM, a cloud API).
Callers decide what to do on failure; `LlmError` covers every way it can fail.
"""

from __future__ import annotations

import http.client
import json
import logging
import re
import subprocess
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager

from .config import LlmConfig

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT_S = 1.5
# How long a background caller (an upload's cleanup) waits for live requests (dictation, polish) to finish.
BACKGROUND_WAIT_S = 30.0

# Requests in flight that someone is waiting on. A model server answers one request at a time, so a
# background caller starts its next request only while this is zero: a live request then waits behind
# at most the background request already running, not behind a whole queue of them.
_live_lock = threading.Condition()
_live_requests = 0
_local = threading.local()

_FENCE_RE = re.compile(r"^```[\w-]*\n(.*)\n```$", re.DOTALL)
# A reasoning model behind protocol = "openai" has no `think: false` and may put its thinking first.
_THINK_RE = re.compile(r"^<think>.*?</think>", re.DOTALL)


class LlmError(Exception):
    """The model server could not be reached or gave no usable answer."""

    def __init__(self, message: str, *, timed_out: bool = False) -> None:
        super().__init__(message)
        self.timed_out = timed_out


def _request(config: LlmConfig, system: str, user: str) -> tuple[str, dict]:
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    base = config.url.rstrip("/")
    if config.protocol == "openai":
        return f"{base}/chat/completions", {"model": config.model, "messages": messages, "temperature": 0.2}
    return f"{base}/api/chat", {
        "model": config.model,
        "messages": messages,
        "stream": False,
        "think": False,
        "keep_alive": config.keep_alive,
        "options": {"temperature": 0.2},
    }


@contextmanager
def background() -> Iterator[None]:
    """Requests made by this thread inside the block yield to live requests (dictation, polish)."""
    _local.background = True
    try:
        yield
    finally:
        _local.background = False


def complete(config: LlmConfig, system: str, user: str) -> str:
    """The model's reply to one system + user message pair. Raises `LlmError`.

    Inside `background()` the request first waits (up to `BACKGROUND_WAIT_S`) until no live request runs.
    """
    global _live_requests
    if getattr(_local, "background", False):
        with _live_lock:
            _live_lock.wait_for(lambda: _live_requests == 0, timeout=BACKGROUND_WAIT_S)
        return _send(config, system, user)
    with _live_lock:
        _live_requests += 1
    try:
        return _send(config, system, user)
    finally:
        with _live_lock:
            _live_requests -= 1
            _live_lock.notify_all()


def _send(config: LlmConfig, system: str, user: str) -> str:
    url, body = _request(config, system, user)
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    request = urllib.request.Request(url, json.dumps(body).encode("utf-8"), headers)
    try:
        with urllib.request.urlopen(request, timeout=config.timeout_s) as response:
            reply = json.load(response)
        if config.protocol == "openai":
            text = reply["choices"][0]["message"]["content"]
        else:
            text = reply["message"]["content"]
    except (
        urllib.error.URLError,
        http.client.HTTPException,
        TimeoutError,
        OSError,
        ValueError,
        KeyError,
        IndexError,
        TypeError,
    ) as exc:
        timed_out = isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError)
        raise LlmError(f"{type(exc).__name__}: {exc}"[:200], timed_out=timed_out) from exc
    if not isinstance(text, str) or not text.strip():
        raise LlmError("the model returned no text")
    return text.strip()


def clean_reply(reply: str) -> str:
    """Strip a leading thinking block and a code fence the model wrapped around its answer."""
    reply = _THINK_RE.sub("", reply.strip()).strip()
    m = _FENCE_RE.match(reply)
    return m.group(1).strip() if m else reply


def warm_up(config: LlmConfig) -> None:
    """Load the model in the background, so the first real request does not wait 10-50 s for
    it - auto cleanup gives up after a few seconds. Never raises; a failure is only logged."""

    def run() -> None:
        try:
            complete(config, "Reply with OK.", "OK")
            logger.info("Language model %s is loaded", config.model)
        except LlmError as exc:
            logger.info("Language model warm-up at %s failed (%s)", config.url, exc)

    threading.Thread(target=run, daemon=True, name="llm-warm-up").start()


def is_reachable(config: LlmConfig) -> bool:
    """Whether anything answers at the server address (any HTTP status counts), probed briefly."""
    try:
        urllib.request.urlopen(config.url, timeout=_PROBE_TIMEOUT_S).close()
    except urllib.error.HTTPError as exc:
        exc.close()
        return True
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError, ValueError):
        return False
    return True


# ── Model catalogue and Ollama's model list ───────────────────────────────────────────────────────
# The models the dashboard offers first, with their download size in GB (docs/llm-cpu-test.md).
RECOMMENDED_MODELS: tuple[tuple[str, float], ...] = (
    ("gemma4:e2b", 4.6),
    ("gemma4:e4b", 6.6),
    ("gemma4:12b-it-qat", 7.2),
)
_LIST_TIMEOUT_S = 3.0


def gpu_vram_gb() -> float | None:
    """Total VRAM of the first NVIDIA GPU in GB, or None without one (read once through nvidia-smi)."""
    global _vram_probed, _vram_gb
    if not _vram_probed:
        _vram_probed = True
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            ).stdout
            _vram_gb = float(out.splitlines()[0].strip()) / 1024
        except (OSError, subprocess.SubprocessError, ValueError, IndexError):
            _vram_gb = None
    return _vram_gb


_vram_probed = False
_vram_gb: float | None = None


def recommended_model(cuda: bool, vram_gb: float | None) -> str:
    """The recommended model for this PC (docs/llm-cpu-test.md): no CUDA GPU or 4-8 GB VRAM -> e2b,
    12 GB and up -> e4b. Between 8 and 12 GB the speech model leaves too little room for e4b: e2b."""
    if cuda and vram_gb is not None and vram_gb >= 12:
        return "gemma4:e4b"
    return "gemma4:e2b"


def installed_models(config: LlmConfig) -> list[dict] | None:
    """Ollama's installed models as `{"name", "size_gb"}`, or None when they cannot be listed
    (server down, or a protocol without Ollama's `/api/tags`)."""
    if config.protocol != "ollama":
        return None
    try:
        with urllib.request.urlopen(f"{config.url.rstrip('/')}/api/tags", timeout=_LIST_TIMEOUT_S) as response:
            models = json.load(response)["models"]
        return [
            {"name": str(m["name"]), "size_gb": round(float(m.get("size", 0)) / 1e9, 1)}
            for m in models
            if isinstance(m, dict) and m.get("name")
        ]
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError, ValueError, KeyError, TypeError):
        return None


def model_is_installed(name: str, installed: list[dict]) -> bool:
    """Ollama treats `gemma4:e4b` and `gemma4:e4b:latest`-style tags as the same only for `:latest`."""
    names = {m["name"] for m in installed}
    return name in names or (":" not in name and f"{name}:latest" in names)


def build_model_list(config: LlmConfig, cuda: bool, vram_gb: float | None) -> dict:
    """What the dropdown shows: `recommended` (with installed flag, size, and the hardware mark),
    `other` (installed models outside the recommended list; always includes the saved value) and `reachable`."""
    installed = installed_models(config)
    reachable = installed is not None
    best = recommended_model(cuda, vram_gb)
    have = installed or []
    recommended = [
        {
            "name": name,
            "size_gb": size,
            "installed": model_is_installed(name, have),
            "recommended_here": name == best,
        }
        for name, size in RECOMMENDED_MODELS
    ]
    known = {name for name, _ in RECOMMENDED_MODELS}
    other = [{"name": m["name"], "size_gb": m["size_gb"], "installed": True} for m in have if m["name"] not in known]
    if config.model not in known and config.model not in {m["name"] for m in other}:
        # A saved model that is not in the lists: keep showing it, installed only if the server said so.
        other.insert(0, {"name": config.model, "size_gb": 0.0, "installed": False if reachable else None})
    return {"reachable": reachable, "recommended": recommended, "other": other, "protocol": config.protocol}


def model_status(config: LlmConfig) -> dict:
    """The status line's data: `state` is `down` (no server), `missing` (server up, model not installed),
    `ok`, or `unknown` (a server without a model list - only reachability is known)."""
    if not is_reachable(config):
        return {"state": "down"}
    installed = installed_models(config)
    if installed is None:
        return {"state": "unknown"}
    return {"state": "ok" if model_is_installed(config.model, installed) else "missing"}


# ── Pulling a model ───────────────────────────────────────────────────────────────────────────────
class ModelPull:
    """One `/api/pull` download at a time, run in a thread. `state()` is polled by the dashboard.

    status: idle -> pulling -> done | error | cancelled."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._model = ""
        self._status = "idle"
        self._detail = ""
        self._completed = 0
        self._total = 0
        self._error = ""
        self._cancel = threading.Event()
        self._response = None

    def state(self) -> dict:
        with self._lock:
            return {
                "status": self._status,
                "model": self._model,
                "detail": self._detail,
                "completed": self._completed,
                "total": self._total,
                "error": self._error,
            }

    def start(self, config: LlmConfig, model: str) -> bool:
        """False when a pull is already running."""
        with self._lock:
            if self._status == "pulling":
                return False
            self._model, self._status, self._detail, self._error = model, "pulling", "starting", ""
            self._completed = self._total = 0
            self._cancel = threading.Event()
        threading.Thread(target=self._run, args=(config, model, self._cancel), daemon=True, name="llm-pull").start()
        return True

    def cancel(self) -> None:
        with self._lock:
            if self._status != "pulling":
                return
            self._cancel.set()
            response = self._response
        if response is not None:
            try:
                response.close()
            except Exception:  # noqa: BLE001 - closing only unblocks the reader
                pass

    def _finish(self, status: str, error: str = "") -> None:
        with self._lock:
            self._status, self._error, self._response = status, error, None

    def _run(self, config: LlmConfig, model: str, cancel: threading.Event) -> None:
        url = f"{config.url.rstrip('/')}/api/pull"
        request = urllib.request.Request(
            url, json.dumps({"model": model, "stream": True}).encode("utf-8"), {"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                with self._lock:
                    self._response = response
                for line in response:
                    if cancel.is_set():
                        break
                    if not line.strip():
                        continue
                    event = json.loads(line)
                    if event.get("error"):
                        self._finish("error", str(event["error"])[:200])
                        return
                    with self._lock:
                        self._detail = str(event.get("status", ""))
                        if event.get("total"):
                            self._completed = int(event.get("completed", 0))
                            self._total = int(event["total"])
                if cancel.is_set():
                    self._finish("cancelled")
                    return
                if self._detail == "success":
                    self._finish("done")
                else:
                    self._finish("error", "the download ended early")
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError, ValueError) as exc:
            if cancel.is_set():
                self._finish("cancelled")
            else:
                self._finish("error", f"{type(exc).__name__}: {exc}"[:200])


model_pull = ModelPull()
