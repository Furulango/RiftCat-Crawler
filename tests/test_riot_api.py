#!/usr/bin/env python3
"""
Script for manual testing of Riot Games API calls.
"""
# python tests/test_riot_api.py --riot-id "summonername#Tagline" 
#  --region "Region" [--match-count N] [--timeline] [--api-key "API_KEY"]
import os
import sys
import asyncio
import json
import argparse
from datetime import datetime, timedelta

# Add the root directory to the path to import modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.riot_api import RiotAPI
from app.core.logger import setup_logger

# Configure logger
logger = setup_logger("riot_api_test")

# Functions to test different endpoints


async def test_get_summoner_by_riot_id(api: RiotAPI, game_name: str, tag_line: str, region: str):
    """Test summoner lookup by Riot ID."""
    logger.info(f"Searching for summoner: {game_name}#{tag_line} in {region}")
    
    try:
        summoner_data = await api.get_summoner_by_name_tag(game_name, tag_line, region)
        if not summoner_data:
            logger.error(f"Summoner {game_name}#{tag_line} not found")
            return
        
        print("\n=== SUMMONER DATA ===")
        print(f"ID: {summoner_data.get('id')}")
        print(f"PUUID: {summoner_data.get('puuid')}")
        print(f"Riot ID: {summoner_data.get('gameName')}#{summoner_data.get('tagLine')}")
        print(f"Name: {summoner_data.get('name')}")
        print(f"Level: {summoner_data.get('summonerLevel')}")
        print(f"Icon: {summoner_data.get('profileIconId')}")
        
        return summoner_data
    
    except Exception as e:
        logger.error(f"Error while searching for summoner: {str(e)}")
        return None


async def test_get_match_history(api: RiotAPI, puuid: str, region: str, count: int = 5):
    """Test fetching match history for a summoner."""
    logger.info(f"Fetching match history for PUUID: {puuid}")
    
    try:
        ranked_queues = [420, 440]  # Ranked queues for solo/duo and flex
        start_time = int((datetime.now() - timedelta(days=30)).timestamp())
        
        # Fetch ranked solo/duo matches
        match_ids_solo = await api.get_match_ids_by_puuid(
            puuid=puuid, 
            region=region, 
            count=count, 
            start_time=start_time,
            queue=420  # Ranked Solo/Duo
        )
        
        # Fetch ranked flex matches
        match_ids_flex = await api.get_match_ids_by_puuid(
            puuid=puuid, 
            region=region, 
            count=count, 
            start_time=start_time,
            queue=440  # Ranked Flex
        )
        
        # Combine results
        match_ids = match_ids_solo + match_ids_flex
        match_ids = match_ids[:count]  # Limit to the requested number
        
        if not match_ids:
            logger.warning(f"No ranked matches found for PUUID: {puuid}")
            return []
        
        print(f"\n=== LAST {len(match_ids)} RANKED MATCHES ===")
        print("Ranked Solo/Duo Matches:", len(match_ids_solo))
        print("Ranked Flex Matches:", len(match_ids_flex))
        
        for idx, match_id in enumerate(match_ids, 1):
            queue_type = "Solo/Duo" if match_id in match_ids_solo else "Flex"
            print(f"{idx}. {match_id} ({queue_type})")
        
        return match_ids
    
    except Exception as e:
        logger.error(f"Error while fetching match history: {str(e)}")
        return []


async def test_get_match_details(api: RiotAPI, match_id: str, region: str):
    """Test fetching match details."""
    logger.info(f"Fetching match details for: {match_id}")
    
    try:
        match_data = await api.get_match_detail(match_id, region)
        if not match_data:
            logger.error(f"No details found for match: {match_id}")
            return None
        
        # Extract relevant information
        info = match_data.get('info', {})
        
        print("\n=== MATCH DETAILS ===")
        print(f"ID: {match_id}")
        print(f"Mode: {info.get('gameMode')}")
        print(f"Type: {info.get('gameType')}")
        print(f"Duration: {info.get('gameDuration')} seconds")
        print(f"Version: {info.get('gameVersion')}")
        
        # Display basic participant information
        participants = info.get('participants', [])
        print(f"\n=== PARTICIPANTS ({len(participants)}) ===")
        for idx, participant in enumerate(participants, 1):
            summoner_name = participant.get('summonerName', 'Unknown')
            champion = participant.get('championName', 'Unknown')
            kda = f"{participant.get('kills', 0)}/{participant.get('deaths', 0)}/{participant.get('assists', 0)}"
            win = "Win" if participant.get('win') else "Loss"
            
            print(f"{idx}. {summoner_name} - {champion} - {kda} - {win}")
        
        return match_data
    
    except Exception as e:
        logger.error(f"Error while fetching match details: {str(e)}")
        return None


async def test_get_match_timeline(api: RiotAPI, match_id: str, region: str):
    """Test fetching match timeline."""
    logger.info(f"Fetching timeline for match: {match_id}")
    
    try:
        timeline_data = await api.get_match_timeline(match_id, region)
        if not timeline_data:
            logger.error(f"No timeline found for match: {match_id}")
            return None
        
        # Extract basic information
        frames = timeline_data.get('info', {}).get('frames', [])
        
        print("\n=== MATCH TIMELINE ===")
        print(f"ID: {match_id}")
        print(f"Number of frames: {len(frames)}")
        
        # Display information from the first frame as an example
        if frames:
            first_frame = frames[0]
            events = first_frame.get('events', [])
            print(f"\nFirst frame - Events: {len(events)}")
            for idx, event in enumerate(events[:5], 1):  # Display only the first 5 events
                event_type = event.get('type', 'Unknown')
                timestamp = event.get('timestamp', 0)
                print(f"{idx}. Type: {event_type}, Time: {timestamp}ms")
            
            if len(events) > 5:
                print(f"... and {len(events) - 5} more events in the first frame")
        
        return timeline_data
    
    except Exception as e:
        logger.error(f"Error while fetching timeline: {str(e)}")
        return None


async def main():
    """Main function."""
    parser = argparse.ArgumentParser(description="Test Riot Games API calls")
    parser.add_argument(
        "--riot-id", 
        type=str, 
        help="Riot ID in the format 'name#tag'"
    )
    parser.add_argument(
        "--region", 
        type=str, 
        default="la1", 
        help="Region (default: la1)"
    )
    parser.add_argument(
        "--match-id", 
        type=str, 
        help="Match ID for specific details"
    )
    parser.add_argument(
        "--match-count", 
        type=int, 
        default=5, 
        help="Number of matches to fetch"
    )
    parser.add_argument(
        "--timeline", 
        action="store_true", 
        help="Fetch match timeline"
    )
    parser.add_argument(
        "--api-key", 
        type=str, 
        help="Riot Games API key (optional, defaults to .env)"
    )
    
    args = parser.parse_args()
    
    # Initialize API client
    api = RiotAPI(api_key=args.api_key)
    
    try:
        if args.match_id:
            # If a match ID is provided, fetch details
            await test_get_match_details(api, args.match_id, args.region)
            
            if args.timeline:
                await test_get_match_timeline(api, args.match_id, args.region)
        
        elif args.riot_id:
            # If a Riot ID is provided, fetch player and match history
            if '#' not in args.riot_id:
                logger.error("Riot ID must be in the format 'name#tag'")
                return
            
            game_name, tag_line = args.riot_id.split('#', 1)
            summoner_data = await test_get_summoner_by_riot_id(api, game_name, tag_line, args.region)
            
            if summoner_data and summoner_data.get('puuid'):
                match_ids = await test_get_match_history(
                    api, 
                    summoner_data.get('puuid'), 
                    args.region, 
                    args.match_count
                )
                
                # If matches exist and the user wants details, show the first one
                if match_ids and args.timeline:
                    await test_get_match_details(api, match_ids[0], args.region)
                    await test_get_match_timeline(api, match_ids[0], args.region)
        
        else:
            logger.error("You must provide a Riot ID (--riot-id) or a match ID (--match-id)")
    
    finally:
        # Close API client
        await api.close()


if __name__ == "__main__":
    asyncio.run(main())
