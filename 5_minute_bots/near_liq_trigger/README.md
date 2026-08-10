# 💣 `near_liq_trigger/`, wait for the whale, then wait again

**Status: live experiment, NO backtest exists, ships in `PAPER_MODE = True`.**

Everything else in this folder is honest about what that means. Read it before you run it.

Not financial advice. Nothing here is plug-and-play.

---

## The idea in four steps

Every big trader's liquidation price on Hyperliquid is **public**. You can see exactly
where somebody gets force-sold. This bot watches that, and then refuses to trade on it
until the falling actually starts.

| # | step | what it means |
|---|---|---|
| 1️⃣ | **ARM** | A BTC position sits within **0.5%** of its liquidation price *and* is worth over **$100,000**. Whichever side holds the *closest* such position sets the direction. |
| 2️⃣ | **TRIGGER** | Do nothing on the arm alone. Wait until somebody on that **same side** actually gets liquidated for **≥ $5,000** within the last **120 seconds**. |
| 3️⃣ | **FIRE** | Take the ask on the BTC 5-minute Up/Down market in that direction. **$5 flat.** |
| 4️⃣ | **HOLD** | No exit. Ever. It resolves at expiration. |

```
closest big LONG near liq   ->  forced SELLING below  ->  buy Down
closest big SHORT near liq  ->  forced BUYING above   ->  buy Up
```

Both locks must agree on the side. A long-liq arm needs a **LONG** liquidation print to
fire; a short-liq arm needs a **SHORT** one.

**Most 5-minute windows this bot does nothing at all.** It sits there armed, or not even
armed, and skips. That is the design, not a bug.

There's a visual explainer in [`explainer.html`](explainer.html) — five slides, plain
english, open it in a browser.

## Why the second lock exists

The whole point. A whale can hover a quarter-percent from his liquidation price for
hours and never break. "Close to liquidation" is a *condition*, not an *event*. The
$5,000 print is the event: somebody smaller already got knocked over, and the forced
selling is now rolling toward the big pile.

The bot's sibling [`liq_cascade_chaser/`](../liq_cascade_chaser/) trades the cascade
itself. This one adds the question *"is there actually a pile of fuel in that
direction?"* before it will touch it.

## Real numbers, and what's honestly missing

**There is no backtest. None.** Nobody archives "who was near liquidation" historically,
so there is no dataset to test this against. That is a real hole, and it's the single
biggest reason to treat this as an experiment rather than a strategy.

What exists instead: `data/*_eval.csv` logs **one row per 5-minute window, fire or not** —
the nearest qualifying whale on each side, the arm, the trigger, the price, and the exact
reason a window didn't fire. That log is the deliverable. It's what will eventually
confirm or kill the idea.

A real snapshot from 2026-08-10 11:59 ET, for what the setup actually looks like:

```
qualifying : 9 longs / 0 shorts   (≤0.5% from liq AND ≥$100,000)
   nearest  : $135,779 LONG, 0.14% away, liq $64,201
within 0.5% BELOW : $9.98M        within 0.5% ABOVE : $0
```

Then a $9,582 Bybit long liquidation printed, and the Down side was trading at 21¢.

## ⚠️ The bug that ate the first live session (worth your time)

First live run: armed twice, fired **zero** times, while a **$715,542 Bybit long
liquidation** went by in plain sight. The strategy logic was fine. The *data feed* was
not.

Measured detection lag on the liquidation API (how long from a real liquidation to it
appearing in the feed) — **with a mandatory warm-up poll**, because the 10-minute
backlog already sitting in the window fakes a ~270s median and gives you a completely
wrong answer:

| exchange | median | max | inside the 120s trigger window |
|---|---|---|---|
| okx | 36s | 73s | 59/59 |
| bybit | 38s | **151s** | 25/26 |
| **binance** | **181s** | **340s** | **43/208** |

Only **43% of prints** arrived within 120 seconds. The Bybit print landed around
130–150s — so by the time the bot could see it, its own timestamp already made it
"too old to be news." **A trigger that requires fresh data will silently never fire if
your feed is slower than your freshness rule, and it will look exactly like "no signal
today."**

Moon Dev fixed the feed server-side. Re-measured the same day, same method:

| exchange | median | max | inside the window |
|---|---|---|---|
| binance | **9s** (was 181s) | 17s | 58/58 |
| bybit | **6s** | 6s | 7/7 |
| okx | 10s | 48s | 29/29 |
| **overall** | **9s** | **48s** | **94/94 (100%)** |

**Check it yourself before trusting any run:**

```bash
python near_liq_trigger.py --lagcheck --minutes=6
```

It warms up, times only genuinely new prints, breaks it down per exchange, and gives you
a straight ✅/❌ against the trigger window. If your feed is slow, this bot cannot work,
and this tells you in six minutes instead of six sessions.

Two more things that broke, both fixed, both worth knowing:

* **Paper fills were landing in the live trade log**, so the tracker showed `W 1 · WR
  100%` with zero real trades. Paper and live now write to separate `*_paper.csv` files.
  If your log can lie to you, it will.
* **The near-liq snapshot goes stale (17 minutes, measured) and its `distance_pct` is
  frozen at snapshot time.** It reported a whale "0.01% away" when price had *already
  fallen through* his liquidation price — he was gone. Distances are now recomputed every
  cycle off live spot against `liq_price` (which never moves); anything price already
  blew through is discarded.

## Running it

```bash
python near_liq_trigger.py --selftest              # read-only. Walks the whole chain, places NOTHING.
python near_liq_trigger.py --lagcheck --minutes=6  # read-only. Is your liquidation feed fast enough?
python near_liq_trigger.py                         # the bot (PAPER_MODE = True as shipped)
```

**Setup:** copy the repo's `.env_example` to `.env` and fill in **your own** keys. The bot
walks up the directory tree to find it. It needs `PRIVATE_KEY{SUFFIX}`,
`PUBLIC_KEY{SUFFIX}` and `MOONDEV_API_KEY`. `ACCOUNT_SUFFIX` is `_AUG14` because that's
Moon Dev's account naming — **change it to match yours**. `SIGNATURE_TYPE = 2` is a
Gnosis Safe proxy wallet; use `0` for a plain EOA.

**Dependencies:** `py_clob_client_v2` for live trading (V1's `post_order` is dead, it
throws `PolyApiException`), plus `requests`. `websockets` only if you set `LIQ_SOURCE` to
`"ws"` or `"auto"`. Everything else is stdlib — the dotenv and termcolor pieces are
inlined so this stays one file.

## Every knob

```python
COIN                 = "BTC"     # BTC only
NEAR_LIQ_PCT         = 0.5       # how close to liq the whale must be
MIN_POSITION_USD     = 100_000   # how big that whale must be
TRIGGER_LIQ_USD      = 5_000     # the liquidation print that fires us
TRIGGER_LOOKBACK_SEC = 120       # how recent it must be
USD_SIZE             = 5         # flat stake
MAX_ENTRY_PRICE      = 0.95      # never pay more than this
MIN_TIME_LEFT        = 30        # don't fire in the last 30s of a window
LIQ_SOURCE           = "api"     # "api" | "ws" (exchange sockets) | "auto"
PAPER_MODE           = True      # ships as paper. Your call.
```

`LIQ_SOURCE = "ws"` connects straight to Binance `!forceOrder@arr`, Bybit
`allLiquidation.BTCUSDT` and OKX `liquidation-orders` (~0.5–1.2s arrival) if you'd rather
not depend on a REST feed at all. Side mapping is the easy thing to get backwards:
Binance `SELL` = a long died, **Bybit `BUY` = a long died (inverted)**, OKX uses `posSide`.

## Files it writes

* `data/near_liq_trigger_trades.csv` — every fill, with the whale and the exact trigger
  print that caused it, resolved WIN/LOSS via Gamma `closed=true` (`outcomePrices` 1/0).
* `data/near_liq_trigger_eval.csv` — one row per window, fire or skip, including the
  biggest print seen on the armed side and why it wasn't good enough.

`data/` is gitignored. Paper runs write to `*_paper.csv` and never mix with live.

## How this loses money

1. **We buy after the news.** The domino already fell, so the price already moved. That's
   what `MAX_ENTRY_PRICE` is defending against.
2. **One domino is not always a chain.** Small liquidations happen constantly and lead
   nowhere. Sometimes the cascade is over before we see it.
3. **The price IS the crowd's odds.** Buy at 21¢ and the market is saying 21%. We have to
   beat that number, not match it. And every loss is a *total* loss of the stake.
4. **No backtest.** Said it twice on purpose.

**Guardrails:** $5 flat · one trade per 5-minute window · daily stop −$30 · auto shut-off
if the trailing 40 resolved trades average worse than −$0.40.

---

*Built live on YouTube by Moon Dev 🌙. Not financial advice, no warranties, use at your
own risk. Questions: https://moondev.com/t/polymarket-github*
