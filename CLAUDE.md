# CLAUDE.md

Maintainer notes for **blog-video-python_pipecat-modular_voice_agent**, the Part 2
sample of a three-part Vonage Developer Blog series. This repository is the companion
to a tutorial and must keep matching it.

## Scope: keep it Part 2

- A modular pipeline: **Deepgram** speech-to-text → **OpenAI** text LLM →
  **ElevenLabs** text-to-speech, through Pipecat's `DeepgramSTTService`,
  `OpenAILLMService`, and `ElevenLabsTTSService`.
- Do **not** add OpenAI Realtime, personas (no Einstein), avatars, AWS/AgentCore,
  telephony, recordings, benchmarks, or a provider-switching framework. Part 3 is a
  separate repository.
- This repo started as a copy of Part 1 (`vonage-pipecat-openai-realtime`) but has no
  runtime, install, or setup dependency on it. Never add one, and never edit Part 1
  from here.
- Keep the Vonage **Video** Connector transport. Don't substitute the Audio Connector
  or the Voice API.
- Use licensed ElevenLabs voices only. Never clone a real person's voice.
- After the `v1.0.0-tutorial` tag: only security fixes and doc corrections.

## Commands

```bash
uv sync                                      # local venv (Linux has the native connector)
uv run pytest -q                             # tests; Linux-only ones skip on macOS/Windows
uv run ruff check app tests && uv run ruff format --check app tests
uv run mypy
docker build --target test .                 # lint + types + tests inside Linux
docker compose up --build                    # run the app on http://localhost:8000
```

## Architecture rules

- `app/modular_agent.py` is the code the blog post teaches. Keep the `Pipeline([...])`
  list readable: one line per stage.
- VAD belongs on `LLMUserAggregatorParams`, not on the transport params (Pipecat
  removed the transport field and ignores it silently).
- `MicrophoneGate` drops audio until `POST /ready`; the greeting is sent once.
- Transcripts go agent process → pipe → server → Vonage Video signal (`send_signal`,
  type `transcript`) → browser. The server queues them only until sent. The browser
  accepts only signals with no `from` connection (sent by the server).
- The waiting room's choices are validated in `app/call_options.py` (allowed lists and
  ranges) and travel to the agent process over the pipe, then onto `Settings` with
  `CallOptions.apply_to`. Never pass a browser value to a provider unchecked. The
  ElevenLabs key is only used server-side, including for listing voices.
- `on_pipeline_error` ends the session only for fatal errors or unusable processors,
  and names the stage. Services retry recoverable errors themselves.
- One agent per OS process (`app/agent_process.py`). Every session has a tracked
  supervisor task with ready, idle, and maximum-duration limits, and an idempotent end.
- Only the browser token is returned to the browser. API keys and the private key
  never leave the server. Logs go through `app.config.setup_logging`, which redacts
  Vonage, Deepgram, OpenAI, and ElevenLabs credentials.

## Dependencies

- `pipecat-ai[deepgram,elevenlabs,openai,vonage-video-connector]` from PyPI at one
  pinned version; see `docs/DEPENDENCIES.md`. Never install the Vonage fork alongside it.
- `uv.lock` is committed; Docker uses `uv sync --frozen`.
- Check the real source of a new Pipecat version before using an API.

## Verification honesty

Mocked tests don't prove WebRTC audio or provider behavior. Any change touching media,
the agent, or dependencies needs `docs/MANUAL_TEST.md` with real credentials. Report
anything not run as NOT VERIFIED.

## Publishing

Don't create remotes, push, or tag without the maintainer's explicit approval.
