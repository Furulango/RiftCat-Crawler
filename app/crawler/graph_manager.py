import json
from typing import Dict, List, Optional, Any, Set, Tuple
from datetime import datetime
import asyncio

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.logger import get_logger
from app.db.database import SessionLocal
from app.db.models import Summoner, Match, MatchSummoner, summoner_relations

logger = get_logger(__name__)


class GraphManager:
    """Manager for the summoner relationship graph."""
    
    def __init__(self):
        """Initialize the graph manager."""
        pass
    
    async def get_neighbors(self, summoner_id: str, depth: int = 1) -> List[Summoner]:
        """Get neighbors of a summoner in the graph.
        
        Args:
            summoner_id: Summoner ID
            depth: Maximum depth for traversal
            
        Returns:
            List of neighboring summoners
        """
        db = SessionLocal()
        try:
            if depth <= 0:
                return []
            
            # First level neighbors
            neighbors = db.query(Summoner).join(
                summoner_relations,
                ((Summoner.id == summoner_relations.c.target_id) &
                 (summoner_relations.c.source_id == summoner_id)) |
                ((Summoner.id == summoner_relations.c.source_id) &
                 (summoner_relations.c.target_id == summoner_id))
            ).all()
            
            if depth == 1:
                return neighbors
            
            # Recursively get more levels
            all_neighbors = set(neighbor.id for neighbor in neighbors)
            all_neighbors.add(summoner_id)  # Add the original summoner
            
            # Process deeper levels
            for level in range(2, depth + 1):
                new_neighbors = []
                
                for neighbor in neighbors:
                    next_level = await self.get_neighbors(neighbor.id, 1)
                    for summoner in next_level:
                        if summoner.id not in all_neighbors:
                            new_neighbors.append(summoner)
                            all_neighbors.add(summoner.id)
                
                neighbors.extend(new_neighbors)
                
                # If no new neighbors, stop early
                if not new_neighbors:
                    break
            
            return neighbors
        
        finally:
            db.close()
    
    async def get_common_matches(self, summoner1_id: str, summoner2_id: str) -> List[Match]:
        """Get matches played together by two summoners.
        
        Args:
            summoner1_id: First summoner ID
            summoner2_id: Second summoner ID
            
        Returns:
            List of common matches
        """
        db = SessionLocal()
        try:
            # Get matches where both summoners participated
            common_matches = db.query(Match).join(
                MatchSummoner, Match.id == MatchSummoner.match_id
            ).filter(
                MatchSummoner.summoner_id == summoner1_id
            ).join(
                MatchSummoner, Match.id == MatchSummoner.match_id
            ).filter(
                MatchSummoner.summoner_id == summoner2_id
            ).all()
            
            return common_matches
        
        finally:
            db.close()
    
    async def get_graph_metrics(self) -> Dict[str, Any]:
        """Get metrics about the graph structure.
        
        Returns:
            Dictionary with graph metrics
        """
        db = SessionLocal()
        try:
            # Total number of summoners (nodes)
            total_summoners = db.query(Summoner).count()
            
            # Total number of relationships (edges)
            total_relations = db.query(summoner_relations).count()
            
            # Average connections per summoner
            avg_connections = total_relations / total_summoners if total_summoners > 0 else 0
            
            # Find most connected summoner
            most_connected_query = text("""
                SELECT s.id, s.name, s.region, COUNT(*) as connection_count
                FROM summoners s
                JOIN summoner_relations sr ON s.id = sr.source_id OR s.id = sr.target_id
                GROUP BY s.id, s.name, s.region
                ORDER BY connection_count DESC
                LIMIT 1
            """)
            
            most_connected_result = db.execute(most_connected_query).fetchone()
            most_connected = {
                "id": most_connected_result[0],
                "name": most_connected_result[1],
                "region": most_connected_result[2],
                "connections": most_connected_result[3]
            } if most_connected_result else None
            
            # Calculate connected components (requires advanced graph algorithm)
            # For simplicity, we'll just count isolated summoners
            isolated_count = db.query(Summoner).outerjoin(
                summoner_relations,
                (Summoner.id == summoner_relations.c.source_id) |
                (Summoner.id == summoner_relations.c.target_id)
            ).filter(
                summoner_relations.c.source_id.is_(None),
                summoner_relations.c.target_id.is_(None)
            ).count()
            
            return {
                "total_summoners": total_summoners,
                "total_relations": total_relations,
                "avg_connections": avg_connections,
                "most_connected": most_connected,
                "isolated_count": isolated_count,
                "timestamp": datetime.utcnow().isoformat()
            }
        
        finally:
            db.close()
    
    async def export_graph(self, file_path: str, max_nodes: int = 1000) -> bool:
        """Export the graph to a JSON file.
        
        Args:
            file_path: Path to output file
            max_nodes: Maximum number of nodes to export
            
        Returns:
            True if successful
        """
        db = SessionLocal()
        try:
            # Get nodes (summoners)
            nodes = db.query(Summoner).limit(max_nodes).all()
            node_ids = [node.id for node in nodes]
            
            # Get edges (relations)
            edges = db.query(summoner_relations).filter(
                summoner_relations.c.source_id.in_(node_ids),
                summoner_relations.c.target_id.in_(node_ids)
            ).all()
            
            # Create graph structure
            graph = {
                "nodes": [
                    {
                        "id": node.id,
                        "name": node.name,
                        "region": node.region,
                        "level": node.summoner_level,
                        "depth": node.processing_depth
                    }
                    for node in nodes
                ],
                "edges": [
                    {
                        "source": edge.source_id,
                        "target": edge.target_id,
                        "weight": edge.matches_count
                    }
                    for edge in edges
                ],
                "metadata": {
                    "nodes_count": len(nodes),
                    "edges_count": len(edges),
                    "export_date": datetime.utcnow().isoformat(),
                    "max_nodes": max_nodes
                }
            }
            
            # Write to file
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(graph, f, indent=2)
            
            logger.info(f"Graph exported to {file_path} with {len(nodes)} nodes and {len(edges)} edges")
            return True
        
        except Exception as e:
            logger.error(f"Error exporting graph: {str(e)}")
            return False
        
        finally:
            db.close()
    
    async def find_paths(self, source_id: str, target_id: str, max_depth: int = 3) -> List[List[str]]:
        """Find paths between two summoners.
        
        Args:
            source_id: Source summoner ID
            target_id: Target summoner ID
            max_depth: Maximum path depth
            
        Returns:
            List of paths (each path is a list of summoner IDs)
        """
        db = SessionLocal()
        try:
            # BFS to find paths
            paths = []
            visited = set()
            queue = [(source_id, [source_id])]
            
            while queue:
                current_id, path = queue.pop(0)
                
                # If we found the target
                if current_id == target_id:
                    paths.append(path)
                    continue
                
                # If we reached max depth
                if len(path) > max_depth:
                    continue
                
                # Skip if already visited
                if current_id in visited:
                    continue
                
                visited.add(current_id)
                
                # Get neighbors
                neighbors = db.query(Summoner).join(
                    summoner_relations,
                    ((Summoner.id == summoner_relations.c.target_id) &
                     (summoner_relations.c.source_id == current_id)) |
                    ((Summoner.id == summoner_relations.c.source_id) &
                     (summoner_relations.c.target_id == current_id))
                ).all()
                
                # Add to queue
                for neighbor in neighbors:
                    if neighbor.id not in path:
                        queue.append((neighbor.id, path + [neighbor.id]))
            
            return paths
        
        finally:
            db.close()


# For direct execution
async def main():
    """Run the graph manager."""
    manager = GraphManager()
    
    # Get graph metrics
    metrics = await manager.get_graph_metrics()
    print(f"Graph metrics: {metrics}")
    
    # Export graph
    await manager.export_graph("summoner_graph.json")


if __name__ == "__main__":
    asyncio.run(main())