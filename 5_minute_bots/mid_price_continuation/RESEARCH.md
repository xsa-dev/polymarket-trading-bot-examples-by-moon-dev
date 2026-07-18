# Mid-Price Continuation — RESEARCH 🌙
*Every number below came out of my own real trade logs. Zero synthetic data.*

## Bot Name
**Mid-Price Continuation 40-55** (`mid_price_continuation.py`)

## One-Line Thesis
The lag-arb family was benched for its expensive bands — but pooled across all eight
versions, the 0.40–0.55 leading-side entries were +15–30% EV the whole time. Take ONLY
those cells, hard-capped, as a taker.

## Methodology
Merged `data/lag_arb_trades.csv` + all V-versions (V1/V3/V4/V5/V6/V9/V10/V11), deduped on
(timestamp, asset, entry_price, shares) → 1,372 unique resolved live trades, 318 on BTC
(Dec 30 – Jan era). These bots bought the physically-leading side of the 5-min updown
markets; outcomes (WIN/LOSE) were logged at resolution. Cut by entry price band ×
direction × minutes-left.

## The band table that produced the bot (BTC, all versions pooled)
| entry price | n | win % | avg px | EV per $1 | pnl |
|---|---|---|---|---|---|
| 0.20–0.30 | 4 | 75.0% | 0.288 | +161% (n=4 — noise) | +$17.95 |
| 0.30–0.40 | 16 | 43.8% | 0.369 | +18.6% | +$24.15 |
| **0.40–0.50** | **128** | **60.2%** | **0.462** | **+30.2%** | **+$87.89** |
| **0.50–0.60** | **40** | **62.5%** | **0.542** | **+15.3%** | **+$11.59** |
| 0.60–0.70 | 14 | 57.1% | 0.651 | -12.3% | -$5.89 |
| 0.70–0.80 | 25 | 64.0% | 0.770 | -16.9% | -$14.27 |
| 0.80–0.90 | 45 | 80.0% | 0.862 | -7.1% | -$3.53 |
| 0.90–0.95 | 24 | 95.8% | 0.928 | +3.3% | +$3.36 |
| 0.95–1.00 | 22 | 100.0% | 0.981 | +1.9% | +$1.32 |

The cliff at 0.60 is the whole story: win rate keeps RISING with price (64% at 0.77!),
but never fast enough to pay for the price. EV peaks at 40–55c.

## Supporting cuts
- **Direction:** at 0.40–0.50 both directions held — DOWN 60.0% (n=80), UP 60.4% (n=48).
  No asymmetry filter needed in-band. (UP 0.30–0.60 pooled: 61.8% win, +34% EV, n=76,
  positive 5 of 6 days — but DOWN at 0.30–0.40 went 0/5, so the bot stays in the
  0.40–0.55 band where both sides were honest.)
- **Time:** mid-price entries (0.30–0.70) with 2–5 minutes left won 80–100% per
  time bucket (n=3–15 per bucket, small) → the bot's 2:00–5:00 band.
- **ITM depth:** win rate is monotone in depth-through-strike (0–0.05% → 57.8%;
  0.10–0.15% → 72.7%; ≥0.15% → 100%, small n) — but deeper costs more. 0.05% minimum
  with the 0.40–0.55 price gate is the EV-optimal compromise.
- **Per-version consistency:** 6 of 8 bot versions were net-positive on BTC. The
  counterexample: V5 won 76.9% of trades and still lost -$12.90 — it entered too high.
  Win rate without price discipline is worthless; that's the bot's founding lesson.
- **The dead zones, confirmed twice:** 0.80+ entries made +1.0% ROI on $1,344 of volume
  across the family's life. Scraping the last cents of a favorite is structural -EV.

## Entry rules (as built)
1. Strike = the market's own openPrice (Polymarket crypto-price endpoint). Spot =
   Hyperliquid BTC mark.
2. Spot ≥ 0.05% through strike, 120–300s left, leading-side ask 0.40–0.55, ask depth ≥
   our shares. Taker (marketable GTC — FAK/FOK 400s under $1 crossable, verified live).
3. One entry per window. Hold to resolution.

## Sizing
$5 flat. Daily stop -$30. Kill switch: < 55% win rate over 100 in-band live trades, or
median fill above 0.54 (band migrated). Scale only after the live log confirms the
historical 60%.

## Key risks (no sugar)
1. **Stale-ask risk — the #1 suspect.** Logged asks were snapshots. Live taker fills run
   1–2c worse, which cuts the +30%/+15% edges roughly in half. The bot logs
   `signal_ask` vs `fill_price` slippage from trade one; that column is the verdict.
2. **Era risk.** Data is Dec–Jan. This exact family was benched after live
   underperformance — this bot is the argument that the benching punished the wrong
   cells. It might still be wrong.
3. **Thin books mid-window** can skip-trade whole sessions; the eval log's
   `ASK_TOO_HIGH` count shows whether the band is drying up.

*— compiled for Moon Dev 🌙, July 18, 2026*
