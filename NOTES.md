# Internal notes — voxtype-tray

Not user docs. Internal context for future sessions / future-me.

## Known upstream issues filed against peteonrails/voxtype

- **#442** — Parakeet streaming downloader missing `tokenizer.model`; root cause is actually a TDT-duration-token bug in the streaming inference loop (see comment thread, not the tokenizer itself).
- **#443** — `/usr/bin/voxtype` should be a wrapper script, not a symlink. ORT provider lookup uses argv[0]'s dirname. Worked around locally with a wrapper at `/usr/bin/voxtype` and one-click "Fix MIGraphX library path" button in the tray (v1.2.2+).
- **#444** — Arch `voxtype` package missing `migraphx` dependency (it ships `voxtype-onnx-migraphx` but doesn't pull in the runtime). Worked around by manual `pacman -S migraphx`.

## Known upstream issue NOT filed (deliberate, to avoid spamming peteonrails)

**ORT_MIGRAPHX_CACHE_PATH not set automatically.** Without this env var, MIGraphX errors at first inference with:

```
migraphx_save: Error: file_buffer.cpp:77: write_buffer: Failure opening file: ""/<hash>-...mxr
ERROR Non-zero status code ... MGXKernel_graph_main_graph_..._0 ... Failed to call function
```

Symptom matches #444 / #443 (silent CPU fallback OR transcription failure), but the cause is one layer deeper: ORT's MIGraphX provider needs a writable kernel-cache directory configured via `ORT_MIGRAPHX_CACHE_PATH` (and optionally `ORT_MIGRAPHX_MODEL_CACHE_PATH`). Worked around with a systemd user drop-in at `~/.config/systemd/user/voxtype.service.d/migraphx-cache.conf`:

```ini
[Service]
Environment="ORT_MIGRAPHX_CACHE_PATH=%h/.cache/migraphx"
Environment="ORT_MIGRAPHX_MODEL_CACHE_PATH=%h/.cache/migraphx-models"
Environment="HIP_VISIBLE_DEVICES=0"
```

If we ever revisit, candidate fixes upstream:
1. Have `voxtype setup gpu --enable` create the systemd drop-in automatically (probably the cleanest UX).
2. Or have voxtype itself set `ORT_MIGRAPHX_CACHE_PATH` programmatically before initializing the ORT session if it's unset, defaulting to `$XDG_CACHE_HOME/voxtype/migraphx`.

We could also surface this in the tray's health check (`provider_lookup_failed_recently()` already detects the symptom — could add a sibling check for the cache-write error and offer a one-click install of the drop-in file).

## How dev tracks installed tray

The user's `/usr/bin/voxtype-tray` is the AUR-installed version. The source of truth is `/home/contra/dev/voxtype-tray/voxtype-tray.py`. To test a dev build:

```bash
sudo install -m755 /home/contra/dev/voxtype-tray/voxtype-tray.py /usr/bin/voxtype-tray
pkill -f voxtype-tray; voxtype-tray &
```

This is a manual step until AUR publishes the new tag. Mentioning in this notes file because it tripped us up multiple times in the v1.2.0 → v1.2.2 cycle.

## Wrapper script details

The wrapper at `/usr/bin/voxtype` (installed via the tray's "Fix MIGraphX library path" button in v1.2.2+, or manually via `sudo install -m755 /tmp/voxtype-wrapper.sh /usr/bin/voxtype`) probes the available variants in this order:

1. voxtype-onnx-migraphx
2. voxtype-onnx-cuda-13
3. voxtype-onnx-cuda-12
4. voxtype-vulkan
5. voxtype-onnx-avx512
6. voxtype-avx512

So it survives `voxtype setup gpu --enable/--disable` swaps without needing to be re-installed.
