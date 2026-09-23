"""Scrub secrets/PII from cluster context BEFORE it reaches any model.

Even though Token Factory keeps data out of third-party closed-model APIs, we still
minimise what leaves the cluster. Every replacement is counted and shown in the UI.
"""
import re

_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("JWT", re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("PRIVATE_KEY", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
    ("AWS_KEY", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("BEARER", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}")),
    ("URL_CREDS", re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)")),
    ("KV_SECRET", re.compile(
        r"(?i)\b([A-Z0-9_]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key)[A-Z0-9_]*)"
        r"(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;]+)"
    )),
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("IPV4", re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")),
]


def redact(text: str) -> tuple[str, int]:
    """Return (clean_text, number_of_replacements)."""
    if not text:
        return text, 0
    total = 0
    for name, pat in _PATTERNS:
        if name == "KV_SECRET":
            text, n = pat.subn(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
        else:
            text, n = pat.subn(f"[{name}]", text)
        total += n
    return text, total


def redact_obj(obj):
    """Recursively redact every string in a dict/list. Returns (obj, count)."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, list):
        out, c = [], 0
        for v in obj:
            v2, n = redact_obj(v)
            out.append(v2)
            c += n
        return out, c
    if isinstance(obj, dict):
        out, c = {}, 0
        for k, v in obj.items():
            v2, n = redact_obj(v)
            out[k] = v2
            c += n
        return out, c
    return obj, 0
