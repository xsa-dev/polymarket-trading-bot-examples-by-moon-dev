# 🔄 Streak Snapper — the streak fader

*Part of Moon Dev's 5-minute bot fleet. Not plug-and-play, not financial advice, not a
money printer. Built live on YouTube. Questions → https://moondev.com/t/polymarket-github*

## What it does, in plain english

BTC 5-minute windows are slightly **anti-persistent**: after a run of same-direction
windows, the next one reverses a bit more often than chance — but ONLY when the run has
actually stretched price. This bot waits for 4+ consecutive same-direction windows whose
combined move is more than 3x the hourly ATR (a genuinely stretched rubber band), then
buys the reversal side of the next window — but only at 52 cents or less, with a limit
order in the first 20 seconds.

## The data behind it

- 52 weeks / 104,762 real 5-minute windows: after a 4+ streak with the 3x-ATR stretch,
  the fade won **54.3%** (n=8,802), positive in all 5 quarters tested (worst 52.2%).
- **The ATR filter is load-bearing:** without the stretch condition the edge collapses to
  50.7% — i.e., nothing. Fading streaks blindly is a coin flip; fading *stretched*
  streaks is the trade.
- My live dog sniper log (1,642 real windows) agrees: after 3 straight Ups, Down won
  61.6% (n=164).
- Entry discipline matters more than the signal: ~95% of late-window stink bids in my old
  logs got cancelled unfilled — so this bot enters at the window open, where fills exist.

Full numbers in [RESEARCH.md](RESEARCH.md).

## The rules it lives by

- 4+ consecutive same-direction windows (graded by the actual Polymarket oracle, with a
  real tick-feed fallback — never simulated).
- |cumulative 4-window move| > 3x the 1-hour ATR.
- Limit buy the reversal side at ≤ 0.52, placed in the first 20 seconds of the new window.
- Cancel if unfilled after 60 seconds — no chasing, skip the window.
- Hold to resolution. One position per window. Trailing kill switch.

## The honest risk

54.3% at ~0.50 entry is a **thin** edge — roughly 4 cents of expectancy per share before
fees and slippage. It only survives at volume with disciplined ≤52c fills; two cents of
slippage cuts the edge in half. This is a grinder, not a rocket. **I have not cracked the
5-minute market — this bot is me testing whether a thin, well-documented edge survives
contact with the live book.**

## Running it (at your own risk)

Needs: `.env` in the repo root (see `.env_example`), `py_clob_client_v2`, and a BTC tick
feed for the ATR math (mine is the Moon Dev API). `PAPER_MODE` flag at the top. Logs
every window — entries AND skips with reasons — to `data/streak_snapper_log.csv`.

*Built live on YouTube by Moon Dev 🌙 — use at your own risk.*
