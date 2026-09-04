# 🌙 5-Minute Bots, Moon Dev's BTC 5-Minute Market Fleet

Hey, these are 5-minute bots, for Polymarket's "Bitcoin Up or Down" 5-minute markets.

**The 5-minute market is very hard to crack. I have not cracked it yet.** But here are
the bots I've built trying. Nobody is ever going to give you a plug-and-play,
make-money-overnight bot, that's not a real thing, and anyone promising it is lying to
you. Since I'm doing all of this hard work live on YouTube, I might as well share it with
you to save you some time.

Every one of these came out of mining my own real trade logs, including the logs of bots
that went breakeven or lost. The research docs in each folder show the actual numbers.
Treat these as **starting points and lesson archives**, not answers.

Questions? Join the Zoom call: **https://moondev.com/t/polymarket-github**

## The fleet

### 📦 [`box_builder/`](box_builder/), the two-sided maker
Quotes lowball bids on BOTH Up and Down early in the window, capped at $0.94 combined.
A filled pair redeems for exactly $1.00 no matter what BTC does, it harvests the spread,
not the direction. Born from the autopsy of five straight directional maker bots that
went breakeven-to-negative.

### 🌊 [`liq_cascade_chaser/`](liq_cascade_chaser/), the signal survivor
Liquidations were the only signal in my logs that actually predicted direction (58.8% on
187 real signals, CVD and MACD graded as coin flips). Buys the liquidation-aligned side
as a taker at 0.50 to 0.85 in minutes 0 to 3. The old stink-bid entry was what lost money, so
this one deletes it.

### 🔄 [`streak_snapper/`](streak_snapper/), the streak fader
After 4+ consecutive same-direction 5-minute windows stretched more than 3x the hourly
ATR, the next window reversed 54.3% of the time across 52 weeks of real 1-minute BTC
data. Thin edge, high frequency, limit entries only.

### 🌉 [`corridor_collector/`](corridor_collector/), the two-market play
The 15-minute market's final 5 minutes contains a 5-minute market that resolves on the
SAME close. Buying 15m-leader + 5m-opposite never pays less than $1 per pair, and pays
$2 when the close lands in the corridor between the two opens (41.3% of the time in the
sweet zone, per 34,918 real windows).

### ⚡ [`mid_price_continuation/`](mid_price_continuation/), the cells that paid
The lag-arb family was benched as a failure, but pooled across all eight versions the
0.40 to 0.55 leading-side entries were +15 to 30% EV the whole time (n=168 of 318 real BTC
trades), it was the 60c+ bands that lost. This is the same signal with brutal price
discipline: 0.40 to 0.55 only, hard cap, never chase. A benched bot's second chance, with
the slippage logging that will convict or acquit it.

### 🌊 [`small_liq_continuation/`](small_liq_continuation/), the cheap-seat cascade
The little sibling of `liq_cascade_chaser`: $25K-$500K liquidations (the tier below the
big cascades) with the continuation side still at 0.30 to 0.45. Re-resolving 184 old fills
against real candles showed that exact cell went 48.8% win at 34c → +43% EV (n=41),
while the same fills on MACD/CVD signals lost, the liquidation signal is the edge.
Skips ≥ $500K events so it never doubles up with the big bot.

### 📖 [`spread_harvest_maker/`](spread_harvest_maker/), paid to wait in wide books
The repo's first mid-price maker: when the coin-flip book goes WIDE (both asks sum to
$1.10+, 17 such windows in one June week), it rests a 0.40 to 0.48 dog bid inside the hole
and pulls it the instant the flip breaks or the spread collapses. Cheap stink bids were
toxic (32 to 35% win) and the 89c maker never filled, but mid-price fills went 57%, this
is the experiment that finds out if that holds for real quotes.

### 💣 [`near_liq_trigger/`](near_liq_trigger/), wait for the whale, then wait again
Every big trader's liquidation price is public. This one arms when a **$100k+** BTC
position sits within **0.5%** of getting force-sold, then refuses to trade until somebody
on that same side *actually* gets liquidated for **$5k+** — the first domino. Then it
takes the aligned side and holds to expiry, no exit. Most windows it does nothing.
**No backtest exists** (nobody archives near-liquidation history), so the eval CSV is the
whole deliverable. Also the folder where a feed-latency bug ate an entire live session:
a $715k liquidation the bot never saw because the data arrived 150 seconds late. That
autopsy, and the `--lagcheck` tool that found it, are in the README.

### 💎 [`flip_harvester/`](flip_harvester/), the coin-flip dog's exit engine
Coin-flip underdogs touch the lead 71.3% of the time but only win 42.4% (n=10,180) — a
29-point gap that every dog bot in the fleet leaves on the table by holding to
resolution. This one buys the identical proven dog gate, then rests a maker sell at 0.62
the instant the buy fills: HIGH-vol windows harvest 100% of shares (touches fade back
there), LOW-vol windows harvest half and hold half (touches stick there). Degrades
gracefully into the incumbent hold-to-resolution bot if the sell never fills. Built
straight from `flip_harvester_IDEA.md`'s research, **no live data exists yet on whether
the book ever really bids 62c during a flip** — that's the whole experiment, ships in
`PAPER_MODE = True` until the eval CSV says otherwise.

## Reality check (again, on purpose)

* These run with **$5-$15 per trade** on my end. That sizing is the strategy, small live
  samples, honest logs, scale only what survives. If your first instinct is to 100x the
  size constant, this repo is not going to end well for you.
* Several of these edges came from **small samples over specific weeks**. Markets adapt.
  Market makers adapt. An edge in the logs is a hypothesis, not a promise.
* Every bot logs every window, entries AND skips with reasons, to CSV. If you run one,
  the log is the deliverable. The PnL is just one column of it.
* **Not financial advice. Use at your own risk.** I have not cracked this market. I'm
  showing my work, not selling a solution.

Come hang out and ask questions on the Zoom: **https://moondev.com/t/polymarket-github**

*Built live on YouTube by Moon Dev 🌙*
