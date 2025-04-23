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
    def create_or_update(db: Session, summoner_data: Dict[str, Any], region: str) -> Optional[Summoner]:
        """Create or update a summoner.
        
        Args:
            db: Database session
            summoner_data: Summoner data from Riot API
            region: Summoner's region
            
        Returns:
            Created or updated summoner
        """
        try:
            # Check if summoner exists by puuid
            summoner = db.query(Summoner).filter(Summoner.puuid == summoner_data.get('puuid')).first()
            
            if summoner:
                # Update existing summoner
                for key, value in summoner_data.items():
                    if hasattr(summoner, key):
                        setattr(summoner, key, value)
                
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
    def get_by_name_and_region(db: Session, name: str, region: str) -> Optional[Summoner]:
        """Get a summoner by name and region.
        
        Args:
            db: Database session
            name: Summoner's name
            region: Summoner's region
            
        Returns:
            Summoner if found, None otherwise
        """
        return db.query(Summoner).filter(
            Summoner.name == name, 
            Summoner.region == region
        ).first()
    
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
            Summoner.processing_depth <= max_depth
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
            
            # Check if relation exists
            if target not in source.related_summoners:
                source.related_summoners.append(target)
                db.commit()
            
            return True
        
        except SQLAlchemyError as e:
            db.rollback()
            logger.error(f"Error adding summoner relation: {str(e)}")
            return False


class MatchRepository:
    """Repository for Match operations."""
    
    @staticmethod
    def create_or_update(db: Session, match_data: Dict[str, Any]) -> Optional[Match]:
        """Create or update a match.
        
        Args:
            db: Database session
            match_data: Match data from Riot API
            
        Returns:
            Created or updated match
        """
        try:
            # Extract match info
            match_id = match_data.get('metadata', {}).get('matchId')
            if not match_id:
                logger.error("Match data does not contain a match ID")
                return None
            
            # Check if match exists
            match = db.query(Match).filter(Match.id == match_id).first()
            
            # Get match info
            info = match_data.get('info', {})
            queue_id = info.get('queueId', 0)
            
            # Map queue ID to queue type
            queue_type = QueueType.OTHER
            if queue_id == 400:
                queue_type = QueueType.NORMAL_DRAFT
            elif queue_id == 420:
                queue_type = QueueType.RANKED_SOLO
            elif queue_id == 440:
                queue_type = QueueType.RANKED_FLEX
            elif queue_id == 450:
                queue_type = QueueType.ARAM
            elif queue_id == 700:
                queue_type = QueueType.CLASH
            
            if match:
                # Update existing match
                match.game_creation = info.get('gameCreation')
                match.game_duration = info.get('gameDuration')
                match.game_end_timestamp = info.get('gameEndTimestamp')
                match.game_id = info.get('gameId')
                match.game_mode = info.get('gameMode')
                match.game_name = info.get('gameName')
                match.game_type = info.get('gameType')
                match.game_version = info.get('gameVersion')
                match.platform_id = info.get('platformId')
                match.queue_id = queue_id
                match.queue_type = queue_type
                match.tournament_code = info.get('tournamentCode')
                match.teams = info.get('teams', [])
                match.last_updated = datetime.utcnow()
                match.processing_status = ProcessingStatus.COMPLETED
            else:
                # Create new match
                match = Match(
                    id=match_id,
                    game_creation=info.get('gameCreation'),
                    game_duration=info.get('gameDuration'),
                    game_end_timestamp=info.get('gameEndTimestamp'),
                    game_id=info.get('gameId'),
                    game_mode=info.get('gameMode'),
                    game_name=info.get('gameName'),
                    game_type=info.get('gameType'),
                    game_version=info.get('gameVersion'),
                    platform_id=info.get('platformId'),
                    queue_id=queue_id,
                    queue_type=queue_type,
                    tournament_code=info.get('tournamentCode'),
                    teams=info.get('teams', []),
                    processing_status=ProcessingStatus.COMPLETED
                )
                db.add(match)
            
            db.commit()
            db.refresh(match)
            
            # Save participant data
            participants = info.get('participants', [])
            for participant in participants:
                puuid = participant.get('puuid')
                if not puuid:
                    continue
                
                # Get or create summoner (minimal info)
                summoner = db.query(Summoner).filter(Summoner.puuid == puuid).first()
                if not summoner:
                    # Create placeholder summoner to be filled later
                    summoner = Summoner(
                        id=participant.get('summonerId', f"placeholder_{puuid[:8]}"),
                        puuid=puuid,
                        account_id=participant.get('accountId', f"placeholder_{puuid[:8]}"),
                        name=participant.get('summonerName', 'Unknown'),
                        profile_icon_id=0,
                        revision_date=0,
                        summoner_level=0,
                        region=match.platform_id[:4] if match.platform_id else 'na1',
                        processing_status=ProcessingStatus.PENDING,
                        processing_depth=0,
                        matches_analyzed=0
                    )
                    db.add(summoner)
                    db.flush()  # Get ID without committing
                
                # Create or update match-summoner relationship
                match_summoner = db.query(MatchSummoner).filter(
                    MatchSummoner.match_id == match_id,
                    MatchSummoner.summoner_id == summoner.id
                ).first()
                
                # Extract stats without duplicating all fields
                stats = {k: v for k, v in participant.items() if k not in [
                    'championId', 'championName', 'championLevel', 'teamId',
                    'teamPosition', 'role', 'kills', 'deaths', 'assists', 'win',
                    'participantId', 'puuid', 'summonerId', 'summonerName'
                ]}
                
                if match_summoner:
                    # Update existing relationship
                    match_summoner.participant_id = participant.get('participantId')
                    match_summoner.team_id = participant.get('teamId')
                    match_summoner.champion_id = participant.get('championId')
                    match_summoner.champion_name = participant.get('championName')
                    match_summoner.champion_level = participant.get('championLevel')
                    match_summoner.team_position = participant.get('teamPosition')
                    match_summoner.role = participant.get('role')
                    match_summoner.kills = participant.get('kills')
                    match_summoner.deaths = participant.get('deaths')
                    match_summoner.assists = participant.get('assists')
                    match_summoner.win = participant.get('win')
                    match_summoner.stats = stats
                else:
                    # Create new relationship
                    match_summoner = MatchSummoner(
                        match_id=match_id,
                        summoner_id=summoner.id,
                        participant_id=participant.get('participantId'),
                        team_id=participant.get('teamId'),
                        champion_id=participant.get('championId'),
                        champion_name=participant.get('championName'),
                        champion_level=participant.get('championLevel'),
                        team_position=participant.get('teamPosition'),
                        role=participant.get('role'),
                        kills=participant.get('kills'),
                        deaths=participant.get('deaths'),
                        assists=participant.get('assists'),
                        win=participant.get('win'),
                        stats=stats
                    )
                    db.add(match_summoner)
            
            db.commit()
            return match
        
        except SQLAlchemyError as e:
            db.rollback()
            logger.error(f"Error creating/updating match: {str(e)}")
            return None
    
    @staticmethod
    def save_timeline(db: Session, match_id: str, timeline_data: Dict[str, Any]) -> Optional[MatchTimeline]:
        """Save match timeline data.
        
        Args:
            db: Database session
            match_id: Match ID
            timeline_data: Timeline data from Riot API
            
        Returns:
            Created or updated timeline
        """
        try:
            # Check if match exists
            match = db.query(Match).filter(Match.id == match_id).first()
            if not match:
                logger.error(f"Cannot save timeline: match not found ({match_id})")
                return None
            
            # Check if timeline exists
            timeline = db.query(MatchTimeline).filter(MatchTimeline.match_id == match_id).first()
            
            if timeline:
                # Update existing timeline
                timeline.metadata = timeline_data.get('metadata', {})
                timeline.frames = timeline_data.get('info', {}).get('frames', [])
            else:
                # Create new timeline
                timeline = MatchTimeline(
                    match_id=match_id,
                    metadata=timeline_data.get('metadata', {}),
                    frames=timeline_data.get('info', {}).get('frames', [])
                )
                db.add(timeline)
            
            # Update match to mark timeline as fetched
            match.is_timeline_fetched = True
            
            db.commit()
            db.refresh(timeline)
            return timeline
        
        except SQLAlchemyError as e:
            db.rollback()
            logger.error(f"Error saving match timeline: {str(e)}")
            return None
    
    @staticmethod
    def get_by_id(db: Session, match_id: str) -> Optional[Match]:
        """Get a match by ID.
        
        Args:
            db: Database session
            match_id: Match ID
            
        Returns:
            Match if found, None otherwise
        """
        return db.query(Match).filter(Match.id == match_id).first()
    
    @staticmethod
    def get_matches_by_summoner(
        db: Session,
        summoner_id: str,
        limit: int = 20
    ) -> List[Match]:
        """Get matches for a summoner.
        
        Args:
            db: Database session
            summoner_id: Summoner ID
            limit: Maximum number of matches to fetch
            
        Returns:
            List of matches
        """
        return db.query(Match).join(MatchSummoner).filter(
            MatchSummoner.summoner_id == summoner_id
        ).order_by(Match.game_end_timestamp.desc()).limit(limit).all()


class CrawlerStateRepository:
    """Repository for CrawlerState operations."""
    
    @staticmethod
    def get_or_create(db: Session) -> CrawlerState:
        """Get or create crawler state.
        
        Args:
            db: Database session
            
        Returns:
            Current crawler state
        """
        state = db.query(CrawlerState).first()
        
        if not state:
            state = CrawlerState(
                active=True,
                total_summoners=0,
                total_matches=0,
                queue_size=0,
                current_depth=0,
                status_message="Crawler initialized"
            )
            db.add(state)
            db.commit()
            db.refresh(state)
        
        return state
    
    @staticmethod
    def update_metrics(
        db: Session,
        total_summoners: Optional[int] = None,
        total_matches: Optional[int] = None,
        queue_size: Optional[int] = None,
        current_depth: Optional[int] = None,
        active: Optional[bool] = None,
        status_message: Optional[str] = None
    ) -> bool:
        """Update crawler state metrics.
        
        Args:
            db: Database session
            total_summoners: Total number of summoners
            total_matches: Total number of matches
            queue_size: Current queue size
            current_depth: Current depth level
            active: Whether the crawler is active
            status_message: Status message
            
        Returns:
            True if successful, False otherwise
        """
        try:
            state = CrawlerStateRepository.get_or_create(db)
            
            # Update provided fields
            if total_summoners is not None:
                state.total_summoners = total_summoners
            
            if total_matches is not None:
                state.total_matches = total_matches
            
            if queue_size is not None:
                state.queue_size = queue_size
            
            if current_depth is not None:
                state.current_depth = current_depth
            
            if active is not None:
                state.active = active
            
            if status_message is not None:
                state.status_message = status_message
            
            # Always update last activity
            state.last_activity = datetime.utcnow()
            
            db.commit()
            return True
        
        except SQLAlchemyError as e:
            db.rollback()
            logger.error(f"Error updating crawler state: {str(e)}")
            return False