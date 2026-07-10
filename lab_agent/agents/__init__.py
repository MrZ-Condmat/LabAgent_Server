"""
Agent modules for the multi-agent system.
"""

from .base_agent import BaseAgent
from .arxiv_daily_agent import ArxivDailyAgent
from .journal_daily_agent import JournalDailyAgent

__all__ = ["BaseAgent", "ArxivDailyAgent", "JournalDailyAgent"]