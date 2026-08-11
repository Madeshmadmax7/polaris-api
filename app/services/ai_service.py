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


# ═══════════════════════════════════════════════════════════
#  LEARNER PROFILE HELPERS
# ═══════════════════════════════════════════════════════════

def get_learner_profile_context(db: Session, user_id: str) -> str:
    """Build a compact learner history string to inject into plan prompts.
    Returns empty string if no history exists yet (first plan — no cost)."""
    from app.models.models import LearnerProfile
    profile = db.query(LearnerProfile).filter(LearnerProfile.user_id == user_id).first()
    if not profile or not profile.completed_plans:
        return ""

    lines = ["## LEARNER HISTORY (personalise this plan based on prior learning):"]

    for p in (profile.completed_plans or [])[-5:]:  # last 5 plans only
        lines.append(f'- Previously studied: "{p["title"]}" (quiz score: {p["score"]}%)')

    if profile.mastered_topics:
        topics = (profile.mastered_topics or [])[-20:]
        lines.append(f"- Topics already covered: {', '.join(topics)}")

    if profile.weak_plan_topics:
        lines.append(f"- Areas needing reinforcement if relevant: {', '.join(profile.weak_plan_topics)}")

    if profile.avg_quiz_score:
        if profile.avg_quiz_score >= 75:
            pace = "fast learner"
        elif profile.avg_quiz_score < 50:
            pace = "needs more practice"
        else:
            pace = "steady learner"
        lines.append(f"- Avg quiz score: {profile.avg_quiz_score}% ({pace})")

    lines += [
        "",
        "INSTRUCTIONS:",
        "- SKIP beginner chapters on topics already covered above — start from intermediate/advanced.",
        "- DO NOT re-explain fundamentals the learner already knows.",
        "- If this goal overlaps with weak areas listed, add extra chapters on those subtopics.",
        "- Calibrate quiz difficulty to learner's past performance.",
    ]
    return "\n".join(lines)


def update_learner_profile(
    db: Session,
    user_id: str,
    plan_title: str,
    quiz_score: float,
    chapter_titles: List[str],
) -> None:
    """Update learner profile after a quiz attempt. No LLM call — pure DB aggregation."""
    from app.models.models import LearnerProfile
    profile = db.query(LearnerProfile).filter(LearnerProfile.user_id == user_id).first()
    if not profile:
        profile = LearnerProfile(user_id=user_id)
        db.add(profile)

    # Deduplicate by plan title, keep latest score
    completed = [p for p in (profile.completed_plans or []) if p.get("title") != plan_title]
    completed.append({"title": plan_title, "score": round(quiz_score, 1), "chapters": len(chapter_titles)})
    profile.completed_plans = completed[-20:]

    # Mastered: chapter titles from plans with score >= 60
    if quiz_score >= 60:
        mastered = set(profile.mastered_topics or [])
        mastered.update(chapter_titles)
        profile.mastered_topics = list(mastered)[-50:]

    # Weak: plan titles where score < 60 (remove if improved)
    if quiz_score < 60:
        weak = list(profile.weak_plan_topics or [])
        if plan_title not in weak:
            weak.append(plan_title)
        profile.weak_plan_topics = weak[-10:]
    else:
        profile.weak_plan_topics = [t for t in (profile.weak_plan_topics or []) if t != plan_title]

    all_scores = [p["score"] for p in profile.completed_plans]
    profile.avg_quiz_score = round(sum(all_scores) / len(all_scores), 1) if all_scores else 0.0

    db.commit()
    print(f"[LearnerProfile] Updated: user={user_id[:8]}... plan='{plan_title}' score={quiz_score:.1f}%")


# ═══════════════════════════════════════════════════════════
#  AGENT QUIZ ANALYSIS
# ═══════════════════════════════════════════════════════════

def analyze_quiz_result(
    quiz_questions: list,
    quiz_results: list,
    plan_title: str,
    current_duration_days: int,
    score_pct: float,
    quiz_history: list = None,
    watch_stats: dict = None,
) -> dict:
    """Agent reasoning: analyze wrong MCQ answers, identify weak topics,
    and recommend precise plan adjustments. Single focused LLM call.
    All data signals (quiz history, watch speed, completion) are used for richer reasoning.
    """
    wrong_items = []
    for r in quiz_results:
        if not r.get("correct"):
            q_idx = r.get("question_number", 0)
            if q_idx < len(quiz_questions):
                q = quiz_questions[q_idx]
                options = q.get("options", [])
                u_idx = r.get("user_answer")
                c_idx = q.get("correct_answer", 0)
                wrong_items.append({
                    "question": q.get("question", "")[:120],
                    "your_answer": options[u_idx] if u_idx is not None and u_idx < len(options) else "Unanswered",
                    "correct_answer": options[c_idx] if c_idx < len(options) else "Unknown",
                    "explanation": q.get("explanation", "")[:150],
                })

    if not wrong_items:
        return {
            "weak_topics": [],
            "reasoning": "Excellent! You answered all questions correctly. You have mastered this material.",
            "recommended_extra_days": 0,
            "recommended_difficulty": "hard",
            "recommended_total_days": current_duration_days,
            "agent_message": "Outstanding performance. You are ready to advance to a more challenging topic.",
            "should_continue": False,
        }

    # Score-based baseline (LLM may refine)
    if score_pct < 40:
        base_extra = max(5, current_duration_days // 2)
        base_diff = "easy"
    elif score_pct < 60:
        base_extra = max(3, current_duration_days // 3)
        base_diff = "easy"
    elif score_pct < 75:
        base_extra = 2
        base_diff = "medium"
    else:
        base_extra = 0
        base_diff = "medium"

    wrong_summary = "\n".join([
        f"- Q: {w['question']}\n  Wrong: \"{w['your_answer']}\" | Correct: \"{w['correct_answer']}\""
        for w in wrong_items[:8]
    ])

    # Additional data signals for richer reasoning
    history_ctx = ""
    if quiz_history:
        scores = [f"{h['score']:.0f}%" for h in quiz_history[-3:]]
        trend = " -> ".join(scores)
        history_ctx = f"\nPast quiz scores on this plan: {trend} (latest last)"

    watch_ctx = ""
    if watch_stats:
        rate = watch_stats.get("avg_playback_rate", 1.0)
        if rate > 1.5:
            watch_ctx = f"\nStudent watches at {rate:.1f}x speed — may be rushing through material."
        slow = watch_stats.get("slow_chapters", [])
        if slow:
            watch_ctx += f"\nChapters with poor video completion: {', '.join(slow[:3])}"

    system_prompt = """You are an AI learning coach. Analyze quiz mistakes and recommend a precise improvement plan.
Respond ONLY with valid JSON. Be specific and constructive."""

    user_prompt = f"""Student scored {score_pct:.0f}% on \"{plan_title}\" ({len(wrong_items)} wrong answers out of {len(quiz_results)} total).
Current plan: {current_duration_days} days.{history_ctx}{watch_ctx}

Wrong answers:
{wrong_summary}

Respond with EXACTLY this JSON (no markdown, no code blocks):
{{
    "weak_topics": ["specific topic 1", "specific topic 2"],
    "reasoning": "2-3 sentences: what the student struggles with and why",
    "recommended_extra_days": {base_extra},
    "recommended_difficulty": "{base_diff}",
    "agent_message": "One specific encouraging sentence with concrete advice mentioning the exact weak topics"
}}

Rules:
- weak_topics: 2-5 specific keywords extracted DIRECTLY from the wrong question text
- recommended_extra_days: extra days to ADD (not total) — 0 if score >= 75
- recommended_difficulty: must be exactly "easy", "medium", or "hard"
- agent_message: be specific, mention topic names"""

    response = _call_llm([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ], temperature=0.3, max_tokens=500)

    try:
        clean = response.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        result = json.loads(clean.strip())
        result["recommended_total_days"] = current_duration_days + result.get("recommended_extra_days", 0)
        result["should_continue"] = score_pct < 75
        return result
    except Exception:
        return {
            "weak_topics": [w["question"][:30] for w in wrong_items[:3]],
            "reasoning": f"You got {len(wrong_items)} questions wrong. Review those specific topics before retrying.",
            "recommended_extra_days": base_extra,
            "recommended_difficulty": base_diff,
            "recommended_total_days": current_duration_days + base_extra,
            "agent_message": "Focus on the highlighted topics above and take practice notes.",
            "should_continue": score_pct < 75,
        }

# Lazy-loaded clients
_openai_client = None
_groq_clients = []
_current_groq_index = 0


def _get_ai_client():
    """Get the configured AI client."""
    global _openai_client, _groq_clients, _current_groq_index

    if settings.AI_PROVIDER == "groq":
        if not _groq_clients:
            from groq import Groq
            # Support multiple comma-separated keys for fallback
            keys = [k.strip() for k in (settings.GROQ_API_KEY or "").split(",")]
            _groq_clients = [Groq(api_key=k) for k in keys if k]
        
        if not _groq_clients:
            raise ValueError("No GROQ_API_KEY configured")
            
        return _groq_clients[_current_groq_index], "groq"
    else:
        if _openai_client is None:
            from openai import OpenAI
            _openai_client = OpenAI(api_key=settings.OPENAI_API_KEY)
        return _openai_client, "openai"


def _call_llm(messages: List[dict], temperature: float = 0.7, max_tokens: int = 4000) -> str:
    """Unified LLM call for both providers with automatic rate-limit fallback."""
    global _current_groq_index
    
    # Calculate max retries based on available keys
    max_retries = 1
    if settings.AI_PROVIDER == "groq":
        # Ensure clients are initialized to get the count
        _get_ai_client()
        max_retries = max(1, len(_groq_clients))

    last_error = None
    for attempt in range(max_retries):
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
            last_error = e
            error_str = str(e).lower()
            
            # Check if it's a rate limit error (429)
            if "rate limit" in error_str or "429" in error_str or "too many requests" in error_str:
                if provider == "groq" and len(_groq_clients) > 1:
                    print(f"[AI Fallback] Rate limit reached on key index {_current_groq_index}. Switching to next key.")
                    _current_groq_index = (_current_groq_index + 1) % len(_groq_clients)
                    continue  # Try next key
            
            # For any other error or if we're out of retries, break out
            break

    return f"[AI Error] {str(last_error)}"


def retrieve_context_for_query(
    db: Session, 
    user_id: str, 
    query: str, 
    document_id: Optional[str] = None,
    top_k: int = 5
) -> str:
    """Retrieve relevant context for a query (simplified - returns full document)."""
    if document_id:
        content = get_document_content(db, document_id)
        if content:
            # Return first 2000 chars as context
            return content[:2000]
    return ""


# ═══════════════════════════════════════════════════════════
#  UNIFIED STUDY PLAN + QUIZ GENERATION
# ═══════════════════════════════════════════════════════════

def generate_study_plan_with_quiz(
    db: Session,
    user_id: str,
    goal: str,
    duration_days: int,
    document_id: Optional[str] = None,
    difficulty: str = "medium",
    learner_context: str = "",
) -> dict:
    """
    UNIFIED API CALL: Generate complete learning path in one go.
    difficulty: 'easy' | 'medium' | 'hard'  — adapts quiz question depth.
    learner_context: compact profile string to skip already-mastered topics.
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

    # Inject learner profile — skips known topics, avoids wasting tokens on basics
    if learner_context:
        context_section += f"\n{learner_context}\n"

    system_prompt = """You are an expert educational consultant and YouTube content curator.
Your job is to create study plans with curated YouTube video chapters and assessment quizzes.

CRITICAL: Respond ONLY with a valid JSON object. No markdown formatting, no code blocks."""

    # Dynamic targets based on duration:
    # 1 chapter per day so number of lessons scales directly with duration_days
    chapters_target = max(3, min(duration_days, 21))
    quiz_target = max(4, min(8, chapters_target))

    difficulty_note = {
        "easy": "Quiz questions should be straightforward — definition-level, direct recall, no trick questions. Beginner-friendly.",
        "medium": "Quiz questions should test understanding and application. Mix recall and scenario-based questions.",
        "hard": "Quiz questions must be advanced — include edge cases, compare-and-contrast, code analysis, and common misconceptions as wrong options. No easy recall.",
    }.get(difficulty, "Mix recall and scenario-based questions.")

    user_prompt = f"""Create a complete {duration_days}-day learning roadmap for this goal:

**Goal:** {goal}
**Duration:** {duration_days} days
**Required chapters:** {chapters_target}
**Required quiz questions:** {quiz_target}

{context_section}

Respond with this EXACT JSON structure (no markdown, no backticks, only pure JSON):

{{
    "title": "{goal}",
    "overview": "Brief 2-3 sentence overview of what will be learned",
    "chapters": [
        {{
            "chapter_number": 1,
            "title": "Chapter title",
            "description": "What this chapter covers",
            "youtube_search_query": "chapter+title+keywords",
            "duration_estimate": "20-30 min",
            "key_topics": ["Topic 1", "Topic 2"],
            "keyword_importance": {{
                "topic_keyword": 90,
                "secondary_keyword": 70
            }},
            "coding_tasks": [
                {{
                    "task_id": "ch1_task1",
                    "title": "Implement basic function",
                    "description": "Write a function `solve(...)` that returns the expected value",
                    "difficulty": "easy",
                    "language": "python",
                    "starter_code": "def solve(n):\\n    # Write your solution here\\n    pass",
                    "test_cases": [
                        {{"input": "5", "expected_output": "10", "is_hidden": false}},
                        {{"input": "0", "expected_output": "0", "is_hidden": false}},
                        {{"input": "-3", "expected_output": "-6", "is_hidden": false}},
                        {{"input": "100", "expected_output": "200", "is_hidden": true}},
                        {{"input": "1", "expected_output": "2", "is_hidden": true}}
                    ],
                    "hints": ["Consider the input types", "Remember to return value directly"],
                    "solution": "def solve(n):\\n    return n * 2"
                }}
            ]
        }}
    ],
    "quiz": [
        {{
            "question": "Full question text",
            "options": ["Option A", "Option B", "Option C", "Option D"],
            "correct_answer": 0,
            "explanation": "Why this answer is correct"
        }}
    ],
    "daily_schedule": [
        {{
            "day": 1,
            "chapters": [1],
            "estimated_time_minutes": 60,
            "topics_focus": "Day 1 focus"
        }}
    ]
}}

CRITICAL RULES:
1. Generate EXACTLY {chapters_target} chapters covering the full {duration_days}-day journey (1 chapter per day).
2. For coding_tasks: Include 1 practical coding challenge per chapter.
3. Every coding task MUST have EXACTLY 5 test_cases: 3 sample cases (is_hidden: false) and 2 hidden edge-case tests (is_hidden: true).
4. Escape newlines in code as \\n (do not write raw unescaped newlines in JSON strings).
5. Generate EXACTLY {quiz_target} quiz questions.
6. "title" field MUST be exactly: {goal}
7. For youtube_search_query: Use '+' separated search terms (e.g. 'python+variables+tutorial').
"""

    token_budget = min(8000, chapters_target * 420 + quiz_target * 120 + 900)
    response = _call_llm([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ], temperature=0.5, max_tokens=token_budget)
    print(f"[AI] Study plan generation: chapters={chapters_target}, quiz={quiz_target}, max_tokens={token_budget}")
    print(f"[AI] Raw response length: {len(response)} chars")

    # Parse JSON response
    plan = None
    try:
        # Remove markdown code blocks if present
        clean_response = response.strip()
        if clean_response.startswith("```"):
            clean_response = clean_response.split("```")[1]
            if clean_response.startswith("json"):
                clean_response = clean_response[4:]
        clean_response = clean_response.strip()
        if clean_response.endswith("```"):
            clean_response = clean_response[:-3].strip()
        
        # Use strict=False to tolerate unescaped control characters in code strings
        plan = json.loads(clean_response, strict=False)
        
    except Exception as e:
        print(f"[AI] Standard JSON parse failed: {e}. Attempting smart repair...")
        
        # ── Smart JSON Repair ──
        try:
            import re
            
            # Clean up response string
            text = response.strip()
            if "```" in text:
                matches = re.findall(r'```(?:json)?\s*(.*?)\s*```', text, re.DOTALL)
                if matches:
                    text = matches[0].strip()

            # Try parsing after strict=False
            plan = json.loads(text, strict=False)
        except Exception as repair_err:
            print(f"[AI] Repair step 1 failed: {repair_err}. Attempting array extraction...")

            try:
                # Extract chapters array
                ch_match = re.search(r'"chapters"\s*:\s*(\[.*?\])(?=\s*,\s*"(?:quiz|daily_schedule)"|\s*\})', text, re.DOTALL)
                if ch_match:
                    chapters = json.loads(ch_match.group(1), strict=False)
                    plan = {
                        "title": goal,
                        "overview": f"A {duration_days}-day study plan for {goal}",
                        "chapters": chapters,
                        "quiz": [],
                        "daily_schedule": []
                    }
                    print(f"[AI] Salvaged {len(chapters)} chapters successfully.")
            except Exception as extract_err:
                print(f"[AI] Array extraction failed: {extract_err}")

    # Validate and fill in defaults
    if plan and isinstance(plan, dict):
        if "chapters" not in plan or not plan["chapters"]:
            plan = None
        else:
            plan["title"] = goal
            if "overview" not in plan:
                plan["overview"] = f"Study plan for {goal}"
            if "quiz" not in plan:
                plan["quiz"] = []
            if "daily_schedule" not in plan or not plan["daily_schedule"]:
                chs = plan["chapters"]
                plan["daily_schedule"] = [
                    {
                        "day": i + 1,
                        "chapters": [chs[i]["chapter_number"] if i < len(chs) else 1],
                        "estimated_time_minutes": 45,
                        "topics_focus": chs[i]["title"] if i < len(chs) else f"Day {i + 1}"
                    }
                    for i in range(min(duration_days, len(chs)))
                ]

    # Fallback multi-chapter generator scaling to match full duration_days
    if not plan:
        print(f"[AI] CRITICAL: Generating full {duration_days}-day structured fallback plan.")
        
        # 14 distinct curriculum templates
        curriculum_templates = [
            ("Introduction & Setup", "Core concepts, interpreter setup, and foundational syntax.", "basics+tutorial", "greet", "name", "'Developer'", "Hello, Developer!", "f'Hello, {name}!'"),
            ("Variables, Data Types & Math", "Numbers, strings, operations, and type conversion.", "variables+data+types", "add_numbers", "a, b", "5, 10", "15", "a + b"),
            ("Booleans & Conditionals", "Logical expressions, if/elif/else statements.", "conditionals+if+else", "is_even", "n", "4", "True", "n % 2 == 0"),
            ("Loops & Iterations", "For loops, while loops, and range iterations.", "for+while+loops", "sum_range", "n", "5", "15", "sum(range(1, n + 1))"),
            ("Lists & Operations", "Working with lists, indexing, slicing, and methods.", "lists+operations", "squares", "numbers", "[1, 2, 3, 4, 5]", "[1, 4, 9, 16, 25]", "[x**2 for x in numbers]"),
            ("Dictionaries & Sets", "Key-value mappings, lookups, and unique sets.", "dictionaries+sets", "count_frequencies", "items", "['a', 'b', 'a']", "{'a': 2, 'b': 1}", "{x: items.count(x) for x in set(items)}"),
            ("Functions & Modular Scope", "Parameters, return values, default args, and local scope.", "functions+scope", "multiply", "x, y=2", "4", "8", "x * y"),
            ("String Processing & Formatting", "Text parsing, reversal, searching, and formatting.", "string+manipulation", "reverse_text", "s", "'polaris'", "siralop", "s[::-1]"),
            ("List Comprehensions & Filtering", "Clean list comprehensions and filtering logic.", "list+comprehension", "filter_evens", "nums", "[1, 2, 3, 4, 5, 6]", "[2, 4, 6]", "[x for x in nums if x % 2 == 0]"),
            ("Error Handling & Exceptions", "Try, except, finally, and robust defensive code.", "error+handling+exceptions", "safe_divide", "a, b", "10, 2", "5.0", "a / b if b != 0 else None"),
            ("File Operations & Data Processing", "Reading and writing files, structured line parsing.", "file+handling", "word_count", "text", "'hello world polaris'", "3", "len(text.split())"),
            ("Object-Oriented Programming (OOP)", "Classes, objects, attributes, and methods.", "object+oriented+programming", "get_area", "w, h", "4, 5", "20", "w * h"),
            ("Standard Library & Utilities", "Using math, random, datetime, and collections.", "standard+library+modules", "max_diff", "nums", "[10, 2, 8, 1]", "9", "max(nums) - min(nums)"),
            ("Real-World Projects & Algorithms", "Designing scalable utilities and algorithms.", "algorithms+projects", "find_primes", "limit", "10", "[2, 3, 5, 7]", "[x for x in range(2, limit + 1) if all(x % d != 0 for d in range(2, int(x**0.5) + 1))]"),
        ]

        num_chapters = max(3, min(duration_days, 14))
        generated_chapters = []

        for i in range(num_chapters):
            tpl = curriculum_templates[i % len(curriculum_templates)]
            title, desc, query, fn_name, fn_args, sample_in, sample_out, fn_body = tpl
            
            # Generate 5 test cases: 3 sample (is_hidden: False) + 2 hidden (is_hidden: True)
            if fn_name == "greet":
                tcases = [
                    {"input": "'Developer'", "expected_output": "Hello, Developer!", "is_hidden": False},
                    {"input": "'Python'", "expected_output": "Hello, Python!", "is_hidden": False},
                    {"input": "'Polaris'", "expected_output": "Hello, Polaris!", "is_hidden": False},
                    {"input": "'World'", "expected_output": "Hello, World!", "is_hidden": True},
                    {"input": "''", "expected_output": "Hello, !", "is_hidden": True}
                ]
            elif fn_name == "squares":
                tcases = [
                    {"input": "[1, 2, 3, 4, 5]", "expected_output": "[1, 4, 9, 16, 25]", "is_hidden": False},
                    {"input": "[0, 3, 10]", "expected_output": "[0, 9, 100]", "is_hidden": False},
                    {"input": "[]", "expected_output": "[]", "is_hidden": False},
                    {"input": "[-2, -4, 6]", "expected_output": "[4, 16, 36]", "is_hidden": True},
                    {"input": "[7]", "expected_output": "[49]", "is_hidden": True}
                ]
            else:
                tcases = [
                    {"input": sample_in, "expected_output": sample_out, "is_hidden": False},
                    {"input": sample_in, "expected_output": sample_out, "is_hidden": False},
                    {"input": sample_in, "expected_output": sample_out, "is_hidden": False},
                    {"input": sample_in, "expected_output": sample_out, "is_hidden": True},
                    {"input": sample_in, "expected_output": sample_out, "is_hidden": True}
                ]

            generated_chapters.append({
                "chapter_number": i + 1,
                "title": f"Day {i + 1}: {title}",
                "description": desc,
                "youtube_search_query": f"{goal.replace(' ', '+')}+{query}",
                "duration_estimate": "30 min",
                "key_topics": [title, "Practice", "Coding"],
                "keyword_importance": {"basics": 90, "practice": 80},
                "coding_tasks": [
                    {
                        "task_id": f"ch{i + 1}_task1",
                        "title": f"Exercise: {title}",
                        "description": f"Write a function `{fn_name}({fn_args})` that returns the computed result.",
                        "difficulty": "easy" if i < 5 else ("medium" if i < 10 else "hard"),
                        "language": "python",
                        "starter_code": f"def {fn_name}({fn_args}):\n    # Return the computed result\n    pass",
                        "test_cases": tcases,
                        "hints": [f"Return value directly using {fn_body}", "Ensure you return instead of print"],
                        "solution": f"def {fn_name}({fn_args}):\n    return {fn_body}"
                    }
                ]
            })

        plan = {
            "title": goal,
            "overview": f"A comprehensive {duration_days}-day learning roadmap for {goal}.",
            "daily_schedule": [
                {"day": i + 1, "chapters": [i + 1], "estimated_time_minutes": 45, "topics_focus": generated_chapters[i]["title"]}
                for i in range(num_chapters)
            ],
            "chapters": generated_chapters,
            "quiz": [
                {
                    "question": f"What is the key focus of studying {goal} over {duration_days} days?",
                    "options": ["Hands-on progressive mastery every day", "Only reading theory without writing code", "Skipping fundamentals", "None of the above"],
                    "correct_answer": 0,
                    "explanation": f"Daily progressive coding challenges ensure deep mastery of {goal}."
                }
            ]
        }

    return plan


# ═══════════════════════════════════════════════════════════
#  AI CHAPTER SUMMARY (auto-generated on completion)
# ═══════════════════════════════════════════════════════════

def generate_chapter_summary(chapter_title: str, youtube_title: str, description: str = "") -> str:
    """
    Generate a concise AI summary of what was learned in a completed chapter.
    Called automatically after chapter completion.
    Returns markdown bullet list.
    """
    video_info = youtube_title or chapter_title
    desc_section = f"\nVideo description hint: {description[:400]}" if description else ""

    system_prompt = """You are a concise academic summarizer.
Given a chapter topic and a video title, generate a short bullet-point summary of what the learner covered.
CRITICAL: Respond with ONLY the bullet points. No intro, no header, no markdown formatting outside of bullets."""

    user_prompt = f"""Chapter: {chapter_title}
Video watched: {video_info}{desc_section}

Generate 4-6 concise bullet points summarizing the key concepts covered in this chapter.
Format: each line starts with •
Example:
• Understood how X works
• Practiced Y technique
• Learned to implement Z"""

    response = _call_llm([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ], temperature=0.4, max_tokens=400)

    # Clean up response
    lines = [l.strip() for l in response.strip().split('\n') if l.strip()]
    # Ensure bullets
    bullets = []
    for line in lines:
        if line.startswith('•') or line.startswith('-') or line.startswith('*'):
            bullets.append(line.lstrip('-*•').strip())
        elif line and not line.startswith('#'):
            bullets.append(line)
    return '\n'.join(f'• {b}' if not b.startswith('•') else b for b in bullets[:6])


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

def classify_desktop_app(app_name: str, window_title: str) -> str:
    """
    Use NLP to classify an unknown desktop application.
    Returns: 'productive', 'distracting', or 'neutral'.
    """
    system_prompt = """You are a productivity classification AI for a desktop tracker.
Classify the given desktop application and window title into exactly one of these categories:
- productive: Software development tools, IDEs, databases, study materials, office suites.
- distracting: Games, social media, entertainment, streaming.
- neutral: Utilities, generic tools, settings, browsers (if not clearly productive/distracting).

Respond with ONLY the exact word: productive, distracting, or neutral. No other text."""

    user_prompt = f"App Name: {app_name}\nWindow Title: {window_title}"
    
    try:
        answer = _call_llm([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ], temperature=0.1, max_tokens=10)
        
        result = answer.strip().lower()
        if result in ("productive", "distracting", "neutral"):
            return result
        return "neutral"
    except Exception:
        return "neutral"
