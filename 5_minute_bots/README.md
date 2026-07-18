# 🌙 5-Minute Bots — Moon Dev's BTC 5-Minute Market Fleet

Hey — these are 5-minute bots, for Polymarket's "Bitcoin Up or Down" 5-minute markets.

**The 5-minute market is very hard to crack. I have not cracked it yet.** But here are
the bots I've built trying. Nobody is ever going to give you a plug-and-play,
make-money-overnight bot — that's not a real thing, and anyone promising it is lying to
you. Since I'm doing all of this hard work live on YouTube, I might as well share it with
you to save you some time.

Every one of these came out of mining my own real trade logs — including the logs of bots
that went breakeven or lost. The research docs in each folder show the actual numbers.
Treat these as **starting points and lesson archives**, not answers.

Questions? Join the Zoom call: **https://moondev.com/t/polymarket-github**

## The fleet

### 📦 [`box_builder/`](box_builder/) — the two-sided maker
Quotes lowball bids on BOTH Up and Down early in the window, capped at $0.94 combined.
A filled pair redeems for exactly $1.00 no matter what BTC does — it harvests the spread,
not the direction. Born from the autopsy of five straight directional maker bots that
went breakeven-to-negative.

### 🌊 [`liq_cascade_chaser/`](liq_cascade_chaser/) — the signal survivor
Liquidations were the only signal in my logs that actually predicted direction (58.8% on
187 real signals — CVD and MACD graded as coin flips). Buys the liquidation-aligned side
as a taker at 0.50–0.85 in minutes 0–3. The old stink-bid entry was what lost money, so
this one deletes it.

### 🔄 [`streak_snapper/`](streak_snapper/) — the streak fader
After 4+ consecutive same-direction 5-minute windows stretched more than 3x the hourly
ATR, the next window reversed 54.3% of the time across 52 weeks of real 1-minute BTC
data. Thin edge, high frequency, limit entries only.

### 🌉 [`corridor_collector/`](corridor_collector/) — the two-market play
The 15-minute market's final 5 minutes contains a 5-minute market that resolves on the
SAME close. Buying 15m-leader + 5m-opposite never pays less than $1 per pair, and pays
$2 when the close lands in the corridor between the two opens (41.3% of the time in the
sweet zone, per 34,918 real windows).

### 💡 `flip_harvester_IDEA.md` — researched, not built yet
Coin-flip underdogs touch the lead 71% of the time but only win 42% — so sell the touch
instead of holding to resolution. Full research is in the doc if you want to build it
before I do.

## Reality check (again, on purpose)

- These run with **$5–$15 per trade** on my end. That sizing is the strategy — small live
  samples, honest logs, scale only what survives. If your first instinct is to 100x the
  size constant, this repo is not going to end well for you.
- Several of these edges came from **small samples over specific weeks**. Markets adapt.
  Market makers adapt. An edge in the logs is a hypothesis, not a promise.
- Every bot logs every window — entries AND skips with reasons — to CSV. If you run one,
  the log is the deliverable. The PnL is just one column of it.
- **Not financial advice. Use at your own risk.** I have not cracked this market. I'm
  showing my work, not selling a solution.

Come hang out and ask questions on the Zoom: **https://moondev.com/t/polymarket-github**

*Built live on YouTube by Moon Dev 🌙*
