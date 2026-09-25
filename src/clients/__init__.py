"""
Jamf-SnipeIT Suite - API Clients
All external service clients.
"""
from .jamf import JamfClient
from .snipeit import SnipeITClient
from .azure import AzureClient
from .slack import SlackClient

# Optional HiBob client
try:
    from .hibob import HiBobClient
    _HAS_HIBOB = True
except ImportError:
    HiBobClient = None
    _HAS_HIBOB = False

__all__ = [
    "JamfClient",
    "SnipeITClient",
    "AzureClient",
    "SlackClient",
    "HiBobClient",
]
