# FBS XAUUSD automation — compliance memo (working draft)

Status: **OWNER MUST REVIEW**. This document is a checklist scaffold, not legal advice.
Nothing here authorizes real-money trading. Promotion gates remain 0/7.

## Why this exists
Before any of this could ever graduate from FBS-Demo to real capital, the broker's
client agreement, instrument SERP, and margin policy must be reviewed by the
account owner, not inferred by an agent. This memo lists the questions that review must answer.

## Known facts (verified against the terminal on 2026-11-17)
- Broker: FBS-Demo server, account 106806652, `trade_mode=0` (demo), equity ~998–1002
- Instrument: XAUUSD (Gold Spot, `Forex\Main\XAUUSD`), contract 100 oz, min lot 0.01,
  spread ~23–27 points (floating), swap_long −69.95/point, filling modes FOK+IOC
- Terminal: Algo Trading enabled, Python trade API enabled, `trade_allowed=True`
- MT5 Python API: allowed on this build (demo)
- Feed symbols seen: XAUUSD only; **no USD index symbol** on this broker build
  (DXY/USDX/USDIDX/US-dollar index all absent — risk-corr is skipped)
- No market depth offered on XAUUSD (L2 probes return empty) — fill-probability at
  size cannot be measured from this broker
- Tick history: available; spread pinned at 23.0 points almost always
- Deal history quirk: demo search only returns decisive deals (TODAY or after
  deal `1912818766`); older records truncated at ~2026-10 — not a broker error, just
  what the demo server retains

## The deal problem — two sub-threads that must be resolved
1. **Orphan fills (2026-11-17)**: two `demo-send --override-blocks` invocations by this
   agent each hit the broker but the ledger was left without `orders`/`fills` rows
   because of a foreign-key crash before the intent-first fix. The broker has these
   deals; the ledger does not. They were closed via the kill switch; the ledger
   recorded them as closed `DEMO_CLOSED` positions with computed P&L.
   -> Owner decision pending: backfill `orders`/`fills` for the two orphans from the
   MT5 deal tickets, or accept them as terminal-integrity noise for the demo record.
2. **Deal-vs-ledger audit**: `goldbot reconcile --deals` flags all 3 bot-tagged deals
   as `deals_broker_only` with no matching fills. Any live production use must close
   this gap first (see the web/Git record and the redo drill).

## Compliance questions the owner must answer before ANY live risk
- [ ] Does FBS (current FBS-M** account type) permit API/EA trading on XAUUSD? On what
      leverage? Any instrument-level restrictions on gold?
- [ ] What is the order type — Market Execution only or Instant Execution? Instant
      Execution would add requote risk on every fill — does the EA handle requotes
      explicitly? (This bot only assumes Market Execution.)
- [ ] Margin requirements on XAUUSD at current leverage: exactly how many lots per
      USD 1,000 of margin at (say) 1:30, 1:100, 1:500? Verified against the SERP
      calculator, not the marketing page.
- [ ] Does the broker's T&C contain an arbitrage/latency-arbitrage prohibition that
      would trip on sub-minute order patterns, or spread-scalp filters?
- [ ] Does the broker require pre-notification or approval for any automation on a
      real (non-demo) account?
- [ ] If a forced margin call occurs during a news event, what is the broker's
      liquidation priority? Does the account go into negative-balance protection
      for CFDs on this instrument?
- [ ] Are there instrument exclusions for EAs (e.g., gold CFDs excluded from Hedging
      or must be One-Way)? If hedged mode required, reconcile with XAUUSD long-only
      configuration.
- [ ] WHT/reporting implications for XAUUSD CFD profits from this account type.

## Non-negotiable gates from AGENTS.md that must NOT relax here
- standard_1k promotion-eligible; micro_100 / micro_50 are NOT promotion-eligible and
  are never a path to live capital, even if this memo ever passes
- All 7 promotion gates (§8) must hold before the first real order is placed
- Kill-switch must be armed and tested on live before any real order
- The kill-switch drill has been performed on demo only (2026-11-17). It has not
  been executed on a live account, authorized under any circumstances.

## Decision log
- 2026-11-17: Created this memo stub. No decision yet. Owner to read the FBS
  Client Agreement and the instrument SERP conditions for XAUUSD and fill in the
  checkboxes above; nothing here is an authorization for live trading.
