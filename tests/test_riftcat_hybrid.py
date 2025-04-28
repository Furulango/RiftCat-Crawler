#!/usr/bin/env python3
"""
Hybrid script that displays summoners and their PUUIDs and then activates the crawler to process matches.
Simplified version with robust rate limiting.
"""

import os
import sys
import asyncio
import argparse
import json
import time
import logging
import httpx
from typing import Dict, List, Any, Optional, Tuple, Set
from datetime import datetime, timedelta
from enum import Enum

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s'
)
logger = logging.getLogger("riftcat_hybrid")

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

# List of ranked queue types
RANKED_QUEUE_TYPES = {
    420: "RANKED_SOLO_DUO",
    440: "RANKED_FLEX"
}

# Processing status
class ProcessingStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class RateLimiter:
    """Rate limiter handler for Riot API."""
    
    def __init__(self, requests_per_second: int = 10, requests_per_minute: int = 50):
        """Initializes the rate limiter."""
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
        """Acquires a rate token before making a request."""
        # Per-second limitation
        await self.second_semaphore.acquire()
        
        # Per-minute limitation
        await self.minute_semaphore.acquire()
        
        # Register the current timestamp
        current_time = time.time()
        async with self.lock:
            self.request_timestamps.append(current_time)
            
            # Clean up old timestamps (> 1 minute)
            self.request_timestamps = [t for t in self.request_timestamps 
                                      if current_time - t < 60]
    
    def release(self) -> None:
        """Releases a rate token after a request."""
        self.second_semaphore.release()
        
        # Schedule release of the minute semaphore after 60 seconds
        asyncio.create_task(self._delayed_release_minute())
    
    async def _delayed_release_minute(self) -> None:
        """Releases a minute token after 60 seconds."""
        await asyncio.sleep(60)
        self.minute_semaphore.release()
    
    async def wait_if_needed(self) -> None:
        """Waits if we are close to the limit."""
        if self.second_semaphore._value <= 2:  # If few tokens are left
            await asyncio.sleep(1)  # Wait 1 second


class RiotAPI:
    """Robust client for the Riot Games API."""
    
    def __init__(
        self, 
        api_key: str,
        timeout: int = 10,
        max_retries: int = 3,
        requests_per_second: int = 10,
        requests_per_minute: int = 50
    ):
        """Initializes the Riot API client."""
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        
        # HTTP client
        self.client = httpx.AsyncClient(
            timeout=timeout,
            headers={"X-Riot-Token": self.api_key}
        )
        
        # Rate limiter
        self.rate_limiter = RateLimiter(
            requests_per_second=requests_per_second,
            requests_per_minute=requests_per_minute
        )
    
    def _get_base_url(self, api_path: str, region: str) -> str:
        """Determines the correct base URL based on the API and region."""
        # Determine if we use platform or region URL
        uses_platform = any(api_path.startswith(platform_api) for platform_api in PLATFORM_APIS)
        
        if uses_platform:
            # Get platform corresponding to the region
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
        """Makes an HTTP request to the Riot API with error handling and retries."""
        region = region or "na1"
        api_path = api_path or endpoint.split('/')[0]
        base_url = self._get_base_url(api_path, region)
        url = f"{base_url}{endpoint}"
        
        # Initialize counters
        retries = 0
        backoff = 1  # Initial backoff seconds for exponential backoff
        
        while retries <= self.max_retries:
            try:
                # Acquire rate limiter token
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
                    
                    # Handle 429 errors
                    if response.status_code == 429:
                        retry_after = int(response.headers.get("Retry-After", backoff))
                        logger.warning(f"Rate limit exceeded. Waiting {retry_after} seconds.")
                        await asyncio.sleep(retry_after)
                        retries += 1
                        backoff *= 2  # Exponential backoff
                        continue
                        
                    # Handle 404 errors
                    if response.status_code == 404:
                        logger.warning(f"Resource not found: {url}")
                        return {}  # Return empty dictionary for easier handling
                    
                    # Handle server errors
                    if response.status_code >= 500:
                        if retries < self.max_retries:
                            logger.warning(f"Server error {response.status_code}, retrying in {backoff} seconds")
                            await asyncio.sleep(backoff)
                            retries += 1
                            backoff *= 2
                            continue
                        else:
                            logger.error(f"Server error after {retries} attempts: {url}")
                            return {}
                    
                    # Check if the response was successful for other cases
                    response.raise_for_status()
                    
                    # Return JSON data
                    return response.json()
                
                except httpx.HTTPStatusError as e:
                    # Handle other HTTP errors not caught above
                    logger.error(f"HTTP error {e.response.status_code}: {url}")
                    
                    if retries < self.max_retries:
                        await asyncio.sleep(backoff)
                        retries += 1
                        backoff *= 2
                    else:
                        logger.error(f"HTTP error after {retries} attempts: {url}")
                        return {}
                
                except httpx.RequestError as e:
                    # Network errors, timeouts, etc.
                    if retries < self.max_retries:
                        await asyncio.sleep(backoff)
                        retries += 1
                        backoff *= 2
                    else:
                        logger.error(f"Request error after {retries} attempts: {str(e)}")
                        return {}
            
            finally:
                # Check if we need to wait before the next request
                await self.rate_limiter.wait_if_needed()
        
        # If we reach here, retries are exhausted
        logger.error(f"Retries exhausted for {url}")
        return {}
    
    async def get_account_by_riot_id(
        self, 
        game_name: str, 
        tag_line: str, 
        region: Optional[str] = None
    ) -> Dict[str, Any]:
        """Gets account data by Riot ID (game name and tag line)."""
        endpoint = f"riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
        return await self._request("GET", endpoint, region=region, api_path="riot/account/v1")
    
    async def get_summoner_by_name_tag(
        self, 
        game_name: str, 
        tag_line: str, 
        region: Optional[str] = None
    ) -> Dict[str, Any]:
        """Gets summoner data by game name and tag line."""
        # First get the PUUID using the Account API
        account_data = await self.get_account_by_riot_id(game_name, tag_line, region)
        
        if not account_data or 'puuid' not in account_data:
            logger.warning(f"Could not find account for {game_name}#{tag_line}")
            return {}
        
        # Then get the summoner data using the PUUID
        puuid = account_data.get('puuid')
        summoner_data = await self.get_summoner_by_puuid(puuid, region)
        
        # Enrich summoner data with game name and tag line
        if summoner_data:
            summoner_data['gameName'] = game_name
            summoner_data['tagLine'] = tag_line
        
        return summoner_data
    
    async def get_summoner_by_puuid(self, puuid: str, region: Optional[str] = None) -> Dict[str, Any]:
        """Gets summoner data by PUUID."""
        endpoint = f"summoner/v4/summoners/by-puuid/{puuid}"
        return await self._request("GET", endpoint, region=region, api_path="summoner/v4")
    
    async def get_match_ids_by_puuid(
        self, 
        puuid: str, 
        region: Optional[str] = None,
        count: int = 20,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        queue: Optional[int] = None,
        start: int = 0,
        type: Optional[str] = None
    ) -> List[str]:
        """Gets recent match IDs for a summoner."""
        endpoint = f"match/v5/matches/by-puuid/{puuid}/ids"
        params = {"count": min(count, 100), "start": start}
        
        # Add optional parameters if present
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        if queue:
            params["queue"] = queue
        if type:
            params["type"] = type
        
        result = await self._request(
            "GET", 
            endpoint, 
            region=region, 
            params=params,
            api_path="match/v5"
        )
        
        # The response is a list of strings (IDs)
        return result if isinstance(result, list) else []
    
    async def get_match_detail(self, match_id: str, region: Optional[str] = None) -> Dict[str, Any]:
        """Gets full details of a match."""
        endpoint = f"match/v5/matches/{match_id}"
        return await self._request("GET", endpoint, region=region, api_path="match/v5")
    
    async def close(self) -> None:
        """Closes the HTTP client."""
        if self.client:
            await self.client.aclose()


class MemorySummoner:
    """Class to store summoner information in memory."""
    
    def __init__(
        self,
        id: str,
        puuid: str,
        name: str,
        game_name: str,
        tag_line: str,
        region: str,
        level: int,
        processing_depth: int = 0
    ):
        self.id = id
        self.puuid = puuid
        self.name = name
        self.game_name = game_name
        self.tag_line = tag_line
        self.region = region
        self.level = level
        self.status = ProcessingStatus.PENDING
        self.processing_depth = processing_depth
        self.matches_analyzed = 0
        self.related_summoners: Set[str] = set()
        self.timestamp = datetime.now()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "puuid": self.puuid,
            "name": self.name,
            "riot_id": f"{self.game_name}#{self.tag_line}",
            "region": self.region,
            "level": self.level,
            "status": self.status,
            "processing_depth": self.processing_depth,
            "matches_analyzed": self.matches_analyzed,
            "related_count": len(self.related_summoners),
            "timestamp": self.timestamp.isoformat()
        }


class RiftCatHybrid:
    """Class that combines PUUID extraction and the crawler."""
    
    def __init__(self, api_key: str):
        """Initialize."""
        self.api_key = api_key
        # Use the robust RiotAPI implementation with proper rate limiting
        self.api = RiotAPI(
            api_key=api_key,
            requests_per_second=5,  # Reduced rate to avoid hitting limits
            requests_per_minute=25
        )
        self.summoners: Dict[str, MemorySummoner] = {}  # ID -> Summoner
        self.summoners_by_puuid: Dict[str, MemorySummoner] = {}  # PUUID -> Summoner
        self.matches_data: Dict[str, Dict[str, Any]] = {}  # Match ID -> Match data
        self.matches_participants: Dict[str, List[str]] = {}  # Match ID -> List of PUUID
    
    async def add_seed_summoner(self, riot_id: str, region: str) -> Optional[MemorySummoner]:
        """Add a seed summoner."""
        if "#" not in riot_id:
            logger.error(f"Invalid format: {riot_id}, must be 'name#tag'")
            return None
        
        game_name, tag_line = riot_id.split("#", 1)
        logger.info(f"Adding seed summoner: {game_name}#{tag_line} in {region}")
        
        # Get summoner data using the robust RiotAPI implementation
        try:
            summoner_data = await self.api.get_summoner_by_name_tag(game_name, tag_line, region)
            
            if not summoner_data or "id" not in summoner_data:
                logger.error(f"Could not fetch summoner: {riot_id}")
                return None
            
            # Create MemorySummoner object
            summoner = MemorySummoner(
                id=summoner_data.get("id"),
                puuid=summoner_data.get("puuid"),
                name=summoner_data.get("name"),
                game_name=game_name,
                tag_line=tag_line,
                region=region,
                level=summoner_data.get("summonerLevel", 0)
            )
            
            # Save to dictionaries
            self.summoners[summoner.id] = summoner
            self.summoners_by_puuid[summoner.puuid] = summoner
            
            logger.info(f"Seed summoner added: {summoner.name} (PUUID: {summoner.puuid})")
            return summoner
        except Exception as e:
            logger.error(f"Error adding seed summoner {riot_id}: {str(e)}")
            return None
    
    async def process_summoner(self, summoner_id: str, match_limit: int = 3) -> List[str]:
        """Process a summoner by fetching their matches and participants."""
        summoner = self.summoners.get(summoner_id)
        if not summoner:
            logger.error(f"Summoner not found: {summoner_id}")
            return []
        
        logger.info(f"Processing summoner: {summoner.name} (PUUID: {summoner.puuid})")
        summoner.status = ProcessingStatus.PROCESSING
        
        # Fetch ranked matches using the robust RiotAPI implementation
        try:
            match_ids = []
            three_months_ago = int((datetime.now() - timedelta(days=90)).timestamp())
            
            for queue_id in [420, 440]:  # Solo/Duo and Flex
                # Get match IDs with proper error handling and rate limiting
                queue_match_ids = await self.api.get_match_ids_by_puuid(
                    puuid=summoner.puuid,
                    region=summoner.region,
                    count=match_limit,
                    start_time=three_months_ago,
                    queue=queue_id
                )
                match_ids.extend(queue_match_ids)
            
            # Limit to the requested number
            match_ids = match_ids[:match_limit]
            
            if not match_ids:
                logger.info(f"No matches found for {summoner.name}")
                summoner.status = ProcessingStatus.COMPLETED
                return []
            
            processed_match_ids = []
            # Process each match
            for match_id in match_ids:
                # Check if we already have this match
                if match_id in self.matches_data:
                    logger.info(f"Match already processed: {match_id}")
                    processed_match_ids.append(match_id)
                    continue
                
                # Fetch match details with proper error handling and rate limiting
                match_data = await self.api.get_match_detail(match_id, summoner.region)
                if not match_data:
                    logger.warning(f"Could not fetch match: {match_id}")
                    continue
                
                # Save match data
                self.matches_data[match_id] = match_data
                processed_match_ids.append(match_id)
                
                # Process participants
                participants = match_data.get("info", {}).get("participants", [])
                participant_puuids = []
                
                for participant in participants:
                    puuid = participant.get("puuid")
                    if not puuid or puuid == summoner.puuid:
                        continue
                    
                    participant_puuids.append(puuid)
                    
                    # Check if we already have this summoner
                    if puuid not in self.summoners_by_puuid:
                        # Create related summoner
                        game_name = participant.get("riotIdGameName")
                        tag_line = participant.get("riotIdTagline")
                        
                        if not game_name or not tag_line:
                            continue
                        
                        # Determine correct region based on platformId
                        platform_id = match_data.get("info", {}).get("platformId", "")
                        related_region = summoner.region
                        
                        if platform_id:
                            if platform_id.startswith("KR"):
                                related_region = "kr"
                            elif platform_id.startswith("NA"):
                                related_region = "na1"
                            elif platform_id.startswith("EUW"):
                                related_region = "euw1"
                            # Add more mappings as needed
                        
                        # Create related summoner
                        related_summoner = MemorySummoner(
                            id=f"placeholder_{puuid[:8]}",
                            puuid=puuid,
                            name=participant.get("summonerName", "Unknown"),
                            game_name=game_name,
                            tag_line=tag_line,
                            region=related_region,
                            level=participant.get("champLevel", 0),
                            processing_depth=summoner.processing_depth + 1
                        )
                        
                        # Save
                        self.summoners[related_summoner.id] = related_summoner
                        self.summoners_by_puuid[puuid] = related_summoner
                        
                        logger.info(f"New summoner found: {related_summoner.name} (PUUID: {puuid})")
                    
                    # Add relationship
                    related_summoner = self.summoners_by_puuid[puuid]
                    summoner.related_summoners.add(related_summoner.id)
                
                # Save list of participants
                self.matches_participants[match_id] = participant_puuids
                
                # Longer pause between matches for safety
                await asyncio.sleep(1.0)
            
            # Update summoner status
            summoner.status = ProcessingStatus.COMPLETED
            summoner.matches_analyzed = len(processed_match_ids)
            
            logger.info(f"Summoner {summoner.name} processed: {len(processed_match_ids)} matches")
            return processed_match_ids
            
        except Exception as e:
            logger.error(f"Error processing summoner {summoner_id}: {str(e)}")
            summoner.status = ProcessingStatus.FAILED
            return []
    
    async def crawl(self, seed_ids: List[Tuple[str, str]], match_limit: int = 3, max_depth: int = 1) -> Dict[str, Any]:
        """Run the crawl process."""
        logger.info(f"Starting crawl with {len(seed_ids)} seeds, depth {max_depth}")
        
        # Add seed summoners
        seed_summoners = []
        for riot_id, region in seed_ids:
            summoner = await self.add_seed_summoner(riot_id, region)
            if summoner:
                seed_summoners.append(summoner)
        
        if not seed_summoners:
            logger.error("Could not add seed summoners")
            return {
                "status": "error",
                "message": "Could not add seed summoners"
            }
        
        # Process seed summoners
        for summoner in seed_summoners:
            await self.process_summoner(summoner.id, match_limit)
        
        # Process additional levels if needed
        for depth in range(1, max_depth + 1):
            logger.info(f"Processing summoners at depth {depth}")
            depth_summoners = [
                s for s in self.summoners.values()
                if s.processing_depth == depth and s.status == ProcessingStatus.PENDING
            ]
            
            for summoner in depth_summoners:
                await self.process_summoner(summoner.id, match_limit)
        
        # Generate results
        results = {
            "seed_summoners": [s.to_dict() for s in seed_summoners],
            "all_summoners": [s.to_dict() for s in self.summoners.values()],
            "matches_count": len(self.matches_data),
            "timestamp": datetime.now().isoformat()
        }
        
        return results
    
    async def cleanup(self):
        """Close API resources."""
        if self.api:
            await self.api.close()


async def main():
    """Main function."""
    parser = argparse.ArgumentParser(description="Extract PUUIDs and activate crawler")
    parser.add_argument("--api-key", type=str, required=True, help="Riot Games API Key")
    parser.add_argument("--seed-players", type=str, nargs="+", required=True, 
                        help="Seed summoners in format 'name#tag:region'")
    parser.add_argument("--match-limit", type=int, default=2, help="Match limit per summoner")
    parser.add_argument("--depth", type=int, default=1, help="Maximum crawler depth")
    parser.add_argument("--output", type=str, help="Output file for results (JSON)")
    
    args = parser.parse_args()
    
    # Process seed summoners
    seed_ids = []
    for player in args.seed_players:
        parts = player.split(":")
        if len(parts) == 2:
            riot_id = parts[0]
            region = parts[1]
            seed_ids.append((riot_id, region))
        else:
            logger.error(f"Invalid format: {player}, must be 'name#tag:region'")
    
    if not seed_ids:
        logger.error("No valid seed summoners provided")
        sys.exit(1)
    
    # Create and execute the hybrid controller
    start_time = time.time()
    hybrid = RiftCatHybrid(args.api_key)
    
    try:
        results = await hybrid.crawl(seed_ids, args.match_limit, args.depth)
        elapsed_time = time.time() - start_time
        
        # Display results
        print("\n===== RESULTS =====")
        print(f"Total time: {elapsed_time:.2f} seconds")
        print(f"Total summoners: {len(results['all_summoners'])}")
        print(f"Total matches: {results['matches_count']}")
        
        # Display seed summoners
        print("\nSEED SUMMONERS:")
        for i, summoner in enumerate(results["seed_summoners"], 1):
            print(f"{i}. {summoner['name']} ({summoner['riot_id']})")
            print(f"   PUUID: {summoner['puuid']}")
            print(f"   Region: {summoner['region']}")
            print(f"   Level: {summoner['level']}")
            print(f"   Matches analyzed: {summoner['matches_analyzed']}")
            print(f"   Related summoners: {summoner['related_count']}")
        
        # Save results if requested
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2)
            print(f"\nResults saved to: {args.output}")
    
    except Exception as e:
        logger.error(f"Error during crawl: {str(e)}")
    
    finally:
        # Ensure API resources are properly closed
        await hybrid.cleanup()


if __name__ == "__main__":
    asyncio.run(main())