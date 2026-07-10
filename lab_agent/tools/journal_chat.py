import os
from typing import Dict, List

from .arxiv_chat import ArxivChat


class JournalChat(ArxivChat):
    def __init__(self):
        super().__init__()
        self.logger.name = "tools.journal_chat"

    def _load_system_prompt(self) -> str:
        prompt_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "config",
            "journalChatPrompt.txt",
        )

        try:
            with open(prompt_path, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            self.logger.error("Journal chat prompt template not found at %s", prompt_path)
            return self._default_prompt()

    def _default_prompt(self) -> str:
        return """You are an expert research assistant for condensed matter physics. Help analyze and discuss the currently loaded selected journal papers, grounding answers in the provided paper context."""

    def _create_papers_summary(self, papers: List[Dict], context_label: str = "today") -> str:
        if not papers:
            return f"No journal papers available for {context_label}."

        priority_groups = {"3": [], "2": [], "1": []}
        for paper in papers:
            score = str(paper.get("score", 1))
            priority_groups.setdefault(score, []).append(paper)

        selection_label = "Today's Journal Daily selection" if context_label == "today" else f"Journal Daily selection for {context_label}"
        summary_parts = [f"{selection_label}: {len(papers)} papers total"]

        for priority in ("3", "2", "1"):
            priority_papers = priority_groups.get(priority, [])
            if not priority_papers:
                continue

            priority_name = {
                "3": "High Priority",
                "2": "Medium Priority",
                "1": "Lower Priority",
            }.get(priority, f"Priority {priority}")
            summary_parts.append(f"\n### {priority_name} Papers ({len(priority_papers)} papers):")

            for i, paper in enumerate(priority_papers[:6]):
                title = paper.get("title", "No title")
                authors = paper.get("authors", "No authors")
                journal = paper.get("journal") or paper.get("source") or paper.get("subjects") or "Unknown journal"
                abstract = (paper.get("abstract") or "No abstract")[:240] + "..."
                reason = paper.get("reason", "No AI assessment")
                key_relevance = paper.get("key_relevance", "")

                summary_parts.append(
                    f"\n**{i + 1}. {title}**\n"
                    f"- Journal: {journal}\n"
                    f"- Authors: {authors}\n"
                    f"- Abstract/Summary: {abstract}\n"
                    f"- AI Assessment: {reason}\n"
                    f"- Key Relevance: {key_relevance}\n"
                )

            if len(priority_papers) > 6:
                summary_parts.append(f"... and {len(priority_papers) - 6} more papers in this priority level.")

        return "\n".join(summary_parts)

    def get_suggested_questions(self) -> List[str]:
        context_label = self.current_context_label or "today"
        source_phrase = "today's" if context_label == "today" else f"the report dated {context_label}"

        if not self.current_papers:
            return [
                f"What journal papers are available for {context_label}?",
                "Help me understand the latest journal highlights",
            ]

        high_priority_count = len([p for p in self.current_papers if str(p.get("score", 1)) == "3"])
        total_count = len(self.current_papers)
        interesting_prompt = (
            f"What are the most interesting papers from today's {total_count} journal articles?"
            if context_label == "today"
            else f"What are the most interesting papers from the {total_count} journal articles on {context_label}?"
        )

        suggestions = [
            interesting_prompt,
            "Summarize the high-priority journal papers for me",
            "Which papers are most relevant to superconductivity or strong correlation?",
            "Compare the key findings across these journal papers",
            f"What experimental techniques are featured in {source_phrase} journal papers?",
            "Which papers suggest useful follow-up directions for our lab?",
        ]

        if high_priority_count > 0:
            suggestions.insert(1, f"Explain the significance of the {high_priority_count} high-priority journal papers")

        return suggestions
