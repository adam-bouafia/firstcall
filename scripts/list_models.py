"""Print the model IDs a provider serves:  python scripts/list_models.py [nebius|ollama|openrouter|custom]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.llm.client import OpenAICompatClient  # noqa: E402

provider = sys.argv[1] if len(sys.argv) > 1 else "nebius"
for mid in OpenAICompatClient().list_models(provider):
    print(mid)
