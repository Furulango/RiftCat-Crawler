from typing import Dict, List, Optional, Any, Set
from datetime import datetime, timedelta

from app.core.config import settings
from app.core.logger import get_logger

logger = get_logger(__name__)


class MatchFilter:
    """Filter for deciding which matches to process."""
    
    def __init__(
        self,
        queue_types: Optional[List[int]] = None,
        min_duration: int = 900,  # 15 minutes in seconds
        max_days_ago: int = 30
    ):
        """Initialize the match filter.
        
        Args:
            queue_types: List of queue types to include
            min_duration: Minimum match duration in seconds
            max_days_ago: Maximum age of matches in days
        """
        self.queue_types = queue_types or settings.CRAWLER_QUEUE_TYPES
        self.min_duration = min_duration
        self.max_days_ago = max_days_ago
    
    def should_process_match(self, match_data: Dict[str, Any]) -> bool:
        """Determine if a match should be processed.
        
        Args:
            match_data: Match data from Riot API
            
        Returns:
            True if the match should be processed
        """
        try:
            # Extract match info
            info = match_data.get('info', {})
            
            # Check queue type
            queue_id = info.get('queueId', 0)
            if self.queue_types and queue_id not in self.queue_types:
                logger.debug(f"Filtering out match {match_data.get('metadata', {}).get('matchId')}: queue {queue_id} not in allowed types")
                return False
            
            # Check duration
            duration = info.get('gameDuration', 0)
            if duration < self.min_duration:
                logger.debug(f"Filtering out match {match_data.get('metadata', {}).get('matchId')}: duration {duration}s too short")
                return False
            
            # Check age
            creation = info.get('gameCreation', 0)
            if creation:
                match_date = datetime.fromtimestamp(creation / 1000)  # Convert from milliseconds to seconds
                days_ago = (datetime.now() - match_date).days
                
                if days_ago > self.max_days_ago:
                    logger.debug(f"Filtering out match {match_data.get('metadata', {}).get('matchId')}: {days_ago} days ago > {self.max_days_ago}")
                    return False
            
            # Check if there are proper participants
            participants = info.get('participants', [])
            if len(participants) < 10:  # Most game modes have 10 players
                logger.debug(f"Filtering out match {match_data.get('metadata', {}).get('matchId')}: only {len(participants)} participants")
                return False
            
            return True
        
        except Exception as e:
            logger.error(f"Error in match filter: {str(e)}")
            return False
    
    def filter_match_ids(self, match_ids: List[str], match_data: Optional[Dict[str, Dict]] = None) -> List[str]:
        """Filter a list of match IDs.
        
        Args:
            match_ids: List of match IDs
            match_data: Optional dictionary of match data keyed by match ID
            
        Returns:
            Filtered list of match IDs
        """
        if not match_data:
            # Without match data, we can't filter
            return match_ids
        
        filtered_ids = []
        
        for match_id in match_ids:
            if match_id in match_data:
                if self.should_process_match(match_data[match_id]):
                    filtered_ids.append(match_id)
            else:
                # If we don't have data, include it to be safe
                filtered_ids.append(match_id)
        
        logger.debug(f"Filtered {len(match_ids) - len(filtered_ids)} matches out of {len(match_ids)}")
        return filtered_ids


class SummonerFilter:
    """Filter for deciding which summoners to process."""
    
    def __init__(
        self,
        min_level: int = 30,
        max_summoners_per_region: Optional[int] = None,
        excluded_regions: Optional[Set[str]] = None
    ):
        """Initialize the summoner filter.
        
        Args:
            min_level: Minimum summoner level
            max_summoners_per_region: Maximum summoners per region
            excluded_regions: Set of excluded region codes
        """
        self.min_level = min_level
        self.max_summoners_per_region = max_summoners_per_region
        self.excluded_regions = excluded_regions or set()
        
        # Track summoners per region
        self.region_counts: Dict[str, int] = {}
    
    def should_process_summoner(self, summoner_data: Dict[str, Any], region: str) -> bool:
        """Determine if a summoner should be processed.
        
        Args:
            summoner_data: Summoner data from Riot API
            region: Summoner's region
            
        Returns:
            True if the summoner should be processed
        """
        try:
            # Check level
            level = summoner_data.get('summonerLevel', 0)
            if level < self.min_level:
                logger.debug(f"Filtering out summoner {summoner_data.get('name')}: level {level} < {self.min_level}")
                return False
            
            # Check excluded regions
            if region in self.excluded_regions:
                logger.debug(f"Filtering out summoner {summoner_data.get('name')}: region {region} is excluded")
                return False
            
            # Check region limits
            if self.max_summoners_per_region:
                current_count = self.region_counts.get(region, 0)
                if current_count >= self.max_summoners_per_region:
                    logger.debug(f"Filtering out summoner {summoner_data.get('name')}: region {region} at capacity")
                    return False
                
                # Increment count
                self.region_counts[region] = current_count + 1
            
            return True
        
        except Exception as e:
            logger.error(f"Error in summoner filter: {str(e)}")
            return False
    
    def filter_summoners(
        self, 
        summoners: List[Dict[str, Any]], 
        regions: List[str]
    ) -> List[Dict[str, Any]]:
        """Filter a list of summoners.
        
        Args:
            summoners: List of summoner data dictionaries
            regions: List of regions corresponding to each summoner
            
        Returns:
            Filtered list of summoner data
        """
        if len(summoners) != len(regions):
            logger.warning(f"Mismatch between summoners ({len(summoners)}) and regions ({len(regions)})")
            return []
        
        filtered = []
        
        for summoner, region in zip(summoners, regions):
            if self.should_process_summoner(summoner, region):
                filtered.append(summoner)
        
        logger.debug(f"Filtered {len(summoners) - len(filtered)} summoners out of {len(summoners)}")
        return filtered
    
    def reset_counts(self) -> None:
        """Reset region counts."""
        self.region_counts = {}