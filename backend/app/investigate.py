"""Human-approved, read-only investigation: run the suggested kubectl command, feed the output
back to the model, get a sharper diagnosis. Mutating commands are refused, always."""
import shlex
import subprocess
import time

from app.config import get_settings
from app.diagnose import parse_diagnosis, redact_snapshot
from app.llm.client import get_client
from app.llm.prompts import SYSTEM_PROMPT, build_user_prompt
from app.redact import redact
from app.safety import is_safe
from app.schemas import Diagnosis, DiagnoseResponse, Snapshot, Usage

MAX_OUTPUT = 6000


class UnsafeCommand(Exception):
    pass


def run_readonly(command: str, timeout: int = 20) -> dict:
    if not is_safe(command):
        raise UnsafeCommand(f"refusing to run a command that is not read-only: {command}")
    args = shlex.split(command)
    args[0] = get_settings().kubectl_bin  # 'k' alias or custom path
    t0 = time.perf_counter()
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        out, code = (p.stdout + ("\n" + p.stderr if p.stderr else "")).strip(), p.returncode
    except subprocess.TimeoutExpired:
        out, code = f"(timed out after {timeout}s)", 124
    except FileNotFoundError:
        out, code = f"(kubectl not found: set KUBECTL_BIN, tried '{args[0]}')", 127
    clean, n = redact(out[-MAX_OUTPUT:])
    return {"command": command, "exit_code": code, "output": clean, "redactions": n,
            "duration_ms": int((time.perf_counter() - t0) * 1000)}


FOLLOWUP = """

INVESTIGATION SO FAR (read-only commands a human approved, with their redacted output):
{steps}

Update your diagnosis using this new evidence. If the root cause is now confirmed, raise confidence
and make `next_command` the command that verifies the fix after it is applied (still read-only).
Do not repeat a command that already ran."""


def followup(snap: Snapshot, steps: list[dict], model_key: str | None = None, client=None) -> DiagnoseResponse:
    clean = redact_snapshot(snap)
    body = "\n\n".join(f"$ {s['command']}  (exit {s['exit_code']})\n{s['output']}" for s in steps)
    user = build_user_prompt(clean) + FOLLOWUP.format(steps=body)
    client = client or get_client()
    res = client.chat(SYSTEM_PROMPT, user, model_key=model_key)
    try:
        d = parse_diagnosis(res.text)
    except Exception:
        d = Diagnosis(summary="Follow-up answer was not parseable.", root_cause=res.text[:500], confidence=0,
                      next_command=f"kubectl get events -n {snap.namespace} --sort-by=.lastTimestamp")
    return DiagnoseResponse(
        diagnosis=d,
        usage=Usage(model_key=res.model_key, model_id=res.model_id, prompt_tokens=res.prompt_tokens,
                    completion_tokens=res.completion_tokens, latency_ms=res.latency_ms, cost_usd=res.cost_usd),
        redactions=clean.redactions + sum(s.get("redactions", 0) for s in steps),
        context_chars=len(user))
