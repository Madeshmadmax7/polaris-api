"""
Polaris Lab — Sandboxed Code Execution Service
Runs user-submitted code in isolated subprocesses with strict limits.
Supports Python and JavaScript.
"""

import asyncio
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class ExecutionResult:
    """Result of a sandboxed code execution."""
    stdout: str
    stderr: str
    exit_code: int
    execution_time_ms: float
    timed_out: bool = False
    language: str = "python"


# ── Safety limits ────────────────────────────────────────────
MAX_TIMEOUT_SECONDS = 10        # Hard kill after 10s
MAX_OUTPUT_BYTES = 50_000       # 50KB output cap
MAX_CODE_LENGTH = 50_000        # 50KB code cap

# Dangerous imports/calls to block in Python
PYTHON_BLACKLIST = [
    "import os", "import sys", "import subprocess", "import shutil",
    "import socket", "import http", "import urllib", "import requests",
    "import ctypes", "import signal", "__import__", "exec(", "eval(",
    "open(", "compile(", "globals(", "locals(", "breakpoint(",
    "import pathlib", "import io",
]


def _sanitize_python(code: str) -> Optional[str]:
    """
    Basic static analysis to reject obviously dangerous Python code.
    Returns an error message if blocked, or None if safe.
    """
    code_lower = code.lower().strip()
    for pattern in PYTHON_BLACKLIST:
        if pattern.lower() in code_lower:
            return f"Blocked: '{pattern}' is not allowed in the sandbox for security reasons."
    return None


def _run_python_sync(tmp_file_path: str):
    """
    Synchronous subprocess runner — runs in an asyncio thread executor.
    Avoids Windows SelectorEventLoop NotImplementedError with asyncio subprocesses.
    """
    start = time.monotonic()
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.run(
        [sys.executable, "-u", tmp_file_path],
        capture_output=True,
        timeout=MAX_TIMEOUT_SECONDS,
        env=env,
    )
    elapsed_ms = (time.monotonic() - start) * 1000
    return proc.stdout, proc.stderr, proc.returncode, round(elapsed_ms, 2)


async def execute_python(code: str) -> ExecutionResult:
    """
    Execute Python code in an isolated subprocess with timeout and output limits.
    """
    if len(code) > MAX_CODE_LENGTH:
        return ExecutionResult(
            stdout="", stderr=f"Code too long ({len(code)} chars). Max: {MAX_CODE_LENGTH}.",
            exit_code=1, execution_time_ms=0, language="python"
        )

    # Static safety check
    block_reason = _sanitize_python(code)
    if block_reason:
        return ExecutionResult(
            stdout="", stderr=block_reason,
            exit_code=1, execution_time_ms=0, language="python"
        )

    # Write code to a temp file
    tmp_file = None
    try:
        tmp_file = tempfile.NamedTemporaryFile(
            mode='w', suffix='.py', delete=False, encoding='utf-8'
        )
        tmp_file.write(code)
        tmp_file.close()

        timed_out = False
        try:
            stdout_bytes, stderr_bytes, exit_code, elapsed_ms = await asyncio.to_thread(
                _run_python_sync, tmp_file.name
            )
        except subprocess.TimeoutExpired:
            timed_out = True
            stdout_bytes = b""
            stderr_bytes = f"Execution timed out after {MAX_TIMEOUT_SECONDS} seconds.".encode()
            exit_code = 1
            elapsed_ms = MAX_TIMEOUT_SECONDS * 1000

        stdout = stdout_bytes[:MAX_OUTPUT_BYTES].decode('utf-8', errors='replace')
        stderr = stderr_bytes[:MAX_OUTPUT_BYTES].decode('utf-8', errors='replace')

        return ExecutionResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            execution_time_ms=elapsed_ms,
            timed_out=timed_out,
            language="python"
        )

    except Exception as e:
        import traceback
        err_msg = str(e) or repr(e)
        print(f"[Lab Sandbox Error] {type(e).__name__}: {err_msg}\n{traceback.format_exc()}")
        return ExecutionResult(
            stdout="", stderr=f"Sandbox error ({type(e).__name__}): {err_msg}",
            exit_code=1, execution_time_ms=0, language="python"
        )
    finally:
        if tmp_file and os.path.exists(tmp_file.name):
            try:
                os.unlink(tmp_file.name)
            except OSError:
                pass


async def execute_code(code: str, language: str) -> ExecutionResult:
    """
    Route code execution to the appropriate sandbox based on language.
    JavaScript is executed client-side (browser), so this only handles Python.
    """
    language = language.lower().strip()

    if language in ("python", "py", "python3"):
        return await execute_python(code)
    elif language in ("javascript", "js", "node"):
        return ExecutionResult(
            stdout="",
            stderr="JavaScript execution is handled client-side in the browser. Use the in-browser runner.",
            exit_code=1,
            execution_time_ms=0,
            language="javascript"
        )
    else:
        return ExecutionResult(
            stdout="",
            stderr=f"Unsupported language: '{language}'. Supported: python, javascript.",
            exit_code=1,
            execution_time_ms=0,
            language=language
        )
