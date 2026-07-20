"""
LifeOS – Learning Path Discovery Service
Analyzes the Knowledge Graph to automatically infer learning paths.

Pipeline:
  1. Cluster related knowledge nodes by category + edge connectivity
  2. Detect dominant domain per cluster
  3. Order nodes by first_seen_at (temporal progression)
  4. Infer learning path name, stage, confidence
  5. Use AI to fill missing milestones (only when needed)
  6. Upsert LearningPath + LearningPathNode records

Runs asynchronously after every knowledge graph update.
"""

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, Tuple

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.models import (
    KnowledgeNode, KnowledgeEdge, KnowledgeSource, LearningSession,
    LearningPath, LearningPathNode,
    utcnow, generate_uuid,
)
from app.config.settings import settings
from app.services.knowledge_gap_service import analyze_knowledge_gaps


# ═══════════════════════════════════════════════════════════
#  CONSTANTS
# ═══════════════════════════════════════════════════════════

# Minimum nodes in a cluster to form a learning path
MIN_CLUSTER_SIZE = 3

# Mastery threshold for a concept to be considered "completed"
MASTERY_COMPLETION_THRESHOLD = 0.50

# Known domain → canonical path name mapping
CATEGORY_PATH_MAP = {
    'Web Development': 'Web Development',
    'Backend Development': 'Backend Development',
    'Database': 'Database Engineering',
    'Machine Learning': 'Machine Learning',
    'Data Science': 'Data Science',
    'DevOps': 'DevOps & Infrastructure',
    'Cloud Computing': 'Cloud Computing',
    'API Design': 'API Engineering',
    'Security': 'Security Engineering',
    'Testing': 'Software Testing',
    'Mobile Development': 'Mobile Development',
    'iOS Development': 'iOS Development',
    'Android Development': 'Android Development',
    'Python': 'Python Development',
    'Java': 'Java Development',
    'C++': 'Systems Programming',
    'C Programming': 'Systems Programming',
    'C#': '.NET Development',
    'Go': 'Go Development',
    'Rust': 'Rust Development',
    'Ruby': 'Ruby Development',
    'PHP': 'PHP Development',
    'Programming': 'General Programming',
    'Technology': 'Technology',
}

# Missing topic suggestions per path (rule-based, no AI needed)
PATH_MILESTONE_MAP = {
    'Backend Development': [
        'HTTP Fundamentals', 'REST API', 'Authentication', 'JWT',
        'Database Design', 'ORM', 'Caching', 'Redis',
        'Message Queues', 'Docker', 'CI/CD', 'Monitoring',
    ],
    'Web Development': [
        'HTML', 'CSS', 'JavaScript', 'DOM Manipulation',
        'React', 'State Management', 'Routing', 'API Integration',
        'Responsive Design', 'Testing', 'Build Tools', 'Deployment',
    ],
    'Machine Learning': [
        'Python', 'NumPy', 'Pandas', 'Data Visualization',
        'Statistics', 'Linear Regression', 'Classification',
        'Decision Trees', 'Neural Networks', 'Model Evaluation',
        'TensorFlow', 'PyTorch', 'Feature Engineering',
    ],
    'Data Science': [
        'Python', 'Statistics', 'Pandas', 'NumPy',
        'Data Cleaning', 'Visualization', 'SQL',
        'Hypothesis Testing', 'Machine Learning Basics',
        'Dashboard Design', 'Storytelling with Data',
    ],
    'DevOps & Infrastructure': [
        'Linux', 'Shell Scripting', 'Git', 'Docker',
        'Kubernetes', 'CI/CD', 'Terraform', 'Monitoring',
        'Cloud Platforms', 'Networking', 'Security',
    ],
    'Cloud Computing': [
        'Cloud Fundamentals', 'Virtual Machines', 'Containers',
        'Serverless', 'Storage Services', 'Networking',
        'IAM', 'Cost Optimization', 'High Availability',
    ],
    'Database Engineering': [
        'SQL', 'Normalization', 'Indexing', 'Joins',
        'Transactions', 'NoSQL', 'Replication',
        'Sharding', 'Query Optimization', 'Migrations',
    ],
}


def slugify(name: str) -> str:
    """Create a URL-safe slug from a path name."""
    slug = name.lower().strip()
    slug = re.sub(r'[^a-z0-9\s-]', '', slug)
    slug = re.sub(r'[\s-]+', '-', slug)
    return slug.strip('-')


# ═══════════════════════════════════════════════════════════
#  STAGE 1: CLUSTER KNOWLEDGE NODES
# ═══════════════════════════════════════════════════════════

def cluster_nodes_by_category(nodes: List[KnowledgeNode], edges: List[KnowledgeEdge]) -> Dict[str, List[KnowledgeNode]]:
    """Group nodes into clusters by category.
    Nodes without a category are assigned via edge connectivity.
    """
    clusters = defaultdict(list)
    uncategorized = []

    for node in nodes:
        if node.category:
            clusters[node.category].append(node)
        else:
            uncategorized.append(node)

    # Try to assign uncategorized nodes via edges
    if uncategorized:
        node_map = {n.id: n for n in nodes}
        edge_neighbors = defaultdict(list)
        for edge in edges:
            edge_neighbors[edge.source_node_id].append(edge.target_node_id)
            edge_neighbors[edge.target_node_id].append(edge.source_node_id)

        for node in uncategorized:
            neighbor_ids = edge_neighbors.get(node.id, [])
            neighbor_cats = []
            for nid in neighbor_ids:
                neighbor = node_map.get(nid)
                if neighbor and neighbor.category:
                    neighbor_cats.append(neighbor.category)

            if neighbor_cats:
                # Assign to the most common neighbor category
                from collections import Counter
                most_common = Counter(neighbor_cats).most_common(1)[0][0]
                clusters[most_common].append(node)
            else:
                clusters['General'].append(node)

    return dict(clusters)


# ═══════════════════════════════════════════════════════════
#  STAGE 2: DETECT PATH & ORDER NODES
# ═══════════════════════════════════════════════════════════

def detect_path_from_cluster(
    category: str,
    nodes: List[KnowledgeNode],
    edges: List[KnowledgeEdge],
) -> Dict[str, Any]:
    """Analyze a cluster to detect a learning path.

    Returns:
        {
            'path_name': str,
            'path_slug': str,
            'confidence': float,
            'stage': str,
            'ordered_nodes': [{node, order, importance, is_completed}],
            'missing_topics': [str],
            'completion_pct': float,
        }
    """
    if len(nodes) < MIN_CLUSTER_SIZE:
        return None

    # Determine path name
    path_name = CATEGORY_PATH_MAP.get(category, category)
    path_slug = slugify(path_name)

    # Order nodes by first_seen_at (temporal learning progression)
    sorted_nodes = sorted(nodes, key=lambda n: n.first_seen_at or n.created_at)

    # Build node importance from encounter_count + edge connectivity
    node_ids = {n.id for n in nodes}
    edge_count = defaultdict(int)
    for edge in edges:
        if edge.source_node_id in node_ids:
            edge_count[edge.source_node_id] += 1
        if edge.target_node_id in node_ids:
            edge_count[edge.target_node_id] += 1

    max_encounters = max((n.encounter_count or 1) for n in nodes)
    max_edges = max(edge_count.values()) if edge_count else 1

    ordered_nodes = []
    mastered_count = 0

    for order, node in enumerate(sorted_nodes):
        encounter_factor = (node.encounter_count or 1) / max_encounters
        edge_factor = edge_count.get(node.id, 0) / max(max_edges, 1)
        importance = round(encounter_factor * 0.6 + edge_factor * 0.4, 3)

        is_completed = (node.mastery_level or 0) >= MASTERY_COMPLETION_THRESHOLD
        if is_completed:
            mastered_count += 1

        ordered_nodes.append({
            'node': node,
            'order': order,
            'importance': importance,
            'is_completed': is_completed,
        })

    # Calculate confidence based on cluster quality signals
    confidence = _compute_path_confidence(nodes, edges, category)

    # Calculate completion
    total = len(nodes)
    completion_pct = round((mastered_count / total) * 100, 1) if total > 0 else 0.0

    # Determine learning stage
    stage = _infer_stage(completion_pct, nodes)

    # Determine missing topics
    missing = _detect_missing_topics(path_name, nodes)

    return {
        'path_name': path_name,
        'path_slug': path_slug,
        'confidence': confidence,
        'stage': stage,
        'ordered_nodes': ordered_nodes,
        'missing_topics': missing,
        'completion_pct': completion_pct,
        'total_concepts': total,
        'mastered_concepts': mastered_count,
    }


def _compute_path_confidence(
    nodes: List[KnowledgeNode],
    edges: List[KnowledgeEdge],
    category: str,
) -> float:
    """Compute confidence that this cluster represents a real learning path.

    Signals:
    - Number of nodes (more = higher)
    - Edge density (more connections = higher)
    - Temporal spread (learning over time = higher)
    - Category consistency (known category = higher)
    """
    n = len(nodes)

    # Node count factor (logarithmic scaling)
    import math
    node_factor = min(math.log2(n + 1) / 5, 0.3)  # Max 0.3

    # Edge density
    node_ids = {nd.id for nd in nodes}
    internal_edges = sum(
        1 for e in edges
        if e.source_node_id in node_ids and e.target_node_id in node_ids
    )
    max_possible = n * (n - 1) / 2 if n > 1 else 1
    edge_density = internal_edges / max_possible if max_possible > 0 else 0
    edge_factor = min(edge_density * 2, 0.25)  # Max 0.25

    # Temporal spread (days between first and last concept)
    dates = [n.first_seen_at or n.created_at for n in nodes if (n.first_seen_at or n.created_at)]
    if len(dates) >= 2:
        spread_days = (max(dates) - min(dates)).total_seconds() / 86400
        temporal_factor = min(spread_days / 30, 0.2)  # Max 0.2 at 30+ days
    else:
        temporal_factor = 0.05

    # Category known bonus
    category_factor = 0.25 if category in CATEGORY_PATH_MAP else 0.1

    confidence = min(1.0, node_factor + edge_factor + temporal_factor + category_factor)
    return round(confidence, 3)


def _infer_stage(completion_pct: float, nodes: List[KnowledgeNode]) -> str:
    """Infer the current learning stage."""
    avg_mastery = sum(n.mastery_level or 0 for n in nodes) / max(len(nodes), 1)

    if completion_pct >= 80 and avg_mastery >= 0.7:
        return "expert"
    elif completion_pct >= 50 or avg_mastery >= 0.5:
        return "advanced"
    elif completion_pct >= 20 or avg_mastery >= 0.25:
        return "intermediate"
    else:
        return "beginner"


def _detect_missing_topics(path_name: str, nodes: List[KnowledgeNode]) -> List[str]:
    """Detect which milestone topics are missing from a learning path."""
    milestones = PATH_MILESTONE_MAP.get(path_name, [])
    if not milestones:
        return []

    existing_canonical = {(n.canonical_name or n.name.lower()) for n in nodes}

    missing = []
    for topic in milestones:
        topic_lower = topic.lower()
        # Check if any existing node name contains this topic
        found = any(topic_lower in cn for cn in existing_canonical)
        if not found:
            missing.append(topic)

    return missing[:10]  # Cap at 10 suggestions


# ═══════════════════════════════════════════════════════════
#  STAGE 3: UPSERT LEARNING PATHS
# ═══════════════════════════════════════════════════════════

def upsert_learning_path(
    db: Session,
    user_id: str,
    path_data: Dict[str, Any],
) -> LearningPath:
    """Create or update a learning path record."""
    slug = path_data['path_slug']

    existing = db.query(LearningPath).filter(
        LearningPath.user_id == user_id,
        LearningPath.path_slug == slug,
    ).first()

    if existing:
        # Update existing path
        existing.confidence = path_data['confidence']
        existing.stage = path_data['stage']
        existing.completion_pct = path_data['completion_pct']
        existing.total_concepts = path_data['total_concepts']
        existing.mastered_concepts = path_data['mastered_concepts']
        existing.missing_topics = path_data['missing_topics']
        existing.last_updated = utcnow()

        # Update status
        if path_data['completion_pct'] >= 90:
            existing.status = 'completed'
        elif path_data['total_concepts'] >= 8:
            existing.status = 'mature'
        else:
            existing.status = 'growing'

        path = existing
    else:
        # Create new path
        status = 'growing'
        if path_data['completion_pct'] >= 90:
            status = 'completed'
        elif path_data['total_concepts'] >= 8:
            status = 'mature'

        path = LearningPath(
            user_id=user_id,
            path_name=path_data['path_name'],
            path_slug=slug,
            confidence=path_data['confidence'],
            status=status,
            stage=path_data['stage'],
            completion_pct=path_data['completion_pct'],
            total_concepts=path_data['total_concepts'],
            mastered_concepts=path_data['mastered_concepts'],
            missing_topics=path_data['missing_topics'],
            milestone_history=[],
        )
        db.add(path)
        db.flush()

    # ── Sync path nodes ──
    # Remove old path nodes
    db.query(LearningPathNode).filter(
        LearningPathNode.learning_path_id == path.id,
    ).delete(synchronize_session=False)

    # Add ordered nodes
    for item in path_data['ordered_nodes']:
        node = item['node']
        pn = LearningPathNode(
            learning_path_id=path.id,
            knowledge_node_id=node.id,
            learning_order=item['order'],
            importance_score=item['importance'],
            is_completed=item['is_completed'],
        )
        db.add(pn)

    # Update milestone history
    milestones = path.milestone_history or []
    for item in path_data['ordered_nodes']:
        node = item['node']
        if item['is_completed']:
            already = any(m.get('concept') == node.name for m in milestones)
            if not already:
                milestones.append({
                    'concept': node.name,
                    'date': (node.last_seen_at or utcnow()).isoformat(),
                    'order': item['order'],
                })
    path.milestone_history = milestones

    return path


# ═══════════════════════════════════════════════════════════
#  STAGE 4: AI PATH DESCRIPTION (on-demand, optional)
# ═══════════════════════════════════════════════════════════

def generate_path_description(db: Session, user_id: str, path_id: str) -> Optional[str]:
    """Generate an AI description for a learning path ON DEMAND."""
    from app.services.ai_service import _call_llm

    path = db.query(LearningPath).filter(
        LearningPath.id == path_id,
        LearningPath.user_id == user_id,
    ).first()

    if not path:
        return None

    # Get node names
    path_nodes = db.query(LearningPathNode).filter(
        LearningPathNode.learning_path_id == path.id,
    ).order_by(LearningPathNode.learning_order).all()

    node_names = []
    for pn in path_nodes:
        knode = db.query(KnowledgeNode).filter(KnowledgeNode.id == pn.knowledge_node_id).first()
        if knode:
            node_names.append(knode.name)

    if not node_names:
        return None

    system_prompt = """You are a learning path analyst. Generate a 2-3 sentence description
of what this learning journey covers. Be concise and motivational. No headers or bullets."""

    user_prompt = f"""Learning Path: {path.path_name}
Stage: {path.stage}
Completion: {path.completion_pct}%
Concepts learned (in order): {', '.join(node_names)}
Missing topics: {', '.join(path.missing_topics or [])}

Generate a brief description of this learning journey."""

    try:
        response = _call_llm(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.4,
            max_tokens=200,
        )
        desc = response.strip()
        path.description = desc
        db.commit()
        return desc
    except Exception as e:
        print(f"[LPD] Description generation error: {e}")
        return None


# ═══════════════════════════════════════════════════════════
#  MAIN DISCOVERY PIPELINE
# ═══════════════════════════════════════════════════════════

def discover_learning_paths(user_id: str):
    """Full learning path discovery pipeline — called as a background task.

    1. Load all knowledge nodes and edges
    2. Cluster by category
    3. Detect paths per cluster
    4. Upsert LearningPath records
    5. Set primary path (highest confidence)
    """
    from app.config.database import SessionLocal

    db = SessionLocal()
    try:
        # Load knowledge graph
        nodes = db.query(KnowledgeNode).filter(
            KnowledgeNode.user_id == user_id,
        ).all()

        if len(nodes) < MIN_CLUSTER_SIZE:
            print(f"[LPD] Not enough nodes ({len(nodes)}) for path discovery")
            return

        edges = db.query(KnowledgeEdge).filter(
            KnowledgeEdge.user_id == user_id,
        ).all()

        print(f"[LPD] Analyzing {len(nodes)} nodes, {len(edges)} edges for user {user_id[:8]}...")

        # Stage 1: Cluster
        clusters = cluster_nodes_by_category(nodes, edges)

        # Stage 2+3: Detect and upsert paths
        discovered_paths = []
        for category, cluster_nodes in clusters.items():
            path_data = detect_path_from_cluster(category, cluster_nodes, edges)
            if path_data:
                path = upsert_learning_path(db, user_id, path_data)
                discovered_paths.append(path)
                print(f"[LPD]   Path: {path.path_name} "
                      f"(confidence={path.confidence:.2f}, "
                      f"stage={path.stage}, "
                      f"completion={path.completion_pct:.0f}%)")

        # Stage 4: Set primary path (highest confidence among non-stale)
        if discovered_paths:
            # Reset all to non-primary
            db.query(LearningPath).filter(
                LearningPath.user_id == user_id,
            ).update({LearningPath.is_primary: False}, synchronize_session=False)

            # Set highest confidence as primary
            primary = max(discovered_paths, key=lambda p: p.confidence)
            primary.is_primary = True

        # Mark paths not seen in this analysis as stale
        active_slugs = {p.path_slug for p in discovered_paths}
        stale_paths = db.query(LearningPath).filter(
            LearningPath.user_id == user_id,
            LearningPath.path_slug.notin_(active_slugs) if active_slugs else True,
            LearningPath.status != 'stale',
        ).all()
        for sp in stale_paths:
            sp.status = 'stale'

        db.commit()
        print(f"[LPD] ✓ Discovered {len(discovered_paths)} learning paths")
        
        # Stage 5: Trigger Knowledge Gap Detection based on updated paths
        analyze_knowledge_gaps(user_id)

    except Exception as e:
        print(f"[LPD] Discovery error: {e}")
        db.rollback()
    finally:
        db.close()


# ═══════════════════════════════════════════════════════════
#  QUERY HELPERS
# ═══════════════════════════════════════════════════════════

def get_learning_paths(db: Session, user_id: str) -> List[dict]:
    """Get all learning paths with enriched node data."""
    paths = db.query(LearningPath).filter(
        LearningPath.user_id == user_id,
        LearningPath.status != 'stale',
    ).order_by(LearningPath.is_primary.desc(), LearningPath.confidence.desc()).all()

    result = []
    for path in paths:
        path_nodes = db.query(LearningPathNode).filter(
            LearningPathNode.learning_path_id == path.id,
        ).order_by(LearningPathNode.learning_order).all()

        enriched_nodes = []
        for pn in path_nodes:
            knode = db.query(KnowledgeNode).filter(
                KnowledgeNode.id == pn.knowledge_node_id,
            ).first()
            enriched_nodes.append({
                'id': pn.id,
                'knowledge_node_id': pn.knowledge_node_id,
                'node_name': knode.name if knode else 'Unknown',
                'node_category': knode.category if knode else None,
                'node_mastery': knode.mastery_level if knode else 0.0,
                'learning_order': pn.learning_order,
                'importance_score': pn.importance_score,
                'is_completed': pn.is_completed,
            })

        result.append({
            'path': path,
            'nodes': enriched_nodes,
        })

    return result


def get_learning_path_summary(db: Session, user_id: str) -> dict:
    """Get a summary of the user's learning journey."""
    paths = db.query(LearningPath).filter(
        LearningPath.user_id == user_id,
        LearningPath.status != 'stale',
    ).order_by(LearningPath.confidence.desc()).all()

    if not paths:
        return {
            'primary_path': None,
            'secondary_paths': [],
            'confidence': 0.0,
            'stage': 'beginner',
            'completion': 0.0,
            'missing_topics': [],
            'total_paths': 0,
            'last_analysis': None,
        }

    primary = next((p for p in paths if p.is_primary), paths[0])
    secondary = [p.path_name for p in paths if p.id != primary.id]

    return {
        'primary_path': primary.path_name,
        'secondary_paths': secondary,
        'confidence': primary.confidence,
        'stage': primary.stage,
        'completion': primary.completion_pct,
        'missing_topics': primary.missing_topics or [],
        'total_paths': len(paths),
        'last_analysis': primary.last_updated,
    }


def get_learning_path_history(db: Session, user_id: str) -> List[dict]:
    """Get historical evolution of all discovered paths."""
    paths = db.query(LearningPath).filter(
        LearningPath.user_id == user_id,
    ).order_by(LearningPath.detected_at.desc()).all()

    return [{
        'path_name': p.path_name,
        'confidence': p.confidence,
        'status': p.status,
        'stage': p.stage,
        'completion_pct': p.completion_pct,
        'detected_at': p.detected_at,
        'last_updated': p.last_updated,
        'node_count': p.total_concepts,
    } for p in paths]
