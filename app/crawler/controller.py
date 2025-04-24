import asyncio
import time
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.riot_api import RiotAPI
from app.core.config import settings
from app.core.logger import get_logger
from app.db.database import SessionLocal
from app.db.models import ProcessingStatus, Summoner
from app.db.persistence import SummonerRepository, CrawlerStateRepository
from app.workers.match_fetcher import MatchFetcher
from app.workers.puuid_expander import PuuidExpander
from app.crawler.filters import MatchFilter, SummonerFilter

logger = get_logger(__name__)


class CrawlerController:
    """Main controller for the crawler system."""
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        match_limit: int = None,
        max_depth: int = None,
        batch_size: int = None
    ):
        """Initialize the crawler controller.
        
        Args:
            api_key: Riot API key
            match_limit: Maximum matches to fetch per summoner
            max_depth: Maximum depth for the crawler graph
            batch_size: Number of summoners to process per batch
        """
        # Configuration
        self.match_limit = match_limit or settings.CRAWLER_MATCH_LIMIT
        self.max_depth = max_depth or settings.CRAWLER_MAX_DEPTH
        self.batch_size = batch_size or settings.CRAWLER_BATCH_SIZE
        
        # API client
        self.riot_api = RiotAPI(api_key=api_key)
        
        # Workers
        self.match_fetcher = MatchFetcher(
            riot_api=self.riot_api,
            match_limit=self.match_limit
        )
        
        self.puuid_expander = PuuidExpander(
            riot_api=self.riot_api,
            max_depth=self.max_depth,
            batch_size=self.batch_size
        )
        
        # Filters
        self.match_filter = MatchFilter()
        self.summoner_filter = SummonerFilter()
        
        # Worker tasks
        self.fetcher_task = None
        self.expander_task = None
        
        # State
        self.running = False
        self.stop_requested = False
    
    async def add_seed_summoner(self, summoner_name: str, region: str) -> Optional[Summoner]:
        """Add a seed summoner to start the crawl.
        
        Args:
            summoner_name: Summoner name (must be in the format "name#tag")
            region: Region code
            
        Returns:
            Added summoner or None if failed
        """
        try:
            # Verificar que el formato sea correcto (name#tag)
            if '#' not in summoner_name:
                logger.error(f"Invalid summoner format: {summoner_name}. Must use 'name#tag' format.")
                return None
            
            # Extraer game_name y tag_line
            game_name, tag_line = summoner_name.split('#', 1)
            logger.info(f"Adding seed summoner: {game_name}#{tag_line} in {region}")
            
            # Get summoner data from API using Riot ID
            summoner_data = await self.riot_api.get_summoner_by_name_tag(game_name, tag_line, region)
            
            if not summoner_data:
                logger.error(f"Failed to get summoner data for {summoner_name} in {region}")
                return None
            
            # Add to database
            db = SessionLocal()
            try:
                # Crear o actualizar en la base de datos
                summoner = SummonerRepository.create_or_update(
                    db, summoner_data, region, game_name=game_name, tag_line=tag_line
                )
                
                if not summoner:
                    logger.error(f"Failed to add seed summoner {summoner_name}")
                    return None
                
                display_name = f"{summoner.game_name}#{summoner.tag_line}"
                logger.info(f"Added seed summoner: {display_name} ({summoner.id})")
                return summoner
            
            finally:
                db.close()
        
        except Exception as e:
            logger.error(f"Error adding seed summoner {summoner_name}: {str(e)}")
            return None
    
    async def add_seed_summoners(self, seed_list: List[Tuple[str, str]]) -> int:
        """Add multiple seed summoners.
        
        Args:
            seed_list: List of (summoner_name, region) tuples
            
        Returns:
            Number of successfully added seeds
        """
        successful = 0
        
        for name, region in seed_list:
            summoner = await self.add_seed_summoner(name, region)
            if summoner:
                successful += 1
        
        logger.info(f"Added {successful}/{len(seed_list)} seed summoners")
        return successful
    
    async def update_metrics(self) -> Dict[str, Any]:
        """Update and retrieve crawler metrics.
        
        Returns:
            Dictionary with current metrics
        """
        db = SessionLocal()
        try:
            # Get counts
            total_summoners = db.query(Summoner).count()
            completed = db.query(Summoner).filter(
                Summoner.processing_status == ProcessingStatus.COMPLETED
            ).count()
            pending = db.query(Summoner).filter(
                Summoner.processing_status == ProcessingStatus.PENDING
            ).count()
            processing = db.query(Summoner).filter(
                Summoner.processing_status == ProcessingStatus.PROCESSING
            ).count()
            failed = db.query(Summoner).filter(
                Summoner.processing_status == ProcessingStatus.FAILED
            ).count()
            
            # Get current max depth
            deepest = db.query(Summoner).order_by(
                Summoner.processing_depth.desc()
            ).first()
            current_depth = deepest.processing_depth if deepest else 0
            
            # Count summoners with and without Riot ID
            with_riot_id = db.query(Summoner).filter(
                Summoner.game_name.isnot(None),
                Summoner.tag_line.isnot(None)
            ).count()
            
            # Update state in database
            status_message = f"Running: {self.running}, Completed: {completed}, Pending: {pending}"
            CrawlerStateRepository.update_metrics(
                db,
                total_summoners=total_summoners,
                queue_size=pending,
                current_depth=current_depth,
                active=self.running,
                status_message=status_message
            )
            
            # Return metrics
            metrics = {
                "total_summoners": total_summoners,
                "completed": completed,
                "pending": pending,
                "processing": processing,
                "failed": failed,
                "with_riot_id": with_riot_id, 
                "current_depth": current_depth,
                "running": self.running,
                "timestamp": datetime.utcnow().isoformat()
            }
            
            return metrics
        
        finally:
            db.close()
    
    async def _metrics_monitor(self, interval: int = 60) -> None:
        """Background task to periodically update metrics.
        
        Args:
            interval: Update interval in seconds
        """
        logger.info("Starting metrics monitor")
        
        while not self.stop_requested:
            try:
                metrics = await self.update_metrics()
                logger.info(f"Crawler stats: {metrics}")
                
                # If nothing is pending and nothing is processing, the crawl is complete
                if (metrics["pending"] == 0 and metrics["processing"] == 0 and 
                    self.running and metrics["total_summoners"] > 0):
                    logger.info("Crawl completed - no more pending or processing summoners")
                
                await asyncio.sleep(interval)
            
            except Exception as e:
                logger.error(f"Error in metrics monitor: {str(e)}")
                await asyncio.sleep(interval)
    
    async def start(self) -> bool:
        """Start the crawler.
        
        Returns:
            True if started successfully
        """
        if self.running:
            logger.warning("Crawler is already running")
            return False
        
        try:
            # Reset stop flag
            self.stop_requested = False
            
            # Start workers
            self.fetcher_task = asyncio.create_task(
                self.match_fetcher.run_continuous()
            )
            
            self.expander_task = asyncio.create_task(
                self.puuid_expander.run_continuous()
            )
            
            # Start metrics monitor
            self.metrics_task = asyncio.create_task(
                self._metrics_monitor()
            )
            
            self.running = True
            logger.info("Crawler started successfully")
            return True
        
        except Exception as e:
            logger.error(f"Error starting crawler: {str(e)}")
            await self.stop()
            return False
    
    async def stop(self) -> None:
        """Stop the crawler."""
        if not self.running:
            logger.warning("Crawler is not running")
            return
        
        logger.info("Stopping crawler...")
        self.stop_requested = True
        
        # Cancel all tasks
        if self.fetcher_task:
            self.fetcher_task.cancel()
        
        if self.expander_task:
            self.expander_task.cancel()
        
        if self.metrics_task:
            self.metrics_task.cancel()
        
        self.running = False
        
        # Close API client
        await self.riot_api.close()
        
        logger.info("Crawler stopped")
    
    async def reset_failed(self) -> int:
        """Reset failed summoners to pending state.
        
        Returns:
            Number of summoners reset
        """
        db = SessionLocal()
        try:
            # Get failed summoners
            failed = db.query(Summoner).filter(
                Summoner.processing_status == ProcessingStatus.FAILED
            ).all()
            
            # Reset to pending
            for summoner in failed:
                summoner.processing_status = ProcessingStatus.PENDING
            
            db.commit()
            logger.info(f"Reset {len(failed)} failed summoners to pending")
            return len(failed)
        
        finally:
            db.close()
    
    async def get_status(self) -> Dict[str, Any]:
        """Get current crawler status.
        
        Returns:
            Status dictionary
        """
        metrics = await self.update_metrics()
        
        status = {
            "running": self.running,
            "metrics": metrics,
            "config": {
                "match_limit": self.match_limit,
                "max_depth": self.max_depth,
                "batch_size": self.batch_size
            }
        }
        
        return status


# For direct execution
async def main():
    """Run the crawler controller."""
    controller = CrawlerController()
    
    # Add seed summoners
    seeds = [
        ("Faker#KR1", "kr"),
        ("Hide on bush#KR1", "kr"),
        ("Bjergsen#NA1", "na1"),
        ("Rekkles#EUW", "euw1")
    ]
    
    await controller.add_seed_summoners(seeds)
    
    # Start crawler
    await controller.start()
    
    try:
        # Keep running until interrupted
        while True:
            await asyncio.sleep(60)
            status = await controller.get_status()
            print(f"Status: {status}")
    
    except KeyboardInterrupt:
        print("Stopping crawler...")
    
    finally:
        await controller.stop()


if __name__ == "__main__":
    asyncio.run(main())