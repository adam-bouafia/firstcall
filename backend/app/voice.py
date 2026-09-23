"""ElevenLabs text-to-speech for night alerts: the diagnosis as a Telegram voice note.

At 3am an engineer hears "payments-api is in CrashLoopBackOff; the last rollout removed
DATABASE_URL; rolling back fixes it" without unlocking anything. Off unless ELEVENLABS_API_KEY is set
and FIRSTCALL_VOICE_ALERTS=true.
"""
import logging

import httpx

from app.config import get_settings

log = logging.getLogger("firstcall.voice")


def spoken_text(inc: dict, resp: dict) -> str:
    d = resp["diagnosis"]
    workload = (inc.get("workload") or inc["pod"]).split("/")[-1].replace("-", " ")
    line = f"FirstCall alert. {workload} in namespace {inc['namespace']} is {inc['reason']}. "
    line += f"{d['root_cause']} "
    if d.get("suspected_change"):
        line += f"{d['suspected_change']} "
    if d.get("remediation_command"):
        line += "A fix is proposed in the app and needs your approval."
    return line[:900]


def synthesize(text: str) -> bytes | None:
    s = get_settings()
    if not s.elevenlabs_api_key:
        return None
    url = f"{s.elevenlabs_base_url.rstrip('/')}/v1/text-to-speech/{s.elevenlabs_voice_id}"
    try:
        r = httpx.post(url, timeout=45,
                       headers={"xi-api-key": s.elevenlabs_api_key, "accept": "audio/mpeg"},
                       json={"text": text, "model_id": "eleven_turbo_v2_5",
                             "voice_settings": {"stability": 0.5, "similarity_boost": 0.7}})
        r.raise_for_status()
        return r.content
    except Exception as e:
        log.warning("elevenlabs tts failed: %s", e)
        return None
