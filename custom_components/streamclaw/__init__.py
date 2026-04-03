"""The StreamClaw integration."""

from __future__ import annotations

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant

from .const import CONF_BASE_URL, CONF_TIMEOUT, DEFAULT_TIMEOUT

PLATFORMS = ["conversation"]

type StreamClawConfigEntry = ConfigEntry[aiohttp.ClientSession]


async def async_setup_entry(hass: HomeAssistant, entry: StreamClawConfigEntry) -> bool:
    """Set up StreamClaw from a config entry."""
    timeout_seconds = entry.options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)
    timeout = (
        aiohttp.ClientTimeout(connect=30)
        if timeout_seconds == 0
        else aiohttp.ClientTimeout(total=timeout_seconds)
    )

    session = aiohttp.ClientSession(
        headers={"Authorization": f"Bearer {entry.data[CONF_API_KEY]}"},
        timeout=timeout,
    )
    entry.runtime_data = session

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: StreamClawConfigEntry
) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.close()
    return unload_ok


async def _async_options_updated(
    hass: HomeAssistant, entry: StreamClawConfigEntry
) -> None:
    """Handle options update by reloading the entry."""
    await hass.config_entries.async_reload(entry.entry_id)
