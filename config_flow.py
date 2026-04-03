"""Config flow for StreamClaw."""

from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import llm
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TemplateSelector,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_BASE_URL,
    CONF_CHAT_MODEL,
    CONF_LLM_HASS_API,
    CONF_MAX_TOKENS,
    CONF_PROMPT,
    CONF_TIMEOUT,
    DEFAULT_BASE_URL,
    DEFAULT_CHAT_MODEL,
    DEFAULT_MAX_TOKENS,
    DEFAULT_PROMPT,
    DEFAULT_TIMEOUT,
    DOMAIN,
    LOGGER,
)


async def _validate_connection(
    hass: HomeAssistant, base_url: str, api_key: str
) -> None:
    """Validate the API connection."""
    url = f"{base_url.rstrip('/')}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": DEFAULT_CHAT_MODEL,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
        "stream": False,
    }

    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status == 401:
                    raise InvalidAuth
                if resp.status == 403:
                    raise InvalidAuth
                if resp.status >= 400:
                    body = await resp.text()
                    LOGGER.error(
                        "OpenClaw validation failed with status %s: %s",
                        resp.status,
                        body,
                    )
                    raise CannotConnect
    except (aiohttp.ClientError, TimeoutError) as err:
        LOGGER.error("Could not connect to OpenClaw at %s: %s", url, err)
        raise CannotConnect from err


class StreamClawConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for StreamClaw."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                await _validate_connection(
                    self.hass,
                    user_input[CONF_BASE_URL],
                    user_input[CONF_API_KEY],
                )
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=f"StreamClaw ({user_input[CONF_BASE_URL]})",
                    data={
                        CONF_BASE_URL: user_input[CONF_BASE_URL],
                        CONF_API_KEY: user_input[CONF_API_KEY],
                    },
                    options={
                        CONF_CHAT_MODEL: DEFAULT_CHAT_MODEL,
                        CONF_PROMPT: DEFAULT_PROMPT,
                        CONF_TIMEOUT: DEFAULT_TIMEOUT,
                        CONF_MAX_TOKENS: DEFAULT_MAX_TOKENS,
                        CONF_LLM_HASS_API: "",
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_BASE_URL, default=DEFAULT_BASE_URL): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.URL)
                    ),
                    vol.Required(CONF_API_KEY): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigFlow,
    ) -> StreamClawOptionsFlow:
        """Create the options flow."""
        return StreamClawOptionsFlow()


class StreamClawOptionsFlow(OptionsFlow):
    """Handle options for StreamClaw."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options

        # Build LLM API options
        llm_apis = [
            SelectOptionDict(value="", label="None (no device control)")
        ]
        for api in await llm.async_get_apis(self.hass):
            llm_apis.append(SelectOptionDict(value=api.id, label=api.name))

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_CHAT_MODEL,
                        default=options.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL),
                    ): TextSelector(),
                    vol.Optional(
                        CONF_PROMPT,
                        default=options.get(CONF_PROMPT, DEFAULT_PROMPT),
                    ): TemplateSelector(),
                    vol.Optional(
                        CONF_TIMEOUT,
                        default=options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0, max=300, step=1, mode=NumberSelectorMode.BOX
                        )
                    ),
                    vol.Optional(
                        CONF_MAX_TOKENS,
                        default=options.get(CONF_MAX_TOKENS, DEFAULT_MAX_TOKENS),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1, max=65536, step=1, mode=NumberSelectorMode.BOX
                        )
                    ),
                    vol.Optional(
                        CONF_LLM_HASS_API,
                        default=options.get(CONF_LLM_HASS_API, ""),
                    ): SelectSelector(
                        SelectSelectorConfig(options=llm_apis)
                    ),
                }
            ),
        )


class InvalidAuth(Exception):
    """Error to indicate invalid authentication."""


class CannotConnect(Exception):
    """Error to indicate we cannot connect."""
