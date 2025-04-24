from sqlalchemy import create_engine
from app.db.models import Base
from app.core.logger import get_logger

# Importar todos los modelos para que se registren con Base
from app.db.models import Summoner, Match, MatchSummoner, MatchTimeline, CrawlerState, summoner_relations, ProcessingStatus, QueueType

# Crear logger
logger = get_logger(__name__)

# URL directa para evitar problemas con variables de entorno
DATABASE_URL = 'postgresql://postgres:postgres@db:5432/riftcat'

try:
    # Crear motor y tablas
    engine = create_engine(DATABASE_URL)
    Base.metadata.create_all(bind=engine)
    print('¡Tablas creadas exitosamente!')
except Exception as e:
    print(f'Error al crear tablas: {str(e)}')