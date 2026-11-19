# Changelog

All notable changes to this sample. After the tutorial release, only security fixes
and documentation corrections land here, as patch versions.

## [Unreleased] 1.0.0 (tutorial)

Not tagged yet: waiting for the live manual test (`docs/MANUAL_TEST.md`).

- Started from a copy of `vonage-pipecat-openai-realtime` (commit `2ff4b77`, MIT,
  © Vonage): browser app, FastAPI session API, limits, and one process per agent.
- Replaced OpenAI Realtime with a modular Pipecat 1.16.0 pipeline: Deepgram
  speech-to-text, OpenAI `gpt-4.1`, and ElevenLabs text-to-speech, each with its own
  key and settings in `.env`.
- Local turn detection with Silero VAD and Smart Turn v3 on the user aggregator.
- A microphone gate: no audio reaches Deepgram until the browser can hear the agent,
  then the agent greets once.
- Errors that name the failed stage, with a hint per error category, and only end the
  session when a service can't continue.
- A transcript panel showing what Deepgram heard and what the agent said, delivered
  as Vonage Video signals from the server and never stored.
- `DEEPGRAM_KEYTERMS` for Deepgram keyterm prompting.
- The prompt tells the agent it can't look up current events.
- `AGENT_MODE=echo` diagnostic that checks Vonage audio in both directions without
  any AI provider.
