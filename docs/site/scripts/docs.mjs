import { posix } from "node:path";
import examples from "../tool-examples.json" with { type: "json" };

export const SITE_BASE = "/ark-mcp";
export const SOURCE_BASE = "https://github.com/byteplus-sa/ark-mcp/blob/main/";

export function handbookPage(source, content, toolCount) {
  content = content.replaceAll("\r\n", "\n");
  const editUrl = JSON.stringify(
    `${SOURCE_BASE.replace("/blob/", "/edit/")}docs/${source.replaceAll("\\", "/")}`,
  );
  content = content.replaceAll("__TOOL_COUNT__", String(toolCount));
  if (content.startsWith("---\n")) {
    return content.replace(/^---\n([\s\S]*?)\n---/, (block, metadata) =>
      /^editUrl:/m.test(metadata)
        ? block
        : `---\n${metadata}\neditUrl: ${editUrl}\n---`,
    );
  }
  const title =
    content.match(/^# (.+)\r?$/m)?.[1] || source.replace(/\.mdx?$/, "");
  return `---\ntitle: ${JSON.stringify(title)}\neditUrl: ${editUrl}\n---\n\n${content.replace(/^# .+\r?\n/, "")}`;
}

export function resolveDocLink(source, target) {
  if (/^(?:[a-z][a-z\d+.-]*:|#|\/)/i.test(target)) return target;
  const match = target.match(/^([^?#]*)(.*)$/);
  const path = posix.normalize(
    posix.join("docs", posix.dirname(source), decodeURI(match[1])),
  );
  if (path.startsWith("../"))
    throw new Error(`Link outside repository: ${source}: ${target}`);
  if (path.startsWith("docs/") && /\.mdx?$/.test(path)) {
    const slug = path
      .slice(5)
      .replace(/\.mdx?$/, "")
      .replaceAll(".", "")
      .replace(/(?:^|\/)index$/, "");
    return `${SITE_BASE}/${slug ? `${slug}/` : ""}${match[2]}`;
  }
  return `${SOURCE_BASE}${path}${match[2]}`;
}

function cell(value) {
  return String(value).replaceAll("|", "\\|").replace(/\r?\n/g, " ");
}

function fieldType(field) {
  if (field.$ref) return field.$ref.split("/").at(-1);
  if (field.anyOf || field.oneOf)
    return (field.anyOf || field.oneOf).map(fieldType).join(" or ");
  if (field.enum)
    return field.enum.map((value) => JSON.stringify(value)).join(", ");
  if ("const" in field) return JSON.stringify(field.const);
  if (field.type === "array") return `array of ${fieldType(field.items || {})}`;
  return field.type || "object";
}

export function schemaTable(schema) {
  const fields = Object.entries(schema.properties || {});
  if (fields.length === 0) return "No fields. Pass `{}`.\n";
  const rows = [
    "| Field | Type | Required | Description and constraints |",
    "| --- | --- | --- | --- |",
  ];
  for (const [name, field] of fields) {
    const constraints = [
      "minimum",
      "maximum",
      "exclusiveMinimum",
      "exclusiveMaximum",
      "minLength",
      "maxLength",
      "minItems",
      "maxItems",
      "pattern",
      "format",
    ]
      .filter((key) => key in field)
      .map((key) => `${key}: ${JSON.stringify(field[key])}`);
    if ("default" in field)
      constraints.push(`Default: ${JSON.stringify(field.default)}`);
    const description = [field.description || "", ...constraints]
      .filter(Boolean)
      .join("; ");
    rows.push(
      `| \`${cell(name)}\` | ${cell(fieldType(field))} | ${(schema.required || []).includes(name) ? "Yes" : "No"} | ${cell(description)} |`,
    );
  }
  return `${rows.join("\n")}\n`;
}

export const EXAMPLES = examples;

export function toolPage(tool) {
  const name = tool.name;
  const mode =
    tool.background === "required"
      ? "Background required"
      : tool.background === "optional"
        ? "Foreground or background"
        : "Foreground only";
  const input = tool.inputSchema;
  const inputRef = input.properties?.input?.$ref;
  const innerInput = inputRef
    ? input.$defs[inputRef.split("/").at(-1)]
    : input.properties?.input || input;
  const args = EXAMPLES[name];
  const call =
    args &&
    (tool.background === "required"
      ? {
          name: "ark_job_submit",
          arguments: { input: { tool_name: name, arguments: args } },
        }
      : { name, arguments: args });
  const execution =
    tool.background === "required"
      ? "Submit through `ark_job_submit` with `tool_name` and the complete target `arguments`, including its inner `input` object. Poll `ark_job_get` using the returned Ark `job_id`, respecting `poll_after_ms`. Inspect both terminal status and `result.is_error`; the original output is in `result.structured_content`. Do not call this tool in the foreground."
      : tool.background === "optional"
        ? "Use a direct foreground call for lightweight operations. Submit through `ark_job_submit` when output persistence or a large download may take time. The full arguments still include the `input` wrapper."
        : "Call this tool directly. It is not a background-job target.";
  const providerTasks = [
    "seedance_",
    "hyper3d_",
    "hitem3d_",
    "omnihuman_",
    "vod_",
    "gen_task_",
  ].some((prefix) => name.startsWith(prefix));
  return `---
title: ${JSON.stringify(name)}
description: ${JSON.stringify(`Inputs, outputs, setup requirements and execution guidance for ${name}.`)}
editUrl: ${JSON.stringify(`${SOURCE_BASE.replace("/blob/", "/edit/")}${tool.module?.replaceAll(".", "/").replace(/^ark_mcp/, "src/ark_mcp") || `src/ark_mcp/tools/${name}`}.py`)}
---

${tool.description}

## Setup

| Requirement | Value |
| --- | --- |
| Registration | ${cell(tool.registration_gate)} |
| Execution | ${mode} |
| Required permission | ${tool.scope ? `\`${tool.scope}\`` : "No fixed per-tool scope; HTTP authentication and target-tool authorization still apply."} |

This site lists the full configured capability surface, not the tools enabled on your particular server. Use [model discovery](${SITE_BASE}/tool-reference/ark-list-models/) and [job discovery](${SITE_BASE}/tool-reference/ark-job-capabilities/) to check your connection. Credentials are server configuration, never tool arguments.

## Usage

Ask your connected AI client to use \`${name}\` for the operation you want.
The client constructs the arguments and handles tool calls. Review its proposed
operation before approving anything billable. For a non-billable introduction,
start with [discovery without generation](${SITE_BASE}/setup/first-run/#discover-before-spending).

${execution}

${
  call
    ? `<details>
<summary>Exact MCP call (for developers)</summary>

The following is an MCP tool-call envelope, not a shell command. Example media URLs and identifiers must be replaced with your own accessible resources. Seedream and Seedance examples are non-billable dry runs; other provider operations may incur charges.

\`\`\`json
${JSON.stringify(call, null, 2)}
\`\`\`

</details>
`
    : `Match the input fields below and the full JSON Schema. See the [worked examples](${SITE_BASE}/tools/) for this capability and [Background Execution](${SITE_BASE}/integration-guide/#background-execution) for job/result handling.`
}

${providerTasks ? `An Ark job ID is not a provider task ID. Video and 3D tasks use [gen_task_get](${SITE_BASE}/tool-reference/gen-task-get/), VOD tasks use [vod_get_task](${SITE_BASE}/tool-reference/vod-get-task/), and avatars use [omnihuman_get_task](${SITE_BASE}/tool-reference/omnihuman-get-task/). Save accepted provider task IDs and resume polling instead of submitting a second generation.` : ""}

## Inputs

${input.properties?.input ? 'The MCP arguments object wraps these fields as `{"input": {...}}`.' : "This tool has no `input` wrapper."}

${schemaTable(innerInput)}

## Outputs

${schemaTable(tool.outputSchema)}

## Nested Types

Field types named above refer to these shared definitions. Optional/null fields, enum choices and bounds are retained in the complete schemas below. Schema validation is followed by runtime capability and security checks.

${Object.entries(input.$defs || {})
  .map(
    ([name, schema]) =>
      `<details>\n<summary>${name}</summary>\n\n${schemaTable(schema)}\n</details>\n`,
  )
  .join("\n")}

## Complete JSON Schemas

<details>
<summary>Input schema, including the MCP wrapper</summary>

\`\`\`json
${JSON.stringify(input, null, 2)}
\`\`\`

</details>

<details>
<summary>Output schema, including nested definitions</summary>

\`\`\`json
${JSON.stringify(tool.outputSchema, null, 2)}
\`\`\`

</details>

## Troubleshooting

- Missing tool: check [provider credentials](${SITE_BASE}/api-keys/) and feature flags, then restart the connection.
- Invalid arguments: keep the correct wrapper and follow current model limits from discovery. A valid schema alone does not prove the chosen model supports the combination.
- A job completes with an error: inspect the original tool result, not only the outer job status. Never automatically resubmit potentially billable work.
- Local output writes from jobs require server-configured \`OUTPUT_ROOTS\`; HTTP cannot write to a client's local disk.

See [configuration](${SITE_BASE}/configuration/), [background execution](${SITE_BASE}/integration-guide/#background-execution), and [artifact delivery](${SITE_BASE}/artifacts/).
`;
}
