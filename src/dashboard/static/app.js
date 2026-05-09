/* ═══════════════════════════════════════════════
   Alt-Data Agentic Long-Short — Dashboard JS v0.2
   Valuation-aware with thematic research + decision trace
   ═══════════════════════════════════════════════ */

let currentResult = null;

// ── Run Pipeline ──
async function runPipeline() {
    const date = document.getElementById('date-select').value;
    const regime = document.getElementById('regime-select').value;
    showLoading('Running Agent Pipeline...');
    try {
        const response = await fetch('/api/run', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ date, regime, skip_alt_data: false }),
        });
        const result = await response.json();
        currentResult = result;
        renderDashboard(result);
    } catch (err) {
        console.error('Pipeline error:', err);
        alert('Pipeline execution failed. Check console.');
    } finally {
        hideLoading();
    }
}

// ── Run Backtest ──
async function runBacktest() {
    const regime = document.getElementById('regime-select').value;
    showLoading('Running Backtest Comparison...');
    try {
        const response = await fetch('/api/backtest', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ regime }),
        });
        const comparison = await response.json();
        renderBacktest(comparison);
    } catch (err) {
        console.error('Backtest error:', err);
        alert('Backtest failed. Check console.');
    } finally {
        hideLoading();
    }
}

// ── Loading ──
function showLoading(text) {
    const overlay = document.getElementById('loading-overlay');
    overlay.querySelector('.loading-text').textContent = text;
    overlay.style.display = 'flex';
}
function hideLoading() {
    document.getElementById('loading-overlay').style.display = 'none';
}

// ── Main Render ──
// NOTE: We deliberately do NOT overwrite the DASHBOARD DATE chip with
// `result.date` (the scenario replay date). The dashboard date chip
// represents "today" and stays live. Scenario replay date appears in
// the labelled selector at top-right and inside the replay summary.
function renderDashboard(result) {
    document.getElementById('regime-value').textContent = result.regime.toUpperCase();
    // Dashboard refresh 2026-05-08: ALT-DATA chip reflects production state
    // — 8+ live adapters wired (SEC family, GitHub, Wikipedia, FMP, Polygon).
    // Per-run skip_alt_data is surfaced in the sub-text only.
    const altEl = document.getElementById('altdata-status');
    if (altEl) {
        altEl.textContent = 'FRAMEWORK';
        altEl.className = 'chip-value';
    }
    const altSubEl = document.getElementById('altdata-sub');
    if (altSubEl) {
        altSubEl.textContent = result.skip_alt_data
            ? '8+ live adapters · this run skipped alt-data'
            : '8+ live adapters (SEC, GitHub, Wikipedia, FMP, Polygon)';
    }
    const altChip = document.getElementById('chip-altdata');
    if (altChip) {
        altChip.title = result.skip_alt_data
            ? '8+ live adapters wired (SEC family, GitHub, Wikipedia, FMP, Polygon). This pipeline run was started with skip_alt_data=true.'
            : '8+ live adapters wired (SEC EDGAR / Form 4 / 13F / DEF 14A, GitHub public, Wikipedia pageviews, FMP news + analyst grades + corporate calendar, Polygon news) plus OpenCLI GitHub commit-messages adapter. See "Alternative Data" tab for per-adapter status.';
    }

    renderAllocation(result.allocation);
    renderSummary(result.summary);
    renderAgentFlow(result);
    renderSurgeTable(result);
    renderQualityTable(result);
    renderAltDataEvidence(result);
    renderThematicResearch(result);
    renderDecisions(result);
}

// ── Header state loader (no FMP calls; reads cached /api/status) ──
async function loadHeaderState() {
    const dateEl = document.getElementById('date-value');
    const modeEl = document.getElementById('mode-value');
    const regimeEl = document.getElementById('regime-value');
    if (!dateEl || !modeEl) return;
    // Always default the dashboard-date chip to today's local date so
    // it never looks stale even if /api/status is slow.
    dateEl.textContent = new Date().toISOString().slice(0, 10);
    try {
        const resp = await fetch('/api/status', { cache: 'no-store' });
        const d = await resp.json();
        const m = d?.market_data_status || {};
        const live = m.active_source === 'live_fmp';
        modeEl.textContent = live ? 'LIVE' : 'MOCK';
        modeEl.style.color = live ? 'var(--bullish)' : 'var(--warning)';
        // Regime chip: show selector default until pipeline runs.
        if (regimeEl && (!regimeEl.textContent || regimeEl.textContent === '—')) {
            const sel = document.getElementById('regime-select');
            regimeEl.textContent = (sel?.value || 'normal').toUpperCase();
        }
    } catch (err) {
        modeEl.textContent = 'UNKNOWN';
        modeEl.style.color = 'var(--on-surface-dim)';
    }
}

// ── Allocation ──
function renderAllocation(alloc) {
    document.getElementById('alloc-regime-label').textContent = alloc.label;
    document.getElementById('fi-bar').style.width = alloc.fixed_income_pct + '%';
    document.getElementById('eq-bar').style.width = alloc.equity_pct + '%';
    document.getElementById('fi-pct').textContent = alloc.fixed_income_pct + '%';
    document.getElementById('eq-pct').textContent = alloc.equity_pct + '%';
    const constraints = [];
    if (alloc.no_margin) constraints.push('NO MARGIN');
    if (alloc.no_derivatives) constraints.push('NO DERIVATIVES');
    const discipline = alloc.equity_discipline || alloc.equity_restriction;
    if (discipline && discipline !== 'none') {
        constraints.push('EQUITY DISCIPLINE: ' + discipline.replace(/_/g, ' ').toUpperCase());
    }
    document.getElementById('alloc-constraints').textContent = constraints.join('  |  ');
    const noteEl = document.getElementById('alloc-discipline-note');
    if (noteEl) {
        noteEl.textContent = alloc.equity_discipline_note || '';
    }
}

// ── Summary Cards ──
function renderSummary(summary) {
    setCount('summary-short', summary.short);
    setCount('summary-buy', summary.buy);
    setCount('summary-watch', summary.watch);
    setCount('summary-notrade', summary.no_trade);
    setCount('summary-veto', summary.veto);
}
function setCount(id, count) {
    document.querySelector(`#${id} .summary-count`).textContent = count;
}

// ── Agent Flow ──
function renderAgentFlow(result) {
    const agents = result.agent_outputs;
    const screener = agents.market_screener;
    const surgeCount = screener.surge_short_candidates.length;

    document.querySelector('#agent-1 .agent-status').textContent =
        `${screener.total_gainers} scanned -> ${surgeCount} qualified`;
    document.querySelector('#agent-2 .agent-status').textContent =
        `${Object.keys(agents.narrative_event.classifications).length} classified`;
    document.querySelector('#agent-3 .agent-status').textContent =
        `${Object.keys(agents.alt_data_verification.verifications).length} verified`;
    document.querySelector('#agent-4 .agent-status').textContent =
        `${Object.keys(agents.fundamental_network.quality_evaluations).length} scored`;

    const s = result.summary;
    document.querySelector('#agent-5 .agent-status').textContent =
        `${s.short}S ${s.buy}B ${s.watch}W`;

    document.querySelectorAll('.agent-node').forEach(n => n.classList.add('agent-active'));
}

// ── Surge Table ──
function renderSurgeTable(result) {
    const candidates = result.agent_outputs.market_screener.surge_short_candidates;
    const narr = result.agent_outputs.narrative_event.classifications;
    const alt = result.agent_outputs.alt_data_verification.verifications;
    const fund = result.agent_outputs.fundamental_network.surge_evaluations;
    const decisions = {};
    result.decisions.forEach(d => { if (d.candidate_type === 'surge_short') decisions[d.ticker] = d; });

    const scanLabel = document.getElementById('surge-scan-label');
    const screener = result.agent_outputs.market_screener;
    scanLabel.textContent = `${screener.total_gainers} gainers scanned`;

    document.getElementById('surge-count').textContent = candidates.length;
    const tbody = document.getElementById('surge-tbody');
    const emptyMsg = document.getElementById('surge-empty');
    const table = document.getElementById('surge-table');
    tbody.innerHTML = '';

    if (candidates.length === 0) {
        emptyMsg.style.display = 'block';
        table.style.display = 'none';
        return;
    }
    emptyMsg.style.display = 'none';
    table.style.display = '';

    candidates.forEach(c => {
        const n = narr[c.ticker] || {};
        const a = alt[c.ticker] || {};
        const f = fund[c.ticker] || {};
        const dec = decisions[c.ticker] || {};
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td><strong>${c.ticker}</strong></td>
            <td>${c.name || ''}</td>
            <td class="num" style="color:var(--bearish)">+${c.change_pct.toFixed(1)}%</td>
            <td class="num">${(c.volume / 1e6).toFixed(1)}M</td>
            <td>${eventTag(n.event_type)}</td>
            <td>${verdictTag(a.verdict)}</td>
            <td class="num">${f.fundamental_score != null ? f.fundamental_score : 'N/A'}</td>
            <td>${decisionTag(dec.decision)}</td>
        `;
        tbody.appendChild(tr);
    });
}

// ── Quality Table (with Valuation) ──
function renderQualityTable(result) {
    const evals = result.agent_outputs.fundamental_network.quality_evaluations;
    const decisions = {};
    result.decisions.forEach(d => { if (d.candidate_type === 'quality_long') decisions[d.ticker] = d; });

    const tickers = Object.keys(evals);
    document.getElementById('quality-count').textContent = tickers.length;
    const tbody = document.getElementById('quality-tbody');
    tbody.innerHTML = '';

    tickers.forEach(ticker => {
        const e = evals[ticker];
        const dec = decisions[ticker] || {};
        const valClass = valAssessmentClass(e.valuation_assessment);
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td><strong>${ticker}</strong></td>
            <td>${e.name || ''}</td>
            <td class="num">${e.fundamental_score}</td>
            <td class="num">${e.network_effect_score}</td>
            <td class="num">${e.alt_data_score != null ? e.alt_data_score : 'N/A'}</td>
            <td class="num ${valClass}">${e.valuation_score != null ? e.valuation_score : 'N/A'}</td>
            <td><span class="${valClass}" style="font-size:0.62rem">${(e.valuation_assessment || 'N/A').replace(/_/g, ' ')}</span></td>
            <td class="num" style="color:${e.combined_quality_score >= 55 ? 'var(--bullish)' : 'var(--on-surface-dim)'}">${e.combined_quality_score}</td>
            <td>${decisionTag(dec.decision)}</td>
        `;
        tbody.appendChild(tr);
    });
}

// ── Alt-Data Evidence ──
function renderAltDataEvidence(result) {
    const verifications = result.agent_outputs.alt_data_verification.verifications;
    const container = document.getElementById('altdata-cards');
    container.innerHTML = '';

    const surges = result.agent_outputs.market_screener.surge_short_candidates.map(c => c.ticker);
    const orderedTickers = [...surges, ...Object.keys(verifications).filter(t => !surges.includes(t))];

    orderedTickers.forEach(ticker => {
        const v = verifications[ticker];
        if (!v || v.total_signals === 0) return;

        const card = document.createElement('div');
        card.className = 'altdata-card';
        let verdictClass = 'verdict-weakly';
        if (v.verdict === 'contradicted') verdictClass = 'verdict-contradicted';
        if (v.verdict === 'narrative_supported') verdictClass = 'verdict-supported';

        let evidenceHtml = '';
        if (v.evidence_for && v.evidence_for.length > 0)
            evidenceHtml += v.evidence_for.map(e => `<div class="evidence-for">+ ${esc(e)}</div>`).join('');
        if (v.evidence_against && v.evidence_against.length > 0)
            evidenceHtml += v.evidence_against.map(e => `<div class="evidence-against">x ${esc(e)}</div>`).join('');

        card.innerHTML = `
            <div class="altdata-card-header">
                <span class="altdata-ticker">${ticker}</span>
                <span class="altdata-verdict tag ${verdictClass}">${(v.verdict || '').replace(/_/g, ' ')}</span>
            </div>
            <div class="altdata-score">Evidence Score: ${v.evidence_score} / 100  |  Signals: ${v.total_signals}</div>
            <div class="altdata-evidence">${evidenceHtml}</div>
            <div class="altdata-sources">Sources: ${(v.data_sources_used || []).join(', ').toUpperCase()}</div>
        `;
        container.appendChild(card);
    });
}

// ── Thematic Research ──
function renderThematicResearch(result) {
    const thematic = (result.agent_outputs.alt_data_verification || {}).thematic_research;
    const body = document.getElementById('thematic-body');
    if (!thematic || !thematic.current_theme) {
        body.innerHTML = '<div style="color:var(--on-surface-dim);font-family:var(--font-data);font-size:0.7rem;">Thematic research data not available.</div>';
        return;
    }
    const t = thematic.current_theme;

    let industriesHtml = '';
    (t.related_industries || []).forEach(ind => {
        industriesHtml += `
            <div class="thematic-industry">
                <span class="thematic-industry-name">${esc(ind.industry)}</span>
                <span class="thematic-industry-tickers">${(ind.example_tickers || []).join(', ')}</span>
                <span class="thematic-industry-status">${esc(ind.status)}</span>
            </div>`;
    });

    let implicationsHtml = (t.decision_implications || []).map(i => `<div>- ${esc(i)}</div>`).join('');

    body.innerHTML = `
        <div class="thematic-grid">
            <div>
                <div class="thematic-section">
                    <h4>Current Theme</h4>
                    <div class="thematic-theme">${esc(t.name)}</div>
                    <div class="thematic-confidence">Confidence: ${(t.confidence || '').toUpperCase()}</div>
                    <div style="font-family:var(--font-data);font-size:0.65rem;color:var(--on-surface-variant);line-height:1.6">${esc(t.description)}</div>
                </div>
                <div class="thematic-section" style="margin-top:var(--sp-2)">
                    <h4>Demand Drivers</h4>
                    <ul class="thematic-list">${(t.demand_drivers || []).map(d => `<li>${esc(d)}</li>`).join('')}</ul>
                </div>
                <div class="thematic-section" style="margin-top:var(--sp-2)">
                    <h4>Bottlenecks</h4>
                    <ul class="thematic-list">${(t.bottlenecks || []).map(b => `<li>${esc(b)}</li>`).join('')}</ul>
                </div>
            </div>
            <div>
                <div class="thematic-section">
                    <h4>Related Industries & Tickers</h4>
                    <div class="thematic-industries">${industriesHtml}</div>
                </div>
                <div class="thematic-caution" style="margin-top:var(--sp-2)">
                    <strong>VALUATION CAUTION:</strong><br>${esc(t.valuation_caution || '')}
                </div>
                <div class="thematic-section" style="margin-top:var(--sp-2)">
                    <h4>Decision Implications</h4>
                    <div class="thematic-implications">${implicationsHtml}</div>
                </div>
            </div>
        </div>
    `;
}

// ── Decisions Table with Decision Trace ──
function renderDecisions(result) {
    const tbody = document.getElementById('decisions-tbody');
    tbody.innerHTML = '';
    const decisions = result.decisions;

    const order = { short: 0, buy: 1, watch: 2, veto: 3, no_trade: 4 };
    const sorted = [...decisions].sort((a, b) => (order[a.decision] || 5) - (order[b.decision] || 5));

    sorted.forEach((d, idx) => {
        const posStr = d.position_size > 0 ? `$${d.position_size.toLocaleString()}` : '-';
        const tr = document.createElement('tr');
        tr.className = 'clickable';
        tr.innerHTML = `
            <td><strong>${d.ticker}</strong></td>
            <td>${(d.candidate_type || '').replace(/_/g, ' ')}</td>
            <td>${decisionTag(d.decision)}</td>
            <td>${d.confidence || '-'}</td>
            <td class="num">${posStr}</td>
            <td style="max-width:350px;font-size:0.65rem;color:var(--on-surface-dim)">${esc(d.reason || '')}</td>
        `;
        tr.addEventListener('click', () => toggleTrace(tbody, tr, d, result, idx));
        tbody.appendChild(tr);
    });
}

function toggleTrace(tbody, triggerRow, decision, result, idx) {
    const existingTrace = triggerRow.nextElementSibling;
    if (existingTrace && existingTrace.classList.contains('trace-row')) {
        existingTrace.remove();
        return;
    }
    tbody.querySelectorAll('.trace-row').forEach(r => r.remove());

    const ticker = decision.ticker;

    // Fetch rich trace from backend API
    fetch(`/api/trace/${ticker}`)
        .then(r => r.json())
        .then(trace => {
            if (trace.error) {
                insertBasicTrace(tbody, triggerRow, decision, result);
                return;
            }
            insertRichTrace(tbody, triggerRow, trace);
        })
        .catch(() => insertBasicTrace(tbody, triggerRow, decision, result));
}

function insertRichTrace(tbody, triggerRow, trace) {
    const agents = trace.agent_trace || [];
    const traceRow = document.createElement('tr');
    traceRow.className = 'trace-row';
    const td = document.createElement('td');
    td.colSpan = 6;

    let agentHtml = agents.map((a, i) => {
        const isCore = i === 2;
        let html = `<div class="trace-agent ${isCore ? 'core-trace' : ''}">`;
        html += `<div class="trace-agent-header">${esc(a.agent)}</div>`;
        html += `<div class="trace-detail">`;

        // Outputs
        const outputs = a.outputs || {};
        for (const [key, val] of Object.entries(outputs)) {
            if (Array.isArray(val)) {
                if (val.length > 0) {
                    html += val.map(v => {
                        const isNeg = typeof v === 'string' && (v.startsWith('x ') || v.startsWith('SEC:') && v.includes('pivot') || v.includes('contradiction') || v.includes('red flag'));
                        return `<div class="trace-signal ${typeof v === 'string' && v.startsWith('+') ? 'positive' : ''}">${esc(String(v))}</div>`;
                    }).join('');
                }
            } else {
                const display = val === null || val === undefined || val === '' ? 'Not evaluated' : val;
                const label = key.replace(/_/g, ' ');
                html += `<div class="trace-metric"><span class="trace-metric-label">${label}</span><span class="trace-metric-value">${esc(String(display))}</span></div>`;
            }
        }

        // Signal breakdown (Agent 3 only)
        if (a.signal_breakdown) {
            const sb = a.signal_breakdown;
            html += `<div class="trace-signals">`;
            for (const [source, signals] of Object.entries(sb)) {
                if (signals.length > 0) {
                    html += `<div style="margin-top:4px;font-size:0.52rem;color:var(--on-surface-dim);font-weight:600">${source.replace(/_/g,' ').toUpperCase()}</div>`;
                    signals.forEach(s => {
                        const cls = s.startsWith('+') || s.includes('Stable') || s.includes('Strong') ? 'positive' : 'negative';
                        html += `<div class="trace-signal ${cls}">${esc(s)}</div>`;
                    });
                }
            }
            html += `</div>`;
        }

        // Key metrics (Agent 4)
        if (a.key_metrics) {
            html += `<div class="trace-signals">`;
            for (const [key, val] of Object.entries(a.key_metrics)) {
                const display = val === null || val === undefined || val === '' || val === 0 ? 'Not evaluated' : val;
                const label = key.replace(/_/g, ' ');
                html += `<div class="trace-metric"><span class="trace-metric-label">${label}</span><span class="trace-metric-value">${esc(String(display))}</span></div>`;
            }
            html += `</div>`;
        }

        html += `</div></div>`;
        return html;
    }).join('');

    td.innerHTML = `<div class="decision-trace"><div class="trace-grid">${agentHtml}</div></div>`;
    traceRow.appendChild(td);
    triggerRow.after(traceRow);
}

function insertBasicTrace(tbody, triggerRow, decision, result) {
    // Fallback basic trace (same as v0.2)
    const ticker = decision.ticker;
    const agents = result.agent_outputs;
    const screener = agents.market_screener;
    const surge = screener.surge_short_candidates.find(c => c.ticker === ticker);
    let a1 = surge
        ? `+${surge.change_pct.toFixed(1)}% daily return<br>Vol: ${(surge.volume/1e6).toFixed(1)}M<br>Passed surge threshold`
        : `In quality-long universe`;
    const narr = (agents.narrative_event.classifications || {})[ticker] || {};
    let a2 = `Event: <strong>${(narr.event_type || 'Data unavailable').replace(/_/g,' ')}</strong><br>Real value: ${narr.is_real_value ? 'YES' : 'NO'}`;
    const alt = (agents.alt_data_verification.verifications || {})[ticker] || {};
    let a3 = `Verdict: <strong>${(alt.verdict || 'Data unavailable').replace(/_/g,' ')}</strong><br>Score: ${alt.evidence_score != null ? alt.evidence_score : 'Data unavailable'}`;
    const isSurge = decision.candidate_type === 'surge_short';
    const fundKey = isSurge ? 'surge_evaluations' : 'quality_evaluations';
    const fund = (agents.fundamental_network[fundKey] || {})[ticker] || {};
    let a4 = `Fund: <strong>${fund.fundamental_score != null ? fund.fundamental_score : 'Data unavailable'}</strong><br>Valuation: ${fund.valuation_score != null ? fund.valuation_score : 'Not evaluated'}`;
    let a5 = `Decision: <strong>${(decision.decision || '').toUpperCase()}</strong><br>Position: ${decision.position_size > 0 ? '$' + decision.position_size.toLocaleString() : 'None'}`;

    const traceRow = document.createElement('tr');
    traceRow.className = 'trace-row';
    const td = document.createElement('td');
    td.colSpan = 6;
    td.innerHTML = `
        <div class="decision-trace"><div class="trace-grid">
            <div class="trace-agent"><div class="trace-agent-header">01 Screener</div><div class="trace-detail">${a1}</div></div>
            <div class="trace-agent"><div class="trace-agent-header">02 Narrative</div><div class="trace-detail">${a2}</div></div>
            <div class="trace-agent core-trace"><div class="trace-agent-header">03 Alt-Data</div><div class="trace-detail">${a3}</div></div>
            <div class="trace-agent"><div class="trace-agent-header">04 Fund/Net/Val</div><div class="trace-detail">${a4}</div></div>
            <div class="trace-agent"><div class="trace-agent-header">05 Risk/PM</div><div class="trace-detail">${a5}</div></div>
        </div></div>`;
    traceRow.appendChild(td);
    triggerRow.after(traceRow);
}

// ── Rules & Thresholds (Logic Audit Mode) ──
let rulesLoaded = false;
async function loadRulesIfNeeded() {
    if (rulesLoaded) return;
    try {
        const resp = await fetch('/api/rules');
        const rules = await resp.json();
        renderRules(rules);
        rulesLoaded = true;
    } catch (e) {
        const body = document.getElementById('rules-body');
        if (body) body.innerHTML = '<div style="color:var(--bearish);">Failed to load rules.</div>';
    }
}
function toggleRules() {
    if (typeof showTab === 'function') showTab('rules');
}

function renderRules(rules) {
    const body = document.getElementById('rules-body');
    let html = '<div class="rules-container">';

    // Investment Philosophy
    html += `<div class="rule-section">
        <div class="rule-section-title">Investment Philosophy</div>
        <div class="rule-philosophy">
            <strong>Long Side:</strong> ${esc(rules.investment_philosophy.long_side)}<br><br>
            <strong>Short Side:</strong> ${esc(rules.investment_philosophy.short_side)}<br><br>
            <strong>Regime-Aware:</strong> ${esc(rules.investment_philosophy.regime_aware)}
        </div>
    </div>`;

    // Surge Short Rules (v0.6 — agent-decision-ready, no hard-coded conclusions)
    const ss = rules.surge_short_rules;
    html += `<div class="rule-section">
        <div class="rule-section-title">1. Surge-Short Sleeve (v0.6)</div>
        <div class="rule-desc">${esc(ss.description)}</div>

        <div class="rule-columns"><div>
            <strong style="color:var(--agent-blue);font-size:0.62rem;text-transform:uppercase">Hard-Coded Screen</strong>
            <table class="rule-table"><thead><tr><th>Parameter</th><th>Value</th></tr></thead><tbody>
                ${Object.entries(ss.scan_parameters).map(([k,v]) =>
                    `<tr><td>${k.replace(/_/g,' ')}</td><td class="rule-value">${Array.isArray(v)?v.join(', '):v}</td></tr>`
                ).join('')}
            </tbody></table>
        </div><div>
            <strong style="color:var(--agent-blue);font-size:0.62rem;text-transform:uppercase">Hard-Coded Baseline Exclusions</strong>
            <table class="rule-table"><thead><tr><th>ID</th><th>Description</th><th>Effect</th></tr></thead><tbody>
                ${(ss.baseline_short_exclusions || []).map(b =>
                    `<tr><td><code style="font-size:0.6rem">${esc(b.id)}</code></td><td>${esc(b.description)}</td><td class="rule-result neutral">${esc(b.block_rule)}</td></tr>`
                ).join('')}
            </tbody></table>
        </div></div>

        <strong style="color:var(--agent-blue);font-size:0.62rem;text-transform:uppercase">Agent-Decided Outcomes (not hard-coded)</strong>
        <table class="rule-table"><thead><tr><th>Agent State</th><th>Engine Result</th><th>Guardrail Applied</th></tr></thead><tbody>
            ${(ss.agent_decision_table || []).map(r => {
                const cls = r.result.includes('EXIT') ? 'negative'
                          : r.result.includes('SHORT') ? 'negative'
                          : r.result.includes('WATCH') || r.result.includes('NO TRADE') ? 'neutral'
                          : 'positive';
                return `<tr><td>${esc(r.condition)}</td><td class="rule-result ${cls}">${esc(r.result)}</td><td>${esc(r.guardrails_applied)}</td></tr>`;
            }).join('')}
        </tbody></table>

        <div class="rule-columns"><div>
            <strong style="color:var(--agent-blue);font-size:0.62rem;text-transform:uppercase">Position Sizing</strong>
            <table class="rule-table"><thead><tr><th>Rule</th><th>Value</th></tr></thead><tbody>
                ${Object.entries(ss.position_sizing || {}).map(([k,v]) =>
                    `<tr><td>${k.replace(/_/g,' ')}</td><td class="rule-value">${v}</td></tr>`
                ).join('')}
            </tbody></table>
        </div><div>
            <strong style="color:var(--agent-blue);font-size:0.62rem;text-transform:uppercase">Risk Monitoring (Agentic + Hard Triggers)</strong>
            <div style="font-family:var(--font-data);font-size:0.62rem;line-height:1.7;padding:var(--sp-2) 0;">
                <div><strong style="color:var(--bearish)">EXIT when ANY of:</strong></div>
                <ul style="margin:0;padding-left:var(--sp-4);color:var(--bearish)">
                    ${(ss.risk_monitoring?.exit_when_any_of || []).map(s => `<li>${esc(s)}</li>`).join('')}
                </ul>
                <div style="margin-top:var(--sp-1)"><strong style="color:var(--warning)">REVIEW when ANY of:</strong></div>
                <ul style="margin:0;padding-left:var(--sp-4);color:var(--warning)">
                    ${(ss.risk_monitoring?.review_when_any_of || []).map(s => `<li>${esc(s)}</li>`).join('')}
                </ul>
                <div style="margin-top:var(--sp-1)"><strong style="color:var(--bullish)">HOLD when ALL of:</strong></div>
                <ul style="margin:0;padding-left:var(--sp-4);color:var(--bullish)">
                    ${(ss.risk_monitoring?.hold_when_all_of || []).map(s => `<li>${esc(s)}</li>`).join('')}
                </ul>
                <div style="margin-top:var(--sp-1)"><strong style="color:var(--on-surface-dim)">DO NOT use:</strong></div>
                <ul style="margin:0;padding-left:var(--sp-4);color:var(--on-surface-dim)">
                    ${(ss.risk_monitoring?.do_not_use || []).map(s => `<li>${esc(s)}</li>`).join('')}
                </ul>
            </div>
        </div></div>

        <div style="font-family:var(--font-data);font-size:0.6rem;color:var(--on-surface-variant);padding:var(--sp-2) 0;line-height:1.7">
            <strong style="color:var(--agent-blue)">Narrative taxonomy (examples for agent prompt — NOT trade rules):</strong>
            ${(ss.narrative_taxonomy_examples || []).join(' · ')}
            <br><em>${esc(ss.narrative_taxonomy_note || '')}</em>
        </div>
    </div>`;

    // Alt-Data Verification Logic
    const ad = rules.alt_data_verification_logic;
    html += `<div class="rule-section">
        <div class="rule-section-title">2. Alternative Data Verification Logic</div>
        <div class="rule-desc">${esc(ad.description)}</div>
        <div class="rule-columns">`;
    const sources = ad.role_1_narrative_verification.data_sources;
    for (const [src, data] of Object.entries(sources)) {
        html += `<div><strong style="color:var(--agent-blue);font-size:0.62rem;text-transform:uppercase">${src.replace(/_/g,' ')}</strong><ul class="rule-list">`;
        data.signals_checked.forEach(s => { html += `<li>${esc(s)}</li>`; });
        html += `</ul></div>`;
    }
    html += `</div>`;
    // Verdict logic
    html += `<table class="rule-table"><thead><tr><th>Verdict</th><th>Rule</th><th>Score Formula</th></tr></thead><tbody>`;
    for (const [verdict, logic] of Object.entries(ad.verdict_logic)) {
        const cls = verdict === 'contradicted' ? 'negative' : verdict === 'narrative_supported' ? 'positive' : 'neutral';
        html += `<tr><td class="rule-result ${cls}">${verdict.replace(/_/g,' ')}</td><td>${esc(logic.rule)}</td><td>${esc(logic.score_formula)}</td></tr>`;
    }
    html += `</tbody></table></div>`;

    // Fundamental Scoring
    const fs = rules.fundamental_scoring;
    html += `<div class="rule-section">
        <div class="rule-section-title">3. Fundamental Quality Scoring (0-100)</div>
        <div class="rule-desc">${esc(fs.description)}</div>
        <table class="rule-table"><thead><tr><th>Factor</th><th>Max Points</th><th>Thresholds</th></tr></thead><tbody>`;
    fs.components.forEach(c => {
        if (c.thresholds) {
            const thresh = c.thresholds.map(t => `${t.condition}: ${t.points}pts${t.flag ? ' ('+t.flag+')' : ''}`).join(' | ');
            html += `<tr><td>${esc(c.factor)}</td><td class="rule-value">${c.max_points}</td><td>${esc(thresh)}</td></tr>`;
        } else {
            html += `<tr><td>${esc(c.factor)}</td><td class="rule-value">${c.penalty}</td><td>${esc(c.condition)}</td></tr>`;
        }
    });
    html += `</tbody></table></div>`;

    // Network Effect Scoring
    const ns = rules.network_effect_scoring;
    html += `<div class="rule-section">
        <div class="rule-section-title">4. Network Effect Scoring (0-100)</div>
        <div class="rule-desc">${esc(ns.description)}</div>
        <table class="rule-table"><thead><tr><th>Factor</th><th>Max Points</th><th>Thresholds</th></tr></thead><tbody>`;
    ns.components.forEach(c => {
        const thresh = c.thresholds.map(t => `${t.condition}: ${t.points}pts`).join(' | ');
        html += `<tr><td>${esc(c.factor)}</td><td class="rule-value">${c.max_points}</td><td>${esc(thresh)}</td></tr>`;
    });
    html += `</tbody></table></div>`;

    // Valuation Scoring
    const vs = rules.valuation_scoring;
    html += `<div class="rule-section">
        <div class="rule-section-title">5. Valuation Attractiveness Scoring (0-100, starts at ${vs.starting_score})</div>
        <div class="rule-desc">${esc(vs.description)}</div>
        <table class="rule-table"><thead><tr><th>Factor</th><th>Condition</th><th>Adjustment</th></tr></thead><tbody>`;
    vs.components.forEach(c => {
        if (c.adjustments) {
            c.adjustments.forEach((a, i) => {
                const cls = a.adjustment.startsWith('+') ? 'positive' : 'negative';
                html += `<tr><td>${i===0 ? esc(c.factor) : ''}</td><td>${esc(a.condition)}</td><td class="rule-result ${cls}">${a.adjustment}</td></tr>`;
            });
        } else {
            html += `<tr><td>${esc(c.factor)}</td><td>${esc(c.condition)}</td><td class="rule-result positive">${c.adjustment}</td></tr>`;
        }
    });
    html += `</tbody></table>
        <table class="rule-table"><thead><tr><th>Score Range</th><th>Assessment</th></tr></thead><tbody>`;
    vs.assessment_classification.forEach(a => {
        const cls = a.assessment === 'attractive' ? 'positive' : a.assessment === 'very_expensive' ? 'negative' : 'neutral';
        html += `<tr><td>${a.range}</td><td class="rule-result ${cls}">${a.assessment.replace(/_/g,' ')}</td></tr>`;
    });
    html += `</tbody></table></div>`;

    // Combined Quality Logic
    const cq = rules.combined_quality_logic;
    html += `<div class="rule-section">
        <div class="rule-section-title">6. Combined Quality + Valuation Decision Logic</div>
        <div class="rule-desc">${esc(cq.description)}</div>
        <div class="rule-formula">${esc(cq.formula)}</div>
        <div class="rule-columns"><div>
            <strong style="color:var(--agent-blue);font-size:0.62rem;">QUALITY GATE</strong>
            <ul class="rule-list">
                <li>Rule 1: ${esc(cq.quality_gate.rule_1)}</li>
                <li>Rule 2: ${esc(cq.quality_gate.rule_2)}</li>
                <li>Both must pass: ${cq.quality_gate.both_must_pass ? 'YES' : 'NO'}</li>
                <li>If fail: ${esc(cq.quality_gate.if_fail)}</li>
            </ul>
        </div><div>
            <strong style="color:var(--agent-blue);font-size:0.62rem;">VALUATION GATE (after quality passes)</strong>
            <table class="rule-table"><tbody>
                ${Object.entries(cq.valuation_gate_after_quality).map(([k,v]) => {
                    const cls = v.includes('BUY') ? 'positive' : 'neutral';
                    return `<tr><td>${k}</td><td class="rule-result ${cls}">${esc(v)}</td></tr>`;
                }).join('')}
            </tbody></table>
        </div></div>
    </div>`;

    // Risk/PM Rules
    const rpm = rules.risk_pm_rules;
    html += `<div class="rule-section">
        <div class="rule-section-title">7. Risk / PM Decision Rules</div>
        <div class="rule-desc">${esc(rpm.description)}</div>
        <div class="rule-columns"><div>
            <strong style="color:var(--agent-blue);font-size:0.62rem;">SURGE-SHORT DECISIONS</strong>
            <table class="rule-table"><tbody>
                ${Object.entries(rpm.surge_short_decisions).map(([k,v]) => {
                    const cls = v.includes('SHORT') ? 'negative' : v.includes('VETO') ? 'negative' : 'neutral';
                    return `<tr><td>${k.replace(/_/g,' ')}</td><td class="rule-result ${cls}">${esc(v)}</td></tr>`;
                }).join('')}
            </tbody></table>
        </div><div>
            <strong style="color:var(--agent-blue);font-size:0.62rem;">QUALITY-LONG DECISIONS</strong>
            <table class="rule-table"><tbody>
                ${Object.entries(rpm.quality_long_decisions).map(([k,v]) => {
                    const cls = v.includes('BUY') ? 'positive' : v.includes('NO TRADE') ? 'neutral' : 'neutral';
                    return `<tr><td>${k.replace(/_/g,' ')}</td><td class="rule-result ${cls}">${esc(v)}</td></tr>`;
                }).join('')}
            </tbody></table>
        </div></div>
        <table class="rule-table"><thead><tr><th>Position Limit</th><th>Value</th></tr></thead><tbody>
            ${Object.entries(rpm.position_limits).map(([k,v]) =>
                `<tr><td>${k.replace(/_/g,' ')}</td><td class="rule-value">${v}</td></tr>`
            ).join('')}
        </tbody></table>
    </div>`;

    // Allocation Policy
    const ap = rules.allocation_policy;
    html += `<div class="rule-section">
        <div class="rule-section-title">8. Regime-Based Allocation Policy</div>
        <div class="rule-desc">${esc(ap.description)}</div>
        <table class="rule-table"><thead><tr><th>Regime</th><th>Label</th><th>Fixed Income</th><th>Equity</th><th>Equity Discipline</th><th>Note</th></tr></thead><tbody>
            ${ap.regimes.map(r => {
                const disc = r.equity_discipline || r.equity_restriction || '';
                const note = r.discipline_note || '';
                return `<tr><td><strong>${r.regime}</strong></td><td>${esc(r.label)}</td><td class="rule-value">${r.fixed_income_pct}%</td><td class="rule-value">${r.equity_pct}%</td><td>${esc(disc)}</td><td class="rule-note">${esc(note)}</td></tr>`;
            }).join('')}
        </tbody></table>
        <ul class="rule-list">${ap.global_constraints.map(c => `<li>${esc(c)}</li>`).join('')}</ul>
    </div>`;

    html += '</div>';
    body.innerHTML = html;
}

// ── Backtest ──
function renderBacktest(comparison) {
    const body = document.getElementById('backtest-body');
    const empty = document.getElementById('backtest-empty');
    if (empty) empty.style.display = 'none';
    if (typeof showTab === 'function') showTab('backtest');
    const w = comparison.with_alt_data;
    const wo = comparison.without_alt_data;
    const qi = comparison.quality_improvement;

    let changesHtml = '';
    if (comparison.decision_changes && comparison.decision_changes.length > 0) {
        changesHtml = `
            <div class="backtest-changes">
                <h3>Decision Changes</h3>
                <table class="data-table">
                    <thead><tr><th>Date</th><th>Ticker</th><th>Type</th><th>With Alt-Data</th><th>Without</th></tr></thead>
                    <tbody>
                        ${comparison.decision_changes.map(c => `
                            <tr>
                                <td>${c.date}</td>
                                <td><strong>${c.ticker}</strong></td>
                                <td>${(c.candidate_type || '').replace(/_/g, ' ')}</td>
                                <td>${decisionTag(c.with_alt_data)}</td>
                                <td>${decisionTag(c.without_alt_data)}</td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            </div>`;
    }

    body.innerHTML = `
        <div class="backtest-grid">
            <div class="backtest-column">
                <h3>With Alt-Data Agent</h3>
                ${statRow('Total Decisions', w.total_decisions)}
                ${statRow('Shorts', w.shorts)}
                ${statRow('Buys', w.buys)}
                ${statRow('Watches', w.watches)}
                ${statRow('No-Trades', w.no_trades)}
                ${statRow('Vetoes', w.vetoes)}
            </div>
            <div class="backtest-column">
                <h3>Without Alt-Data Agent (Baseline)</h3>
                ${statRow('Total Decisions', wo.total_decisions)}
                ${statRow('Shorts', wo.shorts)}
                ${statRow('Buys', wo.buys)}
                ${statRow('Watches', wo.watches)}
                ${statRow('No-Trades', wo.no_trades)}
                ${statRow('Vetoes', wo.vetoes)}
            </div>
        </div>
        <div class="backtest-impact">
            <h3>Alt-Data Quality Impact</h3>
            ${statRow('Decision Changes', qi.total_decision_changes)}
            ${statRow('False Shorts Prevented', qi.false_shorts_prevented)}
            ${statRow('New Shorts Identified', qi.new_shorts_identified)}
            ${statRow('Upgraded to Watch', qi.upgraded_to_watch)}
            ${statRow('Impact Rate', qi.alt_data_impact_pct + '%')}
        </div>
        ${changesHtml}
    `;
}
function statRow(label, value) {
    return `<div class="backtest-stat"><span class="backtest-stat-label">${label}</span><span class="backtest-stat-value">${value}</span></div>`;
}

// ── Helpers ──
function decisionTag(decision) {
    if (!decision) return '<span class="tag tag-notrade">-</span>';
    const cls = { short: 'tag-short', buy: 'tag-buy', watch: 'tag-watch', no_trade: 'tag-notrade', veto: 'tag-veto' };
    return `<span class="tag ${cls[decision] || 'tag-notrade'}">${decision.replace(/_/g, ' ')}</span>`;
}
function verdictTag(verdict) {
    if (!verdict) return '-';
    const cls = { contradicted: 'verdict-contradicted', weakly_supported: 'verdict-weakly', narrative_supported: 'verdict-supported' };
    return `<span class="${cls[verdict] || ''}">${verdict.replace(/_/g, ' ')}</span>`;
}
function eventTag(type) {
    if (!type) return '-';
    return `<span style="font-size:0.62rem">${type.replace(/_/g, ' ')}</span>`;
}
function valAssessmentClass(assessment) {
    const map = { attractive: 'val-attractive', fair: 'val-fair', expensive: 'val-expensive', very_expensive: 'val-very-expensive', not_evaluated: 'val-not-evaluated' };
    return map[assessment] || 'val-not-evaluated';
}
function esc(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ── Live FMP Price Check (lightweight, cached, no auto-refresh) ──
async function loadLivePriceCheck(force = false) {
    const tbody = document.getElementById('live-fmp-tbody');
    if (!tbody) return;
    if (!force) tbody.innerHTML = '<tr><td colspan="9" style="padding:10px;color:var(--on-surface-dim);">Loading…</td></tr>';
    try {
        const resp = await fetch('/api/live_price_check', { cache: 'no-store' });
        const d = await resp.json();
        renderLivePriceCheck(d);
    } catch (err) {
        console.error('Live price check error:', err);
        tbody.innerHTML = `<tr><td colspan="9" style="padding:10px;color:var(--error);">Live price check failed: ${esc(err.message || err)}</td></tr>`;
    }
}

function renderLivePriceCheck(d) {
    const tbody = document.getElementById('live-fmp-tbody');
    const badge = document.getElementById('live-fmp-badge');
    const meta = document.getElementById('live-fmp-meta');
    const warn = document.getElementById('live-fmp-warning');
    const strip = document.getElementById('live-fmp-status-strip');
    if (!tbody) return;

    const rows = d.rows || [];
    const summary = d.summary || {};
    const fmp = d.fmp || {};

    // Badge: how many of N tickers came back live_fmp
    badge.textContent = `${summary.live_count || 0}/${rows.length} live_fmp`;
    meta.textContent = `cache ${summary.cache_ttl_seconds || 300}s · refreshed ${formatRelative(d.generated_at)}`;

    // Warning banner if quota exhausted
    if (d.quota_exhausted) {
        warn.style.display = 'block';
        warn.textContent = d.warning || 'FMP quota may be exhausted. Try again after quota reset.';
    } else {
        warn.style.display = 'none';
        warn.textContent = '';
    }

    // Status strip: API status fields the user requested
    strip.innerHTML = [
        statusChip('active_source', fmp.active_source, fmp.active_source === 'live_fmp'),
        statusChip('base_url', fmp.base_url || '—', false),
        statusChip('key_set', String(fmp.api_key_set), fmp.api_key_set === true),
        statusChip('legacy_v3', fmp.legacy_v3_disabled ? 'disabled' : 'ENABLED!', fmp.legacy_v3_disabled === true),
        statusChip('data_mode', fmp.data_mode || '—', fmp.data_mode === 'live'),
        statusChip('mock_fallbacks', String(summary.mock_fallback_count || 0), (summary.mock_fallback_count || 0) === 0),
        statusChip('cache_hits', String(summary.cache_hits || 0), false),
    ].join('');

    if (!rows.length) {
        tbody.innerHTML = '<tr><td colspan="9" style="padding:10px;color:var(--on-surface-dim);">No tickers configured.</td></tr>';
        return;
    }

    tbody.innerHTML = rows.map(r => {
        const srcClass = r.source === 'live_fmp' ? 'live-fmp-source-live' : 'live-fmp-source-mock';
        const srcLabel = r.source === 'live_fmp' ? 'live_fmp' : (r.source || 'mock_fallback');
        const fallback = r.source === 'live_fmp'
            ? '—'
            : (r.fallback_reason || (r.served_stale ? 'stale cache' : 'mock_fallback'));
        const chg = (typeof r.change_pct === 'number') ? r.change_pct.toFixed(2) + '%' : '—';
        const ts = r.timestamp ? formatTs(r.timestamp) : '—';
        const cacheNote = r.served_from_cache
            ? ` <span style="color:var(--on-surface-dim);font-size:0.55rem;">(cache ${r.cache_age_s ?? '?'}s${r.served_stale ? ', stale' : ''})</span>`
            : '';
        return `<tr>
            <td><strong>${esc(r.ticker)}</strong></td>
            <td>${formatNum(r.price, 2)}</td>
            <td>${formatNum(r.previous_close, 2)}</td>
            <td>${chg}</td>
            <td>${formatVolume(r.volume)}</td>
            <td style="font-size:0.62rem;">${esc(ts)}${cacheNote}</td>
            <td><span class="${srcClass}" style="font-family:var(--font-data);font-size:0.62rem;padding:2px 6px;border-radius:2px;">${esc(srcLabel)}</span></td>
            <td style="font-size:0.62rem;">${esc(fmp.data_mode || '—')}</td>
            <td style="font-size:0.62rem;color:${r.source === 'live_fmp' ? 'var(--on-surface-dim)' : 'var(--warning)'};">${esc(fallback)}</td>
        </tr>`;
    }).join('');
}

function statusChip(label, value, ok) {
    const color = ok ? '#7ec97e' : '#d9a06b';
    return `<span style="display:inline-flex;gap:4px;align-items:baseline;">
        <span style="opacity:0.6;">${esc(label)}=</span>
        <span style="color:${color};font-weight:600;">${esc(String(value))}</span>
    </span>`;
}

function formatNum(n, decimals = 2) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    return Number(n).toFixed(decimals);
}
function formatVolume(v) {
    if (v === null || v === undefined || isNaN(v)) return '—';
    const n = Number(v);
    if (n >= 1e9) return (n / 1e9).toFixed(2) + 'B';
    if (n >= 1e6) return (n / 1e6).toFixed(2) + 'M';
    if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
    return String(n);
}
function formatTs(iso) {
    try {
        const d = new Date(iso);
        if (isNaN(d.getTime())) return iso;
        return d.toISOString().replace('T', ' ').slice(0, 19) + 'Z';
    } catch { return iso; }
}
function formatRelative(iso) {
    if (!iso) return '—';
    try {
        const d = new Date(iso);
        const diff = (Date.now() - d.getTime()) / 1000;
        if (diff < 60) return Math.round(diff) + 's ago';
        if (diff < 3600) return Math.round(diff / 60) + 'm ago';
        return Math.round(diff / 3600) + 'h ago';
    } catch { return '—'; }
}

// ── FMP Premium Ticker Inspector ──
function setTickerInspector(t) {
    const inp = document.getElementById('ti-ticker-input');
    if (inp) { inp.value = t; }
    loadTickerInspector(true);
}

// ── Ticker / company search (Phase 2.1) ──
// - Debounced 450ms.
// - Min 2 chars.
// - Last-input wins (stale-response guard via a monotonic seq counter).
// - On total live failure, surfaces "FMP search failed" — never silently
//   falls back to mock data.
let _tiSearchSeq = 0;
let _tiSearchTimer = null;

function _tiSearchEls() {
    return {
        input:   document.getElementById('ti-search-input'),
        status:  document.getElementById('ti-search-status'),
        results: document.getElementById('ti-search-results'),
    };
}

function _tiSearchClear(hide = true) {
    const { results, status } = _tiSearchEls();
    if (results) { results.innerHTML = ''; if (hide) results.hidden = true; }
    if (status)  { status.textContent = ''; }
}

function _tiSearchEscape(s) {
    return String(s || '').replace(/[&<>"']/g, c => ({
        '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
    })[c]);
}

async function _tiSearchExecute(q) {
    const seq = ++_tiSearchSeq;
    const { status, results } = _tiSearchEls();
    if (!status || !results) return;
    status.textContent = 'searching…';
    status.className = 'ti-search-status ti-search-status-loading';
    try {
        const resp = await fetch(`/api/fmp/search?q=${encodeURIComponent(q)}`,
                                 { cache: 'no-store' });
        if (seq !== _tiSearchSeq) return; // user typed again — drop
        const d = await resp.json();
        if (seq !== _tiSearchSeq) return;
        const isLiveFail = (d.source === 'live_fmp_failed');
        const isMock     = (d.source === 'mock_fallback');
        if (!resp.ok || isLiveFail) {
            status.className = 'ti-search-status ti-search-status-error';
            const why = (d.errors && d.errors[0] && (d.errors[0].error_short
                        || d.errors[0].reason || d.errors[0].error_class))
                        || `HTTP ${resp.status}`;
            status.textContent = `FMP search failed (${why})`;
            results.innerHTML = '';
            results.hidden = true;
            return;
        }
        if (isMock) {
            status.className = 'ti-search-status ti-search-status-warn';
            status.textContent = 'live mode disabled — search not available';
            results.innerHTML = '';
            results.hidden = true;
            return;
        }
        const rows = Array.isArray(d.results) ? d.results : [];
        if (rows.length === 0) {
            status.className = 'ti-search-status';
            status.textContent = 'no US-listed matches';
            results.innerHTML = '';
            results.hidden = true;
            return;
        }
        const cacheTag = d.served_from_cache ? ' (cache)' : '';
        status.className = 'ti-search-status ti-search-status-ok';
        status.textContent = `${rows.length} match${rows.length === 1 ? '' : 'es'}${cacheTag}`;
        results.innerHTML = rows.map((r, i) => {
            const sym  = _tiSearchEscape(r.symbol);
            const name = _tiSearchEscape(r.name || '');
            const exch = _tiSearchEscape(r.exchange || '');
            const typ  = _tiSearchEscape(r.type || 'equity');
            const usTag = r.us_listed
                ? '<span class="ti-search-tag ti-search-tag-us">US</span>'
                : '<span class="ti-search-tag ti-search-tag-other">?</span>';
            return `<li class="ti-search-result" tabindex="0" role="option"
                       data-symbol="${sym}" data-index="${i}">
                <span class="ti-search-symbol">${sym}</span>
                ${usTag}
                <span class="ti-search-name">${name || '<em>—</em>'}</span>
                <span class="ti-search-meta">${exch}${typ ? ' · ' + typ : ''}</span>
            </li>`;
        }).join('');
        results.hidden = false;
        results.querySelectorAll('.ti-search-result').forEach(li => {
            const pick = () => {
                const sym = li.getAttribute('data-symbol');
                if (!sym) return;
                _tiSearchClear(true);
                const inp = document.getElementById('ti-search-input');
                if (inp) inp.value = sym;
                setTickerInspector(sym);
            };
            li.addEventListener('click', pick);
            li.addEventListener('keydown', e => {
                if (e.key === 'Enter') { e.preventDefault(); pick(); }
            });
        });
    } catch (err) {
        if (seq !== _tiSearchSeq) return;
        status.className = 'ti-search-status ti-search-status-error';
        status.textContent = 'FMP search failed (network error)';
        results.innerHTML = '';
        results.hidden = true;
    }
}

function _tiSearchSchedule(q) {
    if (_tiSearchTimer) clearTimeout(_tiSearchTimer);
    if (!q || q.length < 2) { _tiSearchClear(true); return; }
    _tiSearchTimer = setTimeout(() => _tiSearchExecute(q), 450);
}

function initTickerSearch() {
    const { input, results } = _tiSearchEls();
    if (!input) return;
    input.addEventListener('input', e => _tiSearchSchedule(e.target.value.trim()));
    input.addEventListener('keydown', e => {
        if (e.key === 'Escape') { _tiSearchClear(true); input.blur(); }
        if (e.key === 'Enter') {
            const first = results && results.querySelector('.ti-search-result');
            if (first) { first.click(); }
        }
    });
    document.addEventListener('click', e => {
        if (!input.contains(e.target) && (!results || !results.contains(e.target))) {
            if (results) results.hidden = true;
        }
    });
    input.addEventListener('focus', () => {
        if (results && results.children.length > 0) results.hidden = false;
    });
}

async function loadTickerInspector(force = false) {
    const inp = document.getElementById('ti-ticker-input');
    const intervalSel = document.getElementById('ti-interval');
    if (!inp || !intervalSel) return;
    const ticker = (inp.value || 'AAPL').trim().toUpperCase().slice(0, 10);
    const interval = intervalSel.value || '5min';
    const meta = document.getElementById('ti-meta');
    meta.textContent = 'loading…';
    try {
        const url = `/api/fmp/ticker_inspector?ticker=${encodeURIComponent(ticker)}&interval=${encodeURIComponent(interval)}`;
        const resp = await fetch(url, { cache: 'no-store' });
        if (!resp.ok) {
            const errBody = await resp.text();
            meta.textContent = `failed: HTTP ${resp.status}`;
            renderTiError(`HTTP ${resp.status}: ${errBody.slice(0, 200)}`);
            return;
        }
        const d = await resp.json();
        renderTickerInspector(d);
    } catch (err) {
        console.error('Ticker inspector error:', err);
        meta.textContent = 'failed';
        renderTiError(String(err.message || err));
    }
}

function renderTiError(msg) {
    const w = document.getElementById('ti-warning');
    w.style.display = 'block';
    w.textContent = msg;
}

function renderTickerInspector(d) {
    const meta = document.getElementById('ti-meta');
    const warn = document.getElementById('ti-warning');
    const strip = document.getElementById('ti-status-strip');
    if (!d) return;

    meta.textContent = `last refreshed ${formatRelative(d.generated_at)}`;
    if (d.warning) {
        warn.style.display = 'block';
        warn.textContent = d.warning;
    } else {
        warn.style.display = 'none';
    }

    // Status strip
    const r = d.rate_limit || {};
    const c = d.cache_stats || {};
    strip.innerHTML = [
        statusChip('ticker', d.ticker, true),
        statusChip('active_source', d.active_source || (d.live_mode ? 'live_fmp' : 'mock'), (d.active_source === 'live_fmp')),
        statusChip('plan', 'premium', true),
        statusChip('rolling_min_calls', `${r.current_rolling_minute_count || 0}/${r.configured_max_per_minute || 600}`, (r.current_rolling_minute_count || 0) < (r.configured_max_per_minute || 600) * 0.9),
        statusChip('cache', `${c.hits || 0}h / ${c.misses || 0}m`, true),
        statusChip('fallbacks', String(d.fallback_count || 0), (d.fallback_count || 0) === 0),
        statusChip('last_call', r.last_call_at ? formatRelative(r.last_call_at) : '—', true),
    ].join('');

    const blocks = d.blocks || {};
    renderTiQuote(blocks.quote, d.company_name);
    renderTiIntraday(blocks.intraday);
    renderTiTechnicals(blocks.technicals);
    renderTiFundamentals(blocks.fundamentals);
    renderTiCalendar(blocks.calendar);
    renderTiDcf(blocks.dcf, blocks.quote);
}

function tiSourceTag(source) {
    const cls = source === 'live_fmp'
        ? 'live-fmp-source-live'
        : 'live-fmp-source-mock';
    return `<span class="${cls}" style="font-family:var(--font-data);font-size:0.55rem;padding:2px 6px;border-radius:2px;">${esc(source || '—')}</span>`;
}
function tiKv(label, value, extra = '') {
    return `<div class="ti-kv"><span class="ti-kv-label">${esc(label)}</span><span class="ti-kv-value">${value}</span>${extra ? `<span class="ti-kv-extra">${extra}</span>` : ''}</div>`;
}
function fmtN(n, d = 2) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    return Number(n).toFixed(d);
}
function fmtBig(n) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    const v = Number(n);
    if (Math.abs(v) >= 1e12) return (v / 1e12).toFixed(2) + 'T';
    if (Math.abs(v) >= 1e9)  return (v / 1e9).toFixed(2)  + 'B';
    if (Math.abs(v) >= 1e6)  return (v / 1e6).toFixed(2)  + 'M';
    if (Math.abs(v) >= 1e3)  return (v / 1e3).toFixed(1)  + 'K';
    return String(v);
}

function renderTiQuote(q, companyName) {
    const el = document.getElementById('ti-quote-body');
    if (!q) { el.innerHTML = '<div class="ti-empty">—</div>'; return; }
    if (q.source !== 'live_fmp') {
        el.innerHTML = `<div class="ti-fail">${tiSourceTag(q.source || 'mock_fallback')} ${esc(q.error_short || q.error_class || 'unavailable')}</div>`;
        return;
    }
    const chgPct = (typeof q.change_pct === 'number') ? q.change_pct.toFixed(2) + '%' : '—';
    const chgColor = (q.change || 0) >= 0 ? 'var(--success, #7ec97e)' : 'var(--error, #d96b6b)';
    el.innerHTML = [
        companyName ? `<div class="ti-company">${esc(companyName)}</div>` : '',
        `<div class="ti-price"><span style="font-size:1.4rem;font-weight:700;">${fmtN(q.price, 2)}</span> <span style="color:${chgColor};">${chgPct}</span></div>`,
        tiKv('previous_close', fmtN(q.previous_close, 2)),
        tiKv('day range', `${fmtN(q.day_low, 2)} – ${fmtN(q.day_high, 2)}`),
        tiKv('volume', fmtBig(q.volume)),
        tiKv('market_cap', fmtBig(q.market_cap)),
        tiKv('exchange', esc(q.exchange || '—')),
        tiKv('timestamp', esc(q.timestamp || '—')),
        tiKv('source', tiSourceTag(q.source)),
        tiKv('fallback', q.source === 'live_fmp' ? '—' : (q.error_short || 'mock_fallback')),
    ].join('');
}

// ────────────────────────────────────────────────────────────────────
// Multi-timeframe chart state. One slot per ticker; cleared on ticker
// switch in renderTiIntraday so we never serve another ticker's bars.
// ────────────────────────────────────────────────────────────────────
const CHART_TIMEFRAMES = ['1D', '3D', '1W', '1M', '3M', '1Y', '5Y', 'ALL'];
const INTRADAY_TFS = new Set(['1D', '3D', '1W']);
const _chartState = {
    ticker: null,
    activeTf: '1D',
    intradayBars: null,   // array of {datetime, open, high, low, close, volume}
    intradayMeta: null,   // {interval, served_from_cache, source}
    dailyBars: null,      // array of {date, open, high, low, close, volume}
    dailyMeta: null,
    dailyLoading: false,
};

function _windowSliceIntraday(bars, tf) {
    if (!bars || bars.length === 0) return [];
    if (tf === '1D') {
        // last calendar day represented in the data
        const last = bars[bars.length - 1].datetime;
        if (!last) return bars.slice();
        const day = String(last).slice(0, 10);
        return bars.filter(b => String(b.datetime || '').slice(0, 10) === day);
    }
    const days = (tf === '3D') ? 3 : 7;
    const lastTs = Date.parse((bars[bars.length - 1].datetime || '').replace(' ', 'T'));
    if (!Number.isFinite(lastTs)) return bars.slice();
    const cutoff = lastTs - days * 86400000;
    return bars.filter(b => {
        const t = Date.parse(String(b.datetime || '').replace(' ', 'T'));
        return Number.isFinite(t) && t >= cutoff;
    });
}

function _windowSliceDaily(bars, tf) {
    if (!bars || bars.length === 0) return [];
    if (tf === 'ALL') return bars.slice();
    const ndays = ({ '1M': 31, '3M': 92, '1Y': 366, '5Y': 5 * 366 })[tf] || 31;
    const lastTs = Date.parse(bars[bars.length - 1].date);
    if (!Number.isFinite(lastTs)) return bars.slice();
    const cutoff = lastTs - ndays * 86400000;
    return bars.filter(b => {
        const t = Date.parse(b.date);
        return Number.isFinite(t) && t >= cutoff;
    });
}

async function _ensureDailyLoaded(ticker) {
    if (_chartState.dailyBars) return;
    if (_chartState.dailyLoading) return;
    _chartState.dailyLoading = true;
    try {
        const resp = await fetch(`/api/fmp/chart?ticker=${encodeURIComponent(ticker)}&kind=daily`,
                                  { cache: 'no-store' });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const d = await resp.json();
        if (d.source === 'live_fmp' && Array.isArray(d.bars)) {
            _chartState.dailyBars = d.bars;
            _chartState.dailyMeta = {
                served_from_cache: !!d.served_from_cache,
                source: d.source,
                row_count: d.row_count,
                first_date: d.first_date,
                last_date: d.last_date,
            };
        } else {
            _chartState.dailyBars = [];
            _chartState.dailyMeta = {
                served_from_cache: false,
                source: d.source || 'mock_fallback',
                error_short: d.error_short || d.status || 'unavailable',
            };
        }
    } catch (err) {
        _chartState.dailyBars = [];
        _chartState.dailyMeta = { source: 'live_fmp_failed', error_short: String(err.message || err) };
    } finally {
        _chartState.dailyLoading = false;
    }
}

function renderTiIntraday(intra) {
    const el = document.getElementById('ti-intraday-body');
    const sub = document.getElementById('ti-intraday-sub');
    const ticker = (document.getElementById('ti-ticker-input')?.value || '').trim().toUpperCase();

    // Ticker change: drop any cached bars from a previous symbol.
    if (_chartState.ticker !== ticker) {
        _chartState.ticker = ticker;
        _chartState.activeTf = '1D';
        _chartState.intradayBars = null;
        _chartState.intradayMeta = null;
        _chartState.dailyBars = null;
        _chartState.dailyMeta = null;
        _chartState.dailyLoading = false;
    }

    if (!intra || intra.source !== 'live_fmp' || !Array.isArray(intra.bars) || intra.bars.length === 0) {
        sub.textContent = intra?.interval ? `(${intra.interval})` : '';
        // Still draw the timeframe selector + an unavailable banner so the
        // user sees the full UI; daily can still load on click.
        _chartState.intradayBars = [];
        _chartState.intradayMeta = {
            source: intra?.source || 'mock_fallback',
            error_short: intra?.error_short || 'unavailable',
            served_from_cache: !!intra?.served_from_cache,
        };
        el.innerHTML = `
          <div class="chart-tf-row" id="ti-chart-tf-row"></div>
          <div class="ti-fail">${tiSourceTag(intra?.source || 'mock_fallback')} ${esc(intra?.error_short || 'unavailable')}</div>
        `;
        _renderChartTfButtons();
        return;
    }

    sub.textContent = `(${intra.interval}, ${intra.row_count} bars · ${esc(intra.first_datetime || '')} → ${esc(intra.last_datetime || '')})`;
    _chartState.intradayBars = intra.bars;
    _chartState.intradayMeta = {
        interval: intra.interval,
        served_from_cache: !!intra.served_from_cache,
        source: intra.source,
        row_count: intra.row_count,
    };

    el.innerHTML = `
      <div class="chart-tf-row" id="ti-chart-tf-row"></div>
      <div class="chart-wrap" id="ti-chart-wrap"></div>
      <div class="chart-footer" id="ti-chart-footer"></div>
    `;
    _renderChartTfButtons();
    _renderChartForActiveTf();
}

function _renderChartTfButtons() {
    const row = document.getElementById('ti-chart-tf-row');
    if (!row) return;
    row.innerHTML = CHART_TIMEFRAMES.map(tf => {
        const active = (tf === _chartState.activeTf) ? ' active' : '';
        return `<button type="button" class="tf-btn${active}" data-tf="${tf}">${tf}</button>`;
    }).join('');
    row.querySelectorAll('.tf-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
            const tf = btn.getAttribute('data-tf');
            if (tf === _chartState.activeTf) return;
            _chartState.activeTf = tf;
            _renderChartTfButtons();
            if (!INTRADAY_TFS.has(tf) && !_chartState.dailyBars) {
                _renderChartLoadingState(tf);
                await _ensureDailyLoaded(_chartState.ticker);
            }
            _renderChartForActiveTf();
        });
    });
}

function _renderChartLoadingState(tf) {
    const wrap = document.getElementById('ti-chart-wrap');
    if (wrap) wrap.innerHTML = `<div class="chart-loading">loading ${esc(tf)}…</div>`;
    const ftr = document.getElementById('ti-chart-footer');
    if (ftr) ftr.innerHTML = '';
}

// Auto-format an X-axis tick label based on bar type and the active
// window's total span. Pure client-side; the bar object's `datetime`
// (intraday) or `date` (daily) field is what we render against.
function _fmtTickLabel(bar, spanMs, isIntraday) {
    if (isIntraday) {
        const s = String(bar.datetime || '');
        if (spanMs < 24 * 3600 * 1000) {
            return s.slice(11, 16);          // HH:MM
        }
        return s.slice(5, 10) + ' ' + s.slice(11, 16); // MM-DD HH:MM
    }
    const s = String(bar.date || '');
    const d = new Date(s + 'T00:00:00Z');
    if (isNaN(d.getTime())) return s;
    if (spanMs < 95 * 86400000) {
        return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
    }
    if (spanMs < 800 * 86400000) {
        return d.toLocaleDateString('en-US', { month: 'short', year: 'numeric', timeZone: 'UTC' });
    }
    return s.slice(0, 4);                    // YYYY
}

function _renderChartForActiveTf() {
    const wrap = document.getElementById('ti-chart-wrap');
    const ftr = document.getElementById('ti-chart-footer');
    if (!wrap || !ftr) return;

    const tf = _chartState.activeTf;
    let bars, isIntraday;
    if (INTRADAY_TFS.has(tf)) {
        bars = _windowSliceIntraday(_chartState.intradayBars || [], tf);
        isIntraday = true;
    } else {
        if (!_chartState.dailyBars) {
            wrap.innerHTML = `<div class="chart-loading">loading ${esc(tf)}…</div>`;
            ftr.innerHTML = '';
            return;
        }
        bars = _windowSliceDaily(_chartState.dailyBars, tf);
        isIntraday = false;
    }

    if (!bars || bars.length === 0) {
        const meta = isIntraday ? _chartState.intradayMeta : _chartState.dailyMeta;
        wrap.innerHTML = `<div class="ti-fail">${tiSourceTag(meta?.source || 'mock_fallback')} ${esc(meta?.error_short || 'no bars in window')}</div>`;
        ftr.innerHTML = '';
        return;
    }

    // Layout: price area on top, ~4px gap, volume strip below, axis
    // labels in the bottom strip. Total height stays compact.
    const w = 720, h = 220;
    const padTop = 8;
    const priceH = 130;
    const gap = 4;
    const volH = 46;
    const priceTop = padTop;                       // 8
    const priceBottom = priceTop + priceH;         // 138
    const volTop = priceBottom + gap;              // 142
    const volBottom = volTop + volH;               // 188
    const labelY = volBottom + 14;                 // 202
    const padX = 12;

    const closes = bars.map(b => b.close).filter(c => typeof c === 'number');
    if (closes.length === 0) {
        wrap.innerHTML = '<div class="ti-empty">no numeric closes</div>';
        ftr.innerHTML = '';
        return;
    }
    const min = Math.min(...closes), max = Math.max(...closes);
    const range = (max - min) || 1;
    const n = bars.length;
    const stepX = (w - padX * 2) / Math.max(1, n - 1);
    const yOf = c => priceTop + priceH * (1 - (c - min) / range);
    const xOf = i => padX + i * stepX;
    const pts = bars.map((b, i) => `${xOf(i).toFixed(1)},${yOf(b.close).toFixed(1)}`).join(' ');
    const trend = closes[closes.length - 1] >= closes[0] ? '#7ec97e' : '#d96b6b';

    // Volume bars — green if close>=open, red otherwise. Scaled to the
    // max volume in the active window only, so each timeframe has its
    // own vertical scale and small bars stay visible.
    const volumes = bars.map(b => Number(b.volume) || 0);
    const maxVol = Math.max(...volumes, 1);
    const volScale = (volH - 1) / maxVol;
    const volBarW = Math.max(0.5, Math.min(stepX * 0.75, stepX - 0.4));
    const volRects = bars.map((b, i) => {
        const v = Number(b.volume) || 0;
        if (v <= 0) return '';
        const x = xOf(i);
        const barH = v * volScale;
        const isUp = (typeof b.close === 'number' && typeof b.open === 'number')
            ? b.close >= b.open : true;
        const color = isUp ? '#3a7d4f' : '#8d3940';
        return `<rect x="${(x - volBarW / 2).toFixed(1)}" y="${(volBottom - barH).toFixed(1)}" width="${volBarW.toFixed(1)}" height="${barH.toFixed(1)}" fill="${color}" />`;
    }).join('');

    // X-axis tick labels — 5 evenly-spaced ticks (incl. endpoints).
    const numTicks = Math.min(5, n);
    let spanMs;
    if (isIntraday) {
        const t0 = Date.parse(String(bars[0].datetime || '').replace(' ', 'T'));
        const tN = Date.parse(String(bars[bars.length - 1].datetime || '').replace(' ', 'T'));
        spanMs = (Number.isFinite(t0) && Number.isFinite(tN)) ? (tN - t0) : 0;
    } else {
        const t0 = Date.parse(bars[0].date);
        const tN = Date.parse(bars[bars.length - 1].date);
        spanMs = (Number.isFinite(t0) && Number.isFinite(tN)) ? (tN - t0) : 0;
    }
    const tickLabels = [];
    for (let i = 0; i < numTicks; i++) {
        const idx = Math.round((i / Math.max(1, numTicks - 1)) * (n - 1));
        const x = xOf(idx);
        const lbl = _fmtTickLabel(bars[idx], spanMs, isIntraday);
        const anchor = (i === 0) ? 'start' : (i === numTicks - 1 ? 'end' : 'middle');
        tickLabels.push(
            `<text x="${x.toFixed(1)}" y="${labelY}" text-anchor="${anchor}" font-size="9" fill="#888" font-family="var(--font-data)">${esc(lbl)}</text>`
        );
        // light grid tick mark just below the volume strip
        if (i > 0 && i < numTicks - 1) {
            tickLabels.push(
                `<line x1="${x.toFixed(1)}" y1="${volBottom + 1}" x2="${x.toFixed(1)}" y2="${volBottom + 4}" stroke="#555" stroke-width="0.5" />`
            );
        }
    }

    // Price min/max labels on the left edge of the price area.
    const priceMinMax = `
      <text x="${padX}" y="${priceTop + 8}" font-size="9" fill="#888" font-family="var(--font-data)">${fmtN(max,2)}</text>
      <text x="${padX}" y="${priceBottom - 1}" font-size="9" fill="#888" font-family="var(--font-data)">${fmtN(min,2)}</text>
    `;

    wrap.innerHTML = `
      <div class="chart-svg-host" style="position:relative;">
        <svg id="ti-chart-svg" viewBox="0 0 ${w} ${h}" width="100%" preserveAspectRatio="none"
             style="background:#0c0d10;border:1px solid var(--outline-variant);display:block;">
          <line x1="${padX}" y1="${volTop - 2}" x2="${w - padX}" y2="${volTop - 2}" stroke="#222" stroke-width="0.5" />
          <polyline points="${pts}" fill="none" stroke="${trend}" stroke-width="1.5" />
          ${priceMinMax}
          <g class="vol-bars">${volRects}</g>
          ${tickLabels.join('')}
          <line id="ti-chart-cross" x1="0" y1="${priceTop}" x2="0" y2="${volBottom}" stroke="#888" stroke-width="0.5" stroke-dasharray="2,3" style="display:none;pointer-events:none;" />
          <circle id="ti-chart-dot" cx="0" cy="0" r="2.5" fill="${trend}" style="display:none;pointer-events:none;" />
        </svg>
        <div id="ti-chart-tooltip" class="chart-tooltip" style="display:none;"></div>
      </div>
    `;

    const meta = isIntraday ? _chartState.intradayMeta : _chartState.dailyMeta;
    const chgPct = ((closes[closes.length - 1] - closes[0]) / closes[0]) * 100;
    ftr.innerHTML = `
      <span>${tf} · ${n} bars</span>
      <span>open ${fmtN(bars[0].open, 2)}</span>
      <span>last ${fmtN(bars[bars.length - 1].close, 2)}</span>
      <span style="color:${trend};">chg ${chgPct.toFixed(2)}%</span>
      <span style="margin-left:auto;">${tiSourceTag(meta?.source || 'live_fmp')}${meta?.served_from_cache ? ' (cache)' : ''}</span>
    `;

    // Hover wiring — pure client-side, zero API calls.
    const svg = document.getElementById('ti-chart-svg');
    const tooltip = document.getElementById('ti-chart-tooltip');
    const cross = document.getElementById('ti-chart-cross');
    const dot = document.getElementById('ti-chart-dot');
    if (!svg || !tooltip) return;

    svg.addEventListener('mousemove', (e) => {
        const rect = svg.getBoundingClientRect();
        const xPx = e.clientX - rect.left;
        const xVB = (xPx / rect.width) * w;
        let idx = Math.round((xVB - padX) / Math.max(stepX, 1e-6));
        if (idx < 0) idx = 0; else if (idx >= n) idx = n - 1;
        const b = bars[idx];
        if (!b) return;
        const tx = xOf(idx);
        const ty = yOf(b.close);
        cross.setAttribute('x1', tx.toFixed(1));
        cross.setAttribute('x2', tx.toFixed(1));
        cross.style.display = 'block';
        dot.setAttribute('cx', tx.toFixed(1));
        dot.setAttribute('cy', ty.toFixed(1));
        dot.style.display = 'block';

        const ts = isIntraday ? (b.datetime || '') : (b.date || '');
        const lines = [
            `<div class="cht-tt-time">${esc(ts)}</div>`,
            `<div><span>O</span><b>${fmtN(b.open, 2)}</b><span>H</span><b>${fmtN(b.high, 2)}</b></div>`,
            `<div><span>L</span><b>${fmtN(b.low, 2)}</b><span>C</span><b>${fmtN(b.close, 2)}</b></div>`,
        ];
        if (typeof b.volume === 'number') {
            lines.push(`<div><span>V</span><b>${fmtBig(b.volume)}</b></div>`);
        }
        tooltip.innerHTML = lines.join('');
        tooltip.style.display = 'block';

        // Anchor tooltip in pixel space so it tracks the cursor smoothly.
        const ttW = tooltip.offsetWidth || 140;
        let leftPx = xPx + 10;
        if (leftPx + ttW > rect.width) leftPx = xPx - ttW - 10;
        if (leftPx < 0) leftPx = 0;
        const topPx = Math.max(0, (ty / h) * rect.height - 8);
        tooltip.style.left = leftPx + 'px';
        tooltip.style.top = topPx + 'px';
    });
    svg.addEventListener('mouseleave', () => {
        tooltip.style.display = 'none';
        cross.style.display = 'none';
        dot.style.display = 'none';
    });
}

function renderTiTechnicals(tech) {
    const el = document.getElementById('ti-technicals-body');
    if (!tech) { el.innerHTML = '<div class="ti-empty">—</div>'; return; }
    const items = [
        ['SMA(10) 1day', tech.sma_10],
        ['SMA(20) 1day', tech.sma_20],
        ['EMA(20) 1day', tech.ema_20],
        ['RSI(14) 1day', tech.rsi_14],
    ];
    el.innerHTML = items.map(([label, v]) => {
        if (!v || v.source !== 'live_fmp') {
            return tiKv(label, tiSourceTag(v?.source || 'mock_fallback'),
                         esc(v?.error_short || ''));
        }
        const val = (typeof v.value === 'number')
            ? v.value.toFixed(v.indicator_type === 'rsi' ? 2 : 4)
            : '—';
        return tiKv(label,
                     `<strong>${val}</strong> <span style="color:var(--on-surface-dim);font-size:0.55rem;">@ ${esc(v.as_of_date || '—')}</span>`,
                     tiSourceTag(v.source));
    }).join('');
    el.innerHTML += `<div style="margin-top:6px;font-size:0.55rem;color:var(--on-surface-dim);font-family:var(--font-data);">display only — not used as trading rules</div>`;
}

function renderTiFundamentals(fund) {
    const el = document.getElementById('ti-fundamentals-body');
    if (!fund || fund.source !== 'live_fmp') {
        el.innerHTML = `<div class="ti-fail">${tiSourceTag(fund?.source || 'mock_fallback')} ${esc(fund?.error_short || 'unavailable')}</div>`;
        return;
    }
    const q = fund.quarter || {};
    const t = fund.ttm || {};
    const pitNote = `<span style="display:inline-block;margin-left:6px;font-size:0.55rem;padding:1px 5px;border-radius:2px;background:#1f3b2a;color:#7ec97e;border:1px solid #3a6b4a;">PIT-safe via filing_date</span>`;
    const ttmNote = `<span style="display:inline-block;margin-left:6px;font-size:0.55rem;padding:1px 5px;border-radius:2px;background:#3a2a18;color:#f4c890;border:1px solid #b86b2b;">TTM — not point-in-time safe</span>`;
    el.innerHTML = `
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
        <div>
          <div style="font-family:var(--font-ui);font-size:0.65rem;font-weight:600;color:var(--agent-blue);">Latest Quarter ${pitNote}</div>
          <div style="font-size:0.55rem;color:var(--on-surface-dim);font-family:var(--font-data);margin-bottom:4px;">
            ${esc(q.fiscal_year || '?')} ${esc(q.period || '?')} · period_end ${esc(q.fiscal_period_end || '—')} · filed ${esc(q.filing_date || '—')} · accepted ${esc(q.accepted_date || '—')}
          </div>
          ${tiKv('revenue', fmtBig(q.revenue))}
          ${tiKv('net_income', fmtBig(q.net_income))}
          ${tiKv('gross_margin', q.gross_margin_pct != null ? q.gross_margin_pct + '%' : '—')}
          ${tiKv('net_margin', q.net_margin_pct != null ? q.net_margin_pct + '%' : '—')}
          ${tiKv('current_ratio', fmtN(q.current_ratio, 2))}
          ${tiKv('debt/equity', fmtN(q.debt_to_equity, 2))}
          ${tiKv('operating_cf', fmtBig(q.operating_cash_flow))}
          ${tiKv('free_cash_flow', fmtBig(q.free_cash_flow))}
          ${tiKv('source', tiSourceTag(q.source))}
        </div>
        <div>
          <div style="font-family:var(--font-ui);font-size:0.65rem;font-weight:600;color:var(--agent-blue);">TTM Snapshot ${ttmNote}</div>
          <div style="font-size:0.55rem;color:var(--on-surface-dim);font-family:var(--font-data);margin-bottom:4px;">${esc(t.pit_note || '')}</div>
          ${tiKv('PE (TTM)', fmtN(t.pe_ratio, 2))}
          ${tiKv('PEG (TTM)', fmtN(t.peg_ratio, 2))}
          ${tiKv('P/S (TTM)', fmtN(t.price_to_sales, 2))}
          ${tiKv('P/B (TTM)', fmtN(t.price_to_book, 2))}
          ${tiKv('EV/EBITDA', fmtN(t.ev_to_ebitda, 2))}
          ${tiKv('ROE (TTM)', fmtN(t.roe, 4))}
          ${tiKv('ROA (TTM)', fmtN(t.roa, 4))}
          ${tiKv('source', tiSourceTag(t.source))}
        </div>
      </div>`;
}

function renderTiCalendar(cal) {
    const el = document.getElementById('ti-calendar-body');
    if (!cal) { el.innerHTML = '<div class="ti-empty">—</div>'; return; }
    if (cal.source !== 'live_fmp' && (!cal.earnings || cal.earnings.source !== 'live_fmp')) {
        el.innerHTML = `<div class="ti-fail">${tiSourceTag(cal.source || 'mock_fallback')} ${esc(cal.error_short || 'unavailable')}</div>`;
        return;
    }
    const earningsUp = (cal.earnings?.upcoming || []).map(r =>
        `<li>${esc(r.date || '?')} — eps_est ${fmtN(r.eps_estimated, 2)} rev_est ${fmtBig(r.revenue_estimated)}</li>`
    ).join('') || '<li style="color:var(--on-surface-dim);">none</li>';
    const earningsPast = (cal.earnings?.past || []).slice(0, 3).map(r =>
        `<li>${esc(r.date || '?')} — eps ${fmtN(r.eps_actual, 2)} (est ${fmtN(r.eps_estimated, 2)})</li>`
    ).join('') || '<li style="color:var(--on-surface-dim);">none</li>';
    const dividendsUp = (cal.dividends?.upcoming || []).map(r =>
        `<li>${esc(r.date || '?')} · $${fmtN(r.dividend, 4)}</li>`
    ).join('') || '<li style="color:var(--on-surface-dim);">none</li>';
    const dividendsPast = (cal.dividends?.past || []).slice(0, 2).map(r =>
        `<li>${esc(r.date || '?')} · $${fmtN(r.dividend, 4)}</li>`
    ).join('');
    const splits = (cal.splits?.past || []).slice(0, 2).map(r =>
        `<li>${esc(r.date || '?')} · ${esc(r.ratio)}</li>`
    ).join('');
    el.innerHTML = `
        <div style="font-family:var(--font-data);font-size:0.65rem;">
            <div style="font-weight:600;color:var(--agent-blue);">Earnings — upcoming</div>
            <ul style="margin:2px 0 6px 14px;padding:0;">${earningsUp}</ul>
            <div style="font-weight:600;color:var(--on-surface-dim);">Earnings — past</div>
            <ul style="margin:2px 0 6px 14px;padding:0;font-size:0.6rem;">${earningsPast}</ul>
            <div style="font-weight:600;color:var(--agent-blue);">Dividends — upcoming</div>
            <ul style="margin:2px 0 6px 14px;padding:0;">${dividendsUp}</ul>
            ${dividendsPast ? `<div style="font-weight:600;color:var(--on-surface-dim);">Dividends — past</div><ul style="margin:2px 0 6px 14px;padding:0;font-size:0.6rem;">${dividendsPast}</ul>` : ''}
            ${splits ? `<div style="font-weight:600;color:var(--on-surface-dim);">Splits</div><ul style="margin:2px 0 6px 14px;padding:0;font-size:0.6rem;">${splits}</ul>` : ''}
            <div style="margin-top:6px;">${tiSourceTag(cal.source)} <span style="font-size:0.55rem;color:var(--on-surface-dim);">support for future catalyst logic — no rules wired in yet</span></div>
        </div>`;
}

function renderTiDcf(dcf, quote) {
    const el = document.getElementById('ti-dcf-body');
    if (!dcf || dcf.source !== 'live_fmp') {
        el.innerHTML = `<div class="ti-fail">${tiSourceTag(dcf?.source || 'mock_fallback')} ${esc(dcf?.error_short || 'unavailable')}</div>`;
        return;
    }
    const upside = dcf.implied_upside_pct;
    const upsideColor = (typeof upside === 'number') ? (upside >= 0 ? '#7ec97e' : '#d96b6b') : 'var(--on-surface-dim)';
    el.innerHTML = `
      ${tiKv('FMP DCF', fmtN(dcf.dcf, 2))}
      ${tiKv('current_price', fmtN(dcf.current_price, 2))}
      ${tiKv('implied_upside', `<span style="color:${upsideColor}">${typeof upside === 'number' ? upside + '%' : '—'}</span>`)}
      ${tiKv('as_of_date', esc(dcf.as_of_date || '—'))}
      ${tiKv('source', tiSourceTag(dcf.source))}
      ${tiKv('PIT-safe', dcf.is_point_in_time_safe ? 'yes' : '<span style="color:#f4c890;">no — display only</span>')}
      <div style="font-size:0.55rem;color:var(--on-surface-dim);font-family:var(--font-data);margin-top:4px;">
        Display only — no rule maps DCF to a trading decision.
      </div>`;
}

// Live-FMP source styling + Ticker Inspector cards
(function injectLiveFmpStyle() {
    const css = `
      .live-fmp-source-live { background:#1f3b2a; color:#7ec97e; border:1px solid #3a6b4a; }
      .live-fmp-source-mock { background:#3a2a18; color:#d9a06b; border:1px solid #b86b2b; }
      .ti-card { background:var(--surface-dim); border:1px solid var(--outline-variant); border-radius:3px; padding:10px; min-height:120px; }
      .ti-card-title { font-family:var(--font-ui); font-size:0.65rem; font-weight:700; letter-spacing:0.06em; color:var(--agent-blue); margin-bottom:6px; padding-bottom:4px; border-bottom:1px solid var(--outline-variant); }
      .ti-card-body { font-family:var(--font-data); font-size:0.7rem; color:var(--on-surface); line-height:1.4; }
      .ti-kv { display:flex; gap:6px; align-items:baseline; padding:2px 0; }
      .ti-kv-label { color:var(--on-surface-dim); font-size:0.6rem; min-width:96px; flex-shrink:0; }
      .ti-kv-value { font-weight:500; color:var(--on-surface); }
      .ti-kv-extra { margin-left:auto; font-size:0.55rem; }
      .ti-fail { padding:6px 8px; color:var(--on-surface-dim); font-size:0.65rem; }
      .ti-empty { padding:6px 8px; color:var(--on-surface-dim); font-size:0.65rem; font-style:italic; }
      .ti-company { font-family:var(--font-ui); font-size:0.7rem; font-weight:600; margin-bottom:4px; }
      .ti-price { margin:4px 0 8px; font-family:var(--font-data); }
    `;
    const style = document.createElement('style');
    style.textContent = css;
    document.head.appendChild(style);
})();

// ── Tab switcher (Phase 2) ──
// 6 workflow tabs: decision, candidates, current-portfolio, alternative-data,
// backtest, system (plus an external Macro Regime link to /macro).
// Default tab = decision. URL hash drives tab selection for deep-linking.
// Audit Log + Rules&Settings tabs removed 2026-05-08; replaced by System State.
const TAB_IDS = ['decision', 'candidates', 'current-portfolio', 'alternative-data', 'backtest', 'system'];
const DEFAULT_TAB = 'decision';

function showTab(tabId, opts) {
    if (!TAB_IDS.includes(tabId)) tabId = DEFAULT_TAB;
    const updateHash = !(opts && opts.skipHash);
    document.querySelectorAll('[data-tab-panel]').forEach(panel => {
        panel.classList.toggle('tab-panel-active', panel.dataset.tabPanel === tabId);
    });
    document.querySelectorAll('.tab-link[data-tab]').forEach(link => {
        link.classList.toggle('tab-link-active', link.dataset.tab === tabId);
        link.setAttribute('aria-selected', link.dataset.tab === tabId ? 'true' : 'false');
    });
    if (updateHash && window.location.hash !== '#' + tabId) {
        history.replaceState(null, '', '#' + tabId);
    }
    if (tabId === 'current-portfolio') loadPortfolioIfNeeded();
    if (tabId === 'backtest') loadBacktestIfNeeded();
    window.scrollTo({ top: 0, behavior: 'auto' });
}

function initTabs() {
    document.querySelectorAll('.tab-link[data-tab]').forEach(btn => {
        btn.addEventListener('click', e => {
            e.preventDefault();
            showTab(btn.dataset.tab);
        });
    });
    window.addEventListener('hashchange', () => {
        const t = (window.location.hash || '').replace(/^#/, '');
        showTab(t || DEFAULT_TAB, { skipHash: true });
    });
    const initial = (window.location.hash || '').replace(/^#/, '') || DEFAULT_TAB;
    showTab(initial, { skipHash: true });
}

// ── On-load: cached/lightweight only. NO auto pipeline run. ──
// Rationale: the pipeline calls many FMP endpoints per ticker. Even
// with Premium, auto-running on every page load is wasteful and risks
// the rate-limit. Pipeline is now manual-only via the RUN PIPELINE button.
document.addEventListener('DOMContentLoaded', () => {
    initTabs();                  // tab switcher + hash routing
    initTickerSearch();          // search input listeners (no calls until typing)
    loadHeaderState();           // /api/status — no FMP calls beyond connection test
    // Market Lookup widget (panel-pf-lookup) on Current Portfolio mounts the
    // ticker-inspector DOM nodes (#ti-quote-body, #ti-intraday-body, etc.).
    // No initial FMP call — user must search/select a ticker to spend calls.
});

// ════════════════════════════════════════════════════════════════════
// CURRENT PORTFOLIO TAB
// Lazy-loaded on first activation of #current-portfolio. Hits 3 read-only
// endpoints in parallel: /api/portfolio/current, /history, /metrics.
// All rendering is defensive — missing fields fall back to "—".
// ════════════════════════════════════════════════════════════════════

const _pfState = {
    loaded: false,
    loading: false,
    chart: null,
    positions: [],
    sortKey: null,
    sortDir: 1,
};

function loadPortfolioIfNeeded() {
    if (_pfState.loaded || _pfState.loading) return;
    refreshPortfolio();
}

async function refreshPortfolio() {
    if (_pfState.loading) return;
    _pfState.loading = true;
    const statusEl = document.getElementById('pf-load-status');
    if (statusEl) statusEl.textContent = 'loading…';
    try {
        const [current, history, metrics] = await Promise.all([
            fetch('/api/portfolio/current').then(r => r.json()),
            fetch('/api/portfolio/history').then(r => r.json()),
            fetch('/api/portfolio/metrics').then(r => r.json()),
        ]);
        renderPortfolioCurrent(current);
        renderPortfolioHistory(history);
        renderPortfolioMetrics(metrics);
        _pfState.loaded = true;
        if (statusEl) statusEl.textContent = 'loaded ' + new Date().toLocaleTimeString();
    } catch (err) {
        console.error('Portfolio load error:', err);
        if (statusEl) statusEl.textContent = 'load failed: ' + (err.message || err);
    } finally {
        _pfState.loading = false;
    }
}

// ── Formatters ──
function pfFmtCurrency(v) {
    if (v === null || v === undefined || !isFinite(v)) return '—';
    return '$' + Number(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function pfFmtPct(v) {
    if (v === null || v === undefined || !isFinite(v)) return '—';
    return (Number(v) * 100).toFixed(2) + '%';
}
function pfFmtNum(v, dp) {
    if (v === null || v === undefined || !isFinite(v)) return '—';
    return Number(v).toFixed(dp == null ? 4 : dp);
}
function pfReturnClass(v) {
    if (v === null || v === undefined || !isFinite(v) || v === 0) return 'pf-neutral';
    return v > 0 ? 'pf-positive' : 'pf-negative';
}
function pfShortDecisionId(s) {
    if (!s) return '—';
    return String(s).replace(/^sha256:/, '').slice(0, 10);
}
function pfEsc(s) {
    if (s === null || s === undefined) return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

// ── (a) Header strip + (b) NAV row + (e) positions + (f) sleeves + (g) audit ──
function renderPortfolioCurrent(payload) {
    const status = payload?.status;
    if (status !== 'ok') {
        document.getElementById('pf-as-of').textContent = '—';
        document.getElementById('pf-rule-version').textContent = status === 'no_data' ? 'no EOD state yet' : (status || '—');
        document.getElementById('pf-decisions-count').textContent = '—';
        document.getElementById('pf-available-dates').textContent = '0';
        document.getElementById('pf-total-nav').textContent = '—';
        document.getElementById('pf-subline').textContent = 'No portfolio state file found in data/portfolio/.';
        renderPortfolioPositions([]);
        renderPortfolioSleeves(null, null);
        renderPortfolioAudit([]);
        return;
    }
    const state = payload.state || {};
    document.getElementById('pf-as-of').textContent = payload.as_of || state.as_of || '—';
    document.getElementById('pf-rule-version').textContent = state.rule_version || '—';
    const audit = state.audit || {};
    const decisions = Array.isArray(audit.decisions_processed) ? audit.decisions_processed : [];
    document.getElementById('pf-decisions-count').textContent = String(decisions.length);
    const dates = Array.isArray(payload.available_dates) ? payload.available_dates : [];
    document.getElementById('pf-available-dates').textContent = dates.length === 0
        ? '0'
        : (dates.length + ' (' + dates[0] + ' → ' + dates[dates.length - 1] + ')');

    document.getElementById('pf-total-nav').textContent = pfFmtCurrency(state.total_nav);
    const positions = Array.isArray(state.positions) ? state.positions : [];
    const sleeves = state.sleeve_exposure || {};
    const activeSleeves = Object.values(sleeves).filter(v => Number(v) > 0).length;
    document.getElementById('pf-subline').textContent =
        positions.length + ' position' + (positions.length === 1 ? '' : 's') +
        ' across ' + activeSleeves + ' sleeve' + (activeSleeves === 1 ? '' : 's') +
        '; ' + pfFmtCurrency(state.cash_balance) + ' cash';

    renderPortfolioPositions(positions);
    renderPortfolioSleeves(sleeves, state);
    renderPortfolioAudit(audit.events || []);
}

// ── (b) NAV cumulative return + (c) chart ──
function renderPortfolioHistory(payload) {
    const status = payload?.status;
    const rows = (status === 'ok' && Array.isArray(payload.rows)) ? payload.rows : [];

    // Cumulative return is the last row's cumulative_return; total return is
    // the same metric (NAV history starts at inception).
    let cum = null;
    if (rows.length > 0) {
        const last = rows[rows.length - 1];
        cum = (last.cumulative_return === null || last.cumulative_return === undefined) ? null : Number(last.cumulative_return);
    }
    const cumEl = document.getElementById('pf-cum-return');
    const totEl = document.getElementById('pf-total-return');
    cumEl.textContent = pfFmtPct(cum);
    cumEl.className = 'pf-bignum ' + pfReturnClass(cum);
    totEl.textContent = pfFmtPct(cum);
    totEl.className = 'pf-bignum ' + pfReturnClass(cum);

    const chartMetaEl = document.getElementById('pf-chart-meta');
    chartMetaEl.textContent = rows.length + ' row' + (rows.length === 1 ? '' : 's');

    // Render chart only when we have ≥2 NAV observations.
    const emptyEl = document.getElementById('pf-chart-empty');
    const wrapEl = document.getElementById('pf-chart-wrap');
    if (rows.length < 2) {
        emptyEl.style.display = '';
        wrapEl.style.display = 'none';
        if (_pfState.chart) { _pfState.chart.destroy(); _pfState.chart = null; }
        return;
    }
    emptyEl.style.display = 'none';
    wrapEl.style.display = '';
    drawNavChart(rows);
}

function drawNavChart(rows) {
    if (typeof Chart === 'undefined') {
        console.warn('Chart.js not loaded — NAV chart unavailable');
        return;
    }
    const labels = rows.map(r => r.as_of);
    const navs = rows.map(r => Number(r.total_nav));
    const dailyReturns = rows.map(r => r.daily_return);

    const ctx = document.getElementById('pf-nav-canvas');
    if (_pfState.chart) _pfState.chart.destroy();

    _pfState.chart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: 'Total NAV',
                data: navs,
                borderColor: '#a9d0ae',
                backgroundColor: 'rgba(169,208,174,0.10)',
                borderWidth: 1.5,
                pointRadius: 2,
                pointHoverRadius: 4,
                tension: 0.15,
                fill: true,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: (ctx) => {
                            const i = ctx.dataIndex;
                            const dr = dailyReturns[i];
                            const drStr = (dr === null || dr === undefined) ? '—' : pfFmtPct(dr);
                            return ['NAV: ' + pfFmtCurrency(navs[i]), 'Daily return: ' + drStr];
                        },
                    },
                },
            },
            scales: {
                x: { ticks: { color: '#8b8f96', font: { size: 10 } }, grid: { color: 'rgba(139,143,150,0.10)' } },
                y: {
                    ticks: {
                        color: '#8b8f96',
                        font: { size: 10 },
                        callback: (v) => '$' + Number(v).toLocaleString('en-US', { maximumFractionDigits: 0 }),
                    },
                    grid: { color: 'rgba(139,143,150,0.10)' },
                },
            },
        },
    });
}

// ── (d) Financial ratios ──
function renderPortfolioMetrics(payload) {
    const status = payload?.status;
    const statusEl = document.getElementById('pf-ratios-status');
    statusEl.textContent = status === 'ok'
        ? ('computed from ' + (payload.as_of_count || 0) + ' NAV obs')
        : (status === 'insufficient_data' ? 'needs ≥2 NAV observations' : (status || '—'));

    const m = (status === 'ok' && payload.metrics) ? payload.metrics : {};
    const insufficient = (status !== 'ok');

    const setRatio = (id, value, fmt, sub) => {
        const el = document.getElementById(id);
        const subEl = document.getElementById(id + '-sub');
        if (insufficient || value === null || value === undefined || !isFinite(value)) {
            el.textContent = '—';
            el.className = 'pf-ratio-value pf-neutral';
            if (subEl) subEl.textContent = insufficient ? 'needs ≥2 NAV observations' : (sub || '');
            return;
        }
        el.textContent = fmt(value);
        el.className = 'pf-ratio-value ' + pfReturnClass(value);
        if (subEl) subEl.textContent = sub || '';
    };

    setRatio('pf-sharpe', m.sharpe_ratio, v => v.toFixed(2), 'annualized · 252d');
    setRatio('pf-sortino', m.sortino_ratio, v => v.toFixed(2), 'annualized · downside-only');
    setRatio('pf-vol', m.return_volatility, v => pfFmtPct(v), 'annualized · ddof=1');

    // Max drawdown is a dict {value, peak_date, trough_date, recovery_date}
    const ddEl = document.getElementById('pf-maxdd');
    const ddSub = document.getElementById('pf-maxdd-sub');
    const dd = m.max_drawdown;
    if (insufficient || !dd || dd.value === null || dd.value === undefined) {
        ddEl.textContent = '—';
        ddEl.className = 'pf-ratio-value pf-neutral';
        ddSub.textContent = insufficient ? 'needs ≥2 NAV observations' : '';
    } else {
        ddEl.textContent = pfFmtPct(dd.value);
        ddEl.className = 'pf-ratio-value ' + (Number(dd.value) < 0 ? 'pf-negative' : 'pf-neutral');
        const parts = [];
        if (dd.peak_date) parts.push('peak ' + dd.peak_date);
        if (dd.trough_date) parts.push('trough ' + dd.trough_date);
        parts.push(dd.recovery_date ? ('recovered ' + dd.recovery_date) : 'not recovered');
        ddSub.textContent = parts.join(' · ');
    }
}

// ── (e) Positions table (sortable) ──
function renderPortfolioPositions(positions) {
    _pfState.positions = positions.slice();
    const countEl = document.getElementById('pf-positions-count');
    const emptyEl = document.getElementById('pf-positions-empty');
    const tableEl = document.getElementById('pf-positions-table');
    countEl.textContent = positions.length + ' open';
    if (positions.length === 0) {
        emptyEl.style.display = '';
        tableEl.style.display = 'none';
        return;
    }
    emptyEl.style.display = 'none';
    tableEl.style.display = '';
    sortAndRenderPositions();
}

function sortAndRenderPositions() {
    const rows = _pfState.positions.slice();
    if (_pfState.sortKey) {
        const k = _pfState.sortKey;
        const dir = _pfState.sortDir;
        rows.sort((a, b) => {
            const av = a[k]; const bv = b[k];
            if (av === bv) return 0;
            if (av === null || av === undefined) return 1;
            if (bv === null || bv === undefined) return -1;
            if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * dir;
            return String(av).localeCompare(String(bv)) * dir;
        });
    }
    const tbody = document.getElementById('pf-positions-tbody');
    tbody.innerHTML = rows.map(p => {
        const pnlClass = pfReturnClass(p.unrealized_pnl);
        return '<tr>' +
            '<td>' + pfEsc(p.ticker) + '</td>' +
            '<td>' + pfEsc(p.side || '') + '</td>' +
            '<td>' + pfEsc(p.sleeve || '') + '</td>' +
            '<td class="num">' + pfFmtPct(p.size_pct) + '</td>' +
            '<td class="num">' + pfFmtCurrency(p.entry_price) + '</td>' +
            '<td class="num">' + pfFmtCurrency(p.current_price) + '</td>' +
            '<td class="num">' + pfFmtCurrency(p.current_value) + '</td>' +
            '<td class="num ' + pnlClass + '">' + pfFmtCurrency(p.unrealized_pnl) + '</td>' +
            '<td class="num ' + pnlClass + '">' + pfFmtPct(p.unrealized_pnl_pct) + '</td>' +
            '<td>' + pfEsc(p.entry_date || '') + '</td>' +
            '<td title="' + pfEsc(p.decision_id || '') + '">' + pfEsc(pfShortDecisionId(p.decision_id)) + '</td>' +
            '</tr>';
    }).join('');
    // Wire sort headers (idempotent — innerHTML doesn't drop them)
    document.querySelectorAll('#pf-positions-table thead th[data-pf-sort]').forEach(th => {
        if (th._pfWired) return;
        th._pfWired = true;
        th.style.cursor = 'pointer';
        th.addEventListener('click', () => {
            const k = th.dataset.pfSort;
            if (_pfState.sortKey === k) _pfState.sortDir = -_pfState.sortDir;
            else { _pfState.sortKey = k; _pfState.sortDir = 1; }
            sortAndRenderPositions();
        });
    });
}

// ── (f) Sleeve exposure ──
function renderPortfolioSleeves(sleeves, state) {
    sleeves = sleeves || {};
    const totalNav = (state && state.total_nav) ? Number(state.total_nav) : 0;
    const cash = (state && state.cash_balance !== undefined) ? Number(state.cash_balance) : 0;
    const ql = Number(sleeves.quality_long || 0);
    const ss = Number(sleeves.surge_short || 0);
    const fi = Number(sleeves.fixed_income || 0);
    const pct = (v) => totalNav > 0 ? ((v / totalNav) * 100).toFixed(2) + '%' : '—';

    document.getElementById('pf-sleeve-quality').textContent = pfFmtCurrency(ql);
    document.getElementById('pf-sleeve-quality-pct').textContent = pct(ql);
    document.getElementById('pf-sleeve-surge').textContent = pfFmtCurrency(ss);
    document.getElementById('pf-sleeve-surge-pct').textContent = pct(ss);
    document.getElementById('pf-sleeve-fi').textContent = pfFmtCurrency(fi);
    document.getElementById('pf-sleeve-fi-pct').textContent = pct(fi);
    document.getElementById('pf-sleeve-cash').textContent = pfFmtCurrency(cash);
    document.getElementById('pf-sleeve-cash-pct').textContent = pct(cash);
}

// ── (g) Audit trail ──
// Each kind of event uses a different decision-verb field name:
//   surge_short_decision → ev.pm_side (e.g. "short" or null)
//   ql_friday_review     → ev.decision  (e.g. "hold" / "trim" / "exit")
//   friday_fi_review     → derived from ev.decisions_count (>0 ⇒ "deploy" else "no_op")
//   anything else        → ev.decision || ev.pm_side || "—"
// Earlier code rendered ev.decision_id in this column, but the audit events
// don't carry a decision_id → column always showed "—" (fixed 2026-05-08).
function pfAuditDecision(ev) {
    if (ev.decision) return ev.decision;
    if (ev.pm_side) return ev.pm_side;
    if (ev.kind === 'friday_fi_review') {
        const n = ev.decisions_count;
        if (typeof n === 'number') return n > 0 ? 'deploy' : 'no_op';
    }
    return '—';
}
function pfAuditSide(ev) {
    if (ev.side) return ev.side;
    if (ev.pm_side) return ev.pm_side;
    return '';
}
function pfAuditNote(ev) {
    if (ev.note) return ev.note;
    const parts = [];
    if (ev.trigger_id) parts.push('trig=' + pfShortDecisionId(ev.trigger_id));
    if (typeof ev.trigger_cost_usd === 'number') parts.push('cost=$' + ev.trigger_cost_usd.toFixed(4));
    if (typeof ev.cost_usd === 'number' && !ev.trigger_cost_usd) parts.push('cost=$' + ev.cost_usd.toFixed(4));
    if (typeof ev.decisions_count === 'number') parts.push('n=' + ev.decisions_count);
    if (ev.position_opened === false) parts.push('no_position');
    if (ev.position_opened === true) parts.push('opened');
    return parts.join(' · ');
}

function renderPortfolioAudit(events) {
    events = Array.isArray(events) ? events : [];
    document.getElementById('pf-audit-count').textContent = events.length + ' event' + (events.length === 1 ? '' : 's');
    const emptyEl = document.getElementById('pf-audit-empty');
    const tableEl = document.getElementById('pf-audit-table');
    if (events.length === 0) {
        emptyEl.style.display = '';
        tableEl.style.display = 'none';
        return;
    }
    emptyEl.style.display = 'none';
    tableEl.style.display = '';
    const tbody = document.getElementById('pf-audit-tbody');
    tbody.innerHTML = events.map(ev => {
        const sizeDelta = (ev.size_pct_delta === null || ev.size_pct_delta === undefined)
            ? '—' : pfFmtPct(ev.size_pct_delta);
        const decision = pfAuditDecision(ev);
        const side = pfAuditSide(ev);
        const note = pfAuditNote(ev);
        return '<tr>' +
            '<td>' + pfEsc(ev.timestamp || '') + '</td>' +
            '<td>' + pfEsc(ev.kind || '') + '</td>' +
            '<td>' + pfEsc(ev.ticker || '') + '</td>' +
            '<td>' + pfEsc(side) + '</td>' +
            '<td class="num">' + sizeDelta + '</td>' +
            '<td>' + pfEsc(decision) + '</td>' +
            '<td>' + pfEsc(note) + '</td>' +
            '</tr>';
    }).join('');
}

function togglePortfolioAudit() {
    const body = document.getElementById('pf-audit-body');
    const toggle = document.getElementById('pf-audit-toggle');
    const open = body.style.display !== 'none' && body.style.display !== '';
    if (open) {
        body.style.display = 'none';
        toggle.textContent = '[+] expand';
    } else {
        body.style.display = '';
        toggle.textContent = '[−] collapse';
    }
}

// ════════════════════════════════════════════════════════════════════
// BACKTEST LAB TAB — P&L run browser (read-only)
// Reads /api/backtest/runs + /run/<id> + /run/<id>/history. Lazy-loaded
// on first activation of the 'backtest' tab. Coexists with the legacy
// decision-A/B comparator panel above (which keeps its own DOM nodes).
// All formatters/colour modifiers reuse the .pf-* helpers from above.
// ════════════════════════════════════════════════════════════════════

const _btState = {
    loaded: false,
    loading: false,
    runs: null,
    selected_run_id: null,
    detail: null,
    chart: null,
};

function loadBacktestIfNeeded() {
    if (_btState.loaded || _btState.loading) return;
    refreshBacktestRuns();
}

async function refreshBacktestRuns() {
    if (_btState.loading) return;
    _btState.loading = true;
    const statusEl = document.getElementById('bt-load-status');
    if (statusEl) statusEl.textContent = 'loading runs…';
    try {
        const resp = await fetch('/api/backtest/runs');
        const j = await resp.json();
        _btState.runs = (j.status === 'ok' && Array.isArray(j.runs)) ? j.runs : [];
        renderBacktestRunSelector(_btState.runs, j.status);
        _btState.loaded = true;
        if (statusEl) statusEl.textContent = 'runs: ' + (_btState.runs.length);

        // Auto-select most-recent run if any.
        if (_btState.runs.length > 0) {
            selectBacktestRun(_btState.runs[0].run_id);
        }
    } catch (err) {
        console.error('Backtest runs load error:', err);
        if (statusEl) statusEl.textContent = 'load failed: ' + (err.message || err);
    } finally {
        _btState.loading = false;
    }
}

function renderBacktestRunSelector(runs, status) {
    const sel = document.getElementById('bt-run-select');
    const empty = document.getElementById('bt-empty-state');
    const countEl = document.getElementById('bt-runs-count');
    if (!runs || runs.length === 0) {
        sel.innerHTML = '<option value="">— no runs —</option>';
        empty.style.display = '';
        if (countEl) countEl.textContent = (status === 'no_data') ? '0 runs (data/backtest/ empty)' : '0 runs';
        // Hide downstream panels.
        ['panel-bt-header','panel-bt-nav','panel-bt-chart','panel-bt-ratios','panel-bt-events','panel-bt-reinvestment'].forEach(id => {
            const p = document.getElementById(id);
            if (p) p.style.display = 'none';
        });
        return;
    }
    empty.style.display = 'none';
    if (countEl) countEl.textContent = runs.length + ' run' + (runs.length === 1 ? '' : 's');
    sel.innerHTML = runs.map(r => {
        const finalNav = (r.final_nav === null || r.final_nav === undefined) ? '—' : pfFmtCurrency(r.final_nav);
        const days = r.trading_days_processed != null ? r.trading_days_processed : '?';
        const idShort = String(r.run_id || '').slice(-30);
        const label = (r.start_date || '?') + ' → ' + (r.end_date || '?')
            + ' · ' + days + 'd · ' + finalNav + ' · …' + idShort;
        return '<option value="' + pfEsc(r.run_id) + '">' + pfEsc(label) + '</option>';
    }).join('');
}

function onBacktestRunChange() {
    const sel = document.getElementById('bt-run-select');
    const id = sel.value;
    if (id) selectBacktestRun(id);
}

async function selectBacktestRun(run_id) {
    if (!run_id) return;
    _btState.selected_run_id = run_id;
    const sel = document.getElementById('bt-run-select');
    if (sel && sel.value !== run_id) sel.value = run_id;
    const statusEl = document.getElementById('bt-load-status');
    if (statusEl) statusEl.textContent = 'loading run detail…';
    try {
        const [detailResp, historyResp] = await Promise.all([
            fetch('/api/backtest/run/' + encodeURIComponent(run_id)).then(r => r.json()),
            fetch('/api/backtest/run/' + encodeURIComponent(run_id) + '/history').then(r => r.json()),
        ]);
        if (detailResp.status !== 'ok') {
            if (statusEl) statusEl.textContent = 'detail: ' + (detailResp.status || 'error');
            return;
        }
        _btState.detail = { manifest: detailResp.manifest, history: historyResp };
        renderBacktestDetail(detailResp.manifest, historyResp);
        if (statusEl) statusEl.textContent = 'loaded ' + new Date().toLocaleTimeString();
    } catch (err) {
        console.error('Backtest detail load error:', err);
        if (statusEl) statusEl.textContent = 'detail load failed: ' + (err.message || err);
    }
}

function renderBacktestDetail(manifest, historyPayload) {
    // Show all detail panels.
    ['panel-bt-header','panel-bt-nav','panel-bt-chart','panel-bt-ratios','panel-bt-events'].forEach(id => {
        const p = document.getElementById(id);
        if (p) p.style.display = '';
    });
    renderBacktestHeader(manifest);
    renderBacktestNavCards(manifest);
    renderBacktestChart(historyPayload);
    renderBacktestRatios(historyPayload);
    renderBacktestEvents(manifest);
    renderBacktestReinvestment(manifest);
}

function renderBacktestHeader(m) {
    const totals = m.totals || {};
    document.getElementById('bt-run-id-short').textContent = (m.run_id || '').slice(-40);
    document.getElementById('bt-run-id-short').title = m.run_id || '';
    document.getElementById('bt-created-at').textContent = m.created_at
        ? ('manifest mtime: ' + m.created_at) : '';
    document.getElementById('bt-date-range').textContent =
        (m.start_date || '?') + ' → ' + (m.end_date || '?');
    document.getElementById('bt-trading-days').textContent =
        (m.trading_days_processed != null ? m.trading_days_processed : '—');
    document.getElementById('bt-counts').textContent =
        (totals.decisions_seen != null ? totals.decisions_seen : '—') + ' / '
        + (totals.trades_filled != null ? totals.trades_filled : '—') + ' / '
        + (totals.no_ops != null ? totals.no_ops : '—');
    document.getElementById('bt-cap-events').textContent =
        (totals.size_capped_events != null ? totals.size_capped_events : '—');
    document.getElementById('bt-tx-cost').textContent =
        pfFmtCurrency(totals.transaction_cost_dollars);
    document.getElementById('bt-borrow-cost').textContent =
        pfFmtCurrency(totals.borrow_cost_dollars);
    document.getElementById('bt-rule-version').textContent = m.rule_version || '—';
    document.getElementById('bt-regime').textContent = m.regime || '—';
}

function renderBacktestNavCards(m) {
    const finalNav = m.final_nav;
    const initial = m.initial_capital;
    const metrics = m.metrics || {};
    const totalRet = metrics.total_return;
    const dd = metrics.max_drawdown || {};

    document.getElementById('bt-final-nav').textContent = pfFmtCurrency(finalNav);
    document.getElementById('bt-initial-capital').textContent =
        initial != null ? ('initial ' + pfFmtCurrency(initial)) : '';

    const trEl = document.getElementById('bt-total-return');
    trEl.textContent = pfFmtPct(totalRet);
    trEl.className = 'pf-bignum ' + pfReturnClass(totalRet);

    const ddEl = document.getElementById('bt-max-dd');
    ddEl.textContent = (dd.value === null || dd.value === undefined) ? '—' : pfFmtPct(dd.value);
    ddEl.className = 'pf-bignum ' + (Number(dd.value) < 0 ? 'pf-negative' : 'pf-neutral');
    const ddSub = document.getElementById('bt-max-dd-sub');
    if (!dd.value) {
        ddSub.textContent = '';
    } else {
        const parts = [];
        if (dd.peak_date) parts.push('peak ' + dd.peak_date);
        if (dd.trough_date) parts.push('trough ' + dd.trough_date);
        parts.push(dd.recovery_date ? ('recovered ' + dd.recovery_date) : 'not recovered');
        ddSub.textContent = parts.join(' · ');
    }
}

function renderBacktestChart(history) {
    const rows = (history && history.status === 'ok' && Array.isArray(history.rows)) ? history.rows : [];
    const meta = document.getElementById('bt-chart-meta');
    meta.textContent = rows.length + ' row' + (rows.length === 1 ? '' : 's');
    const empty = document.getElementById('bt-chart-empty');
    const wrap = document.getElementById('bt-chart-wrap');
    if (rows.length < 2) {
        empty.style.display = '';
        wrap.style.display = 'none';
        if (_btState.chart) { _btState.chart.destroy(); _btState.chart = null; }
        return;
    }
    empty.style.display = 'none';
    wrap.style.display = '';
    drawBacktestNavChart(rows);
}

function drawBacktestNavChart(rows) {
    if (typeof Chart === 'undefined') {
        console.warn('Chart.js not loaded — backtest NAV chart unavailable');
        return;
    }
    const labels = rows.map(r => r.as_of);
    const navs = rows.map(r => Number(r.total_nav));
    const dailyReturns = rows.map(r => r.daily_return);
    const initial = navs.length > 0 ? navs[0] : 1_000_000;

    const ctx = document.getElementById('bt-nav-canvas');
    if (_btState.chart) _btState.chart.destroy();

    _btState.chart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Total NAV',
                    data: navs,
                    borderColor: '#a9d0ae',
                    backgroundColor: 'rgba(169,208,174,0.10)',
                    borderWidth: 1.5,
                    pointRadius: 2,
                    pointHoverRadius: 4,
                    tension: 0.15,
                    fill: true,
                },
                {
                    label: 'Initial capital',
                    data: navs.map(() => initial),
                    borderColor: '#8b8f96',
                    borderWidth: 1,
                    borderDash: [4, 4],
                    pointRadius: 0,
                    fill: false,
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { display: true, labels: { color: '#8b8f96', font: { size: 10 } } },
                tooltip: {
                    callbacks: {
                        label: (ctx) => {
                            if (ctx.datasetIndex === 1) return 'Initial: ' + pfFmtCurrency(initial);
                            const i = ctx.dataIndex;
                            const dr = dailyReturns[i];
                            const drStr = (dr === null || dr === undefined) ? '—' : pfFmtPct(dr);
                            return ['NAV: ' + pfFmtCurrency(navs[i]), 'Daily return: ' + drStr];
                        },
                    },
                },
            },
            scales: {
                x: { ticks: { color: '#8b8f96', font: { size: 10 } }, grid: { color: 'rgba(139,143,150,0.10)' } },
                y: {
                    ticks: {
                        color: '#8b8f96',
                        font: { size: 10 },
                        callback: (v) => '$' + Number(v).toLocaleString('en-US', { maximumFractionDigits: 0 }),
                    },
                    grid: { color: 'rgba(139,143,150,0.10)' },
                },
            },
        },
    });
}

function renderBacktestRatios(history) {
    const status = history?.metrics_status || (history?.status === 'ok' ? 'ok' : '—');
    const m = history?.metrics || {};
    const insufficient = (status !== 'ok');
    document.getElementById('bt-ratios-status').textContent = insufficient
        ? (history?.row_count != null ? ('only ' + history.row_count + ' obs · needs ≥2') : 'insufficient data')
        : ('computed from ' + (history?.row_count || 0) + ' NAV obs');

    const setRatio = (id, value, fmt, sub) => {
        const el = document.getElementById(id);
        const subEl = document.getElementById(id + '-sub');
        if (insufficient || value === null || value === undefined || !isFinite(value)) {
            el.textContent = '—';
            el.className = 'pf-ratio-value pf-neutral';
            if (subEl) subEl.textContent = insufficient ? 'needs ≥2 NAV observations' : (sub || '');
            return;
        }
        el.textContent = fmt(value);
        el.className = 'pf-ratio-value ' + pfReturnClass(value);
        if (subEl) subEl.textContent = sub || '';
    };
    setRatio('bt-sharpe', m.sharpe_ratio, v => v.toFixed(2), 'annualized · 252d');
    setRatio('bt-sortino', m.sortino_ratio, v => v.toFixed(2), 'annualized · downside-only');
    setRatio('bt-vol', m.return_volatility, v => pfFmtPct(v), 'annualized · ddof=1');
    // Total return is always defined (even single row → 0)
    const tr = m.total_return;
    const trEl = document.getElementById('bt-total-return-ratio');
    if (tr === null || tr === undefined) {
        trEl.textContent = '—';
        trEl.className = 'pf-ratio-value pf-neutral';
    } else {
        trEl.textContent = pfFmtPct(tr);
        trEl.className = 'pf-ratio-value ' + pfReturnClass(tr);
    }
}

function renderBacktestEvents(m) {
    const perDay = Array.isArray(m.per_day) ? m.per_day : [];
    document.getElementById('bt-events-count').textContent =
        perDay.length + ' day' + (perDay.length === 1 ? '' : 's');
    const tbody = document.getElementById('bt-events-tbody');
    tbody.innerHTML = perDay.map(d => {
        const realisedClass = pfReturnClass(d.realised_short_pnl_today);
        return '<tr>'
            + '<td>' + pfEsc(d.as_of) + '</td>'
            + '<td class="num">' + (d.decisions_seen != null ? d.decisions_seen : '—') + '</td>'
            + '<td class="num">' + (d.trades_filled != null ? d.trades_filled : '—') + '</td>'
            + '<td class="num">' + (d.no_ops != null ? d.no_ops : '—') + '</td>'
            + '<td class="num">' + (d.size_capped != null ? d.size_capped : '—') + '</td>'
            + '<td class="num">' + pfFmtCurrency(d.transaction_cost_dollars) + '</td>'
            + '<td class="num">' + pfFmtCurrency(d.borrow_cost_dollars) + '</td>'
            + '<td class="num ' + realisedClass + '">' + pfFmtCurrency(d.realised_short_pnl_today) + '</td>'
            + '<td class="num">' + pfFmtCurrency(d.total_nav_eod) + '</td>'
            + '<td class="num">' + pfFmtCurrency(d.cash_balance_eod) + '</td>'
            + '<td class="num">' + (d.open_positions != null ? d.open_positions : '—') + '</td>'
            + '</tr>';
    }).join('');

    // Auto-expand if event count <= 50, else collapsed.
    const body = document.getElementById('bt-events-body');
    const toggle = document.getElementById('bt-events-toggle');
    const shouldExpand = perDay.length > 0 && perDay.length <= 50;
    if (shouldExpand) {
        body.style.display = '';
        toggle.textContent = '[−] collapse';
    } else {
        body.style.display = 'none';
        toggle.textContent = '[+] expand';
    }
}

function toggleBacktestEvents() {
    const body = document.getElementById('bt-events-body');
    const toggle = document.getElementById('bt-events-toggle');
    const open = body.style.display !== 'none' && body.style.display !== '';
    if (open) {
        body.style.display = 'none';
        toggle.textContent = '[+] expand';
    } else {
        body.style.display = '';
        toggle.textContent = '[−] collapse';
    }
}

function renderBacktestReinvestment(m) {
    const items = Array.isArray(m.reinvestment_decisions) ? m.reinvestment_decisions : [];
    const panel = document.getElementById('panel-bt-reinvestment');
    if (items.length === 0) {
        panel.style.display = 'none';
        return;
    }
    panel.style.display = '';
    const tbody = document.getElementById('bt-reinvestment-tbody');
    tbody.innerHTML = items.map(r => {
        return '<tr>'
            + '<td>' + pfEsc(r.decision_timestamp_iso || '') + '</td>'
            + '<td>' + pfEsc(r.execution_timestamp_iso || '') + '</td>'
            + '<td>' + pfEsc(r.regime || '') + '</td>'
            + '<td class="num">' + pfFmtCurrency(r.realized_short_pnl_dollars) + '</td>'
            + '<td class="num">' + (r.to_long_pct != null ? r.to_long_pct.toFixed(2) + '%' : '—') + '</td>'
            + '<td class="num">' + (r.to_cash_pct != null ? r.to_cash_pct.toFixed(2) + '%' : '—') + '</td>'
            + '<td>' + pfEsc(r.selection_rationale || '') + '</td>'
            + '</tr>';
    }).join('');
}


// ════════════════════════════════════════════════════════════════════
// BACKTEST LAB (rebuilt 2026-05-08) — friction-adjusted 5-cell view
// Backed by /api/backtest_lab/cells. Plotly bundled offline at
// /static/plotly.min.js. Replaces legacy data/backtest/<run_id>/ browser.
// ════════════════════════════════════════════════════════════════════
const _BL_COLORS = {
    solo_mar:    '#9aa0a6',  // grey
    noadas_mar:  '#1f77b4',  // blue
    default_mar: '#2ca02c',  // green
    default_apr: '#1f77b4',  // blue
    no_sec_apr:  '#d62728',  // red
};
const _BL_PHASE1_IDS = ['solo_mar', 'noadas_mar', 'default_mar'];
const _BL_PHASE2_IDS = ['default_apr', 'no_sec_apr'];

let _blState = { loaded: false, payload: null, currentPhase: 'phase1', forwardOpen: false };

async function loadBacktestLab() {
    if (_blState.loaded) return;
    const statusEl = document.getElementById('bl-overall-status');
    if (statusEl) statusEl.textContent = 'Loading…';
    try {
        const resp = await fetch('/api/backtest_lab/cells', { cache: 'no-store' });
        const j = await resp.json();
        if (j.status !== 'ok') throw new Error('non-ok status: ' + j.status);
        _blState.payload = j;
        _blState.loaded = true;
        renderBacktestLab();
    } catch (err) {
        if (statusEl) statusEl.textContent = '⚠ load failed: ' + err.message;
        const p1 = document.getElementById('bl-p1-metrics-row');
        if (p1) p1.innerHTML = '<div class="bl-empty">Failed to load /api/backtest_lab/cells: ' + (err.message || err) + '</div>';
    }
}

function renderBacktestLab() {
    if (!_blState.payload) return;
    const j = _blState.payload;
    const cellsById = {};
    j.cells.forEach(c => { cellsById[c.id] = c; });

    // Footer metadata
    const fdocs = j.cells.filter(c => c.dir_path).map(c => c.dir_path.split(/[\\\/]/).pop()).join(' · ');
    const setIf = (id, txt) => { const el = document.getElementById(id); if (el) el.textContent = txt; };
    setIf('bl-footer-rule-version', j.rule_version || 'unknown');
    setIf('bl-footer-doc-version', j.doc_version || 'unknown');
    setIf('bl-footer-hash', (j.regression_hash || '').slice(0, 24) + '…');
    setIf('bl-footer-asof', new Date().toISOString().slice(0, 19) + 'Z');
    setIf('bl-footer-dirs', 'data/decisions/<cell>_tx30bps_borrow100/ · ' + j.cells.length + ' cells loaded');

    // Overall status
    const warned = j.cells.reduce((n, c) => n + ((c.warnings || []).length), 0);
    const missing = j.cells.filter(c => c.status === 'missing').length;
    let status = `Loaded · ${j.cells.length} cells · cost ${j.cost_model.tx_bps_single_side} bps tx + ${j.cost_model.borrow_apy_pct}% APY borrow`;
    if (warned) status += ` · ⚠ ${warned} canonical mismatch warning(s)`;
    if (missing) status += ` · ⚠ ${missing} missing cell dir(s)`;
    setIf('bl-overall-status', status);

    blRenderPhase1Metrics(_BL_PHASE1_IDS.map(id => cellsById[id]));
    blRenderPhase2Metrics(_BL_PHASE2_IDS.map(id => cellsById[id]));
    blRenderPhase1Chart(_BL_PHASE1_IDS.map(id => cellsById[id]));
    blRenderPhase2Chart(_BL_PHASE2_IDS.map(id => cellsById[id]));
    blRenderAttribution(cellsById);
    blRenderForward(cellsById);
}

function blFmtUSD(v) {
    if (v == null) return '—';
    return '$' + Math.round(v).toLocaleString();
}
function blFmtPct(v, dp) {
    if (v == null) return '—';
    const sign = v > 0 ? '+' : '';
    return sign + v.toFixed(dp == null ? 2 : dp) + '%';
}
function blColorClass(v) { return v >= 0 ? 'bl-pos' : 'bl-neg'; }

function blMetricCard(c) {
    if (!c) return '';
    if (c.status === 'missing') {
        return `<div class="bl-cell-card bl-cell-missing">
            <div class="bl-cell-name">${c.name}</div>
            <div class="bl-cell-config">⚠ ${c.data_quality_flag}</div>
        </div>`;
    }
    const ev = c.events || {};
    const ust = ev.ust_face_total ? '$' + Math.round(ev.ust_face_total).toLocaleString() : '—';
    const warns = (c.warnings || []).length
        ? `<div class="bl-cell-warn">⚠ ${(c.warnings || []).join('; ')}</div>` : '';
    const fwdLine = c.forward
        ? `<div class="bl-cell-secondary"><span>fwd 5/30:</span><strong>${blFmtUSD(c.forward.final_nav)}</strong> <span class="${blColorClass(c.forward.final_ret_pct)}">${blFmtPct(c.forward.final_ret_pct)}</span></div>`
        : '';
    return `<div class="bl-cell-card">
        <div class="bl-cell-name">${c.name}</div>
        <div class="bl-cell-config">${c.config}</div>
        <div class="bl-cell-nav">${blFmtUSD(c.final_nav)}</div>
        <div class="bl-cell-ret ${blColorClass(c.return_pct)}">${blFmtPct(c.return_pct)}</div>
        <div class="bl-cell-dd">MaxDD <strong>${blFmtPct(c.max_drawdown.value_pct)}</strong> <span class="bl-cell-dim">on ${c.max_drawdown.trough_date || '—'}</span></div>
        <div class="bl-cell-secondary">
            <span>entries:</span><strong>${ev.entry || 0}</strong>
            <span>exits:</span><strong>${ev.exit || 0}</strong>
            <span>UST face:</span><strong>${ust}</strong>
        </div>
        <div class="bl-cell-secondary">
            <span>tx:</span><strong>$${Math.round(c.total_tx_cost_usd || 0)}</strong>
            <span>borrow:</span><strong>$${Math.round(c.total_borrow_cost_usd || 0)}</strong>
            <span>median short hold:</span><strong>${c.median_short_holding_days != null ? c.median_short_holding_days + 'd' : '—'}</strong>
        </div>
        ${fwdLine}
        ${warns}
    </div>`;
}

function blRenderPhase1Metrics(cells) {
    const root = document.getElementById('bl-p1-metrics-row');
    if (!root) return;
    root.innerHTML = cells.map(blMetricCard).join('');
}

function blRenderPhase2Metrics(cells) {
    const root = document.getElementById('bl-p2-metrics-row');
    if (!root) return;
    root.innerHTML = cells.map(blMetricCard).join('');
}

function blPlotlyLayout(opts) {
    return Object.assign({
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor:  'rgba(0,0,0,0)',
        font: { family: 'inherit', color: '#cdd2d8', size: 12 },
        margin: { t: 20, r: 80, b: 50, l: 80 },
        hovermode: 'x unified',
        showlegend: true,
        legend: { x: 0.01, y: 0.99, bgcolor: 'rgba(20,24,28,0.7)', bordercolor: '#3a4150', borderwidth: 1 },
        xaxis: { gridcolor: '#2a3038', linecolor: '#3a4150', tickformat: '%m-%d' },
        yaxis: {
            gridcolor: '#2a3038', linecolor: '#3a4150',
            tickformat: '$,.3s',
            tickprefix: '',
        },
    }, opts || {});
}

function blPlotlyConfig() {
    return { responsive: true, displaylogo: false,
             modeBarButtonsToRemove: ['lasso2d', 'select2d', 'autoScale2d'] };
}

function blNavLineTrace(cell, color) {
    if (!cell || cell.status !== 'ok') return null;
    const xs = cell.rows.map(r => r.as_of);
    const ys = cell.rows.map(r => r.combined_nav);
    const customRet = cell.rows.map(r => ((r.combined_nav / 1e6 - 1) * 100));
    return {
        x: xs, y: ys,
        type: 'scatter', mode: 'lines+markers',
        name: cell.name,
        line: { color: color, width: 2.5, shape: 'spline', smoothing: 0.6 },
        marker: { size: 4, color: color },
        customdata: customRet,
        hovertemplate:
            '<b>%{fullData.name}</b><br>' +
            '%{x|%Y-%m-%d}<br>' +
            'NAV $%{y:,.0f}<br>' +
            'cum return %{customdata:+.2f}%' +
            '<extra></extra>',
    };
}

function blRenderPhase1Chart(cells) {
    const div = document.getElementById('bl-p1-chart');
    if (!div || !window.Plotly) return;
    const traces = [];
    cells.forEach(c => {
        const t = blNavLineTrace(c, _BL_COLORS[c.id]);
        if (t) traces.push(t);
        // Drawdown trough marker
        if (c && c.status === 'ok' && c.max_drawdown.trough_date) {
            const r = c.rows.find(rr => rr.as_of === c.max_drawdown.trough_date);
            if (r) {
                traces.push({
                    x: [c.max_drawdown.trough_date], y: [r.combined_nav],
                    type: 'scatter', mode: 'markers',
                    name: c.name + ' MaxDD',
                    marker: { size: 11, color: _BL_COLORS[c.id], symbol: 'diamond-open',
                              line: { width: 2, color: _BL_COLORS[c.id] } },
                    showlegend: false,
                    hovertemplate: '<b>' + c.name + ' · MaxDD trough</b><br>'
                        + c.max_drawdown.trough_date + '<br>NAV $%{y:,.0f}<br>DD ' + c.max_drawdown.value_pct.toFixed(2) + '%<extra></extra>',
                });
            }
        }
    });
    const layout = blPlotlyLayout({
        height: 420,
        shapes: [{ type: 'line', xref: 'paper', x0: 0, x1: 1,
                   yref: 'y', y0: 1_000_000, y1: 1_000_000,
                   line: { color: '#5a6068', width: 1, dash: 'dot' } }],
        annotations: [{ xref: 'paper', x: 1, xanchor: 'right',
                        yref: 'y', y: 1_000_000, yanchor: 'bottom',
                        text: 'initial $1.000M', showarrow: false,
                        font: { color: '#7a808a', size: 10 } }],
    });
    Plotly.newPlot(div, traces, layout, blPlotlyConfig());
}

function blRenderPhase2Chart(cells) {
    const div = document.getElementById('bl-p2-chart');
    if (!div || !window.Plotly) return;
    const traces = [];
    cells.forEach(c => {
        const t = blNavLineTrace(c, _BL_COLORS[c.id]);
        if (t) traces.push(t);
    });
    const layout = blPlotlyLayout({
        height: 420,
        shapes: [
            { type: 'line', xref: 'paper', x0: 0, x1: 1,
              yref: 'y', y0: 1_000_000, y1: 1_000_000,
              line: { color: '#5a6068', width: 1, dash: 'dot' } },
            { type: 'line', x0: '2025-04-07', x1: '2025-04-07',
              yref: 'paper', y0: 0, y1: 1,
              line: { color: '#ffa726', width: 1.5, dash: 'dash' } },
            { type: 'line', x0: '2025-04-08', x1: '2025-04-08',
              yref: 'paper', y0: 0, y1: 1,
              line: { color: '#ffa726', width: 1.5, dash: 'dash' } },
        ],
        annotations: [
            { xref: 'paper', x: 1, xanchor: 'right',
              yref: 'y', y: 1_000_000, yanchor: 'bottom',
              text: 'initial $1.000M', showarrow: false,
              font: { color: '#7a808a', size: 10 } },
            { x: '2025-04-07', xanchor: 'left',
              yref: 'paper', y: 0.04, yanchor: 'bottom',
              text: '§31 fires<br>4/7 −10.65%', showarrow: false,
              font: { color: '#ffa726', size: 10 },
              bgcolor: 'rgba(20,24,28,0.85)',
              bordercolor: '#ffa726', borderwidth: 1, borderpad: 4 },
            { x: '2025-04-08', xanchor: 'left',
              yref: 'paper', y: 0.18, yanchor: 'bottom',
              text: '§31 fires<br>4/8 −12.05%<br>force-buy SPY at next-day open', showarrow: false,
              font: { color: '#ffa726', size: 10 },
              bgcolor: 'rgba(20,24,28,0.85)',
              bordercolor: '#ffa726', borderwidth: 1, borderpad: 4 },
        ],
    });
    Plotly.newPlot(div, traces, layout, blPlotlyConfig());
}

function blRenderAttribution(cellsById) {
    // Phase 1 buckets
    const solo = cellsById.solo_mar; const noad = cellsById.noadas_mar; const def = cellsById.default_mar;
    if (solo && noad && def && solo.status === 'ok' && noad.status === 'ok' && def.status === 'ok') {
        const a = (noad.return_pct - solo.return_pct);
        const b = (def.return_pct - noad.return_pct);
        const aEl = document.getElementById('bl-bucket-a');
        const bEl = document.getElementById('bl-bucket-b');
        if (aEl) aEl.textContent = (a >= 0 ? '+' : '') + a.toFixed(2) + ' pp';
        if (bEl) bEl.textContent = (b >= 0 ? '+' : '') + b.toFixed(2) + ' pp';
    }
    // Phase 2 in-window gap
    const a2 = cellsById.default_apr; const b2 = cellsById.no_sec_apr;
    if (a2 && b2 && a2.status === 'ok' && b2.status === 'ok') {
        const gap = b2.return_pct - a2.return_pct;
        const gEl = document.getElementById('bl-p2-gap');
        if (gEl) gEl.textContent = (gap >= 0 ? '+' : '') + gap.toFixed(2) + ' pp · Cell B leads';
    }
}

function blRenderForward(cellsById) {
    const a = cellsById.default_apr; const b = cellsById.no_sec_apr;
    if (!a || !b || !a.forward || !b.forward) return;
    const inGap = b.return_pct - a.return_pct;
    const fwdGap = b.forward.final_ret_pct - a.forward.final_ret_pct;
    const reversal = fwdGap - inGap;
    const gEl = document.getElementById('bl-fwd-gap');
    const rEl = document.getElementById('bl-fwd-reversal');
    if (gEl) gEl.textContent = (fwdGap >= 0 ? '+' : '') + fwdGap.toFixed(2) + ' pp';
    if (rEl) rEl.textContent = `narrowed from ${inGap >= 0 ? '+' : ''}${inGap.toFixed(2)}pp · reversal ${reversal >= 0 ? '+' : ''}${reversal.toFixed(2)}pp`;
}

function blDrawForwardChart() {
    const div = document.getElementById('bl-p2-fwd-chart');
    if (!div || !window.Plotly || !_blState.payload) return;
    const cells = _BL_PHASE2_IDS.map(id => _blState.payload.cells.find(c => c.id === id));
    const traces = [];
    cells.forEach(c => {
        if (!c || c.status !== 'ok') return;
        const color = _BL_COLORS[c.id];
        // In-window solid
        const inXs = c.rows.map(r => r.as_of);
        const inYs = c.rows.map(r => r.combined_nav);
        const inRets = inYs.map(y => (y/1e6 - 1) * 100);
        traces.push({
            x: inXs, y: inYs, type: 'scatter', mode: 'lines+markers',
            name: c.name + ' (in-window)',
            line: { color: color, width: 2.5 },
            marker: { size: 4, color: color },
            customdata: inRets,
            hovertemplate: '<b>%{fullData.name}</b><br>%{x|%Y-%m-%d}<br>NAV $%{y:,.0f}<br>cum %{customdata:+.2f}%<extra></extra>',
        });
        // Forward dashed
        if (c.forward && c.forward.rows && c.forward.rows.length) {
            const fwdXs = c.forward.rows.map(r => r.as_of);
            const fwdYs = c.forward.rows.map(r => r.nav_combined_forward);
            const fwdRets = fwdYs.map(y => (y/1e6 - 1) * 100);
            traces.push({
                x: fwdXs, y: fwdYs, type: 'scatter', mode: 'lines',
                name: c.name + ' (forward MtM)',
                line: { color: color, width: 2.5, dash: 'dash' },
                customdata: fwdRets,
                hovertemplate: '<b>%{fullData.name}</b><br>%{x|%Y-%m-%d}<br>NAV $%{y:,.0f}<br>cum %{customdata:+.2f}%<extra></extra>',
            });
        }
    });
    const layout = blPlotlyLayout({
        height: 460,
        shapes: [
            { type: 'line', xref: 'paper', x0: 0, x1: 1, yref: 'y', y0: 1_000_000, y1: 1_000_000,
              line: { color: '#5a6068', width: 1, dash: 'dot' } },
            { type: 'line', x0: '2025-04-22', x1: '2025-04-22', yref: 'paper', y0: 0, y1: 1,
              line: { color: '#7a808a', width: 1, dash: 'dot' } },
            { type: 'line', x0: '2025-05-19', x1: '2025-05-19', yref: 'paper', y0: 0, y1: 1,
              line: { color: '#ba68c8', width: 1.5, dash: 'dash' } },
        ],
        annotations: [
            { x: '2025-04-22', xanchor: 'left', yref: 'paper', y: 0.96, yanchor: 'top',
              text: 'backtest cut<br>2025-04-22', showarrow: false,
              font: { color: '#7a808a', size: 10 } },
            { x: '2025-05-19', xanchor: 'left', yref: 'paper', y: 0.7, yanchor: 'top',
              text: 'DFS → COF<br>1.0192 / share<br>2-leg @ 15 bps', showarrow: false,
              font: { color: '#ba68c8', size: 10 },
              bgcolor: 'rgba(20,24,28,0.85)',
              bordercolor: '#ba68c8', borderwidth: 1, borderpad: 4 },
        ],
    });
    Plotly.newPlot(div, traces, layout, blPlotlyConfig());
}

function blSelectPhase(phase) {
    _blState.currentPhase = phase;
    document.querySelectorAll('.bl-seg-btn').forEach(b => {
        b.classList.toggle('bl-seg-active', b.dataset.phase === phase);
    });
    document.getElementById('bl-phase1-view').style.display = phase === 'phase1' ? '' : 'none';
    document.getElementById('bl-phase2-view').style.display = phase === 'phase2' ? '' : 'none';
    // Re-trigger Plotly resize so charts render correctly when switching tabs
    setTimeout(() => {
        if (window.Plotly) {
            const ids = phase === 'phase1' ? ['bl-p1-chart'] : ['bl-p2-chart'];
            ids.forEach(id => {
                const el = document.getElementById(id);
                if (el && el.data) Plotly.Plots.resize(el);
            });
        }
    }, 50);
}

function blToggleForward() {
    const body = document.getElementById('bl-fwd-body');
    const arrow = document.getElementById('bl-fwd-toggle-arrow');
    if (!body) return;
    const isOpen = body.style.display !== 'none';
    body.style.display = isOpen ? 'none' : '';
    if (arrow) arrow.textContent = isOpen ? '▶' : '▼';
    _blState.forwardOpen = !isOpen;
    if (!isOpen) {
        // First open: draw the chart (deferred until visible for sizing)
        setTimeout(() => blDrawForwardChart(), 50);
    }
}

// Hook into the existing tab activation so we lazy-load when user clicks Backtest Lab
document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-tab="backtest"]').forEach(btn => {
        btn.addEventListener('click', () => {
            // Slight delay to let tab show
            setTimeout(() => loadBacktestLab(), 30);
        });
    });
    // If page loads with backtest tab already active, load now
    const tabPanel = document.getElementById('tab-backtest');
    if (tabPanel && tabPanel.classList.contains('tab-panel-active')) {
        loadBacktestLab();
    }
});
