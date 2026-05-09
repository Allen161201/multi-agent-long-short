"""
Dashboard -- Flask app serving the institutional-style HTML dashboard.
Now includes: API Status, Evidence Packet Viewer, Backtest Integrity,
Macro & Pipeline tab with live FRED data.
"""
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

# Load .env at import time so FRED_API_KEY / DATA_MODE / USE_MOCK_DATA / FMP_API_KEY
# reach the process. We pass override=True so that a rotated key in .env wins
# over a stale value already present in os.environ from a previous process.
# Never log key values; only log presence + length + 8-char sha256 fingerprint.
try:
    from dotenv import load_dotenv
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
    _DOTENV_PATH = _PROJECT_ROOT / ".env"
    if _DOTENV_PATH.exists():
        load_dotenv(_DOTENV_PATH, override=True)
except ImportError:
    pass  # python-dotenv optional; env vars from shell still work

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo
from flask import Flask, render_template, jsonify, request
from src.engine.orchestrator import run_pipeline
from src.engine.backtest import run_backtest, compare_results
from src.engine.evidence_packet import RULE_VERSION, AGENT_PROMPT_VERSION
from src.engine.backtest_integrity import run_integrity_check
from src.data_adapters.mock_loader import get_available_dates
from src.data_adapters.market_data import get_api_status
from src.data_adapters.validation_report import validate_all_adapters
from src.data_adapters.fred_adapter import (
    get_macro_indicators, get_yield_curve_data, generate_validation_report,
    get_cache_status, test_connection as fred_test_connection,
    get_macro_indicators_for_dashboard, build_date_metadata,
)
from src.agents.macro_regime import classify_regime as classify_macro_regime
from src.rules.logic_audit import get_all_rules, build_decision_trace
from src.portfolio.nav_history import load_nav_history
from src.portfolio.metrics import compute_all_metrics
logger = logging.getLogger(__name__)

# Resolve the portfolio data dir at import time so the routes don't have to
# recompute. Mirrors the _PROJECT_ROOT pattern used by the dotenv block above.
_DATA_PORTFOLIO_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "portfolio"
_DATA_BACKTEST_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "backtest"

# Run-id input validation. Any value coming off /api/backtest/run/<run_id>
# URLs is matched against this regex BEFORE touching the filesystem so that
# crafted inputs (path traversal, unicode tricks, NUL bytes) cannot reach
# Path() construction. Pattern matches the harness convention exactly:
#   backtest_<8 digits>_<6 digits>_<YYYY-MM-DD>_to_<YYYY-MM-DD>
import re as _re
_RUN_ID_PATTERN = _re.compile(
    r"^backtest_\d{8}_\d{6}_\d{4}-\d{2}-\d{2}_to_\d{4}-\d{2}-\d{2}$"
)


def _today_et() -> str:
    """Return today's date string (YYYY-MM-DD) in America/New_York timezone.
    This is the dynamic dashboard_run_date — never hard-coded."""
    return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def _now_et_iso() -> str:
    """Return current datetime ISO string in America/New_York timezone."""
    return datetime.now(ZoneInfo("America/New_York")).isoformat()


def _sanitize_json_value(value):
    """Recursively replace float('inf') / float('-inf') / float('nan') with
    None so flask.jsonify (Python json) doesn't emit non-standard
    Infinity/NaN tokens. Walks dicts and lists; passes through primitives."""
    import math as _math
    if isinstance(value, float):
        if _math.isnan(value) or _math.isinf(value):
            return None
        return value
    if isinstance(value, dict):
        return {k: _sanitize_json_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_json_value(v) for v in value]
    if isinstance(value, tuple):
        return [_sanitize_json_value(v) for v in value]
    return value

app = Flask(
    __name__,
    template_folder=str(Path(__file__).parent / "templates"),
    static_folder=str(Path(__file__).parent / "static"),
)

# Capture .env load time for /api/status. This is the moment the current
# FMP_API_KEY was loaded into the process — useful when debugging "did my
# .env rotation actually reach the server?" without ever exposing the key.
_KEY_LOADED_AT = datetime.now(ZoneInfo("UTC")).isoformat()

try:
    from src.data_adapters.fmp_adapter import get_key_fingerprint as _fa_fp
    _fp_info = _fa_fp()
    if _fp_info["key_set"]:
        logger.info(
            "FMP_API_KEY loaded: key_set=true len=%d fingerprint=%s "
            "had_outer_whitespace=%s loaded_at=%s",
            _fp_info["key_length"], _fp_info["key_fingerprint"],
            _fp_info["had_outer_whitespace"], _KEY_LOADED_AT,
        )
    else:
        logger.warning(
            "FMP_API_KEY not set in process env at startup. "
            "(.env present? load_dotenv import? override flag?)"
        )
except Exception:
    pass

# Cache for last pipeline result (for trace lookups)
_last_result = {}

# ── Live FMP Price Check cache ─────────────────────────────────
# Cached quotes keyed by ticker. Each entry: {"row": dict, "ts": datetime}.
# Refreshed on demand only; defended by _LIVE_PRICE_TTL.
# A sticky _quota_exhausted flag stops further FMP calls until the next
# UTC-midnight reset.
_LIVE_PRICE_CACHE: dict = {}
_LIVE_PRICE_TTL_SECONDS = 300  # 5 minutes
_LIVE_CHECK_TICKERS = ("AAPL", "UBER", "NVDA")
_quota_exhausted = False
_quota_exhausted_at: str | None = None


@app.route("/")
@app.route("/overview")
def index():
    """Overview tab. Both `/` and `/overview` resolve here."""
    dates = get_available_dates()
    return render_template("index.html", dates=dates)


@app.route("/api/run", methods=["POST"])
def api_run():
    global _last_result
    data = request.get_json(force=True)
    date = data.get("date", get_available_dates()[0])
    regime = data.get("regime", "weakening")
    skip_alt = data.get("skip_alt_data", False)

    result = run_pipeline(date, regime=regime, skip_alt_data=skip_alt)
    _last_result = result
    return jsonify(result)


@app.route("/api/backtest", methods=["POST"])
def api_backtest():
    data = request.get_json(force=True)
    regime = data.get("regime", "weakening")
    comparison = run_backtest(regime=regime)
    return jsonify(comparison)


@app.route("/api/dates")
def api_dates():
    return jsonify(get_available_dates())


@app.route("/api/rules")
def api_rules():
    """Return all system rules, thresholds, and scoring logic."""
    return jsonify(get_all_rules())


@app.route("/api/trace/<ticker>")
def api_trace(ticker):
    """Return decision trace for a specific ticker from the last pipeline run."""
    if not _last_result:
        return jsonify({"error": "No pipeline run yet. Run the pipeline first."}), 400
    trace = build_decision_trace(ticker.upper(), _last_result)
    return jsonify(trace)


@app.route("/api/traces")
def api_traces():
    """Return decision traces for all tickers in the last pipeline run."""
    if not _last_result:
        return jsonify({"error": "No pipeline run yet."}), 400
    decisions = _last_result.get("decisions", [])
    traces = {}
    for d in decisions:
        ticker = d["ticker"]
        traces[ticker] = build_decision_trace(ticker, _last_result)
    return jsonify(traces)


# ═══════════════════════════════════════════════════════════════
# Portfolio data routes (D4)
# Pure read-only endpoints over data/portfolio/. No mutations,
# no upstream API calls — just serialize what's already on disk.
# ═══════════════════════════════════════════════════════════════

def _list_eod_state_paths(portfolio_dir: Path) -> list[Path]:
    """Return *_eod_state.json paths in `portfolio_dir`, sorted by the
    YYYY-MM-DD date in the filename (ascending). Skips files whose stem
    doesn't parse as a date."""
    if not portfolio_dir.exists():
        return []
    out: list[tuple[str, Path]] = []
    for p in portfolio_dir.glob("*_eod_state.json"):
        stem = p.stem.replace("_eod_state", "")
        try:
            datetime.strptime(stem, "%Y-%m-%d")
        except ValueError:
            continue
        out.append((stem, p))
    out.sort(key=lambda x: x[0])
    return [p for _, p in out]


@app.route("/api/portfolio/current")
def api_portfolio_current():
    """Latest EOD state from data/portfolio/. Wrapper exposes the
    discovered date list so the UI can populate a date-picker."""
    paths = _list_eod_state_paths(_DATA_PORTFOLIO_DIR)
    if not paths:
        return jsonify({
            "status": "no_data",
            "message": "No EOD states written yet",
        })
    latest = paths[-1]
    try:
        with latest.open("r", encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return jsonify({
            "status": "error",
            "message": f"failed to read {latest.name}: "
                       f"{type(e).__name__}: {e}",
        }), 500
    return jsonify({
        "status": "ok",
        "as_of": state.get("as_of"),
        "available_dates": [
            p.stem.replace("_eod_state", "") for p in reversed(paths)
        ],
        "state": _sanitize_json_value(state),
    })


def _filter_history_by_date(
    rows: list[dict], from_date: str | None, to_date: str | None,
) -> list[dict]:
    if not from_date and not to_date:
        return list(rows)
    out: list[dict] = []
    for r in rows:
        as_of = r.get("as_of")
        if from_date and as_of < from_date:
            continue
        if to_date and as_of > to_date:
            continue
        out.append(r)
    return out


@app.route("/api/portfolio/history")
def api_portfolio_history():
    """NAV history rows from pnl_history.csv, optionally filtered by
    ?from=YYYY-MM-DD&to=YYYY-MM-DD."""
    history_csv = _DATA_PORTFOLIO_DIR / "pnl_history.csv"
    rows = load_nav_history(history_csv)
    if not rows:
        return jsonify({
            "status": "no_data",
            "row_count": 0,
            "rows": [],
        })
    from_date = request.args.get("from")
    to_date = request.args.get("to")
    filtered = _filter_history_by_date(rows, from_date, to_date)
    return jsonify({
        "status": "ok",
        "row_count": len(filtered),
        "first_date": filtered[0]["as_of"] if filtered else None,
        "last_date": filtered[-1]["as_of"] if filtered else None,
        "rows": _sanitize_json_value(filtered),
    })


@app.route("/api/portfolio/metrics")
def api_portfolio_metrics():
    """Financial-ratio metrics computed from pnl_history.csv. Same
    optional filter as /api/portfolio/history. information_ratio is not
    returned in v1 (no benchmark wired)."""
    history_csv = _DATA_PORTFOLIO_DIR / "pnl_history.csv"
    rows = load_nav_history(history_csv)
    from_date = request.args.get("from")
    to_date = request.args.get("to")
    rows = _filter_history_by_date(rows, from_date, to_date)

    # Derive metrics. compute_all_metrics handles single-row / empty
    # gracefully — we still mark the response status so the UI knows
    # whether the std-dependent metrics are real numbers or nulls.
    if len(rows) <= 1:
        # Build a minimal metrics dict by hand (compute_all_metrics needs
        # a CSV path; for the filtered slice we use the in-memory rows).
        nav = [float(r["total_nav"]) for r in rows]
        dates = [r["as_of"] for r in rows]
        from src.portfolio.metrics import (  # local to keep top imports tidy
            max_drawdown, total_return,
        )
        metrics = {
            "sharpe_ratio": None,
            "sortino_ratio": None,
            "max_drawdown": max_drawdown(nav, dates=dates),
            "return_volatility": None,
            "total_return": total_return(nav),
        }
        return jsonify({
            "status": "insufficient_data",
            "as_of_count": len(rows),
            "first_date": dates[0] if dates else None,
            "last_date": dates[-1] if dates else None,
            "metrics": _sanitize_json_value(metrics),
        })

    # ≥ 2 rows: compute against the unfiltered CSV if no filter, else
    # write a temp CSV view to honor the slice. Cheap path: derive the
    # six metrics directly from in-memory rows (re-using the low-level
    # functions) to avoid touching disk on every request.
    from src.portfolio.metrics import (
        max_drawdown, return_volatility, sharpe_ratio, sortino_ratio,
        total_return,
    )
    nav = [float(r["total_nav"]) for r in rows]
    dates = [r["as_of"] for r in rows]
    # Drop the first row's daily_return: when the slice is the full
    # history it's None anyway, and when the slice is a filtered
    # window the first row's daily_return references a NAV outside
    # the slice (leak). rows[1:] gives the correct in-slice return set.
    returns = [
        float(r["daily_return"]) for r in rows[1:]
        if r.get("daily_return") is not None
    ]
    metrics = {
        "sharpe_ratio": sharpe_ratio(returns),
        "sortino_ratio": sortino_ratio(returns),
        "max_drawdown": max_drawdown(nav, dates=dates),
        "return_volatility": return_volatility(returns),
        "total_return": total_return(nav),
    }
    return jsonify({
        "status": "ok",
        "as_of_count": len(rows),
        "first_date": dates[0],
        "last_date": dates[-1],
        "metrics": _sanitize_json_value(metrics),
    })


# ═══════════════════════════════════════════════════════════════
# Backtest run browser (read-only) — surfaces P&L harness output
# under data/backtest/<run_id>/ to the dashboard. Does NOT trigger
# new runs; the legacy /api/backtest POST keeps that responsibility.
# ═══════════════════════════════════════════════════════════════

def _list_backtest_runs(backtest_dir: Path) -> list[Path]:
    """Return run directories under backtest_dir whose names match the
    run-id regex AND contain a manifest.json. Sorted newest-first by
    manifest mtime (falls back to dirname-sort for ties)."""
    if not backtest_dir.exists():
        return []
    rows: list[tuple[float, str, Path]] = []
    for p in backtest_dir.iterdir():
        if not p.is_dir():
            continue
        if not _RUN_ID_PATTERN.match(p.name):
            continue
        manifest = p / "manifest.json"
        if not manifest.exists():
            continue
        try:
            mtime = manifest.stat().st_mtime
        except OSError:
            continue
        rows.append((mtime, p.name, p))
    rows.sort(key=lambda t: (-t[0], t[1]))
    return [p for _, _, p in rows]


def _resolve_run_dir(run_id: str) -> Path | None:
    """Validate run_id against _RUN_ID_PATTERN and return its directory
    iff it exists under _DATA_BACKTEST_DIR. Returns None otherwise.
    Defends against path traversal: the regex blocks "/" and "..", and
    we further confirm the resolved path is parented at the backtest
    dir so symlink shenanigans can't escape."""
    if not isinstance(run_id, str) or not _RUN_ID_PATTERN.match(run_id):
        return None
    candidate = (_DATA_BACKTEST_DIR / run_id).resolve()
    backtest_root = _DATA_BACKTEST_DIR.resolve()
    try:
        candidate.relative_to(backtest_root)
    except ValueError:
        return None
    if not candidate.exists() or not candidate.is_dir():
        return None
    return candidate


@app.route("/api/backtest/runs")
def api_backtest_runs():
    """List all P&L backtest runs under data/backtest/, newest-first.
    Each row carries the manifest summary fields the UI uses to populate
    its run-selector dropdown without re-fetching every full manifest."""
    if not _DATA_BACKTEST_DIR.exists():
        return jsonify({
            "status": "no_data",
            "run_count": 0,
            "runs": [],
            "message": "data/backtest/ does not exist yet",
        })
    run_dirs = _list_backtest_runs(_DATA_BACKTEST_DIR)
    if not run_dirs:
        return jsonify({
            "status": "no_data",
            "run_count": 0,
            "runs": [],
        })
    out: list[dict] = []
    for d in run_dirs:
        manifest_path = d / "manifest.json"
        try:
            with manifest_path.open("r", encoding="utf-8") as f:
                m = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(
                "skipping backtest %s: manifest unreadable (%s: %s)",
                d.name, type(e).__name__, e,
            )
            continue
        totals = m.get("totals") or {}
        metrics = m.get("metrics") or {}
        max_dd = metrics.get("max_drawdown") if isinstance(metrics, dict) else None
        max_dd_value = (max_dd or {}).get("value") if isinstance(max_dd, dict) else None
        try:
            created_iso = datetime.fromtimestamp(
                manifest_path.stat().st_mtime
            ).isoformat(timespec="seconds")
        except OSError:
            created_iso = None
        out.append({
            "run_id": m.get("run_id") or d.name,
            "start_date": m.get("start_date"),
            "end_date": m.get("end_date"),
            "trading_days_processed": m.get("trading_days_processed"),
            "trades_filled": totals.get("trades_filled"),
            "no_ops": totals.get("no_ops"),
            "size_capped_events": totals.get("size_capped_events"),
            "transaction_cost_dollars": totals.get("transaction_cost_dollars"),
            "borrow_cost_dollars": totals.get("borrow_cost_dollars"),
            "final_nav": m.get("final_nav"),
            "initial_capital": m.get("initial_capital"),
            "total_return": (metrics.get("total_return")
                             if isinstance(metrics, dict) else None),
            "max_drawdown_value": max_dd_value,
            "rule_version": m.get("rule_version"),
            "regime": m.get("regime"),
            "created_at": created_iso,
        })
    return jsonify({
        "status": "ok",
        "run_count": len(out),
        "runs": _sanitize_json_value(out),
    })


@app.route("/api/backtest/run/<run_id>")
def api_backtest_run_detail(run_id: str):
    """Return the full manifest.json for a specific backtest run, with
    inf/nan sanitised. Run-id must match the strict regex; otherwise
    HTTP 404 with status=not_found is returned."""
    run_dir = _resolve_run_dir(run_id)
    if run_dir is None:
        return jsonify({
            "status": "not_found",
            "run_id": run_id,
            "message": "run_id invalid or directory missing",
        }), 404
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        return jsonify({
            "status": "not_found",
            "run_id": run_id,
            "message": "manifest.json missing in run directory",
        }), 404
    try:
        with manifest_path.open("r", encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return jsonify({
            "status": "error",
            "message": f"failed to read manifest.json: {type(e).__name__}: {e}",
        }), 500
    return jsonify({
        "status": "ok",
        "run_id": run_id,
        "manifest": _sanitize_json_value(manifest),
    })


@app.route("/api/backtest/run/<run_id>/history")
def api_backtest_run_history(run_id: str):
    """Return NAV history rows + recomputed metrics for a specific run.
    Optional ?from=YYYY-MM-DD&to=YYYY-MM-DD slices the rows; metrics are
    re-derived on the slice (not on the full history)."""
    run_dir = _resolve_run_dir(run_id)
    if run_dir is None:
        return jsonify({
            "status": "not_found",
            "run_id": run_id,
            "message": "run_id invalid or directory missing",
        }), 404
    history_csv = run_dir / "pnl_history.csv"
    if not history_csv.exists():
        return jsonify({
            "status": "no_data",
            "run_id": run_id,
            "row_count": 0,
            "rows": [],
            "metrics": {},
            "message": "pnl_history.csv missing in run directory",
        })
    rows = load_nav_history(history_csv)
    from_date = request.args.get("from")
    to_date = request.args.get("to")
    filtered = _filter_history_by_date(rows, from_date, to_date)

    # Metrics on the slice. Mirrors the /api/portfolio/metrics
    # implementation: under 2 rows we cannot compute std-dependent metrics
    # so we return a partial dict + status=insufficient_data.
    if len(filtered) <= 1:
        nav = [float(r["total_nav"]) for r in filtered]
        dates = [r["as_of"] for r in filtered]
        from src.portfolio.metrics import (  # local; matches portfolio route
            max_drawdown, total_return,
        )
        metrics = {
            "sharpe_ratio": None,
            "sortino_ratio": None,
            "max_drawdown": max_drawdown(nav, dates=dates),
            "return_volatility": None,
            "total_return": total_return(nav),
        }
        metrics_status = "insufficient_data"
    else:
        from src.portfolio.metrics import (
            max_drawdown, return_volatility, sharpe_ratio, sortino_ratio,
            total_return,
        )
        nav = [float(r["total_nav"]) for r in filtered]
        dates = [r["as_of"] for r in filtered]
        # Same slice-leak guard as /api/portfolio/metrics: drop the
        # first row's daily_return (it references a NAV outside the slice
        # when from/to filters are active).
        returns = [
            float(r["daily_return"]) for r in filtered[1:]
            if r.get("daily_return") is not None
        ]
        metrics = {
            "sharpe_ratio": sharpe_ratio(returns),
            "sortino_ratio": sortino_ratio(returns),
            "max_drawdown": max_drawdown(nav, dates=dates),
            "return_volatility": return_volatility(returns),
            "total_return": total_return(nav),
        }
        metrics_status = "ok"

    return jsonify({
        "status": "ok",
        "run_id": run_id,
        "row_count": len(filtered),
        "first_date": filtered[0]["as_of"] if filtered else None,
        "last_date": filtered[-1]["as_of"] if filtered else None,
        "rows": _sanitize_json_value(filtered),
        "metrics": _sanitize_json_value(metrics),
        "metrics_status": metrics_status,
    })


# ═══════════════════════════════════════════════════════════════
# Backtest Lab — friction-adjusted 5-cell view (added 2026-05-08)
# Reads from data/decisions/<cell>_tx30bps_borrow100/ retrofit artifacts.
# Cost model = 15 bps single-side tx + 100% APY borrow on shorts (RULES.md
# v2.15 §5.16). rule_version v0.9.0_pass8_hardrule. Replaces the legacy
# data/backtest/<run_id>/ run-browser entirely.
# ═══════════════════════════════════════════════════════════════

_BACKTEST_LAB_CELLS = [
    {"id": "solo_mar",        "name": "Solo (1-agent)",                "phase": "phase1",
     "config": "single-agent baseline · no coordination · no ADaS",
     "dir": "phase1_cell1_solo_20260507_tx30bps_borrow100"},
    {"id": "noadas_mar",      "name": "Multi, no ADaS (4-agent)",      "phase": "phase1",
     "config": "multi-agent ensemble · ADaS disabled",
     "dir": "phase1_cell2_noadas_20260507_tx30bps_borrow100"},
    {"id": "default_mar",     "name": "Multi + ADaS (5-agent default)","phase": "phase1",
     "config": "production default · multi-agent + ADaS",
     "dir": "phase1_cell3_default_20260507_tx30bps_borrow100"},
    {"id": "default_apr",     "name": "Cell A · default (with SEC)",   "phase": "phase2",
     "config": "production default · all alt-data sources enabled",
     "dir": "phase1_apr_cell3_default_20260507_tx30bps_borrow100"},
    {"id": "no_sec_apr",      "name": "Cell B · SEC removed",          "phase": "phase2",
     "config": "ablation · SEC family removed (sec_edgar/form4/13f/def14a/8k_fulltext)",
     "dir": "phase1_apr_cell4_no_sec_20260507_tx30bps_borrow100"},
]

_BACKTEST_LAB_DECISIONS_ROOT = (
    Path(__file__).resolve().parent.parent.parent / "data" / "decisions"
)


def _bl_load_pnl(cell_dir: Path) -> tuple[list[dict], dict | None, str | None]:
    """Return (rows, summary, error). rows = list of dicts from
    adjusted_pnl_history.csv with float coercion; summary = adjusted_summary.json
    parsed; error = string explanation if any file is missing."""
    pnl_path = cell_dir / "adjusted_pnl_history.csv"
    sum_path = cell_dir / "adjusted_summary.json"
    if not pnl_path.exists():
        return [], None, f"missing {pnl_path.name} at {cell_dir}"
    if not sum_path.exists():
        return [], None, f"missing {sum_path.name} at {cell_dir}"
    rows: list[dict] = []
    import csv as _csv
    with pnl_path.open("r", encoding="utf-8") as f:
        for r in _csv.DictReader(f):
            rows.append({
                "as_of": r["as_of"],
                "orig_total_nav": float(r["orig_total_nav"]),
                "tx_only_nav": float(r["tx_only_nav"]),
                "combined_nav": float(r["combined_nav"]),
                "daily_tx_cost_usd": float(r["daily_tx_cost_usd"]),
                "daily_borrow_cost_usd": float(r["daily_borrow_cost_usd"]),
                "cumulative_tx_cost_usd": float(r["cumulative_tx_cost_usd"]),
                "cumulative_borrow_cost_usd": float(r["cumulative_borrow_cost_usd"]),
            })
    with sum_path.open("r", encoding="utf-8") as f:
        summary = json.load(f)
    return rows, summary, None


def _bl_load_forward(cell_dir: Path) -> tuple[list[dict], str | None]:
    fwd_path = cell_dir / "adjusted_forward_nav.csv"
    if not fwd_path.exists():
        return [], None  # not an error for Phase 1 cells
    rows: list[dict] = []
    import csv as _csv
    with fwd_path.open("r", encoding="utf-8") as f:
        for r in _csv.DictReader(f):
            rows.append({
                "as_of": r["date"],
                "nav_orig_forward": float(r["nav_orig_forward"]),
                "nav_combined_forward": float(r["nav_combined_forward"]),
                "daily_fwd_tx_cost_usd": float(r["daily_fwd_tx_cost_usd"]),
                "daily_fwd_borrow_cost_usd": float(r["daily_fwd_borrow_cost_usd"]),
            })
    return rows, None


def _bl_max_drawdown(rows: list[dict], nav_field: str) -> dict:
    if not rows:
        return {"value_pct": 0.0, "trough_date": None, "peak_date": None}
    peak = -1.0; peak_d = rows[0]["as_of"]
    worst = 0.0; worst_d = rows[0]["as_of"]; worst_peak = rows[0]["as_of"]
    for r in rows:
        v = r[nav_field]
        if v > peak:
            peak = v; peak_d = r["as_of"]
        if peak > 0:
            dd = v / peak - 1.0
            if dd < worst:
                worst = dd; worst_d = r["as_of"]; worst_peak = peak_d
    return {"value_pct": round(worst * 100, 4),
            "trough_date": worst_d, "peak_date": worst_peak}


def _bl_count_events(cell_dir: Path) -> dict:
    """Walk adjusted_trade_ledger.csv to count event kinds and tally UST face."""
    ledger = cell_dir / "adjusted_trade_ledger.csv"
    counts = {"entry": 0, "exit": 0, "bil_bootstrap": 0, "ust_purchases": 0,
              "ust_face_total": 0.0}
    if not ledger.exists():
        return counts
    import csv as _csv
    with ledger.open("r", encoding="utf-8") as f:
        for r in _csv.DictReader(f):
            kind = r.get("kind", "")
            if kind in counts:
                counts[kind] += 1
            if kind == "entry" and r.get("sleeve") == "fixed_income":
                counts["ust_purchases"] += 1
                try:
                    counts["ust_face_total"] += float(r.get("notional_usd") or 0.0)
                except (TypeError, ValueError):
                    pass
    return counts


@app.route("/api/backtest_lab/cells")
def api_backtest_lab_cells():
    out_cells: list[dict] = []
    canonical = {
        "solo_mar":     {"final_nav": 1_030_639, "ret_pct": 3.06,
                         "maxdd_pct": -0.32,  "maxdd_date": "2025-03-14"},
        "noadas_mar":   {"final_nav": 1_124_389, "ret_pct": 12.44,
                         "maxdd_pct": -0.40,  "maxdd_date": "2025-03-20"},
        "default_mar":  {"final_nav": 1_133_611, "ret_pct": 13.36,
                         "maxdd_pct": -0.71,  "maxdd_date": "2025-03-20"},
        "default_apr":  {"final_nav": 1_127_446, "ret_pct": 12.74,
                         "fwd_nav_5_30": 1_170_083, "fwd_ret_pct": 17.01},
        "no_sec_apr":   {"final_nav": 1_146_762, "ret_pct": 14.68,
                         "fwd_nav_5_30": 1_177_182, "fwd_ret_pct": 17.72},
    }
    for spec in _BACKTEST_LAB_CELLS:
        cell_dir = _BACKTEST_LAB_DECISIONS_ROOT / spec["dir"]
        rows, summary, err = _bl_load_pnl(cell_dir)
        fwd_rows, _ = _bl_load_forward(cell_dir)
        events = _bl_count_events(cell_dir)
        if err:
            out_cells.append({
                **spec,
                "status": "missing",
                "data_quality_flag": err,
                "dir_path": str(cell_dir),
            })
            continue
        mdd = _bl_max_drawdown(rows, "combined_nav")
        final_nav = rows[-1]["combined_nav"] if rows else 0.0
        ret_pct = round((final_nav / 1_000_000.0 - 1.0) * 100, 4) if rows else 0.0
        # canonical-mismatch surface
        warnings = []
        c = canonical.get(spec["id"], {})
        if c:
            nav_drift = abs(final_nav - c["final_nav"])
            if nav_drift > 1000:
                warnings.append(
                    f"final NAV drifted ${nav_drift:,.2f} vs canonical ${c['final_nav']:,}")
            ret_drift = abs(ret_pct - c["ret_pct"])
            if ret_drift > 0.05:
                warnings.append(
                    f"return drifted {ret_drift:.2f}pp vs canonical {c['ret_pct']:.2f}%")
        forward_block = None
        if fwd_rows:
            fwd_final = fwd_rows[-1]["nav_combined_forward"]
            fwd_ret = round((fwd_final / 1_000_000.0 - 1.0) * 100, 4)
            forward_block = {
                "rows": fwd_rows,
                "final_nav": round(fwd_final, 2),
                "final_ret_pct": fwd_ret,
                "first_date": fwd_rows[0]["as_of"],
                "last_date": fwd_rows[-1]["as_of"],
                "n_obs": len(fwd_rows),
                "tx_total_usd": round(sum(r["daily_fwd_tx_cost_usd"] for r in fwd_rows), 2),
                "borrow_total_usd": round(sum(r["daily_fwd_borrow_cost_usd"] for r in fwd_rows), 2),
            }
            if c.get("fwd_nav_5_30"):
                if abs(fwd_final - c["fwd_nav_5_30"]) > 1000:
                    warnings.append(
                        f"forward NAV drifted ${abs(fwd_final - c['fwd_nav_5_30']):,.2f} vs canonical ${c['fwd_nav_5_30']:,}")
        out_cells.append({
            **spec,
            "status": "ok",
            "dir_path": str(cell_dir),
            "first_date": rows[0]["as_of"] if rows else None,
            "last_date": rows[-1]["as_of"] if rows else None,
            "n_obs": len(rows),
            "rows": rows,
            "final_nav": round(final_nav, 2),
            "return_pct": ret_pct,
            "max_drawdown": mdd,
            "total_tx_cost_usd": summary.get("total_tx_cost_usd"),
            "total_borrow_cost_usd": summary.get("total_borrow_cost_usd"),
            "total_combined_friction_usd": summary.get("total_combined_friction_usd"),
            "n_trade_events": summary.get("n_trade_events"),
            "n_short_closes": summary.get("n_short_closes"),
            "median_short_holding_days": summary.get("median_short_holding_days"),
            "events": events,
            "forward": forward_block,
            "warnings": warnings,
        })
    return jsonify({
        "status": "ok",
        "rule_version": "v0.9.0_pass8_hardrule",
        "regression_hash": "sha256:626266c71956d0baec9252d6c9845388a3c324b193d707d2e0e0d75ae2d979bb",
        "doc_version": "v2.15",
        "cost_model": {
            "tx_bps_single_side": 15.0,
            "borrow_apy_pct": 100.0,
            "borrow_days_per_year": 365,
            "spec_ref": "RULES.md §5.16 v2.15",
        },
        "cells": _sanitize_json_value(out_cells),
    })


# ═══════════════════════════════════════════════════════════════
# NEW: API Status Endpoint (TASK 7.1)
# ═══════════════════════════════════════════════════════════════

@app.route("/api/status")
def api_status():
    """
    Return API connection status for all adapters.
    Shows: which APIs connected, mock vs live, timestamp availability,
    FMP plan + rate-limit + cache telemetry, and a SAFE key fingerprint
    (no key value is ever returned).
    """
    from src.data_adapters import fmp_adapter as fa
    market_status = get_api_status()
    validation = validate_all_adapters()

    rate = fa.get_rate_limit_status()
    fp = fa.get_key_fingerprint()
    market_status["plan"] = "premium"
    market_status["rate_limit_configured_per_minute"] = rate["configured_max_per_minute"]
    market_status["rolling_minute_count"] = rate["current_rolling_minute_count"]
    market_status["last_call_at"] = rate["last_call_at"]
    market_status["sticky_paused"] = rate["sticky_paused"]
    market_status["sticky_pause_reason"] = rate["sticky_pause_reason"]
    market_status["sticky_pause_seconds_remaining"] = rate["sticky_pause_seconds_remaining"]
    market_status["cache_stats"] = fa.get_cache_stats()
    market_status["call_groups"] = fa.get_call_group_summary()
    market_status["auto_run_pipeline"] = "disabled"
    market_status["cache_enabled"] = True
    market_status["cache_ttl_seconds"] = {
        "quote": fa.CACHE_TTL_QUOTE,
        "intraday": fa.CACHE_TTL_INTRADAY,
        "technical": fa.CACHE_TTL_TECHNICAL,
        "profile": fa.CACHE_TTL_PROFILE,
        "fundamentals": fa.CACHE_TTL_FUNDAMENTALS,
        "calendar": fa.CACHE_TTL_CALENDAR,
        "dcf": fa.CACHE_TTL_DCF,
        "search": fa.CACHE_TTL_SEARCH,
    }
    # Safe key metadata — fingerprint is sha256(key)[:8]; original key
    # cannot be recovered from this value.
    market_status["key_set"] = fp["key_set"]
    market_status["key_length"] = fp["key_length"]
    market_status["key_fingerprint"] = fp["key_fingerprint"]
    market_status["key_had_outer_whitespace"] = fp["had_outer_whitespace"]
    market_status["key_loaded_at"] = _KEY_LOADED_AT
    market_status["env_change_requires_restart"] = (
        "No — load_dotenv(override=True) is used at app import. "
        "After rotating .env, restart the Flask process OR POST /api/fmp/clear_cache "
        "to drop sticky-pause + cached failure state, then refresh the page."
    )

    return jsonify({
        "rule_version": RULE_VERSION,
        "agent_prompt_version": AGENT_PROMPT_VERSION,
        "market_data_status": market_status,
        "adapter_validation": validation,
    })


@app.route("/api/fmp/clear_cache", methods=["POST"])
def api_fmp_clear_cache():
    """Operator endpoint — drop FMP sticky-pause, in-process cache, and the
    dashboard-level _quota_exhausted flag. Used after rotating FMP_API_KEY
    so the next call exercises the new key instead of replaying stale state.
    Never returns the key itself; only its fingerprint (sha256[:8])."""
    from src.data_adapters import fmp_adapter as fa
    global _quota_exhausted, _quota_exhausted_at, _LIVE_PRICE_CACHE
    pause_status = fa.reset_sticky_pause()
    cache_status = fa.clear_cache()
    prior_quota = _quota_exhausted
    _quota_exhausted = False
    _quota_exhausted_at = None
    _LIVE_PRICE_CACHE.clear()
    fp = fa.get_key_fingerprint()
    return jsonify({
        "ok": True,
        "sticky_pause": pause_status,
        "fmp_cache": cache_status,
        "live_price_cache_cleared": True,
        "prior_quota_exhausted_flag": prior_quota,
        "key_set": fp["key_set"],
        "key_length": fp["key_length"],
        "key_fingerprint": fp["key_fingerprint"],
        "generated_at": datetime.now(ZoneInfo("UTC")).isoformat(),
    })


@app.route("/api/fmp/search")
def api_fmp_search():
    """Lightweight ticker / company-name search.

    - `q` must be at least 2 chars (frontend already enforces this; we
      double-check on the server).
    - Results are cached for ~12h per lower-cased query to keep call
      counts low.
    - On total live failure, returns `source=live_fmp_failed` and an
      explicit error block so the UI can show "FMP search failed".
    """
    from src.data_adapters import fmp_adapter as fa
    from src.data_adapters.market_data import _use_live_fmp

    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({
            "query": q,
            "results": [],
            "count": 0,
            "source": "live_fmp_failed",
            "errors": [{"reason": "query too short (min 2 chars)"}],
            "live_mode": _use_live_fmp(),
            "served_from_cache": False,
            "generated_at": datetime.now(ZoneInfo("UTC")).isoformat(),
        }), 400

    if not _use_live_fmp():
        return jsonify({
            "query": q,
            "results": [],
            "count": 0,
            "source": "mock_fallback",
            "live_mode": False,
            "errors": [{"reason": "DATA_MODE != live — search disabled"}],
            "served_from_cache": False,
            "generated_at": datetime.now(ZoneInfo("UTC")).isoformat(),
        })

    us_only = (request.args.get("us_only", "true").lower() != "false")
    try:
        limit = max(1, min(int(request.args.get("limit", "10")), 25))
    except ValueError:
        limit = 10

    payload = fa.search_symbols(q, limit=limit, us_only=us_only)
    payload["live_mode"] = True
    payload["generated_at"] = datetime.now(ZoneInfo("UTC")).isoformat()
    payload["rate_limit"] = fa.get_rate_limit_status()
    payload["cache_stats"] = fa.get_cache_stats()
    return jsonify(payload)


# ═══════════════════════════════════════════════════════════════
# NEW: Backtest Integrity Endpoint (TASK 7.2)
# ═══════════════════════════════════════════════════════════════

@app.route("/api/integrity")
def api_integrity():
    """
    Return backtest integrity status from the last pipeline run.
    Shows: backtest period, execution timing, filing-date rule,
    no-look-ahead status, rule version.
    """
    if not _last_result:
        return jsonify({"error": "No pipeline run yet. Run the pipeline first."}), 400

    integrity_results = _last_result.get("integrity_results", {})
    total = len(integrity_results)
    violations = sum(1 for v in integrity_results.values() if not v.get("valid", True))

    return jsonify({
        "rule_version": _last_result.get("rule_version", RULE_VERSION),
        "data_mode": _last_result.get("data_mode", "mock"),
        "decision_date": _last_result.get("date"),
        "decision_timestamp": _last_result.get("decision_timestamp"),
        "execution_timestamp": _last_result.get("execution_timestamp"),
        "execution_timing_rule": "Signal Day T close -> trade Day T+1 open",
        "filing_date_rule": "Use SEC filing_date, not fiscal period end date",
        "no_look_ahead_status": "VERIFIED" if violations == 0 else "VIOLATIONS FOUND",
        "total_tickers_checked": total,
        "tickers_passed": total - violations,
        "tickers_failed": violations,
        "integrity_results": integrity_results,
    })


# ═══════════════════════════════════════════════════════════════
# NEW: Evidence Packet Viewer Endpoint (TASK 7.3)
# ═══════════════════════════════════════════════════════════════

@app.route("/api/evidence/<ticker>")
def api_evidence(ticker):
    """
    Return the evidence packet for a specific ticker from the last pipeline run.
    Shows exactly what data each agent saw.
    """
    if not _last_result:
        return jsonify({"error": "No pipeline run yet. Run the pipeline first."}), 400

    packets = _last_result.get("evidence_packets", {})
    packet = packets.get(ticker.upper())

    if not packet:
        available = list(packets.keys())
        return jsonify({
            "error": f"No evidence packet for {ticker.upper()}",
            "available_tickers": available,
        }), 404

    return jsonify(packet)


@app.route("/api/evidence")
def api_evidence_all():
    """Return evidence packet summary for all tickers in the last run."""
    if not _last_result:
        return jsonify({"error": "No pipeline run yet."}), 400

    packets = _last_result.get("evidence_packets", {})
    summaries = {}
    for ticker, packet in packets.items():
        summaries[ticker] = {
            "decision_date": packet.get("decision_date"),
            "execution_timestamp": packet.get("execution_timestamp"),
            "data_mode": packet.get("data_mode"),
            "data_availability": packet.get("data_availability", {}),
        }
    return jsonify(summaries)


# ═══════════════════════════════════════════════════════════════
# Macro & Pipeline Tab
# ═══════════════════════════════════════════════════════════════

@app.route("/macro")
def macro_pipeline():
    """Render the Macro & Pipeline tab."""
    return render_template("macro_pipeline.html")


@app.route("/api/macro")
def api_macro():
    """Live dashboard mode.

    Always uses today_ET as both dashboard_run_date and cache_date.
    Stale-date FRED caches are never loaded as live data. The four date
    concepts (dashboard_run_date, cache_date, observation_date_by_series,
    decision_date) are returned in the `dates` block.

    Note: backtest pipeline state in `_last_result` is intentionally NOT
    reused here — live macro state must be independent of historical
    pipeline runs.
    """
    indicators = get_macro_indicators_for_dashboard()
    output = classify_macro_regime(indicators)
    output["dates"] = build_date_metadata(indicators)
    # Keep top-level field for backwards compat with existing JS readers
    output["dashboard_run_date"] = output["dates"]["dashboard_run_date"]
    return jsonify(output)


@app.route("/api/yield_curve")
def api_yield_curve():
    """Yield curve for charting. Live dashboard mode (today_ET only)."""
    indicators = get_macro_indicators_for_dashboard()
    result = get_yield_curve_data(indicators=indicators)
    result["dates"] = build_date_metadata(indicators)
    result["dashboard_run_date"] = result["dates"]["dashboard_run_date"]
    return jsonify(result)


@app.route("/api/fred_validation")
def api_fred_validation():
    """FRED API validation report. Live dashboard mode (today_ET only)."""
    indicators = get_macro_indicators_for_dashboard()
    result = generate_validation_report(indicators=indicators)
    result["dates"] = build_date_metadata(indicators)
    result["dashboard_run_date"] = result["dates"]["dashboard_run_date"]
    return jsonify(result)


@app.route("/api/fred_refresh", methods=["POST"])
def api_fred_refresh():
    """Force refresh FRED data (bypass today's daily cache).

    Always returns JSON. On error returns HTTP 500 with `success: false`
    and an `error` string so the frontend can display it instead of hanging.
    """
    try:
        indicators = get_macro_indicators_for_dashboard(force_refresh=True)
        non_null = sum(1 for k, v in indicators.items()
                       if not k.startswith("_") and isinstance(v, dict)
                       and v.get("value") is not None)
        dates = build_date_metadata(indicators)
        return jsonify({
            "success": True,
            "dates": dates,
            "dashboard_run_date": dates.get("dashboard_run_date"),
            "data_mode": dates.get("data_mode"),
            "source": dates.get("source"),
            "series_retrieved": non_null,
            "timestamp": _now_et_iso(),
        })
    except Exception as e:
        # Never let an exception escape as an unparseable HTML 500 page.
        logger.warning(f"FRED refresh failed: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "timestamp": _now_et_iso(),
        }), 500


@app.route("/api/macro_evidence_packet")
def api_macro_evidence_packet():
    """Full macro evidence packet with point-in-time metadata.

    Live dashboard mode: dashboard_run_date == cache_date == today_ET.
    decision_date is reported as today_ET (live mode only — backtest packets
    use a different code path).
    """
    indicators = get_macro_indicators_for_dashboard()
    regime = classify_macro_regime(indicators)
    dates = build_date_metadata(indicators)
    dashboard_run_date = dates["dashboard_run_date"]

    decision_ts = _now_et_iso()
    fred_series_used = []
    for key, ind in indicators.items():
        if key.startswith("_") or not isinstance(ind, dict):
            continue
        fred_series_used.append({
            "series_id": ind.get("series_id", ""),
            "observation_date": ind.get("observation_date", ""),
            "value": ind.get("value"),
            "realtime_start": ind.get("realtime_start", ""),
            "realtime_end": ind.get("realtime_end", ""),
            "data_available_as_of": ind.get("data_available_as_of", ""),
            "conservative_lag_applied": ind.get("conservative_lag_applied", False),
            "missing_data_flag": ind.get("missing_data_flag", False),
            "synthetic_observation": ind.get("synthetic_observation", False),
        })

    return jsonify({
        "dates": dates,
        "dashboard_run_date": dashboard_run_date,
        "decision_date": dashboard_run_date,
        "decision_timestamp": decision_ts,
        "macro_regime": regime.get("macro_regime"),
        "macro_condition": regime.get("macro_condition"),
        "condition_reason": regime.get("condition_reason"),
        "regime_label": regime.get("regime_label"),
        "stress_score": regime.get("stress_score"),
        "stress_triggers": regime.get("stress_triggers", []),
        "macro_confidence": regime.get("macro_confidence"),
        "fixed_income_base_allocation": regime.get("fixed_income_base_allocation"),
        "equity_allocation_cap": regime.get("equity_allocation_cap"),
        "lookahead_safe": regime.get("lookahead_safe"),
        "data_available_as_of": regime.get("data_available_as_of"),
        "fred_series_used": fred_series_used,
        "fred_values_used": regime.get("fred_values_used", {}),
        "evidence_summary": regime.get("macro_evidence_summary", []),
        "warnings": regime.get("missing_data_warnings", []),
        "classifier_version": regime.get("classifier_version"),
        "cache_status": get_cache_status(dashboard_run_date),
    })


@app.route("/api/fred_cache_status")
def api_fred_cache_status():
    """Return FRED cache status for today only.

    Stale-date caches existing on disk are ignored — only today's cache
    file (`fred_<today_ET>.json`) is considered relevant to live dashboard
    state. Cache_date is invariant: it ALWAYS equals dashboard_run_date in
    live mode.
    """
    dashboard_run_date = _today_et()
    cache = get_cache_status(dashboard_run_date)
    cache["dashboard_run_date"] = dashboard_run_date
    if cache.get("cached"):
        cache["source"] = "daily_cache"
    elif cache.get("data_mode") == "mock":
        cache["source"] = "mock_fallback"
    else:
        cache["source"] = "fresh_api"
    cache["dates"] = {
        "dashboard_run_date": dashboard_run_date,
        "cache_date": dashboard_run_date,
        "decision_date": None,
        "data_mode": cache.get("data_mode", "unknown"),
        "source": cache["source"],
        "fetched_at": cache.get("fetched_at"),
        "from_cache": cache.get("cached", False),
    }
    return jsonify(cache)


@app.route("/api/fmp/ticker_inspector")
def api_ticker_inspector():
    """Consolidated FMP Premium inspector for one ticker.

    Returns six independent blocks (quote, intraday, technicals,
    fundamentals, calendar, dcf). Each block carries its own source
    flag and PIT-safety hint. All FMP calls go through the rate
    limiter + per-block cache in fmp_adapter.
    """
    from src.data_adapters import fmp_adapter as fa
    from src.data_adapters.market_data import _use_live_fmp

    ticker_raw = (request.args.get("ticker") or "AAPL").strip().upper()
    interval = (request.args.get("interval") or "5min").strip()
    if interval not in fa.VALID_INTRADAY_INTERVALS:
        interval = "5min"

    # Hard reject obvious garbage so we don't waste calls on the rate limit.
    if not (1 <= len(ticker_raw) <= 10) or not ticker_raw.isalnum():
        return jsonify({
            "ticker": ticker_raw,
            "error": "invalid ticker",
        }), 400

    live = _use_live_fmp()

    if not live:
        # Live mode disabled — return a clean mock-fallback shell so
        # the UI never displays mock as live.
        return jsonify({
            "ticker": ticker_raw,
            "interval": interval,
            "live_mode": False,
            "source": "mock_fallback",
            "blocks": {
                "quote": {"source": "mock_fallback", "status": "Data unavailable"},
                "intraday": {"source": "mock_fallback", "status": "Data unavailable"},
                "technicals": {"source": "mock_fallback", "status": "Data unavailable"},
                "fundamentals": {"source": "mock_fallback", "status": "Data unavailable"},
                "calendar": {"source": "mock_fallback", "status": "Data unavailable"},
                "dcf": {"source": "mock_fallback", "status": "Data unavailable"},
            },
            "rate_limit": fa.get_rate_limit_status(),
            "cache_stats": fa.get_cache_stats(),
            "call_groups": fa.get_call_group_summary(),
            "warning": "DATA_MODE != live — live FMP calls are disabled.",
            "generated_at": datetime.now(ZoneInfo("UTC")).isoformat(),
        })

    # Fan out the 10 independent FMP wrapper calls concurrently. Each
    # wrapper internally consults _TTLCache (thread-safe) before hitting
    # FMP, so warm-cache loads stay near-instant. The rate-limiter and
    # call-group counters are also thread-safe. Critical path on cold
    # cache is the slowest *single wrapper*, not the sum.
    with ThreadPoolExecutor(max_workers=8) as ex:
        fut_quote      = ex.submit(fa.get_quote, ticker_raw)
        # Wide chart wrapper so the UI's TF selector can slice
        # 1D/3D/1W from the same cached payload (no extra API call).
        fut_intraday   = ex.submit(fa.get_chart_intraday, ticker_raw, interval=interval)
        fut_sma10      = ex.submit(fa.get_technical_indicator, ticker_raw, "sma", 10, "1day")
        fut_sma20      = ex.submit(fa.get_technical_indicator, ticker_raw, "sma", 20, "1day")
        fut_ema20      = ex.submit(fa.get_technical_indicator, ticker_raw, "ema", 20, "1day")
        fut_rsi14      = ex.submit(fa.get_technical_indicator, ticker_raw, "rsi", 14, "1day")
        fut_fundamentals = ex.submit(fa.get_fundamentals_snapshot, ticker_raw)
        fut_calendar   = ex.submit(fa.get_corporate_calendar, ticker_raw)
        fut_dcf        = ex.submit(fa.get_dcf_valuation, ticker_raw)
        fut_profile    = ex.submit(fa.get_company_profile, ticker_raw)

    quote = fut_quote.result()
    intraday = fut_intraday.result()
    technicals = {
        "sma_10": fut_sma10.result(),
        "sma_20": fut_sma20.result(),
        "ema_20": fut_ema20.result(),
        "rsi_14": fut_rsi14.result(),
    }
    fundamentals = fut_fundamentals.result()
    calendar = fut_calendar.result()
    dcf = fut_dcf.result()
    profile = fut_profile.result()

    # Roll up: any block with source != live_fmp is a fallback signal.
    block_sources = {
        "quote": quote.get("source"),
        "intraday": intraday.get("source"),
        "technicals": next(iter(technicals.values())).get("source") if technicals else None,
        "fundamentals": fundamentals.get("source"),
        "calendar": calendar.get("source"),
        "dcf": dcf.get("source"),
    }
    fallback_count = sum(1 for s in block_sources.values() if s != "live_fmp")

    rate = fa.get_rate_limit_status()
    return jsonify({
        "ticker": ticker_raw,
        "interval": interval,
        "live_mode": True,
        "active_source": "live_fmp",
        "company_name": profile.get("name") if profile.get("source") == "live_fmp" else None,
        "blocks": {
            "quote": quote,
            "intraday": intraday,
            "technicals": technicals,
            "fundamentals": fundamentals,
            "calendar": calendar,
            "dcf": dcf,
        },
        "block_sources": block_sources,
        "fallback_count": fallback_count,
        "rate_limit": rate,
        "cache_stats": fa.get_cache_stats(),
        "call_groups": fa.get_call_group_summary(),
        "warning": (
            "FMP rate-limit pause active — try again in a moment."
            if rate.get("sticky_paused") else None
        ),
        "generated_at": datetime.now(ZoneInfo("UTC")).isoformat(),
    })


@app.route("/api/fmp/chart")
def api_fmp_chart():
    """Multi-timeframe chart data for the Market Data tab.

    `kind=intraday` -> 5-minute bars (60 s TTL, ~7 trading days).
    `kind=daily`    -> daily EOD bars (12 h TTL, full history).
    The frontend slices each payload to whatever timeframe is active,
    so switching between 1D/3D/1W (or 1M/3M/1Y/5Y/ALL) reuses one
    cached fetch.
    """
    from src.data_adapters import fmp_adapter as fa
    from src.data_adapters.market_data import _use_live_fmp

    ticker_raw = (request.args.get("ticker") or "").strip().upper()
    kind = (request.args.get("kind") or "intraday").strip().lower()
    if not (1 <= len(ticker_raw) <= 10) or not ticker_raw.isalnum():
        return jsonify({"error": "invalid ticker"}), 400
    if kind not in ("intraday", "daily"):
        return jsonify({"error": "kind must be 'intraday' or 'daily'"}), 400

    if not _use_live_fmp():
        return jsonify({
            "ticker": ticker_raw, "kind": kind,
            "source": "mock_fallback",
            "status": "Data unavailable",
            "bars": [], "row_count": 0,
            "warning": "DATA_MODE != live — live FMP calls are disabled.",
        })

    if kind == "intraday":
        payload = fa.get_chart_intraday(ticker_raw)
    else:
        payload = fa.get_chart_daily(ticker_raw)

    rate = fa.get_rate_limit_status()
    return jsonify({
        **payload,
        "kind": kind,
        "ticker": ticker_raw,
        "rate_limit": rate,
        "cache_stats": fa.get_cache_stats(),
        "call_groups": fa.get_call_group_summary(),
        "generated_at": datetime.now(ZoneInfo("UTC")).isoformat(),
    })


@app.route("/api/live_price_check")
def api_live_price_check():
    """Lightweight live-FMP confirmation for the Overview panel.

    - Caches each ticker for 5 min so repeated panel loads don't
      repeatedly hit FMP.
    - On HTTP 402 / 429 (paywall or rate limit), sets a sticky
      _quota_exhausted flag and stops further calls; serves stale cache.
    - Mock-fallback rows are tagged source=mock_fallback and never
      pretend to be live.
    """
    global _quota_exhausted, _quota_exhausted_at
    from src.data_adapters.market_data import get_quote, _use_live_fmp
    from src.data_adapters.fmp_adapter import FMP_BASE_URL

    now = datetime.now(ZoneInfo("UTC"))
    rows: list[dict] = []
    live_count = 0
    fallback_count = 0
    cache_hits = 0

    for ticker in _LIVE_CHECK_TICKERS:
        cached = _LIVE_PRICE_CACHE.get(ticker)
        cache_age_s: float | None = None
        if cached:
            cache_age_s = (now - cached["ts"]).total_seconds()

        served_from_cache = False
        served_stale = False
        row: dict

        if cached and cache_age_s is not None and cache_age_s < _LIVE_PRICE_TTL_SECONDS:
            row = dict(cached["row"])
            served_from_cache = True
            cache_hits += 1
        elif _quota_exhausted and cached:
            row = dict(cached["row"])
            served_from_cache = True
            served_stale = True
        elif _quota_exhausted:
            # Sticky: do NOT retry FMP after we've seen 402/429 today.
            row = {
                "ticker": ticker,
                "price": None,
                "previous_close": None,
                "change_pct": None,
                "volume": None,
                "timestamp": None,
                "source": "mock_fallback",
                "fallback_reason": "quota_exhausted_no_retry",
            }
        elif not _use_live_fmp():
            row = {
                "ticker": ticker,
                "price": None,
                "previous_close": None,
                "change_pct": None,
                "volume": None,
                "timestamp": None,
                "source": "mock_fallback",
                "fallback_reason": "live mode disabled",
            }
        else:
            q = get_quote(ticker)
            if q.get("source") == "live_fmp":
                row = {
                    "ticker": q.get("ticker", ticker),
                    "price": q.get("price"),
                    "previous_close": q.get("previous_close"),
                    "change_pct": q.get("change_pct"),
                    "volume": q.get("volume"),
                    "timestamp": q.get("timestamp"),
                    "source": "live_fmp",
                    "fallback_reason": None,
                }
                _LIVE_PRICE_CACHE[ticker] = {"row": dict(row), "ts": now}
            else:
                err_class = q.get("error_class") or ""
                http_status = q.get("http_status")
                if http_status in (402, 429) or err_class.startswith("HTTP402") \
                        or err_class.startswith("HTTP429"):
                    _quota_exhausted = True
                    _quota_exhausted_at = now.isoformat()
                if cached:
                    row = dict(cached["row"])
                    served_from_cache = True
                    served_stale = True
                else:
                    row = {
                        "ticker": ticker,
                        "price": None,
                        "previous_close": None,
                        "change_pct": None,
                        "volume": None,
                        "timestamp": None,
                        "source": "mock_fallback",
                        "fallback_reason": (q.get("error_short")
                                            or err_class
                                            or "live call failed"),
                    }

        row["served_from_cache"] = served_from_cache
        row["served_stale"] = served_stale
        if cache_age_s is not None:
            row["cache_age_s"] = round(cache_age_s, 1)

        if row["source"] == "live_fmp":
            live_count += 1
        else:
            fallback_count += 1
        rows.append(row)

    data_mode = os.environ.get("DATA_MODE", "mock").strip().lower()
    use_mock = os.environ.get("USE_MOCK_DATA", "true").strip().lower()
    api_key_set = bool(os.environ.get("FMP_API_KEY", "").strip())

    return jsonify({
        "panel_title": "Live FMP Price Check",
        "tickers": list(_LIVE_CHECK_TICKERS),
        "rows": rows,
        "summary": {
            "live_count": live_count,
            "mock_fallback_count": fallback_count,
            "cache_hits": cache_hits,
            "cache_ttl_seconds": _LIVE_PRICE_TTL_SECONDS,
        },
        "fmp": {
            "active_source": "live_fmp" if _use_live_fmp() else "mock",
            "base_url": FMP_BASE_URL,
            "api_key_set": api_key_set,
            "legacy_v3_disabled": True,
            "data_mode": data_mode,
            "use_mock_flag": use_mock,
        },
        "quota_exhausted": _quota_exhausted,
        "quota_exhausted_at": _quota_exhausted_at,
        "warning": ("FMP quota may be exhausted. Try again after quota reset."
                    if _quota_exhausted else None),
        "generated_at": now.isoformat(),
    })


def main():
    print("\n  [*] Alt-Data Agentic Long-Short Dashboard")
    print(f"  [*] Rule Version: {RULE_VERSION}")
    print("  -> http://localhost:5000")
    print("  -> http://localhost:5000/macro  (Macro & Pipeline)\n")
    app.run(host="0.0.0.0", port=5000, debug=True)


if __name__ == "__main__":
    main()
