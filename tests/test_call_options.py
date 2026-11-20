from __future__ import annotations

import pytest

from app.call_options import (
    REPLY_PAUSES,
    CallOptions,
    CallOptionsError,
    VoiceDirectory,
    _voice_from,
    parse_call_options,
)

EDDIE = "l7kNoIfnJKPg7779LI2t"


def problems(raw, settings) -> list[str]:
    with pytest.raises(CallOptionsError) as info:
        parse_call_options(raw, settings)
    return info.value.problems


def test_no_options_means_the_env_defaults(settings):
    assert parse_call_options(None, settings) == CallOptions.defaults(settings)
    assert parse_call_options({}, settings) == CallOptions.defaults(settings)


def test_valid_choices_are_applied_to_the_agent_settings(settings):
    options = parse_call_options(
        {
            "language": "es",
            "keyterms": [" Vonage ", "Pipecat", "Vonage", ""],
            "openaiModel": "gpt-4.1-mini",
            "instructions": "  Answer like a patient tutor.\u0007 ",
            "voiceId": "OZ0L6eISlOejga3XjDFt",
            "voiceSpeed": 1.1,
            "interruptMinWords": 2,
            "replyPause": "patient",
        },
        settings,
    )
    assert options.keyterms == ("Vonage", "Pipecat")
    assert options.instructions == "Answer like a patient tutor."

    agent = options.apply_to(settings)
    assert agent.deepgram_language == "es"
    assert agent.deepgram_keyterms == ("Vonage", "Pipecat")
    assert agent.openai_model == "gpt-4.1-mini"
    assert agent.extra_instructions == "Answer like a patient tutor."
    assert agent.elevenlabs_voice_id == "OZ0L6eISlOejga3XjDFt"
    assert agent.elevenlabs_speed == 1.1
    assert agent.interrupt_min_words == 2
    assert agent.vad_stop_secs == REPLY_PAUSES["patient"]
    # Keys and limits are never touched by the browser.
    assert agent.openai_api_key is settings.openai_api_key
    assert agent.max_session_seconds == settings.max_session_seconds


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"language": "xx"}, "language must be one of"),
        ({"keyterms": "Vonage"}, "keyterms must be a list"),
        ({"keyterms": [f"t{i}" for i in range(21)]}, "at most 20"),
        ({"openaiModel": "gpt-5-pro"}, "openaiModel must be one of"),
        ({"instructions": "x" * 501}, "500 characters"),
        ({"instructions": 5}, "instructions must be text"),
        ({"voiceId": "../../etc/passwd"}, "voiceId must be"),
        ({"voiceSpeed": 3}, "voiceSpeed must be between"),
        ({"voiceSpeed": True}, "voiceSpeed must be between"),
        ({"interruptMinWords": 9}, "between 1 and 5"),
        ({"interruptMinWords": 2.5}, "whole number"),
        ({"replyPause": "forever"}, "replyPause must be one of"),
    ],
)
def test_invalid_choices_are_rejected(settings, raw, message):
    assert any(message in p for p in problems(raw, settings))


def test_options_must_be_an_object(settings):
    assert problems(["en"], settings) == ["options must be an object"]


def test_options_round_trip_over_the_pipe(settings):
    options = parse_call_options({"keyterms": ["Vonage"], "voiceId": EDDIE}, settings)
    assert CallOptions.from_json(options.to_json()) == options


def test_account_voices_are_summarized():
    voice = _voice_from(
        {
            "voice_id": EDDIE,
            "name": "Eddie - Natural and Helpful",
            "labels": {"accent": "american", "gender": "male", "age": "young", "x": "y"},
        }
    )
    assert (voice.id, voice.description) == (EDDIE, "american, male, young")


async def test_voice_directory_needs_a_key(settings):
    from tests.conftest import with_settings

    directory = VoiceDirectory(with_settings(settings, elevenlabs_api_key=None))
    assert await directory.account_voices() == ([], "unavailable")


async def test_voice_directory_caches_and_reports_missing_permission(settings, monkeypatch):
    calls = []

    async def fake_fetch(self, api_key):
        calls.append(api_key)
        return [], "missing_permission"

    monkeypatch.setattr(VoiceDirectory, "_fetch", fake_fetch)
    directory = VoiceDirectory(settings)
    assert await directory.account_voices() == ([], "missing_permission")
    await directory.account_voices()
    assert len(calls) == 1
