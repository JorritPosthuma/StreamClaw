"""Constants for the StreamClaw integration."""

import logging

DOMAIN = "streamclaw"
LOGGER = logging.getLogger(__package__)

CONF_BASE_URL = "base_url"
CONF_CHAT_MODEL = "chat_model"
CONF_PROMPT = "prompt"
CONF_TIMEOUT = "timeout"
CONF_MAX_TOKENS = "max_tokens"
CONF_LLM_HASS_API = "llm_hass_api"

DEFAULT_BASE_URL = "http://localhost:8080"
DEFAULT_CHAT_MODEL = "openclaw:main"
DEFAULT_TIMEOUT = 60
DEFAULT_MAX_TOKENS = 1024
DEFAULT_PROMPT = ""

MAX_TOOL_ITERATIONS = 10

RECOMMENDED_CHAT_MODEL = "openclaw:main"
