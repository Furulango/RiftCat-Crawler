#!/usr/bin/env python3
"""
Simplified script to test the League of Legends crawler focused on ranked matches.
It only searches for ranked solo queue (420) and flex queue (440) matches.

Usage:
    python test_crawler_ranked.py --seed-players "name1#tag1:region1" "name2#tag2:region2"
        --api-key "RGAPI-xxxx" --duration 10 --max-depth 2 --match-limit 5
"""

import os
import sys
import asyncio
import argparse
import time
import json
from typing import Dict, List, Set, Any, Optional, Tuple
from datetime import datetime, timedelta
from enum import Enum
import logging
import httpx
from httpx import AsyncClient, HTTPStatusError, RequestError

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("crawler_ranked")

# Additional configuration to see HTTP request details
logging.getLogger("httpx").setLevel(logging.INFO)

# Mapping of regions to platforms
REGION_TO_PLATFORM = {
    "na1": "americas",
    "br1": "americas",
    "la1": "americas",
    "la2": "americas",
    "euw1": "europe",
    "eun1": "europe",
    "tr1": "europe",
    "ru": "europe",
    "kr": "asia",
    "jp1": "asia",
    "oc1": "sea",
    "ph2": "sea",
    "sg2": "sea",
    "th2": "sea",
    "tw2": "sea",
    "vn2": "sea",
}

# APIs that use regional platform URLs
PLATFORM_APIS = ["match/v5", "riot/account/v1"]

# Ranked queue types
RANKED_QUEUE_TYPES = {
    420: "RANKED_SOLO_DUO",
    440: "RANKED_FLEX"
}


# Enumeration for processing status
class ProcessingStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


# Class to limit API request rate
class RateLimiter:
    """Limits API requests to avoid exceeding rate limits."""
    
    def __init__(self, requests_per_second: int = 20, requests_per_minute: int = 100):
        self.requests_per_second = requests_per_second
        self.requests_per_minute = requests_per_minute
        
        # Semaphores to control concurrency
        self.second_semaphore = asyncio.Semaphore(requests_per_second)
        self.minute_semaphore = asyncio.Semaphore(requests_per_minute)
        
        # Request history
        self.request_timestamps: List[float] = []
        
        # Lock for safe access to request_timestamps
        self.lock = asyncio.Lock()
    
    async def acquire(self) -> None:
        """Acquires a token before making a request."""
        # Per-second limitation
        await self.second_semaphore.acquire()
        
        # Per-minute limitation
        await self.minute_semaphore.acquire()
        
        # Record the current timestamp
        current_time = time.time()
        async with self.lock:
            self.request_timestamps.append(current_time)
            
            # Clean up old timestamps (> 1 minute)
            self.request_timestamps = [t for t in self.request_timestamps 
                                      if current_time - t < 60]
    
    def release(self) -> None:
        """Releases a token after a request."""
        self.second_semaphore.release()
        
        # Schedule the release of the minute semaphore after 60 seconds
        asyncio.create_task(self._delayed_release_minute())
    
    async def _delayed_release_minute(self) -> None:
        """Releases a minute token after 60 seconds."""
        await asyncio.sleep(60)
        self.minute_semaphore.release()
    
    async def wait_if_needed(self) -> None:
        """Waits if we are close to the limit."""
        if self.second_semaphore._value <= 2:  # If few tokens are left
            await asyncio.sleep(1)  # Wait 1 second


# Client for Riot Games API
class RiotAPI:
    """Asynchronous client for Riot Games API."""
    
    def __init__(
        self, 
        api_key: str,
        default_region: str = "na1",
        timeout: int = 10,
        max_retries: int = 3,
        requests_per_second: int = 20,
        requests_per_minute: int = 100
    ):
        """Initializes the Riot API client.
        
        Args:
            api_key: Riot API key
            default_region: Default region for requests
            timeout: Timeout in seconds
            max_retries: Maximum number of retries for failed requests
            requests_per_second: Request limit per second
            requests_per_minute: Request limit per minute
        """
        self.api_key = api_key
        self.default_region = default_region
        self.timeout = timeout
        self.max_retries = max_retries
        
        # HTTP client
        self.client = AsyncClient(
            timeout=timeout,
            headers={"X-Riot-Token": self.api_key}
        )
        
        # Rate limiter
        self.rate_limiter = RateLimiter(
            requests_per_second=requests_per_second,
            requests_per_minute=requests_per_minute
        )
    
    def _get_base_url(self, api_path: str, region: str) -> str:
        """Determines the correct base URL based on the API and region.
        
        Args:
            api_path: API path (e.g., 'match/v5')
            region: Region code (e.g., 'na1', 'euw1')
            
        Returns:
            Base URL for the endpoint
        """
        # Determine if we use platform or region URL
        uses_platform = any(api_path.startswith(platform_api) for platform_api in PLATFORM_APIS)
        
        if uses_platform:
            # Get the platform corresponding to the region
            platform = REGION_TO_PLATFORM.get(region, "americas")
            
            # Special handling for account API
            if api_path.startswith("riot/account/v1"):
                return f"https://{platform}.api.riotgames.com/"
            return f"https://{platform}.api.riotgames.com/lol/"
        else:
            # Use region-specific URL
            return f"https://{region}.api.riotgames.com/lol/"
    
    async def _request(
        self, 
        method: str, 
        endpoint: str, 
        region: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        api_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Makes an HTTP request to the Riot API with error handling and retries.
        
        Args:
            method: HTTP method ('GET', 'POST', etc.)
            endpoint: Specific API endpoint
            region: Region code for the request
            params: Query parameters
            api_path: API path to determine the base URL
            
        Returns:
            JSON response as a dictionary
            
        Raises:
            HTTPStatusError: If the request fails after retries
        """
        region = region or self.default_region
        api_path = api_path or endpoint.split('/')[0]
        base_url = self._get_base_url(api_path, region)
        url = f"{base_url}{endpoint}"
        
        # Initialize counters
        retries = 0
        backoff = 1  # Initial backoff time in seconds
        
        while retries <= self.max_retries:
            try:
                # Acquire token from rate limiter
                await self.rate_limiter.acquire()
                
                try:
                    # Make HTTP request
                    response = await self.client.request(
                        method=method, 
                        url=url, 
                        params=params
                    )
                    
                    # Release token after the request
                    self.rate_limiter.release()
                    
                    # Check if the response was successful
                    response.raise_for_status()
                    
                    # Return JSON data
                    return response.json()
                
                except HTTPStatusError as e:
                    # Handle different status codes
                    if e.response.status_code == 429:  # Rate limit exceeded
                        retry_after = int(e.response.headers.get("Retry-After", backoff))
                        logger.warning(f"Rate limit exceeded. Waiting {retry_after} seconds.")
                        await asyncio.sleep(retry_after)
                        retries += 1
                        backoff *= 2  # Exponential backoff
                    
                    elif e.response.status_code == 404:  # Not found
                        logger.warning(f"Resource not found: {url}")
                        return {}  # Return empty dictionary for easier handling
                    
                    elif e.response.status_code >= 500:  # Server error
                        if retries < self.max_retries:
                            await asyncio.sleep(backoff)
                            retries += 1
                            backoff *= 2
                        else:
                            logger.error(f"Server error after {retries} attempts: {url}")
                            raise
                    
                    else:  # Other HTTP errors
                        logger.error(f"HTTP error {e.response.status_code}: {url}")
                        # For 400 errors, it may be useful to see the response content
                        if e.response.status_code == 400:
                            try:
                                content = e.response.text
                                logger.error(f"400 error content: {content}")
                            except:
                                pass
                        raise
                
                except RequestError as e:
                    # Network errors, timeouts, etc.
                    if retries < self.max_retries:
                        await asyncio.sleep(backoff)
                        retries += 1
                        backoff *= 2
                    else:
                        logger.error(f"Request error after {retries} attempts: {str(e)}")
                        raise
            
            finally:
                # Check if we need to wait before the next request
                await self.rate_limiter.wait_if_needed()
        
        # If we reach here, retries are exhausted
        raise HTTPStatusError(f"Retries exhausted for {url}", request=None, response=None)
    
    # ========== Methods for specific endpoints ==========
    
    async def get_account_by_puuid(self, puuid: str, region: Optional[str] = None) -> Dict[str, Any]:
        """Gets account data by PUUID.
        
        Args:
            puuid: Player's PUUID
            region: Region code
            
        Returns:
            Account data
        """
        endpoint = f"riot/account/v1/accounts/by-puuid/{puuid}"
        return await self._request("GET", endpoint, region=region, api_path="riot/account/v1")
    
    async def get_account_by_riot_id(
        self, 
        game_name: str, 
        tag_line: str, 
        region: Optional[str] = None
    ) -> Dict[str, Any]:
        """Gets account data by Riot ID (game name and tagline).
        
        Args:
            game_name: Player's game name
            tag_line: Player's tagline
            region: Region code
            
        Returns:
            Account data
        """
        endpoint = f"riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
        return await self._request("GET", endpoint, region=region, api_path="riot/account/v1")
    
    async def get_summoner_by_name_tag(
        self, 
        game_name: str, 
        tag_line: str, 
        region: Optional[str] = None
    ) -> Dict[str, Any]:
        """Gets summoner data by game name and tagline.
        
        Args:
            game_name: Summoner's game name
            tag_line: Summoner's tagline
            region: Region code
            
        Returns:
            Summoner data
        """
        # First, get the PUUID using the account API
        account_data = await self.get_account_by_riot_id(game_name, tag_line, region)
        
        if not account_data or 'puuid' not in account_data:
            logger.warning(f"Could not find account for {game_name}#{tag_line}")
            return {}
        
        # Then, get the summoner data using the PUUID
        puuid = account_data.get('puuid')
        summoner_data = await self.get_summoner_by_puuid(puuid, region)
        
        # Enrich summoner data with game name and tagline
        if summoner_data:
            summoner_data['gameName'] = game_name
            summoner_data['tagLine'] = tag_line
        
        return summoner_data
    
    async def get_summoner_by_puuid(self, puuid: str, region: Optional[str] = None) -> Dict[str, Any]:
        """Gets summoner data by PUUID.
        
        Args:
            puuid: Summoner's PUUID
            region: Region code
            
        Returns:
            Summoner data
        """
        endpoint = f"summoner/v4/summoners/by-puuid/{puuid}"
        return await self._request("GET", endpoint, region=region, api_path="summoner/v4")
    
    async def get_ranked_matches_by_puuid(
        self, 
        puuid: str, 
        region: Optional[str] = None,
        count: int = 20,
        days_back: int = 120
    ) -> List[Dict[str, str]]:
        """Gets recent ranked match IDs for a summoner.
        
        Args:
            puuid: Summoner's PUUID
            region: Region code
            count: Number of matches to retrieve (max. 100)
            days_back: Days back to search for matches
            
        Returns:
            List of (match ID, queue type) pairs
        """
        matches_by_type = []
        start_time = int((datetime.now() - timedelta(days=days_back)).timestamp())
        
        # Try to get matches for each specific queue type
        for queue_id, queue_name in RANKED_QUEUE_TYPES.items():
            try:
                # Try to get matches of this specific queue type
                endpoint = f"match/v5/matches/by-puuid/{puuid}/ids"
                params = {
                    "count": min(count, 100), 
                    "queue": queue_id,
                    "startTime": start_time
                }
                
                logger.debug(f"Searching for {queue_name} matches for {puuid}")
                
                result = await self._request(
                    "GET", 
                    endpoint, 
                    region=region, 
                    params=params,
                    api_path="match/v5"
                )
                
                # The response is a list of strings (IDs)
                if isinstance(result, list) and result:
                    for match_id in result:
                        matches_by_type.append({
                            "match_id": match_id,
                            "queue_type": queue_name
                        })
                    logger.info(f"Found {len(result)} {queue_name} matches")
                else:
                    logger.info(f"No {queue_name} matches found")
                
                # Small delay to avoid rate issues
                await asyncio.sleep(0.2)
            
            except Exception as e:
                logger.warning(f"Error getting {queue_name} matches: {str(e)}")
                continue
        
        # If nothing is found, try without filters and then filter on our side
        if not matches_by_type:
            try:
                logger.info(f"Trying to get matches without filters for {puuid}")
                endpoint = f"match/v5/matches/by-puuid/{puuid}/ids"
                params = {
                    "count": min(count * 2, 100),  # Request more to increase probability
                    "startTime": start_time
                }
                
                result = await self._request(
                    "GET", 
                    endpoint, 
                    region=region, 
                    params=params,
                    api_path="match/v5"
                )
                
                if isinstance(result, list) and result:
                    for match_id in result:
                        # Assign type after getting details
                        matches_by_type.append({
                            "match_id": match_id,
                            "queue_type": "UNKNOWN"  # Temporary
                        })
                    logger.info(f"Found {len(result)} matches without filter")
            except Exception as e:
                logger.warning(f"Error getting matches without filter: {str(e)}")
        
        # Limit to the desired number
        return matches_by_type[:count]
    
    async def get_match_detail(self, match_id: str, region: Optional[str] = None) -> Dict[str, Any]:
        """Gets full details of a match.
        
        Args:
            match_id: Match ID
            region: Region code
            
        Returns:
            Full match data
        """
        endpoint = f"match/v5/matches/{match_id}"
        return await self._request("GET", endpoint, region=region, api_path="match/v5")
    
    async def close(self) -> None:
        """Closes the HTTP client."""
        if self.client:
            await self.client.aclose()
