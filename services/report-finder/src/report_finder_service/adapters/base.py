from abc import ABC, abstractmethod

from report_finder_service.models.schemas import (
    CompanyEntity,
    ReportCandidate,
    ReportTarget,
    SourceDescriptor,
)


class SourceAdapter(ABC):
    @abstractmethod
    def describe(self) -> SourceDescriptor:
        raise NotImplementedError

    @abstractmethod
    def search(
        self,
        company: CompanyEntity,
        target: ReportTarget = ReportTarget.latest_report,
    ) -> list[ReportCandidate]:
        raise NotImplementedError
