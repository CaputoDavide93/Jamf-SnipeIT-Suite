"""
Jamf-SnipeIT Suite - Unified Snipe-IT API Client
Consolidates all Snipe-IT API functionality from multiple scripts.
"""
import logging
import time
import requests
from typing import Any, Dict, List, Optional

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
                    if attempt < self.max_retries:
                        logger.warning(
                            f"Rate limit hit (attempt {attempt}/{self.max_retries}). "
                            f"Waiting {self.rate_limit_wait}s..."
                        )
                        time.sleep(self.rate_limit_wait)
                        continue
                    else:
                        logger.error("Rate limit exceeded after max retries")
                        return None
                
                # Handle other errors
                if response.status_code >= 400:
                    logger.error(f"API error {response.status_code}: {response.text[:200]}")
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
                    logger.info(f"Retrying in {delay}s...")
                    time.sleep(delay)
                else:
                    return None
        
        return None
    
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
        logger.info("Fetching all users from Snipe-IT...")
        
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
        
        logger.info(f"Retrieved {len(all_users)} users total")
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
        return response is not None and response.status_code in (200, 201)
    
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
            "asset_tag": asset_tag or serial,
        }
        
        if company_id > 0:
            payload["company_id"] = company_id
        if location_id > 0:
            payload["rtd_location_id"] = location_id
        
        logger.info(f"Creating asset: serial={serial}, model_id={model_id}")
        
        response = self._request("POST", "/hardware", json_data=payload)
        if not response or response.status_code not in (200, 201):
            logger.error(f"Failed to create asset: {serial}")
            return None
        
        result = response.json()
        asset = self._normalize_asset(result) or result
        logger.info(f"Created asset: id={asset.get('id')}, serial={serial}")
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
        return response is not None and response.status_code in (200, 201)
    
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
        
        logger.info(f"Checking out asset {asset_id} to user {user_id}")
        
        response = self._request("POST", f"/hardware/{asset_id}/checkout", json_data=payload)
        if response and response.status_code in (200, 201):
            logger.info(f"Checkout successful: asset {asset_id} -> user {user_id}")
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
        
        logger.info(f"Checking in asset {asset_id}")
        
        response = self._request("POST", f"/hardware/{asset_id}/checkin", json_data=payload)
        if response and response.status_code in (200, 201):
            logger.info(f"Check-in successful: asset {asset_id}")
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
        logger.info("Fetching all models from Snipe-IT...")
        
        response = self._request("GET", "/models", params={"limit": limit})
        if not response:
            return []
        
        models = response.json().get("rows", [])
        logger.info(f"Retrieved {len(models)} models")
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
        
        logger.info(f"Creating model: {name}")
        
        response = self._request("POST", "/models", json_data=payload)
        if not response or response.status_code not in (200, 201):
            logger.error(f"Failed to create model: {name}")
            return None
        
        result = response.json()
        if result.get("status") == "success":
            model_id = result.get("payload", {}).get("id")
            logger.info(f"Created model '{name}' with ID={model_id}")
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
        logger.info("Fetching manufacturers from Snipe-IT...")
        
        response = self._request("GET", "/manufacturers", params={"limit": 500})
        if not response:
            return {}
        
        rows = response.json().get("rows", [])
        manufacturers = {
            row["name"].lower(): row["id"]
            for row in rows
            if "name" in row and "id" in row
        }
        
        logger.info(f"Retrieved {len(manufacturers)} manufacturers")
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
        
        logger.info(f"Creating manufacturer: {name}")
        
        response = self._request("POST", "/manufacturers", json_data=payload)
        if not response or response.status_code not in (200, 201):
            logger.error(f"Failed to create manufacturer: {name}")
            return None
        
        result = response.json()
        if result.get("status") == "success":
            mfr_id = result.get("payload", {}).get("id")
            logger.info(f"Created manufacturer '{name}' with ID={mfr_id}")
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
        response = self._request("GET", "/categories", params={"limit": 500})
        if not response:
            return []
        
        return response.json().get("rows", [])
    
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
    
    def close(self) -> None:
        """Close the session."""
        self.session.close()
