"""A small provider interface for hosted LLMs, with an OpenAI Responses API implementation.

The rest of the bot talks to `LanguageModel`, so tests use a scripted fake and no test ever calls the network.
Requests are stateless (`store=False`): OpenAI keeps no conversation state for Rudhabot, and Rudhabot stores none either.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol


@dataclass
class ToolCall:
    call_id: str
    name: str
    arguments: str


@dataclass
class LLMTurn:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    output_items: list[Any] = field(default_factory=list)      # passed back verbatim when continuing a tool loop
    usage: dict = field(default_factory=dict)


class LanguageModel(Protocol):
    model: str

    async def create(self, *, instructions: str, input: list, tools: list, text_format: Optional[dict],
                     max_output_tokens: int, safety_identifier: Optional[str] = None) -> LLMTurn: ...


class OpenAIResponsesModel:
    """OpenAI Responses API (`client.responses.create`) with strict function tools and JSON-schema structured output."""

    def __init__(self, api_key: str, model: str, timeout: float = 20.0, max_retries: int = 1,
                 reasoning_effort: Optional[str] = None):
        from openai import AsyncOpenAI           # imported lazily: the bot runs without the package if AI is off
        self._client = AsyncOpenAI(api_key=api_key, timeout=timeout, max_retries=max_retries)
        self.model = model
        self.reasoning_effort = reasoning_effort or None

    async def create(self, *, instructions, input, tools, text_format, max_output_tokens, safety_identifier=None) -> LLMTurn:
        kwargs: dict[str, Any] = {"model": self.model, "instructions": instructions, "input": input,
                                  "max_output_tokens": max_output_tokens, "store": False}
        if tools:
            kwargs["tools"] = tools
            kwargs["parallel_tool_calls"] = False
        if text_format:
            kwargs["text"] = {"format": text_format}
        if safety_identifier:
            kwargs["safety_identifier"] = safety_identifier
        if self.reasoning_effort:
            kwargs["reasoning"] = {"effort": self.reasoning_effort}
            kwargs["include"] = ["reasoning.encrypted_content"]      # needed to continue a stateless tool loop
        response = await self._client.responses.create(**kwargs)
        calls = [ToolCall(item.call_id, item.name, item.arguments) for item in response.output
                 if getattr(item, "type", None) == "function_call"]
        usage = {}
        if getattr(response, "usage", None) is not None:
            usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
        return LLMTurn(response.output_text or "", calls, list(response.output), usage)

    async def close(self) -> None:
        await self._client.close()
