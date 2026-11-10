"""Shared weekly/monthly report UI; private chat and authentication stay separate."""

import calendar
from datetime import date, timedelta
import json
import math

import streamlit as st

from lab_agent.tools.periodic_report_generator import PeriodicReportGenerator, safe_url
from lab_agent.utils import today_str


def periodic_reports_interface(kind, *, can_generate=False, generator=None):
    generator = generator or PeriodicReportGenerator()
    label = "Weekly" if kind == "weekly" else "Monthly"
    st.caption("Priority 3 only · ArXiv + Journals · Deduplicated by article identity")
    st.caption("Periods follow source daily-report dates; journal entries may have an earlier publication date.")
    if can_generate:
        with st.expander(f"Generate or rebuild {label.lower()} report", expanded=False):
            today = date.fromisoformat(today_str())
            if kind == "weekly":
                default = today - timedelta(days=(today.weekday() - 4) % 7)
                selected = st.date_input("Week ending (Friday)", value=default, max_value=today, key="weekly_generation_date")
                anchor = selected
                valid = selected.weekday() == 4
                if not valid:
                    st.info("Choose a Friday. The report includes the preceding Saturday through that Friday.")
            else:
                default = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
                selected = st.date_input("Any day in the target month", value=default,
                                         max_value=today.replace(day=1) - timedelta(days=1), key="monthly_generation_date")
                anchor = selected.replace(day=1) + timedelta(days=calendar.monthrange(selected.year, selected.month)[1])
                valid = True
            if st.button(f"Generate / rebuild {label.lower()} report", disabled=not valid, key=f"{kind}_generate"):
                try:
                    with st.spinner("Collecting Priority 3 articles from saved reports..."):
                        generator.generate(kind, anchor)
                    st.rerun()
                except (OSError, ValueError):
                    st.error("Unable to generate this period. Check the selected dates and report directory access.")

    keys = generator.list_reports(kind)
    if not keys:
        st.info(f"No {label.lower()} reports yet. Reports are generated after the Friday / month-start daily run. An administrator can also generate a past period.")
        return
    key = st.selectbox("Week ending" if kind == "weekly" else "Month", keys, key=f"{kind}_selected_report")
    try:
        report = generator.get_report(kind, key)
        summary = report["summary"]
        papers = report["all_papers"]
    except (OSError, ValueError, TypeError, KeyError):
        st.error("This report is unavailable or damaged. Ask an administrator to rebuild it.")
        return
    st.subheader(f"{report['start_date']} → {report['end_date']}")
    st.caption(f"Generated: {report['generation_time']}")
    columns = st.columns(3)
    columns[0].metric("Unique Priority 3 articles", summary["total_priority_3"])
    columns[1].metric("ArXiv", summary["arxiv_priority_3"])
    columns[2].metric("Journals", summary["journal_priority_3"])
    st.caption(f"{summary['duplicate_occurrences_removed']} repeated entries removed. Cross-source articles can appear in both source counts.")
    if not summary["coverage_complete"]:
        st.warning("Some daily sources are missing, damaged, or incomplete. This report may not include every Priority 3 article.")
        with st.expander("Source coverage issues"):
            st.dataframe(summary["source_issues"], use_container_width=True, hide_index=True)
    downloads = st.columns(2)
    downloads[0].download_button("Download HTML", generator.render_html(report), file_name=f"{kind}_{key}.html", mime="text/html", key=f"{kind}_html")
    downloads[1].download_button("Download JSON", json.dumps(report, indent=2, ensure_ascii=False), file_name=f"{kind}_{key}.json", mime="application/json", key=f"{kind}_json")
    filters = st.columns([1, 2])
    source = filters[0].selectbox("Source", ["All sources", "ArXiv", "Journals"], key=f"{kind}_source")
    query = filters[1].text_input("Search title, author, journal or assessment", key=f"{kind}_query").strip().casefold()
    source_key = {"ArXiv": "arxiv", "Journals": "journal"}.get(source)
    visible = [p for p in papers if (not source_key or source_key in p["report_sources"]) and
               (not query or query in " ".join(str(p.get(k, "")) for k in ("title", "authors", "journal", "reason", "abstract")).casefold())]
    if not visible:
        st.info("No Priority 3 articles match this selection.")
        return
    page_count = max(1, math.ceil(len(visible) / 25))
    # Page choices reset naturally when source/search filters change.
    page = st.selectbox("Page", list(range(1, page_count + 1)), key=f"{kind}_page")
    st.caption(f"{len(visible)} matching articles · Page {page}/{page_count}")
    for paper in visible[(page - 1) * 25:page * 25]:
        with st.container(border=True):
            st.markdown("**" + str(paper.get("title", "Untitled")) + "**")
            st.caption(" · ".join([", ".join(paper["report_sources"]), str(paper.get("journal") or ""), "Daily reports: " + ", ".join(paper["report_dates"])]))
            st.write(str(paper.get("authors", "")))
            st.write(str(paper.get("reason", "")))
            with st.expander("Abstract and keywords"):
                st.write(str(paper.get("abstract", "")))
                st.caption(str(paper.get("key_relevance", "")))
            for field, button_label in (("url", "Open article"), ("pdf_url", "Open PDF")):
                url = safe_url(paper.get(field))
                if url:
                    st.link_button(button_label, url)
