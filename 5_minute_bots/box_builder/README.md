# 📦 Box Builder — the two-sided maker

*Part of Moon Dev's 5-minute bot fleet. Not plug-and-play, not financial advice, not a
money printer. Built live on YouTube. Questions → https://moondev.com/t/polymarket-github*

## What it does, in plain english

Every 5 minutes Polymarket asks: will Bitcoin close this window UP or DOWN? Winning
shares pay $1, losers pay $0. Here's the trick: if you own one UP share AND one DOWN
share, one of them MUST win — the pair is guaranteed to redeem for exactly $1.00.

So this bot parks patient lowball bids on **both** sides early in the window, capped at
**$0.94 combined**. If both fill, that's a locked ≥6 cents per pair with zero directional
risk. It's not predicting Bitcoin — it's running a tiny convenience store on both sides
of the street, buying dollar bills for 94 cents from people in a hurry.

## Why it exists (the autopsy)

Five straight directional maker bots (fable maker, 89c v5, 95c snipers v1-v4, 97c v6)
went breakeven-to-negative in my logs. The killer was adverse selection: passive fills
arrive precisely when your side is dying (losing fills averaged entry 0.647 vs 0.736 for
winners — a measured ~9c of poison). A completed box is direction-neutral, so that same
panic-seller dumping a dying side cheap is now *building your box*. The poison becomes
the product. Full numbers in [RESEARCH.md](RESEARCH.md).

## The rules it lives by

- **Arm only when the book is wide** (ask_UP + ask_DOWN ≥ 1.03 at open). Tight book =
  your lowball bids are bait for losers only = skip (`SKIP_NARROW`, you'll see it a lot).
- **Quote the first half of the window only.** My logs: early quotes filled 57% of armed
  windows; deep late quotes filled 0 of 35.
- **Never chase.** Repricing into momentum got 249 consecutive post-only rejections in
  the old logs. Static quotes, reprice at most every 20s.
- **Completion ladder:** the moment one leg fills at p1, get aggressive on the other —
  raise the bid toward 0.97−p1, or cross the spread entirely if the ask ≤ 0.99−p1. A
  small locked box beats a naked bet.
- **T-90 bailout:** stranded on one leg? Check where BTC actually is (real candles). Hold
  only if our side is comfortably winning; otherwise cut at the bid and take the scratch.
- **Cancel everything at T-10.** Never carry an order into the next window.

## The honest risk

A clean trend is the worst case: the losing side fills, the winning side never comes back
to the capped bid, and you're stuck with the leg nobody wanted. The both-fill rate is the
one number that decides if this strategy is real — which is why it starts at the exchange
minimum (5 shares/leg, ~$2.35 a side) and logs every window until the CSV proves it.
**I have not cracked the 5-minute market. This is an experiment with a logbook, not an
answer.**

## Running it (at your own risk)

Needs: `.env` in the repo root (see `.env_example`), `py_clob_client_v2`, your own
Polymarket account creds. `PAPER_MODE` flag at the top of `box_builder.py` — start there.
Logs every window to `data/box_builder_log.csv`.

*Built live on YouTube by Moon Dev 🌙 — use at your own risk.*
