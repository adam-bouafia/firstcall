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
})
