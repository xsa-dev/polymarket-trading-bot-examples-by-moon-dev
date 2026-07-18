# Idea 4: Signal Bot — LIQ CASCADE CHASER 🌙
*Built for Moon Dev by resolving every past signal-bot trade against real Coinbase 1m candles. All numbers below are real — zero synthetic data.*

## Bot Name
**Liq Cascade Chaser** (`liq_cascade_chaser.py`)

## One-Line Thesis
Buy the liquidation-aligned side of the 5-min BTC market as a TAKER at 0.50–0.85 in the first ~3 minutes — liquidation cascades continue to window close ~95% of the time, but Polymarket only prices the aligned side at 50–85c that early, and the old stink-bid entry was the only thing losing money.

## Data Evidence (real numbers)

### Methodology
Every logged signal from the March 2026 signal-bot fleet was resolved against real Coinbase Exchange BTC-USD 1m candles (timezone offset +4h verified by matching the BTC prices embedded in the MACD logs — 0.07% median price error). Window outcome = close of minute 4 vs open of minute 0 of each UTC-aligned 5-min bucket. The 52-week study uses `BTCUSD-1m-52wks-data.csv` (104,738 complete 5-min windows).

### 1. Which signal actually predicted direction? Only liquidations.
| Bot | Signals | Directional accuracy |
|---|---|---|
| CVD divergence (`cvd_5min_trades.csv`) | 704 | **51.4%** (coin flip) |
| MACD vs signal line (`macd_signal_5min_trades.csv`) | 548 | **52.4%** (coin flip) |
| MACD histogram (`macd_hist_5min_trades.csv`) | 128 | **52.3%** (coin flip) |
| MACD filter \|hist\|>50 (`macd_filter_5min_trades.csv`) | 12 | 83.3% — but ~4 signals/day and its stink bid **never filled once** |
| **Liquidations** (`liq_stink_poly_only_trades.csv`) | **187** | **58.8%** (60.0% LONG_LIQ, 58.0% SHORT_LIQ) |

### 2. What killed the past bots: the stink-bid entry (adverse selection), not the signal.
Across all 196 stink-bid fills fleet-wide, win rate was **34.2%**. The autopsy:
- Cancelled (unfilled) liq signals won **79.2%** of the time; filled ones won **34.9%**. The 30%-pullback bid only fills when the market is moving against the signal — you systematically miss the winners and own the losers.
- Fills by stink price (all bots): **sub-20c → 5.9% wr** (n=17, -$72.74), 20–30c → 22.2% wr (n=36, -$58.01), **30–40c → 36.9% wr (n=111, +$224.11)**, 40–50c → 56.5% (n=23). Confirms the fleet finding: sub-20c is trash; everything below 30c on a signal stink is -EV.
- The liq bot still netted **+$57.38 on 86 fills** despite this, because its adverse move is *forced flow* (a cascade overshoot that mean-reverts), not informed flow like MACD/CVD pullbacks. Filtered to stink px ≥ 0.30 & liq ≥ $10k it made **+$175.29 on 31 fills, 58.1% wr**. The signal survives even the worst entry style.

### 3. The fix, proven on the same real signals: go TAKER, capped at 0.85.
Counterfactual: take every one of the 187 liq signals at market (`signal_price`), hold to resolution:
| Aligned side priced | n | Win rate | EV per share |
|---|---|---|---|
| 0.00–0.30 | 20 | 15.0% | **-7.3c** (skip) |
| 0.30–0.50 | 74 | 43.2% | **-0.9c** (skip) |
| **0.50–0.70** | **53** | **71.7%** | **+11.8c** |
| **0.70–0.90** | **23** | **87.0%** | **+5.8c** |
| 0.90–1.00 | 17 | 100.0% | +5.1c (but violates the no-late-fav rule — capped out) |
Price IS the confirmation filter: when the aligned side trades below 50c, the market disagrees with the liquidation and it loses. 50–85c is the pocket.

### 4. 52-week validation on `BTCUSD-1m-52wks-data.csv`.
- Baseline: window close matches direction of first-2-minute move **73.2%** of the time (n=104,498).
- **Spike windows** (first-2-min move ≥ 0.15% AND ≥ 3x average volume — the candle signature of a liq cascade): continuation to window close **95.0%** (n=1,029, ~3/day). Robust across thresholds: 0.20% → 95.0% (n=697), 0.30% → 95.7% (n=328).
- So a cascade-aligned side is worth ~90-95c fair; buying it at 50–85c is the edge.

## Entry Rules
1. Watch Moon Dev API BTC liquidations (same feed as `liq_stink_bot_poly_only.py`), rolling 2-min lookback.
2. Signal: single-window liquidation total **≥ $10,000** (sub-10k fills won only 24.2%; ≥10k won 45-50% even on stink entries). LONG_LIQ → buy DOWN, SHORT_LIQ → buy UP.
3. Confirmation (both required):
   - Current 5-min window's move so far is ≥ **0.15%** in the liq direction with elevated volume (the 95%-continuation signature).
   - Aligned token best ask is **0.50–0.85**. Below 0.50 = market disagrees, skip. Above 0.85 = edge gone after the ~2-4c taker fee, skip (and we never buy the late favorite — established -EV).
4. Timing: only enter in **minutes 0–3** of the window (min ≥ 4 fills went 0-for-2 in the logs; too late to matter).
5. TAKER market order. No stink bids. Ever. That is the whole fix.
6. Max 1 position per 5-min window.

## Exit Rules
- Hold to resolution. No scratch-outs — at 71-87% win rates, exiting into a cascade's chop only donates spread.
- If order not filled within 5 seconds (book moved through 0.85), cancel and skip the window.

## Position Sizing
- Flat **$15 per trade** (matches liq bot sizing that produced the +$57 live result).
- Expected frequency from logs: ~19 qualifying signals/day in the 0.50–0.90 pocket (76 of 187 over ~4 days).
- Daily stop: -$60 (4 straight losses at max) → shut down for the day, print the loss table so Moon Dev sees exactly what happened.
- Scale to $30/trade only after 100 live trades confirm ≥ 60% win rate at avg entry ≤ 0.75.

## Logging Plan
- CSV: `poly_hyper/data/liq_cascade_chaser_trades.csv` via pandas, one row per signal (including skips with skip reason) — columns: `timestamp, signal_type, liq_usd, btc_move_pct, direction, entry_price, fee_paid, shares, usd, result, window_start_utc, actual_outcome, win, pnl`. Logging `actual_outcome` at resolution time is mandatory — the March fleet never logged outcomes and it took candle forensics to grade it. Never again.
- Console prints, Moon Dev style:
  - `🌙 Moon Dev's Liq Cascade Chaser | $84,203 LONG_LIQ cascade detected | BTC -0.21% into window`
  - `🚀 Moon Dev taking DOWN @ 0.64 x 23 shares ($14.72) — cascade continuation play`
  - `💰 Moon Dev WIN +$8.28 | running: 61.2% wr over 49 trades, +$97.40`
- Rolling stats block every 10 trades: win rate by entry-price bucket and by liq size, so decay is visible immediately.

## Key Risk
**Taker fees + the cascade already being priced.** The 5-min crypto markets charge taker fees (`fee_rate_bps=1000` → roughly `0.10 × min(p, 1-p)` per share ≈ 4c at p=0.60, 2c at p=0.80). That shaves the measured +11.8c/+5.8c edges to roughly +8c/+4c — still positive, but it means the 0.85 cap is hard, and if market makers start pricing cascades correctly (aligned side jumping straight to 0.90+), signal frequency dies before PnL does. Secondary risk: the counterfactual taker table is n=76 in the money pocket over ~4 days of March 2026 — the 52-week candle study de-risks the physics, but live win rate must be re-verified in the first 100 trades. Also assumes Moon Dev API liq feed latency stays under ~10-15s; a slow feed converts minute-1 entries into minute-3 entries and eats the edge.

## Why This Beats Past Signal Bots
1. **It uses the only signal with a proven edge.** Liquidations graded 58.8% directional on 187 real signals; CVD (51.4%, n=704) and MACD (52.4%, n=548) are coin flips — no entry tweak can save a 50/50 signal.
2. **It deletes the one component that lost money.** Every past signal bot used the same 30%-pullback stink bid, which filled at 34.2% wr fleet-wide by construction (fills = signal being invalidated). This bot pays the taker fee to buy the winner *while it's winning*.
3. **The price band replaces hope with confirmation.** Below 0.50 the market voted against the liq signal and it lost (-0.9c to -7.3c/share); the bot only acts when signal and tape agree, at 71.7-87.0% measured win rates.
4. **It respects every established fleet finding**: never buys the 95c late favorite, never touches sub-20c trash, and stays out of the 20-45c dog pocket that belongs to the dog bots.
5. **It grades itself.** Outcome logging at resolution means Moon Dev knows by trade 50 whether the edge is live — the March fleet ran for 11 days without anyone knowing CVD was a coin flip.

*— compiled for Moon Dev 🌙, July 18, 2026*
