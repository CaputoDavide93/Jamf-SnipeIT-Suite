"""
Jamf-SnipeIT Suite - Azure AD / Microsoft Graph API Client
Used by the Leavers module to fetch disabled users.
"""
import logging
import time
import requests
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Optional: Use MSAL if available for better token management
try:
    import msal
    HAS_MSAL = True
except ImportError:
    HAS_MSAL = False
    logger.debug("MSAL not installed, using basic token acquisition")


class AzureClient:
    """
    Azure AD / Microsoft Graph API client for fetching user information.
    """
    
    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        scope: str = "https://graph.microsoft.com/.default",
        timeout: int = 30,
        max_retries: int = 3,
        retry_delay: int = 2,
    ):
        """
        Initialize Azure AD client.
        
        Args:
            tenant_id: Azure AD tenant ID
            client_id: Application (client) ID
            client_secret: Client secret
            scope: OAuth2 scope
            timeout: Request timeout in seconds
            max_retries: Maximum retry attempts for failed requests
            retry_delay: Initial delay between retries (exponential backoff)
        """
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.scope = scope
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        
        self.authority = f"https://login.microsoftonline.com/{tenant_id}"
        self.session = requests.Session()
        
        self._token: Optional[str] = None
        self._token_exp: float = 0
        self._msal_app: Optional[Any] = None
        
        # Initialize MSAL app if available
        if HAS_MSAL:
            self._msal_app = msal.ConfidentialClientApplication(
                client_id=client_id,
                client_credential=client_secret,
                authority=self.authority,
            )
    
    # =========================================================================
    # Request Helper with Retry Logic
    # =========================================================================
    
    def _request_with_retry(
        self,
        method: str,
        url: str,
        params: Optional[Dict] = None,
        json_data: Optional[Dict] = None,
    ) -> requests.Response:
        """
        Make a Graph API request with retry logic for transient failures.
        
        Args:
            method: HTTP method
            url: Full URL to request
            params: Query parameters
            json_data: JSON body data
        
        Returns:
            Response object
        
        Raises:
            RuntimeError: If all retries fail
        """
        last_error = None
        
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.request(
                    method,
                    url,
                    headers=self._get_headers(),
                    params=params,
                    json=json_data,
                    timeout=self.timeout,
                )
                
                # Handle rate limiting (429)
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", self.retry_delay * attempt))
                    logger.warning(f"Rate limited by Graph API. Waiting {retry_after}s (attempt {attempt})")
                    time.sleep(retry_after)
                    continue
                
                # Handle server errors (5xx) with retry
                if response.status_code >= 500:
                    delay = self.retry_delay * (2 ** (attempt - 1))
                    logger.warning(f"Graph API server error {response.status_code}. Retrying in {delay}s (attempt {attempt})")
                    time.sleep(delay)
                    continue
                
                # Return response for all other status codes (let caller handle)
                return response
                
            except requests.exceptions.RequestException as e:
                last_error = e
                if attempt < self.max_retries:
                    delay = self.retry_delay * (2 ** (attempt - 1))
                    logger.warning(f"Graph API request failed: {e}. Retrying in {delay}s...")
                    time.sleep(delay)
                else:
                    logger.error(f"Graph API request failed after {self.max_retries} attempts")
        
        raise RuntimeError(f"Graph API request failed after {self.max_retries} retries: {last_error}")
    
    # =========================================================================
    # Authentication
    # =========================================================================
    
    def _get_token(self) -> str:
        """Get or refresh access token."""
        now = time.time()
        
        # Return cached token if valid
        if self._token and now < self._token_exp - 60:
            return self._token
        
        if HAS_MSAL and self._msal_app:
            return self._get_token_msal()
        else:
            return self._get_token_requests()
    
    def _get_token_msal(self) -> str:
        """Get token using MSAL library."""
        # Try silent first
        result = self._msal_app.acquire_token_silent([self.scope], account=None)
        
        if not result:
            result = self._msal_app.acquire_token_for_client(scopes=[self.scope])
        
        if "access_token" not in result:
            error = result.get("error_description", "Unknown error")
            raise RuntimeError(f"Failed to acquire Azure token: {error}")
        
        self._token = result["access_token"]
        self._token_exp = time.time() + result.get("expires_in", 3600) - 60
        
        logger.debug("Acquired Azure token via MSAL")
        return self._token
    
    def _get_token_requests(self) -> str:
        """Get token using direct HTTP request."""
        url = f"{self.authority}/oauth2/v2.0/token"
        
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": self.scope,
            "grant_type": "client_credentials",
        }
        
        response = self.session.post(url, data=data, timeout=self.timeout)
        response.raise_for_status()
        
        result = response.json()
        if "access_token" not in result:
            raise RuntimeError("No access_token in Azure response")
        
        self._token = result["access_token"]
        self._token_exp = time.time() + result.get("expires_in", 3600) - 60
        
        logger.debug("Acquired Azure token via HTTP")
        return self._token
    
    def _get_headers(self) -> Dict[str, str]:
        """Get request headers with authorization."""
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Accept": "application/json",
            "ConsistencyLevel": "eventual",  # Required for advanced queries
        }
    
    # =========================================================================
    # User Operations
    # =========================================================================
    
    def get_disabled_users(self, filter_clause: str = "accountEnabled eq false") -> List[Dict[str, Any]]:
        """
        Fetch disabled users from Azure AD.
        
        Args:
            filter_clause: OData filter for users
        
        Returns:
            List of user dictionaries
        """
        logger.debug(f"Fetching disabled users with filter: {filter_clause}")
        
        base_url = "https://graph.microsoft.com/v1.0/users"
        params = {
            "$filter": filter_clause,
            "$select": "id,displayName,mail,userPrincipalName,accountEnabled",
            "$count": "true",
        }
        
        users: List[Dict[str, Any]] = []
        url = base_url
        
        while url:
            response = self._request_with_retry(
                "GET",
                url,
                params=params if url == base_url else None,
            )
            
            if response.status_code != 200:
                try:
                    error = response.json()
                except ValueError:
                    error = response.text
                raise RuntimeError(f"Graph API request failed: {error}")
            
            data = response.json()
            batch = data.get("value", [])
            users.extend(batch)
            
            # Handle pagination
            url = data.get("@odata.nextLink")
            params = None  # Next link includes params
        
        logger.debug(f"Retrieved {len(users)} disabled users")
        return users
    
    def get_all_active_users(self) -> List[Dict[str, Any]]:
        """
        Fetch ALL active users from Azure AD (not just a specific group).
        Returns displayName, mail, userPrincipalName, jobTitle, department.
        """
        logger.debug("Fetching all active Azure AD users...")

        base_url = "https://graph.microsoft.com/v1.0/users"
        params = {
            "$filter": "accountEnabled eq true",
            "$select": "id,displayName,mail,userPrincipalName,jobTitle,department,accountEnabled,givenName,surname",
            "$top": "999",
        }

        users: List[Dict[str, Any]] = []
        url = base_url
        while url:
            response = self._request_with_retry("GET", url, params=params if url == base_url else None)
            if response.status_code != 200:
                try:
                    error = response.json()
                except ValueError:
                    error = response.text
                raise RuntimeError(f"Graph API request failed: {error}")
            data = response.json()
            users.extend(data.get("value", []))
            url = data.get("@odata.nextLink")
            params = None

        logger.debug(f"Retrieved {len(users)} active Azure AD users")
        return users

    def get_group_members(self, group_id: str) -> List[Dict[str, Any]]:
        """
        Fetch members of an Azure AD group.
        
        Args:
            group_id: Azure AD group ID
        
        Returns:
            List of user dictionaries (only user objects, not groups/devices)
        """
        logger.debug(f"Fetching members of group: {group_id}")
        
        base_url = f"https://graph.microsoft.com/v1.0/groups/{group_id}/members"
        params = {
            "$select": "id,displayName,mail,userPrincipalName,accountEnabled,givenName,surname,jobTitle,department,companyName",
        }
        
        members: List[Dict[str, Any]] = []
        url = base_url
        
        while url:
            response = self._request_with_retry(
                "GET",
                url,
                params=params if url == base_url else None,
            )
            
            if response.status_code != 200:
                try:
                    error = response.json()
                except ValueError:
                    error = response.text
                raise RuntimeError(f"Graph API group request failed: {error}")
            
            data = response.json()
            batch = data.get("value", [])
            
            # Filter to only user objects
            for entry in batch:
                if entry.get("@odata.type") == "#microsoft.graph.user":
                    members.append(entry)
            
            # Handle pagination
            url = data.get("@odata.nextLink")
            params = None
        
        logger.debug(f"Retrieved {len(members)} users from group")
        return members
    
    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Get user details by ID.
        
        Args:
            user_id: Azure AD user ID
        
        Returns:
            User dictionary or None
        """
        url = f"https://graph.microsoft.com/v1.0/users/{user_id}"
        params = {
            "$select": "id,displayName,mail,userPrincipalName,accountEnabled,jobTitle,department",
        }
        
        response = self._request_with_retry("GET", url, params=params)
        
        if response.status_code == 404:
            return None
        
        response.raise_for_status()
        return response.json()
    
    # =========================================================================
    # Utility Methods
    # =========================================================================
    
    @staticmethod
    def extract_email(user: Dict[str, Any]) -> Optional[str]:
        """
        Extract email from user object.
        Falls back to userPrincipalName if mail is not set.
        
        Args:
            user: Azure AD user dictionary
        
        Returns:
            Email address or None
        """
        email = user.get("mail") or user.get("userPrincipalName")
        if email:
            return email.strip().lower()
        return None
    
    def close(self) -> None:
        """Close the session."""
        self.session.close()

    def ping(self) -> bool:
        """Quick connectivity check — fetch a single user from Graph."""
        try:
            token = self._get_token()
            resp = self.session.get(
                "https://graph.microsoft.com/v1.0/users?$top=1&$select=id",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            return resp.status_code == 200
        except Exception:
            return False
