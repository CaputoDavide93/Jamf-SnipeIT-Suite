"""
Jamf-SnipeIT Suite - Model Sync Module
Syncs model metadata from Jamf to Snipe-IT.
Auto-creates models and manufacturers as needed.
"""
import logging
import time
from typing import Any, Dict, List, Optional, Set

from core.config import Config
from clients.jamf import JamfClient
from clients.snipeit import SnipeITClient
from infra.progress import ProgressTracker
from infra.helpers import rate_limit_delay

logger = logging.getLogger(__name__)


class ModelSyncModule:
    """
    Module to sync model metadata from Jamf Pro to Snipe-IT.
    - Discovers unique models in Jamf
    - Auto-creates models and manufacturers in Snipe-IT
    - Updates assets with correct model_id and model_number
    """
    
    def __init__(self, config: Config):
        """
        Initialize the Model Sync module.
        
        Args:
            config: Suite configuration
        """
        self.config = config
        self.settings = config.modules.get("model_sync", {})
        self.update_delay = self.settings.get("update_delay_seconds", 0.2)
        self.auto_create_models = self.settings.get("auto_create_models", True)
        self.auto_create_manufacturers = self.settings.get("auto_create_manufacturers", True)
        self.default_category_id = self.settings.get("default_category_id", 1)
        
        # Initialize clients
        self.jamf = JamfClient(
            base_url=config.jamf.base_url,
            username=config.jamf.username,
            password=config.jamf.password,
            client_id=config.jamf.client_id,
            client_secret=config.jamf.client_secret,
            timeout=config.api.timeout_seconds,
            max_retries=config.api.max_retries,
            retry_delay=config.api.retry_delay_seconds,
        )
        
        self.snipe = SnipeITClient(
            base_url=config.snipeit.base_url,
            api_token=config.snipeit.api_token,
            timeout=config.api.timeout_seconds,
            max_retries=config.api.max_retries,
            retry_delay=config.api.retry_delay_seconds,
            rate_limit_wait=config.api.rate_limit_wait_seconds,
        )
        
        # Caches
        self._model_map: Optional[Dict[str, int]] = None
        self._manufacturer_map: Optional[Dict[str, int]] = None
        # serial (upper) -> Jamf hardware subset, populated by check_models so
        # run() does not fetch every computer's detail a second time.
        self._hardware_by_serial: Dict[str, Dict[str, Any]] = {}
    
    def _get_model_map(self) -> Dict[str, int]:
        """Get model name -> ID mapping from Snipe-IT."""
        if self._model_map is None:
            self._model_map = self.snipe.get_model_name_to_id_map()
        return self._model_map
    
    def _get_manufacturer_map(self) -> Dict[str, int]:
        """Get manufacturer name -> ID mapping from Snipe-IT."""
        if self._manufacturer_map is None:
            self._manufacturer_map = self.snipe.get_all_manufacturers()
        return self._manufacturer_map
    
    def _detect_manufacturer(self, model_name: str) -> str:
        """
        Detect manufacturer from model name using comprehensive pattern matching.
        
        Args:
            model_name: The model name string (e.g., "MacBook Pro 16-inch")
        
        Returns:
            Detected manufacturer name
        """
        model_lower = model_name.lower()
        
        # Apple patterns - Mac products, iOS devices, Apple accessories
        apple_patterns = [
            "macbook", "mac mini", "mac pro", "mac studio", "imac", "apple",
            "iphone", "ipad", "ipod", "airpod", "apple watch", "homepod",
            "magic keyboard", "magic mouse", "magic trackpad", "powerbook",
            "macintosh", "powerpc"
        ]
        for pattern in apple_patterns:
            if pattern in model_lower:
                return "Apple"
        
        # Dell patterns
        dell_patterns = ["dell", "latitude", "optiplex", "precision", "xps", "inspiron", "vostro", "alienware"]
        for pattern in dell_patterns:
            if pattern in model_lower:
                return "Dell"
        
        # Lenovo patterns
        lenovo_patterns = ["lenovo", "thinkpad", "thinkcentre", "thinkstation", "ideapad", "ideacentre", "yoga"]
        for pattern in lenovo_patterns:
            if pattern in model_lower:
                return "Lenovo"
        
        # HP patterns
        hp_patterns = ["hewlett", "hp ", "hp-", "elitebook", "probook", "elitedesk", "prodesk", "zbook", "pavilion", "envy", "omen", "spectre"]
        for pattern in hp_patterns:
            if pattern in model_lower:
                return "HP"
        
        # Microsoft patterns
        microsoft_patterns = ["surface", "microsoft"]
        for pattern in microsoft_patterns:
            if pattern in model_lower:
                return "Microsoft"
        
        # Asus patterns
        asus_patterns = ["asus", "zenbook", "vivobook", "rog ", "tuf "]
        for pattern in asus_patterns:
            if pattern in model_lower:
                return "ASUS"
        
        # Acer patterns
        acer_patterns = ["acer", "aspire", "predator", "nitro"]
        for pattern in acer_patterns:
            if pattern in model_lower:
                return "Acer"
        
        # Samsung patterns
        samsung_patterns = ["samsung", "galaxy"]
        for pattern in samsung_patterns:
            if pattern in model_lower:
                return "Samsung"
        
        # Default to Apple for unrecognized models (since this is primarily a Jamf tool for Macs)
        logger.debug(f"Could not detect manufacturer for '{model_name}', defaulting to Apple")
        return "Apple"
    
    def check_models(self) -> Dict[str, Any]:
        """
        List all unique models in Jamf Pro and compare against Snipe-IT.

        Returns:
            Dict with total_jamf_models, missing_models, existing_models
        """
        logger.debug("Discovering unique models in Jamf Pro...")

        computers = self.jamf.get_all_computers_basic()

        if not computers:
            logger.warning("No computers returned from Jamf")
            return {"total_jamf_models": 0, "missing_models": [], "existing_models": []}

        models: Set[str] = set()

        for comp in computers:
            comp_id = comp.get("id")
            if not comp_id:
                continue

            try:
                detail = self.jamf.get_computer_by_id(comp_id, subsets=["Hardware"])
                if detail:
                    computer = detail.get("computer", {}) or {}
                    hardware = computer.get("hardware", {}) or {}
                    # Cache by serial — run() reuses this instead of issuing a
                    # second get_computer_by_serial per machine.
                    serial = (comp.get("serial_number") or "").strip().upper()
                    if serial:
                        self._hardware_by_serial[serial] = hardware
                    model_name = hardware.get("model", "")
                    if model_name:
                        models.add(model_name)
            except Exception as e:
                logger.debug(f"Could not get model for computer {comp_id}: {e}")

        logger.debug(f"Found {len(models)} unique models")

        # Compare against Snipe-IT
        snipe_models = self.snipe.get_model_name_to_id_map()
        missing = [m for m in sorted(models) if m.lower() not in snipe_models]
        existing = [m for m in sorted(models) if m.lower() in snipe_models]

        return {
            "total_jamf_models": len(models),
            "missing_models": missing,
            "existing_models": existing,
        }
    
    def provision_models(self, dry_run: bool = False) -> Dict[str, Any]:
        """
        Auto-provision missing models and manufacturers in Snipe-IT.
        
        Args:
            dry_run: If True, don't create anything
        
        Returns:
            Results dictionary
        """
        logger.info(f"Starting model provisioning: dry_run={dry_run}")
        
        results = {
            "models_checked": 0,
            "models_created": 0,
            "manufacturers_created": 0,
            "models_existing": 0,
            "errors": [],
        }
        
        # Get unique models from Jamf
        jamf_model_report = self.check_models()
        missing_models = jamf_model_report.get("missing_models", [])
        results["models_checked"] = jamf_model_report.get("total_jamf_models", 0)
        results["models_existing"] = len(
            jamf_model_report.get("existing_models", [])
        )
        
        if not results["models_checked"]:
            return results
        
        # Load current Snipe-IT data
        model_map = self._get_model_map()
        manufacturer_map = self._get_manufacturer_map()
        
        for model_index, model_name in enumerate(sorted(missing_models), 1):
            # Need to create model
            logger.debug(f"Model missing in Snipe-IT: {model_name}")
            
            if not self.auto_create_models:
                logger.debug("Auto-create disabled, skipping")
                continue
            
            # Determine manufacturer from model name with improved detection
            manufacturer_name = self._detect_manufacturer(model_name)
            
            # Ensure manufacturer exists
            mfr_id = manufacturer_map.get(manufacturer_name.lower())
            
            if not mfr_id and self.auto_create_manufacturers:
                if dry_run:
                    logger.info(f"[DRY-RUN] Would create manufacturer: {manufacturer_name}")
                    mfr_id = -model_index
                    manufacturer_map[manufacturer_name.lower()] = mfr_id
                    results["manufacturers_created"] += 1
                else:
                    mfr_id = self.snipe.create_manufacturer(manufacturer_name)
                    if mfr_id:
                        manufacturer_map[manufacturer_name.lower()] = mfr_id
                        results["manufacturers_created"] += 1
            
            if not mfr_id:
                logger.warning(f"No manufacturer ID for {manufacturer_name}, skipping model")
                results["errors"].append(f"No manufacturer: {manufacturer_name}")
                continue
            
            # Create model
            if dry_run:
                logger.info(f"[DRY-RUN] Would create model: {model_name}")
                model_map[model_name.lower()] = -(1000 + model_index)
                results["models_created"] += 1
            else:
                model_id = self.snipe.create_model(
                    name=model_name,
                    manufacturer_id=mfr_id,
                    category_id=self.default_category_id,
                )
                if model_id:
                    model_map[model_name.lower()] = model_id
                    results["models_created"] += 1
                else:
                    results["errors"].append(f"Failed to create model: {model_name}")
        
        logger.info(
            f"Model provisioning: {results['models_checked']} checked, "
            f"{results['models_existing']} exist, "
            f"{results['models_created']} created, "
            f"{results['manufacturers_created']} manufacturers"
        )
        
        return results
    
    def run(self, dry_run: bool = False) -> Dict[str, Any]:
        """
        Run the full model metadata sync.
        
        Args:
            dry_run: If True, don't make changes
        
        Returns:
            Results dictionary
        """
        logger.info(f"Starting Model Sync: dry_run={dry_run}")
        
        results = {
            "total_processed": 0,
            "models_checked": 0,
            "models_created": 0,
            "manufacturers_created": 0,
            "updated": 0,
            "skipped": 0,
            "errors": 0,
        }
        
        # First, provision any missing models
        provision_results = self.provision_models(dry_run=dry_run)
        results["models_checked"] = provision_results["models_checked"]
        results["models_created"] = provision_results["models_created"]
        results["manufacturers_created"] = provision_results["manufacturers_created"]
        results["errors"] += len(provision_results["errors"])
        
        # Refresh model map after provisioning
        if not dry_run:
            self._model_map = None
        model_map = self._get_model_map()
        
        # Get all Jamf computers
        computers = self.jamf.get_all_computers_basic()
        
        if not computers:
            logger.warning("No computers from Jamf")
            return results
        
        logger.info(f"Processing {len(computers)} computers for metadata sync")

        # Pre-fetch all Snipe-IT assets in one bulk call (eliminates N+1)
        logger.info("Pre-fetching Snipe-IT asset serial map...")
        snipe_serial_map = self.snipe.get_assets_by_serial_map()
        logger.info(f"Loaded {len(snipe_serial_map)} Snipe-IT assets")

        progress = ProgressTracker("Model Sync", total=len(computers), log_every=50)

        for i, comp in enumerate(computers, 1):
            serial = comp.get("serial_number", "").strip()

            if not serial:
                progress.advance()
                continue
            
            logger.debug(f"[{i}/{len(computers)}] Processing: {serial}")
            
            updated = False
            try:
                updated = self._sync_single_asset(serial, model_map, dry_run, snipe_serial_map)
                results["total_processed"] += 1

                if updated:
                    results["updated"] += 1
                else:
                    results["skipped"] += 1

            except Exception as e:
                logger.error(f"Error processing {serial}: {e}")
                results["errors"] += 1

            progress.advance()

            # Rate limiting — only after an actual write. Machines whose model
            # already matches issue no API call (the hardware subset is served
            # from the check_models cache), so throttling them was pure sleep:
            # a steady-state run spent ~2 minutes waiting on zero requests.
            if updated and not dry_run and self.update_delay > 0:
                rate_limit_delay(self.update_delay, "Model Sync", i, len(computers))
        
        progress.finish(extra=f"updated={results['updated']}, skipped={results['skipped']}, errors={results['errors']}")
        
        # Print summary
        self._print_summary(results, dry_run)
        
        return results
    
    def _sync_single_asset(
        self,
        serial: str,
        model_map: Dict[str, int],
        dry_run: bool,
        snipe_serial_map: Optional[Dict[str, Dict]] = None,
    ) -> bool:
        """
        Sync metadata for a single asset.

        Returns:
            True if updated
        """
        # Prefer the hardware subset already fetched by check_models
        hardware = self._hardware_by_serial.get(serial.upper())
        if hardware is None:
            jamf_comp = self.jamf.get_computer_by_serial(serial)
            if not jamf_comp:
                logger.debug(f"No Jamf computer for serial: {serial}")
                return False
            hardware = jamf_comp.get("hardware", {}) or {}

        model_name = hardware.get("model", "")
        model_identifier = hardware.get("model_identifier", "")

        if not model_name:
            return False

        # Use pre-fetched map if available, otherwise fall back to individual lookup
        snipe_asset = None
        if snipe_serial_map is not None:
            snipe_asset = snipe_serial_map.get(serial.upper())
        else:
            snipe_asset = self.snipe.get_asset_by_serial(serial)
        if not snipe_asset:
            logger.debug(f"No Snipe-IT asset for serial: {serial}")
            return False
        
        asset_id = snipe_asset.get("id")
        
        # Determine target model_id
        target_model_id = model_map.get(model_name.lower())
        
        if not target_model_id:
            logger.warning(f"No Snipe-IT model mapping for: {model_name}")
            return False
        
        # Check if update needed
        current_model = snipe_asset.get("model")
        current_model_id = current_model.get("id") if isinstance(current_model, dict) else None
        
        if current_model_id == target_model_id:
            logger.debug(f"No changes needed for: {serial}")
            return False
        
        update_data = {"model_id": target_model_id}
        
        # Update asset
        if dry_run:
            logger.info(f"[DRY-RUN] Would update {serial}: {update_data}")
            return True
        
        if self.snipe.update_asset(asset_id, update_data):
            logger.debug(f"Updated {serial}: {update_data}")
            return True
        else:
            logger.error(f"Failed to update {serial}")
            return False
    
    def _print_summary(self, results: Dict[str, Any], dry_run: bool) -> None:
        """Print processing summary."""
        
        mode = "DRY RUN" if dry_run else "LIVE RUN"
        
        logger.info(
            f"Model Sync ({mode}): {results['total_processed']} processed, "
            f"{results['models_created']} models created, "
            f"{results['manufacturers_created']} manufacturers created, "
            f"{results['updated']} updated, "
            f"{results['skipped']} skipped, "
            f"{results['errors']} errors"
        )
    
    def close(self) -> None:
        """Clean up resources."""
        self.jamf.close()
        self.snipe.close()


def run_model_sync(config: Config, dry_run: bool = False) -> Dict[str, Any]:
    """
    Convenience function to run the model sync.
    
    Args:
        config: Suite configuration
        dry_run: If True, don't make changes
    
    Returns:
        Results dictionary
    """
    module = ModelSyncModule(config)
    try:
        return module.run(dry_run=dry_run)
    finally:
        module.close()


def check_models(config: Config) -> Set[str]:
    """
    Convenience function to list unique models.
    
    Args:
        config: Suite configuration
    
    Returns:
        Set of unique model names
    """
    module = ModelSyncModule(config)
    try:
        return module.check_models()
    finally:
        module.close()
