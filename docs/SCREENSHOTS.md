# Dashboard screenshots

`scripts/dashboard_screenshots.py` makes the dashboard images for the README and the website from
invented data. One command, no setup beyond the usual `pip install -e ".[dev]"`:

```bash
python scripts/dashboard_screenshots.py
```

It writes four PNGs to `docs/images/`:

| File | Tab | Image ends |
|---|---|---|
| `dashboard-home.png` | Home | the whole page (the page the dashboard opens on) |
| `dashboard-analytics.png` | Analytics | below the Latency Trend chart |
| `dashboard-recordings.png` | Recordings | one window (1280 x 860): list and detail pane |
| `dashboard-settings.png` | Settings | the Dictation page (the page Settings opens on) |

The Recordings tab fills one window with its two panes and is photographed at a window's height. The
Analytics tab runs on for a long way. The script serves its own copy of the page with
the elements after the last card to show hidden (the selectors are in `SHOTS`), photographs it in a tall
viewport and cuts the empty strip below the last card, so each image ends at an element and follows the
dashboard when its layout changes. Settings is photographed on the page it opens on (Dictation); its other pages are
not displayed, so nothing is hidden there. If a selector stops matching, the image simply gets longer: look at the
result before committing.

The dashboard has a single (dark) theme, so there is one image per tab. The older `dashboard.png`,
`settings.png`, `dashboard-meetings.png` and `dashboard-upload.png` (from before the Recordings tab) are
not touched.

## Options

| Option | Default | Meaning |
|---|---|---|
| `--out DIR` | `docs/images` | where the PNGs go |
| `--browser PATH` | found automatically | `msedge.exe` or `chrome.exe`; `NEXUSVOX_SCREENSHOT_BROWSER` works too |
| `--scale N` | `1.5` | device pixel ratio (1280 px wide viewport times N) |

A Chromium browser is the only extra requirement: Microsoft Edge ships with Windows 11, Google Chrome works
as well. No browser-automation package is needed.

## What it does

1. Moves into a temporary folder and builds a fresh config (saved nowhere but inside that folder).
2. Creates a database there and fills it with about 500 made-up dictations over 75 days, three meetings (with
   word timing and an audio path, so the player and the timed lines show), four uploads (two of them with speaker
   detection), five dictionary entries and three polish presets.
3. Serves the real dashboard on a free port on 127.0.0.1 and photographs each tab with a headless browser into a
   staging folder.
4. Copies the four images to `--out` once all of them succeeded (a failed run never leaves a mix of old and
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
