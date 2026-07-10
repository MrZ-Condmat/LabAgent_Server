import os
from typing import Optional

from .runtime import configure_process_timezone, load_project_dotenv


class Config:
    def __init__(self, config_path: Optional[str] = None):
        load_project_dotenv(config_path)
        configure_process_timezone()

        # DeepSeek configuration. Scoring and chat intentionally use separate keys.
        self.deepseek_scoring_api_key = os.getenv("DEEPSEEK_SCORING_API_KEY")
        self.deepseek_chat_api_key = os.getenv("DEEPSEEK_CHAT_API_KEY")
        self.deepseek_base_url = (
            os.getenv("DEEPSEEK_BASE_URL")
            or os.getenv("DEEPSEEK_API_URL")
            or os.getenv("API_URL")
            or os.getenv("BASE_URL")
            or "https://llmapi.paratera.com"
        )

        # Application settings
        self.debug = os.getenv("DEBUG", "false").lower() == "true"
        self.log_level = os.getenv("LOG_LEVEL", "INFO")

        # Web interface settings
        self.streamlit_port = int(os.getenv("STREAMLIT_PORT", "8501"))
        self.streamlit_host = os.getenv("STREAMLIT_HOST", "0.0.0.0")

        # WebSocket settings
        self.websocket_port = int(os.getenv("WEBSOCKET_PORT", "8765"))
        self.websocket_host = os.getenv("WEBSOCKET_HOST", "0.0.0.0")

        # Database settings
        self.database_url = os.getenv("DATABASE_URL", "sqlite:///lab_agent.db")

        # External API settings
        self.arxiv_base_url = os.getenv("ARXIV_BASE_URL", "http://export.arxiv.org/api/query")

        # Rate limiting
        self.api_rate_limit = int(os.getenv("API_RATE_LIMIT", "60"))
        self.scraping_delay = float(os.getenv("SCRAPING_DELAY", "1.0"))

        # Timezone settings
        self.default_timezone = os.getenv("DEFAULT_TIMEZONE", "Asia/Shanghai")

    def validate(self) -> bool:
        missing_keys = []

        if not self.deepseek_scoring_api_key:
            missing_keys.append("DEEPSEEK_SCORING_API_KEY")
        if not self.deepseek_chat_api_key:
            missing_keys.append("DEEPSEEK_CHAT_API_KEY")

        if missing_keys:
            print(f"Missing required configuration: {', '.join(missing_keys)}")
            return False

        return True

    def to_dict(self) -> dict:
        return {
            "deepseek_base_url": self.deepseek_base_url,
            "has_deepseek_scoring_api_key": bool(self.deepseek_scoring_api_key),
            "has_deepseek_chat_api_key": bool(self.deepseek_chat_api_key),
            "debug": self.debug,
            "log_level": self.log_level,
            "streamlit_port": self.streamlit_port,
            "streamlit_host": self.streamlit_host,
            "websocket_port": self.websocket_port,
            "websocket_host": self.websocket_host,
            "database_url": self.database_url,
            "arxiv_base_url": self.arxiv_base_url,
            "api_rate_limit": self.api_rate_limit,
            "scraping_delay": self.scraping_delay,
            "default_timezone": self.default_timezone,
        }