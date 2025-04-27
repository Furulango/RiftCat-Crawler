#!/usr/bin/env python3
"""
Hybrid script that displays summoners and their PUUIDs and then activates the crawler to process matches.
"""

import os
import sys
import asyncio
import argparse
import json
import time
from typing import Dict, List, Any, Optional, Tuple, Set
from datetime import datetime, timedelta
from enum import Enum
import logging

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


class RiotAPI:
    """Simplified client for the Riot API."""
    
    def __init__(self, api_key: str, requests_per_second: int = 20):
        """Initialize the client."""
        self.api_key = api_key
        self.headers = {"X-Riot-Token": api_key}
        self.requests_per_second = requests_per_second
        self.last_request_time = 0
    
    async def _rate_limit(self):
        """Simple rate limiting to avoid exceeding limits."""
        current_time = time.time()
        time_since_last = current_time - self.last_request_time
        
        # If less than 1/requests_per_second seconds have passed, wait
        if time_since_last < (1.0 / self.requests_per_second):
            await asyncio.sleep((1.0 / self.requests_per_second) - time_since_last)
        
        self.last_request_time = time.time()
    
    async def get_summoner_by_riot_id(self, game_name: str, tag_line: str, region: str) -> Dict[str, Any]:
        """Get summoner data by Riot ID."""
        import httpx
        
        # Determine the correct platform for the account API
        platform = REGION_TO_PLATFORM.get(region.lower(), "americas")
        
        # Step 1: Get PUUID using the account endpoint
        await self._rate_limit()
        account_url = f"https://{platform}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
        logger.info(f"Fetching account from: {account_url}")
        
        async with httpx.AsyncClient(headers=self.headers) as client:
            response = await client.get(account_url)
            response.raise_for_status()
            account_data = response.json()
        
        puuid = account_data.get("puuid")
        if not puuid:
            logger.error(f"PUUID not found for {game_name}#{tag_line}")
            return {}
            
        logger.info(f"PUUID obtained: {puuid}")
        
        # Step 2: Use the PUUID to get summoner data
        await self._rate_limit()
        summoner_url = f"https://{region}.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}"
        logger.info(f"Fetching summoner from: {summoner_url}")
        
        async with httpx.AsyncClient(headers=self.headers) as client:
            response = await client.get(summoner_url)
            response.raise_for_status()
            summoner_data = response.json()
        
        # Enrich with Riot ID data
        summoner_data["gameName"] = game_name
        summoner_data["tagLine"] = tag_line
        
        return summoner_data
    
    async def get_match_ids_by_puuid(
        self, 
        puuid: str, 
        region: str,
        count: int = 5,
        queue_type: int = 420
    ) -> List[str]:
        """Get recent match IDs for a summoner."""
        import httpx
        
        # Determine the correct platform for the match API
        platform = REGION_TO_PLATFORM.get(region.lower(), "americas")
        
        # Calculate the date 90 days ago (3 months)
        three_months_ago = datetime.now() - timedelta(days=90)
        start_time = int(three_months_ago.timestamp())
        
        await self._rate_limit()
        matches_url = f"https://{platform}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids"
        params = {
            "count": count, 
            "queue": queue_type,
            "startTime": start_time
        }
        
        logger.info(f"Fetching matches from: {matches_url} (region: {region}, platform: {platform})")
        
        async with httpx.AsyncClient(headers=self.headers) as client:
            response = await client.get(matches_url, params=params)
            response.raise_for_status()
            match_ids = response.json()
        
        logger.info(f"Found {len(match_ids)} matches for queue {queue_type}")
        return match_ids
    
    async def get_match_detail(self, match_id: str, region: str) -> Dict[str, Any]:
        """Get match details."""
        import httpx
        
        # Determine the correct platform for the match API
        platform = REGION_TO_PLATFORM.get(region.lower(), "americas")
        
        await self._rate_limit()
        match_url = f"https://{platform}.api.riotgames.com/lol/match/v5/matches/{match_id}"
        logger.info(f"Fetching match from: {match_url}")
        
        async with httpx.AsyncClient(headers=self.headers) as client:
            response = await client.get(match_url)
            response.raise_for_status()
            match_data = response.json()
        
        return match_data


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
        self.api = RiotAPI(api_key)
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
        
        # Get summoner data
        summoner_data = await self.api.get_summoner_by_riot_id(game_name, tag_line, region)
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
    
    async def process_summoner(self, summoner_id: str, match_limit: int = 3) -> List[str]:
        """Process a summoner by fetching their matches and participants."""
        summoner = self.summoners.get(summoner_id)
        if not summoner:
            logger.error(f"Summoner not found: {summoner_id}")
            return []
        
        logger.info(f"Processing summoner: {summoner.name} (PUUID: {summoner.puuid})")
        summoner.status = ProcessingStatus.PROCESSING
        
        # Fetch ranked matches
        match_ids = []
        for queue_type in [420, 440]:  # Solo/Duo and Flex
            queue_match_ids = await self.api.get_match_ids_by_puuid(
                puuid=summoner.puuid,
                region=summoner.region,
                count=match_limit,
                queue_type=queue_type
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
            
            # Fetch match details
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
            
            # Small pause between matches
            await asyncio.sleep(0.2)
        
        # Update summoner status
        summoner.status = ProcessingStatus.COMPLETED
        summoner.matches_analyzed = len(processed_match_ids)
        
        logger.info(f"Summoner {summoner.name} processed: {len(processed_match_ids)} matches")
        return processed_match_ids
    
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


async def main():
    """Main function."""
    parser = argparse.ArgumentParser(description="Extract PUUIDs and activate crawler")
    parser.add_argument("--api-key", type=str, required=True, help="Riot Games API Key")
    parser.add_argument("--seed-players", type=str, nargs="+", required=True, 
                        help="Seed summoners in format 'name#tag:region'")
    parser.add_argument("--match-limit", type=int, default=3, help="Match limit per summoner")
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


if __name__ == "__main__":
    asyncio.run(main())