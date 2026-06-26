from report_finder_service.models.schemas import CompanyEntity, Market


class SourceRouter:
    SOURCE_BY_MARKET = {
        Market.cn: "cninfo",
    }

    def route(self, company: CompanyEntity) -> str:
        try:
            return self.SOURCE_BY_MARKET[company.market]
        except KeyError as exc:
            raise ValueError(f"不支持的市场: {company.market}") from exc
