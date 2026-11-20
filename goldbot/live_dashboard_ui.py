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


_JS_HELPERS = r"""
var byId = function (id) { return document.getElementById(id); };
var escapeHtml = function (v) {
  return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
    return ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'})[c];
  });
};
var toast = function (message) {
  var el = byId('toast');
  if (!el) { return; }
  el.textContent = message;
  el.className = 'toast show';
  window.clearTimeout(el._timer);
  el._timer = window.setTimeout(function () { el.className = 'toast'; }, 4000);
};
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
"""

_FEEDBACK_BODY = """
<section class="panel feedback-section" id="feedbackSection" aria-label="Feedback and issues">
  <div class="feedback-head">
    <div>
      <h2 class="panel-title">Feedback &amp; Issues</h2>
      <p class="panel-subtitle">Bugs, improvements, features, chores, questions. File it once, then copy it as Markdown and hand it to the agent.</p>
    </div>
    <div class="feedback-counts" id="feedbackCounts"><span class="muted">Loading…</span></div>
  </div>
  <div class="feedback-grid">
    <form id="feedbackForm" class="feedback-form" autocomplete="off">
      <div class="feedback-form-head">
        <h3 class="feedback-form-title" id="feedbackFormTitle">File feedback</h3>
        <button class="btn btn-ghost btn-small" id="feedbackCancelEditBtn" type="button" hidden>Cancel edit</button>
      </div>
      <input type="hidden" id="feedbackEditId" value="">
      <div class="feedback-row-2">
        <label class="field"><span>Type</span>
          <select id="feedbackType">
            <option value="bug">Bug</option>
            <option value="improvement">Improvement</option>
            <option value="feature">Feature request</option>
            <option value="task">Task / chore</option>
            <option value="question">Question</option>
          </select>
        </label>
        <label class="field"><span>Severity</span>
          <select id="feedbackSeverity">
            <option value="critical">Critical</option>
            <option value="high">High</option>
            <option value="medium" selected>Medium</option>
            <option value="low">Low</option>
          </select>
        </label>
      </div>
      <label class="field"><span>Title</span><input id="feedbackTitle" type="text" maxlength="300" placeholder="Short, specific summary" required></label>
      <label class="field"><span>Description</span><textarea id="feedbackDescription" rows="3" placeholder="What's wrong / what should change, and why it matters"></textarea></label>
      <label class="field"><span>Steps to reproduce</span><textarea id="feedbackSteps" rows="3" placeholder="1. …&#10;2. …&#10;3. …"></textarea></label>
      <div class="feedback-row-2">
        <label class="field"><span>Expected</span><textarea id="feedbackExpected" rows="2" placeholder="What should happen"></textarea></label>
        <label class="field"><span>Actual</span><textarea id="feedbackActual" rows="2" placeholder="What happens instead"></textarea></label>
      </div>
      <label class="field"><span>Recommended fix / change</span><textarea id="feedbackRecommended" rows="2" placeholder="Your proposed fix, or leave blank if unsure"></textarea></label>
      <div class="field">
        <span>Screenshots</span>
        <div class="feedback-dropzone" id="feedbackDropzone" tabindex="0" role="button" aria-label="Paste or drop screenshots here">
          <p class="muted">Paste (Ctrl+V), drop, or</p>
          <label class="btn btn-secondary btn-small feedback-browse">Browse files<input id="feedbackFileInput" type="file" accept="image/png,image/jpeg,image/gif,image/webp" multiple hidden></label>
        </div>
        <div class="feedback-attachments" id="feedbackAttachments"></div>
      </div>
      <div class="feedback-form-actions">
        <button class="btn btn-primary" id="feedbackSubmitBtn" type="submit">Submit feedback</button>
        <button class="btn btn-ghost" id="feedbackResetBtn" type="button">Clear</button>
      </div>
    </form>
    <div class="feedback-list-wrap">
      <div class="feedback-list-toolbar">
        <h3 class="feedback-form-title">Submitted</h3>
        <div class="feedback-list-controls">
          <label class="field field-inline"><span>Sort</span>
            <select id="feedbackSort">
              <option value="created">Newest</option>
              <option value="severity">Severity</option>
              <option value="status">Status</option>
              <option value="type">Type</option>
              <option value="title">Title A–Z</option>
              <option value="updated">Recently updated</option>
            </select>
          </label>
          <button class="btn btn-ghost btn-small" id="feedbackSortDirBtn" type="button" title="Toggle sort direction">↓</button>
          <label class="field field-inline"><span>Status</span>
            <select id="feedbackStatusFilter">
              <option value="">All</option>
              <option value="new">New</option>
              <option value="accepted">Triaged · Accepted (queued)</option>
              <option value="needs_info">Needs information</option>
              <option value="duplicate">Duplicate</option>
              <option value="wont_fix">Won't fix</option>
              <option value="not_a_bug">Not a bug</option>
              <option value="done">Done</option>
            </select>
          </label>
          <label class="field field-inline"><span>Type</span>
            <select id="feedbackTypeFilter">
              <option value="">All</option>
              <option value="bug">Bug</option>
              <option value="improvement">Improvement</option>
              <option value="feature">Feature request</option>
              <option value="task">Task / chore</option>
              <option value="question">Question</option>
            </select>
          </label>
        </div>
      </div>
      <div id="feedbackList" class="feedback-list"><p class="muted">Loading…</p></div>
    </div>
  </div>
</section>
"""

_APP_JS = _JS_HELPERS + r"""
var REFRESH_MS = 2000;
var CONN_HEALTH_MS = Math.max(8000, REFRESH_MS * 3);
window._conn = {
  lastOk: null,
  lost: false,
  stream: null,
  pollTimer: null,
  polling: false,
  retryTimer: null,
  retryDelay: 2000,
  shutdown: false
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
function setStep(id, state) {
  var el = byId(id);
  if (el) { el.className = 'step step-' + state; }
}

var pendingExitTicket = null;
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
  var leverage = '';
  var specRows = spec.spec_rows || [];
  for (var j = 0; j < specRows.length; j += 1) {
    if (specRows[j].label === 'Bot leverage cap') { leverage = String(specRows[j].value || ''); break; }
  }
  var summary = byId('profileSummary');
  if (summary) {
    summary.textContent = spec.key
      ? (spec.key + (spec.high_risk_override ? ' \u00b7 HIGH RISK \u00b7 not promotion-eligible' : ' \u00b7 promotion-eligible'))
      : '';
  }
  var stage = byId('profileStage');
  if (stage) {
    stage.textContent = spec.promotion_eligible ? 'STANDARD' : 'DEMO';
    stage.className = 'mission-stage-badge ' + (spec.promotion_eligible ? 'profile-stage-green' : 'profile-stage-demo');
  }
  var chip = byId('profileRiskChip');
  if (chip) { chip.textContent = leverage ? ('\u2212' + leverage) : '\u2014'; }
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

function renderNotifications(items) {
  var list = byId('notificationsList');
  var badge = byId('bellBadge');
  var summary = byId('bellSummary');
  var bell = byId('bellBtn');
  items = items || [];
  // Only items that need consistent reconciliation are shown; anything
  // proven complete disappears from the bell.
  var pending = [];
  var i;
  for (i = 0; i < items.length; i += 1) {
    if (!items[i].completed) { pending.push(items[i]); }
  }
  var buckets = { critical: [], warning: [], info: [] };
  for (i = 0; i < pending.length; i += 1) {
    var item = pending[i];
    var id = String(item.id || '');
    var title = String(item.title || '');
    var severity = String(item.severity || 'info').toLowerCase();
    var bucket = 'info';
    if (severity === 'critical' || id.indexOf('kill') !== -1 || /halt/i.test(title) || /kill.?switch/i.test(title)) {
      bucket = 'critical';
    } else if (severity === 'warning' || id === 'macro_data' || id === 'human.signoff' || id.indexOf('stale:') === 0 || id.indexOf('missing:') === 0 || /macro|stale|sign.?off/i.test(title)) {
      bucket = 'warning';
    }
    buckets[bucket].push(item);
  }
  if (badge) {
    badge.textContent = String(buckets.critical.length + buckets.warning.length + buckets.info.length);
    badge.style.display = pending.length ? 'inline-flex' : 'none';
  }
  if (bell) {
    bell.className = pending.length ? 'bell-btn bell-alert' : 'bell-btn bell-clear';
    bell.setAttribute('aria-label', 'Notifications, ' + pending.length + ' pending');
  }
  if (summary) {
    summary.textContent = pending.length ? (String(pending.length) + ' pending') : 'all clear';
  }
  if (!list) { return; }

  // Bell shake once when a new pending item id appears
  var pendingIds = {};
  for (i = 0; i < pending.length; i += 1) { pendingIds[String(pending[i].id || i)] = true; }
  var last = window._lastPendingIds || {};
  var isNew = false;
  for (var key in pendingIds) {
    if (pendingIds.hasOwnProperty(key) && !last[key]) { isNew = true; }
  }
  window._lastPendingIds = pendingIds;
  if (bell && isNew) {
    bell.classList.remove('bell-shake');
    void bell.offsetWidth; // force reflow to restart animation
    bell.classList.add('bell-shake');
  }

  if (!pending.length) {
    list.innerHTML = '<li class="muted notify-empty">All clear.</li>';
    return;
  }

  var html = '';
  var groupSpec = [
    { key: 'critical', label: 'CRITICAL' },
    { key: 'warning', label: 'WARNING' },
    { key: 'info', label: 'INFO' },
  ];
  for (var g = 0; g < groupSpec.length; g += 1) {
    var group = buckets[groupSpec[g].key];
    if (!group.length) { continue; }
    html += '<li class="notify-group-header ' + groupSpec[g].key + '">' + groupSpec[g].label + '</li>';
    for (var j = 0; j < group.length; j += 1) {
      html += notifyRow(group[j]);
    }
  }
  list.innerHTML = html;
}

function notifyRow(item) {
  var actions = '';
  if (item.link) {
    actions += '<a href="' + escapeHtml(item.link) + '" target="_blank" rel="noopener noreferrer">' + escapeHtml(item.link_text || 'Open') + '</a>';
  }
  if (item.action === 'refresh') {
    actions += '<button class="notify-action refresh-btn" data-id="' + escapeHtml(item.id || '') + '" type="button">Refresh data</button>';
  } else if (item.action === 'reconfirm') {
    actions += '<button class="notify-action reconfirm-btn" data-id="' + escapeHtml(item.id || '') + '" type="button">Reconfirm</button>';
  }
  return '<li class="notify ' + escapeHtml(item.severity || 'info') + '">'
    + '<div class="notify-body">'
    + '<div class="notify-title-row"><strong>' + escapeHtml(item.title || '') + '</strong></div>'
    + (item.note ? '<p>' + escapeHtml(item.note) + '</p>' : '')
    + '</div>'
    + (actions ? '<div class="notify-actions">' + actions + '</div>' : '')
    + '</li>';
}

(function wireNotificationBell() {
  var bellBtn = byId('bellBtn');
  var dropdown = byId('notificationsDropdown');
  var wrap = byId('bellWrap');
  if (!bellBtn || !dropdown || !wrap) { return; }
  function closeDropdown() {
    dropdown.hidden = true;
    bellBtn.setAttribute('aria-expanded', 'false');
  }
  bellBtn.addEventListener('click', function (ev) {
    if (ev) { ev.stopPropagation(); }
    dropdown.hidden = !dropdown.hidden;
    bellBtn.setAttribute('aria-expanded', dropdown.hidden ? 'false' : 'true');
  });
  dropdown.addEventListener('click', function (ev) {
    if (ev) { ev.stopPropagation(); }
  });
  document.addEventListener('click', function () { closeDropdown(); });
  document.addEventListener('keydown', function (ev) {
    if (ev && (ev.key === 'Escape' || ev.key === 'Esc')) { closeDropdown(); }
  });
  var list = byId('notificationsList');
  if (list) {
    list.addEventListener('click', function (ev) {
      var target = ev.target || ev.srcElement;
      var className = target && target.className ? String(target.className) : '';
      var refresh = className.indexOf('refresh-btn') !== -1;
      var reconfirm = className.indexOf('reconfirm-btn') !== -1;
      if (!refresh && !reconfirm) { return; }
      target.disabled = true;
      var label = target.textContent;
      target.textContent = refresh ? 'Refreshing...' : 'Reconfirming...';
      var endpoint = refresh ? '/api/refresh-macro' : '/api/wgc/reconfirm';
      postJson(endpoint, {}, function (err, result) {
        if (err) {
          target.disabled = false;
          target.textContent = label;
          toast((refresh ? 'Macro refresh failed: ' : 'WGC reconfirm failed: ') + err.message);
          return;
        }
        toast(
          (refresh ? 'Macro refresh' : 'WGC reconfirm')
          + (result && result.ok ? ' completed.' : ' finished with errors.')
        );
      });
    });
  }
})();

function fmtSigned(n, digits) {
  digits = digits == null ? 3 : digits;
  if (n == null || isNaN(Number(n))) { return 'n/a'; }
  var v = Number(n);
  return (v >= 0 ? '+' : '') + v.toFixed(digits);
}

function renderExecAnatomy(s) {
  var fundamentals = s.fundamentals || {};
  var technicals = s.technicals || {};
  var fusion = s.fusion || {};
  var es = s.exec_quality || {};

  var fund = fundamentals.score;
  var tech = technicals.score;
  var fundContrib = (fund == null ? null : 0.55 * fund);
  var techContrib = (tech == null ? null : 0.40 * tech);

  var setText = function (id, text, cls) {
    var el = byId(id);
    if (!el) { return; }
    el.textContent = text;
    el.className = 'anatomy-value' + (cls ? ' ' + cls : '');
  };
  var setBar = function (id, weight, contrib) {
    var el = byId(id);
    if (!el) { return; }
    if (weight == null || contrib == null) { el.style.width = '0%'; el.className = 'anatomy-fill'; return; }
    var pct = Math.min(1, Math.abs(contrib) / weight) * 100;
    el.style.width = pct.toFixed(1) + '%';
    el.className = 'anatomy-fill' + (contrib >= 0 ? '' : ' warn');
  };
  var setCenterFill = function (id, combined) {
    var el = byId(id);
    if (!el) { return; }
    if (combined == null) { el.style.cssText = 'left:50%;width:0%'; return; }
    var half = Math.min(1, Math.abs(combined)) * 50;
    if (combined >= 0) {
      el.style.cssText = 'left:50%;width:' + half.toFixed(1) + '%;background:#22c55e';
    } else {
      el.style.cssText = 'left:' + (50 - half).toFixed(1) + '%;width:' + half.toFixed(1) + '%;background:#ef4444';
    }
  };
  var signCls = function (v) { return v == null ? '' : (v >= 0 ? 'ok' : 'warn'); };

  var fundWord = fund == null ? '\u2014' : (fund >= 0.3 ? 'SUPPORTIVE' : (fund <= -0.3 ? 'HEADWIND' : 'NEUTRAL'));
  var techWord = tech == null ? '\u2014' : (tech >= 0.3 ? 'FIRING' : (tech <= -0.3 ? 'FADING' : 'QUIET'));
  var fundTone = fund == null ? 'neutral' : (fund >= 0.3 ? 'ok' : (fund <= -0.3 ? 'bad' : 'neutral'));
  var techTone = tech == null ? 'neutral' : (tech >= 0.3 ? 'ok' : (tech <= -0.3 ? 'bad' : 'neutral'));
  var decisionType = (s.latest_decision || {}).decision_type || '';
  var callText = decisionType === 'ENTER_LONG' ? 'ENTER LONG'
    : decisionType ? String(decisionType).replace(/_/g, ' ') : '\u2014';
  var callCls = decisionType === 'ENTER_LONG' ? 'ok' : (decisionType ? 'warn' : 'neutral');
  var overseerDecision = (s.overseer || {}).decision || (s.overseer || {}).verdict || '\u2014';
  var fw = byId('anFundWord'); if (fw) { fw.textContent = fundWord; fw.className = 'anat-word ' + fundTone; }
  var tw = byId('anTechWord'); if (tw) { tw.textContent = techWord; tw.className = 'anat-word ' + techTone; }
  var cw = byId('anCallWord'); if (cw) { cw.textContent = callText; cw.className = 'anat-word ' + callCls; }
  var fEl = byId('anFund'); if (fEl) { fEl.textContent = fundContrib == null ? '\u2014' : ('= ' + fmtSigned(fundContrib, 3)); fEl.className = 'anat-num ' + signCls(fundContrib); }
  var tEl = byId('anTech'); if (tEl) { tEl.textContent = techContrib == null ? '\u2014' : ('= ' + fmtSigned(techContrib, 3)); tEl.className = 'anat-num ' + signCls(techContrib); }
  var cEl = byId('anCombined'); if (cEl) { cEl.textContent = fmtSigned(fusion.combined_score, 3) + ' vs +0.200'; cEl.className = 'anat-num ' + signCls(fusion.combined_score); }
  var csEl = byId('anCallSub'); if (csEl) { csEl.textContent = 'overseer ' + overseerDecision + ' \u00b7 needs \u2265 +0.200'; }
  setBar('anFundBar', 0.55, fundContrib);
  setBar('anTechBar', 0.40, techContrib);
  setCenterFill('anCombinedFill', fusion.combined_score);
  var blocker = s.gate_blocker || (fusion.reasons && fusion.reasons.length ? fusion.reasons[fusion.reasons.length - 1] : '');
  var isBlock = !!blocker && blocker !== 'none' && blocker !== 'unknown';
  setText('anBlocker', isBlock ? blocker : 'clear', isBlock ? 'warn' : 'ok');
  var blockWrap = byId('anBlockerWrap');
  if (blockWrap) {
    blockWrap.className = 'anatomy-line anatomy-blocker' + (isBlock ? ' block' : '');
  }
  var panel = byId('anatomyPanel');
  if (panel) {
    panel.className = 'anatomy' + (decisionType === 'ENTER_LONG' ? ' call-enter' : (isBlock ? ' call-block' : ''));
  }

  var stamp = byId('anatomyStamp');
  if (stamp) {
    var last = s.latest_decision || {};
    stamp.textContent = last.as_of_utc ? 'as of ' + String(last.as_of_utc).slice(11, 16) + 'Z' : '';
  }

  var card = byId('managerCard');
  if (card) {
    var decisionType = (s.latest_decision || {}).decision_type || 'unknown';
    var running = !!((s.agent || {}).running);
    var state = decisionType === 'ENTER_LONG' ? 'state-enter'
      : es.halt_flag ? 'state-halt'
      : decisionType === 'NO_TRADE' ? 'state-notrade'
      : 'state-idle';
    var upstreamState = (s.upstream && s.upstream.state) || 'idle';
    card.className = 'manager-card ' + state + (running ? ' running' : '') + ' upstream-' + upstreamState;
    var combined = fusion.combined_score == null ? 0 : fusion.combined_score;
    var magnitude = Math.min(1, Math.abs(combined));
    var duration = Math.max(8, Math.round(26 - magnitude * 18));
    var orbWrap = byId('managerOrbWrap');
    if (orbWrap) {
      orbWrap.style.setProperty('--orb-spin-duration', duration + 's');
      orbWrap.style.setProperty('--orb-spin-dir', combined >= 0 ? 'normal' : 'reverse');
    }
  }

  var nodePanel = document.querySelector('.node-panel');
  if (nodePanel) {
    var mt5 = (s.mt5 && s.mt5.status) || 'unknown';
    nodePanel.className = mt5 === 'up' ? 'panel node-panel' : 'panel node-panel node-panel-offline';
  }

  var fusionNode = byId('fusion');
  if (fusionNode) {
    fusionNode.classList.toggle('node-conflict', !!fusion.conflict);
  }
}

function renderExecQuality(s) {
  var q = s.exec_quality || {};
  var setText = function (id, text, cls) {
    var el = byId(id);
    if (!el) { return; }
    el.textContent = text;
    el.className = 'eq-value' + (cls ? ' ' + cls : '');
  };

  // Gates
  if (q.blackout_active && q.active_event) {
    setText('eqBlackout', 'BLOCKED ' + String(q.active_event.label || ''), 'warn');
  } else {
    setText('eqBlackout', 'clear', 'ok');
  }

  if (q.spread && q.spread.samples > 0) {
    var over = q.tick_backtest && q.tick_backtest.bars_over_limit_pct > 0;
    var spreadText = 'p50=' + Number(q.spread.p50).toFixed(0) + ' p99=' + Number(q.spread.p99).toFixed(0) + ' / lim 100';
    setText('eqSpread', spreadText, (q.spread.p99 > 60 || over) ? 'warn' : 'ok');
  } else if (q.tick_backtest && q.tick_backtest.bars_measured) {
    setText('eqSpread', 'open p50=' + Number(q.tick_backtest.spread_at_open_p50).toFixed(0) + ' p99=' + Number(q.tick_backtest.spread_at_open_p99).toFixed(0), 'ok');
  } else {
    setText('eqSpread', 'no samples yet', 'muted');
  }

  if (q.halt_flag) {
    setText('eqHalt', 'ARMED (bot must not trade)', 'warn');
  } else {
    setText('eqHalt', 'clear', 'ok');
  }
  var banner = byId('haltBanner');
  if (banner) { banner.className = 'halt-banner' + (q.halt_flag ? ' armed' : ''); }

  // Vitals
  var mt5Status = (s.mt5 && s.mt5.status) || 'unknown';
  setText('eqMt5', String(mt5Status).toUpperCase(), mt5Status === 'up' ? 'ok' : (mt5Status === 'down' ? 'warn' : 'muted'));

  var lastAge = s.last_activity_age_seconds;
  var lastText = lastAge == null ? 'n/a'
    : (lastAge < 60 ? Math.floor(lastAge) + 's ago'
    : lastAge < 3600 ? Math.floor(lastAge / 60) + 'm ago'
    : (lastAge / 3600).toFixed(1) + 'h ago');
  setText('eqLastDecision', lastText, lastAge != null && lastAge < 900 ? 'ok' : 'warn');

  if (q.next_event) {
    setText('eqNextEvent', String(q.next_event.label || '') + ' · ' + String(Math.max(0, Math.round(q.minutes_until_blackout || 0))) + 'm', 'muted');
  } else if (q.minutes_until_blackout != null) {
    setText('eqNextEvent', 'in ' + Math.max(0, Math.round(q.minutes_until_blackout)) + 'm', 'muted');
  } else {
    setText('eqNextEvent', 'none in 30d', 'muted');
  }

  // Evidence
  if (q.news_stress && q.news_stress.events > 0) {
    setText('eqNewsStress', q.news_stress.stop_hits + '/' + q.news_stress.events + ' blown (' + q.news_stress.stop_hit_rate_pct + '%)', 'warn');
  } else if (q.news_stress) {
    setText('eqNewsStress', q.news_stress.events + ' events checked', 'ok');
  } else {
    setText('eqNewsStress', 'no report', 'muted');
  }

  if (q.tick_backtest && q.tick_backtest.bars_measured) {
    var tb = q.tick_backtest;
    setText('eqTick', tb.bars_measured + ' bars · p50=' + Number(tb.spread_at_open_p50).toFixed(0) + ' p99=' + Number(tb.spread_at_open_p99).toFixed(0), '');
  } else {
    setText('eqTick', 'no tick check', 'muted');
  }

  setText('eqExits', String(q.exits == null ? 0 : q.exits), q.exits && q.exits > 0 ? 'ok' : 'muted');

  var h4pct = q.h4_trend_pct;
  var h4dir = q.h4_trend;
  var h4Text = h4pct == null ? 'n/a' : ((h4dir === 'up' ? '+' : '') + Number(h4pct).toFixed(2) + '%');
  setText('eqH4Regime', h4Text, h4pct == null ? 'muted' : (h4dir === 'up' ? 'ok' : 'warn'));

  var acct = q.account_summary || {};
  setText('eqDemoTrades', String(acct.closed_trades == null ? 0 : acct.closed_trades) + ' (' + String(acct.wins == null ? 0 : acct.wins) + 'W)', acct.closed_trades ? (acct.total_pnl >= 0 ? 'ok' : 'warn') : 'muted');
  setText('eqDemoPnl', (acct.total_pnl == null ? 'n/a' : (acct.total_pnl >= 0 ? '+' : '') + Number(acct.total_pnl).toFixed(2)), acct.total_pnl == null ? 'muted' : (acct.total_pnl >= 0 ? 'ok' : 'warn'));
}

function renderLiveTrades(state) {
  var positions = state.open_positions || [];
  var snapshot = state.latest_snapshot || {};
  var market = snapshot.market || {};
  var tick = market.symbol_tick || {};
  var chartBars = state.chart_bars || [];
  var lastBar = chartBars.length ? chartBars[chartBars.length - 1] : null;
  var barPrice = lastBar ? Number(lastBar.close) : null;
  var bid = tick.bid != null ? Number(tick.bid) : null;
  var price = (bid != null && !isNaN(bid)) ? bid : (barPrice != null && !isNaN(barPrice) ? barPrice : null);
  var list = byId('liveTradesList');
  var count = byId('liveTradesCount');
  var card = byId('liveTradesCard');
  if (card) { card.className = positions.length ? 'panel live-trades-card positions-open' : 'panel live-trades-card'; }
  if (!list || !count) { return; }
  count.textContent = String(positions.length) + ' open';
  if (!positions.length) {
    list.innerHTML = '<p class="muted">No open positions.</p>';
    return;
  }
  var html = '';
  for (var i = 0; i < positions.length; i += 1) {
    var p = positions[i];
    var side = p.side || 'LONG';
    var direction = side === 'LONG' ? 1 : -1;
    var volume = Number(p.volume || 0);
    var entry = Number(p.open_price || 0);
    var stop = p.stop_loss == null ? null : Number(p.stop_loss);
    var target = p.take_profit == null ? null : Number(p.take_profit);
    var pnl = null;
    if (price != null && !isNaN(price) && !isNaN(entry)) {
      pnl = (price - entry) * volume * 100.0 * direction;
    }
    var rowClass = pnl == null ? '' : (pnl >= 0 ? ' win' : ' loss');
    var pnlClass = pnl == null ? '' : (pnl >= 0 ? 'pnl-positive' : 'pnl-negative');
    var pnlText = pnl == null ? 'n/a' : (pnl >= 0 ? '+' : '') + pnl.toFixed(2);
    var ticket = p.broker_position_ticket != null ? p.broker_position_ticket : '';
    html += '<div class="trade-row' + rowClass + '">'
      + '<div>' + escapeHtml(side) + '</div>'
      + '<div>' + volume.toFixed(2) + ' @ ' + fmtNum(entry, 2) + '</div>'
      + '<div>' + (price != null ? fmtNum(price, 2) : 'n/a') + '</div>'
      + '<div class="' + pnlClass + '">' + escapeHtml(pnlText) + '</div>'
      + '<div>SL ' + (stop == null ? 'n/a' : Number(stop).toFixed(2)) + '</div>'
      + '<div>TP ' + (target == null ? 'n/a' : Number(target).toFixed(2)) + '</div>'
      + '<button class="exit-trade-btn" data-ticket="' + escapeHtml(ticket) + '" data-id="' + escapeHtml(p.position_id || '') + '" type="button">END TRADE</button>'
      + '</div>';
  }
  list.innerHTML = html;
}

function renderMission(s) {
  var mission = s.promotion || {};
  var stage = byId('missionStage');
  var summary = byId('missionSummary');
  var list = byId('missionGateList');
  var nextEl = byId('missionNext');
  var complete = byId('missionComplete');
  var overallPct = byId('missionOverall');
  var overallFill = byId('missionOverallFill');
  if (!list || !stage || !summary) { return; }

  stage.textContent = mission.stage_label || 'DEMO VALIDATION';
  complete.textContent = (mission.gates_passed || 0) + '/' + (mission.gates_total || 7);
  if (mission.promotion_eligible) {
    summary.textContent = '7 promotion gates — passed (paper records). Live still requires your sign-off per gate.';
    stage.className = 'mission-stage-badge mission-stage-live';
    complete.style.color = '#4ade80';
    if (overallPct) { overallPct.textContent = '100%'; }
  } else {
    summary.textContent = '7 promotion gates — demo must earn each one before any live capital is touched.';
    stage.className = 'mission-stage-badge';
    complete.style.color = '#fca5a5';
  }

  var gates = (mission.gates || []).slice();
  // Sort completed → least complete; stable tiebreak by gate number (1..7)
  gates.sort(function (a, b) {
    var dp = (Number(b.progress) || 0) - (Number(a.progress) || 0);
    if (dp !== 0) { return dp; }
    return (Number(a.n) || 0) - (Number(b.n) || 0);
  });
  var totalProgress = 0;
  var html = '';
  for (var i = 0; i < gates.length; i += 1) {
    var g = gates[i];
    var status = String(g.status || 'not_started').replace(/[^a-z_]/g, '_');
    var pct = Math.max(0, Math.min(100, Number(g.progress || 0)));
    totalProgress += pct;
    var summaryLine = String(g.summary || g.detail || '');
    var rows = g.detail_rows || [];
    var metricsHtml = '';
    for (var d = 0; d < rows.length; d += 1) {
      metricsHtml += '<div><dt>' + escapeHtml(rows[d].label) + '</dt><dd>' + escapeHtml(rows[d].value) + '</dd></div>';
    }
    var key = String(g.id || i);
    var isOpen = !!window._missionGateOpen && window._missionGateOpen[key] === true;
    var gateCls = 'gate state-' + status + (isOpen ? ' open' : '');
    html += '<li class="' + gateCls + '" data-gate="' + escapeHtml(key) + '">'
      + '<button class="gate-head" type="button" aria-expanded="' + (isOpen ? 'true' : 'false') + '" aria-controls="gate-detail-' + escapeHtml(key) + '">'
      + '<span class="gate-head-left">'
      + '<span class="gate-num-n">#' + g.n + '</span>'
      + '<span class="gate-title-block">'
      + '<span class="gate-title">' + escapeHtml(g.label || '') + '</span>'
      + '<span class="gate-summary">' + escapeHtml(summaryLine) + '</span>'
      + '</span></span>'
      + '<span class="gate-right">'
      + '<span class="gate-pct">' + pct + '%</span>'
      + '<span class="gate-status">' + escapeHtml(status.replace(/_/g, ' ')) + '</span>'
      + '<span class="gate-caret">&#x25BE;</span>'
      + '</span>'
      + '</button>'
      + '<div class="gate-bar"><span class="gate-bar-fill" style="width:' + pct + '%"></span></div>'
      + '<div class="gate-body" id="gate-detail-' + escapeHtml(key) + '" role="region" aria-label="' + escapeHtml(g.label || '') + ' details">'
      + '<dl class="gate-metrics">' + metricsHtml + '</dl>'
      + '</div>'
      + '</li>';
  }
  list.innerHTML = html;

  var avg = gates.length ? Math.round(totalProgress / gates.length) : 0;
  if (overallPct) { overallPct.textContent = avg + '%'; }
  if (overallFill) { overallFill.style.width = avg + '%'; }

  list.onclick = function (ev) {
    var node = ev.target;
    while (node && node !== list) {
      if (node.className && String(node.className).indexOf('gate-head') === 0) { break; }
      node = node.parentNode;
    }
    if (!node || node === list) { return; }
    var li = node.parentNode;
    if (!li) { return; }
    var key = String(li.getAttribute('data-gate') || li.getAttribute('data-index') || '');
    if (!window._missionGateOpen) { window._missionGateOpen = {}; }
    var opened = li.className.indexOf(' open') !== -1;
    window._missionGateOpen[key] = !opened;
    li.className = opened
      ? li.className.replace(/\bopen\b/g, '').trim().replace(/\s+/g, ' ')
      : li.className + ' open';
    node.setAttribute('aria-expanded', opened ? 'false' : 'true');
  };

  nextEl.textContent = 'next: ' + (mission.critical_next || 'record M1 sign-off');
}

function wireMissionToggle() {
  var panel = byId('missionPanel');
  var toggle = byId('missionToggle');
  if (!panel || !toggle) { return; }
  toggle.addEventListener('click', function () {
    var collapsed = panel.className.indexOf('mission-collapsed') !== -1;
    if (collapsed) {
      panel.className = panel.className.replace(' mission-collapsed', '');
    } else {
      panel.className = panel.className + ' mission-collapsed';
    }
    toggle.setAttribute('aria-expanded', collapsed ? 'true' : 'false');
  });
}

function wireProfileToggle() {
  var panel = byId('profilePanel');
  var toggle = byId('profileToggle');
  if (!panel || !toggle) { return; }
  toggle.addEventListener('click', function () {
    var collapsed = panel.className.indexOf('profile-collapsed') !== -1;
    if (collapsed) {
      panel.className = panel.className.replace(' profile-collapsed', '');
    } else {
      panel.className = panel.className + ' profile-collapsed';
    }
    toggle.setAttribute('aria-expanded', collapsed ? 'true' : 'false');
  });
}

function orbToast(text, tone) {
  if (!text) { return; }
  var seen = window._orbToastSeen = window._orbToastSeen || {};
  if (Object.keys(seen).length > 60) { window._orbToastSeen = seen = {}; }
  var key = String(text) + '|' + Math.floor(Date.now() / 60000);
  if (seen[key]) { return; }
  seen[key] = true;
  var queue = window._orbToastQueue = window._orbToastQueue || [];
  queue.push({ text: text, tone: tone || 'mute' });
  if (queue.length > 3) { queue.shift(); }
  pumpOrbToast();
}

function pumpOrbToast() {
  var el = byId('orbToast');
  if (!el) { return; }
  if (window._orbToastUntil && Date.now() < window._orbToastUntil) { return; }
  var next = (window._orbToastQueue || []).shift();
  if (!next) { return; }
  el.textContent = next.text;
  el.className = 'orb-toast show ' + next.tone;
  window._orbToastUntil = Date.now() + 6000;
  window.setTimeout(function () {
    el.classList.remove('show');
    window._orbToastUntil = 0;
    window.setTimeout(pumpOrbToast, 250);
  }, 6000);
}

function updateUpstreamLine() {
  var el = byId('upstreamText');
  if (!el) { return; }
  var state = window._upstreamState || 'idle';
  var age = window._upstreamBase;
  if (age != null && window._upstreamAt != null) {
    age = age + (Date.now() - window._upstreamAt) / 1000;
  }
  var ageText = age == null ? '-' : (age < 60 ? Math.floor(age) + 's ago' : Math.floor(age / 60) + 'm ago');
  var bid = window._upstreamBid;
  var price = (bid != null) ? ' ? XAUUSD ' + Number(bid).toFixed(2) : '';
  if (state === 'idle') {
    el.innerHTML = 'agent stopped ? no upstream';
  } else if (state === 'live') {
    el.innerHTML = '<strong>live</strong>' + price + ' ? last write ' + ageText;
  } else {
    el.innerHTML = '<strong>STALE</strong> ? last write ' + ageText;
  }
}

function renderUpstream(s) {
  var u = s.upstream || {};
  var dot = byId('upstreamDot');
  if (!dot) { return; }
  dot.className = 'up-dot ' + (u.state || 'idle');
  window._upstreamBase = u.age_seconds;
  window._upstreamAt = Date.now();
  window._upstreamState = u.state || 'idle';
  window._upstreamBid = u.bid;
  updateUpstreamLine();
  var seq = u.seq || null;
  if (window._lastUpstreamSeq != null && seq !== window._lastUpstreamSeq) {
    window._upstreamFrames = (window._upstreamFrames || 0) + 1;
    var svg = byId('orbSvg');
    if (svg) { svg.classList.remove('beat'); void svg.getBoundingClientRect(); svg.classList.add('beat'); }
  }
  window._lastUpstreamSeq = seq;
  var countEl = byId('upstreamCount');
  if (countEl) { countEl.textContent = (window._upstreamFrames || 0) + ' frames'; }
}

function diffOrbEvents(prev, s) {
  if (!prev) { return; }
  var logs = s.agent_logs || [];
  var prevLen = (prev.agent_logs || []).length;
  for (var i = Math.max(prevLen, logs.length - 12); i < logs.length; i += 1) {
    var line = String(logs[i] || '');
    if (/ERROR|WARN/i.test(line)) {
      orbToast(line.length > 96 ? line.slice(0, 93) + '...' : line, /ERROR/i.test(line) ? 'error' : 'warn');
      break;
    }
  }
  var pd = prev.latest_decision || {};
  var d = s.latest_decision || {};
  if (d.as_of_utc && d.as_of_utc !== pd.as_of_utc) {
    var score = d.combined_score != null ? ' ? score ' + fmtScore(d.combined_score) : '';
    orbToast('new decision: ' + (d.decision_type || '?') + score, d.decision_type === 'ENTER_LONG' ? 'good' : 'mute');
  } else if (d.decision_type && pd.decision_type && d.decision_type !== pd.decision_type) {
    orbToast('decision ' + pd.decision_type + ' -> ' + d.decision_type, d.decision_type === 'ENTER_LONG' ? 'good' : 'warn');
  }
  var po = (prev.orders || [])[0] || {};
  var o = (s.orders || [])[0] || {};
  if (o.order_id && (o.order_id !== po.order_id || o.state !== po.state)) {
    orbToast('order ' + o.order_id + ' ? ' + (o.side || '') + ' ' + (o.volume != null ? o.volume : '') + ' @ ' + (o.price != null ? o.price : '-') + ' ? ' + (o.state || ''), 'good');
  }
  var pf = (prev.fills || [])[0] || {};
  var f = (s.fills || [])[0] || {};
  if (f.fill_id && (f.fill_id !== pf.fill_id || f.price !== pf.price)) {
    orbToast('fill ' + f.fill_id + ' ? @ ' + (f.price != null ? f.price : '-'), 'good');
  }
  var pe = (prev.exec_quality || {}).halt_flag;
  var e = (s.exec_quality || {}).halt_flag;
  if (!pe && e) { orbToast('KILL SWITCH armed - bot halted', 'error'); }
  var pu = (prev.upstream || {}).state;
  var u2 = (s.upstream || {}).state;
  if (pu && u2 && pu !== u2) {
    if (u2 === 'stale') { orbToast('UPSTREAM LOST - no ledger writes', 'warn'); }
    else if (u2 === 'live') { orbToast('UPSTREAM RESTORED', 'good'); }
    else if (u2 === 'idle') { orbToast('agent stopped', 'mute'); }
  }
}

function wireOrbTilt() {
  var wrap = byId('managerOrbWrap');
  if (!wrap) { return; }
  var reduceMotion = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  if (reduceMotion) { return; }
  var card = byId('managerCard');
  wrap.addEventListener('pointermove', function (ev) {
    var rect = wrap.getBoundingClientRect();
    var x = (ev.clientX - rect.left) / rect.width - 0.5;
    var y = (ev.clientY - rect.top) / rect.height - 0.5;
    wrap.style.transition = 'transform .08s ease';
    wrap.style.transform = 'rotateY(' + (x * 26).toFixed(2) + 'deg) rotateX(' + (-y * 22).toFixed(2) + 'deg) translateZ(18px)';
  });
  wrap.addEventListener('pointerenter', function () {
    if (card) { card.classList.add('orb-hover'); }
  });
  wrap.addEventListener('pointerleave', function () {
    wrap.style.transition = 'transform .45s cubic-bezier(.2,.9,.3,1.18)';
    wrap.style.transform = 'rotateY(0deg) rotateX(0deg) translateZ(0)';
    if (card) { card.classList.remove('orb-hover'); }
  });
}

function updateState(data) {
  markConnOk();
  var s = data.state || data;
  var previousRenderState = window._prevRenderState;
  window._prevRenderState = s;
  window._lastState = s;
  renderProfileSpec(s.profile_spec || {});
  renderSteps(s);
  renderMission(s);
  renderExecAnatomy(s);
  renderUpstream(s);
  diffOrbEvents(previousRenderState, s);
  renderNotifications(s.notifications || []);
  renderExecQuality(s);
  renderLiveTrades(s);
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
  var entryReady = !!(fusion.entry_candidate && decision.decision_type === 'ENTER_LONG');
  byId('acceptBtn').disabled = !(entryReady || overrideOn);
  var acceptBtn = byId('acceptBtn');
  acceptBtn.classList.toggle('accept-ready', entryReady || overrideOn);

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
  var allLogs = s.agent_logs || [];
  var slice = allLogs.slice(-120);
  var offset = allLogs.length - slice.length;
  var makeLine = function (line) {
    var cls = line.indexOf('ERROR') !== -1 ? 'line-error' : (line.indexOf('WARN') !== -1 ? 'line-warn' : 'line-info');
    var span = document.createElement('span');
    span.className = 'line ' + cls;
    span.textContent = line;
    return span;
  };
  if (window._lastLogCount == null || window._lastLogCount < offset) {
    while (logEl.firstChild) { logEl.removeChild(logEl.firstChild); }
    for (var k = 0; k < slice.length; k += 1) { logEl.appendChild(makeLine(String(slice[k]))); }
  } else {
    var start = Math.max(0, window._lastLogCount - offset);
    for (var j = start; j < slice.length; j += 1) { logEl.appendChild(makeLine(String(slice[j]))); }
    while (logEl.children.length > 120) { logEl.removeChild(logEl.firstChild); }
  }
  window._lastLogCount = allLogs.length;
  logEl.scrollTop = logEl.scrollHeight;
  byId('logCount').textContent = allLogs.length + ' lines';

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
  byId('recentOrders').innerHTML = ordersText.length
    ? ordersText.map(function (t) { return '<div class="evidence-line">' + escapeHtml(t) + '</div>'; }).join('')
    : '<span class="muted">no orders</span>';
  renderPositionAggregate(s);
  renderEvidencePositions(s);
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

window._openTraces = window._openTraces || {};

function fmtTraceTime(value) {
  if (!value) { return '\u2014'; }
  var text = String(value);
  if (text.length >= 16) { return text.slice(0, 10) + ' ' + text.slice(11, 16) + 'Z'; }
  return text;
}

function downloadJson(name, payload) {
  try {
    var blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  } catch (e) {
    toast('Download failed: ' + e.message);
  }
}

function renderPositionAggregate(s) {
  var el = byId('positionAggregate');
  if (!el) { return; }
  var agg = s.position_aggregate || {};
  var total = Number(agg.total || 0);
  var pnl = agg.realized_pnl == null ? null : Number(agg.realized_pnl);
  var pnlCls = total === 0 || pnl == null ? 'muted' : (pnl >= 0 ? 'ok' : 'warn');
  var winRate = agg.win_rate_pct == null ? null : Number(agg.win_rate_pct);
  var tiles = [
    ['POSITIONS', String(total) + ' (' + Number(agg.open_count || 0) + ' open / ' + Number(agg.closed_count || 0) + ' closed)', ''],
    ['VOLUME', Number(agg.total_volume || 0).toFixed(2) + ' lots' + (Number(agg.open_volume || 0) > 0 ? ' - ' + Number(agg.open_volume).toFixed(2) + ' open' : ''), ''],
    ['REALIZED P&L', pnl == null ? 'n/a' : (pnl >= 0 ? '+' : '') + pnl.toFixed(2), pnlCls],
    ['WIN RATE', winRate == null ? 'n/a' : winRate.toFixed(0) + '% (' + Number(agg.wins || 0) + 'W/' + Number(agg.losses || 0) + 'L)', winRate == null ? 'muted' : (winRate >= 50 ? 'ok' : 'warn')]
  ];
  el.innerHTML = tiles.map(function (tile) {
    return '<span class="agg-tile ' + tile[2] + '"><span class="agg-k">' + escapeHtml(tile[0]) + '</span><span class="agg-v">' + escapeHtml(tile[1]) + '</span></span>';
  }).join('');
}

function renderTraceHtml(trace) {
  var steps = trace.steps || [];
  var html = '<ol class="trace-steps">';
  for (var i = 0; i < steps.length; i += 1) {
    var step = steps[i] || {};
    html += '<li class="trace-step' + (step.missing ? ' missing' : '') + '">'
      + '<span class="trace-key">' + escapeHtml(String(step.key || '')) + '</span>'
      + '<span class="trace-time">' + escapeHtml(fmtTraceTime(step.at)) + '</span>'
      + '<span class="trace-detail">' + escapeHtml(String(step.detail || '')) + '</span></li>';
  }
  html += '</ol>';
  var logs = trace.logs || [];
  if (logs.length) {
    html += '<div class="trace-logs"><span class="trace-logs-head">ledger logs within 20m of open</span>';
    for (var j = 0; j < logs.length; j += 1) {
      var entry = logs[j] || {};
      html += '<span class="trace-log"><span class="trace-time">' + escapeHtml(fmtTraceTime(entry.at)) + '</span> '
        + '<span class="trace-key">' + escapeHtml(String(entry.level || '')) + '</span> '
        + escapeHtml(String(entry.event || '') + ': ' + String(entry.message || '')) + '</span>';
    }
    html += '</div>';
  } else {
    html += '<span class="trace-empty">no ledger log lines within 20 minutes of the open</span>';
  }
  return html;
}

function renderEvidencePositions(s) {
  var host = byId('recentPositions');
  if (!host) { return; }
  var positions = s.positions || [];
  var traces = s.position_traces || [];
  if (!positions.length) {
    host.innerHTML = '<span class="muted">no positions</span>';
    return;
  }
  var visible = Math.min(positions.length, traces.length);
  var html = '';
  for (var i = 0; i < visible; i += 1) {
    var pos = positions[i] || {};
    var trace = traces[i] || {};
    var pid = String(pos.position_id || ('pos-' + i));
    var open = !!window._openTraces[pid];
    var pnl = pos.realized_pnl;
    var pnlCls = pnl == null ? 'muted' : (Number(pnl) >= 0 ? 'ok' : 'warn');
    var pnlText = pnl == null ? 'n/a' : (Number(pnl) >= 0 ? '+' : '') + Number(pnl).toFixed(2);
    html += '<div class="evidence-line position-line">'
      + '<div class="position-row">'
      + '<span class="position-text">' + escapeHtml(String(pos.side || '?') + ' ' + String(pos.volume == null ? '?' : pos.volume) + ' @ ' + String(pos.open_price == null ? '?' : pos.open_price) + ' \u00b7 ' + (pos.close_time_utc ? 'closed' : 'open') + ' \u00b7 pnl ')
      + '<span class="' + pnlCls + '">' + escapeHtml(pnlText) + '</span></span>'
      + '<span class="position-actions">'
      + '<button class="trace-btn" type="button" data-trace-idx="' + i + '" aria-expanded="' + (open ? 'true' : 'false') + '">' + (open ? 'Hide steps' : 'Steps') + '</button>'
      + '<button class="trace-dl" type="button" data-trace-dl="' + i + '" title="Download this position and its ledger trace as JSON">JSON</button>'
      + '</span></div>'
      + '<div class="trace-panel" id="trace-' + i + '"' + (open ? '' : ' hidden') + '>' + renderTraceHtml(trace) + '</div>'
      + '</div>';
  }
  if (positions.length > visible) {
    html += '<div class="muted trace-more">' + String(positions.length - visible) + ' older positions in the ledger - latest ' + visible + ' shown</div>';
  }
  host.innerHTML = html;
}

function findTraceButton(el) {
  while (el && el !== document) {
    if (el.getAttribute && (el.getAttribute('data-trace-idx') != null || el.getAttribute('data-trace-dl') != null)) {
      return el;
    }
    el = el.parentNode;
  }
  return null;
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
  var decisionSide = (state.latest_decision || {}).side || null;
  var sideActive = decisionSide === 'LONG' || openPositions.length > 0;
  if (sideActive && openPositions.length) {
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
  var asOf = (state.latest_decision || {}).as_of_utc;
  ctx.setLineDash([3, 4]);
  ctx.strokeStyle = 'rgba(156,163,175,.4)';
  ctx.beginPath();
  ctx.moveTo(width - 1, 4);
  ctx.lineTo(width - 1, height - 4);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = 'rgba(156,163,175,.85)';
  ctx.font = '10px system-ui';
  ctx.fillText(asOf ? 'as of ' + String(asOf).slice(11, 16) + 'Z' : 'now', width - 64, height - 4);
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
byId('liveTradesList').addEventListener('click', function (e) {
  var target = e.target || e.srcElement;
  if (!target.classList.contains('exit-trade-btn')) { return; }
  pendingExitTicket = target.getAttribute('data-ticket') || '';
  byId('exitPhraseInput').value = '';
  try { byId('exitPhraseInput').focus(); } catch (e) {}
  byId('closeTradeModal').showModal();
});
byId('recentPositions').addEventListener('click', function (e) {
  var target = e.target || e.srcElement;
  var btn = findTraceButton(target);
  if (!btn) { return; }
  var st = window._lastState || {};
  var positions = st.positions || [];
  var traces = st.position_traces || [];
  var dlIdx = btn.getAttribute('data-trace-dl');
  if (dlIdx != null) {
    var idx = Number(dlIdx);
    var pos = positions[idx] || {};
    var trace = traces[idx] || {};
    downloadJson('position-' + String(pos.position_id || idx) + '-trace.json', {
      exported_at_utc: new Date().toISOString(),
      source: 'ledger (read-only)',
      position: pos,
      trace: trace
    });
    return;
  }
  var traceIdx = btn.getAttribute('data-trace-idx');
  if (traceIdx == null) { return; }
  var panel = byId('trace-' + traceIdx);
  if (!panel) { return; }
  var p = positions[Number(traceIdx)] || {};
  var pid = String(p.position_id || ('pos-' + traceIdx));
  var nowHidden = !panel.hasAttribute('hidden');
  if (nowHidden) {
    panel.setAttribute('hidden', 'hidden');
    delete window._openTraces[pid];
  } else {
    panel.removeAttribute('hidden');
    window._openTraces[pid] = true;
  }
  btn.setAttribute('aria-expanded', nowHidden ? 'false' : 'true');
  btn.textContent = nowHidden ? 'Steps' : 'Hide steps';
});
byId('downloadAllTracesBtn').addEventListener('click', function () {
  var st = window._lastState || {};
  downloadJson('position-traces-' + String(new Date().toISOString().slice(0, 10)) + '.json', {
    exported_at_utc: new Date().toISOString(),
    source: 'ledger (read-only)',
    aggregate: st.position_aggregate || {},
    positions: st.positions || [],
    traces: st.position_traces || []
  });
});
byId('closeDeclineModalBtn').addEventListener('click', function () { closeDialog('declineModal'); });
byId('cancelDeclineBtn').addEventListener('click', function () { closeDialog('declineModal'); });
byId('closeCloseTradeModalBtn').addEventListener('click', function () { closeDialog('closeTradeModal'); });
byId('cancelCloseTradeBtn').addEventListener('click', function () { closeDialog('closeTradeModal'); });
byId('exitPhraseInput').addEventListener('input', function (e) {
  var target = e.target || e.srcElement;
  byId('confirmCloseTradeBtn').disabled = String(target.value).replace(/^\s+|\s+$/g, '') !== 'END TRADE';
});
byId('confirmCloseTradeBtn').addEventListener('click', function () {
  var phrase = String(byId('exitPhraseInput').value).replace(/^\s+|\s+$/g, '');
  if (phrase !== 'END TRADE') { alert('Phrase must be END TRADE'); return; }
  if (!pendingExitTicket) { alert('No position selected'); return; }
  postJson('/api/agent/close-position', { confirm: phrase, ticket: pendingExitTicket }, function (err, result) {
    if (err) { alert(err.message); return; }
    closeDialog('closeTradeModal');
    if (result && result.ok) {
      toast('Position closed (ticket ' + pendingExitTicket + ').');
    } else {
      toast('Close failed; see terminal for details.');
    }
  });
});
byId('shutdownDashboardBtn').addEventListener('click', function () { byId('shutdownModal').showModal(); });
byId('killSwitchBtn').addEventListener('click', function () {
  byId('killSwitchPhraseInput').value = '';
  byId('confirmKillSwitchBtn').disabled = true;
  try { byId('killSwitchPhraseInput').focus(); } catch (e) {}
  byId('killSwitchModal').showModal();
});
byId('closeKillSwitchModalBtn').addEventListener('click', function () { closeDialog('killSwitchModal'); });
byId('cancelKillSwitchBtn').addEventListener('click', function () { closeDialog('killSwitchModal'); });
byId('killSwitchPhraseInput').addEventListener('input', function (e) {
  var target = e.target || e.srcElement;
  byId('confirmKillSwitchBtn').disabled = String(target.value).replace(/^\s+|\s+$/g, '') !== 'FLATTEN AND HALT';
});
byId('confirmKillSwitchBtn').addEventListener('click', function () {
  var phrase = String(byId('killSwitchPhraseInput').value).replace(/^\s+|\s+$/g, '');
  if (phrase !== 'FLATTEN AND HALT') { alert('Phrase must be FLATTEN AND HALT'); return; }
  setButtonState('confirmKillSwitchBtn', 'FLATTENING...', true);
  postJson('/api/agent/kill-switch', { confirm: phrase }, function (err, result) {
    setButtonState('confirmKillSwitchBtn', 'FLATTEN AND HALT', false);
    closeDialog('killSwitchModal');
    if (err) { alert(err.message); return; }
    toast(result && result.ok ? 'Kill switch executed: positions flattened, bot halted.' : 'Kill switch failed; see terminal.');
  });
});
byId('resumeTradingBtn').addEventListener('click', function () {
  byId('clearHaltPhraseInput').value = '';
  byId('confirmClearHaltBtn').disabled = true;
  try { byId('clearHaltPhraseInput').focus(); } catch (e) {}
  byId('clearHaltModal').showModal();
});
byId('closeClearHaltModalBtn').addEventListener('click', function () { closeDialog('clearHaltModal'); });
byId('cancelClearHaltBtn').addEventListener('click', function () { closeDialog('clearHaltModal'); });
byId('clearHaltPhraseInput').addEventListener('input', function (e) {
  var target = e.target || e.srcElement;
  byId('confirmClearHaltBtn').disabled = String(target.value).replace(/^\s+|\s+$/g, '') !== 'RESUME TRADING';
});
byId('confirmClearHaltBtn').addEventListener('click', function () {
  var phrase = String(byId('clearHaltPhraseInput').value).replace(/^\s+|\s+$/g, '');
  if (phrase !== 'RESUME TRADING') { alert('Phrase must be RESUME TRADING'); return; }
  setButtonState('confirmClearHaltBtn', 'RESUMING...', true);
  postJson('/api/agent/clear-halt', { confirm: phrase }, function (err, result) {
    setButtonState('confirmClearHaltBtn', 'RESUME TRADING', false);
    closeDialog('clearHaltModal');
    if (err) { alert(err.message); return; }
    toast(result && result.ok ? 'Halt flag cleared: the agent may run again. The kill switch can re-arm it at any time.' : 'Clear-halt failed; see terminal.');
  });
});
byId('closeShutdownModalBtn').addEventListener('click', function () { closeDialog('shutdownModal'); });
byId('cancelShutdownBtn').addEventListener('click', function () { closeDialog('shutdownModal'); });
byId('confirmShutdownBtn').addEventListener('click', function () {
  postJson('/api/server/shutdown', { confirm: 'SHUT DASHBOARD' }, function () {
    window._conn.shutdown = true;
    stopPolling();
    if (window._conn.stream) { try { window._conn.stream.close(); } catch (e) {} window._conn.stream = null; }
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
function fallbackCopyText(text, done) {
  var area = document.createElement('textarea');
  area.value = text;
  document.body.appendChild(area);
  area.select();
  var ok = false;
  try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
  document.body.removeChild(area);
  done(ok);
}

function flashCopyButton(label, ok) {
  var btn = byId('copyLogsBtn');
  if (!btn) { return; }
  if (btn._resetTimer) { clearTimeout(btn._resetTimer); }
  btn.textContent = label;
  btn.className = 'btn btn-ghost' + (ok ? ' copy-ok' : ' copy-fail');
  btn._resetTimer = setTimeout(function () {
    btn.textContent = 'Copy logs';
    btn.className = 'btn btn-ghost';
  }, 2000);
}

byId('copyLogsBtn').addEventListener('click', function () {
  var text = byId('terminal').innerText || '';
  var lines = text.split('\n').filter(function (line) { return line.trim() !== ''; }).length;
  if (!lines) {
    flashCopyButton('Nothing to copy', false);
    toast('Agent terminal is empty - start the agent first.');
    return;
  }
  var done = function (ok) {
    if (ok) {
      flashCopyButton('\u2713 Copied', true);
      toast('Copied ' + lines + ' log line' + (lines === 1 ? '' : 's') + ' to clipboard.');
    } else {
      flashCopyButton('Copy failed', false);
      toast('Copy failed - select the terminal text manually.');
    }
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(function () { done(true); }, function () {
      fallbackCopyText(text, done);
    });
    return;
  }
  fallbackCopyText(text, done);
});

function padClock(n) { return (n < 10 ? '0' : '') + n; }
function fmtUtcClock(value) {
  var d = value instanceof Date ? value : new Date(value);
  if (isNaN(d.getTime())) { return ''; }
  return padClock(d.getUTCHours()) + ':' + padClock(d.getUTCMinutes()) + ':' + padClock(d.getUTCSeconds());
}
function setChartRefreshNote(text, cls) {
  var el = byId('chartRefreshNote');
  if (!el) { return; }
  el.textContent = text;
  el.className = 'chart-refresh-note' + (cls ? ' ' + cls : '');
}
function describeChartFreshness(bars) {
  var checked = fmtUtcClock(new Date());
  if (!bars.length) {
    setChartRefreshNote('checked ' + checked + ' UTC - no bars yet, start the agent', 'stale');
    return { empty: true, text: 'No candlesticks in the ledger yet - start the agent.' };
  }
  var last = bars[bars.length - 1];
  var lastTime = new Date(last.time);
  if (isNaN(lastTime.getTime())) {
    setChartRefreshNote('checked ' + checked + ' UTC - ' + bars.length + ' bars, time unknown', '');
    return { empty: false, text: 'Candlesticks refreshed: ' + bars.length + ' bars.' };
  }
  var ageMinutes = Math.max(0, Math.floor((Date.now() - lastTime.getTime()) / 60000));
  var current = ageMinutes <= 30;
  var note = 'checked ' + checked + ' UTC - last bar ' + fmtUtcClock(lastTime)
    + ' UTC (' + ageMinutes + 'm old)'
    + (current ? ' - current' : ' - stale, waiting on the agent');
  setChartRefreshNote(note, current ? 'fresh' : 'stale');
  return {
    empty: false,
    current: current,
    text: 'Candlesticks refreshed: ' + bars.length + ' bars, last bar ' + fmtUtcClock(lastTime)
      + ' UTC (' + (current ? 'current' : ageMinutes + 'm old - stale') + ').'
  };
}
byId('refreshChartBtn').addEventListener('click', function () {
  var btn = this;
  if (btn.disabled) { return; }
  btn.disabled = true;
  btn.textContent = 'Refreshing...';
  getJson('/api/state', function (err, data) {
    btn.disabled = false;
    btn.textContent = 'Refresh';
    if (err) {
      setChartRefreshNote('refresh failed - ' + err.message, 'stale');
      toast('Chart refresh failed: ' + err.message);
      return;
    }
    updateState(data);
    var s = (data && data.state) || data || {};
    var result = describeChartFreshness(s.chart_bars || []);
    toast(result.text);
  });
});

function fmtConnTime(ts) {
  try { return new Date(ts).toLocaleTimeString(); } catch (e) { return String(ts); }
}
function setConnBanner(kind, text, meta) {
  var el = byId('connBanner');
  if (!el) { return; }
  if (!kind) {
    el.className = 'conn-banner';
    if (document.body) { document.body.classList.remove('conn-lost'); }
    return;
  }
  el.className = 'conn-banner show ' + kind;
  var textEl = byId('connBannerText');
  var metaEl = byId('connBannerMeta');
  if (textEl) { textEl.textContent = text; }
  if (metaEl) { metaEl.textContent = meta || ''; }
  if (document.body) { document.body.classList.add('conn-lost'); }
}
function markConnOk() {
  var c = window._conn;
  if (!c || c.shutdown) { return; }
  var wasLost = c.lost;
  c.lastOk = Date.now();
  c.lost = false;
  if (wasLost) {
    setConnBanner(null);
    toast('Dashboard connection restored.');
  }
}
function markConnLost(reason) {
  var c = window._conn;
  if (!c || c.shutdown) { return; }
  var first = !c.lost;
  c.lost = true;
  var meta = c.lastOk ? ('last update ' + fmtConnTime(c.lastOk)) : 'no update received';
  if (reason) { meta += ' · ' + reason; }
  setConnBanner(
    'error',
    'Dashboard server unreachable — this page is stale. The bot may still be running.',
    meta + ' · retrying…'
  );
  if (first) {
    toast('Dashboard connection lost. Data on this page is stale.');
    var chipValue = byId('botChipValue');
    var chip = byId('botChip');
    if (chipValue) { chipValue.textContent = 'UNKNOWN'; }
    if (chip) { chip.className = 'chip chip-red'; }
  }
}
function stopPolling() {
  var c = window._conn;
  if (!c) { return; }
  c.polling = false;
  if (c.pollTimer) { window.clearTimeout(c.pollTimer); c.pollTimer = null; }
}
function startPolling() {
  var c = window._conn;
  if (!c || c.polling || c.shutdown) { return; }
  c.polling = true;
  var tick = function () {
    if (!c.polling || c.shutdown) { return; }
    getJson('/api/state', function (err, data) {
      if (!c.polling || c.shutdown) { return; }
      if (err) { markConnLost(err.message || 'request failed'); }
      else { updateState(data); }
      c.pollTimer = window.setTimeout(tick, REFRESH_MS);
    });
  };
  tick();
}
function scheduleStreamReconnect() {
  var c = window._conn;
  if (!c || c.shutdown || c.retryTimer) { return; }
  var delay = c.retryDelay || 2000;
  c.retryTimer = window.setTimeout(function () {
    c.retryTimer = null;
    c.retryDelay = Math.min((c.retryDelay || 2000) * 2, 15000);
    connectStream();
  }, delay);
}
function connectStream() {
  var c = window._conn;
  if (!c || c.shutdown) { return; }
  if (c.stream) {
    try { c.stream.close(); } catch (e) {}
    c.stream = null;
  }
  if (typeof EventSource === 'undefined') {
    startPolling();
    return;
  }
  var source = new EventSource('/api/stream');
  c.stream = source;
  source.onopen = function () {
    if (c.stream !== source) { return; }
    c.retryDelay = 2000;
    stopPolling();
    markConnOk();
  };
  source.addEventListener('state', function (ev) {
    var data;
    try {
      data = JSON.parse(ev.data);
    } catch (e) {
      source.close();
      if (c.stream === source) { c.stream = null; }
      startPolling();
      scheduleStreamReconnect();
      return;
    }
    updateState(data);
  });
  source.onerror = function () {
    source.close();
    if (c.stream === source) { c.stream = null; }
    startPolling();
    scheduleStreamReconnect();
  };
}
function checkConnectionHealth() {
  var c = window._conn;
  if (!c || c.shutdown) { return; }
  if (c.lastOk == null) {
    c.lastOk = Date.now();
    return;
  }
  var age = Date.now() - c.lastOk;
  if (age <= CONN_HEALTH_MS) { return; }
  markConnLost('no update for ' + Math.round(age / 1000) + 's');
  startPolling();
  if (c.stream) {
    try { c.stream.close(); } catch (e) {}
    c.stream = null;
  }
  scheduleStreamReconnect();
}
wireMissionToggle();
wireProfileToggle();
wireOrbTilt();
window.setInterval(updateUpstreamLine, 1000);
window.setInterval(checkConnectionHealth, 1000);
connectStream();
"""

_FEEDBACK_JS = _JS_HELPERS + r"""
// ---- Feedback section ----
var feedbackState = { items: [], editingId: null, attachments: [], sort: 'created', desc: true, status: '', type: '' };
var feedbackTypeLabels = { bug: 'Bug', improvement: 'Improvement', feature: 'Feature request', task: 'Task / chore', question: 'Question' };
var feedbackSeverityLabels = { critical: 'Critical', high: 'High', medium: 'Medium', low: 'Low' };
var feedbackStatusLabels = { new: 'New', accepted: 'Triaged · Accepted (queued)', needs_info: 'Needs information', duplicate: 'Duplicate', wont_fix: "Won't fix", not_a_bug: 'Not a bug', done: 'Done' };

function feedbackText(id) { return (byId(id).value || '').trim(); }

function copyTextFeedback(text, done) {
  var fallback = function () {
    var area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    document.body.appendChild(area);
    area.select();
    var ok = false;
    try { ok = document.execCommand('copy'); } catch (e) {}
    document.body.removeChild(area);
    done(ok);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(function () { done(true); }, fallback);
  } else {
    fallback();
  }
}

function renderFeedbackAttachments() {
  var wrap = byId('feedbackAttachments');
  wrap.innerHTML = '';
  for (var i = 0; i < feedbackState.attachments.length; i += 1) {
    (function (name) {
      var div = document.createElement('div');
      div.className = 'feedback-thumb';
      div.innerHTML = '<img alt="screenshot" src="/feedback/assets/' + encodeURIComponent(name) + '"><button type="button" aria-label="Remove screenshot">&times;</button>';
      div.querySelector('button').addEventListener('click', function () {
        feedbackState.attachments = feedbackState.attachments.filter(function (n) { return n !== name; });
        renderFeedbackAttachments();
      });
      wrap.appendChild(div);
    })(feedbackState.attachments[i]);
  }
}

function uploadFeedbackFile(file) {
  if (!file || !/^image\//.test(file.type)) { toast('Only image files can be attached.'); return; }
  var reader = new FileReader();
  reader.onload = function () {
    var dataUrl = String(reader.result || '');
    var comma = dataUrl.indexOf(',');
    postJson('/api/feedback/screenshot', { filename: file.name || 'screenshot.png', mime: file.type, data_base64: dataUrl.slice(comma + 1) }, function (err, result) {
      if (err) { toast('Screenshot upload failed: ' + err.message); return; }
      feedbackState.attachments.push(result.name);
      renderFeedbackAttachments();
      toast('Screenshot attached.');
    });
  };
  reader.readAsDataURL(file);
}

function resetFeedbackForm() {
  byId('feedbackForm').reset();
  feedbackState.editingId = null;
  feedbackState.attachments = [];
  byId('feedbackEditId').value = '';
  byId('feedbackFormTitle').textContent = 'File feedback';
  byId('feedbackSubmitBtn').textContent = 'Submit feedback';
  byId('feedbackSubmitBtn').disabled = false;
  byId('feedbackCancelEditBtn').hidden = true;
  renderFeedbackAttachments();
}

function fillFeedbackForm(item) {
  feedbackState.editingId = item.id;
  feedbackState.attachments = (item.attachments || []).slice();
  byId('feedbackEditId').value = String(item.id);
  byId('feedbackType').value = item.type;
  byId('feedbackSeverity').value = item.severity;
  byId('feedbackTitle').value = item.title || '';
  byId('feedbackDescription').value = item.description || '';
  byId('feedbackSteps').value = item.steps || '';
  byId('feedbackExpected').value = item.expected || '';
  byId('feedbackActual').value = item.actual || '';
  byId('feedbackRecommended').value = item.recommended || '';
  byId('feedbackFormTitle').textContent = 'Edit #' + item.id;
  byId('feedbackSubmitBtn').textContent = 'Save changes';
  byId('feedbackCancelEditBtn').hidden = false;
  renderFeedbackAttachments();
  byId('feedbackSection').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function collectFeedbackPayload() {
  return {
    type: byId('feedbackType').value,
    severity: byId('feedbackSeverity').value,
    title: feedbackText('feedbackTitle'),
    description: feedbackText('feedbackDescription'),
    steps: feedbackText('feedbackSteps'),
    expected: feedbackText('feedbackExpected'),
    actual: feedbackText('feedbackActual'),
    recommended: feedbackText('feedbackRecommended'),
    attachments: feedbackState.attachments.slice()
  };
}

function feedbackQuery() {
  var q = '?sort=' + encodeURIComponent(feedbackState.sort) + '&descending=' + (feedbackState.desc ? '1' : '0');
  if (feedbackState.status) { q += '&status=' + encodeURIComponent(feedbackState.status); }
  if (feedbackState.type) { q += '&type=' + encodeURIComponent(feedbackState.type); }
  return q;
}

function loadFeedback() {
  getJson('/api/feedback' + feedbackQuery(), function (err, data) {
    if (err) { byId('feedbackList').innerHTML = '<p class="muted">Could not load feedback: ' + escapeHtml(err.message) + '</p>'; return; }
    feedbackState.items = (data && data.items) || [];
    renderFeedbackCounts();
    renderFeedbackList();
  });
}

function renderFeedbackCounts() {
  var counts = { open: 0, accepted: 0, criticalHigh: 0, done: 0 };
  for (var i = 0; i < feedbackState.items.length; i += 1) {
    var it = feedbackState.items[i];
    if (it.status === 'done') { counts.done += 1; } else { counts.open += 1; }
    if (it.status === 'accepted') { counts.accepted += 1; }
    if (it.severity === 'critical' || it.severity === 'high') { counts.criticalHigh += 1; }
  }
  byId('feedbackCounts').innerHTML =
    '<span class="feedback-count-chip"><b>' + counts.open + '</b> open</span>' +
    '<span class="feedback-count-chip"><b>' + counts.accepted + '</b> queued</span>' +
    '<span class="feedback-count-chip"><b>' + counts.criticalHigh + '</b> crit/high</span>' +
    '<span class="feedback-count-chip"><b>' + counts.done + '</b> done</span>';
}

function feedbackTime(value) { return String(value || '').replace('T', ' ').replace('Z', ' UTC'); }

function renderFeedbackItem(item) {
  var html = '<article class="feedback-item" data-id="' + item.id + '" data-severity="' + escapeHtml(item.severity) + '" data-status="' + escapeHtml(item.status) + '">';
  html += '<div class="feedback-item-head">'
    + '<span class="badge-chip badge-type">' + escapeHtml(feedbackTypeLabels[item.type] || item.type) + '</span>'
    + '<span class="badge-chip badge-severity-' + escapeHtml(item.severity) + '">' + escapeHtml(feedbackSeverityLabels[item.severity] || item.severity) + '</span>'
    + '<span class="badge-chip badge-status-' + escapeHtml(item.status) + '">' + escapeHtml(feedbackStatusLabels[item.status] || item.status) + '</span>'
    + '<span class="feedback-item-title">' + escapeHtml(item.title) + '</span>'
    + '<span class="muted">#' + item.id + '</span>'
    + '</div>';
  html += '<div class="feedback-item-meta">filed ' + escapeHtml(feedbackTime(item.created_at)) + ' · updated ' + escapeHtml(feedbackTime(item.updated_at)) + '</div>';
  if (item.description) { html += '<div class="feedback-item-body">' + escapeHtml(item.description) + '</div>'; }
  var detailParts = [];
  if (item.steps) { detailParts.push('<strong>Steps to reproduce</strong>\n' + escapeHtml(item.steps)); }
  if (item.expected) { detailParts.push('<strong>Expected</strong>\n' + escapeHtml(item.expected)); }
  if (item.actual) { detailParts.push('<strong>Actual</strong>\n' + escapeHtml(item.actual)); }
  if (item.recommended) { detailParts.push('<strong>Recommended fix</strong>\n' + escapeHtml(item.recommended)); }
  if (detailParts.length) { html += '<details class="feedback-details"><summary class="muted">Steps / Expected / Actual / Recommended</summary><div class="feedback-item-body">' + detailParts.join('\n\n') + '</div></details>'; }
  var attachments = item.attachments || [];
  if (attachments.length) {
    html += '<div class="feedback-item-images">';
    for (var i = 0; i < attachments.length; i += 1) {
      html += '<a href="/feedback/assets/' + encodeURIComponent(attachments[i]) + '" target="_blank" rel="noopener"><img alt="screenshot ' + (i + 1) + '" src="/feedback/assets/' + encodeURIComponent(attachments[i]) + '"></a>';
    }
    html += '</div>';
  }
  html += '<div class="feedback-item-actions">'
    + '<button class="btn btn-secondary btn-small" data-action="copy" type="button">Copy as Markdown</button>'
    + '<button class="btn btn-ghost btn-small" data-action="edit" type="button">Edit</button>'
    + '<select data-action="status" aria-label="Mark triaged"><option value="" selected>Mark triaged…</option>'
    + Object.keys(feedbackStatusLabels).filter(function (k) { return k !== 'new'; }).map(function (k) { return '<option value="' + k + '">' + escapeHtml(feedbackStatusLabels[k]) + '</option>'; }).join('')
    + '</select>'
    + '<button class="btn btn-ghost btn-small" data-action="toggle-done" type="button">' + (item.status === 'done' ? 'Reopen' : 'Mark done') + '</button>'
    + '<button class="btn btn-danger btn-small" data-action="delete" type="button">Delete</button>'
    + '</div>';
  html += '</article>';
  return html;
}

function renderFeedbackList() {
  var list = byId('feedbackList');
  if (!feedbackState.items.length) { list.innerHTML = '<p class="muted">No feedback yet. File the first item on the left.</p>'; return; }
  var html = '';
  for (var i = 0; i < feedbackState.items.length; i += 1) { html += renderFeedbackItem(feedbackState.items[i]); }
  list.innerHTML = html;
}

byId('feedbackForm').addEventListener('submit', function (ev) {
  ev.preventDefault();
  var payload = collectFeedbackPayload();
  if (!payload.title) { toast('Title is required.'); return; }
  var submitBtn = byId('feedbackSubmitBtn');
  if (submitBtn.disabled) { return; } // already in flight; ignore double-clicks
  var wasEditing = Boolean(feedbackState.editingId);
  submitBtn.disabled = true;
  submitBtn.textContent = wasEditing ? 'Saving…' : 'Submitting…';
  byId('feedbackCancelEditBtn').disabled = true;
  byId('feedbackResetBtn').disabled = true;
  var settled = function (failed) {
    if (failed) {
      submitBtn.disabled = false;
      submitBtn.textContent = wasEditing ? 'Save changes' : 'Submit feedback';
      byId('feedbackCancelEditBtn').disabled = false;
      byId('feedbackResetBtn').disabled = false;
    } else {
      byId('feedbackCancelEditBtn').disabled = false;
      byId('feedbackResetBtn').disabled = false;
    }
  };
  if (feedbackState.editingId) {
    payload.id = feedbackState.editingId;
    postJson('/api/feedback/update', payload, function (err) {
      if (err) { settled(true); toast('Save failed: ' + err.message); return; }
      settled(false);
      toast('Feedback #' + feedbackState.editingId + ' updated.');
      resetFeedbackForm();
      loadFeedback();
    });
  } else {
    postJson('/api/feedback/create', payload, function (err, result) {
      if (err) { settled(true); toast('Submit failed: ' + err.message); return; }
      settled(false);
      toast('Feedback #' + (result && result.item ? result.item.id : '') + ' filed.');
      resetFeedbackForm();
      loadFeedback();
    });
  }
});
byId('feedbackResetBtn').addEventListener('click', resetFeedbackForm);
byId('feedbackCancelEditBtn').addEventListener('click', resetFeedbackForm);
byId('feedbackSort').addEventListener('change', function () { feedbackState.sort = this.value; loadFeedback(); });
byId('feedbackSortDirBtn').addEventListener('click', function () { feedbackState.desc = !feedbackState.desc; this.textContent = feedbackState.desc ? '↓' : '↑'; loadFeedback(); });
byId('feedbackStatusFilter').addEventListener('change', function () { feedbackState.status = this.value; loadFeedback(); });
byId('feedbackTypeFilter').addEventListener('change', function () { feedbackState.type = this.value; loadFeedback(); });
byId('feedbackFileInput').addEventListener('change', function () { var files = this.files || []; for (var i = 0; i < files.length; i += 1) { uploadFeedbackFile(files[i]); } this.value = ''; });
var feedbackDropzone = byId('feedbackDropzone');
feedbackDropzone.addEventListener('click', function (ev) { if (ev.target.closest('.feedback-browse')) { return; } byId('feedbackFileInput').click(); });
feedbackDropzone.addEventListener('keydown', function (ev) { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); byId('feedbackFileInput').click(); } });
['dragenter', 'dragover'].forEach(function (name) { feedbackDropzone.addEventListener(name, function (ev) { ev.preventDefault(); feedbackDropzone.classList.add('drag-over'); }); });
['dragleave', 'drop'].forEach(function (name) { feedbackDropzone.addEventListener(name, function (ev) { ev.preventDefault(); feedbackDropzone.classList.remove('drag-over'); }); });
feedbackDropzone.addEventListener('drop', function (ev) { var files = (ev.dataTransfer && ev.dataTransfer.files) || []; for (var i = 0; i < files.length; i += 1) { uploadFeedbackFile(files[i]); } });
byId('feedbackSection').addEventListener('paste', function (ev) {
  var items = (ev.clipboardData && ev.clipboardData.items) || [];
  for (var i = 0; i < items.length; i += 1) { if (items[i].kind === 'file') { var file = items[i].getAsFile(); if (file) { uploadFeedbackFile(file); ev.preventDefault(); } } }
});
byId('feedbackList').addEventListener('click', function (ev) {
  var button = ev.target.closest('[data-action]');
  if (!button || button.tagName === 'SELECT') { return; }
  var article = button.closest('.feedback-item');
  var id = article ? Number(article.getAttribute('data-id')) : 0;
  var item = null;
  for (var i = 0; i < feedbackState.items.length; i += 1) { if (feedbackState.items[i].id === id) { item = feedbackState.items[i]; break; } }
  if (!item) { return; }
  var action = button.getAttribute('data-action');
  if (action === 'copy') {
    getJson('/api/feedback/markdown?id=' + id, function (err, data) {
      if (err) { toast('Could not build Markdown: ' + err.message); return; }
      copyTextFeedback((data && data.markdown) || '', function (ok) {
        if (!ok) { toast('Clipboard copy failed — try again.'); return; }
        button.textContent = '✓ Copied';
        button.disabled = true;
        window.setTimeout(function () { button.textContent = 'Copy as Markdown'; button.disabled = false; }, 2000);
        toast('Markdown for #' + id + ' copied to clipboard.');
      });
    });
    return;
  }
  if (action === 'edit') { fillFeedbackForm(item); return; }
  if (action === 'toggle-done') {
    var next = item.status === 'done' ? 'new' : 'done';
    postJson('/api/feedback/update', { id: id, status: next }, function (err) { if (err) { toast('Update failed: ' + err.message); return; } loadFeedback(); });
    return;
  }
  if (action === 'delete') {
    if (!confirm('Delete feedback #' + id + ' (' + item.title + ')? This cannot be undone.')) { return; }
    postJson('/api/feedback/delete', { id: id }, function (err) { if (err) { toast('Delete failed: ' + err.message); return; } toast('Feedback #' + id + ' deleted.'); if (feedbackState.editingId === id) { resetFeedbackForm(); } loadFeedback(); });
  }
});
byId('feedbackList').addEventListener('change', function (ev) {
  var select = ev.target.closest('select[data-action="status"]');
  if (!select || !select.value) { return; }
  var article = select.closest('.feedback-item');
  var id = article ? Number(article.getAttribute('data-id')) : 0;
  postJson('/api/feedback/update', { id: id, status: select.value }, function (err) {
    if (err) { toast('Update failed: ' + err.message); return; }
    toast('Feedback #' + id + ' marked: ' + feedbackStatusLabels[select.value]);
    loadFeedback();
  });
});
loadFeedback();

"""

_CSS = """
:root{
  color-scheme: dark;
  --bg:#000; --surface:#0a0a0a; --panel:#111; --panel2:#161616; --border:#222;
  --text:#e5e7eb; --muted:#9ca3af; --cyan:#22d3ee; --amber:#fbbf24; --green:#22c55e; --red:#ef4444; --purple:#a855f7; --gold:#fbbf24;
}
*{box-sizing:border-box} body{margin:0;padding:18px;background:var(--bg);color:var(--text);font:13px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
h1,h2,h3{margin:0;font-weight:700}
.state-strip{display:flex;align-items:center;justify-content:space-between;gap:14px;row-gap:10px;flex-wrap:wrap;padding:12px 16px;background:var(--surface);border:1px solid var(--border);border-radius:12px;position:sticky;top:8px;z-index:5}
.top-panel-row{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px;align-items:start}
.top-panel-row .mission-panel{margin-top:0}
@media (max-width:1180px){.top-panel-row{grid-template-columns:1fr}}
.profile-panel{padding:12px;margin-top:0;min-width:0;display:block}
.profile-collapsed .profile-body{display:none}
.profile-toggle{display:flex;align-items:center;justify-content:space-between;gap:12px;width:100%;background:transparent;border:none;color:inherit;text-align:left;cursor:pointer;padding:0}
.profile-toggle:focus-visible{outline:2px solid var(--cyan);outline-offset:4px}
.profile-toggle-left{display:flex;flex-direction:column;gap:2px;min-width:0}
.profile-toggle-title{font-size:12px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:var(--text)}
.profile-toggle-sub{font-size:10px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.profile-toggle-right{display:flex;align-items:center;gap:10px;flex-shrink:0}
.profile-stage-demo{border-color:#92400e;color:#fbbf24;background:#451a03}
.profile-stage-green{border-color:#166534;color:#4ade80;background:#052e1c}
.profile-collapsed .mission-caret{transform:rotate(-90deg)}
.profile-panel:not(.profile-collapsed) .mission-caret{transform:rotate(0)}
.profile-body{margin-top:12px;display:grid;grid-template-columns:1fr 1fr;gap:14px;min-height:0}
@media (max-width:760px){.profile-body{grid-template-columns:1fr}}
.warn-text{color:#fbbf24}
.halt-banner{display:none;align-items:center;justify-content:space-between;gap:14px;margin-top:12px;padding:10px 14px;border:1px solid #7f1d1d;background:#450a0a;border-radius:12px}
.halt-banner.armed{display:flex}
.halt-banner-text{font-size:11px;color:#fecaca;letter-spacing:.02em;line-height:1.45;min-width:0}
.halt-banner-text strong{color:#fca5a5;text-transform:uppercase;letter-spacing:.1em;margin-right:6px}
.halt-banner-btn{border:1px solid #b91c1c;background:#7f1d1d;color:#fecaca;border-radius:8px;padding:8px 14px;font-size:11px;font-weight:800;letter-spacing:.08em;cursor:pointer;white-space:nowrap}
.halt-banner-btn:hover{background:#991b1b}
.halt-banner-btn:focus-visible{outline:2px solid #fca5a5;outline-offset:2px}
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
.exec-quality-panel{margin-top:12px;padding:12px}
.eq-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:6px}
.vitals-panel{margin-top:12px;padding:10px;border:1px solid var(--border);border-radius:10px;background:#070707;display:flex;flex-direction:column;gap:6px}
.vitals-title{margin:0;font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);display:flex;justify-content:space-between;align-items:baseline}
.vitals-live{color:#4ade80;font-size:9px;letter-spacing:.14em}
.vitals-group{margin:2px 0 0;font-size:9px;color:var(--muted);text-transform:uppercase;letter-spacing:.1em;border-bottom:1px solid var(--border);padding-bottom:4px}
.mission-panel{padding:12px;margin-top:12px}
.mission-collapsed .mission-body{display:none}
.mission-collapsed .mission-overall-bar{display:none}
.mission-toggle{display:flex;align-items:center;justify-content:space-between;gap:12px;width:100%;background:transparent;border:none;color:inherit;text-align:left;cursor:pointer;padding:0}
.mission-toggle:focus-visible{outline:2px solid var(--cyan);outline-offset:4px}
.mission-toggle-left{display:flex;flex-direction:column;gap:2px;min-width:0}
.mission-toggle-title{font-size:12px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:var(--text)}
.mission-toggle-sub{font-size:10px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mission-toggle-right{display:flex;align-items:center;gap:10px;flex-shrink:0}
.mission-pct{font-size:11px;font-weight:800;letter-spacing:.06em;color:#fca5a5;min-width:36px;text-align:right}
.mission-stage-badge{font-size:9px;font-weight:800;letter-spacing:.14em;padding:3px 10px;border-radius:6px;border:1px solid #b91c1c;color:#fca5a5;background:#450a0a;white-space:nowrap}
.mission-stage-live{border-color:#166534;color:#4ade80;background:#052e1c}
.mission-caret{transition:transform .18s ease;color:var(--muted)}
.mission-collapsed .mission-caret{transform:rotate(-90deg)}
.mission-overall-bar{height:6px;border-radius:99px;overflow:hidden;background:#1f2937;margin:10px 0 0}
.mission-overall-fill{display:block;height:100%;width:0%;background:linear-gradient(90deg,#22d3ee,#a855f7,#fbbf24,#22c55e);transition:width .35s ease}
.mission-panel:not(.mission-collapsed) .mission-caret{transform:rotate(0)}
.gate-rows{list-style:none;display:flex;flex-direction:column;gap:6px;margin:10px 0 0;padding:0}
.gate{display:flex;flex-direction:column;gap:4px;padding:8px 10px;border:1px solid var(--border);border-radius:10px;background:var(--surface);transition:border-color .18s ease}
.gate.open{border-color:#164e63;background:#07161f}
.gate-head{display:flex;justify-content:space-between;align-items:center;gap:10px;background:transparent;border:none;color:inherit;text-align:left;cursor:pointer;width:100%;padding:0}
.gate-head:hover{opacity:.92}
.gate-head:focus-visible{outline:2px solid var(--cyan);outline-offset:2px}
.gate-head-left{display:flex;align-items:center;gap:10px;min-width:0;flex:1}
.gate-num-n{font-size:18px;font-weight:800;letter-spacing:.06em;color:var(--text);min-width:28px}
.gate-title-block{min-width:0;flex:1}
.gate-title{font-size:11px;font-weight:700;letter-spacing:.03em;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.gate-summary{font-size:10px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.gate-right{display:flex;align-items:center;gap:10px;flex-shrink:0}
.gate-pct{font-size:11px;font-weight:800;letter-spacing:.05em;color:var(--text);min-width:36px;text-align:right}
.gate-status{font-size:9px;font-weight:800;letter-spacing:.1em;text-transform:uppercase;padding:2px 6px;border-radius:4px;border:1px solid var(--border)}
.gate-caret{transition:transform .18s ease;color:var(--muted);font-size:10px}
.gate.open .gate-caret{transform:rotate(180deg);color:var(--cyan)}
.gate-bar{height:4px;border-radius:99px;overflow:hidden;background:#1f2937;margin:4px 0}
.gate-bar-fill{display:block;height:100%;width:0%;background:var(--green);transition:width .35s ease}
.gate-body{display:none;padding:6px 2px 4px 38px;border-top:1px solid var(--border);margin-top:4px}
.gate.open .gate-body{display:block}
.gate-metrics{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:4px 16px;margin:0}
.gate-metrics div{display:flex;justify-content:space-between;align-items:baseline;gap:8px;border-bottom:1px dotted #1f2937;padding:2px 0}
.gate-metrics dt{font-size:9px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}
.gate-metrics dd{margin:0;font-size:10px;color:var(--text);font-weight:600;text-align:right}
.gate.state-passed{border-color:#166534;background:#052e1c}
.gate.state-passed .gate-num-n{color:#4ade80}
.gate.state-passed .gate-bar-fill{background:#22c55e}
.gate.state-passed .gate-status{color:#4ade80;border-color:#166534;background:#031a0f}
.gate.state-in_progress .gate-num-n{color:#fcd34d}
.gate.state-in_progress .gate-bar-fill{background:#fbbf24}
.gate.state-in_progress .gate-status{color:#fcd34d;border-color:#713f12;background:#231a05}
.gate.state-partial .gate-num-n{color:#fbbf24}
.gate.state-partial .gate-bar-fill{background:#f59e0b}
.gate.state-partial .gate-status{color:#fbbf24;border-color:#713f12}
.gate.state-synthetic_only{border-color:#713f12}
.gate.state-synthetic_only .gate-num-n{color:#fbbf24}
.gate.state-synthetic_only .gate-bar-fill{background:#f59e0b}
.gate.state-synthetic_only .gate-status{color:#fcd34d;border-color:#713f12;background:#231a05}
.gate.state-insufficient_data{border-color:#164e63}
.gate.state-insufficient_data .gate-num-n{color:#67e8f9}
.gate.state-insufficient_data .gate-bar-fill{background:#22d3ee}
.gate.state-insufficient_data .gate-status{color:#67e8f9;border-color:#164e63;background:#04232d}
.gate.state-unproven{border-color:#164e63}
.gate.state-unproven .gate-num-n{color:#67e8f9}
.gate.state-unproven .gate-bar-fill{background:#164e63}
.gate.state-unproven .gate-status{color:#67e8f9;border-color:#164e63;background:#04232d}
.gate.state-failing{border-color:#7f1d1d;background:#1f0404}
.gate.state-failing .gate-num-n{color:#ef4444}
.gate.state-failing .gate-bar-fill{background:#ef4444}
.gate.state-failing .gate-status{color:#fca5a5;border-color:#7f1d1d;background:#1c0606}
.gate.state-not_started{border-color:#334155}
.gate.state-not_started .gate-status{color:#94a3b8;border-color:#334155}
.gate.state-blocked{border-color:#7f1d1d;background:#1f0404}
.gate.state-blocked .gate-num-n{color:#fca5a5}
.gate.state-blocked .gate-status{color:#fca5a5;border-color:#7f1d1d;background:#1c0606}
.mission-footer{display:flex;justify-content:space-between;align-items:center;margin-top:10px;border-top:1px solid var(--border);padding-top:8px}
.mission-complete{font-size:11px;font-weight:800;letter-spacing:.14em;color:#fca5a5}
.mission-hint{font-size:9px;color:var(--muted);margin-top:2px}
.eq-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:6px}
.eq-list li{display:flex;justify-content:space-between;align-items:center;gap:12px;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:6px 10px;font-size:11px}
.eq-label{color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.05em;white-space:nowrap}
.eq-value{color:var(--text);font-weight:600;text-align:right}
.eq-value.warn{color:#fca5a5}
.eq-value.ok{color:#4ade80}
.eq-value.muted{color:var(--muted);font-weight:400}
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
.conn-banner{position:fixed;top:0;left:0;right:0;z-index:120;display:none;align-items:center;justify-content:center;gap:10px;padding:8px 16px;background:#450a0a;border-bottom:1px solid #b91c1c;color:#fecaca;font-size:12px;font-weight:700;letter-spacing:.02em;box-shadow:0 4px 18px rgba(0,0,0,.55);text-align:center}
.conn-banner.show{display:flex}
body.conn-lost{padding-top:42px}
.conn-dot{width:9px;height:9px;border-radius:50%;background:currentColor;box-shadow:0 0 9px currentColor;flex:0 0 auto;animation:connPulse 1.1s ease-in-out infinite}
@keyframes connPulse{0%,100%{opacity:1}50%{opacity:.35}}
.conn-meta{color:#fca5a5;font-weight:500}
.brand{display:flex;gap:10px;align-items:center}
.logo{width:24px;height:24px;border-radius:7px;background:radial-gradient(circle at 40% 40%,var(--cyan),var(--gold));box-shadow:0 0 12px var(--cyan)}
.brand-text h1{font-size:16px;letter-spacing:.08em}
.brand-text p{margin:0;color:var(--muted);font-size:11px}
.chips{display:flex;gap:8px;flex-wrap:wrap}
.status-rail{display:flex;flex-wrap:wrap;gap:6px;padding:10px 12px;border-bottom:1px solid var(--border)}
.status-rail .chip{min-width:0;padding:4px 8px;gap:2px}
.status-rail .chip-label{font-size:8px;letter-spacing:.04em}
.status-rail .chip-value{font-size:10px}
.evidence-line{font-size:11px;line-height:1.45;padding:3px 0;border-bottom:1px dotted var(--border)}
.evidence-line:last-child{border-bottom:none;padding-bottom:0}
.chip{display:flex;flex-direction:column;min-width:86px;padding:6px 10px;border:1px solid var(--border);border-radius:8px;background:var(--panel)}
.chip-label{color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.05em}
.chip-value{font-weight:700;font-size:12px}
.chip-green{border-color:#166534;box-shadow:0 0 10px rgba(34,197,94,.28)}
.chip-red{border-color:#7f1d1d;box-shadow:0 0 10px rgba(239,68,68,.28)}
.chip-amber{border-color:#713f12}
.controls{display:flex;gap:8px;align-items:center;flex-wrap:wrap;justify-content:flex-end}
.override-toggle{display:inline-flex;align-items:center;gap:8px;padding:6px 12px;border:1px solid var(--border);border-radius:8px;background:#0a0a0a;color:var(--muted);font-weight:700;font-size:11px;cursor:pointer;user-select:none;white-space:nowrap}
.override-toggle input{accent-color:#f59e0b;cursor:pointer;transform:scale(1.2)}
.override-toggle.on{color:#fca5a5;border-color:#7f1d1d;box-shadow:0 0 14px rgba(239,68,68,.45)}
.bell-wrap{position:relative;display:inline-flex}
.bell-btn{position:relative;background:var(--panel2);border:1px solid var(--border);color:var(--muted);border-radius:10px;padding:8px 10px;cursor:pointer;transition:all .15s ease;line-height:0}
.bell-btn:hover{border-color:#334155;color:var(--text)}
.bell-btn:focus-visible{outline:2px solid var(--cyan);outline-offset:2px}
.bell-clear{color:#4ade80;border-color:#166534}
.bell-alert{color:#fca5a5;border-color:#7f1d1d;box-shadow:0 0 10px rgba(239,68,68,.25)}
.bell-badge{position:absolute;top:-8px;right:-8px;min-width:20px;height:20px;padding:0 5px;border-radius:999px;background:#dc2626;color:#fff;font-size:10px;font-weight:800;display:inline-flex;align-items:center;justify-content:center;border:2px solid var(--bg)}
.bell-dropdown{position:absolute;top:calc(100% + 10px);right:0;width:400px;max-width:92vw;max-height:440px;overflow-y:auto;background:var(--panel);border:1px solid var(--border);border-radius:12px;box-shadow:0 16px 48px rgba(0,0,0,.65);z-index:50;padding:12px}
.bell-dropdown[hidden]{display:none}
.bell-dropdown-header{display:flex;align-items:center;justify-content:space-between;padding-bottom:8px;border-bottom:1px solid var(--border);margin-bottom:8px}
.bell-dropdown-footer{margin-top:8px;border-top:1px solid var(--border);padding-top:8px;font-size:10px}
.notifications{display:flex;flex-direction:column;gap:8px;list-style:none;margin:0;padding:0}
.notify{display:flex;gap:10px;align-items:flex-start;justify-content:space-between;padding:10px 12px;border:1px solid var(--border);border-radius:10px;background:var(--surface)}
.notify.warning{border-color:#7f1d1d;background:#1f0404}
.notify.info{border-color:#164e63;background:#07161f}
.notify.done{opacity:.6}
.notify-body{min-width:0}
.notify-title-row{display:flex;align-items:center;gap:8px}
.notify-title-row strong{font-size:11px}
.notify p{margin:2px 0 0;color:var(--muted);font-size:11px}
.notify-actions{flex-shrink:0}
.notify-action{background:#111;border:1px solid #155e75;color:#67e8f9;border-radius:6px;padding:4px 10px;font-size:11px;font-weight:700;cursor:pointer;white-space:nowrap}
.notify-action:hover:not(:disabled){border-color:#22d3ee}
.notify-action:disabled{opacity:.5;cursor:default}
.notify-check{color:#4ade80;font-weight:800}
.notify-empty{justify-content:center}
.refresh-btn{letter-spacing:.02em}
.layout{display:grid;grid-template-columns:330px 1fr 370px;gap:14px;margin-top:12px}
.panel{background:var(--panel);border:1px solid var(--border);border-radius:12px}
.node-panel{padding:12px}
.panel-title{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.panel-subtitle{margin:0 0 8px;color:var(--muted);font-size:11px}
.node-grid{position:relative;display:grid;gap:8px;padding-left:14px}
.pipeline-svg{position:absolute;left:1px;top:4px;width:4px;height:calc(100% - 8px);pointer-events:none;opacity:.95}
.node-panel-offline .pipeline-svg{opacity:.35}
.node-conflict{border-color:#713f12;box-shadow:0 0 12px rgba(251,191,36,.4);animation:conflictFlash 1.1s ease-in-out infinite}
@keyframes conflictFlash{0%,100%{box-shadow:0 0 10px rgba(251,191,36,.25)}50%{box-shadow:0 0 22px rgba(251,191,36,.65)}}
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
.manager-orb-svg{transform-origin:50% 50%;transition:filter .25s ease}
.manager-orb-svg.beat{animation:sonarBreath 2.6s cubic-bezier(.16,.65,.3,1)}
@keyframes sonarBreath{0%{transform:scale(1)}32%{transform:scale(1.16)}62%{transform:scale(1.05)}100%{transform:scale(1)}}
.sonar{transform-origin:115px 115px;transform:scale(1);opacity:0;pointer-events:none;stroke-linecap:round}
.sonar-a{stroke:rgba(216,180,254,.6);stroke-width:5}
.sonar-b{stroke:rgba(168,85,247,.35);stroke-width:2.5}
.manager-orb-svg.beat .sonar-a{animation:sonarPing 2.6s cubic-bezier(.13,.6,.3,1)}
.manager-orb-svg.beat .sonar-b{animation:sonarPing 2.6s .38s cubic-bezier(.13,.6,.3,1)}
@keyframes sonarPing{0%{transform:scale(1);opacity:.85}55%{opacity:.32}100%{transform:scale(3.1);opacity:0}}
.manager-orb-svg.beat .orb-body{animation:coreEcho 2.6s ease-out}
@keyframes coreEcho{0%{opacity:1}30%{opacity:.5}62%{opacity:.5}100%{opacity:1}}
.manager-orb-svg.beat .orb-gloss{animation:glossEcho 2.6s ease-out}
@keyframes glossEcho{0%{opacity:.5}30%{opacity:.1}62%{opacity:.1}100%{opacity:.5}}
.upstream-stale .orb-ring,.upstream-idle .orb-ring{animation-play-state:paused !important}
.upstream-stale .manager-orb-svg,.upstream-idle .manager-orb-svg{filter:saturate(.45);opacity:.72}
.orb-hover .manager-orb-svg{filter:saturate(1.28) brightness(1.13)}
.upstream-line{display:flex;align-items:center;justify-content:center;gap:8px;margin:-2px 0 2px}
.up-dot{width:8px;height:8px;border-radius:50%;background:#475569;flex:0 0 auto}
.up-dot.live{background:#22c55e;box-shadow:0 0 8px rgba(34,197,94,.75)}
.up-dot.stale{background:#f59e0b;box-shadow:0 0 8px rgba(245,158,11,.7)}
.up-dot.idle{background:#475569}
.up-text{font-size:10px;letter-spacing:.03em;color:var(--muted)}
.up-text strong{color:var(--text)}
.up-count{font-size:9px;color:var(--muted)}
.orb-toast{position:absolute;top:2px;left:50%;transform:translate(-50%,-10px);z-index:6;max-width:92%;background:rgba(6,10,14,.92);border:1px solid #22d3ee;border-radius:9px;padding:6px 10px;font-size:10px;line-height:1.4;letter-spacing:.02em;color:#e2f5ff;opacity:0;pointer-events:none;transition:opacity .18s ease,transform .18s ease;text-align:left}
.orb-toast.show{opacity:1;transform:translate(-50%,0)}
.orb-toast.warn{border-color:#f59e0b;color:#fde68a}
.orb-toast.error{border-color:#ef4444;color:#fecaca}
.orb-toast.good{border-color:#22c55e;color:#bbf7d0}
.orb-toast.mute{border-color:#475569;color:#cbd5e1}
@media (prefers-reduced-motion: reduce){.manager-orb-svg.beat{animation:none !important}.manager-orb-svg.beat .sonar-a,.manager-orb-svg.beat .sonar-b,.manager-orb-svg.beat .orb-body,.manager-orb-svg.beat .orb-gloss{animation:none !important}.manager-orb-svg.beat .orb-body{opacity:.85}}
.manager-card{position:relative;display:flex;flex-direction:column;align-items:center;gap:10px;min-height:280px;border-radius:14px;background:linear-gradient(180deg,#161616,#0a0a0a);border:1px solid var(--border);overflow:hidden;text-align:center;perspective:720px;padding:14px}
.manager-orb-wrap{position:relative;width:230px;height:230px;transform-style:preserve-3d;transition:transform .18s ease}
.manager-orb-svg{position:absolute;inset:0;overflow:visible}
.orb-ring{transform-origin:115px 115px}
.orb-ring{transform-origin:115px 115px;animation-name:spinSlow;animation-duration:var(--orb-spin-duration,26s);animation-timing-function:linear;animation-iteration-count:infinite;animation-direction:var(--orb-spin-dir,normal)}
.orb-ring-1{animation-name:spinSlow}
.orb-ring-2{animation-name:spinReverse}
.orb-ring-3{animation-name:spinSlow}
.orb-body{filter:drop-shadow(0 0 22px rgba(168,85,247,.5))}
.orb-gloss{animation:breathe 3.4s ease-in-out infinite}
@keyframes spinSlow{from{transform:rotate(0)}to{transform:rotate(360deg)}}
@keyframes spinReverse{from{transform:rotate(0)}to{transform:rotate(-360deg)}}
@keyframes breathe{0%,100%{opacity:.4}50%{opacity:.75}}
.manager-core{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);text-align:center;z-index:2;pointer-events:none;background:rgba(5,5,5,.78);border:1px solid rgba(168,85,247,.35);border-radius:12px;padding:8px 14px;box-shadow:0 0 18px rgba(0,0,0,.6),inset 0 0 12px rgba(168,85,247,.12);backdrop-filter:blur(2px);width:max-content;max-width:170px}
.manager-label{font-size:9px;letter-spacing:.16em;color:#d1d5db;text-transform:uppercase}
.manager-decision{font-size:23px;font-weight:900;letter-spacing:.06em;color:#fde68a;line-height:1.15;margin-top:4px;text-shadow:0 0 10px rgba(251,191,36,.55),0 1px 2px #000}
.manager-status{font-size:8px;letter-spacing:.18em;color:#c084fc;text-transform:uppercase;margin-top:4px;text-shadow:0 0 8px rgba(168,85,247,.8),0 1px 2px #000}
.anatomy{width:100%;border:1px solid var(--border);border-radius:10px;background:#070707;padding:10px 12px;display:flex;flex-direction:column;gap:6px;text-align:left}
.anatomy-title-row{display:flex;justify-content:space-between;align-items:baseline}
.anatomy-title{margin:0;font-size:10px;text-transform:uppercase;letter-spacing:.1em;color:var(--muted)}
.anatomy-stamp{font-size:9px;color:var(--muted)}
.anatomy-line{display:grid;grid-template-columns:minmax(0,1fr) 90px minmax(0,1fr);gap:10px;align-items:center;font-size:11px}
.anatomy-track{height:8px;background:#0f0f0f;border:1px solid var(--border);border-radius:99px;position:relative;overflow:hidden}
.anatomy-fill{position:absolute;left:0;top:0;bottom:0;width:0%;background:#22c55e;border-radius:99px;transition:width .45s ease}
.anatomy-fill.warn{background:#ef4444}
.anatomy-track-center{display:flex;justify-content:center}
.anatomy-center-fill{position:absolute;top:0;bottom:0;left:50%;width:0%;background:#fbbf24;border-radius:0;transition:all .45s ease}
.anatomy-center-warn{background:#ef4444}
.anatomy-track-empty{visibility:hidden}
.anIngredient{display:flex;justify-content:space-between;gap:8px}
.anatomy-blocker .anatomy-value{color:#fca5a5}
.anatomy-blocker.block .anatomy-value{animation:blockerPulse 1.1s ease-in-out infinite}
@keyframes blockerPulse{0%,100%{color:#fca5a5;text-shadow:0 0 6px rgba(248,113,113,.45)}50%{color:#fecaca;text-shadow:0 0 18px rgba(248,113,113,.85)}}
.anatomy-label{color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.05em}
.anatomy-value{font-family:'Cascadia Code',Consolas,monospace;font-size:11px;color:var(--text);text-align:right}
.anatomy-blocker .anatomy-value{color:#fca5a5}
.anatomy-value.ok{color:#4ade80}
.anatomy-legend{margin:0 0 2px;font-size:8.5px;color:var(--muted);letter-spacing:.02em}
.anatomy-cols{display:grid;grid-template-columns:1fr 1fr 1.15fr;gap:8px}
.anat-col{border:1px solid var(--border);border-radius:9px;background:#0a0a0a;padding:7px 8px;display:flex;flex-direction:column;gap:5px;min-width:0}
.anat-col-head{font-size:8px;letter-spacing:.12em;color:var(--muted);text-transform:uppercase}
.anat-word{font-size:11px;font-weight:800;letter-spacing:.04em;line-height:1.15;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.anat-word.ok{color:#4ade80}.anat-word.warn{color:#fbbf24}.anat-word.bad{color:#fca5a5}.anat-word.neutral{color:#cbd5e1}
.anat-cap{font-size:8px;color:var(--muted)}
.anat-num{font-family:'Cascadia Code',Consolas,monospace;font-size:10px;color:var(--text);white-space:nowrap}
.anat-num.ok{color:#4ade80}.anat-num.warn{color:#fca5a5}
.anat-call .anat-word{font-size:12px}
.anatomy.call-enter .anat-call{border-color:#166534;box-shadow:0 0 12px rgba(34,197,94,.18)}
.anatomy.call-block .anat-call{border-color:#7f1d1d;box-shadow:0 0 12px rgba(239,68,68,.16)}
.anatomy-value.warn{color:#fca5a5}
.manager-card.state-enter .orb-body{filter:drop-shadow(0 0 30px rgba(34,197,94,.65))}
.manager-card.state-enter .manager-decision{color:#86efac}
.manager-card.state-notrade .orb-body{filter:drop-shadow(0 0 24px rgba(251,191,36,.45))}
.manager-card.state-notrade .manager-decision{color:#fcd34d}
.manager-card.state-halt .orb-body{filter:drop-shadow(0 0 34px rgba(239,68,68,.75))}
.manager-card.state-halt .manager-decision{color:#fca5a5}
.manager-card.state-halt .manager-status{color:#ef4444;animation:breathe 1.6s ease-in-out infinite}
.manager-card.state-idle .orb-body{filter:drop-shadow(0 0 22px rgba(168,85,247,.55))}
@media (prefers-reduced-motion: reduce){.orb-ring,.orb-gloss{animation:none}}
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
.chart-header{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}
.chart-tools{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.chart-badges{display:flex;gap:6px}
.chart-refresh-note{font-size:10px;color:var(--muted)}
.chart-refresh-note.fresh{color:#4ade80}
.chart-refresh-note.stale{color:#fca5a5}
#priceChart{width:100%;height:260px;border-radius:10px;background:var(--surface)}
.chart-footer{display:flex;justify-content:space-between;color:var(--muted);font-size:11px;margin-top:6px}
.evidence-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;padding:10px}
.evidence-item{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:8px}
.evidence-item dt{color:var(--muted);font-size:10px;text-transform:uppercase}
.evidence-item dd{margin:3px 0 0;font-size:11px}
.evidence-aggregate{grid-column:1/-1}
.evidence-aggregate dt{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.agg-note{font-size:9px;letter-spacing:0;text-transform:none;color:var(--muted)}
.agg-tile{display:inline-flex;flex-direction:column;gap:1px;margin:5px 6px 0 0;padding:5px 8px;border:1px solid var(--border);border-radius:8px;background:#0a0a0a}
.agg-k{font-size:8px;letter-spacing:.1em;color:var(--muted)}
.agg-v{font-size:11px;font-weight:700}
.agg-tile.ok .agg-v{color:#4ade80}.agg-tile.warn .agg-v{color:#fca5a5}.agg-tile.muted .agg-v{color:#cbd5e1}
.position-line{display:block}
.position-row{display:flex;align-items:center;justify-content:space-between;gap:8px}
.position-text{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.position-text .ok{color:#4ade80}.position-text .warn{color:#fca5a5}
.position-actions{display:flex;gap:5px;flex-shrink:0}
.trace-btn,.trace-dl{font:inherit;font-size:9px;letter-spacing:.06em;text-transform:uppercase;padding:2px 7px;border-radius:6px;border:1px solid var(--border);background:#0d0d0d;color:#cbd5e1;cursor:pointer}
.trace-btn:hover,.trace-dl:hover{border-color:#334155;color:#e5e7eb}
.trace-btn[aria-expanded="true"]{border-color:#0e7490;color:#67e8f9}
.trace-panel{margin-top:6px;padding-top:6px;border-top:1px dashed var(--border);display:flex;flex-direction:column;gap:5px}
.trace-panel[hidden]{display:none}
.trace-steps{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:4px}
.trace-step{display:grid;grid-template-columns:50px 78px minmax(0,1fr);gap:6px;font-size:10px;line-height:1.35;align-items:baseline}
.trace-key{font-size:8px;letter-spacing:.1em;color:var(--muted)}
.trace-time{font-family:'Cascadia Code',Consolas,monospace;font-size:9px;color:#9ca3af;white-space:nowrap}
.trace-detail{color:#d1d5db;overflow-wrap:anywhere}
.trace-step.missing .trace-key{color:#fbbf24}
.trace-step.missing .trace-detail{color:#fbbf24}
.trace-logs{display:flex;flex-direction:column;gap:2px;font-size:9.5px;color:#9ca3af}
.trace-logs-head{font-size:8px;letter-spacing:.1em;color:var(--muted);text-transform:uppercase}
.trace-log{overflow-wrap:anywhere}
.trace-empty{font-size:9.5px;color:var(--muted)}
.trace-more{font-size:9.5px;padding-top:4px}
.layout{display:grid;grid-template-columns:330px 1fr 370px;gap:14px;margin-top:12px;align-items:start}
.agent-steps ol{list-style:none}
.terminal-panel{max-height:420px}
#terminal{max-height:420px;overflow-y:auto}
.live-trades-card{padding:12px}
.live-trades{display:flex;flex-direction:column;gap:6px;max-height:280px;overflow-y:auto;margin-top:4px}
.trade-row{display:grid;grid-template-columns:auto auto auto auto auto auto;gap:6px;align-items:center;padding:8px;border:1px solid var(--border);border-radius:8px;background:var(--surface);font-size:11px}
.trade-row.win{border-color:#166534}
.trade-row.loss{border-color:#7f1d1d}
.pnl-positive{color:#4ade80}.pnl-negative{color:#f87171}
.exit-trade-btn{background:transparent;color:#fbbf24;border:1px solid #713f12;border-radius:6px;padding:2px 8px;font-size:11px;font-weight:700;cursor:pointer}
.exit-trade-btn:hover{color:#fef3c7;border-color:#f59e0b;box-shadow:0 0 10px rgba(245,158,11,.35)}
.live-trades-card.positions-open .exit-trade-btn{border-color:#ef4444;color:#fca5a5;box-shadow:0 0 12px rgba(239,68,68,.35);animation:exitPulse 1.6s ease-in-out infinite}
@keyframes exitPulse{0%,100%{box-shadow:0 0 8px rgba(239,68,68,.25)}50%{box-shadow:0 0 20px rgba(239,68,68,.6)}}
.bell-btn.bell-shake{animation:bellShake .5s cubic-bezier(.34,1.56,.64,1) 1}
@keyframes bellShake{0%,100%{transform:rotate(0)}20%{transform:rotate(-18deg)}40%{transform:rotate(14deg)}60%{transform:rotate(-9deg)}80%{transform:rotate(5deg)}}
.line{display:block;opacity:0;transform:translateX(-10px);animation:lineIn .32s ease forwards}
@keyframes lineIn{from{opacity:0;transform:translateX(-10px)}to{opacity:1;transform:translateX(0)}}
.notify-group-header{list-style:none;padding:6px 12px 2px;font-size:9px;text-transform:uppercase;letter-spacing:.14em;color:var(--muted);border-top:1px solid var(--border)}
.notify-group-header:first-child{border-top:none;padding-top:2px}
.notify-group-header.critical{color:#f87171}
.notify-group-header.warning{color:#fbbf24}
.notify-group-header.info{color:#67e8f9}
.terminal-panel{display:flex;flex-direction:column;padding:12px;gap:8px}
.terminal-header{display:flex;align-items:center;justify-content:space-between}
#terminal{background:#050505;border:1px solid var(--border);border-radius:10px;color:#a3e635;font-family:'Cascadia Code',Consolas,monospace;font-size:11px;line-height:1.45;overflow:auto;flex:1;min-height:220px;padding:10px;white-space:pre-wrap}
.line-error{color:#f87171}.line-warn{color:#fbbf24}.line-info{color:#cbd5e1}
.terminal-footer{display:flex;justify-content:space-between;align-items:center}
.btn.copy-ok{border-color:#166534;color:#4ade80}
.btn.copy-fail{border-color:#7f1d1d;color:#fca5a5}
.pill{display:inline-block;margin:2px 2px;padding:2px 6px;background:#111;border:1px solid var(--border);border-radius:6px;font-size:10px}
#latestDecisions{display:flex;flex-direction:column;gap:4px;max-height:132px;overflow-y:auto;overscroll-behavior:contain;padding-right:4px}
#latestDecisions .pill{display:block;margin:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.btn{display:inline-flex;align-items:center;gap:8px;padding:8px 12px;border-radius:8px;border:1px solid var(--border);background:#111;color:var(--text);font-weight:600;cursor:pointer;transition:all .15s ease;text-decoration:none}
.btn:hover{border-color:#334155;background:#161616}
.btn:disabled{opacity:.4;cursor:not-allowed}
.btn-primary{border-color:#155e75;background:#083344;color:#67e8f9}
.btn-primary:hover{box-shadow:0 0 12px rgba(34,211,238,.35)}
.btn-danger{border-color:#7f1d1d;background:#450a0a;color:#fca5a5}
.btn-danger:hover{box-shadow:0 0 12px rgba(239,68,68,.35)}
.btn-kill-switch{border-color:#ef4444;background:#7f1d1d;color:#fee2e2;font-size:14px;font-weight:800;letter-spacing:.08em;box-shadow:0 0 14px rgba(239,68,68,.35)}
.btn-kill-switch:hover:not(:disabled){background:#991b1b;border-color:#f87171;box-shadow:0 0 22px rgba(239,68,68,.5)}
.btn-kill-switch:disabled{opacity:.45;cursor:not-allowed;box-shadow:none}
.btn-execute{display:inline-flex;align-items:center;justify-content:center;gap:8px;border-color:#713f12;background:#3f2a08;color:var(--gold);font-size:13px;transition:all .2s ease}
.btn-execute:hover:not(:disabled){box-shadow:0 0 16px rgba(245,158,11,.45)}
.btn-execute .lock-icon{display:inline-block}
.btn-execute:not(:disabled) .lock-icon{display:none}
.btn-execute.accept-ready{border-color:#166534;background:#052e1c;color:#86efac;box-shadow:0 0 16px rgba(34,197,94,.4);animation:readyPulse 1.8s ease-in-out infinite}
@keyframes readyPulse{0%,100%{box-shadow:0 0 10px rgba(34,197,94,.3)}50%{box-shadow:0 0 24px rgba(34,197,94,.65)}}
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
@media (prefers-reduced-motion: reduce){.orb-ring,.orb-gloss,.line,.bell-btn.bell-shake,.anatomy-blocker.block .anatomy-value,.live-trades-card.positions-open .exit-trade-btn,.node-conflict,.btn-execute.accept-ready{animation:none !important}.manager-orb-wrap{transition:none}.line{opacity:1 !important;transform:none !important}.anatomy-fill,.anatomy-center-fill{transition:none}}
.feedback-section{margin:16px 0;padding:18px}
.feedback-page{max-width:1500px;margin:0 auto;padding:0 16px}
.feedback-page .feedback-section{margin-top:12px}
.feedback-strip{justify-content:space-between}
.feedback-head{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap;margin-bottom:12px}
.feedback-counts{display:flex;gap:8px;flex-wrap:wrap}
.feedback-count-chip{border:1px solid var(--border);border-radius:8px;padding:4px 10px;font-size:12px;color:var(--muted)}
.feedback-count-chip b{color:#e5e5e5}
.feedback-grid{display:grid;grid-template-columns:minmax(320px,460px) 1fr;gap:18px;align-items:start}
.feedback-form{max-height:calc(100vh - 150px);overflow-y:auto;padding-right:6px;scrollbar-width:thin}
.feedback-form-head{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:8px;position:sticky;top:-1px;background:var(--panel);z-index:2;padding:4px 0}
.feedback-form-title{font-size:14px;text-transform:uppercase;letter-spacing:.08em;color:#a3a3a3;margin:0}
.feedback-row-2{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.feedback-form .field{margin-bottom:10px}
.feedback-form textarea,.feedback-form input[type=text],.feedback-form select{width:100%;background:var(--panel2);border:1px solid var(--border);border-radius:8px;color:#e5e5e5;padding:8px 10px;font:inherit;font-size:13px}
.feedback-form textarea:focus,.feedback-form input[type=text]:focus,.feedback-form select:focus{outline:2px solid #22d3ee;outline-offset:0}
.field-inline{margin:0}
.field-inline select{font-size:12px;padding:6px 8px}
.feedback-dropzone{border:1.5px dashed var(--border);border-radius:10px;padding:12px;text-align:center;display:flex;flex-direction:column;gap:6px;align-items:center;cursor:pointer}
.feedback-dropzone:focus,.feedback-dropzone.drag-over{outline:2px solid #22d3ee;border-color:#22d3ee}
.feedback-browse{display:inline-block;cursor:pointer}
.feedback-attachments{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}
.feedback-thumb{position:relative;border:1px solid var(--border);border-radius:8px;overflow:hidden;width:84px;height:84px}
.feedback-thumb img{width:100%;height:100%;object-fit:cover;display:block}
.feedback-thumb button{position:absolute;top:2px;right:2px;background:rgba(0,0,0,.75);border:none;color:#fff;border-radius:50%;width:20px;height:20px;cursor:pointer;font-size:12px;line-height:1}
.feedback-form-actions{display:flex;gap:10px;margin-top:4px}
.btn-small{font-size:12px;padding:6px 10px}
.feedback-list-toolbar{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:10px}
.feedback-list-controls{display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap}
.feedback-list{display:flex;flex-direction:column;gap:10px;max-height:calc(100vh - 190px);min-height:320px;overflow-y:auto;padding-right:4px;scrollbar-width:thin}
.feedback-item{border:1px solid var(--border);border-left:3px solid #525252;border-radius:10px;padding:10px 12px;background:var(--panel2)}
.feedback-item[data-severity=critical]{border-left-color:#ef4444}
.feedback-item[data-severity=high]{border-left-color:#f97316}
.feedback-item[data-severity=medium]{border-left-color:#fbbf24}
.feedback-item[data-severity=low]{border-left-color:#22c55e}
.feedback-item[data-status=done]{opacity:.65}
.feedback-item-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.feedback-item-title{font-weight:600;font-size:14px;color:#f5f5f5;flex:1;min-width:160px}
.feedback-item-meta{color:var(--muted);font-size:11px;margin:4px 0 6px}
.badge-chip{border:1px solid var(--border);border-radius:999px;padding:2px 9px;font-size:11px;color:#d4d4d4;white-space:nowrap}
.badge-type{color:#67e8f9;border-color:#164e63}
.badge-severity-critical,.badge-severity-high{color:#fca5a5;border-color:#7f1d1d}
.badge-severity-medium{color:#fde68a;border-color:#713f12}
.badge-severity-low{color:#86efac;border-color:#14532d}
.badge-status-new{color:#e5e5e5}
.badge-status-accepted{color:#86efac;border-color:#14532d}
.badge-status-needs_info{color:#fde68a;border-color:#713f12}
.badge-status-duplicate,.badge-status-wont_fix,.badge-status-not_a_bug{color:#a3a3a3}
.badge-status-done{color:#86efac;border-color:#14532d}
.feedback-item-body{font-size:12.5px;color:#d4d4d4;white-space:pre-wrap;margin:6px 0 8px;max-height:180px;overflow-y:auto}
.feedback-item-images{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px}
.feedback-item-images a img{width:72px;height:72px;object-fit:cover;border-radius:6px;border:1px solid var(--border)}
.feedback-item-actions{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.feedback-item-actions select{background:var(--panel);border:1px solid var(--border);border-radius:6px;color:#e5e5e5;padding:6px 8px;font-size:12px}
@media (max-width:1100px){.feedback-grid{grid-template-columns:1fr}.feedback-row-2{grid-template-columns:1fr}.feedback-form,.feedback-list{max-height:none;overflow-y:visible}}
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
<div id="connBanner" class="conn-banner" role="alert" aria-live="assertive">
  <span class="conn-dot" aria-hidden="true"></span>
  <span id="connBannerText">Dashboard server unreachable — this page is stale. The bot may still be running.</span>
  <span id="connBannerMeta" class="conn-meta"></span>
</div>
<header class="state-strip" role="banner">
  <div class="brand">
    <span class="logo"></span>
    <div class="brand-text">
      <h1>GOLD BOT</h1>
      <p>Quantamental Agentic Desk · FBS-Demo XAUUSD</p>
    </div>
  </div>
  <div class="controls">
    <button id="startAgentBtn" class="btn btn-primary" type="button">{'Running' if running else 'Start Agent'}</button>
    <button id="stopAgentBtn" class="btn btn-danger" type="button" {'disabled' if not running else ''}>Stop Agent</button>
    <button id="killSwitchBtn" class="btn btn-kill-switch" type="button" title="EMERGENCY: flatten all XAUUSD demo positions and halt the bot">KILL SWITCH</button>
    <button id="shutdownDashboardBtn" class="btn btn-danger btn-shutdown" type="button">Shutdown Dashboard</button>
    <a class="btn btn-ghost" href="/feedback" title="Open the feedback and issues page">Feedback</a>
    <label class="override-toggle" id="overrideToggleLabel" title="TESTING ONLY: bypass macro/fusion gates and allow a bare 0.01-lot demo order">
      <input type="checkbox" id="overrideBlocksToggle" />
      <span>DEMO OVERRIDE</span>
    </label>
    <div class="bell-wrap" id="bellWrap">
      <button id="bellBtn" class="bell-btn" type="button" aria-label="Notifications" aria-expanded="false" aria-controls="notificationsDropdown">
        <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false"><path fill="currentColor" d="M12 22a2.4 2.4 0 0 0 2.4-2.4H9.6A2.4 2.4 0 0 0 12 22zm8-6.2-1.7-2.4v-5.2c0-3.1-1.7-5.6-4.4-6.4V6.6a1.9 1.9 0 0 0-3.8 0v1.2C7.4 3.7 5.7 6.1 5.7 9.2v5.2L4 15.8c-.4.7.1 1.5.9 1.5h14.2c.8 0 1.3-.8.9-1.5z"/></svg>
        <span id="bellBadge" class="bell-badge">0</span>
      </button>
      <div id="notificationsDropdown" class="bell-dropdown" role="dialog" aria-label="Notifications" hidden>
        <div class="bell-dropdown-header">
          <span class="panel-title">Notifications</span>
          <span id="bellSummary" class="muted">—</span>
        </div>
        <ul id="notificationsList" class="notifications"></ul>
        <div class="bell-dropdown-footer muted">Proven (macro files / ledger / WGC file + ledger), never by clicking. WGC: weight 0, diagnostic, HUMAN_JUDGMENT — reconfirm the newest monthly file each month.</div>
      </div>
    </div>
  </div>
</header>

<div id="overrideBanner" class="banner banner-danger" style="display:none">DEMO OVERRIDE ACTIVE - macro/fusion gates are bypassed. This will place a bare 0.01 XAUUSD demo order if you click Accept &amp; Execute. For testing only.</div>
<div id="haltBanner" class="halt-banner" role="alert">
  <div class="halt-banner-text"><strong>Halt armed</strong> The emergency kill-switch flag is set. The agent refuses to run; no orders can be sent, even with <code>--auto-execute</code>. Only clear it when you are intentionally resuming demo trading.</div>
  <button id="resumeTradingBtn" class="halt-banner-btn" type="button">RESUME TRADING</button>
</div>



<div class="top-panel-row">
<section class="panel profile-panel profile-collapsed" id="profilePanel">
  <button class="profile-toggle" id="profileToggle" type="button" aria-expanded="false" aria-controls="profileBody">
    <span class="profile-toggle-left">
      <span class="profile-toggle-title">Account Profile</span>
      <span class="profile-toggle-sub muted" id="profileSummary">micro_50 · HIGH RISK · not promotion-eligible</span>
    </span>
    <span class="profile-toggle-right">
      <span class="mission-stage-badge profile-stage-demo" id="profileStage">DEMO</span>
      <span class="mission-pct" id="profileRiskChip">−100:1</span>
      <span class="mission-caret" aria-hidden="true">&#x25BE;</span>
    </span>
  </button>
  <div class="profile-body" id="profileBody">
    <div class="profile-left">
      <label class="field"><span>Account type</span>
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
  </div>
</section>

<section class="panel mission-panel mission-collapsed" id="missionPanel">
  <button class="mission-toggle" id="missionToggle" type="button" aria-expanded="false" aria-controls="missionBody">
    <span class="mission-toggle-left">
      <span class="mission-toggle-title">Mission — Demo to Live</span>
      <span class="mission-toggle-sub muted" id="missionSummary">7 promotion gates — demo must earn each one before any live capital is touched.</span>
    </span>
    <span class="mission-toggle-right">
      <span class="mission-stage-badge" id="missionStage">DEMO</span>
      <span class="mission-pct" id="missionOverall">0%</span>
      <span class="gate-caret mission-caret" aria-hidden="true">&#x25BE;</span>
    </span>
  </button>
  <div class="mission-overall-bar" id="missionOverallBar" aria-hidden="true"><span class="mission-overall-fill" id="missionOverallFill"></span></div>
  <div class="mission-body" id="missionBody">
    <ol class="gate-rows" id="missionGateList"></ol>
    <div class="mission-footer"><span id="missionNext" class="muted">next: ...</span><span id="missionComplete" class="mission-complete">0/7</span></div>
  </div>
</section>

</div>

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

    <div class="node-grid" id="nodeGrid">
      <svg class="pipeline-svg" id="pipelineSvg" width="4" height="460" viewBox="0 0 4 460" aria-hidden="true">
        <line x1="2" y1="8" x2="2" y2="452" stroke="url(#pipelineGrad)" stroke-width="2" stroke-linecap="round"/>
        <defs>
          <linearGradient id="pipelineGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#22d3ee"/>
            <stop offset="45%" stop-color="#a855f7"/>
            <stop offset="100%" stop-color="#fbbf24"/>
          </linearGradient>
        </defs>
      </svg>
                        {_card("mt5", "MT5 XAUUSD", "Price + Execution Venue", '<div class="kv"><span>Feed</span><span id="kvMt5">…</span></div><div class="kv"><span>Bars</span><span>M15 / H4</span></div>', "cyan")}
      {_card("fundamental", "Fundamental Agent", "What to consider", '<div class="kv"><span>Score</span><span id="kvFundScore">…</span></div><div class="kv"><span>Stale</span><span id="kvFundStale">…</span></div>', "cyan")}
      {_card("technical", "Technical Agent", "When to act", '<div class="kv"><span>Score</span><span id="kvTechScore">…</span></div><div class="kv"><span>ATR</span><span id="kvTechAtr">…</span></div>', "cyan")}
      {_card("fusion", "Fusion Engine", "Both-layer agreement", '<div class="kv"><span>Score</span><span id="kvFusionScore">…</span></div><div class="kv"><span>Entry</span><span id="kvFusionEntry">…</span></div>', "amber")}
      {_card("overseer", "Overseer", "Rule-based referee", '<div class="kv"><span>Verdict</span><span id="kvOverseerVerdict">…</span></div><div class="kv"><span>Gate blocker</span><span id="kvGateBlocker">…</span></div>', "purple")}
      {_card("risk", "Risk Engine", "Veto-immune exits", '<div class="kv"><span>Drawdown</span><span id="kvDrawdown">…</span></div><div class="kv"><span>Kill switch</span><span id="kvKillSwitch">…</span></div>', "green")}
    </div>
    <div class="vitals-panel" id="vitalsPanel">
      <h3 class="vitals-title">Execution vitals <span class="vitals-live">live</span></h3>
      <h4 class="vitals-group">Gates</h4>
      <ul class="eq-list">
        <li><span class="eq-label">News blackout</span><span class="eq-value" id="eqBlackout">&mdash;</span></li>
        <li><span class="eq-label">Spread p50 / p99 / limit</span><span class="eq-value" id="eqSpread">&mdash;</span></li>
        <li><span class="eq-label">Kill switch</span><span class="eq-value" id="eqHalt">&mdash;</span></li>
      </ul>
      <h4 class="vitals-group">Vitals</h4>
      <ul class="eq-list">
        <li><span class="eq-label">MT5</span><span class="eq-value" id="eqMt5">&mdash;</span></li>
        <li><span class="eq-label">Last decision</span><span class="eq-value" id="eqLastDecision">&mdash;</span></li>
        <li><span class="eq-label">Next event</span><span class="eq-value" id="eqNextEvent">&mdash;</span></li>
      </ul>
      <h4 class="vitals-group">Evidence</h4>
      <ul class="eq-list">
        <li><span class="eq-label">Stop-stress @1.5x ATR</span><span class="eq-value" id="eqNewsStress">&mdash;</span></li>
        <li><span class="eq-label">Tick check @M15 opens</span><span class="eq-value" id="eqTick">&mdash;</span></li>
        <li><span class="eq-label">Exits recorded</span><span class="eq-value" id="eqExits">&mdash;</span></li>
      </ul>
      <h4 class="vitals-group">Ledger</h4>
      <ul class="eq-list">
        <li><span class="eq-label">H4 trend</span><span class="eq-value" id="eqH4Regime">&mdash;</span></li>
        <li><span class="eq-label">Demo trades closed</span><span class="eq-value" id="eqDemoTrades">&mdash;</span></li>
        <li><span class="eq-label">Demo P&amp;L</span><span class="eq-value" id="eqDemoPnl">&mdash;</span></li>
      </ul>
    </div>
  </aside>

  <section class="center">
    <div class="manager-card" id="managerCard">
      <div class="orb-toast" id="orbToast" role="status" aria-live="polite"></div>
      <div class="manager-orb-wrap" id="managerOrbWrap" role="status" aria-label="current manager decision">
        <svg class="manager-orb-svg" id="orbSvg" viewBox="0 0 230 230" width="230" height="230" aria-hidden="true">
          <defs>
            <radialGradient id="orbCore" cx="42%" cy="38%" r="62%">
              <stop offset="0%" stop-color="#fdf4ff"/>
              <stop offset="35%" stop-color="#c084fc"/>
              <stop offset="70%" stop-color="#7c3aed"/>
              <stop offset="100%" stop-color="#12051f"/>
            </radialGradient>
            <linearGradient id="orbHighlight" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="#ffffff" stop-opacity=".9"/>
              <stop offset="45%" stop-color="#ffffff" stop-opacity="0"/>
            </linearGradient>
          </defs>
          <circle class="sonar sonar-b" cx="115" cy="115" r="46" fill="none"/>
          <circle class="sonar sonar-a" cx="115" cy="115" r="46" fill="none"/>
          <circle class="orb-ring orb-ring-1" cx="115" cy="115" r="106" fill="none" stroke="rgba(168,85,247,.32)" stroke-width="1.5" stroke-dasharray="5 13"/>
          <circle class="orb-ring orb-ring-2" cx="115" cy="115" r="86" fill="none" stroke="rgba(34,211,238,.30)" stroke-width="1.2" stroke-dasharray="2 9"/>
          <circle class="orb-ring orb-ring-3" cx="115" cy="115" r="63" fill="none" stroke="rgba(251,191,36,.38)" stroke-width="1.4" stroke-dasharray="18 10"/>
          <circle class="orb-body" cx="115" cy="115" r="46" fill="url(#orbCore)"/>
          <ellipse class="orb-gloss" cx="97" cy="101" rx="17" ry="9" fill="url(#orbHighlight)" opacity=".5" transform="rotate(-16 97 101)"/>
        </svg>
        <div class="manager-core">
          <div class="manager-label">MANAGER</div>
          <div class="manager-decision" id="managerDecision">—</div>
          <div class="manager-status" id="managerStatus">IDLE</div>
        </div>
      </div>

      <div class="upstream-line" id="upstreamLine"><span class="up-dot idle" id="upstreamDot"></span><span class="up-text" id="upstreamText">upstream: waiting</span><span class="up-count" id="upstreamCount">0 frames</span></div>
      <div class="anatomy" id="anatomyPanel">
        <div class="anatomy-title-row">
          <h3 class="anatomy-title">Decision anatomy</h3>
          <span class="anatomy-stamp" id="anatomyStamp"></span>
        </div>
        <p class="anatomy-legend">score = 0.55 × fundamentals + 0.40 × technicals · entry gate: ≥ +0.200 and no blockers</p>
        <div class="anatomy-cols">
          <div class="anat-col">
            <div class="anat-col-head">Fundamental</div>
            <div class="anat-word" id="anFundWord">—</div>
            <div class="anatomy-track"><span class="anatomy-fill" id="anFundBar"></span></div>
            <div class="anat-num" id="anFund">—</div>
            <div class="anat-cap">0.55 × score</div>
          </div>
          <div class="anat-col">
            <div class="anat-col-head">Technical</div>
            <div class="anat-word" id="anTechWord">—</div>
            <div class="anatomy-track"><span class="anatomy-fill" id="anTechBar"></span></div>
            <div class="anat-num" id="anTech">—</div>
            <div class="anat-cap">0.40 × score</div>
          </div>
          <div class="anat-col anat-call">
            <div class="anat-col-head">The call</div>
            <div class="anat-word" id="anCallWord">—</div>
            <div class="anatomy-track anatomy-track-center"><span class="anatomy-center-fill" id="anCombinedFill"></span></div>
            <div class="anat-num" id="anCombined">—</div>
            <div class="anat-cap" id="anCallSub">overseer — · needs ≥ +0.200</div>
          </div>
        </div>
        <div class="anatomy-line anatomy-blocker" id="anBlockerWrap">
          <span class="anatomy-label">blocked by</span>
          <span class="anatomy-track anatomy-track-empty"></span>
          <span class="anatomy-value" id="anBlocker">—</span>
        </div>
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
          <button class="btn btn-execute" id="acceptBtn" type="button" disabled><svg class="lock-icon" id="acceptLock" width="14" height="14" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path fill="currentColor" d="M17 8V7a5 5 0 0 0-10 0v1H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V10a2 2 0 0 0-2-2h-1zm-9-1a3 3 0 0 1 6 0v1H8V7z"/></svg>Accept &amp; Execute</button>
        </div>
        <p class="hint">Accept &amp; Execute stays locked until every preflight item is PASS and the decision is ENTER_LONG.</p>
      </div>
    </div>

    <div class="panel chart-card" id="chartCard">
      <div class="chart-header">
        <h2 class="panel-title">XAUUSD · Candlesticks</h2>
        <div class="chart-tools">
          <span class="chart-refresh-note" id="chartRefreshNote" role="status"></span>
          <div class="chart-badges" id="chartBadges"><span class="badge badge-neutral">WAITING FOR BARS</span></div>
          <button class="btn btn-ghost btn-small" id="refreshChartBtn" type="button" title="Re-read the ledger and redraw the candlesticks">Refresh</button>
        </div>
      </div>
      <canvas id="priceChart" aria-label="Price chart" height="260"></canvas>
      <div class="chart-footer"><span id="chartFooterText">bars will appear when the bot writes them to the ledger</span><span id="chartTimeRange" class="muted"></span></div>
    </div>

    <div class="panel live-trades-card" id="liveTradesCard">
      <div class="chart-header">
        <h2 class="panel-title">Live Positions</h2>
        <span id="liveTradesCount" class="badge badge-neutral">0 open</span>
      </div>
      <div id="liveTradesList" class="live-trades"><p class="muted">No open positions.</p></div>
    </div>

    <div class="panel evidence-grid" id="evidenceGrid">
      <div class="evidence-item"><dt>Latest decisions</dt><dd id="latestDecisions" class="muted">—</dd></div>
      <div class="evidence-item"><dt>Recent orders</dt><dd id="recentOrders" class="muted">—</dd></div>
      <div class="evidence-item"><dt>Positions</dt><dd id="recentPositions" class="muted">—</dd></div>
      <div class="evidence-item"><dt>Equity curve</dt><dd id="equitySummary" class="muted">—</dd></div>
      <div class="evidence-item evidence-aggregate">
        <dt>Positions · aggregate <span class="agg-note" id="aggNote">all ledger rows</span><button class="trace-dl" id="downloadAllTracesBtn" type="button" title="Download every position plus its ledger trace as JSON">Download all</button></dt>
        <dd id="positionAggregate" class="muted">—</dd>
      </div>
    </div>
  </section>

  <aside class="panel terminal-panel">
    <div class="status-rail" aria-label="Runtime status">
      {_chip("Mode", mode, "neutral")}
      {_chip("Profile", profile.upper(), "amber")}
      <div class="chip chip-{'green' if running else 'neutral'}" id="botChip"><span class="chip-label">BOT</span><span class="chip-value" id="botChipValue">{'RUNNING' if running else 'STOPPED'}</span></div>
      <div class="chip chip-{'green' if mt5.get('status') == 'up' else 'red'}" id="mt5Chip"><span class="chip-label">MT5</span><span class="chip-value" id="mt5ChipValue">{_e(str(mt5.get('status', 'unknown')).upper())}</span></div>
      <div class="chip chip-{'red' if kill_switch else 'green'}" id="riskChip"><span class="chip-label">RISK</span><span class="chip-value" id="riskChipValue">DD {_e(drawdown)} KILL {'ON' if kill_switch else 'off'}</span></div>
    </div>
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

<dialog id="closeTradeModal" class="modal">
  <div class="modal-content modal-narrow">
    <div class="modal-header"><h2>End trade</h2><button class="icon-btn" id="closeCloseTradeModalBtn" type="button" aria-label="Close">×</button></div>
    <div class="modal-body">
      <p class="danger-text">This will close the selected MT5 position now at market. Risk exits remain veto-immune; this is an explicit operator override.</p>
      <label class="field"><span>Type END TRADE to authorize</span><input id="exitPhraseInput" type="text" autocomplete="off" spellcheck="false" placeholder="END TRADE"></label>
      <div class="modal-actions"><button class="btn btn-secondary" id="cancelCloseTradeBtn" type="button">Cancel</button><button class="btn btn-execute" id="confirmCloseTradeBtn" type="button" disabled>Close trade</button></div>
    </div>
  </div>
</dialog>

<dialog id="killSwitchModal" class="modal">
  <div class="modal-content modal-narrow">
    <div class="modal-header"><h2 class="danger-text">KILL SWITCH</h2><button class="icon-btn" id="closeKillSwitchModalBtn" type="button" aria-label="Close">×</button></div>
    <div class="modal-body">
      <p class="danger-text">This IMMEDIATELY closes EVERY open XAUUSD demo position at market, writes <code>kill-switch.halt</code> (the agent refuses to run again), and records a CRITICAL incident in the ledger. It does not flatten other instruments and cannot undo a bad fill. Demo account only.</p>
      <label class="field"><span>Type FLATTEN AND HALT to authorize</span><input id="killSwitchPhraseInput" type="text" autocomplete="off" spellcheck="false" placeholder="FLATTEN AND HALT"></label>
      <div class="modal-actions"><button class="btn btn-secondary" id="cancelKillSwitchBtn" type="button">Cancel</button><button class="btn btn-kill-switch" id="confirmKillSwitchBtn" type="button" disabled>FLATTEN AND HALT</button></div>
    </div>
  </div>
</dialog>

<dialog id="clearHaltModal" class="modal">
  <div class="modal-content modal-narrow">
    <div class="modal-header"><h2 class="warn-text">RESUME TRADING</h2><button class="icon-btn" id="closeClearHaltModalBtn" type="button" aria-label="Close">&times;</button></div>
    <div class="modal-body">
      <p class="warn-text">This DELETES <code>reports/kill-switch.halt</code>. The agent will be allowed to start and, if you run it with <code>--auto-execute</code>, to send orders on qualifying ENTER_LONG signals to the demo account. The kill switch can be re-armed at any time. Demo account only. The event is recorded in the ledger.</p>
      <label class="field"><span>Type RESUME TRADING to authorize</span><input id="clearHaltPhraseInput" type="text" autocomplete="off" spellcheck="false" placeholder="RESUME TRADING"></label>
      <div class="modal-actions"><button class="btn btn-secondary" id="cancelClearHaltBtn" type="button">Cancel</button><button class="btn btn-secondary" id="confirmClearHaltBtn" type="button" disabled>RESUME TRADING</button></div>
    </div>
  </div>
</dialog>

<div id="toast" class="toast"></div>

<footer class="footer"><span>GOLD BOT · read-only trading dashboard + guarded executor + local feedback log (no MT5, no ledger writes) · loopback only · no scope changes</span><span>Risk exits are veto-immune. The Kill Switch button flattens positions and halts the bot via the guarded CLI.</span></footer>

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


def render_feedback_page() -> str:
    body = f"""
<header class="state-strip feedback-strip" role="banner">
  <div class="brand">
    <span class="logo"></span>
    <div class="brand-text">
      <h1>GOLD BOT</h1>
      <p>Quantamental Agentic Desk · Feedback &amp; Issues</p>
    </div>
  </div>
  <div class="controls">
    <a class="btn btn-primary" href="/" title="Back to the live trading dashboard">← Dashboard</a>
    <span class="chip chip-neutral"><span class="chip-label">LOCAL</span><span class="chip-value">FEEDBACK LOG</span></span>
  </div>
</header>

<main class="feedback-page">
{_FEEDBACK_BODY}
</main>

<div id="toast" class="toast"></div>

<footer class="footer"><span>GOLD BOT · feedback log · loopback only · no MT5, no ledger writes, no scope changes</span><span>Copy any item as Markdown and hand it to the agent.</span></footer>

<script>
{_FEEDBACK_JS}
</script>
"""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="Gold bot feedback and issues - bugs, improvements and ideas, copied out as Markdown tickets.">
<title>Gold Bot - Feedback &amp; Issues</title>
<style>{_CSS}</style>
</head>
<body>
{body}
</body>
</html>"""
