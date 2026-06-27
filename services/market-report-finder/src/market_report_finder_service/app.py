from fastapi import FastAPI

from market_report_finder_service import __version__
from market_report_finder_service.api.routes import company_router, health_router, reports_router, sources_router

app = FastAPI(
    title="Market Report Finder Service",
    version=__version__,
    description="Standalone backend for resolving companies and downloading official market filings.",
)

app.include_router(health_router)
app.include_router(sources_router)
app.include_router(company_router)
app.include_router(reports_router)


@app.get("/")
def index():
    return {
        "service": "market-report-finder-service",
        "version": __version__,
        "docs": "/docs",
        "health": "/health",
    }
