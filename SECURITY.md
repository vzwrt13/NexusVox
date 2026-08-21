# Security Policy

## Reporting a vulnerability

**Please do not open a public issue for a security problem.** A public issue is
visible to everyone the moment you file it, including anyone who would rather
exploit the bug than see it fixed.

Two private channels:

- **GitHub Private Vulnerability Reporting** — the *Security* tab of this
  repository, *Report a vulnerability*. Preferred, because the discussion stays
  attached to the code.
- **Email** — info@nightshift-ai.de

Useful things to include: what an attacker gains, the steps to reproduce it, the
NexusVox version, your Windows version, and whether you were on the CPU or the
GPU path. A proof of concept is welcome but not required — a clear description of
the mechanism is worth more than a script that only runs on your machine.

**What to expect.** NexusVox is published as a reference implementation and is
not under active development; the repository is not watched daily. A first reply
may take a couple of weeks. If a report is valid and the fix is small, it will be
made. If it is valid and the fix is large, that will be said plainly rather than
left open indefinitely. You are free to disclose publicly after 90 days
regardless of whether anything has happened — you do not need permission for
that, and asking you to wait longer would not be reasonable given the stated
maintenance level.

## Supported versions

Only the most recent release receives fixes. There are no maintenance branches
and no backports.

## What NexusVox does on your machine

Understanding the design makes it much easier to tell a real vulnerability from
intended behaviour. NexusVox is a desktop dictation tool, and its normal
operation involves capabilities that look alarming out of context:

- **Global hotkey listener.** A system-wide keyboard hook (pynput) watches for
  the configured modifier combination. It observes modifier state in order to
  detect press and release; it does not log the keys you type.
- **Microphone capture.** Audio is recorded only while the hotkey is held.
- **Clipboard access.** Text is injected by writing to the clipboard and sending
  `Ctrl+V`. Your previous clipboard contents are saved and restored afterwards,
  which means they pass through the process.
- **Synthetic input.** `SendInput`, with a `WM_PASTE` fallback, types into
  whatever window currently has focus. NexusVox does not choose that window.
- **Local dashboard.** A Flask server bound to `127.0.0.1:47392`, started on
  demand from the tray menu. **It has no authentication.** Anything able to make
  HTTP requests from your machine can read your full transcription history,
  change settings, and submit audio for transcription. This is a deliberate
  trade-off for a single-user desktop tool, not an oversight — but you should
  know it before running NexusVox on a shared or multi-user machine.
- **Local storage.** Transcriptions go into a SQLite database and recordings into
  an audio directory, both unencrypted, both readable by anything running as your
  user. Everything you have ever dictated is in there.
- **Optional inference containers.** The GPU backends listen on `localhost` ports
  8000–8003 and are likewise unauthenticated.
- **Network.** No telemetry, no accounts, no cloud transcription. The only
  outbound traffic is downloading model weights from Hugging Face on first use,
  and whatever Docker does when building images.

## Trust boundary

Everything above runs as your user, on your machine, inside your session.
NexusVox assumes that anything already running as your user is trusted. It is not
a sandbox and does not try to defend against local malware — if something hostile
is already executing under your account, it can read the database directly and
does not need to go through the dashboard to do it.

## In scope

- Remote or cross-origin access to the dashboard, or anything that reaches it
  from outside the loopback interface
- Escaping the intended trust boundary: privilege escalation, code execution
  triggered by a crafted audio file or a crafted transcription result
- Injection into a window other than the focused one, or injection the user did
  not initiate
- Credential or transcription leakage to any destination outside the machine
- A dependency vulnerability that is genuinely reachable through NexusVox

## Out of scope

- The capabilities listed under *What NexusVox does on your machine*, when
  behaving as described
- Attacks that require an attacker already running code as your user
- Vulnerabilities in the speech recognition models themselves — those belong to
  their publishers; see the model table in [README.md](README.md)
- Vulnerabilities in third-party dependencies with no reachable path through
  NexusVox — report those to the project that owns them
- Findings from an automated scanner with no demonstrated impact
