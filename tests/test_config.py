from __future__ import annotations

import os
from pathlib import Path

import pytest
from loguru import logger

from app.config import ConfigError, load_settings, redact, setup_logging


def problems_for(env: dict[str, str]) -> list[str]:
    with pytest.raises(ConfigError) as info:
        load_settings(env)
    return info.value.problems


def test_valid_environment_loads_with_defaults(valid_env):
    settings = load_settings(valid_env)
    assert settings.vonage_application_id == valid_env["VONAGE_APPLICATION_ID"]
    assert "BEGIN PRIVATE KEY" in settings.vonage_private_key.get()
    assert settings.deepgram_api_key is not None
    assert settings.deepgram_api_key.get() == valid_env["DEEPGRAM_API_KEY"]
    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get() == valid_env["OPENAI_API_KEY"]
    assert settings.elevenlabs_api_key is not None
    assert settings.elevenlabs_api_key.get() == valid_env["ELEVENLABS_API_KEY"]
    assert settings.deepgram_model == "nova-3-general"
    assert settings.deepgram_language == "en"
    assert settings.openai_model == "gpt-4.1"
    assert settings.elevenlabs_voice_id == "l7kNoIfnJKPg7779LI2t"
    assert settings.elevenlabs_model == "eleven_flash_v2_5"
    assert settings.agent_mode == "modular"
    assert settings.max_active_sessions == 1
    assert settings.max_session_seconds == 600
    assert settings.log_transcripts is False


def test_every_missing_setting_is_reported_at_once(tmp_path):
    problems = problems_for({"VONAGE_PRIVATE_KEY_PATH": str(tmp_path / "missing.key")})
    assert len(problems) == 5
    assert any("VONAGE_APPLICATION_ID is not set" in p for p in problems)
    assert any("No Vonage private key" in p for p in problems)
    assert any("DEEPGRAM_API_KEY is not set" in p for p in problems)
    assert any("OPENAI_API_KEY is not set" in p for p in problems)
    assert any("ELEVENLABS_API_KEY is not set" in p and "paid plan" in p for p in problems)


def test_legacy_opentok_api_key_is_explained(valid_env):
    problems = problems_for({**valid_env, "VONAGE_APPLICATION_ID": "47123456"})
    assert "legacy OpenTok" in problems[0]


def test_application_id_must_be_a_uuid(valid_env):
    problems = problems_for({**valid_env, "VONAGE_APPLICATION_ID": "not-a-uuid"})
    assert "must be a UUID" in problems[0]


def test_pkcs1_private_key_gets_a_conversion_hint(valid_env, tmp_path):
    key = tmp_path / "rsa.key"
    key.write_text("-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----\n")
    problems = problems_for({**valid_env, "VONAGE_PRIVATE_KEY_PATH": str(key)})
    assert "openssl pkcs8" in problems[0]


def test_non_pem_private_key_is_rejected(valid_env, tmp_path):
    key = tmp_path / "junk.key"
    key.write_text("this is not a key")
    problems = problems_for({**valid_env, "VONAGE_PRIVATE_KEY_PATH": str(key)})
    assert "does not contain a PEM private key" in problems[0]


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read any file")
def test_unreadable_private_key_explains_the_permission_fix(valid_env, private_key_file: Path):
    private_key_file.chmod(0o000)
    problems = problems_for(valid_env)
    assert "chmod 644" in problems[0]


def test_echo_mode_needs_no_provider_keys(valid_env):
    names = ("DEEPGRAM_API_KEY", "OPENAI_API_KEY", "ELEVENLABS_API_KEY")
    env = {k: v for k, v in valid_env.items() if k not in names}
    settings = load_settings({**env, "AGENT_MODE": "echo"})
    assert settings.agent_mode == "echo"
    assert settings.deepgram_api_key is None
    assert settings.openai_api_key is None
    assert settings.elevenlabs_api_key is None


@pytest.mark.parametrize("name", ["DEEPGRAM_API_KEY", "OPENAI_API_KEY", "ELEVENLABS_API_KEY"])
def test_each_missing_provider_key_is_named(valid_env, name):
    problems = problems_for({k: v for k, v in valid_env.items() if k != name})
    assert problems == [p for p in problems if p.startswith(f"{name} is not set")]
    assert len(problems) == 1


@pytest.mark.parametrize("name", ["DEEPGRAM_API_KEY", "OPENAI_API_KEY", "ELEVENLABS_API_KEY"])
def test_quoted_provider_key_is_rejected(valid_env, name):
    problems = problems_for({**valid_env, name: '"abc123"'})
    assert problems == [f"{name} contains spaces or quotes; paste the bare key"]


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("MAX_SESSION_SECONDS", "ten", "whole number"),
        ("MAX_SESSION_SECONDS", "5", "between 30 and 3600"),
        ("MAX_ACTIVE_SESSIONS", "50", "between 1 and 5"),
        ("LOG_TRANSCRIPTS", "maybe", "true or false"),
        ("AGENT_MODE", "gemini", "AGENT_MODE must be one of"),
        ("LOG_LEVEL", "LOUD", "LOG_LEVEL must be one of"),
    ],
)
def test_invalid_values_are_reported(valid_env, name, value, message):
    assert message in problems_for({**valid_env, name: value})[0]


def test_optional_settings_are_read(valid_env):
    settings = load_settings(
        {
            **valid_env,
            "DEEPGRAM_MODEL": "nova-3-medical",
            "DEEPGRAM_LANGUAGE": "en-GB",
            "OPENAI_MODEL": "gpt-4.1-mini",
            "ELEVENLABS_VOICE_ID": "OZ0L6eISlOejga3XjDFt",
            "ELEVENLABS_MODEL": "eleven_multilingual_v2",
            "MAX_SESSION_SECONDS": "120",
            "LOG_TRANSCRIPTS": "true",
        }
    )
    assert settings.deepgram_model == "nova-3-medical"
    assert settings.deepgram_language == "en-GB"
    assert settings.openai_model == "gpt-4.1-mini"
    assert settings.elevenlabs_voice_id == "OZ0L6eISlOejga3XjDFt"
    assert settings.elevenlabs_model == "eleven_multilingual_v2"
    assert settings.max_session_seconds == 120
    assert settings.log_transcripts is True


def test_secrets_never_appear_in_reprs_or_errors(valid_env, private_key_pem):
    settings = load_settings(valid_env)
    text = repr(settings) + str(settings.vonage_private_key)
    text += str(settings.deepgram_api_key) + str(settings.openai_api_key)
    text += str(settings.elevenlabs_api_key)
    for name in ("DEEPGRAM_API_KEY", "OPENAI_API_KEY", "ELEVENLABS_API_KEY"):
        assert valid_env[name] not in text
    assert "BEGIN PRIVATE KEY" not in text

    with pytest.raises(ConfigError) as info:
        load_settings({**valid_env, "VONAGE_APPLICATION_ID": "bad", "OPENAI_API_KEY": "sk 1"})
    assert "sk 1" not in str(info.value)


@pytest.mark.parametrize(
    "secret",
    [
        "sk-proj-AbCdEf0123456789xyz",
        "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ2aWRlbyJ9.c2lnbmF0dXJl",
        "T1==cGFydG5lcl9pZD0xMjM0NTY3OCZzaWc9YWJj",
        # Shaped like Deepgram and ElevenLabs keys; not real.
        "0123456789abcdef0123456789abcdef01234567",
        "sk_0123456789abcdef0123456789abcdef0123456789abcdef",
        "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBg\n-----END PRIVATE KEY-----",
    ],
)
def test_redact_masks_credentials(secret):
    assert secret not in redact(f"before {secret} after")


@pytest.mark.parametrize(
    "line",
    [
        "Authorization: Bearer abc123",
        "Authorization: Token abc123",
        "xi-api-key: abc123",
        "wss://api.elevenlabs.io/v1?xi_api_key=abc123&model=x",
    ],
)
def test_redact_masks_header_and_query_values(line):
    assert "abc123" not in redact(line)


def test_redact_leaves_ordinary_words_alone():
    line = "Agent token sent over the pipe"
    assert redact(line) == line


def test_log_output_is_redacted(capsys):
    setup_logging("INFO")
    logger.info("calling OpenAI with sk-fake-1234567890abcdef")
    try:
        raise RuntimeError("token eyJhbGciOiJSUzI1NiJ9.eyJhIjoxfQ.c2ln leaked")
    except RuntimeError:
        logger.exception("failure")
    err = capsys.readouterr().err
    assert "sk-fake-1234567890abcdef" not in err
    assert "eyJhbGciOiJSUzI1NiJ9.eyJhIjoxfQ.c2ln" not in err
    assert "sk-[redacted]" in err
    assert "[jwt redacted]" in err
