"""LLM provider abstraction — protocol, response type, and JSON parsing."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol

from film_director.errors import LLMStructuredOutputError


@dataclass(frozen=True)
class LLMResponse:
    content: str        # raw LLM text
    parsed: dict | None  # parsed JSON if expect_json=True, else None
    model: str          # model name from response


class LLMProvider(Protocol):
    def chat(self, messages: list[dict], expect_json: bool = False) -> LLMResponse: ...
    def health(self) -> bool: ...


def parse_llm_json(raw: str) -> dict:
    """Robust JSON extraction from LLM output.

    Tries in order:
    1. Direct json.loads()
    2. Extract from ```json ... ``` markdown fences
    3. Find first { ... } block

    Raises LLMStructuredOutputError if all strategies fail or input is empty.
    Does NOT validate shape — returns whatever dict the LLM produced.
    """
    if not raw or not raw.strip():
        raise LLMStructuredOutputError(
            "Cannot parse JSON from empty LLM response",
            detail=repr(raw),
        )

    # Strategy 1: direct parse
    try:
        result = json.loads(raw.strip())
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # Strategy 2: markdown fence extraction
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fence_match:
        try:
            result = json.loads(fence_match.group(1))
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    # Strategy 3: first { ... } block
    brace_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if brace_match:
        try:
            result = json.loads(brace_match.group(0))
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    raise LLMStructuredOutputError(
        "LLM response could not be parsed as JSON",
        detail=raw[:500],
    )
