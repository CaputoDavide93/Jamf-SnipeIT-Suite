"""
Jamf-SnipeIT Suite - Core module
Contains unified API clients and configuration.
"""
from .config import Config, get_config, reload_config, ConfigurationError
from .jamf_client import JamfClient
from .snipe_client import SnipeITClient
from .azure_client import AzureClient
from .health import HealthCheckServer, start_health_server, stop_health_server, get_health_server

# Optional async clients (requires aiohttp)
try:
    from .async_clients import AsyncJamfClient, AsyncSnipeClient, ParallelProcessor, AsyncConfig
    _HAS_ASYNC = True
except ImportError:
    AsyncJamfClient = None
    AsyncSnipeClient = None
    ParallelProcessor = None
    AsyncConfig = None
    _HAS_ASYNC = False

__all__ = [
    # Config
    "Config",
    "get_config",
    "reload_config",
    "ConfigurationError",
    # Sync Clients
    "JamfClient",
    "SnipeITClient",
    "AzureClient",
    # Health Check
    "HealthCheckServer",
    "start_health_server",
    "stop_health_server",
    "get_health_server",
    # Async Clients (optional)
    "AsyncJamfClient",
    "AsyncSnipeClient",
    "ParallelProcessor",
    "AsyncConfig",
]
