"""Tavily web search: used only when the model is unsure.

Kubernetes failures often hinge on a specific error string ("failed to create shim task ...",
a CSI driver message, an operator's error). Open-weight models may not know a recent one.
When confidence is low, FirstCall searches the web for the *error text only* and re-asks the model
with the top results, which must be cited.

Responsible design: what leaves for the search is a sanitized error string, never pod names,
namespaces, node names, IPs, images from your registry, or logs. Off unless TAVILY_API_KEY is set.
"""
import logging
import re

import httpx

from app.config import get_settings
from app.redact import redact
from app.schemas import Snapshot

log = logging.getLogger("firstcall.websearch")

# strings that identify the customer's cluster: stripped before anything is searched
_STRIP = [
    (re.compile(r"\b[a-z0-9-]{3,}-[a-f0-9]{8,10}-[a-z0-9]{5}\b"), "<pod>"),          # pod names
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b"), "<ip>"),
    (re.compile(r"\b[a-f0-9]{12,64}\b"), "<id>"),                                    # container/image ids
    (re.compile(r"(?i)\b(?:[a-z0-9.-]+\.)?(?:ecr|gcr|acr|azurecr|registry)[a-z0-9./-]*"), "<registry>"),
    (re.compile(r'"[^"]*\.(?:internal|local|svc|cluster\.local)[^"]*"'), '"<host>"'),
]


def sanitize(text: str) -> str:
    clean, _ = redact(text)
    for pat, repl in _STRIP:
        clean = pat.sub(repl, clean)
    return " ".join(clean.split())[:280]


def build_query(snap: Snapshot, category: str) -> str:
    """The most informative error line, stripped of anything identifying."""
    parts = []
    for ev in reversed(snap.events):
        if ev.get("type") == "Warning" and ev.get("message"):
            parts.append(ev["message"])
            break
    for cs in snap.container_statuses:
        if cs.get("message"):
            parts.append(cs["message"])
            break
    for text in snap.logs.values():
        lines = [x for x in (text or "").splitlines() if re.search(r"(?i)error|fatal|exception|denied|refused", x)]
        if lines:
            parts.append(lines[-1])
            break
    q = sanitize(" ".join(parts)) or f"kubernetes {snap.phase} {category}"
    return f"kubernetes {q}"


class Tavily:
    def __init__(self, api_key: str = "", base_url: str = "https://api.tavily.com"):
        s = get_settings()
        self.key = api_key or s.tavily_api_key
        self.base = (base_url or s.tavily_base_url).rstrip("/")
        self.http = httpx.Client(timeout=20)

    @property
    def configured(self) -> bool:
        return bool(self.key)

    def search(self, query: str, max_results: int = 4) -> list[dict]:
        r = self.http.post(f"{self.base}/search",
                           headers={"Authorization": f"Bearer {self.key}"},
                           json={"query": query, "max_results": max_results, "search_depth": "basic",
                                 "include_answer": False})
        r.raise_for_status()
        out = []
        for item in r.json().get("results", [])[:max_results]:
            out.append({"title": item.get("title", "")[:120], "url": item.get("url", ""),
                        "snippet": " ".join((item.get("content") or "").split())[:400]})
        return out


WEB_PROMPT = """

WEB EVIDENCE (public sources found by searching for the error text only; the cluster's names, IPs and
logs were NOT sent anywhere):
search query: {query}
{results}

Use these only if they match the snapshot. Update the diagnosis, cite the URLs you used in `sources`,
and raise `confidence` only if a source really explains the failure."""


def format_results(query: str, results: list[dict]) -> str:
    body = "\n".join(f"[{i + 1}] {r['title']} — {r['url']}\n    {r['snippet']}" for i, r in enumerate(results))
    return WEB_PROMPT.format(query=query, results=body or "(no results)")
