# 🌾🧠 Flip Harvester ML, the exit engine that learns

*Part of Moon Dev's 5-minute bot fleet. Not plug-and-play, not financial advice, not a
money printer. Built live on YouTube. Questions → https://moondev.com/t/polymarket-github*

This is `flip_harvester_IDEA.md`, finally built, plus the one thing that idea was missing:
the two numbers at the heart of it are no longer hardcoded. They are learned, live, from
the bot's own windows, with [river](https://riverml.xyz) online models.

## What it does, in plain english

In a BTC 5-minute window, the **dog** is the side that's behind. Coin-flip dogs (the
window is nearly tied, `coa ≤ 0.20`) **touch** the strike 71.3% of the time, but only
**win** 42.4% of the time. Every other dog bot in this repo holds to resolution, so the
29-point gap between "touched" and "won" dies at zero, every single day.

This bot buys the dog at 0.22 to 0.45 in the last minute, and **the instant it fills it
rests a maker SELL**. If the flip comes, the ask gets lifted and we harvest ~62c on a 37c
cost. If it never comes, the ask expires and we hold to resolution exactly like the fleet
bot, which is still +EV. The bracket is a free option layered on a proven entry.

## The data behind it (from the idea doc, all real, no synthetic anything)

52-week physical backtest, 104,669 clean 5-minute windows, `coa ≤ 0.20`, n=10,180:

| | P(dog TOUCHES) | P(dog WINS) | P(win \| touch) | the gap |
|---|---|---|---|---|
| coa ≤ 0.20 | **71.3%** | 42.4% | 58.0% | **29.0 points** |

| Exit strategy | EV/share @ 0.37 entry | ROI | hit rate |
|---|---|---|---|
| Hold to resolution (the entire current fleet) | +5.4c | +14.6% | 42% |
| **Sell at the flip @ 0.62** | **+7.2c** | **+19.5%** | **71%** |

**Breakeven flip-exit price = P(win)/P(touch) = 0.5947.** Any fill at 60c or better beats
holding. That number is why `MIN_SELL_ASK = 0.60` is a hard floor in the code and why no
model output is ever allowed below it.

## What the ML actually changes (and what it is NOT allowed to change)

The idea doc hardcodes two numbers, and both are really estimates of the same thing,
**P(win | touched)**, the fair value of the dog token at the moment of the flip (0.580
pooled, 0.553 in high vol, 0.609 in low vol):

* the exit **ask**: a flat 0.62 for every window, forever
* the exit **size**: 100% in high vol, 50% in low vol, split at the ATR median

So the bot trains two online logistic regressions, one window at a time:

| model | predicts | prior it starts from |
|---|---|---|
| `touch_model` | P(dog touches the strike) | 0.713 |
| `stick_model` | P(dog wins \| it touched) | 0.580 |

and every window prices its own exit: **ask = P(win\|touch) + 4c**, floored at 0.60,
selling 100% when the model says the touch fades and 50% when it says it sticks. The pair
also produces an expected value for the whole structure, which becomes an entry **veto**.

**The hard rails the ML may never touch** (it can only subtract trades, never add them):

* dog ask **0.22 to 0.45** only. The live ladder says 0.45+ is −11.4% EV and sub-20c dogs
  won 18.5% at a 16.9c average. No model output unlocks those.
* `coa ≤ 0.20` and `|cushion| ≤ 1.5 bps`, the inherited, already-proven entry gate.
* the resting sell never goes below **0.60**, and it is never lowered to chase a fade.
* no stop loss. Unfilled at T-0 → hold, worst case we ARE `coinflip_discount_dog`.

**Cold start:** until **40** labeled windows the model gets zero say and the bot trades
the doc's static 0.62 rules, logging what the model *would* have done. Trust ramps to full
at 200 windows. Every row records which mode was live (`STATIC` vs `MODEL`), so the log
itself settles whether the brain beat the constant it replaced.

**Drift:** a river `ADWIN` detector watches the models' own error. When the market changes
under them, trust snaps back to zero, the static rules take over and entries pause for 20
windows. A hardcoded 0.62 can never notice that it stopped working. This can.

**Skipped windows train it too.** Every window that clears the structural gate gets its
features logged and its label graded at resolution, entered or not, so the brain learns
from roughly ten times more windows than we ever have fills for.

## Features you can switch on and off (`features.py`)

```
core   the window itself: coa, cushion, ATR4, dog ask, spread, seconds left, tick rate
ta     technical indicators on Hyperliquid BTC 1m candles:
       RSI(14), MACD histogram, Bollinger %B, ATR(14), 15m realized vol, VWAP distance,
       5m and 15m returns                                       (hand-rolled, no ta lib)
hl     Hyperliquid microstructure: funding, open interest, mark-vs-oracle premium,
       24h volume, top-10 book imbalance, and the basis vs our own spot tape
corr   cross-asset: rolling 30-bar correlation of BTC vs ETH and BTC vs SOL 1m returns,
       plus ETH's own 5m return
```

Three ways to control them, later ones win:

```bash
# 1. edit FEATURE_GROUPS in features.py
# 2. env var
FLIP_FEATURES=core,ta python flip_harvester_ml.py
# 3. CLI
python flip_harvester_ml.py --groups core,ta,hl
```

Two warnings that are not decoration:

1. **Changing groups changes the model's input shape**, so the saved brain is refused and
   you start training from zero. That is deliberate: a scaler fitted on columns that no
   longer exist is worse than no model.
2. **All four groups on is 27 inputs.** On a few hundred real windows a 27-input model
   will happily memorize noise. Start with `core`, add ONE group, and let the logloss in
   `--report` say whether it earned its place. That is the experiment, not a settings menu.

If an enabled group's feed is down, the window is logged as a SKIP and is **not** used for
training. No feed is ever padded with a zero and called data.

## Running it (at your own risk)

```bash
pip install river polars requests python-dotenv termcolor   # + py_clob_client_v2, web3

python selftest.py                       # offline: math, brain, drift, log loop. No keys.
python flip_harvester_ml.py --report     # what the brain currently believes
python flip_harvester_ml.py --replay data/flip_harvester_ml_log.csv   # retrain from a log
python flip_harvester_ml.py              # run it (PAPER_MODE = True at the top of the file)
```

Needs `.env` in the repo root (see `.env_example`), the V2 CLOB SDK, and the Moon Dev API
for the BTC tick feed. Hyperliquid is public, no key. Paths and the `_AUG14` account suffix
come from Moon Dev's machine, you will adapt both. **`PAPER_MODE` ships as `True` here**,
unlike the older bots, because both the harvest mechanic and the ML layer are unproven.

`--replay` also accepts a JSON array from your own bot service, as long as each record
carries the feature columns plus `touched` and `dog_won`. It scores itself by progressive
validation: predict first, then learn, so every number it prints was made on data the
model had not seen.

### Pulling history out of your own bot service

`export_from_service.py` turns a running service (anything serving `/openapi.json`) into a
replay CSV:

```bash
python export_from_service.py --url http://dragon:8787 --inspect     # what does it return?
python export_from_service.py --url http://dragon:8787     --pull /api/polymarket/fleet/fills --out flip_history.csv --groups core
python flip_harvester_ml.py --replay flip_history.csv --groups core
```

It guesses column names from an alias table and **prints every guess** (`--map ours=theirs`
overrides any of them). What it will not do is invent a label: if the history has no
`touched` column, it says so and stops, because `touched` is specific to this bot and
almost nothing else logs it. In that case the honest path is to let the bot label its own
windows in `PAPER_MODE` for a few hundred windows, which is what the cold start is for.

## The log is the deliverable

One row per window, entries AND skips, in `data/flip_harvester_ml_log.csv`. The columns
that decide whether this bot deserves to exist:

* **`touched`** — is the 71.3% backtest touch rate real on live tape?
* **`max_bid_after_touch`** — **the one untested link in the entire chain.** Nobody in
  this repo has ever logged whether the CLOB bid actually PRINTS 0.60+ during a flip.
* **`pnl_scalp_usd` vs `pnl_hold_usd`** — which leg is actually earning?
* **`exit_source`** (`STATIC` vs `MODEL`) — did the brain beat the flat 0.62, or not?
* **`p_touch` / `p_stick` / `trust` / `model_mode`** — what the brain believed, before it
  knew the answer.

Every N graded windows the bot prints a scorecard: live touch rate vs the backtest's
71.3%, live P(win|touch) vs 58.0%, the harvest fill rate, and **P(win | we SOLD)**. If
that last number runs above 62%, the harvest is adversely selected, we are selling exactly
the shares that would have paid $1.00, and the ask has to go up or the bot has to die.
That check is in the code from day one.

## The honest risk

**The 62c print is unproven.** The touch rate is physical spot-price fact; whether a
resting 62c ask gets lifted during that touch has never been measured here. Two ways this
dies: (a) flips are quoted thin, the bid tops out at 55-58c, the sell rarely fills and we
degrade into a plain dog bot; (b) adverse selection, the flips strong enough to lift 62c
are the ones that would have won anyway.

**And the ML layer has no backtest at all.** It starts at the priors and learns from your
fills. Two logistic regressions on a few hundred noisy windows is a hypothesis with a
logger attached, not an edge. The trust ramp, the L2, the 0.60 floor, the drift detector
and the hard entry rails all exist because that's exactly the kind of model that fools
you. **I have not cracked the 5-minute market.** Use any of this at your own risk.

***
*Built live on YouTube by Moon Dev 🌙, harvest the flip, don't pray for the close.*
