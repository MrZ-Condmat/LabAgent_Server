"""Verify actual SDK request bodies offline, without an API key or LLM call."""

import ast
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
from openai import OpenAI
import pytest

from lab_agent.tools.arxiv_chat import ArxivChat
from lab_agent.tools.journal_chat import JournalChat
from lab_agent.tools.paper_scorer import PaperScorer, ScoringTokenLimitError
from lab_agent.utils.model_requests import thinking_request_options
from lab_agent.utils.scoring_notifications import collect_scoring_failures


ROOT = Path(__file__).resolve().parents[1]
MODELS = json.loads((ROOT / "lab_agent/config/models.json").read_text(encoding="utf-8"))


@pytest.fixture
def sdk():
    requests = []
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(200, json={
            "id": "fixture", "object": "chat.completion", "created": 0, "model": body["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content":
                "Score: 2\nReason: Relevant science\nKey Relevance: materials"}, "finish_reason": "stop"}],
        })
    client = OpenAI(api_key="test-only", base_url="https://example.invalid/v1", max_retries=0,
                    http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    yield client, requests
    client.close()


def scorer_with(client):
    scorer = object.__new__(PaperScorer)
    scorer.client = client
    scorer.logger = logging.getLogger("scorer-fixture")
    scorer.model_config = MODELS["arxivFilterModel"]
    scorer.prompt_template = "Rate this paper: {PAPER_INFO}"
    return scorer


def test_scoring_sdk_serializes_high_thinking_and_finite_budget(sdk):
    client, requests = sdk
    result = scorer_with(client)._score_single_paper({"title": "Fixture"})
    assert result["score"] == 2
    body = requests[0]
    assert body["reasoning_effort"] == "high"
    assert body["thinking"] == {"type": "enabled"}
    assert body["max_tokens"] == 16384
    assert "temperature" not in body


@pytest.mark.parametrize("chat_class", [ArxivChat, JournalChat])
def test_paper_chat_sdk_serializes_max_without_output_cap(sdk, chat_class):
    client, requests = sdk
    chat = object.__new__(chat_class)
    chat.client = client
    chat.logger = logging.getLogger("chat-fixture")
    chat.model_config = MODELS["chatModel"]
    chat.conversation_history = [{"role": "system", "content": "Fixture"}]
    chat.current_papers = []
    assert chat.chat("Explain this") ["success"]
    body = requests[0]
    assert body["reasoning_effort"] == "max"
    assert body["thinking"] == {"type": "enabled"}
    assert "max_tokens" not in body and "max_completion_tokens" not in body
    assert "temperature" not in body


def test_overview_uses_same_chat_options_without_hidden_fallback_cap():
    tree = ast.parse((ROOT / "lab_agent/web/app.py").read_text(encoding="utf-8-sig"))
    interface = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "deepseek_assistant_interface")
    requests = [n for n in ast.walk(interface) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "create"]
    assert len(requests) == 1
    call = requests[0]
    assert not {"max_tokens", "max_completion_tokens", "temperature"} & {k.arg for k in call.keywords}
    assert any(k.arg is None and isinstance(k.value, ast.Call)
               and isinstance(k.value.func, ast.Name) and k.value.func.id == "thinking_request_options"
               and any(a.arg == "scoring" and a.value.value is False for a in k.value.keywords)
               for k in call.keywords)


def test_chat_ignores_legacy_config_caps_and_defaults_to_max():
    options = thinking_request_options({"maxTokens": 50000, "temperature": 0.3}, scoring=False)
    assert options == {"reasoning_effort": "max", "extra_body": {"thinking": {"type": "enabled"}}}


@pytest.mark.parametrize("cap", [None, 0, -1, True, "16384"])
def test_scoring_cannot_silently_drop_its_budget(cap):
    with pytest.raises(ValueError):
        thinking_request_options({"maxTokens": cap}, scoring=True)


def test_scoring_budget_can_be_changed_in_configuration():
    assert thinking_request_options({"maxTokens": 8192}, scoring=True)["max_tokens"] == 8192


def test_bad_effort_fails_instead_of_silently_removing_parameter():
    with pytest.raises(ValueError):
        thinking_request_options({"reasoningEffort": "typo"}, scoring=False)


def test_length_truncation_is_failure_and_not_retried_or_saved_as_success():
    client = Mock()
    client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(
        finish_reason="length", message=SimpleNamespace(content="Score: 3\nReason: Incomplete"))])
    scorer = scorer_with(client)
    with collect_scoring_failures() as failures:
        output = scorer.score_papers([{"title": "Fixture"}])
    assert output[0]["reason"] == "Error in scoring - defaulted to low priority"
    assert failures.total == 1
    assert "ScoringTokenLimitError" in failures.samples[0]
    client.chat.completions.create.assert_called_once()


def test_empty_final_answer_is_an_error():
    client = Mock()
    client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(
        finish_reason="stop", message=SimpleNamespace(content=None))])
    with pytest.raises(ValueError, match="no final answer"):
        scorer_with(client)._score_single_paper({"title": "Fixture"})
