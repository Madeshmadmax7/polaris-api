"""
LifeOS – Semantic Chapter-Video Matching Service
Uses sentence-transformers (all-MiniLM-L6-v2) for embedding-based cosine similarity.
Model is loaded ONCE at FastAPI startup, not per request.

ONLY handles learning chapter → YouTube video matching.
Does NOT touch distraction/blocking/tracking logic.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Singleton model instance ────────────────────────────────────────────────
# Loaded once on first call to get_model() (or explicitly via preload_model()).
_model = None


def preload_model() -> bool:
    """
    Eagerly load the embedding model.
    Call this at application startup so the first request is not slow.
    Returns True if model loaded successfully, False otherwise.
    """
    global _model
    if _model is not None:
        return True
    try:
        from sentence_transformers import SentenceTransformer
        logger.info("[Matching] Loading all-MiniLM-L6-v2 (384-dim)...")
        _model = SentenceTransformer("all-MiniLM-L6-v2")
        logger.info("[Matching] Embedding model ready.")
        return True
    except Exception as e:
        logger.error(f"[Matching] Failed to load model: {e}")
        _model = None
        return False


def get_model():
    """Return the singleton model, loading it lazily if needed."""
    global _model
    if _model is None:
        preload_model()
    return _model


# ─── Embedding helpers ────────────────────────────────────────────────────────

def embed_text(text: str) -> Optional[list]:
    """
    Generate an L2-normalised embedding for the given text.
    Returns a plain Python list[float] (JSON-serialisable) or None on failure.
    """
    model = get_model()
    if model is None:
        return None
    try:
        # normalize_embeddings=True → dot product == cosine similarity
        vec = model.encode(text.strip(), normalize_embeddings=True)
        return vec.tolist()
    except Exception as e:
        logger.error(f"[Matching] embed_text failed: {e}")
        return None


def cosine_similarity(a: list, b: list) -> float:
    """
    Cosine similarity between two L2-normalised vectors.
    Because they are normalised, this is just the dot product.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    return float(max(-1.0, min(1.0, dot)))


# ─── Text builders ─────────────────────────────────────────────────────────────

def build_chapter_text(
    chapter_title: str,
    keyword_importance: Optional[dict] = None,
    description: Optional[str] = None,
) -> str:
    """
    Combine chapter title + description + top keywords into a single string
    for embedding. Keyword weighting: high-importance keywords are repeated
    so they influence the embedding more strongly.
    """
    parts = [chapter_title]

    if description:
        parts.append(description[:300])  # First 300 chars enough for context

    if keyword_importance:
        # Sort by importance descending; keep top-15
        sorted_kw = sorted(keyword_importance.items(), key=lambda x: x[1], reverse=True)[:15]
        for kw, importance in sorted_kw:
            # Repeat high-importance (≥75) keywords for emphasis
            repeat = 2 if importance >= 75 else 1
            parts.extend([kw] * repeat)

    return " ".join(parts)


def build_video_text(
    video_title: str,
    video_description: Optional[str] = None,
) -> str:
    """Combine video title + optional description for embedding."""
    parts = [video_title]
    if video_description:
        parts.append(video_description[:300])
    return " ".join(parts)


# ─── Matching logic ───────────────────────────────────────────────────────────

# Thresholds
THRESHOLD_MATCH = 0.60
THRESHOLD_CONFIRM = 0.45


def compute_match(
    chapter_embedding: list,
    video_title: str,
    video_description: Optional[str] = None,
) -> tuple:
    """
    Compare a pre-computed chapter embedding against a new video.

    Returns:
        (similarity: float, status: str)
        status is one of: 'match' | 'needs_confirmation' | 'no_match'
    """
    video_text = build_video_text(video_title, video_description)
    video_embedding = embed_text(video_text)

    if video_embedding is None:
        logger.warning("[Matching] Could not embed video text — falling back to no_match")
        return 0.0, "no_match"

    sim = cosine_similarity(chapter_embedding, video_embedding)
    logger.debug(f"[Matching] '{video_title[:60]}' similarity={sim:.3f}")

    if sim >= THRESHOLD_MATCH:
        return sim, "match"
    elif sim >= THRESHOLD_CONFIRM:
        return sim, "needs_confirmation"
    else:
        return sim, "no_match"


def find_best_chapter(
    chapters: list,  # list of dicts with keys: chapter_index, chapter_title, chapter_embedding, is_completed
    video_title: str,
    video_description: Optional[str] = None,
) -> Optional[dict]:
    """
    Find the best matching chapter for a given video across all chapters.

    Returns a dict with match info, or None if nothing meets threshold.
    Prefers incomplete chapters; will still return completed ones for re-watch tracking.

    Each item in `chapters` must have:
        chapter_index       int
        chapter_title       str
        chapter_embedding   list[float] | None
        is_completed        bool
        plan_id             str
        plan_title          str
    """
    video_text = build_video_text(video_title, video_description)
    video_embedding = embed_text(video_text)

    if video_embedding is None:
        logger.warning("[Matching] Could not generate video embedding")
        return None

    best_sim = -1.0
    best_chapter = None

    for ch in chapters:
        ch_emb = ch.get("chapter_embedding")
        if not ch_emb:
            continue

        sim = cosine_similarity(ch_emb, video_embedding)

        # Print every chapter's score for debugging (always visible in FastAPI terminal)
        print(
            f"[Matching] '{ch['chapter_title']}' sim={sim:.3f} "
            f"(incomplete={not ch['is_completed']})"
        )
        logger.info(
            f"[Matching] '{ch['chapter_title']}' sim={sim:.3f} "
            f"(incomplete={not ch['is_completed']})"
        )

        if sim < THRESHOLD_CONFIRM:
            continue

        # Scoring: raw similarity + bonus for incomplete chapters
        score = sim
        if not ch["is_completed"]:
            score += 0.15  # Prefer incomplete chapters

        if score > best_sim:
            best_sim = score
            best_chapter = {**ch, "similarity": sim}

    if best_chapter is None:
        logger.info(f"[Matching] No chapter reached threshold for '{video_title}'")
        return None

    sim = best_chapter["similarity"]
    if sim >= THRESHOLD_MATCH:
        best_chapter["match_type"] = "rewatch" if best_chapter["is_completed"] else "semantic"
    else:
        best_chapter["match_type"] = "needs_confirmation"

    logger.info(
        f"[Matching] Best: '{best_chapter['chapter_title']}' "
        f"sim={sim:.3f} type={best_chapter['match_type']}"
    )
    return best_chapter
