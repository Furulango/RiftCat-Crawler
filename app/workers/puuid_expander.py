import asyncio
from typing import Dict, List, Optional, Any
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.riot_api import RiotAPI
from app.core.config import settings
from app.core.logger import get_logger
from app.db.database import SessionLocal
from app.db.models import Summoner, Match, MatchSummoner, ProcessingStatus
from app.db.persistence import SummonerRepository

logger = get_logger(__name__)


class PuuidExpander:
    """Worker for expanding the crawler by discovering new players."""
    
    def __init__(
        self,
        riot_api: Optional[RiotAPI] = None,
        max_depth: int = None,
        batch_size: int = None
    ):
        """Initialize the PUUID expander.
        
        Args:
            riot_api: RiotAPI instance
            max_depth: Maximum depth for crawling
            batch_size: Batch size for processing
        """
        self.riot_api = riot_api or RiotAPI()
        self.max_depth = max_depth or settings.CRAWLER_MAX_DEPTH
        self.batch_size = batch_size or settings.CRAWLER_BATCH_SIZE
    
    async def discover_new_summoners(self, db: Session) -> int:
        """Discover new summoners from match data.
        
        Args:
            db: Database session
            
        Returns:
            Number of new summoners discovered
        """
        logger.info("Discovering new summoners from matches")
        
        try:
            # Find placeholder summoners that need to complete their information
            placeholders = db.query(Summoner).filter(
                Summoner.summoner_level == 0,  # Placeholder indication
                Summoner.processing_status == ProcessingStatus.PENDING,
                # Ensure they have Riot ID information
                Summoner.game_name.isnot(None),
                Summoner.tag_line.isnot(None)
            ).limit(self.batch_size).all()
            
            discovered = 0
            
            # Process each placeholder
            for placeholder in placeholders:
                try:
                    # Determine correct region for API requests
                    account_region = placeholder.region
                    if placeholder.region.upper() in ["KR", "ASIA"]:
                        account_region = "asia"  # Use Asia region for Korean players in account API
                    elif placeholder.region.upper().startswith("NA"):
                        account_region = "americas"
                    elif placeholder.region.upper().startswith("EU"):
                        account_region = "europe"
                    
                    logger.info(f"Fetching account data for {placeholder.game_name}#{placeholder.tag_line} using region {account_region} (original: {placeholder.region})")
                    
                    # Get account info using Riot ID
                    account_data = await self.riot_api._request(
                        "GET", 
                        f"riot/account/v1/accounts/by-riot-id/{placeholder.game_name}/{placeholder.tag_line}", 
                        region=account_region,
                        api_path="riot/account/v1"
                    )
                    
                    if not account_data or 'puuid' not in account_data:
                        logger.warning(f"Could not find account for {placeholder.game_name}#{placeholder.tag_line}: {account_data}")
                        placeholder.processing_status = ProcessingStatus.FAILED
                        db.commit()
                        continue
                    
                    # Get summoner data by PUUID
                    puuid = account_data.get('puuid')
                    
                    # Determine correct region for summoner API
                    summoner_region = placeholder.region
                    if placeholder.region.upper() in ["ASIA", "KR1"]:
                        summoner_region = "kr"
                    
                    logger.info(f"Fetching summoner data for PUUID {puuid} using region {summoner_region}")
                    summoner_data = await self.riot_api.get_summoner_by_puuid(puuid, summoner_region)
                    
                    if not summoner_data:
                        logger.warning(f"Could not retrieve data for summoner with PUUID: {puuid}")
                        placeholder.processing_status = ProcessingStatus.FAILED
                        db.commit()
                        continue
                    
                    # Update with complete data
                    summoner = SummonerRepository.create_or_update(
                        db, summoner_data, summoner_region,
                        game_name=placeholder.game_name,
                        tag_line=placeholder.tag_line
                    )
                    
                    if summoner:
                        discovered += 1
                        logger.info(f"Updated summoner: {summoner.game_name}#{summoner.tag_line} ({summoner.id})")
                
                except Exception as e:
                    logger.error(f"Error discovering summoner {placeholder.game_name}#{placeholder.tag_line}: {str(e)}")
                    placeholder.processing_status = ProcessingStatus.FAILED
                    db.commit()
                
                # Small delay between API calls
                await asyncio.sleep(0.1)
            
            logger.info(f"Discovered {discovered} new summoners")
            return discovered
    
        except Exception as e:
            logger.error(f"Error in discover_new_summoners: {str(e)}")
            return 0
    
    async def expand_from_completed(self, db: Session) -> int:
        """Find new summoners from relation graphs of completed summoners.
        
        Args:
            db: Database session
            
        Returns:
            Number of new targets added to queue
        """
        logger.info("Expanding crawler from completed summoners")
        
        try:
            # Find matches with completed summoners that have related players
            query = db.query(
                Match.id, func.count(MatchSummoner.summoner_id).label('player_count')
            ).join(
                MatchSummoner
            ).filter(
                Match.queue_id.in_(settings.CRAWLER_QUEUE_TYPES)
            ).group_by(
                Match.id
            ).having(
                func.count(MatchSummoner.summoner_id) > 1
            ).limit(20)
            
            matches = query.all()
            
            # Process each match to build relations
            added_count = 0
            for match_id, player_count in matches:
                try:
                    # Get all participants
                    participants = db.query(MatchSummoner).filter(
                        MatchSummoner.match_id == match_id
                    ).all()
                    
                    # For each participant, create relations to other participants
                    for p1 in participants:
                        # Skip if no valid Riot ID
                        if not p1.summoner or not p1.summoner.game_name or not p1.summoner.tag_line:
                            continue
                            
                        if p1.summoner.processing_status != ProcessingStatus.COMPLETED:
                            continue
                            
                        for p2 in participants:
                            # Skip self-relations and summoners without valid Riot ID
                            if p1.summoner_id == p2.summoner_id or not p2.summoner or not p2.summoner.game_name or not p2.summoner.tag_line:
                                continue
                            
                            # Add relation
                            if p2.summoner.processing_status == ProcessingStatus.COMPLETED:
                                # Both completed, just add relation
                                SummonerRepository.add_relation(
                                    db, p1.summoner_id, p2.summoner_id
                                )
                            elif p2.summoner.processing_depth < self.max_depth:
                                # Target not completed, check depth
                                SummonerRepository.add_relation(
                                    db, p1.summoner_id, p2.summoner_id
                                )
                                
                                # Mark for processing if not yet processed
                                if p2.summoner.processing_status not in [
                                    ProcessingStatus.PROCESSING, 
                                    ProcessingStatus.PENDING
                                ]:
                                    p2.summoner.processing_status = ProcessingStatus.PENDING
                                    added_count += 1
                    
                    db.commit()
                
                except Exception as e:
                    logger.error(f"Error expanding from match {match_id}: {str(e)}")
                    db.rollback()
            
            logger.info(f"Added {added_count} new targets from expansion")
            return added_count
        
        except Exception as e:
            logger.error(f"Error in expand_from_completed: {str(e)}")
            db.rollback()
            return 0
    
    async def run_cycle(self) -> int:
        """Run a complete expansion cycle.
        
        Returns:
            Number of new summoners discovered
        """
        db = SessionLocal()
        try:
            # First, discover complete data for placeholder summoners
            discovered = await self.discover_new_summoners(db)
            
            # Then expand to related summoners from completed ones
            expanded = await self.expand_from_completed(db)
            
            return discovered + expanded
        
        finally:
            db.close()
    
    async def run_continuous(self, interval: int = 30) -> None:
        """Run the PUUID expander continuously.
        
        Args:
            interval: Seconds to wait between cycles
        """
        logger.info("Starting continuous PUUID expansion")
        
        try:
            while True:
                try:
                    discovered = await self.run_cycle()
                    
                    if discovered == 0:
                        # No new summoners, wait longer
                        logger.info(f"No new summoners discovered, waiting {interval * 2} seconds")
                        await asyncio.sleep(interval * 2)
                    else:
                        # Normal interval
                        logger.info(f"Discovered {discovered} new summoners, waiting {interval} seconds")
                        await asyncio.sleep(interval)
                
                except Exception as e:
                    logger.error(f"Error in expansion cycle: {str(e)}")
                    await asyncio.sleep(interval)
        
        except asyncio.CancelledError:
            logger.info("PUUID expander stopped")
        
        except Exception as e:
            logger.error(f"Error in continuous PUUID expansion: {str(e)}")
            raise
        
        finally:
            # Close API client
            await self.riot_api.close()


# For direct execution
async def main():
    """Run the PUUID expander."""
    expander = PuuidExpander()
    await expander.run_continuous()


if __name__ == "__main__":
    asyncio.run(main())