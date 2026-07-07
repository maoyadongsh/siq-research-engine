import importlib.util
from pathlib import Path


PROFILE_DIR = Path(__file__).resolve().parents[1]


def _load_peer_metrics_builder():
    path = PROFILE_DIR / "scripts" / "peer_metrics_builder.py"
    spec = importlib.util.spec_from_file_location("peer_metrics_builder", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


peer_metrics_builder = _load_peer_metrics_builder()


def _row(company_id: str, **metrics):
    defaults = {
        "operating_revenue_yi": 120.0,
        "operating_revenue_yoy_pct": 8.0,
        "gross_margin_pct": 22.0,
        "parent_net_profit_yi": 12.0,
        "net_margin_pct": 10.0,
        "operating_cash_flow_margin_pct": 9.0,
        "total_assets_yi": 240.0,
        "total_liabilities_yi": 120.0,
        "debt_to_asset_ratio_pct": 50.0,
        "roe_pct": 11.0,
    }
    defaults.update(metrics)
    return {
        "company_id": company_id,
        "stock_code": company_id[-6:],
        "company_short_name": f"Peer {company_id}",
        "metrics": defaults,
        "available_metric_count": len([value for value in defaults.values() if value is not None]),
    }


def test_peer_sanity_keeps_normal_peer_rows():
    rows = [_row("CN:600001"), _row("CN:600002", gross_margin_pct=-20.0, roe_pct=-35.0)]

    accepted, quarantine = peer_metrics_builder.filter_peer_rows_for_sanity(rows)

    assert accepted == rows
    assert quarantine == []


def test_peer_sanity_quarantines_extreme_rows_without_blocking_all_peers():
    normal = _row("CN:600001")
    extreme = _row(
        "CN:600002",
        operating_revenue_yi=-10.0,
        gross_margin_pct=450.0,
        roe_pct=350.0,
        operating_revenue_yoy_pct=1500.0,
    )

    accepted, quarantine = peer_metrics_builder.filter_peer_rows_for_sanity([normal, extreme])

    assert accepted == [normal]
    assert len(quarantine) == 1
    assert quarantine[0]["company_id"] == "CN:600002"
    assert any(issue.startswith("negative_operating_revenue_yi") for issue in quarantine[0]["issues"])
    assert any(issue.startswith("extreme_margin:gross_margin_pct") for issue in quarantine[0]["issues"])
    assert any(issue.startswith("extreme_roe_pct") for issue in quarantine[0]["issues"])
    assert any(issue.startswith("extreme_operating_revenue_yoy_pct") for issue in quarantine[0]["issues"])
