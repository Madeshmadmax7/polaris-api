"""
LifeOS – AI Service
Handles communication with LLM providers (OpenAI / Groq).
All AI calls go through backend only — extension never calls AI directly.
"""

import json
from typing import Optional, List
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.services.rag_service import retrieve_context_for_query

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


def _call_llm(messages: List[dict], temperature: float = 0.7, max_tokens: int = 2000) -> str:
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
#  STUDY PLAN GENERATION
# ═══════════════════════════════════════════════════════════

def generate_study_plan(
    db: Session,
    user_id: str,
    goal: str,
    duration_days: int,
    document_id: Optional[str] = None,
) -> dict:
    """
    Generate a structured study plan using RAG-grounded AI.
    Retrieves relevant syllabus context to reduce hallucination.
    """
    # Retrieve context from uploaded documents
    context = ""
    if document_id:
        context = retrieve_context_for_query(db, user_id, goal, document_id)

    context_section = ""
    if context:
        context_section = f"""
## SYLLABUS CONTEXT (from uploaded documents):
{context}

Use this context to ground your study plan. Only include topics that appear in the syllabus.
"""

    system_prompt = """You are an expert academic planner and tutor. 
Create structured, actionable study plans grounded in the provided syllabus content.
Always respond in valid JSON format."""

    user_prompt = f"""Create a detailed study plan for the following goal:

**Goal:** {goal}
**Duration:** {duration_days} days

{context_section}

Respond with a JSON object containing:
{{
    "title": "Short title for this plan",
    "overview": "Brief overview of the plan",
    "daily_plan": [
        {{
            "day": 1,
            "topic": "Topic name",
            "subtopics": ["Sub1", "Sub2"],
            "estimated_hours": 2,
            "resources": ["Resource suggestions"],
            "goals": ["What to achieve"],
            "is_revision": false
        }}
    ],
    "revision_strategy": "How to handle revision cycles",
    "milestones": [
        {{"day": 7, "milestone": "Complete Chapter 1-3"}}
    ]
}}

Include revision days every 5-7 days. Adapt difficulty progressively.
Ensure topic allocation covers the entire syllabus if provided."""

    response = _call_llm([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ], temperature=0.5, max_tokens=4000)

    # Parse JSON response
    try:
        # Try to extract JSON from response
        json_start = response.find("{")
        json_end = response.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            plan = json.loads(response[json_start:json_end])
        else:
            plan = {"title": goal, "raw_response": response, "daily_plan": []}
    except json.JSONDecodeError:
        plan = {"title": goal, "raw_response": response, "daily_plan": []}

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
