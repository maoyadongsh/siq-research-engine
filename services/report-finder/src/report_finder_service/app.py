from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from report_finder_service.api.routes import (
    company_router,
    downloads_router,
    health_router,
    reports_router,
)

BASE_DIR = Path(__file__).resolve().parent.parent.parent

app = FastAPI(
    title="Report Finder Service",
    version="0.1.0",
    description="Resolve company names and select the latest public report from official sources.",
)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

app.include_router(health_router)
app.include_router(company_router)
app.include_router(reports_router)
app.include_router(downloads_router)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")
