from __future__ import annotations

from ipaddress import ip_address, ip_network
from urllib.parse import urlparse

from market_report_finder_service.models.schemas import Market


OFFICIAL_VERIFIED_STATUS = "official_verified"
MANUAL_UNVERIFIED_STATUS = "manual_unverified"
MANUAL_UNVERIFIED_SOURCE_ID = "manual_unverified"
MANUAL_UNVERIFIED_SOURCE_NAME = "Manual unverified URL"

HTTP_SCHEMES = {"http", "https"}
CLOUD_METADATA_HOSTS = {
    "metadata",
    "metadata.google.internal",
    "metadata.goog",
}
CLOUD_METADATA_IPS = {
    "169.254.169.254",
    "100.100.100.200",
}
PRIVATE_REPORT_IP_NETWORKS = tuple(
    ip_network(network)
    for network in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "::/128",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
)

CN_HOST_SUFFIXES = (
    "cninfo.com.cn",
)

HK_HOST_SUFFIXES = (
    "hkexnews.hk",
    "hkex.com.hk",
)

US_HOST_SUFFIXES = (
    "sec.gov",
)

EU_HOST_SUFFIXES = (
    "filings.xbrl.org",
    "sec.gov",
    "annualreports.ai",
    "financialreports.eu",
    "financialfilings.com",
    "fca.org.uk",
    "amf-france.org",
    "info-financiere.fr",
    "unternehmensregister.de",
    "bundesanzeiger.de",
    "afm.nl",
    "six-group.com",
    "ser-ag.com",
    "astrazeneca.com",
    "bp.com",
    "barclays",
    "totalenergies.com",
    "sanofi.com",
    "airliquide.com",
    "siemens.com",
    "sap.com",
    "telekom.com",
    "asml.com",
    "philips.com",
    "heinekencompany.com",
    "theheinekencompany.com",
    "nestle.com",
    "novartis.com",
    "roche.com",
    "hsbc.com",
    "shell.com",
    "londonstockexchange.com",
    "investegate.co.uk",
    "unilever.com",
    "diageo.com",
    "cdn-rio.dataweavers.io",
    "riotinto.com",
    "glencore.com",
    "lseg.com",
    "lvmh-com.cdn.prismic.io",
    "www-axa-com.cdn.prismic.io",
    "lvmh.com",
    "loreal-finance.com",
    "schneider-electric.com",
    "se.com",
    "bnpparibas",
    "airbus.com",
    "vinci.com",
    "allianz.com",
    "eqs-news.com",
    "bmwgroup.com",
    "vw-mms.de",
    "volkswagen-group.com",
    "basf.com",
    "infineon.com",
    "munichre.com",
    "ing.com",
    "prosus.com",
    "adyen.com",
    "aholddelhaize.com",
    "dsm-firmenich.com",
    "ubs.com",
    "zurich.com",
    "edge.sitecorecloud.io",
    "abb.com",
    "richemont.com",
    "swissre.com",
    "sika.com",
    "holcim.com",
)

JP_HOST_SUFFIXES = (
    "edinet-fsa.go.jp",
    "release.tdnet.info",
    "jpx.co.jp",
    "www2.jpx.co.jp",
)

KR_HOST_SUFFIXES = (
    "dart.fss.or.kr",
    "opendart.fss.or.kr",
    "englishdart.fss.or.kr",
    "kind.krx.co.kr",
)


def validate_http_url(document_url: str) -> str:
    parsed = urlparse(str(document_url or "").strip())
    if parsed.scheme.lower() not in HTTP_SCHEMES:
        raise ValueError("Only http and https report URLs are supported")
    if parsed.username or parsed.password:
        raise ValueError("Report URL must not include credentials")
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host:
        raise ValueError("Report URL must include a host")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("Report URL includes an invalid port") from exc
    validate_public_host_literal(host)
    return host


def is_forbidden_report_ip(value: object) -> bool:
    try:
        address = ip_address(value)
    except ValueError:
        return False
    return str(address) in CLOUD_METADATA_IPS or any(address in network for network in PRIVATE_REPORT_IP_NETWORKS)


def validate_public_host_literal(host: str) -> None:
    normalized = str(host or "").strip().rstrip(".").lower()
    if normalized in CLOUD_METADATA_HOSTS:
        raise ValueError("Report URL host is a cloud metadata endpoint")
    try:
        address = ip_address(normalized)
    except ValueError:
        return
    if is_forbidden_report_ip(address):
        raise ValueError("Report URL host resolves to a non-public IP address")


def host_matches(host: str, suffix: str) -> bool:
    normalized_host = host.rstrip(".").lower()
    normalized_suffix = suffix.rstrip(".").lower()
    return normalized_host == normalized_suffix or normalized_host.endswith(f".{normalized_suffix}")


def host_matches_any(host: str, suffixes: tuple[str, ...] | set[str]) -> bool:
    return any(host_matches(host, suffix) for suffix in suffixes)


def url_host_matches_any(document_url: str, suffixes: tuple[str, ...] | set[str]) -> bool:
    try:
        host = validate_http_url(document_url)
    except ValueError:
        return False
    return host_matches_any(host, suffixes)


def market_owns_url(market: Market, document_url: str) -> bool:
    try:
        host = validate_http_url(document_url)
    except ValueError:
        return False
    return market_owns_host(market, host)


def market_owns_host(market: Market, host: str) -> bool:
    if market == Market.cn:
        return host_matches_any(host, CN_HOST_SUFFIXES)
    if market == Market.hk:
        return host_matches_any(host, HK_HOST_SUFFIXES)
    if market == Market.us:
        return host_matches_any(host, US_HOST_SUFFIXES)
    if market == Market.eu:
        return host_matches_any(host, EU_HOST_SUFFIXES)
    if market == Market.jp:
        from market_report_finder_service.markets.jp.catalog import JpAnnualReportCatalog

        return host_matches_any(host, JP_HOST_SUFFIXES) or host_matches_any(host, JpAnnualReportCatalog.source_hosts())
    if market == Market.kr:
        from market_report_finder_service.markets.kr.catalog import KrAnnualReportCatalog

        return host_matches_any(host, KR_HOST_SUFFIXES) or host_matches_any(host, KrAnnualReportCatalog.source_hosts())
    return False


def catalog_owns_url(market: Market, document_url: str) -> bool:
    if market == Market.eu:
        from market_report_finder_service.markets.eu.catalog import EuAnnualReportCatalog

        return EuAnnualReportCatalog.entry_for_url(document_url) is not None
    if market == Market.jp:
        from market_report_finder_service.markets.jp.catalog import JpAnnualReportCatalog

        return JpAnnualReportCatalog.entry_for_url(document_url) is not None
    if market == Market.kr:
        from market_report_finder_service.markets.kr.catalog import KrAnnualReportCatalog

        return KrAnnualReportCatalog.entry_for_url(document_url) is not None
    return False
