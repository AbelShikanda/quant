"""Render the agentic gold dashboard.

This module builds the HTML page served by :mod:`goldbot.live_dashboard`.
No MT5 access happens here; everything comes from the read-only ledger.
"""

from __future__ import annotations

import html
import json
from typing import Any


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _chip(label: str, value: str, tone: str = "neutral") -> str:
    return (
        f'<div class="chip chip-{_e(tone)}"><span class="chip-label">{_e(label)}</span>'
        f'<span class="chip-value">{_e(value)}</span></div>'
    )


def _card(node_id: str, title: str, subtitle: str, body: str, tone: str) -> str:
    return (
        f'<div class="node node-{_e(tone)}" id="{_e(node_id)}">'
        f'<div class="node-title">{_e(title)}</div>'
        f'<div class="node-subtitle">{_e(subtitle)}</div>'
        f'<div class="node-body" id="{_e(node_id)}-body">{body}</div>'
        f'</div>'
    )


_APP_JS = r"""
const REFRESH_MS = 2000;
const escapeHtml = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const byId = (id) => document.getElementById(id);
const fmtScore = (v) => v == null ? 'n/a' : (v >= 0 ? '+' : '') + Number(v).toFixed(3);
const fmtNum = (v, d = 2) => v == null ? 'n/a' : Number(v).toFixed(d);
const fmtTime = (t) => { try { return new Date(t).toISOString(); } catch { return String(t); } };

function renderProfileSpec(spec) {
  const select = byId('profileSelect'); if (!select) return;
  select.value = (spec.key || 'micro_50');
  byId('profileDescription').textContent = spec.description || '';
  byId('profileWarning').textContent = spec.high_risk_override ? 'HIGH RISK OVERRIDE — not promotion-eligible · one stop can be a one-fifth account loss' : 'Promotion-eligible profile';
  const rows = (spec.spec_rows || []).map((r) => `<div class="spec-row"><dt>${escapeHtml(r.label)}</dt><dd class="${escapeHtml(r.tone || '')}">${escapeHtml(r.value)}</dd></div>`).join('');
  byId('profileSpecList').innerHTML = rows;
  byId('profileSelectSummary').textContent = spec.select_summary || '';
  byId('profileForgoSummary').textContent = spec.forgo_summary || '';
}
function renderSteps(s) {
  const running = !!((s.agent || {}).running);
  const mt5 = (s.mt5 || {}).status || 'unknown';
  const accountTradeMode = s.account_trade_mode;
  const accountLogin = s.account_login;
  const expectedLogin = s.expected_login;
  const loginOk = (accountLogin != null && expectedLogin != null) ? String(accountLogin) === String(expectedLogin) : null;
  const accountOk = accountTradeMode === 0 && loginOk === true;
  const accountWait = accountTradeMode == null || accountLogin == null;
  const fundamentals = s.fundamentals || {};
  const stale = fundamentals.stale_inputs || [];
  const missing = fundamentals.missing_inputs || [];
  const macroOk = stale.length === 0 && missing.length === 0;
  const fusion = s.fusion || {};
  const decision = s.latest_decision || {};
  const decisionType = decision.decision_type;
  const entryReady = fusion.entry_candidate && decisionType === 'ENTER_LONG';
  const stepState = {
    spawn: running ? 'pass' : 'wait',
    mt5: mt5 === 'up' ? 'pass' : (mt5 === 'down' ? 'fail' : 'wait'),
    account: accountOk ? 'pass' : (accountWait ? 'wait' : 'fail'),
    macro: macroOk ? 'pass' : 'fail',
    decision: decisionType === 'ENTER_LONG' ? 'pass' : (decisionType ? 'wait' : 'wait'),
    order: entryReady ? 'pass' : (decisionType ? 'fail' : 'wait'),
  };
  const ids = { spawn: 'step-spawn', mt5: 'step-mt5', account: 'step-account', macro: 'step-macro', decision: 'step-decision', order: 'step-order' };
  Object.entries(ids).forEach(([key, id]) => { const el = byId(id); if (!el) return; el.className = 'step step-' + stepState[key]; });
  const notes = [];
  if (!running) notes.push('Agent stopped; click Start Agent to run the evaluation loop.');
  if (mt5 !== 'up') notes.push('MT5 status: ' + mt5 + '.');
  if (accountTradeMode != null && accountTradeMode !== 0) notes.push('Account trade_mode is ' + accountTradeMode + ' (expected 0 demo).');
  if (loginOk === false) notes.push('Expected login ' + expectedLogin + ' but connected to ' + accountLogin + '.');
  if (stale.length) notes.push('Stale macro: ' + stale.join(', ') + ' (fresh required).');
  if (missing.length) notes.push('Missing macro: ' + missing.join(', ') + '.');
  if (decisionType === 'NO_TRADE') notes.push('Decision: NO_TRADE — ' + ((decision.reasons || []).join('; ')));
  if (decisionType === 'ENTER_LONG') notes.push('ENTER_LONG — ready for explicit SEND DEMO ORDER authorization.');
  byId('agentStepsNote').textContent = notes.join(' ') || 'Watching agent steps...';
}
function updateState(data) {
  const s = data.state || data;
  renderProfileSpec(s.profile_spec || {});
  renderSteps(s);
  const agent = s.agent || {};
  const running = !!agent.running;
  const mt5Status = (s.mt5 || {}).status || 'unknown';
  const killSwitch = !!(s.risk_state || {}).kill_switch;
  const drawdown = (s.risk_state || {}).current_drawdown_pct;
  const fundamentals = s.fundamentals || {};
  const technicals = s.technicals || {};
  const fusion = s.fusion || {};
  const overseer = s.overseer || {};
  const decision = s.latest_decision || {};

  byId('botChipValue').textContent = running ? 'RUNNING' : 'STOPPED';
  byId('botChip').className = 'chip chip-' + (running ? 'green' : 'neutral');
  byId('mt5ChipValue').textContent = mt5Status.toUpperCase();
  byId('mt5Chip').className = 'chip chip-' + (mt5Status === 'up' ? 'green' : (mt5Status === 'down' ? 'red' : 'neutral'));
  byId('riskChipValue').textContent = 'DD ' + (drawdown == null ? 'n/a' : Number(drawdown).toFixed(2) + '%') + ' · KILL ' + (killSwitch ? 'ON' : 'off');
  byId('riskChip').className = 'chip chip-' + (killSwitch ? 'red' : 'green');

  const profile = (s.account_profile || 'n/a').toUpperCase();
  const profileChip = document.querySelector('.chip-amber');
  if (profileChip) profileChip.querySelector('.chip-value').textContent = profile;

  byId('managerDecision').textContent = decision.decision_type || 'n/a';
  byId('managerStatus').textContent = running ? 'THINKING' : 'IDLE';
  byId('managerCard').classList.toggle('running', running);

  byId('signalScore').textContent = fmtScore(fusion.combined_score);
  const score = fusion.combined_score == null ? 0 : Math.max(-1, Math.min(1, fusion.combined_score));
  byId('signalBar').style.width = Math.round((score + 1) * 50) + '%';
  byId('oppFundScore').textContent = fmtScore(fundamentals.score);
  byId('oppTechScore').textContent = fmtScore(technicals.score);
  byId('oppOverseerVerdict').textContent = overseer.verdict || 'n/a';
  byId('oppGate').textContent = s.gate_blocker || 'unknown';
  byId('opportunityReason').textContent = s.gate_blocker || 'unknown';
  byId('acceptBtn').disabled = !(fusion.entry_candidate && decision.decision_type === 'ENTER_LONG');

  const comp = (fundamentals.components || []).map((c) => `<tr><td>${escapeHtml(c.input_id)}</td><td>${escapeHtml(c.series_id)}</td><td>${fmtNum(c.value, 2)}</td><td>${fmtNum(c.weight, 2)}</td><td class="${c.stale ? 'stale' : ''}">${c.stale ? 'STALE' : 'ok'}</td><td>${escapeHtml(c.note)}</td></tr>`).join('');
  byId('fundamentalsTable').innerHTML = `<table><thead><tr><th>Input</th><th>Series</th><th>Value</th><th>Weight</th><th>State</th><th>Note</th></tr></thead><tbody>${comp || '<tr><td colspan="6">no components</td></tr>'}</tbody></table>`;
  const triggers = technicals.entry_triggers || {};
  const indicators = technicals.indicators || {};
  byId('technicalsTable').innerHTML = `<table><thead><tr><th>Item</th><th>Value</th></tr></thead><tbody><tr><td>score</td><td>${fmtScore(technicals.score)}</td></tr>${Object.entries(indicators).map(([k,v]) => `<tr><td>${escapeHtml(k)}</td><td>${escapeHtml(JSON.stringify(v))}</td></tr>`).join('')}${Object.entries(triggers).map(([k,v]) => `<tr><td>trigger: ${escapeHtml(k)}</td><td>${v ? 'pass' : 'fail'}</td></tr>`).join('')}</tbody></table>`;
  byId('fusionReasons').innerHTML = `<ul>${(fusion.reasons || []).map((r) => `<li>${escapeHtml(r)}</li>`).join('')}</ul>`;
  byId('overseerVerdict').textContent = overseer.verdict || overseer.decision || 'n/a';
  byId('gateBlockerDetail').textContent = s.gate_blocker || 'unknown';

  const logEl = byId('terminal');
  const lines = (s.agent_logs || []).slice(-120);
  logEl.innerHTML = lines.map((l) => `<span class="${l.includes('ERROR') ? 'line-error' : l.includes('WARN') ? 'line-warn' : 'line-info'}">${escapeHtml(l)}</span>`).join('');
  logEl.scrollTop = logEl.scrollHeight;
  byId('logCount').textContent = (s.agent_logs || []).length + ' lines';

  const bars = s.chart_bars || [];
  byId('chartBadges').innerHTML = bars.length >= 30 ? '<span class="badge badge-green">LIVE BARS</span>' : '<span class="badge badge-neutral">WAITING FOR BARS</span>';
  const decisions = (s.decisions || []).slice(0, 5).map((d) => `<span class="pill">${escapeHtml(d.as_of_utc)} ${escapeHtml(d.decision_type)} ${fmtScore(d.combined_score)}</span>`).join('');
  byId('latestDecisions').innerHTML = decisions || 'no decisions';
  const orders = (s.orders || []).slice(0, 3).map((o) => `${o.side} ${o.volume} @ ${o.price ?? 'n/a'}`).join(' · ');
  byId('recentOrders').textContent = orders || 'no orders';
  const positions = (s.positions || []).slice(0, 3).map((p) => `${p.side} ${p.volume} open ${p.open_price} pnl ${p.realized_pnl ?? 'n/a'}`).join(' · ');
  byId('recentPositions').textContent = positions || 'no positions';
  const curve = s.equity_curve || [];
  const lastEq = curve.length ? curve[curve.length - 1].equity : s.equity;
  byId('equitySummary').textContent = `equity ${fmtNum(lastEq, 2)} · dd ${fmtNum(drawdown, 2)}%`;
  byId('chartFooterText').textContent = bars.length ? `${bars.length} bars loaded · M15` : 'bars will appear when the bot writes them to the ledger';
  byId('chartTimeRange').textContent = bars.length && bars[0].time ? fmtTime(bars[0].time) + ' → ' + fmtTime(bars[bars.length - 1].time) : '';
  drawChart(bars, s);
}

function drawChart(bars, state) {
  const canvas = byId('priceChart'); const ctx = canvas.getContext('2d');
  const w = canvas.clientWidth; const h = 260; canvas.width = w * 2; canvas.height = h * 2; ctx.scale(2, 2);
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = '#0a0a0a'; ctx.fillRect(0, 0, w, h);
  if (!bars || bars.length < 2) { ctx.fillStyle = '#4b5563'; ctx.font = '12px system-ui'; ctx.fillText('No price history in ledger yet', 16, h / 2); return; }
  const pad = 6; const chartH = h - pad * 2; const highs = bars.map(b => b.high); const lows = bars.map(b => b.low);
  const hi = Math.max(...highs); const lo = Math.min(...lows); const range = hi - lo || 1;
  const barW = Math.max(2, Math.floor((w - 20) / bars.length) - 1);
  bars.forEach((b, i) => {
    const x = 6 + i * (barW + 1);
    const yH = pad + (hi - b.high) * chartH / range; const yL = pad + (hi - b.low) * chartH / range;
    const yO = pad + (hi - b.open) * chartH / range; const yC = pad + (hi - b.close) * chartH / range;
    const up = b.close >= b.open; ctx.fillStyle = up ? '#22c55e' : '#ef4444'; ctx.strokeStyle = up ? '#22c55e' : '#ef4444';
    ctx.beginPath(); ctx.moveTo(x + barW / 2, yH); ctx.lineTo(x + barW / 2, yL); ctx.stroke();
    const top = Math.min(yO, yC); const bh = Math.max(1, Math.abs(yC - yO)); ctx.fillRect(x, top, barW, bh);
  });
  const pos = (state.open_positions || [])[0];
  if (pos) {
    const lines = [[pos.open_price, '#fbbf24', 'Entry'], [pos.stop_loss, '#ef4444', 'Stop'], [pos.take_profit, '#22c55e', 'Target']];
    lines.forEach(([p, c, label]) => { if (p == null) return; const y = pad + (hi - p) * chartH / range; ctx.strokeStyle = c; ctx.setLineDash([5,4]); ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke(); ctx.setLineDash([]); ctx.fillStyle = c; ctx.font = '10px system-ui'; ctx.fillText(label, 4, y - 2); });
  }
}

const byIdApp = (id) => document.getElementById(id);
const initialState = JSON.parse(byIdApp('appState').dataset.state);
updateState({ v: 1, state: initialState });

async function postJson(path, body) {
  const res = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  if (!res.ok) { const err = await res.json().catch(() => ({ error: res.statusText })); throw new Error(err.error || 'request failed'); }
  return res.json();
}
function closeDialog(id) { byId(id).close(); }
byId('startAgentBtn').addEventListener('click', async () => { try { await postJson('/api/agent/start', {}); } catch (e) { alert(e.message); } });
byId('stopAgentBtn').addEventListener('click', async () => { try { await postJson('/api/agent/stop', {}); } catch (e) { alert(e.message); } });
byId('profileSelect').addEventListener('change', async (e) => { try { await postJson('/api/agent/profile', { profile: e.target.value }); } catch (err) { alert(err.message); } });
byId('reviewEvidenceBtn').addEventListener('click', () => byId('evidenceDrawer').showModal());
byId('closeEvidenceDrawerBtn').addEventListener('click', () => closeDialog('evidenceDrawer'));
byId('declineBtn').addEventListener('click', () => byId('declineModal').showModal());
byId('closeDeclineModalBtn').addEventListener('click', () => closeDialog('declineModal'));
byId('cancelDeclineBtn').addEventListener('click', () => closeDialog('declineModal'));
byId('shutdownDashboardBtn').addEventListener('click', () => byId('shutdownModal').showModal());
byId('closeShutdownModalBtn').addEventListener('click', () => closeDialog('shutdownModal'));
byId('cancelShutdownBtn').addEventListener('click', () => closeDialog('shutdownModal'));
byId('confirmShutdownBtn').addEventListener('click', async () => {
  try { await postJson('/api/server/shutdown', { confirm: 'SHUT DASHBOARD' }); } catch (e) { /* server may exit before the response */ }
  document.body.innerHTML = '<div class="panel" style="padding:40px;text-align:center;margin:48px auto;max-width:520px"><h2>Dashboard is shutting down</h2><p class="muted">You can close this tab. Reopen the dashboard with the Gold Bot Dashboard shortcut on your Desktop.</p></div>';
});
byId('confirmDeclineBtn').addEventListener('click', async () => { const reason = byId('declineReasonInput').value.trim(); if (!reason) { alert('Reason required'); return; } try { await postJson('/api/agent/decline', { reason }); byId('declineModal').close(); byId('declineReasonInput').value = ''; } catch (e) { alert(e.message); } });
byId('acceptBtn').addEventListener('click', () => byId('executeModal').showModal());
byId('closeExecuteModalBtn').addEventListener('click', () => closeDialog('executeModal'));
byId('cancelExecuteBtn').addEventListener('click', () => closeDialog('executeModal'));
byId('executePhraseInput').addEventListener('input', (e) => { byId('confirmExecuteBtn').disabled = e.target.value.trim() !== 'SEND DEMO ORDER'; });
byId('confirmExecuteBtn').addEventListener('click', async () => { const phrase = byId('executePhraseInput').value.trim(); if (phrase !== 'SEND DEMO ORDER') { alert('Phrase must be SEND DEMO ORDER'); return; } try { await postJson('/api/agent/execute', { confirm: phrase }); byId('executeModal').close(); byId('executePhraseInput').value = ''; } catch (e) { alert(e.message); } });
byId('copyLogsBtn').addEventListener('click', async () => { try { await navigator.clipboard.writeText(byId('terminal').innerText); } catch (e) { /* ignore */ } });

function connectStream() {
  const source = new EventSource('/api/stream');
  source.addEventListener('state', (ev) => { try { const data = JSON.parse(ev.data); updateState(data); } catch (e) { source.close(); fallbackPoll(); } });
  source.onerror = () => { source.close(); fallbackPoll(); };
}
function fallbackPoll() { setInterval(async () => { try { const res = await fetch('/api/state'); const data = await res.json(); updateState(data); } catch (e) { /* retry */ } }, REFRESH_MS); }
connectStream();
"""

_CSS = """
:root{
  color-scheme: dark;
  --bg:#000; --surface:#0a0a0a; --panel:#111; --panel2:#161616; --border:#222;
  --text:#e5e7eb; --muted:#9ca3af; --cyan:#22d3ee; --amber:#fbbf24; --green:#22c55e; --red:#ef4444; --purple:#a855f7; --gold:#fbbf24;
}
*{box-sizing:border-box} body{margin:0;padding:18px;background:var(--bg);color:var(--text);font:13px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
h1,h2,h3{margin:0;font-weight:700}
.state-strip{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:12px 16px;background:var(--surface);border:1px solid var(--border);border-radius:12px;position:sticky;top:8px;z-index:5}
.profile-panel{display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:14px;margin-top:12px}
.profile-select{background:var(--surface);border:1px solid var(--border);color:var(--text);padding:10px 12px;border-radius:8px;font-size:13px;width:100%}
.profile-spec{display:grid;gap:6px}
.spec-row{display:flex;justify-content:space-between;gap:12px;padding:6px 8px;background:var(--surface);border:1px solid var(--border);border-radius:8px;font-size:11px}
.spec-row dt{color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.spec-row dd{margin:0;font-weight:600}
.spec-row dd.green{color:#4ade80}.spec-row dd.red{color:#f87171}.spec-row dd.amber{color:#fbbf24}
.profile-compare{display:grid;grid-template-columns:auto 1fr;gap:8px 12px;margin-top:10px;border-top:1px solid var(--border);padding-top:10px;font-size:11px}
.profile-compare dt{color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.profile-compare dd{margin:0}
.profile-warning{margin:0;font-size:11px;color:#fca5a5}
.agent-steps{margin-top:12px;padding:14px}
.steps{display:grid;grid-template-columns:repeat(6,1fr);gap:8px;list-style:none;margin:8px 0 0;padding:0}
.step{display:flex;align-items:center;gap:8px;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:8px;font-size:11px;color:var(--muted)}
.step-dot{width:14px;height:14px;border-radius:99px;background:#1f2937;flex-shrink:0}
.step-pass .step-dot{background:var(--green);box-shadow:0 0 10px rgba(34,197,94,.4)}
.step-fail .step-dot{background:var(--red);box-shadow:0 0 10px rgba(239,68,68,.4)}
.step-wait .step-dot{background:#713f12;box-shadow:0 0 10px rgba(251,191,36,.25)}
.step-pass{color:#bbf7d0}.step-fail{color:#fecaca}.step-wait{color:#fde68a}
.agent-steps-note{margin:8px 0 0;color:var(--muted);font-size:11px}
.brand{display:flex;gap:10px;align-items:center}
.logo{width:24px;height:24px;border-radius:7px;background:radial-gradient(circle at 40% 40%,var(--cyan),var(--gold));box-shadow:0 0 12px var(--cyan)}
.brand-text h1{font-size:16px;letter-spacing:.08em}
.brand-text p{margin:0;color:var(--muted);font-size:11px}
.chips{display:flex;gap:8px;flex-wrap:wrap}
.chip{display:flex;flex-direction:column;min-width:86px;padding:6px 10px;border:1px solid var(--border);border-radius:8px;background:var(--panel)}
.chip-label{color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.05em}
.chip-value{font-weight:700;font-size:12px}
.chip-green{border-color:#166534;box-shadow:0 0 10px rgba(34,197,94,.28)}
.chip-red{border-color:#7f1d1d;box-shadow:0 0 10px rgba(239,68,68,.28)}
.chip-amber{border-color:#713f12}
.controls{display:flex;gap:8px}
.layout{display:grid;grid-template-columns:330px 1fr 370px;gap:14px;margin-top:12px}
.panel{background:var(--panel);border:1px solid var(--border);border-radius:12px}
.node-panel{padding:12px}
.panel-title{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.panel-subtitle{margin:0 0 8px;color:var(--muted);font-size:11px}
.node-grid{display:grid;gap:8px}
.node{border:1px solid var(--border);border-radius:10px;padding:8px;background:var(--surface)}
.node-title{font-size:12px;font-weight:700}
.node-subtitle{font-size:10px;color:var(--muted)}
.kv{display:flex;justify-content:space-between;font-size:11px;margin:2px 0}
.kv span:last-child{color:#e5e7eb}
.node-cyan{border-color:#164e63;box-shadow:inset 0 0 12px rgba(34,211,238,.08)}
.node-amber{border-color:#713f12;box-shadow:inset 0 0 12px rgba(251,191,36,.08)}
.node-purple{border-color:#581c87;box-shadow:inset 0 0 12px rgba(168,85,247,.08)}
.node-green{border-color:#14532d;box-shadow:inset 0 0 12px rgba(34,197,94,.08)}
.center{display:flex;flex-direction:column;gap:12px}
.manager-card{position:relative;display:flex;align-items:center;justify-content:center;min-height:150px;border-radius:14px;background:linear-gradient(180deg,#161616,#0a0a0a);border:1px solid var(--border);overflow:hidden}
.manager-ring{position:absolute;border:1px solid rgba(168,85,247,.25);border-radius:50%;box-shadow:0 0 24px rgba(168,85,247,.12)}
.manager-ring-outer{width:220px;height:220px}
.manager-ring-inner{width:150px;height:150px;border-color:rgba(168,85,247,.35);animation:spin 18s linear infinite}
.manager-core{position:relative;text-align:center;z-index:1}
.manager-label{font-size:10px;letter-spacing:.12em;color:var(--muted)}
.manager-decision{font-size:38px;font-weight:800;letter-spacing:.08em;color:var(--gold)}
.manager-status{font-size:12px;letter-spacing:.18em;color:var(--purple)}
.running .manager-ring-outer{animation:pulse 2.2s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:.35;transform:scale(1)}50%{opacity:.75;transform:scale(1.04)}}
@keyframes spin{from{transform:rotate(0)}to{transform:rotate(360deg)}}
.opportunity-card{display:flex;flex-direction:column;padding:14px;gap:12px}
.opportunity-body{display:grid;grid-template-columns:1fr 1fr;gap:12px;align-items:center}
.signal-score{font-size:36px;font-weight:800;color:var(--gold)}
.signal-label{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.signal-bar{height:10px;background:#111;border-radius:99px;overflow:hidden;margin-top:6px}
.signal-bar span{display:block;height:100%;background:linear-gradient(90deg,var(--red),var(--amber),var(--gold))}
.opportunity-meta{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}
.opportunity-meta div{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:8px}
.opportunity-meta dt{color:var(--muted);font-size:10px;text-transform:uppercase}
.opportunity-meta dd{margin:2px 0 0;font-size:13px}
.opportunity-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:6px}
.hint{color:var(--muted);font-size:11px;margin:0}
.chart-card{padding:12px}
.chart-header{display:flex;justify-content:space-between;align-items:center}
.chart-badges{display:flex;gap:6px}
#priceChart{width:100%;height:260px;border-radius:10px;background:var(--surface)}
.chart-footer{display:flex;justify-content:space-between;color:var(--muted);font-size:11px;margin-top:6px}
.evidence-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;padding:10px}
.evidence-item{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:8px}
.evidence-item dt{color:var(--muted);font-size:10px;text-transform:uppercase}
.evidence-item dd{margin:3px 0 0;font-size:11px}
.terminal-panel{display:flex;flex-direction:column;padding:12px;gap:8px}
.terminal-header{display:flex;align-items:center;justify-content:space-between}
#terminal{background:#050505;border:1px solid var(--border);border-radius:10px;color:#a3e635;font-family:'Cascadia Code',Consolas,monospace;font-size:11px;line-height:1.45;overflow:auto;flex:1;min-height:220px;padding:10px;white-space:pre-wrap}
.line-error{color:#f87171}.line-warn{color:#fbbf24}.line-info{color:#cbd5e1}
.terminal-footer{display:flex;justify-content:space-between;align-items:center}
.pill{display:inline-block;margin:2px 2px;padding:2px 6px;background:#111;border:1px solid var(--border);border-radius:6px;font-size:10px}
.btn{display:inline-flex;align-items:center;gap:8px;padding:8px 12px;border-radius:8px;border:1px solid var(--border);background:#111;color:var(--text);font-weight:600;cursor:pointer;transition:all .15s ease}
.btn:hover{border-color:#334155;background:#161616}
.btn:disabled{opacity:.4;cursor:not-allowed}
.btn-primary{border-color:#155e75;background:#083344;color:#67e8f9}
.btn-primary:hover{box-shadow:0 0 12px rgba(34,211,238,.35)}
.btn-danger{border-color:#7f1d1d;background:#450a0a;color:#fca5a5}
.btn-danger:hover{box-shadow:0 0 12px rgba(239,68,68,.35)}
.btn-execute{border-color:#713f12;background:#3f2a08;color:var(--gold);font-size:13px}
.btn-execute:hover:not(:disabled){box-shadow:0 0 16px rgba(245,158,11,.45)}
.btn-secondary{border-color:#1e293b;background:#0f172a}
.btn-ghost{background:transparent;border-color:#1e293b}
.badge{display:inline-flex;align-items:center;padding:2px 7px;border-radius:999px;font-size:10px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;border:1px solid var(--border)}
.badge-green{color:#4ade80;border-color:#166534;box-shadow:0 0 8px rgba(34,197,94,.25)}
.badge-red{color:#f87171;border-color:#7f1d1d;box-shadow:0 0 8px rgba(239,68,68,.25)}
.badge-neutral{color:var(--muted)}
.modal{border:none;border-radius:12px;background:#111;color:var(--text);padding:0}
.modal::backdrop{background:rgba(0,0,0,.75)}
.modal-content{padding:14px;min-width:320px;max-width:560px}
.modal-narrow{max-width:460px}
.modal-header{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--border);padding-bottom:10px;margin-bottom:10px}
.modal-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:10px}
.icon-btn{background:transparent;border:none;color:var(--muted);font-size:20px;cursor:pointer}
.field{display:flex;flex-direction:column;gap:6px;margin:8px 0}
.field input,.field textarea{background:var(--surface);border:1px solid var(--border);border-radius:8px;color:var(--text);padding:10px;font-family:inherit;font-size:13px}
.field input:focus,.field textarea:focus{outline:1px solid var(--gold);border-color:var(--gold)}
.danger-text{color:#fca5a5}
.gate-blocker{color:var(--amber)}
.footer{display:flex;justify-content:space-between;gap:20px;color:var(--muted);font-size:11px;margin-top:14px;border-top:1px solid var(--border);padding-top:10px}
.stale{color:#fca5a5}
.muted{color:var(--muted)}
@media (max-width:1100px){.layout{grid-template-columns:1fr}.node-grid{grid-template-columns:repeat(2,1fr)}.opportunity-body{grid-template-columns:1fr}}
@media (prefers-reduced-motion: reduce){.manager-ring-inner,.running .manager-ring-outer{animation:none}}
"""


def render_page(state: dict[str, Any], refresh_seconds: float = 2.0) -> str:
    profile = state.get("account_profile") or "n/a"
    mode = str(state.get("mode") or "unknown").replace("_", " ").title()
    agent = state.get("agent") or {}
    running = bool(agent.get("running"))
    mt5 = state.get("mt5") or {}
    risk = state.get("risk_state") or {}
    kill_switch = risk.get("kill_switch")
    drawdown = risk.get("current_drawdown_pct")

    body = f"""
<header class="state-strip" role="banner">
  <div class="brand">
    <span class="logo"></span>
    <div class="brand-text">
      <h1>GOLD BOT</h1>
      <p>Quantamental Agentic Desk · FBS-Demo XAUUSD</p>
    </div>
  </div>
  <div class="chips">
    {_chip("Mode", mode, "neutral")}
    {_chip("Profile", profile.upper(), "amber")}
    <div class="chip chip-{'green' if running else 'neutral'}" id="botChip"><span class="chip-label">BOT</span><span class="chip-value" id="botChipValue">{'RUNNING' if running else 'STOPPED'}</span></div>
    <div class="chip chip-{'green' if mt5.get('status') == 'up' else 'red'}" id="mt5Chip"><span class="chip-label">MT5</span><span class="chip-value" id="mt5ChipValue">{_e(str(mt5.get('status', 'unknown')).upper())}</span></div>
    <div class="chip chip-{'red' if kill_switch else 'green'}" id="riskChip"><span class="chip-label">RISK</span><span class="chip-value" id="riskChipValue">DD {_e(drawdown)} · KILL {'ON' if kill_switch else 'off'}</span></div>
  </div>
  <div class="controls">
    <button id="startAgentBtn" class="btn btn-primary" type="button">{'Running' if running else 'Start Agent'}</button>
    <button id="stopAgentBtn" class="btn btn-danger" type="button" {'disabled' if not running else ''}>Stop Agent</button>
    <button id="shutdownDashboardBtn" class="btn btn-danger btn-shutdown" type="button">Shutdown Dashboard</button>
  </div>
</header>

<section class="panel profile-panel" id="profilePanel">
  <div class="profile-left">
    <h2 class="panel-title">Account Profile</h2>
    <label class="field"><span>Profile</span>
      <select id="profileSelect" class="profile-select">
        <option value="micro_50">micro_50 · $50 · 100:1 · HIGH RISK</option>
        <option value="micro_100">micro_100 · $100 · 50:1 · HIGH RISK</option>
        <option value="standard_1k">standard_1k · $1,000 · 5:1 · promotion-eligible</option>
      </select>
    </label>
    <p class="panel-subtitle" id="profileDescription"></p>
    <p class="profile-warning" id="profileWarning"></p>
  </div>
  <div class="profile-right">
    <div class="profile-spec" id="profileSpecList"></div>
    <dl class="profile-compare">
      <dt>You select</dt><dd id="profileSelectSummary"></dd>
      <dt>You forgo</dt><dd id="profileForgoSummary"></dd>
    </dl>
  </div>
</section>

<section class="panel agent-steps" id="agentStepsPanel">
  <h2 class="panel-title">Agent Steps</h2>
  <ol class="steps" id="agentStepsList">
    <li class="step" id="step-spawn"><span class="step-dot"></span> Spawn agent</li>
    <li class="step" id="step-mt5"><span class="step-dot"></span> MT5 connection</li>
    <li class="step" id="step-account"><span class="step-dot"></span> Demo account &amp; expected login</li>
    <li class="step" id="step-macro"><span class="step-dot"></span> Macro inputs fresh</li>
    <li class="step" id="step-decision"><span class="step-dot"></span> Strategy decision</li>
    <li class="step" id="step-order"><span class="step-dot"></span> Order authorization</li>
  </ol>
  <p class="agent-steps-note" id="agentStepsNote">Click Start Agent to run the evaluation loop; watch the steps clear or see where it blocks.</p>
</section>

<main class="layout">
  <aside class="panel node-panel">
    <h2 class="panel-title">Agent Architecture</h2>
    <p class="panel-subtitle">Sources → Analysts → Fusion → Overseer → Risk → Execution</p>

    <div class="node-grid">
      {_card("mt5", "MT5 XAUUSD", "Price + Execution Venue", '<div class="kv"><span>Feed</span><span id="kvMt5">…</span></div><div class="kv"><span>Bars</span><span>M15 / H4</span></div>', "cyan")}
      {_card("fundamental", "Fundamental Agent", "What to consider", '<div class="kv"><span>Score</span><span id="kvFundScore">…</span></div><div class="kv"><span>Weight-bearing</span><span id="kvFundInputs">…</span></div>', "cyan")}
      {_card("technical", "Technical Agent", "When to act", '<div class="kv"><span>Score</span><span id="kvTechScore">…</span></div><div class="kv"><span>ATR</span><span id="kvTechAtr">…</span></div>', "cyan")}
      {_card("fusion", "Fusion Engine", "Both-layer agreement", '<div class="kv"><span>Score</span><span id="kvFusionScore">…</span></div><div class="kv"><span>Entry</span><span id="kvFusionEntry">…</span></div>', "amber")}
      {_card("overseer", "Overseer", "Rule-based referee", '<div class="kv"><span>Verdict</span><span id="kvOverseerVerdict">…</span></div><div class="kv"><span>Gate blocker</span><span id="kvGateBlocker">…</span></div>', "purple")}
      {_card("risk", "Risk Engine", "Veto-immune exits", '<div class="kv"><span>Drawdown</span><span id="kvDrawdown">…</span></div><div class="kv"><span>Kill switch</span><span id="kvKillSwitch">…</span></div>', "green")}
    </div>
  </aside>

  <section class="center">
    <div class="manager-card" id="managerCard">
      <div class="manager-ring manager-ring-outer"></div>
      <div class="manager-ring manager-ring-inner"></div>
      <div class="manager-core">
        <div class="manager-label">MANAGER</div>
        <div class="manager-decision" id="managerDecision">—</div>
        <div class="manager-status" id="managerStatus">IDLE</div>
      </div>
    </div>

    <div class="panel opportunity-card" id="opportunityCard">
      <div class="opportunity-header">
        <h2 class="panel-title">Opportunity</h2>
        <p class="panel-subtitle" id="opportunityReason">{_e(state.get('gate_blocker') or 'unknown')}</p>
      </div>
      <div class="opportunity-body">
        <div class="opportunity-signal">
          <div class="signal-score" id="signalScore">—</div>
          <div class="signal-label">combined score</div>
          <div class="signal-bar"><span id="signalBar" style="width:0%"></span></div>
        </div>
        <dl class="opportunity-meta">
          <div><dt>Fundamentals</dt><dd id="oppFundScore">—</dd></div>
          <div><dt>Technicals</dt><dd id="oppTechScore">—</dd></div>
          <div><dt>Overseer</dt><dd id="oppOverseerVerdict">—</dd></div>
          <div><dt>Gate</dt><dd id="oppGate">—</dd></div>
        </dl>
        <div class="opportunity-actions">
          <button class="btn btn-ghost" id="reviewEvidenceBtn" type="button">Review Evidence</button>
          <button class="btn btn-secondary" id="declineBtn" type="button">Decline</button>
          <button class="btn btn-execute" id="acceptBtn" type="button" disabled>Accept &amp; Execute</button>
        </div>
        <p class="hint">Accept &amp; Execute stays locked until every preflight item is PASS and the decision is ENTER_LONG.</p>
      </div>
    </div>

    <div class="panel chart-card" id="chartCard">
      <div class="chart-header">
        <h2 class="panel-title">XAUUSD · Candlesticks</h2>
        <div class="chart-badges" id="chartBadges"><span class="badge badge-neutral">WAITING FOR BARS</span></div>
      </div>
      <canvas id="priceChart" aria-label="Price chart" height="260"></canvas>
      <div class="chart-footer"><span id="chartFooterText">bars will appear when the bot writes them to the ledger</span><span id="chartTimeRange" class="muted"></span></div>
    </div>

    <div class="panel evidence-grid" id="evidenceGrid">
      <div class="evidence-item"><dt>Latest decisions</dt><dd id="latestDecisions" class="muted">—</dd></div>
      <div class="evidence-item"><dt>Recent orders</dt><dd id="recentOrders" class="muted">—</dd></div>
      <div class="evidence-item"><dt>Positions</dt><dd id="recentPositions" class="muted">—</dd></div>
      <div class="evidence-item"><dt>Equity curve</dt><dd id="equitySummary" class="muted">—</dd></div>
    </div>
  </section>

  <aside class="panel terminal-panel">
    <div class="terminal-header">
      <h2 class="panel-title">Agent Terminal</h2>
      <span id="terminalStatus" class="badge badge-{'green' if running else 'neutral'}">{'STREAMING' if running else 'IDLE'}</span>
    </div>
    <pre id="terminal" tabindex="0" aria-label="Agent output"></pre>
    <div class="terminal-footer"><button class="btn btn-ghost" id="copyLogsBtn" type="button">Copy logs</button><span class="muted" id="logCount">0 lines</span></div>
  </aside>
</main>

<dialog id="evidenceDrawer" class="modal">
  <div class="modal-content">
    <div class="modal-header"><h2>Decision Evidence</h2><button class="icon-btn" id="closeEvidenceDrawerBtn" type="button" aria-label="Close">×</button></div>
    <div class="modal-body">
      <h3>Fundamentals</h3>
      <div id="fundamentalsTable"></div>
      <h3>Technicals</h3>
      <div id="technicalsTable"></div>
      <h3>Fusion</h3>
      <div id="fusionReasons"></div>
      <h3>Overseer</h3>
      <div id="overseerVerdict"></div>
      <h3>Gate blocker</h3>
      <p id="gateBlockerDetail" class="gate-blocker"></p>
    </div>
  </div>
</dialog>

<dialog id="executeModal" class="modal">
  <div class="modal-content modal-narrow">
    <div class="modal-header"><h2>Authorize demo order</h2><button class="icon-btn" id="closeExecuteModalBtn" type="button" aria-label="Close">×</button></div>
    <div class="modal-body">
      <p class="danger-text">This places one demo-market order on XAUUSD with the strategy's stop and target. It is not real money, but it is a real broker order on the demo account.</p>
      <label class="field"><span>Type SEND DEMO ORDER to authorize</span><input id="executePhraseInput" type="text" autocomplete="off" spellcheck="false" placeholder="SEND DEMO ORDER"></label>
      <div class="modal-actions"><button class="btn btn-secondary" id="cancelExecuteBtn" type="button">Cancel</button><button class="btn btn-execute" id="confirmExecuteBtn" type="button" disabled>Authorize &amp; Execute</button></div>
    </div>
  </div>
</dialog>

<dialog id="declineModal" class="modal">
  <div class="modal-content modal-narrow">
    <div class="modal-header"><h2>Decline opportunity</h2><button class="icon-btn" id="closeDeclineModalBtn" type="button" aria-label="Close">×</button></div>
    <div class="modal-body">
      <label class="field"><span>Reason</span><textarea id="declineReasonInput" rows="3" placeholder="e.g. fundamental gate still failing; wait for fresh broad USD print"></textarea></label>
      <div class="modal-actions"><button class="btn btn-secondary" id="cancelDeclineBtn" type="button">Cancel</button><button class="btn btn-danger" id="confirmDeclineBtn" type="button">Log decline</button></div>
    </div>
  </div>
</dialog>

<dialog id="shutdownModal" class="modal">
  <div class="modal-content modal-narrow">
    <div class="modal-header"><h2>Shut down the dashboard</h2><button class="icon-btn" id="closeShutdownModalBtn" type="button" aria-label="Close">×</button></div>
    <div class="modal-body">
      <p class="danger-text">This stops the local dashboard HTTP server and the local agent (if running). The SQLite ledger is not affected; your bot state is preserved.</p>
      <p class="muted">Reopen the dashboard with the <strong>Gold Bot Dashboard</strong> shortcut on your Desktop, or by running <code>goldbot-launch.bat</code>.</p>
      <div class="modal-actions"><button class="btn btn-secondary" id="cancelShutdownBtn" type="button">Cancel</button><button class="btn btn-danger" id="confirmShutdownBtn" type="button">Shut it down</button></div>
    </div>
  </div>
</dialog>

<footer class="footer"><span>GOLD BOT · read-only dashboard + guarded executor · loopback only · no scope changes</span><span>Kill switch / risk exits are veto-immune and are never affected by this UI.</span></footer>

<script>
{_APP_JS}
</script>
"""

    state_json = json.dumps(state, default=str, ensure_ascii=False)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="Gold bot agentic dashboard — read-only observer with guarded demo execution.">
<title>Gold Bot — Agentic Desk</title>
<style>{_CSS}</style>
</head>
<body>
{body}
<div id="appState" data-state="{_e(state_json)}" hidden></div>
</body>
</html>"""
