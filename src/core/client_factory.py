"""
Client factory — centralised creation of API clients from config.
Eliminates ~150 lines of duplicated initialization across modules.
"""
import json
import logging
from pathlib import Path
from typing import Any, Dict

from core.config import Config
from clients.jamf import JamfClient
from clients.snipeit import SnipeITClient
from clients.azure import AzureClient
from clients.slack import SlackClient

logger = logging.getLogger(__name__)


def load_user_overrides(path: str = "config/user_overrides.json") -> Dict[str, Any]:
    """Load manual user matching overrides. Empty dict if file missing."""
    p = Path(path)
    if not p.exists():
        # Try Docker default location
        p = Path("/app/config/user_overrides.json")
    if not p.exists():
        return {}
    try:
        with open(p, "r") as f:
            data = json.load(f)
        overrides = data.get("overrides", {})
        if overrides:
            logger.info(f"Loaded {len(overrides)} user matching overrides from {p}")
        return overrides
    except Exception as e:
        logger.warning(f"Could not load user overrides from {p}: {e}")
        return {}


def create_jamf_client(config: Config) -> JamfClient:
    return JamfClient(
        base_url=config.jamf.base_url,
        username=config.jamf.username,
        password=config.jamf.password,
        client_id=config.jamf.client_id,
        client_secret=config.jamf.client_secret,
        timeout=config.api.timeout_seconds,
        max_retries=config.api.max_retries,
        retry_delay=config.api.retry_delay_seconds,
    )


def create_snipeit_client(config: Config) -> SnipeITClient:
    return SnipeITClient(
        base_url=config.snipeit.base_url,
        api_token=config.snipeit.api_token,
        timeout=config.api.timeout_seconds,
        max_retries=config.api.max_retries,
        retry_delay=config.api.retry_delay_seconds,
        rate_limit_wait=config.api.rate_limit_wait_seconds,
    )


def create_azure_client(config: Config) -> AzureClient:
    return AzureClient(
        tenant_id=config.azure.tenant_id,
        client_id=config.azure.client_id,
        client_secret=config.azure.client_secret,
        scope=config.azure.scope,
        timeout=config.api.timeout_seconds,
    )


def create_slack_client(config: Config) -> SlackClient:
    return SlackClient(
        bot_token=config.slack.bot_token,
        channel_id=config.slack.channel_id,
        enabled=config.slack.enabled,
    )
