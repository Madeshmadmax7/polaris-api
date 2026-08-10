"""
Polaris Lab — Code Execution & Verification API Routes
Provides sandboxed code execution, coding task management, and
automated code verification for the interactive Lab IDE.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List

from sqlalchemy.orm import Session
from app.config.database import get_db
from app.utils.auth import get_current_user
from app.services.sandbox_service import execute_code
from app.services.code_verifier import verify_code, VerificationResult
from app.models.models import User, CodingTask, CodingSubmission

router = APIRouter(prefix="/api/lab", tags=["Lab IDE"])


# ── Request / Response Schemas ───────────────────────────────

class CodeExecutionRequest(BaseModel):
    """Request to execute code in the sandbox."""
    code: str = Field(..., min_length=1, max_length=50000, description="Source code to execute")
    language: str = Field(default="python", description="Programming language: python | javascript")
    chapter_id: Optional[str] = Field(default=None, description="Associated chapter ID for context")


class CodeExecutionResponse(BaseModel):
    """Response from code execution."""
    stdout: str
    stderr: str
    exit_code: int
    execution_time_ms: float
    timed_out: bool
    language: str


class CodeSubmitRequest(BaseModel):
    """Request to submit code for verification."""
    task_id: str = Field(..., description="Coding task ID to submit against")
    code: str = Field(..., min_length=1, max_length=50000, description="Source code to verify")
    language: str = Field(default="python", description="Programming language")


class TestCaseResultSchema(BaseModel):
    input_data: str = ""
    expected: str = ""
    actual: str = ""
    passed: bool = False


class VerificationResponse(BaseModel):
    """Response from code verification."""
    submission_id: str
    passed: bool
    score: int
    total_tests: int
    passed_tests: int
    failed_tests: list = []
    feedback: str
    ai_review: Optional[str] = None
    execution_time_ms: float


class CodingTaskResponse(BaseModel):
    """Coding task details for the frontend."""
    id: str
    chapter_number: int
    task_index: int
    title: str
    description: str
    difficulty: str
    language: str
    starter_code: str
    hints: list = []
    # Don't expose solution or test case expected outputs
    test_count: int = 0
    best_score: Optional[int] = None
    solved: bool = False

    class Config:
        from_attributes = True


class SubmissionHistoryItem(BaseModel):
    id: str
    passed: bool
    score: int
    total_tests: int
    passed_tests: int
    feedback: str
    created_at: str

    class Config:
        from_attributes = True


# ── Endpoints ────────────────────────────────────────────────

@router.post("/execute", response_model=CodeExecutionResponse)
async def run_code(
    payload: CodeExecutionRequest,
    user=Depends(get_current_user),
):
    """
    Execute user-submitted code in a sandboxed environment.
    
    - **Python**: Runs in an isolated subprocess with 10s timeout, no network/file access.
    - **JavaScript**: Should be executed client-side in the browser. Returns an error if called here.
    """
    result = await execute_code(payload.code, payload.language)

    return CodeExecutionResponse(
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.exit_code,
        execution_time_ms=result.execution_time_ms,
        timed_out=result.timed_out,
        language=result.language,
    )


@router.get("/tasks/{plan_id}/{chapter_num}", response_model=List[CodingTaskResponse])
async def get_chapter_tasks(
    plan_id: str,
    chapter_num: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get all coding tasks for a specific study plan chapter."""
    tasks = db.query(CodingTask).filter(
        CodingTask.study_plan_id == plan_id,
        CodingTask.chapter_number == chapter_num,
    ).order_by(CodingTask.task_index).all()

    result = []
    for task in tasks:
        # Check if user has solved this task
        best_submission = db.query(CodingSubmission).filter(
            CodingSubmission.task_id == task.id,
            CodingSubmission.user_id == user.id,
        ).order_by(CodingSubmission.score.desc()).first()

        result.append(CodingTaskResponse(
            id=task.id,
            chapter_number=task.chapter_number,
            task_index=task.task_index,
            title=task.title,
            description=task.description,
            difficulty=task.difficulty,
            language=task.language,
            starter_code=task.starter_code,
            hints=task.hints or [],
            test_count=len(task.test_cases or []),
            best_score=best_submission.score if best_submission else None,
            solved=best_submission.passed if best_submission else False,
        ))

    return result


@router.post("/submit", response_model=VerificationResponse)
async def submit_code(
    payload: CodeSubmitRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Submit code for automated verification against a coding task.
    Runs test cases and optionally AI review. Returns detailed results.
    """
    task = db.query(CodingTask).filter(CodingTask.id == payload.task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Coding task not found")

    # Run verification
    vr = await verify_code(
        user_code=payload.code,
        test_cases=task.test_cases or [],
        task_description=task.description,
        solution=task.solution or "",
        language=payload.language or task.language,
    )

    # Persist submission
    submission = CodingSubmission(
        user_id=user.id,
        task_id=task.id,
        code=payload.code,
        language=payload.language or task.language,
        passed=vr.passed,
        score=vr.score,
        total_tests=vr.total_tests,
        passed_tests=vr.passed_tests,
        test_results=vr.failed_tests,
        ai_feedback=vr.ai_review or "",
        execution_time_ms=vr.execution_time_ms,
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)

    return VerificationResponse(
        submission_id=submission.id,
        passed=vr.passed,
        score=vr.score,
        total_tests=vr.total_tests,
        passed_tests=vr.passed_tests,
        failed_tests=vr.failed_tests,
        feedback=vr.feedback,
        ai_review=vr.ai_review,
        execution_time_ms=vr.execution_time_ms,
    )


@router.get("/submissions/{task_id}", response_model=List[SubmissionHistoryItem])
async def get_submissions(
    task_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get submission history for a specific coding task."""
    submissions = db.query(CodingSubmission).filter(
        CodingSubmission.task_id == task_id,
        CodingSubmission.user_id == user.id,
    ).order_by(CodingSubmission.created_at.desc()).limit(20).all()

    return [
        SubmissionHistoryItem(
            id=s.id,
            passed=s.passed,
            score=s.score,
            total_tests=s.total_tests,
            passed_tests=s.passed_tests,
            feedback=s.ai_feedback or "",
            created_at=s.created_at.isoformat() if s.created_at else "",
        )
        for s in submissions
    ]


@router.get("/languages")
async def get_supported_languages(user=Depends(get_current_user)):
    """Return list of supported languages and their execution modes."""
    return {
        "languages": [
            {
                "id": "python",
                "name": "Python 3",
                "mode": "server",
                "description": "Executed on the server in a sandboxed subprocess",
                "file_extension": ".py",
                "default_code": 'print("Hello, Polaris!")\n\n# Write your Python code here\nfor i in range(5):\n    print(f"  Step {i+1}")\n',
            },
            {
                "id": "javascript",
                "name": "JavaScript",
                "mode": "browser",
                "description": "Executed directly in the browser (no server needed)",
                "file_extension": ".js",
                "default_code": 'console.log("Hello, Polaris!");\n\n// Write your JavaScript code here\nfor (let i = 0; i < 5; i++) {\n  console.log(`  Step ${i + 1}`);\n}\n',
            },
            {
                "id": "html",
                "name": "HTML / CSS",
                "mode": "preview",
                "description": "Live preview rendered in an embedded iframe",
                "file_extension": ".html",
                "default_code": '<!DOCTYPE html>\n<html>\n<head>\n  <style>\n    body { background: #000; color: #fff; font-family: sans-serif; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }\n    h1 { font-size: 2rem; letter-spacing: 0.3em; text-transform: uppercase; }\n  </style>\n</head>\n<body>\n  <h1>Polaris Lab</h1>\n</body>\n</html>\n',
            },
        ]
    }
