import asyncio
import time
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.riot_api import RiotAPI
from app.core.config import settings
from app.core.logger import get_logger
from app.db.database import SessionLocal
from app.db.models import ProcessingStatus
from app.db.persistence import SummonerRepository, MatchRepository

logger = get_logger(__name__)


class MatchFetcher:
    """Worker for fetching match data."""
    
    def __init__(
        self,
        riot_api: Optional[RiotAPI] = None,
        match_limit: int = None,
        queue_types: List[int] = None,
        fetch_timeline: bool = True
    ):
        """Initialize the match fetcher.
        
        Args:
            riot_api: RiotAPI instance
            match_limit: Maximum number of matches to fetch per summoner
            queue_types: Queue types to filter (e.g., [400, 420, 440])
            fetch_timeline: Whether to fetch match timeline
        """
        self.riot_api = riot_api or RiotAPI()
        self.match_limit = match_limit or settings.CRAWLER_MATCH_LIMIT
        self.queue_types = queue_types or settings.CRAWLER_QUEUE_TYPES
        self.fetch_timeline = fetch_timeline
    
    async def process_summoner(self, summoner_id: str, region: str) -> bool:
        """Process a summoner by fetching their recent matches.
        
        Args:
            summoner_id: Summoner ID
            region: Region code
            
        Returns:
            True if successful, False otherwise
        """
        # Create a new session for this task
        db = SessionLocal()
        
        try:
            logger.info(f"Processing summoner {summoner_id} in region {region}")
            
            # Get summoner from database
            summoner = db.query(Summoner).filter(Summoner.id == summoner_id).first()
            if not summoner:
                logger.error(f"Summoner not found: {summoner_id}")
                return False
            
            # Mark as processing
            SummonerRepository.update_status(
                db, summoner_id, ProcessingStatus.PROCESSING
            )
            
            # Fetch match IDs for the summoner
            start_time = int((datetime.now() - timedelta(days=30)).timestamp())
            match_ids = await self.riot_api.get_match_ids_by_puuid(
                puuid=summoner.puuid,
                region=region,
                count=self.match_limit,
                start_time=start_time,
                queue=self.queue_types
            )
            
            if not match_ids:
                logger.info(f"No matches found for summoner {summoner_id}")
                SummonerRepository.update_status(
                    db, summoner_id, ProcessingStatus.COMPLETED, 
                    matches_analyzed=0, increment_depth=True
                )
                return True
            
            # Process each match
            processed_count = 0
            for match_id in match_ids:
                try:
                    # Check if match already exists
                    existing_match = MatchRepository.get_by_id(db, match_id)
                    if existing_match and existing_match.processing_status == ProcessingStatus.COMPLETED:
                        logger.debug(f"Match already processed: {match_id}")
                        processed_count += 1
                        continue
                    
                    # Fetch match details
                    match_data = await self.riot_api.get_match_detail(match_id, region)
                    if not match_data:
                        logger.warning(f"Failed to fetch match data: {match_id}")
                        continue
                    
                    # Save match to database
                    match = MatchRepository.create_or_update(db, match_data)
                    if not match:
                        logger.error(f"Failed to save match: {match_id}")
                        continue
                    
                    processed_count += 1
                    
                    # Fetch timeline if needed
                    if self.fetch_timeline and not match.is_timeline_fetched:
                        timeline_data = await self.riot_api.get_match_timeline(match_id, region)
                        if timeline_data:
                            MatchRepository.save_timeline(db, match_id, timeline_data)
                    
                    # Add summoner relations - all players in this match are related
                    participants = match_data.get('info', {}).get('participants', [])
                    for participant in participants:
                        if participant.get('puuid') != summoner.puuid:
                            # Get or create related summoner
                            related_summoner = db.query(Summoner).filter(
                                Summoner.puuid == participant.get('puuid')
                            ).first()
                            
                            if related_summoner:
                                SummonerRepository.add_relation(
                                    db, summoner.id, related_summoner.id
                                )
                    
                    # Small delay between matches to avoid rate issues
                    await asyncio.sleep(0.1)
                
                except Exception as e:
                    logger.error(f"Error processing match {match_id}: {str(e)}")
            
            # Mark summoner as completed and increment depth
            logger.info(f"Completed processing summoner {summoner_id}, processed {processed_count} matches")
            SummonerRepository.update_status(
                db, summoner_id, ProcessingStatus.COMPLETED, 
                matches_analyzed=processed_count, increment_depth=True
            )
            
            return True
        
        except Exception as e:
            logger.error(f"Error processing summoner {summoner_id}: {str(e)}")
            
            # Mark as failed
            SummonerRepository.update_status(
                db, summoner_id, ProcessingStatus.FAILED
            )
            
            return False
        
        finally:
            db.close()
    
    async def run_batch(self, batch_size: int = 10) -> int:
        """Process a batch of pending summoners.
        
        Args:
            batch_size: Number of summoners to process in parallel
            
        Returns:
            Number of summoners processed
        """
        db = SessionLocal()
        try:
            # Get pending summoners
            summoners = SummonerRepository.get_pending_summoners(
                db, limit=batch_size, max_depth=settings.CRAWLER_MAX_DEPTH
            )
            
            if not summoners:
                logger.info("No pending summoners found")
                return 0
            
            logger.info(f"Processing batch of {len(summoners)} summoners")
            
            # Process summoners in parallel
            tasks = []
            for summoner in summoners:
                task = asyncio.create_task(
                    self.process_summoner(summoner.id, summoner.region)
                )
                tasks.append(task)
            
            # Wait for all tasks to complete
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Count successful completions
            successful = sum(1 for r in results if r is True)
            logger.info(f"Batch completed: {successful}/{len(summoners)} successful")
            
            return len(summoners)
        
        finally:
            db.close()
    
    async def run_continuous(self, interval: int = 5) -> None:
        """Run the match fetcher continuously.
        
        Args:
            interval: Seconds to wait between batches
        """
        logger.info("Starting continuous match fetching")
        
        try:
            while True:
                batch_size = settings.CRAWLER_BATCH_SIZE
                processed = await self.run_batch(batch_size)
                
                if processed == 0:
                    # No summoners processed, wait longer
                    logger.info(f"No summoners processed, waiting {interval * 2} seconds")
                    await asyncio.sleep(interval * 2)
                else:
                    # Normal interval
                    await asyncio.sleep(interval)
        
        except asyncio.CancelledError:
            logger.info("Match fetcher stopped")
        
        except Exception as e:
            logger.error(f"Error in continuous match fetching: {str(e)}")
            raise
        
        finally:
            # Close API client
            await self.riot_api.close()


# For direct execution
async def main():
    """Run the match fetcher."""
    fetcher = MatchFetcher()
    await fetcher.run_continuous()


if __name__ == "__main__":
    asyncio.run(main())