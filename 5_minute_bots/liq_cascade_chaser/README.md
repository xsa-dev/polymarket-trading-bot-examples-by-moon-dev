# 🌊 Liq Cascade Chaser — the signal survivor

*Part of Moon Dev's 5-minute bot fleet. Not plug-and-play, not financial advice, not a
money printer. Built live on YouTube. Questions → https://moondev.com/t/polymarket-github*

## What it does, in plain english

When leveraged traders get liquidated on BTC, their positions are force-closed into the
market, which shoves price further in the same direction — a cascade. Cascades tend to
keep going through the end of a 5-minute window (95% continuation in 52 weeks of real
1-minute data when the move is 0.15%+ on 3x volume), but early in the window Polymarket
often still prices the cascade side at only 50–85 cents. This bot buys that side at
market price and holds to resolution.

## Why it exists (the autopsy)

I graded every signal my March fleet ever logged against real candles:

- CVD divergence: **51.4%** directional (704 signals) — a coin flip
- MACD: **52.4%** (548 signals) — a coin flip
- **Liquidations: 58.8%** (187 signals) — the only real signal in the building

And the thing that lost the money was never the signal — it was the **stink-bid entry**
(fills won just 34.2%, because a discount bid only fills when the market is moving
against you). So this bot's entire innovation is deleting that: it pays the taker fee and
buys the winner *while it's winning*.

## The rules it lives by

- ≥ **$10k** of one-sided BTC liquidations in the trailing 2 minutes. Longs liquidated →
  buy DOWN; shorts liquidated → buy UP.
- Tape confirmation: the window has already moved ≥ **0.15%** in the liq direction on an
  elevated tick rate (the 95%-continuation signature).
- Price IS the filter: only enter at **0.50–0.85**. Below 0.50 the market disagrees with
  the liq — historically that loses. Above 0.85 the fee eats the edge (and buying late
  favorites is a proven loser).
- **Minutes 0–3 only.** No stink bids, ever. Unfilled after 5 seconds → cancel and skip.
- Hold to resolution. $15 flat per trade, daily stop at −$60, kill switch if the trailing
  win rate breaks.

## The honest risk

The measured pocket (71.7%–87.0% win rates) came from **n=76 signals over ~4 days** of
March 2026. The 52-week candle study de-risks the physics, but the live win rate has to
re-prove itself in the first 100 trades — that's what the bucket-stats logging is for.
If market makers start pricing cascades correctly, the signal frequency dies. **I have
not cracked the 5-minute market. This is a hypothesis with a logbook.**

## Running it (at your own risk)

Needs: `.env` in the repo root (see `.env_example`), `py_clob_client_v2`, AND a real-time
liquidation + tick feed (mine comes from the Moon Dev API — you'll need your own key or
your own feed). `PAPER_MODE` flag at the top. Logs to `data/liq_cascade_chaser_trades.csv`
and `data/liq_cascade_signals.csv` — entries AND skips, with reasons.

*Built live on YouTube by Moon Dev 🌙 — use at your own risk.*
