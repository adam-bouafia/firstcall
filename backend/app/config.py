from functools import lru_cache
from pathlib import Path

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]  # repo root


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), extra="ignore")

    # LLM
    firstcall_llm: str = "real"  # real | mock
    firstcall_default_model: str = ""
    nebius_api_key: str = ""
    openrouter_api_key: str = ""
    ollama_base_url: str = ""
    custom_llm_base_url: str = ""
    custom_llm_api_key: str = ""
    groq_api_key: str = ""
    cerebras_api_key: str = ""

    # Cluster
    firstcall_source: str = "cluster"  # cluster | fixtures
    firstcall_namespaces: str = "firstcall-demo"  # comma list, or * for all non-system namespaces
    firstcall_log_tail: int = 80
    fixtures_dir: Path = ROOT / "scenarios" / "fixtures"
    kubectl_bin: str = "kubectl"

    # Product loop
    firstcall_watch: bool = True
    firstcall_watch_interval: int = 15
    firstcall_auto_diagnose: bool = True
    firstcall_auto_investigate: int = 1  # read-only follow-up steps run automatically (0 = off)
    firstcall_resolve_after: int = 2  # consecutive healthy scans before an incident is resolved
    firstcall_db: Path = ROOT / "data" / "firstcall.db"
    tavily_api_key: str = ""
    tavily_base_url: str = "https://api.tavily.com"
    firstcall_web_search: str = "low-confidence"   # off | low-confidence | always
    firstcall_web_search_threshold: float = 0.6
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"   # "Rachel", a default public voice
    elevenlabs_base_url: str = "https://api.elevenlabs.io"
    firstcall_voice_alerts: bool = False            # send the alert as a Telegram voice note too
    firstcall_remediation: str = "off"  # off | approve (a human approves each fix; dry run first)
    firstcall_write_events: bool = True  # post diagnoses as Kubernetes Events on the broken workload
    slack_webhook_url: str = ""
    telegram_bot_token: str = ""
    telegram_chat_ids: str = ""          # comma separated; the bot tells you yours on /start
    telegram_api_base: str = "https://api.telegram.org"
    telegram_commands: bool = True        # long-poll for /status, /incidents, buttons
    firstcall_alert_mode: str = "always"  # always | offhours (initial value; runtime changes persist)
    firstcall_timezone: str = "Europe/Amsterdam"
    firstcall_business_start: str = "09:00"
    firstcall_business_end: str = "17:00"
    firstcall_business_days: str = "mon-fri"
    firstcall_public_url: str = "http://localhost:3000"

    @property
    def namespaces(self) -> list[str]:
        return [n.strip() for n in self.firstcall_namespaces.split(",") if n.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def load_models() -> dict:
    with open(Path(__file__).parent / "models.yaml") as f:
        return yaml.safe_load(f)
