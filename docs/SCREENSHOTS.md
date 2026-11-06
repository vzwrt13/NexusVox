# Dashboard screenshots

`scripts/dashboard_screenshots.py` makes the dashboard images for the README and the website from
invented data. One command, no setup beyond the usual `pip install -e ".[dev]"`:

```bash
python scripts/dashboard_screenshots.py
```

It writes four PNGs to `docs/images/`:

| File | Tab | Cut |
|---|---|---|
| `dashboard-analytics.png` | Analytics | at a fixed height, below the latency chart |
| `dashboard-meetings.png` | Meetings | whole tab |
| `dashboard-upload.png` | Upload | at a fixed height, below the fourth upload |
| `dashboard-settings.png` | Settings | at a fixed height, below the Vocabulary card |

The dashboard has a single (dark) theme, so there is one image per tab. The older `dashboard.png` and
`settings.png` are not touched.

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
2. Creates a database there and fills it with about 530 made-up dictations over 75 days (English and German),
   three meetings, four uploads, five dictionary entries and three polish presets.
3. Serves the real dashboard on a free port on 127.0.0.1 and photographs each tab with a headless browser.
4. Deletes the temporary folder.

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

The data comes from a fixed seed and is placed relative to noon of today. Running the script twice on the same
day gives identical files; on another day only the dates in the charts and lists move.

## Changing the demo data

Everything lives at the top of the script: the sentence pools, the apps and models, the meetings, uploads,
dictionary and presets. Keep it invented: no real names, companies, transcripts or paths, in English only
(the German sentences are the one exception, because the dashboard shows both languages).
