"""
Jamf-SnipeIT Suite - Configuration Loader
Unified configuration management for all modules.
"""
import os
import yaml
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class JamfConfig:
    """Jamf Pro configuration."""
    base_url: str = ""
    username: str = ""
    password: str = ""
    client_id: str = ""
    client_secret: str = ""
    ea_snipe_asset_id: str = "SnipeIT_Asset_ID"
    smart_group: str = "All Managed Clients"


@dataclass
class SnipeITConfig:
    """Snipe-IT configuration."""
    base_url: str = ""
    api_token: str = ""
    status_deployed_id: int = 1
    status_pending_id: int = 8
    status_checkedout_id: int = 2
    model_fallback_id: int = 40
    company_id: int = 1
    location_id: int = 0
    category_id: int = 0


@dataclass
class AzureConfig:
    """Azure AD configuration."""
    tenant_id: str = ""
    client_id: str = ""
    client_secret: str = ""
    scope: str = "https://graph.microsoft.com/.default"
    leavers_group_id: str = ""
    disabled_group_id: str = ""


@dataclass
class MatchingConfig:
    """User matching configuration."""
    email_domain: str = ""
    min_score: int = 14
    weight_lcs: float = 1.0
    weight_char_overlap: float = 0.3
    weight_bigram_dice: float = 2.0
    use_bigram_dice: bool = True
    allow_reassignment: bool = True
    skip_usernames: List[str] = field(default_factory=list)  # Generic/shared accounts to skip


@dataclass
class APIConfig:
    """API settings."""
    timeout_seconds: int = 30
    max_retries: int = 3
    retry_delay_seconds: int = 2
    rate_limit_wait_seconds: int = 60


@dataclass
class LoggingConfig:
    """Logging configuration."""
    level: str = "INFO"
    dir: str = "./logs"
    audit_csv: bool = True
    max_log_files: int = 30


@dataclass
class ModuleSettings:
    """Settings for individual modules."""
    enabled: bool = True
    dry_run: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)


class ConfigurationError(Exception):
    """Raised when configuration is invalid or missing required fields."""
    pass


class Config:
    """
    Unified configuration manager for Jamf-SnipeIT Suite.
    Loads configuration from YAML file and provides typed access.
    """
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize configuration from YAML file.
        
        Args:
            config_path: Path to config YAML file. If None, searches default locations.
        
        Raises:
            FileNotFoundError: If config file cannot be found
            ConfigurationError: If config is invalid or missing required fields
        """
        self._raw: Dict[str, Any] = {}
        self._config_path = self._resolve_config_path(config_path)
        self._load()
        
        # Parse into typed configs
        self.jamf = self._parse_jamf()
        self.snipeit = self._parse_snipeit()
        self.azure = self._parse_azure()
        self.matching = self._parse_matching()
        self.api = self._parse_api()
        self.logging = self._parse_logging()
        self.scheduler = self._raw.get("scheduler", {})
        self.modules = self._raw.get("modules", {})
        
        # Validate critical configuration
        self._validate()
    
    def _validate(self) -> None:
        """Validate that required configuration fields are present."""
        errors = []
        
        # Check Jamf config
        if not self.jamf.base_url:
            errors.append("jamf.base_url is required")
        if not (self.jamf.username and self.jamf.password) and not (self.jamf.client_id and self.jamf.client_secret):
            errors.append("jamf: Either username/password OR client_id/client_secret is required")
        
        # Check Snipe-IT config
        if not self.snipeit.base_url:
            errors.append("snipeit.base_url is required")
        if not self.snipeit.api_token:
            errors.append("snipeit.api_token is required")
        
        # Check Azure config (optional but warn if partially configured)
        azure_fields = [self.azure.tenant_id, self.azure.client_id, self.azure.client_secret]
        if any(azure_fields) and not all(azure_fields):
            errors.append("azure: If any Azure field is set, tenant_id, client_id, and client_secret are all required")
        
        if errors:
            error_msg = "Configuration validation failed:\n  - " + "\n  - ".join(errors)
            logger.error(error_msg)
            raise ConfigurationError(error_msg)
        
        logger.debug("Configuration validation passed")
    
    def _resolve_config_path(self, config_path: Optional[str]) -> Path:
        """Find the configuration file."""
        if config_path:
            path = Path(config_path)
            if path.exists():
                return path
            raise FileNotFoundError(f"Config file not found: {config_path}")
        
        # Search default locations
        search_paths = [
            Path("config/config.yaml"),
            Path("config.yaml"),
            Path("/app/config/config.yaml"),  # Docker default
            Path.home() / ".jamf-snipeit-suite" / "config.yaml",
        ]
        
        for path in search_paths:
            if path.exists():
                return path
        
        raise FileNotFoundError(
            f"Config file not found. Searched: {[str(p) for p in search_paths]}"
        )
    
    def _load(self) -> None:
        """Load configuration from YAML file."""
        logger.info(f"Loading configuration from: {self._config_path}")
        with open(self._config_path, "r", encoding="utf-8") as f:
            self._raw = yaml.safe_load(f) or {}
    
    def reload(self) -> None:
        """Reload configuration from file."""
        self._load()
        self.jamf = self._parse_jamf()
        self.snipeit = self._parse_snipeit()
        self.azure = self._parse_azure()
        self.matching = self._parse_matching()
        self.api = self._parse_api()
        self.logging = self._parse_logging()
        self.scheduler = self._raw.get("scheduler", {})
        self.modules = self._raw.get("modules", {})
    
    def _parse_jamf(self) -> JamfConfig:
        """Parse Jamf configuration section."""
        data = self._raw.get("jamf", {})
        return JamfConfig(
            base_url=data.get("base_url", "").rstrip("/"),
            username=data.get("username", ""),
            password=data.get("password", ""),
            client_id=data.get("client_id", ""),
            client_secret=data.get("client_secret", ""),
            ea_snipe_asset_id=data.get("ea_snipe_asset_id", "SnipeIT_Asset_ID"),
            smart_group=data.get("smart_group", "All Managed Clients"),
        )
    
    def _safe_int(self, value: Any, default: int, field_name: str = "") -> int:
        """Safely convert a value to int with error handling."""
        if value is None:
            return default
        try:
            return int(value)
        except (ValueError, TypeError) as e:
            logger.warning(f"Invalid integer value for {field_name}: {value}, using default {default}")
            return default
    
    def _safe_float(self, value: Any, default: float, field_name: str = "") -> float:
        """Safely convert a value to float with error handling."""
        if value is None:
            return default
        try:
            return float(value)
        except (ValueError, TypeError) as e:
            logger.warning(f"Invalid float value for {field_name}: {value}, using default {default}")
            return default
    
    def _parse_snipeit(self) -> SnipeITConfig:
        """Parse Snipe-IT configuration section."""
        data = self._raw.get("snipeit", {})
        return SnipeITConfig(
            base_url=data.get("base_url", "").rstrip("/"),
            api_token=data.get("api_token", ""),
            status_deployed_id=self._safe_int(data.get("status_deployed_id"), 1, "snipeit.status_deployed_id"),
            status_pending_id=self._safe_int(data.get("status_pending_id"), 8, "snipeit.status_pending_id"),
            status_checkedout_id=self._safe_int(data.get("status_checkedout_id"), 2, "snipeit.status_checkedout_id"),
            model_fallback_id=self._safe_int(data.get("model_fallback_id"), 40, "snipeit.model_fallback_id"),
            company_id=self._safe_int(data.get("company_id"), 1, "snipeit.company_id"),
            location_id=self._safe_int(data.get("location_id"), 0, "snipeit.location_id"),
            category_id=self._safe_int(data.get("category_id"), 0, "snipeit.category_id"),
        )
    
    def _parse_azure(self) -> AzureConfig:
        """Parse Azure AD configuration section."""
        data = self._raw.get("azure", {})
        return AzureConfig(
            tenant_id=data.get("tenant_id", ""),
            client_id=data.get("client_id", ""),
            client_secret=data.get("client_secret", ""),
            scope=data.get("scope", "https://graph.microsoft.com/.default"),
            leavers_group_id=data.get("leavers_group_id", ""),
            disabled_group_id=data.get("disabled_group_id", ""),
        )
    
    def _parse_matching(self) -> MatchingConfig:
        """Parse matching configuration section."""
        data = self._raw.get("matching", {})
        skip_usernames = data.get("skip_usernames", [])
        if not isinstance(skip_usernames, list):
            skip_usernames = []
        return MatchingConfig(
            email_domain=data.get("email_domain", ""),
            min_score=self._safe_int(data.get("min_score"), 14, "matching.min_score"),
            weight_lcs=self._safe_float(data.get("weight_lcs"), 1.0, "matching.weight_lcs"),
            weight_char_overlap=self._safe_float(data.get("weight_char_overlap"), 0.3, "matching.weight_char_overlap"),
            weight_bigram_dice=self._safe_float(data.get("weight_bigram_dice"), 2.0, "matching.weight_bigram_dice"),
            use_bigram_dice=bool(data.get("use_bigram_dice", True)),
            allow_reassignment=bool(data.get("allow_reassignment", True)),
            skip_usernames=[str(u).lower() for u in skip_usernames],  # Normalize to lowercase
        )
    
    def _parse_api(self) -> APIConfig:
        """Parse API configuration section."""
        data = self._raw.get("api", {})
        return APIConfig(
            timeout_seconds=self._safe_int(data.get("timeout_seconds"), 30, "api.timeout_seconds"),
            max_retries=self._safe_int(data.get("max_retries"), 3, "api.max_retries"),
            retry_delay_seconds=self._safe_int(data.get("retry_delay_seconds"), 2, "api.retry_delay_seconds"),
            rate_limit_wait_seconds=self._safe_int(data.get("rate_limit_wait_seconds"), 60, "api.rate_limit_wait_seconds"),
        )
    
    def _parse_logging(self) -> LoggingConfig:
        """Parse logging configuration section."""
        data = self._raw.get("logging", {})
        return LoggingConfig(
            level=data.get("level", "INFO"),
            dir=data.get("dir", "./logs"),
            audit_csv=bool(data.get("audit_csv", True)),
            max_log_files=self._safe_int(data.get("max_log_files"), 30, "logging.max_log_files"),
        )
    
    def get_module_settings(self, module_name: str) -> ModuleSettings:
        """Get settings for a specific module."""
        data = self.modules.get(module_name, {})
        return ModuleSettings(
            enabled=bool(data.get("enabled", True)),
            dry_run=bool(data.get("dry_run", False)),
            extra=data,
        )
    
    def is_module_enabled(self, module_name: str) -> bool:
        """Check if a module is enabled."""
        return self.get_module_settings(module_name).enabled
    
    @property
    def config_path(self) -> Path:
        """Get the path to the loaded config file."""
        return self._config_path


# Global config instance (lazy-loaded)
_config: Optional[Config] = None


def get_config(config_path: Optional[str] = None) -> Config:
    """
    Get the global configuration instance.
    
    Args:
        config_path: Optional path to config file. Only used on first call.
    
    Returns:
        Config instance
    """
    global _config
    if _config is None:
        _config = Config(config_path)
    return _config


def reload_config() -> Config:
    """Reload configuration from file."""
    global _config
    if _config is not None:
        _config.reload()
    return get_config()
