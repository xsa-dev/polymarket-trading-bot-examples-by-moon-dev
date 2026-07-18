# Small-Liq Continuation 30-45 — RESEARCH 🌙
*Every number below is real: my own bot logs, re-resolved against Coinbase BTC-USD 300s candles. Zero synthetic data.*

## Bot Name
**Small-Liq Continuation 30-45** (`small_liq_continuation.py`)

## One-Line Thesis
The $25K–$500K liquidation tier, standalone, taker, ONLY in the 0.30–0.45 price band —
the hole between the cheap toxic fills (< 0.30) and the priced-in cascades (> 0.45).

## Methodology (why these numbers can be trusted and the old ones couldn't)
The March signal bots logged `FILLED`/`CANCELLED` but never win/loss. Every fill from
`liq_stink_poly_only_trades.csv` (86), `macd_signal/hist_5min_trades.csv` (61) and
`cvd_5min_trades.csv` (37) was re-resolved against real Coinbase 300s BTC candles
(log timestamps are US Eastern; +4h → UTC → floored to the 300s window; winner =
close vs open of that candle). 184/184 fills resolved. No gamma, no guessing.

## The cell (liq fills, ground-truthed)
| pocket | n | win % | avg px | EV per $1 |
|---|---|---|---|---|
| ALL liq fills pooled | 86 | 34.9% | 0.307 | +13.7% |
| **0.30–0.40** | **41** | **48.8%** | **0.341** | **+43.2%** |
| 0.40–0.50 | 8 | 50.0% | 0.445 | +12.3% |
| 0.20–0.30 | 18 | 16.7% | 0.253 | -34.2% (toxic) |
| 0.15–0.20 | 8 | 0.0% | 0.182 | -100% (toxic) |
| liq size $25K–$100K | 31 | 35.5% | 0.275 | +29.4% |
| liq size < $25K | 55 | 34.5% | 0.325 | +6.2% |

## The controls (why the signal is the edge, not the mechanic)
Same entry style (resting bids), different signals, same resolution method:
| signal source | n | win % | EV |
|---|---|---|---|
| MACD fills, all | 61 | 30.5% | ~-8% |
| CVD fills 0.30–0.40 | 22 | 18.2% | -47.8% |
| **LIQ fills 0.30–0.40** | **41** | **48.8%** | **+43.2%** |

If cheap mid-band fills were inherently good, the MACD/CVD rows would be green too.
They're not. The liquidation signal is what pays; everything below 0.30 is adverse
selection no matter the signal.

## The boundaries this bot respects
- **< 0.30: never.** Toxic across every signal source.
- **> 0.45: not ours.** `liq_cascade_chaser` owns the strong-cascade 0.50–0.85 pocket;
  mega-cascade events (≥ $500K) are skipped entirely so the two bots never stack on the
  same event. Above ~0.45 the continuation is priced anyway (+11% at best).
- **Kicker tier:** $25K–$100K measured +29.4% EV (n=31) — so ≥ $100K gets 1.5x, not a
  bigger threshold.

## Entry rules (as built)
1. Moon Dev API liquidation feed, trailing 2 minutes, BTC only.
2. Dominant side ≥ $25K and < $500K. LONG_LIQ dominant → buy DOWN; SHORT_LIQ → buy UP.
3. Continuation-side ask 0.30–0.45, 60–240s left, taker (marketable GTC). One per window.
4. ≥ $100K dominant → 1.5x size. ≥ $500K → SKIP (the big bot's trade).

## Sizing
$5 flat base ($7.50 with kicker). Daily stop -$30. Kill switch: < 40% win rate over 100
in-band live trades (breakeven at 0.40 avg), or median fill migrating above 0.44.

## Key risks (no sugar)
1. **Maker-origin bias.** The evidence fills were resting bids that filled when the
   market moved against them — a taker pays 1–3c more. Realistic live EV is below +43%.
2. **n=41 in the money cell.** One bad week erases it. First 100 trades are revalidation.
3. **Direction at small size is assumed, not proven** — the 58.8% liq-direction stat is
   pooled across all sizes; the bot's eval CSV logs the full tape every 6s so the
   $25K–$500K tier gets its own verdict from live data.
4. **Feed latency** — a slow liq feed turns the 0.30–0.45 band into the >0.45 band.

*— compiled for Moon Dev 🌙, July 18, 2026*
