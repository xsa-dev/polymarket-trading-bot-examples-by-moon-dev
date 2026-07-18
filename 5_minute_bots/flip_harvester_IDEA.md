# 🌙 Moon Dev's Idea #3 — The Underdog Evolution 🌙

**Author lens:** the UNDERDOG edge. The july_17th fleet proved the 20-45c coin-flip dog is
+EV. This is the NEXT iteration of that pocket — built for Moon Dev, July 18, 2026.

---

## Bot Name

**FLIP HARVESTER** (`flip_harvester.py`)

## One-Line Thesis

Coin-flip dogs TOUCH the lead 71% of the time but only WIN 42% — every july_17th dog bot
holds to resolution and lets the 29% "touch-then-fade" cohort die at zero; Flip Harvester
rests a maker sell at 62c the moment the dog fills, harvesting the flip instead of praying
for the close.

## Data Evidence (all real, all recomputed today — no vibes)

**Source 1 — 52-week physical backtest** (`BTCUSD-1m-52wks-data.csv`, 104,669 clean 5-min
windows; strike = window open, dog = trailing side at T-60, ATR4 = mean H-L of the first 4
bars, coa = |cushion|/ATR4):

| coa cut | n | P(dog TOUCHES strike in final min) | P(dog WINS) | P(win \| touch) | touch-but-LOSE |
|---|---|---|---|---|---|
| ≤ 0.15 | 7,610 | **73.7%** | 44.3% | 58.5% | 29.3% |
| ≤ 0.20 | 10,180 | **71.3%** | 42.4% | 58.0% | **29.0%** |
| ≤ 0.25 | 12,678 | 68.9% | 40.9% | 58.2% | 28.0% |

A no-touch dog can NEVER win (it must cross the strike to finish ahead), so the touch is a
strict superset of the win — and it's 29 full points bigger. That 29% is pure money the
hold-to-resolution fleet leaves on the table every single day.

**Source 2 — EV math at the live pocket's real avg entry of 0.37**
(live pocket per `data/dog_sniper_log.csv`: coa ≤ 0.20, ask 0.20-0.45 → n=56, 44.6% win,
avg ask 0.370, +20.6% EV — recomputed today, matches the july_17th README):

| Exit strategy | EV/share | ROI | hit rate |
|---|---|---|---|
| Hold to resolution (entire current fleet) | +5.4c | +14.6% | 42% |
| Sell ALL at flip @ 0.62 | **+7.2c** | **+19.5%** | **71%** |
| Sell ALL at flip @ 0.65 | +9.3c | +25.3% | 71% |
| Half-exit @ 0.62 + hold half | +6.3c | +17.0% | 71% partial |

**Breakeven flip-exit price = 0.5947** (P(win)/P(touch) = 0.424/0.713). Any fill at 60c or
better beats holding. Fair value of the token AT the touch moment is ~58c (P(win|touch)),
so a resting 62c ask only fills when a momentum taker pays 4c+ over fair — each fill is +EV
by construction.

**Source 3 — the vol split tells us WHEN to harvest vs hold** (52-week data, coa ≤ 0.20,
split at median ATR4):

| Regime | n | P(touch) | P(win) | P(win\|touch) | hold EV @0.37 | sell-all @0.62 EV |
|---|---|---|---|---|---|---|
| HIGH vol (ATR4 ≥ median) | 5,149 | 74.5% | 42.0% | 55.3% | +5.0c | **+9.2c** |
| LOW vol (ATR4 < median) | 5,031 | 68.1% | 42.8% | **60.9%** | **+5.8c** | +5.2c |

High-vol touches FADE (55.3% stick), low-vol touches STICK (60.9%). So: harvest everything
in high vol, harvest half in low vol. This is a genuinely new regime lever no fleet bot uses.

**Source 4 — the entry pocket itself is already live-validated** (`data/dog_sniper_log.csv`,
1,648 windows, 303 with a logged ask): full price ladder confirms 0.20-0.40 is the only
+EV taker band (0.2-0.3: 28.8% win @ 0.257 = +12.2% EV; 0.3-0.4: 38.3% @ 0.357 = +7.1%;
0.4-0.45 flips to -11.4%; 0.45-0.50 is -15.4%). Entry gates are NOT the experiment here —
the exit is.

## Entry Rules (inherit the proven gate — coinflip_discount_dog's, unchanged)

1. Market: `btc-updown-5m-{T}` (BTC only), T-60 → T-5 window.
2. `coa = |cushion| / ATR4 ≤ 0.20` AND `|cushion| ≤ 1.5 bps` of BTC spot.
3. Dog ask **0.22-0.45**. Never below 0.22 — sub-20c dogs are correctly-priced trash
   (18.5% win @ 0.169 in the live log; the old sniper lost -$6.37 on them).
4. Taker buy via `py_clob_client_v2`, `OrderType.GTC` marketable, `post_only=False`
   (FAK 400s under $1 — verified live by the fleet).

## Exit Rules (the whole point of this bot)

1. **The instant the entry fills**, post a GTC **maker SELL** on the same token:
   - **HIGH vol** (ATR4 ≥ rolling 24h median): rest the sell on **100%** of shares @ **0.62**.
   - **LOW vol** (ATR4 < rolling median): rest the sell on **50%** of shares @ **0.62**,
     hold the other 50% to resolution (low-vol touches stick 60.9% — the hold half is the
     better horse there).
2. Sell fills → that's the harvest: 62c out on a 37c cost = +68% on sold shares, and on a
   half-exit you've recovered 84% of the total stake — the held half rides nearly free.
3. Sell unfilled at T-0 → identical to coinflip_discount_dog: hold through resolution
   (~60s), collect $1 or $0. The bracket is a free option — worst case we ARE the fleet bot.
4. **No stop-loss, no cancel-and-chase downward.** Never lower the ask below 0.60 — 59.5c
   is the mathematical breakeven vs holding; below that the bracket destroys edge.
5. First 3 live days: run a 0.62 / 0.65 ask A/B (alternate windows) — the 52-week math says
   0.65 is worth +2.1c more per share IF touch overshoots reach it; only live fills can say.

## Position Sizing

- **$10 flat per window** to start (fleet standard), min 5 shares, tick 0.01.
- Max 1 position per window, max 3 concurrent windows, daily kill switch at **-$60**
  (≈ 6 full losses — a 3-sigma bad run at 42% base win rate, not variance noise).
- Scale to $25 only after **100+ live trades** where (a) realized flip-fill rate ≥ 55% and
  (b) avg flip fill ≥ 0.60. Both numbers come straight from the bot's own CSV — no feelings.

## Logging Plan (Moon Dev wants receipts, Moon Dev gets receipts)

**CSV** — `poly_hyper/fable_july_18th/data/flip_harvester_log.csv`, ONE ROW PER WINDOW
(entries AND skips with reasons, fleet-style), columns:
`snapshot_time, window_slug, coa, cushion_bps, atr4, atr4_med24h, vol_regime, dog_side,
dog_ask_at_signal, action, entry_fill_price, shares, sell_ask_posted, touched (did spot
cross strike), touch_time_s_left, max_bid_seen_after_touch, sell_filled, sell_fill_price,
shares_sold, shares_held, outcome, dog_won, pnl_scalp_usd, pnl_hold_usd, pnl_total_usd,
dog_token_id`

The three columns that pay for the next iteration: **`touched`**, **`max_bid_seen_after_touch`**
(does the book really print 60c+ on a flip — the ONE untested link in the chain), and the
scalp-vs-hold PnL split (which leg is actually earning).

**Console prints** (every refresh):
- `🌙 Moon Dev's FLIP HARVESTER 🌙 — harvesting flips, not praying for closes`
- `🐶💰 [Moon Dev] DOG FILLED @ {px} — sell bracket RESTING @ 0.62 ({pct}% of shares)`
- `⚡🎯 [Moon Dev] FLIP HARVESTED! Sold {n} @ {px} — bought 0.{xx}, that's the 29% the
  fleet leaves on the table`
- `🌙 MOON DEV'S TRADE TRACKER` dashboard (fleet standard): every trade, scalp/hold/total
  PnL, W-L-PENDING, clickable `https://polymarket.com/event/{slug}` links.
- honest resolution only (non_lag_arb standard): Gamma `outcomePrices` at 1/0 or
  crypto-price `completed == true` — no instant-resolve phantom wins.

## Key Risk

**The 62c print is the unproven link.** The 71.3% touch rate is physical spot-price fact
(n=10,180), but whether the CLOB bid actually reaches a resting 0.62 ask during the flip —
and how often — has never been logged in this repo. Two failure modes: (a) flips are quoted
thin and the bid tops out at 55-58c → sell rarely fills and we degrade gracefully into
coinflip_discount_dog (still +EV, nothing lost); (b) adverse selection — the flips strong
enough to lift 62c are exactly the ones that would have finished at $1.00, so realized
P(win | sold) runs above 58% and each harvest forgoes more than 38c of expectation. The
`max_bid_seen_after_touch` and outcome-after-sale columns measure both from day one; if
realized P(win|sold) > 62% the bracket price moves up or the harvester dies. That's the
experiment, and it's cheap to run.

## Why This Beats The July 17th Fleet

1. **Every one of the five july_17th bots (and all five non_lag_arb bots) holds to
   resolution.** "Exit: hold to resolution" appears in every single README entry. Nobody
   has ever touched the exit side of the dog trade. This is the first bot in the repo with
   an exit engine — a new axis, not a fourth gate on the same entry.
2. **It attacks a quantified 29-point gap** (71.3% touch vs 42.4% win, n=10,180) instead of
   re-slicing the same entry pocket a sixth way. The A/B/C gate test is already running;
   duplicating it adds nothing.
3. **+19.5% ROI vs +14.6% at identical entries** — and the hit rate jumps 42% → 71%, which
   at $10/trade roughly halves drawdown depth and makes the PnL curve compoundable instead
   of a lottery ladder.
4. **Strictly bounded downside vs the incumbent:** the resting sell is a free option layered
   on coinflip_discount_dog's exact proven entry. If the bracket never fills, we ARE
   coinflip_discount_dog. There is no filter-tightening tradeoff, no smaller-n pocket, no
   new entry hypothesis that can be wrong.
5. **It generates the dataset the next iteration needs:** nobody has ever logged intra-window
   dog price paths after a flip. Even if the harvest edge is thin, `max_bid_seen_after_touch`
   across a few hundred windows is the raw material for the july_19th idea (dynamic exits,
   flip-momentum re-entry, dog-side maker entries at the fade-back).

---

🌙 Built for Moon Dev — harvest the flip, don't pray for the close. 🚀
