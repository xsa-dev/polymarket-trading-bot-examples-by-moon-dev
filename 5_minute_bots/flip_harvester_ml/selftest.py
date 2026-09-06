"""
================================================================================
🌙 MOON DEV's FLIP HARVESTER ML, SELF TEST
================================================================================
Run this BEFORE you ever point the bot at money:

    python selftest.py

No API keys, no .env, no network. It checks the parts that CAN be checked
offline, and it is honest about which parts those are:

  ✅ the indicator math (RSI / MACD / Bollinger / ATR / VWAP / correlation)
     against hand-computable values, and that a short history returns None
     instead of a fabricated number
  ✅ the feature switch board: groups on/off, bad group rejected, env override
  ✅ the river brain: COLD start = exactly the idea doc's static rules, the exit
     ask never breaches the 0.60 floor, the EV math, that the models really do
     LEARN an injected relationship, that the pickle round-trips, that changing
     feature groups REFUSES the stale brain, and that ADWIN fires on a regime flip
  ✅ the polars log → resolve → learn → PnL loop, including that a SKIPPED window
     still trains the brain and that no row is ever learned twice

  ❌ it does NOT prove any edge. The synthetic rows below exercise CODE PATHS.
     Nothing here is market data and nothing here is a backtest.
Built by Moon Dev 🌙
================================================================================
"""
import os
import sys
import json
import math
import random
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import polars as pl
import features as F
import flip_harvester_ml as B

# ============================================================================
# 🌙 PART 1, the indicator math (hand-checkable, no market data involved)
# ============================================================================
flat = [100.0] * 60
up = [100.0 + i for i in range(60)]
down = [160.0 - i for i in range(60)]
assert abs(F.rsi(up, 14) - 100.0) < 1e-9, F.rsi(up, 14)
assert abs(F.rsi(down, 14) - 0.0) < 1e-9, F.rsi(down, 14)
print("RSI: monotonic up = 100, monotonic down = 0 ✅")

w = [100.0] * 19
spike = F.bollinger_pct_b(w + [110.0], 20)
assert spike > 1.0, spike                    # a close above the upper band SHOULD read > 1
assert F.bollinger_pct_b(w + [90.0], 20) < 0.0
print(f"Bollinger %B: break above the band = {spike:.2f} (>1 by construction) ✅")

bars = [{'t': i, 'o': 100, 'h': 101, 'l': 99, 'c': 100, 'v': 10} for i in range(40)]
assert abs(F.atr_bps(bars, 14) - 200.0) < 1e-6, F.atr_bps(bars, 14)   # TR 2 on a 100 price = 200bps
assert abs(F.vwap_dist_bps(bars, 60) - 0.0) < 1e-9
assert F.realized_vol_bps([100.0] * 40, 15) == 0.0
assert abs(F.ret_bps([100.0, 101.0, 102.0, 103.0, 104.0, 105.0], 5) - 500.0) < 1e-9
print("ATR = 200bps, VWAP distance = 0 on a flat tape, 5-bar return = 500bps ✅")

assert abs(F.pearson([1, 2, 3, 4, 5], [2, 4, 6, 8, 10]) - 1.0) < 1e-9
assert abs(F.pearson([1, 2, 3, 4, 5], [10, 8, 6, 4, 2]) + 1.0) < 1e-9
print("Pearson correlation: +1 and -1 ✅")

mh = F.macd_hist_bps([100 + math.sin(i / 5) * 3 for i in range(80)])
assert mh is not None and abs(mh) < 10_000
print(f"MACD histogram computes: {mh:.3f} bps ✅")

assert F.rsi([1, 2, 3], 14) is None
assert F.atr_bps(bars[:3], 14) is None
assert F.macd_hist_bps([1, 2, 3]) is None
print("short history → None, never a fabricated indicator ✅")

# ============================================================================
# 🌙 PART 2, the feature switch board
# ============================================================================
assert F.active_feature_names()[:10] == F.CORE_FEATURES
F.set_groups("core,hl")
assert F.active_feature_names() == F.CORE_FEATURES + F.HL_FEATURES
try:
    F.set_groups("core,bogus")
    raise SystemExit("a bogus group should have raised")
except ValueError as e:
    print(f"unknown group rejected ✅  ({e})")
try:
    F.set_groups("ta")
    raise SystemExit("dropping core should have raised")
except ValueError as e:
    print(f"'core' cannot be disabled ✅  ({e})")
os.environ['FLIP_FEATURES'] = 'core'
F._apply_env_override()
assert F.active_groups() == ['core']
print("env override FLIP_FEATURES=core ✅")

# ============================================================================
# 🌙 PART 3, the river brain
# ============================================================================
tmp = tempfile.mkdtemp()
B.DATA_DIR = tmp
B.LOG_FILE = os.path.join(tmp, "log.csv")
B.MODEL_FILE = os.path.join(tmp, "brain.pkl")

brain = B.FlipBrain(path=B.MODEL_FILE)
assert brain.FEATURES == F.CORE_FEATURES

tape = {'coa': 0.11, 'cushion_bps': 0.9, 'atr4_bps': 8.0, 'atr_ratio': 1.2,
        'dog_side': 'DOWN', 'rate_mult': 1.4, 'spot': 100000.0, 'strike': 100050.0}
x, why = brain.build_features(tape, dog_ask=0.35, spread=0.02, secs_left=45)
assert why == "OK" and len(x) == 10, (why, x)
print(f"feature vector built: {why}, {len(x)} inputs ✅")

# COLD start must reproduce the idea doc EXACTLY: flat 0.62, vol-split size
p = brain.predict(x)
assert p['mode'] == "COLD" and abs(p['p_touch'] - B.PRIOR_P_TOUCH) < 1e-9, p
assert brain.plan_exit(p['p_stick'], 1.2) == (B.STATIC_SELL_ASK, 1.0, "STATIC")
assert brain.plan_exit(p['p_stick'], 0.8)[1] == B.MIN_SELL_FRACTION
print("COLD start = the doc's static rules, 0.62 / 100% high vol / 50% low vol ✅")

ev = brain.expected_value(0.35, 0.713, 0.58, 0.62, 1.0, 0.0)
assert abs(ev - (0.713 * (0.62 - 0.35) + 0.287 * (-0.35))) < 1e-12
sell_ev = brain.expected_value(0.37, 0.713, 0.58, 0.62, 1.0, 0.0)
hold_ev = brain.expected_value(0.37, 0.713, 0.58, 0.62, 0.0, 0.0)
print(f"EV math ✅  sell-all @0.62 from 0.37 = {sell_ev*100:+.2f}c vs pure hold {hold_ev*100:+.2f}c "
      f"(idea doc: +7.2c vs +5.4c)")
assert sell_ev > hold_ev

random.seed(7)


def synth_window():
    """🌙 Tighter windows touch more often. Injected on purpose, so we can check
    the model FINDS it. This is a test fixture, not a claim about markets."""
    coa = random.uniform(0.0, 0.20)
    xs = {'coa': coa, 'cushion_bps': coa * 8, 'atr4_bps': random.uniform(4, 14),
          'atr_ratio': random.uniform(0.6, 1.6), 'dog_ask': random.uniform(0.22, 0.45),
          'spread': random.uniform(0.01, 0.05), 'secs_left': random.uniform(5, 60),
          'dog_is_up': float(random.random() > .5), 'rate_mult': random.uniform(0.5, 2.5),
          'hour_et': float(random.randrange(24))}
    touched = random.random() < max(0.05, min(0.95, 0.9 - 1.5 * coa))
    won = (random.random() < 0.62) if touched else False
    return xs, touched, won


for _ in range(400):
    xs, t, wn = synth_window()
    brain.learn(xs, t, wn if t else None)
print(f"after 400 windows: {brain.mode()} | trust {brain.trust()*100:.0f}% | "
      f"touch logloss {brain.touch_ll.get():.4f} | stick logloss {brain.stick_ll.get():.4f}")
tight = dict(x, coa=0.02)
wide = dict(x, coa=0.19)
pt, pw = brain.predict(tight)['p_touch'], brain.predict(wide)['p_touch']
print(f"P(touch) at coa 0.02 = {pt:.3f} vs coa 0.19 = {pw:.3f} ✅ (learned the injected signal)")
assert pt > pw, "the model failed to learn the injected coa → touch relationship"

for ps in (0.30, 0.50, 0.58, 0.66, 0.85, 0.99):
    a, fr, src = brain.plan_exit(ps, 1.0)
    assert B.MIN_SELL_ASK <= a <= B.MAX_SELL_ASK and a >= B.BREAKEVEN_ASK, a
    assert B.MIN_SELL_FRACTION <= fr <= 1.0
    print(f"   P(win|touch) {ps:.2f} → harvest ask ${a:.2f} on {fr*100:.0f}% ({src})")
print("exit pricing never breaches the 0.60 floor, size tracks the model ✅")

brain.save()
b2 = B.FlipBrain(path=B.MODEL_FILE)
assert b2.n_touch == brain.n_touch and abs(b2.predict(tight)['p_touch'] - pt) < 1e-9
print(f"pickle round-trip ✅  reloaded {b2.n_touch} labeled windows")

F.set_groups("core,ta")
b3 = B.FlipBrain(path=B.MODEL_FILE)
assert b3.n_touch == 0 and len(b3.FEATURES) == 18, (b3.n_touch, len(b3.FEATURES))
print(f"feature-set guard ✅  group change → cold start on {len(b3.FEATURES)} inputs")
F.set_groups("core")

b4 = B.FlipBrain(path=os.path.join(tmp, "b4.pkl"))
for _ in range(300):
    b4.learn(synth_window()[0], True, True)      # a long stretch of "always sticks"
before = b4.drift_events
for _ in range(300):
    b4.learn(synth_window()[0], False, None)     # ...then the world stops touching
assert b4.drift_events > before, "ADWIN never fired on a regime flip"
print(f"ADWIN drift detection ✅  {before} → {b4.drift_events} drift events on a regime flip")

# ============================================================================
# 🌙 PART 4, the polars log → resolve → learn → PnL loop
# ============================================================================
brain = B.FlipBrain(path=os.path.join(tmp, "loop.pkl"))
x = {k: 1.0 for k in brain.FEATURES}
past = int(time.time()) - 1000

B.log_window(market_ts=past, window_slug="btc-updown-5m-A", strike=100000.0, dog_side="UP",
             action="ENTER", entry_fill_price=0.35, shares=28, sell_ask_posted=0.62,
             sell_filled=True, sell_fill_price=0.62, shares_sold=28, shares_held=0,
             max_bid_after_touch=0.63, outcome="PENDING", learnable=True, exit_source="STATIC",
             features_json=json.dumps(x), p_touch=0.713, p_stick=0.58, model_mode="COLD")
B.log_window(market_ts=past, window_slug="btc-updown-5m-B", strike=100000.0, dog_side="DOWN",
             action="ENTER", entry_fill_price=0.40, shares=25, sell_ask_posted=0.62,
             sell_filled=False, sell_fill_price='', shares_sold=0, shares_held=25,
             max_bid_after_touch=0.51, outcome="PENDING", learnable=True, exit_source="MODEL",
             features_json=json.dumps(x), p_touch=0.70, p_stick=0.60, model_mode="LIVE")
B.log_window(market_ts=past, window_slug="btc-updown-5m-C", strike=100000.0, dog_side="UP",
             action="SKIP_PRICE", outcome="PENDING", learnable=True,
             features_json=json.dumps(x), p_touch=0.71, p_stick=0.58, model_mode="COLD")

# offline stand-ins for the only two calls that need the network
B.get_window_winner = lambda slug: {"btc-updown-5m-A": "UP", "btc-updown-5m-B": "UP",
                                    "btc-updown-5m-C": "DOWN"}[slug]
B.did_dog_touch = lambda ts, strike, side, from_sec=240: (True, 12.5)
B.resolve_and_learn(brain)

rows = {r['window_slug']: r for r in pl.read_csv(B.LOG_FILE, infer_schema_length=0).to_dicts()}
a, b, c = rows["btc-updown-5m-A"], rows["btc-updown-5m-B"], rows["btc-updown-5m-C"]
assert abs(float(a['pnl_scalp_usd']) - 28 * (0.62 - 0.35)) < 0.01, a['pnl_scalp_usd']
assert abs(float(a['pnl_total_usd']) - (28 * 0.27 - B.taker_fee_est(0.35, 28))) < 0.01
assert math.isfinite(float(b['pnl_total_usd'])) and math.isfinite(float(b['pnl_scalp_usd'])), "NaN leaked into PnL"
assert abs(float(b['pnl_total_usd']) - (-25 * 0.40 - B.taker_fee_est(0.40, 25))) < 0.01, b['pnl_total_usd']
assert a['dog_won'] == "True" and b['dog_won'] == "False"
assert float(c['pnl_total_usd']) == 0.0 and c['dog_won'] == "False"
assert all(r['learned'] == "True" for r in rows.values())
assert brain.n_touch == 3, brain.n_touch          # the SKIPPED window trained the brain too
print(f"resolve + PnL + learn ✅  harvested window ${float(a['pnl_total_usd']):+.2f} | "
      f"held loser ${float(b['pnl_total_usd']):+.2f} | brain learned {brain.n_touch} windows "
      f"including the SKIP")

B.resolve_and_learn(brain)
assert brain.n_touch == 3, "a row was learned twice!"
print("no double-learning on a second pass ✅")
assert B.kill_switch_active() is False and B.daily_stop_hit() is False
print(f"risk rails ✅  today ${B.todays_pnl():+.2f}, kill switch off, daily stop off")

b5 = B.FlipBrain(path=os.path.join(tmp, "replay.pkl"))
B.replay(B.LOG_FILE, b5)
assert b5.n_touch == 3, b5.n_touch
print("\n🌙 ALL SELF TESTS PASSED, and none of them prove an edge. Go read the README. 🌙")
