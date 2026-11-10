"""Offline period boundaries, shared-source aggregation, scheduling and UI checks."""

import asyncio
import ast
from datetime import date, timedelta
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

from lab_agent.tools.periodic_report_generator import PeriodicReportGenerator, article_key, period_window
from scripts import generate_daily_report as daily
from scripts.generate_periodic_reports import generate_due_reports


def write_day(root, source, day, papers, **extra):
    folder = "ArXiv_reports" if source == "arxiv" else "Journal_Summary_reports"
    path = root / folder / f"{day}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"date": day, "all_papers": papers, **extra}), encoding="utf-8")
    return path


def fill_week(root):
    start = date(2026, 10, 31)
    for offset in range(7):
        day = start + timedelta(days=offset)
        if day.weekday() < 5:
            write_day(root, "arxiv", day.isoformat(), [{"id": "2611.12345v1", "score": 3, "title": "Graphene study"}])
        write_day(root, "journal", day.isoformat(), [
            {"doi": "10.1000/example", "score": "3", "title": "Nickelate study", "journal": "Test Journal"},
            {"id": "low", "score": 1, "title": "Do not include low priority"},
            {"id": "medium", "score": 2, "title": "Do not include medium priority"},
        ])


def test_week_includes_saturday_and_generation_friday():
    assert period_window("weekly", date(2026, 11, 6)) == (
        "2026-11-06", date(2026, 10, 31), date(2026, 11, 6))


@pytest.mark.parametrize("anchor,start,end", [
    (date(2024, 3, 1), date(2024, 2, 1), date(2024, 2, 29)),
    (date(2026, 3, 1), date(2026, 2, 1), date(2026, 2, 28)),
    (date(2027, 1, 1), date(2026, 12, 1), date(2026, 12, 31)),
])
def test_month_boundaries_include_leap_and_year_change(anchor, start, end):
    assert period_window("monthly", anchor) == (start.strftime("%Y-%m"), start, end)


@pytest.mark.parametrize("kind,anchor", [("weekly", date(2026, 11, 10)), ("monthly", date(2026, 11, 2)), ("../bad", date(2026, 11, 6))])
def test_invalid_anchor_or_kind_refused(kind, anchor):
    with pytest.raises(ValueError):
        period_window(kind, anchor)


def test_priority_three_only_dedup_and_weekend_coverage(tmp_path):
    fill_week(tmp_path)
    original = (tmp_path / "Journal_Summary_reports/2026-11-06.json").read_bytes()
    generator = PeriodicReportGenerator(reports_root=tmp_path)
    report = generator.generate("weekly", "2026-11-06")
    summary = report["summary"]
    assert summary["total_priority_3"] == 2
    assert summary["arxiv_priority_3"] == summary["journal_priority_3"] == 1
    assert summary["priority_3_occurrences"] == 12
    assert summary["duplicate_occurrences_removed"] == 10
    assert summary["coverage_complete"]
    assert len(report["coverage"]) == 14
    assert sum(x["status"] == "scheduled_skip" for x in report["coverage"]) == 2
    assert {p["score"] for p in report["all_papers"]} == {3}
    assert (tmp_path / "Journal_Summary_reports/2026-11-06.json").read_bytes() == original
    assert generator.get_report("weekly", "2026-11-06") == report
    assert (tmp_path / "Weekly_reports/2026-11-06.html").exists()
    assert not list((tmp_path / "Weekly_reports").glob("*.tmp"))


def test_both_window_endpoints_and_outside_dates(tmp_path):
    for day in ("2026-10-30", "2026-10-31", "2026-11-06", "2026-11-07"):
        write_day(tmp_path, "journal", day, [{"id": day, "title": day, "score": 3}])
    report = PeriodicReportGenerator(reports_root=tmp_path).generate("weekly", "2026-11-06")
    assert {p["id"] for p in report["all_papers"]} == {"2026-10-31", "2026-11-06"}
    assert not report["summary"]["coverage_complete"]


@pytest.mark.parametrize("case", ["missing", "invalid_json", "wrong_date", "missing_journal", "scoring_failed"])
def test_incomplete_sources_cannot_be_claimed_complete(tmp_path, case):
    fill_week(tmp_path)
    path = tmp_path / "Journal_Summary_reports/2026-11-06.json"
    if case == "missing":
        path.unlink()
    elif case == "invalid_json":
        path.write_text("not-json")
    else:
        data = json.loads(path.read_text())
        if case == "wrong_date":
            data["date"] = "2020-01-01"
        elif case == "missing_journal":
            data["missing_journals"] = ["Test Journal"]
        else:
            data["all_papers"].append({"score": 1, "reason": "Error in scoring - defaulted to low priority"})
        path.write_text(json.dumps(data))
    result = PeriodicReportGenerator(reports_root=tmp_path).generate("weekly", "2026-11-06")
    assert not result["summary"]["coverage_complete"]
    assert any(x["date"] == "2026-11-06" and x["source"] == "journal" for x in result["summary"]["source_issues"])


def test_doi_cross_source_duplicate_and_arxiv_versions(tmp_path):
    write_day(tmp_path, "arxiv", "2026-11-06", [{"doi": "10.1000/EXAMPLE", "score": 3, "title": "One paper"}])
    write_day(tmp_path, "journal", "2026-11-06", [{"url": "https://doi.org/10.1000/example", "score": 3, "title": "One paper"}])
    report = PeriodicReportGenerator(reports_root=tmp_path).generate("weekly", "2026-11-06")
    assert report["summary"]["total_priority_3"] == 1
    assert report["all_papers"][0]["report_sources"] == ["arxiv", "journal"]
    assert article_key({"id": "2611.12345v2"}) == article_key({"url": "https://arxiv.org/abs/2611.12345v1"})


def test_grouped_json_utf8_and_html_safety(tmp_path):
    path = tmp_path / "Journal_Summary_reports/2026-11-06.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"papers_by_priority": {"3": [{"score": 3, "title": "中文<script>alert(1)</script>", "url": "javascript:alert(1)"}]}}))
    generator = PeriodicReportGenerator(reports_root=tmp_path)
    result = generator.generate("weekly", "2026-11-06")
    rendered = generator.render_html(result)
    assert "中文" in rendered
    assert "<script>" not in rendered and "javascript:" not in rendered
    assert generator.get_report("weekly", "../../outside") is None


def test_regeneration_updates_snapshot_and_no_duplicate_periods(tmp_path):
    fill_week(tmp_path)
    generator = PeriodicReportGenerator(reports_root=tmp_path)
    generator.generate("weekly", "2026-11-06")
    write_day(tmp_path, "journal", "2026-11-06", [{"id": "new", "title": "New paper", "score": 3}])
    assert generator.generate("weekly", "2026-11-06")["summary"]["total_priority_3"] == 3
    assert generator.list_reports("weekly") == ["2026-11-06"]


def test_friday_first_of_month_generates_both_and_non_due_day_writes_nothing(tmp_path):
    assert generate_due_reports("2026-11-10", reports_root=tmp_path) == []
    assert not list(tmp_path.iterdir())
    results = generate_due_reports("2027-01-01", reports_root=tmp_path)
    assert [r["period_type"] for r in results] == ["weekly", "monthly"]
    assert results[0]["end_date"] == "2027-01-01"
    assert results[1]["end_date"] == "2026-12-31"


@pytest.mark.parametrize("daily_error", [False, True])
def test_daily_finally_runs_periodic_after_daily_even_on_error(monkeypatch, daily_error):
    order = []
    async def generate(*args):
        order.append("daily")
        if daily_error:
            raise ValueError("fake")
        return 0
    def periodic(*args, **kwargs):
        order.append("periodic")
        return []
    monkeypatch.setattr(daily, "load_project_dotenv", lambda: None)
    monkeypatch.setattr(daily, "_generate_reports", generate)
    monkeypatch.setattr(daily, "generate_due_reports", periodic)
    monkeypatch.setattr(daily, "notify_scoring_failures", lambda *a: None)
    args = SimpleNamespace(date="2026-11-06")
    if daily_error:
        with pytest.raises(ValueError):
            asyncio.run(daily.run_once(args))
    else:
        assert asyncio.run(daily.run_once(args)) == 0
    assert order == ["daily", "periodic"]


def test_skip_periodic_flag(monkeypatch):
    async def generate(*args): return 0
    monkeypatch.setattr(daily, "load_project_dotenv", lambda: None)
    monkeypatch.setattr(daily, "_generate_reports", generate)
    monkeypatch.setattr(daily, "notify_scoring_failures", lambda *a: None)
    periodic = Mock()
    monkeypatch.setattr(daily, "generate_due_reports", periodic)
    assert asyncio.run(daily.run_once(SimpleNamespace(date="2026-11-06", skip_periodic_reports=True))) == 0
    periodic.assert_not_called()


def test_periodic_incomplete_makes_daily_run_nonzero(monkeypatch):
    async def generate(*args): return 0
    monkeypatch.setattr(daily, "load_project_dotenv", lambda: None)
    monkeypatch.setattr(daily, "_generate_reports", generate)
    monkeypatch.setattr(daily, "notify_scoring_failures", lambda *a: None)
    monkeypatch.setattr(daily, "generate_due_reports", lambda *a, **k: [{"summary": {"coverage_complete": False}}])
    assert asyncio.run(daily.run_once(SimpleNamespace(date="2026-11-06"))) == 1


def test_navigation_and_generation_access_are_wired_after_auth():
    tree = ast.parse((Path(__file__).parents[1] / "lab_agent/web/app.py").read_text(encoding="utf-8-sig"))
    navigation = next(n.value for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "NAV_ITEMS" for t in n.targets))
    assert {"Weekly Reports", "Monthly Reports"} <= set(ast.literal_eval(navigation))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    auth = next(n for n in ast.walk(main) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "require_current_user")
    display = next(n for n in ast.walk(main) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "periodic_reports_interface")
    assert auth.lineno < display.lineno
    access = next(k.value for k in display.keywords if k.arg == "can_generate")
    assert isinstance(access, ast.Call) and access.func.id == "is_admin"


def test_viewer_ui_source_filter_and_search(tmp_path):
    fill_week(tmp_path)
    generator = PeriodicReportGenerator(reports_root=tmp_path)
    generator.generate("weekly", "2026-11-06")
    app = AppTest.from_string(
        "from lab_agent.web.periodic_reports import periodic_reports_interface\n"
        "from lab_agent.tools.periodic_report_generator import PeriodicReportGenerator\n"
        f"periodic_reports_interface('weekly', generator=PeriodicReportGenerator(reports_root={str(tmp_path)!r}))\n"
    ).run()
    assert not app.exception
    assert app.metric[0].value == "2"
    assert not app.button  # Ordinary viewers cannot generate/rebuild.
    app.selectbox(key="weekly_source").select("Journals").run()
    assert not app.exception
    markdown = "\n".join(item.value for item in app.markdown)
    assert "Nickelate study" in markdown and "Graphene study" not in markdown
    app.text_input(key="weekly_query").input("no-match").run()
    assert any("No Priority 3 articles match" in item.value for item in app.info)


def test_monthly_ui_empty_and_incomplete_states(tmp_path):
    script = ("from lab_agent.web.periodic_reports import periodic_reports_interface\n"
              "from lab_agent.tools.periodic_report_generator import PeriodicReportGenerator\n"
              f"periodic_reports_interface('monthly', generator=PeriodicReportGenerator(reports_root={str(tmp_path)!r}))\n")
    empty = AppTest.from_string(script).run()
    assert not empty.exception and any("No monthly reports" in item.value for item in empty.info)
    PeriodicReportGenerator(reports_root=tmp_path).generate("monthly", "2026-11-01")
    incomplete = AppTest.from_string(script).run(timeout=15)
    assert not incomplete.exception and incomplete.warning
    assert incomplete.metric[0].value == "0"


def test_admin_can_rebuild_a_completed_week_without_llm(tmp_path, monkeypatch):
    from lab_agent.web import periodic_reports as page
    monkeypatch.setattr(page, "today_str", lambda: "2026-11-10")
    fill_week(tmp_path)
    script = ("from lab_agent.web.periodic_reports import periodic_reports_interface\n"
              "from lab_agent.tools.periodic_report_generator import PeriodicReportGenerator\n"
              f"periodic_reports_interface('weekly', can_generate=True, generator=PeriodicReportGenerator(reports_root={str(tmp_path)!r}))\n")
    app = AppTest.from_string(script).run(timeout=15)
    assert not app.exception
    app.button(key="weekly_generate").click().run(timeout=15)
    assert not app.exception
    assert app.metric[0].value == "2"
    assert (tmp_path / "Weekly_reports/2026-11-06.json").exists()
