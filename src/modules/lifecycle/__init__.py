"""Lifecycle modules — onboarding, offboarding, user enrichment."""
from .azure_starters import AzureStartersModule, run_azure_starters
from .leavers import LeaversModule, run_leavers
from .rehire_detection import RehireDetectionModule, run_rehire_detection
from .user_enrichment import UserEnrichmentModule

__all__ = [
    "AzureStartersModule", "run_azure_starters",
    "LeaversModule", "run_leavers",
    "RehireDetectionModule", "run_rehire_detection",
    "UserEnrichmentModule",
]
