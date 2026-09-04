# 💎 Flip Harvester, the coin-flip dog's exit engine

*Part of Moon Dev's 5-minute bot fleet. Not plug-and-play, not financial advice, not a
money printer. Questions → https://moondev.com/t/polymarket-github*

## ⚠️ Read this first, it is not the usual disclaimer

**This bot has never placed a single order. Not live, not paper. It has never been run.**

**It was written by an AI assistant** from Moon Dev's research doc
[`../flip_harvester_IDEA.md`](../flip_harvester_IDEA.md), not by Moon Dev at the keyboard.
Every performance number below is **inherited from that doc and was not re-measured**
while building this. The underlying files it cites (`BTCUSD-1m-52wks-data.csv`,
`data/dog_sniper_log.csv`) are **not in this repo**, so you cannot check them from here
and neither could the thing that wrote this code.

Treat the numbers as the **hypothesis this bot exists to test**, never as its results.
Where the source doc's own arithmetic does not hold up, this README says so out loud
(see "Where the source research doesn't add up").

## What it does, in plain english

Every dog bot in this fleet buys the coin-flip underdog cheap and holds it to
resolution, win or lose. The 52-week backtest says that leaves money on the table: a
genuine coin-flip dog **touches** the strike (briefly leads) in the final minute 71.3% of
the time, but only **wins** 42.4% of the time. That ~29-point gap is the "touched, then
faded back to zero" cohort, and holding to resolution captures none of it.

Flip Harvester buys the exact same dog, at the exact same gate. The only thing it
changes: the instant the buy fills, it rests a maker sell at **0.62**. If the dog flips
ahead and someone pays up for it, that's the harvest. If the flip never comes, the sell
never fills and the position rides to resolution exactly like every other dog bot in the
repo. It's a free option on top of the proven trade, not a new bet.

## The math, re-derived (this part IS checked)

The source doc's EV table compares "hold" against "harvest" and reports +5.4c vs +7.2c
per share. That comparison does not survive the arithmetic — the two rows use different
values for the same quantity (the hold row counts no-touch wins, the harvest rows assume
there are none). Here is the comparison that does hold:

```
EV(harvest) − EV(hold) = P(touch) × f × [ ask − q ]

    f = P(our sell fills | the dog touched)
    q = P(the dog goes on to WIN | it touched AND our sell filled)
```

**The entry price cancels out. So does the entire no-touch branch. The harvest beats
holding if and only if `q < ask`.** That is the whole bet, and it has three consequences
the source doc does not draw:

* **The ceiling is small.** At `f = 1.0` and zero adverse selection, the harvest adds
  **+1.8 to +2.9c per share** over holding — not the +1.8c the doc's table implies by
  accident, and nowhere near the "+19.5% vs +14.6% ROI" framing suggests. On a $10 stake
  that is at most about **77 cents per trade**.
* **`q` is a selected population, and that is the entire risk.** A resting ask only fills
  when a momentum taker lifts it, and those are exactly the flips most likely to go on and
  win. Unconditional `P(win | touch)` is 0.580–0.595, so a 0.62 ask starts with only
  **~2.5 points of headroom**. Adverse selection does not need to be large to eat all of it.
* **You cannot judge this bot by its P&L curve.** Per-trade P&L standard deviation at $10
  is ~$13 against an edge of at most $0.77 — roughly **1,200 trades** before the total
  edge is even two standard errors from zero. But `q` is directly observable from the
  outcomes of sold positions, which is a paired comparison: **~600 filled sells** resolve
  it to ±4 points. Read `q`, not the P&L.

## The data behind it — inherited, not re-verified here

All of the following is copied from `../flip_harvester_IDEA.md`. **This bot produced none
of it.**

* **52-week backtest**, 104,669 windows: at `coa <= 0.20` (`coa = |cushion| / ATR4`, the
  gate the fleet's dog bots already use), the dog touches the strike in the final minute
  71.3% of the time (n=10,180), wins 42.4%.
* **Vol regime split** (median ATR4): HIGH-vol touches fade back — only 55.3% stick;
  LOW-vol touches stick 60.9%. Sampling error says this split is real (~4.8 sigma).
* **Live entry pocket**: n=**56** trades, 44.6% win at an average ask of 0.370.

## Where the source research doesn't add up

Stating these plainly because the repo's rule is honest numbers, and because two of them
changed what this bot actually does.

1. **`P(win|touch)` is reported as 58.0%, but the doc's own `P(win)/P(touch)` is 59.5%**
   (42.4/71.3), and the same ~1.5-point gap appears in all three coa rows. The doc's
   claim that "a no-touch dog can NEVER win" is what forces those to be equal — and it is
   wrong for a *final-minute* touch definition: a dog that crossed early and never
   re-touched wins uncounted (~1% of windows). **This bot plans against the worse number
   (0.5947)**, which shrinks the headroom at a 0.62 ask from 4.0 points to 2.5.
2. **The LOW-vol half-harvest the spec asks for is -EV on the spec's own numbers.** At
   `P(win|touch) = 0.609` the headroom at 0.62 is 1.1 points; on the conservative estimate
   (0.6285) it is **negative before adverse selection**. The doc's own table agrees — LOW
   vol hold (+5.8c) beats sell-all (+5.2c). **So this bot does not harvest in LOW vol at
   all** (`LOW_VOL_SELL_PCT = 0.00`), a deliberate deviation from the spec.
3. **The weakest link is the entry price, not the 62c print.** The doc names the 62c fill
   as "the one unproven link." But the entire base edge is "fair value 0.424 minus a 0.37
   entry," and that 0.37 comes from **56 trades** (95% CI on its win rate: 32%–58%). At a
   0.42 entry — still inside the shipped 0.22–0.45 band — the base edge is zero. The
   backtest's 10,180 windows say nothing about achievable entry *price*.
4. **The README's own floor justification was wrong and is now fixed.** The 18.5% figure
   the doc cites is for **sub-20c** dogs; the 0.20–0.30 bucket in the same ladder is
   28.8% win, **+12.2% EV**. And the doc's live ladder marks **0.40–0.45 at -11.4% EV** —
   the top 5 cents of the shipped entry band is negative in the source's own data. The
   band is left at 0.22–0.45 because that is what the spec specifies; know that its top
   end is not supported.

## The rules it lives by

**Entry** (unchanged from the fleet's proven coinflip-dog gate):
* `coa = |cushion| / ATR4 <= 0.20`, from the first 4 closed 1-minute bars (real Binance
  klines, Coinbase fallback), same math as `box_builder`'s live COA signal.
* `|cushion| <= 1.5 bps`.
* Dog ask **0.22 to 0.45**.
* Fires in the **T-60 to T-5** window — bars 0-3 are always closed by T-60, so no lookahead.

**Exit** (the new part):
* On fill, rest a **post-only GTC sell at 0.62** (A/B'd against 0.65 by window parity).
* **HIGH vol** (ATR4 ≥ rolling 24h median): sell 100%. **LOW vol: sell nothing** — see above.
* **Never an ask below 0.60.** No stop-loss, no chasing down. If the book already ran past
  our target when we post (post-only "would cross"), we take the better bid instead —
  strictly better, never worse.
* **Adverse-selection halt**: harvesting switches off automatically once the 95% CI lower
  bound on realized `q` clears the ask, i.e. once the bot is confident it is selling
  winners. The source doc named this failure mode; nothing in it guarded against it.

**Sizing & risk**: $10 flat, max 3 unresolved windows at once, daily kill switch at -$60.
Note the rest of the fleet runs **$5** and `near_liq_trigger` — the other unproven bot —
holds one position at a time. $10 × 3 concurrent is the largest envelope in the fleet on
the least-proven idea; drop `USD_SIZE` to 5 if that trade-off isn't yours.

## Running it

```bash
pip install requests                 # paper mode needs nothing else
python flip_harvester.py             # PAPER_MODE = True is the default
```

* **Paper mode needs no `.env`, no API keys, and no CLOB client** — it uses only public
  endpoints, and it runs the *full* research instrumentation: touch detection, max-bid
  tracking, and a simulated harvest the moment the observed bid reaches the ask.
* **Live** additionally needs `pip install py_clob_client_v2` (which pulls `web3`) and a
  repo-root `.env`. It also needs you to change `ACCOUNT_SUFFIX = "_AUG14"` to **your own**
  account naming, and to read every line of the code first.
* Paper and live write to **separate** CSVs (`*_paper.csv`), so paper P&L can never feed
  the live daily stop.
* With no network at all it does not crash — it sits on "waiting for strike/spot" and
  logs nothing, because there is nothing honest to log.

## Reading the run: `analyze_run.py`

```bash
python analyze_run.py            # the paper CSVs
python analyze_run.py --live     # the live ones
```

Raw CSVs won't tell you anything by eye, and **the P&L column is actively
misleading** for the first ~1,200 trades — per-trade noise is ~$13 against an edge of
at most $0.77. The analyzer answers the four questions that actually decide it, each
with a confidence interval and an explicit "not enough data yet" when the sample is
too small to say:

1. **The funnel** — is the gate firing at all, and which skip reason is the binding
   constraint?
2. **Does the book ever BID 0.62 during a flip?** The distribution of
   `max_bid_after_touch` and the fill rate `f` at 0.60 / 0.62 / 0.65. If the bid
   rarely gets there, the idea is dead and you learned it for free.
3. **`q` = P(dog won | we sold it).** The harvest is +EV iff `q < ask`. Needs ~600
   resolved sells to resolve to ±4 points — this is the expensive question, so only
   ask it after (2) and (4) survive.
4. **The realized entry price and dog win rate on YOUR population** — the base edge,
   worth ~3× the harvest, and the one measured on n=56 in the source doc.

Kill it early on (2) or (4). Either one ends the experiment cheaply.

## What to read in the logs

* `data/flip_harvester_eval[_paper].csv` — every window, entries AND skips, with the
  coa/cushion numbers that decided it and the reason.
* `data/flip_harvester_trades[_paper].csv` — every fill: entry, `touched`,
  `max_bid_after_touch`, `sell_fill_price`, shares sold vs held, and the scalp/hold P&L
  split.
* **The three columns that decide whether this idea is real**: `touched` (does the 71.3%
  hold up live?), `max_bid_after_touch` (does the book ever actually *bid* 62c during a
  flip?), and `dog_won` on rows where `shares_sold > 0` (that's `q` — is the harvest
  selling winners?). The dashboard prints `q` with its confidence interval once there are
  enough resolved sells.

⚠️ One caveat on comparability: the backtest's 71.3% counts a touch **in the final
minute**. This bot's `touched` counts a touch at any point after entry — since entry is in
the T-60→T-5 band the two are close, but they are not the same statistic, and the logged
number is a superset.

***
*Built from Moon Dev's `../flip_harvester_IDEA.md` by an AI assistant. Not financial
advice, no warranties, use at your own risk. Nobody has run this.*
