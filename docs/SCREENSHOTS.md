# Dashboard screenshots

`scripts/dashboard_screenshots.py` makes the dashboard images for the README and the website from
invented data. One command, no setup beyond the usual `pip install -e ".[dev]"`:

```bash
python scripts/dashboard_screenshots.py
```

It writes fourteen PNGs to `docs/images/`: one per tab, one per step of the first-run setup, and five for the website user guide:

| File | Tab | Image ends |
|---|---|---|
| `dashboard-home.png` | Home | one window (1280 x 860): the page the dashboard opens on, with Pip and his bubble in the bottom-right corner |
| `dashboard-analytics.png` | Analytics | the Overview (the view the tab opens on) |
| `dashboard-analytics-week.png` | Analytics | the Your week view |
| `dashboard-recordings.png` | Recordings | one window (1280 x 860): list and detail pane |
| `dashboard-settings.png` | Settings | the Dictation page (the page Settings opens on) |
| `dashboard-setup-welcome.png`, `-microphone.png`, `-hotkey.png`, `-ready.png` | First-run setup | one image per step, ending at the step's card and the *Skip setup* link |
| `guide-settings-dictation-hotkey.png` | Settings > Dictation | the Hotkey section (the rows after it are hidden) |
| `guide-settings-vocabulary.png` | Settings > Vocabulary | the whole page |
| `guide-settings-text-polish.png` | Settings > Text & Polish | the whole page |
| `guide-recordings-upload.png` | Recordings | one window: the *Upload a file* dialog with its drop area |
| `guide-recordings-meeting-start.png` | Recordings | one window: the consent card that *Start recording* opens (it is never confirmed, so nothing records) |

The `guide-*` images are copied into the website's user guide. Before any shot the script checks that every click target (`#id`) and settings page slug of `GUIDE_SHOTS` is still in
`index.html` and stops with a message when not. The Start recording click is also safe by construction: the consent
hint is pinned on and `start_meeting` is replaced, so no recording can start. The two Recordings ones are photographed with a short script
that clicks the button once the page has loaded (`GUIDE_SHOTS` in the script); the Settings ones open on their page the
same way Analytics > Your week does.

The setup is an overlay that opens while `[general].setup_done` is false, so those four are photographed with the
setup due; the script tells the page which step to show (`SETUP_SCRIPT`), fills the test box on the hotkey step and
replaces the microphone list and level with demo values (the real audio devices are never probed).

`scripts/pip_poses.py` is a separate, smaller script for Pip, the dashboard mascot: `python scripts/pip_poses.py` writes `docs/images/pip-poses.png`, one headless photograph of the real `dashboard-pip.js` and `style.css` with Pip in each pose (idle, blink, head tilt, sleeping) and once with a speech bubble, and `docs/images/pip-empty-recordings.png`, the empty Recordings list with Pip sleeping. It builds a throwaway page in a temporary folder with invented sample text, opens no window, reads no config or database and switches the idle motion off so two runs match. It reuses the browser lookup and launch of `dashboard_screenshots.py`.

`scripts/pip_greetings.py` does the same for Pip's daily speech bubble on Home: `python scripts/pip_greetings.py` writes two images: `docs/images/pip-greetings.png`, one headless photograph of the three kinds of bubble (a milestone, a tip about a feature never used, a usage statistic), each chosen by the real `pickPipGreeting` from invented counts, and `docs/images/pip-bubble-variants.png`, three looks of the bubble side by side (the shipped one and two alternatives drawn only in the throwaway page). It opens no window and reads no config, database or saved browser state.

The Recordings tab fills one window with its two panes and is photographed at a window's height. The
script serves its own copy of the page with the elements after the last card to show hidden when a tab
runs on for a long way (the selectors are in `SHOTS` and `GUIDE_SHOTS`; Analytics needs none now), photographs it in a tall
viewport and cuts the empty strip below the last card, so each image ends at an element and follows the
dashboard when its layout changes. The Settings page of `dashboard-settings.png` is Dictation; the `guide-*` shots open Vocabulary and Text & Polish by
slug and hide the rows after the hotkey row on Dictation (selector in `GUIDE_SHOTS`). If a hide selector stops
matching, the image simply gets longer: look at the
result before committing.

The dashboard has a single (dark) theme, so there is no light/dark pair. Every image in `docs/images/` comes from
this script; re-run it and commit the images that differ whenever the dashboard's look changes.

## Options

| Option | Default | Meaning |
|---|---|---|
| `--out DIR` | `docs/images` | where the PNGs go |
| `--browser PATH` | found automatically | `msedge.exe` or `chrome.exe`; `NEXUSVOX_SCREENSHOT_BROWSER` works too |
| `--scale N` | `1.5` | device pixel ratio (1280 px wide viewport times N) |

A Chromium browser is the only extra requirement: Microsoft Edge ships with Windows 10 (version 2004 or newer) and Windows 11, Google Chrome works
as well. No browser-automation package is needed.

## What it does

1. Moves into a temporary folder and builds a fresh config (saved nowhere but inside that folder).
2. Creates a database there and fills it with about 500 made-up dictations over 75 days, three meetings (with
   word timing and an audio path, so the player and the timed lines show), four uploads (two of them with speaker
   detection), five dictionary entries and three polish presets.
3. Serves the real dashboard on a free port on 127.0.0.1 and photographs each tab with a headless browser into a
   staging folder.
4. Copies the images to `--out` once all of them succeeded (a failed run never leaves a mix of old and
   new images), then deletes the temporary folder.

## What it never touches

- **Your data.** `config.toml`, the database and the audio of your install are never opened or written.
- **The machine.** The GPU, language-model server, translator and audio-device probes are replaced by fixed
  answers, the log folder is a made-up path, and no extension is loaded. No hotkey listener starts, no audio
  device opens, no model is loaded or downloaded, Docker is not touched.
- **The network.** The browser gets a throwaway profile, sync and update traffic are off, and every host except
  127.0.0.1 cannot be resolved.
- **The app.** No application code is changed. The script serves its own copy of the page with chart and CSS
  animation switched off, so that a screenshot never catches the page mid-motion.

## Repeatable

The data is the same on every day: each day of the 75-day window has its own random stream, seeded by its
position in the window, and the window ends today at noon. Running the script twice on the same day gives
identical files; on another day only the dates in the charts and lists move.

The machine does not leak in either. The time zone is pinned to UTC for the script and the browser, and the
browser runs with `--lang=en-US`, so dates, numbers and date inputs read the same everywhere.

## Changing the demo data

Everything lives at the top of the script: the sentence pool, the apps and models, the meetings, uploads,
dictionary and presets. Keep it invented (no real names, companies, transcripts or paths) and in English like
the rest of the repository. The language chart therefore shows a single language.
