"""Small Streamlit administrator page; all mutations remain service guarded."""

from collections.abc import Callable

import streamlit as st

from lab_agent.auth.admin_users import (
    AdminSafetyError,
    AdminUserNotFoundError,
    AdminUserService,
)
from lab_agent.auth.authorization import AdminAuthorizationError, require_admin
from lab_agent.auth.models import CurrentUser
from lab_agent.db.models import UserRole
from lab_agent.db.session import database_session
from lab_agent.repositories.admin_users import AdminUserRepository


_ACTIONS = (
    "Promote to admin", "Demote to user", "Enable", "Disable",
)


def admin_interface(current_user: CurrentUser, render_page_header: Callable[[str, str], None]) -> None:
    """Recheck the page guard even when navigation state is manipulated."""
    try:
        require_admin(current_user)
    except AdminAuthorizationError:
        st.error("Administrator access required.")
        st.stop()
    try:
        with database_session() as session:
            users = AdminUserService(AdminUserRepository(session)).list_users(actor=current_user)
    except Exception:
        st.error("User management is temporarily unavailable.")
        st.stop()

    render_page_header("Admin", "Manage LabAgent user access.")
    if notice := st.session_state.pop("admin_action_notice", None):
        st.success(notice)
    st.dataframe(
        [
            {
                "Email": user.email,
                "Display name": user.display_name,
                "Role": user.role.value,
                "Active": user.is_active,
                "Created": user.created_at,
                "Last login": user.last_login_at,
            }
            for user in users
        ],
        use_container_width=True,
        hide_index=True,
    )
    if not users:
        return

    with st.form("admin_user_action"):
        target_id = st.selectbox(
            "User", [user.id for user in users],
            format_func=lambda user_id: next(user.email for user in users if user.id == user_id),
        )
        action = st.selectbox("Action", _ACTIONS)
        confirmed = st.checkbox("I confirm this account change")
        submitted = st.form_submit_button("Apply change")

    if not submitted:
        return
    if not confirmed:
        st.warning("Confirm the account change before applying it.")
        return
    try:
        with database_session() as session:
            service = AdminUserService(AdminUserRepository(session))
            if action == "Promote to admin":
                service.change_user_role(actor=current_user, target_user_id=target_id, new_role=UserRole.ADMIN)
            elif action == "Demote to user":
                service.change_user_role(actor=current_user, target_user_id=target_id, new_role=UserRole.USER)
            elif action == "Enable":
                service.set_user_active(actor=current_user, target_user_id=target_id, is_active=True)
            elif action == "Disable":
                service.set_user_active(actor=current_user, target_user_id=target_id, is_active=False)
            else:
                raise ValueError("Unsupported account action")
    except (AdminAuthorizationError, AdminSafetyError, AdminUserNotFoundError) as exc:
        st.error(str(exc))
        return
    except Exception:
        st.error("Unable to update this user right now.")
        return

    st.session_state.admin_action_notice = "User account updated."
    st.rerun()
