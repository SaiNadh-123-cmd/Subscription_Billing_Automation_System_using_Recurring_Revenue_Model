"""
Frontend UI Page Endpoints.
Renders responsive Tailwind CSS pages for Authentication and Subscription State Machine.
"""

from pathlib import Path
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter(tags=["Frontend Pages"])


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    """Root redirect to /home (client-side redirects to /signin if unauthenticated)."""
    return RedirectResponse(url="/home")


@router.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request):
    """Serves the user registration page."""
    return templates.TemplateResponse("signup.html", {"request": request})


@router.get("/signin", response_class=HTMLResponse)
def signin_page(request: Request):
    """Serves the user login page."""
    return templates.TemplateResponse("signin.html", {"request": request})


@router.get("/home", response_class=HTMLResponse)
def home_page(request: Request):
    """Serves the main protected dashboard and subscription state machine simulator."""
    return templates.TemplateResponse("home.html", {"request": request})
