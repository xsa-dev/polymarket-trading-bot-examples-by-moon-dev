# 🌉 Corridor Collector — the two-market play

*Part of Moon Dev's 5-minute bot fleet. Not plug-and-play, not financial advice, not a
money printer. Built live on YouTube. Questions → https://moondev.com/t/polymarket-github*

## What it does, in plain english

Polymarket runs BTC Up/Down markets on both 5-minute AND 15-minute windows — and the
15-minute window's final 5 minutes IS a 5-minute market. Both resolve on the **same
closing price**. That overlap creates a structural trade:

Buy the side that's **leading the 15-minute window** + buy the **opposite side of the
final 5-minute window**. Work through the cases and you'll see at least one leg always
wins — the pair can never pay less than $1. And when the close lands *between* the
15-minute open and the 5-minute open (the "corridor"), **both** legs win and the pair
pays $2.

So you're buying something floored at $1 that sometimes pays $2 — the only question is
whether you paid little enough for it.

## The data behind it

- 34,918 real 15-minute windows (52 weeks of 1-minute BTC data): **zero** windows where
  both legs lost. The $1 floor held every single time.
- Sweet zone (15m lead of 5–30 bps AND lead/ATR14 ≥ 1.0): the double-win corridor hit
  **41.3%** (n=18,649) → fair pair value ≈ **$1.41**.
- The 5m-opposite leg is a true coin flip (49.7–50.1% across every lead bin) — buying it
  under 50c is never overpaying.
- The old version of this idea only fired when the pair cost **under $1** (risk-free
  money). That basically never happens. The evolution: pay up to fair value minus an
  8-cent edge buffer. More fills, still positive expectancy — if the corridor rate holds.

## The rules it lives by

- Acts once per 15-minute window, only in the first 90 seconds of its final third.
- Zone gate: 15m lead between 5–30 bps and lead/ATR14 ≥ 1.0 (leads too small flip, leads
  too big never come back — the corridor needs a Goldilocks lead).
- Price gate: ask(15m leader) + ask(5m opposite) ≤ 1 + p_corridor − 0.08.
- Equal shares both legs (the math requires 1:1). ~$5 total per pair. If leg 2 fails
  after leg 1 fills, it alerts loudly and flattens — an unpaired leg is a bet, not a
  corridor.
- Hold both to resolution.

## The honest risk

The $1 floor is real, but you're paying ~$1.30+ for it — the loss case (no corridor) is
a slow bleed of ~30c per pair, and the 41.3% double-win rate is the entire edge. If
market makers price the pair efficiently (they usually do), it just skips all day. And
the corridor probability came from candle data, not from live Polymarket fills — live
asks may never be as generous as the study assumed. **I have not cracked the 5-minute
market. This is the most structurally interesting bot in the fleet and also the least
proven.**

## Running it (at your own risk)

Needs: `.env` in the repo root (see `.env_example`), `py_clob_client_v2`, public BTC
candle data (Binance, Coinbase fallback). `PAPER_MODE` flag at the top. Logs every 15m
window — fires AND skips with reasons — to `data/corridor_collector_log.csv`.

*Built live on YouTube by Moon Dev 🌙 — use at your own risk.*
