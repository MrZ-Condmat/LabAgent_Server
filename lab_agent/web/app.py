import streamlit as st
import asyncio
import calendar
import nest_asyncio
from datetime import datetime
import inspect
import json
from typing import Any, Dict

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from lab_agent.main import LabAgent
from lab_agent.utils import Config, load_project_dotenv, now, project_path_str, today_str
from lab_agent.agents.arxiv_daily_agent import ArxivDailyAgent
from lab_agent.agents.journal_daily_agent import JournalDailyAgent
from lab_agent.tools.arxiv_chat import ArxivChat
from lab_agent.tools.journal_chat import JournalChat
from lab_agent.tools.highlights_report_generator import HighlightsReportGenerator
from lab_agent.web.auth import require_current_user, render_authenticated_identity
from lab_agent.web.admin import admin_interface
from lab_agent.auth.authorization import is_admin
from lab_agent.auth.models import CurrentUser
from lab_agent.db.models import ConversationType
from lab_agent.services.chat_conversations import model_history
from lab_agent.web.chat_context import arxiv_context, journal_context
from lab_agent.web.chat_ui import render_paper_chat, select_workspace
from lab_agent.web.chat_reports import load_arxiv_papers, load_journal_papers
from openai import OpenAI

nest_asyncio.apply()

NAV_ITEMS = [
    "Overview",
    "ArXiv Daily",
    "Journal Daily",
    "Database",
    "Logs",
]


def render_sidebar_navigation(current_user: CurrentUser) -> str:
    visible_items = [*NAV_ITEMS]
    if is_admin(current_user):
        visible_items.append("Admin")
    if "active_nav" not in st.session_state:
        st.session_state.active_nav = "Overview"

    current = st.session_state.active_nav
    if current not in visible_items:
        current = "Overview"
        st.session_state.active_nav = current

    with st.sidebar:
        st.markdown(
            """
            <div class="sidebar-brand">
                <div class="brand-mark">LA</div>
                <div>
                    <div class="brand-title">Lab Agent</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        for item in visible_items:
            if item == current:
                st.markdown(
                    f'<div class="nav-card nav-card-active"><span class="nav-dot"></span><span>{item}</span></div>',
                    unsafe_allow_html=True,
                )
            elif st.button(item, key=f"nav_{item.replace(' ', '_').lower()}", use_container_width=True):
                st.session_state.active_nav = item
                st.rerun()


    return current


def render_page_header(title: str, subtitle: str = "") -> None:
    subtitle_html = f'<div class="page-subtitle">{subtitle}</div>' if subtitle else ""
    st.markdown(
        f"""
        <div class="page-header-card">
            <div class="page-kicker">Lab Agent System</div>
            <div class="page-title">{title}</div>
            {subtitle_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def normalize_report_dates(reports) -> list[str]:
    return sorted([str(report) for report in reports], reverse=True)


def report_matches_query(date: str, query: str) -> bool:
    query = (query or "").strip().lower()
    if not query:
        return True

    normalized_date = date.lower()
    compact_date = normalized_date.replace("-", "").replace("/", "")
    compact_query = query.replace("-", "").replace("/", "").replace(" ", "")
    return query in normalized_date or compact_query in compact_date


def parse_report_date(date_value: str):
    try:
        return datetime.strptime(str(date_value), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def add_months(year: int, month: int, offset: int) -> tuple[int, int]:
    month_index = (year * 12 + month - 1) + offset
    return month_index // 12, month_index % 12 + 1


def report_calendar_month_default(reports: list[str], selected_date: str = "") -> str:
    base_date = parse_report_date(selected_date)
    if base_date is None and reports:
        base_date = parse_report_date(reports[0])
    if base_date is None:
        base_date = now().date()
    return base_date.strftime("%Y-%m")


def render_report_calendar(reports, key_prefix: str) -> str:
    reports = normalize_report_dates(reports)
    available_dates = {date for date in reports if parse_report_date(date) is not None}
    selected_key = f"{key_prefix}_selected_report_date"
    month_key = f"{key_prefix}_calendar_month"

    selected_date = st.session_state.get(selected_key, "")
    if selected_date and selected_date not in available_dates:
        selected_date = ""
        st.session_state[selected_key] = ""

    if month_key not in st.session_state:
        st.session_state[month_key] = report_calendar_month_default(reports, selected_date)

    try:
        year, month = [int(part) for part in st.session_state[month_key].split("-", 1)]
    except (ValueError, AttributeError):
        st.session_state[month_key] = report_calendar_month_default(reports, selected_date)
        year, month = [int(part) for part in st.session_state[month_key].split("-", 1)]

    button_label = selected_date or "Select date"
    popover = getattr(st, "popover", None)

    def render_calendar_body() -> None:
        nav_cols = st.columns([0.2, 0.6, 0.2])
        with nav_cols[0]:
            if st.button("<", key=f"{key_prefix}_prev_month", use_container_width=True):
                prev_year, prev_month = add_months(year, month, -1)
                st.session_state[month_key] = f"{prev_year:04d}-{prev_month:02d}"
                st.rerun()
        with nav_cols[1]:
            st.markdown(f'<div class="calendar-month-title">{year:04d}-{month:02d}</div>', unsafe_allow_html=True)
        with nav_cols[2]:
            if st.button(">", key=f"{key_prefix}_next_month", use_container_width=True):
                next_year, next_month = add_months(year, month, 1)
                st.session_state[month_key] = f"{next_year:04d}-{next_month:02d}"
                st.rerun()

        weekday_cols = st.columns(7)
        for col, label in zip(weekday_cols, ["M", "T", "W", "T", "F", "S", "S"]):
            with col:
                st.markdown(f'<div class="calendar-weekday">{label}</div>', unsafe_allow_html=True)

        for week in calendar.Calendar(firstweekday=0).monthdatescalendar(year, month):
            day_cols = st.columns(7)
            for col, day in zip(day_cols, week):
                with col:
                    if day.month != month:
                        st.markdown('<div class="calendar-day-empty"></div>', unsafe_allow_html=True)
                        continue

                    iso_day = day.strftime("%Y-%m-%d")
                    if iso_day in available_dates:
                        label = str(day.day)
                        if iso_day == selected_date:
                            label = f"[{day.day}]"
                        if st.button(label, key=f"{key_prefix}_calendar_day_{iso_day}", use_container_width=True):
                            st.session_state[selected_key] = iso_day
                            st.session_state[month_key] = f"{year:04d}-{month:02d}"
                            st.rerun()
                    else:
                        st.markdown(f'<div class="calendar-day-disabled">{day.day}</div>', unsafe_allow_html=True)

        if selected_date:
            if st.button("Clear date", key=f"{key_prefix}_clear_calendar_date", use_container_width=True):
                st.session_state[selected_key] = ""
                st.rerun()

    if popover is not None:
        with popover(button_label, use_container_width=True):
            render_calendar_body()
    else:
        with st.expander(button_label, expanded=False):
            render_calendar_body()

    return st.session_state.get(selected_key, "")



def render_report_collection(
    reports,
    selected_date: str,
    key_prefix: str,
    title_for_date,
    render_report,
    empty_message: str,
) -> None:
    reports = normalize_report_dates(reports)
    filtered_reports = [date for date in reports if report_matches_query(date, selected_date)]

    if not reports:
        st.info(empty_message)
        return

    if selected_date.strip():
        st.caption(f"Selected report date: {selected_date}")
        if not filtered_reports:
            st.info("No report found for the selected date.")
            return
        for date in filtered_reports:
            with st.expander(title_for_date(date), expanded=True):
                render_report(date)
        return

    recent_reports = reports[:5]
    older_reports = reports[5:]

    for date in recent_reports:
        with st.expander(title_for_date(date), expanded=False):
            render_report(date)

    if older_reports:
        with st.expander("Previous Reports", expanded=False):
            selected_date = st.selectbox(
                "Choose an earlier report",
                older_reports,
                key=f"{key_prefix}_older_report_selector",
            )
            if selected_date:
                render_report(selected_date)



def journal_config_signature():
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "config",
        "journal_feeds.json",
    )
    try:
        stat = os.stat(config_path)
        return (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return None


def journal_agent_needs_refresh(agent) -> bool:
    if agent is None:
        return True
    required_attrs = ("_list_summary_reports", "_get_summary_report", "summary_generator")
    return any(not hasattr(agent, attr) for attr in required_attrs)


def paper_chat_needs_refresh(chat_obj) -> bool:
    """Detect stale Streamlit chat objects created before date-aware context support."""
    if chat_obj is None:
        return True
    if not hasattr(chat_obj, "current_context_label"):
        return True
    if not hasattr(chat_obj, "set_papers_context"):
        return True

    try:
        # Bound method signature should expose both papers and optional context_label.
        return len(inspect.signature(chat_obj.set_papers_context).parameters) < 2
    except (TypeError, ValueError):
        return True


def get_date_aware_suggestions(chat_obj, date_label: str, source: str) -> list[str]:
    if date_label == "today":
        return chat_obj.get_suggested_questions()

    total = len(getattr(chat_obj, "current_papers", []) or [])
    if source == "journal":
        if total:
            return [
                f"What are the most interesting journal papers from {date_label}?",
                "Summarize the high-priority journal papers for me",
                "Which papers are most relevant to superconductivity or strong correlation?",
                f"What experimental techniques are featured in the {date_label} journal report?",
            ]
        return [
            f"What journal papers are available for {date_label}?",
            "Help me understand the latest journal highlights",
        ]

    if total:
        return [
            f"What are the most interesting ArXiv papers from {date_label}?",
            "Summarize the high-priority papers for me",
            "Which papers are related to 2D materials and graphene?",
            f"Tell me about any superconductivity papers from the {date_label} report",
        ]
    return [
        f"What ArXiv papers are available for {date_label}?",
        "Help me understand the latest trends in 2D materials",
    ]


def render_toggleable_html_report(html_content: str, state_key: str, height: int = 700) -> None:
    if st.button("View HTML Report", key=f"open_{state_key}"):
        render_html_report_dialog(html_content, state_key=state_key, height=height)


def render_html_report_dialog(html_content: str, state_key: str, height: int = 700) -> None:
    dialog = getattr(st, "dialog", None) or getattr(st, "experimental_dialog", None)

    if dialog is None:
        st.warning("This Streamlit version does not support modal dialogs. Showing the report inline instead.")
        st.components.v1.html(html_content, height=height, scrolling=True)
        return

    def dialog_body():
        st.components.v1.html(html_content, height=height, scrolling=True)

    try:
        dialog_fn = dialog("HTML Report", width="large")(dialog_body)
    except TypeError:
        dialog_fn = dialog("HTML Report")(dialog_body)

    dialog_fn()


def ensure_sidebar_expanded() -> None:
    st.components.v1.html(
        """
        <script>
        (function restoreSidebar() {
            function clickOpenControl() {
                const doc = window.parent && window.parent.document;
                if (!doc) return;

                const sidebar = doc.querySelector('section[data-testid="stSidebar"]');
                const sidebarVisible = sidebar && sidebar.getBoundingClientRect().width > 40;
                if (sidebarVisible) return;

                const selectors = [
                    '[data-testid="collapsedControl"] button',
                    '[data-testid="collapsedControl"]',
                    'button[title="Open sidebar"]',
                    'button[title="Expand sidebar"]',
                    'button[aria-label="Open sidebar"]',
                    'button[aria-label="Expand sidebar"]'
                ];

                for (const selector of selectors) {
                    const target = doc.querySelector(selector);
                    if (target) {
                        target.click();
                        break;
                    }
                }
            }

            clickOpenControl();
            window.setTimeout(clickOpenControl, 250);
            window.setTimeout(clickOpenControl, 1000);
        })();
        </script>
        """,
        height=0,
        width=0,
    )


def main():
    load_project_dotenv()
    config = Config()
    
    st.set_page_config(
        page_title="Lab Agent System",
        page_icon=":material/science:",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    current_user = require_current_user(config=config)
    render_authenticated_identity(current_user, config=config)

    apply_overview_styles()
    ensure_sidebar_expanded()

    # Initialize agents in session state
    if "agent" not in st.session_state:
        st.session_state.agent = LabAgent()
    
    if "arxiv_agent" not in st.session_state:
        try:
            st.session_state.arxiv_agent = ArxivDailyAgent({
                'reports_dir': project_path_str('reports/ArXiv_reports')
            })
            asyncio.run(st.session_state.arxiv_agent.initialize())
        except Exception as e:
            st.error(f"Failed to initialize ArXiv agent: {e}")
            st.session_state.arxiv_agent = None
    
    # Initialize Journal Daily agent. Rebuild stale session objects from older code versions
    # or when journal_feeds.json changes.
    current_journal_config_signature = journal_config_signature()
    journal_agent = st.session_state.get("journal_agent")
    if (
        journal_agent_needs_refresh(journal_agent)
        or st.session_state.get("journal_config_signature") != current_journal_config_signature
    ):
        try:
            if journal_agent is not None and hasattr(journal_agent, "cleanup"):
                try:
                    asyncio.run(journal_agent.cleanup())
                except Exception:
                    pass
            st.session_state.journal_agent = JournalDailyAgent({
                'reports_base_dir': project_path_str('reports')
            })
            asyncio.run(st.session_state.journal_agent.initialize())
            st.session_state.journal_config_signature = current_journal_config_signature
        except Exception as e:
            st.error(f"Failed to initialize Journal Daily agent: {e}")
            st.session_state.journal_agent = None
    
    # Initialize or rebuild ArXiv chat. Streamlit can retain old class instances
    # across code reloads, so detect stale objects before using date-aware context.
    if "arxiv_chat" not in st.session_state or paper_chat_needs_refresh(st.session_state.get("arxiv_chat")):
        try:
            st.session_state.arxiv_chat = ArxivChat()
        except Exception as e:
            st.error(f"Failed to initialize ArXiv chat: {e}")
            st.session_state.arxiv_chat = None

    # Initialize or rebuild Journal Daily chat for date-aware context support.
    if "journal_chat" not in st.session_state or paper_chat_needs_refresh(st.session_state.get("journal_chat")):
        try:
            st.session_state.journal_chat = JournalChat()
        except Exception as e:
            st.error(f"Failed to initialize Journal Daily chat: {e}")
            st.session_state.journal_chat = None
    
    # Old in-memory chat histories are never authoritative after this migration.
    for old_key in (
        "chat_messages", "journal_chat_messages", "overview_chat_messages",
        "overview_chat_history", "arxiv_chat_context_key", "journal_chat_context_key",
        "deepseek_assistant_error", "deepseek_assistant",
    ):
        st.session_state.pop(old_key, None)
    
    active_page = render_sidebar_navigation(current_user)

    if active_page == "Overview":
        overview_interface(config, current_user)
    elif active_page == "ArXiv Daily":
        arxiv_daily_interface(current_user)
    elif active_page == "Journal Daily":
        journal_daily_interface(current_user)
    elif active_page == "Database":
        database_interface()
    elif active_page == "Logs":
        logs_interface()
    elif active_page == "Admin":
        admin_interface(current_user, render_page_header)


def database_interface():
    render_page_header("Database", "Knowledge base conversation workspace.")
    st.components.v1.html(
        """
        <style>
            :root {
                --accent: rgb(102, 8, 116);
                --ink: #191724;
                --muted: #70707f;
                --line: #e7d6ec;
            }
            body {
                margin: 0;
                background: transparent;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            }
            .database-frame-card {
                box-sizing: border-box;
                width: 100%;
                min-height: 735px;
                padding: 14px;
                border: 1px solid rgba(102, 8, 116, 0.12);
                border-radius: 24px;
                background: rgba(255, 255, 255, 0.88);
                box-shadow: 0 20px 50px rgba(25, 23, 36, 0.08);
            }
            .database-frame-head {
                display: flex;
                justify-content: space-between;
                align-items: center;
                gap: 16px;
                padding: 4px 4px 14px;
            }
            .database-frame-title {
                color: var(--ink);
                font-size: 1.05rem;
                font-weight: 800;
                line-height: 1.2;
            }
            .database-frame-note {
                color: var(--muted);
                font-size: 0.86rem;
            }
            .database-frame-shell {
                height: 700px;
                overflow: hidden;
                border: 1px solid rgba(102, 8, 116, 0.10);
                border-radius: 18px;
                background: #fff;
            }
            .database-frame-shell iframe {
                display: block;
                width: 100%;
                height: 100%;
                min-height: 700px;
                border: 0;
            }
            @media (max-width: 760px) {
                .database-frame-card { padding: 10px; border-radius: 18px; }
                .database-frame-head { align-items: flex-start; flex-direction: column; gap: 4px; }
            }
        </style>
        <div class="database-frame-card">
            <div class="database-frame-head">
                <div class="database-frame-title">Knowledge Base Chat</div>
                <div class="database-frame-note">Embedded research knowledge assistant</div>
            </div>
            <div class="database-frame-shell">
                <iframe
                    src="http://166.111.26.183:8080/chat/K5FEfFUZ0b0gWgrl"
                    style="width: 100%; height: 100%; min-height: 700px"
                    frameborder="0"
                    allow="microphone;clipboard-write">
                </iframe>
            </div>
        </div>
        """,
        height=760,
        scrolling=False,
    )


def logs_interface():
    render_page_header("Logs", "System messages and runtime notes.")
    st.text_area("Logs", "System initialized successfully...", height=220)


def apply_overview_styles():
    st.markdown(
        """
        <style>
        :root {
            --accent: rgb(102, 8, 116);
            --accent-dark: #2a1032;
            --accent-soft: #f3e8f6;
            --accent-line: #e7d6ec;
            --ink: #191724;
            --muted: #70707f;
            --surface: #ffffff;
            --surface-soft: #f8f6fa;
            --line: #e7e4ed;
        }

        .stApp {
            background: radial-gradient(circle at top right, rgba(102, 8, 116, 0.08), transparent 34rem), #f7f4f8;
            color: var(--ink);
        }

        header[data-testid="stHeader"] {
            background: transparent !important;
            box-shadow: none !important;
        }

        div[data-testid="stToolbar"],
        div[data-testid="stDecoration"],
        div[data-testid="stStatusWidget"],
        div[data-testid="stDeployButton"],
        #MainMenu,
        footer {
            display: none !important;
            visibility: hidden !important;
            height: 0 !important;
        }

        [data-testid="stSidebarCollapseButton"],
        [data-testid="stSidebarCollapseButton"] *,
        section[data-testid="stSidebar"] [data-testid="stBaseButton-header"],
        section[data-testid="stSidebar"] button[kind="header"],
        section[data-testid="stSidebar"] button[title="Close sidebar"],
        section[data-testid="stSidebar"] button[title="Collapse sidebar"],
        section[data-testid="stSidebar"] button[aria-label="Close sidebar"],
        section[data-testid="stSidebar"] button[aria-label="Collapse sidebar"] {
            display: none !important;
            visibility: hidden !important;
            pointer-events: none !important;
        }

        .block-container {
            max-width: 1580px;
            padding: 1.1rem 2rem 3rem 2rem;
        }

        h1, h2, h3, h4, h5, h6 {
            color: var(--ink);
            letter-spacing: 0;
        }

        section[data-testid="stSidebar"] {
            min-width: 168px !important;
            max-width: 168px !important;
            width: 168px !important;
            left: 0 !important;
            transform: translateX(0) !important;
            visibility: visible !important;
            background: linear-gradient(180deg, #fbf9fc 0%, #efe8f2 100%);
            border-right: 1px solid rgba(102, 8, 116, 0.10);
            box-shadow: 18px 0 45px rgba(25, 23, 36, 0.06);
        }

        section[data-testid="stSidebar"] .block-container {
            padding: 1.25rem 0.55rem 1rem 0.55rem;
        }

        .sidebar-brand {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            padding: 0.15rem 0.15rem 1.1rem 0.15rem;
        }

        .brand-mark {
            width: 34px;
            height: 34px;
            border-radius: 12px;
            display: grid;
            place-items: center;
            background: linear-gradient(135deg, var(--accent), #28102f);
            color: #fff;
            font-weight: 800;
            box-shadow: 0 18px 35px rgba(102, 8, 116, 0.26);
        }

        .brand-title {
            color: var(--ink);
            font-size: 0.9rem;
            font-weight: 800;
            line-height: 1.1;
        }

        .nav-card {
            min-height: 2.85rem;
            border-radius: 14px;
            display: flex;
            align-items: center;
            gap: 0.45rem;
            padding: 0 0.65rem;
            margin: 0.25rem 0;
            font-size: 0.88rem;
            font-weight: 700;
        }

        .nav-card-active {
            color: #fff;
            background: rgb(102, 8, 116);
            box-shadow: 0 18px 38px rgba(102, 8, 116, 0.25);
        }

        .nav-dot {
            width: 8px;
            height: 8px;
            border-radius: 999px;
            background: rgba(255, 255, 255, 0.9);
            box-shadow: 0 0 0 6px rgba(255, 255, 255, 0.12);
        }

        section[data-testid="stSidebar"] div.stButton > button {
            min-height: 2.85rem;
            border-radius: 14px;
            border: 1px solid transparent;
            background: transparent;
            color: #6e6874;
            font-weight: 650;
            justify-content: flex-start;
            padding-left: 0.65rem;
            transition: all 140ms ease;
        }

        section[data-testid="stSidebar"] div.stButton > button:hover {
            background: #fff;
            border-color: var(--accent-line);
            color: var(--accent);
            transform: translateX(3px);
            box-shadow: 0 12px 28px rgba(25, 23, 36, 0.08);
        }

        .page-header-card {
            border-radius: 24px;
            padding: 1.6rem 1.8rem;
            margin-bottom: 1.4rem;
            color: #fff;
            background: linear-gradient(135deg, #191724 0%, #3b1647 48%, rgb(102, 8, 116) 100%);
            box-shadow: 0 24px 60px rgba(31, 16, 39, 0.22);
        }

        .page-kicker {
            color: rgba(255, 255, 255, 0.68);
            font-size: 0.68rem;
            font-weight: 700;
            text-transform: uppercase;
            margin-bottom: 0.4rem;
        }

        .page-title {
            color: #fff;
            font-size: 2.15rem;
            font-weight: 850;
            line-height: 1.12;
        }

        .page-subtitle {
            color: rgba(255, 255, 255, 0.76);
            margin-top: 0.55rem;
            font-size: 1rem;
            max-width: 780px;
        }

        .overview-panel,
        .report-toolbar,
        .empty-state-card {
            border: 1px solid rgba(102, 8, 116, 0.10);
            border-radius: 18px;
            padding: 18px 20px;
            background: rgba(255, 255, 255, 0.88);
            margin-bottom: 18px;
            box-shadow: 0 14px 34px rgba(25, 23, 36, 0.06);
        }

        .overview-kicker {
            color: var(--muted);
            font-size: 0.88rem;
            margin-bottom: 6px;
        }

        .overview-title {
            color: var(--ink);
            font-size: 1.65rem;
            font-weight: 850;
            margin-bottom: 6px;
        }

        .overview-muted { color: var(--muted); }

        .status-line {
            display: flex;
            gap: 10px;
            align-items: center;
            padding: 12px 0;
            border-bottom: 1px solid #f0edf4;
        }

        .status-line:last-child { border-bottom: 0; }
        .status-name { font-weight: 800; color: var(--ink); min-width: 116px; }
        .status-meta { color: var(--muted); }

        div[data-testid="stMetric"] {
            background: rgba(255, 255, 255, 0.9);
            border: 1px solid rgba(102, 8, 116, 0.10);
            border-radius: 18px;
            padding: 14px 16px;
            box-shadow: 0 12px 30px rgba(25, 23, 36, 0.05);
        }

        div.stButton > button {
            border-radius: 14px;
            border: 1px solid #ded8e6;
            background: rgba(255, 255, 255, 0.88);
            color: var(--ink);
            font-weight: 650;
            transition: all 140ms ease;
        }

        div.stButton > button:hover {
            border-color: var(--accent);
            color: var(--accent);
            transform: translateY(-1px);
            box-shadow: 0 12px 28px rgba(102, 8, 116, 0.13);
        }

        div[data-testid="stFormSubmitButton"] button {
            white-space: nowrap;
            min-width: 76px;
        }

        div[data-testid="stTextInput"] input,
        div[data-testid="stSelectbox"] div[data-baseweb="select"] > div {
            border-radius: 15px;
            border-color: transparent;
            background: rgba(255, 255, 255, 0.92);
            box-shadow: inset 0 0 0 1px rgba(102, 8, 116, 0.10);
        }

        div[data-testid="stTextInput"] input:focus,
        div[data-testid="stSelectbox"] div[data-baseweb="select"] > div:focus-within {
            border-color: var(--accent);
            box-shadow: 0 0 0 3px rgba(102, 8, 116, 0.12);
        }

        div[data-testid="stExpander"] {
            border: 1px solid rgba(102, 8, 116, 0.10);
            border-radius: 18px;
            overflow: hidden;
            background: rgba(255, 255, 255, 0.9);
            box-shadow: 0 10px 28px rgba(25, 23, 36, 0.05);
        }

        div[data-testid="stExpander"] summary {
            font-weight: 750;
            color: var(--ink);
            background: rgba(255, 255, 255, 0.66);
        }

        div[data-testid="stVerticalBlockBorderWrapper"] {
            border-radius: 22px;
            background: linear-gradient(180deg, rgba(255, 255, 255, 0.96), rgba(245, 239, 247, 0.92));
            border-color: rgba(102, 8, 116, 0.12) !important;
            box-shadow: 0 22px 55px rgba(25, 23, 36, 0.10);
        }

        .section-caption {
            color: var(--muted);
            font-size: 0.92rem;
            margin-top: -0.35rem;
            margin-bottom: 0.8rem;
        }

        .chat-notice {
            border-radius: 16px;
            border: 1px solid rgba(102, 8, 116, 0.12);
            background: linear-gradient(135deg, rgba(102, 8, 116, 0.10), rgba(255, 255, 255, 0.72));
            color: var(--accent-dark);
            padding: 0.95rem 1rem;
            margin: 0.85rem 0 1rem 0;
            line-height: 1.45;
            font-weight: 600;
        }

        .journal-footer-note {
            color: var(--muted);
            font-size: 0.9rem;
            line-height: 1.55;
            margin-top: 1.25rem;
            padding-top: 1rem;
            border-top: 1px solid rgba(102, 8, 116, 0.10);
        }

        .journal-footer-note div + div { margin-top: 0.45rem; }

        .compact-action button {
            min-height: 2.45rem;
        }

        div[data-baseweb="popover"] li:first-child { font-weight: 800; }

        .calendar-month-title {
            text-align: center;
            font-weight: 800;
            color: var(--accent-dark);
            padding-top: 0.45rem;
        }

        .calendar-weekday {
            text-align: center;
            color: var(--muted);
            font-size: 0.72rem;
            font-weight: 800;
            padding: 0.25rem 0;
        }

        .calendar-day-empty,
        .calendar-day-disabled {
            min-height: 2.35rem;
            display: grid;
            place-items: center;
            border-radius: 12px;
            font-size: 0.85rem;
        }

        .calendar-day-empty { color: transparent; }

        .calendar-day-disabled {
            color: #b8b0bd;
            background: rgba(255, 255, 255, 0.42);
            border: 1px solid rgba(102, 8, 116, 0.05);
        }

        div[data-baseweb="popover"] div.stButton > button {
            min-width: 0 !important;
            width: 100%;
            min-height: 2.25rem;
            padding: 0 !important;
            font-size: 0.78rem;
            line-height: 1;
            white-space: nowrap;
        }

        div[data-testid="stDialog"] {
            backdrop-filter: blur(8px);
            -webkit-backdrop-filter: blur(8px);
            background-color: rgba(25, 23, 36, 0.24);
        }

        div[data-testid="stDialog"] section[role="dialog"] {
            border-radius: 22px;
            box-shadow: 0 28px 80px rgba(25, 23, 36, 0.32);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

def load_overview_model_config(config: Config) -> Dict[str, Any]:
    try:
        models_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "lab_agent",
            "config",
            "models.json",
        )
        with open(models_path, "r", encoding="utf-8") as f:
            models_data = json.load(f)
        return {
            "ArXiv Filter Model": models_data.get("arxivFilterModel", {}).get("name", "Unknown"),
            "Chat Model": models_data.get("chatModel", {}).get("name", "Unknown"),
            "DeepSeek Base URL": getattr(config, "deepseek_base_url", os.getenv("DEEPSEEK_BASE_URL", "https://llmapi.paratera.com")),
            "Scoring API Key": "configured" if (getattr(config, "deepseek_scoring_api_key", None) or os.getenv("DEEPSEEK_SCORING_API_KEY")) else "missing",
            "Chat API Key": "configured" if (getattr(config, "deepseek_chat_api_key", None) or os.getenv("DEEPSEEK_CHAT_API_KEY")) else "missing",
            "Debug Mode": config.debug,
            "Log Level": config.log_level,
        }
    except Exception:
        return {
            "Chat Model": "DeepSeek-V4-Pro",
            "DeepSeek Base URL": getattr(config, "deepseek_base_url", os.getenv("DEEPSEEK_BASE_URL", "https://llmapi.paratera.com")),
            "Debug Mode": config.debug,
            "Log Level": config.log_level,
        }


def priority_value(summary: Dict[str, Any], priority: str) -> int:
    counts = summary.get("priority_counts", {}) if summary else {}
    return int(counts.get(priority, counts.get(int(priority), 0)) or 0)


def get_today_arxiv_overview(today: str) -> Dict[str, Any]:
    if st.session_state.arxiv_agent is None:
        return {"name": "ArXiv Daily", "status": "Unavailable", "total": 0, "p3": 0, "p2": 0, "p1": 0}

    result = st.session_state.arxiv_agent._get_report(today)
    if not result.get("success"):
        return {"name": "ArXiv Daily", "status": "No report", "total": 0, "p3": 0, "p2": 0, "p1": 0}

    summary = result["report"]["json_data"].get("summary", {})
    return {
        "name": "ArXiv Daily",
        "status": "Ready",
        "total": int(summary.get("total_papers", 0) or 0),
        "p3": priority_value(summary, "3"),
        "p2": priority_value(summary, "2"),
        "p1": priority_value(summary, "1"),
    }


def get_today_journal_overview(today: str) -> Dict[str, Any]:
    if st.session_state.journal_agent is None:
        return {"name": "Journal Daily", "status": "Unavailable", "total": 0, "p3": 0, "p2": 0, "p1": 0, "journals": 0}

    result = asyncio.run(st.session_state.journal_agent.process_task({
        "type": "get_summary_report",
        "date": today,
    }))
    if not result.get("success"):
        return {"name": "Journal Daily", "status": "No summary", "total": 0, "p3": 0, "p2": 0, "p1": 0, "journals": len(st.session_state.journal_agent.journals)}

    data = result["report"]["json_data"]
    summary = data.get("summary", {})
    return {
        "name": "Journal Daily",
        "status": "Ready",
        "total": int(summary.get("total_papers", 0) or 0),
        "p3": priority_value(summary, "3"),
        "p2": priority_value(summary, "2"),
        "p1": 0,
        "journals": len(data.get("journal_summaries", [])),
        "rss_entries": summary.get("rss_entries_fetched", 0),
        "date_window_articles": summary.get("date_window_articles", 0),
        "date_window_dates": summary.get("date_window_dates", []),
    }


def overview_interface(config: Config, current_user: CurrentUser):
    render_page_header(
        "Daily Research Brief",
        "A compact view of today's ArXiv and journal literature signals.",
    )
    today = today_str()
    arxiv = get_today_arxiv_overview(today)
    journal = get_today_journal_overview(today)
    total_p3 = arxiv["p3"] + journal["p3"]
    total_p2 = arxiv["p2"] + journal["p2"]

    st.markdown(
        f"""
        <div class="overview-panel">
            <div class="overview-kicker">Daily Research Brief &middot; {today}</div>
            <div class="overview-title">{total_p3} high-priority papers need attention</div>
            <div class="overview-muted">Combined from ArXiv Daily and Journal Daily reports. Journal Daily is limited to RSS updates from today and yesterday.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    left, right = st.columns([1.15, 0.85], gap="large")
    with left:
        metric_cols = st.columns(2)
        with metric_cols[0]:
            st.metric("\U0001F534 Priority 3", total_p3)
        with metric_cols[1]:
            st.metric("\U0001F7E1 Priority 2", total_p2)

        st.subheader("Daily Status")
        render_overview_status_lines(today, arxiv, journal)
        render_overview_secondary_sections(config)

    with right:
        with st.container(border=True):
            deepseek_assistant_interface(current_user)


def render_overview_status_lines(today: str, arxiv: Dict[str, Any], journal: Dict[str, Any]) -> None:
    journal_window = journal.get("date_window_dates", [])
    journal_meta = f"{journal['total']} saved articles"
    if journal_window:
        journal_meta += f" | RSS window: {', '.join(journal_window)}"

    st.markdown(
        f"""
        <div class="overview-panel">
            <div class="status-line"><span class="status-name">ArXiv Daily</span><span class="status-meta">{arxiv['status']} &middot; {arxiv['total']} papers &middot; {arxiv['p3']} Priority 3</span></div>
            <div class="status-line"><span class="status-name">Journal Daily</span><span class="status-meta">{journal['status']} &middot; {journal_meta} &middot; {journal['p3']} Priority 3</span></div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    action_col, _ = st.columns([0.26, 0.74])
    with action_col:
        if st.button("Today's Highlights", key="overview_today_highlights", help="View today's combined Priority 3 highlights"):
            show_today_highlights(today)


def show_today_highlights(today: str) -> None:
    try:
        generator = HighlightsReportGenerator(
            reports_dir=project_path_str("reports/Highlights_reports"),
            arxiv_reports_dir=project_path_str("reports/ArXiv_reports"),
            journal_summary_reports_dir=project_path_str("reports/Journal_Summary_reports"),
        )
        result = generator.generate(today, force=True)
        if not result.get("success"):
            st.error(result.get("error", "Could not generate today's highlights report"))
            return

        render_html_report_dialog(
            result["html_content"],
            state_key=f"overview_today_highlights_{today}",
            height=720,
        )
    except Exception as e:
        st.error(f"Error loading today's highlights: {e}")

def render_overview_secondary_sections(config: Config) -> None:
    with st.expander("Configuration", expanded=False):
        st.json(load_overview_model_config(config))

    with st.expander("Database", expanded=False):
        st.empty()


def arxiv_daily_interface(current_user: CurrentUser):
    render_page_header(
        "ArXiv Daily",
        "AI-scored condensed matter recommendations with full HTML reports and paper-aware chat.",
    )

    if st.session_state.arxiv_agent is None:
        st.error("ArXiv agent not available. Please check DEEPSEEK_SCORING_API_KEY in .env file.")
        with st.container(border=True):
            arxiv_chat_interface(current_user)
        return

    col_reports, col_chat = st.columns([1.25, 0.75], gap="large")

    with col_reports:
        try:
            result = st.session_state.arxiv_agent._list_reports()
            if not result['success']:
                st.error(f"Error loading reports: {result.get('error', 'Unknown error')}")
                return

            reports = result.get('reports', [])
            report_title_col, calendar_col = st.columns([0.68, 0.32], gap="large", vertical_alignment="center")
            with report_title_col:
                st.subheader("Reports")
            with calendar_col:
                selected_report_date = render_report_calendar(reports, "arxiv")

            st.markdown('<div class="section-caption">Latest five reports are shown first. Earlier reports are available in Previous Reports. Pick an available date from the calendar to jump to a report.</div>', unsafe_allow_html=True)

            action_col, _ = st.columns([0.18, 0.82])
            with action_col:
                if st.button("Generate", key="generate_arxiv_daily_compact", help="Generate today's ArXiv report"):
                    generate_daily_report()

            render_report_collection(
                reports,
                selected_date=selected_report_date,
                key_prefix="arxiv",
                title_for_date=lambda date: f"Report for {date}",
                render_report=display_report,
                empty_message="No ArXiv Daily reports available yet.",
            )
        except Exception as e:
            st.error(f"Error loading reports: {e}")

    with col_chat:
        with st.container(border=True):
            arxiv_chat_interface(current_user, selected_report_date)


def generate_daily_report():
    """Generate a new daily report"""
    with st.spinner("Generating daily report... This may take a few minutes."):
        try:
            task = {
                'type': 'generate_daily_report',
                'date': today_str(),
                'url': 'https://arxiv.org/list/cond-mat/new'
            }
            
            result = asyncio.run(st.session_state.arxiv_agent.process_task(task))
            
            if result['success']:
                if result.get('from_cache'):
                    st.info(f"Cached report: {result['message']}")
                else:
                    st.success(f"{result['message']}")
                
                # Handle both string and integer keys for priority counts
                priority_counts = result.get('priority_counts', {})
                p3 = priority_counts.get('3', priority_counts.get(3, 0))
                p2 = priority_counts.get('2', priority_counts.get(2, 0))
                p1 = priority_counts.get('1', priority_counts.get(1, 0))
                
                st.info(f"Summary: {result['total_papers']} papers | "
                       f"Priority 3: {p3} | "
                       f"Priority 2: {p2} | "
                       f"Priority 1: {p1}")
                st.rerun()
            else:
                st.error(f"鉂?Error: {result.get('error', 'Unknown error')}")
                
        except Exception as e:
            st.error(f"鉂?Error generating report: {e}")


def clear_reports():
    """Clear all stored reports"""
    try:
        result = st.session_state.arxiv_agent._clear_reports()
        if result['success']:
            st.success(f"{result['message']}")
            st.rerun()
        else:
            st.error(f"鉂?Error: {result.get('error', 'Unknown error')}")
    except Exception as e:
        st.error(f"鉂?Error clearing reports: {e}")


def display_report(date):
    """Display a specific report"""
    try:
        result = st.session_state.arxiv_agent._get_report(date)
        
        if result['success']:
            report_data = result['report']['json_data']
            html_content = result['report']['html_content']
            
            # Show summary
            summary = report_data['summary']
            col1, col2, col3, col4 = st.columns(4)
            
            with col1:
                st.metric("Total Papers", summary['total_papers'])
            with col2:
                # Handle both string and integer keys for priority counts
                priority_3 = summary['priority_counts'].get('3', summary['priority_counts'].get(3, 0))
                st.metric("Priority 3", priority_3)
            with col3:
                priority_2 = summary['priority_counts'].get('2', summary['priority_counts'].get(2, 0))
                st.metric("Priority 2", priority_2)
            with col4:
                priority_1 = summary['priority_counts'].get('1', summary['priority_counts'].get(1, 0))
                st.metric("Priority 1", priority_1)
            
            # Show report options
            render_toggleable_html_report(
                html_content,
                state_key=f"arxiv_html_visible_{date}",
                height=600,
            )
            
        else:
            st.error(f"Error loading report: {result.get('error', 'Unknown error')}")
            
    except Exception as e:
        st.error(f"Error displaying report: {e}")



def journal_daily_interface(current_user: CurrentUser):
    render_page_header(
        "Journal Daily",
        "RSS-based journal reports limited to articles updated today and yesterday.",
    )

    if st.session_state.journal_agent is None:
        st.error("Journal Daily agent not available. Please check your API key in .env file.")
        with st.container(border=True):
            journal_chat_interface(current_user, "Summary", None)
        return

    journals = st.session_state.journal_agent.journals
    if not journals:
        st.warning("No journal RSS feeds configured.")
        with st.container(border=True):
            journal_chat_interface(current_user, "Summary", None)
        return

    sorted_journals = sorted(journals, key=lambda journal: journal["name"].casefold())
    journal_by_name = {journal["name"]: journal for journal in sorted_journals}
    report_options = ["Summary"] + [journal["name"] for journal in sorted_journals]
    selected_report = st.session_state.get("journal_daily_report_selector", "Summary")
    if selected_report not in report_options:
        selected_report = "Summary"

    selected_journal = None
    report_dates = []
    report_list_error = None
    calendar_key_prefix = "journal_summary"

    try:
        if selected_report == "Summary":
            list_result = asyncio.run(st.session_state.journal_agent.process_task({
                "type": "list_summary_reports",
            }))
        else:
            selected_journal = journal_by_name[selected_report]
            calendar_key_prefix = f"journal_{selected_journal['slug']}"
            list_result = asyncio.run(st.session_state.journal_agent.process_task({
                "type": "list_reports",
                "journal": selected_journal["slug"],
            }))

        if list_result.get("success"):
            report_dates = list_result.get("reports", [])
        else:
            report_list_error = list_result.get("error", "Unknown error")
    except Exception as e:
        report_list_error = str(e)

    col_reports, col_chat = st.columns([1.25, 0.75], gap="large")

    with col_reports:
        report_title_col, calendar_col = st.columns([0.68, 0.32], gap="large", vertical_alignment="center")
        with report_title_col:
            st.subheader("Reports")
        with calendar_col:
            selected_report_date = render_report_calendar(report_dates, calendar_key_prefix)

        selected_report = st.selectbox(
            "Report",
            report_options,
            index=report_options.index(selected_report),
            key="journal_daily_report_selector",
            label_visibility="collapsed",
        )

        selected_journal = None
        if selected_report == "Summary":
            st.markdown("**Journal Daily Summary Report**")
            display_journal_summary_section(sorted_journals, selected_report_date, report_dates, report_list_error)
        else:
            selected_journal = journal_by_name[selected_report]
            display_single_journal_section(selected_journal, selected_report_date, report_dates, report_list_error)

    with col_chat:
        with st.container(border=True):
            journal_chat_interface(current_user, selected_report, selected_journal, selected_report_date)


def display_journal_summary_section(journals, selected_date: str = "", reports=None, report_list_error=None):
    journal_names = ", ".join(journal["name"] for journal in journals)

    action_col, _ = st.columns([0.18, 0.82])
    with action_col:
        if st.button("Generate", key="generate_journal_summary_compact", help="Generate the Journal Daily summary report"):
            generate_journal_summary_report()

    if report_list_error:
        st.error(f"Error loading summary reports: {report_list_error}")
    else:
        render_report_collection(
            reports or [],
            selected_date=selected_date,
            key_prefix="journal_summary",
            title_for_date=lambda date: f"Summary Report for {date}",
            render_report=display_journal_summary_report,
            empty_message="No Journal Daily summary reports available yet.",
        )

    st.markdown(
        f'''
        <div class="journal-footer-note">
            <div>Included journals: {journal_names}</div>
            <div>Latest five summary reports are shown first. Earlier reports are available in Previous Reports. Pick an available date from the calendar to jump to a report.</div>
        </div>
        ''',
        unsafe_allow_html=True,
    )


def display_single_journal_section(journal, selected_date: str = "", reports=None, report_list_error=None):
    selected_slug = journal["slug"]

    action_col, _ = st.columns([0.18, 0.82])
    with action_col:
        if st.button("Generate", key=f"generate_journal_{selected_slug}_compact", help=f"Generate today's {journal['name']} report"):
            generate_journal_daily_report(selected_slug)

    if report_list_error:
        st.error(f"Error loading {journal['name']} reports: {report_list_error}")
    else:
        render_report_collection(
            reports or [],
            selected_date=selected_date,
            key_prefix=f"journal_{selected_slug}",
            title_for_date=lambda date: f"{journal['name']} Report for {date}",
            render_report=lambda date: display_journal_report(selected_slug, date),
            empty_message=f"No reports available for {journal['name']} yet.",
        )

    st.markdown(
        f'''
        <div class="journal-footer-note">
            <div>RSS: {journal['feed_url']}</div>
            <div>Latest five reports are shown first. Earlier reports are available in Previous Reports. Pick an available date from the calendar to jump to a report.</div>
        </div>
        ''',
        unsafe_allow_html=True,
    )


def generate_journal_summary_report():
    with st.spinner("Generating Journal Daily summary... This may take a few minutes per journal."):
        try:
            result = asyncio.run(st.session_state.journal_agent.process_task({
                "type": "generate_all_reports",
                "date": today_str(),
            }))

            if result.get("success"):
                st.success(result.get("message", "Generated Journal Daily summary"))
            else:
                st.warning(result.get("message", "Some journal reports failed"))

            for item in result.get("results", []):
                journal = item.get("journal", {}).get("name", "Unknown journal")
                if item.get("success"):
                    st.info(f"{journal}: {item.get('message', 'OK')}")
                else:
                    st.error(f"{journal}: {item.get('error', 'Unknown error')}")

            summary_result = result.get("summary_result", {})
            if summary_result.get("success"):
                counts = summary_result.get("priority_counts", {})
                p3 = counts.get("3", counts.get(3, 0))
                p2 = counts.get("2", counts.get(2, 0))
                st.info(
                    f"Summary: {summary_result.get('total_papers', 0)} articles "
                    f"| Priority 3: {p3} | Priority 2: {p2}"
                )
            else:
                st.error(f"Summary error: {summary_result.get('error', 'Unknown error')}")

            st.rerun()
        except Exception as e:
            st.error(f"Error generating Journal Daily summary: {e}")


def generate_journal_daily_report(journal_slug: str):
    with st.spinner("Generating journal report... This may take a few minutes."):
        try:
            result = asyncio.run(st.session_state.journal_agent.process_task({
                "type": "generate_daily_report",
                "journal": journal_slug,
                "date": today_str(),
            }))
            if result.get("success"):
                if result.get("from_cache"):
                    st.info(result["message"])
                else:
                    st.success(result["message"])
                counts = result.get("priority_counts", {})
                p3 = counts.get("3", counts.get(3, 0))
                p2 = counts.get("2", counts.get(2, 0))
                fetched = result.get("total_fetched", result.get("total_papers", 0))
                date_window_count = result.get("total_in_date_window", result.get("total_scored", result.get("total_papers", 0)))
                kept = result.get("total_papers", 0)
                st.info(
                    f"Summary: {kept} saved from {date_window_count} today/yesterday RSS articles "
                    f"({fetched} RSS entries fetched) | Priority 3: {p3} | Priority 2: {p2}"
                )
                st.rerun()
            else:
                st.error(f"Error: {result.get('error', 'Unknown error')}")
        except Exception as e:
            st.error(f"Error generating journal report: {e}")


def clear_journal_summary_reports():
    try:
        result = asyncio.run(st.session_state.journal_agent.process_task({
            "type": "clear_summary_reports",
        }))
        if result.get("success"):
            st.success(result["message"])
            st.rerun()
        else:
            st.error(f"Error: {result.get('error', 'Unknown error')}")
    except Exception as e:
        st.error(f"Error clearing summary reports: {e}")


def clear_journal_reports(journal_slug: str):
    try:
        result = asyncio.run(st.session_state.journal_agent.process_task({
            "type": "clear_reports",
            "journal": journal_slug,
        }))
        if result.get("success"):
            st.success(result["message"])
            st.rerun()
        else:
            st.error(f"Error: {result.get('error', 'Unknown error')}")
    except Exception as e:
        st.error(f"Error clearing journal reports: {e}")


def _journal_total_updated_articles(summary: dict) -> int:
    """Return all scored updated articles, including Priority 1/2/3."""
    return int(
        summary.get(
            "total_scored",
            summary.get("date_window_articles", summary.get("total_papers", 0)),
        )
        or 0
    )


def display_journal_summary_report(date: str):
    try:
        result = asyncio.run(st.session_state.journal_agent.process_task({
            "type": "get_summary_report",
            "date": date,
        }))
        if not result.get("success"):
            st.error(f"Error loading summary report: {result.get('error', 'Unknown error')}")
            return

        report = result["report"]
        report_data = report["json_data"]
        html_content = report["html_content"]
        summary = report_data.get("summary", {})
        counts = summary.get("priority_counts", {})
        p3 = counts.get("3", counts.get(3, 0))
        p2 = counts.get("2", counts.get(2, 0))
        journal_summaries = report_data.get("journal_summaries", [])
        missing_journals = report_data.get("missing_journals", [])
        date_window_dates = summary.get("date_window_dates", [])

        if date_window_dates:
            st.caption("RSS date window: today and yesterday updates (" + ", ".join(date_window_dates) + ")")

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Total Updated Articles", _journal_total_updated_articles(summary))
        with col2:
            st.metric("Priority 3", p3)
        with col3:
            st.metric("Priority 2", p2)


        if missing_journals:
            st.warning("Missing source reports: " + ", ".join(missing_journals))

        render_toggleable_html_report(
            html_content,
            state_key=f"journal_summary_html_visible_{date}",
            height=700,
        )

    except Exception as e:
        st.error(f"Error displaying summary report: {e}")


def display_journal_report(journal_slug: str, date: str):
    try:
        result = asyncio.run(st.session_state.journal_agent.process_task({
            "type": "get_report",
            "journal": journal_slug,
            "date": date,
        }))
        if not result.get("success"):
            st.error(f"Error loading report: {result.get('error', 'Unknown error')}")
            return

        report_data = result["report"]["json_data"]
        html_content = result["report"]["html_content"]
        summary = report_data.get("summary", {})
        counts = summary.get("priority_counts", {})
        p3 = counts.get("3", counts.get(3, 0))
        p2 = counts.get("2", counts.get(2, 0))
        date_window_dates = summary.get("date_window_dates", [])

        if date_window_dates:
            st.caption("RSS date window: today and yesterday updates (" + ", ".join(date_window_dates) + ")")

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Total Updated Articles", _journal_total_updated_articles(summary))
        with col2:
            st.metric("Priority 3", p3)
        with col3:
            st.metric("Priority 2", p2)

        render_toggleable_html_report(
            html_content,
            state_key=f"journal_html_visible_{journal_slug}_{date}",
            height=700,
        )

    except Exception as e:
        st.error(f"Error displaying journal report: {e}")


def arxiv_chat_interface(current_user: CurrentUser, selected_date: str = ""):
    """Render private ArXiv chat bound to its saved report date."""
    selected_context = arxiv_context(selected_date or today_str())

    render_paper_chat(
        current_user=current_user,
        conversation_type=ConversationType.ARXIV,
        key="arxiv_active_conversation_id",
        selected_context=selected_context,
        chat=st.session_state.get("arxiv_chat"),
        load_papers=lambda context: load_arxiv_papers(st.session_state.get("arxiv_agent"), context),
        suggested_questions=lambda chat, date: get_date_aware_suggestions(chat, date, "arxiv"),
        source_label="ArXiv papers",
    )


def journal_chat_interface(
    current_user: CurrentUser, selected_report: str, selected_journal, selected_date: str = ""
):
    """Render private Journal chat bound to its saved date and journal slug."""
    selected_context = journal_context(
        selected_date or today_str(),
        None if selected_report == "Summary" else selected_journal,
    )

    render_paper_chat(
        current_user=current_user,
        conversation_type=ConversationType.JOURNAL,
        key="journal_active_conversation_id",
        selected_context=selected_context,
        chat=st.session_state.get("journal_chat"),
        load_papers=lambda context: load_journal_papers(st.session_state.get("journal_agent"), context),
        suggested_questions=lambda chat, date: get_date_aware_suggestions(chat, date, "journal"),
        source_label="Journal papers",
    )

def load_overview_chat_model_config() -> Dict[str, Any]:
    models_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "lab_agent",
        "config",
        "models.json",
    )
    try:
        with open(models_path, "r", encoding="utf-8") as f:
            models_data = json.load(f)
        return models_data.get("chatModel", {
            "name": "DeepSeek-V4-Pro",
            "maxTokens": 1500,
            "temperature": 0.3,
        })
    except Exception:
        return {
            "name": "DeepSeek-V4-Pro",
            "maxTokens": 1500,
            "temperature": 0.3,
        }


def get_deepseek_chat_settings() -> Dict[str, str]:
    return {
        "api_key": os.getenv("DEEPSEEK_CHAT_API_KEY", ""),
        "base_url": (
            os.getenv("DEEPSEEK_BASE_URL")
            or os.getenv("DEEPSEEK_API_URL")
            or os.getenv("API_URL")
            or os.getenv("BASE_URL")
            or "https://llmapi.paratera.com"
        ),
    }


def overview_system_prompt() -> str:
    return (
        "You are Lab Assistant for a condensed matter physics lab dashboard. "
        "Use the same DeepSeek chat configuration as the Journal Daily chat module. "
        "Help with daily ArXiv and journal reports, research planning, paper interpretation, "
        "and lab workflow questions. Be concise, practical, and scientifically careful."
    )


def get_overview_suggested_prompts() -> list[str]:
    return [
        "Summarize today's high-priority papers",
        "Help me compare ArXiv and journal highlights",
        "Suggest follow-up experiments from today's reports",
        "Explain a condensed matter concept",
    ]


def deepseek_assistant_interface(current_user: CurrentUser):
    """Overview chat reads every visible turn from the user's private database history."""
    model_config = load_overview_chat_model_config()
    chat_settings = get_deepseek_chat_settings()
    st.subheader("Lab Assistant")
    st.caption(f"Research and lab planning assistant - {model_config.get('name', 'DeepSeek-V4-Pro')}")

    store, conversation, messages = select_workspace(
        current_user, ConversationType.OVERVIEW, "overview_active_conversation_id"
    )

    if not chat_settings["api_key"]:
        st.error("Lab Assistant is not available. Please check DEEPSEEK_CHAT_API_KEY in .env.")
        return

    def submit(text: str) -> None:
        try:
            conversation_id = store.append_user(
                current_user, ConversationType.OVERVIEW, {}, text,
                conversation.id if conversation is not None else None,
            )
            st.session_state.overview_active_conversation_id = conversation_id

            def generate(saved_messages):
                runtime_messages = [
                    {"role": "system", "content": overview_system_prompt()},
                    *model_history(saved_messages),
                ]
                with st.spinner("Lab Assistant is thinking..."):
                    client = OpenAI(
                        api_key=chat_settings["api_key"],
                        base_url=chat_settings["base_url"],
                        timeout=60.0,
                        max_retries=0,
                    )
                    response = client.chat.completions.create(
                        model=model_config.get("name", "DeepSeek-V4-Pro"),
                        messages=runtime_messages,
                        max_tokens=model_config.get("maxTokens", 1500),
                        temperature=model_config.get("temperature", 0.3),
                    )
                return response.choices[0].message.content

            store.generate_reply(current_user, ConversationType.OVERVIEW, conversation_id, generate)
            st.rerun()
        except Exception:
            st.error("Unable to generate a response. Your message may have been saved; refresh to check.")

    if not messages:
        st.markdown("**Quick prompts**")
        suggestion_cols = st.columns(2)
        for index, suggestion in enumerate(get_overview_suggested_prompts()):
            with suggestion_cols[index % 2]:
                if st.button(suggestion, key=f"overview_suggestion_{index}", use_container_width=True):
                    submit(suggestion)

    if prompt := st.chat_input("Ask about reports, papers, or lab planning...", key="overview_chat_input"):
        submit(prompt)

def generate_daily_report_quick():
    """Generate daily report from overview (simplified version)"""
    if st.session_state.arxiv_agent is None:
        st.error("ArXiv agent not available")
        return
    
    with st.spinner("Generating today's ArXiv report... This may take a few minutes."):
        try:
            task = {
                'type': 'generate_daily_report',
                'date': today_str(),
                'url': 'https://arxiv.org/list/cond-mat/new'
            }
            
            result = asyncio.run(st.session_state.arxiv_agent.process_task(task))
            
            if result['success']:
                if result.get('from_cache'):
                    st.info("Report was already available")
                else:
                    st.success("Report generated successfully")
                
                # Show summary
                priority_counts = result.get('priority_counts', {})
                p3 = priority_counts.get('3', priority_counts.get(3, 0))
                
                if p3 > 0:
                    st.success(f"{p3} high-priority papers found")
                else:
                    st.info("No high-priority papers today")
                
                st.rerun()  # Refresh the page to show updated status
            else:
                st.error(f"鉂?Error: {result.get('error', 'Unknown error')}")
                
        except Exception as e:
            st.error(f"鉂?Error generating report: {e}")


if __name__ == "__main__":
    main()
