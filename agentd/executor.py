import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

RUNS = Path("/srv/agentos/runs")
MAX_REPLY_BYTES = 65_536


def _save_stream(stream, path):
    stream.flush()
    stream.seek(0)
    with path.open("wb") as target:
        shutil.copyfileobj(stream, target)


def _read_tail(stream):
    stream.flush()
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(max(0, size - MAX_REPLY_BYTES))
    return stream.read().decode("utf-8", errors="replace")


def execute(argv, timeout, cwd=None):
    if not isinstance(argv, list) or not argv:
        raise ValueError("argv must be a non-empty string array")
    if not all(isinstance(item, str) and item for item in argv):
        raise ValueError("argv must be a non-empty string array")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("timeout must be between 1 and 86400 seconds")
    if not 1 <= timeout <= 86400:
        raise ValueError("timeout must be between 1 and 86400 seconds")

    working_directory = cwd or "/srv/agentos"
    if not isinstance(working_directory, str) or not Path(working_directory).is_dir():
        raise ValueError("cwd must be an existing directory")

    run_id = uuid.uuid4().hex
    RUNS.mkdir(parents=True, exist_ok=True)
    stdout_log = RUNS / f"{run_id}.stdout"
    stderr_log = RUNS / f"{run_id}.stderr"
    started = time.monotonic()

    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(
            argv,
            cwd=working_directory,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        timed_out = False
        try:
            exit_code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            exit_code = process.wait()

        _save_stream(stdout, stdout_log)
        _save_stream(stderr, stderr_log)
        result = {
            "run_id": run_id,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout": _read_tail(stdout),
            "stderr": _read_tail(stderr),
            "stdout_log": str(stdout_log),
            "stderr_log": str(stderr_log),
        }

    metadata = {
        "run_id": run_id,
        "argv": argv,
        "cwd": working_directory,
        "timeout": timeout,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "duration_seconds": result["duration_seconds"],
    }
    (RUNS / f"{run_id}.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result
