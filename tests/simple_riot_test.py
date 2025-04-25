#!/usr/bin/env python3
"""
Simple script to test Riot API functionality.
Only checks for a summoner and their recent ranked matches.
"""

import asyncio
import argparse
import logging
from datetime import datetime, timedelta
import httpx

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("riot-test")

# Region to platform mapping
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

# Queue types
QUEUE_TYPES = {
    420: "RANKED_SOLO",
    440: "RANKED_FLEX"
}

async def test_riot_api(api_key, riot_id, region):
    """Test basic Riot API functionality."""
    try:
        # Split Riot ID into game_name and tag_line
        if '#' not in riot_id:
            logger.error(f"Invalid Riot ID format: {riot_id}. Must use 'name#tag' format.")
            return
            
        game_name, tag_line = riot_id.split('#', 1)
        logger.info(f"Looking up summoner: {game_name}#{tag_line} in {region}")
        
        # Set up HTTP client
        headers = {"X-Riot-Token": api_key}
        async with httpx.AsyncClient(headers=headers, timeout=10) as client:
            # Step 1: Find account by Riot ID
            platform = REGION_TO_PLATFORM.get(region, "americas")
            account_url = f"https://{platform}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
            
            logger.info(f"Requesting account data from: {account_url}")
            response = await client.get(account_url)
            response.raise_for_status()
            account_data = response.json()
            
            if 'puuid' not in account_data:
                logger.error("No PUUID found in account data")
                return
                
            puuid = account_data['puuid']
            logger.info(f"Found account with PUUID: {puuid}")
            
            # Step 2: Get summoner data
            summoner_url = f"https://{region}.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}"
            
            logger.info(f"Requesting summoner data from: {summoner_url}")
            response = await client.get(summoner_url)
            response.raise_for_status()
            summoner_data = response.json()
            
            logger.info(f"Summoner Name: {summoner_data.get('name')}")
            logger.info(f"Summoner Level: {summoner_data.get('summonerLevel')}")
            
            # Step 3: Get match history (last 30 days)
            start_time = int((datetime.now() - timedelta(days=30)).timestamp())
            
            # Try each ranked queue type
            for queue_id, queue_name in QUEUE_TYPES.items():
                matches_url = f"https://{platform}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids"
                params = {
                    "count": 5,
                    "queue": queue_id,
                    "startTime": start_time
                }
                
                logger.info(f"Requesting {queue_name} matches from: {matches_url}")
                response = await client.get(matches_url, params=params)
                response.raise_for_status()
                match_ids = response.json()
                
                logger.info(f"Found {len(match_ids)} {queue_name} matches")
                
                # Get details for the first match if available
                if match_ids:
                    match_id = match_ids[0]
                    match_url = f"https://{platform}.api.riotgames.com/lol/match/v5/matches/{match_id}"
                    
                    logger.info(f"Requesting details for match: {match_id}")
                    response = await client.get(match_url)
                    response.raise_for_status()
                    match_data = response.json()
                    
                    match_info = match_data.get('info', {})
                    logger.info(f"Match Duration: {match_info.get('gameDuration')} seconds")
                    logger.info(f"Match Creation: {datetime.fromtimestamp(match_info.get('gameCreation', 0)/1000)}")
                    logger.info(f"Queue ID: {match_info.get('queueId')}")
                    
                    # Find the player in the match
                    participants = match_info.get('participants', [])
                    for participant in participants:
                        if participant.get('puuid') == puuid:
                            champion = participant.get('championName', 'Unknown')
                            kills = participant.get('kills', 0)
                            deaths = participant.get('deaths', 0)
                            assists = participant.get('assists', 0)
                            win = "Win" if participant.get('win') else "Loss"
                            logger.info(f"Player performance: {champion} - {kills}/{deaths}/{assists} - {win}")
                            break
            
            logger.info("API test completed successfully!")
    
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error: {e.response.status_code} - {e.response.text}")
    except Exception as e:
        logger.error(f"Error: {str(e)}")


def main():
    """Main function."""
    parser = argparse.ArgumentParser(description="Test Riot API functionality")
    parser.add_argument(
        "--api-key",
        type=str,
        required=True,
        help="Riot Games API key"
    )
    parser.add_argument(
        "--riot-id",
        type=str,
        required=True,
        help="Summoner Riot ID (format: name#tag)"
    )
    parser.add_argument(
        "--region",
        type=str,
        default="kr",
        help="Region code (default: kr)"
    )
    
    args = parser.parse_args()
    
    # Run the async function
    asyncio.run(test_riot_api(args.api_key, args.riot_id, args.region))


if __name__ == "__main__":
    main()