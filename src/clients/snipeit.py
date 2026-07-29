"""
Jamf-SnipeIT Suite - Unified Snipe-IT API Client
Consolidates all Snipe-IT API functionality from multiple scripts.
"""
import logging
import time
import requests
from typing import Any, Dict, List, Optional
from requests.adapters import HTTPAdapter, Retry

logger = logging.getLogger(__name__)


class SnipeITClient:
    """
    Unified Snipe-IT API client with retry logic and all operations
    needed by the suite modules.
    """
    
    def __init__(
        self,
        base_url: str,
        api_token: str,
        timeout: int = 30,
        max_retries: int = 3,
        retry_delay: int = 2,
        rate_limit_wait: int = 60,
    ):
        """
        Initialize Snipe-IT API client.
        
        Args:
            base_url: Snipe-IT URL (e.g., https://snipeit.example.com)
            api_token: API Bearer token
            timeout: Request timeout in seconds
            max_retries: Maximum retry attempts
            retry_delay: Initial delay between retries (exponential backoff)
            rate_limit_wait: Wait time when rate limited
        """
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.rate_limit_wait = rate_limit_wait
        
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        adapter = HTTPAdapter(
            pool_connections=20,
            pool_maxsize=50,
            max_retries=Retry(
                total=0,  # handled manually below
                backoff_factor=0,
            ),
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        # High-water mark for CF-#### asset tags, scanned lazily once.
        self._cf_tag_high_water: Optional[int] = None

    def _url(self, path: str) -> str:
        """Build full URL for API endpoint."""
        # Ensure /api/v1 prefix
        if not path.startswith("/api/"):
            path = f"/api/v1{path}"
        return f"{self.base_url}{path}"
    
    # =========================================================================
    # Request Helpers with Retry Logic
    # =========================================================================
    
    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict] = None,
        json_data: Optional[Dict] = None,
    ) -> Optional[requests.Response]:
        """
        Make an API request with retry logic.
        
        Args:
            method: HTTP method
            path: API path (without /api/v1 prefix)
            params: Query parameters
            json_data: JSON body data
        
        Returns:
            Response object or None on failure
        """
        url = self._url(path)
        
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.request(
                    method,
                    url,
                    params=params,
                    json=json_data,
                    timeout=self.timeout,
                )
                
                # Handle rate limiting
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    delay = None
                    if retry_after:
                        try:
                            delay = min(int(retry_after), 120)
                        except ValueError:
                            delay = None
                    if delay is None:
                        delay = self.rate_limit_wait * (2 ** (attempt - 1))
                    if attempt < self.max_retries:
                        logger.warning(
                            f"Rate limit hit (attempt {attempt}/{self.max_retries}). "
                            f"Waiting {delay}s..."
                        )
                        time.sleep(delay)
                        continue
                    else:
                        logger.error("Rate limit exceeded after max retries")
                        return None
                
                # Handle 404 - Not Found (legitimate, do NOT retry)
                if response.status_code == 404:
                    logger.debug(f"404 Not Found for {url} — resource does not exist")
                    return None
                
                # Handle other errors (retry-able)
                if response.status_code >= 400:
                    # Truncate and sanitize — response body may contain reflected credentials
                    safe_body = "[REDACTED]"
                    logger.error(f"API error {response.status_code}: {safe_body}")
                    if attempt < self.max_retries:
                        delay = self.retry_delay * (2 ** (attempt - 1))
                        time.sleep(delay)
                        continue
                    return None
                
                return response
                
            except requests.exceptions.RequestException as e:
                logger.error(f"Request exception ({url}): {e}")
                if attempt < self.max_retries:
                    delay = self.retry_delay * (2 ** (attempt - 1))
                    logger.debug(f"Retrying in {delay}s...")
                    time.sleep(delay)
                else:
                    return None
        
        return None
    
    def _write_ok(self, response: Optional[requests.Response], what: str) -> bool:
        """
        Return whether a write actually succeeded.

        Snipe-IT answers HTTP 200 with a ``{"status": "error"}`` body on
        validation failures (locked asset, already checked out, bad field).
        Treating HTTP 200 alone as success makes callers count silent
        no-ops as completed work.
        """
        if response is None or response.status_code not in (200, 201):
            return False
        try:
            result = response.json()
        except ValueError:
            return True  # empty/non-JSON body on 2xx — nothing to contradict
        if isinstance(result, dict) and result.get("status") == "error":
            logger.warning(
                f"{what} returned HTTP {response.status_code} but status=error: "
                f"{result.get('messages', '')}"
            )
            return False
        return True

    def _paginated_rows(
        self,
        path: str,
        limit: int = 500,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch every row of a paginated list endpoint (never truncates)."""
        rows: List[Dict[str, Any]] = []
        offset = 0
        while True:
            params = {"limit": limit, "offset": offset}
            if extra_params:
                params.update(extra_params)
            response = self._request("GET", path, params=params)
            if not response:
                break
            data = response.json()
            page = data.get("rows", [])
            if not page:
                break
            rows.extend(page)
            total = data.get("total", 0)
            offset += limit
            if len(page) < limit or offset >= total:
                break
        return rows

    # =========================================================================
    # User Operations
    # =========================================================================

    def get_all_users(self, limit: int = 500) -> List[Dict[str, Any]]:
        """
        Get all users from Snipe-IT with pagination support.
        
        Args:
            limit: Page size for pagination (max users per request)
        
        Returns:
            List of all user dictionaries
        """
        logger.debug("Fetching all users from Snipe-IT...")
        
        all_users: List[Dict[str, Any]] = []
        offset = 0
        
        while True:
            response = self._request("GET", "/users", params={"limit": limit, "offset": offset})
            if not response:
                break
            
            data = response.json()
            users = data.get("rows", [])
            total = data.get("total", 0)
            
            if not users:
                break
            
            all_users.extend(users)
            offset += len(users)
            
            logger.debug(f"Fetched {len(all_users)}/{total} users")
            
            # Check if we've fetched all users
            if len(all_users) >= total or len(users) < limit:
                break
        
        logger.debug(f"Retrieved {len(all_users)} users total")
        return all_users

    def find_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """
        Find a user by email address.
        
        Args:
            email: Email address to search
        
        Returns:
            User dictionary or None
        """
        if not email:
            return None
        
        response = self._request("GET", "/users", params={"search": email, "limit": 5})
        if not response:
            return None
        
        data = response.json()
        for user in data.get("rows", []):
            if (user.get("email", "") or "").lower() == email.lower():
                logger.debug(f"Found Snipe user by email {email}: id={user.get('id')}")
                return user
        
        logger.debug(f"No Snipe user found for email: {email}")
        return None
    
    def find_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """
        Find a user by username (searches username field and email prefix).
        Uses strict normalized matching to avoid false positives.
        
        Args:
            username: Username to search (e.g., 'lewistowart' or 'lewis.towart')
        
        Returns:
            User dictionary or None
        """
        if not username or len(username) < 3:
            return None
        
        # Normalize the search username for comparison
        search_norm = username.lower().strip().replace(".", "").replace("_", "").replace("-", "")
        
        # Try searching with the username directly
        response = self._request("GET", "/users", params={"search": username, "limit": 50})
        if not response:
            return None
        
        data = response.json()
        
        for user in data.get("rows", []):
            # Check username match (normalized)
            snipe_username = (user.get("username") or "").lower()
            snipe_username_norm = snipe_username.replace(".", "").replace("_", "").replace("-", "")
            
            # Remove domain from username if present
            if "@" in snipe_username_norm:
                snipe_username_norm = snipe_username_norm.split("@")[0]
            
            # Check email prefix match (normalized)
            snipe_email = (user.get("email") or "").lower()
            snipe_email_prefix = snipe_email.split("@")[0] if "@" in snipe_email else ""
            snipe_email_norm = snipe_email_prefix.replace(".", "").replace("_", "").replace("-", "")
            
            # STRICT match: require exact normalized match (not substring!)
            if search_norm == snipe_username_norm or search_norm == snipe_email_norm:
                logger.debug(f"Found Snipe user by username {username}: id={user.get('id')}, name={user.get('name')}")
                return user
        
        logger.debug(f"No Snipe user found for username: {username}")
        return None
    
    def get_user_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        """
        Get user details by ID.
        
        Args:
            user_id: Snipe-IT user ID
        
        Returns:
            User dictionary or None
        """
        response = self._request("GET", f"/users/{user_id}")
        if not response:
            return None
        
        return response.json()
    
    def get_user_assets(self, user_id: int) -> List[Dict[str, Any]]:
        """
        Get assets assigned to a specific user via the dedicated API endpoint.
        Much more reliable than searching by name.
        
        Args:
            user_id: Snipe-IT user ID
        
        Returns:
            List of asset dictionaries assigned to this user
        """
        response = self._request("GET", f"/users/{user_id}/assets")
        if not response:
            return []
        
        data = response.json()
        rows = data.get("rows", [])
        total = data.get("total", 0)
        
        logger.debug(f"Found {len(rows)}/{total} assets for user {user_id}")
        return rows
    
    def update_user(self, user_id: int, data: Dict[str, Any]) -> bool:
        """
        Update user information.

        Args:
            user_id: Snipe-IT user ID
            data: Fields to update

        Returns:
            True if successful
        """
        response = self._request("PATCH", f"/users/{user_id}", json_data=data)
        if response is None or response.status_code not in (200, 201):
            return False
        # Snipe-IT returns HTTP 200 with {"status": "error"} on validation
        # failures — check the JSON body like create_user does, otherwise
        # callers count silent failures as successes.
        try:
            result = response.json()
        except ValueError:
            return True
        if isinstance(result, dict) and result.get("status") == "error":
            logger.warning(
                f"update_user {user_id} returned HTTP {response.status_code} "
                f"but status=error: {result.get('messages', '')}"
            )
            return False
        return True
    
    def create_user(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Create a new user in Snipe-IT.
        
        Args:
            data: User data with fields:
                - first_name (required)
                - last_name (required)
                - email (required)
                - username (required)
                - password (required)
                - password_confirmation (required)
                - jobtitle (optional)
                - company_id (optional)
                - department_id (optional)
        
        Returns:
            Created user dictionary or None if failed
        """
        response = self._request("POST", "/users", json_data=data)
        if response and response.status_code in (200, 201):
            result = response.json()
            if result.get("status") == "success":
                return result.get("payload")
            # Status is not "success" — log the error and return None
            logger.warning(f"create_user returned non-success status: {result.get('status')} — {result.get('messages', '')}")
        return None

    def delete_user(self, user_id: int) -> bool:
        """
        Delete a user from Snipe-IT.

        Args:
            user_id: Snipe-IT user ID

        Returns:
            True if successfully deleted
        """
        response = self._request("DELETE", f"/users/{user_id}")
        return response is not None and response.status_code in (200, 204)
    
    # =========================================================================
    # Asset/Hardware Operations
    # =========================================================================
    
    def get_asset_by_serial(self, serial: str) -> Optional[Dict[str, Any]]:
        """
        Get asset by serial number.
        
        Args:
            serial: Serial number
        
        Returns:
            Asset dictionary or None
        """
        if not serial:
            return None
        
        # Try byserial endpoint first
        response = self._request("GET", f"/hardware/byserial/{serial}")
        if response and response.status_code == 200:
            data = response.json()
            asset = self._normalize_asset(data)
            if asset:
                logger.debug(f"Found asset by serial {serial}: id={asset.get('id')}")
                return asset
        
        # Fallback to search
        response = self._request("GET", "/hardware", params={"search": serial})
        if not response:
            return None
        
        data = response.json()
        for item in data.get("rows", []):
            if (item.get("serial", "") or "").upper() == serial.upper():
                logger.debug(f"Found asset by search {serial}: id={item.get('id')}")
                return item
        
        return None
    
    def get_asset_by_id(self, asset_id: int) -> Optional[Dict[str, Any]]:
        """
        Get asset details by ID.
        
        Args:
            asset_id: Snipe-IT asset ID
        
        Returns:
            Asset dictionary or None
        """
        response = self._request("GET", f"/hardware/{asset_id}")
        if not response:
            return None
        
        return response.json()
    
    def get_all_assets(self, limit: int = 1000) -> List[Dict[str, Any]]:
        """
        Get all assets with pagination.
        Useful for bulk operations to avoid N+1 API calls.
        
        Args:
            limit: Max results per page (Snipe-IT max is usually 1000)
        
        Returns:
            List of all asset dictionaries
        """
        all_assets = []
        offset = 0
        
        while True:
            response = self._request(
                "GET", 
                "/hardware",
                params={"limit": limit, "offset": offset, "sort": "id", "order": "asc"}
            )
            if not response:
                break
            
            data = response.json()
            rows = data.get("rows", [])
            all_assets.extend(rows)
            
            total = data.get("total", 0)
            offset += limit
            
            logger.debug(f"Fetched {len(all_assets)}/{total} assets")
            
            if offset >= total or not rows:
                break
        
        logger.debug(f"Retrieved {len(all_assets)} total assets from Snipe-IT")
        return all_assets
    
    def get_assets_by_serial_map(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all assets indexed by serial number for fast lookup.
        
        Returns:
            Dictionary mapping serial (uppercase) -> asset dict
        """
        assets = self.get_all_assets()
        serial_map = {}
        
        for asset in assets:
            serial = (asset.get("serial") or "").strip().upper()
            if serial:
                serial_map[serial] = asset
        
        logger.debug(f"Built serial map with {len(serial_map)} entries")
        return serial_map
    
    def search_assets(
        self,
        search: str = "",
        status: str = "",
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Search for assets.
        
        Args:
            search: Search term
            status: Filter by status
            limit: Maximum results
        
        Returns:
            List of asset dictionaries
        """
        params = {"limit": limit}
        if search:
            params["search"] = search
        if status:
            params["status"] = status
        
        response = self._request("GET", "/hardware", params=params)
        if not response:
            return []
        
        return response.json().get("rows", [])
    
    def next_cf_tag(self) -> str:
        """
        Return the next CF-#### asset tag.

        The high-water mark is scanned from Snipe-IT once per client and then
        incremented locally. Re-scanning every asset on each call meant a run
        that created N machines paged through the whole inventory N times.
        """
        if self._cf_tag_high_water is None:
            import re
            max_n = 0
            for a in self.get_all_assets() or []:
                m = re.match(r"^CF-(\d+)$", (a.get("asset_tag") or "").strip())
                if m:
                    n = int(m.group(1))
                    if n > max_n:
                        max_n = n
            self._cf_tag_high_water = max_n
        self._cf_tag_high_water += 1
        return f"CF-{self._cf_tag_high_water:04d}"

    def create_asset(
        self,
        name: str,
        serial: str,
        model_id: int,
        status_id: int,
        asset_tag: str = "",
        company_id: int = 0,
        location_id: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """
        Create a new asset.
        
        Args:
            name: Asset name
            serial: Serial number
            model_id: Model ID
            status_id: Status ID
            asset_tag: Asset tag (defaults to serial)
            company_id: Company ID
            location_id: Location ID
        
        Returns:
            Created asset dictionary or None
        """
        payload = {
            "name": name or serial,
            "serial": serial,
            "model_id": model_id,
            "status_id": status_id,
        }
        
        # Set asset_tag if provided; caller should pass next_cf_tag() for CF-#### format.
        # Otherwise Snipe-IT defaults asset_tag to the serial number.
        if asset_tag:
            payload["asset_tag"] = asset_tag
        
        if company_id > 0:
            payload["company_id"] = company_id
        if location_id > 0:
            payload["rtd_location_id"] = location_id
        
        logger.debug(f"Creating asset: serial={serial}, model_id={model_id}")
        
        response = self._request("POST", "/hardware", json_data=payload)
        if not self._write_ok(response, f"create_asset {serial}"):
            logger.error(f"Failed to create asset: {serial}")
            return None

        result = response.json()
        asset = self._normalize_asset(result)
        if not asset or not asset.get("id"):
            # 200 + success-looking body but no usable id — callers would
            # otherwise carry an id-less dict into checkout and fail obscurely.
            logger.error(f"create_asset {serial}: response contained no asset id")
            return None
        logger.debug(f"Created asset: id={asset.get('id')}, serial={serial}")
        return asset
    
    def update_asset(self, asset_id: int, data: Dict[str, Any]) -> bool:
        """
        Update asset fields.
        
        Args:
            asset_id: Asset ID
            data: Fields to update
        
        Returns:
            True if successful
        """
        response = self._request("PATCH", f"/hardware/{asset_id}", json_data=data)
        return self._write_ok(response, f"update_asset {asset_id}")
    
    def update_asset_status(self, asset_id: int, status_id: int) -> bool:
        """
        Update asset status.
        
        Args:
            asset_id: Asset ID
            status_id: New status ID
        
        Returns:
            True if successful
        """
        logger.debug(f"Updating asset {asset_id} status to {status_id}")
        return self.update_asset(asset_id, {"status_id": status_id})
    
    def checkout_asset(
        self,
        asset_id: int,
        user_id: int,
        note: str = "Provisioned via automation",
    ) -> bool:
        """
        Checkout asset to a user.
        
        Args:
            asset_id: Asset ID
            user_id: User ID to checkout to
            note: Checkout note
        
        Returns:
            True if successful
        """
        payload = {
            "checkout_to_type": "user",
            "assigned_user": user_id,
            "note": note,
        }
        
        logger.debug(f"Checking out asset {asset_id} to user {user_id}")
        
        response = self._request("POST", f"/hardware/{asset_id}/checkout", json_data=payload)
        if self._write_ok(response, f"checkout_asset {asset_id}"):
            logger.debug(f"Checkout successful: asset {asset_id} -> user {user_id}")
            return True

        logger.error(f"Checkout failed: asset {asset_id} -> user {user_id}")
        return False
    
    def checkin_asset(self, asset_id: int, note: str = "Auto check-in") -> bool:
        """
        Check in an asset.
        
        Args:
            asset_id: Asset ID
            note: Check-in note
        
        Returns:
            True if successful
        """
        payload = {"note": note}
        
        logger.debug(f"Checking in asset {asset_id}")
        
        response = self._request("POST", f"/hardware/{asset_id}/checkin", json_data=payload)
        if self._write_ok(response, f"checkin_asset {asset_id}"):
            logger.debug(f"Check-in successful: asset {asset_id}")
            return True

        logger.error(f"Check-in failed: asset {asset_id}")
        return False
    
    def get_assigned_user_id(self, asset: Dict[str, Any]) -> Optional[int]:
        """
        Extract assigned user ID from asset data.
        
        Args:
            asset: Asset dictionary
        
        Returns:
            User ID or None
        """
        if not asset:
            return None
        
        # Try assigned_to.id
        assigned_to = asset.get("assigned_to")
        if isinstance(assigned_to, dict):
            uid = assigned_to.get("id") or assigned_to.get("user_id")
            if uid:
                return int(uid)
        
        # Try user.id
        user = asset.get("user")
        if isinstance(user, dict) and user.get("id"):
            return int(user.get("id"))
        
        # Try payload.assigned_to
        payload = asset.get("payload")
        if isinstance(payload, dict):
            at = payload.get("assigned_to")
            if isinstance(at, dict) and at.get("id"):
                return int(at.get("id"))
        
        return None
    
    # =========================================================================
    # Model Operations
    # =========================================================================
    
    def get_all_models(self, limit: int = 500) -> List[Dict[str, Any]]:
        """
        Get all models from Snipe-IT.
        
        Returns:
            List of model dictionaries
        """
        logger.debug("Fetching all models from Snipe-IT...")

        # Paginated: a truncated model list makes Model Sync believe existing
        # models are missing and try to re-create them.
        models = self._paginated_rows("/models", limit=limit)
        logger.debug(f"Retrieved {len(models)} models")
        return models
    
    def get_model_name_to_id_map(self) -> Dict[str, int]:
        """
        Build a mapping of model names to IDs.
        
        Returns:
            Dict mapping model name (lowercase) to model ID
        """
        models = self.get_all_models()
        return {
            model["name"].lower(): model["id"]
            for model in models
            if "name" in model and "id" in model
        }
    
    def create_model(
        self,
        name: str,
        manufacturer_id: int,
        category_id: int = 1,
    ) -> Optional[int]:
        """
        Create a new model.
        
        Args:
            name: Model name
            manufacturer_id: Manufacturer ID
            category_id: Category ID
        
        Returns:
            New model ID or None
        """
        payload = {
            "name": name,
            "manufacturer_id": manufacturer_id,
            "category_id": category_id,
        }
        
        logger.debug(f"Creating model: {name}")
        
        response = self._request("POST", "/models", json_data=payload)
        if not response or response.status_code not in (200, 201):
            logger.error(f"Failed to create model: {name}")
            return None
        
        result = response.json()
        if result.get("status") == "success":
            model_id = result.get("payload", {}).get("id")
            logger.debug(f"Created model '{name}' with ID={model_id}")
            return model_id
        
        logger.error(f"Model creation failed: {result}")
        return None
    
    # =========================================================================
    # Manufacturer Operations
    # =========================================================================
    
    def get_all_manufacturers(self) -> Dict[str, int]:
        """
        Get all manufacturers as name->ID mapping.
        
        Returns:
            Dict mapping manufacturer name (lowercase) to ID
        """
        logger.debug("Fetching manufacturers from Snipe-IT...")

        rows = self._paginated_rows("/manufacturers")
        manufacturers = {
            row["name"].lower(): row["id"]
            for row in rows
            if "name" in row and "id" in row
        }
        
        logger.debug(f"Retrieved {len(manufacturers)} manufacturers")
        return manufacturers
    
    def create_manufacturer(self, name: str) -> Optional[int]:
        """
        Create a new manufacturer.
        
        Args:
            name: Manufacturer name
        
        Returns:
            New manufacturer ID or None
        """
        payload = {"name": name}
        
        logger.debug(f"Creating manufacturer: {name}")
        
        response = self._request("POST", "/manufacturers", json_data=payload)
        if not response or response.status_code not in (200, 201):
            logger.error(f"Failed to create manufacturer: {name}")
            return None
        
        result = response.json()
        if result.get("status") == "success":
            mfr_id = result.get("payload", {}).get("id")
            logger.debug(f"Created manufacturer '{name}' with ID={mfr_id}")
            return mfr_id
        
        return None
    
    # =========================================================================
    # Category Operations
    # =========================================================================
    
    def get_all_categories(self) -> List[Dict[str, Any]]:
        """
        Get all categories from Snipe-IT.
        
        Returns:
            List of category dictionaries
        """
        return self._paginated_rows("/categories")
    
    # =========================================================================
    # Utility Methods
    # =========================================================================
    
    def _normalize_asset(self, data: Any) -> Optional[Dict[str, Any]]:
        """
        Normalize various asset response formats to a consistent structure.
        
        Args:
            data: Raw API response data
        
        Returns:
            Normalized asset dict with at least 'id' and 'serial'
        """
        if not isinstance(data, (dict, list)):
            return None
        
        if isinstance(data, dict):
            # Direct asset object
            if "id" in data and isinstance(data["id"], (int, str)):
                return {"id": data.get("id"), "serial": data.get("serial"), **data}
            
            # Wrapped in payload/item/hardware/data
            for root in ("payload", "item", "hardware", "data"):
                obj = data.get(root)
                if isinstance(obj, dict) and "id" in obj:
                    return {"id": obj.get("id"), "serial": obj.get("serial"), **obj}
            
            # In rows array
            rows = data.get("rows")
            if isinstance(rows, list) and rows:
                first = rows[0]
                if isinstance(first, dict) and "id" in first:
                    return {"id": first.get("id"), "serial": first.get("serial"), **first}
        
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict) and "id" in first:
                return {"id": first.get("id"), "serial": first.get("serial"), **first}
        
        return None

    # ------------------------------------------------------------------
    # Accessories
    # ------------------------------------------------------------------

    def get_all_accessories(self, limit: int = 500) -> Dict[str, Dict[str, Any]]:
        """
        Fetch all accessories from Snipe-IT, indexed by lowercase name.

        Returns:
            {lowercase_name: accessory_dict}
        """
        accessories: Dict[str, Dict[str, Any]] = {}
        offset = 0

        while True:
            response = self._request(
                "GET", "/accessories",
                params={"limit": limit, "offset": offset},
            )
            if not response:
                break
            data = response.json()
            for acc in data.get("rows", []):
                name = (acc.get("name") or "").lower()
                if name:
                    accessories[name] = acc
            total = data.get("total", 0)
            offset += limit
            if offset >= total or not data.get("rows"):
                break

        logger.debug(f"Retrieved {len(accessories)} accessories from Snipe-IT")
        return accessories

    def create_accessory(
        self,
        name: str,
        category_id: int,
        qty: int = 100,
    ) -> Optional[Dict[str, Any]]:
        """Create a new accessory in Snipe-IT."""
        payload = {"name": name, "qty": qty, "category_id": category_id}
        response = self._request("POST", "/accessories", json_data=payload)
        if not response or response.status_code not in (200, 201):
            logger.error(f"Failed to create accessory '{name}'")
            return None
        result = response.json()
        if result.get("status") == "success":
            acc = result.get("payload")
            logger.debug(f"Created accessory '{name}' (ID={acc.get('id') if acc else '?'})")
            return acc
        logger.error(f"Create accessory '{name}': {result.get('messages')}")
        return None

    def get_accessory_checkouts(self, accessory_id: int, limit: int = 500) -> List[Dict[str, Any]]:
        """Get all users who have a specific accessory checked out."""
        response = self._request(
            "GET", f"/accessories/{accessory_id}/checkedout",
            params={"limit": limit},
        )
        if not response or response.status_code != 200:
            return []
        return response.json().get("rows", [])

    def checkout_accessory(
        self,
        accessory_id: int,
        user_id: int,
        note: str = "",
    ) -> bool:
        """Check out one unit of an accessory to a user."""
        payload: Dict[str, Any] = {"assigned_user": user_id, "checkout_qty": 1}
        if note:
            payload["note"] = note
        response = self._request(
            "POST", f"/accessories/{accessory_id}/checkout", json_data=payload,
        )
        if not response:
            return False
        if response.status_code == 200 and response.json().get("status") == "success":
            return True
        logger.warning(
            f"Accessory checkout failed: acc={accessory_id} user={user_id} "
            f"— {response.text[:200]}"
        )
        return False

    def close(self) -> None:
        """Close the session."""
        self.session.close()

    def ping(self) -> bool:
        """Quick connectivity check — fetches page 1 of users (limit=1)."""
        try:
            resp = self._request("GET", "/users", params={"limit": 1})
            return resp is not None and resp.status_code == 200
        except Exception:
            return False
