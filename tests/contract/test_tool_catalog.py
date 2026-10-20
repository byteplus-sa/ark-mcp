"""Contract tests for the static tool catalog."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import BaseModel

from ark_mcp.background_jobs import BACKGROUND_TOOL_SPECS
from ark_mcp.config.env import get_settings
from ark_mcp.server import SERVER_INSTRUCTIONS, create_server
from ark_mcp.tools._catalog import CATALOG, catalog_entry, resolve_tool_definition

SECURITY_DOC = Path(__file__).resolve().parents[2] / "docs" / "security.md"


def _undescribed_fields(model: type[BaseModel], seen: set[type[BaseModel]]) -> list[str]:
    if model in seen:
        return []
    seen.add(model)
    missing = [
        f"{model.__name__}.{name}"
        for name, field in model.model_fields.items()
        if not field.description
    ]
    for field in model.model_fields.values():
        for nested in _nested_models(field.annotation):
            missing.extend(_undescribed_fields(nested, seen))
    return missing


def _nested_models(annotation: object) -> list[type[BaseModel]]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    return [
        model
        for argument in getattr(annotation, "__args__", ())
        for model in _nested_models(argument)
    ]


def test_catalog_names_are_unique() -> None:
    names = [entry.name for entry in CATALOG]
    assert len(names) == len(set(names))


@pytest.mark.parametrize("entry", CATALOG, ids=lambda entry: entry.name)
def test_entry_resolves_to_a_documented_tool(entry: object) -> None:
    definition = resolve_tool_definition(entry)  # type: ignore[arg-type]
    assert definition.handler.__doc__, "tool handler needs a docstring (tool description)"
    assert _undescribed_fields(definition.output_model, set()) == []


def test_every_scope_is_documented() -> None:
    security_doc = SECURITY_DOC.read_text(encoding="utf-8")
    scopes = {entry.scope for entry in CATALOG if entry.scope is not None}
    assert {scope for scope in scopes if f"`{scope}`" not in security_doc} == set()


def test_background_specs_are_derived_from_the_catalog() -> None:
    expected = {entry.name: entry.background for entry in CATALOG if entry.background}
    assert {name: spec.mode for name, spec in BACKGROUND_TOOL_SPECS.items()} == expected
    for name, spec in BACKGROUND_TOOL_SPECS.items():
        assert spec.required_scope == catalog_entry(name).scope  # type: ignore[union-attr]


def test_unknown_tool_has_no_catalog_entry() -> None:
    assert catalog_entry("seedance_does_not_exist") is None


async def test_all_catalog_tools_register_with_every_service(all_services_enabled: None) -> None:
    server = create_server(get_settings())
    tools = {tool.name: tool for tool in await server.list_tools()}
    assert set(tools) == {entry.name for entry in CATALOG}
    for entry in CATALOG:
        mode = tools[entry.name].task_config.mode
        assert mode == (entry.background or "forbidden"), entry.name


async def test_gated_tools_are_skipped_without_credentials() -> None:
    server = create_server(get_settings())
    names = {tool.name for tool in await server.list_tools()}
    always_on = {entry.name for entry in CATALOG if entry.gate(get_settings())}
    assert names == always_on
    assert "ark_list_models" in names
    assert "seedance_create_task" not in names


async def test_server_instructions_name_only_registered_tools(all_services_enabled: None) -> None:
    server = create_server(get_settings())
    names = {tool.name for tool in await server.list_tools()}
    pattern = r"\b(?:ark|seed|seedance|seedream|hyper3d|hitem3d|vod|omnihuman|gen|media|speech)_[a-z0-9_]+\b"
    mentioned = set(re.findall(pattern, SERVER_INSTRUCTIONS))
    assert mentioned
    assert mentioned - names == set()
    assert "ark_list_models" in mentioned
