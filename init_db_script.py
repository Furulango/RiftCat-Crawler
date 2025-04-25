from sqlalchemy import create_engine
from app.db.models import Base
from app.core.logger import get_logger

# Import all models so they are registered with Base
from app.db.models import Summoner, Match, MatchSummoner, MatchTimeline, CrawlerState, summoner_relations, ProcessingStatus, QueueType

# Create logger
logger = get_logger(__name__)

# Direct URL to avoid issues with environment variables
DATABASE_URL = 'postgresql://postgres:postgres@db:5432/riftcat'

try:
    # Create engine and tables
    engine = create_engine(DATABASE_URL)
    Base.metadata.create_all(bind=engine)
    print('Tables created successfully!')
except Exception as e:
    print(f'Error creating tables: {str(e)}')