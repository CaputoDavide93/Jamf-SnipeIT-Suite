"""
Inventory Reconciliation Module
Finds discrepancies between Jamf Pro and Snipe-IT inventories.
- Devices in Jamf but NOT in Snipe-IT
- Assets in Snipe-IT but NOT in Jamf
- Duplicate detection in both systems
"""
import csv
import os
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field
from collections import defaultdict

from core.config import Config
from core.jamf_client import JamfClient
from core.snipe_client import SnipeITClient


@dataclass
class ReconciliationResults:
    """Results from inventory reconciliation."""
    # Devices only in Jamf (not in Snipe-IT)
    jamf_only: List[Dict] = field(default_factory=list)
    
    # Assets only in Snipe-IT (not in Jamf)
    snipe_only: List[Dict] = field(default_factory=list)
    
    # Matched devices (in both systems)
    matched: List[Dict] = field(default_factory=list)
    
    # Duplicates in Jamf (same serial)
    jamf_duplicates: List[Dict] = field(default_factory=list)
    
    # Duplicates in Snipe-IT (same serial)
    snipe_duplicates: List[Dict] = field(default_factory=list)
    
    # Data mismatches (same device, different data)
    data_mismatches: List[Dict] = field(default_factory=list)
    
    # Statistics
    total_jamf: int = 0
    total_snipe: int = 0
    scan_timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class ReconciliationModule:
    """
    Reconciles inventory between Jamf Pro and Snipe-IT.
    Identifies missing devices, duplicates, and data mismatches.
    """
    
    def __init__(self, config: Config, dry_run: bool = False):
        self.config = config
        self.dry_run = dry_run
        self.jamf = JamfClient(
            base_url=config.jamf.base_url,
            username=config.jamf.username,
            password=config.jamf.password,
            client_id=config.jamf.client_id,
            client_secret=config.jamf.client_secret
        )
        self.snipe = SnipeITClient(
            base_url=config.snipeit.base_url,
            api_token=config.snipeit.api_token
        )
        self.results = ReconciliationResults()
    
    def run(self, 
            check_duplicates: bool = True,
            check_mismatches: bool = True,
            export_csv: bool = False,
            output_dir: str = "./output") -> ReconciliationResults:
        """
        Run full inventory reconciliation.
        
        Args:
            check_duplicates: Check for duplicate serials in each system
            check_mismatches: Check for data mismatches between systems
            export_csv: Export results to CSV files
            output_dir: Directory for CSV exports
        
        Returns:
            ReconciliationResults with all findings
        """
        print("🔍 Starting Inventory Reconciliation...")
        print("="*60)
        
        # Fetch all devices from both systems
        print("\n📥 Fetching Jamf Pro inventory...")
        jamf_devices = self._fetch_jamf_devices()
        print(f"   Found {len(jamf_devices)} devices in Jamf Pro")
        
        print("\n📥 Fetching Snipe-IT inventory...")
        snipe_assets = self._fetch_snipe_assets()
        print(f"   Found {len(snipe_assets)} assets in Snipe-IT")
        
        self.results.total_jamf = len(jamf_devices)
        self.results.total_snipe = len(snipe_assets)
        
        # Build lookup dictionaries by serial number
        jamf_by_serial = self._build_serial_map(jamf_devices, 'serial_number')
        snipe_by_serial = self._build_serial_map(snipe_assets, 'serial')
        
        # Find devices only in Jamf
        print("\n🔎 Finding devices only in Jamf...")
        self._find_jamf_only(jamf_devices, snipe_by_serial)
        
        # Find assets only in Snipe-IT
        print("🔎 Finding assets only in Snipe-IT...")
        self._find_snipe_only(snipe_assets, jamf_by_serial)
        
        # Find matched devices
        print("🔎 Finding matched devices...")
        self._find_matched(jamf_devices, snipe_by_serial)
        
        # Check for duplicates
        if check_duplicates:
            print("🔎 Checking for duplicates...")
            self._find_duplicates(jamf_devices, snipe_assets)
        
        # Check for data mismatches
        if check_mismatches:
            print("🔎 Checking for data mismatches...")
            self._find_data_mismatches(jamf_by_serial, snipe_by_serial)
        
        # Export to CSV if requested
        if export_csv:
            print(f"\n📄 Exporting results to {output_dir}...")
            self._export_csv(output_dir)
        
        return self.results
    
    def _fetch_jamf_devices(self) -> List[Dict]:
        """Fetch all computers from Jamf Pro."""
        computers = self.jamf.get_all_computers_basic()
        devices = []
        
        for computer in computers:
            comp_id = computer.get('id')
            # Get basic info - we'll fetch details if needed
            devices.append({
                'id': comp_id,
                'name': computer.get('name', ''),
                'serial_number': computer.get('serial_number', computer.get('serialNumber', '')),
                'model': computer.get('model', ''),
                'username': computer.get('username', ''),
                'managed': computer.get('managed', True),
                'source': 'jamf'
            })
        
        return devices
    
    def _fetch_snipe_assets(self) -> List[Dict]:
        """Fetch all hardware assets from Snipe-IT."""
        assets = self.snipe.search_assets(limit=5000)
        devices = []
        
        for asset in assets:
            devices.append({
                'id': asset.get('id'),
                'asset_tag': asset.get('asset_tag', ''),
                'name': asset.get('name', ''),
                'serial': asset.get('serial', ''),
                'model': asset.get('model', {}).get('name', '') if asset.get('model') else '',
                'model_number': asset.get('model_number', ''),
                'status': asset.get('status_label', {}).get('name', '') if asset.get('status_label') else '',
                'assigned_to': asset.get('assigned_to', {}).get('name', '') if asset.get('assigned_to') else '',
                'source': 'snipeit'
            })
        
        return devices
    
    def _build_serial_map(self, devices: List[Dict], serial_key: str) -> Dict[str, List[Dict]]:
        """Build a map of serial numbers to devices (handles duplicates)."""
        serial_map = defaultdict(list)
        for device in devices:
            serial = device.get(serial_key, '').strip().upper()
            if serial:
                serial_map[serial].append(device)
        return dict(serial_map)
    
    def _find_jamf_only(self, jamf_devices: List[Dict], snipe_by_serial: Dict[str, List[Dict]]):
        """Find devices that exist in Jamf but not in Snipe-IT."""
        for device in jamf_devices:
            serial = device.get('serial_number', '').strip().upper()
            if serial and serial not in snipe_by_serial:
                self.results.jamf_only.append(device)
        
        print(f"   Found {len(self.results.jamf_only)} devices only in Jamf")
    
    def _find_snipe_only(self, snipe_assets: List[Dict], jamf_by_serial: Dict[str, List[Dict]]):
        """Find assets that exist in Snipe-IT but not in Jamf."""
        for asset in snipe_assets:
            serial = asset.get('serial', '').strip().upper()
            if serial and serial not in jamf_by_serial:
                self.results.snipe_only.append(asset)
        
        print(f"   Found {len(self.results.snipe_only)} assets only in Snipe-IT")
    
    def _find_matched(self, jamf_devices: List[Dict], snipe_by_serial: Dict[str, List[Dict]]):
        """Find devices that exist in both systems."""
        for device in jamf_devices:
            serial = device.get('serial_number', '').strip().upper()
            if serial and serial in snipe_by_serial:
                snipe_device = snipe_by_serial[serial][0]  # Take first match
                self.results.matched.append({
                    'serial': serial,
                    'jamf_id': device.get('id'),
                    'jamf_name': device.get('name'),
                    'snipe_id': snipe_device.get('id'),
                    'snipe_name': snipe_device.get('name'),
                    'snipe_asset_tag': snipe_device.get('asset_tag')
                })
        
        print(f"   Found {len(self.results.matched)} matched devices")
    
    def _find_duplicates(self, jamf_devices: List[Dict], snipe_assets: List[Dict]):
        """Find duplicate serial numbers within each system."""
        # Jamf duplicates
        jamf_serials = defaultdict(list)
        for device in jamf_devices:
            serial = device.get('serial_number', '').strip().upper()
            if serial:
                jamf_serials[serial].append(device)
        
        for serial, devices in jamf_serials.items():
            if len(devices) > 1:
                self.results.jamf_duplicates.append({
                    'serial': serial,
                    'count': len(devices),
                    'devices': [{'id': d['id'], 'name': d['name']} for d in devices]
                })
        
        # Snipe-IT duplicates
        snipe_serials = defaultdict(list)
        for asset in snipe_assets:
            serial = asset.get('serial', '').strip().upper()
            if serial:
                snipe_serials[serial].append(asset)
        
        for serial, assets in snipe_serials.items():
            if len(assets) > 1:
                self.results.snipe_duplicates.append({
                    'serial': serial,
                    'count': len(assets),
                    'assets': [{'id': a['id'], 'name': a['name'], 'asset_tag': a['asset_tag']} for a in assets]
                })
        
        print(f"   Found {len(self.results.jamf_duplicates)} duplicate serials in Jamf")
        print(f"   Found {len(self.results.snipe_duplicates)} duplicate serials in Snipe-IT")
    
    def _find_data_mismatches(self, jamf_by_serial: Dict[str, List[Dict]], 
                               snipe_by_serial: Dict[str, List[Dict]]):
        """Find devices with mismatched data between systems."""
        for serial in jamf_by_serial:
            if serial in snipe_by_serial:
                jamf_device = jamf_by_serial[serial][0]
                snipe_asset = snipe_by_serial[serial][0]
                
                mismatches = []
                
                # Check name mismatch
                jamf_name = jamf_device.get('name', '').strip().lower()
                snipe_name = snipe_asset.get('name', '').strip().lower()
                if jamf_name and snipe_name and jamf_name != snipe_name:
                    mismatches.append({
                        'field': 'name',
                        'jamf_value': jamf_device.get('name'),
                        'snipe_value': snipe_asset.get('name')
                    })
                
                # Check model mismatch
                jamf_model = jamf_device.get('model', '').strip().lower()
                snipe_model = snipe_asset.get('model', '').strip().lower()
                if jamf_model and snipe_model and jamf_model != snipe_model:
                    # Allow partial matches (Snipe might have shorter model names)
                    if jamf_model not in snipe_model and snipe_model not in jamf_model:
                        mismatches.append({
                            'field': 'model',
                            'jamf_value': jamf_device.get('model'),
                            'snipe_value': snipe_asset.get('model')
                        })
                
                if mismatches:
                    self.results.data_mismatches.append({
                        'serial': serial,
                        'jamf_id': jamf_device.get('id'),
                        'snipe_id': snipe_asset.get('id'),
                        'mismatches': mismatches
                    })
        
        print(f"   Found {len(self.results.data_mismatches)} devices with data mismatches")
    
    def _export_csv(self, output_dir: str):
        """Export reconciliation results to CSV files."""
        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        # Export Jamf-only devices
        if self.results.jamf_only:
            path = os.path.join(output_dir, f'jamf_only_{timestamp}.csv')
            with open(path, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=['id', 'name', 'serial_number', 'model', 'username'])
                writer.writeheader()
                for device in self.results.jamf_only:
                    writer.writerow({k: device.get(k, '') for k in ['id', 'name', 'serial_number', 'model', 'username']})
            print(f"   Exported: {path}")
        
        # Export Snipe-only assets
        if self.results.snipe_only:
            path = os.path.join(output_dir, f'snipe_only_{timestamp}.csv')
            with open(path, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=['id', 'asset_tag', 'name', 'serial', 'model', 'status'])
                writer.writeheader()
                for asset in self.results.snipe_only:
                    writer.writerow({k: asset.get(k, '') for k in ['id', 'asset_tag', 'name', 'serial', 'model', 'status']})
            print(f"   Exported: {path}")
        
        # Export duplicates
        if self.results.jamf_duplicates or self.results.snipe_duplicates:
            path = os.path.join(output_dir, f'duplicates_{timestamp}.csv')
            with open(path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['source', 'serial', 'count', 'device_ids'])
                for dup in self.results.jamf_duplicates:
                    ids = ', '.join(str(d['id']) for d in dup['devices'])
                    writer.writerow(['jamf', dup['serial'], dup['count'], ids])
                for dup in self.results.snipe_duplicates:
                    ids = ', '.join(str(a['id']) for a in dup['assets'])
                    writer.writerow(['snipeit', dup['serial'], dup['count'], ids])
            print(f"   Exported: {path}")
        
        # Export data mismatches
        if self.results.data_mismatches:
            path = os.path.join(output_dir, f'mismatches_{timestamp}.csv')
            with open(path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['serial', 'jamf_id', 'snipe_id', 'field', 'jamf_value', 'snipe_value'])
                for mismatch in self.results.data_mismatches:
                    for m in mismatch['mismatches']:
                        writer.writerow([
                            mismatch['serial'],
                            mismatch['jamf_id'],
                            mismatch['snipe_id'],
                            m['field'],
                            m['jamf_value'],
                            m['snipe_value']
                        ])
            print(f"   Exported: {path}")
    
    def print_summary(self):
        """Print a summary of reconciliation results."""
        print("\n" + "="*60)
        print("📊 RECONCILIATION SUMMARY")
        print("="*60)
        
        print(f"\n📈 Total Inventory:")
        print(f"   Jamf Pro:  {self.results.total_jamf} devices")
        print(f"   Snipe-IT:  {self.results.total_snipe} assets")
        print(f"   Matched:   {len(self.results.matched)} devices")
        
        print(f"\n⚠️  Discrepancies:")
        print(f"   Only in Jamf:    {len(self.results.jamf_only)} devices")
        print(f"   Only in Snipe:   {len(self.results.snipe_only)} assets")
        
        print(f"\n🔁 Duplicates:")
        print(f"   Jamf duplicates:  {len(self.results.jamf_duplicates)} serial(s)")
        print(f"   Snipe duplicates: {len(self.results.snipe_duplicates)} serial(s)")
        
        print(f"\n📝 Data Mismatches:")
        print(f"   Devices with field mismatches: {len(self.results.data_mismatches)}")
        
        # Show some examples if any issues found
        if self.results.jamf_only:
            print(f"\n📋 Sample devices only in Jamf (first 5):")
            for device in self.results.jamf_only[:5]:
                print(f"   - {device['serial_number']}: {device['name']}")
        
        if self.results.snipe_only:
            print(f"\n📋 Sample assets only in Snipe-IT (first 5):")
            for asset in self.results.snipe_only[:5]:
                print(f"   - {asset['serial']}: {asset['name']} ({asset['asset_tag']})")
        
        print("\n" + "="*60)
    
    def get_summary_dict(self) -> Dict:
        """Get summary as a dictionary (useful for API responses)."""
        return {
            'timestamp': self.results.scan_timestamp,
            'total_jamf': self.results.total_jamf,
            'total_snipe': self.results.total_snipe,
            'matched': len(self.results.matched),
            'jamf_only': len(self.results.jamf_only),
            'snipe_only': len(self.results.snipe_only),
            'jamf_duplicates': len(self.results.jamf_duplicates),
            'snipe_duplicates': len(self.results.snipe_duplicates),
            'data_mismatches': len(self.results.data_mismatches),
            'health': 'ok' if (len(self.results.jamf_only) == 0 and 
                              len(self.results.snipe_only) == 0 and
                              len(self.results.jamf_duplicates) == 0 and
                              len(self.results.snipe_duplicates) == 0) else 'issues_found'
        }
    
    def close(self) -> None:
        """Clean up resources."""
        if hasattr(self, 'jamf') and self.jamf:
            self.jamf.close()
        if hasattr(self, 'snipe') and self.snipe:
            self.snipe.close()


def run_reconciliation(config_path: str = "config/config.yaml", 
                       export_csv: bool = True,
                       output_dir: str = "./output") -> ReconciliationResults:
    """
    Convenience function to run reconciliation.
    
    Args:
        config_path: Path to configuration file
        export_csv: Whether to export results to CSV
        output_dir: Directory for CSV exports
    
    Returns:
        ReconciliationResults object
    """
    from core.config import get_config
    
    config = get_config(config_path)
    module = ReconciliationModule(config)
    results = module.run(export_csv=export_csv, output_dir=output_dir)
    module.print_summary()
    
    return results


if __name__ == "__main__":
    run_reconciliation()
