from typing import Generator
import logging

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

from app.core.config import settings
from app.core.logger import get_logger

logger = get_logger(__name__)

# Create SQLAlchemy engine
engine = create_engine(
    settings.DATABASE_URL, 
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_recycle=3600
)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for all models
Base = declarative_base()


def init_db() -> None:
    """Initialize the database by creating all tables."""
    try:
        # Import models to register them with Base
        from app.db.models import (
            Summoner, Match, MatchSummoner, MatchTimeline, 
            CrawlerState, summoner_relations, ProcessingStatus, QueueType
        )
        
        # Create all tables
        Base.metadata.create_all(bind=engine)
        
        # Verify tables were created
        from sqlalchemy import inspect
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        logger.info(f"Database tables created successfully: {tables}")
        
        if not tables or len(tables) < 5:
            logger.warning("Not all tables were created! Trying again...")
            Base.metadata.create_all(bind=engine)
            
    except Exception as e:
        logger.error(f"Database initialization failed: {str(e)}")
        raise

def get_db() -> Generator[Session, None, None]:
    """Get a database session.
    
    Yields:
        Database session
        
    Notes:
        This function is intended to be used as a dependency in FastAPI endpoints
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_db_async() -> Generator[Session, None, None]:
    """Async version of get_db for FastAPI dependency injection.
    
    Yields:
        Database session
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()