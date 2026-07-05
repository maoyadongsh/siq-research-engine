from datetime import datetime, timezone

from fastapi import APIRouter

from market_report_finder_service.core.config import settings

router = APIRouter()


@router.get("/health")
def health():
    dart_ready = bool(settings.dart_api_key)
    edinet_ready = bool(settings.edinet_api_key)
    return {
        "status": "ok",
        "service": "market-report-finder-service",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "dart_api_key_configured": dart_ready,
            "edinet_api_key_configured": edinet_ready,
        },
        "markets": {
            "KR": {
                "official_source": "DART public + OpenDART",
                "official_sources": [
                    {
                        "source_id": "dart_public",
                        "source_name": "DART public disclosure PDF",
                        "official_domain": "dart.fss.or.kr",
                        "role": "primary_periodic_reports_without_api_key",
                        "ready": True,
                        "required_config": [],
                        "status": "public_pdf_search_download_supported",
                    },
                    {
                        "source_id": "dart",
                        "source_name": "DART / OpenDART",
                        "official_domain": "opendart.fss.or.kr",
                        "role": "primary_periodic_reports",
                        "ready": dart_ready,
                        "required_config": [] if dart_ready else ["DART_API_KEY"],
                    },
                    {
                        "source_id": "krx_kind",
                        "source_name": "KRX KIND",
                        "official_domain": "kind.krx.co.kr",
                        "role": "exchange_disclosures",
                        "ready": True,
                        "required_config": [],
                        "status": "secondary_official_source",
                    },
                ],
                "report_search_ready": True,
                "required_config": [],
                "message": (
                    "Korean periodic report search can use DART public PDF downloads and OpenDART API ZIP downloads are available."
                    if dart_ready
                    else "Korean mainstream annual-report search/download can use public DART PDF downloads without DART_API_KEY. Configure DART_API_KEY to additionally download OpenDART XML ZIP packages."
                ),
            },
            "JP": {
                "official_source": "EDINET + Issuer IR + TDnet",
                "official_sources": [
                    {
                        "source_id": "edinet",
                        "source_name": "EDINET",
                        "official_domain": "api.edinet-fsa.go.jp",
                        "role": "primary_statutory_reports",
                        "ready": edinet_ready,
                        "required_config": [] if edinet_ready else ["EDINET_API_KEY"],
                    },
                    {
                        "source_id": "issuer_annual_report",
                        "source_name": "Issuer Annual Securities Report / IR",
                        "official_domain": "issuer websites",
                        "role": "statutory_mirror_or_auxiliary_ir",
                        "ready": True,
                        "required_config": [],
                        "status": "statutory_mirror_or_auxiliary_only",
                    },
                    {
                        "source_id": "jpx_listed_company_search",
                        "source_name": "JPX Listed Company Search",
                        "official_domain": "jpx.co.jp / www2.jpx.co.jp",
                        "role": "listed_company_index_and_filing_pointer",
                        "ready": True,
                        "required_config": [],
                        "status": "official_index_not_primary_pdf_source",
                    },
                    {
                        "source_id": "tdnet",
                        "source_name": "TDnet",
                        "official_domain": "release.tdnet.info",
                        "role": "exchange_disclosures",
                        "ready": True,
                        "required_config": [],
                        "status": "free_public_recent_listing",
                    },
                ],
                "report_search_ready": True,
                "required_config": [] if edinet_ready else ["EDINET_API_KEY"],
                "message": (
                    "Japanese EDINET statutory search is available; issuer-hosted statutory mirrors and auxiliary IR PDFs remain supported."
                    if edinet_ready
                    else "Japanese complete statutory annual-report download requires EDINET_API_KEY. Integrated Report/IR PDFs are auxiliary only and are not used as the primary annual-report fallback."
                ),
            },
            "EU": {
                "official_source": "European ESEF / OAM official filings",
                "official_sources": [
                    {
                        "source_id": "xbrl_filings_esef",
                        "source_name": "filings.xbrl.org ESEF index",
                        "official_domain": "filings.xbrl.org",
                        "role": "official_oam_esef_mirror",
                        "ready": True,
                        "required_config": [],
                        "countries": ["UK", "France", "Germany", "Netherlands"],
                    },
                    {
                        "source_id": "six_direct",
                        "source_name": "SIX / issuer official direct URLs",
                        "official_domain": "six-group.com",
                        "role": "switzerland_direct_download",
                        "ready": True,
                        "required_config": [],
                        "countries": ["Switzerland"],
                        "status": "direct_download_supported_search_provider_pending",
                    },
                    {
                        "source_id": "issuer_annual_report",
                        "source_name": "Issuer / mainstream annual report downloads",
                        "official_domain": "issuer websites",
                        "role": "current_year_major_company_annual_reports",
                        "ready": True,
                        "required_config": [],
                        "countries": ["UK", "France", "Germany", "Netherlands", "Switzerland"],
                    },
                ],
                "report_search_ready": True,
                "required_config": [],
                "message": "European search/download is scoped to UK, France, Germany, Netherlands, and Switzerland. ESEF search is available for UK/FR/DE/NL; current-year major-company issuer downloads cover all five markets.",
            },
        },
    }
