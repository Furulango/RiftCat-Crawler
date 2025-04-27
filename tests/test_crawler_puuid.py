#!/usr/bin/env python3
"""
Test script to extract League of Legends summoners and display their names and PUUIDs.
It does not attempt to fetch matches, only extracts basic summoner information.
"""

import asyncio
import argparse
import json
import time
from datetime import datetime
from typing import Dict, List, Any, Optional
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("lol_summoner_extractor")

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

class RiotAPI:
    """Simplified client for the Riot API."""
    
    def __init__(self, api_key: str):
        """Initialize the client."""
        self.api_key = api_key
        self.headers = {"X-Riot-Token": api_key}
    
    async def get_summoner_by_riot_id(self, game_name: str, tag_line: str, region: str) -> Dict[str, Any]:
        """Get summoner data by Riot ID."""
        import httpx
        
        # Determine the correct platform for the account API
        platform = REGION_TO_PLATFORM.get(region.lower(), "americas")
        
        # Step 1: Get PUUID
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
        
        # Step 2: Get summoner data
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
    
    async def get_match_by_id(self, match_id: str, region: str) -> Dict[str, Any]:
        """Get match data by ID."""
        import httpx
        
        # Determine the correct platform for the match API
        platform = REGION_TO_PLATFORM.get(region.lower(), "americas")
        
        match_url = f"https://{platform}.api.riotgames.com/lol/match/v5/matches/{match_id}"
        logger.info(f"Fetching match from: {match_url}")
        
        async with httpx.AsyncClient(headers=self.headers) as client:
            response = await client.get(match_url)
            response.raise_for_status()
            match_data = response.json()
        
        return match_data
    
    async def get_recent_matches(self, puuid: str, region: str, count: int = 1) -> List[str]:
        """Get recent match IDs for a summoner."""
        import httpx
        
        # Determine the correct platform for the match API
        platform = REGION_TO_PLATFORM.get(region.lower(), "americas")
        
        matches_url = f"https://{platform}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids"
        params = {"count": count, "start": 0}
        
        logger.info(f"Fetching matches from: {matches_url}")
        
        async with httpx.AsyncClient(headers=self.headers) as client:
            response = await client.get(matches_url, params=params)
            response.raise_for_status()
            match_ids = response.json()
        
        return match_ids


async def extract_summoners(api_key: str, seed_riot_id: str, region: str) -> Dict[str, Any]:
    """Extract seed summoner and related summoners."""
    api = RiotAPI(api_key)
    results = {
        "seed_summoner": {},
        "related_summoners": [],
        "timestamp": datetime.now().isoformat()
    }
    
    try:
        # Verify Riot ID format
        if "#" not in seed_riot_id:
            logger.error(f"Invalid Riot ID format: {seed_riot_id}. Use 'name#tag'")
            return results
        
        game_name, tag_line = seed_riot_id.split("#", 1)
        
        # Get seed summoner
        logger.info(f"Fetching seed summoner: {game_name}#{tag_line} in {region}")
        seed_data = await api.get_summoner_by_riot_id(game_name, tag_line, region)
        
        if not seed_data:
            logger.error(f"Seed summoner not found: {seed_riot_id}")
            return results
        
        # Save seed summoner data
        seed_puuid = seed_data.get("puuid")
        seed_result = {
            "name": seed_data.get("name"),
            "riot_id": f"{game_name}#{tag_line}",
            "puuid": seed_puuid,
            "level": seed_data.get("summonerLevel"),
            "region": region
        }
        results["seed_summoner"] = seed_result
        
        logger.info(f"Seed summoner found: {seed_result['name']} (PUUID: {seed_puuid})")
        
        # Get a recent match
        logger.info(f"Looking for a recent match for {seed_riot_id}")
        match_ids = await api.get_recent_matches(seed_puuid, region, count=1)
        
        if not match_ids:
            logger.info(f"No recent matches found for {seed_riot_id}")
            return results
        
        # Get match details
        match_id = match_ids[0]
        logger.info(f"Fetching match details: {match_id}")
        match_data = await api.get_match_by_id(match_id, region)
        
        if not match_data:
            logger.error(f"Match details not found: {match_id}")
            return results
        
        # Extract participants
        participants = match_data.get("info", {}).get("participants", [])
        logger.info(f"Found {len(participants)} participants in the match")
        
        for participant in participants:
            puuid = participant.get("puuid")
            
            # Skip seed summoner and participants without Riot ID
            if puuid == seed_puuid or not puuid:
                continue
                
            game_name = participant.get("riotIdGameName")
            tag_line = participant.get("riotIdTagline")
            
            if not game_name or not tag_line:
                logger.warning(f"Participant without valid Riot ID, PUUID: {puuid}")
                continue
            
            # Adjust region for each participant based on platformId
            participant_region = region
            platform_id = match_data.get("info", {}).get("platformId", "")
            
            if platform_id:
                if platform_id.startswith("KR"):
                    participant_region = "kr"
                elif platform_id.startswith("NA"):
                    participant_region = "na1"
                elif platform_id.startswith("EUW"):
                    participant_region = "euw1"
                # Add more mappings as needed
            
            # Save participant data
            participant_result = {
                "name": participant.get("summonerName", "Unknown"),
                "riot_id": f"{game_name}#{tag_line}",
                "puuid": puuid,
                "level": participant.get("summonerLevel", 0),
                "region": participant_region,
                "champion": participant.get("championName", "Unknown")
            }
            
            results["related_summoners"].append(participant_result)
            
            logger.info(f"Participant found: {participant_result['name']} (PUUID: {puuid})")
        
        return results
    
    except Exception as e:
        logger.error(f"Error extracting summoners: {str(e)}")
        return results


async def main():
    """Main function."""
    parser = argparse.ArgumentParser(description="Extract League of Legends summoners")
    parser.add_argument("--api-key", type=str, required=True, help="Riot Games API key")
    parser.add_argument("--riot-id", type=str, required=True, help="Riot ID in the format 'name#tag'")
    parser.add_argument("--region", type=str, default="kr", help="Region (default: kr)")
    parser.add_argument("--output", type=str, help="Output file (JSON)")
    
    args = parser.parse_args()
    
    # Extract summoners
    start_time = time.time()
    results = await extract_summoners(args.api_key, args.riot_id, args.region)
    elapsed_time = time.time() - start_time
    
    # Display results
    print("\n===== RESULTS =====")
    if results["seed_summoner"]:
        seed = results["seed_summoner"]
        print(f"\nSEED SUMMONER:")
        print(f"Name: {seed['name']}")
        print(f"Riot ID: {seed['riot_id']}")
        print(f"PUUID: {seed['puuid']}")
        print(f"Level: {seed['level']}")
        print(f"Region: {seed['region']}")
    
    if results["related_summoners"]:
        print(f"\nRELATED SUMMONERS ({len(results['related_summoners'])}):")
        for i, summoner in enumerate(results["related_summoners"], 1):
            print(f"\n{i}. {summoner['name']} ({summoner['champion']})")
            print(f"   Riot ID: {summoner['riot_id']}")
            print(f"   PUUID: {summoner['puuid']}")
            print(f"   Region: {summoner['region']}")
    
    print(f"\nExecution time: {elapsed_time:.2f} seconds")
    
    # Save results if requested
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2)
        print(f"\nResults saved to: {args.output}")


if __name__ == "__main__":
    asyncio.run(main())