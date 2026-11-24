# Dependencies and verified versions

Everything installs from one manifest (`pyproject.toml`) and one lock file (`uv.lock`).
The Docker image runs `uv sync --frozen`, so a build installs exactly what the lock
says or fails.

## Pinned versions

Versions as installed in the Docker image and printed by `importlib.metadata` inside
the container (2026-11-19):

| Component | Version | Source |
|---|---|---|
| Python | 3.12.16 | `python:3.12-slim-bookworm` (Debian 12, glibc 2.36) |
| `pipecat-ai` | 1.16.0 | PyPI, with the `deepgram`, `elevenlabs`, `openai`, and `vonage-video-connector` extras |
| `vonage-video-connector` | 1.0.3 | PyPI, via Pipecat's `vonage-video-connector` extra (Linux only) |
| `deepgram-sdk` | 7.19.0 | PyPI, via Pipecat's `deepgram` extra |
| `openai` | 3.55.1 | PyPI, a Pipecat core dependency |
| ElevenLabs | no SDK | Pipecat's `ElevenLabsTTSService` talks to the WebSocket API with `websockets` 17.4 |
| Silero VAD, Smart Turn v3 | ONNX models inside the `pipecat-ai` wheel | run with `onnxruntime` 1.32.0 on the CPU |
| `vonage` (server SDK) | 4.10.0 | PyPI |
| `fastapi` / `uvicorn` | 0.165.0 / 0.54.0 | PyPI |
| Vonage Video Web SDK | `@vonage/client-sdk-video@2.36.0` | npm via unpkg |
| OS packages | `libexpat1`, `libglib2.0-0` | Debian bookworm; the list Vonage's server samples install |
| uv | 0.11.32 | PyPI, build stage only |

`[tool.uv] exclude-newer = "2026-11-19T00:00:00Z"` keeps re-locking reproducible.

## Provider defaults

| Stage | Default | Why |
|---|---|---|
| Speech-to-text | Deepgram `nova-3-general`, `en` | Pipecat's default for `DeepgramSTTService`. Interim results on, so the user aggregator sees speech early. |
| LLM | OpenAI `gpt-4.1` | Pipecat's default for `OpenAILLMService`, and within the ~700 ms first-token budget in [Pipecat's voice benchmark](https://www.pipecat.ai/benchmarks/voice-readiness). Configurable with `OPENAI_MODEL`. |
| Text-to-speech | ElevenLabs `eleven_flash_v2_5` | Pipecat's default; ElevenLabs recommends Flash for agents (~100 ms). `eleven_v4_turbo` uses the Text to Dialogue WebSocket, which `ElevenLabsTTSService` doesn't use. |
| Voice | `l7kNoIfnJKPg7779LI2t` (*Eddie - Natural and Helpful*) | One of ElevenLabs' [recommended replacements](https://elevenlabs.io/docs/help-center/product/voices/my-voices/what-are-default-voices) for the Default voices that expire on December 31, 2026. A Voice Library voice, so it needs a paid plan through the API. |

## The Pipecat source: PyPI `pipecat-ai`

The [Vonage Video Transport for Pipecat guide](https://developer.vonage.com/en/video/guides/vonage-video-connector-pipecat-transport)
installs `pipecat-ai[vonage-video-connector]` from PyPI. This sample adds the three
service extras to the same requirement, so Pipecat is installed once. Never install
the [Vonage Pipecat fork](https://github.com/Vonage/pipecat) from Git alongside it.

## APIs verified at the pinned version

Read in Pipecat 1.16.0's source and exercised by `tests/test_modular_agent.py` with
the real classes:

- `VonageVideoConnectorTransport(application_id, session_id, token, params)`;
  input defaults to 16 kHz mono and output to 24 kHz mono. Deepgram takes the input
  rate and ElevenLabs the output rate from the pipeline, so there is no manual
  resampling.
- `DeepgramSTTService(api_key=..., settings=DeepgramSTTService.Settings(model, language))`.
  It sends a KeepAlive every 5 seconds, so a closed microphone gate doesn't time out
  the Deepgram connection.
- `OpenAILLMService(api_key=..., settings=OpenAILLMService.Settings(model, system_instruction))`.
- `ElevenLabsTTSService(api_key=..., settings=ElevenLabsTTSService.Settings(model, voice))`.
  The `voice_id=` and `model=` constructor arguments are deprecated.
- `LLMContextAggregatorPair(context, user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()))`.
  With no `user_turn_strategies`, turns start on VAD or a transcript and stop when
  `LocalSmartTurnAnalyzerV3` says the sentence is complete.
- `DeepgramSTTService.Settings(keyterm=[...])`, sent as repeated `keyterm` query
  parameters; Nova-3 only.
- Aggregator events `on_user_turn_message_added` (`message.content`) and
  `on_assistant_turn_stopped` (`message.content`, `message.interrupted`).
- `vonage` 4.10.0: `video.send_signal(session_id, SignalData(type, data))`, which
  signals every connection in the session. Signal data is capped at 8 kB, so lines are
  cut to 2,000 characters.
- `ErrorFrame.processor`, `.category` (`ErrorCategory`), `.fatal`, and
  `FrameProcessor.is_usable`, used to name the failed stage and to decide whether to
  end the session.
- `PipelineWorker`, `WorkerRunner`, `ProcessorUnusablePolicy.END`, and worker events
  `on_pipeline_error`, `on_idle_timeout`, `on_setup_timeout`.

## Where sources disagreed, and what this sample does

| Topic | Sources | Choice |
|---|---|---|
| Where VAD goes | The Vonage transport guide passes `vad_analyzer=SileroVADAnalyzer()` to `VonageVideoConnectorTransportParams`. Pipecat deprecated the transport field, then removed it from `TransportParams`; Pydantic ignores the unknown argument with no warning (checked in the 1.16.0 image). | VAD on `LLMUserAggregatorParams`, as Pipecat's own examples do. |
| Pipeline classes | Older project notes used `PipelineTask` and `OpenAILLMContext`. | `PipelineWorker`, `WorkerRunner`, `LLMContext`. |
| Greeting trigger | Pipecat examples greet in `on_client_connected`. | Greet on the browser's `POST /ready`, because only the browser knows it can play audio. |
| Connector instances | The Video Connector guide allows one connector instance per process. | One OS process per session (`app/agent_process.py`). |
| Routed sessions | Required by the project brief; Kitt Phi's tested tutorial uses routed sessions. | Always routed. |
| When the agent stops speaking | `VonageVideoConnectorOutputTransport.write_audio_frame` calls the native `add_audio`, which buffers and returns at once. On 2026-11-24, Pipecat logged *Bot stopped speaking* 0.6 s into a reply that played for 15 s, and a one-word "Okay." mid-reply interrupted it (`MinWordsUserTurnStartStrategy ... should_trigger=True num_spoken_words=1`). Pipecat's websocket transports pace with `_write_audio_sleep`. | `pace_audio_to_real_time()` waits for each chunk's playing time, 0.2 s ahead. With it, the same "Okay." logged `should_trigger=False` and the reply played to the end. |

## Platforms

| Platform | Status |
|---|---|
| Linux arm64 (Docker Desktop on Apple Silicon) | **Verified 2026-11-19:** image builds; ruff, mypy, and pytest pass inside it. |
| Linux x86_64 | Not verified with this repo. Wheels exist for all dependencies. |
| Windows host | Not verified. Supported through Docker Desktop. |
| Running the agent outside Docker on macOS/Windows | Not supported: the connector ships Linux wheels only. |

## Updating dependencies

1. Change the pin in `pyproject.toml` (and `exclude-newer`), then run `uv lock`.
2. If the Pipecat version changes, re-read the Vonage transport and the Deepgram,
   OpenAI, and ElevenLabs services at the new version, and update the tables above.
3. Run `docker build --target test .` and the full manual test.
4. Ship a new release with a CHANGELOG entry. Never move the `v1.0.0-tutorial` tag.
