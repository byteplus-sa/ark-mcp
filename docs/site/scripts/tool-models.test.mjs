import assert from "node:assert/strict";
import { test } from "node:test";
import { toolPage } from "./docs.mjs";

const tool = (name) => ({
  name,
  description: "Operate on media.",
  background: "required",
  registration_gate: "Requires provider configuration.",
  scope: null,
  inputSchema: { properties: {} },
  outputSchema: { properties: {} },
});

test("model-backed tool pages each show one supported-model table", () => {
  for (const name of [
    "seedream_generate_image",
    "seedance_create_task",
    "seed_audio_generate",
    "seed_understand",
    "seed_audio_understand",
    "hyper3d_create_task",
    "hitem3d_create_task",
    "omnihuman_create_task",
    "speech_to_text",
  ]) {
    const page = toolPage(tool(name));
    assert.equal(page.match(/^## Supported Models$/gm)?.length, 1, name);
    assert.match(
      page,
      /\| Model \| Model or service ID \| Availability \| Capabilities \|/,
    );
    assert.ok(
      page.indexOf("## Supported Models") < page.indexOf("## Usage"),
      name,
    );
    assert.match(page, /not provider account access/i);
  }
});

test("image and video tables distinguish defaults, custom IDs and gated models", () => {
  const image = toolPage(tool("seedream_generate_image"));
  const video = toolPage(tool("seedance_create_task"));
  for (const model of [
    "dola-seedream-5-5-261028",
    "dola-seedream-5-0-pro-260628",
  ]) {
    assert.match(image, new RegExp(`\\|[^\\n]*${model}[^\\n]*\\|`));
    assert.doesNotMatch(video, new RegExp(model));
  }
  for (const label of ["Seedream Lite", "Seedream 4.x"]) {
    assert.match(image, new RegExp(`\\| ${label.replace(".", "\\.")} \\|`));
  }
  assert.match(video, /dreamina-seedance-2-5-260628/);
  assert.match(video, /dreamina-seedance-2-0-260128/);
  assert.match(video, /dreamina-seedance-2-5-flash-261115/);
  assert.match(video, /seedance_2_5_flash/);
  assert.doesNotMatch(video, /premium/i);
  assert.match(video, /Seedance 2\.5 \|[^\n]*480p\/720p\/1080p\/4K on the same model/);
  assert.match(video, /Seedance 2\.5 Flash \|[^\n]*480p\/720p;[^\n]*no Draft/);
  assert.match(video, /explicit lists must add/);
  assert.match(video, /discovery does not verify entitlement/);
  assert.match(video, /Seedance 2\.0 Fast/);
  assert.match(video, /Seedance 2\.0 Mini/);
  assert.match(image, /no built-in ID/i);
  assert.doesNotMatch(image, /^## Seedream 5\.5$/m);
  assert.doesNotMatch(image, /^### Model Limits$/m);
  assert.match(image, /id="seedream-5-5"/);
});

test("fixed-service tools do not advertise a per-call model selector", () => {
  for (const name of [
    "seed_audio_generate",
    "seed_audio_understand",
    "speech_to_text",
    "omnihuman_create_task",
  ]) {
    assert.match(toolPage(tool(name)), /no per-call `model` argument/i);
  }
  assert.match(toolPage(tool("seed_audio_generate")), /seed-audio-1\.0/);
  assert.match(
    toolPage(tool("seed_audio_understand")),
    /SEED_AUDIO_UNDERSTANDING_MODEL/,
  );
  assert.match(
    toolPage(tool("omnihuman_create_task")),
    /realman_avatar_picture_omni20_cv/,
  );
});

test("the ASR model table describes returned timings without promising diarization", () => {
  const page = toolPage(tool("speech_to_text"));
  const table = page.split("## Supported Models")[1].split("## Usage")[0];
  assert.match(table, /utterance\/word timings when returned by the provider/i);
  assert.match(table, /submission and polling handled inside the Ark job/);
  assert.doesNotMatch(table, /diarization|optional word timestamps/i);
});

test("utility and unknown tools do not inherit unrelated model tables", () => {
  for (const name of [
    "ark_list_models",
    "gen_task_get",
    "media_upload",
    "vod_get_task",
    "unknown_tool",
    "",
  ]) {
    assert.doesNotMatch(toolPage(tool(name)), /## Supported Models/);
  }
});
