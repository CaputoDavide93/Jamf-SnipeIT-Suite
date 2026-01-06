"""
Jamf-SnipeIT Suite - Unified Jamf Pro API Client
Consolidates all Jamf API functionality from multiple scripts.
"""
import logging
import re
import time
import requests
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape as xml_escape

logger = logging.getLogger(__name__)


def safe_xml_text(value: Optional[str]) -> str:
    """
    Safely escape a string for use in XML text content.
    Handles None, escapes special characters, and removes invalid XML characters.
    
    Args:
        value: String to escape, or None
    
    Returns:
        XML-safe string
    """
    if value is None:
        return ""
    
    # Convert to string if not already
    text = str(value)
    
    # Remove invalid XML characters (control chars except tab, newline, carriage return)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    
    # Use standard XML escaping for <, >, &, ", '
    text = xml_escape(text, entities={'"': '&quot;', "'": '&apos;'})
    
    return text


class JamfClient:
    """
    Unified Jamf Pro API client with token management, retry logic,
    and all operations needed by the suite modules.
    """
    
    def __init__(
        self,
        base_url: str,
        username: str = "",
        password: str = "",
        client_id: str = "",
        client_secret: str = "",
        timeout: int = 30,
        max_retries: int = 5,
        retry_delay: int = 3,
    ):
        """
        Initialize Jamf Pro API client.
        
        Args:
            base_url: Jamf Pro URL (e.g., https://company.jamfcloud.com)
            username: Username for basic auth
            password: Password for basic auth
            client_id: OAuth2 client ID (alternative to username/password)
            client_secret: OAuth2 client secret
            timeout: Request timeout in seconds
            max_retries: Maximum retry attempts for failed requests
            retry_delay: Initial delay between retries (exponential backoff)
        """
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.client_id = client_id
        self.client_secret = client_secret
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        
        self.session = requests.Session()
        self._token: Optional[str] = None
        self._token_exp: float = 0
    
    # =========================================================================
    # Authentication
    # =========================================================================
    
    def _get_token(self) -> str:
        """
        Get or refresh authentication token.
        Tries multiple token endpoints for compatibility.
        """
        now = time.time()
        
        # Return cached token if still valid
        if self._token and now < self._token_exp - 30:
            return self._token
        
        # Try multiple token endpoints
        endpoints = [
            "/api/v1/auth/token",
            "/api/auth/tokens",
            "/uapi/auth/tokens",
        ]
        
        for path in endpoints:
            url = f"{self.base_url}{path}"
            headers = {}
            auth = None
            data = None
            
            # Username/password auth
            if self.username and self.password:
                auth = (self.username, self.password)
            # OAuth2 client credentials
            elif self.client_id and self.client_secret:
                headers["Content-Type"] = "application/x-www-form-urlencoded"
                data = {
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type": "client_credentials",
                }
            else:
                raise RuntimeError("No valid authentication credentials provided")
            
            try:
                logger.debug(f"Attempting token auth at {path}")
                response = self.session.post(
                    url, auth=auth, headers=headers, data=data, timeout=self.timeout
                )
                
                if response.status_code in (200, 201):
                    result = response.json()
                    token = result.get("token") or result.get("access_token")
                    expires_in = result.get("expires_in", 1800)
                    
                    if token:
                        self._token = token
                        self._token_exp = now + int(expires_in) - 30
                        logger.debug(f"Successfully obtained token from {path}")
                        return token
            except Exception as e:
                logger.debug(f"Token endpoint {path} failed: {e}")
        
        raise RuntimeError(
            f"Failed to obtain Jamf API token. Check credentials and base URL: {self.base_url}"
        )
    
    def _get_headers(self, content_type: str = "application/json") -> Dict[str, str]:
        """Get request headers with authorization."""
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Accept": "application/json",
            "Content-Type": content_type,
        }
    
    # =========================================================================
    # Request Helpers with Retry Logic
    # =========================================================================
    
    def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        json_data: Optional[Dict] = None,
        xml_data: Optional[bytes] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Optional[requests.Response]:
        """
        Make an API request with retry logic and token refresh.
        
        Args:
            method: HTTP method (GET, POST, PUT, PATCH, DELETE)
            endpoint: API endpoint path
            params: Query parameters
            json_data: JSON body data
            xml_data: XML body data (for Classic API)
            headers: Optional custom headers
        
        Returns:
            Response object or None on failure
        """
        url = f"{self.base_url}{endpoint}"
        
        if headers is None:
            content_type = "application/xml" if xml_data else "application/json"
            headers = self._get_headers(content_type)
        
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=json_data,
                    data=xml_data,
                    timeout=self.timeout,
                )
                
                # Handle 401 - token expired
                if response.status_code == 401:
                    logger.warning(f"401 Unauthorized (attempt {attempt}). Refreshing token...")
                    self._token = None
                    headers["Authorization"] = f"Bearer {self._get_token()}"
                    continue
                
                # Handle 429 - rate limit
                if response.status_code == 429:
                    delay = self.retry_delay * (2 ** (attempt - 1))
                    logger.warning(
                        f"429 Rate limit (attempt {attempt}/{self.max_retries}). "
                        f"Retrying in {delay}s..."
                    )
                    time.sleep(delay)
                    continue
                
                # Handle 409 - Conflict (resource locked, concurrent modification)
                # This is common in Jamf Classic API when records are being updated elsewhere
                # or when the device is actively checking in
                if response.status_code == 409:
                    # Log the actual response body to understand the error
                    logger.debug(f"409 Response body: {response.text}")
                    if attempt < self.max_retries:
                        # Use jitter to avoid thundering herd - random delay 1-5 seconds
                        import random
                        jitter = random.uniform(1, 5)
                        delay = (self.retry_delay * (2 ** (attempt - 1))) + jitter
                        logger.warning(
                            f"409 Conflict (attempt {attempt}/{self.max_retries}). "
                            f"Resource may be locked by device check-in. Retrying in {delay:.1f}s..."
                        )
                        time.sleep(delay)
                        continue
                    else:
                        logger.warning(
                            f"409 Conflict persists for {url}. "
                            "Device may be actively checking in. Skipping this update."
                        )
                        return None
                
                response.raise_for_status()
                return response
                
            except requests.exceptions.RequestException as e:
                logger.error(f"Request error ({url}): {e}")
                if attempt < self.max_retries:
                    delay = self.retry_delay * (2 ** (attempt - 1))
                    logger.info(f"Retrying in {delay}s...")
                    time.sleep(delay)
                else:
                    logger.error(f"Exhausted retries for {url}")
                    return None
        
        return None
    
    # =========================================================================
    # Computer Operations (Read)
    # =========================================================================
    
    def get_all_computers_basic(self) -> List[Dict[str, Any]]:
        """
        Get all computers with basic info (ID, serial number).
        Uses Classic API subset=basic.
        """
        logger.info("Fetching all computers (basic subset)...")
        
        response = self._request("GET", "/JSSResource/computers/subset/basic")
        if not response:
            return []
        
        data = response.json()
        computers = data.get("computers", [])
        
        results = []
        for item in computers:
            jid = item.get("id")
            serial = item.get("serial_number", "").strip()
            if jid and serial:
                results.append({"id": jid, "serial_number": serial})
        
        logger.info(f"Retrieved {len(results)} computers")
        return results
    
    def get_computer_by_id(
        self, computer_id: int, subsets: Optional[List[str]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Get full computer details by ID.
        
        Args:
            computer_id: Jamf computer ID
            subsets: List of subsets to retrieve (default: all common ones)
        
        Returns:
            Computer data dictionary
        """
        if subsets is None:
            subsets = ["General", "Location", "Hardware", "GroupsAccounts", "Accounts"]
        
        subset_str = ",".join(subsets)
        endpoint = f"/JSSResource/computers/id/{computer_id}/subset/{subset_str}"
        
        response = self._request("GET", endpoint)
        if not response:
            return None
        
        return response.json()
    
    def get_computer_by_serial(self, serial_number: str) -> Optional[Dict[str, Any]]:
        """
        Get computer details by serial number.
        
        Args:
            serial_number: Computer serial number
        
        Returns:
            Computer data dictionary
        """
        logger.debug(f"Looking up computer by serial: {serial_number}")
        
        endpoint = f"/JSSResource/computers/serialnumber/{serial_number}"
        response = self._request("GET", endpoint)
        
        if not response:
            return None
        
        data = response.json()
        return data.get("computer")
    
    def get_smart_group(self, group_name: str) -> Dict[str, Any]:
        """
        Get smart group details by name.
        
        Args:
            group_name: Smart group name
        
        Returns:
            Smart group data dictionary
        """
        endpoint = f"/JSSResource/computergroups/name/{group_name}"
        response = self._request("GET", endpoint)
        
        if not response:
            raise RuntimeError(f"Failed to fetch smart group: {group_name}")
        
        return response.json()
    
    def get_computers_in_smart_group(self, group_name: str) -> List[Dict[str, Any]]:
        """
        Get all computers in a smart group.
        
        Args:
            group_name: Smart group name
        
        Returns:
            List of computer dictionaries
        """
        logger.info(f"Fetching computers from smart group: {group_name}")
        
        data = self.get_smart_group(group_name)
        computers = data.get("computer_group", {}).get("computers", []) or []
        
        logger.info(f"Found {len(computers)} computers in smart group")
        return computers
    
    def get_dynamic_group_by_id(self, group_id: str) -> List[Dict[str, Any]]:
        """
        Get computers in a dynamic group by ID with detailed info.
        
        Args:
            group_id: Dynamic group ID
        
        Returns:
            List of computer dictionaries with extended details
        """
        logger.info(f"Fetching dynamic group: {group_id}")
        
        response = self._request("GET", f"/JSSResource/computergroups/id/{group_id}")
        if not response:
            return []
        
        data = response.json()
        computers = data.get("computer_group", {}).get("computers", [])
        
        logger.info(f"Found {len(computers)} computers in dynamic group")
        return computers
    
    # =========================================================================
    # Computer Operations (Write)
    # =========================================================================
    
    def update_computer_location(
        self,
        computer_id: int,
        username: str = "",
        realname: str = "",
        email: str = "",
        position: str = "",
        department: str = "",
        dry_run: bool = False,
    ) -> bool:
        """
        Update computer location/user information.
        
        Args:
            computer_id: Jamf computer ID
            username: Username
            realname: Real name / full name
            email: Email address
            position: Job title / position
            department: Department (stored in Jamf's 'room' field since department is pre-defined)
            dry_run: If True, don't actually update
        
        Returns:
            True if successful
        """
        # Build XML dynamically - only include non-empty fields
        # Note: We use 'room' field for department since Jamf's department is a pre-defined dropdown
        location_fields = []
        if username:
            location_fields.append(f"    <username>{safe_xml_text(username)}</username>")
        if realname:
            location_fields.append(f"    <real_name>{safe_xml_text(realname)}</real_name>")
        if email:
            location_fields.append(f"    <email_address>{safe_xml_text(email)}</email_address>")
        if position:
            location_fields.append(f"    <position>{safe_xml_text(position)}</position>")
        if department:
            location_fields.append(f"    <room>{safe_xml_text(department)}</room>")
        
        xml = f"""<computer>
  <location>
{chr(10).join(location_fields)}
  </location>
</computer>"""
        
        logger.debug(f"Update XML for computer {computer_id}: {xml}")
        
        if dry_run:
            logger.info(
                f"[DRY-RUN] Would update computer {computer_id}: "
                f"username={username}, realname={realname}, email={email}"
            )
            return True
        
        endpoint = f"/JSSResource/computers/id/{computer_id}"
        response = self._request("PUT", endpoint, xml_data=xml.encode("utf-8"))
        
        if response and response.status_code in (200, 201):
            logger.info(f"Updated computer {computer_id} location info")
            return True
        
        logger.error(f"Failed to update computer {computer_id}")
        return False
    
    def update_computer_location_and_ea(
        self,
        computer_id: int,
        username: str = "",
        realname: str = "",
        email: str = "",
        position: str = "",
        ea_name: str = "",
        ea_value: str = "",
        dry_run: bool = False,
    ) -> bool:
        """
        Update computer location AND an extension attribute.
        
        Args:
            computer_id: Jamf computer ID
            username: Username
            realname: Real name / full name
            email: Email address
            position: Job title / position
            ea_name: Extension attribute name
            ea_value: Extension attribute value
            dry_run: If True, don't actually update
        
        Returns:
            True if successful
        """
        xml = f"""<computer>
  <location>
    <username>{safe_xml_text(username)}</username>
    <real_name>{safe_xml_text(realname)}</real_name>
    <email_address>{safe_xml_text(email)}</email_address>
    <position>{safe_xml_text(position)}</position>
  </location>
  <extension_attributes>
    <extension_attribute>
      <name>{safe_xml_text(ea_name)}</name>
      <value>{safe_xml_text(ea_value)}</value>
    </extension_attribute>
  </extension_attributes>
</computer>"""
        
        if dry_run:
            logger.info(
                f"[DRY-RUN] Would update computer {computer_id}: "
                f"username={username}, EA {ea_name}={ea_value}"
            )
            return True
        
        endpoint = f"/JSSResource/computers/id/{computer_id}"
        response = self._request("PUT", endpoint, xml_data=xml.encode("utf-8"))
        
        if response and response.status_code in (200, 201):
            logger.info(f"Updated computer {computer_id} with EA {ea_name}={ea_value}")
            return True
        
        logger.error(f"Failed to update computer {computer_id}")
        return False
    
    # =========================================================================
    # MDM Commands (Wake-Up / Redeploy)
    # =========================================================================
    
    def redeploy_management_framework(self, computer_id: int) -> Optional[Dict[str, Any]]:
        """
        Send redeploy (wake-up) command to a computer.
        
        Args:
            computer_id: Jamf computer ID
        
        Returns:
            Response data with commandUuid, or None on failure
        """
        logger.info(f"Sending redeploy command to computer {computer_id}")
        
        endpoint = f"/api/v1/jamf-management-framework/redeploy/{computer_id}"
        response = self._request("POST", endpoint)
        
        if response:
            result = response.json()
            logger.info(f"Redeploy command queued. UUID: {result.get('commandUuid')}")
            return result
        
        logger.error(f"Failed to send redeploy command to computer {computer_id}")
        return None
    
    def redeploy_batch(
        self, computer_ids: List[int], stop_on_error: bool = False
    ) -> Dict[str, Any]:
        """
        Send redeploy command to multiple computers.
        
        Args:
            computer_ids: List of computer IDs
            stop_on_error: Stop processing on first error
        
        Returns:
            Results dictionary with success/failure counts
        """
        results = {
            "total": len(computer_ids),
            "successful": 0,
            "failed": 0,
            "details": [],
        }
        
        for computer_id in computer_ids:
            try:
                result = self.redeploy_management_framework(computer_id)
                if result:
                    results["successful"] += 1
                    results["details"].append({
                        "computer_id": computer_id,
                        "status": "success",
                        "command_uuid": result.get("commandUuid"),
                    })
                else:
                    raise RuntimeError("No response received")
            except Exception as e:
                results["failed"] += 1
                results["details"].append({
                    "computer_id": computer_id,
                    "status": "failed",
                    "error": str(e),
                })
                if stop_on_error:
                    logger.error("Stopping batch due to error")
                    break
        
        logger.info(
            f"Batch complete: {results['successful']} successful, {results['failed']} failed"
        )
        return results
    
    # =========================================================================
    # Utility Methods
    # =========================================================================
    
    def extract_user_info(self, computer_data: Dict[str, Any]) -> Dict[str, Optional[str]]:
        """
        Extract user/location info from computer data.
        Handles multiple API response formats.
        
        Args:
            computer_data: Computer dictionary from API
        
        Returns:
            Dictionary with username, full_name, email, position
        """
        user_info = {
            "username": None,
            "full_name": None,
            "email": None,
            "position": None,
        }
        
        # Try v2 API format
        if "userAndLocation" in computer_data:
            loc = computer_data["userAndLocation"]
            user_info["username"] = loc.get("username")
            user_info["full_name"] = loc.get("realName")
            user_info["email"] = loc.get("email")
            user_info["position"] = loc.get("position")
        
        # Try Classic API format
        elif "location" in computer_data:
            loc = computer_data["location"]
            user_info["username"] = loc.get("username")
            user_info["full_name"] = (
                loc.get("real_name") or loc.get("realName") or loc.get("realname")
            )
            user_info["email"] = loc.get("email_address") or loc.get("email")
            user_info["position"] = loc.get("position")
        
        return user_info
    
    def close(self) -> None:
        """Close the session."""
        self.session.close()
