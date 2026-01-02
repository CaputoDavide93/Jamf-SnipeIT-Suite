"""
Jamf-SnipeIT Suite - Model Sync Module
Syncs model metadata from Jamf to Snipe-IT.
Auto-creates models and manufacturers as needed.
"""
import logging
import time
from typing import Any, Dict, List, Optional, Set

from core import Config, JamfClient, SnipeITClient

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
    
    def check_models(self) -> Set[str]:
        """
        List all unique models in Jamf Pro.
        
        Returns:
            Set of unique model names
        """
        logger.info("Discovering unique models in Jamf Pro...")
        
        computers = self.jamf.get_all_computers_basic()
        
        if not computers:
            logger.warning("No computers returned from Jamf")
            return set()
        
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
                    model_name = hardware.get("model", "")
                    if model_name:
                        models.add(model_name)
            except Exception as e:
                logger.debug(f"Could not get model for computer {comp_id}: {e}")
        
        logger.info(f"Found {len(models)} unique models")
        return models
    
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
        jamf_models = self.check_models()
        results["models_checked"] = len(jamf_models)
        
        if not jamf_models:
            return results
        
        # Load current Snipe-IT data
        model_map = self._get_model_map()
        manufacturer_map = self._get_manufacturer_map()
        
        for model_name in sorted(jamf_models):
            # Check if model exists
            if model_name.lower() in model_map:
                results["models_existing"] += 1
                logger.debug(f"Model exists: {model_name}")
                continue
            
            # Need to create model
            logger.info(f"Model missing in Snipe-IT: {model_name}")
            
            if not self.auto_create_models:
                logger.info("Auto-create disabled, skipping")
                continue
            
            # Determine manufacturer from model name with improved detection
            manufacturer_name = self._detect_manufacturer(model_name)
            
            # Ensure manufacturer exists
            mfr_id = manufacturer_map.get(manufacturer_name.lower())
            
            if not mfr_id and self.auto_create_manufacturers:
                if dry_run:
                    logger.info(f"[DRY-RUN] Would create manufacturer: {manufacturer_name}")
                    mfr_id = 999  # Placeholder
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
        
        # Print summary
        print("\n" + "=" * 60)
        print("MODEL PROVISIONING COMPLETE")
        print("=" * 60)
        print(f"Models checked:        {results['models_checked']}")
        print(f"Models already exist:  {results['models_existing']}")
        print(f"Models created:        {results['models_created']}")
        print(f"Manufacturers created: {results['manufacturers_created']}")
        print("=" * 60 + "\n")
        
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
            "updated": 0,
            "skipped": 0,
            "errors": 0,
        }
        
        # First, provision any missing models
        provision_results = self.provision_models(dry_run=dry_run)
        
        # Refresh model map after provisioning
        self._model_map = None
        model_map = self._get_model_map()
        
        # Get all Jamf computers
        computers = self.jamf.get_all_computers_basic()
        
        if not computers:
            logger.warning("No computers from Jamf")
            return results
        
        logger.info(f"Processing {len(computers)} computers for metadata sync")
        
        for i, comp in enumerate(computers, 1):
            serial = comp.get("serial_number", "").strip()
            
            if not serial:
                continue
            
            logger.debug(f"[{i}/{len(computers)}] Processing: {serial}")
            
            try:
                updated = self._sync_single_asset(serial, model_map, dry_run)
                results["total_processed"] += 1
                
                if updated:
                    results["updated"] += 1
                else:
                    results["skipped"] += 1
                    
            except Exception as e:
                logger.error(f"Error processing {serial}: {e}")
                results["errors"] += 1
            
            # Rate limiting with visual feedback for longer delays
            if self.update_delay > 0:
                if self.update_delay >= 2:
                    try:
                        from utils import wait_with_countdown
                    except ImportError:
                        from src.utils import wait_with_countdown
                    wait_with_countdown(self.update_delay, f"Processed {i}/{len(computers)}")
                else:
                    time.sleep(self.update_delay)
        
        # Print summary
        self._print_summary(results, dry_run)
        
        return results
    
    def _sync_single_asset(
        self,
        serial: str,
        model_map: Dict[str, int],
        dry_run: bool,
    ) -> bool:
        """
        Sync metadata for a single asset.
        
        Returns:
            True if updated
        """
        # Get Jamf computer details
        jamf_comp = self.jamf.get_computer_by_serial(serial)
        if not jamf_comp:
            logger.debug(f"No Jamf computer for serial: {serial}")
            return False
        
        hardware = jamf_comp.get("hardware", {}) or {}
        model_name = hardware.get("model", "")
        model_identifier = hardware.get("model_identifier", "")
        
        if not model_name:
            return False
        
        # Get Snipe-IT asset
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
        current_model_number = snipe_asset.get("model_number", "")
        
        needs_update = False
        update_data = {}
        
        if current_model_id != target_model_id:
            update_data["model_id"] = target_model_id
            needs_update = True
        
        if model_identifier and current_model_number != model_identifier:
            update_data["model_number"] = model_identifier
            needs_update = True
        
        if not needs_update:
            logger.debug(f"No changes needed for: {serial}")
            return False
        
        # Update asset
        if dry_run:
            logger.info(f"[DRY-RUN] Would update {serial}: {update_data}")
            return True
        
        if self.snipe.update_asset(asset_id, update_data):
            logger.info(f"Updated {serial}: {update_data}")
            return True
        else:
            logger.error(f"Failed to update {serial}")
            return False
    
    def _print_summary(self, results: Dict[str, Any], dry_run: bool) -> None:
        """Print processing summary."""
        
        mode = "DRY RUN" if dry_run else "LIVE RUN"
        
        print("\n" + "=" * 60)
        print(f"MODEL SYNC - {mode} COMPLETE")
        print("=" * 60)
        print(f"Total processed:  {results['total_processed']}")
        print(f"Updated:          {results['updated']}")
        print(f"Skipped:          {results['skipped']}")
        print(f"Errors:           {results['errors']}")
        print("=" * 60 + "\n")
    
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
