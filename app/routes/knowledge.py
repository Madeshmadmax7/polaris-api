"""
LifeOS – Knowledge Intelligence API Routes (LCIE)
Endpoints for ingesting educational content, querying the knowledge graph,
and generating on-demand summaries.

All endpoints are under /api/knowledge/.
"""

import hashlib
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import func, or_

from app.config.database import get_db
from app.models.models import (
    User, KnowledgeNode, KnowledgeEdge, KnowledgeSource, LearningSession,
)
from app.schemas.schemas import (
    KnowledgeIngestRequest,
    KnowledgeNodeResponse,
    KnowledgeEdgeResponse,
    KnowledgeSourceResponse,
    LearningSessionResponse,
    KnowledgeGraphResponse,
    KnowledgeStatsResponse,
)
from app.utils.auth import get_current_user
from app.services.knowledge_service import (
    process_educational_content,
    generate_node_summary,
    get_knowledge_graph,
    get_knowledge_stats,
)
from app.services.learning_path_service import discover_learning_paths

router = APIRouter(prefix="/knowledge", tags=["Knowledge Intelligence"])


# ═══════════════════════════════════════════════════════════
#  INGEST — Receive content from extension
# ═══════════════════════════════════════════════════════════

@router.post("/ingest", status_code=202)
def ingest_content(
    data: KnowledgeIngestRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Accept educational content and process in background.
    Returns 202 Accepted immediately — never blocks the browser.

    The background task runs the full hybrid pipeline:
    1. Rule-based extraction
    2. AI concept extraction (only if needed)
    3. Confidence validation
    4. Knowledge graph update
    """
    # Quick dedup check (sync, fast — indexed lookup)
    url_hash = hashlib.sha256(data.url.encode()).hexdigest()
    existing = db.query(KnowledgeSource).filter(
        KnowledgeSource.user_id == user.id,
        KnowledgeSource.url_hash == url_hash,
    ).first()

    if existing:
        return {"status": "already_analyzed", "source_id": existing.id}

    # Queue for background processing
    background_tasks.add_task(
        process_educational_content,
        user_id=user.id,
        data=data.model_dump(),
        url_hash=url_hash,
    )

    # Also trigger learning path re-discovery
    background_tasks.add_task(discover_learning_paths, user_id=user.id)

    return {"status": "queued", "url": data.url}


# ═══════════════════════════════════════════════════════════
#  KNOWLEDGE GRAPH — Full graph retrieval
# ═══════════════════════════════════════════════════════════

@router.get("/graph", response_model=KnowledgeGraphResponse)
def get_graph(
    category: Optional[str] = Query(None, description="Filter by category"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get the user's knowledge graph (nodes + edges) for visualization."""
    result = get_knowledge_graph(db, user.id, category)
    return KnowledgeGraphResponse(
        nodes=[KnowledgeNodeResponse.model_validate(n) for n in result['nodes']],
        edges=[KnowledgeEdgeResponse.model_validate(e) for e in result['edges']],
        stats=result['stats'],
    )


# ═══════════════════════════════════════════════════════════
#  NODES — Concept management
# ═══════════════════════════════════════════════════════════

@router.get("/nodes", response_model=list[KnowledgeNodeResponse])
def get_nodes(
    category: Optional[str] = Query(None),
    node_type: Optional[str] = Query(None),
    min_mastery: Optional[float] = Query(None, ge=0.0, le=1.0),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get knowledge nodes with optional filters."""
    query = db.query(KnowledgeNode).filter(KnowledgeNode.user_id == user.id)

    if category:
        query = query.filter(KnowledgeNode.category == category)
    if node_type:
        query = query.filter(KnowledgeNode.node_type == node_type)
    if min_mastery is not None:
        query = query.filter(KnowledgeNode.mastery_level >= min_mastery)

    nodes = query.order_by(
        KnowledgeNode.encounter_count.desc()
    ).offset(offset).limit(limit).all()

    return [KnowledgeNodeResponse.model_validate(n) for n in nodes]


@router.delete("/nodes/{node_id}")
def delete_node(
    node_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Delete a knowledge node and its associated edges."""
    node = db.query(KnowledgeNode).filter(
        KnowledgeNode.id == node_id,
        KnowledgeNode.user_id == user.id,
    ).first()

    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    # Delete associated edges
    db.query(KnowledgeEdge).filter(
        KnowledgeEdge.user_id == user.id,
        or_(
            KnowledgeEdge.source_node_id == node_id,
            KnowledgeEdge.target_node_id == node_id,
        ),
    ).delete(synchronize_session=False)

    db.delete(node)
    db.commit()

    return {"success": True, "deleted": node.name}


# ═══════════════════════════════════════════════════════════
#  SUMMARY — On-demand AI summary generation
# ═══════════════════════════════════════════════════════════

@router.get("/summary/{node_id}")
def get_node_summary(
    node_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Generate an AI summary for a concept node ON DEMAND.
    Uses the current LLM — so future model improvements give better summaries.
    """
    node = db.query(KnowledgeNode).filter(
        KnowledgeNode.id == node_id,
        KnowledgeNode.user_id == user.id,
    ).first()

    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    summary = generate_node_summary(db, user.id, node_id)

    return {
        "node_id": node_id,
        "node_name": node.name,
        "category": node.category,
        "summary": summary or "Unable to generate summary — insufficient context.",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ═══════════════════════════════════════════════════════════
#  STATS — Knowledge analytics
# ═══════════════════════════════════════════════════════════

@router.get("/stats", response_model=KnowledgeStatsResponse)
def get_stats(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get comprehensive knowledge statistics."""
    return get_knowledge_stats(db, user.id)


# ═══════════════════════════════════════════════════════════
#  SOURCES — Page provenance
# ═══════════════════════════════════════════════════════════

@router.get("/sources", response_model=list[KnowledgeSourceResponse])
def get_sources(
    domain: Optional[str] = Query(None),
    intent: Optional[str] = Query(None),
    limit: int = Query(30, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get analyzed knowledge sources with filters."""
    query = db.query(KnowledgeSource).filter(KnowledgeSource.user_id == user.id)

    if domain:
        query = query.filter(KnowledgeSource.domain == domain)
    if intent:
        query = query.filter(KnowledgeSource.learning_intent == intent)

    sources = query.order_by(
        KnowledgeSource.created_at.desc()
    ).offset(offset).limit(limit).all()

    return [KnowledgeSourceResponse.model_validate(s) for s in sources]


# ═══════════════════════════════════════════════════════════
#  SESSIONS — Learning time tracking
# ═══════════════════════════════════════════════════════════

@router.get("/sessions", response_model=list[LearningSessionResponse])
def get_sessions(
    intent: Optional[str] = Query(None),
    limit: int = Query(30, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get learning sessions with optional intent filter."""
    query = db.query(LearningSession).filter(LearningSession.user_id == user.id)

    if intent:
        query = query.filter(LearningSession.learning_intent == intent)

    sessions = query.order_by(
        LearningSession.created_at.desc()
    ).limit(limit).all()

    return [LearningSessionResponse.model_validate(s) for s in sessions]


# ═══════════════════════════════════════════════════════════
#  SEARCH — Full-text search across knowledge
# ═══════════════════════════════════════════════════════════

@router.get("/search")
def search_knowledge(
    q: str = Query(..., min_length=2, description="Search query"),
    limit: int = Query(20, le=50),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Search across knowledge nodes and sources."""
    search_term = f"%{q.lower()}%"

    # Search nodes
    nodes = db.query(KnowledgeNode).filter(
        KnowledgeNode.user_id == user.id,
        or_(
            KnowledgeNode.canonical_name.like(search_term),
            KnowledgeNode.category.like(search_term),
        ),
    ).order_by(KnowledgeNode.encounter_count.desc()).limit(limit).all()

    # Search sources
    sources = db.query(KnowledgeSource).filter(
        KnowledgeSource.user_id == user.id,
        or_(
            func.lower(KnowledgeSource.page_title).like(search_term),
            func.lower(KnowledgeSource.domain).like(search_term),
        ),
    ).order_by(KnowledgeSource.created_at.desc()).limit(limit).all()

    return {
        "query": q,
        "nodes": [KnowledgeNodeResponse.model_validate(n) for n in nodes],
        "sources": [KnowledgeSourceResponse.model_validate(s) for s in sources],
        "total_results": len(nodes) + len(sources),
    }


# ═══════════════════════════════════════════════════════════
#  TOPICS — Unique topics with counts
# ═══════════════════════════════════════════════════════════

@router.get("/topics")
def get_topics(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get unique categories with node counts (for dashboard filters)."""
    categories = db.query(
        KnowledgeNode.category,
        func.count(KnowledgeNode.id).label('count'),
    ).filter(
        KnowledgeNode.user_id == user.id,
        KnowledgeNode.category.isnot(None),
    ).group_by(KnowledgeNode.category).order_by(
        func.count(KnowledgeNode.id).desc()
    ).all()

    return [{"category": c.category, "count": c.count} for c in categories]
