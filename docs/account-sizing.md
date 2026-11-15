# Account sizing for XAUUSD at FBS-Demo

This document records the minimum safe account size for the standard
`0.01` lot on the 100 oz/lot XAUUSD contract. It exists because the owner
asked about trading with **$50**. The numbers below are not a preference;
they are the broker contract, the ATR stop, and the bot's own leverage cap.

## Current inputs (2026-11-15, last closed bar)

```text
close = 3,880.26
ATR(14) = 6.45
1.5x ATR stop = 9.68
0.01 lot = 1 oz
risk per 0.01 lot = 9.68
notional of 0.01 lot = 3,880.26
```

## Minimum capital for 0.01 lot

| Profile | Cap | Max notional | Fits 0.01 lot? | Stop risk % |
|---|---:|---:|---|---:|
| $50 | 50:1 | 2,500 | **No** | 19.36% |
| $50 | 5:1 | 250 | **No** | 19.36% |
| $100 | 50:1 | 5,000 | Yes | 9.68% |
| $1,000 | 5:1 | 5,000 | Yes | 0.97% |
| $776.05 | 5:1 | 3,880.26 | Yes (exact) | 1.25% |

## Conclusion

A **$50** XAUUSD account cannot trade the broker minimum of `0.01` lot at
FBS-Demo without exceeding even the high-risk `micro_100` leverage cap of
**50:1**. It would require **77.6x** leverage, and a single 1.5x ATR stop
would remove **~19%** of the account. The bot correctly refuses.

The smallest live override the owner has already approved is `micro_100`
($100): 50:1 cap, 11% per-trade cap, 12% daily halt, 25% max drawdown. It is
**HIGH RISK**, **not promotion-eligible**, and not a substitute for a
micro-lot broker.

The owner explicitly approved `micro_50` on 2026-11-15: 100:1 leverage cap
(after 80:1 failed the current 4,114 notional), 25% per-trade cap, 25%
minimum-lot risk guard, 12% daily loss halt, and 20% max drawdown
flatten/halt. It is HIGH RISK, not promotion-eligible, and remains a stop from
blowing the account: one 1.5x ATR stop (~19-21% of $50) flattens and halts.

| Option | Cap | Notional needed | Stop risk | Promotion-eligible |
|---|---:|---:|---:|---|
| $50 micro_50 | 100:1 | 3,880.26-4,114.11 | ~19-21% | No |
| $100 micro_100 | 50:1 | 3,880.26 | ~9.7% | No |
| $1,000 standard_1k | 5:1 | 3,880.26 | ~0.97% | Yes (after gates) |

The $50 profile is an owner override, not a recommendation. It has been
created in `config/profiles/micro_50.yaml` but has **not** been run against a
live broker order.
