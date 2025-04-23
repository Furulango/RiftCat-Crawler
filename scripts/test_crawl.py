#!/usr/bin/env python3
"""
Test script for running a small-scale crawl operation.
"""

import os
import sys
import asyncio
import argparse
import time
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.riot_api import RiotAPI
from app.db.database import init_db, SessionLocal
from app.db.models import Summoner, Match, ProcessingStatus
from app.crawler.controller import CrawlerController
from app.core.logger import setup_logger

# Setup logger
logger = setup_logger("test_crawl")


async def run_test_crawl(
    seed_players: List[tuple],
    duration_minutes: int = 10,
    max_depth: int = 2,
    match_limit: int = 3,
    batch_size: int = 5
) -> Dict[str, Any]:
    """Run a test crawl for a specified duration.
    
    Args:
        seed_players: List of (name, region) tuples
        duration_minutes: How long to run the crawl
        max_depth: Maximum depth for the crawler
        match_limit: Maximum matches per summoner
        batch_size: Batch size for processing
        
    Returns:
        Results dictionary
    """
    logger.info(f"Starting test crawl with {len(seed_players)} seed players")
    logger.info(f"Duration: {duration_minutes} minutes, Max depth: {max_depth}")
    
    # Create controller with custom settings
    controller = CrawlerController(
        max_depth=max_depth,
        match_limit=match_limit,
        batch_size=batch_size
    )
    
    # Add seed players
    for name, region in seed_players:
        await controller.add_seed_summoner(name, region)
    
    # Start the crawler
    await controller.start()
    
    try:
        # Track metrics over time
        start_time = time.time()
        end_time = start_time + (duration_minutes * 60)
        
        metrics_over_time = []
        
        while time.time() < end_time and controller.running:
            # Get current status
            status = await controller.get_status()
            metrics = status["metrics"]
            
            # Log progress
            logger.info(
                f"Progress: Summoners={metrics['total_summoners']} "
                f"(Completed={metrics['completed']}, Pending={metrics['pending']}), "
                f"Depth={metrics['current_depth']}"
            )
            
            # Save metrics
            metrics_over_time.append({
                "timestamp": datetime.now().isoformat(),
                "elapsed_seconds": int(time.time() - start_time),
                **metrics
            })
            
            # Check for completion
            if metrics["pending"] == 0 and metrics["processing"] == 0:
                logger.info("Crawl completed early - no more pending summoners")
                break
            
            # Wait before checking again
            await asyncio.sleep(10)
    
    finally:
        # Stop the crawler
        await controller.stop()
    
    # Get final metrics
    db = SessionLocal()
    try:
        summoners_count = db.query(Summoner).count()
        matches_count = db.query(Match).count()
        completed_count = db.query(Summoner).filter(
            Summoner.processing_status == ProcessingStatus.COMPLETED
        ).count()
        failed_count = db.query(Summoner).filter(
            Summoner.processing_status == ProcessingStatus.FAILED
        ).count()
        
        # Calculate the farthest depths reached
        max_depth_reached = db.query(Summoner).order_by(
            Summoner.processing_depth.desc()
        ).first()
        
        # Get match distribution by queue type
        match_distribution = {}
        for match in db.query(Match).all():
            queue_type = match.queue_type.name
            match_distribution[queue_type] = match_distribution.get(queue_type, 0) + 1
        
        results = {
            "duration_seconds": int(time.time() - start_time),
            "summoners_total": summoners_count,
            "matches_total": matches_count,
            "summoners_completed": completed_count,
            "summoners_failed": failed_count,
            "max_depth_reached": max_depth_reached.processing_depth if max_depth_reached else 0,
            "metrics_over_time": metrics_over_time,
            "match_distribution": match_distribution
        }
        
        return results
    
    finally:
        db.close()


def main():
    """Main function."""
    parser = argparse.ArgumentParser(description="Run a test crawl")
    parser.add_argument(
        "--seed-players", 
        type=str,
        nargs="+",
        default=["Faker:kr", "Bjergsen:na1"],
        help="Seed players in format name:region"
    )
    parser.add_argument(
        "--duration", 
        type=int,
        default=10,
        help="Duration in minutes"
    )
    parser.add_argument(
        "--max-depth", 
        type=int,
        default=2,
        help="Maximum graph depth"
    )
    parser.add_argument(
        "--match-limit", 
        type=int,
        default=3,
        help="Maximum matches per summoner"
    )
    parser.add_argument(
        "--batch-size", 
        type=int,
        default=5,
        help="Batch size for processing"
    )
    parser.add_argument(
        "--init-db",
        action="store_true",
        help="Initialize database before test"
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output file for results (JSON)"
    )
    
    args = parser.parse_args()
    
    # Parse seed players
    seed_players = []
    for player in args.seed_players:
        parts = player.split(":")
        if len(parts) == 2:
            seed_players.append((parts[0], parts[1]))
        else:
            logger.error(f"Invalid seed player format: {player}, expected name:region")
    
    if not seed_players:
        logger.error("No valid seed players provided")
        sys.exit(1)
    
    # Initialize database if requested
    if args.init_db:
        logger.info("Initializing database...")
        init_db()
    
    # Run the crawl
    results = asyncio.run(run_test_crawl(
        seed_players=seed_players,
        duration_minutes=args.duration,
        max_depth=args.max_depth,
        match_limit=args.match_limit,
        batch_size=args.batch_size
    ))
    
    # Print results summary
    logger.info("Test crawl complete")
    logger.info(f"Duration: {results['duration_seconds']} seconds")
    logger.info(f"Summoners: {results['summoners_total']} (Completed: {results['summoners_completed']})")
    logger.info(f"Matches: {results['matches_total']}")
    logger.info(f"Maximum depth reached: {results['max_depth_reached']}")
    
    # Write results to file if requested
    if args.output:
        import json
        with open(args.output, 'w') as f:
            json.dump(results, f, indent=2)
        logger.info(f"Results written to {args.output}")


if __name__ == "__main__":
    main()