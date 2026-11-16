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
var REFRESH_MS = 2000;
var byId = function (id) { return document.getElementById(id); };
var escapeHtml = function (v) {
  return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
    return ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'})[c];
  });
};
var fmtScore = function (v) {
  if (v == null) { return 'n/a'; }
  var n = Number(v);
  if (isNaN(n)) { return 'n/a'; }
  return (n >= 0 ? '+' : '') + n.toFixed(3);
};
var fmtNum = function (v, d) {
  if (v == null) { return 'n/a'; }
  var n = Number(v);
  if (isNaN(n)) { return 'n/a'; }
  return n.toFixed(d == null ? 2 : d);
};
var fmtTime = function (t) {
  try { return new Date(t).toISOString(); } catch (e) { return String(t); }
};
var toast = function (message) {
  var el = byId('toast');
  if (!el) { return; }
  el.textContent = message;
  el.className = 'toast show';
  window.clearTimeout(el._timer);
  el._timer = window.setTimeout(function () { el.className = 'toast'; }, 4000);
};

function setStep(id, state) {
  var el = byId(id);
  if (el) { el.className = 'step step-' + state; }
}

function setNodeState(id, state) {
  var el = byId(id);
  if (!el) { return; }
  el.className = 'node node-' + state;
}
function renderProfileSpec(spec) {
  if (!spec) { return; }
  var select = byId('profileSelect');
  if (select) { select.value = spec.key || 'micro_50'; }
  byId('profileDescription').textContent = spec.description || '';
  byId('profileWarning').textContent = spec.high_risk_override
    ? 'HIGH RISK OVERRIDE - not promotion-eligible. One stop can be a one-fifth account loss.'
    : 'Promotion-eligible profile';
  var rowsHtml = '';
  var rows = spec.spec_rows || [];
  for (var i = 0; i < rows.length; i += 1) {
    var r = rows[i];
    rowsHtml += '<div class="spec-row"><dt>' + escapeHtml(r.label) + '</dt><dd class="' + escapeHtml(r.tone || '') + '">' + escapeHtml(r.value) + '</dd></div>';
  }
  byId('profileSpecList').innerHTML = rowsHtml;
  byId('profileSelectSummary').textContent = spec.select_summary || '';
  byId('profileForgoSummary').textContent = spec.forgo_summary || '';
}

function renderSteps(s) {
  var running = !!(s.agent && s.agent.running);
  var mt5 = (s.mt5 && s.mt5.status) || 'unknown';
  var accountTradeMode = s.account_trade_mode;
  var accountLogin = s.account_login;
  var expectedLogin = s.expected_login;
  var loginOk = null;
  if (accountLogin != null && expectedLogin != null) {
    loginOk = String(accountLogin) === String(expectedLogin);
  }
  var accountOk = (accountTradeMode === 0 && loginOk === true);
  var accountWait = (accountTradeMode == null || accountLogin == null);
  var fundamentals = s.fundamentals || {};
  var stale = fundamentals.stale_inputs || [];
  var missing = fundamentals.missing_inputs || [];
  var macroOk = stale.length === 0 && missing.length === 0;
  var fusion = s.fusion || {};
  var decision = s.latest_decision || {};
  var decisionType = decision.decision_type;
  var entryReady = !!(fusion.entry_candidate && decisionType === 'ENTER_LONG');
  setStep('step-spawn', running ? 'pass' : 'wait');
  setStep('step-mt5', mt5 === 'up' ? 'pass' : (mt5 === 'down' ? 'fail' : 'wait'));
  setStep('step-account', accountOk ? 'pass' : (accountWait ? 'wait' : 'fail'));
  setStep('step-macro', macroOk ? 'pass' : 'fail');
  setStep('step-decision', decisionType === 'ENTER_LONG' ? 'pass' : (decisionType ? 'wait' : 'wait'));
  setStep('step-order', entryReady ? 'pass' : (decisionType ? 'fail' : 'wait'));
  var notes = [];
  if (running) {
    notes.push('AGENT RUNNING - evaluation loop active.');
  } else {
    notes.push('Agent stopped; click Start Agent to run the evaluation loop.');
  }
  if (mt5 !== 'up') { notes.push('MT5 status: ' + mt5 + '.'); }
  if (accountTradeMode != null && accountTradeMode !== 0) {
    notes.push('Account trade_mode is ' + accountTradeMode + ' (expected 0 demo).');
  }
  if (loginOk === false) {
    notes.push('Expected login ' + expectedLogin + ' but connected to ' + accountLogin + '.');
  }
  if (stale.length) { notes.push('Stale macro: ' + stale.join(', ') + ' (fresh required).'); }
  if (missing.length) { notes.push('Missing macro: ' + missing.join(', ') + '.'); }
  if (decisionType === 'NO_TRADE') {
    notes.push('Decision: NO_TRADE - ' + ((decision.reasons || []).join('; ')));
  } else if (decisionType === 'ENTER_LONG') {
    notes.push('ENTER_LONG - ready for explicit SEND DEMO ORDER authorization.');
  }
  byId('agentStepsNote').textContent = notes.join(' ') || 'Watching agent steps...';
}

function updateState(data) {
  var s = data.state || data;
  renderProfileSpec(s.profile_spec || {});
  renderSteps(s);
  var agent = s.agent || {};
  var running = !!agent.running;
  var mt5Status = (s.mt5 && s.mt5.status) || 'unknown';
  var killSwitch = !!(s.risk_state && s.risk_state.kill_switch);
  var drawdown = s.risk_state ? s.risk_state.current_drawdown_pct : null;
  var fundamentals = s.fundamentals || {};
  var technicals = s.technicals || {};
  var fusion = s.fusion || {};
  var overseer = s.overseer || {};
  var decision = s.latest_decision || {};

  byId('botChipValue').textContent = running ? 'RUNNING' : 'STOPPED';
  byId('botChip').className = 'chip chip-' + (running ? 'green' : 'neutral');
  byId('mt5ChipValue').textContent = String(mt5Status).toUpperCase();
  byId('mt5Chip').className = 'chip chip-' + (mt5Status === 'up' ? 'green' : (mt5Status === 'down' ? 'red' : 'neutral'));
  var drawdownText = drawdown == null ? 'n/a' : Number(drawdown).toFixed(2) + '%';
  byId('riskChipValue').textContent = 'DD ' + drawdownText + ' KILL ' + (killSwitch ? 'ON' : 'off');
  byId('riskChip').className = 'chip chip-' + (killSwitch ? 'red' : 'green');

  var profile = (s.account_profile || 'n/a').toUpperCase();
  var profileChip = document.querySelector('.chip-amber');
  if (profileChip) {
    profileChip.querySelector('.chip-value').textContent = profile;
  }

  byId('managerDecision').textContent = decision.decision_type || 'n/a';
  byId('managerStatus').textContent = running ? 'THINKING' : 'IDLE';
  if (byId('managerCard')) {
    byId('managerCard').className = 'manager-card' + (running ? ' running' : '');
  }

  byId('signalScore').textContent = fmtScore(fusion.combined_score);
  var score = fusion.combined_score == null ? 0 : Math.max(-1, Math.min(1, fusion.combined_score));
  byId('signalBar').style.width = Math.round((score + 1) * 50) + '%';
  byId('oppFundScore').textContent = fmtScore(fundamentals.score);
  byId('oppTechScore').textContent = fmtScore(technicals.score);
  byId('oppOverseerVerdict').textContent = overseer.verdict || 'n/a';
  byId('oppGate').textContent = s.gate_blocker || 'unknown';
  byId('opportunityReason').textContent = s.gate_blocker || 'unknown';
  var riskState = s.risk_state || {};
  var riskKillSwitch = !!riskState.kill_switch;

  setNodeState('mt5', mt5Status === 'up' ? 'ok' : (mt5Status === 'down' ? 'fail' : 'wait'));
  byId('kvMt5').textContent = mt5Status === 'up' ? 'feed up' : (mt5Status === 'down' ? 'feed down' : 'unknown');

  var fundScore = fundamentals.score;
  var fundMissing = fundamentals.missing_inputs || [];
  var fundStale = fundamentals.stale_inputs || [];
  var fundState = (fundMissing.length === 0 && fundStale.length === 0 && fundScore != null) ? 'ok' : (fundScore == null ? 'wait' : 'fail');
  setNodeState('fundamental', fundState);
  byId('kvFundScore').textContent = fmtScore(fundScore);
  byId('kvFundStale').textContent = fundStale.length ? ('stale: ' + fundStale.join(', ')) : 'fresh';

  var techValid = !!technicals.is_valid;
  setNodeState('technical', techValid ? 'ok' : 'wait');
  byId('kvTechScore').textContent = fmtScore(technicals.score);
  var indicatorsObj = technicals.indicators || {};
  byId('kvTechAtr').textContent = indicatorsObj.atr == null ? 'n/a' : Number(indicatorsObj.atr).toFixed(2);

  setNodeState('fusion', fusion.entry_candidate ? 'ok' : 'wait');
  byId('kvFusionScore').textContent = fmtScore(fusion.combined_score);
  byId('kvFusionEntry').textContent = fusion.entry_candidate ? 'ENTRY' : 'WAIT';

  var overseerVerdict = overseer.verdict || 'n/a';
  setNodeState('overseer', overseerVerdict === 'approved' ? 'ok' : 'wait');
  byId('kvOverseerVerdict').textContent = overseerVerdict;
  byId('kvGateBlocker').textContent = s.gate_blocker || 'unknown';

  setNodeState('risk', riskKillSwitch ? 'fail' : 'ok');
  byId('kvDrawdown').textContent = drawdown == null ? 'n/a' : Number(drawdown).toFixed(2) + '%';
  byId('kvKillSwitch').textContent = riskKillSwitch ? 'ON' : 'off';

  var overrideOn = !!(byId('overrideBlocksToggle') && byId('overrideBlocksToggle').checked);
  byId('acceptBtn').disabled = !((fusion.entry_candidate && decision.decision_type === 'ENTER_LONG') || overrideOn);

  var comp = '';
  var components = fundamentals.components || [];
  for (var i = 0; i < components.length; i += 1) {
    var c = components[i];
    comp += '<tr><td>' + escapeHtml(c.input_id) + '</td><td>' + escapeHtml(c.series_id) + '</td><td>' + fmtNum(c.value, 2) + '</td><td>' + fmtNum(c.weight, 2) + '</td><td class="' + (c.stale ? 'stale' : '') + '">' + (c.stale ? 'STALE' : 'ok') + '</td><td>' + escapeHtml(c.note) + '</td></tr>';
  }
  byId('fundamentalsTable').innerHTML = '<table><thead><tr><th>Input</th><th>Series</th><th>Value</th><th>Weight</th><th>State</th><th>Note</th></tr></thead><tbody>' + (comp || '<tr><td colspan="6">no components</td></tr>') + '</tbody></table>';

  var technicalsHtml = '<table><thead><tr><th>Item</th><th>Value</th></tr></thead><tbody>';
  technicalsHtml += '<tr><td>score</td><td>' + fmtScore(technicals.score) + '</td></tr>';
  var indicators = technicals.indicators || {};
  for (var key in indicators) {
    if (Object.prototype.hasOwnProperty.call(indicators, key)) {
      technicalsHtml += '<tr><td>' + escapeHtml(key) + '</td><td>' + escapeHtml(JSON.stringify(indicators[key])) + '</td></tr>';
    }
  }
  var triggers = technicals.entry_triggers || {};
  for (var trigger in triggers) {
    if (Object.prototype.hasOwnProperty.call(triggers, trigger)) {
      technicalsHtml += '<tr><td>trigger: ' + escapeHtml(trigger) + '</td><td>' + (triggers[trigger] ? 'pass' : 'fail') + '</td></tr>';
    }
  }
  technicalsHtml += '</tbody></table>';
  byId('technicalsTable').innerHTML = technicalsHtml;

  var fusionHtml = '';
  var reasons = fusion.reasons || [];
  for (var j = 0; j < reasons.length; j += 1) {
    fusionHtml += '<li>' + escapeHtml(reasons[j]) + '</li>';
  }
  byId('fusionReasons').innerHTML = '<ul>' + fusionHtml + '</ul>';
  byId('overseerVerdict').textContent = overseer.verdict || overseer.decision || 'n/a';
  byId('gateBlockerDetail').textContent = s.gate_blocker || 'unknown';

  var logEl = byId('terminal');
  var logLines = (s.agent_logs || []).slice(-120);
  var logHtml = '';
  for (var k = 0; k < logLines.length; k += 1) {
    var line = String(logLines[k]);
    var cls = line.indexOf('ERROR') !== -1 ? 'line-error' : (line.indexOf('WARN') !== -1 ? 'line-warn' : 'line-info');
    logHtml += '<span class="' + cls + '">' + escapeHtml(logLines[k]) + '</span>';
  }
  logEl.innerHTML = logHtml;
  logEl.scrollTop = logEl.scrollHeight;
  byId('logCount').textContent = (s.agent_logs || []).length + ' lines';

  var bars = s.chart_bars || [];
  byId('chartBadges').innerHTML = bars.length >= 30
    ? '<span class="badge badge-green">LIVE BARS</span>'
    : '<span class="badge badge-neutral">WAITING FOR BARS</span>';
  var decisions = s.decisions || [];
  var decisionsHtml = '';
  for (var d = 0; d < decisions.length; d += 1) {
    var dec = decisions[d];
    decisionsHtml += '<span class="pill">' + escapeHtml(dec.as_of_utc) + ' ' + escapeHtml(dec.decision_type) + ' ' + fmtScore(dec.combined_score) + '</span>';
  }
  byId('latestDecisions').innerHTML = decisionsHtml || 'no decisions';
  var orders = (s.orders || []).slice(0, 3);
  var ordersText = [];
  for (var o = 0; o < orders.length; o += 1) {
    var ord = orders[o];
    var price = ord.price == null ? 'n/a' : String(ord.price);
    ordersText.push(ord.side + ' ' + ord.volume + ' @ ' + price);
  }
  byId('recentOrders').textContent = ordersText.length ? ordersText.join(' · ') : 'no orders';
  var positions = (s.positions || []).slice(0, 3);
  var positionsText = [];
  for (var p = 0; p < positions.length; p += 1) {
    var pos = positions[p];
    positionsText.push(pos.side + ' ' + pos.volume + ' open ' + pos.open_price + ' pnl ' + (pos.realized_pnl == null ? 'n/a' : pos.realized_pnl));
  }
  byId('recentPositions').textContent = positionsText.length ? positionsText.join(' · ') : 'no positions';
  var curve = s.equity_curve || [];
  var lastEq = curve.length ? curve[curve.length - 1].equity : s.equity;
  byId('equitySummary').textContent = 'equity ' + fmtNum(lastEq, 2) + ' dd ' + fmtNum(drawdown, 2) + '%';
  byId('chartFooterText').textContent = bars.length
    ? String(bars.length) + ' bars loaded - M15'
    : 'bars will appear when the bot writes them to the ledger';
  if (bars.length && bars[0].time) {
    byId('chartTimeRange').textContent = fmtTime(bars[0].time) + ' to ' + fmtTime(bars[bars.length - 1].time);
  } else {
    byId('chartTimeRange').textContent = '';
  }
  drawChart(bars, s);
}

function drawChart(bars, state) {
  var canvas = byId('priceChart');
  if (!canvas || !canvas.getContext) { return; }
  var ctx = canvas.getContext('2d');
  var width = canvas.clientWidth;
  var height = 260;
  canvas.width = Math.floor(width * 2);
  canvas.height = Math.floor(height * 2);
  ctx.scale(2, 2);
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = '#0a0a0a';
  ctx.fillRect(0, 0, width, height);
  if (!bars || bars.length < 2) {
    ctx.fillStyle = '#4b5563';
    ctx.font = '12px system-ui';
    ctx.fillText('No price history in ledger yet', 16, height / 2);
    return;
  }
  var pad = 6;
  var chartH = height - pad * 2;
  var hi = -Infinity;
  var lo = Infinity;
  for (var i = 0; i < bars.length; i += 1) {
    if (bars[i].high > hi) { hi = bars[i].high; }
    if (bars[i].low < lo) { lo = bars[i].low; }
  }
  var range = hi - lo || 1;
  var barW = Math.max(2, Math.floor((width - 20) / bars.length) - 1);
  for (var j = 0; j < bars.length; j += 1) {
    var b = bars[j];
    var x = 6 + j * (barW + 1);
    var yH = pad + (hi - b.high) * chartH / range;
    var yL = pad + (hi - b.low) * chartH / range;
    var yO = pad + (hi - b.open) * chartH / range;
    var yC = pad + (hi - b.close) * chartH / range;
    var up = b.close >= b.open;
    ctx.fillStyle = up ? '#22c55e' : '#ef4444';
    ctx.strokeStyle = up ? '#22c55e' : '#ef4444';
    ctx.beginPath();
    ctx.moveTo(x + barW / 2, yH);
    ctx.lineTo(x + barW / 2, yL);
    ctx.stroke();
    var top = Math.min(yO, yC);
    var bh = Math.max(1, Math.abs(yC - yO));
    ctx.fillRect(x, top, barW, bh);
  }
  var openPositions = state.open_positions || [];
  if (openPositions.length) {
    var pos = openPositions[0];
    var lines = [];
    if (pos.open_price != null) { lines.push([pos.open_price, '#fbbf24', 'Entry']); }
    if (pos.stop_loss != null) { lines.push([pos.stop_loss, '#ef4444', 'Stop']); }
    if (pos.take_profit != null) { lines.push([pos.take_profit, '#22c55e', 'Target']); }
    for (var m = 0; m < lines.length; m += 1) {
      var p = lines[m][0];
      var c = lines[m][1];
      var label = lines[m][2];
      var y = pad + (hi - p) * chartH / range;
      ctx.strokeStyle = c;
      ctx.setLineDash([5, 4]);
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(width, y);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = c;
      ctx.font = '10px system-ui';
      ctx.fillText(label, 4, y - 2);
    }
  }
}

var initialState = null;
try {
  initialState = JSON.parse(byId('appState').getAttribute('data-state'));
} catch (e) {
  byId('agentStepsNote').textContent = 'Could not load initial state: ' + e.message;
}
if (initialState && typeof updateState === 'function') {
  updateState({ v: 1, state: initialState });
}

function postJson(path, body, callback) {
  var req = new XMLHttpRequest();
  req.open('POST', path, true);
  req.setRequestHeader('Content-Type', 'application/json');
  req.onreadystatechange = function () {
    if (req.readyState !== 4) { return; }
    var text = req.responseText || '{}';
    if (req.status >= 200 && req.status < 300) {
      try { callback(null, JSON.parse(text)); } catch (e) { callback(null, {}); }
    } else {
      var message = 'request failed';
      try { var parsed = JSON.parse(text); message = parsed.error || message; } catch (e) {}
      callback(new Error(message), null);
    }
  };
  req.send(JSON.stringify(body || {}));
}

function getJson(path, callback) {
  var req = new XMLHttpRequest();
  req.open('GET', path, true);
  req.onreadystatechange = function () {
    if (req.readyState !== 4) { return; }
    var text = req.responseText || '{}';
    if (req.status >= 200 && req.status < 300) {
      try { callback(null, JSON.parse(text)); } catch (e) { callback(null, {}); }
    } else {
      callback(new Error(req.statusText || 'request failed'), null);
    }
  };
  req.send();
}

function closeDialog(id) { byId(id).close(); }
function setButtonState(id, label, disabled) {
  var el = byId(id);
  if (el) { el.textContent = label; el.disabled = disabled; }
}

byId('startAgentBtn').addEventListener('click', function () {
  setButtonState('startAgentBtn', 'Starting...', true);
  postJson('/api/agent/start', {}, function (err, result) {
    if (err) {
      setButtonState('startAgentBtn', 'Start Agent', false);
      alert(err.message);
      return;
    }
    setButtonState('startAgentBtn', 'Running', true);
    toast('Agent started.');
  });
});
byId('stopAgentBtn').addEventListener('click', function () {
  setButtonState('stopAgentBtn', 'Stopping...', true);
  postJson('/api/agent/stop', {}, function (err) {
    if (err) {
      setButtonState('stopAgentBtn', 'Stop Agent', false);
      alert(err.message);
      return;
    }
    setButtonState('stopAgentBtn', 'Stop Agent', false);
    toast('Agent stopped.');
  });
});
byId('overrideBlocksToggle').addEventListener('change', function (e) {
  var target = e.target || e.srcElement;
  var on = !!target.checked;
  byId('overrideBanner').style.display = on ? 'block' : 'none';
  byId('overrideToggleLabel').className = 'override-toggle' + (on ? ' on' : '');
  if (on) {
    toast('DEMO OVERRIDE active - gates bypassed.');
  } else {
    toast('DEMO OVERRIDE off.');
  }
  state = JSON.parse(byId('appState').getAttribute('data-state'));
  updateState({ v: 1, state: state });
});
byId('profileSelect').addEventListener('change', function (e) {
  var target = e.target || e.srcElement;
  postJson('/api/agent/profile', { profile: target.value }, function (err, result) {
    if (err) { alert(err.message); return; }
    if (result && result.profile) { renderProfileSpec(result.profile); }
    toast('Profile: ' + target.value);
  });
});
byId('reviewEvidenceBtn').addEventListener('click', function () { byId('evidenceDrawer').showModal(); });
byId('closeEvidenceDrawerBtn').addEventListener('click', function () { closeDialog('evidenceDrawer'); });
byId('declineBtn').addEventListener('click', function () { byId('declineModal').showModal(); });
byId('closeDeclineModalBtn').addEventListener('click', function () { closeDialog('declineModal'); });
byId('cancelDeclineBtn').addEventListener('click', function () { closeDialog('declineModal'); });
byId('shutdownDashboardBtn').addEventListener('click', function () { byId('shutdownModal').showModal(); });
byId('closeShutdownModalBtn').addEventListener('click', function () { closeDialog('shutdownModal'); });
byId('cancelShutdownBtn').addEventListener('click', function () { closeDialog('shutdownModal'); });
byId('confirmShutdownBtn').addEventListener('click', function () {
  postJson('/api/server/shutdown', { confirm: 'SHUT DASHBOARD' }, function () {
    document.body.innerHTML = '<div class="panel" style="padding:40px;text-align:center;margin:48px auto;max-width:520px"><h2>Dashboard is shutting down</h2><p class="muted">You can close this tab. Reopen the dashboard with the Gold Bot Dashboard shortcut on your Desktop.</p></div>';
  });
});
byId('confirmDeclineBtn').addEventListener('click', function () {
  var reason = byId('declineReasonInput').value;
  reason = reason ? reason.replace(/^\s+|\s+$/g, '') : '';
  if (!reason) { alert('Reason required'); return; }
  postJson('/api/agent/decline', { reason: reason }, function (err) {
    if (err) { alert(err.message); return; }
    closeDialog('declineModal');
    byId('declineReasonInput').value = '';
    toast('Decline logged.');
  });
});
byId('acceptBtn').addEventListener('click', function () { byId('executeModal').showModal(); });
byId('closeExecuteModalBtn').addEventListener('click', function () { closeDialog('executeModal'); });
byId('cancelExecuteBtn').addEventListener('click', function () { closeDialog('executeModal'); });
byId('executePhraseInput').addEventListener('input', function (e) {
  var target = e.target || e.srcElement;
  byId('confirmExecuteBtn').disabled = String(target.value).replace(/^\s+|\s+$/g, '') !== 'SEND DEMO ORDER';
});
byId('confirmExecuteBtn').addEventListener('click', function () {
  var phrase = String(byId('executePhraseInput').value).replace(/^\s+|\s+$/g, '');
  if (phrase !== 'SEND DEMO ORDER') { alert('Phrase must be SEND DEMO ORDER'); return; }
  byId('confirmExecuteBtn').disabled = true;
  byId('confirmExecuteBtn').textContent = 'Authorizing...';
  postJson('/api/agent/execute', { confirm: phrase, override: !!byId('overrideBlocksToggle').checked }, function (err, result) {
    if (err) {
      setButtonState('acceptBtn', 'Accept & Execute', false);
      setButtonState('confirmExecuteBtn', 'Authorize & Execute', false);
      alert(err.message);
      return;
    }
    if (result && result.sent) {
      setButtonState('acceptBtn', 'Order Sent', true);
      toast('Demo order placed. It may take a few seconds to appear in the Trade tab.');
    } else {
      setButtonState('acceptBtn', 'Order Failed', false);
      toast('Demo order failed. Check the terminal panel for the rejection reason.');
    }
    closeDialog('executeModal');
    byId('executePhraseInput').value = '';
    byId('acceptBtn').disabled = true;
    updateState({ v: 1, state: JSON.parse(byId('appState').getAttribute('data-state')) });
  });
});
byId('copyLogsBtn').addEventListener('click', function () {
  var text = byId('terminal').innerText || '';
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text);
    return;
  }
  var area = document.createElement('textarea');
  area.value = text;
  document.body.appendChild(area);
  area.select();
  try { document.execCommand('copy'); } catch (e) {}
  document.body.removeChild(area);
});

function connectStream() {
  if (typeof EventSource === 'undefined') {
    fallbackPoll();
    return;
  }
  var source = new EventSource('/api/stream');
  source.addEventListener('state', function (ev) {
    var data;
    try { data = JSON.parse(ev.data); } catch (e) { source.close(); fallbackPoll(); return; }
    updateState(data);
  });
  source.onerror = function () {
    source.close();
    fallbackPoll();
  };
}
function fallbackPoll() {
  setInterval(function () {
    getJson('/api/state', function (err, data) {
      if (err) { return; }
      updateState(data);
    });
  }, REFRESH_MS);
}
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
.toast{position:fixed;bottom:16px;right:16px;background:#052e16;border:1px solid #16a34a;border-radius:10px;padding:10px 14px;color:#bbf7d0;font-weight:700;display:none;z-index:10;box-shadow:0 0 20px rgba(34,197,94,.35)}
.toast.show{display:block}
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
.controls{display:flex;gap:8px;align-items:center}
.override-toggle{display:inline-flex;align-items:center;gap:8px;padding:6px 12px;border:1px solid var(--border);border-radius:8px;background:#0a0a0a;color:var(--muted);font-weight:700;font-size:11px;cursor:pointer;user-select:none;white-space:nowrap}
.override-toggle input{accent-color:#f59e0b;cursor:pointer;transform:scale(1.2)}
.override-toggle.on{color:#fca5a5;border-color:#7f1d1d;box-shadow:0 0 14px rgba(239,68,68,.45)}
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
.node-ok{border-color:#166534;box-shadow:0 0 14px rgba(34,197,94,.22)}
.node-fail{border-color:#7f1d1d;box-shadow:0 0 14px rgba(239,68,68,.22)}
.node-wait{border-color:#713f12;box-shadow:0 0 14px rgba(251,191,36,.22)}
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
    <div class="chip chip-{'red' if kill_switch else 'green'}" id="riskChip"><span class="chip-label">RISK</span><span class="chip-value" id="riskChipValue">DD {_e(drawdown)} KILL {'ON' if kill_switch else 'off'}</span></div>
  </div>
  <div class="controls">
    <button id="startAgentBtn" class="btn btn-primary" type="button">{'Running' if running else 'Start Agent'}</button>
    <button id="stopAgentBtn" class="btn btn-danger" type="button" {'disabled' if not running else ''}>Stop Agent</button>
    <button id="shutdownDashboardBtn" class="btn btn-danger btn-shutdown" type="button">Shutdown Dashboard</button>
    <label class="override-toggle" id="overrideToggleLabel" title="TESTING ONLY: bypass macro/fusion gates and allow a bare 0.01-lot demo order">
      <input type="checkbox" id="overrideBlocksToggle" />
      <span>DEMO OVERRIDE</span>
    </label>
  </div>
</header>

<div id="overrideBanner" class="banner banner-danger" style="display:none">DEMO OVERRIDE ACTIVE - macro/fusion gates are bypassed. This will place a bare 0.01 XAUUSD demo order if you click Accept &amp; Execute. For testing only.</div>

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
      {_card("fundamental", "Fundamental Agent", "What to consider", '<div class="kv"><span>Score</span><span id="kvFundScore">…</span></div><div class="kv"><span>Stale</span><span id="kvFundStale">…</span></div>', "cyan")}
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

<div id="toast" class="toast"></div>

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
<meta name="description" content="Gold bot agentic dashboard - read-only observer with guarded demo execution.">
<title>Gold Bot - Agentic Desk</title>
<style>{_CSS}</style>
</head>
<body>
{body}
<div id="appState" data-state="{_e(state_json)}" hidden></div>
</body>
</html>"""
