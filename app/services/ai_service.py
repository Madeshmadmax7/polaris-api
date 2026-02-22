"""
LifeOS – AI Service (Unified Learning Flow)
Single API call generates: YouTube chapters + quiz questions.
No standalone quiz generation - integrated with study plans.
"""

import json
from typing import Optional, List
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.services.rag_service import get_document_content

# Lazy-loaded clients
_openai_client = None
_groq_client = None


def _get_ai_client():
    """Get the configured AI client."""
    global _openai_client, _groq_client

    if settings.AI_PROVIDER == "groq":
        if _groq_client is None:
            from groq import Groq
            _groq_client = Groq(api_key=settings.GROQ_API_KEY)
        return _groq_client, "groq"
    else:
        if _openai_client is None:
            from openai import OpenAI
            _openai_client = OpenAI(api_key=settings.OPENAI_API_KEY)
        return _openai_client, "openai"


def _call_llm(messages: List[dict], temperature: float = 0.7, max_tokens: int = 4000) -> str:
    """Unified LLM call for both providers."""
    client, provider = _get_ai_client()

    try:
        response = client.chat.completions.create(
            model=settings.AI_MODEL,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"[AI Error] {str(e)}"


# ═══════════════════════════════════════════════════════════
#  UNIFIED STUDY PLAN + QUIZ GENERATION
# ═══════════════════════════════════════════════════════════

def generate_study_plan_with_quiz(
    db: Session,
    user_id: str,
    goal: str,
    duration_days: int,
    document_id: Optional[str] = None,
) -> dict:
    """
    UNIFIED API CALL: Generate complete learning path in one go.
    
    Returns JSON with:
    - chapters: List of topics with YouTube video links
    - quiz: 5-10 MCQ questions to test understanding
    - metadata: Title, overview, etc.
    """
    # Get PDF content if available
    pdf_content = ""
    if document_id:
        pdf_content = get_document_content(db, document_id) or ""

    context_section = ""
    if pdf_content:
        # Truncate if too long (keep first ~4000 chars)
        truncated_content = pdf_content[:4000]
        if len(pdf_content) > 4000:
            truncated_content += "\n... [content truncated]"
        
        context_section = f"""
## PDF CONTEXT (from uploaded document):
{truncated_content}

Base your study plan on this content. Extract key topics and find relevant YouTube videos.
"""

    system_prompt = """You are an expert educational consultant and YouTube content curator.
Your job is to create study plans with curated YouTube video chapters and assessment quizzes.

CRITICAL: Respond ONLY with a valid JSON object. No markdown formatting, no code blocks."""

    user_prompt = f"""Create a complete learning plan for this goal:

**Goal:** {goal}
**Duration:** {duration_days} days

{context_section}

Respond with this EXACT JSON structure (no markdown, no code blocks):

{{
    "title": "Concise title for the plan",
    "overview": "Brief 2-3 sentence overview",
    "chapters": [
        {{
            "chapter_number": 1,
            "title": "Chapter title",
            "description": "What this chapter covers",
            "youtube_url": "https://www.youtube.com/watch?v=ACTUAL_VIDEO_ID",
            "duration_estimate": "15 min",
            "key_topics": ["Topic 1", "Topic 2"]
        }}
    ],
    "quiz": [
        {{
            "question": "Full question text",
            "options": ["Option A", "Option B", "Option C", "Option D"],
            "correct_answer": 0,
            "explanation": "Why this answer is correct"
        }}
    ]
}}

REQUIREMENTS:
1. Generate 3-8 chapters based on content complexity
2. YouTube URLs MUST be real educational videos (use popular channels: Khan Academy, 3Blue1Brown, CrashCourse, freeCodeCamp, etc.)
3. Generate 5-10 quiz questions covering all chapters
4. Quiz questions should test understanding, not just recall
5. Each question has 4 options, correct_answer is index (0-3)

Be specific with YouTube video recommendations - use actual video IDs from educational channels."""

    response = _call_llm([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ], temperature=0.6, max_tokens=4000)

    # Parse JSON response
    try:
        # Remove markdown code blocks if present
        clean_response = response.strip()
        if clean_response.startswith("```"):
            clean_response = clean_response.split("```")[1]
            if clean_response.startswith("json"):
                clean_response = clean_response[4:]
        clean_response = clean_response.strip()
        
        plan = json.loads(clean_response)
        
        # Validate structure
        if "chapters" not in plan:
            plan["chapters"] = []
        if "quiz" not in plan:
            plan["quiz"] = []
        if "title" not in plan:
            plan["title"] = goal[:100]
        if "overview" not in plan:
            plan["overview"] = f"Study plan for {goal}"
            
    except json.JSONDecodeError as e:
        # Fallback structure
        plan = {
            "title": goal[:100],
            "overview": f"Study plan for {goal}",
            "chapters": [
                {
                    "chapter_number": 1,
                    "title": "Introduction",
                    "description": "Getting started with the topic",
                    "youtube_url": "",
                    "duration_estimate": "10 min",
                    "key_topics": ["Basics"]
                }
            ],
            "quiz": [
                {
                    "question": "This is a placeholder question",
                    "options": ["A", "B", "C", "D"],
                    "correct_answer": 0,
                    "explanation": "AI generation failed, please try again"
                }
            ],
            "error": f"Failed to parse AI response: {str(e)}",
            "raw_response": response[:500]
        }

    return plan


# ═══════════════════════════════════════════════════════════
#  QUIZ GENERATION
# ═══════════════════════════════════════════════════════════

QUIZ_TEMPLATE_FALLBACK = {
    "mcq": [
        {
            "type": "mcq",
            "question": "Sample question about the topic",
            "options": ["A", "B", "C", "D"],
            "correct": 0,
            "explanation": "Explanation here",
        }
    ],
    "conceptual": [
        {
            "type": "conceptual",
            "question": "Explain the concept in your own words",
            "rubric": "Key points to cover",
        }
    ],
}


def generate_quiz(
    db: Session,
    user_id: str,
    topic: str,
    difficulty: str = "medium",
    document_id: Optional[str] = None,
) -> dict:
    """
    Generate quiz questions using RAG context.
    Falls back to template if AI unavailable.

    Generates:
    - 5 MCQs
    - 2 conceptual questions
    - 1 optional coding problem (if relevant)
    """
    # Retrieve context
    context = ""
    if document_id:
        context = retrieve_context_for_query(db, user_id, topic, document_id)

    difficulty_guide = {
        "easy": "basic recall and understanding",
        "medium": "application and analysis",
        "hard": "synthesis, evaluation, and edge cases",
    }

    context_section = f"\n## REFERENCE MATERIAL:\n{context}\n" if context else ""

    system_prompt = """You are an expert quiz generator. Create challenging but fair questions.
Always respond in valid JSON format. Grade difficulty appropriately."""

    user_prompt = f"""Generate a quiz on: **{topic}**
Difficulty: **{difficulty}** ({difficulty_guide.get(difficulty, 'medium level')})

{context_section}

Respond with a JSON object:
{{
    "questions": [
        {{
            "id": 1,
            "type": "mcq",
            "question": "Question text",
            "options": ["A", "B", "C", "D"],
            "correct": 0,
            "explanation": "Why this is correct"
        }},
        // ... 5 MCQs total
        {{
            "id": 6,
            "type": "conceptual",
            "question": "Explain X...",
            "rubric": "Key points: ..."
        }},
        // ... 2 conceptual questions
        {{
            "id": 8,
            "type": "coding",
            "question": "Write a function that...",
            "language": "python",
            "test_cases": ["input → output"],
            "solution_hint": "Approach hint"
        }}
    ]
}}

Generate exactly 5 MCQs, 2 conceptual questions, and 1 coding problem if the topic involves programming."""

    response = _call_llm([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ], temperature=0.6, max_tokens=3000)

    try:
        json_start = response.find("{")
        json_end = response.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            quiz = json.loads(response[json_start:json_end])
        else:
            quiz = QUIZ_TEMPLATE_FALLBACK
    except json.JSONDecodeError:
        quiz = QUIZ_TEMPLATE_FALLBACK

    # Randomize MCQ order
    import random
    if "questions" in quiz:
        mcqs = [q for q in quiz["questions"] if q.get("type") == "mcq"]
        others = [q for q in quiz["questions"] if q.get("type") != "mcq"]
        random.shuffle(mcqs)
        quiz["questions"] = mcqs + others

    return quiz


# ═══════════════════════════════════════════════════════════
#  RAG QUERY (General)
# ═══════════════════════════════════════════════════════════

def rag_query(
    db: Session,
    user_id: str,
    query: str,
    document_id: Optional[str] = None,
    top_k: int = 5,
) -> dict:
    """
    General RAG query — retrieves context and generates grounded answer.
    """
    context = retrieve_context_for_query(db, user_id, query, document_id, top_k)

    if not context:
        return {
            "answer": "No relevant documents found. Please upload a syllabus or curriculum first.",
            "sources": [],
            "confidence": 0.0,
        }

    system_prompt = """You are an intelligent academic assistant. 
Answer questions based ONLY on the provided context. 
If the context doesn't contain the answer, say so explicitly.
Be precise, thorough, and educational."""

    user_prompt = f"""## CONTEXT:
{context}

## QUESTION:
{query}

Provide a clear, detailed answer grounded in the context above."""

    answer = _call_llm([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ], temperature=0.3)

    return {
        "answer": answer,
        "sources": [{"snippet": c[:200]} for c in context.split("\n\n---\n\n")],
        "confidence": min(0.9, len(context) / 1000),  # Rough confidence estimate
    }
