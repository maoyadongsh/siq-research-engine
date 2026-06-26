from report_finder_service.api.routes.company import router as company_router
from report_finder_service.api.routes.downloads import router as downloads_router
from report_finder_service.api.routes.health import router as health_router
from report_finder_service.api.routes.reports import router as reports_router

__all__ = ["health_router", "company_router", "reports_router", "downloads_router"]
