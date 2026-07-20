"""
LifeOS – Knowledge Service (LCIE)
Hybrid rule-based + AI concept extraction pipeline with confidence validation
and knowledge graph update logic.

Pipeline:
  1. Rule-based pre-processing (languages, technologies, headings → known concepts)
  2. AI concept extraction (only for what rules couldn't determine)
  3. Confidence validation (discard < 0.55, mark low-confidence 0.55-0.70)
  4. Knowledge graph update (find-or-create nodes, upsert edges)
"""

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, Tuple

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.models import (
    KnowledgeNode, KnowledgeEdge, KnowledgeSource, LearningSession,
    utcnow, generate_uuid,
)
from app.config.settings import settings


# ═══════════════════════════════════════════════════════════
#  CANONICAL NAME NORMALIZATION
# ═══════════════════════════════════════════════════════════

def canonicalize(name: str) -> str:
    """Normalize a concept name for deduplication.
    'React.js' → 'react.js', 'React JS' → 'react js', 'C++' → 'c++'
    """
    if not name:
        return ""
    # Lowercase, strip whitespace, collapse multiple spaces
    canonical = name.lower().strip()
    canonical = re.sub(r'\s+', ' ', canonical)
    # Remove trailing punctuation except meaningful ones like ++ or #
    canonical = re.sub(r'[.,;:!?\'"]+$', '', canonical)
    return canonical


# ═══════════════════════════════════════════════════════════
#  STAGE 1: RULE-BASED PRE-PROCESSING
# ═══════════════════════════════════════════════════════════

# Known technology → category mapping (rule-based, no AI)
TECH_CATEGORY_MAP = {
    # Web Development
    'react': 'Web Development', 'angular': 'Web Development', 'vue': 'Web Development',
    'svelte': 'Web Development', 'next.js': 'Web Development', 'nextjs': 'Web Development',
    'nuxt': 'Web Development', 'html': 'Web Development', 'css': 'Web Development',
    'javascript': 'Web Development', 'typescript': 'Web Development',
    'tailwind': 'Web Development', 'bootstrap': 'Web Development',
    'webpack': 'Web Development', 'vite': 'Web Development',
    # Backend
    'express': 'Backend Development', 'fastapi': 'Backend Development',
    'flask': 'Backend Development', 'django': 'Backend Development',
    'spring': 'Backend Development', 'spring boot': 'Backend Development',
    'node.js': 'Backend Development', 'nodejs': 'Backend Development',
    # Data Science / ML
    'tensorflow': 'Machine Learning', 'pytorch': 'Machine Learning',
    'keras': 'Machine Learning', 'scikit-learn': 'Machine Learning',
    'pandas': 'Data Science', 'numpy': 'Data Science',
    'matplotlib': 'Data Science', 'jupyter': 'Data Science',
    # DevOps
    'docker': 'DevOps', 'kubernetes': 'DevOps', 'k8s': 'DevOps',
    'terraform': 'DevOps', 'ansible': 'DevOps', 'jenkins': 'DevOps',
    'ci/cd': 'DevOps', 'nginx': 'DevOps',
    # Cloud
    'aws': 'Cloud Computing', 'azure': 'Cloud Computing', 'gcp': 'Cloud Computing',
    # Database
    'mongodb': 'Database', 'postgresql': 'Database', 'mysql': 'Database',
    'redis': 'Database', 'elasticsearch': 'Database', 'sql': 'Database',
    'graphql': 'API Design', 'rest api': 'API Design', 'grpc': 'API Design',
    # Languages
    'python': 'Python', 'java': 'Java', 'c++': 'C++', 'c': 'C Programming',
    'c#': 'C#', 'go': 'Go', 'rust': 'Rust', 'ruby': 'Ruby', 'php': 'PHP',
    'swift': 'iOS Development', 'kotlin': 'Android Development',
    'dart': 'Mobile Development', 'flutter': 'Mobile Development',
    'react native': 'Mobile Development',
    # Security
    'jwt': 'Security', 'oauth': 'Security', 'encryption': 'Security',
    # Testing
    'jest': 'Testing', 'mocha': 'Testing', 'pytest': 'Testing', 'junit': 'Testing',
}

# Heading keywords that suggest topic areas
HEADING_TOPIC_PATTERNS = {
    'introduction': 'concept',
    'getting started': 'concept',
    'installation': 'technique',
    'setup': 'technique',
    'example': 'technique',
    'tutorial': 'concept',
    'api reference': 'tool',
    'configuration': 'technique',
    'troubleshooting': 'technique',
    'best practices': 'technique',
    'architecture': 'concept',
    'design pattern': 'concept',
    'algorithm': 'concept',
    'data structure': 'concept',
}


def rule_based_extract(data: dict) -> dict:
    """Extract what we can WITHOUT calling AI.

    Uses pre-detected languages, technologies, and heading analysis
    from the extension's rule-based extraction.

    Returns:
        {
            'known_concepts': [{'name': 'React', 'type': 'tool', 'category': 'Web Development'}],
            'known_category': 'Web Development' or None,
            'known_relationships': [('Spring Boot', 'contains', 'REST API')],
            'needs_ai': bool,
            'has_code_blocks': bool,
        }
    """
    known_concepts = []
    known_category = None
    known_relationships = []
    seen_canonical = set()

    # ── Extract from detected languages ──
    for lang in data.get('detected_languages', []):
        canonical = canonicalize(lang)
        if canonical and canonical not in seen_canonical:
            seen_canonical.add(canonical)
            cat = TECH_CATEGORY_MAP.get(canonical, 'Programming')
            known_concepts.append({
                'name': lang,
                'type': 'tool',
                'category': cat,
            })
            if not known_category:
                known_category = cat

    # ── Extract from detected technologies ──
    for tech in data.get('detected_technologies', []):
        canonical = canonicalize(tech)
        if canonical and canonical not in seen_canonical:
            seen_canonical.add(canonical)
            cat = TECH_CATEGORY_MAP.get(canonical, known_category or 'Technology')
            known_concepts.append({
                'name': tech,
                'type': 'tool',
                'category': cat,
            })
            if not known_category:
                known_category = cat

    # ── Extract topic concepts from headings ──
    for heading in data.get('headings', [])[:10]:
        heading_lower = heading.lower().strip()
        # Skip generic headings
        if len(heading_lower) < 3 or heading_lower in ('home', 'menu', 'search', 'about', 'contact'):
            continue
        # Determine node type from heading patterns
        node_type = 'concept'
        for pattern, ntype in HEADING_TOPIC_PATTERNS.items():
            if pattern in heading_lower:
                node_type = ntype
                break

        canonical = canonicalize(heading)
        if canonical and canonical not in seen_canonical and len(canonical) < 100:
            # Only add headings that look like actual topics (not navigation)
            if len(heading.split()) <= 8:  # Max 8 words for a topic heading
                seen_canonical.add(canonical)
                known_concepts.append({
                    'name': heading.strip(),
                    'type': node_type,
                    'category': known_category,
                })

    # ── Build relationships between known concepts ──
    # Technologies in same page are likely related
    tech_concepts = [c for c in known_concepts if c['type'] == 'tool']
    if len(tech_concepts) >= 2:
        # First tech is likely the primary, rest are related
        primary = tech_concepts[0]['name']
        for other in tech_concepts[1:]:
            known_relationships.append((primary, 'related_to', other['name']))

    # Check if content has code blocks
    has_code = len(data.get('detected_languages', [])) > 0

    # ── Determine if AI is needed ──
    # AI is needed if we have few concepts OR no clear category
    needs_ai = len(known_concepts) < 3 or known_category is None

    return {
        'known_concepts': known_concepts,
        'known_category': known_category,
        'known_relationships': known_relationships,
        'needs_ai': needs_ai,
        'has_code_blocks': has_code,
    }


# ═══════════════════════════════════════════════════════════
#  STAGE 2: AI CONCEPT EXTRACTION (only when needed)
# ═══════════════════════════════════════════════════════════

def ai_concept_extract(data: dict, rule_results: dict) -> Optional[dict]:
    """Call LLM ONLY for what rule-based couldn't determine.

    The prompt is narrowly scoped: we tell the AI what we already know
    and ask it to fill in the gaps (concepts, hierarchy, prerequisites).

    Returns parsed JSON or None on failure.
    """
    from app.services.ai_service import _call_llm

    known_names = [c['name'] for c in rule_results.get('known_concepts', [])]
    known_category = rule_results.get('known_category', 'Unknown')

    # Build focused content excerpt (first 2000 chars)
    content = (data.get('content', '') or '')[:2000]
    headings = data.get('headings', [])[:10]
    title = data.get('page_title', '')

    if not content and not headings:
        return None

    known_str = ', '.join(known_names[:10]) if known_names else 'none detected'
    headings_str = '\n'.join(f'- {h}' for h in headings) if headings else 'none'

    system_prompt = """You are an educational content analyzer. Extract learning concepts from webpage content.
Respond ONLY with valid JSON. No markdown, no code blocks, no explanation."""

    user_prompt = f"""Page Title: {title}
Domain: {data.get('domain', '')}
Already known concepts: {known_str}
Category guess: {known_category}

Headings:
{headings_str}

Content excerpt:
{content[:1500]}

Extract ONLY concepts NOT already listed above. Respond with this JSON:
{{
    "concepts": [
        {{"name": "Concept Name", "type": "concept", "parent": null}},
        {{"name": "Sub-concept", "type": "technique", "parent": "Concept Name"}}
    ],
    "relationships": [
        {{"from": "A", "to": "B", "type": "requires"}}
    ],
    "category": "{known_category or 'determine from content'}",
    "difficulty": "beginner",
    "confidence": 0.85
}}

Rules:
- "type" must be: "domain", "topic", "concept", "technique", or "tool"
- "parent" links child to parent concept (null for top-level)
- relationships "type" must be: "contains", "requires", "related_to", "extends", or "implements"
- "difficulty" must be: "beginner", "intermediate", or "advanced"
- "confidence" is your confidence in extraction accuracy (0.0-1.0)
- Extract 3-10 concepts maximum
- Be specific — "Binary Search Tree" not just "Tree"
- Don't repeat concepts from the "already known" list"""

    try:
        response = _call_llm(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=800,
        )

        # Parse JSON response
        clean = response.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
            clean = clean.strip()

        # Find JSON object
        json_start = clean.find('{')
        json_end = clean.rfind('}') + 1
        if json_start >= 0 and json_end > json_start:
            result = json.loads(clean[json_start:json_end])
            return result

        return None
    except Exception as e:
        print(f"[LCIE] AI extraction error: {e}")
        return None


# ═══════════════════════════════════════════════════════════
#  STAGE 3: CONFIDENCE VALIDATION
# ═══════════════════════════════════════════════════════════

def validate_extraction(ai_result: Optional[dict], rule_result: dict) -> dict:
    """Validate and merge AI output with rule-based output.

    Rules:
    1. confidence < 0.55 → discard AI result entirely, keep only rule-based
    2. confidence 0.55-0.70 → keep but mark as low_confidence
    3. confidence > 0.70 → accept fully
    4. Cross-check: if AI concept matches rule-based → trust rule-based
    5. Dedup by canonical name

    Returns merged result with all validated concepts and relationships.
    """
    merged_concepts = []
    merged_relationships = list(rule_result.get('known_relationships', []))
    seen_canonical = set()
    category = rule_result.get('known_category')
    difficulty = 'intermediate'  # Default
    confidence = rule_result.get('detection_confidence', 0.5) if not ai_result else 0.5

    # ── Add all rule-based concepts (always trusted) ──
    for concept in rule_result.get('known_concepts', []):
        canonical = canonicalize(concept['name'])
        if canonical and canonical not in seen_canonical:
            seen_canonical.add(canonical)
            merged_concepts.append({
                **concept,
                'confidence': 0.9,  # Rule-based is high confidence
                'source': 'rule_based',
            })

    # ── Merge AI concepts if confidence passes validation ──
    if ai_result:
        ai_confidence = ai_result.get('confidence', 0.0)
        confidence = ai_confidence

        if ai_confidence < 0.55:
            # Discard AI entirely — hallucination risk too high
            print(f"[LCIE] AI confidence too low ({ai_confidence:.2f}), discarding AI results")
        else:
            is_low_confidence = ai_confidence < 0.70

            # Use AI category if rule-based didn't find one
            if not category and ai_result.get('category'):
                category = ai_result['category']

            # Use AI difficulty
            if ai_result.get('difficulty') in ('beginner', 'intermediate', 'advanced'):
                difficulty = ai_result['difficulty']

            # Add AI concepts
            for concept in ai_result.get('concepts', []):
                name = concept.get('name', '').strip()
                if not name or len(name) < 2 or len(name) > 200:
                    continue

                canonical = canonicalize(name)
                if canonical in seen_canonical:
                    continue  # Already have from rule-based

                seen_canonical.add(canonical)
                merged_concepts.append({
                    'name': name,
                    'type': concept.get('type', 'concept'),
                    'category': category,
                    'parent': concept.get('parent'),
                    'confidence': ai_confidence if not is_low_confidence else ai_confidence * 0.8,
                    'source': 'ai',
                    'low_confidence': is_low_confidence,
                })

            # Add AI relationships
            for rel in ai_result.get('relationships', []):
                from_name = rel.get('from', '').strip()
                to_name = rel.get('to', '').strip()
                rel_type = rel.get('type', 'related_to')
                if from_name and to_name and rel_type in ('contains', 'requires', 'related_to', 'extends', 'implements'):
                    merged_relationships.append((from_name, rel_type, to_name))

            # Add parent→child relationships from AI concepts
            for concept in ai_result.get('concepts', []):
                parent = concept.get('parent')
                if parent and concept.get('name'):
                    merged_relationships.append((parent, 'contains', concept['name']))

    return {
        'concepts': merged_concepts,
        'relationships': merged_relationships,
        'category': category,
        'difficulty': difficulty,
        'confidence': confidence,
        'extraction_method': 'hybrid' if ai_result and confidence >= 0.55 else 'rule_based',
    }


# ═══════════════════════════════════════════════════════════
#  STAGE 4: KNOWLEDGE GRAPH UPDATE
# ═══════════════════════════════════════════════════════════

def find_or_create_node(
    db: Session,
    user_id: str,
    name: str,
    category: Optional[str],
    node_type: str,
    confidence: float,
    snippet: Optional[str] = None,
) -> KnowledgeNode:
    """Find existing node by canonical name or create a new one.

    If node exists:
      - increment encounter_count
      - update last_seen_at
      - append context snippet (keep last 5)
      - update confidence if higher
      - update category if was None

    If new:
      - create with initial values
    """
    canonical = canonicalize(name)
    if not canonical:
        return None

    # Exact canonical name match (fast, indexed)
    existing = db.query(KnowledgeNode).filter(
        KnowledgeNode.user_id == user_id,
        KnowledgeNode.canonical_name == canonical,
    ).first()

    if existing:
        # Update existing node
        existing.encounter_count += 1
        existing.last_seen_at = utcnow()
        if confidence > existing.confidence:
            existing.confidence = confidence
        if category and not existing.category:
            existing.category = category

        # Append snippet (keep last 5)
        if snippet:
            snippets = existing.context_snippets or []
            snippets.append(snippet[:500])  # Max 500 chars per snippet
            existing.context_snippets = snippets[-5:]  # Keep last 5

        # Update mastery level
        existing.mastery_level = _compute_mastery(existing)

        return existing
    else:
        # Create new node
        node = KnowledgeNode(
            user_id=user_id,
            name=name.strip(),
            canonical_name=canonical,
            category=category,
            node_type=node_type,
            confidence=confidence,
            encounter_count=1,
            mastery_level=0.0,
            context_snippets=[snippet[:500]] if snippet else [],
        )
        db.add(node)
        db.flush()  # Get the ID without committing
        return node


def _compute_mastery(node: KnowledgeNode) -> float:
    """Compute mastery level from engagement signals.

    Factors:
    - encounter_count: logarithmic scaling (diminishing returns)
    - recency: recent encounters weigh more
    - confidence: higher extraction confidence → higher mastery
    """
    encounters = node.encounter_count or 1
    confidence = node.confidence or 0.5

    # Logarithmic encounter scaling (fast growth initially, then plateaus)
    encounter_factor = min(math.log2(encounters + 1) * 0.2, 0.6)

    # Recency factor
    now = datetime.now(timezone.utc)
    last_seen = node.last_seen_at
    if last_seen and last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    if last_seen:
        days_ago = (now - last_seen).total_seconds() / 86400
        recency_factor = max(0, 0.3 - (days_ago * 0.01))  # Decays over 30 days
    else:
        recency_factor = 0.1

    # Confidence factor
    confidence_factor = confidence * 0.1

    mastery = min(1.0, encounter_factor + recency_factor + confidence_factor)
    return round(mastery, 3)


def upsert_edge(
    db: Session,
    user_id: str,
    source_node_id: str,
    target_node_id: str,
    relation_type: str,
) -> Optional[KnowledgeEdge]:
    """Create or reinforce a knowledge edge.

    If edge exists: weight += 0.1 (max 5.0), source_count += 1
    If new: weight = 1.0, source_count = 1
    """
    if not source_node_id or not target_node_id or source_node_id == target_node_id:
        return None

    existing = db.query(KnowledgeEdge).filter(
        KnowledgeEdge.user_id == user_id,
        KnowledgeEdge.source_node_id == source_node_id,
        KnowledgeEdge.target_node_id == target_node_id,
    ).first()

    if existing:
        existing.weight = min(5.0, existing.weight + 0.1)
        existing.source_count += 1
        existing.updated_at = utcnow()
        return existing
    else:
        edge = KnowledgeEdge(
            user_id=user_id,
            source_node_id=source_node_id,
            target_node_id=target_node_id,
            relation_type=relation_type,
            weight=1.0,
            source_count=1,
        )
        db.add(edge)
        return edge


def update_knowledge_graph(
    db: Session,
    user_id: str,
    validated: dict,
    source_data: dict,
    url_hash: str,
) -> dict:
    """Update the knowledge graph with validated extraction results.

    1. Create KnowledgeSource record
    2. For each concept → find_or_create_node
    3. For each relationship → upsert_edge
    4. Create LearningSession

    Returns summary of what was created/updated.
    """
    created_nodes = 0
    updated_nodes = 0
    created_edges = 0
    node_map = {}  # name → node for relationship linking
    node_ids = []

    # ── Build a context snippet from the content ──
    content = source_data.get('content', '')
    snippet = content[:300] if content else None

    # ── Create/update nodes ──
    for concept in validated.get('concepts', []):
        name = concept.get('name', '').strip()
        if not name:
            continue

        node = find_or_create_node(
            db=db,
            user_id=user_id,
            name=name,
            category=concept.get('category') or validated.get('category'),
            node_type=concept.get('type', 'concept'),
            confidence=concept.get('confidence', 0.5),
            snippet=snippet,
        )
        if node:
            if node.encounter_count == 1:
                created_nodes += 1
            else:
                updated_nodes += 1
            node_map[canonicalize(name)] = node
            node_ids.append(node.id)

    # ── Create/reinforce edges ──
    for (from_name, rel_type, to_name) in validated.get('relationships', []):
        from_canonical = canonicalize(from_name)
        to_canonical = canonicalize(to_name)

        from_node = node_map.get(from_canonical)
        to_node = node_map.get(to_canonical)

        # If we don't have both nodes in this batch, try to find them in DB
        if not from_node:
            from_node = db.query(KnowledgeNode).filter(
                KnowledgeNode.user_id == user_id,
                KnowledgeNode.canonical_name == from_canonical,
            ).first()
        if not to_node:
            to_node = db.query(KnowledgeNode).filter(
                KnowledgeNode.user_id == user_id,
                KnowledgeNode.canonical_name == to_canonical,
            ).first()

        if from_node and to_node:
            edge = upsert_edge(db, user_id, from_node.id, to_node.id, rel_type)
            if edge and edge.source_count == 1:
                created_edges += 1

    # ── Create KnowledgeSource ──
    languages = source_data.get('detected_languages', [])
    source = KnowledgeSource(
        user_id=user_id,
        url=source_data.get('url', ''),
        url_hash=url_hash,
        domain=source_data.get('domain', ''),
        page_title=source_data.get('page_title', ''),
        content_type=source_data.get('source_type'),
        detected_language=', '.join(languages) if languages else None,
        has_code_blocks=bool(languages),
        estimated_reading_minutes=source_data.get('estimated_reading_minutes', 0),
        learning_intent=source_data.get('learning_intent'),
        extraction_method=validated.get('extraction_method', 'hybrid'),
        ai_confidence=validated.get('confidence', 0.0),
        raw_extracted_text=content[:3000] if content else None,
        rule_extracted_data={
            'languages': source_data.get('detected_languages', []),
            'technologies': source_data.get('detected_technologies', []),
            'headings': source_data.get('headings', [])[:10],
        },
        node_ids=node_ids,
    )
    db.add(source)

    # ── Create LearningSession ──
    db.flush()  # Ensure source has ID
    session = LearningSession(
        user_id=user_id,
        source_id=source.id,
        duration_seconds=0,  # Updated later from tracking data
        learning_intent=source_data.get('learning_intent'),
        concepts_encountered=[c['name'] for c in validated.get('concepts', [])],
    )
    db.add(session)

    # Commit everything
    db.commit()

    return {
        'created_nodes': created_nodes,
        'updated_nodes': updated_nodes,
        'created_edges': created_edges,
        'total_concepts': len(validated.get('concepts', [])),
        'source_id': source.id,
    }


# ═══════════════════════════════════════════════════════════
#  MAIN PROCESSING PIPELINE
# ═══════════════════════════════════════════════════════════

def process_educational_content(user_id: str, data: dict, url_hash: str):
    """Full processing pipeline — called as a background task.

    1. Rule-based pre-processing
    2. AI concept extraction (if needed)
    3. Confidence validation
    4. Knowledge graph update
    """
    from app.config.database import SessionLocal

    db = SessionLocal()
    try:
        # Double-check dedup (another request may have processed this URL)
        existing = db.query(KnowledgeSource).filter(
            KnowledgeSource.user_id == user_id,
            KnowledgeSource.url_hash == url_hash,
        ).first()
        if existing:
            print(f"[LCIE] URL already processed (dedup): {data.get('url', '')[:60]}")
            return

        print(f"[LCIE] Processing: {data.get('page_title', '')[:80]} ({data.get('domain', '')})")

        # Stage 1: Rule-based extraction
        rule_result = rule_based_extract(data)
        print(f"[LCIE]   Rule-based: {len(rule_result['known_concepts'])} concepts, "
              f"category={rule_result['known_category']}, needs_ai={rule_result['needs_ai']}")

        # Stage 2: AI extraction (only if needed)
        ai_result = None
        if rule_result['needs_ai']:
            ai_result = ai_concept_extract(data, rule_result)
            if ai_result:
                print(f"[LCIE]   AI extracted: {len(ai_result.get('concepts', []))} concepts, "
                      f"confidence={ai_result.get('confidence', 0):.2f}")
            else:
                print("[LCIE]   AI extraction returned no results")

        # Stage 3: Validation
        validated = validate_extraction(ai_result, rule_result)
        print(f"[LCIE]   Validated: {len(validated['concepts'])} concepts, "
              f"method={validated['extraction_method']}")

        # Skip if no concepts found at all
        if not validated['concepts']:
            print("[LCIE]   No concepts extracted, skipping graph update")
            # Still record the source for dedup purposes
            source = KnowledgeSource(
                user_id=user_id,
                url=data.get('url', ''),
                url_hash=url_hash,
                domain=data.get('domain', ''),
                page_title=data.get('page_title', ''),
                content_type=data.get('source_type'),
                learning_intent=data.get('learning_intent'),
                extraction_method='none',
                ai_confidence=0.0,
                node_ids=[],
            )
            db.add(source)
            db.commit()
            return

        # Stage 4: Knowledge graph update
        result = update_knowledge_graph(db, user_id, validated, data, url_hash)
        print(f"[LCIE] ✓ Graph updated: +{result['created_nodes']} new nodes, "
              f"~{result['updated_nodes']} updated, +{result['created_edges']} edges")

    except Exception as e:
        print(f"[LCIE] Processing error: {e}")
        db.rollback()
    finally:
        db.close()


# ═══════════════════════════════════════════════════════════
#  ON-DEMAND SUMMARY GENERATION
# ═══════════════════════════════════════════════════════════

def generate_node_summary(db: Session, user_id: str, node_id: str) -> Optional[str]:
    """Generate a summary for a knowledge node ON DEMAND.

    Uses the node's context_snippets + connected node names for rich context.
    Always uses the current LLM — so future model improvements give better summaries.
    """
    from app.services.ai_service import _call_llm

    node = db.query(KnowledgeNode).filter(
        KnowledgeNode.id == node_id,
        KnowledgeNode.user_id == user_id,
    ).first()

    if not node:
        return None

    # Get connected concepts
    edges = db.query(KnowledgeEdge).filter(
        KnowledgeEdge.user_id == user_id,
        (KnowledgeEdge.source_node_id == node_id) | (KnowledgeEdge.target_node_id == node_id),
    ).all()

    connected = set()
    for edge in edges:
        other_id = edge.target_node_id if edge.source_node_id == node_id else edge.source_node_id
        other = db.query(KnowledgeNode).filter(KnowledgeNode.id == other_id).first()
        if other:
            connected.add(f"{other.name} ({edge.relation_type})")

    # Build context
    snippets = node.context_snippets or []
    snippets_text = '\n---\n'.join(snippets[:3]) if snippets else 'No context snippets available.'
    connected_text = ', '.join(list(connected)[:10]) if connected else 'None'

    system_prompt = """You are a concise academic summarizer.
Generate a clear, helpful summary of what this concept covers and why it matters.
Use 3-5 bullet points. No intro text, no headers — just bullets starting with •."""

    user_prompt = f"""Concept: {node.name}
Category: {node.category or 'General'}
Connected concepts: {connected_text}
Times encountered: {node.encounter_count}

Context from sources:
{snippets_text}

Generate a summary of what "{node.name}" covers based on the context above."""

    try:
        response = _call_llm(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.4,
            max_tokens=400,
        )
        return response.strip()
    except Exception as e:
        print(f"[LCIE] Summary generation error: {e}")
        return None


# ═══════════════════════════════════════════════════════════
#  QUERY HELPERS
# ═══════════════════════════════════════════════════════════

def get_knowledge_graph(db: Session, user_id: str, category: Optional[str] = None) -> dict:
    """Get the full knowledge graph for a user (nodes + edges)."""
    nodes_query = db.query(KnowledgeNode).filter(KnowledgeNode.user_id == user_id)
    if category:
        nodes_query = nodes_query.filter(KnowledgeNode.category == category)
    nodes = nodes_query.order_by(KnowledgeNode.encounter_count.desc()).all()

    node_ids = {n.id for n in nodes}

    edges = db.query(KnowledgeEdge).filter(
        KnowledgeEdge.user_id == user_id,
        KnowledgeEdge.source_node_id.in_(node_ids),
        KnowledgeEdge.target_node_id.in_(node_ids),
    ).all()

    return {
        'nodes': nodes,
        'edges': edges,
        'stats': {
            'total_nodes': len(nodes),
            'total_edges': len(edges),
        },
    }


def get_knowledge_stats(db: Session, user_id: str) -> dict:
    """Get comprehensive knowledge statistics."""
    # Counts
    total_nodes = db.query(func.count(KnowledgeNode.id)).filter(
        KnowledgeNode.user_id == user_id
    ).scalar() or 0

    total_edges = db.query(func.count(KnowledgeEdge.id)).filter(
        KnowledgeEdge.user_id == user_id
    ).scalar() or 0

    total_sources = db.query(func.count(KnowledgeSource.id)).filter(
        KnowledgeSource.user_id == user_id
    ).scalar() or 0

    total_reading = db.query(func.sum(KnowledgeSource.estimated_reading_minutes)).filter(
        KnowledgeSource.user_id == user_id
    ).scalar() or 0

    # Categories
    categories = db.query(
        KnowledgeNode.category,
        func.count(KnowledgeNode.id).label('count'),
    ).filter(
        KnowledgeNode.user_id == user_id,
        KnowledgeNode.category.isnot(None),
    ).group_by(KnowledgeNode.category).order_by(func.count(KnowledgeNode.id).desc()).all()

    # Intent distribution
    intents = db.query(
        KnowledgeSource.learning_intent,
        func.count(KnowledgeSource.id).label('count'),
    ).filter(
        KnowledgeSource.user_id == user_id,
        KnowledgeSource.learning_intent.isnot(None),
    ).group_by(KnowledgeSource.learning_intent).all()

    intent_dist = {i.learning_intent: i.count for i in intents}

    # Top concepts by encounter count
    top_concepts = db.query(KnowledgeNode).filter(
        KnowledgeNode.user_id == user_id,
    ).order_by(KnowledgeNode.encounter_count.desc()).limit(15).all()

    # Mastery overview
    mastery_ranges = {'beginner': 0, 'developing': 0, 'proficient': 0, 'mastered': 0}
    all_nodes = db.query(KnowledgeNode.mastery_level).filter(
        KnowledgeNode.user_id == user_id
    ).all()
    for (m,) in all_nodes:
        if m < 0.25:
            mastery_ranges['beginner'] += 1
        elif m < 0.50:
            mastery_ranges['developing'] += 1
        elif m < 0.75:
            mastery_ranges['proficient'] += 1
        else:
            mastery_ranges['mastered'] += 1

    return {
        'total_nodes': total_nodes,
        'total_edges': total_edges,
        'total_sources': total_sources,
        'total_learning_minutes': total_reading,
        'categories': [{'name': c.category, 'count': c.count} for c in categories],
        'intent_distribution': intent_dist,
        'difficulty_distribution': {},  # Could be added from source analysis
        'top_concepts': [
            {'name': n.name, 'category': n.category, 'encounters': n.encounter_count, 'mastery': n.mastery_level}
            for n in top_concepts
        ],
        'mastery_overview': mastery_ranges,
    }
