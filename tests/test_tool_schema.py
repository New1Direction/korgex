"""
Tool schema validity — array properties must carry `items`.

register_user_tool built each property from only type/description/default/enum,
silently dropping `items` (for arrays) and `properties` (for nested objects).
OpenAI, Anthropic, and most OpenRouter models tolerate an array schema with no
items; Google's Gemini API enforces JSON Schema and rejects the whole request:

  GenerateContentRequest.tools[0].function_declarations[N]
    .parameters.properties[questions].items: missing field

So a latent invalid-schema bug only surfaced when driving Gemini. These tests
pin the fix: items/properties survive translation, and the two shipped array
tools (AskUserQuestion.questions, TaskCreate.tasks) are valid.
"""

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.tool_abstraction import register_user_tool, USER_TOOLS  # noqa: E402


def test_array_param_keeps_items_through_translation():
    register_user_tool("ZZArrayItemsProbe", "probe", [
        {"name": "xs", "type": "array", "items": {"type": "string"},
         "required": True, "description": "a list of strings"},
    ])
    prop = USER_TOOLS["ZZArrayItemsProbe"]["input_schema"]["properties"]["xs"]
    assert prop["type"] == "array"
    assert prop["items"] == {"type": "string"}


def test_object_param_keeps_nested_properties():
    register_user_tool("ZZObjectPropsProbe", "probe", [
        {"name": "cfg", "type": "object",
         "properties": {"k": {"type": "string"}},
         "description": "a config object"},
    ])
    prop = USER_TOOLS["ZZObjectPropsProbe"]["input_schema"]["properties"]["cfg"]
    assert prop["properties"] == {"k": {"type": "string"}}


def test_shipped_array_tools_are_valid_for_strict_providers():
    for tool, name in [("AskUserQuestion", "questions"), ("TaskCreate", "tasks")]:
        schema = USER_TOOLS[tool]["input_schema"]["properties"][name]
        assert schema["type"] == "array"
        assert "items" in schema, f"{tool}.{name} array is missing items (Gemini rejects)"
