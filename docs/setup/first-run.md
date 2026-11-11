---
title: Your First Tool Call
description: Discover enabled tools, preview an image request without charges, and retrieve background results.
---

Complete [installation](install.md) and your
[client integration](../integrations/index.md) first. Examples below are MCP
tool-call envelopes: the `name` selects the tool and `arguments` contains its
input. They are not shell commands.

## Discover Before Spending

```json
{
  "name": "ark_list_models",
  "arguments": {
    "input": {}
  }
}
```

Read exact model IDs, capability limits, serving tools and enabled services.
Models and gates come from server configuration. Discovery itself makes no
provider calls; cached pricing may come from an optional background refresher.
It does not validate the provider key or account whitelist.

Next, discover long-running targets and their full input schemas:

```json
{
  "name": "ark_job_capabilities",
  "arguments": {}
}
```

Unlike most Ark tools, `ark_job_capabilities` takes `{}` without an `input`
wrapper. If `seedream_generate_image` is absent, enable ModelArk and reconnect
before trying the next step.

## Preview an Image Request

Use the ordinary background-job tools by default. You do not need a client
that supports native MCP task augmentation.

```json
{
  "name": "ark_job_submit",
  "arguments": {
    "input": {
      "tool_name": "seedream_generate_image",
      "arguments": {
        "input": {
          "prompt": "A red ceramic cup on a wooden table.",
          "dry_run": true
        }
      }
    }
  }
}
```

Both `input` wrappers matter: the outer one belongs to job submission and the
inner one to Seedream. This text-only dry run previews the request and cost
without generating an image or making a billable generation request.
Dry runs still require the target to be registered.

## Retrieve the Job Result

Save the returned `job_id`. Wait at least `poll_after_ms` milliseconds, then
poll through the same client/server connection:

```json
{
  "name": "ark_job_get",
  "arguments": {
    "input": {
      "job_id": "example-job-id-replace-me"
    }
  }
}
```

Continue while `status` is `working`, using each returned `poll_after_ms`.
On a terminal response, inspect status and `result.is_error` before reading
`result.structured_content`. A completed worker may contain an original tool
error; completion alone is not generation success.

Default in-memory jobs are lost on server restart. Another stdio client
starts another process and cannot retrieve this process's jobs.
An ambiguous submission or timeout is not permission to submit again.

## Generate Deliberately

Once you have reviewed the preview and approved possible charges, change
`dry_run` to `false` and submit a new job. Generation is billable.
Choose model IDs and parameters from discovery, not from ComfyUI model lists:
the two integrations can expose different models and options.

Inspect returned variations individually. Billed outputs may include a
provider URL and `persistence_error` if storage failed. Retrieve persisted
media with the [artifact tools](../artifacts.md), or recover a URL through
`seed_media_persist_url` before it expires.

Explicit local output paths from background jobs require `OUTPUT_ROOTS`,
because worker jobs have no client MCP roots. HTTP servers cannot write to
the client machine.

## Video and Other Provider Tasks

A worker job and a provider task are different:

1. Submit a create tool through `ark_job_submit`.
2. Poll `ark_job_get` and save the provider task ID from its structured result.
3. Poll `gen_task_get` for Seedance/3D, `vod_get_task` for MediaKit, or
   `omnihuman_get_task` for avatars.
4. Request output persistence through a background job when a large download
   may take time. Never repeat a create call to check status.

`ark_job_cancel` is cooperative local cancellation. It does not guarantee
that upstream work already accepted by a provider is cancelled.

Continue with [Choose a Tool](../capabilities.md),
[worked examples](../tools.md), or the
[Python client](../integrations/python.md).
