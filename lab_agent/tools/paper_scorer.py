import logging
import os
import json
import time
from typing import List, Dict
from openai import OpenAI
from ..utils import Config


class PaperScorer:
    def __init__(self):
        self.logger = logging.getLogger("tools.paper_scorer")
        self.config = Config()
        
        if not self.config.deepseek_scoring_api_key:
            raise ValueError("DeepSeek scoring API key not found. Please set DEEPSEEK_SCORING_API_KEY in your .env file")
        
        self.client = OpenAI(
            api_key=self.config.deepseek_scoring_api_key,
            base_url=self.config.deepseek_base_url,
            timeout=60.0,
            max_retries=0,
        )
        self.prompt_template = self._load_prompt_template()
        
        # Load model configuration
        self.model_config = self._load_model_config()
        
    def _load_model_config(self) -> Dict:
        """Load model configuration from models.json"""
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), 
            'config', 
            'models.json'
        )
        
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                models_config = json.load(f)
                return models_config.get('arxivFilterModel', {})
        except FileNotFoundError:
            self.logger.warning(f"Models config not found at {config_path}, using DeepSeek-V4-Flash")
            return {"name": "DeepSeek-V4-Flash", "provider": "deepseek"}
    
    def _load_prompt_template(self) -> str:
        prompt_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            'config',
            'promptArticleRecommender.txt'
        )
        
        try:
            with open(prompt_path, 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            self.logger.error(f"Prompt template not found at {prompt_path}")
            return self._default_prompt()
    
    def _default_prompt(self) -> str:
        return """Rate this arXiv paper from 1-3 based on relevance to a condensed matter physics laboratory focused on cuprate superconductors, nickelate superconductors, strongly correlated electron systems, STM/STS, STEM/EELS, electron ptychography, graphene-based correlated systems, and quantum anomalous Hall physics.
        3 = High relevance: directly related to cuprate superconductors, nickelate superconductors, strongly correlated superconductivity, STM/STS of quantum materials, STEM/EELS or electron ptychography of quantum materials, graphene-based correlated phases, or quantum anomalous Hall physics.
        2 = Medium relevance: related to condensed matter physics, materials science, quantum transport, topological materials, 2D superconductivity, or experimental/theoretical methods that may inform the lab's research.
        1 = Low relevance: general scientific interest only, or not directly related to the lab's research directions.

        Respond with: Score: X, Reason: [brief explanation], Key Relevance: [up to 5 English keywords or short phrases]
        
        {PAPER_INFO}"""
    
    def score_papers(self, papers: List[Dict[str, str]]) -> List[Dict[str, any]]:
        print(f"[DEBUG] Starting to score {len(papers)} papers")
        scored_papers = []
        
        for i, paper in enumerate(papers):
            try:
                print(f"[DEBUG] Scoring paper {i+1}/{len(papers)}: {paper.get('title', 'No title')[:50]}...")
                score_data = self._score_single_paper_with_retry(paper)
                paper_with_score = paper.copy()
                paper_with_score.update(score_data)
                scored_papers.append(paper_with_score)
                print(f"[DEBUG] Paper scored: {score_data.get('score', 'N/A')} - {score_data.get('reason', 'N/A')}")
                
            except Exception as e:
                self.logger.error(f"Error scoring paper {paper.get('id', 'unknown')}: {e}")
                print(f"[DEBUG] Scoring error for paper {i+1}: {e}")
                # Add paper with default score if scoring fails
                paper_with_score = paper.copy()
                paper_with_score.update({
                    'score': 1,
                    'reason': 'Error in scoring - defaulted to low priority',
                    'key_relevance': 'N/A'
                })
                scored_papers.append(paper_with_score)
        
        print(f"[DEBUG] Finished scoring all papers")
        return scored_papers

    def _score_single_paper_with_retry(self, paper: Dict[str, str]) -> Dict[str, any]:
        max_attempts = 2
        last_error = None

        for attempt in range(1, max_attempts + 1):
            try:
                if attempt > 1:
                    print(f"[DEBUG] Retrying paper scoring attempt {attempt}/{max_attempts}...")
                return self._score_single_paper(paper)
            except Exception as e:
                last_error = e
                self.logger.warning(
                    f"Scoring attempt {attempt}/{max_attempts} failed for "
                    f"{paper.get('id', 'unknown')}: {e}"
                )
                if attempt < max_attempts:
                    time.sleep(2)

        raise last_error
    
    def _score_single_paper(self, paper: Dict[str, str]) -> Dict[str, any]:
        # Format paper info for the prompt
        paper_info = f"""
Title: {paper.get('title', 'N/A')}
Authors: {paper.get('authors', 'N/A')}
Abstract: {paper.get('abstract', 'N/A')}
Subjects: {paper.get('subjects', 'N/A')}
"""
        
        prompt = self.prompt_template.replace('{PAPER_INFO}', paper_info)
        
        try:
            print(f"[DEBUG] Making DeepSeek scoring API call...")
            model_name = self.model_config.get('name', 'DeepSeek-V4-Flash')
            response = self.client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": "You are an expert research paper evaluator for a condensed matter physics laboratory focused on cuprate superconductors, nickelate superconductors, strongly correlated electron systems, STM/STS, STEM/EELS, electron ptychography, graphene-based correlated systems, and quantum anomalous Hall physics."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=5000,
                temperature=0.1
            )

            print(f"[DEBUG] DeepSeek scoring API call successful")
            response_text = response.choices[0].message.content
            print(f"[DEBUG] Response: {response_text[:100]}...")
            return self._parse_response(response_text)

        except Exception as e:
            self.logger.error(f"API error: {e}")
            print(f"[DEBUG] API error: {e}")
            raise
    
    def _parse_response(self, response_text: str) -> Dict[str, any]:
        # Parse the response to extract score, reason, and key relevance
        lines = response_text.strip().split('\n')
        
        score = 1
        reason = "Unable to parse response"
        key_relevance = "N/A"
        
        for line in lines:
            line = line.strip()
            if line.lower().startswith('score:') or line.lower().startswith('**score**:'):
                try:
                    score_part = line.split(':', 1)[1].strip()
                    # Extract the number from the score part
                    import re
                    score_match = re.search(r'(\d+)', score_part)
                    if score_match:
                        score = int(score_match.group(1))
                        score = max(1, min(3, score))  # Clamp between 1-3
                except:
                    pass
            elif line.lower().startswith('reason:') or line.lower().startswith('**reason**:'):
                reason = line.split(':', 1)[1].strip()
            elif line.lower().startswith('key relevance:') or line.lower().startswith('**key relevance**:'):
                key_relevance = line.split(':', 1)[1].strip()
        
        return {
            'score': score,
            'reason': reason,
            'key_relevance': key_relevance
        }
    
    def batch_score_papers(self, papers: List[Dict[str, str]], batch_size: int = 5) -> List[Dict[str, any]]:
        """Score papers in batches to manage API rate limits"""
        all_scored = []
        
        for i in range(0, len(papers), batch_size):
            batch = papers[i:i + batch_size]
            self.logger.info(f"Scoring batch {i//batch_size + 1} ({len(batch)} papers)")
            
            scored_batch = self.score_papers(batch)
            all_scored.extend(scored_batch)
            
            # Brief pause between batches to respect rate limits
            time.sleep(1)
        
        return all_scored
