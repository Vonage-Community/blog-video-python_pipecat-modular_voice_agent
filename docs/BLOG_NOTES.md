# Blog 2 notes (for the author, not the published post)

**Working title:** Build a Custom AI Voice Agent With Vonage, Deepgram, and ElevenLabs

**Status:** builds and passes its automated tests in Linux Docker. **The live call
with Deepgram, OpenAI, and ElevenLabs has not been verified yet**; run
`docs/MANUAL_TEST.md` Gate 3 first.

## Rationale (one paragraph, connects to Part 1)

> In Part 1, one OpenAI Realtime model did everything: it heard you, decided what to
> say, and said it. That's the fastest way to get an agent talking, but listening,
> thinking, and the voice all come from one provider. This post splits them into
> three services in a Pipecat pipeline: Deepgram for speech-to-text, an OpenAI text
> model for the reply, and ElevenLabs for the voice. Each one has its own key and
> settings, so you can pick a voice or change the prompt without touching
> transcription, and when something breaks, the error tells you which stage it was.

## Architecture

```text
Browser (Vonage Video Web SDK) ⇄ Vonage Video routed session ⇄ Pipecat agent (Docker)
  transport.input → MicrophoneGate → Deepgram STT → user turn (Silero VAD + Smart Turn v3)
  → OpenAI LLM → ElevenLabs TTS → transport.output → assistant aggregator
```

The README's "Architecture" diagram has the full version.

## Exact test steps

1. `cp .env.example .env`; set the Vonage app ID and the three provider keys.
2. `docker compose up --build`; wait for the `Ready: agent=Deepgram …` line.
3. Open `http://localhost:8000`, click **Start conversation**, allow the microphone.
4. Hear one greeting; ask two or three questions; talk over a long answer; Mute;
   Unmute; Leave.
5. `AGENT_MODE=echo` to check Vonage alone.

## The main screenshot

The transcript panel mid-conversation: *Deepgram heard:* and *Agent said:* lines, one
marked *(interrupted)*. It shows the text between the stages, which a speech-to-speech
model never exposes.

## Measured response time (2026-11-19, arm64 Docker Desktop, seven replies)

From the agent deciding the user stopped to its first audio leaving the container:
0.94 to 1.32 s, typically 1.04 s. VAD silence 0.20 s, Deepgram 0.05 to 0.14 s, Smart
Turn under 0.01 s (once 0.11 s), OpenAI `gpt-4.1` first token 0.39 to 0.77 s, sentence
aggregation 0 to 0.16 s, ElevenLabs first audio 0.15 to 0.21 s. Vonage network time to
the browser not measured. Present these as measured numbers, with OpenAI as the
obvious place to experiment (a faster model), not as a benchmark.

## Lessons from the live runs

- A one-word "Okay." interrupted the agent mid-answer. `MinWordsUserTurnStartStrategy`
  (3 words while the agent talks) fixed it: a good "tune each stage" moment.
- Without keyterms, Deepgram heard "Pipecat" once as "PIPECAT" and once as "Pipe cut".
  With `DEEPGRAM_KEYTERMS=Vonage,Pipecat` it wrote "Pipecat". It also heard "Aronov" as
  "Aronoff", another keyterm candidate.
- `gpt-4.1` doesn't know what Pipecat is. Worth one line: transcription was right, the
  model's knowledge wasn't, and the transcript panel shows which.
- The ElevenLabs dashboard's voice and model don't affect API calls.
- vonage-python-sdk#323: a JSON request (`send_signal`) breaks later session creation
  (HTTP 415). One sentence in the post, since readers who add signals will hit it.

## Most educational code excerpts

1. **The pipeline list** in `ModularAgent.build_pipeline`: one line per stage.
2. **The three services**, each built from its own `.env` settings.
3. **Turn detection on the user aggregator**: `LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer())`,
   and the note that the transport's `vad_analyzer` argument is ignored.
4. **`MicrophoneGate`** and `begin_conversation`: no audio to Deepgram until the
   browser is ready, then one `LLMRunFrame` for the greeting.
5. **`failed_stage` and `describe_failure`**: errors that say which provider failed.
6. **The transcript path:** the aggregator events in `ModularAgent`, the server's
   `send_signal`, and the browser's `signal:transcript` handler, which ignores signals
   that didn't come from the server (`event.from`).
7. **Keyterms:** `DEEPGRAM_KEYTERMS=Vonage,Pipecat`, with before-and-after screenshots.

## Screenshot and video opportunities

- The call page mid-conversation, with the *The AI agent is speaking* status.
- The server log with `Transcript user:` and `Transcript agent:` lines
  (`LOG_TRANSCRIPTS=true`), showing each stage's output.
- The page showing *Text-to-speech (ElevenLabs) failed … Check ELEVENLABS_API_KEY.*
- Video: design a voice with ElevenLabs Voice Design, set `ELEVENLABS_VOICE_ID`, restart,
  and hear it. (Needs a paid ElevenLabs plan.)

## Links

- Vonage Video Transport for Pipecat: https://developer.vonage.com/en/video/guides/vonage-video-connector-pipecat-transport
- Pipecat: https://docs.pipecat.ai
- Deepgram in Pipecat: https://docs.pipecat.ai/api-reference/server/services/stt/deepgram
- ElevenLabs models: https://elevenlabs.io/docs/overview/models
- ElevenLabs voices and plans: https://elevenlabs.io/docs/overview/capabilities/voices
- Part 1 (optional reading): link once published
- Part 3, an AI historical character built on this pipeline: link once published
