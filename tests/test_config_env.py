"""Environment-only configuration regressions for Fargate tasks."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.config import Config


def test_environment_module_controls(monkeypatch):
    monkeypatch.setenv("MODULE_USER_MATCH_ENABLED", "false")
    monkeypatch.setenv("MODULE_CLEANUP_DRY_RUN", "true")
    monkeypatch.setenv("MODULE_MODEL_SYNC_ENABLED", "false")
    monkeypatch.setenv("HEALTH_CHECK_MAX_WORKERS", "4")
    monkeypatch.setenv("HEALTH_CHECK_SCAN_ERROR_RATIO_THRESHOLD", "0.25")

    modules = Config._build_from_env()["modules"]

    assert modules["user_match"]["enabled"] is False
    assert modules["cleanup"]["dry_run"] is True
    assert modules["model_sync"]["enabled"] is False
    assert modules["health_check"]["max_workers"] == 4
    assert modules["health_check"]["scan_error_ratio_threshold"] == 0.25


def test_rehire_dry_run_supports_legacy_and_generic_environment_names(monkeypatch):
    monkeypatch.setenv("REHIRE_DETECTION_DRY_RUN", "false")
    modules = Config._build_from_env()["modules"]
    assert modules["rehire_detection"]["dry_run"] is False

    monkeypatch.setenv("MODULE_REHIRE_DETECTION_DRY_RUN", "true")
    modules = Config._build_from_env()["modules"]
    assert modules["rehire_detection"]["dry_run"] is True