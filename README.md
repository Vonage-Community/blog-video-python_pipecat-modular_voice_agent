# Custom AI voice agent in a Vonage Video call: Deepgram, OpenAI, and ElevenLabs (Pipecat)

Talk to an AI voice agent that joins your Vonage Video session as a real participant.
Deepgram transcribes what you say, OpenAI writes the reply, and ElevenLabs speaks it,
each configured separately, all connected by a [Pipecat](https://docs.pipecat.ai)
pipeline.

> **Docker required.** The native Vonage Video Connector SDK runs on Linux, so the
> agent runs in a Linux container. macOS and Windows users can use Docker Desktop;
> your browser connects to it on `localhost`.

## What you will build

- A web page where you click **Start conversation**, allow your microphone, and talk.
- A small FastAPI server that creates a **routed** Vonage Video session and two
  separate tokens: one for your browser, one for the agent. Only the browser's
  token ever leaves the server.
- An AI participant that joins the same session with `VonageVideoConnectorTransport`
  and runs three services in a row: **Deepgram** speech-to-text, an **OpenAI** text
  model, and **ElevenLabs** text-to-speech. The spoken reply is published back into
  the session.
- A **transcript panel** shows the text between the stages as you talk: what
  Deepgram heard you say, and what the agent said. It reaches the page as Vonage
  Video signals, and nothing is stored.
- You hear the agent **through your Vonage subscription**, not through a direct
  connection to any provider. Talk over it to interrupt; mute, leave, or let it time
  out, and the agent leaves the call and closes all three provider connections.

The agent is **audio-only**. The page shows a labeled placeholder for it, not a fake
video. Your camera is optional.

### Why three services instead of one?

A speech-to-speech model is the quickest way to get an agent talking, but it ties
listening, thinking, and the voice to one provider. Splitting them gives you
independent control: pick any ElevenLabs voice, use your own prompt and OpenAI model,
or teach Deepgram the words it should listen for, each with one line in `.env`.
Because the stages pass plain text to each other, the page can show you exactly what
the agent heard and said, and when something goes wrong, the error names the stage
that failed. That control matters once the agent needs a
specific character and voice.

## Architecture

```text
┌────────────────────────────────┐                         ┌────────────────────────────────┐
│ Browser: Vonage Video Web SDK  │    POST /api/sessions   │ FastAPI server                 │
│ • publishes your microphone    │  ─────────────────────► │ • routed session + 2 tokens    │
│ • plays only the stream whose  │  ◄───────────────────── │ • limits, timeouts, ready/end  │
│   connection data says agent   │    browser token only   │ • one process per agent        │
└────────────────┬───────────────┘                         └────────────────┬───────────────┘
                 │ WebRTC                                                   │ starts it (agent token)
                 ▼                                                          ▼
┌────────────────────────────────┐                         ┌────────────────────────────────┐
│ Vonage Video API               │◄──────── WebRTC ───────►│ Agent process: Pipecat         │
│ routed session                 │                         │ VonageVideoConnectorTransport  │
└────────────────────────────────┘                         │ → DeepgramSTTService ──────────┼──► Deepgram
                                                           │ → user turn (VAD, Smart Turn)  │
                                                           │ → OpenAILLMService ────────────┼──► OpenAI
                                                           │ → ElevenLabsTTSService ────────┼──► ElevenLabs
                                                           │ → VonageVideoConnectorTransport│
                                                           └────────────────────────────────┘
```

| Piece | Role |
|---|---|
| Vonage Video API | The call: a routed session that carries audio between you and the agent. |
| Vonage Video Web SDK | Your side: microphone, optional camera, playing the agent's audio. |
| Vonage Video Connector + Pipecat transport | The agent's side: joins the session from Python as a WebRTC participant. |
| Deepgram (`nova-3-general`) | Streaming speech-to-text: turns your speech into text. |
| Silero VAD + Smart Turn v3 | Run locally in the container and decide when you've finished speaking. |
| OpenAI (`gpt-4.1`) | Writes a short text reply from the conversation so far. |
| ElevenLabs (`eleven_flash_v2_5`) | Streaming text-to-speech in the voice you choose. |
| FastAPI | Creates sessions and tokens, keeps keys server-side, starts and stops agents. |

Each session's agent runs in its own process because the Video Connector supports
one connector instance per process. It also means a stuck agent can always be killed.

## Prerequisites

- **A Vonage account** and the [Vonage CLI](https://github.com/Vonage/vonage-cli)
  3.3.1 or newer (`npm install -g @vonage/cli`), to create the Video application
  (steps below).
- **A Deepgram API key.** Create one in the [Deepgram Console](https://console.deepgram.com).
- **An OpenAI API key** with API billing set up, from
  [platform.openai.com/api-keys](https://platform.openai.com/api-keys). A ChatGPT
  subscription does not include API credits.
- **An ElevenLabs API key on a paid plan** (Starter or above), from
  [ElevenLabs API keys](https://elevenlabs.io/app/developers/api-keys). The free plan
  can't use Voice Library voices, including this sample's default, through the API.
- **Docker** with Docker Compose v2 (Docker Desktop on macOS/Windows).
- A current **Chrome, Edge, Firefox, or Safari** and a microphone. Headphones help.
- **Outbound network access** from the container to Vonage (HTTPS plus WebRTC media),
  `api.deepgram.com`, `api.openai.com`, and `api.elevenlabs.io`.
- Open the app at `http://localhost`. Browsers only grant microphone access on
  `localhost` or HTTPS, so opening it from another device needs HTTPS.

All four services bill per use; see [Limitations and costs](#limitations-and-costs).

## Quick start

```bash
git clone https://github.com/Vonage-Community/blog-video-python_pipecat-modular_voice_agent.git
cd blog-video-python_pipecat-modular_voice_agent
```

### 1. Create the Vonage application

Authenticate the CLI with the API key and secret from the
[Vonage API Dashboard](https://dashboard.vonage.com), then create the application.
`--private-key-file` saves its private key straight into `secrets/`:

```bash
vonage auth set --api-key='YOUR_API_KEY' --api-secret='YOUR_API_SECRET'
vonage apps create "Pipecat Modular Voice Agent" --private-key-file ./secrets/private.key
```

Copy the **Application ID** from the output, then turn on Video for it:

```bash
vonage apps capabilities update <APPLICATION_ID> video > /dev/null
vonage apps show <APPLICATION_ID>
```

At the time of writing, Vonage CLI 3.3.1 prints your stored credentials when it
updates the Video capability, so the command discards its output (errors still
show). `vonage apps show` confirms the application now lists `VIDEO`.

### 2. Configure the app

```bash
cp .env.example .env
```

Edit `.env` and set the application ID and the three provider keys:

```dotenv
VONAGE_APPLICATION_ID=your-application-id
DEEPGRAM_API_KEY=your-deepgram-key
OPENAI_API_KEY=sk-...
ELEVENLABS_API_KEY=sk_...
```

### 3. Set the private key's permissions

```bash
chmod 700 secrets              # other users on this machine can't open the folder
chmod 644 secrets/private.key  # the container's unprivileged user can read the file
```

`secrets/` is ignored by Git and excluded from the Docker build. Compose mounts the key
read-only into the container at `/run/secrets/vonage_private_key`.

### 4. Start it

```bash
docker compose up --build
```

The first build downloads Python dependencies and takes a few minutes. When it's
ready you'll see:

```text
app-1  | … | INFO    | server | app.main:… - Ready: agent=Deepgram nova-3-general -> OpenAI gpt-4.1 -> ElevenLabs eleven_flash_v2_5 (voice l7kNoIfnJKPg7779LI2t); max 1 session(s) of 600s; idle timeout 120s
app-1  | INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```

If any setting is missing, the server still starts and lists what to fix, in the
logs and on the page.

### 5. Talk to it

Open **<http://localhost:8000>**, click **Start conversation**, and allow microphone
access. The agent joins within a few seconds and greets you once. Answer it, ask it
something, and talk over it to interrupt. Click **Leave** when you're done.

Stop the server with `Ctrl-C` (or `docker compose down`).

## Change the voice, the prompt, the model, or the vocabulary

Each stage is configured on its own:

- **Vocabulary:** Deepgram may hear "Pipecat" as "pipe cat". List the words it should
  listen for in `DEEPGRAM_KEYTERMS` (for example `DEEPGRAM_KEYTERMS=Vonage,Pipecat`),
  restart, and watch the transcript panel spell them correctly. This is Deepgram's
  [keyterm prompting](https://developers.deepgram.com/docs/keyterm): it works with
  Nova-3 models and is billed as a small per-minute add-on.

- **Voice:** set `ELEVENLABS_VOICE_ID` to any voice ID from the ElevenLabs
  [Voice Library](https://elevenlabs.io/app/voice-library) (add it to My Voices, then
  copy its ID), or design a new one with
  [Voice Design](https://elevenlabs.io/docs/eleven-creative/voices/voice-design) by
  describing it in a sentence. The default is *Eddie - Natural and Helpful*. The
  ElevenLabs dashboard's own voice and model settings don't affect this app. Use a voice you're
  licensed to use; don't clone a real person's voice without their permission.
- **Model:** set `OPENAI_MODEL` to another OpenAI chat model your key can use. Voice
  needs fast first tokens, so prefer models without long reasoning.
- **Language:** set `DEEPGRAM_LANGUAGE` (for example `es`), and the prompt already tells
  the agent to reply in the language you speak. `eleven_flash_v2_5` speaks 32 languages.
- **Prompt:** edit `SYSTEM_INSTRUCTION` in [`app/modular_agent.py`](app/modular_agent.py).

Restart `docker compose up` after changing `.env`.

## Configuration

All settings live in `.env` ([`.env.example`](.env.example) documents each one).

| Variable | Default | Purpose |
|---|---|---|
| `VONAGE_APPLICATION_ID` | (required) | Vonage application ID (UUID) with Video enabled. |
| `VONAGE_PRIVATE_KEY_FILE` | `./secrets/private.key` | Host path of the private key that Compose mounts into the container. |
| `DEEPGRAM_API_KEY` | (required for `modular` mode) | Deepgram API key. |
| `DEEPGRAM_MODEL` | `nova-3-general` | Deepgram streaming model. |
| `DEEPGRAM_LANGUAGE` | `en` | Language code for transcription. |
| `DEEPGRAM_KEYTERMS` | (empty) | Comma-separated words Deepgram should listen for (up to 50). |
| `OPENAI_API_KEY` | (required for `modular` mode) | OpenAI API key. |
| `OPENAI_MODEL` | `gpt-4.1` | OpenAI chat model that writes the replies. |
| `ELEVENLABS_API_KEY` | (required for `modular` mode) | ElevenLabs API key (paid plan). |
| `ELEVENLABS_VOICE_ID` | `l7kNoIfnJKPg7779LI2t` | ElevenLabs voice ID. |
| `ELEVENLABS_MODEL` | `eleven_flash_v2_5` | ElevenLabs text-to-speech model. |
| `AGENT_MODE` | `modular` | `echo` is a no-provider diagnostic: the agent beeps, then echoes your mic. |
| `MAX_ACTIVE_SESSIONS` | `1` | Concurrent sessions (1–5). Each runs its own agent process. |
| `MAX_SESSION_SECONDS` | `600` | Hard limit per session. |
| `IDLE_TIMEOUT_SECONDS` | `120` | Ends a session when neither side has spoken for this long. |
| `READY_TIMEOUT_SECONDS` | `60` | Ends a session if the browser never becomes ready to play the agent. |
| `SESSION_CREATES_PER_MINUTE` | `6` | Rate limit on starting sessions. |
| `LOG_LEVEL` | `INFO` | `DEBUG` also enables the native connector's own, unredacted logs. |
| `LOG_TRANSCRIPTS` | `false` | Also write what both sides say to the server logs. The page always shows it; nothing is stored. |
| `PORT` | `8000` | Port on `127.0.0.1` where the page is served. |

## How to verify it works

A working call goes through these states on the page; each comes from a real SDK
or server event:

1. *Asking for microphone access…* → *Starting a session…* → *Connecting to Vonage Video…*
2. *Connected. Waiting for the AI agent to join…*
3. *The AI agent joined. Connecting to its audio…* (the agent's stream arrived)
4. *Starting the conversation…* → the agent greets you.
5. While talking: *The AI agent is speaking*, *Listening…*, or *Your turn*. These are
   driven by the audio levels of the two streams. The **Can hear you** badge appears
   once the agent's subscription to your microphone is connected.

The server logs follow along (lines from the agent process are labeled `agent <id>`):

```text
server         | Session …: created (Vonage session …)
server         | Started agent process …
agent …        | Agent joined the Vonage Video session
agent …        | Stream … received from a 'user' participant
agent …        | Agent is receiving the user's audio
server         | Session …: browser ready, conversation started
agent …        | Browser is ready: opening the microphone and requesting a greeting
```

Smoke test:

- [ ] You hear one greeting, once, without speaking first.
- [ ] The agent answers a question, and stops when you talk over it.
- [ ] A conversation of several turns stays on topic (the agent remembers context).
- [ ] The transcript panel shows a *Deepgram heard:* line for each thing you say and
      an *Agent said:* line for each reply.
- [ ] **Mute** stops it from hearing you; **Unmute** restores it.
- [ ] **Leave** returns you to the start screen, and the logs show `ended (left)`.
- [ ] Closing the tab mid-call also ends the session within a few seconds.

To check the Vonage audio path without any AI provider, set `AGENT_MODE=echo` and
restart: you should hear a beep, then your own voice.
[docs/MANUAL_TEST.md](docs/MANUAL_TEST.md) has the full checklist.

To run the automated tests (Linux container, no credentials needed):

```bash
docker build --target test -t modular-voice-agent:test .
```

## Troubleshooting

When a provider fails, the page names the stage, for example
**Speech-to-text (Deepgram) failed: … Check DEEPGRAM_API_KEY.**

| Symptom | What to check |
|---|---|
| Page says **The server is not configured yet** | Fix each listed item in `.env` (or `secrets/`), then restart `docker compose up`. |
| `Cannot read the Vonage private key … permission denied` | Run `chmod 644 secrets/private.key` (keep `chmod 700 secrets`). The container runs as an unprivileged user. |
| **Vonage rejected the credentials (401)** | `VONAGE_APPLICATION_ID` and `private.key` must come from the same application. A regenerated key replaces the old one. |
| **Vonage refused the request (403)** | Turn on Video: `vonage apps capabilities update <APPLICATION_ID> video > /dev/null`. |
| `VONAGE_APPLICATION_ID looks like a legacy OpenTok project API key` | This sample needs a Vonage application ID (a UUID), not an OpenTok API key and secret. |
| Browser: **Could not connect to Vonage Video (1004)** | The token was rejected. Check that the app ID and key match, and that your computer's clock (and Docker Desktop's, after sleep) is correct. |
| **Speech-to-text (Deepgram) failed** | Check `DEEPGRAM_API_KEY`, your Deepgram balance, and that `DEEPGRAM_MODEL` supports `DEEPGRAM_LANGUAGE`. |
| **LLM (OpenAI) failed** | Check `OPENAI_API_KEY`, API billing (ChatGPT plans don't include API usage), and that your key can use `OPENAI_MODEL`. |
| **Text-to-speech (ElevenLabs) failed** with a plan or permission error | Voice Library voices need a paid ElevenLabs plan through the API. Check `ELEVENLABS_VOICE_ID` too. |
| The agent greets you but never answers | No *Deepgram heard:* lines in the transcript panel means Deepgram isn't hearing you: check mute, your mic, and the **Can hear you** badge. |
| The voice isn't the one you picked in the ElevenLabs dashboard | The dashboard's voice and model only apply to its own playground. This app uses `ELEVENLABS_VOICE_ID` and `ELEVENLABS_MODEL` from `.env`. |
| `Error adding Ice Candidate: The remote description was null` in the logs | Logged by the native connector while the agent connects. Harmless if the call works. |
| `ModuleNotFoundError: vonage_video_connector` | You ran the agent outside Docker on macOS or Windows. The connector ships Linux wheels only; use `docker compose up`. |
| **Microphone access is blocked** | Allow the microphone in the browser's site settings. Use `http://localhost:8000`, not a LAN IP (that needs HTTPS). |
| **Your browser blocked audio playback** | Click **Enable audio**. Browsers block autoplay until you interact with the page. |
| Stuck on *Waiting for the AI agent to join* | Read the server logs. A failed join ends the session with the reason shown on the page; if nothing ends it, the ready timeout does after 60 s. |
| **Timed out connecting to Vonage Video and the AI services** | The container can't reach one of them: a proxy or firewall is dropping traffic. It needs outbound HTTPS and WebRTC media (UDP, with TCP fallback). |
| You hear yourself, or the agent keeps interrupting itself | Use headphones; the agent hears your speakers otherwise. Browser echo cancellation is on by default. |
| `Bind for 127.0.0.1:8000 failed: port is already allocated` | Set `PORT=8001` in `.env` and open `http://localhost:8001`. |
| Build fails on an ARM machine | Wheels exist for Linux arm64, so a native build should work; update Docker Desktop first. Forcing `linux/amd64` runs under emulation and is slow. |
| **A demo session is already running** | Leave the other tab's call, or wait for it to time out. `MAX_ACTIVE_SESSIONS` sets the limit. |

`AGENT_MODE=echo` separates Vonage problems from provider problems: if you hear the
beep and your echo, Vonage works end to end.

## Limitations and costs

- **A local demo, not a production service.** There are no user accounts; anyone who
  can reach the port can start a (billed) session. Compose binds it to `127.0.0.1`
  for that reason. Session keys protect each session, and limits cap usage, but
  that isn't production authentication.
- **You pay for usage, with four accounts.** Vonage Video bills per
  participant-minute, and both you and the agent are connected for the whole call (see
  [Vonage's participant pricing](https://api.support.vonage.com/hc/en-us/articles/6646227378204-How-does-participant-based-video-pricing-work)).
  Deepgram bills streaming audio time, OpenAI bills text tokens, and ElevenLabs bills
  characters spoken. The defaults cap a session at 10 minutes, 2 minutes of silence,
  and one session at a time. No audio goes to Deepgram until the browser is ready.
- **Nothing is recorded.** The app stores no audio, video, or transcripts. The
  transcript panel lives only in your open page, and logs omit what you say unless
  you set `LOG_TRANSCRIPTS=true`.
- **The agent only listens.** It doesn't see your camera, and it has no avatar.
- **One room per session.** Each session holds your browser and one agent; other
  streams are ignored by the page.
- Tested platforms: see [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md).

## Cleanup

Stop the app and remove its image, then delete the Vonage application if you no
longer need it:

```bash
docker compose down --rmi local
vonage apps delete <APPLICATION_ID>
```

Revoke the Deepgram, OpenAI, and ElevenLabs keys in their dashboards if you created
them only for this sample.

## Project layout

```text
app/main.py            FastAPI routes, static files, startup checks
app/config.py          settings from .env, validation, log redaction
app/video_sessions.py  Vonage routed session + per-participant tokens
app/session_manager.py session registry, supervisor, limits and timeouts
app/agent_process.py   one process per agent, and the pipe protocol between them
app/modular_agent.py   the Pipecat pipeline: Vonage → Deepgram → OpenAI → ElevenLabs → Vonage
web/                   index.html, app.js, styles.css (no build step)
tests/                 unit and integration tests (no network needed)
docs/                  dependency notes, manual test plan, blog notes
```

## Related reading (optional)

- Blog post: *Build a Custom AI Voice Agent With Vonage, Deepgram, and ElevenLabs*
  (link coming when it's published)
- [vonage-pipecat-openai-realtime](https://github.com/Vonage-Community/vonage-pipecat-openai-realtime):
  the same app with one OpenAI Realtime speech-to-speech model instead of three
  services. You don't need it to run this one.
- [Vonage Video Transport for Pipecat](https://developer.vonage.com/en/video/guides/vonage-video-connector-pipecat-transport)
  and the [Pipecat documentation](https://docs.pipecat.ai)
- [Vonage Video getting started](https://developer.vonage.com/en/video/getting-started)
- [Deepgram speech-to-text in Pipecat](https://docs.pipecat.ai/api-reference/server/services/stt/deepgram),
  [ElevenLabs models](https://elevenlabs.io/docs/overview/models)

## License, privacy and attributions

[MIT](LICENSE). This repository started from a copy of
[vonage-pipecat-openai-realtime](https://github.com/Vonage-Community/vonage-pipecat-openai-realtime)
(MIT, © Vonage). Pipecat is BSD-2-Clause, © Daily. The Vonage Video Web SDK and Video
Connector are used under [Vonage's terms](https://www.vonage.com/legal/communications-apis/terms-of-use/).

**Privacy:** during a call your microphone audio travels from your browser through
Vonage Video to the agent container and on to Deepgram. The transcript and the
conversation go to OpenAI, and the agent's replies go to ElevenLabs. Each provider's
API data policies apply to what it receives. This app keeps nothing after the call ends.

**AI disclosure:** the agent and its voice are AI-generated. It can be wrong, and the
page says so.
