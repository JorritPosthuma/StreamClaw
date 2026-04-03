"""Conversation entity for the StreamClaw integration."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Callable, Iterable
from typing import Any, Literal

import aiohttp
from voluptuous_openapi import convert as vol_to_openapi

from homeassistant.components import conversation
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import StreamClawConfigEntry
from .const import (
    CONF_BASE_URL,
    CONF_CHAT_MODEL,
    CONF_LLM_HASS_API,
    CONF_MAX_TOKENS,
    CONF_PROMPT,
    DEFAULT_CHAT_MODEL,
    DEFAULT_MAX_TOKENS,
    DEFAULT_PROMPT,
    DOMAIN,
    LOGGER,
    MAX_TOOL_ITERATIONS,
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: StreamClawConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up conversation entities."""
    async_add_entities([StreamClawConversationEntity(config_entry)])


class StreamClawConversationEntity(conversation.ConversationEntity):
    """StreamClaw conversation agent."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supports_streaming = True

    def __init__(self, entry: StreamClawConfigEntry) -> None:
        """Initialize the entity."""
        self.entry = entry
        self._attr_unique_id = entry.entry_id
        if entry.options.get(CONF_LLM_HASS_API):
            self._attr_supported_features = (
                conversation.ConversationEntityFeature.CONTROL
            )

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Return a list of supported languages."""
        return MATCH_ALL

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.entry.entry_id)},
            name=self.entry.title,
            manufacturer="OpenClaw",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        """Process a conversation turn."""
        options = self.entry.options

        try:
            await chat_log.async_provide_llm_data(
                user_input.as_llm_context(DOMAIN),
                options.get(CONF_LLM_HASS_API) or None,
                options.get(CONF_PROMPT) or None,
                user_input.extra_system_prompt,
            )
        except conversation.ConverseError as err:
            return err.as_conversation_result()

        await self._async_handle_chat_log(chat_log)

        return conversation.async_get_result_from_chat_log(user_input, chat_log)

    async def _async_handle_chat_log(self, chat_log: conversation.ChatLog) -> None:
        """Send messages to OpenClaw and process streaming response."""
        options = self.entry.options
        session: aiohttp.ClientSession = self.entry.runtime_data
        base_url = self.entry.data[CONF_BASE_URL].rstrip("/")
        url = f"{base_url}/v1/chat/completions"

        for _iteration in range(MAX_TOOL_ITERATIONS):
            messages = _convert_chat_log_to_messages(chat_log.content)

            tools = None
            if chat_log.llm_api:
                tools = [
                    _format_tool(tool, chat_log.llm_api.custom_serializer)
                    for tool in chat_log.llm_api.tools
                ]

            payload: dict[str, Any] = {
                "model": options.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL),
                "messages": messages,
                "stream": True,
                "max_tokens": int(
                    options.get(CONF_MAX_TOKENS, DEFAULT_MAX_TOKENS)
                ),
            }
            if tools:
                payload["tools"] = tools

            try:
                async with session.post(url, json=payload) as resp:
                    if resp.status == 401:
                        raise HomeAssistantError(
                            translation_domain=DOMAIN,
                            translation_key="authentication_error",
                        )
                    if resp.status != 200:
                        body = await resp.text()
                        LOGGER.error(
                            "OpenClaw API error %s: %s", resp.status, body
                        )
                        raise HomeAssistantError(
                            translation_domain=DOMAIN,
                            translation_key="api_error",
                            translation_placeholders={"status": str(resp.status)},
                        )

                    sse_stream = _parse_sse_stream(resp)
                    delta_stream = _transform_stream(chat_log, sse_stream)

                    async for _content in chat_log.async_add_delta_content_stream(
                        self.entity_id, delta_stream
                    ):
                        pass

            except aiohttp.ClientError as err:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="connection_error",
                    translation_placeholders={"message": str(err)},
                ) from err

            if not chat_log.unresponded_tool_results:
                break


def _convert_chat_log_to_messages(
    chat_content: Iterable[conversation.Content],
) -> list[dict[str, Any]]:
    """Convert ChatLog content to OpenAI Chat Completions messages format."""
    messages: list[dict[str, Any]] = []

    for content in chat_content:
        if isinstance(content, conversation.SystemContent):
            messages.append({"role": "system", "content": content.content})

        elif isinstance(content, conversation.UserContent):
            messages.append({"role": "user", "content": content.content})

        elif isinstance(content, conversation.AssistantContent):
            msg: dict[str, Any] = {"role": "assistant"}
            if content.content:
                msg["content"] = content.content
            if content.tool_calls:
                msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.tool_name,
                            "arguments": json.dumps(tc.tool_args),
                        },
                    }
                    for tc in content.tool_calls
                ]
            messages.append(msg)

        elif isinstance(content, conversation.ToolResultContent):
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": content.tool_call_id,
                    "content": json.dumps(content.tool_result),
                }
            )

    return messages


def _format_tool(
    tool: llm.Tool,
    custom_serializer: Callable[[Any], Any] | None,
) -> dict[str, Any]:
    """Format an HA LLM tool as OpenAI function-calling tool definition."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": vol_to_openapi(
                tool.parameters, custom_serializer=custom_serializer
            ),
        },
    }


async def _parse_sse_stream(
    response: aiohttp.ClientResponse,
) -> AsyncGenerator[dict[str, Any]]:
    """Parse SSE stream from aiohttp response, yielding parsed JSON data."""
    async for line_bytes in response.content:
        line = line_bytes.decode("utf-8").rstrip("\n\r")
        if not line or line.startswith(":"):
            continue
        if line.startswith("data: "):
            data = line[6:]
            if data == "[DONE]":
                return
            try:
                yield json.loads(data)
            except json.JSONDecodeError:
                LOGGER.warning("Failed to parse SSE data: %s", data)


async def _transform_stream(
    chat_log: conversation.ChatLog,
    sse_stream: AsyncGenerator[dict[str, Any]],
) -> AsyncGenerator[
    conversation.AssistantContentDeltaDict
    | conversation.ToolResultContentDeltaDict
]:
    """Transform OpenAI-compatible SSE stream into HA delta format."""
    started = False
    current_tool_calls: dict[int, dict[str, Any]] = {}

    async for chunk in sse_stream:
        choices = chunk.get("choices", [])
        if not choices:
            if usage := chunk.get("usage"):
                chat_log.async_trace(
                    {
                        "stats": {
                            "input_tokens": usage.get("prompt_tokens", 0),
                            "output_tokens": usage.get("completion_tokens", 0),
                        }
                    }
                )
            continue

        choice = choices[0]
        delta = choice.get("delta", {})
        finish_reason = choice.get("finish_reason")

        if not started:
            yield {"role": "assistant"}
            started = True

        if content := delta.get("content"):
            yield {"content": content}

        if tool_calls := delta.get("tool_calls"):
            for tc in tool_calls:
                idx = tc["index"]
                if idx not in current_tool_calls:
                    current_tool_calls[idx] = {
                        "id": tc.get("id", ""),
                        "name": tc.get("function", {}).get("name", ""),
                        "arguments": "",
                    }
                else:
                    if tc.get("id"):
                        current_tool_calls[idx]["id"] = tc["id"]
                    if fn := tc.get("function"):
                        if fn.get("name"):
                            current_tool_calls[idx]["name"] = fn["name"]

                if fn := tc.get("function"):
                    if args := fn.get("arguments"):
                        current_tool_calls[idx]["arguments"] += args

        if finish_reason == "tool_calls" and current_tool_calls:
            for _idx, tc_data in sorted(current_tool_calls.items()):
                try:
                    tool_args = (
                        json.loads(tc_data["arguments"])
                        if tc_data["arguments"]
                        else {}
                    )
                except json.JSONDecodeError:
                    LOGGER.warning(
                        "Failed to parse tool arguments: %s",
                        tc_data["arguments"],
                    )
                    tool_args = {}

                yield {
                    "tool_calls": [
                        llm.ToolInput(
                            id=tc_data["id"],
                            tool_name=tc_data["name"],
                            tool_args=tool_args,
                        )
                    ],
                }
            current_tool_calls = {}

        if usage := chunk.get("usage"):
            chat_log.async_trace(
                {
                    "stats": {
                        "input_tokens": usage.get("prompt_tokens", 0),
                        "output_tokens": usage.get("completion_tokens", 0),
                    }
                }
            )
