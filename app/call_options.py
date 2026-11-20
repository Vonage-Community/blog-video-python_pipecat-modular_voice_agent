"""What the waiting room lets you choose for each call, and how the server checks it.

The browser sends its choices with POST /api/sessions. Nothing it sends is trusted:
every value is checked against an allowed list or range here, then copied onto the
agent's Settings. The defaults come from .env, so a call with no choices behaves
exactly like the server's configuration.
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import asdict, dataclass, replace
from typing import Any

import aiohttp
from loguru import logger

from app.config import Settings

# Languages Deepgram's Nova-3 model transcribes and ElevenLabs' Flash v2.5 speaks.
LANGUAGES: dict[str, str] = {
    "en": "English",
    "en-GB": "English (UK)",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "nl": "Dutch",
    "hi": "Hindi",
    "ja": "Japanese",
}

# How much silence ends your turn before Smart Turn decides you've finished.
REPLY_PAUSES: dict[str, float] = {"quick": 0.2, "relaxed": 0.5, "patient": 0.8}

MAX_KEYTERMS_PER_CALL = 20
MAX_INSTRUCTIONS_CHARS = 500
SPEED_RANGE = (0.7, 1.2)  # what ElevenLabs accepts for streaming speech
INTERRUPT_WORDS_RANGE = (1, 5)
VOICE_ID = re.compile(r"^[A-Za-z0-9]{20}$")  # the shape of an ElevenLabs voice ID


class CallOptionsError(Exception):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class CallOptions:
    language: str
    keyterms: tuple[str, ...]
    openai_model: str
    instructions: str
    voice_id: str
    voice_speed: float
    interrupt_min_words: int
    reply_pause: str

    @classmethod
    def defaults(cls, settings: Settings) -> CallOptions:
        return cls(
            language=settings.deepgram_language,
            keyterms=settings.deepgram_keyterms,
            openai_model=settings.openai_model,
            instructions="",
            voice_id=settings.elevenlabs_voice_id,
            voice_speed=1.0,
            interrupt_min_words=settings.interrupt_min_words,
            reply_pause="quick",
        )

    def to_json(self) -> dict[str, Any]:
        """The same camelCase names the browser sends."""
        fields = {**asdict(self), "keyterms": list(self.keyterms)}
        return {_JSON_NAMES[name]: value for name, value in fields.items()}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> CallOptions:
        """Rebuild options the server already checked (the agent process side)."""
        fields = {name: data[json_name] for name, json_name in _JSON_NAMES.items()}
        return cls(**{**fields, "keyterms": tuple(fields["keyterms"])})

    def apply_to(self, settings: Settings) -> Settings:
        """The agent's settings for this call."""
        return replace(
            settings,
            deepgram_language=self.language,
            deepgram_keyterms=self.keyterms,
            openai_model=self.openai_model,
            extra_instructions=self.instructions,
            elevenlabs_voice_id=self.voice_id,
            elevenlabs_speed=self.voice_speed,
            interrupt_min_words=self.interrupt_min_words,
            vad_stop_secs=REPLY_PAUSES[self.reply_pause],
        )


_JSON_NAMES = {
    "language": "language",
    "keyterms": "keyterms",
    "openai_model": "openaiModel",
    "instructions": "instructions",
    "voice_id": "voiceId",
    "voice_speed": "voiceSpeed",
    "interrupt_min_words": "interruptMinWords",
    "reply_pause": "replyPause",
}


def parse_call_options(raw: Any, settings: Settings) -> CallOptions:
    """Check the browser's choices. Raises CallOptionsError listing every problem."""
    defaults = CallOptions.defaults(settings)
    if raw is None:
        return defaults
    if not isinstance(raw, dict):
        raise CallOptionsError(["options must be an object"])
    problems: list[str] = []

    language = raw.get("language", defaults.language)
    if language not in LANGUAGES:
        problems.append(f"language must be one of {', '.join(LANGUAGES)}")

    keyterms_raw = raw.get("keyterms", list(defaults.keyterms))
    keyterms: tuple[str, ...] = ()
    if not isinstance(keyterms_raw, list) or not all(isinstance(t, str) for t in keyterms_raw):
        problems.append("keyterms must be a list of words")
    else:
        keyterms = tuple(dict.fromkeys(_clean(t)[:100] for t in keyterms_raw if _clean(t)))
        if len(keyterms) > MAX_KEYTERMS_PER_CALL:
            problems.append(f"Use at most {MAX_KEYTERMS_PER_CALL} keyterms")

    model = raw.get("openaiModel", defaults.openai_model)
    if model not in settings.openai_models:
        problems.append(f"openaiModel must be one of {', '.join(settings.openai_models)}")

    instructions = raw.get("instructions", "")
    if not isinstance(instructions, str):
        problems.append("instructions must be text")
        instructions = ""
    instructions = _clean(instructions, keep_newlines=True)
    if len(instructions) > MAX_INSTRUCTIONS_CHARS:
        problems.append(f"instructions must be {MAX_INSTRUCTIONS_CHARS} characters or fewer")

    voice_id = raw.get("voiceId", defaults.voice_id)
    if not isinstance(voice_id, str) or not VOICE_ID.match(voice_id):
        problems.append("voiceId must be an ElevenLabs voice ID (20 letters and digits)")

    speed = raw.get("voiceSpeed", defaults.voice_speed)
    if not _number_in(speed, *SPEED_RANGE):
        problems.append(f"voiceSpeed must be between {SPEED_RANGE[0]} and {SPEED_RANGE[1]}")

    min_words = raw.get("interruptMinWords", defaults.interrupt_min_words)
    if isinstance(min_words, bool) or not isinstance(min_words, int):
        problems.append("interruptMinWords must be a whole number")
    elif not INTERRUPT_WORDS_RANGE[0] <= min_words <= INTERRUPT_WORDS_RANGE[1]:
        problems.append(
            f"interruptMinWords must be between {INTERRUPT_WORDS_RANGE[0]} and "
            f"{INTERRUPT_WORDS_RANGE[1]}"
        )

    pause = raw.get("replyPause", defaults.reply_pause)
    if pause not in REPLY_PAUSES:
        problems.append(f"replyPause must be one of {', '.join(REPLY_PAUSES)}")

    if problems:
        raise CallOptionsError(problems)
    return CallOptions(
        language=language,
        keyterms=keyterms,
        openai_model=model,
        instructions=instructions,
        voice_id=voice_id,
        voice_speed=round(float(speed), 2),
        interrupt_min_words=min_words,
        reply_pause=pause,
    )


def _clean(text: str, keep_newlines: bool = False) -> str:
    """Drop control characters (except newlines in the instructions) and trim."""
    allowed = {"\n"} if keep_newlines else set()
    return "".join(ch for ch in text if ch in allowed or unicodedata.category(ch)[0] != "C").strip()


def _number_in(value: Any, low: float, high: float) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and low <= value <= high


# --- Voices --------------------------------------------------------------------------


@dataclass(frozen=True)
class Voice:
    id: str
    name: str
    description: str


class VoiceDirectory:
    """The voices the waiting room offers.

    Recommended voices come from .env and always work on a paid ElevenLabs plan.
    "Your voices" come from your ElevenLabs account (including voices you designed),
    only if your API key has the Voices: Read permission. The key never leaves the
    server; the browser asks this server for the list.
    """

    URL = "https://api.elevenlabs.io/v2/voices?page_size=100"
    CACHE_SECONDS = 300

    def __init__(self, settings: Settings):
        self._settings = settings
        self._cached: tuple[float, list[Voice], str] | None = None

    async def account_voices(self) -> tuple[list[Voice], str]:
        """Returns (voices, status). Status is ok, missing_permission, or unavailable."""
        key = self._settings.elevenlabs_api_key
        if key is None:
            return [], "unavailable"
        now = time.monotonic()
        if self._cached and now - self._cached[0] < self.CACHE_SECONDS:
            return self._cached[1], self._cached[2]
        voices, status = await self._fetch(key.get())
        self._cached = (now, voices, status)
        return voices, status

    async def _fetch(self, api_key: str) -> tuple[list[Voice], str]:
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with (
                aiohttp.ClientSession(timeout=timeout) as http,
                http.get(self.URL, headers={"xi-api-key": api_key}) as response,
            ):
                if response.status in (401, 403):
                    return [], "missing_permission"
                response.raise_for_status()
                data = await response.json()
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            logger.warning(f"Could not list ElevenLabs voices: {type(exc).__name__}")
            return [], "unavailable"
        return [_voice_from(item) for item in data.get("voices", [])], "ok"


def _voice_from(item: dict[str, Any]) -> Voice:
    labels = item.get("labels") or {}
    details = [labels.get(k) for k in ("accent", "gender", "age")]
    return Voice(
        id=str(item.get("voice_id", "")),
        name=str(item.get("name", "Unnamed voice")),
        description=", ".join(str(d).replace("_", " ") for d in details if d),
    )
