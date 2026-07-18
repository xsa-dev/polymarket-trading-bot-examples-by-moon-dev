# Spread-Harvest Maker — RESEARCH 🌙
*Every number below is real: my own logs, plus 184 stink-bid fills re-resolved against Coinbase candles. Zero synthetic data.*

## Bot Name
**Spread-Harvest Maker** (`spread_harvest_maker.py`)

## One-Line Thesis
In verified coin-flip windows where the book has gone WIDE (ask sum ≥ 1.10), a resting
dog-side bid at 0.40–0.48 buys a ~50/50 shot at a discount — the one maker shelf the
repo's fill data does NOT condemn.

## The three exhibits

### Exhibit 1 — wide books in coin flips are real (dog_sniper_log.csv, June 8–14)
The scanner logged 1,648 windows. In 17 of them — one week — the underdog's ask sat at
**0.60–0.68 with coa ≤ 0.43** (near-tie physical state). Both asks ≥ 0.60 means a ≥ 20c
combined spread with a ~50/50 true state. Nobody was quoting the middle. Sample rows:
coa 0.0006–0.44, cushions of a few dollars, dog asks 0.60/0.62/0.64/0.67/0.68.

### Exhibit 2 — the maker graveyard (why cheap and high are both dead)
| maker style | source | result |
|---|---|---|
| 89c high-favorite maker | v5_89c_maker_log.csv | **0 fills / 275 windows** (240 gate-skips, 35 armed-no-fill) |
| Directional favorite maker (AI) | fable_5min_maker_log.csv | 20 resolved entries, 70% win @ 0.68 avg → **+2.9% EV, ~breakeven** |
| 95c volatile-window taker | v4_volatile_log.csv | 8 entries @ 0.95 avg → 87.5% win, **-$3.00** (scraping is dead) |
| Cheap stink bids ≤ 0.35 (all signal bots) | 184 fills, candle-resolved | **32–35% win → -8% to -48% EV** |

The toxicity gradient is the design input: ≤ 0.35 fills were poison, 89c never fills,
0.68 is breakeven. What's left is the middle.

### Exhibit 3 — mid-price fills were NOT toxic (the same 184 re-resolved fills)
| fill band (all signal bots pooled) | n | win % | avg px | EV per $1 |
|---|---|---|---|---|
| ≤ 0.35 (macd/cvd/liq pooled) | ~120 | 32–35% | 0.31–0.34 | -8% to -48% |
| **0.40–0.50 (macd+cvd+liq pooled)** | **21** | **57.1%** | **0.44** | **+30%** |
| (of which liq-only 0.40–0.50) | 8 | 50.0% | 0.445 | +12.3% |

Honest caveat, in bold because it matters: **those were signal-backed taker-era fills,
not coin-flip maker quotes.** The prior is +EV but the experiment is unrun. That's why
the bot's logging is the product.

## The gates (as built)
1. coa = |spot − strike| / ATR4 ≤ 0.40 (strike = market openPrice; ATR4 = mean range of
   last 4 completed 1-min bars, Binance/Coinbase).
2. ask_sum = best_ask_up + best_ask_down ≥ 1.10.
3. Quote = min(dog_best_bid + 0.01, 0.48), floored at 0.40, strictly < dog ask.
   Post-only GTC, T-120 → T-30, one per window.
4. Cancel instantly on coa > 0.60 / ask_sum < 1.05 / T-30 / rollover.

## Sizing
$5 flat. Daily stop -$30. Kill switches: after 30 fills, win rate < 50% at avg fill ≥
0.44 → toxic, retire. Fewer than 2 quoteable windows/day for a week → spreads tightened,
retire. Fills clustering right after coa expansions (coa_at_fill ≫ coa_at_quote in the
CSV) → the gate failed, retune.

## Key risks (no sugar)
1. **Adverse selection, unanswered.** A resting bid fills when someone wants OUT. If the
   coin flips that fill us are the ones about to break, this loses slowly at $5 a pop —
   and the CSV will show it (coa_at_fill vs outcome, from day one).
2. **Fill rate may be ~zero.** The 89c maker proved high quotes never fill; mid quotes in
   wide books are unproven. The wide-book gate targets exactly the windows where MMs are
   absent, which is also where fill odds are best — that's the hypothesis.
3. **Wide books may be a thin-week artifact.** The 17-window exhibit came from one June
   week. The eval CSV logs ask_sum for EVERY window, so the opportunity rate is measured
   live from the first hour.

*— compiled for Moon Dev 🌙, July 18, 2026*
