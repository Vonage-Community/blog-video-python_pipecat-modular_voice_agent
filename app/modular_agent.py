"""The AI participant: a modular Pipecat pipeline that joins the Vonage Video session.

    Vonage Video session (routed)
        │  your microphone audio
        ▼
    VonageVideoConnectorTransport.input()
        ▼
    MicrophoneGate          drops your audio until the browser can hear the agent
        ▼
    DeepgramSTTService      speech-to-text: your words as text
        ▼
    user aggregator         decides your turn is over (Silero VAD + Smart Turn v3)
        ▼
    OpenAILLMService        writes a text reply from the conversation so far
        ▼
    ElevenLabsTTSService    text-to-speech: the reply in the voice you picked
        ▼
    VonageVideoConnectorTransport.output()
        │  the agent's voice, published as a Vonage stream
        ▼
    assistant aggregator    adds what the agent actually said to the conversation

Each stage is a separate service with its own key, model, and settings. The browser
never talks to any of them directly: every sound reaches you through Vonage Video.
"""

from __future__ import annotations

import json
import re
from typing import Any

import numpy as np
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    ErrorFrame,
    Frame,
    InputAudioRawFrame,
    LLMRunFrame,
    OutputAudioRawFrame,
)
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker, ProcessorUnusablePolicy
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.services.llm_service import LLMService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.stt_service import STTService
from pipecat.services.tts_service import TTSService
from pipecat.transports.vonage.video_connector import (
    VonageVideoConnectorTransport,
    VonageVideoConnectorTransportParams,
)
from pipecat.utils.errors import ErrorCategory
from pipecat.workers.runner import WorkerRunner

from app.config import Settings, redact
from app.session_manager import AgentConnection, AgentEvents

SYSTEM_INSTRUCTION = """\
You are a friendly AI voice assistant taking part in a Vonage Video call.
You can hear the user, but you cannot see them.
Everything you say is spoken aloud by a text-to-speech voice, so keep replies short
and conversational: one to three sentences unless the user asks for more. Write
plain sentences only: no lists, markdown, emoji, or symbols that can't be spoken.
Spell out numbers the way you would say them. You hear the user through a
speech-to-text service, so if a sentence looks garbled, ask them to repeat it.
You can't browse the internet or look up today's news, so say so instead of
guessing about current events.
If you are not sure about something, say so. If anyone asks, you are an AI
model, not a human."""

GREETING_PROMPT = (
    "The user has just joined the call and can hear you now. Greet them in one short "
    "sentence and ask what they would like to talk about."
)


def connection_role(client: dict[str, Any]) -> str | None:
    """Read the role our server put in the token's connection data."""
    try:
        data = json.loads(client.get("connectionData") or "{}")
    except (TypeError, ValueError):
        return None
    return data.get("role") if isinstance(data, dict) else None


class VonageAgent:
    """Runs one Pipecat pipeline inside one Vonage Video session.

    Subclasses build the pipeline. This base class wires up the Vonage transport
    events, the session lifecycle, and stopping.
    """

    def __init__(self, settings: Settings, connection: AgentConnection, events: AgentEvents):
        self._settings = settings
        self._connection = connection
        self._events = events
        self._runner: WorkerRunner | None = None
        self._worker: PipelineWorker | None = None
        self._stop_requested = False
        self._conversation_requested = False
        self._conversation_started = False
        self._joined = False
        self._failure_reported = False

    def build_pipeline(self, transport: VonageVideoConnectorTransport) -> PipelineWorker:
        raise NotImplementedError

    async def begin_conversation(self, worker: PipelineWorker) -> None:
        raise NotImplementedError

    async def run(self) -> None:
        """Join the session and run until stopped, idle, or failed."""
        if self._stop_requested:
            return

        transport = VonageVideoConnectorTransport(
            self._connection.application_id,
            self._connection.session_id,
            self._connection.token,
            VonageVideoConnectorTransportParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                # The browser shows this name next to the agent's stream.
                publisher_name="AI agent",
                # Subscribe to every audio stream in the session. Only the browser
                # holds the other token, so that is the user's microphone.
                audio_in_auto_subscribe=True,
                # The native SDK logs straight to stderr, bypassing our redaction.
                video_connector_log_level="INFO" if self._settings.log_level == "DEBUG" else "WARN",
            ),
        )
        self._register_transport_events(transport)

        worker = self.build_pipeline(transport)
        self._register_worker_events(worker)
        self._worker = worker
        if self._conversation_requested:
            await self._begin_once()

        runner = WorkerRunner(handle_sigint=False)
        self._runner = runner

        @runner.event_handler("on_ready")
        async def on_ready(runner: WorkerRunner) -> None:
            # stop() may have been called while the runner was starting.
            if self._stop_requested:
                await runner.cancel(reason="session ended")

        await runner.add_workers(worker)
        await runner.run()

        # Safety net: never end silently without having joined.
        if not self._joined and not self._stop_requested:
            self._report_failure(
                "The agent stopped before it joined the Vonage session. See the server logs."
            )

    async def start_conversation(self) -> None:
        """Called once the browser has subscribed to the agent and can play its audio."""
        self._conversation_requested = True
        if self._worker is not None:
            await self._begin_once()

    async def stop(self) -> None:
        """Leave the Vonage session and close provider connections. Idempotent."""
        self._stop_requested = True
        if self._runner is not None:
            await self._runner.cancel(reason="session ended")

    def _report_failure(self, message: str) -> None:
        if not self._failure_reported:
            self._failure_reported = True
            self._events.agent_failed(message)

    async def _begin_once(self) -> None:
        if self._conversation_started or self._worker is None:
            return
        self._conversation_started = True
        await self.begin_conversation(self._worker)

    def _register_transport_events(self, transport: VonageVideoConnectorTransport) -> None:
        @transport.event_handler("on_joined")
        async def on_joined(transport: VonageVideoConnectorTransport, data: dict) -> None:
            logger.info("Agent joined the Vonage Video session")
            self._joined = True
            self._events.agent_joined()

        @transport.event_handler("on_participant_joined")
        async def on_participant_joined(transport: VonageVideoConnectorTransport, client: dict):
            role = connection_role(client) or "unknown"
            logger.info(f"Stream {client['streamId']} received from a '{role}' participant")

        # Fires when the agent's subscription to a stream is connected, i.e. it
        # can now hear that participant.
        @transport.event_handler("on_client_connected")
        async def on_client_connected(transport: VonageVideoConnectorTransport, client: dict):
            if connection_role(client) == "user":
                logger.info("Agent is receiving the user's audio")
                self._events.user_audio_connected()

        @transport.event_handler("on_participant_left")
        async def on_participant_left(transport: VonageVideoConnectorTransport, client: dict):
            if connection_role(client) == "user":
                logger.info("The user's stream left the session")
                self._events.user_left()

        @transport.event_handler("on_error")
        async def on_error(transport: VonageVideoConnectorTransport, description: str) -> None:
            self._report_failure(f"Vonage Video Connector error: {description}")

    def _register_worker_events(self, worker: PipelineWorker) -> None:
        @worker.event_handler("on_pipeline_error")
        async def on_pipeline_error(worker: PipelineWorker, frame: ErrorFrame) -> None:
            stage = failed_stage(frame)
            # Services retry dropped connections on their own. Only end the session
            # when a service says it can't continue (a bad key, an unknown model or
            # voice, no credit), so the browser shows why instead of a silent agent.
            if not frame.fatal and (frame.processor is None or frame.processor.is_usable):
                logger.warning(f"{stage} reported a recoverable error: {redact(frame.error)}")
                return
            self._report_failure(describe_failure(stage, frame))
            if self._runner is not None:
                await self._runner.cancel(reason="pipeline error")

        @worker.event_handler("on_setup_timeout")
        async def on_setup_timeout(worker: PipelineWorker) -> None:
            # Connecting to Vonage or a provider hung (a firewall that drops packets,
            # for example). The worker stops itself after this.
            self._report_failure(
                "Timed out connecting to Vonage Video and the AI services. Check that the "
                "container can reach Vonage, Deepgram, OpenAI, and ElevenLabs."
            )

        @worker.event_handler("on_idle_timeout")
        async def on_idle_timeout(worker: PipelineWorker) -> None:
            # PipelineWorker cancels itself and the runner after this.
            self._events.idle_timeout()


# --- Which stage failed? -------------------------------------------------------------

# What to check for each stage, by the error category Pipecat reports.
_HINTS: dict[str, dict[ErrorCategory, str]] = {
    "Speech-to-text (Deepgram)": {
        ErrorCategory.AUTHENTICATION: "Check DEEPGRAM_API_KEY.",
        ErrorCategory.INVALID_REQUEST: "Check DEEPGRAM_MODEL and DEEPGRAM_LANGUAGE.",
        ErrorCategory.QUOTA: "Your Deepgram account is out of credit.",
    },
    "LLM (OpenAI)": {
        ErrorCategory.AUTHENTICATION: "Check OPENAI_API_KEY.",
        ErrorCategory.INVALID_REQUEST: "Check OPENAI_MODEL.",
        ErrorCategory.QUOTA: "Your OpenAI API account is out of credit.",
    },
    "Text-to-speech (ElevenLabs)": {
        ErrorCategory.AUTHENTICATION: "Check ELEVENLABS_API_KEY.",
        ErrorCategory.AUTHORIZATION: (
            "Your ElevenLabs plan can't use this voice through the API. Voice Library "
            "voices need a paid plan."
        ),
        ErrorCategory.INVALID_REQUEST: "Check ELEVENLABS_VOICE_ID and ELEVENLABS_MODEL.",
        ErrorCategory.QUOTA: "Your ElevenLabs account is out of credits.",
    },
}


def failed_stage(frame: ErrorFrame) -> str:
    """Name the pipeline stage an error came from."""
    processor = frame.processor
    if isinstance(processor, STTService):
        return "Speech-to-text (Deepgram)"
    if isinstance(processor, LLMService):
        return "LLM (OpenAI)"
    if isinstance(processor, TTSService):
        return "Text-to-speech (ElevenLabs)"
    return "Vonage Video"


# Provider SDKs put the full request headers in some errors. They're noise on the page.
_HEADERS = re.compile(r"headers: \{.*?\},?\s*")


def describe_failure(stage: str, frame: ErrorFrame) -> str:
    hint = _HINTS.get(stage, {}).get(frame.category, "") if frame.category else ""
    error = _HEADERS.sub("", redact(frame.error))
    return " ".join(part for part in (f"{stage} failed: {error}", hint) if part)


class MicrophoneGate(FrameProcessor):
    """Drops microphone audio until the browser can hear the agent.

    Nothing reaches Deepgram (or costs anything) before then, so the agent can't
    answer something you said before it greeted you.
    """

    def __init__(self) -> None:
        super().__init__()
        self.open = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame) and not self.open:
            return
        await self.push_frame(frame, direction)


class ModularAgent(VonageAgent):
    """Deepgram speech-to-text -> OpenAI LLM -> ElevenLabs text-to-speech."""

    def build_pipeline(self, transport: VonageVideoConnectorTransport) -> PipelineWorker:
        settings = self._settings
        assert settings.deepgram_api_key and settings.openai_api_key
        assert settings.elevenlabs_api_key

        self._gate = MicrophoneGate()

        # Listen: streaming transcription of the user's microphone.
        stt = DeepgramSTTService(
            api_key=settings.deepgram_api_key.get(),
            settings=DeepgramSTTService.Settings(
                model=settings.deepgram_model,
                language=settings.deepgram_language,
                # Keyterm prompting: boost words Deepgram would otherwise mishear.
                keyterm=list(settings.deepgram_keyterms) or None,
            ),
        )

        # Think: a text reply from the whole conversation so far.
        llm = OpenAILLMService(
            api_key=settings.openai_api_key.get(),
            settings=OpenAILLMService.Settings(
                model=settings.openai_model,
                system_instruction=SYSTEM_INSTRUCTION,
            ),
        )

        # Speak: stream the reply's sentences to ElevenLabs as they arrive.
        tts = ElevenLabsTTSService(
            api_key=settings.elevenlabs_api_key.get(),
            settings=ElevenLabsTTSService.Settings(
                model=settings.elevenlabs_model,
                voice=settings.elevenlabs_voice_id,
            ),
        )

        context = LLMContext([{"role": "developer", "content": GREETING_PROMPT}])
        # No speech-to-speech model decides when you've finished talking anymore, so
        # the user aggregator does: Silero VAD hears speech start and stop, and
        # Pipecat's default Smart Turn v3 model decides whether the sentence is done.
        # Both run locally on the CPU. Speaking over the agent interrupts it.
        user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
            context,
            user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
        )

        # The text between the stages: what Deepgram heard, and what the agent said.
        # Both go to your browser's transcript panel; logging them is optional.
        @user_aggregator.event_handler("on_user_turn_message_added")
        async def on_user_message(aggregator: Any, message: Any) -> None:
            self._send_transcript("user", message.content)

        @assistant_aggregator.event_handler("on_assistant_turn_stopped")
        async def on_assistant_message(aggregator: Any, message: Any) -> None:
            # Built from ElevenLabs' word timestamps, so after an interruption it holds
            # only the words that played.
            self._send_transcript("agent", message.content, interrupted=message.interrupted)

        # Logs, for each reply, how long you waited and which stage the time went to.
        latency = UserBotLatencyObserver()

        @latency.event_handler("on_latency_breakdown")
        async def on_latency_breakdown(observer: Any, breakdown: Any) -> None:
            logger.info("Latency: " + " | ".join(breakdown.turn_contribution_lines()))

        pipeline = Pipeline(
            [
                transport.input(),
                self._gate,
                stt,
                user_aggregator,
                llm,
                tts,
                transport.output(),
                assistant_aggregator,
            ]
        )
        return PipelineWorker(
            pipeline,
            params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
            observers=[latency],
            idle_timeout_secs=settings.idle_timeout_seconds,
            processor_unusable_policy=ProcessorUnusablePolicy.END,
            # The browser follows the call through Vonage events, not Pipecat's RTVI protocol.
            enable_rtvi=False,
        )

    def _send_transcript(self, role: str, text: str, interrupted: bool = False) -> None:
        if not text.strip():
            return
        if self._settings.log_transcripts:
            logger.info(f"Transcript {role}: {text}" + (" (interrupted)" if interrupted else ""))
        self._events.transcript(role, text, interrupted)

    async def begin_conversation(self, worker: PipelineWorker) -> None:
        logger.info("Browser is ready: opening the microphone and requesting a greeting")
        self._gate.open = True
        # LLMRunFrame sends the context (the greeting prompt) to OpenAI for a reply.
        await worker.queue_frames([LLMRunFrame()])


class MicrophoneEcho(FrameProcessor):
    """Turns incoming microphone audio into outgoing audio once enabled."""

    def __init__(self) -> None:
        super().__init__()
        self.enabled = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if self.enabled and isinstance(frame, InputAudioRawFrame):
            await self.push_frame(
                OutputAudioRawFrame(
                    audio=frame.audio,
                    sample_rate=frame.sample_rate,
                    num_channels=frame.num_channels,
                )
            )
        await self.push_frame(frame, direction)


def beep(seconds: float = 0.6, hz: float = 440.0, sample_rate: int = 24000) -> OutputAudioRawFrame:
    t = np.arange(int(seconds * sample_rate)) / sample_rate
    samples = (0.3 * 32767 * np.sin(2 * np.pi * hz * t)).astype(np.int16)
    return OutputAudioRawFrame(audio=samples.tobytes(), sample_rate=sample_rate, num_channels=1)


class EchoAgent(VonageAgent):
    """Diagnostic mode (AGENT_MODE=echo): no AI providers, no cost beyond Vonage.

    Plays a beep when the browser is ready, then echoes your microphone back.
    Hearing both proves audio flows in each direction through Vonage Video.
    """

    def build_pipeline(self, transport: VonageVideoConnectorTransport) -> PipelineWorker:
        self._echo = MicrophoneEcho()
        pipeline = Pipeline([transport.input(), self._echo, transport.output()])
        return PipelineWorker(
            pipeline,
            idle_timeout_secs=self._settings.idle_timeout_seconds,
            processor_unusable_policy=ProcessorUnusablePolicy.END,
            enable_rtvi=False,
        )

    async def begin_conversation(self, worker: PipelineWorker) -> None:
        logger.info("Browser is ready: playing a test beep, then echoing the microphone")
        await worker.queue_frames([beep()])
        self._echo.enabled = True


def create_agent(
    settings: Settings, connection: AgentConnection, events: AgentEvents
) -> VonageAgent:
    if settings.agent_mode == "echo":
        return EchoAgent(settings, connection, events)
    return ModularAgent(settings, connection, events)
