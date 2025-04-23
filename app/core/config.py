import os
from typing import Dict, List, Optional, Any
from pydantic_settings import BaseSettings
from pydantic import field_validator

class Settings(BaseSettings):
    """Application settings with environment variable support."""
    
    # API Configuration
    RIOT_API_KEY: str
    DEFAULT_REGION: str = "na1"
    API_REQUESTS_PER_SECOND: int = 20
    API_REQUESTS_PER_MINUTE: int = 100
    API_TIMEOUT: int = 10
    API_MAX_RETRIES: int = 3
    
    # Database Configuration
    DATABASE_URL: str
    
    # Redis Configuration
    REDIS_URL: str = "redis://redis:6379/0"
    
    # Crawler Configuration
    CRAWLER_MATCH_LIMIT: int = 5  # Max matches per summoner to analyze
    CRAWLER_MAX_DEPTH: int = 3  # Max depth of graph traversal
    CRAWLER_BATCH_SIZE: int = 100  # Number of summoners to process in a batch
    CRAWLER_QUEUE_TYPES: List[int] = [400, 420, 440]  # Ranked queue types
    
    # Logging Configuration
    LOG_LEVEL: str = "INFO"
    LOG_FILE: Optional[str] = "riftcat.log"
    LOG_FORMAT: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    
    # API Paths to Platform Mapping
    PLATFORM_APIS: List[str] = ["match/v5"]
    
    # Region to Platform Mapping
    REGION_TO_PLATFORM: Dict[str, str] = {
        # Americas
        "na1": "americas",
        "br1": "americas",
        "la1": "americas",
        "la2": "americas",
        # Europe
        "euw1": "europe",
        "eun1": "europe",
        "tr1": "europe",
        "ru": "europe",
        # Asia
        "kr": "asia",
        "jp1": "asia",
        # Oceania and others
        "oc1": "sea",
        "ph2": "sea",
        "sg2": "sea",
        "th2": "sea",
        "tw2": "sea",
        "vn2": "sea",
    }
    
    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def validate_database_url(cls, v: Optional[str]) -> str:
        """Validate and construct the database URL if not provided."""
        if isinstance(v, str):
            return v
            
        # Construct from individual components if not provided as a single URL
        user = os.getenv("POSTGRES_USER", "postgres")
        password = os.getenv("POSTGRES_PASSWORD", "postgres")
        host = os.getenv("POSTGRES_HOST", "db")
        port = os.getenv("POSTGRES_PORT", "5432")
        database = os.getenv("POSTGRES_DB", "riftcat")
        
        return f"postgresql://{user}:{password}@{host}:{port}/{database}"
    
    @field_validator("CRAWLER_QUEUE_TYPES", mode="before")
    @classmethod
    def parse_queue_types(cls, v):
        """Parse queue types from string if needed."""
        if isinstance(v, str):
            return [int(x.strip()) for x in v.split(",")]
        return v
    
    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
        "extra": "ignore"  # Ignora campos extras en el archivo .env
    }


# Create global settings instance
settings = Settings()