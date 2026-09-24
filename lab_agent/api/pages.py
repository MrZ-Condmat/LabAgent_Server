"""Small same-origin browser pages for Email OTP login and logout."""

from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

from lab_agent.utils.config import Config
from .dependencies import get_auth_config


router = APIRouter(prefix="/auth")
templates = Jinja2Templates(directory=str(Path(__file__).with_name("templates")))


def streamlit_redirect_target(config: Config) -> str:
    """Use only the configured destination; never honor a request's next URL."""
    value = config.streamlit_public_url
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Invalid LABAGENT_STREAMLIT_PUBLIC_URL")
    return value


@router.get("/login")
def login_page(request: Request, config: Config = Depends(get_auth_config)):
    response = templates.TemplateResponse(
        request=request, name="login.html",
        context={"streamlit_public_url": streamlit_redirect_target(config)},
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/logout-page")
def logout_page(request: Request, config: Config = Depends(get_auth_config)):
    response = templates.TemplateResponse(
        request=request, name="logout.html",
        context={"streamlit_public_url": streamlit_redirect_target(config)},
    )
    response.headers["Cache-Control"] = "no-store"
    return response
