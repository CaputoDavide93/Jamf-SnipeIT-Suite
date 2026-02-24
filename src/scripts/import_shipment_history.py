#!/usr/bin/env python3
"""
One-off Shipment History Import Script

Reads a supplier CSV of shipped peripherals, standardises device names,
matches recipients to Snipe-IT users, creates missing accessories, and
checks them out — all driven by the main suite's config.yaml.

Usage (from the project root):
    python -m scripts.import_shipment_history <csv_file> --dry-run
    python -m scripts.import_shipment_history <csv_file> --execute
"""

import argparse
import csv
import logging
import os
import re
import sys
import time
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Set, Tuple

# Ensure src/ is on the path when invoked directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.config import get_config, Config
from clients.snipeit import SnipeITClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Device-name normalisation
# ---------------------------------------------------------------------------

DEVICE_NAME_MAPPING: Dict[str, str] = {
    "14-inch macbook pro": "MacBook Pro 14\"",
    "macbook pro 14\"": "MacBook Pro 14\"",
    "macbook pro 14": "MacBook Pro 14\"",
    "macbook pro (14-inch, 2023": "MacBook Pro 14\"",
    "macbook pro (14-inch, 2023)": "MacBook Pro 14\"",
    "apple macbook pro \"m2 pro\"": "MacBook Pro 14\"",
    "the apple macbook pro \"m1\"": "MacBook Pro 14\"",
    "macbook pro a3401": "MacBook Pro 14\"",
    "macbook": "MacBook Pro",
    "apple macbook air m2": "MacBook Air M2",
    "apple macbook air 13-inch": "MacBook Air 13\"",
    "dell precision 7680": "Dell Precision 7680",
    "benq gl2760-t 27": "Monitor (1080p)",
    "benq ew2780u 27 inch": "Monitor (1080p)",
    "benq ew2780q 27 inch 2k qhd": "Monitor (1080p)",
    "benq designvue pd2705q": "Monitor (1080p)",
    "philips s line 275s1ae/00 led display": "Monitor (1080p)",
    "iiyama g-master": "Monitor (1080p)",
    "jabra evolve 2 65 usb-c": "Headphones (Jabra)",
    "jabra evolve2 65 usb-c": "Headphones (Jabra)",
    "apple magic mouse white": "Mouse and Keyboard (Apple)",
    "magic mouse": "Mouse and Keyboard (Apple)",
    "apple magic keyboard": "Mouse and Keyboard (Apple)",
    "apple wireless keyboard": "Mouse and Keyboard (Apple)",
    "apple magic wireless keyboard": "Mouse and Keyboard (Apple)",
    "laptop stand": "Laptop Riser",
    "laptop riser": "Laptop Riser",
}

SKIP_DEVICES: Set[str] = {
    "14-inch macbook pro",
    "macbook pro 14\"",
    "macbook pro 14",
    "macbook pro (14-inch, 2023",
    "macbook pro (14-inch, 2023)",
    "apple macbook pro \"m2 pro\"",
    "the apple macbook pro \"m1\"",
    "macbook pro a3401",
    "macbook",
    "apple macbook air m2",
    "apple macbook air 13-inch",
    "dell precision 7680",
}

NAME_CORRECTIONS: Dict[str, str] = {
    "sapna gurlar": "sapna gurjar",
    "connor maqueen": "connor mcqueen",
    "liam orourke": "liam o'rourke",
    "fernando perez": "[disabled] fernando perez",
    "jonny morris": "jonathan morris",
    "liz radcliffe": "elizabeth radcliffe",
    "nayana padmanabha": "nayana p",
    "nicole shelley de santana": "nicole santana",
    "sophie lexi": "sophie bampton",
}


def normalize_device_name(desc: str) -> Tuple[Optional[str], bool]:
    """Return (snipeit_name, should_skip)."""
    if not desc:
        return None, True
    clean = desc.strip()
    lower = clean.lower().rstrip("\"')")
    if lower in SKIP_DEVICES:
        return clean, True
    for pat in (r"macbook", r"dell precision", r"laptop$"):
        if re.search(pat, lower):
            return clean, True
    if lower in DEVICE_NAME_MAPPING:
        mapped = DEVICE_NAME_MAPPING[lower]
        return mapped, mapped.lower() in {s.lower() for s in SKIP_DEVICES}
    if "jabra" in lower:
        return "Headphones (Jabra)", False
    if any(k in lower for k in ("benq", "monitor", "display", "philips", "iiyama")):
        return "Monitor (1080p)", False
    if "magic mouse" in lower or ("mouse" in lower and "apple" in lower):
        return "Mouse and Keyboard (Apple)", False
    if "magic keyboard" in lower or ("keyboard" in lower and ("apple" in lower or "wireless" in lower)):
        return "Mouse and Keyboard (Apple)", False
    if "laptop stand" in lower or "laptop riser" in lower:
        return "Laptop Riser", False
    logger.warning(f"No mapping for device: '{desc}'")
    return clean, False


# ---------------------------------------------------------------------------
# User matching helpers
# ---------------------------------------------------------------------------

def _similar_names(name: str, pool: List[str], threshold: float = 0.7, limit: int = 3):
    hits = [(c, SequenceMatcher(None, name.lower(), c.lower()).ratio()) for c in pool]
    return sorted([h for h in hits if h[1] >= threshold], key=lambda x: -x[1])[:limit]


def find_user(recipient: str, users_by_name: Dict[str, Dict]):
    low = recipient.strip().lower()
    if low in users_by_name:
        return users_by_name[low], "exact", recipient
    if low in NAME_CORRECTIONS:
        corrected = NAME_CORRECTIONS[low]
        if corrected.lower() in users_by_name:
            return users_by_name[corrected.lower()], "corrected", f"{recipient} -> {corrected}"
    for pat, rep in [(r"^o([a-z])", r"o'\1"), (r" o([a-z])", r" o'\1")]:
        mod = re.sub(pat, rep, low, flags=re.IGNORECASE)
        if mod != low and mod in users_by_name:
            return users_by_name[mod], "corrected", f"{recipient} -> {mod}"
    if " de " in low:
        joined = low.replace(" de ", " de")
        if joined in users_by_name:
            return users_by_name[joined], "corrected", f"{recipient} -> {joined}"
    similar = _similar_names(recipient, list(users_by_name.keys()), 0.85)
    if similar:
        best = similar[0][0]
        return users_by_name[best], "fuzzy", f"{recipient} ~ {best} ({similar[0][1]:.0%})"
    return None, None, recipient


# ---------------------------------------------------------------------------
# CSV reader + analyser
# ---------------------------------------------------------------------------

def read_csv(path: str) -> List[Tuple[str, str]]:
    rows = []
    with open(path, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            dev = row.get("Device Description", "").strip()
            rec = row.get("Recipient", "").strip()
            if dev and rec:
                rows.append((dev, rec))
    return rows


def analyse(shipments):
    devices: Dict[str, int] = {}
    accessories, skipped = [], []
    for dev, rec in shipments:
        name, skip = normalize_device_name(dev)
        if skip:
            skipped.append((dev, rec))
        else:
            accessories.append((name, rec, dev))
            devices[name] = devices.get(name, 0) + 1
    return {
        "total": len(shipments),
        "devices": devices,
        "accessories": accessories,
        "skipped": skipped,
    }


# ---------------------------------------------------------------------------
# Import logic (uses SnipeITClient from the main suite)
# ---------------------------------------------------------------------------

def import_to_snipeit(analysis: Dict, config: Config, dry_run: bool = True):
    snipe = SnipeITClient(
        base_url=config.snipeit.base_url,
        api_token=config.snipeit.api_token,
        timeout=config.api.timeout_seconds,
        max_retries=config.api.max_retries,
        retry_delay=config.api.retry_delay_seconds,
    )

    periph_cfg = config.modules.get("peripherals_sync", {})
    category_id = periph_cfg.get("accessory_category_id", 4)
    default_qty = periph_cfg.get("default_accessory_qty", 100)
    batch_size = periph_cfg.get("batch_size", 10)
    batch_delay = periph_cfg.get("batch_delay_seconds", 30)

    accessories_to_import = analysis["accessories"]
    if not accessories_to_import:
        print("\nNo accessories to import.")
        return

    # 1. Existing accessories
    print("\n[1/5] Fetching existing accessories …")
    existing = snipe.get_all_accessories()
    acc_id_map = {n: a["id"] for n, a in existing.items()}
    print(f"      {len(existing)} accessories in Snipe-IT")

    # 2. Create missing
    print("\n[2/5] Creating missing accessories …")
    needed = {item[0] for item in accessories_to_import}
    created = 0
    for name in sorted(needed):
        if name.lower() in acc_id_map:
            continue
        print(f"  + {name}")
        if not dry_run:
            acc = snipe.create_accessory(name, category_id, default_qty)
            if acc:
                acc_id_map[name.lower()] = acc["id"]
                created += 1
        else:
            created += 1
    print(f"      Created: {created}" if created else "      All exist ✅")

    if not dry_run and created:
        existing = snipe.get_all_accessories()
        acc_id_map = {n: a["id"] for n, a in existing.items()}

    # 3. Users
    print("\n[3/5] Fetching users …")
    all_users = snipe.get_all_users()
    users_by_name: Dict[str, Dict] = {}
    for u in all_users:
        full = (u.get("name") or "").strip().lower()
        if not full:
            full = f"{u.get('first_name', '')} {u.get('last_name', '')}".strip().lower()
        if full:
            prev = users_by_name.get(full)
            if prev and (u.get("assets_count", 0) or 0) <= (prev.get("assets_count", 0) or 0):
                continue
            users_by_name[full] = u
    print(f"      {len(users_by_name)} users indexed by name")

    # 4. Existing checkouts
    print("\n[4/5] Fetching checkouts …")
    user_co: Dict[int, Set[int]] = {}
    for acc_data in existing.values():
        aid = acc_data.get("id")
        if not aid:
            continue
        for co in snipe.get_accessory_checkouts(aid):
            uid = (co.get("assigned_to") or {}).get("id")
            if uid:
                user_co.setdefault(uid, set()).add(aid)
        time.sleep(0.1)
    print(f"      {len(user_co)} users with checkouts")

    # 5. Process
    print("\n[5/5] Processing …")
    planned, not_found, already, dupes = [], [], 0, 0
    pending: Set[Tuple[int, int]] = set()
    fuzzy_matches = []

    for sname, recipient, orig in accessories_to_import:
        user, mtype, minfo = find_user(recipient, users_by_name)
        if not user:
            sugg = _similar_names(recipient, list(users_by_name.keys()), 0.6)
            not_found.append((recipient, sname, sugg))
            continue
        if mtype in ("corrected", "fuzzy"):
            fuzzy_matches.append(minfo)
        uid = user["id"]
        aid = acc_id_map.get(sname.lower())
        if not aid:
            continue
        if aid in user_co.get(uid, set()):
            already += 1
            continue
        key = (uid, aid)
        if key in pending:
            dupes += 1
            continue
        pending.add(key)
        planned.append({"aid": aid, "uid": uid, "name": sname, "user": recipient, "orig": orig})

    if fuzzy_matches:
        unique = sorted(set(fuzzy_matches))
        print(f"\n      Fuzzy/corrected matches ({len(unique)}):")
        for m in unique:
            print(f"        {m}")

    print(f"\n      Planned: {len(planned)}, already out: {already}, dupes: {dupes}")
    if not_found:
        unique_nf = {n[0] for n in not_found}
        print(f"      Users not found: {len(unique_nf)}")
        for name in sorted(unique_nf)[:15]:
            print(f"        - {name}")

    ok, fail = 0, 0
    for i, co in enumerate(planned):
        if dry_run:
            print(f"  [DRY] {co['name']} -> {co['user']}")
            ok += 1
        else:
            success = snipe.checkout_accessory(co["aid"], co["uid"],
                                               note=f"Imported from shipment — {co['orig']}")
            if success:
                print(f"  ✅ {co['name']} -> {co['user']}")
                ok += 1
            else:
                print(f"  ❌ {co['name']} -> {co['user']}")
                fail += 1
            if (i + 1) % batch_size == 0 and (i + 1) < len(planned):
                print(f"  Batch pause ({batch_delay}s) …")
                time.sleep(batch_delay)
            else:
                time.sleep(0.5)

    print(f"\n{'='*60}")
    print(f"  ✅ {ok}  ❌ {fail}  ⏭ {already + dupes}")
    if dry_run:
        print("  (DRY RUN — no changes made)")
    print(f"{'='*60}")

    snipe.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Import shipment history CSV into Snipe-IT accessories",
    )
    parser.add_argument("csv_file", help="Path to the shipment CSV")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Preview only (default)")
    parser.add_argument("--execute", action="store_true",
                        help="Actually perform the import")
    parser.add_argument("--analyze-only", action="store_true",
                        help="Analyse the CSV without connecting to Snipe-IT")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if not os.path.exists(args.csv_file):
        logger.error(f"File not found: {args.csv_file}")
        sys.exit(1)

    dry_run = not args.execute

    print(f"\n{'='*60}")
    print("Shipment History Import Tool")
    print(f"{'='*60}")
    print(f"CSV: {args.csv_file}")
    print(f"Mode: {'DRY RUN' if dry_run else 'EXECUTE'}\n")

    shipments = read_csv(args.csv_file)
    print(f"Rows: {len(shipments)}")

    info = analyse(shipments)
    print(f"Accessories: {len(info['accessories'])}, Skipped (assets): {len(info['skipped'])}")
    for dev, cnt in sorted(info["devices"].items(), key=lambda x: -x[1]):
        print(f"  {cnt:3d} x {dev}")

    if args.analyze_only:
        print("\nAnalysis complete.")
        return

    config = get_config()
    import_to_snipeit(info, config, dry_run=dry_run)


if __name__ == "__main__":
    main()
