"""
Adapter Validation Report — tests each API adapter and generates a report.

After each API adapter is connected, this produces a validation report covering:
1. API connection success/failure
2. Fields retrieved
3. Timestamp fields retrieved
4. Missing fields
5. Fallback behavior
6. Whether data can be used safely in point-in-time backtest
7. Whether it should be limited to live mode only
"""
import os
import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

DATA_UNAVAILABLE = "Data unavailable"

# Expected fields for each adapter
EXPECTED_FIELDS = {
    "market_data": {
        "required": ["ticker", "close", "volume", "change_pct"],
        "timestamp_fields": ["timestamp", "available_as_of"],
        "optional": ["open", "high", "low", "prior_close", "market_cap", "source"],
    },
    "fundamentals": {
        "required": ["ticker", "revenue_ttm", "gross_margin_pct", "debt_to_equity"],
        "timestamp_fields": ["filing_date", "fiscal_period_end", "available_as_of"],
        "optional": [
            "pe_ratio", "peg_ratio", "price_to_sales", "price_to_fcf",
            "operating_margin_pct", "free_cash_flow_ttm", "net_income_ttm",
            "revenue_growth_pct", "rd_expense_ttm",
        ],
    },
    "sec_filings": {
        "required": ["ticker"],
        "timestamp_fields": ["filing_date", "available_as_of"],
        "optional": [
            "business_description_changed", "going_concern_language",
            "auditor_change_recent", "related_party_transactions",
        ],
    },
    "reddit": {
        "required": ["ticker"],
        "timestamp_fields": ["available_as_of"],
        "optional": [
            "wsb_mentions_7d", "squeeze_mentions", "bot_suspicion_score",
            "dd_posts", "yolo_posts", "retail_attention_level",
        ],
    },
    "github": {
        "required": ["ticker"],
        "timestamp_fields": ["available_as_of"],
        "optional": [
            "developer_ecosystem_score", "org_repos", "total_stars",
            "commits_90d", "has_ai_ml_repos",
        ],
    },
    "h1b_lca": {
        "required": ["ticker"],
        "timestamp_fields": ["available_as_of"],
        "optional": [
            "technical_intensity_score", "ai_ml_roles", "engineering_roles",
            "stem_roles_pct", "hiring_trend",
        ],
    },
}


def validate_adapter(
    adapter_name: str,
    test_ticker: str = "AAPL",
    test_data: dict | list | None = None,
) -> dict:
    """
    Validate a single adapter by checking its output against expected fields.

    Returns a validation report dict.
    """
    expected = EXPECTED_FIELDS.get(adapter_name, {})
    required = set(expected.get("required", []))
    timestamp_fields = set(expected.get("timestamp_fields", []))
    optional = set(expected.get("optional", []))
    all_expected = required | timestamp_fields | optional

    report = {
        "adapter": adapter_name,
        "test_ticker": test_ticker,
        "timestamp": datetime.now().isoformat(),
        "connection_status": "unknown",
        "fields_retrieved": [],
        "timestamp_fields_retrieved": [],
        "missing_required_fields": [],
        "missing_optional_fields": [],
        "fallback_behavior": "mock data",
        "backtest_safe": False,
        "live_only": False,
        "notes": [],
    }

    if test_data is None:
        report["connection_status"] = "no_data"
        report["missing_required_fields"] = list(required)
        report["notes"].append("No data returned — adapter may not be connected")
        return report

    # Flatten data if it's a list (take first item)
    data = test_data[0] if isinstance(test_data, list) and test_data else test_data
    if not isinstance(data, dict):
        report["connection_status"] = "error"
        report["notes"].append(f"Unexpected data type: {type(data)}")
        return report

    # Check fields
    retrieved = set(data.keys())
    report["fields_retrieved"] = sorted(retrieved & all_expected)
    report["timestamp_fields_retrieved"] = sorted(retrieved & timestamp_fields)
    report["missing_required_fields"] = sorted(required - retrieved)
    report["missing_optional_fields"] = sorted(optional - retrieved)

    # Connection status
    if not report["missing_required_fields"]:
        report["connection_status"] = "success"
    else:
        report["connection_status"] = "partial"
        report["notes"].append(
            f"Missing required fields: {report['missing_required_fields']}"
        )

    # Timestamp safety
    has_timestamps = len(report["timestamp_fields_retrieved"]) > 0
    has_filing_date = "filing_date" in retrieved or "available_as_of" in retrieved

    if has_timestamps and has_filing_date:
        report["backtest_safe"] = True
        report["notes"].append("Timestamp fields present — safe for point-in-time backtest")
    elif has_timestamps:
        report["backtest_safe"] = True
        report["notes"].append(
            "Has availability timestamps but missing filing_date — "
            "use with caution in backtest"
        )
    else:
        report["backtest_safe"] = False
        report["live_only"] = True
        report["notes"].append(
            "No timestamp fields retrieved — unsafe for historical backtest. "
            "Limit to live mode only."
        )

    # Check for DATA_UNAVAILABLE values
    unavailable_fields = [
        k for k, v in data.items()
        if v == DATA_UNAVAILABLE or v == "not_evaluated"
    ]
    if unavailable_fields:
        report["notes"].append(
            f"Fields marked as unavailable: {unavailable_fields}"
        )

    return report


def validate_market_data_adapter() -> dict:
    """Validate the market data adapter (Phase A).

    Pass 8 Step B1.6 (2026-05-04): no longer calls get_top_gainers() —
    that function is now mode-strict (replay requires a date) and a
    no-arg call would raise. The validator was a startup health check
    that was incidentally invoking the live FMP biggest-gainers
    endpoint on every dashboard load — a lookahead vector if anyone
    invoked it mid-backtest. Replaced with a pure connectivity ping
    that exercises FMP profile (a current-state read, but read-only and
    NOT used by the agent surge-discovery hot path) and the get_api_status
    call. Surge ranking is verified by tests/test_historical_surge_ranker_pit.py
    instead.
    """
    from src.data_adapters.market_data import get_api_status, get_fundamentals

    status = get_api_status()
    report = {
        "adapter": "market_data",
        "provider": status.get("provider", "unknown"),
        "active_source": status.get("active_source", "mock"),
        "api_key_set": status.get("api_key_set", False),
        "data_mode": status.get("data_mode", "mock"),
        "sub_reports": {},
    }

    # Connectivity ping — does NOT touch surge ranking.
    # connection_test field is populated by get_api_status() when a live
    # provider is configured; we just surface its result.
    conn = status.get("connection_test")
    if conn is not None:
        report["sub_reports"]["connectivity"] = {
            "connection_status": "success" if conn.get("connected") else "error",
            "elapsed_ms": conn.get("elapsed_ms"),
            "test_ticker": conn.get("test_ticker"),
        }
    else:
        report["sub_reports"]["connectivity"] = {
            "connection_status": "skipped",
            "reason": "live provider not configured",
        }

    # Test fundamentals (current-state; ETF/profile, not used in PIT path)
    try:
        fund = get_fundamentals("AAPL")
        report["sub_reports"]["fundamentals"] = validate_adapter(
            "fundamentals",
            test_data=fund,
        )
    except Exception as e:
        report["sub_reports"]["fundamentals"] = {
            "connection_status": "error",
            "error": str(e),
        }

    # Overall assessment
    all_ok = all(
        sr.get("connection_status") in ("success", "partial", "skipped")
        for sr in report["sub_reports"].values()
    )
    report["overall_status"] = "connected" if all_ok else "partial_or_failed"
    report["timestamp"] = datetime.now().isoformat()

    return report


def validate_all_adapters() -> dict:
    """
    Run validation across all connected adapters.
    Returns a comprehensive report for the dashboard.
    """
    reports = {}

    # Phase A: Market Data
    reports["market_data"] = validate_market_data_adapter()

    # Phase B-E: Check if adapters are in live or mock mode
    adapter_checks = [
        ("sec_filings", "SEC_USER_AGENT", "sec_adapter"),
        ("reddit", "REDDIT_CLIENT_ID", "reddit_adapter"),
        ("github", "GITHUB_TOKEN", "github_adapter"),
        ("h1b_lca", None, "h1b_adapter"),
    ]

    for adapter_name, env_var, module_name in adapter_checks:
        has_key = bool(os.environ.get(env_var, "")) if env_var else False
        reports[adapter_name] = {
            "adapter": adapter_name,
            "active_source": "mock",
            "api_key_set": has_key,
            "status": "mock_only",
            "note": f"Phase {'BCDE'['sec_filings reddit github h1b_lca'.split().index(adapter_name)]} — not yet implemented",
            "timestamp": datetime.now().isoformat(),
        }

    return {
        "validation_timestamp": datetime.now().isoformat(),
        "total_adapters": len(reports),
        "live_adapters": sum(
            1 for r in reports.values()
            if r.get("active_source", "mock") != "mock"
        ),
        "mock_adapters": sum(
            1 for r in reports.values()
            if r.get("active_source", "mock") == "mock"
        ),
        "adapters": reports,
    }


def print_validation_report(report: dict):
    """Print a clean terminal validation report."""
    print("\n" + "=" * 80)
    print("  API ADAPTER VALIDATION REPORT")
    print("=" * 80)
    print(f"  Generated: {report['validation_timestamp']}")
    print(f"  Total adapters: {report['total_adapters']}")
    print(f"  Live: {report['live_adapters']}  |  Mock: {report['mock_adapters']}")
    print()

    for name, adapter in report.get("adapters", {}).items():
        source = adapter.get("active_source", "mock")
        icon = "[LIVE]" if source != "mock" else "[MOCK]"
        status = adapter.get("overall_status", adapter.get("status", "unknown"))
        print(f"  {icon} {name:<20} source={source:<12} status={status}")

        # Sub-reports (for market_data)
        for sub_name, sub in adapter.get("sub_reports", {}).items():
            conn = sub.get("connection_status", "?")
            fields = len(sub.get("fields_retrieved", []))
            ts_fields = len(sub.get("timestamp_fields_retrieved", []))
            safe = sub.get("backtest_safe", False)
            print(f"     +- {sub_name:<16} conn={conn:<10} "
                  f"fields={fields}  ts_fields={ts_fields}  "
                  f"backtest_safe={'Y' if safe else 'N'}")

    print("=" * 80 + "\n")
