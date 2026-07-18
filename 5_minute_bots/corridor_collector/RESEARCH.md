# 🌙 Idea 5 — Market Structure Angle 🌙

**Author:** Moon Dev (research by the market-structure agent, July 18, 2026)
**Universe:** BTC 5-minute + 15-minute up/down markets on Polymarket, BTC ONLY.

---

## Bot Name

**CORRIDOR COLLECTOR** — `corridor_collector.py`

---

## One-Line Thesis

The 15-min market's final 5 minutes contains a 5-min market with the SAME close — buying
15m-leader + 5m-opposite is a pair that can NEVER pay less than $1 and pays $2 whenever the
close lands in the corridor between the two opens (a 41% event in the sweet zone), so any
live pair sum meaningfully below its physical fair value of ~$1.41 is +EV with a hard floor.

---

## The Structure (why the floor is airtight)

A 15-min window [T, T+900] and the 5-min window [T+600, T+900] resolve at the SAME instant
off the SAME BTC close P15. Only the reference points differ:

- `P0`  = BTC open at T (15m strike)
- `P10` = BTC open at T+600 (5m strike)
- `P15` = the SHARED close

If the 15m leads UP at T+600 (P10 > P0), buy **15m-UP + 5m-DOWN** (mirror for DOWN lead):

| Where P15 lands | 15m-UP | 5m-DOWN | Pair payout |
|---|---|---|---|
| P15 ≥ P10 (leader runs) | WIN | lose | $1 |
| **P0 < P15 < P10 (THE CORRIDOR)** | **WIN** | **WIN** | **$2** |
| P15 ≤ P0 (full reversal) | lose | WIN | $1 |

There is no outcome where both legs lose. Fair pair value = 1 + P(corridor).
Moon Dev's existing `5min_15min_arb.py` only fires when the pair costs < $0.98 — free money
that essentially never exists (the bot never even produced a log file). The unlock: the pair
is worth WAY more than $1, so you can pay ABOVE $1 and still print.

---

## Data Evidence (all real — BTCUSD-1m-52wks-data.csv, 34,918 15-min windows, 52 weeks)

Study run July 18, 2026 with pandas on the real 1-min candles. No synthetic data.

1. **The floor held in 34,918 of 34,918 windows.** Zero windows where both legs lose.
   Full reversal (5m leg saves you) happens 17.3% of the time — the hedge is real, not
   theoretical.

2. **Corridor probability by 10-min lead size (|P10−P0| in bps):**

   | lead (bps) | n | P(corridor) | fair pair value |
   |---|---|---|---|
   | 0–2 | 4,543 | 7.2% | $1.07 |
   | 2–5 | 6,404 | 21.9% | $1.22 |
   | 5–10 | 8,578 | 32.6% | $1.33 |
   | 10–15 | 5,522 | 40.5% | $1.41 |
   | 15–20 | 3,387 | 44.0% | $1.44 |
   | 20–30 | 3,423 | 46.4% | $1.46 |
   | 30–50 | 2,082 | 49.7% | $1.50 |

3. **Vol-scaling sharpens it (lead / ATR14 at T+600):** lead/ATR 2–3 → corridor 43.5%
   (n=7,334); lead/ATR 3–5 → 48.9% (n=6,412). A lead that's big RELATIVE to current vol is
   the highest-value corridor.

4. **SWEET ZONE: lead 5–30 bps AND lead/ATR14 ≥ 1.0 → n=18,649, P(corridor)=41.3%,
   fair pair sum $1.413.** That zone shows up ~51 times per day — no starvation.

5. **The 5m-opposite leg is a pure coin flip: 49.7–50.1% win rate in EVERY lead bin.**
   The last-5-min direction is independent of the first-10-min lead. So near the 5m open,
   any 5m-opposite ask ≤ ~0.50 is fairly bought — all pair edge then comes from the 15m
   leg pricing plus the corridor bonus.

---

## Entry Rules

Work each 15-min window once, in the FIRST 90 seconds of its final 5-min market
(T+600 → T+690, while the 5m-opposite still trades near 50c and the corridor table applies):

1. Compute `P0` (Binance 1m open at T) and `P10` (open at T+600). Lead = |P10−P0| in bps;
   pull ATR14 from the last 14 1-min bars.
2. **Zone gate:** lead 5–30 bps AND lead/ATR14 ≥ 1.0. Outside the zone → log and skip.
3. Read `ask15` = best ask on the 15m LEADER side, `ask5` = best ask on the 5m OPPOSITE side.
4. Look up `p_corridor` from the (lead bps × lead/ATR) table above (hardcode the bins).
5. **Fire when `ask15 + ask5 ≤ 1 + p_corridor − EDGE`** with `EDGE = 0.08` (e.g. fair $1.41
   → pay at most $1.33). Sanity caps: `ask5 ≤ 0.55`, `ask15 ≤ 0.93`.
6. Take BOTH legs as marketable GTC, equal shares, back-to-back — reuse the leg-verify +
   orphan-flatten logic from `5min_15min_arb.py` (that part of the old bot is solid).
7. One pair per 15-min window, max.

## Exit Rules

- **Hold both legs to resolution (≤5 min).** No stops — the $1 floor IS the stop.
- Worst case at max price $1.33: lose 33c per pair (−25%). Corridor case: collect $2
  (+50%). At the measured 41.3% corridor rate, EV = 1.413 − 1.33 = **+8.3c per pair
  (+6.2% ROI) with loss capped structurally**, not by an exit that can slip.
- Orphan (one leg fills): retry once, then flatten immediately at the bid — never carry a
  naked leg.

## Position Sizing

- Flat **$10 per pair** (~$5/leg equivalent, 5-share minimum respected) for the first 300
  pairs. Max loss per pair ≈ $2.50 — a worst-week is survivable while the live `sum` data
  accumulates.
- After 300 resolved pairs: if realized corridor rate within 5 points of table and ROI > 0,
  step to $25/pair. Never exceed 5 concurrent open pairs (only possible across assets/never
  here — one window at a time keeps it to 1).

## Logging Plan

CSV: `poly_hyper/fable_july_18th/data/corridor_collector_log.csv` — one row EVERY 15-min
window (entries AND skips), fields:
`time, T15, slug15, slug5, P0, P10, lead_bps, atr14, lead_atr, p_corridor_table, ask15,
ask5, live_sum, fair_sum, gate_pass, executed, fill15, fill5, hedged, P15, corridor_hit,
payout, cost, pnl, skip_reason`

The `live_sum` column on skipped windows is the whole research payoff — it finally measures
how the market actually prices this pair vs. the physical table (the one number 52 weeks of
candles can't give us).

Console prints (Moon Dev wants his name everywhere):
- `🌙 Moon Dev's CORRIDOR COLLECTOR — hunting the $2 corridor...`
- `🎯 Moon Dev: lead 12.4bps (2.1x ATR) → corridor 42% → fair $1.42 | live sum $1.31 → COLLECT!`
- `💰 Moon Dev DOUBLE WIN — close landed in the corridor! +$X.XX`
- `🧱 Moon Dev floor save — full reversal, 5m leg paid. -$0.XX only.`
- Resolution via definitive data only (crypto-price `completed==true` / Gamma outcomePrices),
  W / L / PENDING shown separately — the honest-P&L standard from the july_17th fleet.

## Key Risk

**The market may never sell the pair cheap enough.** The physical fair value ($1.41 in-zone)
is rock solid, but if the CLOB consistently quotes the 15m leader rich enough that
`live_sum` sits at ~$1.40+, the gate never opens and the bot earns nothing (that's a data
win, not a money loss). Secondary risks: leg risk on the two takes (mitigated by
retry+flatten), and strike-source basis — the table uses Binance 1-min opens while
Polymarket resolves off its own crypto-price feed; a few-bps basis mostly cancels here
because BOTH markets share the same close, but sub-2bps-lead windows are excluded partly
for this reason.

## Why This Beats Past Structure Bots

- **`5min_15min_arb.py`** demanded `sum < $0.98` — a locked arb that basically never exists
  (zero logged windows). Corridor Collector prices the SAME structure correctly: the pair's
  fair value is $1.07–$1.50 depending on lead, so the +EV region is ~40c wider than the old
  bot believed. It's the difference between waiting for free money and buying discounted
  merchandise with a hard floor.
- **Lag-arb / strike-cross family (benched):** those were naked one-leg directional bets on
  the leader — the exact 80c+ dead zone that made +1% ROI on $1,344. Every Corridor
  Collector trade is hedged; the worst case is capped at entry, structurally.
- **It doesn't touch the other agents' pockets:** no naked favorite (banned, −EV), no
  20–45c dog coin-flip cell, no sub-20c trash. The edge lives in the CROSS-market relative
  price — a lane nothing else in the repo trades.
- It self-audits: even at zero fills it produces the first-ever dataset of live 15m+5m pair
  sums vs. physical fair value, ~51 in-zone windows a day.

🌙 Built for Moon Dev — the floor is a feature, the corridor is the payday. 🚀
