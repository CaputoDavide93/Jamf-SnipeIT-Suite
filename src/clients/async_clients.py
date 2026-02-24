"""
Async API Clients for Parallel Processing
High-performance async versions of API clients using aiohttp.
Enables concurrent API calls for faster bulk operations.
"""
import asyncio
import aiohttp
import logging
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

logger = logging.getLogger('jamf-snipeit-async')


@dataclass
class AsyncConfig:
    """Configuration for async operations."""
    max_concurrent: int = 10  # Max concurrent requests
    timeout_seconds: int = 30
    retry_attempts: int = 3
    retry_delay: float = 1.0
    rate_limit_per_second: float = 10.0  # Max requests per second


class RateLimiter:
    """Token bucket rate limiter for API calls."""
    
    def __init__(self, rate: float):
        """
        Initialize rate limiter.
        
        Args:
            rate: Maximum requests per second
        """
        self.rate = rate
        self.tokens = rate
        self.last_update = asyncio.get_event_loop().time() if asyncio.get_event_loop().is_running() else 0
        self._lock = asyncio.Lock()
    
    async def acquire(self):
        """Wait until a request token is available."""
        async with self._lock:
            now = asyncio.get_event_loop().time()
            elapsed = now - self.last_update
            self.tokens = min(self.rate, self.tokens + elapsed * self.rate)
            self.last_update = now
            
            if self.tokens < 1:
                wait_time = (1 - self.tokens) / self.rate
                await asyncio.sleep(wait_time)
                self.tokens = 0
            else:
                self.tokens -= 1


class AsyncJamfClient:
    """
    Async Jamf Pro API client for parallel operations.
    """
    
    def __init__(self, base_url: str, username: str, password: str,
                 config: Optional[AsyncConfig] = None):
        self.base_url = base_url.rstrip('/')
        self.username = username
        self.password = password
        self.config = config or AsyncConfig()
        
        self._token: Optional[str] = None
        self._token_expires: Optional[datetime] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._rate_limiter = RateLimiter(self.config.rate_limit_per_second)
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent)
    
    async def __aenter__(self):
        await self._create_session()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
    
    async def _create_session(self):
        """Create aiohttp session."""
        if self._session is None:
            timeout = aiohttp.ClientTimeout(total=self.config.timeout_seconds)
            self._session = aiohttp.ClientSession(timeout=timeout)
    
    async def close(self):
        """Close the session."""
        if self._session:
            await self._session.close()
            self._session = None
    
    async def _get_token(self) -> str:
        """Get or refresh authentication token."""
        if self._token and self._token_expires and datetime.now() < self._token_expires:
            return self._token
        
        await self._create_session()
        
        auth = aiohttp.BasicAuth(self.username, self.password)
        url = f"{self.base_url}/api/v1/auth/token"
        
        async with self._session.post(url, auth=auth) as response:
            response.raise_for_status()
            data = await response.json()
            self._token = data['token']
            self._token_expires = datetime.now() + timedelta(minutes=25)
            return self._token
    
    async def _request(self, method: str, endpoint: str, **kwargs) -> Dict:
        """Make an authenticated API request with rate limiting and retries."""
        await self._create_session()
        await self._rate_limiter.acquire()
        
        token = await self._get_token()
        headers = kwargs.pop('headers', {})
        headers['Authorization'] = f'Bearer {token}'
        headers.setdefault('Accept', 'application/json')
        
        url = f"{self.base_url}{endpoint}"
        
        for attempt in range(self.config.retry_attempts):
            try:
                async with self._semaphore:
                    async with self._session.request(method, url, headers=headers, **kwargs) as response:
                        if response.status == 401:
                            # Token expired, refresh and retry
                            self._token = None
                            token = await self._get_token()
                            headers['Authorization'] = f'Bearer {token}'
                            continue
                        
                        response.raise_for_status()
                        
                        if response.content_type == 'application/json':
                            return await response.json()
                        return {'status': 'success', 'text': await response.text()}
            
            except aiohttp.ClientError as e:
                if attempt < self.config.retry_attempts - 1:
                    await asyncio.sleep(self.config.retry_delay * (attempt + 1))
                    continue
                raise
        
        return {}
    
    async def get_computer(self, computer_id: int) -> Dict:
        """Get a single computer by ID."""
        return await self._request('GET', f'/JSSResource/computers/id/{computer_id}')
    
    async def get_computers_batch(self, computer_ids: List[int]) -> List[Dict]:
        """Get multiple computers concurrently."""
        tasks = [self.get_computer(cid) for cid in computer_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Filter out exceptions
        return [r for r in results if isinstance(r, dict)]
    
    async def get_all_computers_basic(self) -> List[Dict]:
        """Get basic info for all computers."""
        data = await self._request('GET', '/JSSResource/computers')
        return data.get('computers', [])
    
    async def send_mdm_command_batch(self, computer_ids: List[int], command: str = "RedeployMDM") -> Dict[int, bool]:
        """
        Send MDM command to multiple computers concurrently.
        
        Returns:
            Dict mapping computer_id to success status
        """
        async def send_command(computer_id: int) -> tuple:
            try:
                await self._request(
                    'POST',
                    f'/JSSResource/computercommands/command/{command}/id/{computer_id}'
                )
                return (computer_id, True)
            except Exception as e:
                logger.warning(f"Failed to send {command} to {computer_id}: {e}")
                return (computer_id, False)
        
        tasks = [send_command(cid) for cid in computer_ids]
        results = await asyncio.gather(*tasks)
        
        return dict(results)


class AsyncSnipeClient:
    """
    Async Snipe-IT API client for parallel operations.
    """
    
    def __init__(self, base_url: str, api_token: str,
                 config: Optional[AsyncConfig] = None):
        self.base_url = base_url.rstrip('/')
        self.api_token = api_token
        self.config = config or AsyncConfig()
        
        self._session: Optional[aiohttp.ClientSession] = None
        self._rate_limiter = RateLimiter(self.config.rate_limit_per_second)
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent)
    
    async def __aenter__(self):
        await self._create_session()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
    
    async def _create_session(self):
        """Create aiohttp session."""
        if self._session is None:
            timeout = aiohttp.ClientTimeout(total=self.config.timeout_seconds)
            headers = {
                'Authorization': f'Bearer {self.api_token}',
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            }
            self._session = aiohttp.ClientSession(timeout=timeout, headers=headers)
    
    async def close(self):
        """Close the session."""
        if self._session:
            await self._session.close()
            self._session = None
    
    async def _request(self, method: str, endpoint: str, **kwargs) -> Dict:
        """Make an API request with rate limiting and retries."""
        await self._create_session()
        await self._rate_limiter.acquire()
        
        url = f"{self.base_url}/api/v1{endpoint}"
        
        for attempt in range(self.config.retry_attempts):
            try:
                async with self._semaphore:
                    async with self._session.request(method, url, **kwargs) as response:
                        response.raise_for_status()
                        return await response.json()
            
            except aiohttp.ClientError as e:
                if attempt < self.config.retry_attempts - 1:
                    await asyncio.sleep(self.config.retry_delay * (attempt + 1))
                    continue
                raise
        
        return {}
    
    async def get_asset(self, asset_id: int) -> Dict:
        """Get a single asset by ID."""
        data = await self._request('GET', f'/hardware/{asset_id}')
        return data
    
    async def get_assets_batch(self, asset_ids: List[int]) -> List[Dict]:
        """Get multiple assets concurrently."""
        tasks = [self.get_asset(aid) for aid in asset_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        return [r for r in results if isinstance(r, dict)]
    
    async def get_all_assets(self, limit: int = 500) -> List[Dict]:
        """Get all assets with pagination."""
        all_assets = []
        offset = 0
        
        while True:
            data = await self._request('GET', f'/hardware?limit={limit}&offset={offset}')
            rows = data.get('rows', [])
            
            if not rows:
                break
            
            all_assets.extend(rows)
            
            if len(rows) < limit:
                break
            
            offset += limit
        
        return all_assets
    
    async def update_asset(self, asset_id: int, data: Dict) -> Dict:
        """Update an asset."""
        return await self._request('PATCH', f'/hardware/{asset_id}', json=data)
    
    async def update_assets_batch(self, updates: List[Dict]) -> List[Dict]:
        """
        Update multiple assets concurrently.
        
        Args:
            updates: List of dicts with 'id' and update fields
        
        Returns:
            List of response dicts
        """
        async def do_update(update: Dict) -> Dict:
            asset_id = update.pop('id')
            try:
                result = await self.update_asset(asset_id, update)
                return {'id': asset_id, 'success': True, 'result': result}
            except Exception as e:
                return {'id': asset_id, 'success': False, 'error': str(e)}
        
        tasks = [do_update(u.copy()) for u in updates]
        return await asyncio.gather(*tasks)
    
    async def get_all_users(self, limit: int = 500) -> List[Dict]:
        """Get all users with pagination."""
        all_users = []
        offset = 0
        
        while True:
            data = await self._request('GET', f'/users?limit={limit}&offset={offset}')
            rows = data.get('rows', [])
            
            if not rows:
                break
            
            all_users.extend(rows)
            
            if len(rows) < limit:
                break
            
            offset += limit
        
        return all_users


class ParallelProcessor:
    """
    High-level parallel processor for bulk operations.
    Coordinates async operations across multiple API clients.
    """
    
    def __init__(self, jamf_client: AsyncJamfClient, snipe_client: AsyncSnipeClient):
        self.jamf = jamf_client
        self.snipe = snipe_client
    
    async def fetch_all_inventory(self) -> Dict[str, List[Dict]]:
        """
        Fetch all inventory from both systems concurrently.
        
        Returns:
            Dict with 'jamf' and 'snipe' keys containing device lists
        """
        logger.info("Fetching inventory from both systems concurrently...")
        
        jamf_task = self.jamf.get_all_computers_basic()
        snipe_task = self.snipe.get_all_assets()
        
        jamf_computers, snipe_assets = await asyncio.gather(jamf_task, snipe_task)
        
        logger.info(f"Fetched {len(jamf_computers)} Jamf computers, {len(snipe_assets)} Snipe assets")
        
        return {
            'jamf': jamf_computers,
            'snipe': snipe_assets
        }
    
    async def batch_wake_devices(self, serial_numbers: List[str], 
                                  serial_to_id_map: Dict[str, int]) -> Dict[str, bool]:
        """
        Send wake commands to multiple devices.
        
        Args:
            serial_numbers: List of serial numbers to wake
            serial_to_id_map: Mapping of serial to Jamf computer ID
        
        Returns:
            Dict mapping serial to success status
        """
        computer_ids = [
            serial_to_id_map[serial] 
            for serial in serial_numbers 
            if serial in serial_to_id_map
        ]
        
        id_results = await self.jamf.send_mdm_command_batch(computer_ids)
        
        # Convert back to serial numbers
        id_to_serial = {v: k for k, v in serial_to_id_map.items()}
        return {
            id_to_serial.get(cid, str(cid)): success
            for cid, success in id_results.items()
        }


async def run_parallel_reconciliation(jamf_url: str, jamf_user: str, jamf_pass: str,
                                       snipe_url: str, snipe_token: str) -> Dict:
    """
    Run inventory reconciliation using parallel API calls.
    
    Returns:
        Dict with reconciliation results
    """
    config = AsyncConfig(max_concurrent=20, rate_limit_per_second=15)
    
    async with AsyncJamfClient(jamf_url, jamf_user, jamf_pass, config) as jamf:
        async with AsyncSnipeClient(snipe_url, snipe_token, config) as snipe:
            processor = ParallelProcessor(jamf, snipe)
            
            print("🚀 Fetching inventory in parallel...")
            start = asyncio.get_event_loop().time()
            
            inventory = await processor.fetch_all_inventory()
            
            elapsed = asyncio.get_event_loop().time() - start
            print(f"✅ Fetched all inventory in {elapsed:.2f} seconds")
            
            # Build serial number sets
            jamf_serials = {
                c.get('serial_number', c.get('serialNumber', '')).upper() 
                for c in inventory['jamf'] 
                if c.get('serial_number') or c.get('serialNumber')
            }
            
            snipe_serials = {
                a.get('serial', '').upper() 
                for a in inventory['snipe'] 
                if a.get('serial')
            }
            
            return {
                'total_jamf': len(inventory['jamf']),
                'total_snipe': len(inventory['snipe']),
                'jamf_only': list(jamf_serials - snipe_serials),
                'snipe_only': list(snipe_serials - jamf_serials),
                'matched': list(jamf_serials & snipe_serials),
                'fetch_time_seconds': elapsed
            }


def run_async(coro):
    """Helper to run async code from sync context."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're already in an async context
            return asyncio.ensure_future(coro)
        return loop.run_until_complete(coro)
    except RuntimeError:
        # No event loop, create one
        return asyncio.run(coro)


if __name__ == "__main__":
    # Example usage
    async def test():
        print("Testing async clients...")
        
        # Would use real credentials in practice
        config = AsyncConfig(max_concurrent=5, rate_limit_per_second=5)
        
        # Test rate limiter
        limiter = RateLimiter(2.0)  # 2 requests per second
        
        import time
        start = time.time()
        for i in range(5):
            await limiter.acquire()
            print(f"Request {i+1} at {time.time() - start:.2f}s")
        
        print("\nRate limiter test complete!")
    
    asyncio.run(test())
