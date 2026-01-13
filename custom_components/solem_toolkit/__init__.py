import logging
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

async def async_setup(hass: HomeAssistant, config: dict):
    _LOGGER.info("Solem Toolkit loaded.")

    # Register services
    async_setup_services(hass)

    return True
