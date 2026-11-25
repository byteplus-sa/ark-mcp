"""Typed Seed TTS 3 controls and normalized word alignment."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TTS_MODEL = "dola-seed-tts-3.0"
TTS_MAX_CHARACTERS = 10_000


class TtsOutputFormat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["mp3", "pcm", "ogg_opus"] = Field("mp3", description="Audio encoding.")
    sample_rate: Literal[8000, 16000, 22050, 24000, 32000, 44100, 48000] | None = Field(
        None, description="Hz; defaults to 24000 for MP3/PCM or 48000 for Ogg Opus."
    )
    bit_rate: Literal[64000, 160000] | None = Field(
        None, description="Bits per second; compressed audio defaults to 64000. Omit for PCM."
    )

    @model_validator(mode="after")
    def validate_encoding(self) -> TtsOutputFormat:
        if self.format == "ogg_opus" and self.sample_rate not in (None, 48000):
            raise ValueError("ogg_opus requires sample_rate=48000.")
        if self.format == "pcm" and self.bit_rate is not None:
            raise ValueError("PCM requires bit_rate to be omitted.")
        if self.format == "mp3" and self.sample_rate == 8000 and self.bit_rate == 160000:
            raise ValueError("MP3 at 8000 Hz supports only bit_rate=64000.")
        return self

    def resolved(self) -> TtsOutputFormat:
        return self.model_copy(
            update={
                "sample_rate": self.sample_rate or (48000 if self.format == "ogg_opus" else 24000),
                "bit_rate": None if self.format == "pcm" else (self.bit_rate or 64000),
            }
        )

    @property
    def mime_type(self) -> str:
        return {"mp3": "audio/mpeg", "pcm": "audio/pcm", "ogg_opus": "audio/ogg"}[self.format]


class TtsVoiceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speed: float = Field(1.0, ge=0.5, le=2.0, description="Speech rate multiplier (0.5-2.0).")
    volume: float = Field(1.0, ge=0.5, le=2.0, description="Volume multiplier (0.5-2.0).")
    pitch: int = Field(0, ge=-12, le=12, description="Pitch adjustment (-12 to 12).")
    instructions: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(
        default_factory=list,
        max_length=16,
        description="Delivery instructions for compatible voices; local cap 16 items of 1000 characters.",
    )
    similarity_boost: bool = Field(False, description="Enable similarity boost for cloned voices.")


class TtsPronunciationDictionary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tone: list[Annotated[str, Field(min_length=3, max_length=1000)]] = Field(
        ...,
        max_length=100,
        description=(
            "Rules in term/replacement or term/(pinyin)(pinyin) form. "
            "Local cap of 100 rules, each up to 1000 characters."
        ),
    )

    @field_validator("tone")
    @classmethod
    def validate_rules(cls, rules: list[str]) -> list[str]:
        for rule in rules:
            term, _, correction = rule.partition("/")
            if not term.strip() or not correction.strip():
                raise ValueError(
                    "Each rule must contain a nonblank term and correction separated by '/'."
                )
        return rules


class TtsWord(BaseModel):
    word: str = Field(..., description="Text of the aligned word.")
    start_time: float = Field(..., ge=0, allow_inf_nan=False, description="Start time in seconds.")
    end_time: float = Field(..., ge=0, allow_inf_nan=False, description="End time in seconds.")

    @model_validator(mode="after")
    def validate_interval(self) -> TtsWord:
        if self.end_time < self.start_time:
            raise ValueError("Word end_time must not precede start_time.")
        return self


class TtsAlignment(BaseModel):
    text: str = Field(..., description="Text of this subtitle segment.")
    words: list[TtsWord] = Field(..., description="Word timings on the audio timeline in seconds.")
