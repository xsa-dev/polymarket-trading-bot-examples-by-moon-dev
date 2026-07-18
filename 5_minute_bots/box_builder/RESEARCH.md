# Idea 2 — Maker / Microstructure Angle

Built by Moon Dev 🌙 (Fable maker-microstructure agent, July 18)

---

## Bot Name

**BOX BUILDER** — the early-window two-sided maker (`5min_box_builder.py`)

## One-Line Thesis

Quote post-only bids on **BOTH** UP and DOWN in the first half of each 5-min BTC
window and only ever pay a combined `bid_UP + bid_DOWN <= 0.94` — when both legs
fill, the pair redeems for exactly $1.00 at resolution, so adverse selection stops
being the enemy and becomes the thing that pays us.

## Evidence (from code lessons + real log stats)

All numbers below were computed with pandas from Moon Dev's actual logs — nothing invented.

**1. Directional maker fills carry ~9c of hidden adverse selection (fable maker log).**
`data/fable_5min_maker_log.csv`: 82 windows, 21 fills, 20 resolved → **70.0% win
rate at avg entry 0.7095** — breakeven for a binary is the entry price, so the bot
netted **-$0.95** (dead even). The killer detail: **losing fills averaged entry
0.647 vs 0.736 for winners**. The book hands you a "discount" precisely when your
side is dying. A completed box is direction-neutral (payoff = 1.00 − combined
cost), so this poison becomes profit: whoever panic-sells a leg into us cheap is
building our box.

**2. Deep one-sided bids in the final minute NEVER fill (v5 log).**
`data/v5_89c_maker_log.csv`: 275 windows, **35 armed under the 99.88% gate
(coa≥2, ≥15bps), ZERO fills** at 0.89. Logged `dog_ask_at_lock` was 0.02–0.03,
i.e. the favorite trades 0.97–0.98 at lock — an 0.89 bid sits 8–9c off-market and
is untouchable. Meanwhile the fable maker, which quoted **early** (T-240), got a
**57% fill rate (21/37 armed)**. Lesson: maker fills live in the wide, two-way
first half of the window, not the collapsed 1–2c book of the last 60 seconds.

**3. Post-only chasing a moving book is broken (error log).**
`data/fable_5min_order_errors.log`: **249 rejects, 100% of them**
`invalid post-only order: order crosses book` — repricing to the best bid every
10s during momentum gets rejected exactly when you want the fill. Box Builder
quotes **static, resting** bids inside a wide early spread (no chasing), and any
box-completion order that must cross uses the marketable-GTC pattern that
v6/dog-sniper already proved (real FAK gets 400'd).

**4. Version-evolution lesson (v1→v3→v4→v5→v6): the maker edge cannot live on the
favorite side.** v1 (rest 0.95 on the decided side) lost to flips; v3's
consolidation gate still lost because the truly consolidated windows are priced
>0.95 so only the toxic ones filled; v4 was a declared small-sample fluke test
(10 entries, 2 with the pre-fix 0.0000-entry logging bug); v5's fix (deeper bid)
never filled; v6's fix (become a taker) abandoned making entirely. Every version
tried to make a directional favorite bet passively. Box Builder is the first one
where the maker discount itself — not the direction — is the product.

**5. The coa signal is real and free for the bailout path.**
`poly_hyper/data/backtest_95c_filters.py` on 52wks / ~104k real windows: baseline
favorite win rate ~87%; `coa>=2.0 & cushion>=15bps` → **99.88%** (~18.5/day).
We reuse it only to decide what to do with a stranded single leg.

## Entry Rules

1. Track `btc-updown-5m` windows (same rollover plumbing as v5/v6). Active quoting
   period: **T-300 to T-150** (first half only). No new quotes after T-150.
2. At window open, pull both books. **Arm only if** `best_ask_UP + best_ask_DOWN >= 1.03`
   (a genuinely wide market — verified live, never assumed).
3. Place **post-only GTC bids on BOTH tokens**, joining each side's best bid, but
   capped so `bid_UP + bid_DOWN <= 0.94` (target ≥6c per box). If joining both
   best bids breaks the cap, back both off symmetrically in 1c ticks.
4. Resting bids are **static** — reprice at most once per 20s and only if a bid is
   >2c behind its best bid AND the 0.94 cap still holds (kills the 249-reject
   chase problem).
5. **Completion ladder** — the moment one leg fills at `p1`:
   - Raise the other leg's bid to `min(best_bid_other, 0.97 - p1)` (still ≥3c locked).
   - If `ask_other <= 0.99 - p1`, **lift it** with a marketable GTC (v6 pattern)
     — take a guaranteed ≥1c box instead of risking a naked leg.
6. One box per window, max. Both filled legs are held to resolution — a completed
   box pays $1.00/pair no matter what BTC does.

## Exit Rules

- **Completed box:** hold to expiry, redeem. PnL per pair = `1.00 − (p1 + p2)`, locked.
- **Stranded leg at T-90** (one fill, no completion): compute live coa/cushion
  (Binance 1m bars, Coinbase fallback — same code as v5/v6):
  - If our filled leg IS the coa-favored side with `coa >= 1.0` → hold naked to expiry.
  - Otherwise → **cut**: sell at best bid immediately (scratch/small loss beats
    riding the wrong side of a flip — the fable log's 0.647-entry losers are
    exactly this trade).
- Cancel ALL resting orders at **T-10**. Never carry an open order into rollover.

## Position Sizing

- Equal shares both legs. Start at the exchange floor: **5 shares/leg (~$2.35/leg
  at 0.47)**, i.e. under $5 total risk per window.
- Hard cap 20 shares/leg until 100 windows are logged.
- Scale only if the log shows: both-fill rate ≥ 50% AND stranded-leg net PnL ≥ −$0.02/leg.
  Both numbers come straight out of the CSV — no vibes-based sizing.
- One box per window means worst-case daily exposure is bounded and known.

## Logging Plan

CSV: `data/box_builder_log.csv` — **one row per window**, even skips (v5 discipline),
with the v5 logging fixes baked in (entry price never 0.0000, pnl scored on what
we actually hold, ties resolve UP per Chainlink):

`snapshot_time, window_slug, spread_sum_at_open, bid_up, bid_dn, action, up_fill_px,
dn_fill_px, box_cost, box_locked_pnl, stranded_side, stranded_px, coa_at_t90,
bailout_action, outcome, won, pnl_usd`

Console prints (Moon Dev wants his name everywhere):
- `🌙 Moon Dev BOX BUILDER | quoting UP 0.46 / DOWN 0.47 | cap 0.94 | spread_sum 1.05`
- `🧱 Moon Dev leg filled: DOWN @ 0.44 — hunting completion <= 0.53`
- `📦 BOX COMPLETE for Moon Dev: cost 0.91 -> locked +$0.09/pair 🔒`
- `⚠️ Moon Dev stranded leg at T-90 | coa 0.4 -> CUTTING at bid`
- Session footer every window: boxes built, both-fill %, locked PnL, stranded PnL — signed `Built by Moon Dev 🌙`

## Key Risk

**One-legged inventory in a trending window.** If BTC runs one way from the open,
the losing side's leg fills and the winning side never comes back to our capped
bid — we end up long the dog with no box. This is the same adverse selection that
killed v1–v5, now confined to a single failure path. Mitigations: the completion
ladder (pay up to 0.99 − p1, even as a taker), the T-90 coa bailout, the cut rule,
and the fact that an early fill near ~0.45 is close to a fair coin (unlike a
0.65–0.95 late fill), so a stranded leg is near-scratch in expectation, not a
19-wins-erased bomb. The both-fill rate is the one unknown — that's why the bot
logs it from window one and stays at 5 shares until the CSV proves it.

## Why This Beats Past Bots

1. **It's the first non-directional maker.** v1/v3/v4/v5/fable all made a
   directional favorite bet with a passive order; the logged result of that whole
   family is breakeven-to-negative (fable: 70.0% wr vs 70.95% breakeven; v1/v3
   flip losses; v5 zero fills). Box Builder's payoff is `1 − cost`, independent of
   UP/DOWN — the only thing being harvested is the spread, which is the only thing
   the logs show a maker actually receives.
2. **It quotes where fills exist.** 57% fill rate quoting early (fable) vs 0/35
   quoting deep late (v5). Box Builder lives entirely in the wide first half.
3. **It inverts adverse selection.** Getting picked off cheap on a leg used to be
   the loss mechanism; here it's cheap box inventory — the counterparty's panic is
   the edge.
4. **It respects every established finding:** no late favorite buying (that's
   -EV, fav wins ~87% priced ~95c), no sub-20c dog holding beyond the bailout
   rule, and it doesn't touch the 20–45c dog-flip pocket — a stranded leg is cut
   or coa-justified, never a dog thesis.
5. **It reuses only proven plumbing:** v5/v6 window rollover + Binance/Coinbase
   bars + Chainlink tie rule, v6's marketable-GTC crossing pattern, v5's honest
   per-window logging.

Built by Moon Dev 🌙
