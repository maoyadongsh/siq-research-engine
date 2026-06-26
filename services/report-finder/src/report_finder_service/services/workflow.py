from dataclasses import dataclass

from report_finder_service.models.schemas import CompanyEntity, LatestReportResponse, ReportTarget


@dataclass
class WorkflowState:
    query: str
    target: ReportTarget
    resolved: CompanyEntity | None = None
    source_id: str | None = None
    candidate_count: int = 0
    result: LatestReportResponse | None = None


def has_langgraph() -> bool:
    try:
        import langgraph  # noqa: F401
    except ImportError:
        return False
    return True
