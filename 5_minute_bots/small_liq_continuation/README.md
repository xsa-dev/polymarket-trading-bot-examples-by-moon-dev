# 🌊 Small-Liq Continuation, the cheap-seat cascade

*Part of Moon Dev's 5-minute bot fleet. Not plug-and-play, not financial advice, not a
money printer. Built live on YouTube. Questions → https://moondev.com/t/polymarket-github*

## What it does, in plain english

When $25K-$500K of leveraged BTC positions get liquidated inside two minutes, that's
forced flow, and forced flow tends to keep going through the end of a 5-minute window.
The big-cascade version of this idea already exists (`liq_cascade_chaser`, which buys the
strong cascades at 50 to 85c). This bot hunts the OTHER pocket the logs found: the smaller
liquidation tier, where the continuation side is still cheap, 30 to 45 cents, because the
move isn't dramatic enough to have been priced yet. It buys that side at market and holds
to resolution.

## Why it exists (the autopsy)

My March signal bots never logged outcomes, only FILLED/CANCELLED, so I re-resolved
every fill against real Coinbase 5-minute candles and graded them honestly:

* **Liq-signal fills at 0.30 to 0.40: 48.8% win at a 34.1c average → +43% EV** (n=41),
  the best cell in the entire order-flow dataset.
* Control group, same mechanic with MACD/CVD signals: 18 to 31% win → **-8% to -48% EV**.
  Same entry style, different signal, so the edge is the LIQUIDATION signal, not the
  fill mechanic. That's the whole reason this bot exists.
* Sub-0.30 liq fills: 16.7% win. Toxic. Cheap is not cheerful.
* Above 0.45 the market has priced the move, that's `liq_cascade_chaser`'s zone, and
  this bot deliberately stays out of it (and skips ≥ $500K events entirely so the two
  never double up on the same cascade).


## The rules it lives by

* **$25K-$500K** of one-sided BTC liquidations in the trailing 2 minutes, dominant side
  wins. Longs rekt → buy DOWN. Shorts rekt → buy UP.
* Continuation-side ask **0.30 to 0.45 only**, **60 to 240 seconds** left, taker. One entry
  per window.
* **≥ $100K** in the burst → 1.5x size kicker (that tier measured +29% EV).
* **≥ $500K → skip.** That's the big bot's trade.
* Hold to resolution. $5 flat base, daily stop at −$30. Honest deferred resolution,
  W / L / PENDING as three numbers, P&L counts resolved trades only.

## The honest risk

The +43% number is **n=41**, and those fills were resting *bids*, this bot is a *taker*
and will pay 1 to 3c more, so the realistic live edge is lower, maybe much lower. The $25K
tier's raw directional accuracy was never proven on its own (the 58.8% liq-direction stat
is pooled across all sizes). This is a small-sample hypothesis with a good control group,
not a proven edge, the eval CSV logs the full liq tape every 6 seconds precisely so the
direction question gets answered with live data. **I have not cracked the 5-minute
market.**

## Running it (at your own risk)

Needs: `.env` in the repo root (see `.env_example`), `py_clob_client_v2`, AND a real-time
liquidation feed, mine comes from the Moon Dev API (`MOONDEV_API_KEY`, code path from
Moon Dev's machine, you'll adapt both). `PAPER_MODE` flag at the top, ships LIVE FIRE,
flip to True for paper. Logs the full tape to `data/small_liq_continuation_eval.csv` and
fills to `data/small_liq_continuation_trades.csv`, entries AND skips, with reasons.

*Built live on YouTube by Moon Dev 🌙, use at your own risk.*
