# 📖 Spread-Harvest Maker, paid to wait in wide books

*Part of Moon Dev's 5-minute bot fleet. Not plug-and-play, not financial advice, not a
money printer. Built live on YouTube. Questions → https://moondev.com/t/polymarket-github*

## What it does, in plain english

Sometimes the BTC 5-minute book goes WIDE, the market makers step away and both sides'
asks add up to $1.10 or more, leaving a 10 to 20 cent hole in the middle of a genuine
coin-flip window. Takers get slaughtered in those windows. This bot does the opposite:
it rest a single bid inside the hole, on the underdog side, at best-bid-plus-a-penny,
hard-banded between 0.40 and 0.48, and only when the window is a verified coin flip.
If someone sells into the bid, we own a 50/50 shot at a discount. If the flip breaks or
the spread collapses, the quote is pulled instantly. It never, ever crosses the spread.

## Why it exists (the autopsy)

My maker history is a graveyard, and the graveyard is the research:

* The **89c maker** quoted high favorites: **0 fills in 275 windows**. Dead.
* The **stink-bid signal bots** bid cheap (≤ 0.35): fills won **32 to 35%**, a resting bid
  fills exactly when the market is moving against it. Textbook adverse selection, and I
  have the receipts (184 fills re-resolved against real candles).
* But the **mid-price** fills told a different story: the 0.40 to 0.50 band went **12/21 =
  57% win at a 44c average**, NOT toxic. And my June dog-scanner logged **17 windows in
  one week** where the dog ask sat at 0.60 to 0.68 in near-tie windows, the wide books are
  real, and nobody in the repo has ever quoted inside them.

So the bot is the synthesis: coin-flip gate + wide-book gate + mid-price band + instant
cancel.

## The rules it lives by

* **Coin-flip gate:** coa = |cushion| / ATR4 ≤ **0.40** (spot vs the market's own strike,
  vol-normalized).
* **Wide-book gate:** best_ask_up + best_ask_down ≥ **1.10**, the MMs are gone or the
  quote doesn't happen.
* **Quote:** dog-side best_bid + $0.01, hard band **0.40 to 0.48**, strictly below the dog
  ask (never cross), post-only, T-120 → T-30. One quote per window.
* **Instant cancel** on: coa > 0.60 (flip broke), ask sum < 1.05 (spread collapsed),
  T-30 (time up), or window rollover.
* Filled → hold to resolution. $5 flat, daily stop −$30. Honest deferred resolution:
  W / L / PENDING as three numbers.

## The honest risk

**Adverse selection is the entire question, and it is unanswered.** The 57%-win mid-band
fills were *signal-backed* fills, not coin-flip quotes, this exact quote style has never
been run, and its fill rate might be near zero, or its fills might be just as toxic as
the cheap stink bids were. This bot is deliberately built as the experiment that answers
it: every fill logs coa-at-quote vs coa-at-fill vs mid-at-fill vs outcome, so a toxic
pattern shows up in the CSV within days. Treat it as a data-collecting instrument with a
plausible edge attached, not as a proven earner. **I have not cracked the 5-minute
market.**

## Running it (at your own risk)

Needs: `.env` in the repo root (see `.env_example`), `py_clob_client_v2`. No paid feeds,
BTC price comes from public endpoints (Hyperliquid mark, Binance/Coinbase 1-minute
candles). `PAPER_MODE` flag at the top, ships LIVE FIRE, flip to True for paper. Logs
every window's gate values to `data/spread_harvest_eval.csv` (the ask_sum spread dataset
this repo has never had) and the full quote lifecycle to
`data/spread_harvest_trades.csv`.

*Built live on YouTube by Moon Dev 🌙, use at your own risk.*
