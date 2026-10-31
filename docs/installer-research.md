# Packaging NexusVox as a Windows installer

Status: research only, 2026-10-31 (issue #149). No packaging code was written and nothing was built.
Free release is planned for 2026-11-13, 13 days after this document.

Method: sizes marked **measured** come from the maintainer's development venv and Hugging Face cache on
2026-10-31. Everything marked **published** comes from the linked pages. Everything marked **estimate**
is my reasoning and has to be confirmed in the spike (ticket 1 below). Facts I could not confirm are
marked **unverified**; they are collected under [Open questions](#open-questions).

## Recommendation

Ship 1.0 as **PyInstaller one-folder (onedir) build, wrapped in a per-user Inno Setup installer, signed
with Azure Artifact Signing**. The installer carries the app and the CPU runtimes only. The speech model
is downloaded on first start. GPU acceleration and the language model are optional downloads after 1.0,
not part of it.

The main trade-off: **PyInstaller is the fastest route to a working installer by 13 November, but it
is the packager with the longest history of antivirus false positives, and signing does not remove the
SmartScreen warning on day one.** Nuitka is the alternative with fewer reported flags and some code
protection, at the price of much longer builds and more risk with native packages (`onnxruntime`,
`ctranslate2`, `comtypes`, `pynput`). With 13 days left, the plan is PyInstaller for 1.0 and a Nuitka
comparison as a non-blocking spike for 1.1 (ticket 11).

| Question | Answer |
|---|---|
| Packager | PyInstaller 6.x, onedir, windowed (no console), custom bootloader build if flags show up |
| Installer | Inno Setup, per-user (`PrivilegesRequired=lowest`), `%LOCALAPPDATA%\Programs\NexusVox` |
| Signing | Azure Artifact Signing, start identity validation **this week** |
| CPU users | Parakeet TDT v3 int8 in-process, model downloaded on first start (640 MB) |
| NVIDIA users, 1.0 | Same CPU path. Docker path stays for people who already use it |
| NVIDIA users, later | Optional "GPU pack" with `onnxruntime-gpu` and the NVIDIA runtime DLLs |
| Language model (Polish, Cleanup) | Not bundled in 1.0. Later: optional download of `llama-server` (Vulkan build) |
| Logs | Already in `%LOCALAPPDATA%\NexusVox\logs`. Missing: "Copy diagnostics" |
| Updates | "New version available" from the GitHub releases API; manual download |
| Autostart | Per-user `HKCU\...\Run` entry, a checkbox in the installer and in Settings |

## What the app needs from a package

Read from the code on this branch.

| Fact | Where | Consequence for packaging |
|---|---|---|
| `config.toml` is read from the **working directory** (`DEFAULT_CONFIG_PATH = Path("config.toml")`) | `config.py:12` | Installed app has no meaningful working directory. Needs a fixed config folder, `%APPDATA%\NexusVox`. |
| Database path and `audio/` are relative paths by default | `config.example.toml` | Same: they must resolve under the data folder when frozen. |
| The config template is found through `__file__` | `config.py:18` | Works in PyInstaller if the file is listed as data. |
| `DOCKER_DIR` is `parent.parent.parent / "docker"` | `docker_ctl.py:14` | Points outside the package in a frozen build. The Docker path needs either the compose files as data or a "not available in the installed app" message. |
| Logging already writes a rotating file to `%LOCALAPPDATA%\NexusVox\logs`, keeps 7 days, level from `[general].log_level` | `logsetup.py` | Done. |
| "Open log folder" already exists | `dashboard/api.py:462` | Done. "Copy diagnostics" does not exist. |
| Parakeet CPU downloads its model on first use and reports `idle`/`downloading`/`loading`/`ready`/`error` to the dashboard, with a background preload at start | `transcribers/local_parakeet.py` | The mechanism exists. What is missing is a first-run screen that shows it as a progress bar and explains the 640 MB. |
| Whisper models download through `faster-whisper` on first use | `transcribers/local_whisper.py` | Same, no progress reporting found. |
| Tray via `pystray`, entry point `python -m nexusvox` | `tray.py`, `__main__.py` | Windowed build needs `console=False`. All `print` and stderr use has to survive having no console. |
| No autostart, no update check | grep found none | New work. |

## Sizes

**Measured, development venv** (`site-packages` 661 MB in total):

| Package | MB | Note |
|---|---|---|
| `lingua` (language detector) | 291 | One `.pyd` with every language model. `lang_detect.py` builds the detector for **English and German only**. |
| `ctranslate2` | 60 | CPU build, for Whisper |
| `onnxruntime` | 45 | CPU build, for Parakeet |
| `numpy` | 34 | |
| `sqlalchemy` | 19 | |
| `PIL` | 16 | |
| everything else | ~196 | |

**Measured, models** (Hugging Face cache): Parakeet TDT 0.6B v3 int8 ONNX **640 MB** (encoder 652 MB
file); Whisper large-v3-turbo CT2 **1547 MB**. Both are under the 2 GB limit; Turbo only just.

**Estimate, installer** (to be measured in the spike): the frozen app should land near 600 to 700 MB
unpacked and roughly 250 to 400 MB as an Inno LZMA installer, because `lingua` is a compiled model blob
that may compress poorly. **The single biggest saving is `lingua`**: it is 44 percent of site-packages
for a two-language decision. Replacing it with a small German/English detector would cut the installer
by a similar share (ticket 9).

| Variant | Download | After first start | Notes |
|---|---|---|---|
| CPU, installer only | est. 250-400 MB | + 640 MB model | The 1.0 target |
| NVIDIA GPU pack | + est. 1 to 1.5 GB (**unverified**) | | `onnxruntime-gpu` Windows wheel is 221 MB for CUDA 12.8 (**published**, [onnxruntime commit table](https://app.semanticdiff.com/gh/microsoft/onnxruntime/commit/8fceca0d853090b0cde8ce939802a4f8d79f0800)); cuDNN and cuBLAS DLLs come on top and I found no size. Must stay under 2 GB. |
| Docker/GPU path (today) | multi-GB image, plus Docker Desktop | | Power-user path, stays as is |
| Language model | + 2 to 4 GB (GGUF, see `llm-low-gpu-research.md`) | | Optional, over the 2 GB cap, so it is a separate user decision, not part of any bundle |

## Packaging options compared

| Option | Size | AV risk | Build effort for 13 Nov | Tray, no console | Autostart | Verdict |
|---|---|---|---|---|---|---|
| **PyInstaller onedir** + Inno Setup | as above | Medium; mitigated by signing and a rebuilt bootloader | Low: hooks exist for `onnxruntime`, `numpy`, `pystray` | `console=False` | Registry key | **Recommended for 1.0** |
| PyInstaller onefile | same, plus a temp unpack on every start | Higher: it unpacks a payload to a temp folder, which looks like a dropper | Low | same | same | Reject. Slower start (unpacks hundreds of MB each launch) and worse AV behaviour |
| **Nuitka** standalone | similar | Reported lower, only by vendor blogs (unverified) | Medium to high: C compile of a large dependency tree, slow builds, native-package quirks | `--windows-console-mode=disable` | same | Spike for 1.1, see below |
| Briefcase (BeeWare) | similar | Uses stock Python embeddable; not tested here | Medium: targets MSI (WiX), expects its own project layout and app metadata | yes | via MSI | No clear gain over PyInstaller+Inno for a Flask+pystray app |
| Embedded Python + Inno Setup (no freezer) | smallest source of truth: ship Python's own signed `python.exe` plus a venv | Lowest: the interpreter is the PSF-signed binary, our code stays `.py` | Medium: hand-built wheel layout, a launcher `.exe`, update logic | `pythonw.exe` | registry | Best AV story, worst for Pro licence protection (plain `.py` files) and for GPU upgrades. Keep as the fallback if PyInstaller flags cannot be cleared |
| MSIX | similar | Low once signed | High: needs signing, package identity, and a test that `pynput` low-level hooks and `SendInput` still reach other apps from a packaged process (**unverified**) | yes | `StartupTask` in the manifest | Not for 1.0. Revisit with a Store listing if SmartScreen stays a problem |

Why not `pip install` as the user path: the README already documents it, it needs Python and a working
`pip`, and the maintainer's own machine shows how easily that goes wrong (bare `pip` resolves to a
different Python). The installer is for people who do not know what a venv is.

## Antivirus false positives

What is known (**published**):

- PyInstaller's bootloader is a prebuilt `.exe` that thousands of programs share, and scanners keep
  flagging it. A long-running PyInstaller issue reports that the bootloader's PE checksum is one signal
  scanners use: rebuilding `runw.exe` cut one case from 12 flagging engines to 5
  ([pyinstaller#4343](https://github.com/pyinstaller/pyinstaller/issues/4343),
  [#5854](https://github.com/pyinstaller/pyinstaller/issues/5854)). Defender flagged one 4.3 release
  as `Trojan:Win32/Wacatac.B!ml` in both onefile and onedir mode.
- Onefile extracts a payload into a temp directory on every start. That resembles a dropper and is the
  usual reason it is flagged more often than onedir. Onedir avoids it and starts faster.
- PyInstaller's maintainers say they cannot fix a false positive: the advice is to report it to the
  antivirus vendor, rebuild the bootloader from source, and code-sign.
- Nuitka produces native code and has no unpack step in standalone mode, which is why packaging blogs
  say it is flagged less. Those posts are written by a vendor that sells a Nuitka-based tool and the
  figures are self-reported, so I treat them as a hypothesis, not a result. Nuitka's own onefile mode
  unpacks like PyInstaller's and can be flagged too.

What signing changes:

- A valid signature gives a **verified publisher name** in the SmartScreen and UAC dialogs and lets you
  report and track the file against one identity. Many engines also weigh signed binaries more kindly.
- It does **not** give instant SmartScreen trust. Microsoft states that Artifact Signing, formerly
  Trusted Signing, does not provide instant SmartScreen reputation, and that EV certificates no longer
  bypass it ([code signing options](https://learn.microsoft.com/windows/apps/package-and-deploy/code-signing-options),
  [SmartScreen reputation](https://learn.microsoft.com/windows/apps/package-and-deploy/smartscreen-reputation)).
  Reputation builds with downloads, so expect "Windows protected your PC" on the first days and
  document "More info, Run anyway" on the download page.

What to do for 1.0 (cheap, in order):

1. Onedir, never onefile.
2. Build the PyInstaller bootloader from source in CI (needs the MSVC toolchain, available on
   `windows-latest`).
3. Sign the app `.exe`, the bundled `.dll`/`.pyd` files that are ours, and the installer.
4. Upload every release candidate to VirusTotal and submit any engine that flags it through that
   engine's false-positive form. Submit the installer to Microsoft's Defender analysis portal before
   the public announcement.
5. Keep the embedded-Python variant as the fallback if a major engine (Defender) keeps flagging.

Acceptance rule for the release: **zero detections from Defender, and a short list of named minor
engines in the release notes if any remain.** Zero across 70 engines is not a realistic bar.

## Code signing

| Option | Cost | Notes |
|---|---|---|
| **Azure Artifact Signing** (renamed from Trusted Signing) | **$9.99 / month** for up to 5,000 signatures, one certificate profile (third-party report, [devclass](https://www.devclass.com/security/2026/01/14/code-signing-windows-apps-may-be-easier-and-more-secure-with-new-azure-artifact-service/4079554); Microsoft's pricing page showed placeholders when I looked) | Generally available in the US, Canada and Europe. Needs identity validation of the organisation. Can sign from CI. |
| OV code-signing certificate from a CA | **n/r**; commonly low hundreds of euros per year | Hardware-token or cloud-HSM rules since 2023 make CI signing awkward |
| EV certificate | **n/r**, more than OV | Microsoft says paying the EV premium only to avoid SmartScreen is no longer justified |
| Unsigned | 0 | Publisher shows "Unknown publisher"; Defender is stricter on unsigned unpacking code |

Recommendation: Artifact Signing, from CI, with the signing step in the release workflow. **Open the
identity validation now.** I could not confirm how long validation takes for a GmbH, nor whether
NightShift AI GmbH meets the organisation-history rule that I remember from the preview (**unverified**).
If validation is not through by 2026-11-10, ship 1.0 unsigned with a clear note in the README and the
release text, and sign from the next release. An unsigned 1.0 is acceptable for a first free release; a
delayed 1.0 for a certificate is not.

Reputation is tracked from downloads, so keep the installer URL stable (one GitHub release asset name)
and do not re-upload builds under the same version.

## First start, models, updates, uninstall

**First start.** The installer does not contain a model. On first launch the dashboard opens on a
short setup screen: detected hardware, the model it will download (Parakeet v3 int8, 640 MB, from
Hugging Face), a progress bar fed by the existing `load_status` states, and a "Retry" button. Downloads
resume (the Hugging Face client resumes partial files). If the download cannot complete, the app stays
usable for everything that does not need a model. The default model folder is the Hugging Face cache
(`%USERPROFILE%\.cache\huggingface`); for an installed app it should move under `%LOCALAPPDATA%\NexusVox\models`
so the uninstaller can offer to delete it (ticket 6).

**Updates.** At start, and once a day, call
`https://api.github.com/repos/vzwrt13/NexusVox/releases/latest`, compare with the installed version, and
show "Version x.y.z is available" in the tray menu and on the dashboard with a link to the release. The
check is off by default if the maintainer prefers (it contacts GitHub), and it sends no identifiers.
Silent in-place update is a later step: the installer is already per-user and Inno can run
`/VERYSILENT /CLOSEAPPLICATIONS`, so the app can download, verify the signature and hand over.

**Uninstall.** Inno writes an uninstaller and an Add/Remove Programs entry. It removes the program
folder and the autostart key. A final page asks, defaulting to **keep**, whether to delete user data
(config, history database, stored audio, logs) and the model folder.

**Autostart.** `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` value `NexusVox` pointing at the
installed `.exe`. An installer task ("Start NexusVox when I sign in") sets it; a Settings toggle changes
it later. No admin rights, no scheduled task. Note that a low-level hook cannot see keystrokes in
windows that run as administrator; unchanged from today.

**Diagnostics.** Add "Copy diagnostics" next to "Open log folder": app version, Windows build, Python
and package versions, CPU/GPU, selected model and its status, config with secrets (`api_key`,
`remote_mic.token`) removed, last 200 log lines. The existing `Text` wrapper already shortens dictated
text in the file log, so the log tail is safe by default.

## Parakeet on GPU without Docker

**Yes in principle, not recommended for 1.0.**

- `onnx-asr` supports CUDA, TensorRT, DirectML and others through ONNX Runtime execution providers.
  Its own table shows NeMo Parakeet at **36x real time on a 9800X3D CPU**, 57x on a T4 with CUDA and
  **320x on an RTX 5070 Ti with TensorRT** ([onnx-asr README](https://github.com/istupakov/onnx-asr)).
- NexusVox currently pins the provider list to CPU in `local_parakeet.py` (`providers=["CPUExecutionProvider"]`
  for both model and VAD). A GPU path is a provider list change plus a runtime that actually loads.
- That second part is the risk. On the maintainer's RTX 5070 Ti, `onnxruntime-gpu` 1.23 failed in cuDNN
  and **silently fell back to CPU**; 1.30+ (CUDA 13) was the working direction (memory note from
  2026-10-20, not re-tested here). The README of `onnx-asr` also warns that `onnxruntime` 1.24.1 has
  compatibility problems. A GPU pack therefore needs a version matrix per GPU generation, and a check
  that the CUDA provider is really active, not just requested.
- `onnxruntime` and `onnxruntime-gpu` share one module name and cannot sit in the same environment. In a
  frozen app the GPU pack must be a separate folder that is put first on the DLL search path before
  `onnxruntime` is imported. This is doable but needs a proof of concept.
- For dictation the gain is small. CPU Parakeet at ~36x turns a 10-second dictation into about
  0.3 seconds; the GPU matters for long files and meetings. Speaker detection (Sortformer diarization)
  is a NeMo model in the Docker image and has no ONNX path, so Docker stays for that.
- Whisper on GPU in-process (`ctranslate2`) needs CUDA 12 and cuDNN 9 DLLs next to the process; the
  published guidance is to put the cuBLAS and cuDNN libraries on `PATH` ([faster-whisper](https://pypi.org/project/faster-whisper/)).
  I found nothing on `ctranslate2` and RTX 50-series (Blackwell) support (**unverified**).
- NVIDIA licence: redistributing the CUDA/cuDNN DLLs inside a downloadable pack needs a check against
  the CUDA EULA and cuDNN SLA redistribution lists (**unverified**). Downloading them from NVIDIA's own
  PyPI packages (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`) on the user's machine sidesteps the question.

Recommendation: 1.0 has no GPU pack. Ticket 8 builds a spike behind a setting, measures a real
dictation on the 5070 Ti, and decides whether the pack is worth shipping in 1.1.

## Should NexusVox ship its own llama.cpp server instead of Ollama?

**Yes, as an optional download after 1.0, not in the installer.** From `llm-low-gpu-research.md`
(PR #151): llama.cpp is the single runtime worth keeping, and `[llm].protocol = "openai"` already
speaks to `llama-server`, so no protocol work is needed.

- Why not Ollama alone: it is a second installer the user must find, run and keep updated, and it
  runs its own background service. For a "just works" first run that is one step too many.
- Why not bundle in the installer: Polish, Cleanup and summaries are optional, and the model is 2 to 4 GB,
  over the 2 GB cap. Bundling the engine alone saves little.
- Which build: the llama.cpp releases carry separate Windows zips for CPU, Vulkan, CUDA 12 and CUDA 13;
  the CUDA zips need a separate matching `cudart` zip (**published**, third-party guide; exact sizes
  **n/r**). **Vulkan is the right default**: one asset runs on NVIDIA, AMD and Intel GPUs without a
  hand-installed CUDA, and the CPU zip is the fallback. Licence is MIT (**unverified** here, from memory);
  check before adding it to `THIRD_PARTY_LICENSES.md`.
- Shape: a "Language model" card in Settings. It detects hardware, downloads the matching `llama-server`
  zip and the GGUF chosen in the low-GPU research, starts the server on a local port on demand, stops it
  on exit, and writes `[llm]` for the user. Users who already run Ollama or LM Studio keep working
  unchanged.
- Risks: a child process that must be reaped on crash, port conflicts, and one more thing AV tools
  look at (an unsigned downloaded `.exe`). Download only from the official release URL and verify the
  hash.

## Pro licence check hardening

Context: the Pro edition lives in a private repo (`vzwrt13/nexusvox-pro`), is an add-on, and has no
release date. **The free 1.0 contains no licence check, so nothing here blocks the installer.** This is
the plan for the Pro installer later.

The premise is right: a plain PyInstaller build is not protected. PyInstaller stores the application
as compiled bytecode (`.pyc`) in an archive, and extractors plus decompilers recover readable source
for most code. Removing an offline check is a one-line patch. Realistic options:

| Option | What it stops | Cost | Verdict |
|---|---|---|---|
| Nothing (bytecode in PyInstaller) | Nobody | 0 | Fine for 1.0 (no check at all) |
| Signed licence file, Ed25519, public key in the app | Forged or edited licence files. Not a patched check. | Small | **Do this regardless.** Never ship a secret, only a public key |
| Compile the licence module to a native extension (Nuitka `--module`, or Cython) and bundle that `.pyd` in the PyInstaller build | Casual decompilation. The check becomes machine code that has to be reverse-engineered, not read | Small to medium; no new packager needed | **Realistic for Pro v1.** Keep the module tiny and make the unlock affect real behaviour in several places, not one `if` |
| Whole app built with Nuitka standalone | Same, for all code | Medium to high, see packager table | Possible in 1.1 if the Nuitka spike goes well |
| Nuitka Commercial data-hiding plugin | Encrypts string constants (the licence messages and URL stop showing in `strings`) | €400 per year (Full Package) per Nuitka's page; a third-party list says €250, so check | Not needed for v1 |
| Online activation or periodic check | A copied licence on many machines | Needs a server, an account system and an offline grace period | Add when Pro sells, not before |
| Server-side value (cloud features, updates) | The only protection that does not depend on client secrecy | Product decision | The honest end state |

Realistic position: **no client-side check is unbreakable on a machine the attacker controls.** The
goal is to stop casual sharing and make cracking inconvenient, not impossible. Pro will also have
legal friction on top: the core is AGPL-3.0-or-later, so a proprietary add-on that links with it needs
a lawyer's reading before it ships. That is a licensing question, not a packaging one.

## Is Python still fine for a packaged desktop app?

Yes for 1.0 and probably for the product's life. NexusVox spends its time in native code anyway
(ONNX Runtime, CTranslate2, WASAPI, Win32 hooks) and in the network; Python is glue. What it costs is
installer size (an interpreter plus numpy and friends, here 661 MB of dependencies), start time, and
the packaging and AV friction described above, all of which are manageable and mostly one-time work.
A Rust or C# rewrite would fix size and IP protection but would throw away a working product two weeks
before release, so it stays out of scope. Revisit only if size, start time or AV flags stay a
support burden after 1.1.

## Proposed epic: tickets

Order matters. Tickets 1 to 4 are the critical path to 2026-11-13.

| # | Ticket | Size | Notes |
|---|---|---|---|
| 1 | **Spike: PyInstaller onedir build of the current app.** Build on `windows-latest` or locally, measure real size, run the tray, dictation, dashboard and Parakeet download from the frozen app, list missing hooks and data files | M | Confirms every estimate in this document. Include the VirusTotal result |
| 2 | **Frozen-app paths.** When frozen: config in `%APPDATA%\NexusVox`, database, audio and models under the data folder, `DOCKER_DIR` handled, no console and no `print` crashes. Keep source runs unchanged | M | Needed by 1; touches `config.py`, `docker_ctl.py`, `__main__.py` |
| 3 | **Inno Setup script and CI release job.** Per-user install, Start Menu entry, optional autostart task, uninstaller with "delete my data" page (default keep), attach the installer to the GitHub release | M | Extends `release.yml` with a Windows job |
| 4 | **Code signing in CI.** Open Azure Artifact Signing validation, add the signing step, document the unsigned fallback | S | **Start the validation today**, it is the longest wait. Bootloader rebuild belongs here too |
| 5 | **First-run setup screen.** Hardware detection, model name and size, download progress from `load_status`, retry, offline message | M | Mostly dashboard work on existing states |
| 6 | **Model folder under the data folder** with a Settings entry to open and clear it | S | Uninstall can then offer to remove it |
| 7 | **"Copy diagnostics" and update check.** Diagnostics text per above; GitHub releases check once a day with tray and dashboard notice, off-switch in Settings | S | Two small features, one ticket |
| 8 | **Spike: Parakeet on GPU in-process.** `onnxruntime-gpu` in a side folder, active-provider check, measure on the RTX 5070 Ti, decide on a GPU pack | M | After 1.0. Includes the NVIDIA redistribution question |
| 9 | **Shrink the language detector.** Replace or slim `lingua` (291 MB for English and German) | S | Biggest single size win; needs a German/English accuracy test |
| 10 | **Optional language model download (`llama-server` + GGUF).** Settings card, hardware detection, on-demand server process | M | After 1.0; follows the low-GPU research |
| 11 | **Spike: Nuitka standalone build.** Same checks as ticket 1, compare size, AV result and build time; also try compiling a stand-in licence module | M | Decides the 1.1 packager and the Pro protection route |
| 12 | **Autostart toggle** in Settings and the matching installer task | S | Could merge into 3 |
| 13 | **Decision, not code: Store listing.** Check whether a Microsoft Store (MSIX) listing is worth it if SmartScreen stays a problem | S | Only after real SmartScreen data |

If the date slips, 1.0 is tickets 1, 2, 3 (4 optional). Tickets 5 and 7 are strongly wanted but can
land in 1.0.1 without making the first install worse than today's `pip install`.

## Open questions

For the maintainer, with my default:

1. **Sign or not for 13 Nov?** Default: start validation now; ship unsigned if it is not through by 11-10.
2. **Is the update check opt-in or on by default?** Default: on, daily, no identifiers, one Settings toggle.
3. **Where do models live?** Default: `%LOCALAPPDATA%\NexusVox\models`, not the shared Hugging Face cache.
4. **Is Docker/GPU mode still advertised after 1.0?** Default: moved to an "Advanced" docs page, kept in code.

Unverified facts that the spike or a quick check has to settle: Azure Artifact Signing validation time
and eligibility for NightShift AI GmbH; NVIDIA redistribution terms for cuDNN/cuBLAS; `ctranslate2`
support for RTX 50-series; whether `pynput` hooks and `SendInput` behave under MSIX; exact `llama.cpp`
zip sizes and licence text; real installer size.

## Sources

- PyInstaller false positives: [pyinstaller#4343](https://github.com/pyinstaller/pyinstaller/issues/4343), [#5854](https://github.com/pyinstaller/pyinstaller/issues/5854), [#5579](https://github.com/pyinstaller/pyinstaller/issues/5579)
- Microsoft: [code signing options](https://learn.microsoft.com/windows/apps/package-and-deploy/code-signing-options), [SmartScreen reputation](https://learn.microsoft.com/windows/apps/package-and-deploy/smartscreen-reputation)
- Artifact Signing price report: [devclass, 2026-01-14](https://www.devclass.com/security/2026/01/14/code-signing-windows-apps-may-be-easier-and-more-secure-with-new-azure-artifact-service/4079554)
- Nuitka Commercial: [commercial](https://nuitka.net/doc/commercial.html), [protect constants](https://nuitka.net/doc/commercial/protect-constants-data.html)
- Vendor-written Nuitka and PyInstaller comparisons (low weight): [beatsyncpro.ai](https://beatsyncpro.ai/blog/nuitka-vs-pyinstaller.html)
- `onnx-asr`: [README and benchmark table](https://github.com/istupakov/onnx-asr)
- `onnxruntime-gpu` wheel size: [onnxruntime commit](https://app.semanticdiff.com/gh/microsoft/onnxruntime/commit/8fceca0d853090b0cde8ce939802a4f8d79f0800)
- `faster-whisper` CUDA notes: [PyPI](https://pypi.org/project/faster-whisper/)
- Related research in this repo: `docs/model-research.md` (#138), `docs/llm-low-gpu-research.md` (#151)
