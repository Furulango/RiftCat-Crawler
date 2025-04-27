from datetime import datetime
from typing import Dict, List, Optional, Union, Any, Tuple

from sqlalchemy import and_, or_, func, update
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.core.logger import get_logger
from app.db.models import (
    Summoner, Match, MatchSummoner, MatchTimeline, 
    CrawlerState, ProcessingStatus, QueueType
)

logger = get_logger(__name__)


class SummonerRepository:
    """Repository for Summoner operations."""
    
    @staticmethod
    def create_or_update(
        db: Session, 
        summoner_data: Dict[str, Any], 
        region: str,
        game_name: Optional[str] = None,
        tag_line: Optional[str] = None
    ) -> Optional[Summoner]:
        """Create or update a summoner.
        
        Args:
            db: Database session
            summoner_data: Summoner data from Riot API
            region: Summoner's region
            game_name: Game name from Riot ID
            tag_line: Tag line from Riot ID
            
        Returns:
            Created or updated summoner
        """
        try:
            # Check if summoner exists by puuid
            summoner = db.query(Summoner).filter(Summoner.puuid == summoner_data.get('puuid')).first()
            
            if summoner:
                # Update existing summoner
                for key, value in summoner_data.items():
                    if hasattr(summoner, key) and key not in ['gameName', 'tagLine']:
                        setattr(summoner, key, value)
                
                # Update Riot ID fields
                if game_name is not None:
                    summoner.game_name = game_name
                elif summoner_data.get('gameName'):
                    summoner.game_name = summoner_data.get('gameName')
                    
                if tag_line is not None:
                    summoner.tag_line = tag_line
                elif summoner_data.get('tagLine'):
                    summoner.tag_line = summoner_data.get('tagLine')
                
                summoner.region = region
                summoner.last_updated = datetime.utcnow()
            else:
                # Create new summoner
                summoner = Summoner(
                    id=summoner_data.get('id'),
                    puuid=summoner_data.get('puuid'),
                    account_id=summoner_data.get('accountId'),
                    name=summoner_data.get('name'),
                    profile_icon_id=summoner_data.get('profileIconId'),
                    revision_date=summoner_data.get('revisionDate'),
                    summoner_level=summoner_data.get('summonerLevel'),
                    game_name=game_name or summoner_data.get('gameName'),
                    tag_line=tag_line or summoner_data.get('tagLine'),
                    region=region,
                    processing_status=ProcessingStatus.PENDING,
                    processing_depth=0,
                    matches_analyzed=0
                )
                db.add(summoner)
            
            db.commit()
            db.refresh(summoner)
            return summoner
        
        except SQLAlchemyError as e:
            db.rollback()
            logger.error(f"Error creating/updating summoner: {str(e)}")
            return None
    
    @staticmethod
    def get_by_puuid(db: Session, puuid: str) -> Optional[Summoner]:
        """Get a summoner by PUUID.
        
        Args:
            db: Database session
            puuid: Summoner's PUUID
            
        Returns:
            Summoner if found, None otherwise
        """
        return db.query(Summoner).filter(Summoner.puuid == puuid).first()
    
    @staticmethod
    def get_by_riot_id(db: Session, game_name: str, tag_line: str, region: Optional[str] = None) -> Optional[Summoner]:
        """Get a summoner by Riot ID components.
        
        Args:
            db: Database session
            game_name: Game name
            tag_line: Tag line
            region: Optional region filter
            
        Returns:
            Summoner if found, None otherwise
        """
        query = db.query(Summoner).filter(
            Summoner.game_name == game_name,
            Summoner.tag_line == tag_line
        )
        
        if region:
            query = query.filter(Summoner.region == region)
            
        return query.first()
    
    @staticmethod
    def get_by_name_and_region(db: Session, name: str, region: str) -> Optional[Summoner]:
        """Get a summoner by name and region.
        
        Args:
            db: Database session
            name: Summoner's name with tag (format: name#tag)
            region: Summoner's region
            
        Returns:
            Summoner if found, None otherwise
        """
        # Verify that the format is correct
        if '#' not in name:
            logger.error(f"Invalid summoner format: {name}. Must use 'name#tag' format.")
            return None
            
        game_name, tag_line = name.split('#', 1)
        return SummonerRepository.get_by_riot_id(db, game_name, tag_line, region)
    
    @staticmethod
    def get_pending_summoners(
        db: Session, 
        limit: int = 100, 
        max_depth: int = 3
    ) -> List[Summoner]:
        """Get summoners pending processing.
        
        Args:
            db: Database session
            limit: Maximum number of summoners to fetch
            max_depth: Maximum depth to consider
            
        Returns:
            List of pending summoners
        """
        return db.query(Summoner).filter(
            Summoner.processing_status == ProcessingStatus.PENDING,
            Summoner.processing_depth <= max_depth,
            # Ensure they have Riot ID
            Summoner.game_name.isnot(None),
            Summoner.tag_line.isnot(None)
        ).order_by(Summoner.processing_depth.asc()).limit(limit).all()
    
    @staticmethod
    def update_status(
        db: Session, 
        summoner_id: str, 
        status: ProcessingStatus,
        matches_analyzed: Optional[int] = None,
        increment_depth: bool = False
    ) -> bool:
        """Update a summoner's processing status.
        
        Args:
            db: Database session
            summoner_id: Summoner ID
            status: New processing status
            matches_analyzed: Number of matches analyzed (if provided)
            increment_depth: Whether to increment the processing depth
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Start with base updates
            updates = {
                "processing_status": status,
                "last_updated": datetime.utcnow()
            }
            
            # Add optional updates
            if matches_analyzed is not None:
                updates["matches_analyzed"] = matches_analyzed
            
            # Get the summoner
            summoner = db.query(Summoner).filter(Summoner.id == summoner_id).first()
            if not summoner:
                logger.warning(f"Summoner not found for status update: {summoner_id}")
                return False
                
            # Verify that the summoner has a Riot ID
            if not summoner.game_name or not summoner.tag_line:
                logger.warning(f"Summoner {summoner_id} does not have a valid Riot ID")
                return False
            
            # Apply updates
            for key, value in updates.items():
                setattr(summoner, key, value)
            
            # Increment depth if requested
            if increment_depth:
                summoner.processing_depth += 1
            
            db.commit()
            return True
        
        except SQLAlchemyError as e:
            db.rollback()
            logger.error(f"Error updating summoner status: {str(e)}")
            return False
    
    @staticmethod
    def add_relation(
        db: Session, 
        source_id: str, 
        target_id: str
    ) -> bool:
        """Add or update a relation between two summoners.
        
        Args:
            db: Database session
            source_id: Source summoner ID
            target_id: Target summoner ID
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # First check if both summoners exist
            source = db.query(Summoner).filter(Summoner.id == source_id).first()
            target = db.query(Summoner).filter(Summoner.id == target_id).first()
            
            if not source or not target:
                logger.warning(f"Cannot create relation: summoner not found ({source_id} -> {target_id})")
                return False
                
            # Verify that both have Riot ID
            if not source.game_name or not source.tag_line:
                logger.warning(f"Source summoner {source_id} does not have a valid Riot ID")
                return False
                
            if not target.game_name or not target.tag_line:
                logger.warning(f"Target summoner {target_id} does not have a valid Riot ID")
                return False
            
            # Check if relation exists
            if target not in source.related_summoners:
                source.related_summoners.append(target)
                db.commit()
            
            return True
        
        except SQLAlchemyError as e:
            db.rollback()
            logger.error(f"Error adding summoner relation: {str(e)}")
            return False
