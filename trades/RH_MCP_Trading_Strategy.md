# Robinhood MCP Dip-DCA Trading Strategy

Status: **design only, not yet implemented or backtested at full fidelity.** This
document captures the strategy as designed in conversation so it isn't lost --
see "Open items before going live" at the bottom before building/running this
for real.

---

## 1. Account setup

- Robinhood **cash account**, **no margin enabled**.
- Funded with **$2,000**.
- Cash accounts are not subject to the Pattern Day Trader rule at all (PDT only
  ever applied to margin accounts), and separately, FINRA/SEC eliminated the
  $25,000 PDT minimum-equity requirement industry-wide effective 2026-06-04
  (SR-FINRA-2025-017), replaced by an intraday-margin-based standard. Robinhood
  has indicated a fast rollout of this change rather than using the full
  18-month (until 2027-10-20) implementation window other brokers get.
- **Real constraint instead of PDT: Good Faith Violations (GFVs).** In a cash
  account, buying with unsettled funds (e.g. same-day sale proceeds -- stock
  settlement is T+1) and then selling that new position before the original
  sale settles is a GFV. 3 GFVs in a rolling 12 months triggers a 90-day
  restriction to settled-cash-only trading. Given how often this strategy
  recycles capital across many tickers in one day, hitting this is plausible
  unless explicitly guarded against.
  - **Mitigation:** keep a portion of the $2,000 as an untouched settled-cash
    buffer that buys draw from first; don't immediately reuse the same day's
    sale proceeds to open a new position that might also close same-day.

## 2. Universe: which stocks to trade

Do **not** use `daily_snapshot.investment_verdict` -- it is *only*
`current_drawdown_pct`-based (`BUY / ACCUMULATE` if drawdown < -15%, `HOLD` if
< -5%, else `MONITOR`; see `pipeline.py` around line 806) and ignores every
other score in the table.

Instead, filter on a real multi-factor combination of stored scores. As
verified live against `drawdown_analyzer.db` on 2026-08-27/28, this query:

```sql
SELECT ds.ticker
FROM daily_snapshot ds JOIN universe u ON u.ticker = ds.ticker
WHERE u.asset_type = 'Stock' AND u.status = 'active'
  AND ds.quality_score >= 60
  AND ds.investment_score >= 55
  AND ds.risk_score <= 55
  AND ds.distress_risk_level = 'Low financial-distress risk'
  AND ds.valuation_tier IN ('Deep Value', 'Fair Value')
  AND (ds.dcf_margin_of_safety_pct BETWEEN -30 AND 150 OR ds.dcf_margin_of_safety_pct IS NULL);
```

produced 14 US-domestic tickers as of the 2026-08-27/28 pipeline snapshot
(foreign private issuers/ADRs deliberately excluded -- they mostly pass only
because `distress_risk_level` comes back "Insufficient data" and
`insider_sentiment_score` defaults to a neutral 50 for them, due to 20-F
annual filing / no Section 16 insider reporting, not because they're actually
lower risk):

```
META, PG, PM, SNDK, BKNG, UBER, NEM, MO, ADBE, INTU, SLB, MPLX, LNG, MDT
```

**This list is not fixed.** The pipeline (`.github/workflows/pipeline.yml`)
runs weekly, refreshing `daily_snapshot` with new prices/filings/scores each
time -- so re-running the query above after every pipeline run will produce a
different 14-or-so tickers as scores drift (names can enter or drop out of
the "Low financial-distress risk" / "Deep Value or Fair Value" / score
thresholds week to week). Treat the query in this doc as the source of truth,
not the specific ticker list printed above, which is only a snapshot example.
The live system should re-run this query on each check cycle's startup (or at
least once per pipeline run) rather than hardcoding the 14 names.

Columns used and why:

| Column | Threshold | Why |
|---|---|---|
| `quality_score` | >= 60 | fundamentals-based composite |
| `investment_score` | >= 55 | composite of quality + valuation + risk + filing-risk + drawdown-opportunity + dividend + insider scores (`compute_composite_investment_score`) |
| `risk_score` | <= 55 | lower = safer |
| `distress_risk_level` | = 'Low financial-distress risk' | excludes both "Elevated"/"Material solvency concerns" AND "Insufficient data" (mostly foreign filers) |
| `valuation_tier` | in (Deep Value, Fair Value) | excludes "Premium / Growth" and "Speculative" |
| `dcf_margin_of_safety_pct` | between -30 and 150, or NULL | sanity bound -- excludes broken/outlier DCF values |

Re-run this query periodically (e.g. weekly, after each pipeline run) to
refresh the 14-name list -- scores change as new filings/prices come in.

## 3. Buy logic: rolling-peak drawdown steps

**Do not** compare each check to the *previous* check's price -- that misses
slow multi-hour bleeds entirely (e.g. -1%/hour for 4 hours never crosses a
single -3% hop even though the cumulative move is -4%).

Instead, track two pieces of state **per ticker**:
- `rolling_peak`: highest price observed since the position was last fully
  flat. Updates upward whenever a new high prints. Never resets just because
  time passed or price ticked down.
- `steps_bought`: how many 3% steps below `rolling_peak` have already been
  bought at, for the *current* peak. Resets to 0 whenever `rolling_peak`
  itself updates to a new high.

On each check (every 30-60 min, or whatever interval RH MCP data supports):
1. If `current_price > rolling_peak`: set `rolling_peak = current_price`,
   `steps_bought = 0`.
2. Compute `pct_down = (current_price - rolling_peak) / rolling_peak`.
3. `target_steps = floor(abs(pct_down) / 0.03)` (whole 3% steps reached).
4. If `target_steps > steps_bought`: buy `(target_steps - steps_bought)` more
   $50 lots at `current_price`, then set `steps_bought = target_steps`.

### Worked examples (verified against real hourly price sequences)

**Slow bleed (the case that breaks "compare to last check"):**

| Time | Price | vs. last-check method | vs. rolling-peak method |
|---|---|---|---|
| 9:30 | $100.00 | peak set | peak=$100, 0% down |
| 10:30 | $99.00 | -1.0%, no trigger | -1.0% from peak, no trigger |
| 11:30 | $98.00 | -1.0%, no trigger | -2.0% from peak, no trigger |
| 12:30 | $97.00 | -1.0%, no trigger | **-3.0% from peak -> BUY** (step 1) |
| 1:30 | $96.00 | -1.0%, no trigger | -4.0% from peak, step 2 needs -6% ($94), no buy |
| 2:30 | $95.00 | -1.0%, no trigger | -5.0% from peak, still no new step |

Old method: zero buys despite a real -5% day. New method: one correctly-timed
buy at 12:30.

**Whipsaw / peak reset:**

| Time | Price | rolling_peak | %_down | steps_bought | Action |
|---|---|---|---|---|---|
| 9:30 | $100.00 | $100.00 | 0% | 0 | none |
| 10:30 | $96.00 | $100.00 | -4.0% | 0->1 | BUY at $96 |
| 11:30 | $102.00 (new high) | **-> $102.00** | 0% | **-> 0** | none |
| 12:30 | $99.00 | $102.00 | -2.9% | 0 | none |
| 1:30 | $98.00 | $102.00 | -3.9% | 0->1 | BUY at $98 |

This mirrors the peak-to-trough logic already used by `drawdown_events` /
`drawdown_summary` in this codebase, just applied at intraday granularity.

## 4. Sell logic: armed trailing profit lock (not a fixed +2% limit)

A fixed "sell the instant +2% is hit" limit order caps upside arbitrarily and
creates an unfilled-order ambiguity if price dips back below the limit before
filling. Instead:

1. Compute `avg_cost = cost_basis / shares` for the open position.
2. Once `current_price >= avg_cost * 1.02` for the first time, **arm** the
   position: start tracking `peak_price_since_armed = current_price`. No order
   is placed yet -- arming is purely internal state.
3. On every later check while armed and **no order has fired yet**: if
   `current_price > peak_price_since_armed`, update
   `peak_price_since_armed = current_price`. Still no order placed -- this is
   just tracking the peak while price keeps climbing (e.g. +2% -> +4% produces
   zero order activity).
4. **Trigger condition:** `current_price <= peak_price_since_armed * (1 - trail_pct)`,
   with `trail_pct = 0.5%`. E.g. if profit peaked at +4%, the trigger fires
   once it pulls back to roughly +3.5%. This is the *first and only* moment an
   order gets placed for this arm cycle -- there is no resting order during
   the run-up, only after the pullback is confirmed. This avoids constant
   cancel/replace churn while price is still climbing.
5. **On trigger:** place a limit sell at the price seen at trigger time. If it
   hasn't filled by the next poll and price has moved further away, cancel and
   replace at the new price (same "stale order" fallback as before, but now
   only active after the trigger fires, not continuously during arming).
6. **Hard floor -- never sell below `avg_cost * 1.02`.** Because the trail
   trigger is computed off `peak_price_since_armed`, and that peak could be
   only marginally above the arm threshold (e.g. armed right at +2.0%, price
   never climbs further before reversing), a 0.5% pullback from a peak that
   low could compute a trigger price *below* the +2% floor (e.g. peak =
   avg_cost * 1.02 exactly -> trail trigger = avg_cost * 1.02 * 0.995 =~
   avg_cost * 1.0149, i.e. only +1.49%). This must never be allowed to place
   an order: the effective sell trigger is
   `max(peak_price_since_armed * (1 - trail_pct), avg_cost * 1.02)` is the
   wrong fix (it would artificially force an early sell); the correct fix is
   to require **both** conditions before placing an order --
   `current_price <= peak_price_since_armed * (1 - trail_pct)` **and**
   `current_price >= avg_cost * 1.02`. If price falls straight through the
   +2% floor without the trail condition ever being satisfied at a qualifying
   (>=2%) price, **no sell is placed at all** -- simply un-arm
   (`armed = False`) and return to monitoring for a fresh +2% cross. The
   position is never sold for less than +2% profit under this rule.
7. Because a *new buy* changes `avg_cost` (and therefore the arm threshold),
   re-check arming/trailing state after every buy -- a new lower-average-cost
   position may need to un-arm if price is now below the new avg_cost * 1.02.
   If a sell order was already resting (post-trigger) when a new buy changes
   `avg_cost`, cancel it and re-evaluate arming from scratch against the new
   `avg_cost`.

### Order placement: self-managed, not a native trailing-stop order

Robinhood natively supports trailing-stop sell orders (percent or $ trail).
However:
- Community Robinhood MCP servers are inconsistent about exposing this order
  type -- `open-stocks-mcp` explicitly removed trailing-stop support;
  `Jimgitsit/robinhood-mcp` is limit-only for options; only
  `kevin1chun/robinhood-for-agents` claims trailing-stop support (and is also
  the only one with a review->confirm->place safety flow).
- The underlying `robin_stocks` library's trailing-stop implementation has
  **known open bugs** in its order payload construction (reported errors like
  "stop market sell order requested, but price provided" -- GitHub issue
  jmfernandes/robin_stocks#462). A silently-failed trailing-stop order is a
  real risk: a position could ride back down to breakeven/loss unnoticed.

**Decision: self-manage the trail in our own polling loop, and use RH MCP only
to place/cancel/replace plain limit orders** we compute ourselves each check --
not to rely on a broker-side native trailing-stop order type. This keeps every
step visible/loggable on our side rather than trusting an unverified
third-party pass-through.

Per-check order management:
- While armed but not yet triggered: no order exists yet -- just update
  `peak_price_since_armed` if a new high prints. Nothing to place or cancel.
- The moment the 0.5% pullback-from-peak trigger fires: place the first limit
  sell, at the price seen at trigger time.
- After that, if the resting limit sell hasn't filled by the next check and
  price has moved further away from it: cancel and replace at the new price.
- If a new buy occurs at any point (changes `avg_cost`): cancel any resting
  sell order and re-evaluate arming from scratch against the new `avg_cost`.

## 5. State to track per ticker (summary)

```
rolling_peak            # highest price since flat; buy-side reference
steps_bought            # 3% steps already bought from rolling_peak
shares, cost_basis      # position size / capital in
avg_cost                # cost_basis / shares
armed                   # bool: has price ever hit avg_cost * 1.02?
peak_price_since_armed  # highest price since arming; sell-side reference
open_sell_order_id      # resting limit sell order id on Robinhood, if any
```

## 6. Open items before going live

- [ ] **No intraday backtest exists for this exact mechanic.** Everything
      backtested so far (`dip_dca_backtest.py` in scratch, not committed) used
      *daily* close-to-close data (`price_history` table only stores daily
      closes). The rolling-peak/30-60min-check version described here has
      never been backtested against real intraday data -- we don't have an
      intraday price history source yet.
- [ ] Confirm which Robinhood MCP server to actually connect (none configured
      as of 2026-08-28 -- `claude mcp list` returns empty), and verify it
      supports: real-time/recent quotes, placing limit orders, canceling/
      replacing open orders, and checking order fill status.
- [ ] Decide and implement the settled-cash-buffer approach to avoid GFVs
      (see section 1).
- [ ] Decide the check interval (30 min vs 60 min) -- still a placeholder.
      `trail_pct` is decided: 0.5% pullback from peak profit, order placed
      only once, at trigger (see section 4).
- [ ] This entire strategy still has **no hard stop-loss / max-DCA-depth
      cap** -- in the daily-bar backtest of the (looser, drawdown-only-filtered)
      95-ticker universe, 68% of tickers ended up in a stuck, never-recovered
      open position; the quality-filtered 14-ticker universe did much better
      (29% stuck, all roughly flat) but still has no explicit floor on how far
      DCA-ing down can go for a genuinely broken name.
- [ ] Re-verify Robinhood's actual current day-trading/PDT-transition policy
      directly (support page or account settings) before relying on the
      2026-06-04 rule change -- broker rollout timelines vary.
