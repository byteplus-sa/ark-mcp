# Seed TTS 3.0

Use **`seed_tts_generate` for text-to-speech with a required `voice_id`**.
It is distinct from `seed_audio_generate`, which generates full-scene audio
from a prompt, including speech, ambience, music and effects.

## Setup and Execution

Seed TTS reuses `BYTEPLUS_SEED_SPEECH_API_KEY` (`X-Api-Key`) and
`BYTEPLUS_SEED_AUDIO_BASE_URL` (default
`https://voice.ap-southeast-1.bytepluses.com`). There are **no new environment
variables**. The tool uses `seed:audio:generate`, the same ordinary shared-team
scope as Seed Audio. Provider access and voice availability still require
account authorization; configuration discovery does not verify either.

`ark_list_models(kind="audio")` lists `dola-seed-tts-3.0` via service
`seed_tts`. Its `tts` limits contain only `max_text_characters`,
`max_ssml_characters`, `output_formats` and `requires_voice_id`.
This is separate from the `kind="understanding"` audio model.

**Background execution is required:** submit through `ark_job_submit`, then
poll `ark_job_get` at `poll_after_ms` until terminal. A client that negotiates
native MCP tasks may use task augmentation instead. Foreground generation is
rejected before a provider call.

The worker aggregates provider HTTP JSONL and persists the complete audio.
**This is not a live MCP audio stream** and has no `gen_task_get` phase.

## Choose a Voice

Use the **Speaker ID** for the desired language from the
[official Seed TTS 3.0 Voice List](https://docs.byteplus.com/en/docs/byteplusvoice/voicelist-3-0)
or supply an authorized cloned voice ID. Check the voice's language support
before synthesis. For US English, `language_code` is `en`. See the
[supported language and dialect codes](https://docs.byteplus.com/en/docs/byteplusvoice/seed-tts-3-0-language-support).

These `ark_job_submit` arguments use the English preset Skye. The account
must have access to the selected voice.

```json
{"input": {
  "tool_name": "seed_tts_generate",
  "arguments": {"input": {
    "voice_id": "multi_female_skye_v3_uranus_bigtts",
    "text": "Welcome to BytePlus.",
    "language_code": "en",
    "enable_subtitle": true
  }}
}}
```

## Inputs

| Field | Contract |
|---|---|
| `model` | Literal `dola-seed-tts-3.0`; not a ModelArk endpoint or configurable model binding |
| `voice_id` | Required authorized preset or cloned voice ID; local cap 256 characters |
| `text` / `ssml` | Exactly one nonempty input. `text` max 10,000 characters; `ssml` max 10,000 characters is a local safety bound, not a claimed provider SSML limit |
| `output_format` | Object with `format`, `sample_rate` and, for compressed output, `bit_rate`; resolved rules below |
| `voice_settings` | Optional: `speed`/`volume` each 0.5-2.0 (default 1), integer `pitch` -12 to 12 (default 0), `instructions` string list (local cap 16 items, each 1-1000 characters), `similarity_boost` boolean (default `false`) |
| `pronunciation_dictionary` | Optional `{"tone": [...]}` for term-level pronunciation corrections or text replacements. Local caps are 100 rules and 1,000 characters per rule |
| `language_code` | `auto` by default; local cap 64 characters |
| `enable_subtitle` | Defaults to `false`; requests provider alignments |
| `session_id` | Optional; local cap 256 characters. Reuse only for sequential requests in one conversation; not a task ID or idempotency guarantee |
| `text_normalization` | Defaults to `true` |
| `enable_markdown_filter` | Defaults to `false` |
| `silence_duration` | Appended silence, 0-30,000 milliseconds; default 0 |
| `output_path` / `overwrite` | Optional stdio-only local export; `overwrite` defaults to `false` |

Use `term/replacement` for substitutions such as `omg/oh my god`. For pinyin
corrections, wrap each syllable in parentheses, such as `北京/(bei3)(jing1)`.

### Output Format

| `format` | Default `sample_rate` (Hz) | `bit_rate` (bits/second) |
|---|---|---|
| `mp3` (default) | `24000` | Defaults to `64000`; only `64000` or `160000`. At `8000` Hz, only `64000` |
| `pcm` | `24000` | Omit; not accepted for PCM |
| `ogg_opus` | `48000` (only accepted rate) | Defaults to `64000`; only `64000` or `160000` |

MP3/PCM rates: 8000, 16000, 22050, 24000, 32000, 44100 or 48000 Hz.
Retain the resolved `output_format` with the audio, especially for raw PCM.

## Result and Storage

Read `result.structured_content` from the terminal `ark_job_get` response and
check `result.is_error` before treating the job as a successful generation.

| Field | Meaning |
|---|---|
| `artifact` | Complete audio, persisted or carrying an explicit bounded storage fallback |
| `output_format` | Resolved format, sample rate and applicable bit rate |
| `session_id` | Session identifier reported in the result |
| `alignments` | List of `{text, words: [{word, start_time, end_time}]}`; word times are normalized to **seconds**, not milliseconds |
| `request_id`, `provider_log_id` | Client-generated request ID and optional provider log ID; retain for reconciliation |

The result also identifies `provider` and `model`. Alignments are empty when
not returned. No duration or provider URL is fabricated; the last word's end
time is not the complete audio duration.

Persistence is mandatory: **there is no `persist=false` mode**. Use
`seed_media_get_artifact` or `seed_media_export_artifact` for the completed
artifact. Optional `output_path` follows the existing
[local output policy](security.md#local-output-paths): absolute path inside an
allowed root, stdio only, no replacement unless `overwrite=true`. Inside
`ark_job_submit`, local export requires server `OUTPUT_ROOTS`; persistence
itself does not.

Storage uses the existing [bounded fallback](artifacts.md#persistence-failures):
check `artifact.persistence_error`; `inline-fallback` contains Base64 bytes
only within `ARTIFACT_INLINE_FALLBACK_MAX_BYTES`. Above that cap, failed
storage is a tool error. There is no provider URL to recover.

## Bounds and Billing

Local stream bounds are 8 MiB per JSONL event and 8 MiB of metadata; total
wire size is bounded from the audio artifact cap plus metadata.
`BYTEPLUS_REQUEST_TIMEOUT_MS` bounds total stream wall-clock time as well as
the existing per-read timeout. No new environment settings are introduced.

The published TTS 3.0 rate is **USD 20 per million characters**, including
**SSML and audio tags**, not just spoken text. This is the published rate, not
a verified account-specific quote.

The server makes one attempt per job. Connection failures before dispatch
and definitive HTTP rejections release the budget reservation. The HTTP
status still determines billing classification if the error body is
unreadable. If synthesis may have started, retain the Ark job and diagnostic
IDs and reconcile the request before resubmitting.

## Sources and Verification

Provider documentation for this contract was checked on **2026-11-25**.

- [Seed TTS 3.0 HTTP streaming](https://docs.byteplus.com/en/docs/byteplusvoice/seed-tts-3-0-http-streaming)
  ([Markdown source](https://docs.byteplus.com/docs/en/byteplusvoice/seed-tts-3-0-http-streaming.md)).
- [Seed TTS 3.0 Voice List](https://docs.byteplus.com/en/docs/byteplusvoice/voicelist-3-0).
- [Seed TTS 3.0 errors](https://docs.byteplus.com/en/docs/byteplusvoice/seed-tts-3-0-errors).
- [TTS billing](https://docs.byteplus.com/en/docs/byteplusvoice/TTS_Billing).

**Live smoke tests, 2026-11-25.** Stdio MCP jobs synthesized US English MP3
with the Skye and Megan presets. Word alignment included the replacement from
`pronunciation_dictionary.tone`, and FFmpeg decoded both persisted artifacts
retrieved through MCP.
