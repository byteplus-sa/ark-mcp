---
title: ModelArk 3D Generation (Hyper3D + Hitem3d)
type: plan
status: in-progress
created: 2026-09-01
updated: 2026-09-01
tags:
  - byteplus
  - modelark
  - mcp
  - fastmcp
  - python
  - 3d
  - hyper3d
  - hitem3d
source:
  - https://docs.byteplus.com/en/docs/ModelArk/2279947
  - https://docs.byteplus.com/en/docs/ModelArk/2307070
  - https://docs.byteplus.com/en/docs/ModelArk/2310461
  - https://docs.byteplus.com/en/docs/ModelArk/2310462
  - https://docs.byteplus.com/en/docs/ModelArk/2310463
  - https://docs.byteplus.com/en/docs/ModelArk/2310468
  - https://docs.byteplus.com/en/docs/ModelArk/2314182
  - https://docs.byteplus.com/en/docs/ModelArk/2310467
related:
  - "[[PLAN_ARK_SEED_MULTIMODAL_MCP]]"
---

<!-- markdownlint-disable MD013 MD025 -->

# ModelArk 3D Generation (Hyper3D + Hitem3d)

## Outcome

Add two async 3D generation model families — **Hyper3D** (`hyper3d`) and
**Hitem3d** (`hitem3d`) — to the Ark Seed Multimodal MCP server. Each
family exposes a full task lifecycle mirroring Seedance:

- `hyper3d_create_task`, `hyper3d_get_task`, `hyper3d_list_tasks`,
  `hyper3d_cancel_or_delete_task`
- `hitem3d_create_task`, `hitem3d_get_task`, `hitem3d_list_tasks`,
  `hitem3d_cancel_or_delete_task`

Both families reuse the existing ModelArk data plane
(`BYTEPLUS_MODELARK_BASE_URL`, Bearer `BYTEPLUS_MODELARK_API_KEY`) and the
**same** task endpoints Seedance already uses:

| Operation | Method | Path |
|---|---|---|
| Create task | `POST` | `/contents/generations/tasks` |
| Retrieve task | `GET` | `/contents/generations/tasks/{id}` |
| List tasks | `GET` | `/contents/generations/tasks` |
| Cancel/delete task | `DELETE` | `/contents/generations/tasks/{id}` |

Generated 3D files are persisted into the durable artifact store on first
successful retrieval, so `seed-media://artifacts/{id}` resources survive the
provider's 24-hour `file_url` expiry. Task IDs are kept by the provider for
7 days.

## Feature flag (disabled by default)

A dedicated feature flag gates the two families **independently** of the
other ModelArk products, even though they share `BYTEPLUS_MODELARK_API_KEY`:

- New env var `BYTEPLUS_MODELARK_3D_ENABLED` (boolean, default `false`).
- New `Settings` field `seed3d_enabled: bool` with
  `validation_alias="BYTEPLUS_MODELARK_3D_ENABLED"`.
- New `Settings.has_seed3d` property:
  `bool(self.seed3d_enabled) and bool(self.modelark_api_key)`.

When the flag is unset (default), no 3D tool is registered regardless of
whether the ModelArk API key is configured.

## Model contract (per official docs)

### Hyper3D (`Hyper3d-Gen2`)

- Text-to-3D: prompt only (English, ≤400 chars) or prompt + params.
- Image-to-3D: 1–5 images (jpg/jpeg/png, <4096×4096 px, ≤30 MB each),
  with optional prompt.
- Body fields: `model`, `content[]`, `seed` (0–65535), `callback_url`.
- Model text commands (`--<param> <value>` inside `content.text`):
  `material` (PBR|Shaded|All|None, default PBR), `mesh_mode` (Raw|Quad,
  default Quad), `quality_override` (int), `addons` (HighPack),
  `use_original_alpha` (bool), `bbox_condition` (int[3]), `TAPose` (bool),
  `subdivisionlevel` (high|medium|low), `fileformat` (glb|obj|usdz|fbx|stl,
  default glb), `hd_texture` (bool).

### Hitem3d (`Hitem3d-2.0`)

- Image-to-3D only: 1–4 images (jpg/jpeg/png/webp, <4096×4096 px, ≤10 MB
  each). No free-text prompt.
- Body fields: `model`, `content[]`, `callback_url`.
- Model text commands: `resolution` (1536|1536pro, default 1536),
  `face` (100000–2000000), `fileformat` (1=obj, 2=glb, 3=stl, 4=fbx,
  5=usdz, default 1), `request_type` (1=geometry only, 3=geometry+texture,
  default 3), `multi_images_bit` (string of 0/1 over
  front/back/left/right views).

### Shared task response shape

`id`, `model`, `status` (`queued|running|cancelled|succeeded|failed`),
`content.file_url` (zip package of the 3D file), `created_at`, `updated_at`,
`error{code,message}`, `usage{completion_tokens,total_tokens}`.
List returns `items[]` + `total`; DELETE returns HTTP 200 with an empty body.

DELETE semantics (identical to Seedance): `queued`→cancel; `running` and
`cancelled`→not allowed; `succeeded|failed|expired`→delete record.

## Implementation

### 1. Config — `src/ark_mcp/config/env.py`

```python
class Seed3DFamily(StrEnum):
    HYPER3D = "hyper3d"
    HITEM3D = "hitem3d"

class Seed3DModelBinding(BaseModel):
    model_id: str = Field(min_length=1)
    family: Seed3DFamily
```

New `Settings` fields (all optional, defaults below):

```python
seed3d_enabled: bool = Field(
    default=False,
    validation_alias="BYTEPLUS_MODELARK_3D_ENABLED",
)
hyper3d_default_model: str = Field(
    default="hyper3d-gen2",
    validation_alias="HYPER3D_DEFAULT_MODEL",
)
hitem3d_default_model: str = Field(
    default="hitem3d-2-0",
    validation_alias="HITEM3D_DEFAULT_MODEL",
)
seed3d_model_bindings: list[Seed3DModelBinding] = Field(
    default_factory=list,
    validation_alias="SEED3D_MODEL_BINDINGS",
)
```

Property:

```python
@property
def has_seed3d(self) -> bool:
    return self.seed3d_enabled and bool(self.modelark_api_key)
```

`validate_model_bindings` (after-validator): if `seed3d_model_bindings` is
empty, build it from `hyper3d_default_model` (family HYPER3D) and
`hitem3d_default_model` (family HITEM3D). Add both to the duplicate-ID /
default-missing check loop.

### 2. Capability registry — `src/ark_mcp/config/model_capabilities.py`

Add `ModelFamily.SEED3D_HYPER3D = "seed3d_hyper3d"` and
`ModelFamily.SEED3D_HITEM3D = "seed3d_hitem3d"`.

```python
@dataclass(frozen=True)
class Seed3DCapabilities:
    family: ModelFamily
    model_id: str
    supports_text_to_3d: bool
    max_reference_images: int
    supported_file_formats: tuple[str, ...]
    supports_seed: bool
    seed_range: tuple[int, int]
    supports_callback_url: bool = True
```

Build `_seed3d_capabilities()` keyed by model ID from
`settings.seed3d_model_bindings` (HYPER3D: text-to-3D True, 5 images,
`("glb","obj","usdz","fbx","stl")`, seed (0,65535); HITEM3D: text-to-3D
False, 4 images, `("obj","glb","stl","fbx","usdz")`, no seed). Expose
`get_seed3d_capabilities`, `list_seed3d_models` on `CapabilityRegistry`.

### 3. Provider schemas — `src/ark_mcp/providers/modelark/schemas.py`

Add a Seed3D section:

```python
class Seed3DContentItem(BaseModel):
    type: str                      # text | image_url
    text: str | None = None
    image_url: dict[str, str] | None = None

class Seed3DCreateProviderRequest(BaseModel):
    model: str
    content: list[Seed3DContentItem] = Field(default_factory=list)
    seed: int | None = None
    callback_url: str | None = None

class Seed3DCreateProviderResponse(BaseModel):
    id: str

class Seed3DErrorDetail(BaseModel):
    code: str = ""
    message: str = ""

class Seed3DUsage(BaseModel):
    completion_tokens: int | None = None
    total_tokens: int | None = None

class Seed3DTaskResponse(BaseModel):
    id: str
    model: str = ""
    status: str = ""
    content: dict[str, Any] | None = None
    created_at: int | str | None = None
    updated_at: int | str | None = None
    error: Seed3DErrorDetail | None = None
    usage: Seed3DUsage | None = None

    @property
    def file_url(self) -> str | None: ...

class Seed3DTaskListResponse(BaseModel):
    items: list[Seed3DTaskResponse] = Field(default_factory=list,
        validation_alias=AliasChoices("items", "data"))
    total: int = 0
```

### 4. Provider service — `src/ark_mcp/providers/modelark/seed3d.py`

`Seed3DService` mirroring `SeedanceService` (create/get/list/delete against
`/contents/generations/tasks`), plus:

- `build_content(prompt, images, command_params)` — text item (prompt +
  `--k v` commands) then `image_url` items.
- `build_text_command(params: dict[str, Any]) -> str` — renders truthy
  params as `--k v`; booleans render as `true`/`false`; lists as
  comma-joined `[a,b,c]`.
- `to_task_summary`, `extract_usage`, `get_created_at`, `get_updated_at`.

### 5. Domain models — `src/ark_mcp/domain/models.py`

Add `Seed3DTaskStatus` (with `_missing_` → UNKNOWN), `Seed3DTaskError`,
`Seed3DTaskUsage`, `Seed3DTaskSummary`, `Seed3DTaskSettings`
(`file_format`, `subdivision_level` — both optional, `extra="allow"`).

### 6. Artifacts — `src/ark_mcp/domain/artifacts.py`

Add `MediaType.THREE_D = "three_d"`.

- `security/media_policy.py`: add `_ALLOWED_3D_MIMES` (application/zip,
  application/x-zip-compressed, model/gltf-binary, model/gltf+json,
  application/octet-stream, model/vnd.usdz+zip, text/plain),
  `three_d_max_bytes` (default 200 MB), `validate_3d_mime`.
- `artifacts/filesystem_store.py` and
  `artifacts/object_storage_store.py`: add `"three_d"` to the three
  `max_bytes` maps, the two mime-validation maps, and `_MIME_TO_EXT`
  (`application/zip: .zip`, `model/gltf-binary: .glb`,
  `model/vnd.usdz+zip: .usdz`, etc.).

Persistence in get-tools uses `mime_type="application/zip"` (the provider
packs the generated 3D file into a zip).

### 7. Tools — `src/ark_mcp/tools/`

Shared module `_seed3d_shared.py`:

- `Seed3DImageInput(MediaSource)` with `MEDIA_CATEGORY = MediaType.IMAGE`.
- `Seed3DCreateTaskOutput` (task_id, status="queued",
  recommended_poll_after_ms).
- `execute_seed3d_create(input_model, ctx, caps, family)` — builds content,
  request, billing slot (`product="3d"`), retry, ownership record.
- `Seed3DTaskOutput`, `Seed3DTaskPage`, `Seed3DCancelOrDeleteOutput`.
- `seed3d_get_task_impl(input, ctx, family_label)` — get + persist zip as
  `MediaType.THREE_D` / `application/zip` on first success, with the
  task-artifact cache.
- `seed3d_list_tasks_impl(input, ctx, family_label)`.
- `seed3d_cancel_or_delete_impl(input, ctx, family_label)`.

Eight tool modules:

- `hyper3d_create_task.py` — prompt/seed/text-command inputs; max 5 images.
- `hitem3d_create_task.py` — images required (max 4), resolution/face/
  file_format/request_type/multi_images_bit inputs.
- `hyper3d_get_task.py`, `hitem3d_get_task.py` — thin wrappers over
  `seed3d_get_task_impl`.
- `hyper3d_list_tasks.py`, `hitem3d_list_tasks.py` — thin wrappers over
  `seed3d_list_tasks_impl` (no `service_tier`; `model` filter allowed).
- `hyper3d_cancel_or_delete_task.py`,
  `hitem3d_cancel_or_delete_task.py` — thin wrappers over
  `seed3d_cancel_or_delete_impl`.

Each tool module exports `TOOL_ANNOTATIONS` and its output model (create
tools export their own; lifecycle tools re-export shared output models).

### 8. Cost — `src/ark_mcp/tools/_cost.py`

Add `COST_PER_3D_TASK_HYPER3D = 0.25` and `COST_PER_3D_TASK_HITEM3D = 1.0`
(conservative USD estimates; provider bills in CNY/token). Add a `"3d"`
branch in `estimate_cost` keyed on `model_id`.

### 9. Server registration — `src/ark_mcp/server.py`

After the ModelArk block, add:

```python
if settings.has_seed3d:
    # import 8 modules; register with component_auth scopes:
    #   hyper3d:create / hyper3d:read / hyper3d:delete
    #   hitem3d:create / hitem3d:read / hitem3d:delete
```

### 10. Documentation + skill

- `docs/api-reference.md`: add 8 tools to inventory + annotations tables.
- `docs/tools.md`: document the 8 tools.
- `docs/models.md`: add the two Seed3D families.
- `docs/configuration.md`: document `BYTEPLUS_MODELARK_3D_ENABLED` and the
  model binding vars.
- `README.md`: mention 3D generation.
- `.env.example`: add the new vars (flag default commented `false`).
- `.agents/skills/ark-mcp/SKILL.md`: add 3D generation section and
  registration rule (requires `BYTEPLUS_MODELARK_3D_ENABLED=true`).

### 11. Tests

- `tests/unit/test_env.py`: flag default false; `has_seed3d` requires both
  flag and key; bindings built from defaults; duplicate detection.
- `tests/unit/test_model_capabilities.py`: Seed3D capability lookups.
- `tests/contract/test_seed3d_adapter.py`: content/command building,
  request DTO, task response `file_url` extraction, list alias.
- `tests/unit/test_seed3d_input.py`: create input validators (image counts,
  text-to-3D requirement for Hyper3D, images-required for Hitem3d).
- `tests/integration/test_seed3d_tool.py`: registration gating on the flag.

## Out of scope

- Seed3D (`doubao-seed3d`) — not requested; the same service can be extended
  later by adding a third binding family.
- Parallel `_variations` tools for 3D.
- Auto-polling helpers; clients poll via the get tools.

## Verification

- `make test`, `make lint`, `make typecheck` all pass.
- `make build` succeeds.
- With `BYTEPLUS_MODELARK_3D_ENABLED` unset, `seed-health://status` and the
  tool list show no 3D tools.
