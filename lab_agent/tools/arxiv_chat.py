import json
import logging
import os
from typing import List, Dict, Optional
from openai import OpenAI
from ..utils import Config


class ArxivChat:
    def __init__(self):
        self.logger = logging.getLogger("tools.arxiv_chat")
        self.config = Config()
        
        if not self.config.deepseek_chat_api_key:
            raise ValueError("DeepSeek chat API key not found. Please set DEEPSEEK_CHAT_API_KEY in your .env file")
        
        self.client = OpenAI(
            api_key=self.config.deepseek_chat_api_key,
            base_url=self.config.deepseek_base_url,
            timeout=60.0,
            max_retries=0,
        )
        self.system_prompt = self._load_system_prompt()
        self.model_config = self._load_model_config()
        self.conversation_history = []
        self.current_papers = []
        self.current_context_label = "today"
        
    def _load_system_prompt(self) -> str:
        prompt_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), 
            'config', 
            'arXivChatPrompt.txt'
        )
        
        try:
            with open(prompt_path, 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            self.logger.error(f"Chat prompt template not found at {prompt_path}")
            return self._default_prompt()
    
    def _load_model_config(self) -> Dict:
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), 
            'config', 
            'models.json'
        )
        
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                return config.get('chatModel', {
                    'name': 'DeepSeek-V4-Pro',
                    'maxTokens': 1500,
                    'temperature': 0.3
                })
        except FileNotFoundError:
            self.logger.error(f"Model config not found at {config_path}")
            return {
                'name': 'DeepSeek-V4-Pro',
                'maxTokens': 1500,
                'temperature': 0.3
            }
    
    def _default_prompt(self) -> str:
        return """You are an expert research assistant for condensed matter physics and 2D materials. 
        Help analyze and discuss the currently loaded arXiv papers. Provide scientific insights 
        and connect papers to broader research trends."""
    
    def set_papers_context(self, papers: List[Dict], context_label: str = "today") -> None:
        """Set the paper context for the chat session."""
        self.current_papers = papers
        self.current_context_label = context_label or "today"
        self.logger.info(f"Set context with {len(papers)} papers for {self.current_context_label}")
        
        # Clear conversation history when new papers are set
        self.conversation_history = []
        
        # Create papers summary for context
        papers_summary = self._create_papers_summary(papers, self.current_context_label)
        
        # Add papers context as initial system message
        context_title = "Today's Papers Context" if self.current_context_label == "today" else f"Papers Context for {self.current_context_label}"
        context_message = f"{self.system_prompt}\n\n## {context_title}\n{papers_summary}"
        self.conversation_history.append({
            "role": "system", 
            "content": context_message
        })
    
    def _create_papers_summary(self, papers: List[Dict], context_label: str = "today") -> str:
        """Create a concise summary of papers for context."""
        if not papers:
            return f"No papers available for {context_label}."
        
        # Group papers by priority
        priority_groups = {"3": [], "2": [], "1": []}
        for paper in papers:
            score = str(paper.get('score', 1))
            if score in priority_groups:
                priority_groups[score].append(paper)
        
        summary_parts = []
        
        for priority, priority_papers in priority_groups.items():
            if not priority_papers:
                continue
                
            priority_name = {
                "3": "High Priority",
                "2": "Medium Priority", 
                "1": "Lower Priority"
            }[priority]
            
            summary_parts.append(f"\n### {priority_name} Papers ({len(priority_papers)} papers):")
            
            for i, paper in enumerate(priority_papers[:5]):  # Show first 5 per priority
                title = paper.get('title', 'No title')
                authors = paper.get('authors', 'No authors')
                abstract = paper.get('abstract', 'No abstract')[:200] + "..."
                reason = paper.get('reason', 'No AI assessment')
                
                summary_parts.append(f"""
**{i+1}. {title}**
- Authors: {authors}
- Abstract: {abstract}
- AI Assessment: {reason}
""")
                
            if len(priority_papers) > 5:
                summary_parts.append(f"... and {len(priority_papers) - 5} more papers in this priority level.")
        
        total_papers = len(papers)
        collection_label = "Today's arXiv cond-mat collection" if context_label == "today" else f"ArXiv cond-mat collection for {context_label}"
        summary_parts.insert(0, f"{collection_label}: {total_papers} papers total")
        
        return "\n".join(summary_parts)
    
    def chat(self, user_message: str) -> Dict[str, str]:
        """Chat with the configured DeepSeek chat model about the loaded papers."""
        try:
            self.conversation_history.append({
                "role": "user",
                "content": user_message
            })

            response = self.client.chat.completions.create(
                model=self.model_config.get('name', 'DeepSeek-V4-Pro'),
                messages=self.conversation_history,
                max_tokens=self.model_config.get('maxTokens', 1500),
                temperature=self.model_config.get('temperature', 0.3)
            )
            assistant_message = response.choices[0].message.content

            self.conversation_history.append({
                "role": "assistant",
                "content": assistant_message
            })

            if len(self.conversation_history) > 21:
                self.conversation_history = [self.conversation_history[0]] + self.conversation_history[-20:]

            return {
                'success': True,
                'response': assistant_message,
                'papers_count': len(self.current_papers)
            }

        except Exception as e:
            self.logger.error(f"Chat error: {e}")
            return {
                'success': False,
                'error': str(e),
                'response': f"Sorry, I encountered an error: {e}"
            }

    def get_suggested_questions(self) -> List[str]:
        """Get suggested questions based on current papers"""
        context_label = self.current_context_label or "today"
        source_phrase = "today's" if context_label == "today" else f"the report dated {context_label}"
        from_phrase = "today" if context_label == "today" else f"the {context_label} report"

        if not self.current_papers:
            return [
                f"What papers are available for {context_label}?",
                "Help me understand the latest trends in 2D materials"
            ]
        
        # Count papers by subject for suggestions
        high_priority_count = len([p for p in self.current_papers if p.get('score') == 3])
        total_count = len(self.current_papers)
        interesting_prompt = (
            f"What are the most interesting papers from today's {total_count} submissions?"
            if context_label == "today"
            else f"What are the most interesting papers from the {total_count} submissions on {context_label}?"
        )
        
        suggestions = [
            interesting_prompt,
            "Summarize the high-priority papers for me",
            "Which papers are related to 2D materials and graphene?",
            f"Tell me about any superconductivity papers from {from_phrase}",
            f"What experimental techniques are featured in {source_phrase} papers?",
            "Are there any papers on topological materials?",
            "Which papers might be relevant for device applications?",
            "Help me identify potential collaboration opportunities"
        ]
        
        if high_priority_count > 0:
            suggestions.insert(1, f"Explain the significance of the {high_priority_count} high-priority papers")
        
        return suggestions
    
    def clear_conversation(self):
        """Clear the conversation history but keep papers context"""
        if self.current_papers:
            # Reset with just the system prompt and papers context
            self.set_papers_context(self.current_papers, self.current_context_label)
        else:
            self.conversation_history = []
            
    def get_conversation_summary(self) -> Dict:
        """Get a summary of the current conversation"""
        user_messages = len([msg for msg in self.conversation_history if msg['role'] == 'user'])
        assistant_messages = len([msg for msg in self.conversation_history if msg['role'] == 'assistant'])
        
        return {
            'total_exchanges': user_messages,
            'papers_in_context': len(self.current_papers),
            'conversation_active': user_messages > 0
        }