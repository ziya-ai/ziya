"""``JobManager``: launch, re-attach, wait on, kill and record managed jobs.

design/job-runner.md.  Lives in the Ziya server process; holds no pipes to
any job.  Everything it knows about a running job it reads from the job
directory ``<project>/.ziya/jobs/<job_id>/`` that ``ziya-jobwrap`` writes:

  job.json       owner, command, cwd, declared outputs, limits, the policy
                 decision, wrapper pid + its process start time
  journal.jsonl  output records (shadow journal format)
  status.json    written by the wrapper at spawn (pgid) and at exit

Security: a job is admitted only by ``app.jobs.policy.check`` (the shell
tool's own allowlist + write checker + task grants, all or nothing, with
background operators refused on top).  The child env is the shell tool's
credential-stripped env.  Killing always targets the process GROUP.  The
job directory is in ``SERVER_ONLY_PATHS``, so tools cannot edit a record.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.utils.logging_utils import logger

DEFAULT_MAX_RUNTIME_S = 6 * 3600
UNGRANTED_MAX_RUNTIME_S = 24 * 3600
MAX_JOBS_PER_OWNER = 8
MAX_JOBS_PER_USER = 32
KILL_GRACE_S = 10.0
WAIT_CAP_S = 3600


class JobError(Exception):
    def __init__(self, code: str, msg: str):
        super().__init__(msg)
        self.code = code


def _jobs_root(project_root: str) -> Path:
    return Path(project_root) / ".ziya" / "jobs"


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _pid_start_time(pid: int) -> Optional[float]:
    """Process start time, the pid-reuse guard for re-attach."""
    try:
        import psutil
        return float(psutil.Process(pid).create_time())
    except Exception:  # noqa: BLE001 -- gone, or no permission
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _killpg(pgid: int, sig: int) -> bool:
    try:
        os.killpg(pgid, sig)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Job:
    """In-memory view of one job directory.  All durable state is on disk."""

    def __init__(self, job_dir: Path, meta: Dict[str, Any]):
        self.dir = job_dir
        self.meta = meta

    @property
    def id(self) -> str:
        return self.meta["job_id"]

    @property
    def journal_path(self) -> Path:
        return self.dir / "journal.jsonl"

    def status_file(self) -> Optional[Dict[str, Any]]:
        return _read_json(self.dir / "status.json")

    def state(self) -> Dict[str, Any]:
        """running / exited / lost, judged from status.json and the wrapper pid."""
        st = self.status_file() or {}
        pid = int(self.meta.get("wrapper_pid") or 0)
        out: Dict[str, Any] = {"job_id": self.id, "label": self.meta.get("label"),
                               "command": self.meta.get("command"), "cwd": self.meta.get("cwd"),
                               "owner": self.meta.get("owner"), "pid": pid,
                               "pgid": st.get("pgid"), "started_at": self.meta.get("started_at")}
        if st.get("state") == "exited":
            out.update(state="exited", exit_code=st.get("exit_code"), signal=st.get("signal"),
                       duration_s=st.get("duration_s"), reason=st.get("reason"),
                       peak_rss_bytes=st.get("peak_rss_bytes"))
            return out
        alive = pid > 0 and _pid_alive(pid)
        if alive:
            recorded = self.meta.get("wrapper_start_time")
            now_start = _pid_start_time(pid)
            # A different process wearing this pid is not our wrapper.
            if recorded is not None and now_start is not None and abs(now_start - float(recorded)) > 2.0:
                alive = False
        if alive:
            out.update(state="running",
                       runtime_s=round(time.time() - float(self.meta.get("started_at") or time.time()), 1))
        else:
            out.update(state="lost", exit_code=None,
                       reason="wrapper exited without writing a status (killed -9, reboot, or crash)")
        return out


class JobManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: Dict[str, Job] = {}
        self._scanned_roots: set = set()

    # -- discovery / re-attach ------------------------------------------------

    def scan(self, project_root: str) -> int:
        """Re-attach every job directory under a project (startup, or first use)."""
        root = _jobs_root(project_root)
        n = 0
        if not root.is_dir():
            self._scanned_roots.add(os.path.realpath(project_root))
            return 0
        for d in sorted(root.iterdir()):
            meta = _read_json(d / "job.json") if d.is_dir() else None
            if not meta or not meta.get("job_id"):
                continue
            with self._lock:
                if meta["job_id"] not in self._jobs:
                    self._jobs[meta["job_id"]] = Job(d, meta)
                    n += 1
        self._scanned_roots.add(os.path.realpath(project_root))
        return n

    def _ensure_scanned(self, project_root: str) -> None:
        if os.path.realpath(project_root) not in self._scanned_roots:
            self.scan(project_root)

    def get(self, job_id: str, project_root: Optional[str] = None) -> Job:
        if project_root:
            self._ensure_scanned(project_root)
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise JobError("not_found", f"no job {job_id!r}")
        return job

    def list(self, project_root: Optional[str] = None,
             owner: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        if project_root:
            self._ensure_scanned(project_root)
        with self._lock:
            jobs = list(self._jobs.values())
        out = []
        for j in jobs:
            if project_root and os.path.realpath(j.meta.get("project_root", "")) != os.path.realpath(project_root):
                continue
            if owner and not _owner_matches(j.meta.get("owner") or {}, owner):
                continue
            out.append(j.state())
        return out

    def _running(self, pred) -> int:
        with self._lock:
            jobs = list(self._jobs.values())
        return sum(1 for j in jobs if pred(j) and j.state()["state"] == "running")

    # -- launch -----------------------------------------------------------------

    def start(self, command: str, *, project_root: str, cwd: Optional[str] = None,
              outputs: Optional[List[str]] = None, max_runtime_s: Optional[float] = None,
              label: Optional[str] = None, owner: Optional[Dict[str, Any]] = None,
              task_scope: Optional[Dict[str, Any]] = None,
              conversation_id: Optional[str] = None,
              keep_awake: bool = False) -> Dict[str, Any]:
        """Admit, prepare and spawn a job.  Raises ``JobError`` on refusal;
        on refusal nothing has been created and nothing runs."""
        from app.jobs import policy

        project_root = os.path.realpath(project_root)
        if not os.path.isdir(project_root):
            raise JobError("bad_request", f"project root is not a directory: {project_root}")
        self._ensure_scanned(project_root)
        cwd = os.path.realpath(cwd) if cwd else project_root
        if not os.path.isdir(cwd):
            raise JobError("bad_request", f"cwd is not a directory: {cwd}")
        outputs = [str(o) for o in (outputs or []) if str(o).strip()]
        owner = dict(owner or {})

        # Runtime cap: default 6 h, up to 24 h; longer needs a task-scope grant.
        limit = UNGRANTED_MAX_RUNTIME_S
        granted = (task_scope or {}).get("job_max_runtime_s") if isinstance(task_scope, dict) else None
        try:
            if granted and float(granted) > 0:
                limit = max(limit, float(granted))
        except (TypeError, ValueError):
            pass
        runtime = float(max_runtime_s) if max_runtime_s else float(DEFAULT_MAX_RUNTIME_S)
        if runtime <= 0:
            raise JobError("bad_request", "max_runtime_s must be positive")
        if runtime > limit:
            raise JobError("limit", f"max_runtime_s {runtime:.0f} exceeds the {limit:.0f} s ceiling "
                                    "(a longer run needs a task-scope job_max_runtime_s grant)")

        # 1. Policy, all or nothing, before anything is created.
        decision, shell_env = policy.check(command, project_root=project_root, outputs=outputs,
                                           task_scope=task_scope)
        if not decision.allowed:
            raise JobError("refused", decision.reason
                           + (f"\n\n⚠️ {decision.clamp_notice}" if decision.clamp_notice else ""))

        # 2. Limits.
        if owner and self._running(lambda j: _owner_matches(j.meta.get("owner") or {}, owner)) >= MAX_JOBS_PER_OWNER:
            raise JobError("limit", f"{MAX_JOBS_PER_OWNER} jobs already running for this owner")
        if self._running(lambda j: True) >= MAX_JOBS_PER_USER:
            raise JobError("limit", f"{MAX_JOBS_PER_USER} jobs already running")

        # 3. Declared outputs: create parents (a trailing slash means the dir itself).
        resolved_outputs: List[str] = []
        for out in outputs:
            target = out if os.path.isabs(out) else os.path.join(project_root, out)
            is_dir = out.endswith("/")
            os.makedirs(target if is_dir else os.path.dirname(target) or project_root, exist_ok=True)
            resolved_outputs.append(os.path.realpath(target.rstrip("/")))

        # 4. Job directory and record.
        job_id = "job_" + secrets.token_hex(6)
        job_dir = _jobs_root(project_root) / job_id
        job_dir.mkdir(parents=True, exist_ok=False)
        os.chmod(job_dir, 0o700)
        meta: Dict[str, Any] = {
            "job_id": job_id, "label": label or command[:60], "command": command,
            "project_root": project_root, "cwd": cwd, "outputs": resolved_outputs,
            "declared_outputs": outputs, "max_runtime_s": runtime, "owner": owner,
            "conversation_id": conversation_id, "started_at": time.time(),
            "policy": {"segments": decision.segments, "task_scope_used": decision.task_scope_used,
                       "yolo": decision.yolo, "fingerprint": decision.policy_fingerprint},
        }

        # 5. Spawn the wrapper, detached, under the shell tool's child env.
        env = policy.child_env(shell_env, project_root=project_root, cwd=cwd, job_id=job_id,
                               conversation_id=conversation_id)
        import app as _app_pkg
        pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(_app_pkg.__file__)))
        env["PYTHONPATH"] = os.pathsep.join(
            [pkg_root] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p])
        argv = [sys.executable, "-m", "app.jobs.wrap", "--job-dir", str(job_dir), "--cwd", cwd,
                "--max-runtime", str(runtime), "--", command]
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, cwd=cwd, env=env,
                                    start_new_session=True, close_fds=True)
        except OSError as e:
            try:
                job_dir.rmdir()
            except OSError:
                pass
            raise JobError("spawn_failed", str(e))
        meta["wrapper_pid"] = proc.pid
        meta["wrapper_start_time"] = _pid_start_time(proc.pid)
        # Keep-awake (design: Keep-awake).  Bound to the wrapper pid, so the
        # inhibitor exits on its own when the job does; recorded so kill()
        # can release it early and the caller can see whether it is held.
        awake: Dict[str, Any] = {}
        if keep_awake:
            from app.jobs import inhibit
            awake = inhibit.acquire(proc.pid, meta["label"])
            meta["keep_awake"] = awake.get("keep_awake")
            meta["inhibitor_pid"] = awake.get("inhibitor_pid")
        _write_json(job_dir / "job.json", meta)
        job = Job(job_dir, meta)
        with self._lock:
            self._jobs[job_id] = job
        # The wrapper is a child of this process until it exits; reap it in
        # the background so it never lingers as a zombie.
        threading.Thread(target=proc.wait, name=f"jobwrap-reap-{job_id}", daemon=True).start()
        logger.info(f"🧵 job {job_id} started: {command[:80]!r} (pid {proc.pid})")
        return {"job_id": job_id, "pid": proc.pid, "journal": str(job.journal_path),
                "label": meta["label"], "max_runtime_s": runtime, "outputs": resolved_outputs,
                **({k: v for k, v in awake.items() if k in ("keep_awake", "detail")})}

    # -- wait / read ------------------------------------------------------------

    def wait(self, job_id: str, *, until: Optional[List[str]] = None, pattern: Optional[str] = None,
             lines: int = 5, quiet_ms: int = 800, from_seq: Optional[int] = None,
             timeout_s: float = 240.0, max_tail_records: int = 100,
             project_root: Optional[str] = None) -> Dict[str, Any]:
        from app.shadow.journal import JournalReader, compile_search_pattern, searchable_text
        from app.utils.journal_wait import PollWaiter, parse_wait_request, wait_on_journal
        job = self.get(job_id, project_root)
        timeout_ms = int(max(0.0, min(float(timeout_s), WAIT_CAP_S)) * 1000)
        spec, err = parse_wait_request(
            {"until": until or ["exit"], "cmd_seq": 1, "pattern": pattern, "lines": lines,
             # A job's journal is its own (one command), so a first wait with no
             # from_seq replays it whole; the shadow default (start at the tail)
             # would silently drop output that landed before the call.
             "quiet_ms": quiet_ms, "from_seq": from_seq if from_seq is not None else 1,
             "timeout_ms": timeout_ms,
             "max_tail_records": max_tail_records},
            cap_ms=WAIT_CAP_S * 1000, max_tail_records=500, compile_pattern=compile_search_pattern)
        if err is not None:
            raise JobError(*err)
        reader = JournalReader(str(job.journal_path))

        def stopped() -> Optional[str]:
            # An exit record is the normal end; this catches a wrapper that
            # died without writing one.
            st = job.state()
            return "lost" if st["state"] == "lost" else None

        result = wait_on_journal(reader, spec, waiter=PollWaiter(0.2),
                                 searchable_text=searchable_text, stopped=stopped)
        state = job.state()
        result["state"] = state["state"]
        if state["state"] == "exited":
            result["exit_code"] = state.get("exit_code")
            result["still_running"] = False
            result["obs_id"] = self._record_exit(job, state)
        elif state["state"] == "lost":
            result["still_running"] = False
        return result

    def read(self, job_id: str, *, from_seq: int = 1, max_records: int = 500,
             search: Optional[str] = None, project_root: Optional[str] = None) -> Dict[str, Any]:
        from app.shadow.journal import JournalReader
        job = self.get(job_id, project_root)
        reader = JournalReader(str(job.journal_path))
        if search:
            return {"records": reader.search(search, max_hits=min(max_records, 100)), "mode": "search"}
        return {**reader.read(from_seq, max_records), "mode": "page"}

    # -- kill -------------------------------------------------------------------

    def kill(self, job_id: str, *, project_root: Optional[str] = None,
             reason: str = "killed") -> Dict[str, Any]:
        """TERM the process group, KILL after the grace period.  Always the
        group: a surviving grandchild is exactly what _popen_group guards."""
        job = self.get(job_id, project_root)
        state = job.state()
        if state["state"] != "running":
            return {**state, "killed": False}
        pgid = int(state.get("pgid") or 0)
        wrapper = int(state.get("pid") or 0)
        # The wrapper relays TERM to its group; signal it directly too so a
        # wrapper that has not yet written pgid still dies.
        if pgid:
            _killpg(pgid, signal.SIGTERM)
        if wrapper:
            try:
                os.kill(wrapper, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        deadline = time.monotonic() + KILL_GRACE_S
        while time.monotonic() < deadline:
            if job.state()["state"] != "running" and not (pgid and _group_alive(pgid)):
                break
            time.sleep(0.1)
        if pgid and _group_alive(pgid):
            _killpg(pgid, signal.SIGKILL)
        if wrapper and _pid_alive(wrapper):
            try:
                os.kill(wrapper, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        time.sleep(0.2)
        final = job.state()
        if final["state"] == "running":
            final["state"] = "lost"
        final.setdefault("reason", reason)
        final["killed"] = True
        if final["state"] == "exited":
            final["obs_id"] = self._record_exit(job, final)
        if job.meta.get("inhibitor_pid"):
            from app.jobs import inhibit
            inhibit.release(job.meta.get("inhibitor_pid"))
        logger.info(f"🧵 job {job_id} killed ({reason})")
        return final

    def kill_owned(self, owner: Dict[str, Any], reason: str) -> List[str]:
        """Kill every running job whose owner matches (block end, run end/cancel)."""
        with self._lock:
            jobs = list(self._jobs.values())
        killed = []
        for j in jobs:
            if _owner_matches(j.meta.get("owner") or {}, owner) and j.state()["state"] == "running":
                try:
                    self.kill(j.id, reason=reason)
                    killed.append(j.id)
                except JobError:
                    pass
        return killed

    # -- ledger -----------------------------------------------------------------

    def _record_exit(self, job: Job, state: Dict[str, Any]) -> Optional[str]:
        """One observation per job exit: command, exit, duration, output hashes.

        Recorded as an ``inline`` observation (the store attests only
        single-file / dataset results); the hashes are computed here from
        bytes the runner read.  Idempotent: the obs id is stored in job.json.
        """
        if job.meta.get("obs_id"):
            return job.meta["obs_id"]
        with self._lock:
            if job.meta.get("obs_id"):
                return job.meta["obs_id"]
            try:
                from app.utils.evidence.service import ledger_root, task_context
                from app.utils.evidence.store import ledger_for
                present, missing = [], []
                for p in job.meta.get("outputs") or []:
                    path = Path(p)
                    if path.is_file():
                        present.append({"path": p, "sha256": _sha256(path), "bytes": path.stat().st_size})
                    elif path.is_dir():
                        files = sorted(str(f) for f in path.rglob("*") if f.is_file())
                        present.append({"path": p, "dir": True, "files": len(files),
                                        "sha256": hashlib.sha256(
                                            "".join(_sha256(Path(f)) for f in files[:10000]).encode()).hexdigest()})
                    else:
                        missing.append(p)
                inline = {"job_id": job.id, "command": job.meta.get("command"), "cwd": job.meta.get("cwd"),
                          "exit_code": state.get("exit_code"), "signal": state.get("signal"),
                          "duration_s": state.get("duration_s"), "reason": state.get("reason"),
                          "outputs": present, "outputs_missing": missing}
                prov = {"via": "job", **task_context(job.meta.get("conversation_id")),
                        **{k: v for k, v in (job.meta.get("owner") or {}).items() if v}}
                led = ledger_for(ledger_root(job.meta["project_root"]) or job.meta["project_root"])
                rec = led.record_observation("command", argv=["sh", "-c", job.meta.get("command", "")],
                                             path=job.meta.get("cwd"), inline=inline, provenance=prov)
                job.meta["obs_id"] = rec["id"]
                job.meta["outputs_missing"] = missing
                _write_json(job.dir / "job.json", job.meta)
                return rec["id"]
            except Exception as e:  # noqa: BLE001 -- the ledger must never break job_wait
                logger.warning(f"job {job.id}: ledger observation failed: {e}")
                return None


def _owner_matches(have: Dict[str, Any], want: Dict[str, Any]) -> bool:
    """Every key in ``want`` matches; a run-level filter matches all its blocks."""
    return all(str(have.get(k)) == str(v) for k, v in want.items() if v)


_MANAGER: Optional[JobManager] = None
_MANAGER_LOCK = threading.Lock()


def get_job_manager() -> JobManager:
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = JobManager()
        return _MANAGER
