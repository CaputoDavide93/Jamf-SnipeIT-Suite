"""
Jamf-SnipeIT Suite - Peripherals Sync Module
Syncs employee equipment data from HiBob → Snipe-IT accessories.

Pipeline:
1. Extract IT equipment assignments from HiBob (read-only)
2. Map equipment names via equipment_mapping.json
3. Create missing accessories in Snipe-IT
4. Check out accessories to matching Snipe-IT users
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from core.config import Config
from clients.snipeit import SnipeITClient
from clients.hibob import HiBobClient
from infra.audit_csv import AuditCSV

logger = logging.getLogger(__name__)


class PeripheralsSyncModule:
    """Sync HiBob employee peripherals to Snipe-IT accessories."""

    def __init__(self, config: Config, dry_run: bool = False):
        self.config = config
        self.dry_run = dry_run

        hibob_cfg = config.modules.get("peripherals_sync", {})
        self.batch_size = hibob_cfg.get("batch_size", 10)
        self.batch_delay = hibob_cfg.get("batch_delay_seconds", 30)
        self.accessory_category_id = hibob_cfg.get("accessory_category_id", 4)
        self.default_qty = hibob_cfg.get("default_accessory_qty", 100)
        self.equipment_field_id = hibob_cfg.get("equipment_field_id")  # None = auto-discover
        self.employee_limit = hibob_cfg.get("employee_limit")  # None = all

        # Mapping file location (inside config/)
        mapping_path = hibob_cfg.get(
            "equipment_mapping_file", "config/equipment_mapping.json"
        )
        self.mapping_file = Path(mapping_path)

        # HiBob credentials from config
        hibob_creds = config._raw.get("hibob", {})
        self.hibob = HiBobClient(
            service_user_id=hibob_creds.get("service_user_id", ""),
            service_user_token=hibob_creds.get("service_user_token", ""),
            timeout=config.api.timeout_seconds,
            max_retries=config.api.max_retries,
            retry_delay=config.api.retry_delay_seconds,
        )

        # Snipe-IT client (reuse existing one)
        self.snipe = SnipeITClient(
            base_url=config.snipeit.base_url,
            api_token=config.snipeit.api_token,
            timeout=config.api.timeout_seconds,
            max_retries=config.api.max_retries,
            retry_delay=config.api.retry_delay_seconds,
        )

        self.audit = AuditCSV(
            log_dir=config.logging.dir,
            module_name="peripherals_sync",
            headers=[
                "timestamp", "action", "accessory_name",
                "accessory_id", "user_name", "user_id", "status", "notes",
            ],
            enabled=config.logging.audit_csv,
        )

    # ------------------------------------------------------------------
    # Equipment name mapping
    # ------------------------------------------------------------------

    def _load_mapping(self) -> Dict[str, str]:
        """Load equipment name mapping from JSON."""
        try:
            with open(self.mapping_file, "r") as f:
                data = json.load(f)
                mapping = data.get("mappings", {})
                logger.debug(f"Loaded {len(mapping)} equipment name mappings")
                return mapping
        except FileNotFoundError:
            logger.warning(f"Mapping file '{self.mapping_file}' not found — using HiBob names as-is")
            return {}
        except json.JSONDecodeError as exc:
            logger.warning(f"Bad mapping file: {exc} — using HiBob names as-is")
            return {}

    def _standardize_name(self, raw_name: str, mapping: Dict[str, str]) -> str:
        """Map a HiBob/supplier equipment name to the Snipe-IT accessory name."""
        clean = raw_name.strip()
        if not clean:
            return clean

        # Exact match
        if clean in mapping:
            return mapping[clean]

        # Case-insensitive match
        lower = clean.lower()
        for key, val in mapping.items():
            if key.lower() == lower:
                return val

        return clean  # unmapped — use as-is

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    def run(self, dry_run: Optional[bool] = None) -> Dict[str, Any]:
        """
        Execute the full peripherals sync.

        Returns a summary dict.
        """
        if dry_run is None:
            dry_run = self.dry_run

        logger.info("=" * 60)
        logger.info(f"Peripherals Sync — {'DRY RUN' if dry_run else 'LIVE'}")
        logger.info("=" * 60)

        # ------ Step 1: HiBob extraction ------
        logger.info("[1/5] Extracting equipment data from HiBob …")
        employees = self.hibob.extract_equipment(
            equipment_field_id=self.equipment_field_id,
            limit=self.employee_limit,
        )
        employees_with_eq = [e for e in employees if e.get("extra_equipment")]
        logger.info(f"  HiBob employees: {len(employees)}, with equipment: {len(employees_with_eq)}")

        if not employees_with_eq:
            logger.info("No employees with equipment — nothing to sync")
            return self._summary(0, 0, 0, 0, 0, 0)

        # ------ Step 2: Name mapping ------
        logger.info("[2/5] Mapping equipment names …")
        mapping = self._load_mapping()
        equipment_types: Dict[str, str] = {}  # raw -> snipeit
        for emp in employees_with_eq:
            for item in (i.strip() for i in emp["extra_equipment"].split(",")):
                if item and item not in equipment_types:
                    equipment_types[item] = self._standardize_name(item, mapping)

        logger.info(f"  Unique equipment types: {len(equipment_types)}")

        # ------ Step 3: Ensure accessories exist ------
        logger.info("[3/5] Syncing accessories in Snipe-IT …")
        existing = self.snipe.get_all_accessories()
        acc_name_to_id: Dict[str, int] = {
            name: acc["id"] for name, acc in existing.items()
        }

        needed_names = set(equipment_types.values())
        created_count = 0
        for sname in sorted(needed_names):
            if sname.lower() in acc_name_to_id:
                continue
            logger.debug(f"  Creating accessory: {sname}")
            if not dry_run:
                acc = self.snipe.create_accessory(
                    sname, self.accessory_category_id, self.default_qty
                )
                if acc:
                    acc_name_to_id[sname.lower()] = acc["id"]
                    created_count += 1
            else:
                created_count += 1

        if created_count:
            logger.info(f"  Accessories created: {created_count}")
        else:
            logger.info("  All accessories already exist")

        # Refresh after creates
        if not dry_run and created_count:
            existing = self.snipe.get_all_accessories()
            acc_name_to_id = {n: a["id"] for n, a in existing.items()}

        # ------ Step 4: Fetch Snipe-IT users + existing checkouts ------
        logger.info("[4/5] Fetching Snipe-IT users and existing checkouts …")
        snipe_users = self.snipe.get_all_users()
        users_by_email: Dict[str, Dict] = {}
        users_by_name: Dict[str, Dict] = {}

        for u in snipe_users:
            email = (u.get("email") or "").lower()
            if email:
                users_by_email[email] = u
            full_name = (u.get("name") or "").strip().lower()
            if not full_name:
                fn = u.get("first_name") or ""
                ln = u.get("last_name") or ""
                full_name = f"{fn} {ln}".strip().lower()
            if full_name:
                # Prefer user with assets (resolve duplicates)
                prev = users_by_name.get(full_name)
                if prev:
                    if (u.get("assets_count", 0) or 0) > (prev.get("assets_count", 0) or 0):
                        users_by_name[full_name] = u
                else:
                    users_by_name[full_name] = u

        logger.debug(f"  Snipe-IT users: {len(snipe_users)} (email index: {len(users_by_email)}, name index: {len(users_by_name)})")

        # Build existing-checkouts map: user_id → set(accessory_ids)
        user_checkouts: Dict[int, Set[int]] = {}
        for acc_name, acc_data in existing.items():
            acc_id = acc_data.get("id")
            if not acc_id:
                continue
            checkouts = self.snipe.get_accessory_checkouts(acc_id)
            for co in checkouts:
                uid = (co.get("assigned_to") or {}).get("id")
                if uid:
                    user_checkouts.setdefault(uid, set()).add(acc_id)
            time.sleep(0.1)

        logger.debug(f"  Users with existing checkouts: {len(user_checkouts)}")

        # ------ Step 5: Process checkouts ------
        logger.info("[5/5] Processing accessory checkouts …")
        planned: List[Dict] = []
        already_out = 0
        not_found = 0

        for emp in employees_with_eq:
            email = (emp.get("email") or "").lower()
            hibob_name = (emp.get("full_name") or "").strip()

            snipe_user = users_by_email.get(email)
            match_method = "email" if snipe_user else None

            if not snipe_user and hibob_name:
                snipe_user = users_by_name.get(hibob_name.lower())
                if snipe_user:
                    match_method = "name"

            if not snipe_user:
                not_found += 1
                logger.debug(f"  User not in Snipe-IT: {hibob_name} ({email})")
                continue

            user_id = snipe_user["id"]
            user_name = snipe_user.get("name") or hibob_name
            user_current = user_checkouts.get(user_id, set())

            for item in (i.strip() for i in emp["extra_equipment"].split(",")):
                sname = equipment_types.get(item, item)
                acc_id = acc_name_to_id.get(sname.lower())
                if not acc_id:
                    logger.warning(f"  Accessory '{sname}' not found for {user_name}")
                    continue

                if acc_id in user_current:
                    already_out += 1
                    continue

                planned.append({
                    "user_name": user_name,
                    "user_id": user_id,
                    "accessory_name": sname,
                    "accessory_id": acc_id,
                    "hibob_item": item,
                    "match_method": match_method,
                })

        logger.info(f"  Checkouts planned: {len(planned)}, already checked out: {already_out}, users not found: {not_found}")

        # Execute checkouts in batches
        success_count = 0
        fail_count = 0

        for i, co in enumerate(planned):
            if dry_run:
                logger.info(
                    f"  [DRY] {co['accessory_name']} → {co['user_name']}"
                )
                success_count += 1
                continue

            ok = self.snipe.checkout_accessory(
                co["accessory_id"],
                co["user_id"],
                note=f"Synced from HiBob — {co['hibob_item']}",
            )
            if ok:
                success_count += 1
                logger.debug(f"  ✅ {co['accessory_name']} → {co['user_name']}")
                self.audit.write(
                    action="accessory_checkout",
                    accessory_name=co["accessory_name"],
                    accessory_id=str(co["accessory_id"]),
                    user_name=co["user_name"],
                    user_id=str(co["user_id"]),
                    status="ok",
                    notes=f"Matched via {co['match_method']}",
                )
            else:
                fail_count += 1
                logger.warning(f"  ❌ {co['accessory_name']} → {co['user_name']}")

            # Batch pause
            if (i + 1) % self.batch_size == 0 and (i + 1) < len(planned):
                logger.debug(f"  Batch pause ({self.batch_delay}s) …")
                time.sleep(self.batch_delay)

        summary = self._summary(
            len(employees_with_eq),
            created_count,
            success_count,
            fail_count,
            already_out,
            not_found,
        )
        self._print_summary(summary, dry_run)
        return summary

    # ------------------------------------------------------------------
    # Summary helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _summary(
        employees: int,
        accessories_created: int,
        checkouts_ok: int,
        checkouts_fail: int,
        already_out: int,
        users_not_found: int,
    ) -> Dict[str, Any]:
        return {
            "employees_with_equipment": employees,
            "accessories_created": accessories_created,
            "checkouts_successful": checkouts_ok,
            "checkouts_failed": checkouts_fail,
            "already_checked_out": already_out,
            "users_not_found": users_not_found,
        }

    @staticmethod
    def _print_summary(s: Dict, dry_run: bool) -> None:
        logger.info("")
        logger.info("=" * 60)
        logger.info(f"  Peripherals Sync — Summary{' (DRY RUN)' if dry_run else ''}")
        logger.info("=" * 60)
        logger.info(f"  Employees with equipment:  {s['employees_with_equipment']}")
        logger.info(f"  Accessories created:       {s['accessories_created']}")
        logger.info(f"  Checkouts successful:      {s['checkouts_successful']}")
        logger.info(f"  Checkouts failed:          {s['checkouts_failed']}")
        logger.info(f"  Already checked out:       {s['already_checked_out']}")
        logger.info(f"  Users not in Snipe-IT:     {s['users_not_found']}")
        logger.info("=" * 60)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close API sessions."""
        self.audit.close()
        self.hibob.close()
        self.snipe.close()


# ------------------------------------------------------------------
# Convenience runner (matches other modules' pattern)
# ------------------------------------------------------------------

def run_peripherals_sync(config: Config, dry_run: bool = False) -> Dict[str, Any]:
    """Run Peripherals Sync as a standalone call."""
    module = PeripheralsSyncModule(config, dry_run=dry_run)
    try:
        return module.run(dry_run=dry_run)
    finally:
        module.close()
