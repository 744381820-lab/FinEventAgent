"""统一配置入口：业务/运行参数读 demo/config/analysis.json；密钥只读环境变量。"""
from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

DEMO_ROOT = Path(__file__).resolve().parents[1]
_ANALYSIS_CONFIG_PATH = DEMO_ROOT / "config" / "analysis.json"


def load_analysis_config() -> dict:
    """统一配置文件（估值/舆情/LLM/运行），每次调用实时读盘。"""
    try:
        return json.loads(_ANALYSIS_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _bool_env(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _cfg_bool(val, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    """配置门面：非密钥项以 analysis.json 为准，环境变量可覆盖；密钥仅来自环境变量。"""

    @property
    def config_path(self) -> Path:
        return _ANALYSIS_CONFIG_PATH

    @property
    def raw(self) -> dict:
        return load_analysis_config()

    # ---------- runtime ----------
    @property
    def runtime_cfg(self) -> dict:
        return self.raw.get("runtime", {})

    @property
    def run_mode(self) -> str:
        return (os.getenv("RUN_MODE") or self.runtime_cfg.get("run_mode") or "auto").lower()

    @property
    def jinmen_mcp_url(self) -> str:
        return (
            os.getenv("JINMEN_MCP_URL")
            or self.runtime_cfg.get("jinmen_mcp_url")
            or "https://mcp-server-global.comein.cn/mcp-servers/mcp-server-brm/sse"
        )

    @property
    def jinmen_mcp_timeout(self) -> int:
        raw = os.getenv("JINMEN_MCP_TIMEOUT") or self.runtime_cfg.get("jinmen_mcp_timeout", 45)
        return int(raw)

    @property
    def jinmen_mcp_key(self) -> str:
        return os.getenv("JINMEN_MCP_KEY", "")

    # ---------- llm（增强能力，非主链路必需） ----------
    @property
    def llm_cfg(self) -> dict:
        c = self.raw.get("llm", {})
        features = c.get("features") or {}
        return {
            "enabled": _cfg_bool(c.get("enabled"), True),
            "provider": c.get("provider") or "openai_compatible",
            "base_url": (os.getenv("LLM_BASE_URL") or c.get("base_url") or "https://aiapi.gyasset.com/v1").rstrip("/"),
            "api_key": os.getenv("LLM_API_KEY", ""),
            "model": os.getenv("LLM_MODEL") or c.get("model") or "qwen3.7-plus",
            "timeout": int(os.getenv("LLM_TIMEOUT") or c.get("timeout") or 90),
            "circuit_seconds": int(c.get("circuit_seconds") or 600),
            "temperature_extract": float(c.get("temperature_extract") or 0.1),
            "temperature_narrative": float(c.get("temperature_narrative") or 0.2),
            "features": {
                "event_extract": _cfg_bool(features.get("event_extract"), True),
                "fundamental_narrative": _cfg_bool(features.get("fundamental_narrative"), True),
            },
        }

    @property
    def llm_base_url(self) -> str:
        return self.llm_cfg["base_url"]

    @property
    def llm_api_key(self) -> str:
        return self.llm_cfg["api_key"]

    @property
    def llm_model(self) -> str:
        return self.llm_cfg["model"]

    @property
    def llm_timeout(self) -> int:
        return int(self.llm_cfg["timeout"])

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_cfg["enabled"]) and bool(self.llm_api_key)

    def llm_feature(self, name: str) -> bool:
        """某项 LLM 增强是否开启（总开关 + 子功能）。"""
        if not self.llm_cfg["enabled"]:
            return False
        return bool(self.llm_cfg["features"].get(name, False))

    # ---------- sentiment / valuation ----------
    @property
    def sentiment_cfg(self) -> dict:
        cfg = self.raw.get("sentiment", {})
        return {
            "jinmen_weight": float(os.getenv("SENTIMENT_JINMEN_WEIGHT", cfg.get("jinmen_weight", 0.9))),
            "web_weight": float(os.getenv("SENTIMENT_WEB_WEIGHT", cfg.get("web_weight", 0.1))),
            "web_search_enabled": _bool_env("WEB_SEARCH_ENABLED", bool(cfg.get("web_search_enabled", True))),
            "web_search_provider": (
                os.getenv("SEARCH_PROVIDER") or cfg.get("web_search_provider") or "bing_html"
            ).lower(),
            "web_search_max_results": int(cfg.get("web_search_max_results", 8)),
            "jinmen_top_k": int(cfg.get("jinmen_top_k", 25)),
        }

    @property
    def valuation_cfg(self) -> dict:
        return self.raw.get("valuation", {})

    @property
    def search_provider(self) -> str:
        return self.sentiment_cfg["web_search_provider"]

    @property
    def tavily_api_key(self) -> str:
        return os.getenv("TAVILY_API_KEY", "")

    @property
    def bing_search_api_key(self) -> str:
        return os.getenv("BING_SEARCH_API_KEY", "")

    @property
    def fixture_dir(self) -> Path:
        return DEMO_ROOT / "data" / "fixtures"

    @property
    def effective_mode(self) -> str:
        run_mode = self.run_mode
        if run_mode != "auto":
            return run_mode
        has_mcp = bool(self.jinmen_mcp_key)
        has_llm = self.llm_enabled
        if has_mcp and has_llm:
            return "live"
        if has_mcp:
            return "hybrid"
        return "demo"

    def public_status(self) -> dict:
        """给 /api/health 用的非敏感配置摘要。"""
        llm = self.llm_cfg
        return {
            "config_file": str(self.config_path.relative_to(ROOT)).replace("\\", "/"),
            "run_mode": self.run_mode,
            "effective_mode": self.effective_mode,
            "llm": {
                "enabled": llm["enabled"],
                "provider": llm["provider"],
                "base_url": llm["base_url"],
                "model": llm["model"],
                "timeout": llm["timeout"],
                "features": llm["features"],
                "api_key_configured": bool(llm["api_key"]),
            },
            "jinmen": {
                "url": self.jinmen_mcp_url,
                "timeout": self.jinmen_mcp_timeout,
                "api_key_configured": bool(self.jinmen_mcp_key),
            },
            "sentiment": {
                "web_search_provider": self.sentiment_cfg["web_search_provider"],
                "web_search_enabled": self.sentiment_cfg["web_search_enabled"],
            },
        }


settings = Settings()
