"""Human-approved remediation: preview with a server-side dry run, apply only after a person approves.

Safety layers:
  1. the command must pass remediation_check() (rollout undo | set image | set resources | scale | patch,
     Deployment/Service only, watched namespace, no chaining)
  2. a server-side dry run must succeed first (Kubernetes validates it against the live object + RBAC)
  3. a named human approves the exact command (hash-checked), in the console or on Telegram
  4. in-cluster, RBAC only grants those verbs in the watched namespaces (Helm: remediation.enabled)
Everything lands in the incident timeline with who approved it.
"""
import hashlib
import shlex
import subprocess
import time

from app.config import get_settings
from app.redact import redact
from app.safety import remediation_check


def cmd_hash(command: str) -> str:
    return hashlib.sha1(command.strip().encode()).hexdigest()[:10]


def _run(command: str, extra: list[str], timeout: int = 30) -> dict:
    args = shlex.split(command)
    args[0] = get_settings().kubectl_bin
    t0 = time.perf_counter()
    try:
        p = subprocess.run(args + extra, capture_output=True, text=True, timeout=timeout)
        out, code = (p.stdout + ("\n" + p.stderr if p.stderr else "")).strip(), p.returncode
    except subprocess.TimeoutExpired:
        out, code = f"(timed out after {timeout}s)", 124
    except FileNotFoundError:
        out, code = "(kubectl not found)", 127
    clean, _ = redact(out[-4000:])
    return {"command": command, "exit_code": code, "output": clean, "duration_ms": int((time.perf_counter() - t0) * 1000)}


def check(command: str) -> tuple[bool, str]:
    s = get_settings()
    if s.firstcall_remediation != "approve":
        return False, "remediation is disabled (FIRSTCALL_REMEDIATION=off)"
    return remediation_check(command, s.namespaces)


def preview(command: str) -> dict:
    ok, why = check(command)
    if not ok:
        return {"command": command, "ok": False, "reason": why, "hash": cmd_hash(command)}
    r = _run(command, ["--dry-run=server"])
    return {**r, "ok": r["exit_code"] == 0, "reason": "dry run passed" if r["exit_code"] == 0 else "dry run failed",
            "hash": cmd_hash(command)}


def apply(command: str) -> dict:
    ok, why = check(command)
    if not ok:
        return {"command": command, "ok": False, "reason": why}
    r = _run(command, [])
    return {**r, "ok": r["exit_code"] == 0, "reason": "applied" if r["exit_code"] == 0 else "kubectl failed"}
