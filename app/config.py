"""Configuration from environment variables, plus log redaction.

Every problem is reported by variable name with a fix. Secret values never
appear in error messages, reprs, or logs.
"""

from __future__ import annotations

import os
import re
import sys
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

DEFAULT_PRIVATE_KEY_PATH = "/run/secrets/vonage_private_key"
DEFAULT_DEEPGRAM_MODEL = "nova-3-general"
DEFAULT_DEEPGRAM_LANGUAGE = "en"
DEFAULT_OPENAI_MODEL = "gpt-4.1"
# "Eddie - Natural and Helpful", one of the voices ElevenLabs recommends in place of its
# Default voices, which expire on December 31, 2026.
DEFAULT_ELEVENLABS_VOICE_ID = "l7kNoIfnJKPg7779LI2t"
DEFAULT_ELEVENLABS_MODEL = "eleven_flash_v2_5"
AGENT_MODES = ("modular", "echo")
MAX_KEYTERMS = 50
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


class ConfigError(Exception):
    """Configuration is missing or invalid. ``problems`` lists every issue found."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


class Secret:
    """A string that hides itself from repr(), str() and f-strings."""

    __slots__ = ("_value",)

    def __init__(self, value: str):
        self._value = value

    def get(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret('********')"

    __str__ = __repr__


@dataclass(frozen=True)
class Settings:
    vonage_application_id: str
    vonage_private_key: Secret = field(repr=False)
    deepgram_api_key: Secret | None = field(repr=False)
    openai_api_key: Secret | None = field(repr=False)
    elevenlabs_api_key: Secret | None = field(repr=False)
    deepgram_model: str = DEFAULT_DEEPGRAM_MODEL
    deepgram_language: str = DEFAULT_DEEPGRAM_LANGUAGE
    # Words Deepgram should listen for, such as product or people's names.
    deepgram_keyterms: tuple[str, ...] = ()
    openai_model: str = DEFAULT_OPENAI_MODEL
    elevenlabs_voice_id: str = DEFAULT_ELEVENLABS_VOICE_ID
    elevenlabs_model: str = DEFAULT_ELEVENLABS_MODEL
    # "modular" runs Deepgram -> OpenAI -> ElevenLabs. "echo" is a diagnostic that plays
    # a tone and echoes your microphone back through Vonage, with no AI providers.
    agent_mode: str = "modular"
    max_active_sessions: int = 1
    max_session_seconds: int = 600
    idle_timeout_seconds: int = 120
    ready_timeout_seconds: int = 60
    session_creates_per_minute: int = 6
    log_level: str = "INFO"
    log_transcripts: bool = False


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Read and validate settings. Raises ConfigError listing every problem."""
    env = os.environ if env is None else env
    problems: list[str] = []

    def text(name: str, default: str = "") -> str:
        return env.get(name, default).strip()

    def integer(name: str, default: int, low: int, high: int) -> int:
        raw = text(name)
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            problems.append(f"{name} must be a whole number (got {raw!r})")
            return default
        if not low <= value <= high:
            problems.append(f"{name} must be between {low} and {high} (got {value})")
            return default
        return value

    def boolean(name: str, default: bool) -> bool:
        raw = text(name).lower()
        if not raw:
            return default
        if raw in ("1", "true", "yes", "on"):
            return True
        if raw in ("0", "false", "no", "off"):
            return False
        problems.append(f"{name} must be true or false (got {raw!r})")
        return default

    application_id = text("VONAGE_APPLICATION_ID")
    if not application_id:
        problems.append(
            "VONAGE_APPLICATION_ID is not set. Copy it from your Vonage application "
            "(Vonage API Dashboard > Applications)"
        )
    elif application_id.isdigit():
        problems.append(
            "VONAGE_APPLICATION_ID looks like a legacy OpenTok project API key. This sample "
            "needs a Vonage application ID (a UUID) with Video capabilities enabled"
        )
    else:
        try:
            uuid.UUID(application_id)
        except ValueError:
            problems.append("VONAGE_APPLICATION_ID must be a UUID like 1a2b3c4d-...")

    key_path = Path(text("VONAGE_PRIVATE_KEY_PATH", DEFAULT_PRIVATE_KEY_PATH))
    private_key = _read_private_key(key_path, problems)

    agent_mode = text("AGENT_MODE", "modular").lower()
    if agent_mode not in AGENT_MODES:
        problems.append(f"AGENT_MODE must be one of {', '.join(AGENT_MODES)}")
        agent_mode = "modular"

    def api_key(name: str, where: str) -> Secret | None:
        value = text(name)
        if agent_mode != "modular":
            return Secret(value) if value else None
        if not value:
            problems.append(f"{name} is not set. {where}")
        elif any(ch.isspace() for ch in value) or value[0] in "'\"":
            problems.append(f"{name} contains spaces or quotes; paste the bare key")
        return Secret(value) if value else None

    deepgram_api_key = api_key(
        "DEEPGRAM_API_KEY", "Create one in the Deepgram Console (console.deepgram.com)"
    )
    openai_api_key = api_key(
        "OPENAI_API_KEY",
        "Create one at platform.openai.com/api-keys "
        "(API billing is separate from a ChatGPT subscription)",
    )
    elevenlabs_api_key = api_key(
        "ELEVENLABS_API_KEY",
        "Create one at elevenlabs.io/app/developers/api-keys (a paid plan is needed to "
        "use Voice Library voices through the API)",
    )

    keyterms = tuple(term.strip() for term in text("DEEPGRAM_KEYTERMS").split(",") if term.strip())
    if len(keyterms) > MAX_KEYTERMS:
        problems.append(f"DEEPGRAM_KEYTERMS can list at most {MAX_KEYTERMS} terms")
    elif any(len(term) > 100 for term in keyterms):
        problems.append("Each DEEPGRAM_KEYTERMS term must be 100 characters or fewer")

    log_level = text("LOG_LEVEL", "INFO").upper()
    if log_level not in LOG_LEVELS:
        problems.append(f"LOG_LEVEL must be one of {', '.join(LOG_LEVELS)}")
        log_level = "INFO"

    settings = Settings(
        vonage_application_id=application_id,
        vonage_private_key=Secret(private_key),
        deepgram_api_key=deepgram_api_key,
        openai_api_key=openai_api_key,
        elevenlabs_api_key=elevenlabs_api_key,
        deepgram_model=text("DEEPGRAM_MODEL") or DEFAULT_DEEPGRAM_MODEL,
        deepgram_language=text("DEEPGRAM_LANGUAGE") or DEFAULT_DEEPGRAM_LANGUAGE,
        deepgram_keyterms=keyterms,
        openai_model=text("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL,
        elevenlabs_voice_id=text("ELEVENLABS_VOICE_ID") or DEFAULT_ELEVENLABS_VOICE_ID,
        elevenlabs_model=text("ELEVENLABS_MODEL") or DEFAULT_ELEVENLABS_MODEL,
        agent_mode=agent_mode,
        max_active_sessions=integer("MAX_ACTIVE_SESSIONS", 1, 1, 5),
        max_session_seconds=integer("MAX_SESSION_SECONDS", 600, 30, 3600),
        idle_timeout_seconds=integer("IDLE_TIMEOUT_SECONDS", 120, 15, 3600),
        ready_timeout_seconds=integer("READY_TIMEOUT_SECONDS", 60, 10, 600),
        session_creates_per_minute=integer("SESSION_CREATES_PER_MINUTE", 6, 1, 120),
        log_level=log_level,
        log_transcripts=boolean("LOG_TRANSCRIPTS", False),
    )
    if problems:
        raise ConfigError(problems)
    return settings


def _read_private_key(path: Path, problems: list[str]) -> str:
    try:
        key = path.read_text()
    except FileNotFoundError:
        problems.append(
            f"No Vonage private key at {path}. Save the key from your Vonage application "
            "as secrets/private.key (compose.yaml mounts it into the container)"
        )
        return ""
    except PermissionError:
        problems.append(
            f"Cannot read the Vonage private key at {path} (permission denied). The container "
            "runs as an unprivileged user: run `chmod 644 secrets/private.key` and keep the "
            "folder private with `chmod 700 secrets`"
        )
        return ""
    except (OSError, UnicodeDecodeError) as exc:
        problems.append(f"Cannot read the Vonage private key at {path} ({type(exc).__name__})")
        return ""
    if "-----BEGIN RSA PRIVATE KEY-----" in key:
        problems.append(
            "The Vonage private key is in PKCS#1 format. Convert it with "
            "`openssl pkcs8 -topk8 -nocrypt -in private.key -out private.pkcs8.key`"
        )
    elif "-----BEGIN PRIVATE KEY-----" not in key:
        problems.append(
            f"{path} does not contain a PEM private key. Use the private.key file that "
            "Vonage downloaded when you generated the application's key pair"
        )
    return key


# --- Logging ---------------------------------------------------------------------

_REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
        "[private key redacted]",
    ),
    (re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+"), "[jwt redacted]"),
    (re.compile(r"\bsk-[\w-]{8,}"), "sk-[redacted]"),
    # ElevenLabs keys start with sk_; Deepgram keys are 40 hex characters.
    (re.compile(r"\bsk_[0-9a-fA-F]{16,}"), "sk_[redacted]"),
    (re.compile(r"\b[0-9a-f]{40}\b"), "[key redacted]"),
    (re.compile(r"(?i)\b(xi[-_]api[-_]key[\"']?\s*[:=]\s*[\"']?)[^\s'\",&]+"), r"\1[redacted]"),
    (re.compile(r"\bT1==[\w+/=-]+"), "T1==[redacted]"),
    # Deepgram authenticates with "Authorization: Token <key>".
    (
        re.compile(r"(?i)\b(authorization:\s*(?:bearer\s+|token\s+)?|bearer\s+)[^\s'\",]+"),
        r"\1[redacted]",
    ),
]


def redact(message: str) -> str:
    """Mask API keys, tokens, and private keys in a log line."""
    for pattern, replacement in _REDACTIONS:
        message = pattern.sub(replacement, message)
    return message


def _write_redacted(message: Any) -> None:
    # The sink receives the fully formatted line, including any traceback text.
    sys.stderr.write(redact(str(message)))


def setup_logging(level: str = "INFO", label: str = "server") -> None:
    """Route all loguru output (ours and Pipecat's) through the redactor.

    `label` tells the server's lines apart from each agent process's lines.
    """
    logger.remove()
    logger.add(
        _write_redacted,
        level=level if level in LOG_LEVELS else "INFO",
        format=("{time:HH:mm:ss.SSS} | {level: <7} | " + label + " | {name}:{line} - {message}"),
        # diagnose=False: never print local variable values (which can hold secrets)
        # in exception tracebacks.
        backtrace=False,
        diagnose=False,
        colorize=False,
    )
