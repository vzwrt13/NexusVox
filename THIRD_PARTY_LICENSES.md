# Third-Party Licenses

NexusVox
Copyright (C) 2026 NightShift AI GmbH

NexusVox itself is licensed under the GNU Affero General Public License v3.0 or later —
see [LICENSE](LICENSE). This file records the licenses of third-party material that is
bundled with, or used by, NexusVox. Those licenses apply to that material only.

---

## Bundled third-party software

### Chart.js v4.4.7

- Bundled at: `src/nexusvox/dashboard/static/chart.min.js`
- Homepage: https://www.chartjs.org
- Source: https://github.com/chartjs/Chart.js

This file is a verbatim copy of the minified Chart.js UMD build and is redistributed as part
of every NexusVox wheel and source distribution. Reproducing the license text below is a
condition of the MIT License and applies regardless of NexusVox's own license.

```
The MIT License (MIT)

Copyright (c) 2014-2024 Chart.js Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### Geist and Geist Mono (variable fonts)

- Bundled at: `src/nexusvox/dashboard/static/Geist-Variable.woff2` and
  `src/nexusvox/dashboard/static/GeistMono-Variable.woff2`
- Homepage: https://vercel.com/font
- Source: https://github.com/vercel/geist-font (npm package `geist` 1.3.1)

These are verbatim copies of the unmodified variable WOFF2 files, redistributed as part of
every NexusVox wheel and source distribution so the dashboard works offline. They are licensed
under the SIL Open Font License, Version 1.1, Copyright (c) 2023 Vercel, in collaboration with
basement.studio. The full license text ships next to the fonts as
`src/nexusvox/dashboard/static/Geist-OFL.txt`.

---

## Runtime dependencies

No dependency is redistributed with NexusVox; pip installs them separately. Their licenses
are recorded here so the compatibility picture is on file.

| Dependency | License |
|---|---|
| pynput, pystray | LGPL-3.0 |
| sqlalchemy, alembic, flask, pydub, faster-whisper, ctranslate2, onnxruntime, Pillow | MIT |
| httpx, pyperclip, sounddevice, numpy, av | BSD |
| lingua-language-detector, tokenizers, huggingface-hub, websockets | Apache-2.0 |

All of these are compatible with AGPL-3.0. LGPL-3.0 is explicitly compatible with the GNU
AGPL; MIT, BSD and Apache-2.0 are permissive and may be combined into an AGPL-licensed work.
`pynput` and `pystray` are used as unmodified libraries through ordinary Python imports.

---

## Speech recognition models

No model weights are distributed with NexusVox — they are downloaded from Hugging Face at
runtime and are covered by their own licenses, which are listed in the model table in
[README.md](README.md). Review them before using a model, in particular for commercial use.

### Speaker detection: NVIDIA Streaming Sortformer

- Model: `nvidia/diar_streaming_sortformer_4spk-v2`
  (https://huggingface.co/nvidia/diar_streaming_sortformer_4spk-v2)
- License: Creative Commons Attribution 4.0 International (CC-BY-4.0),
  https://creativecommons.org/licenses/by/4.0/legalcode
- Used by: the Parakeet container (`docker/parakeet_server.py`), only when an upload in the
  dashboard asks for speaker detection. Loaded through NVIDIA NeMo (Apache-2.0), pinned in
  `docker/Dockerfile.parakeet`.

Like the speech models, the weights are not distributed with NexusVox; the container downloads
them from Hugging Face on first use. CC-BY-4.0 allows use, modification and redistribution,
commercial use included, provided NVIDIA is credited; NexusVox credits the model here and in
[README.md](README.md). The model tells at most four speakers apart.
