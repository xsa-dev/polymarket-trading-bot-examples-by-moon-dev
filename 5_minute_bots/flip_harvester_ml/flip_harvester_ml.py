#!/opt/anaconda3/envs/tflow/bin/python
"""
================================================================================
🌙 MOON DEV's FLIP HARVESTER ML v1.0 (river online-learning edition)
================================================================================
The FLIP HARVESTER idea (flip_harvester_IDEA.md), finally built, with the two
hardcoded numbers replaced by two ONLINE models that learn from every window.

THE ORIGINAL IDEA (all numbers real, from flip_harvester_IDEA.md):
  Coin-flip dogs TOUCH the strike 71.3% of the time but only WIN 42.4%
  (n=10,180 real 5-min windows). The whole fleet holds to resolution and lets
  that 29-point gap die at zero. Flip Harvester rests a maker SELL the moment
  the dog fills, harvesting the touch instead of praying for the close.
  Breakeven flip-exit price = P(win)/P(touch) = 0.424/0.713 = 0.5947.

WHAT RIVER ADDS (this is the "improved version", and the only new claim):
  The idea doc hardcodes TWO numbers, and both are averages over a 52-week
  backtest that has already ended:
     • the exit ask, a flat 0.62 for every window, forever
     • the exit size, 100% in high vol / 50% in low vol, split at the ATR median
  Both are really estimates of ONE quantity: P(dog WINS | dog TOUCHED), the fair
  value of the token at the touch moment (0.58 pooled, 0.553 high vol, 0.609 low
  vol). A static number cannot track a market that adapts. So:

     touch_model  →  P(touch)          online logistic regression (river)
     stick_model  →  P(win | touched)  online logistic regression (river)

  and every window prices its OWN exit:
     exit ask   = clamp(p_stick + EDGE_MARGIN, 0.60, 0.90)   ← 0.60 hard floor,
                  because 0.5947 is the mathematical breakeven vs just holding
     exit size  = 100% when the model says the touch FADES,
                  50% when it says the touch STICKS (the doc's vol rule, learned
                  per-window from the real features instead of an ATR median)
  Plus a river ADWIN drift detector on the models' own errors: when the edge
  decays, the bot SEES it, falls back to the static priors and pauses. That is
  the thing a hardcoded 0.62 can never do.

HARD RAILS (the ML may only VETO a trade, never unlock one):
  ❌ NO entry outside dog ask 0.22-0.45, the live ladder says 0.45+ is -11.4% EV
     and sub-0.22 dogs are correctly-priced trash (18.5% win @ 0.169)
  ❌ NO entry unless coa ≤ 0.20 AND |cushion| ≤ 1.5 bps (the proven july_17th gate)
  ❌ NO resting sell below 0.60, EVER. Below 0.5947 the bracket destroys edge.
  ❌ NO stop loss, NO cancel-and-chase down. Unfilled at T-0 → hold to resolution,
     worst case we ARE coinflip_discount_dog (still +EV, nothing lost).
  ✅ COLD START: until MIN_TRAIN_N labeled windows the bot trades the doc's static
     0.62 / vol-split rules and only SHADOW-logs what the model would have done.
     The model has to earn its say. Every row logs which mode was live.
  ✅ Every window logs one row, entries AND skips, including `touched` and
     `max_bid_after_touch`, the ONE untested link in the whole chain.

⚠️ HONEST WARNING: the 71.3% touch rate is physical backtest fact. Whether the
   CLOB bid actually PRINTS 0.60+ during a flip has never been logged in this
   repo, and the ML layer has NO backtest at all, it starts at the priors and
   learns from your fills. This is an experiment with a logger, not an edge.
   Not financial advice. Use at your own risk.

Usage:
  python flip_harvester_ml.py                 # run the bot (PAPER_MODE at top)
  python flip_harvester_ml.py --replay FILE   # offline-train from a CSV/JSON log
  python flip_harvester_ml.py --report        # print model state + weights

Account: AUG14 | CLOB V2 SDK (V1 post_order now throws PolyApiException)
Built by Moon Dev 🌙
================================================================================
"""

import sys
import os

# 🌙 Moon Dev - Auto re-exec with tflow python if we're in the wrong env
TFLOW_PYTHON = "/opt/anaconda3/envs/tflow/bin/python"
if os.path.exists(TFLOW_PYTHON) and sys.executable != TFLOW_PYTHON:
    os.execv(TFLOW_PYTHON, [TFLOW_PYTHON] + sys.argv)

import time
import json
import math
import pickle
import argparse
import csv
import requests
import polars as pl
from collections import deque
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from termcolor import colored

# 🌙 Moon Dev - river is THE dependency of this bot: pip install river
try:
    from river import compose, linear_model, preprocessing, optim, drift, metrics
except ImportError:
    print("❌ Moon Dev - river not installed! This bot IS the river bot: pip install river polars")
    sys.exit(1)

# 🌙 Moon Dev - the switch board for WHAT the brain is allowed to look at
# (core window / technical indicators / Hyperliquid microstructure / correlation).
# Everything about feature control lives in features.py, see its docstring.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import features as FEAT

# ============================================================================
# 🌙 MOON DEV - PATH SETUP
# ============================================================================
BOT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(BOT_DIR))              # repo root
MOON_DEV_API_PATH = "/Users/md/Dropbox/dev/github/moon-dev-trading-bots"

load_dotenv(os.path.join(PROJECT_ROOT, '.env'))
sys.path.insert(0, MOON_DEV_API_PATH)

# ============================================================================
# 🌙 MOON DEV - KEY PARAMS (entry gates are INHERITED, the exit is the experiment)
# ============================================================================
PAPER_MODE = True                   # 🌙 Brand new bot + unproven ML layer = paper first.
BTC_FEED = os.getenv("FLIP_BTC_FEED", "auto")   # auto | moondev | binance (see get_btc_ticks)

# === Entry gate (coinflip_discount_dog's proven gate, unchanged, DO NOT loosen) ===
MAX_COA = 0.20                      # |cushion| / ATR4 ≤ 0.20 (the coin-flip pocket)
MAX_CUSHION_BPS = 1.5               # AND |cushion| ≤ 1.5 bps of BTC spot
DOG_PRICE_ZONE = (0.22, 0.45)       # 0.20-0.40 is the only +EV taker band; 0.45+ = -11.4% EV
ENTRY_WINDOW = (240, 295)           # elapsed seconds: T-60 → T-5 of the 300s window
MAX_SPREAD = 0.06                   # Dog books are thin, but 6c+ is untradeable

# === Exit engine (THE experiment: static doc numbers vs the river models) ===
STATIC_SELL_ASK = 0.62              # The doc's flat ask, used during COLD start
BREAKEVEN_ASK = 0.5947              # P(win)/P(touch) = 0.424/0.713, the math floor
MIN_SELL_ASK = 0.60                 # HARD floor, one cent of daylight over breakeven
MAX_SELL_ASK = 0.90                 # Above this nobody lifts a flip, it just never fills
EDGE_MARGIN = 0.04                  # Rest this far OVER model fair value (the doc's
                                    # 0.62 vs 0.58 fair = exactly 4c, kept honest)
STICK_FADE = 0.55                   # p_stick below this → touch FADES → sell 100%
STICK_HOLD = 0.65                   # p_stick above this → touch STICKS → sell 50%, ride half
MIN_SELL_FRACTION = 0.50            # Never harvest less than half, that's the whole thesis

# === The river layer ===
MIN_TRAIN_N = 40                    # Labeled windows before the model gets ANY say
FULL_TRUST_N = 200                  # ...and full say only here. Between = blended w/ priors
PRIOR_P_TOUCH = 0.713               # 52-week backtest, coa ≤ 0.20, n=10,180
PRIOR_P_STICK = 0.580               # P(win | touch) pooled. High vol 0.553 / low vol 0.609
LEARNING_RATE = 0.02                # SGD step, small: markets are noisy, we are not fitting
L2 = 0.001                          # ...and regularized, 11 features on a few hundred rows
DRIFT_DELTA = 0.002                 # river ADWIN sensitivity on model error
DRIFT_COOLDOWN_WINDOWS = 20         # Drift → back to priors + no entries for N windows
MODEL_VERSION = 3                   # Bump when FEATURES change → old pickle is discarded

# === Risk (fleet standard) ===
BASE_SIZE_USD = 10                  # $10 flat per window, the doc's sizing
MIN_SHARES = 5                      # Polymarket minimum order size
MIN_EV_PER_SHARE = 0.02             # Model must see ≥ 2c/share of edge or we skip
MAX_CONCURRENT = 1                  # One window at a time (this bot polls one window)
DAILY_STOP_USD = -60                # Down $60 on the day → done, print the loss table
KILL_SWITCH_WR = 0.30               # Trailing-30 TOTAL win rate < 30% → pause (base is 42%)
KILL_SWITCH_WINDOW = 30
KILL_SWITCH_MIN_TRADES = 15

# === Loop / market ===
BOT_POLL_INTERVAL = 3               # The flip happens in the last 60s, poll fast
MARKET_DURATION = 300               # btc-updown-5m markets = 300 seconds
STATS_EVERY = 10                    # Rolling bucket stats every N resolved windows

# === Files ===
DATA_DIR = os.path.join(BOT_DIR, "data")
LOG_FILE = os.path.join(DATA_DIR, "flip_harvester_ml_log.csv")    # ONE ROW PER WINDOW,
                                                                  # entries AND skips (doc spec)
MODEL_FILE = os.path.join(DATA_DIR, "flip_harvester_ml_brain.pkl")

ET = timezone(timedelta(hours=-5))

# ============================================================================
# 🌙 MOON DEV - ACCOUNT CONFIGURATION (AUG14, keeps OG free)
# ============================================================================
ACCOUNT_SUFFIX = "_AUG14"
PRIVATE_KEY_ENV_NAME = f"PRIVATE_KEY{ACCOUNT_SUFFIX}"
PUBLIC_KEY_ENV_NAME = f"PUBLIC_KEY{ACCOUNT_SUFFIX}"
SIGNATURE_TYPE = 2                  # Gnosis Safe

# ============================================================================
# 🌙 MOON DEV - MOON DEV API (BTC tick feed: spot, strike, ATR4)
# ============================================================================
_API_CACHE = None


def get_api():
    """🌙 Moon Dev - Lazy MoonDevAPI so --report / --replay run with no keys."""
    global _API_CACHE
    if _API_CACHE is not None:
        return _API_CACHE
    from api import MoonDevAPI
    api = MoonDevAPI()
    if not api.api_key:
        print(colored("❌ Moon Dev - MOONDEV_API_KEY not found in .env!", "red"))
        sys.exit(1)
    _API_CACHE = api
    return api

# ============================================================================
# 🌙 MOON DEV - V2 CLOB CLIENT (V1 post_order is dead, throws PolyApiException)
# ============================================================================
_CLIENT_CACHE = None


def _build_client():
    """🌙 Moon Dev - cached CLOB V2 client for AUG14."""
    global _CLIENT_CACHE
    if _CLIENT_CACHE is not None:
        return _CLIENT_CACHE
    from py_clob_client_v2.client import ClobClient
    from py_clob_client_v2.clob_types import ApiCreds
    from py_clob_client_v2.constants import POLYGON
    from web3 import Web3

    key = os.getenv(PRIVATE_KEY_ENV_NAME)
    pub = os.getenv(PUBLIC_KEY_ENV_NAME)
    if not key or not pub:
        print(colored(f"❌ Moon Dev - Missing {PRIVATE_KEY_ENV_NAME}/{PUBLIC_KEY_ENV_NAME} in .env!", "red"))
        sys.exit(1)
    try:
        funder = Web3.toChecksumAddress(pub)
    except AttributeError:
        funder = Web3.to_checksum_address(pub)

    client = ClobClient(host="https://clob.polymarket.com", chain_id=POLYGON,
                        key=key, signature_type=SIGNATURE_TYPE, funder=funder)
    api_key = os.getenv(f"API_KEY{ACCOUNT_SUFFIX}")
    api_secret = os.getenv(f"API_SECRET{ACCOUNT_SUFFIX}") or os.getenv(f"SECRET{ACCOUNT_SUFFIX}")
    passphrase = os.getenv(f"PASSPHRASE{ACCOUNT_SUFFIX}")
    if api_key and api_secret and passphrase:
        client.set_api_creds(ApiCreds(api_key=api_key, api_secret=api_secret, api_passphrase=passphrase))
    else:
        client.set_api_creds(client.create_or_derive_api_creds())
    _CLIENT_CACHE = client
    return client


def place_taker_buy(token_id, price, size):
    """🌙 Moon Dev - Marketable GTC BUY that CROSSES the ask (post_only=False).
    GTC not FAK/FOK: Polymarket 400s marketable FAK/FOK under $1, verified live."""
    from py_clob_client_v2.clob_types import OrderArgs, OrderType
    client = _build_client()
    args = OrderArgs(token_id=str(token_id), price=float(price), size=float(size), side="BUY")
    print(colored(f"   🐶 Moon Dev - TAKING the dog @ ${price:.3f} x{size} shares", "cyan", attrs=['bold']))
    try:
        return client.create_and_post_order(args, order_type=OrderType.GTC, post_only=False) or {}
    except Exception as e:
        err = str(e).lower()
        if any(t in err for t in ['timeout', 'readtimeout', 'duplicated', 'request exception']):
            print(colored("   ⚠️ Moon Dev - buy timed out, will verify via positions", "yellow"))
            return {"status": "timeout"}
        print(colored(f"   ❌ Moon Dev - buy failed: {type(e).__name__}: {e}", "red"))
        return {}


def place_maker_sell(token_id, price, size):
    """🌙 Moon Dev - The bracket: a RESTING GTC maker sell (post_only=True).
    This is the whole bot. If it fills we harvested the flip; if it doesn't we
    degrade into a plain hold-to-resolution dog bot, which is still +EV."""
    from py_clob_client_v2.clob_types import OrderArgs, OrderType
    client = _build_client()
    args = OrderArgs(token_id=str(token_id), price=float(price), size=float(size), side="SELL")
    print(colored(f"   🪤 Moon Dev - RESTING the harvest ask @ ${price:.3f} x{size} shares (maker, post_only)",
                  "magenta", attrs=['bold']))
    try:
        return client.create_and_post_order(args, order_type=OrderType.GTC, post_only=True) or {}
    except Exception as e:
        print(colored(f"   ❌ Moon Dev - sell post failed: {type(e).__name__}: {e}", "red"))
        return {}


def cancel_token_orders(token_id):
    """🌙 Moon Dev - Cancel resting orders on a token (GTC remainders + the bracket)."""
    from py_clob_client_v2.clob_types import OrderMarketCancelParams
    try:
        _build_client().cancel_market_orders(OrderMarketCancelParams(asset_id=str(token_id)))
    except Exception as e:
        print(colored(f"   ⚠️ Moon Dev - cancel failed: {type(e).__name__}", "yellow"))


def get_position_size(token_id):
    """🌙 Moon Dev - Shares of token_id actually held (fill check for BOTH legs)."""
    pub = os.getenv(PUBLIC_KEY_ENV_NAME)
    try:
        r = requests.get("https://data-api.polymarket.com/positions",
                         params={'user': pub, 'limit': 500, 'sortBy': 'CURRENT', 'sortDirection': 'DESC'},
                         timeout=10)
        if r.status_code != 200:
            return 0.0
        for pos in r.json():
            if str(pos.get('asset')) == str(token_id):
                return float(pos.get('size', 0))
    except Exception as e:
        print(colored(f"   ⚠️ Moon Dev - positions check failed: {type(e).__name__}", "yellow"))
    return 0.0

# ============================================================================
# 🌙 MOON DEV - BTC TAPE (strike = window open, cushion, ATR4, all from real ticks)
# ============================================================================


BINANCE_KLINES = "https://api.binance.com/api/v3/klines"
_TICK_CACHE = {'at': 0.0, 'ticks': []}
TICK_CACHE_TTL = 5          # seconds; one loop asks for the tape up to 3 times


def _moondev_available():
    """🌙 Moon Dev - Is the Moon Dev tape actually usable on THIS box? No key or no
    api.py (it does not ship in this repo) means no, and we say so instead of dying."""
    if not os.getenv("MOONDEV_API_KEY"):
        return False
    try:
        import api  # noqa: F401
    except ImportError:
        return False
    return True


def _binance_ticks(lookback_sec=3600):
    """🌙 Moon Dev - The keyless BTC tape: Binance 1s klines, no account, no key.

    One kline per second becomes four ticks (open, low, high, close) so that the
    1-minute high/low ATR4 is built from, and the strike crossing did_dog_touch
    looks for, both survive at 1-second resolution.

    Each second's REAL trade count rides along as `n`. rate_mult is a tick-RATE
    feature: a synthetic 4-ticks-per-second tape would pin it at exactly 1.0
    forever and feed the model a dead column. The trade count is the honest
    stand-in, and it is the only thing `n` is used for."""
    out, end = [], int(time.time() * 1000)
    floor_ms = end - lookback_sec * 1000
    while end > floor_ms:
        try:
            rows = requests.get(BINANCE_KLINES, params={
                'symbol': 'BTCUSDT', 'interval': '1s',
                'endTime': end, 'limit': 1000}, timeout=10).json()
        except Exception as e:
            print(colored(f"   ⚠️ Moon Dev - Binance tape failed: {type(e).__name__}", "yellow"))
            break
        if not isinstance(rows, list) or not rows:
            break
        for k in rows:
            ms, n = k[0], int(k[8])
            if n <= 0:                      # a second with no trades is not a tick
                continue
            o, h, l, c = float(k[1]), float(k[2]), float(k[3]), float(k[4])
            out.extend([{'t': ms, 'p': o, 'n': n}, {'t': ms + 250, 'p': l, 'n': 0},
                        {'t': ms + 500, 'p': h, 'n': 0}, {'t': ms + 750, 'p': c, 'n': 0}])
        if len(rows) < 1000:
            break
        end = rows[0][0] - 1
    out.sort(key=lambda t: t['t'])
    return out


def get_btc_ticks(lookback="1h", limit=10000):
    """🌙 Moon Dev - Raw BTC ticks [{'t': ms, 'p': price}, ...] or [] on a dead feed.

    FLIP_BTC_FEED=auto (default) takes the Moon Dev tape when the key is there and
    falls back to Binance, so the bot runs on a box with no Moon Dev subscription.
    Pin it with FLIP_BTC_FEED=moondev or =binance. Cached for TICK_CACHE_TTL so one
    loop does not pull an hour of klines three times over."""
    now = time.time()
    if _TICK_CACHE['ticks'] and now - _TICK_CACHE['at'] < TICK_CACHE_TTL:
        return _TICK_CACHE['ticks']

    ticks = []
    if BTC_FEED in ('auto', 'moondev') and _moondev_available():
        resp = get_api().get_ticks("BTC", lookback, limit=limit)
        if resp and isinstance(resp, dict):
            ticks = resp.get('ticks', []) or []
    elif BTC_FEED == 'moondev':
        print(colored("❌ Moon Dev - FLIP_BTC_FEED=moondev but no MOONDEV_API_KEY / api.py", "red"))
        sys.exit(1)

    if not ticks and BTC_FEED in ('auto', 'binance'):
        ticks = _binance_ticks(3600)

    _TICK_CACHE.update(at=now, ticks=ticks)
    return ticks


def active_btc_feed():
    """🌙 Moon Dev - Which tape is actually live, for the startup banner and the log."""
    if BTC_FEED in ('auto', 'moondev') and _moondev_available():
        return 'moondev'
    return 'binance' if BTC_FEED in ('auto', 'binance') else BTC_FEED


def _bars_from_ticks(ticks, start_ms, bar_sec=60, n_bars=4):
    """🌙 Moon Dev - Bucket ticks into n_bars 1-minute bars → [(high, low), ...].
    Real ticks only, a missing bar is DROPPED, never interpolated."""
    bars = []
    for i in range(n_bars):
        lo_ms = start_ms + i * bar_sec * 1000
        hi_ms = lo_ms + bar_sec * 1000
        prices = [t.get('p', t.get('price')) for t in ticks
                  if lo_ms <= t.get('t', 0) < hi_ms and t.get('p', t.get('price'))]
        if len(prices) >= 2:
            bars.append((max(prices), min(prices)))
    return bars


def read_window_tape(market_ts):
    """🌙 Moon Dev - Everything the models need from the BTC tape, in one shot.

    strike     = window OPEN price (Polymarket resolves 5-min Up/Down vs the open)
    cushion    = spot - strike, the leader's lead; the DOG is the side that's behind
    atr4       = mean (high-low) of the first four 1-minute bars of THIS window
    coa        = |cushion| / atr4, the coin-flip-ness of the window (≤0.20 = flip)
    Returns a dict, or None when the feed is too thin. NO DATA → NO TRADE. Ever."""
    ticks = get_btc_ticks("1h", limit=10000)
    if len(ticks) < 100:
        return None
    start_ms = market_ts * 1000
    win_ticks = [t for t in ticks if t.get('t', 0) >= start_ms]
    if len(win_ticks) < 20:
        return None

    prices = [t.get('p', t.get('price', 0)) for t in win_ticks]
    strike = prices[0]
    spot = prices[-1]
    if not strike or not spot:
        return None

    bars = _bars_from_ticks(win_ticks, start_ms, bar_sec=60, n_bars=4)
    if len(bars) < 3:                      # need at least 3 real bars to call it ATR4
        return None
    atr4 = sum(h - l for h, l in bars) / len(bars)
    if atr4 <= 0:
        return None

    cushion = spot - strike
    cushion_bps = abs(cushion) / strike * 10_000
    coa = abs(cushion) / atr4

    # 🌙 Moon Dev - trailing-hour ATR median for the vol regime (the doc's lever,
    # kept as a FEATURE for the model instead of a hardcoded 50/50 split)
    hour_bars = _bars_from_ticks(ticks, ticks[0].get('t', start_ms), bar_sec=60, n_bars=60)
    ranges = sorted(h - l for h, l in hour_bars) if hour_bars else []
    atr_med = ranges[len(ranges) // 2] if ranges else atr4

    # 🌙 Moon Dev - tick-rate multiple: is this window busier than the hour?
    elapsed = max(1, int(time.time()) - market_ts)
    if any('n' in t for t in win_ticks):        # keyless tape: count trades, not ticks
        win_rate = sum(t.get('n', 0) for t in win_ticks) / elapsed
        hour_rate = sum(t.get('n', 0) for t in ticks) / 3600
    else:
        win_rate = len(win_ticks) / elapsed
        hour_rate = len(ticks) / 3600
    rate_mult = (win_rate / hour_rate) if hour_rate else 0.0

    return {
        'strike': strike, 'spot': spot, 'cushion': cushion,
        'cushion_bps': cushion_bps, 'atr4': atr4, 'atr4_bps': atr4 / strike * 10_000,
        'atr_med': atr_med, 'atr_ratio': (atr4 / atr_med) if atr_med else 1.0,
        'coa': coa, 'rate_mult': rate_mult, 'n_ticks': len(win_ticks),
        # 🌙 dog = the side that is BEHIND. spot above strike → UP leads → dog is DOWN
        'dog_side': "DOWN" if cushion > 0 else "UP",
        'move_bps': cushion / strike * 10_000,
    }


def did_dog_touch(market_ts, strike, dog_side, from_sec=240):
    """🌙 Moon Dev - THE LABEL. After the window closed: did spot cross back through
    the strike in the dog's favor at any point after our entry window opened?
    A dog that never touches can NEVER win, so touch is a strict superset of win.
    Returns (touched: bool|None, seconds_left_at_touch: float|None)."""
    ticks = get_btc_ticks("1h", limit=10000)
    if not ticks:
        return None, None
    lo_ms = (market_ts + from_sec) * 1000
    hi_ms = (market_ts + MARKET_DURATION) * 1000
    seg = [t for t in ticks if lo_ms <= t.get('t', 0) <= hi_ms]
    if len(seg) < 3:
        return None, None
    for t in seg:
        px = t.get('p', t.get('price', 0))
        if not px:
            continue
        crossed = (px >= strike) if dog_side == "UP" else (px <= strike)
        if crossed:
            secs_left = (hi_ms - t.get('t', 0)) / 1000.0
            return True, round(secs_left, 1)
    return False, None

# ============================================================================
# 🌙 MOON DEV - MARKET DISCOVERY + BOOK
# ============================================================================


def get_current_market_timestamp():
    return (int(time.time()) // MARKET_DURATION) * MARKET_DURATION


def get_time_remaining(market_ts):
    return MARKET_DURATION - (int(time.time()) - market_ts)


def get_market_info(market_ts):
    """🌙 Moon Dev - Find the active btc-updown-5m market + UP/DOWN token ids"""
    slug = f"btc-updown-5m-{market_ts}"
    try:
        r = requests.get("https://gamma-api.polymarket.com/markets",
                         params={'slug': slug, 'closed': 'false', 'active': 'true'}, timeout=10)
        if r.status_code != 200 or not r.json():
            return None
        market = r.json()[0]
        token_ids = json.loads(market['clobTokenIds'])   # [UP, DOWN], matches outcomes
        print(colored(f"   ✅ Moon Dev - Found market: {market['question']}", "green"))
        return {'market_id': market['id'], 'up_token_id': token_ids[0],
                'down_token_id': token_ids[1], 'question': market['question'], 'slug': slug}
    except Exception as e:
        print(colored(f"   ⚠️ Moon Dev - market lookup failed: {type(e).__name__}", "yellow"))
        return None


def get_order_book(token_id):
    """🌙 Moon Dev - Best bid/ask from the CLOB"""
    try:
        r = requests.get("https://clob.polymarket.com/book", params={'token_id': token_id}, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        bids, asks = data.get('bids', []), data.get('asks', [])
        if not bids or not asks:
            return None
        best_bid = float(bids[-1]['price'])
        best_ask = float(asks[0]['price'])
        return {'best_bid': best_bid, 'best_ask': best_ask, 'spread': best_ask - best_bid}
    except Exception:
        return None


def taker_fee_est(price, shares):
    """🌙 Moon Dev - 5-min crypto taker fee ≈ 0.10 x min(p, 1-p) per share"""
    return round(0.10 * min(price, 1 - price) * shares, 2)

# ============================================================================
# 🌙 MOON DEV - 🧠 THE FLIP BRAIN (river online learning, the "improved" part)
# ============================================================================
# Two logistic regressions, trained one window at a time, never in batch:
#
#   touch_model : P(dog TOUCHES the strike before the close)     prior 0.713
#   stick_model : P(dog WINS | it touched)                       prior 0.580
#                 ...trained ONLY on windows that actually touched, because
#                 that is literally the conditional we need.
#
# stick_model output IS the fair value of the dog token at the touch moment, so
# it prices the exit directly: rest EDGE_MARGIN above fair, never below 0.60.
# touch_model tells us how often that exit gets a chance to fill at all, which
# is what turns the pair into an expected value we can gate the ENTRY on.
#
# Trust ramps: the model says nothing until MIN_TRAIN_N labeled windows, then
# blends with the backtest priors until FULL_TRUST_N. An ADWIN drift detector
# watches its own error and yanks trust back to zero when the market changes.
# ============================================================================


class FlipBrain:
    """🌙 Moon Dev's online brain. Learns the flip, one real window at a time."""

    def __init__(self, path=MODEL_FILE):
        self.path = path
        # 🌙 Moon Dev - the column list is whatever features.py currently has switched
        # ON. Change the groups → this list changes → the old pickle is refused.
        self.FEATURES = FEAT.active_feature_names()
        self.touch_model = self._new_model()
        self.stick_model = self._new_model()
        self.touch_drift = drift.ADWIN(delta=DRIFT_DELTA)
        self.stick_drift = drift.ADWIN(delta=DRIFT_DELTA)
        self.touch_ll = metrics.LogLoss()
        self.stick_ll = metrics.LogLoss()
        self.n_touch = 0            # labeled windows seen SINCE the last drift reset
        self.n_stick = 0            # ...of which actually touched (stick training rows)
        self.total_seen = 0         # lifetime, never reset, for the report
        self.drift_cooldown = 0     # windows left with entries paused after a drift
        self.drift_events = 0
        self.recent_touch_hits = deque(maxlen=50)   # rolling accuracy, for the console
        self.recent_stick_hits = deque(maxlen=50)
        self.version = MODEL_VERSION
        self.load()

    # ---- plumbing ---------------------------------------------------------
    @staticmethod
    def _new_model():
        """🌙 Moon Dev - scaler + logistic regression, small LR, real L2.
        11 features on a few hundred noisy windows: regularize or hallucinate."""
        return compose.Pipeline(
            preprocessing.StandardScaler(),
            linear_model.LogisticRegression(optimizer=optim.SGD(LEARNING_RATE), l2=L2),
        )

    def save(self):
        """🌙 Moon Dev - Persist the brain, a bot that forgets on restart learns nothing."""
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, 'wb') as f:
                pickle.dump({
                    'version': MODEL_VERSION, 'features': self.FEATURES,
                    'groups': FEAT.active_groups(),
                    'touch_model': self.touch_model, 'stick_model': self.stick_model,
                    'touch_drift': self.touch_drift, 'stick_drift': self.stick_drift,
                    'touch_ll': self.touch_ll, 'stick_ll': self.stick_ll,
                    'n_touch': self.n_touch, 'n_stick': self.n_stick,
                    'total_seen': self.total_seen, 'drift_cooldown': self.drift_cooldown,
                    'drift_events': self.drift_events,
                    'recent_touch_hits': list(self.recent_touch_hits),
                    'recent_stick_hits': list(self.recent_stick_hits),
                }, f)
        except Exception as e:
            print(colored(f"   ⚠️ Moon Dev - brain save failed: {type(e).__name__}: {e}", "yellow"))

    def load(self):
        """🌙 Moon Dev - Reload the brain. A version/feature mismatch DISCARDS it,
        a scaler fitted on different columns is worse than no model at all."""
        if not os.path.exists(self.path):
            print(colored("   🧠 Moon Dev - No brain on disk, starting COLD at the backtest priors", "yellow"))
            return
        try:
            with open(self.path, 'rb') as f:
                s = pickle.load(f)
            if s.get('version') != MODEL_VERSION or s.get('features') != self.FEATURES:
                print(colored(f"   🧠 Moon Dev - Brain on disk was trained on a DIFFERENT feature set "
                              f"(groups {s.get('groups')} vs {FEAT.active_groups()}), discarding it. "
                              f"Flip the groups back to reuse it, or train this one from zero.", "yellow"))
                return
            self.touch_model = s['touch_model']
            self.stick_model = s['stick_model']
            self.touch_drift = s.get('touch_drift', self.touch_drift)
            self.stick_drift = s.get('stick_drift', self.stick_drift)
            self.touch_ll = s.get('touch_ll', self.touch_ll)
            self.stick_ll = s.get('stick_ll', self.stick_ll)
            self.n_touch = s.get('n_touch', 0)
            self.n_stick = s.get('n_stick', 0)
            self.total_seen = s.get('total_seen', self.n_touch)
            self.drift_cooldown = s.get('drift_cooldown', 0)
            self.drift_events = s.get('drift_events', 0)
            self.recent_touch_hits = deque(s.get('recent_touch_hits', []), maxlen=50)
            self.recent_stick_hits = deque(s.get('recent_stick_hits', []), maxlen=50)
            print(colored(f"   🧠 Moon Dev - Brain loaded: {self.n_touch} labeled windows "
                          f"({self.n_stick} touched) | trust {self.trust()*100:.0f}%", "green"))
        except Exception as e:
            print(colored(f"   ⚠️ Moon Dev - brain load failed ({type(e).__name__}), starting COLD", "yellow"))

    # ---- features ---------------------------------------------------------
    def build_features(self, tape, dog_ask, spread, secs_left):
        """🌙 Moon Dev - The full feature vector for one window: the core window
        numbers, plus every group switched on in features.py (technical indicators,
        Hyperliquid microstructure, cross-asset correlation).

        Returns (x, "OK") or (None, reason). A missing feed is a SKIP, never a zero."""
        core = {
            'coa': float(tape['coa']),
            'cushion_bps': float(tape['cushion_bps']),
            'atr4_bps': float(tape['atr4_bps']),
            'atr_ratio': float(tape['atr_ratio']),
            'dog_ask': float(dog_ask),
            'spread': float(spread),
            'secs_left': float(secs_left),
            'dog_is_up': 1.0 if tape['dog_side'] == "UP" else 0.0,
            'rate_mult': float(tape['rate_mult']),
            'hour_et': float(datetime.now(ET).hour),
        }
        return FEAT.build_feature_vector(core, spot_price=tape.get('spot'))

    # ---- prediction -------------------------------------------------------
    def trust(self):
        """🌙 Moon Dev - How much say the model gets, 0.0 → 1.0.
        Zero until MIN_TRAIN_N, full at FULL_TRUST_N, zero again after a drift."""
        if self.drift_cooldown > 0 or self.n_touch < MIN_TRAIN_N:
            return 0.0
        span = max(1, FULL_TRUST_N - MIN_TRAIN_N)
        return max(0.0, min(1.0, (self.n_touch - MIN_TRAIN_N) / span))

    def mode(self):
        if self.drift_cooldown > 0:
            return "DRIFT"
        t = self.trust()
        return "COLD" if t <= 0 else ("BLEND" if t < 1 else "LIVE")

    def _p(self, model, x, prior):
        """🌙 Moon Dev - Blend model probability with the backtest prior by trust."""
        try:
            raw = model.predict_proba_one(x).get(True, prior)
        except Exception:
            raw = prior
        if raw is None or not (0.0 < raw < 1.0):
            raw = prior
        t = self.trust()
        return t * raw + (1 - t) * prior, raw

    def predict(self, x):
        """🌙 Moon Dev - P(touch) and P(win|touch) for THIS window."""
        p_touch, raw_touch = self._p(self.touch_model, x, PRIOR_P_TOUCH)
        p_stick, raw_stick = self._p(self.stick_model, x, PRIOR_P_STICK)
        return {'p_touch': p_touch, 'p_stick': p_stick,
                'raw_touch': raw_touch, 'raw_stick': raw_stick,
                'trust': self.trust(), 'mode': self.mode()}

    # ---- the exit plan (this is what replaces the doc's hardcoded 0.62) ----
    def plan_exit(self, p_stick, atr_ratio):
        """🌙 Moon Dev - Price and size the harvest.

        COLD/DRIFT → exactly the idea doc: flat 0.62, 100% in high vol, 50% in low.
        Otherwise  → ask = p_stick + EDGE_MARGIN (fair value at the touch, plus the
        same 4c of edge the doc's 0.62-over-0.58 was really asking for), and sell
        MORE when the model says the touch fades, LESS when it says it sticks.
        The 0.60 floor is not negotiable: 0.5947 is breakeven vs simply holding."""
        if self.trust() <= 0:
            ask = STATIC_SELL_ASK
            frac = 1.0 if atr_ratio >= 1.0 else MIN_SELL_FRACTION
            return round(ask, 2), frac, "STATIC"

        ask = math.ceil((p_stick + EDGE_MARGIN) * 100) / 100.0   # ceil to the cent, never round DOWN into the floor
        ask = min(MAX_SELL_ASK, max(MIN_SELL_ASK, ask))
        if p_stick <= STICK_FADE:
            frac = 1.0                       # touch fades → take the whole harvest
        elif p_stick >= STICK_HOLD:
            frac = MIN_SELL_FRACTION         # touch sticks → keep half for the $1.00
        else:
            span = max(1e-9, STICK_HOLD - STICK_FADE)
            frac = 1.0 - (1.0 - MIN_SELL_FRACTION) * (p_stick - STICK_FADE) / span
        return round(ask, 2), round(frac, 2), "MODEL"

    @staticmethod
    def expected_value(entry_px, p_touch, p_stick, sell_ask, frac, fee_per_share):
        """🌙 Moon Dev - EV per share of the WHOLE structure, in dollars.

        no touch  (1 - p_touch) : dog cannot win, the shares die at 0     → -entry
        touch     (p_touch)     : frac sold at sell_ask                   → +(ask - entry)
                                  (1-frac) held, wins with prob p_stick   → +(1-entry) / -entry
        A resting ask only fills when somebody pays over fair, so treating the sold
        leg as a certainty ON A TOUCH is the optimistic side of this estimate, that
        is exactly why the live `sell_filled` column exists to convict it."""
        held_ev = p_stick * (1 - entry_px) - (1 - p_stick) * entry_px
        on_touch = frac * (sell_ask - entry_px) + (1 - frac) * held_ev
        return p_touch * on_touch + (1 - p_touch) * (-entry_px) - fee_per_share

    # ---- learning ---------------------------------------------------------
    def learn(self, x, touched, dog_won):
        """🌙 Moon Dev - One resolved window → one learning step. Predict FIRST
        (progressive validation, river-style), then learn, then watch for drift."""
        if x is None or touched is None:
            return
        p_touch_pred = self.touch_model.predict_proba_one(x).get(True, PRIOR_P_TOUCH)
        self.touch_ll.update(bool(touched), p_touch_pred)
        self.touch_model.learn_one(x, bool(touched))
        self.n_touch += 1
        self.total_seen += 1
        self.recent_touch_hits.append(1 if (p_touch_pred >= 0.5) == bool(touched) else 0)
        self.touch_drift.update(abs(float(bool(touched)) - float(p_touch_pred)))

        # 🌙 Moon Dev - stick model only ever sees TOUCHED windows, that's the conditional
        if touched and dog_won is not None:
            p_stick_pred = self.stick_model.predict_proba_one(x).get(True, PRIOR_P_STICK)
            self.stick_ll.update(bool(dog_won), p_stick_pred)
            self.stick_model.learn_one(x, bool(dog_won))
            self.n_stick += 1
            self.recent_stick_hits.append(1 if (p_stick_pred >= 0.5) == bool(dog_won) else 0)
            self.stick_drift.update(abs(float(bool(dog_won)) - float(p_stick_pred)))

        if self.drift_cooldown > 0:
            self.drift_cooldown -= 1
        if self.touch_drift.drift_detected or self.stick_drift.drift_detected:
            self._on_drift()
        self.save()

    def _on_drift(self):
        """🌙 Moon Dev - ADWIN says the error distribution moved: the market changed.
        We do NOT quietly keep trading a stale model. Trust → 0 (back to the doc's
        static 0.62 rules), entries paused, and the model has to re-earn its say."""
        self.drift_events += 1
        self.drift_cooldown = DRIFT_COOLDOWN_WINDOWS
        self.n_touch = 0                  # trust resets; the fitted weights stay and keep learning
        self.touch_drift = drift.ADWIN(delta=DRIFT_DELTA)
        self.stick_drift = drift.ADWIN(delta=DRIFT_DELTA)
        print(colored(f"\n   🌪️  Moon Dev - CONCEPT DRIFT #{self.drift_events} DETECTED (river ADWIN)! "
                      f"Back to the static 0.62 rules, entries paused for {DRIFT_COOLDOWN_WINDOWS} windows.",
                      "red", attrs=['bold']))

    # ---- reporting --------------------------------------------------------
    def report(self):
        """🌙 Moon Dev - What the brain currently believes, in plain english."""
        print(colored("\n🧠 MOON DEV'S FLIP BRAIN (river online models) 🧠", "magenta", attrs=['bold']))
        print(colored(f"   Features: groups {FEAT.active_groups()} → {len(self.FEATURES)} inputs", "white"))
        print(colored(f"   Mode: {self.mode()} | trust {self.trust()*100:.0f}% | "
                      f"labeled windows {self.n_touch} (lifetime {self.total_seen}) | "
                      f"touched rows {self.n_stick} | drifts {self.drift_events}", "white"))
        t_acc = (sum(self.recent_touch_hits) / len(self.recent_touch_hits) * 100) if self.recent_touch_hits else 0
        s_acc = (sum(self.recent_stick_hits) / len(self.recent_stick_hits) * 100) if self.recent_stick_hits else 0
        print(colored(f"   touch_model : logloss {self.touch_ll.get():.4f} | rolling acc {t_acc:.1f}% "
                      f"(n={len(self.recent_touch_hits)}) | prior {PRIOR_P_TOUCH:.3f}", "white"))
        print(colored(f"   stick_model : logloss {self.stick_ll.get():.4f} | rolling acc {s_acc:.1f}% "
                      f"(n={len(self.recent_stick_hits)}) | prior {PRIOR_P_STICK:.3f}", "white"))
        for name, model in (("touch", self.touch_model), ("stick", self.stick_model)):
            try:
                lr = model[-1]
                weights = sorted(lr.weights.items(), key=lambda kv: -abs(kv[1]))[:5]
                pretty = " | ".join(f"{k} {v:+.4f}" for k, v in weights)
                print(colored(f"   {name}_model top weights (scaled): {pretty}", "cyan"))
            except Exception:
                pass
        if self.trust() <= 0:
            print(colored(f"   ⚠️  Not trusted yet: trading the doc's static ${STATIC_SELL_ASK:.2f} exit. "
                          f"Needs {max(0, MIN_TRAIN_N - self.n_touch)} more labeled windows.", "yellow"))

# ============================================================================
# 🌙 MOON DEV - LOGGING (ONE ROW PER WINDOW, entries AND skips, doc spec)
# ============================================================================
# The three columns that pay for the next iteration, straight from the idea doc:
#   `touched`                 → is the 71.3% backtest touch rate real on live tape?
#   `max_bid_after_touch`     → does the CLOB bid actually PRINT 0.60+ on a flip?
#   `pnl_scalp / pnl_hold`    → which leg is actually earning?
# Plus the ML receipts: p_touch, p_stick, model_mode, trust, ev_per_share.
# `features_json` is what makes the whole thing learn: EVERY coin-flip window
# gets a label at resolution, entered or not, so the brain trains on ~10x more
# rows than we have fills.
#
# 🐻‍❄️ Data layer is POLARS, with two deliberate rules:
#   1. the CSV is read as ALL STRINGS (infer_schema_length=0). A log where every
#      column is blank until it is graded has no stable dtype, and a half-typed
#      column is how you get a NaN silently eating a PnL sum. Numbers are cast
#      exactly where they are used, through _num().
#   2. appends go through stdlib csv (polars has no append mode), so adding a
#      window is one line written, not the whole file rewritten.
# ============================================================================
LOG_COLS = ['snapshot_time', 'market_ts', 'window_slug', 'strike', 'coa', 'cushion_bps',
            'atr4_bps', 'atr_ratio', 'vol_regime', 'dog_side', 'dog_token_id',
            'dog_ask_at_signal', 'spread', 'secs_left',
            'p_touch', 'p_stick', 'model_mode', 'trust', 'ev_per_share', 'exit_source',
            'action', 'entry_fill_price', 'shares', 'sell_ask_posted', 'sell_frac',
            'touched', 'touch_time_s_left', 'max_bid_after_touch',
            'sell_filled', 'sell_fill_price', 'shares_sold', 'shares_held',
            'outcome', 'dog_won', 'pnl_scalp_usd', 'pnl_hold_usd', 'pnl_total_usd',
            'paper', 'learnable', 'learned', 'features_json']


def _num(value, default=0.0):
    """🌙 Moon Dev - Every number read back out of the log goes through here.
    A blank cell is not a zero until we say so, and float('nan') is TRUTHY in
    python (`nan or 0` is nan, not 0), which silently poisons any sum it touches."""
    if value is None:
        return default
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(v) else v


def log_window(**kw):
    """🌙 Moon Dev - Append ONE window row. Unknown keys dropped, missing keys blank.
    stdlib csv on purpose: an append is an append, not a full-file rewrite."""
    os.makedirs(DATA_DIR, exist_ok=True)
    kw['snapshot_time'] = datetime.now(ET).isoformat()
    row = ['' if kw.get(c) is None else str(kw.get(c, '')) for c in LOG_COLS]
    new_file = not os.path.exists(LOG_FILE)
    with open(LOG_FILE, 'a', newline='') as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(LOG_COLS)
        w.writerow(row)
    action = kw.get('action', '?')
    color = "green" if action == "ENTER" else "yellow"
    print(colored(f"   📝 Moon Dev - Logged {action} | {kw.get('window_slug', '')} | "
                  f"p_touch {_num(kw.get('p_touch')):.3f} p_stick {_num(kw.get('p_stick')):.3f} "
                  f"({kw.get('model_mode', '')})", color))


def _load_log():
    """🌙 Moon Dev - The whole log as a polars frame of strings, or None."""
    if not os.path.exists(LOG_FILE):
        return None
    try:
        return pl.read_csv(LOG_FILE, infer_schema_length=0)   # every column Utf8, on purpose
    except Exception as e:
        print(colored(f"   ⚠️ Moon Dev - log read failed: {type(e).__name__}", "yellow"))
        return None


def _write_rows(rows):
    """🌙 Moon Dev - Rewrite the log from a list of dicts (used after grading)."""
    df = pl.DataFrame(
        [{c: ('' if r.get(c) is None else str(r.get(c, ''))) for c in LOG_COLS} for r in rows],
        schema={c: pl.Utf8 for c in LOG_COLS},
    )
    df.write_csv(LOG_FILE)


def _f(df, col):
    """🌙 Moon Dev - One string column → floats, blanks and junk become null."""
    return df.get_column(col).cast(pl.Float64, strict=False)

# ============================================================================
# 🌙 MOON DEV - RESOLUTION + LABELING + LEARNING (the loop that makes this a bot
# that gets better, instead of a bot that just runs)
# ============================================================================


def get_window_winner(slug):
    """🌙 Moon Dev - Honest resolution ONLY: gamma outcomePrices at exactly 1/0.
    No instant-resolve phantom wins, that was a fleet-wide bug once."""
    try:
        r = requests.get("https://gamma-api.polymarket.com/markets", params={'slug': slug}, timeout=10)
        if r.status_code != 200 or not r.json():
            return None
        prices = json.loads(r.json()[0].get('outcomePrices', '[]') or '[]')
        if len(prices) != 2:
            return None
        up = float(prices[0])
        if up not in (0.0, 1.0):
            return None
        return "UP" if up == 1.0 else "DOWN"
    except Exception:
        return None


def resolve_and_learn(brain):
    """🌙 Moon Dev - For every window whose outcome is still PENDING:
       1. grade it against the real resolution
       2. label it (touched? dog won?) from the real tick tape
       3. feed it to the brain, TRADED OR NOT
       4. book the scalp/hold PnL split
    This is the whole online-learning loop, and it runs once per cycle."""
    df = _load_log()
    if df is None or df.height == 0:
        return
    rows = df.to_dicts()
    changed = False

    for row in rows:
        if str(row.get('outcome', '')) != 'PENDING':
            continue
        market_ts = int(_num(row.get('market_ts')))
        if time.time() < market_ts + MARKET_DURATION + 30:
            continue                                   # give the oracle a beat
        winner = get_window_winner(row['window_slug'])
        if winner is None:
            continue                                   # not resolved yet, try next cycle

        dog_side = str(row['dog_side'])
        dog_won = (dog_side == winner)
        touched, touch_secs = did_dog_touch(market_ts, _num(row.get('strike')), dog_side)
        # 🌙 Moon Dev - a dog that WON must have touched (it had to cross the strike)
        if touched is None and dog_won:
            touched = True
        # 🌙 ...and if our own resting sell filled, the book flipped, period.
        if touched is None and str(row.get('sell_filled', '')) == 'True':
            touched = True

        row['outcome'] = winner
        row['dog_won'] = bool(dog_won)
        row['touched'] = '' if touched is None else bool(touched)
        row['touch_time_s_left'] = '' if touch_secs is None else touch_secs

        # === PnL, split by leg (the doc's whole "which leg earns?" question) ===
        if str(row.get('action')) == 'ENTER':
            entry_px = _num(row.get('entry_fill_price'))
            shares = _num(row.get('shares'))
            shares_sold = _num(row.get('shares_sold'))
            shares_held = shares - shares_sold
            sell_px = _num(row.get('sell_fill_price'))
            fee = taker_fee_est(entry_px, shares)
            pnl_scalp = shares_sold * (sell_px - entry_px)
            pnl_hold = shares_held * ((1 - entry_px) if dog_won else -entry_px)
            total = pnl_scalp + pnl_hold - fee
            row['pnl_scalp_usd'] = round(pnl_scalp, 2)
            row['pnl_hold_usd'] = round(pnl_hold, 2)
            row['pnl_total_usd'] = round(total, 2)
            emoji = "💰" if total > 0 else "💀"
            print(colored(f"   {emoji} Moon Dev - {row['window_slug']}: dog {dog_side} "
                          f"{'WON' if dog_won else 'lost'} | touched={touched} | "
                          f"scalp ${pnl_scalp:+.2f} + hold ${pnl_hold:+.2f} - fee ${fee:.2f} "
                          f"= ${total:+.2f}", "green" if total > 0 else "red"))
        else:
            row['pnl_scalp_usd'] = 0.0
            row['pnl_hold_usd'] = 0.0
            row['pnl_total_usd'] = 0.0

        # === LEARN. Skipped windows teach the brain too, that's the point. ===
        if str(row.get('learnable', '')) in ('True', '1', 'true') and str(row.get('learned', '')) != 'True':
            try:
                x = json.loads(row.get('features_json') or '')
            except Exception:
                x = None
            if x and touched is not None:
                brain.learn(x, touched, dog_won if touched else None)
                row['learned'] = True
                print(colored(f"   🧠 Moon Dev - Brain learned {row['window_slug']} "
                              f"(touched={touched}, dog_won={dog_won}) → {brain.n_touch} labeled, "
                              f"trust {brain.trust()*100:.0f}%", "magenta"))
            else:
                row['learned'] = 'NO_LABEL'            # thin tape, unlearnable, don't retry forever
        changed = True

    if changed:
        _write_rows(rows)
        maybe_print_bucket_stats(_load_log())

# ============================================================================
# 🌙 MOON DEV - RISK: daily stop + kill switch (fleet standard)
# ============================================================================


def _resolved_entries(df):
    """🌙 Moon Dev - Entries that have actually been graded."""
    if df is None or df.height == 0:
        return None
    out = df.filter(
        (pl.col('action') == 'ENTER')
        & pl.col('outcome').is_not_null()
        & (~pl.col('outcome').is_in(['PENDING', '']))
    )
    return out if out.height else None


def todays_pnl():
    """🌙 Moon Dev - Realized PnL since midnight ET (resolved entries only).
    snapshot_time is written in ET, so the day boundary and the log agree."""
    res = _resolved_entries(_load_log())
    if res is None:
        return 0.0
    today = datetime.now(ET).date().isoformat()
    todays = res.filter(pl.col('snapshot_time').str.starts_with(today))
    if todays.height == 0:
        return 0.0
    return float(_f(todays, 'pnl_total_usd').fill_null(0).sum())


def daily_stop_hit():
    """🌙 Moon Dev - Down $60 on the day → shut it down + print the loss table."""
    pnl = todays_pnl()
    if pnl > DAILY_STOP_USD:
        return False
    print(colored(f"\n   🛑 Moon Dev - DAILY STOP! Today ${pnl:+.2f} ≤ ${DAILY_STOP_USD}, done for the day.",
                  "red", attrs=['bold']))
    res = _resolved_entries(_load_log())
    if res is not None:
        today = datetime.now(ET).date().isoformat()
        print(colored("   📋 Moon Dev - Today's table:", "red"))
        for t in res.filter(pl.col('snapshot_time').str.starts_with(today)).to_dicts():
            print(colored(f"      {t['snapshot_time'][11:16]} | dog {t['dog_side']:<4} @ "
                          f"{_num(t['entry_fill_price']):.2f} | sold {t['shares_sold']} @ "
                          f"{t['sell_fill_price']} | ${_num(t['pnl_total_usd']):+.2f}", "white"))
    return True


def kill_switch_active():
    """🌙 Moon Dev - Trailing-30 profitable-window rate < 30% → pause.
    Base rate here is 42% dog wins / 71% touches, sub-30% means it's broken."""
    res = _resolved_entries(_load_log())
    if res is None:
        return False
    res = res.tail(KILL_SWITCH_WINDOW)
    if res.height < KILL_SWITCH_MIN_TRADES:
        return False
    wins = float((_f(res, 'pnl_total_usd').fill_null(0) > 0).mean())
    if wins < KILL_SWITCH_WR:
        print(colored(f"   🚨 Moon Dev - KILL SWITCH! Trailing-{res.height} profitable rate "
                      f"{wins*100:.1f}% < {KILL_SWITCH_WR*100:.0f}%, entries PAUSED", "red", attrs=['bold']))
        return True
    return False

# ============================================================================
# 🌙 MOON DEV - STATS + TRACKER
# ============================================================================


def maybe_print_bucket_stats(df):
    """🌙 Moon Dev - Every N graded windows: the numbers that grade the THESIS,
    not just the PnL. Touch rate vs the 71.3% backtest, harvest fill rate, and
    the scalp-vs-hold split, which is the whole reason this bot exists."""
    if df is None or df.height == 0:
        return
    graded = df.filter(pl.col('touched').is_in(['True', 'False']))
    if graded.height == 0 or graded.height % STATS_EVERY != 0:
        return
    touch_rate = float((graded.get_column('touched') == 'True').mean())
    print(colored(f"\n   📊 Moon Dev - THESIS SCORECARD ({graded.height} graded windows) 📊",
                  "magenta", attrs=['bold']))
    print(colored(f"      touch rate: {touch_rate*100:.1f}% (backtest said {PRIOR_P_TOUCH*100:.1f}%)", "white"))

    touched = graded.filter(pl.col('touched') == 'True')
    if touched.height:
        stick = float((touched.get_column('dog_won') == 'True').mean())
        print(colored(f"      P(win|touch): {stick*100:.1f}% (backtest said {PRIOR_P_STICK*100:.1f}%) "
                      f"| breakeven exit = {stick:.4f}", "white"))
        mb = _f(touched, 'max_bid_after_touch').drop_nulls()
        if len(mb):
            print(colored(f"      max bid after touch: median {mb.median():.3f} | ≥0.60 in "
                          f"{float((mb >= 0.60).mean())*100:.1f}% of touches ← THE unproven link", "cyan"))

    entered = _resolved_entries(df)
    if entered is not None:
        filled = entered.get_column('sell_filled') == 'True'
        scalp = float(_f(entered, 'pnl_scalp_usd').fill_null(0).sum())
        hold = float(_f(entered, 'pnl_hold_usd').fill_null(0).sum())
        print(colored(f"      entries {entered.height} | harvest fill rate {float(filled.mean())*100:.1f}% | "
                      f"scalp ${scalp:+.2f} vs hold ${hold:+.2f}", "white"))
        # 🌙 Moon Dev - the adverse-selection check the idea doc demanded up front
        sold = entered.filter((pl.col('sell_filled') == 'True') & pl.col('dog_won').is_in(['True', 'False']))
        if sold.height >= 5:
            pw = float((sold.get_column('dog_won') == 'True').mean())
            warn = "  ⚠️ ADVERSE SELECTION, raise the ask or kill the harvest" if pw > 0.62 else ""
            print(colored(f"      P(win | we SOLD) = {pw*100:.1f}% (n={sold.height}){warn}",
                          "red" if pw > 0.62 else "white"))
        # 🌙 Moon Dev - is the brain actually beating the static rule it replaced?
        for mode in ('STATIC', 'MODEL'):
            b = entered.filter(pl.col('exit_source') == mode)
            if b.height:
                p = _f(b, 'pnl_total_usd').fill_null(0)
                print(colored(f"      exit {mode:<6}: n={b.height} | total ${float(p.sum()):+.2f} | "
                              f"avg ${float(p.mean()):+.3f}/window", "white"))


def print_trade_tracker():
    """🌙 Moon Dev - Clickable tracker, printed FIRST every cycle (fleet standard)."""
    df = _load_log()
    if df is None or df.height == 0:
        return
    entries = df.filter(pl.col('action') == 'ENTER')
    if entries.height == 0:
        return
    print(colored(f"============ 🌙 MOON DEV'S FLIP TRACKER ({entries.height} entries) 🌙 ============",
                  "cyan", attrs=['bold']))
    for t in entries.tail(20).to_dicts():
        ts = str(t['snapshot_time'])[5:16].replace('T', ' ')
        if str(t['outcome']) == 'PENDING':
            status, color = "PENDING", "white"
        else:
            pnl = _num(t['pnl_total_usd'])
            harvest = "🌾" if str(t['sell_filled']) == 'True' else "  "
            status, color = f"{harvest} ${pnl:+.2f}", ("green" if pnl > 0 else "red")
        print(colored(f"{ts} | dog {str(t['dog_side']):<4} @ {_num(t['entry_fill_price']):.2f} "
                      f"→ ask {t['sell_ask_posted']} ({t['exit_source']}) | {status:<12} | "
                      f"https://polymarket.com/event/{t['window_slug']}", color))
    print(colored("=" * 78, "cyan", attrs=['bold']))


def print_session_summary():
    df = _load_log()
    if df is None or df.height == 0:
        print(colored("   📋 Moon Dev - No window history yet (first run)", "yellow"))
        return
    res = _resolved_entries(df)
    pnl = _f(res, 'pnl_total_usd').fill_null(0) if res is not None else None
    total = float(pnl.sum()) if pnl is not None else 0.0
    profitable = int((pnl > 0).sum()) if pnl is not None else 0
    print(colored("\n📋 Moon Dev - FLIP HARVESTER ML HISTORY", "cyan", attrs=['bold']))
    print(colored(f"   Windows logged: {df.height} | "
                  f"Entries: {df.filter(pl.col('action') == 'ENTER').height} | "
                  f"Resolved: {res.height if res is not None else 0} | Profitable: {profitable} | "
                  f"PnL: ${total:+.2f} | Today: ${todays_pnl():+.2f}", "white"))


# ============================================================================
# 🌙 MOON DEV - THE BOT
# ============================================================================


class FlipHarvesterML:
    """🌙 Moon Dev - One window at a time: gate it, price the exit with the brain,
    take the dog, rest the harvest ask, watch the book, log EVERYTHING."""

    def __init__(self, brain):
        self.brain = brain
        self.reset()

    def reset(self):
        self.market_info = None
        self.evaluated = False          # one evaluation → one log row per window
        self.entered = False
        self.dog_token_id = None
        self.dog_side = None
        self.entry_px = 0.0
        self.shares = 0
        self.sell_ask = 0.0
        self.sell_frac = 1.0
        self.shares_posted = 0
        self.shares_sold = 0
        self.sell_filled = False
        self.max_bid_seen = 0.0
        self.row = {}                   # the pending log row, written at window close

    # ---- the evaluation (runs ONCE per window, in the T-60 → T-5 slot) -----
    def evaluate(self, market_ts, elapsed):
        slug = self.market_info['slug']
        secs_left = MARKET_DURATION - elapsed
        base = {'market_ts': market_ts, 'window_slug': slug, 'outcome': 'PENDING',
                'paper': PAPER_MODE, 'learned': False}

        # === The tape: strike, cushion, ATR4. No data, no trade. ===
        tape = read_window_tape(market_ts)
        if tape is None:
            print(colored("   📡 Moon Dev - BTC tape too thin, no data, no trade!", "yellow"))
            log_window(action="SKIP_NO_TAPE", learnable=False, **base)
            self.evaluated = True
            return
        base.update({'strike': round(tape['strike'], 2), 'coa': round(tape['coa'], 4),
                     'cushion_bps': round(tape['cushion_bps'], 3),
                     'atr4_bps': round(tape['atr4_bps'], 2),
                     'atr_ratio': round(tape['atr_ratio'], 3),
                     'vol_regime': "HIGH" if tape['atr_ratio'] >= 1.0 else "LOW",
                     'dog_side': tape['dog_side'], 'secs_left': secs_left})

        print(colored(f"   📊 Moon Dev - strike ${tape['strike']:,.1f} | spot ${tape['spot']:,.1f} | "
                      f"cushion {tape['cushion_bps']:.2f}bps | ATR4 {tape['atr4_bps']:.1f}bps | "
                      f"coa {tape['coa']:.3f} | dog = {tape['dog_side']}", "cyan"))

        # === GATE 1: is this even a coin flip? (the inherited, proven gate) ===
        if tape['coa'] > MAX_COA or tape['cushion_bps'] > MAX_CUSHION_BPS:
            print(colored(f"   🙅 Moon Dev - Not a coin flip (coa {tape['coa']:.3f} > {MAX_COA} or "
                          f"cushion {tape['cushion_bps']:.2f} > {MAX_CUSHION_BPS}bps), skipping", "yellow"))
            log_window(action="SKIP_NOT_FLIP", learnable=False, **base)
            self.evaluated = True
            return

        # === The dog's book ===
        dog_token = self.market_info['up_token_id'] if tape['dog_side'] == "UP" \
            else self.market_info['down_token_id']
        book = get_order_book(dog_token)
        if not book:
            log_window(action="SKIP_NO_BOOK", learnable=False, dog_token_id=dog_token, **base)
            self.evaluated = True
            return
        dog_ask, spread = book['best_ask'], book['spread']
        base.update({'dog_token_id': dog_token, 'dog_ask_at_signal': round(dog_ask, 3),
                     'spread': round(spread, 3)})

        # === The brain: features (core + whatever groups are switched on) ===
        x, why = self.brain.build_features(tape, dog_ask, spread, secs_left)
        if x is None:
            print(colored(f"   🧠 Moon Dev - Feature vector incomplete ({why}), "
                          f"no half-filled inputs, skipping", "yellow"))
            log_window(action=f"SKIP_{why}", learnable=False, **base)
            self.evaluated = True
            return

        pred = self.brain.predict(x)
        sell_ask, frac, exit_source = self.brain.plan_exit(pred['p_stick'], tape['atr_ratio'])
        shares = max(MIN_SHARES, math.floor(BASE_SIZE_USD / dog_ask))
        fee_ps = taker_fee_est(dog_ask, shares) / shares
        ev = self.brain.expected_value(dog_ask, pred['p_touch'], pred['p_stick'],
                                       sell_ask, frac, fee_ps)
        base.update({'p_touch': round(pred['p_touch'], 4), 'p_stick': round(pred['p_stick'], 4),
                     'model_mode': pred['mode'], 'trust': round(pred['trust'], 3),
                     'ev_per_share': round(ev, 4), 'exit_source': exit_source,
                     'sell_ask_posted': sell_ask, 'sell_frac': frac,
                     'features_json': json.dumps(x, sort_keys=True)})

        print(colored(f"   🧠 Moon Dev - brain [{pred['mode']}, trust {pred['trust']*100:.0f}%]: "
                      f"P(touch) {pred['p_touch']:.3f} | P(win|touch) {pred['p_stick']:.3f} → "
                      f"harvest ask ${sell_ask:.2f} on {frac*100:.0f}% ({exit_source}) | "
                      f"EV {ev*100:+.2f}c/share", "magenta"))

        # 🌙 From here the window IS in the pocket and the features are real, so
        # every remaining skip is still a LEARNABLE row. That is why the brain
        # trains on roughly ten times more windows than we ever have fills for.
        base['learnable'] = True

        # === GATE 2: the price ladder (hard rail, the ML cannot unlock this) ===
        if dog_ask < DOG_PRICE_ZONE[0] or dog_ask > DOG_PRICE_ZONE[1]:
            reason = "sub-22c dogs are correctly-priced trash" if dog_ask < DOG_PRICE_ZONE[0] \
                else "0.45+ dogs graded -11.4% EV live"
            print(colored(f"   🙅 Moon Dev - Dog ask ${dog_ask:.3f} outside "
                          f"{DOG_PRICE_ZONE[0]:.2f}-{DOG_PRICE_ZONE[1]:.2f} ({reason})", "yellow"))
            log_window(action="SKIP_PRICE", **base)
            self.evaluated = True
            return

        # === GATE 3: spread ===
        if spread > MAX_SPREAD:
            log_window(action="SKIP_SPREAD", **base)
            self.evaluated = True
            return

        # === GATE 4: the ML veto (it may only SUBTRACT trades, never add them) ===
        if ev < MIN_EV_PER_SHARE:
            print(colored(f"   🧠 Moon Dev - Brain VETO: EV {ev*100:+.2f}c < "
                          f"{MIN_EV_PER_SHARE*100:.0f}c/share required, skipping", "yellow"))
            log_window(action="SKIP_EV", **base)
            self.evaluated = True
            return

        # === GATE 5: risk pauses (drift cooldown, kill switch) ===
        if self.brain.drift_cooldown > 0:
            print(colored(f"   🌪️ Moon Dev - Drift cooldown, {self.brain.drift_cooldown} windows left, "
                          f"watching only", "red"))
            log_window(action="SKIP_DRIFT", **base)
            self.evaluated = True
            return

        # === TAKE THE DOG 🐶 ===
        cost = shares * dog_ask
        print(colored(f"\n   🐶💰 MOON DEV - DOG {tape['dog_side']} @ ${dog_ask:.3f} x{shares} "
                      f"(${cost:.2f}) | harvest ask ${sell_ask:.2f} on {int(round(shares*frac))} shares "
                      f"🐶💰", "green" if tape['dog_side'] == "UP" else "red", attrs=['bold']))
        print(colored(f"   🔗 https://polymarket.com/event/{slug}", "cyan"))

        if PAPER_MODE:
            print(colored(f"   📄 Moon Dev - PAPER MODE: simulated fill @ ${dog_ask:.3f}", "yellow", attrs=['bold']))
        else:
            resp = place_taker_buy(dog_token, dog_ask, shares)
            if not resp:
                log_window(action="ORDER_FAILED", **base)
                self.evaluated = True
                return
            time.sleep(3)
            held = get_position_size(dog_token)
            if held <= 0:
                print(colored("   ⏱️ Moon Dev - No fill (book moved), cancelling, skipping window", "yellow"))
                cancel_token_orders(dog_token)
                log_window(action="NO_FILL", **base)
                self.evaluated = True
                return
            shares = int(held)

        # === REST THE HARVEST ASK 🪤 (the entire point of this bot) ===
        to_sell = int(round(shares * frac))
        if to_sell < MIN_SHARES:                 # can't post under the exchange minimum
            to_sell = shares                     # → harvest the whole thing instead
        if shares - to_sell < MIN_SHARES:
            to_sell = shares                     # ...and never strand an unsellable stub
        if not PAPER_MODE:
            place_maker_sell(dog_token, sell_ask, to_sell)
        else:
            print(colored(f"   🪤 Moon Dev - PAPER: harvest ask RESTING @ ${sell_ask:.2f} x{to_sell}",
                          "magenta", attrs=['bold']))

        self.entered = True
        self.evaluated = True
        self.dog_token_id = dog_token
        self.dog_side = tape['dog_side']
        self.entry_px = dog_ask
        self.shares = shares
        self.sell_ask = sell_ask
        self.sell_frac = frac
        self.shares_posted = to_sell
        base.update({'action': "ENTER", 'entry_fill_price': round(dog_ask, 3), 'shares': shares,
                     'sell_frac': round(to_sell / shares, 2)})
        self.row = base

    # ---- watching the flip -------------------------------------------------
    def watch_bracket(self):
        """🌙 Moon Dev - Poll the dog's book: record the best bid we EVER see (the
        `max_bid_after_touch` column, the one number nobody in this repo has ever
        logged) and detect the harvest fill."""
        book = get_order_book(self.dog_token_id)
        if not book:
            return
        self.max_bid_seen = max(self.max_bid_seen, book['best_bid'])

        if self.sell_filled:
            return
        if PAPER_MODE:
            # 🌙 Moon Dev - a resting maker ask fills when the BID crosses it. That is
            # the honest paper rule: no fill just because the mid drifted near it.
            if book['best_bid'] >= self.sell_ask:
                self.sell_filled = True
                self.shares_sold = self.shares_posted
        else:
            held = get_position_size(self.dog_token_id)
            sold = max(0, self.shares - int(held))
            if sold > 0:
                self.sell_filled = True
                self.shares_sold = sold
        if self.sell_filled:
            gain = (self.sell_ask - self.entry_px) * self.shares_sold
            print(colored(f"\n   ⚡🎯 MOON DEV - FLIP HARVESTED! Sold {self.shares_sold} @ "
                          f"${self.sell_ask:.2f}, bought @ ${self.entry_px:.3f} → ${gain:+.2f} on the "
                          f"scalp leg. That's the 29% the fleet leaves on the table. 🎯⚡",
                          "green", attrs=['bold']))

    def finalize_window(self):
        """🌙 Moon Dev - Window closed: pull the bracket, write the one row."""
        if not self.entered:
            return
        if not PAPER_MODE:
            cancel_token_orders(self.dog_token_id)      # unfilled ask → we just hold, per the doc
        held = self.shares - self.shares_sold
        self.row.update({
            'max_bid_after_touch': round(self.max_bid_seen, 3),
            'sell_filled': bool(self.sell_filled),
            'sell_fill_price': round(self.sell_ask, 3) if self.sell_filled else '',
            'shares_sold': self.shares_sold,
            'shares_held': held,
        })
        log_window(**self.row)
        if self.sell_filled:
            print(colored(f"   🌾 Moon Dev - Harvested {self.shares_sold}, holding {held} to resolution", "green"))
        else:
            print(colored(f"   🤷 Moon Dev - Ask never lifted (best bid peaked at ${self.max_bid_seen:.3f}), "
                          f"holding all {held} to resolution. Worst case we ARE the dog bot.", "yellow"))

    # ---- one 5-minute window ----------------------------------------------
    def run_market_cycle(self, market_ts):
        market_et = datetime.fromtimestamp(market_ts, tz=ET)
        print("")
        print_trade_tracker()
        print(colored(f"\n{'='*78}", "cyan"))
        print(colored(f"🌙 Moon Dev - FLIP HARVESTER ML | {market_et.strftime('%I:%M:%S%p ET')} | "
                      f"brain {self.brain.mode()} ({self.brain.n_touch} labeled)", "cyan", attrs=['bold']))
        print(colored(f"{'='*78}", "cyan"))

        resolve_and_learn(self.brain)
        if daily_stop_hit():
            print(colored("   😴 Moon Dev - Sleeping out the ET day...", "yellow"))
            time.sleep(3600)
            return
        paused = kill_switch_active()

        if get_time_remaining(market_ts) > MARKET_DURATION - 10:
            time.sleep(3)                      # let gamma index the fresh market

        self.market_info = None
        for attempt in range(5):
            self.market_info = get_market_info(market_ts)
            if self.market_info:
                break
            print(colored(f"   🔄 Moon Dev - Market not indexed yet, retry {attempt+1}/5...", "yellow"))
            time.sleep(2)
        if not self.market_info:
            print(colored("   ❌ Moon Dev - Could not find market, skipping cycle", "red"))
            return
        print(colored(f"   🔗 https://polymarket.com/event/{self.market_info['slug']}", "cyan"))

        while True:
            time_remaining = get_time_remaining(market_ts)
            elapsed = MARKET_DURATION - time_remaining

            if time_remaining <= 0:
                print(colored("\n   ⏰ Moon Dev - Window closed!", "yellow"))
                self.finalize_window()
                break

            if self.entered:
                self.watch_bracket()
                mins, secs = divmod(max(0, time_remaining), 60)
                state = "HARVESTED 🌾" if self.sell_filled else f"ask ${self.sell_ask:.2f} resting"
                print(colored(f"\r   🐶 Moon Dev - dog {self.dog_side} @ ${self.entry_px:.3f} | {state} | "
                              f"best bid ${self.max_bid_seen:.3f} | {mins}:{secs:02d} left    ", "white"),
                      end='', flush=True)
                time.sleep(min(BOT_POLL_INTERVAL, max(1, time_remaining)))
                continue

            if self.evaluated:
                time.sleep(min(BOT_POLL_INTERVAL, max(1, time_remaining)))
                continue

            # 🌙 Moon Dev - the T-60 → T-5 slot, the same one the dog fleet uses
            if elapsed < ENTRY_WINDOW[0]:
                wait = ENTRY_WINDOW[0] - elapsed
                print(colored(f"\r   ⏳ Moon Dev - waiting for the T-60 slot... {wait}s   ", "white"),
                      end='', flush=True)
                time.sleep(min(wait, 5))
                continue
            if elapsed > ENTRY_WINDOW[1]:
                print(colored("\n   ⏳ Moon Dev - Past T-5, too late to enter this window", "white"))
                self.evaluated = True
                continue

            if paused:
                print(colored("\n   🚨 Kill switch active, watching, not trading", "red"))
                self.evaluated = True
                continue

            print(colored(f"\n   📡 Moon Dev - Evaluating window ({time_remaining}s left)...", "yellow"))
            self.evaluate(market_ts, elapsed)

# ============================================================================
# 🌙 MOON DEV - OFFLINE REPLAY (train the brain from a log, no market needed)
# ============================================================================


def replay(path, brain):
    """🌙 Moon Dev - Progressive validation over a saved log: for every row, PREDICT
    first, then learn. That is the only honest way to score an online model, each
    prediction is made on data the model has never seen.

    Accepts this bot's own CSV, or a JSON array exported from somewhere else
    (your own bot service, a research notebook), as long as each record carries
    the feature columns plus `touched` and `dog_won`."""
    if not os.path.exists(path):
        print(colored(f"❌ Moon Dev - No such file: {path}", "red"))
        return
    if path.endswith('.json'):
        with open(path) as f:
            records = json.load(f)
        raw = records if isinstance(records, list) else records.get('rows', [])
        rows = [dict(r) for r in raw]
    else:
        rows = pl.read_csv(path, infer_schema_length=0).to_dicts()
    if not rows:
        print(colored("❌ Moon Dev - Empty log, nothing to replay", "red"))
        return

    need = brain.FEATURES
    print(colored(f"🌙 Moon Dev - Replaying {len(rows)} rows from {os.path.basename(path)} | "
                  f"{len(need)} features | groups {FEAT.active_groups()}", "cyan", attrs=['bold']))

    used = skipped = 0
    correct_t, correct_s = [], []
    for row in rows:
        # features_json (our own log) wins; otherwise take the columns directly
        x = None
        if str(row.get('features_json', '')) not in ('', 'nan', 'None'):
            try:
                x = json.loads(row['features_json'])
            except Exception:
                x = None
        if x is None:
            try:
                x = {k: float(row[k]) for k in need}
            except (KeyError, TypeError, ValueError):
                x = None
        if x is None or sorted(x) != sorted(need):
            skipped += 1
            continue
        touched = str(row.get('touched', '')).lower()
        if touched not in ('true', 'false', '1', '0'):
            skipped += 1
            continue
        touched = touched in ('true', '1')
        won_raw = str(row.get('dog_won', '')).lower()
        dog_won = won_raw in ('true', '1') if won_raw in ('true', 'false', '1', '0') else None

        p = brain.predict(x)
        correct_t.append(1 if (p['p_touch'] >= 0.5) == touched else 0)
        if touched and dog_won is not None:
            correct_s.append(1 if (p['p_stick'] >= 0.5) == dog_won else 0)
        brain.learn(x, touched, dog_won if touched else None)
        used += 1
        if used % 25 == 0:
            acc_t = sum(correct_t[-25:]) / min(25, len(correct_t)) * 100
            print(colored(f"   ... {used} rows | last-25 touch acc {acc_t:.0f}% | "
                          f"touch logloss {brain.touch_ll.get():.4f} | trust {brain.trust()*100:.0f}%", "white"))

    print(colored(f"\n✅ Moon Dev - Replay done: {used} learned, {skipped} unusable "
                  f"(missing features or no label)", "green", attrs=['bold']))
    if correct_t:
        print(colored(f"   Progressive-validation accuracy, touch: "
                      f"{sum(correct_t)/len(correct_t)*100:.1f}% (n={len(correct_t)})", "white"))
    if correct_s:
        print(colored(f"   Progressive-validation accuracy, stick: "
                      f"{sum(correct_s)/len(correct_s)*100:.1f}% (n={len(correct_s)})", "white"))
    print(colored("   ⚠️ Replay teaches the brain the PAST. It does not prove the future.", "yellow"))
    brain.report()

# ============================================================================
# 🌙 MOON DEV - MAIN ENTRY
# ============================================================================


def main():
    parser = argparse.ArgumentParser(description="Moon Dev's Flip Harvester ML (river)")
    parser.add_argument('--replay', metavar='FILE', help="offline-train the brain from a CSV/JSON log")
    parser.add_argument('--report', action='store_true', help="print the brain's state and quit")
    parser.add_argument('--groups', metavar='LIST',
                        help="feature groups to enable, e.g. core,ta,hl,corr (default: features.py)")
    args = parser.parse_args()

    if args.groups:
        try:
            FEAT.set_groups(args.groups)
        except ValueError as e:
            print(colored(f"❌ Moon Dev - {e}", "red"))
            sys.exit(1)

    print(colored("""
╔══════════════════════════════════════════════════════════════════════════════╗
║   🌙 MOON DEV's FLIP HARVESTER ML v1.0 (river online learning) 🌙            ║
║   Dogs TOUCH 71.3% but WIN 42.4%. Harvest the touch, don't pray for close.   ║
║   Two online models price the exit every window. Floor 0.60, never lower.    ║
╚══════════════════════════════════════════════════════════════════════════════╝
    """, "cyan", attrs=['bold']))

    brain = FlipBrain()

    if args.report:
        brain.report()
        return
    if args.replay:
        replay(args.replay, brain)
        return

    print(colored("⚙️  Moon Dev - Configuration:", "yellow", attrs=['bold']))
    print(colored(f"   📄 PAPER MODE:     {PAPER_MODE} {'(flip to False for live!)' if PAPER_MODE else '🔴 LIVE FIRE'}",
                  "yellow" if PAPER_MODE else "red"))
    print(colored(f"   🐶 Entry gate:     coa ≤ {MAX_COA} AND cushion ≤ {MAX_CUSHION_BPS}bps AND dog ask "
                  f"{DOG_PRICE_ZONE[0]:.2f}-{DOG_PRICE_ZONE[1]:.2f} (HARD, the ML cannot loosen it)", "white"))
    print(colored(f"   ⏳ Entry slot:     T-60 → T-5 ({ENTRY_WINDOW[0]}-{ENTRY_WINDOW[1]}s elapsed)", "white"))
    print(colored(f"   🪤 Exit engine:    ask = P(win|touch) + {EDGE_MARGIN:.2f}, floored at "
                  f"${MIN_SELL_ASK:.2f} (breakeven {BREAKEVEN_ASK:.4f}), static ${STATIC_SELL_ASK:.2f} until trusted", "white"))
    print(colored(f"   🧠 Brain:          river logistic x2 | trust at {MIN_TRAIN_N} windows, full at "
                  f"{FULL_TRUST_N} | ADWIN drift δ={DRIFT_DELTA}", "white"))
    print(colored(f"   📈 BTC tape:       {active_btc_feed()} (FLIP_BTC_FEED={BTC_FEED})", "white"))
    print(colored(f"   🎛️  Features:       groups {FEAT.active_groups()} → {len(brain.FEATURES)} inputs", "white"))
    print(colored(f"      {', '.join(brain.FEATURES)}", "cyan"))
    print(colored(f"   💵 Size:           ${BASE_SIZE_USD} flat | min EV {MIN_EV_PER_SHARE*100:.0f}c/share | "
                  f"daily stop ${DAILY_STOP_USD}", "white"))

    brain.report()
    print_session_summary()
    print("")
    print_trade_tracker()

    # 🌙 Moon Dev - smoke-test every enabled feed BEFORE going live. No data, no bot.
    print(colored("\n📡 Moon Dev - Testing feeds...", "yellow", attrs=['bold']))
    ticks = get_btc_ticks("1h", limit=10000)
    if len(ticks) < 100:
        print(colored("❌ Moon Dev - BTC tick feed is dead, refusing to run on no data!", "red"))
        sys.exit(1)
    print(colored(f"   BTC ticks (1h): {len(ticks)} ✅", "cyan"))
    if FEAT.FEATURE_GROUPS.get('ta') or FEAT.FEATURE_GROUPS.get('corr'):
        bars = FEAT.hl_candles("BTC", "1m")
        if not bars:
            print(colored("❌ Moon Dev - Hyperliquid candles unreachable but `ta`/`corr` are ON. "
                          "Fix the feed or run with --groups core", "red"))
            sys.exit(1)
        print(colored(f"   Hyperliquid BTC 1m candles: {len(bars)} ✅", "cyan"))
    if FEAT.FEATURE_GROUPS.get('hl'):
        hl = FEAT.build_hl_features(spot_price=ticks[-1].get('p'))
        if not hl:
            print(colored("❌ Moon Dev - Hyperliquid ctx/book unreachable but `hl` is ON. "
                          "Fix the feed or run with --groups core", "red"))
            sys.exit(1)
        print(colored(f"   Hyperliquid: funding {hl['hl_funding_bps']:+.2f}bps | "
                      f"OI ${hl['hl_oi_musd']:,.0f}M | book imb {hl['hl_book_imb']:+.3f} | "
                      f"basis {hl['hl_basis_bps']:+.2f}bps ✅", "cyan"))

    print(colored(f"\n{'='*78}", "green"))
    print(colored("🚀 Moon Dev - FLIP HARVESTER ML LIVE! Harvesting flips, not praying for closes...",
                  "green", attrs=['bold']))
    print(colored("   Press Ctrl+C to stop", "yellow"))
    print(colored(f"{'='*78}\n", "green"))

    bot = FlipHarvesterML(brain)
    while True:
        try:
            market_ts = get_current_market_timestamp()
            bot.reset()
            bot.run_market_cycle(market_ts)
        except KeyboardInterrupt:
            print(colored("\n\n🛑 Moon Dev - Flip Harvester ML stopped! Clean shutdown ✅", "yellow", attrs=['bold']))
            if bot.entered and not PAPER_MODE:
                cancel_token_orders(bot.dog_token_id)
            bot.brain.save()
            bot.brain.report()
            break
        except Exception as e:
            print(colored(f"\n   ❌ Moon Dev - Cycle error: {type(e).__name__}: {e}, "
                          f"riding it out to the next window", "red"))
            time.sleep(10)


if __name__ == "__main__":
    print("🌙 Moon Dev's Flip Harvester ML - harvest the flip, let the brain price the exit! 🌾")
    main()
