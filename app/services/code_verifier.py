"""
Polaris Lab — Code Verification Service
Two-tier verification:
  Tier 1: Test-case runner — deterministic I/O comparison
  Tier 2: AI code review  — fallback for complex / non-I/O tasks
"""

import asyncio
import json
import logging
import subprocess
import sys
import tempfile
import os
import re
from dataclasses import dataclass, field, asdict
from typing import List, Optional

logger = logging.getLogger(__name__)


# ── Result Types ─────────────────────────────────────────────

@dataclass
class TestCaseResult:
    input_data: str
    expected: str
    actual: str
    passed: bool
    is_hidden: bool = False


@dataclass
class VerificationResult:
    passed: bool = False
    total_tests: int = 0
    passed_tests: int = 0
    test_results: list = field(default_factory=list)  # List[TestCaseResult as dict]
    failed_tests: list = field(default_factory=list)  # List[TestCaseResult as dict]
    score: int = 0  # 0-100
    feedback: str = ""
    ai_review: Optional[str] = None
    execution_time_ms: float = 0.0

    def to_dict(self):
        return asdict(self)


# ── Tier 1: Test Case Runner ────────────────────────────────

def _exec_python_sync(tmp_path: str, input_data: str, timeout: int) -> tuple:
    """Synchronous subprocess execution for Python."""
    try:
        res = subprocess.run(
            [sys.executable, '-u', tmp_path],
            input=input_data if input_data else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding='utf-8',
            errors='replace',
        )
        return (res.stdout.strip(), res.stderr.strip(), res.returncode, False)
    except subprocess.TimeoutExpired:
        return ('', 'Execution timed out (infinite loop or taking >10s)', -1, True)
    except Exception as e:
        return ('', str(e), 1, False)


async def _run_python_with_input(code: str, input_data: str, timeout: int = 10) -> tuple:
    """Run Python code with given input safely across all OS platforms."""
    with tempfile.NamedTemporaryFile(
        mode='w', suffix='.py', delete=False, encoding='utf-8'
    ) as f:
        f.write(code)
        f.flush()
        tmp_path = f.name

    try:
        return await asyncio.to_thread(_exec_python_sync, tmp_path, input_data, timeout)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _exec_javascript_sync(tmp_path: str, input_data: str, timeout: int) -> tuple:
    """Synchronous subprocess execution for Node.js."""
    try:
        res = subprocess.run(
            ['node', tmp_path],
            input=input_data if input_data else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding='utf-8',
            errors='replace',
        )
        return (res.stdout.strip(), res.stderr.strip(), res.returncode, False)
    except subprocess.TimeoutExpired:
        return ('', 'Execution timed out', -1, True)
    except Exception as e:
        return ('', str(e), 1, False)


async def _run_javascript_with_input(code: str, input_data: str, timeout: int = 10) -> tuple:
    """Run JavaScript code with Node.js safely across all OS platforms."""
    with tempfile.NamedTemporaryFile(
        mode='w', suffix='.js', delete=False, encoding='utf-8'
    ) as f:
        f.write(code)
        f.flush()
        tmp_path = f.name

    try:
        return await asyncio.to_thread(_exec_javascript_sync, tmp_path, input_data, timeout)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _build_test_harness(user_code: str, test_case: dict, language: str) -> str:
    """
    Build a test harness that invokes the user's function with dynamic test inputs
    and captures the return value cleanly without interference from user print statements.
    """
    input_val = test_case.get("input", "")

    if language == "python":
        # Find function definition
        func_match = re.search(r'def\s+(\w+)\s*\(', user_code)
        if func_match:
            func_name = func_match.group(1)
            args = input_val
            harness = f"""{user_code}

# === Auto-test harness ===
try:
    import json
    __result = {func_name}({args})
    print("__POLARIS_RESULT_START__")
    if isinstance(__result, (dict, list, bool, int, float, str)):
        print(json.dumps(__result) if not isinstance(__result, str) else __result)
    else:
        print(__result)
    print("__POLARIS_RESULT_END__")
except Exception as __e:
    print("__POLARIS_ERROR_START__")
    print(f"Error calling {func_name}({args}): {{__e}}")
    print("__POLARIS_ERROR_END__")
"""
            return harness
        else:
            return user_code

    elif language == "javascript":
        func_match = re.search(r'function\s+(\w+)\s*\(', user_code)
        if not func_match:
            func_match = re.search(r'(?:const|let|var)\s+(\w+)\s*=\s*(?:\(|function)', user_code)
        if func_match:
            func_name = func_match.group(1)
            args = input_val
            harness = f"""{user_code}

// === Auto-test harness ===
try {{
    const __result = {func_name}({args});
    console.log("__POLARIS_RESULT_START__");
    console.log(typeof __result === 'object' ? JSON.stringify(__result) : String(__result));
    console.log("__POLARIS_RESULT_END__");
}} catch (__e) {{
    console.log("__POLARIS_ERROR_START__");
    console.log("Error: " + __e.message);
    console.log("__POLARIS_ERROR_END__");
}}
"""
            return harness
        else:
            return user_code

    return user_code


async def verify_with_test_cases(
    user_code: str,
    test_cases: list,
    language: str = "python",
) -> VerificationResult:
    """
    Run user code against each dynamic test case and compare return values.
    Returns a VerificationResult with pass/fail per test and diagnostic hints.
    """
    import time
    start = time.perf_counter()

    results = []
    runner = _run_python_with_input if language == "python" else _run_javascript_with_input

    returned_none_count = 0

    for idx, tc in enumerate(test_cases):
        input_data = str(tc.get("input", ""))
        expected = str(tc.get("expected_output", "")).strip()
        is_hidden = bool(tc.get("is_hidden", False) or idx >= 3)

        # Build harness code with isolated result extraction
        harness_code = _build_test_harness(user_code, tc, language)

        # Run
        stdout, stderr, exit_code, timed_out = await runner(harness_code, input_data)

        if timed_out:
            actual = "[TIMEOUT]"
            passed = False
        elif exit_code != 0 and "__POLARIS_RESULT_START__" not in stdout and "__POLARIS_ERROR_START__" not in stdout:
            actual = stderr or "[SYNTAX / RUNTIME ERROR]"
            passed = False
        else:
            # Extract result from delimiters if present
            if "__POLARIS_RESULT_START__" in stdout:
                actual = stdout.split("__POLARIS_RESULT_START__")[1].split("__POLARIS_RESULT_END__")[0].strip()
            elif "__POLARIS_ERROR_START__" in stdout:
                actual = stdout.split("__POLARIS_ERROR_START__")[1].split("__POLARIS_ERROR_END__")[0].strip()
            else:
                actual = stdout.strip()

            if actual == "None" and expected != "None":
                returned_none_count += 1

            passed = _compare_output(actual, expected)

        results.append(TestCaseResult(
            input_data=input_data if not is_hidden else "[Hidden Case]",
            expected=expected if not is_hidden else "[Hidden Case]",
            actual=actual if not is_hidden else ("[Passed]" if passed else "[Failed]"),
            passed=passed,
            is_hidden=is_hidden,
        ))

    elapsed = (time.perf_counter() - start) * 1000
    total = len(results)
    passed_count = sum(1 for r in results if r.passed)

    if total == 0:
        score = 0
    elif passed_count == total:
        score = 100
    else:
        score = int((passed_count / total) * 80)

    all_passed = passed_count == total

    feedback_parts = []
    if all_passed:
        feedback_parts.append(f"✅ All {total} test cases passed successfully! (3 Sample Cases + 2 Hidden Cases)")
    else:
        feedback_parts.append(f"❌ {total - passed_count}/{total} test cases failed.")
        for i, r in enumerate(results):
            status = "✓ Passed" if r.passed else "✗ Failed"
            if r.is_hidden:
                feedback_parts.append(f"  Test {i+1} [Hidden Case]: {status}")
            else:
                feedback_parts.append(
                    f"  Test {i+1} [{status}]: Input ({r.input_data}) → Expected: {r.expected} | Got: {r.actual}"
                )

        if returned_none_count > 0:
            feedback_parts.append(
                "\n💡 Hint: Your function returned 'None'. Make sure to 'return' the result instead of using 'print()' inside your function."
            )

    return VerificationResult(
        passed=all_passed,
        total_tests=total,
        passed_tests=passed_count,
        test_results=[asdict(r) for r in results],
        failed_tests=[asdict(r) for r in results if not r.passed],
        score=score,
        feedback="\n".join(feedback_parts),
        execution_time_ms=round(elapsed, 2),
    )


def _compare_output(actual: str, expected: str) -> bool:
    """Flexible comparison for test case outputs."""
    if actual == expected:
        return True
    # Strip and normalize whitespace
    a = actual.strip().lower()
    e = expected.strip().lower()
    if a == e:
        return True
    # Handle Python bool repr: True vs true
    if a in ('true', 'false') and e in ('true', 'false'):
        return a == e
    # Handle numeric comparison: "55" == "55.0"
    try:
        return float(a) == float(e)
    except (ValueError, TypeError):
        pass
    # Handle list/array output: [1, 2, 3] == [1,2,3]
    try:
        parsed_a = json.loads(a)
        parsed_e = json.loads(e)
        return parsed_a == parsed_e
    except (json.JSONDecodeError, TypeError):
        pass
    return False


# ── Tier 2: AI Code Review ──────────────────────────────────

async def ai_code_review(
    user_code: str,
    task_description: str,
    solution: str = "",
    language: str = "python",
) -> dict:
    """
    Use AI to evaluate code quality when test cases aren't applicable.
    Returns {"score": 0-100, "feedback": str, "improvements": [str]}
    """
    from app.services.ai_service import _call_llm

    prompt = f"""You are a code reviewer. Evaluate the student's code submission.

TASK: {task_description}
LANGUAGE: {language}
{"REFERENCE SOLUTION:" + chr(10) + solution if solution else ""}

STUDENT CODE:
```{language}
{user_code}
```

Evaluate on:
1. Correctness (does it solve the problem?)
2. Code quality (readability, naming, structure)
3. Edge cases (does it handle edge cases?)

Respond with ONLY this JSON:
{{
    "score": <0-100>,
    "feedback": "Short summary of the evaluation",
    "improvements": ["suggestion 1", "suggestion 2"]
}}"""

    try:
        response = _call_llm(
            [{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=500,
        )
        # Parse response
        clean = response.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        clean = clean.strip()
        result = json.loads(clean)
        return {
            "score": min(100, max(0, int(result.get("score", 50)))),
            "feedback": result.get("feedback", "Code reviewed by AI."),
            "improvements": result.get("improvements", []),
        }
    except Exception as e:
        logger.error(f"AI code review failed: {e}")
        return {
            "score": 50,
            "feedback": "AI review unavailable. Code executed successfully.",
            "improvements": [],
        }


# ── Main verify_code entrypoint ─────────────────────────────

async def verify_code(
    user_code: str,
    test_cases: list,
    task_description: str = "",
    solution: str = "",
    language: str = "python",
) -> VerificationResult:
    """
    Main verification entrypoint.
    Tier 1: If test_cases exist → run test case verification
    Tier 2: If no test_cases → use AI review as fallback
    """
    if test_cases and len(test_cases) > 0:
        result = await verify_with_test_cases(user_code, test_cases, language)

        # Optional: Add AI review for partial failures
        if not result.passed and result.score < 50:
            try:
                ai_result = await ai_code_review(user_code, task_description, solution, language)
                result.ai_review = ai_result.get("feedback", "")
                # Blend score: 70% test cases + 30% AI
                result.score = int(result.score * 0.7 + ai_result.get("score", 0) * 0.3)
            except Exception:
                pass  # AI is optional

        return result
    else:
        # No test cases → pure AI review
        ai_result = await ai_code_review(user_code, task_description, solution, language)
        return VerificationResult(
            passed=ai_result.get("score", 0) >= 70,
            total_tests=0,
            passed_tests=0,
            score=ai_result.get("score", 0),
            feedback=ai_result.get("feedback", ""),
            ai_review="\n".join(ai_result.get("improvements", [])),
        )
