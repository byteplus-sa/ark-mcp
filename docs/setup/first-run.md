---
title: Your First Tool Call
description: Ask your agent to generate an image or video with a simple prompt.
---

After [setup](install.md), **describe what you want to create in your agent's
chat**. The agent handles model discovery, tool calls and progress checks.
You do not need to write JSON, choose tool names or run terminal commands.

## Generate an Image

Copy this prompt into your connected agent:

```text
Use ark-seed to generate one image of a red ceramic cup on a wooden
table beside a window. Warm morning light, realistic product photography,
soft shadows, square composition. Show me the finished image and a
download link.
```

<span id="generate-deliberately"></span>

**Generation may incur charges.** Review the agent's proposed operation and
any tool approval before proceeding. Each generation prompt is a separate
request. If you only want to check setup without generating, use the
[optional checks below](#discover-before-spending).

Change the subject, style or composition to suit your idea. The agent should
choose a supported model from your configured services. If image generation
is unavailable, it should explain what is missing instead of substituting
another service without asking.

For transparent PNGs, reference edits and a no-charge request preview, see
[the Seedream tool reference](/ark-mcp/tool-reference/seedream-generate-image/#seedream-5-5).
Seedream 5.5 is the built-in image default in the
server revision documented here; older installations and explicit operator
defaults may select a different model.

<span id="video-and-other-provider-tasks"></span>

## Generate a Video

When video generation is enabled, try:

```text
Use ark-seed to generate one 5-second landscape video of a red ceramic
cup on a wooden table beside a window. The camera slowly moves closer
as steam rises in warm morning light. Realistic, cinematic style.
Show me the finished video and a download link.
```

This creates a new video from text; it does not reuse the image above.
The agent should keep checking the existing request until it finishes.
A job or task ID alone is not the finished video.

<span id="retrieve-the-job-result"></span>

## If the Agent Pauses

Send a follow-up in the same chat:

```text
Continue checking my existing request and show me the result when it is
ready. Do not submit a new generation request. If the existing request
cannot be recovered, explain what happened before doing anything else.
```

Keep the same client connection open while it works. Restarting the server
can lose in-memory jobs; a timeout does not mean generation failed.
Repeating the generation prompt could create another charge.

Download results you want to keep. Provider links expire, and inline previews
depend on your client. If a preview is unavailable, use the returned download
link. For persistent storage, see [Artifacts](../artifacts.md).

## Discover Before Spending

This optional check lists configured capabilities without creating media:

```text
Use ark-seed discovery only to show which media services and models are
configured for me. Do not generate media, analyze content, transcribe,
upload files or submit jobs.
```

Discovery makes no provider calls. It does not prove that your provider key
or account is authorized to generate.

## Preview an Image Request

To check an image request without paying for generation:

```text
Preview a text-only Seedream request for one image of a red ceramic cup
on a wooden table. Show the request and estimated cost if available.
Do not generate an image or make a billable request. If a no-charge
preview is unavailable, stop and explain what is missing.
```

A request preview is not a generated image. Only approve a dry-run call
with `dry_run: true`, never a substitute generation call. Seedream must be
enabled even for a preview.

## Technical Reference

Developers can find exact arguments in the
[tool reference](../api-reference.md) and job/result handling in
[Background Execution](../integration-guide.md#background-execution).
For other capabilities, see [Choose a Tool](../capabilities.md).
