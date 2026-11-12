# Third-Party Licenses

NexusVox
Copyright (C) 2026 NightShift AI GmbH

NexusVox itself is source-available under the PolyForm Noncommercial License 1.0.0 —
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

The wheel and the source distribution do not contain these dependencies; pip installs them
separately. **The Windows installer does contain them** (the frozen app bundles every runtime
dependency, including the FFmpeg libraries inside PyAV and PortAudio inside sounddevice). The
installer therefore shows and installs `THIRD_PARTY_NOTICES.txt`, generated at build time
(`packaging/third_party_notices.py`), which reproduces each package's licence text. The table
below records the main licences so the compatibility picture is on file.

| Dependency | License |
|---|---|
| pynput, pystray | LGPL-3.0 |
| sqlalchemy, alembic, flask, faster-whisper, ctranslate2, onnxruntime, onnx-asr, Pillow | MIT |
| httpx, pyperclip, sounddevice, numpy | BSD |
| av (PyAV) | BSD for PyAV itself; its wheel bundles FFmpeg (LGPL-3.0-or-later) and codec libraries under LGPL and GPL, see below |
| lingua-language-detector, tokenizers, huggingface-hub, websockets | Apache-2.0 |

MIT, BSD and Apache-2.0 are permissive. LGPL-3.0 applies to `pynput` and `pystray` only, which are used as
unmodified libraries through ordinary Python imports.

**PyAV's FFmpeg libraries are the exception.** The PyAV wheels carry FFmpeg and a set of codec libraries
(`av.libs`). The FFmpeg libraries themselves are licensed under LGPL-3.0-or-later: the bundled DLLs are built with
`--enable-version3`, which raises FFmpeg from LGPL-2.1-or-later to version 3. Among the codecs are x264 and x265, which are licensed under the GPL (version 2 or later). NexusVox does not
use the encoders: `faster-whisper` uses PyAV only to decode audio (the WAV it is given). In the Windows installer they are bundled
anyway, and `THIRD_PARTY_NOTICES.txt` lists them with where their source is.

---

## Speech recognition models

No model weights are distributed with NexusVox — they are downloaded from Hugging Face at
runtime and are covered by their own licenses, which are listed in the model table in
[README.md](README.md). Review them before using a model, in particular for commercial use.

### Whisper Large V3 Turbo

- Model: `deepdml/faster-whisper-large-v3-turbo-ct2`
  (https://huggingface.co/deepdml/faster-whisper-large-v3-turbo-ct2), a CTranslate2 conversion of
  `openai/whisper-large-v3-turbo` (https://huggingface.co/openai/whisper-large-v3-turbo)
- License: MIT, the same as the original OpenAI model
- Used by: the Whisper container and the in-process faster-whisper backend (`[inference].device = "cpu"`).

The weights are not distributed with NexusVox; they are downloaded from Hugging Face on first use. The MIT
License allows use, modification and redistribution, commercial use included, provided the copyright notice
of OpenAI is kept; NexusVox credits the model here and in [README.md](README.md).

### Gemma 4 (cleanup and translation)

- Models: `gemma4:e4b` and `gemma4:e2b` (Ollama tags), Google's Gemma 4
- License: Apache License 2.0, https://ai.google.dev/gemma/docs/gemma_4_license
- Used by: the optional text cleanup and translation steps, through a local Ollama server.

The weights are not distributed with NexusVox; Ollama downloads them on first use. Apache-2.0 allows use,
modification and redistribution, commercial use included. Gemma is a trademark of Google LLC. NexusVox is not
affiliated with Google, and nothing here implies endorsement by Google.

### Parakeet TDT 0.6B v3 on CPU

- Model: `nvidia/parakeet-tdt-0.6b-v3` (https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3)
- License: Creative Commons Attribution 4.0 International (CC-BY-4.0),
  https://creativecommons.org/licenses/by/4.0/legalcode
- ONNX conversion: `istupakov/parakeet-tdt-0.6b-v3-onnx`
  (https://huggingface.co/istupakov/parakeet-tdt-0.6b-v3-onnx), int8 build, same license as the
  original model
- Runtime: onnx-asr (MIT, https://github.com/istupakov/onnx-asr), which also supplies the Silero VAD
  (`istupakov/silero-vad-onnx`, MIT, https://huggingface.co/istupakov/silero-vad-onnx) that cuts long audio into speech segments
- Used by: `src/nexusvox/transcribers/local_parakeet.py`, when Parakeet runs on the CPU instead of
  in the Docker container.

The weights are not distributed with NexusVox; onnx-asr downloads them (about 670 MB) from Hugging
Face on first use. CC-BY-4.0 allows use, modification and redistribution, commercial use included,
provided NVIDIA is credited; NexusVox credits the model here and in [README.md](README.md).

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
