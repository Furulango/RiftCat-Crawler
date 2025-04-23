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

# Seed data by region
SEED_PLAYERS = {
    "korea": [
        # Korea - LCK players
        ("Faker", "kr"),
        ("Hide on bush", "kr"),
        ("T1 Gumayusi", "kr"),
        ("T1 Keria", "kr"),
        ("T1 Zeus", "kr"),
        ("T1 Oner", "kr"),
        ("GEN Chovy", "kr"),
        ("GEN Ruler", "kr"),
        ("DK Canyon", "kr"),
        ("DK ShowMaker", "kr")
    ],
    "na": [
        # North America - LCS players
        ("TL CoreJJ", "na1"),
        ("TL Bjergsen", "na1"),
        ("C9 Blaber", "na1"),
        ("C9 Berserker", "na1"),
        ("100 Closer", "na1"),
        ("TSM Spica", "na1"),
        ("EG Inspired", "na1"),
        ("EG Danny", "na1"),
        ("EG Jojopyun", "na1"),
        ("C9 Fudge", "na1")
    ],
    "europe": [
        # Europe - LEC players
        ("G2 caPs", "euw1"),
        ("G2 Jankos", "euw1"),
        ("G2 BrokenBlade", "euw1"),
        ("FNC Upset", "euw1"),
        ("FNC Humanoid", "euw1"),
        ("RGE Larssen", "euw1"),
        ("RGE Comp", "euw1"),
        ("RGE Malrang", "euw1"),
        ("MAD Elyoya", "euw1"),
        ("MAD Nisqy", "euw1")
    ],
    "china": [
        # China - LPL players (may need account ID lookup)
        ("JDG 369", "kr"),
        ("JDG Knight", "kr"),
        ("EDG Scout", "kr"),
        ("WBG TheShy", "kr"),
        ("RNG GALA", "kr"),
        ("RNG Xiaohu", "kr"),
        ("TES knight", "kr"),
        ("TES JackeyLove", "kr"),
        ("BLG Bin", "kr"),
        ("BLG Uzi", "kr")
    ],
    "challenger": {
        # Top challenger players by region
        "kr": ["Mika", "Untara", "T1 Zeus", "T1 Oner", "Quadra", "Agurin", "Kanavi", "Chasy", "Lucid", "Effort"],
        "na1": ["General Sniper", "Sheiden", "5fire", "toucouille", "Tomio", "Stixxay", "Zeyzal", "Suna", "Copy", "Haeri"],
        "euw1": ["Agurin", "Tynx", "Targamas", "Crownie", "Cinkrof", "Jackspektra", "Oscar", "Saken", "Nji", "Innaxe"],
        "eun1": ["Dumbledoge", "Crownshot", "Qase", "Shlatan", "Odoamne", "HeaQ", "Carzzy", "Flakked", "Jeskla", "Comp"],
        "br1": ["Titan", "Dynquedo", "Aegis", "RedBert", "Wizer", "Route", "Envy", "Goot", "Bounce", "Damage"],
        "jp1": ["Ceros", "Yutapon", "Evi", "Steal", "Aria", "Gaeng", "Paz", "Taka", "Yoshi", "Blank"],
        "la1": ["SeiyaLoL", "Jauny", "Acce", "Aloned", "Straight", "Kz", "Fix", "Maho", "Leza", "Mantarraya"]
    }
}


async def add_seed_player(api: RiotAPI, name: str, region: str) -> bool:
    """Add a seed player to the database.
    
    Args:
        api: RiotAPI instance
        name: Summoner name
        region: Region code
        
    Returns:
        True if successful
    """
    try:
        logger.info(f"Adding seed player: {name} ({region})")
        
        # Get summoner data from API
        summoner_data = await api.get_summoner_by_name(name, region)
        if not summoner_data:
            logger.warning(f"Summoner not found: {name} ({region})")
            return False
        
        # Add to database
        db = SessionLocal()
        try:
            summoner = SummonerRepository.create_or_update(db, summoner_data, region)
            if not summoner:
                logger.error(f"Failed to save summoner: {name}")
                return False
            
            logger.info(f"Added seed player: {summoner.name} ({summoner.id})")
            return True
        
        finally:
            db.close()
    
    except Exception as e:
        logger.error(f"Error adding seed player {name}: {str(e)}")
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
                    
                    for name in players:
                        players_to_add.append((name, region))
        
        elif category in SEED_PLAYERS:
            players_to_add = SEED_PLAYERS[category]
        
        else:
            logger.error(f"Unknown category: {category}")
            return 0, 0
        
        # Add players
        total = len(players_to_add)
        logger.info(f"Adding {total} seed players...")
        
        for name, region in players_to_add:
            if await add_seed_player(api, name, region):
                success_count += 1
            
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