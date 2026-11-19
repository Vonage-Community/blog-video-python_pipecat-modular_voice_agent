"""The Pipecat pipeline, built with the real (pinned) Pipecat and Vonage classes.

These tests need the native Vonage Video Connector, which installs only on Linux,
so they are skipped elsewhere. Run them in Docker:
    docker build --target test .
Nothing here connects to Vonage or any AI provider successfully.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("vonage_video_connector", reason="native connector is Linux-only")

from pipecat.frames.frames import (  # noqa: E402
    ErrorFrame,
    InputAudioRawFrame,
    LLMRunFrame,
    OutputAudioRawFrame,
)
from pipecat.services.deepgram.stt import DeepgramSTTService  # noqa: E402
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService  # noqa: E402
from pipecat.services.openai.llm import OpenAILLMService  # noqa: E402
from pipecat.tests.utils import run_test  # noqa: E402
from pipecat.transports.vonage.video_connector import (  # noqa: E402
    VonageVideoConnectorTransport,
    VonageVideoConnectorTransportParams,
)
from pipecat.utils.errors import ErrorCategory  # noqa: E402

from app.agent_process import ProcessAgent  # noqa: E402
from app.modular_agent import (  # noqa: E402
    EchoAgent,
    MicrophoneEcho,
    MicrophoneGate,
    ModularAgent,
    beep,
    connection_role,
    create_agent,
    describe_failure,
    failed_stage,
)
from app.session_manager import AgentConnection  # noqa: E402
from tests.conftest import with_settings  # noqa: E402
from tests.test_agent_process import RecordedEvents  # noqa: E402

CONNECTION = AgentConnection("app-id", "session-id", "agent-token")


@pytest.fixture(scope="module")
def transport():
    # One native client per test process: the connector warns on a second instance.
    return VonageVideoConnectorTransport(
        "app-id",
        "session-id",
        "agent-token",
        VonageVideoConnectorTransportParams(audio_in_enabled=True, audio_out_enabled=True),
    )


def pipeline_processors(worker) -> list:
    """Every processor in the worker's pipeline, in order, nested pipelines flattened."""

    def flatten(processor) -> list:
        children = processor.processors
        return [processor] if not children else [p for c in children for p in flatten(c)]

    return flatten(worker._pipeline)


def test_pipeline_runs_stt_then_llm_then_tts(settings, transport):
    agent = ModularAgent(settings, CONNECTION, RecordedEvents())
    worker = agent.build_pipeline(transport)
    services = [
        p
        for p in pipeline_processors(worker)
        if isinstance(
            p, (MicrophoneGate, DeepgramSTTService, OpenAILLMService, ElevenLabsTTSService)
        )
    ]
    assert [type(p) for p in services] == [
        MicrophoneGate,
        DeepgramSTTService,
        OpenAILLMService,
        ElevenLabsTTSService,
    ]


def test_services_use_the_configured_models_and_voice(settings, transport):
    custom = with_settings(
        settings,
        deepgram_model="nova-3-medical",
        deepgram_language="en-GB",
        openai_model="gpt-4.1-mini",
        elevenlabs_voice_id="OZ0L6eISlOejga3XjDFt",
        elevenlabs_model="eleven_multilingual_v2",
    )
    worker = ModularAgent(custom, CONNECTION, RecordedEvents()).build_pipeline(transport)
    by_type = {type(p): p for p in pipeline_processors(worker)}
    stt = by_type[DeepgramSTTService]._settings
    llm = by_type[OpenAILLMService]._settings
    tts = by_type[ElevenLabsTTSService]._settings
    assert (stt.model, str(stt.language)) == ("nova-3-medical", "en-GB")
    assert llm.model == "gpt-4.1-mini"
    assert "spoken aloud" in llm.system_instruction
    assert (tts.model, tts.voice) == ("eleven_multilingual_v2", "OZ0L6eISlOejga3XjDFt")


async def test_microphone_gate_drops_audio_until_open():
    mic = InputAudioRawFrame(audio=b"\x01\x00" * 160, sample_rate=16000, num_channels=1)

    down, _ = await run_test(MicrophoneGate(), frames_to_send=[mic])
    assert not any(isinstance(f, InputAudioRawFrame) for f in down)

    gate = MicrophoneGate()
    gate.open = True
    down, _ = await run_test(gate, frames_to_send=[mic])
    assert [f.audio for f in down if isinstance(f, InputAudioRawFrame)] == [mic.audio]


async def test_conversation_starts_once_and_opens_the_microphone(settings, transport):
    agent = ModularAgent(settings, CONNECTION, RecordedEvents())
    worker = agent.build_pipeline(transport)
    queued: list = []

    async def record(frames):
        queued.extend(frames)

    worker.queue_frames = record  # type: ignore[method-assign]

    await agent.start_conversation()  # before the worker exists: deferred
    assert queued == []
    assert agent._gate.open is False
    agent._worker = worker
    await agent._begin_once()
    await agent.start_conversation()  # again: ignored

    assert [type(frame) for frame in queued] == [LLMRunFrame]
    assert agent._gate.open is True


@pytest.mark.parametrize(
    ("service", "stage", "category", "hint"),
    [
        (
            DeepgramSTTService,
            "Speech-to-text (Deepgram)",
            ErrorCategory.AUTHENTICATION,
            "DEEPGRAM_API_KEY",
        ),
        (OpenAILLMService, "LLM (OpenAI)", ErrorCategory.INVALID_REQUEST, "OPENAI_MODEL"),
        (
            ElevenLabsTTSService,
            "Text-to-speech (ElevenLabs)",
            ErrorCategory.AUTHORIZATION,
            "paid plan",
        ),
    ],
)
def test_errors_name_the_failed_stage(settings, transport, service, stage, category, hint):
    worker = ModularAgent(settings, CONNECTION, RecordedEvents()).build_pipeline(transport)
    processor = next(p for p in pipeline_processors(worker) if isinstance(p, service))
    frame = ErrorFrame("rejected key sk-proj-AbCdEf0123456789xyz", processor=processor)
    frame.category = category
    assert failed_stage(frame) == stage
    message = describe_failure(stage, frame)
    assert message.startswith(f"{stage} failed:")
    assert hint in message
    assert "AbCdEf0123456789xyz" not in message


def test_errors_without_a_service_are_blamed_on_vonage():
    assert failed_stage(ErrorFrame("connection lost")) == "Vonage Video"


async def test_stop_before_run_never_joins(settings):
    agent = ModularAgent(settings, CONNECTION, RecordedEvents())
    await agent.stop()
    await asyncio.wait_for(agent.run(), 1)
    assert agent._runner is None


def test_create_agent_picks_the_mode(settings):
    assert isinstance(create_agent(settings, CONNECTION, RecordedEvents()), ModularAgent)
    echo = with_settings(
        settings,
        agent_mode="echo",
        deepgram_api_key=None,
        openai_api_key=None,
        elevenlabs_api_key=None,
    )
    assert isinstance(create_agent(echo, CONNECTION, RecordedEvents()), EchoAgent)


@pytest.mark.parametrize(
    ("client", "role"),
    [
        ({"connectionData": '{"role": "user"}'}, "user"),
        ({"connectionData": '{"role": "agent"}'}, "agent"),
        ({"connectionData": "not json"}, None),
        ({"connectionData": "[1, 2]"}, None),
        ({}, None),
    ],
)
def test_connection_role(client, role):
    assert connection_role(client) == role


def test_beep_is_audible_mono_pcm():
    frame = beep(seconds=0.5, sample_rate=16000)
    assert frame.sample_rate == 16000
    assert frame.num_channels == 1
    assert len(frame.audio) == 0.5 * 16000 * 2
    assert max(frame.audio) > 0


async def test_microphone_echo_only_when_enabled():
    mic = InputAudioRawFrame(audio=b"\x01\x00" * 160, sample_rate=16000, num_channels=1)

    echo = MicrophoneEcho()
    down, _ = await run_test(echo, frames_to_send=[mic])
    assert not any(isinstance(f, OutputAudioRawFrame) for f in down)

    echo = MicrophoneEcho()
    echo.enabled = True
    down, _ = await run_test(echo, frames_to_send=[mic])
    echoed = [f for f in down if isinstance(f, OutputAudioRawFrame)]
    assert len(echoed) == 1 and echoed[0].audio == mic.audio


async def test_real_agent_process_reports_a_failed_join(settings, valid_env, monkeypatch):
    """Full agent process with Pipecat and the native connector, bogus credentials.

    Whether or not the machine can reach Vonage, the join must fail, be reported,
    and the process must exit on its own.
    """
    for name, value in valid_env.items():
        monkeypatch.setenv(name, value)
    events = RecordedEvents()
    agent = ProcessAgent(
        settings,
        AgentConnection(valid_env["VONAGE_APPLICATION_ID"], "1_MX4wfmZha2V-fn4", "not-a-token"),
        events,
    )
    await asyncio.wait_for(agent.run(), 120)
    assert events.failures, "the failed join was not reported"
    assert "agent_joined" not in events.names
    assert agent._process is not None and agent._process.returncode is not None
