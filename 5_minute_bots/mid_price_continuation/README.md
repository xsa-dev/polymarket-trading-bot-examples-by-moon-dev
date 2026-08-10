# ⚡ Mid-Price Continuation, the cells that paid

*Part of Moon Dev's 5-minute bot fleet. Not plug-and-play, not financial advice, not a
money printer. Built live on YouTube. Questions → https://moondev.com/t/polymarket-github*

## What it does, in plain english

When BTC punches through its 5-minute strike price, the side that's winning should cost
more than ~50 cents immediately, but Polymarket's book is slow, and the leading side
keeps trading at 40 to 55 cents with 2 to 5 minutes left. This bot buys the leading side at
market price, ONLY inside that 40 to 55c band, and holds to resolution. The historical logs
say that band won ~60% of the time, a 60% shot sold at 47c is a real edge. It refuses
everything above 0.55, because that's exactly where this strategy's ancestors went to die.

## Why it exists (the autopsy)

I pooled all eight versions of my old lag-arb trade logs, 1,372 real resolved trades,
318 on BTC, and cut them by entry price. The result was embarrassing for me:

* Entries at 0.40 to 0.50: **60.2% win, +30% EV** (+$87.89)
* Entries at 0.50 to 0.60: **62.5% win, +15% EV**
* Entries at 0.60 to 0.70: **-12% EV**. At 0.70 to 0.80: **-17% EV**. At 0.80 to 0.90: **-7% EV**.

The family was benched as a failure, but the failure was the expensive bands, and the
40 to 55c cells were quietly profitable the whole time. One version won 76.9% of its trades
and still LOST money because it entered too high. So this bot's entire personality is
price discipline: the same signal, the right cells only, hard cap at 0.55, no chasing,
ever.

## The rules it lives by

* BTC spot ≥ **0.05%** through the strike (the market's own openPrice, not my guess).
* Leading side best ask **0.40 to 0.55 only**. Above 0.55 = dead zone, skip and never chase.
* **2:00 to 5:00 minutes** left in the window. One entry per window. Taker (marketable GTC).
* Hold to resolution. $5 flat per trade, daily stop at −$30.
* Resolution is honest-by-construction: only the finalized oracle counts, pending trades
  are never assumed, unfilled orders are marked NO_FILL, and the scoreboard shows
  W / L / PENDING as three separate numbers.

## The honest risk

**This family already failed live once.** The most likely killer is stale asks: the
historical 40 to 55c entries were snapshots, and live fills come in 1 to 2c worse, that alone
can eat half the edge. The data is also from December-January; market makers may have
tightened the mid band since. That's why the bot logs `signal_ask` vs `fill_price` on
every trade, if the slippage is real, the CSV will say so within a week, and the kill
switch (< 55% win rate over 100 in-band trades) retires it. This is a benched bot's
second chance with better rules, not a proven winner. **I have not cracked the 5-minute
market.**

## Running it (at your own risk)

Needs: `.env` in the repo root (see `.env_example`), `py_clob_client_v2` (the V2 SDK, V1
order placement is dead), python3.11 on Moon Dev's machine. `PAPER_MODE` flag at the top,
ships LIVE FIRE, flip to True for paper. Logs every window (entries AND skips with
reasons) to `data/mid_price_continuation_eval.csv`, and fills with slippage + resolution
to `data/mid_price_continuation_trades.csv`.

*Built live on YouTube by Moon Dev 🌙, use at your own risk.*
