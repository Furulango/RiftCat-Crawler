#!/usr/bin/env python3
"""
Seed script for initializing the database with known players.
"""

import os
import sys
import asyncio
import argparse
from typing import List, Tuple, Dict, Any

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.riot_api import RiotAPI
from app.db.database import init_db, SessionLocal
from app.db.persistence import SummonerRepository
from app.core.logger import setup_logger

# Setup logger
logger = setup_logger("seed_players")

# Seed data by region (all with Riot ID format)
SEED_PLAYERS = {
    "korea": [
        # Korea - LCK players
        ("Faker", "KR1", "kr"),
        ("Hide on bush", "KR1", "kr"),
        ("T1 Gumayusi", "KR1", "kr"),
        ("T1 Keria", "KR1", "kr"),
        ("T1 Zeus", "KR1", "kr"),
        ("T1 Oner", "KR1", "kr"),
        ("GEN Chovy", "KR1", "kr"),
        ("GEN Ruler", "KR1", "kr"),
        ("DK Canyon", "KR1", "kr"),
        ("DK ShowMaker", "KR1", "kr")
    ],
    "na": [
        # North America - LCS players
        ("TL CoreJJ", "NA1", "na1"),
        ("TL Bjergsen", "NA1", "na1"),
        ("C9 Blaber", "NA1", "na1"),
        ("C9 Berserker", "NA1", "na1"),
        ("100 Closer", "NA1", "na1"),
        ("TSM Spica", "NA1", "na1"),
        ("EG Inspired", "NA1", "na1"),
        ("EG Danny", "NA1", "na1"),
        ("EG Jojopyun", "NA1", "na1"),
        ("C9 Fudge", "NA1", "na1")
    ],
    "europe": [
        # Europe - LEC players
        ("G2 caPs", "EUW", "euw1"),
        ("G2 Jankos", "EUW", "euw1"),
        ("G2 BrokenBlade", "EUW", "euw1"),
        ("FNC Upset", "EUW", "euw1"),
        ("FNC Humanoid", "EUW", "euw1"),
        ("RGE Larssen", "EUW", "euw1"),
        ("RGE Comp", "EUW", "euw1"),
        ("RGE Malrang", "EUW", "euw1"),
        ("MAD Elyoya", "EUW", "euw1"),
        ("MAD Nisqy", "EUW", "euw1")
    ],
    "china": [
        # China - LPL players (may need account ID lookup)
        ("JDG 369", "KR1", "kr"),
        ("JDG Knight", "KR1", "kr"),
        ("EDG Scout", "KR1", "kr"),
        ("WBG TheShy", "KR1", "kr"),
        ("RNG GALA", "KR1", "kr"),
        ("RNG Xiaohu", "KR1", "kr"),
        ("TES knight", "KR1", "kr"),
        ("TES JackeyLove", "KR1", "kr"),
        ("BLG Bin", "KR1", "kr"),
        ("BLG Uzi", "KR1", "kr")
    ],
    "challenger": {
        # Top challenger players by region with taglines
        "kr": [
            ("Mika", "KR1"),
            ("Untara", "KR1"),
            ("T1 Zeus", "KR1"),
            ("T1 Oner", "KR1"),
            ("Quadra", "KR1"),
            ("Agurin", "KR1"),
            ("Kanavi", "KR1"),
            ("Chasy", "KR1"),
            ("Lucid", "KR1"),
            ("Effort", "KR1")
        ],
        "na1": [
            ("General Sniper", "NA1"),
            ("Sheiden", "NA1"),
            ("5fire", "NA1"),
            ("toucouille", "NA1"),
            ("Tomio", "NA1"),
            ("Stixxay", "NA1"),
            ("Zeyzal", "NA1"),
            ("Suna", "NA1"),
            ("Copy", "NA1"),
            ("Haeri", "NA1")
        ],
        "euw1": [
            ("Agurin", "EUW"),
            ("Tynx", "EUW"),
            ("Targamas", "EUW"),
            ("Crownie", "EUW"),
            ("Cinkrof", "EUW"),
            ("Jackspektra", "EUW"),
            ("Oscar", "EUW"),
            ("Saken", "EUW"),
            ("Nji", "EUW"),
            ("Innaxe", "EUW")
        ],
        "eun1": [
            ("Dumbledoge", "EUN"),
            ("Crownshot", "EUN"),
            ("Qase", "EUN"),
            ("Shlatan", "EUN"),
            ("Odoamne", "EUN"),
            ("HeaQ", "EUN"),
            ("Carzzy", "EUN"),
            ("Flakked", "EUN"),
            ("Jeskla", "EUN"),
            ("Comp", "EUN")
        ],
        "br1": [
            ("Titan", "BR1"),
            ("Dynquedo", "BR1"),
            ("Aegis", "BR1"),
            ("RedBert", "BR1"),
            ("Wizer", "BR1"),
            ("Route", "BR1"),
            ("Envy", "BR1"),
            ("Goot", "BR1"),
            ("Bounce", "BR1"),
            ("Damage", "BR1")
        ],
        "jp1": [
            ("Ceros", "JP1"),
            ("Yutapon", "JP1"),
            ("Evi", "JP1"),
            ("Steal", "JP1"),
            ("Aria", "JP1"),
            ("Gaeng", "JP1"),
            ("Paz", "JP1"),
            ("Taka", "JP1"),
            ("Yoshi", "JP1"),
            ("Blank", "JP1")
        ],
        "la1": [
            ("SeiyaLoL", "LA1"),
            ("Jauny", "LA1"),
            ("Acce", "LA1"),
            ("Aloned", "LA1"),
            ("Straight", "LA1"),
            ("Kz", "LA1"),
            ("Fix", "LA1"),
            ("Maho", "LA1"),
            ("Leza", "LA1"),
            ("Mantarraya", "LA1")
        ]
    }
}


async def add_seed_player(api: RiotAPI, game_name: str, tag_line: str, region: str) -> bool:
    """Add a seed player to the database.
    
    Args:
        api: RiotAPI instance
        game_name: Player's game name 
        tag_line: Player's tag line
        region: Region code
        
    Returns:
        True if successful
    """
    try:
        logger.info(f"Adding seed player: {game_name}#{tag_line} ({region})")
        
        # Get summoner data from API using Riot ID
        summoner_data = await api.get_summoner_by_name_tag(game_name, tag_line, region)
        
        if not summoner_data:
            logger.warning(f"Summoner not found: {game_name}#{tag_line} ({region})")
            return False
        
        # Add to database
        db = SessionLocal()
        try:
            # Create with Riot ID
            summoner = SummonerRepository.create_or_update(
                db, summoner_data, region, game_name=game_name, tag_line=tag_line
            )
                
            if not summoner:
                logger.error(f"Failed to save summoner: {game_name}#{tag_line}")
                return False
            
            display_name = f"{summoner.game_name}#{summoner.tag_line}"
            logger.info(f"Added seed player: {display_name} ({summoner.id})")
            return True
        
        finally:
            db.close()
    
    except Exception as e:
        logger.error(f"Error adding seed player {game_name}#{tag_line}: {str(e)}")
        return False


async def seed_players(category: str, regions: List[str] = None, limit: int = None) -> Tuple[int, int]:
    """Seed players from the specified category.
    
    Args:
        category: Player category (korea, na, europe, china, challenger, all)
        regions: List of regions to include
        limit: Maximum players per region
        
    Returns:
        Tuple of (success_count, total)
    """
    api = RiotAPI()
    success_count = 0
    total = 0
    
    try:
        players_to_add = []
        
        # Determine which players to add
        if category == "all":
            # Add all players except challenger
            for cat in ["korea", "na", "europe", "china"]:
                players_to_add.extend(SEED_PLAYERS[cat])
        
        elif category == "challenger":
            # Add challenger players
            challenger_data = SEED_PLAYERS["challenger"]
            
            # Filter by regions if specified
            regions_to_add = regions or challenger_data.keys()
            for region in regions_to_add:
                if region in challenger_data:
                    players = challenger_data[region]
                    if limit:
                        players = players[:limit]
                    
                    for game_name, tag_line in players:
                        players_to_add.append((game_name, tag_line, region))
        
        elif category in SEED_PLAYERS:
            players_to_add = SEED_PLAYERS[category]
        
        else:
            logger.error(f"Unknown category: {category}")
            return 0, 0
        
        # Add players
        total = len(players_to_add)
        logger.info(f"Adding {total} seed players...")
        
        for player_data in players_to_add:
            # Asegurarse de que tenemos tres elementos (game_name, tag_line, region)
            if len(player_data) == 3:
                game_name, tag_line, region = player_data
                if await add_seed_player(api, game_name, tag_line, region):
                    success_count += 1
            else:
                logger.warning(f"Invalid player data format: {player_data}")
            
            # Small delay to avoid rate limits
            await asyncio.sleep(0.2)
        
        logger.info(f"Seed complete: {success_count}/{total} players added")
        return success_count, total
    
    finally:
        await api.close()


def main():
    """Main function."""
    parser = argparse.ArgumentParser(description="Seed the database with known players")
    parser.add_argument(
        "--category", 
        choices=["korea", "na", "europe", "china", "challenger", "all"],
        default="all",
        help="Category of players to seed"
    )
    parser.add_argument(
        "--regions",
        nargs="+",
        help="Regions to include (for challenger category)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum players per region (for challenger category)"
    )
    parser.add_argument(
        "--init-db",
        action="store_true",
        help="Initialize database before seeding"
    )
    
    args = parser.parse_args()
    
    # Initialize database if requested
    if args.init_db:
        logger.info("Initializing database...")
        init_db()
    
    # Seed players
    success, total = asyncio.run(seed_players(args.category, args.regions, args.limit))
    
    if success == 0:
        sys.exit(1)
    
    sys.exit(0)


if __name__ == "__main__":
    main()