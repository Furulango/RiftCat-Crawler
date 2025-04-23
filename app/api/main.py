import uvicorn
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.logger import get_logger, setup_logger
from app.db.database import init_db
from app.api.endpoints import router as api_router

# Setup logger
logger = get_logger(__name__)

# Create FastAPI app
app = FastAPI(
    title="RiftCat API",
    description="API for the RiftCat League of Legends data crawler",
    version="0.1.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API router
app.include_router(api_router, prefix="/api")


@app.on_event("startup")
async def startup_event():
    """Initialize services on startup."""
    logger.info("Starting RiftCat API")
    
    # Initialize the database
    try:
        init_db()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Database initialization failed: {str(e)}")
        # Continue even if DB init fails, as endpoints might still work


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown."""
    logger.info("Shutting down RiftCat API")


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "RiftCat API",
        "version": "0.1.0",
        "status": "online",
        "docs_url": "/docs"
    }


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "services": {
            "api": "up",
            # Add other service statuses here
        }
    }


def start():
    """Start the API server."""
    uvicorn.run(
        "app.api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True  # Set to False in production
    )


if __name__ == "__main__":
    start()