"""Background jobs: one bash script per job, log captured line by line,
stage progress reported by the flow's own stage detector."""
from __future__ import annotations

import json
import math
import os
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import tool_env

MAX_PARALLEL = 2
_sem = threading.Semaphore(MAX_PARALLEL)
_jobs: dict[str, "Job"] = {}
_lock = threading.Lock()


@dataclass
class Job:
    id: str
    project: str
    kind: str
    title: str
    script: str
    cwd: Path
    run_dir: Path
    stages: list[str] = field(default_factory=list)
    stage_of: Callable[[str], str | None] | None = None
    finish: Callable[["Job"], dict] | None = None
    status: str = "queued"  # queued running ok failed cancelled
    rc: int | None = None
    started: float | None = None
    ended: float | None = None
    current: str | None = None
    done_stages: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    result: dict = field(default_factory=dict)
    proc: subprocess.Popen | None = None

    def info(self) -> dict:
        return {
            "id": self.id, "project": self.project, "kind": self.kind,
            "title": self.title, "status": self.status, "rc": self.rc,
            "started": self.started, "ended": self.ended,
            "stages": self.stages, "current": self.current,
            "done_stages": self.done_stages, "nlines": len(self.lines),
            "run_dir": str(self.run_dir), "result": self.result,
        }

    def _set_stage(self, st: str) -> None:
        if st == self.current:
            return
        if self.current and self.current not in self.done_stages:
            self.done_stages.append(self.current)
        self.current = st

    def _run(self) -> None:
        with _sem:
            if self.status == "cancelled":
                return
            self.status, self.started = "running", time.time()
            self.run_dir.mkdir(parents=True, exist_ok=True)
            (self.run_dir / "job.sh").write_text(self.script)
            log = open(self.run_dir / "log.txt", "w", buffering=1)
            try:
                self.proc = subprocess.Popen(
                    ["bash", str(self.run_dir / "job.sh")], cwd=self.cwd,
                    env=tool_env(), stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, errors="replace",
                    start_new_session=True)
                assert self.proc.stdout
                for line in self.proc.stdout:
                    line = line.rstrip("\n")
                    self.lines.append(line)
                    log.write(line + "\n")
                    if self.stage_of:
                        st = self.stage_of(line)
                        if st:
                            self._set_stage(st)
                self.rc = self.proc.wait()
            except Exception as e:  # noqa: BLE001
                self.lines.append(f"[kahroba] job error: {e}")
                self.rc = -1
            finally:
                log.close()
            if self.status != "cancelled":
                self.status = "ok" if self.rc == 0 else "failed"
            if self.status == "ok" and self.current:
                self._set_stage("__end__")
                self.current = None
            if self.finish:
                try:
                    self.result = clean(self.finish(self) or {})
                except Exception as e:  # noqa: BLE001
                    self.result = {"error": f"report parse failed: {e}"}
                    self.lines.append(f"[kahroba] {self.result['error']}")
            self.ended = time.time()
            (self.run_dir / "job.json").write_text(json.dumps(self.info(), indent=1))


def clean(x):
    """JSON-safe copy: non-finite floats (LibreLane reports inf slack) -> None."""
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    return x


def submit(job: Job) -> Job:
    with _lock:
        _jobs[job.id] = job
    threading.Thread(target=job._run, daemon=True).start()
    return job


def new_id(kind: str) -> str:
    return time.strftime("%Y%m%d-%H%M%S-") + kind + "-" + uuid.uuid4().hex[:4]


def get(jid: str) -> Job | None:
    return _jobs.get(jid)


def all_jobs(project: str | None = None) -> list[dict]:
    js = [j.info() for j in _jobs.values() if project in (None, j.project)]
    return sorted(js, key=lambda j: j["id"], reverse=True)


def cancel(jid: str) -> bool:
    j = _jobs.get(jid)
    if not j or j.status not in ("queued", "running"):
        return False
    j.status = "cancelled"
    if j.proc and j.proc.poll() is None:
        try:
            os.killpg(j.proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    return True
