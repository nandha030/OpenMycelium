# Open WebUI integration smoke — 0.1.0a8

**Result: passed, with no OpenMycelium code change.**

## Versions

```
Open WebUI      v0.11.1  (reported by /api/version at runtime)
image           ghcr.io/open-webui/open-webui:v0.11.1
digest          sha256:6bb1fbe8ab0a3e0456067f493044ffb66a30a65a34be47f6a5862176a370dd16
created         2026-08-25T21:21:52Z
platform        linux/amd64, 1.84 GB compressed
OpenMycelium    0.1.0a8, content b430eff6e79cd56b30841716431014c7b566942704b8e4c1df5c9336ad43981a
distribution    om-clean2 (the clean-bootstrap distribution)
```

The container was re-created pinned **by digest**, not by tag.

## Test configuration

- Temporary container `om-smoke-webui-<timestamp>`, temporary named volume, neither reusing any existing resource.
- **No GPU**: `Devices []`, `DeviceRequests []`, `Runtime runc`.
- **No token in the environment**: verified `0` secret-bearing variables via `docker inspect`.
- Provider URL `http://host.docker.internal:11500/v1`.
- Ollama disabled. The host runs one, Open WebUI discovers it by default, and a smoke that silently mixed in another inference server would prove nothing.

## Result by acceptance step

| # | Step | Result |
|---|---|---|
| 1 | Wrong/missing token rejected | `/openai/verify` → **401**; container→host with no token and with a bad token → **401**; **no models discovered** |
| 2 | Correct token configured | stored, 48 chars, value never displayed |
| 3 | `/v1/models` discovery and selection | `Mistral-Nemo-Instruct-2407`, `owned_by: openai`; no Ollama models present |
| 4 | Streaming request | **397 chunks** |
| 5 | Text appears incrementally | first token at **225 ms** of a 36,698 ms response |
| 6 | Two-turn conversation | asked to remember `8317`; turn two answered `8317` |
| 7 | Full history reaches OpenMycelium | prompt tokens **18 → 32** |
| 8 | EOS vs length | EOS: `finish_reason=stop`, 2 of 200 tokens. Length: `finish_reason=length`, 24 of 24 |
| 9 | Stop during generation | see below |
| 10–12 | Outage during OpenMycelium restart | Open WebUI surfaced **HTTP 400** rather than inventing a reply; no chat corruption |
| 13 | Reconnect with existing config | replied `reconnected` |
| 14 | Container restart, config persists | base URL, 48-char key and enabled flag all survived; discovery works again |
| 15 | `ps` / `stop` | stopped cleanly, **0 orphan workers**, CUDA VRAM **1313 MiB** idle |

## Step 9 — stated precisely

The stream was closed after 9 chunks, which is what Open WebUI's Stop button does
to the connection.

**This is not backend cancellation.** This build has no worker-side
cancellation; the generation continued to completion on both GPUs. What was
demonstrated:

```
during the drain     HTTP 429, Retry-After: 5
drain completed      next request served after 34 s
contamination        none - asked for "banana", answered 'banana',
                     no trace of the abandoned essay
```

## Open WebUI's default parameters

With Open WebUI's defaults and no parameters set in its UI, every request was
accepted. Open WebUI did not send a sampling field that OpenMycelium refuses, so
no greedy-mode reconfiguration was required to make the integration pass.
OpenMycelium's refusal of non-default sampling remains verified separately:
`temperature: 0.9` → **HTTP 400**, rather than being silently ignored.

## Deviations, stated rather than hidden

**The GUI was not driven by clicking.** The browser pane could not be displayed
in this environment, so screenshots were unavailable and overlay menus did not
reach the accessibility tree. Configuration and every request were driven
through Open WebUI's own backend endpoints — `/openai/config/update`,
`/openai/verify`, `/api/models`, `/api/chat/completions` — which is the same
path its settings page and chat view use. **Visual rendering was therefore not
verified**; the request path, streaming behaviour, history handling, error
surfacing and persistence were.

**`ENABLE_OPENAI_API_PASSTHROUGH=True`** was set on the container so
`POST /openai/config/update` could be reached without a GUI. This is a property
of the harness, not of OpenMycelium.

**`WEBUI_AUTH=False`** was set so the throwaway container needed no account
creation or password handling.

## Token handling

- Generated inside `om-clean2`, stored at `/run/openmycelium/openwebui.token` on **tmpfs**, mode 600, owner root.
- Passed to `serve` through the **environment**, never `--token`: the process command line was verified clean.
- Delivered to Open WebUI on **stdin** to a script inside the container, so it was never a command-line argument, never in a shell history, and never in this session's transcript.
- Never passed via `docker -e`, so `docker inspect` never exposed it.
- Never printed; only its length was ever displayed.
- Destroyed at teardown. `/var/log`, `/run`, `/tmp` and `/root` scanned for stray 48-hex strings: all clean.

An earlier token was briefly written under `/var/log` and placed on the Windows
clipboard. Both were destroyed — shredded and cleared — and the practice was
corrected before Open WebUI received a single request.

## Cleanup

Temporary container and volume **removed** after the persistence evidence was
recorded. The `alpine:3.20` speed-probe image was removed. The
`ghcr.io/open-webui/open-webui:v0.11.1` image was retained so a future smoke
does not repeat the download. All six pre-existing `open_mycelium-*` containers
remain untouched.

## Notes for the release documentation

`openmycelium serve` must be held by a live client. A server started from a
script whose session then exits is terminated with that session — which is why
the Windows launcher holds one foreground `wsl.exe` for the lifetime of `run`,
`chat` and `serve`. Observed here when a `serve` spawned inside a test script
died with its parent. Not a defect, but it must be documented so users do not
walk into it.
