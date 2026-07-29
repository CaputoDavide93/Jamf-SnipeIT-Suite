"""
Jamf-SnipeIT Suite - HiBob API Client
Read-only client for extracting employee equipment data from HiBob.
"""
import base64
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)


class HiBobClient:
    """
    HiBob REST API client.

    All operations are **read-only** — only GET and POST /search
    endpoints are used (POST /search is HiBob's read-query pattern).
    """

    API_BASE = "https://api.hibob.com/v1"

    # Equipment values that mean "no equipment"
    SKIP_VALUES = frozenset([
        "none",
        "i am a contractor - no extra equipment",
    ])

    def __init__(
        self,
        service_user_id: str,
        service_user_token: str,
        timeout: int = 30,
        max_retries: int = 3,
        retry_delay: int = 5,
        base_url: str = "",
    ):
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        # hibob.base_url was configurable but never reached the client, so the
        # class constant always won. Honour it, falling back to the constant.
        self.api_base = (base_url or self.API_BASE).rstrip("/")

        creds = f"{service_user_id}:{service_user_token}"
        encoded = base64.b64encode(creds.encode()).decode()
        self._headers = {
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self.session = requests.Session()
        self.session.headers.update(self._headers)
        logger.debug("HiBob client initialised")

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict] = None,
        json_data: Optional[Dict] = None,
    ) -> Optional[requests.Response]:
        """Make an API request with retries."""
        url = f"{self.api_base}{path}"

        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.request(
                    method,
                    url,
                    params=params,
                    json=json_data,
                    timeout=self.timeout,
                )

                if resp.status_code == 429:
                    wait = self.retry_delay * (attempt + 1)
                    logger.warning(f"HiBob rate-limited, waiting {wait}s …")
                    time.sleep(wait)
                    continue

                if resp.status_code >= 500 and attempt < self.max_retries:
                    wait = self.retry_delay * (attempt + 1)
                    logger.warning(
                        f"HiBob server error {resp.status_code}, retrying in {wait}s …"
                    )
                    time.sleep(wait)
                    continue

                return resp

            except requests.RequestException as exc:
                if attempt < self.max_retries:
                    wait = self.retry_delay * (attempt + 1)
                    logger.warning(f"HiBob request error: {exc}, retrying in {wait}s …")
                    time.sleep(wait)
                    continue
                logger.error(f"HiBob request failed after {self.max_retries} retries: {exc}")
                return None

        return None

    # ------------------------------------------------------------------
    # Field discovery
    # ------------------------------------------------------------------

    def get_employee_fields(self) -> List[Dict[str, Any]]:
        """Fetch all employee field metadata."""
        resp = self._request("GET", "/company/people/fields")
        if not resp or resp.status_code != 200:
            logger.error("Failed to fetch HiBob fields")
            return []
        return resp.json()

    def find_equipment_field(
        self, fields: Optional[List[Dict]] = None
    ) -> Optional[Dict[str, Any]]:
        """Auto-discover the 'Extra Equipment' field."""
        if fields is None:
            fields = self.get_employee_fields()

        for f in fields:
            name_lower = f.get("name", "").lower()
            if ("extra" in name_lower and "equipment" in name_lower) or \
               "it equipment" in name_lower:
                logger.info(f"Discovered equipment field: {f.get('id')} ({f.get('name')})")
                return f

        # Broader fallback
        for f in fields:
            if "equipment" in f.get("name", "").lower():
                logger.info(f"Discovered equipment field (fallback): {f.get('id')} ({f.get('name')})")
                return f

        logger.warning("Could not auto-discover equipment field in HiBob")
        return None

    # ------------------------------------------------------------------
    # Named lists (for converting IDs → human-readable values)
    # ------------------------------------------------------------------

    def get_named_list(self, list_name: str) -> Dict[str, str]:
        """Fetch a named list and return {id: display_value} mapping."""
        resp = self._request("GET", f"/company/named-lists/{list_name}")
        if not resp or resp.status_code != 200:
            return {}

        data = resp.json()
        id_to_name: Dict[str, str] = {}

        def _walk(items: list) -> None:
            for item in items:
                item_id = str(item.get("id", ""))
                item_val = item.get("value") or item.get("name", "")
                if item_id:
                    id_to_name[item_id] = item_val
                if item.get("children"):
                    _walk(item["children"])

        if isinstance(data, dict):
            for val in data.values():
                if isinstance(val, dict) and "values" in val:
                    _walk(val["values"])
                elif isinstance(val, list):
                    _walk(val)

        return id_to_name

    # ------------------------------------------------------------------
    # Employee search
    # ------------------------------------------------------------------

    def search_employees(
        self,
        fields_to_fetch: Optional[List[str]] = None,
        show_inactive: bool = False,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search employees (read-only POST).

        Args:
            fields_to_fetch: Field IDs to retrieve.
            show_inactive: Include inactive employees.
            limit: Cap the number of employees returned.
        """
        payload: Dict[str, Any] = {
            "showInactive": show_inactive,
            "humanReadable": "APPEND",
        }
        if fields_to_fetch:
            payload["fields"] = fields_to_fetch

        resp = self._request("POST", "/people/search", json_data=payload)
        if not resp or resp.status_code != 200:
            logger.error("Failed to search HiBob employees")
            return []

        employees = resp.json().get("employees", [])
        if limit and limit > 0:
            employees = employees[:limit]
        return employees

    # ------------------------------------------------------------------
    # High-level: extract equipment per employee
    # ------------------------------------------------------------------

    def _resolve_equipment_value(
        self,
        emp: Dict,
        field_id: str,
        list_map: Dict[str, str],
    ) -> Optional[str]:
        """Extract the equipment value for one employee, resolving list IDs."""
        value = None

        # 1) humanReadable section (preferred — pre-converted)
        hr = emp.get("humanReadable")
        if hr:
            parts = field_id.split(".")
            cur: Any = hr
            for p in parts:
                if isinstance(cur, dict) and p in cur:
                    cur = cur[p]
                else:
                    cur = None
                    break
            if cur is not None and not isinstance(cur, dict):
                value = cur

        # 2) /<section>/<field> path
        if value is None:
            key = "/" + field_id.replace(".", "/")
            if key in emp:
                value = emp[key].get("value") if isinstance(emp[key], dict) else emp[key]

        # 3) Dot-path fallback
        if value is None:
            parts = field_id.split(".")
            cur = emp
            for p in parts:
                if isinstance(cur, dict) and p in cur:
                    cur = cur[p]
                else:
                    cur = None
                    break
            if isinstance(cur, dict) and "value" in cur:
                value = cur["value"]
            elif cur is not None and not isinstance(cur, dict):
                value = cur

        if value is None:
            return None

        # Convert list IDs → names
        if list_map:
            if isinstance(value, list):
                value = ", ".join(
                    list_map.get(str(v), str(v)) for v in value
                )
            elif isinstance(value, (int, float)):
                value = list_map.get(str(int(value)), str(value))
            elif isinstance(value, str):
                parts = [p.strip() for p in value.split(",")]
                converted = [list_map.get(p, p) for p in parts]
                value = ", ".join(converted)

        # Filter out skip values
        if not value:
            return None
        parts = [p.strip() for p in str(value).split(",")]
        filtered = [p for p in parts if p.lower().strip() not in self.SKIP_VALUES]
        return ", ".join(filtered) if filtered else None

    def extract_equipment(
        self,
        equipment_field_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Extract IT equipment data for all employees.

        Returns a list of dicts:
            {employee_id, full_name, email, extra_equipment, start_date}
        """
        # Discover field if not provided
        if not equipment_field_id:
            field_meta = self.find_equipment_field()
            if not field_meta:
                logger.error("Cannot extract equipment — field not found")
                return []
            equipment_field_id = field_meta.get("id")
        else:
            fields = self.get_employee_fields()
            field_meta = next(
                (f for f in fields if f.get("id") == equipment_field_id),
                None,
            )

        # Resolve list IDs if it's a list field
        list_map: Dict[str, str] = {}
        if field_meta:
            type_data = field_meta.get("typeData", {})
            list_id = type_data.get("listId") or type_data.get("list")
            f_type = field_meta.get("type", "")
            if f_type in ("list", "multi-list", "hierarchy-list") and list_id:
                list_map = self.get_named_list(list_id)
                logger.debug(f"Loaded {len(list_map)} list items for equipment field")

        # Fetch employees
        fetch_fields = [
            "root.id",
            "root.firstName",
            "root.surname",
            "root.email",
            "work.email",
            "work.startDate",
            equipment_field_id,
        ]
        employees = self.search_employees(fields_to_fetch=fetch_fields, limit=limit)
        logger.debug(f"Fetched {len(employees)} employees from HiBob")

        results: List[Dict[str, Any]] = []
        with_equipment = 0

        for emp in employees:
            eid = (emp.get("/root/id", {}).get("value")
                   if isinstance(emp.get("/root/id"), dict) else emp.get("id"))
            first = (emp.get("/root/firstName", {}).get("value")
                     if isinstance(emp.get("/root/firstName"), dict) else emp.get("firstName", ""))
            last = (emp.get("/root/surname", {}).get("value")
                    if isinstance(emp.get("/root/surname"), dict) else emp.get("surname", ""))
            full_name = emp.get("fullName") or f"{first} {last}".strip()
            email = (
                (emp.get("/work/email", {}).get("value") if isinstance(emp.get("/work/email"), dict) else None)
                or (emp.get("/root/email", {}).get("value") if isinstance(emp.get("/root/email"), dict) else None)
                or emp.get("email", "")
            )

            equipment = self._resolve_equipment_value(emp, equipment_field_id, list_map)

            # Start date
            start_date = None
            sd_data = emp.get("/work/startDate", {})
            if isinstance(sd_data, dict):
                start_date = sd_data.get("value")
            elif sd_data:
                start_date = sd_data

            if equipment:
                with_equipment += 1

            results.append({
                "employee_id": eid,
                "full_name": full_name,
                "email": email or "",
                "extra_equipment": equipment,
                "start_date": start_date,
            })

        logger.debug(f"Employees with equipment: {with_equipment}/{len(results)}")
        return results

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the HTTP session."""
        self.session.close()
