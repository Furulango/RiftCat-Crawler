import logging
import logging.handlers
import os
import sys
from typing import Optional

from app.core.config import settings


def setup_logger(
    name: str,
    level: Optional[str] = None,
    log_file: Optional[str] = None,
    log_format: Optional[str] = None
) -> logging.Logger:
    """Setup and configure a logger.
    
    Args:
        name: Logger name
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Path to log file
        log_format: Log format string
        
    Returns:
        Configured logger instance
    """
    # Use settings if parameters are not provided
    level = level or settings.LOG_LEVEL
    log_file = log_file or settings.LOG_FILE
    log_format = log_format or settings.LOG_FORMAT
    
    # Create logger
    logger = logging.getLogger(name)
    
    # Set log level
    numeric_level = getattr(logging, level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError(f"Invalid log level: {level}")
    
    logger.setLevel(numeric_level)
    
    # Create formatter
    formatter = logging.Formatter(log_format)
    
    # Add console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # Add file handler if file is specified
    if log_file:
        # Create logs directory if it doesn't exist
        log_dir = os.path.dirname(log_file)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir)
            
        # Create rotating file handler (10 MB max, keep 5 backups)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
            encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    return logger


# Create root logger
root_logger = setup_logger("riftcat")

# Logger creation function for modules
def get_logger(name: str) -> logging.Logger:
    """Get a logger for a module.
    
    Args:
        name: Module name
        
    Returns:
        Module logger
    """
    return logging.getLogger(f"riftcat.{name}")