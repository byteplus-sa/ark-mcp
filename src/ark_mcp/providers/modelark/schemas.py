"""ModelArk provider request/response schemas (Seedream + Seedance).

These DTOs model the raw JSON the ModelArk API expects and returns. They
are internal to the provider layer — tool inputs and domain outputs use
separate Pydantic models so vendor field changes do not leak through.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, Field

# ---------------------------------------------------------------------------
# Seedream image generation
# ---------------------------------------------------------------------------


class SeedreamProviderRequest(BaseModel):
    """Raw request body for ``POST /images/generations``."""

    model: str
    prompt: str
    image: str | list[str] | None = None
    size: str | None = None
    seed: int | None = None
    sequential_image_generation: str | None = None
    sequential_image_generation_options: dict[str, Any] | None = None
    stream: bool = False
    output_format: str | None = None
    response_format: str | None = None
    watermark: bool | None = None
    optimize_prompt_options: dict[str, str] | None = None


class SeedreamImageData(BaseModel):
    """Single image item in a Seedream generation response."""

    url: str | None = None
    b64_json: str | None = None
    output_format: str | None = None
    index: int | None = None
    revised_prompt: str | None = None


class SeedreamProviderResponse(BaseModel):
    """Raw response from ``POST /images/generations``."""

    created: int | None = None
    data: list[SeedreamImageData] = Field(default_factory=list)
    usage: dict[str, Any] | None = None


class SeedreamItemErrorData(BaseModel):
    """Error for a single failed image in a Seedream batch."""

    index: int | None = None
    code: str | None = None
    message: str = ""


class SeedreamProviderErrorResponse(BaseModel):
    """Error response shape from the Seedream API."""

    error: SeedreamItemErrorData = Field(default_factory=SeedreamItemErrorData)


# ---------------------------------------------------------------------------
# Seedance video task API
# ---------------------------------------------------------------------------


class SeedanceContentItem(BaseModel):
    """Content item for the Seedance task creation request."""

    type: str = Field(..., description="text, image_url, video_url, or audio_url")
    text: str | None = None
    image_url: dict[str, str] | str | None = None
    video_url: dict[str, str] | str | None = None
    audio_url: dict[str, str] | str | None = None
    role: str | None = None


class SeedanceCreateProviderRequest(BaseModel):
    """Raw request body for ``POST /contents/generations/tasks``."""

    model: str
    content: list[SeedanceContentItem] = Field(default_factory=list)
    resolution: str | None = None
    ratio: str | None = None
    duration: int | None = None
    omni_reference_task_type: str | None = None
    camera_fixed: bool | None = None
    watermark: bool | None = None
    generate_audio: bool | None = None
    return_last_frame: bool | None = None
    service_tier: str | None = None
    execution_expires_after: int | None = None
    priority: int | None = None
    safety_identifier: str | None = None
    callback_url: str | None = None


class SeedanceCreateProviderResponse(BaseModel):
    """Response from ``POST /contents/generations/tasks``."""

    id: str


class SeedanceVideoUrl(BaseModel):
    """Video output in a Seedance task response."""

    url: str
    duration: str | None = None


class SeedanceGenerationUsage(BaseModel):
    """Usage data in a Seedance task response."""

    completion_tokens: int | None = None
    prompt_tokens: int | None = None


class SeedanceGenerationConfig(BaseModel):
    """Generation config echoed in a Seedance task response."""

    resolution: str | None = None
    ratio: str | None = None
    duration: int | str | None = None
    omni_reference_task_type: str | None = None
    seed: int | None = None
    camera_fixed: bool | None = None
    watermark: bool | None = None
    generate_audio: bool | None = None
    return_last_frame: bool | None = None
    service_tier: str | None = None
    execution_expires_after: int | None = None
    priority: int | None = None
    safety_identifier: str | None = None


class SeedanceErrorDetail(BaseModel):
    """Error detail in a Seedance task response."""

    code: str = ""
    message: str = ""


class SeedanceTaskResponse(BaseModel):
    """Full task object from the Seedance retrieve/list APIs."""

    id: str
    model: str = ""
    status: str = ""
    created_at: int | str | None = None
    updated_at: int | str | None = None
    error: SeedanceErrorDetail | None = None
    content: dict[str, Any] | None = None
    usage: SeedanceGenerationUsage | None = None
    # Generation settings (may be top-level or inside content).
    seed: int | None = None
    resolution: str | None = None
    ratio: str | None = None
    duration: int | str | None = None
    omni_reference_task_type: str | None = None
    framespersecond: int | None = None
    service_tier: str | None = None
    execution_expires_after: int | None = None
    generate_audio: bool | None = None
    draft: bool | None = None
    priority: int | None = None

    @property
    def video_url(self) -> str | None:
        """Extract the video URL from the content object."""
        if self.content and isinstance(self.content, dict):
            url = self.content.get("video_url")
            if isinstance(url, str):
                return url
            if isinstance(url, dict):
                return url.get("url")
        return None

    @property
    def last_frame_url(self) -> str | None:
        """Extract the last frame URL from the content object."""
        if self.content and isinstance(self.content, dict):
            url = self.content.get("last_frame_url")
            if isinstance(url, str):
                return url
            if isinstance(url, dict):
                return url.get("url")
        return None


class SeedanceTaskListResponse(BaseModel):
    """Response from ``GET /contents/generations/tasks``."""

    data: list[SeedanceTaskResponse] = Field(
        default_factory=list,
        validation_alias=AliasChoices("data", "items"),
    )
    total: int = 0
    has_more: bool | None = None


# ---------------------------------------------------------------------------
# Seed3D 3D generation task API
# ---------------------------------------------------------------------------


class Seed3DContentItem(BaseModel):
    """Content item for the Seed3D task creation request."""

    type: str = Field(..., description="text or image_url")
    text: str | None = None
    image_url: dict[str, str] | None = None


class Seed3DCreateProviderRequest(BaseModel):
    """Raw request body for ``POST /contents/generations/tasks`` (3D)."""

    model: str
    content: list[Seed3DContentItem] = Field(default_factory=list)
    seed: int | None = None
    callback_url: str | None = None


class Seed3DCreateProviderResponse(BaseModel):
    """Response from ``POST /contents/generations/tasks`` (3D)."""

    id: str


class Seed3DErrorDetail(BaseModel):
    """Error detail in a Seed3D task response."""

    code: str = ""
    message: str = ""


class Seed3DUsage(BaseModel):
    """Usage data in a Seed3D task response."""

    completion_tokens: int | None = None
    total_tokens: int | None = None


class Seed3DTaskResponse(BaseModel):
    """Full task object from the Seed3D retrieve/list APIs."""

    id: str
    model: str = ""
    status: str = ""
    content: dict[str, Any] | None = None
    created_at: int | str | None = None
    updated_at: int | str | None = None
    error: Seed3DErrorDetail | None = None
    usage: Seed3DUsage | None = None

    @property
    def file_url(self) -> str | None:
        """Extract the generated 3D file URL from the content object."""
        if self.content and isinstance(self.content, dict):
            url = self.content.get("file_url")
            if isinstance(url, str):
                return url
            if isinstance(url, dict):
                return url.get("url")
        return None


class Seed3DTaskListResponse(BaseModel):
    """Response from ``GET /contents/generations/tasks`` (3D)."""

    items: list[Seed3DTaskResponse] = Field(
        default_factory=list,
        validation_alias=AliasChoices("items", "data"),
    )
    total: int = 0
    has_more: bool | None = None


# ---------------------------------------------------------------------------
# Chat Completions (Seed 2.1 multimodal understanding)
# ---------------------------------------------------------------------------


class ChatContentPart(BaseModel):
    """A single content part in a chat completion message."""

    type: str
    text: str | None = None
    image_url: dict[str, str] | None = None
    video_url: dict[str, str] | None = None
    input_audio: dict[str, str] | None = None


class ChatMessage(BaseModel):
    """A message in a chat completion conversation."""

    role: str
    content: str | list[ChatContentPart]


class ChatThinkingConfig(BaseModel):
    """Thinking/reasoning configuration for deep-thinking models."""

    type: Literal["enabled", "disabled", "auto"] = "enabled"


class ChatCompletionProviderRequest(BaseModel):
    """Raw request body for ``POST /chat/completions``."""

    model: str
    messages: list[ChatMessage]
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    repetition_penalty: float | None = None
    reasoning_effort: str | None = None
    thinking: ChatThinkingConfig | None = None
    response_format: dict[str, Any] | None = None
    service_tier: str | None = None
    stream: bool = False


class ChatCompletionTokensDetails(BaseModel):
    """Breakdown of completion tokens, when the provider reports it."""

    reasoning_tokens: int | None = None


class ChatUsage(BaseModel):
    """Token usage in a chat completion response."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    completion_tokens_details: ChatCompletionTokensDetails | None = None


class ChatChoiceMessage(BaseModel):
    """The assistant message in a chat completion choice."""

    role: str = "assistant"
    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


class ChatChoice(BaseModel):
    """A single choice in a chat completion response."""

    index: int = 0
    message: ChatChoiceMessage = Field(default_factory=ChatChoiceMessage)
    finish_reason: str | None = None


class ChatCompletionProviderResponse(BaseModel):
    """Raw response from ``POST /chat/completions``."""

    id: str | None = None
    model: str | None = None
    choices: list[ChatChoice] = Field(default_factory=list)
    usage: ChatUsage | None = None
