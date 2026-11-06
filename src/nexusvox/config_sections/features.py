"""`[os_commands]`, `[experimental]`, `[mic_guard]`, `[feedback]` and `[learn_from_edits]`: optional app features."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class OSCommandsConfig:
    enabled: bool = False
    apps: dict[str, str] = field(default_factory=dict)


@dataclass
class ExperimentalConfig:
    """Features that are finished in code but not part of v1; each is off (hidden and unreachable) by default.

    - ``os_commands``: Nexus OS commands (voice window management) and their Settings card.
    - ``flag_command``: the "nexus flag" voice command and the Edit tab ("Flagged Transcriptions").
    - ``dev_tab``: the developer benchmark page of the dashboard.
    """

    os_commands: bool = False
    flag_command: bool = False
    dev_tab: bool = False


@dataclass
class MicGuardConfig:
    """Mute every other app's microphone session (Discord, Teams, browser calls) while
    push-to-talk is held, and put each back the way it was on release. Per-session
    WASAPI mute, never the device. `watchdog_s` restores the microphones if no key-up
    is ever seen while the keys are no longer physically down."""

    enabled: bool = False
    watchdog_s: float = 30.0


FEEDBACK_MODES = ("sound", "visual", "both")


@dataclass
class FeedbackConfig:
    """How a dictation signals its state: the start/stop chimes (`sound`), a small waveform at the bottom
    centre of the screen (`visual`), or `both`. Read on every cue, so a change applies without a restart."""

    mode: str = "sound"


@dataclass
class LearnFromEditsConfig:
    """After a paste, read the focused text field through UI Automation for `window_s`
    seconds and turn the words the user corrects into dictionary suggestions, accepted
    or dismissed in the dashboard. Off by default: it reads another app's text field
    (never a password field)."""

    enabled: bool = False
    window_s: float = 20.0
