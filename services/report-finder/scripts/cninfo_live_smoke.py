from report_finder_service.adapters.cninfo import CninfoAdapter
from report_finder_service.models.schemas import ReportTarget
from report_finder_service.services.company_resolver import CompanyResolver
from report_finder_service.services.latest_selector import LatestReportSelector


def main() -> None:
    resolver = CompanyResolver()
    selector = LatestReportSelector()
    adapter = CninfoAdapter()

    company = resolver.resolve("茅台")
    candidates = adapter.search(company)
    selected = selector.select(candidates, ReportTarget.annual_report)
    print("resolved", company.display_name, company.ticker)
    print("candidates", len(candidates))
    print("selected_type", selected.report_type.value)
    print("selected_title", selected.title)
    print("selected_url", selected.document_url)
    print("landing_url", selected.landing_url)


if __name__ == "__main__":
    main()
