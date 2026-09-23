"""Classify the suggested next command. FirstCall suggests, a human runs.

Read-only kubectl verbs are marked safe. Anything that mutates the cluster is still
shown, but flagged so the UI renders it as 'review before running'.
"""
import shlex

READ_ONLY_VERBS = {
    "get", "describe", "logs", "top", "explain", "events", "api-resources",
    "api-versions", "version", "cluster-info", "auth", "diff",
}
MUTATING_VERBS = {
    "delete", "apply", "create", "patch", "edit", "replace", "scale", "set", "label",
    "annotate", "drain", "cordon", "uncordon", "taint", "rollout", "exec", "cp",
    "run", "expose", "autoscale", "port-forward", "debug",
}
_ROLLOUT_READONLY = {"status", "history"}
_VALUE_FLAGS = {"-n", "--namespace", "--context", "--kubeconfig", "-l", "--selector", "-o", "--output",
                "-c", "--container", "--field-selector", "--tail", "--since", "-f", "--filename"}


def is_safe(command: str) -> bool:
    cmd = command.strip()
    if not cmd:
        return False
    if any(t in cmd for t in (";", "&&", "||", "|", "`", "$(", ">")):
        return False  # no chaining / redirection in one-click commands
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return False
    if not parts or parts[0] not in ("kubectl", "k"):
        return False
    verbs, skip = [], False
    for p in parts[1:]:
        if skip:
            skip = False
            continue
        if p.startswith("-"):
            skip = p in _VALUE_FLAGS
            continue
        verbs.append(p)
    if not verbs:
        return False
    verb = verbs[0]
    if verb == "rollout":
        return len(verbs) > 1 and verbs[1] in _ROLLOUT_READONLY
    if verb == "auth":
        return len(verbs) > 1 and verbs[1] == "can-i"
    return verb in READ_ONLY_VERBS and verb not in MUTATING_VERBS


# ---------------------------------------------------------------- remediation (human-approved writes)
REMEDIATION_KINDS = {"deployment", "deployments", "deploy", "service", "services", "svc"}


def remediation_check(command: str, namespaces: list[str]) -> tuple[bool, str]:
    """Is this a write we allow after approval? Returns (ok, reason).

    Allowed: rollout undo | set image | set resources | scale | patch, on a Deployment or Service,
    in one of the watched namespaces, one command, no shell tricks. Never delete/exec/secrets/RBAC.
    """
    cmd = command.strip()
    if not cmd:
        return False, "no command"
    if any(t in cmd for t in (";", "&&", "||", "|", "`", "$(", ">", "<")):
        return False, "chaining/redirection not allowed"
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return False, "unparseable"
    if not parts or parts[0] not in ("kubectl", "k"):
        return False, "not a kubectl command"
    words, ns, skip = [], None, None
    for p in parts[1:]:
        if skip:
            if skip in ("-n", "--namespace"):
                ns = p
            skip = None
            continue
        if p.startswith("--namespace="):
            ns = p.split("=", 1)[1]
            continue
        if p in ("-n", "--namespace", "-p", "--patch", "--type", "-c", "--containers", "--replicas",
                 "--limits", "--requests", "--to-revision", "-l", "--selector"):
            skip = p
            continue
        if p.startswith("-"):
            if p.startswith(("--dry-run", "--context", "--kubeconfig", "--server", "--token", "--as")):
                return False, f"flag {p.split('=')[0]} not allowed"
            continue
        words.append(p)
    if not words:
        return False, "no verb"
    verb = words[0]
    if verb == "rollout":
        if len(words) < 2 or words[1] != "undo":
            return False, "only 'rollout undo' is allowed"
        target = words[2] if len(words) > 2 else ""
    elif verb == "set":
        if len(words) < 2 or words[1] not in ("image", "resources"):
            return False, "only 'set image' and 'set resources' are allowed"
        target = words[2] if len(words) > 2 else ""
    elif verb in ("scale", "patch"):
        target = words[1] if len(words) > 1 else ""
        if "/" not in target and len(words) > 2:  # "patch svc orders"
            target = f"{words[1]}/{words[2]}"
    else:
        return False, f"verb '{verb}' is never run by FirstCall"
    kind = target.split("/", 1)[0].lower()
    if kind not in REMEDIATION_KINDS:
        return False, f"target must be a deployment or service (got '{target or '?'}')"
    if verb in ("rollout", "set", "scale") and kind in ("service", "services", "svc"):
        return False, f"'{verb}' needs a deployment"
    if not ns:
        return False, "namespace (-n) is required"
    if "*" not in namespaces and ns not in namespaces:
        return False, f"namespace '{ns}' is not watched by FirstCall"
    if ns in ("kube-system", "kube-public", "kube-node-lease"):
        return False, "system namespaces are off limits"
    return True, "ok"
