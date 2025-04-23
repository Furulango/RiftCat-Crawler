from typing import Dict, List, Optional, Any
import asyncio

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query, Path
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.logger import get_logger
from app.core.riot_api import RiotAPI
from app.db.database import get_db, get_db_async
from app.db.models import Summoner, Match, MatchSummoner, ProcessingStatus
from app.db.persistence import SummonerRepository, MatchRepository, CrawlerStateRepository
from app.crawler.controller import CrawlerController
from app.crawler.graph_manager import GraphManager

logger = get_logger(__name__)

# Create router
router = APIRouter()

# Global controller instance
controller = None


async def get_controller():
    """Get or create the crawler controller."""
    global controller
    if controller is None:
        controller = CrawlerController()
    return controller


async def get_graph_manager():
    """Get graph manager instance."""
    return GraphManager()


# Summoner endpoints
@router.get("/summoners", response_model=Dict[str, Any])
async def get_summoners(
    skip: int = 0,
    limit: int = 100,
    status: Optional[str] = None,
    region: Optional[str] = None,
    db: Session = Depends(get_db_async)
):
    """Get summoners with pagination and filtering."""
    query = db.query(Summoner)
    
    # Apply filters
    if status:
        try:
            status_enum = ProcessingStatus(status)
            query = query.filter(Summoner.processing_status == status_enum)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status value: {status}. Valid values: {[s.value for s in ProcessingStatus]}"
            )
    
    if region:
        query = query.filter(Summoner.region == region)
    
    # Get total count for pagination
    total = query.count()
    
    # Apply pagination
    summoners = query.order_by(Summoner.last_updated.desc()).offset(skip).limit(limit).all()
    
    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "data": [
            {
                "id": s.id,
                "puuid": s.puuid,
                "name": s.name,
                "region": s.region,
                "level": s.summoner_level,
                "status": s.processing_status.value,
                "depth": s.processing_depth,
                "matches_analyzed": s.matches_analyzed,
                "last_updated": s.last_updated.isoformat() if s.last_updated else None
            }
            for s in summoners
        ]
    }


@router.get("/summoners/{summoner_id}", response_model=Dict[str, Any])
async def get_summoner(
    summoner_id: str = Path(..., description="The ID of the summoner"),
    db: Session = Depends(get_db_async)
):
    """Get a summoner by ID."""
    summoner = db.query(Summoner).filter(Summoner.id == summoner_id).first()
    if not summoner:
        raise HTTPException(status_code=404, detail="Summoner not found")
    
    return {
        "id": summoner.id,
        "puuid": summoner.puuid,
        "name": summoner.name,
        "region": summoner.region,
        "profile_icon_id": summoner.profile_icon_id,
        "revision_date": summoner.revision_date,
        "level": summoner.summoner_level,
        "status": summoner.processing_status.value,
        "depth": summoner.processing_depth,
        "matches_analyzed": summoner.matches_analyzed,
        "last_updated": summoner.last_updated.isoformat() if summoner.last_updated else None
    }


@router.post("/summoners/add", response_model=Dict[str, Any])
async def add_summoner(
    name: str,
    region: str,
    ctrl: CrawlerController = Depends(get_controller)
):
    """Add a summoner to the crawl list."""
    summoner = await ctrl.add_seed_summoner(name, region)
    
    if not summoner:
        raise HTTPException(status_code=404, detail=f"Summoner {name} not found in region {region}")
    
    return {
        "id": summoner.id,
        "puuid": summoner.puuid,
        "name": summoner.name,
        "region": summoner.region,
        "level": summoner.summoner_level,
        "status": summoner.processing_status.value,
        "added_at": summoner.last_updated.isoformat() if summoner.last_updated else None
    }


@router.get("/summoners/{summoner_id}/matches", response_model=Dict[str, Any])
async def get_summoner_matches(
    summoner_id: str = Path(..., description="The ID of the summoner"),
    limit: int = 10,
    db: Session = Depends(get_db_async)
):
    """Get matches for a summoner."""
    summoner = db.query(Summoner).filter(Summoner.id == summoner_id).first()
    if not summoner:
        raise HTTPException(status_code=404, detail="Summoner not found")
    
    # Get matches
    matches = MatchRepository.get_matches_by_summoner(db, summoner_id, limit)
    
    return {
        "summoner": {
            "id": summoner.id,
            "name": summoner.name,
            "region": summoner.region
        },
        "matches": [
            {
                "id": m.id,
                "game_id": m.game_id,
                "queue_type": m.queue_type.name,
                "game_creation": m.game_creation,
                "game_duration": m.game_duration,
                "game_version": m.game_version,
                "platform_id": m.platform_id
            }
            for m in matches
        ],
        "total": len(matches)
    }


@router.get("/summoners/{summoner_id}/neighbors", response_model=Dict[str, Any])
async def get_summoner_neighbors(
    summoner_id: str = Path(..., description="The ID of the summoner"),
    depth: int = 1,
    graph_manager: GraphManager = Depends(get_graph_manager)
):
    """Get neighbors of a summoner in the relationship graph."""
    neighbors = await graph_manager.get_neighbors(summoner_id, depth)
    
    return {
        "summoner_id": summoner_id,
        "depth": depth,
        "neighbors": [
            {
                "id": n.id,
                "name": n.name,
                "region": n.region,
                "level": n.summoner_level
            }
            for n in neighbors
        ],
        "total": len(neighbors)
    }


# Match endpoints
@router.get("/matches", response_model=Dict[str, Any])
async def get_matches(
    skip: int = 0,
    limit: int = 50,
    queue_type: Optional[str] = None,
    region: Optional[str] = None,
    db: Session = Depends(get_db_async)
):
    """Get matches with pagination and filtering."""
    query = db.query(Match)
    
    # Apply filters
    if queue_type:
        query = query.filter(Match.queue_type.name == queue_type)
    
    if region:
        query = query.filter(Match.platform_id.startswith(region))
    
    # Get total count for pagination
    total = query.count()
    
    # Apply pagination
    matches = query.order_by(Match.game_creation.desc()).offset(skip).limit(limit).all()
    
    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "data": [
            {
                "id": m.id,
                "game_id": m.game_id,
                "queue_type": m.queue_type.name,
                "game_creation": m.game_creation,
                "game_duration": m.game_duration,
                "game_version": m.game_version,
                "platform_id": m.platform_id,
                "has_timeline": m.is_timeline_fetched
            }
            for m in matches
        ]
    }


@router.get("/matches/{match_id}", response_model=Dict[str, Any])
async def get_match(
    match_id: str = Path(..., description="The ID of the match"),
    include_timeline: bool = False,
    db: Session = Depends(get_db_async)
):
    """Get details of a match by ID."""
    match = MatchRepository.get_by_id(db, match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    
    # Get participants
    participants = db.query(MatchSummoner).filter(
        MatchSummoner.match_id == match_id
    ).all()
    
    # Get timeline if requested
    timeline = None
    if include_timeline and match.is_timeline_fetched:
        timeline = match.timeline
    
    return {
        "id": match.id,
        "game_id": match.game_id,
        "queue_type": match.queue_type.name,
        "game_creation": match.game_creation,
        "game_duration": match.game_duration,
        "game_version": match.game_version,
        "platform_id": match.platform_id,
        "teams": match.teams,
        "participants": [
            {
                "summoner_id": p.summoner_id,
                "summoner_name": p.summoner.name,
                "champion_id": p.champion_id,
                "champion_name": p.champion_name,
                "team_id": p.team_id,
                "team_position": p.team_position,
                "kills": p.kills,
                "deaths": p.deaths,
                "assists": p.assists,
                "win": p.win,
                "stats": p.stats
            }
            for p in participants
        ],
        "timeline": {
            "frames": timeline.frames if timeline else None
        } if include_timeline and timeline else None
    }


# Crawler control endpoints
@router.get("/crawler/status", response_model=Dict[str, Any])
async def get_crawler_status(
    ctrl: CrawlerController = Depends(get_controller)
):
    """Get current status of the crawler."""
    status = await ctrl.get_status()
    return status


@router.post("/crawler/start", response_model=Dict[str, Any])
async def start_crawler(
    background_tasks: BackgroundTasks,
    ctrl: CrawlerController = Depends(get_controller)
):
    """Start the crawler."""
    if ctrl.running:
        return {"status": "already_running", "message": "Crawler is already running"}
    
    # Start in background to avoid blocking
    background_tasks.add_task(ctrl.start)
    
    return {"status": "starting", "message": "Crawler is starting"}


@router.post("/crawler/stop", response_model=Dict[str, Any])
async def stop_crawler(
    background_tasks: BackgroundTasks,
    ctrl: CrawlerController = Depends(get_controller)
):
    """Stop the crawler."""
    if not ctrl.running:
        return {"status": "not_running", "message": "Crawler is not running"}
    
    # Stop in background to avoid blocking
    background_tasks.add_task(ctrl.stop)
    
    return {"status": "stopping", "message": "Crawler is stopping"}


@router.post("/crawler/reset", response_model=Dict[str, Any])
async def reset_crawler(
    ctrl: CrawlerController = Depends(get_controller)
):
    """Reset failed summoners to pending state."""
    reset_count = await ctrl.reset_failed()
    
    return {
        "status": "success",
        "reset_count": reset_count,
        "message": f"Reset {reset_count} failed summoners to pending state"
    }


# Graph analytics endpoints
@router.get("/graph/metrics", response_model=Dict[str, Any])
async def get_graph_metrics(
    graph_manager: GraphManager = Depends(get_graph_manager)
):
    """Get metrics about the summoner relationship graph."""
    metrics = await graph_manager.get_graph_metrics()
    return metrics


@router.get("/graph/paths", response_model=Dict[str, Any])
async def find_paths(
    source_id: str,
    target_id: str,
    max_depth: int = 3,
    graph_manager: GraphManager = Depends(get_graph_manager),
    db: Session = Depends(get_db_async)
):
    """Find paths between two summoners in the graph."""
    # Check if summoners exist
    source = db.query(Summoner).filter(Summoner.id == source_id).first()
    target = db.query(Summoner).filter(Summoner.id == target_id).first()
    
    if not source or not target:
        raise HTTPException(status_code=404, detail="Source or target summoner not found")
    
    paths = await graph_manager.find_paths(source_id, target_id, max_depth)
    
    # Get summoner details for paths
    path_details = []
    summoner_cache = {source.id: source, target.id: target}
    
    for path in paths:
        path_with_details = []
        for summoner_id in path:
            if summoner_id not in summoner_cache:
                summoner_cache[summoner_id] = db.query(Summoner).filter(Summoner.id == summoner_id).first()
            
            summoner = summoner_cache.get(summoner_id)
            if summoner:
                path_with_details.append({
                    "id": summoner.id,
                    "name": summoner.name,
                    "region": summoner.region
                })
            else:
                path_with_details.append({"id": summoner_id, "name": "Unknown", "region": "Unknown"})
        
        path_details.append(path_with_details)
    
    return {
        "source": {
            "id": source.id,
            "name": source.name,
            "region": source.region
        },
        "target": {
            "id": target.id,
            "name": target.name,
            "region": target.region
        },
        "max_depth": max_depth,
        "paths_count": len(paths),
        "paths": path_details
    }