import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.update({
    "FIRSTCALL_LLM": "mock",
    "FIRSTCALL_SOURCE": "fixtures",
    "FIRSTCALL_WATCH": "false",
    "FIRSTCALL_AUTO_INVESTIGATE": "0",
    "FIRSTCALL_DEFAULT_MODEL": "qwen3-30b",
    "FIRSTCALL_DB": str(Path(tempfile.mkdtemp()) / "test.db"),
    # env vars beat the repo's .env: a developer's real keys and settings must not reach the tests
    "FIRSTCALL_REMEDIATION": "off",
    "FIRSTCALL_NAMESPACES": "firstcall-demo",
    **{k: "" for k in ("NEBIUS_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY", "OPENROUTER_API_KEY",
                       "CUSTOM_LLM_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_IDS", "SLACK_WEBHOOK_URL",
                       "TAVILY_API_KEY", "ELEVENLABS_API_KEY")},
})
