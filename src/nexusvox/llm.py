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
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

from .config import LLM_E4B_MIN_VRAM_GB, LlmConfig, LlmServer

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
# A reasoning model behind protocol = "openai" has no `think: false` and may put its thinking first. Only a block that
# the reply opens itself is cut: a `</think>` in the middle of the text is the user's own (HTML, prompt notes).
_THINK_RE = re.compile(r"^<think>.*?</think>", re.DOTALL)
# Servers that answered the request for no thinking (`chat_template_kwargs`) with a client error: not asked again.
_NO_THINKING_REJECTED: set[str] = set()


class LlmError(Exception):
    """The model server could not be reached or gave no usable answer."""

    def __init__(self, message: str, *, timed_out: bool = False) -> None:
        super().__init__(message)
        self.timed_out = timed_out


def _request(config: LlmConfig, system: str, user: str) -> tuple[str, dict]:
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    base = config.url.rstrip("/")
    if config.protocol == "openai":
        body: dict = {"model": config.model, "messages": messages, "temperature": 0.2}
        if base not in _NO_THINKING_REJECTED:
            # llama.cpp's server reads this to keep a reasoning model from thinking first; others ignore it.
            body["chat_template_kwargs"] = {"enable_thinking": False}
        return f"{base}/chat/completions", body
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


_GRACE_S = 1.0


def _post(request: urllib.request.Request, timeout_s: float) -> dict:
    """The JSON reply to *request*, or `TimeoutError` once *timeout_s* seconds have passed in all.

    The socket timeout only limits each single wait for bytes. The body is therefore read in pieces and the deadline
    is checked between them, so a server that trickles its answer is dropped (the connection closes, the server stops
    generating) and cannot hold the caller on. The request runs in its own thread and the caller gives up a moment
    after the deadline in any case, for a connect that never returns."""
    deadline = time.monotonic() + timeout_s
    outcome: dict = {}

    def run() -> None:
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                chunks = []
                while chunk := response.read(65536):
                    chunks.append(chunk)
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"no complete answer within {timeout_s:g} s")
                outcome["reply"] = json.loads(b"".join(chunks))
        except BaseException as exc:  # noqa: BLE001 - handed to the caller, who raises it there
            outcome["error"] = exc

    worker = threading.Thread(target=run, daemon=True, name="llm-request")
    worker.start()
    worker.join(timeout_s + _GRACE_S)
    if worker.is_alive():
        raise TimeoutError(f"no answer within {timeout_s:g} s")
    if "error" in outcome:
        raise outcome["error"]
    return outcome["reply"]


def _rejects_thinking_flag(exc: urllib.error.HTTPError) -> bool:
    """Whether an HTTP error says the server does not accept `chat_template_kwargs`. Only a 400/422 that names the
    field counts: a too long prompt or a wrong model name answers 400 as well and says nothing of the sort."""
    if exc.code not in (400, 422):
        return False
    try:
        detail = exc.read(4096).decode("utf-8", "replace")
    except (OSError, http.client.HTTPException):
        return False
    return "chat_template_kwargs" in detail


def _log_request(config: LlmConfig, started: float, outcome: str) -> None:
    """One INFO line per request to the model server: where, which model, how long and how it ended. Never the text,
    and never the API key or a password in the address."""
    parts = urllib.parse.urlsplit(config.url)
    where = f"{parts.scheme}://{parts.hostname or ''}{f':{parts.port}' if parts.port else ''}{parts.path.rstrip('/')}"
    logger.info(
        "LLM request: %s (%s), model %s, %d ms, %s",
        where,
        config.protocol,
        config.model,
        round((time.monotonic() - started) * 1000),
        outcome,
    )


def _send(config: LlmConfig, system: str, user: str) -> str:
    started = time.monotonic()
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    deadline = time.monotonic() + config.timeout_s  # one deadline for the request and a retry together
    try:
        url, body = _request(config, system, user)
        try:
            reply = _post(urllib.request.Request(url, json.dumps(body).encode("utf-8"), headers), config.timeout_s)
        except urllib.error.HTTPError as exc:
            if config.protocol != "openai" or "chat_template_kwargs" not in body or not _rejects_thinking_flag(exc):
                raise
            # A strict server refuses the field it does not know: ask again without it, and remember.
            exc.close()
            _NO_THINKING_REJECTED.add(config.url.rstrip("/"))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"no answer within {config.timeout_s:g} s") from exc
            url, body = _request(config, system, user)
            reply = _post(urllib.request.Request(url, json.dumps(body).encode("utf-8"), headers), remaining)
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
        _log_request(config, started, "timeout" if timed_out else f"error: {_short_reason(exc)}")
        raise LlmError(f"{type(exc).__name__}: {exc}"[:200], timed_out=timed_out) from exc
    stripped = False
    if config.protocol == "openai" and isinstance(text, str):
        answer = _THINK_RE.sub("", text.strip())  # thinking that came anyway is not part of the answer
        stripped, text = answer != text.strip(), answer
    if not isinstance(text, str) or not text.strip():
        _log_request(config, started, "error: the model returned no text")
        raise LlmError("the model returned no text")
    _log_request(config, started, "ok, thinking stripped" if stripped else "ok")
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


def recommended_model(cuda: bool, vram_gb: float | None) -> str:
    """The recommended model for this PC (docs/llm-cpu-test.md): 12 GB VRAM and up -> e4b, else e2b."""
    if cuda and vram_gb is not None and vram_gb >= LLM_E4B_MIN_VRAM_GB - 0.5:
        return "gemma4:e4b"
    return "gemma4:e2b"


def installed_models(config: LlmConfig) -> list[dict] | None:
    """Ollama's installed models as `{"name", "size_gb"}`, or None when they cannot be listed
    (server down, or a protocol without Ollama's `/api/tags`)."""
    if config.protocol != "ollama":
        return None
    listing = server_models(LlmServer(config.url, "ollama", api_key=config.api_key))
    return listing["models"] if listing["reachable"] else None


# ── Custom servers: detection and model lists ─────────────────────────────────────────────────────
def _short_reason(exc: BaseException) -> str:
    """Why a server did not answer, in a few words for the dropdown."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}, check the API key" if exc.code in (401, 403) else f"HTTP {exc.code}"
    reason = getattr(exc, "reason", None)
    inner = reason if isinstance(reason, BaseException) else exc
    if isinstance(inner, TimeoutError):
        return "timed out"
    if isinstance(inner, ConnectionRefusedError):
        return "connection refused"
    if isinstance(inner, (ValueError, KeyError, TypeError)):
        return "not a model server"
    if isinstance(reason, str):
        return reason[:60]
    return type(inner).__name__


def _get_json(url: str, api_key: str, timeout_s: float) -> object:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout_s) as response:
        return json.load(response)


def server_models(server: LlmServer, timeout_s: float = _LIST_TIMEOUT_S) -> dict:
    """What one server offers: `{"reachable": True, "models": [{"name", "size_gb"}]}`, or
    `{"reachable": False, "models": [], "error": "<short reason>"}`. Never raises.

    Ollama lists its installed models at `/api/tags`, an OpenAI-compatible server at `/models` (`url` up to `/v1`)."""
    base = server.url.rstrip("/")
    try:
        if server.protocol == "ollama":
            payload = _get_json(f"{base}/api/tags", server.api_key, timeout_s)
            models = [
                {"name": str(m["name"]), "size_gb": round(float(m.get("size", 0)) / 1e9, 1)}
                for m in payload["models"]
                if isinstance(m, dict) and m.get("name")
            ]
        else:
            payload = _get_json(f"{base}/models", server.api_key, timeout_s)
            models = [
                {"name": str(m["id"]), "size_gb": 0.0} for m in payload["data"] if isinstance(m, dict) and m.get("id")
            ]
    except (
        urllib.error.URLError,
        http.client.HTTPException,
        TimeoutError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
    ) as exc:
        return {"reachable": False, "models": [], "error": _short_reason(exc)}
    return {"reachable": True, "models": models}


def detect_server(url: str, api_key: str = "", timeout_s: float = _LIST_TIMEOUT_S) -> LlmServer:
    """Find out which API the server at *url* speaks and return it as an `LlmServer` (without a name).

    *url* is what the user typed: `http://host:port`, with or without `/v1`. Ollama's `/api/tags` is asked first
    (for an address without `/v1`), then the OpenAI-compatible `/v1/models`, then `/models`. The `url` of the result
    is what `_request` needs: the Ollama base, or the address up to `/v1`. Raises `LlmError` with a short reason."""
    url = str(url or "").strip()
    if url and "://" not in url:
        url = f"http://{url}"
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise LlmError("enter an address like http://127.0.0.1:8081")
    root = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))
    if root.endswith("/v1"):
        base = root[: -len("/v1")]
        # Ollama answers /v1/models too, but it is asked at its own API first: it can then switch thinking off
        # and keep the model loaded, which the OpenAI-compatible API cannot.
        candidates = [("ollama", base), ("openai", root)]
    else:
        candidates = [("ollama", root), ("openai", f"{root}/v1"), ("openai", root)]
    probes = [LlmServer(address, protocol, api_key=api_key) for protocol, address in candidates]
    with ThreadPoolExecutor(max_workers=len(probes)) as pool:  # a dead address costs one timeout, not one per candidate
        results = list(pool.map(lambda probe: server_models(probe, timeout_s), probes))
    for probe, result in zip(probes, results, strict=True):
        if result["reachable"]:
            return probe
    reasons = [result["error"] for result in results]
    # Nothing answered at all: say why. Something answered, but with no model list: say that.
    down = [r for r in reasons if r in ("connection refused", "timed out")]
    if len(down) == len(reasons):
        raise LlmError(down[0])
    auth = [r for r in reasons if "API key" in r]
    raise LlmError(auth[0] if auth else "not an Ollama or OpenAI-compatible server")


# Ollama reports some models (gemma4:e2b: 0.22 GB for 4.6 GB on disk) far too small in /api/ps. A reported size
# below this share of the size on disk is not believed.
PLAUSIBLE_SIZE_RATIO = 0.5


def loaded_model(config: LlmConfig, name: str, disk_gb: float | None = None) -> dict | None:
    """Where Ollama holds `name` right now (`/api/ps`), or None when it is not loaded or the server cannot say.

    Plausible figures: `{"plausible": True, "vram_gb", "ram_gb"}` (`size_vram` is the VRAM part, the rest is RAM).
    When `size` is under `PLAUSIBLE_SIZE_RATIO` of the size on disk the figures are dropped:
    `{"plausible": False, "on_gpu": bool}` - the caller shows an estimate and at most "GPU"."""
    if config.protocol != "ollama":
        return None
    try:
        with urllib.request.urlopen(f"{config.url.rstrip('/')}/api/ps", timeout=_LIST_TIMEOUT_S) as response:
            running = json.load(response)["models"]
        for m in running:
            if isinstance(m, dict) and name in (m.get("name"), m.get("model")):
                total, vram = float(m.get("size", 0)), float(m.get("size_vram", 0))
                if disk_gb and total / 1e9 < disk_gb * PLAUSIBLE_SIZE_RATIO:
                    return {"plausible": False, "on_gpu": vram > 0}
                return {
                    "plausible": True,
                    "vram_gb": round(vram / 1e9, 1),
                    "ram_gb": round(max(total - vram, 0) / 1e9, 1),
                }
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError, ValueError, KeyError, TypeError):
        return None
    return None


def model_is_installed(name: str, installed: list[dict]) -> bool:
    """Ollama treats `gemma4:e4b` and `gemma4:e4b:latest`-style tags as the same only for `:latest`."""
    names = {m["name"] for m in installed}
    return name in names or (":" not in name and f"{name}:latest" in names)


def build_model_list(config: LlmConfig, cuda: bool, vram_gb: float | None) -> dict:
    """What the dropdown shows for the server in use: `recommended` (Ollama only: with installed flag, size, and the
    hardware mark), `other` (the models outside the recommended list; always includes the saved value) and `reachable`.
    An OpenAI-compatible server lists its own models; they count as installed, and a saved name the server does not
    list stays `installed: None` because such servers often accept any name."""
    if config.protocol == "ollama":
        installed = installed_models(config)
    else:
        listing = server_models(LlmServer(config.url, config.protocol, api_key=config.api_key))
        installed = listing["models"] if listing["reachable"] else None
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
        for name, size in (RECOMMENDED_MODELS if config.protocol == "ollama" else ())
    ]
    known = {m["name"] for m in recommended}
    other = [{"name": m["name"], "size_gb": m["size_gb"], "installed": True} for m in have if m["name"] not in known]
    if config.model not in known and config.model not in {m["name"] for m in other}:
        # A saved model that is not in the lists: keep showing it, installed only if the server said so.
        missing = False if reachable and config.protocol == "ollama" else None
        other.insert(0, {"name": config.model, "size_gb": 0.0, "installed": missing})
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


# ── Installing and starting Ollama ────────────────────────────────────────────────────────────────
def ollama_executable() -> Path | None:
    """`ollama.exe` of an installed Ollama: the default install folder (`%LOCALAPPDATA%\\Programs\\Ollama`), else
    whatever is on PATH. None when Ollama is not installed. Looks at the disk only; asks no server."""
    local = os.environ.get("LOCALAPPDATA")
    if local:
        default = Path(local) / "Programs" / "Ollama" / "ollama.exe"
        if default.is_file():
            return default
    found = shutil.which("ollama")
    return Path(found) if found else None


def start_ollama() -> None:
    """Start the installed Ollama the way the Start menu does (its tray app, which runs the server), or
    `ollama serve` when there is no app next to `ollama.exe`. Returns at once; the caller waits for the server.
    Never downloads or pulls anything. Raises `LlmError` when Ollama is not installed or cannot be started."""
    exe = ollama_executable()
    if exe is None:
        raise LlmError("Ollama is not installed")
    tray_app = exe.with_name("ollama app.exe")
    command = [str(tray_app)] if tray_app.is_file() else [str(exe), "serve"]
    flags = sum(
        getattr(subprocess, name, 0) for name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP", "CREATE_NO_WINDOW")
    )
    try:
        subprocess.Popen(  # noqa: S603 - a fixed program found on this PC, no user input in the command
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            close_fds=True,
        )
    except OSError as exc:
        raise LlmError(f"could not start Ollama: {exc}"[:200]) from exc
    logger.info("Started Ollama (%s)", command[0])


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
