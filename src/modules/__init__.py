"""
Jamf-SnipeIT Suite - Modules
Re-exports from sync/, lifecycle/, and maintenance/ subpackages.
"""
# --- Sync ---
from .sync import (
    UserMatchModule, run_user_match,
    SnipeToJamfModule, run_snipe_to_jamf,
    ModelSyncModule, run_model_sync, check_models,
    CorrectionModule, run_correction,
    PeripheralsSyncModule, run_peripherals_sync,
)

# --- Lifecycle ---
from .lifecycle import (
    AzureStartersModule, run_azure_starters,
    LeaversModule, run_leavers,
    RehireDetectionModule, run_rehire_detection,
    UserEnrichmentModule,
)

# --- Maintenance ---
from .maintenance import (
    WakeUpModule, run_wakeup_group, run_wakeup_serial, run_wakeup_file,
    ReconciliationModule, ReconciliationResults, run_reconciliation,
    CleanupModule, run_cleanup,
    UsernameStandardizer, run_username_standardize,
)

__all__ = [
    # Sync
    "UserMatchModule", "run_user_match",
    "SnipeToJamfModule", "run_snipe_to_jamf",
    "ModelSyncModule", "run_model_sync", "check_models",
    "CorrectionModule", "run_correction",
    "PeripheralsSyncModule", "run_peripherals_sync",
    # Lifecycle
    "AzureStartersModule", "run_azure_starters",
    "LeaversModule", "run_leavers",
    "RehireDetectionModule", "run_rehire_detection",
    "UserEnrichmentModule",
    # Maintenance
    "WakeUpModule", "run_wakeup_group", "run_wakeup_serial", "run_wakeup_file",
    "ReconciliationModule", "ReconciliationResults", "run_reconciliation",
    "CleanupModule", "run_cleanup",
    "UsernameStandardizer", "run_username_standardize",
]
