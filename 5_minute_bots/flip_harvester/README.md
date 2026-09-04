# 💎 Flip Harvester, the coin-flip dog's exit engine

*Part of Moon Dev's 5-minute bot fleet. Not plug-and-play, not financial advice, not a
money printer. Built live on YouTube. Questions → https://moondev.com/t/polymarket-github*

**Status: built from `flip_harvester_IDEA.md`, ships in `PAPER_MODE = True`.** It reuses
a proven entry gate, but nobody in this repo has ever run the exit side live. Read the
"Key Risk" section before you touch `PAPER_MODE`.

## What it does, in plain english

Every dog bot in this fleet buys the coin-flip underdog cheap and holds it to
resolution, win or lose. The 52-week backtest says that leaves money on the table: a
genuine coin-flip dog **touches** the strike (briefly leads) 71.3% of the time, but only
**wins** 42.4% of the time. A dog that never touches the strike can't possibly win, so
that 29-point gap is real — it's the "touched, then faded back to zero" cohort, and
holding to resolution captures none of it.

Flip Harvester buys the exact same dog, at the exact same gate, as the rest of the
fleet's coin-flip pocket. The only thing it changes: the instant the buy fills, it rests
a maker sell at **0.62** on the same shares. If the dog flips ahead and someone pays up
for it, that's the harvest — 62c out on a ~37c entry. If the flip never comes, the sell
never fills and the position rides to resolution exactly like every other dog bot in the
repo. It's a free option layered on the proven trade, not a new bet.

## The data behind it (from `flip_harvester_IDEA.md`, all real, all recomputed)

* **52-week physical backtest**, 104,669 clean 5-min windows: at `coa <= 0.20`
  (`coa = |cushion| / ATR4`, the same gate the fleet's dog bots already use), the dog
  **touches** the strike 71.3% of the time (n=10,180) but only **wins** 42.4%.
  `P(win | touch)` = 58.0%, so `touch − win` = 29.0 points of pure unharvested value.
* **Breakeven flip-exit price = 0.5947** (`P(win)/P(touch)` = 0.424/0.713). Any resting
  sell filled at 60c or better beats holding, by construction.
* **EV comparison at the live pocket's real avg entry of 0.37**: hold-to-resolution is
  +5.4c/share (+14.6% ROI, 42% hit rate). Sell-all at 0.62 on the flip is +7.2c/share
  (+19.5% ROI, **71% hit rate** — the touch rate, not the win rate, because every touch
  is now a chance to harvest).
* **Vol regime is a real lever, not noise**: split at the median ATR4, HIGH-vol touches
  FADE back (only 55.3% stick), LOW-vol touches STICK (60.9%). So HIGH-vol windows
  harvest 100% of shares on the flip; LOW-vol windows harvest half and hold the other
  half, because the held half has better odds there than the sold half's fixed 62c.

## The rules it lives by

**Entry** (identical to the fleet's proven coinflip-dog gate, nothing new here):
* `coa = |cushion| / ATR4 <= 0.20` — cushion measured from the first 4 closed 1-minute
  bars (`bars 0-3`, real Binance klines, Coinbase fallback), same math as `box_builder`'s
  live COA signal.
* `|cushion| <= 1.5 bps` of spot — genuinely undecided, not just low-ATR.
* Dog ask **0.22 to 0.45**. Below 0.22 is correctly-priced trash (18.5% win in the live
  log). Above 0.45 the market has already priced the move.
* Fires in the **T-60 to T-5** window (bars 0-3 are always closed by T-60, so the signal
  is real the moment it's checkable — no lookahead).

**Exit** (the new part, the whole reason this bot exists):
* The instant the entry fills, rest a **post-only GTC sell at 0.62** (A/B'd against 0.65
  by window parity for the first live batch — the 52-week math says 0.65 is worth
  +2.1c/share *if* touches actually overshoot it, but only live fills can confirm that).
* **HIGH vol** (this window's ATR4 ≥ the rolling 24h median): sell **100%** of shares.
* **LOW vol** (ATR4 < the rolling median): sell **50%**, hold the rest to resolution.
* **Never lower the ask below 0.60, ever.** No stop-loss, no chasing down. If the book
  already ran past our target when we try to post (a post-only reject "would cross"),
  we take the better bid immediately instead of resting below 62c — a strictly better
  outcome, never a worse one.
* Unsold shares at window close just ride to resolution — worst case, this bot degrades
  gracefully into the exact bot it's built on top of.

## Position sizing & risk

* **$10 flat per window**, 5-share minimum, max **3** unresolved windows stacked at once.
* Daily kill switch at **-$60** (≈ 6 full losses at the base 42% win rate — a real bad
  run, not variance noise).
* Scale up only after **100+ live trades** show a realized flip-fill rate ≥ 55% and an
  average flip fill ≥ 0.60 — both numbers come straight out of this bot's own CSV.

## Key Risk (read this before flipping `PAPER_MODE`)

**The 0.62 print is the one unproven link in the whole chain.** The 71.3% touch rate is
a physical fact about spot price crossing a strike (n=10,180, no market microstructure
involved). Whether Polymarket's actual order book ever *bids* 62c during a real flip has
never been logged anywhere in this repo. Two ways this goes wrong:

1. **Flips are quoted thin.** The bid tops out at 55-58c and the sell rarely fills — we
   degrade into the incumbent hold-to-resolution dog bot. Nothing lost, nothing gained.
2. **Adverse selection.** The flips strong enough to actually lift a 62c ask are exactly
   the ones that would have gone on to win at $1.00 anyway — realized `P(win | sold)`
   comes in *above* 58%, and every harvest gives up more than 38c of expectation versus
   just holding.

`touched` and `max_bid_after_touch` are logged on **every** entered window specifically
to answer this before the bot is ever trusted with more than $10. Check the eval CSV
before you even think about `PAPER_MODE = False`.

## Logging

* `data/flip_harvester_eval.csv` — every window: entries AND skips, with the coa/cushion
  numbers that decided it.
* `data/flip_harvester_trades.csv` — every fill: entry price, exit ask, `touched`,
  `max_bid_after_touch`, shares sold vs held, and the scalp/hold/total P&L split (which
  leg is actually earning is the whole point of splitting them).
* `data/flip_harvester_atr_history.json` — rolling 24h ATR4 samples, feeds the vol-regime
  lever (defaults to the safer HIGH/100%-harvest behavior until it has 20+ samples).

***
*Built by Moon Dev 🌙, from `flip_harvester_IDEA.md`. Not financial advice, no
warranties, use at your own risk.*
