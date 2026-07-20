"""
LifeOS – Knowledge Gap Detection Service
Analyzes the Knowledge Graph and Learning Paths to identify missing prerequisite concepts.

Pipeline:
  1. Load known concepts (Knowledge Graph)
  2. Load active learning paths (Learning Path Discovery)
  3. Extract "missing topics" from learning paths
  4. Use Graph Traversal (rule-based mapped dependencies)
  5. Use AI to infer complex dependency gaps
  6. Calculate severity & priority
  7. Upsert KnowledgeGap and Recommendations
"""

import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, Tuple

from sqlalchemy.orm import Session

from app.models.models import (
    KnowledgeNode, KnowledgeEdge, LearningPath, LearningPathNode,
    KnowledgeGap, KnowledgeGapRecommendation,
    utcnow, generate_uuid,
)
from app.config.settings import settings
from app.services.ai_service import _call_llm


# ═══════════════════════════════════════════════════════════
#  CONSTANTS & RULE-BASED MAPS
# ═══════════════════════════════════════════════════════════

# A static map of common prerequisite chains (Rule-based detection)
# Concept -> List of Prerequisites
PREREQUISITE_MAP = {
    'Spring Boot': ['Java Core', 'Dependency Injection', 'HTTP Fundamentals', 'REST API'],
    'React': ['JavaScript', 'DOM Manipulation', 'HTML', 'CSS', 'ES6+'],
    'Machine Learning': ['Python', 'Statistics', 'Linear Algebra', 'Data Cleaning', 'NumPy'],
    'Docker': ['Linux Basics', 'Shell Scripting', 'Virtualization Basics'],
    'Kubernetes': ['Docker', 'Containers', 'Networking Basics'],
    'REST API': ['HTTP Fundamentals', 'JSON', 'Client-Server Architecture'],
    'JWT': ['Authentication', 'Cryptography Basics', 'HTTP Headers'],
    'ORM': ['SQL', 'Database Design', 'Relational Databases'],
    'Pandas': ['Python', 'Data Structures', 'Data Cleaning'],
    'TensorFlow': ['Machine Learning', 'Python', 'Calculus Basics', 'Linear Algebra'],
    'Microservices': ['REST API', 'Docker', 'Distributed Systems Basics', 'API Gateway'],
    'CI/CD': ['Git', 'Shell Scripting', 'Automated Testing'],
    'Next.js': ['React', 'Server-Side Rendering', 'Routing'],
    'GraphQL': ['API Design', 'JSON', 'Client-Server Architecture'],
}


# ═══════════════════════════════════════════════════════════
#  STAGE 1: RULE-BASED GAP DETECTION
# ═══════════════════════════════════════════════════════════

def detect_rule_based_gaps(
    known_concepts_lower: set,
    active_concepts: List[str]
) -> List[Dict[str, Any]]:
    """Detect missing prerequisites based on a static mapping."""
    detected_gaps = []

    for concept in active_concepts:
        prereqs = PREREQUISITE_MAP.get(concept, [])
        for prereq in prereqs:
            prereq_lower = prereq.lower()
            # If the prerequisite is not in the user's known concepts
            if prereq_lower not in known_concepts_lower:
                # We found a gap!
                detected_gaps.append({
                    'concept': prereq,
                    'blocks_concepts': [concept],
                    'detection_method': 'rule_based',
                    'reason': f"Understanding '{concept}' requires foundational knowledge of '{prereq}'.",
                    'severity': 0.8,  # Hard prerequisite = high severity
                    'priority': 'high',
                })
    return detected_gaps


# ═══════════════════════════════════════════════════════════
#  STAGE 2: LEARNING PATH MILESTONE GAPS
# ═══════════════════════════════════════════════════════════

def detect_learning_path_gaps(
    learning_paths: List[LearningPath]
) -> List[Dict[str, Any]]:
    """Convert 'missing_topics' from learning paths into formalized gaps."""
    detected_gaps = []

    for path in learning_paths:
        missing = path.missing_topics or []
        for topic in missing:
            # Severity scales with how far along the path they are
            # If they are 80% done but missing a core topic, it's critical
            severity = 0.5 + (path.completion_pct / 200.0) # 0.5 - 1.0 range
            priority = 'high' if severity > 0.8 else ('medium' if severity > 0.6 else 'low')

            detected_gaps.append({
                'concept': topic,
                'category': path.path_name,
                'learning_path_id': path.id,
                'learning_path_name': path.path_name,
                'detection_method': 'learning_path',
                'reason': f"Suggested milestone for your journey in {path.path_name}.",
                'severity': severity,
                'priority': priority,
            })

    return detected_gaps


# ═══════════════════════════════════════════════════════════
#  STAGE 3: AI-DRIVEN GAP DETECTION
# ═══════════════════════════════════════════════════════════

def detect_ai_driven_gaps(
    active_concepts: List[str],
    known_concepts: List[str],
    learning_path_name: str
) -> List[Dict[str, Any]]:
    """Use AI to infer complex, non-obvious gaps specific to the user's graph."""
    if not active_concepts:
        return []

    system_prompt = """You are an expert Educational Graph Analyst. 
Analyze the learner's current knowledge and identify CRITICAL MISSING PREREQUISITES.
Do not recommend random "next steps". Only recommend foundational concepts they are likely missing based on what they are currently trying to learn.

Return ONLY a JSON array of objects with the following schema:
[
  {
    "concept": "Name of missing concept",
    "blocks_concepts": ["concept1", "concept2"], // which currently known concepts depend on this?
    "reason": "Why is this missing piece critical?",
    "severity": 0.0-1.0 (float, 1.0 is highest risk),
    "difficulty": "beginner|intermediate|advanced",
    "estimated_study_minutes": int
  }
]"""

    user_prompt = f"""Learning Path: {learning_path_name}
Currently Exploring Concepts: {', '.join(active_concepts[:10])}
Known Graph Concepts (Context): {', '.join(known_concepts[:50])}

What critical foundational gaps exist in this graph?"""

    try:
        response = _call_llm(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=800,
        )
        
        # Clean response (handle markdown blocks)
        cleaned = response.replace("```json", "").replace("```", "").strip()
        gaps_data = json.loads(cleaned)
        
        valid_gaps = []
        for g in gaps_data:
            if 'concept' in g:
                priority = 'high' if g.get('severity', 0.5) > 0.8 else ('medium' if g.get('severity', 0.5) > 0.5 else 'low')
                valid_gaps.append({
                    'concept': g['concept'],
                    'blocks_concepts': g.get('blocks_concepts', []),
                    'detection_method': 'ai',
                    'reason': g.get('reason', 'Identified by AI analysis.'),
                    'severity': g.get('severity', 0.5),
                    'priority': priority,
                    'difficulty': g.get('difficulty', 'intermediate'),
                    'estimated_study_minutes': g.get('estimated_study_minutes', 30),
                    'confidence': 0.85
                })
        return valid_gaps

    except Exception as e:
        print(f"[GapEngine] AI Detection Error: {e}")
        return []


# ═══════════════════════════════════════════════════════════
#  STAGE 4: MERGE AND UPSERT
# ═══════════════════════════════════════════════════════════

def merge_and_upsert_gaps(
    db: Session,
    user_id: str,
    all_gaps: List[Dict[str, Any]]
):
    """Deduplicate gaps and upsert into the database."""
    merged_gaps = {}

    for gap in all_gaps:
        canon = gap['concept'].lower().strip()
        if canon in merged_gaps:
            existing = merged_gaps[canon]
            # Merge logic: take highest severity, combine blocked concepts
            existing['severity'] = max(existing['severity'], gap['severity'])
            
            blocks = set(existing.get('blocks_concepts', []))
            blocks.update(gap.get('blocks_concepts', []))
            existing['blocks_concepts'] = list(blocks)
            
            if gap['severity'] > existing['severity']:
                existing['priority'] = gap['priority']
                existing['reason'] = gap['reason']
            
            if 'learning_path_id' in gap and not existing.get('learning_path_id'):
                existing['learning_path_id'] = gap['learning_path_id']
                existing['learning_path_name'] = gap['learning_path_name']
                existing['category'] = gap.get('category')
        else:
            merged_gaps[canon] = gap

    print(f"[GapEngine] Merged down to {len(merged_gaps)} unique gaps.")

    for canon, gap_data in merged_gaps.items():
        # Check if it already exists
        existing_gap = db.query(KnowledgeGap).filter(
            KnowledgeGap.user_id == user_id,
            KnowledgeGap.canonical_concept == canon
        ).first()

        if existing_gap:
            # If resolved, don't reopen unless severity is very high and it's a new path
            if existing_gap.status == 'resolved':
                continue
                
            existing_gap.severity = gap_data['severity']
            existing_gap.priority = gap_data['priority']
            
            # Combine blocks
            blocks = set(existing_gap.blocks_concepts or [])
            blocks.update(gap_data.get('blocks_concepts', []))
            existing_gap.blocks_concepts = list(blocks)
            
            if not existing_gap.learning_path_id and gap_data.get('learning_path_id'):
                 existing_gap.learning_path_id = gap_data['learning_path_id']
                 existing_gap.learning_path_name = gap_data['learning_path_name']
                 existing_gap.category = gap_data.get('category')
                 
            existing_gap.updated_at = utcnow()
        else:
            new_gap = KnowledgeGap(
                user_id=user_id,
                concept=gap_data['concept'],
                canonical_concept=canon,
                category=gap_data.get('category'),
                learning_path_id=gap_data.get('learning_path_id'),
                learning_path_name=gap_data.get('learning_path_name'),
                severity=gap_data.get('severity', 0.5),
                priority=gap_data.get('priority', 'medium'),
                confidence=gap_data.get('confidence', 0.9),
                reason=gap_data.get('reason'),
                detection_method=gap_data.get('detection_method', 'hybrid'),
                blocks_concepts=gap_data.get('blocks_concepts', []),
                difficulty=gap_data.get('difficulty', 'intermediate'),
                estimated_study_minutes=gap_data.get('estimated_study_minutes', 30),
            )
            db.add(new_gap)

    db.commit()


# ═══════════════════════════════════════════════════════════
#  MAIN PIPELINE
# ═══════════════════════════════════════════════════════════

def analyze_knowledge_gaps(user_id: str):
    """Full knowledge gap detection pipeline — called as a background task.
    Runs asynchronously whenever the knowledge graph or learning paths update.
    """
    from app.config.database import SessionLocal

    db = SessionLocal()
    try:
        print(f"[GapEngine] Starting analysis for user {user_id[:8]}...")

        # 1. Load Knowledge Graph context
        nodes = db.query(KnowledgeNode).filter(
            KnowledgeNode.user_id == user_id
        ).all()
        
        known_concepts_lower = {n.canonical_name for n in nodes}
        known_concepts_names = [n.name for n in nodes]

        # Extract recently encountered/active concepts to focus analysis
        # (Nodes seen in the last 7 days or highest encounter counts)
        # Simplified: take top 20 by encounter count
        active_nodes = sorted(nodes, key=lambda n: n.encounter_count or 0, reverse=True)[:20]
        active_concepts_names = [n.name for n in active_nodes]

        # 2. Load Active Learning Paths
        learning_paths = db.query(LearningPath).filter(
            LearningPath.user_id == user_id,
            LearningPath.status.in_(['growing', 'mature'])
        ).all()

        all_detected_gaps = []

        # Stage 1: Rule-based
        print(f"[GapEngine] Running Rule-Based Detection...")
        rule_gaps = detect_rule_based_gaps(known_concepts_lower, active_concepts_names)
        all_detected_gaps.extend(rule_gaps)

        # Stage 2: Learning Path Milestones
        print(f"[GapEngine] Running Learning Path Milestone Detection...")
        lp_gaps = detect_learning_path_gaps(learning_paths)
        # Filter out ones we already know
        lp_gaps = [g for g in lp_gaps if g['concept'].lower().strip() not in known_concepts_lower]
        all_detected_gaps.extend(lp_gaps)

        # Stage 3: AI-Driven (Only if we have a primary path and active concepts)
        primary_path = next((p for p in learning_paths if p.is_primary), None)
        if primary_path and active_concepts_names:
            print(f"[GapEngine] Running AI-Driven Detection for path: {primary_path.path_name}...")
            ai_gaps = detect_ai_driven_gaps(
                active_concepts_names, 
                known_concepts_names, 
                primary_path.path_name
            )
            # Filter known
            ai_gaps = [g for g in ai_gaps if g['concept'].lower().strip() not in known_concepts_lower]
            # Link to path
            for g in ai_gaps:
                g['learning_path_id'] = primary_path.id
                g['learning_path_name'] = primary_path.path_name
                g['category'] = primary_path.path_name
                
            all_detected_gaps.extend(ai_gaps)

        # Stage 4: Deduplicate and Upsert
        print(f"[GapEngine] Total raw gaps detected: {len(all_detected_gaps)}. Merging...")
        merge_and_upsert_gaps(db, user_id, all_detected_gaps)
        
        # Check for resolutions (concepts that were gaps but are now known)
        check_resolved_gaps(db, user_id, known_concepts_lower)

        print(f"[GapEngine] ✓ Analysis complete.")

    except Exception as e:
        print(f"[GapEngine] Error: {e}")
        db.rollback()
    finally:
        db.close()


def check_resolved_gaps(db: Session, user_id: str, known_concepts_lower: set):
    """Mark gaps as resolved if the concept now exists in the knowledge graph."""
    active_gaps = db.query(KnowledgeGap).filter(
        KnowledgeGap.user_id == user_id,
        KnowledgeGap.status.in_(['detected', 'learning'])
    ).all()
    
    resolved_count = 0
    for gap in active_gaps:
        if gap.canonical_concept in known_concepts_lower:
            gap.status = 'resolved'
            gap.resolved_at = utcnow()
            resolved_count += 1
            
    if resolved_count > 0:
        db.commit()
        print(f"[GapEngine] Marked {resolved_count} gaps as resolved!")


# ═══════════════════════════════════════════════════════════
#  RECOMMENDATION GENERATION
# ═══════════════════════════════════════════════════════════

def generate_gap_recommendations(db: Session, user_id: str) -> List[Dict[str, Any]]:
    """Generate study recommendations for the top priority gaps on demand."""
    
    # Get top 3 unresolved gaps
    top_gaps = db.query(KnowledgeGap).filter(
        KnowledgeGap.user_id == user_id,
        KnowledgeGap.status.in_(['detected', 'learning'])
    ).order_by(KnowledgeGap.severity.desc()).limit(3).all()
    
    if not top_gaps:
        return []
        
    gap_concepts = [g.concept for g in top_gaps]
    
    system_prompt = """You are an AI Study Advisor.
Given a list of missing knowledge concepts (gaps), recommend ONE specific, highly-effective study resource or action for EACH gap to help the user resolve it.

Return ONLY a JSON array of objects with the following schema:
[
  {
    "gap_concept": "The concept name",
    "resource_type": "documentation|tutorial|video|practice|article",
    "title": "A specific suggested title (e.g., 'MDN Web Docs: HTTP Headers')",
    "description": "Why this is the best way to learn it",
    "estimated_minutes": int,
    "difficulty": "beginner|intermediate|advanced"
  }
]"""

    user_prompt = f"Missing Concepts: {', '.join(gap_concepts)}"
    
    try:
        response = _call_llm(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=600,
        )
        
        cleaned = response.replace("```json", "").replace("```", "").strip()
        recs_data = json.loads(cleaned)
        
        # Format for response
        results = []
        for rec in recs_data:
            # Map back to gap
            gap = next((g for g in top_gaps if g.concept.lower() == rec.get('gap_concept', '').lower()), None)
            if gap:
                results.append({
                    "gap_id": gap.id,
                    "concept": gap.concept,
                    "learning_path_name": gap.learning_path_name,
                    "severity": gap.severity,
                    "resource_type": rec.get("resource_type", "article"),
                    "title": rec.get("title", f"Learn {gap.concept}"),
                    "description": rec.get("description", ""),
                    "estimated_minutes": rec.get("estimated_minutes", 30),
                    "difficulty": rec.get("difficulty", "intermediate")
                })
                
        return results

    except Exception as e:
        print(f"[GapEngine] Recommendation generation error: {e}")
        return []


# ═══════════════════════════════════════════════════════════
#  QUERY HELPERS
# ═══════════════════════════════════════════════════════════

def get_knowledge_gaps(db: Session, user_id: str) -> List[KnowledgeGap]:
    """Get all unresolved knowledge gaps ordered by severity."""
    return db.query(KnowledgeGap).filter(
        KnowledgeGap.user_id == user_id,
        KnowledgeGap.status.in_(['detected', 'learning'])
    ).order_by(KnowledgeGap.severity.desc()).all()


def get_knowledge_gaps_history(db: Session, user_id: str) -> List[KnowledgeGap]:
    """Get resolved knowledge gaps (history)."""
    return db.query(KnowledgeGap).filter(
        KnowledgeGap.user_id == user_id,
        KnowledgeGap.status == 'resolved'
    ).order_by(KnowledgeGap.resolved_at.desc()).all()


def get_knowledge_gap_summary(db: Session, user_id: str) -> dict:
    """Calculate summary statistics for knowledge gaps."""
    gaps = db.query(KnowledgeGap).filter(
        KnowledgeGap.user_id == user_id
    ).all()
    
    active = [g for g in gaps if g.status in ('detected', 'learning')]
    resolved = [g for g in gaps if g.status == 'resolved']
    
    critical = sum(1 for g in active if g.priority == 'critical')
    high = sum(1 for g in active if g.priority == 'high')
    medium = sum(1 for g in active if g.priority == 'medium')
    low = sum(1 for g in active if g.priority == 'low')
    
    avg_severity = sum(g.severity for g in active) / len(active) if active else 0.0
    
    # Find most impacted path
    path_counts = defaultdict(int)
    for g in active:
        if g.learning_path_name:
            path_counts[g.learning_path_name] += 1
            
    most_impacted = max(path_counts.items(), key=lambda x: x[1])[0] if path_counts else None
    
    # Top gaps for quick view
    top_gaps_data = sorted(active, key=lambda g: g.severity, reverse=True)[:3]
    top_gaps = [{"concept": g.concept, "severity": g.severity, "priority": g.priority} for g in top_gaps_data]

    return {
        "total_gaps": len(active),
        "critical_gaps": critical,
        "high_gaps": high,
        "medium_gaps": medium,
        "low_gaps": low,
        "resolved_gaps": len(resolved),
        "avg_severity": avg_severity,
        "most_impacted_path": most_impacted,
        "top_gaps": top_gaps,
        "last_analysis": max((g.updated_at for g in gaps), default=None)
    }
