from datetime import datetime
from typing import Dict, Any

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, String, 
    Integer, JSON, Float, Table, Enum, Text
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
import enum

Base = declarative_base()


# Association table for relationships between summoners
summoner_relations = Table(
    'summoner_relations',
    Base.metadata,
    Column('source_id', String(36), ForeignKey('summoners.id'), primary_key=True),
    Column('target_id', String(36), ForeignKey('summoners.id'), primary_key=True),
    Column('matches_count', Integer, default=0),
    Column('last_updated', DateTime, default=datetime.utcnow)
)


class QueueType(enum.Enum):
    """Queue types in League of Legends."""
    NORMAL_DRAFT = 400
    RANKED_SOLO = 420
    RANKED_FLEX = 440
    ARAM = 450
    CLASH = 700
    OTHER = 0


class ProcessingStatus(enum.Enum):
    """Processing status of entities."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class Summoner(Base):
    """Model for League of Legends summoners."""
    __tablename__ = 'summoners'
    
    # Identifiers
    id = Column(String(36), primary_key=True)  # Riot's summoner ID
    puuid = Column(String(78), unique=True, index=True)
    account_id = Column(String(56), unique=True)
    
    # Basic info
    name = Column(String(128))
    profile_icon_id = Column(Integer)
    revision_date = Column(Integer)
    summoner_level = Column(Integer)
    
    # Region info
    region = Column(String(4))
    
    # Metadata
    last_updated = Column(DateTime, default=datetime.utcnow)
    processing_status = Column(Enum(ProcessingStatus), default=ProcessingStatus.PENDING)
    processing_depth = Column(Integer, default=0)
    matches_analyzed = Column(Integer, default=0)
    
    # Relationships
    matches = relationship("MatchSummoner", back_populates="summoner")
    
    # Connected summoners through relations
    related_summoners = relationship(
        "Summoner",
        secondary=summoner_relations,
        primaryjoin=id == summoner_relations.c.source_id,
        secondaryjoin=id == summoner_relations.c.target_id,
        backref="related_by"
    )
    
    def __repr__(self) -> str:
        return f"<Summoner(name='{self.name}', region='{self.region}', status='{self.processing_status}')>"


class Match(Base):
    """Model for League of Legends matches."""
    __tablename__ = 'matches'
    
    # Match ID (unique)
    id = Column(String(64), primary_key=True)  # Match ID from Riot API
    
    # Basic match info
    game_creation = Column(Integer)  # Timestamp
    game_duration = Column(Integer)  # Seconds
    game_end_timestamp = Column(Integer)  # Timestamp
    game_id = Column(Integer)
    game_mode = Column(String(32))
    game_name = Column(String(128))
    game_type = Column(String(32))
    game_version = Column(String(32))
    platform_id = Column(String(10))
    queue_id = Column(Integer)
    queue_type = Column(Enum(QueueType))
    tournament_code = Column(String(64), nullable=True)
    
    # Match data
    teams = Column(JSON)  # Teams data including bans and objectives
    
    # Processing metadata
    is_timeline_fetched = Column(Boolean, default=False)
    processing_status = Column(Enum(ProcessingStatus), default=ProcessingStatus.PENDING)
    last_updated = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    participants = relationship("MatchSummoner", back_populates="match")
    timeline = relationship("MatchTimeline", uselist=False, back_populates="match")
    
    def __repr__(self) -> str:
        return f"<Match(id='{self.id}', queue={self.queue_id}, duration={self.game_duration})>"


class MatchSummoner(Base):
    """Model for the relationship between summoners and matches."""
    __tablename__ = 'match_summoners'
    
    # Composite primary key
    match_id = Column(String(64), ForeignKey('matches.id'), primary_key=True)
    summoner_id = Column(String(36), ForeignKey('summoners.id'), primary_key=True)
    
    # Participant info
    participant_id = Column(Integer)  # 1-10 player position in match
    team_id = Column(Integer)  # 100 or 200
    
    # Champion info
    champion_id = Column(Integer)
    champion_name = Column(String(32))
    champion_level = Column(Integer)
    
    # Role and position info
    team_position = Column(String(32))  # TOP, JUNGLE, MIDDLE, BOTTOM, UTILITY
    role = Column(String(32))  # SOLO, DUO, DUO_CARRY, DUO_SUPPORT, etc.
    
    # Performance data
    kills = Column(Integer)
    deaths = Column(Integer)
    assists = Column(Integer)
    win = Column(Boolean)
    
    # Detailed stats
    stats = Column(JSON)  # All other participant stats
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    match = relationship("Match", back_populates="participants")
    summoner = relationship("Summoner", back_populates="matches")
    
    def __repr__(self) -> str:
        return f"<MatchSummoner(match='{self.match_id}', summoner='{self.summoner_id}', champion='{self.champion_name}')>"


class MatchTimeline(Base):
    """Model for match timelines."""
    __tablename__ = 'match_timelines'
    
    # Match ID (one-to-one with Match)
    match_id = Column(String(64), ForeignKey('matches.id'), primary_key=True)
    
    # Timeline data
    metadata = Column(JSON)
    frames = Column(JSON)  # Timeline frames with events and participant frames
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationship
    match = relationship("Match", back_populates="timeline")
    
    def __repr__(self) -> str:
        return f"<MatchTimeline(match='{self.match_id}')>"


class CrawlerState(Base):
    """Model to store the state of the crawler."""
    __tablename__ = 'crawler_state'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # State metrics
    active = Column(Boolean, default=True)
    total_summoners = Column(Integer, default=0)
    total_matches = Column(Integer, default=0)
    queue_size = Column(Integer, default=0)
    current_depth = Column(Integer, default=0)
    start_time = Column(DateTime, default=datetime.utcnow)
    last_activity = Column(DateTime, default=datetime.utcnow)
    
    # Configuration snapshot
    configuration = Column(JSON)
    
    # Status message
    status_message = Column(Text)
    
    def __repr__(self) -> str:
        return f"<CrawlerState(active={self.active}, summoners={self.total_summoners}, matches={self.total_matches})>"