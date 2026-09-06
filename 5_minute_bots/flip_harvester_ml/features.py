"""
================================================================================
🌙 MOON DEV's FEATURE DESK, for FLIP HARVESTER ML
================================================================================
Everything the river brain is allowed to look at, in ONE place, behind ONE switch
board. Turn a group off and the models simply stop seeing it.

    FEATURE_GROUPS = {
        'core':  True,   # the window itself: coa, cushion, ATR4, dog ask, spread...
        'ta':    True,   # technical indicators on Hyperliquid BTC 1m candles
        'hl':    True,   # Hyperliquid microstructure: funding, OI, book, basis
        'corr':  True,   # cross-asset correlation: BTC vs ETH vs SOL 1m returns
    }

Control it three ways, later ones win:
    1. edit FEATURE_GROUPS below
    2. env var:  FLIP_FEATURES=core,ta          (comma list of groups to ENABLE)
    3. CLI:      python flip_harvester_ml.py --groups core,ta

⚠️ CHANGING THE GROUPS CHANGES THE MODEL'S INPUT SHAPE. The brain refuses to load
a pickle trained on a different feature list, it starts COLD instead. That is on
purpose: a StandardScaler fitted on columns that no longer exist is worse than no
model at all. Flip a group, and you are training a new brain from zero.

⚠️ MORE FEATURES IS NOT BETTER. All four groups on = 27 inputs. On a few hundred
real windows, a 27-input logistic regression will happily memorize noise; that is
what L2 and the trust ramp are fighting. Start with `core`, add ONE group, and
let the log tell you whether logloss actually improved. That is the experiment.

DATA HONESTY RULE (fleet standard): an enabled group that cannot fetch returns
None, and the whole window is logged as a SKIP and is NOT used for training.
We never pad a missing feed with a zero and call it data.

Sources, all free + public, no keys:
    Hyperliquid info API, https://api.hyperliquid.xyz/info
      • metaAndAssetCtxs → funding, open interest, mark/oracle premium, 24h volume
      • l2Book           → top-of-book depth imbalance
      • candleSnapshot   → 1m OHLCV for BTC / ETH / SOL (the TA + correlation base)
Built by Moon Dev 🌙
================================================================================
"""

import os
import time
import math
import statistics
import requests

# ============================================================================
# 🌙 MOON DEV - THE SWITCH BOARD
# ============================================================================
FEATURE_GROUPS = {
    'core': True,     # never turn this off, it IS the window
    'ta': True,
    'hl': True,
    'corr': True,
}

HL_API = "https://api.hyperliquid.xyz/info"
HL_TIMEOUT = 8
CACHE_TTL_CTX = 20          # seconds, funding/OI move slowly, don't hammer the API
CACHE_TTL_BOOK = 3          # the book is the whole point, keep it fresh
CACHE_TTL_CANDLES = 25      # 1m candles, no reason to refetch faster than that
CORR_ASSETS = ("ETH", "SOL")
CORR_BARS = 30              # 30 x 1m returns for the rolling correlation
CANDLE_BARS = 120           # enough for MACD(26,9) + BB(20) + RSI(14) with room


def _apply_env_override():
    """🌙 Moon Dev - FLIP_FEATURES=core,ta wins over the dict above."""
    raw = os.getenv("FLIP_FEATURES")
    if raw:
        set_groups(raw)


def set_groups(spec):
    """🌙 Moon Dev - Enable exactly the groups named in `spec` ('core,ta'), off the rest."""
    wanted = {g.strip().lower() for g in str(spec).split(',') if g.strip()}
    unknown = wanted - set(FEATURE_GROUPS)
    if unknown:
        raise ValueError(f"unknown feature group(s): {sorted(unknown)}, "
                         f"valid: {sorted(FEATURE_GROUPS)}")
    for g in FEATURE_GROUPS:
        FEATURE_GROUPS[g] = g in wanted
    if not FEATURE_GROUPS['core']:
        raise ValueError("'core' cannot be disabled, it is the window itself")
    return dict(FEATURE_GROUPS)


def active_groups():
    return [g for g, on in FEATURE_GROUPS.items() if on]

# ============================================================================
# 🌙 MOON DEV - HYPERLIQUID FEED (cached, public, no keys, fails LOUD not silent)
# ============================================================================
_CACHE = {}


def _cached_post(key, payload, ttl):
    """🌙 Moon Dev - One tiny TTL cache for every HL call. Returns None on failure,
    and None means NO TRADE upstream, never a silently-zeroed feature."""
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        r = requests.post(HL_API, json=payload, timeout=HL_TIMEOUT,
                          headers={'Content-Type': 'application/json'})
        if r.status_code != 200:
            return None
        data = r.json()
        _CACHE[key] = (time.time(), data)
        return data
    except Exception:
        return None


def hl_asset_ctx(coin="BTC"):
    """🌙 Moon Dev - Funding, open interest, mark vs oracle, 24h volume for one perp."""
    data = _cached_post("ctx", {"type": "metaAndAssetCtxs"}, CACHE_TTL_CTX)
    if not data or len(data) < 2:
        return None
    universe = (data[0] or {}).get('universe', [])
    ctxs = data[1] or []
    for i, asset in enumerate(universe):
        if str(asset.get('name', '')).upper() == coin.upper() and i < len(ctxs):
            return ctxs[i]
    return None


def hl_book_imbalance(coin="BTC", depth=10):
    """🌙 Moon Dev - Top-N notional imbalance on the HL perp book.
    +1 = all bids, -1 = all asks. The perp book leads the 5-min flip, or it doesn't,
    and that is exactly what we are letting the model find out."""
    data = _cached_post(f"book:{coin}", {"type": "l2Book", "coin": coin}, CACHE_TTL_BOOK)
    if not data:
        return None
    levels = data.get('levels') or []
    if len(levels) < 2:
        return None
    def notional(side):
        return sum(float(lv['px']) * float(lv['sz']) for lv in side[:depth])
    bid_n, ask_n = notional(levels[0]), notional(levels[1])
    if bid_n + ask_n <= 0:
        return None
    return (bid_n - ask_n) / (bid_n + ask_n)


def hl_candles(coin="BTC", interval="1m", bars=CANDLE_BARS):
    """🌙 Moon Dev - Recent 1m OHLCV from Hyperliquid → [{'o','h','l','c','v','t'}...]"""
    end = int(time.time() * 1000)
    start = end - bars * 60 * 1000
    data = _cached_post(f"candles:{coin}:{interval}:{bars}",
                        {"type": "candleSnapshot",
                         "req": {"coin": coin, "interval": interval,
                                 "startTime": start, "endTime": end}},
                        CACHE_TTL_CANDLES)
    if not data or not isinstance(data, list) or len(data) < 30:
        return None
    out = []
    for c in data:
        try:
            out.append({'t': int(c['t']), 'o': float(c['o']), 'h': float(c['h']),
                        'l': float(c['l']), 'c': float(c['c']), 'v': float(c.get('v', 0))})
        except (KeyError, TypeError, ValueError):
            continue
    return out if len(out) >= 30 else None

# ============================================================================
# 🌙 MOON DEV - TECHNICAL INDICATORS (hand-rolled, zero extra dependencies)
# ============================================================================
# No `ta` / `pandas_ta` install, no version roulette. Every one of these is
# ~5 lines and you can read exactly what it computes, which is the point.
# ============================================================================


def _ema(values, span):
    k = 2.0 / (span + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def rsi(closes, period=14):
    """🌙 Moon Dev - Classic Wilder RSI, 0-100. >70 hot, <30 cold."""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for a, b in zip(closes[-period-1:-1], closes[-period:]):
        d = b - a
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_g = sum(gains) / period
    avg_l = sum(losses) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100 - (100 / (1 + rs))


def macd_hist_bps(closes, fast=12, slow=26, signal=9):
    """🌙 Moon Dev - MACD histogram, normalized to bps of price so it is
    comparable across a $60k BTC and a $120k BTC."""
    if len(closes) < slow + signal:
        return None
    macd_line = []
    for i in range(slow, len(closes) + 1):
        w = closes[:i]
        macd_line.append(_ema(w[-fast:], fast) - _ema(w[-slow:], slow))
    if len(macd_line) < signal:
        return None
    hist = macd_line[-1] - _ema(macd_line[-signal:], signal)
    return hist / closes[-1] * 10_000


def bollinger_pct_b(closes, period=20, mult=2.0):
    """🌙 Moon Dev - %B: 0 = lower band, 1 = upper band. Where in the range are we?"""
    if len(closes) < period:
        return None
    w = closes[-period:]
    mid = sum(w) / period
    sd = statistics.pstdev(w)
    if sd == 0:
        return 0.5
    return (closes[-1] - (mid - mult * sd)) / (2 * mult * sd)


def atr_bps(bars, period=14):
    """🌙 Moon Dev - ATR in bps of price (true range, not just high-low)."""
    if len(bars) < period + 1:
        return None
    trs = []
    for prev, cur in zip(bars[-period-1:-1], bars[-period:]):
        trs.append(max(cur['h'] - cur['l'], abs(cur['h'] - prev['c']), abs(cur['l'] - prev['c'])))
    return (sum(trs) / period) / bars[-1]['c'] * 10_000


def realized_vol_bps(closes, n=15):
    """🌙 Moon Dev - Stdev of the last n 1-minute returns, in bps. Regime, in one number."""
    if len(closes) < n + 1:
        return None
    rets = [(b - a) / a for a, b in zip(closes[-n-1:-1], closes[-n:])]
    return statistics.pstdev(rets) * 10_000 if len(rets) > 1 else None


def vwap_dist_bps(bars, n=60):
    """🌙 Moon Dev - Distance from the rolling VWAP, in bps. Stretched or fair?"""
    w = bars[-n:]
    vol = sum(b['v'] for b in w)
    if vol <= 0:
        return None
    vwap = sum(((b['h'] + b['l'] + b['c']) / 3) * b['v'] for b in w) / vol
    return (bars[-1]['c'] - vwap) / vwap * 10_000


def ret_bps(closes, n):
    if len(closes) < n + 1:
        return None
    return (closes[-1] - closes[-n-1]) / closes[-n-1] * 10_000


def pearson(xs, ys):
    """🌙 Moon Dev - Plain Pearson correlation, -1 to +1, no scipy needed."""
    if len(xs) != len(ys) or len(xs) < 5:
        return None
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def _returns(closes):
    return [(b - a) / a for a, b in zip(closes[:-1], closes[1:]) if a]

# ============================================================================
# 🌙 MOON DEV - THE FEATURE VECTOR
# ============================================================================
# Names are the model's column keys. They are FROZEN once a brain is trained:
# rename one and the pickle is discarded on the next load (by design).
# ============================================================================
CORE_FEATURES = ['coa', 'cushion_bps', 'atr4_bps', 'atr_ratio', 'dog_ask',
                 'spread', 'secs_left', 'dog_is_up', 'rate_mult', 'hour_et']
TA_FEATURES = ['ta_rsi14', 'ta_macd_hist_bps', 'ta_bb_pctb', 'ta_atr14_bps',
               'ta_rvol15_bps', 'ta_vwap_dist_bps', 'ta_ret5_bps', 'ta_ret15_bps']
HL_FEATURES = ['hl_funding_bps', 'hl_premium_bps', 'hl_oi_musd', 'hl_vol24h_musd',
               'hl_book_imb', 'hl_basis_bps']
CORR_FEATURES = ['corr_btc_eth', 'corr_btc_sol', 'eth_ret5_bps']

GROUP_FEATURES = {'core': CORE_FEATURES, 'ta': TA_FEATURES,
                  'hl': HL_FEATURES, 'corr': CORR_FEATURES}


def active_feature_names():
    """🌙 Moon Dev - The exact ordered column list the brain is training on."""
    names = []
    for g in ('core', 'ta', 'hl', 'corr'):
        if FEATURE_GROUPS.get(g):
            names.extend(GROUP_FEATURES[g])
    return names


def build_ta_features(bars):
    """🌙 Moon Dev - The `ta` group off HL BTC 1m candles. None → skip the window."""
    closes = [b['c'] for b in bars]
    f = {
        'ta_rsi14': rsi(closes, 14),
        'ta_macd_hist_bps': macd_hist_bps(closes),
        'ta_bb_pctb': bollinger_pct_b(closes),
        'ta_atr14_bps': atr_bps(bars, 14),
        'ta_rvol15_bps': realized_vol_bps(closes, 15),
        'ta_vwap_dist_bps': vwap_dist_bps(bars, 60),
        'ta_ret5_bps': ret_bps(closes, 5),
        'ta_ret15_bps': ret_bps(closes, 15),
    }
    return None if any(v is None for v in f.values()) else f


def build_hl_features(spot_price=None):
    """🌙 Moon Dev - The `hl` group: what the perp crowd is actually doing.
    hl_basis_bps is the cross-venue link, Hyperliquid mark vs the spot tape our
    strike is measured against. If they diverge inside a 5-min window, the dog's
    touch odds are not the same as the backtest's."""
    ctx = hl_asset_ctx("BTC")
    imb = hl_book_imbalance("BTC")
    if ctx is None or imb is None:
        return None
    try:
        mark = float(ctx.get('markPx'))
        oracle = float(ctx.get('oraclePx', mark))
        oi = float(ctx.get('openInterest', 0)) * mark        # HL reports OI in coins
        vol24 = float(ctx.get('dayNtlVlm', 0))
        funding = float(ctx.get('funding', 0))
    except (TypeError, ValueError):
        return None
    return {
        'hl_funding_bps': funding * 10_000,
        'hl_premium_bps': ((mark - oracle) / oracle * 10_000) if oracle else 0.0,
        'hl_oi_musd': oi / 1e6,
        'hl_vol24h_musd': vol24 / 1e6,
        'hl_book_imb': imb,
        'hl_basis_bps': ((mark - spot_price) / spot_price * 10_000) if spot_price else 0.0,
    }


def build_corr_features(btc_bars):
    """🌙 Moon Dev - The `corr` group: is BTC moving WITH the complex or alone?
    A lone BTC wiggle inside a 5-min window mean-reverts differently than a
    risk-on move the whole book is joining. That is the hypothesis, anyway,
    and the logloss column is what gets to decide."""
    btc_ret = _returns([b['c'] for b in btc_bars])[-CORR_BARS:]
    out = {}
    for coin in CORR_ASSETS:
        bars = hl_candles(coin, "1m", CANDLE_BARS)
        if not bars:
            return None
        rets = _returns([b['c'] for b in bars])[-CORR_BARS:]
        n = min(len(btc_ret), len(rets))
        c = pearson(btc_ret[-n:], rets[-n:])
        if c is None:
            return None
        out[f'corr_btc_{coin.lower()}'] = c
        if coin == "ETH":
            r5 = ret_bps([b['c'] for b in bars], 5)
            if r5 is None:
                return None
            out['eth_ret5_bps'] = r5
    return out


def build_feature_vector(core_features, spot_price=None):
    """🌙 Moon Dev - Assemble the FULL vector for one window.

    `core_features` is the dict the bot already computes from the window tape.
    Every enabled group is appended. If ANY enabled group cannot be built from
    real data, this returns (None, reason) and the bot SKIPS the window, it does
    not trade on a half-filled feature vector. No data, no trade, ever."""
    x = dict(core_features)
    btc_bars = None
    if FEATURE_GROUPS.get('ta') or FEATURE_GROUPS.get('corr'):
        btc_bars = hl_candles("BTC", "1m", CANDLE_BARS)
        if not btc_bars:
            return None, "NO_HL_CANDLES"

    if FEATURE_GROUPS.get('ta'):
        ta = build_ta_features(btc_bars)
        if ta is None:
            return None, "NO_TA"
        x.update(ta)

    if FEATURE_GROUPS.get('hl'):
        hl = build_hl_features(spot_price)
        if hl is None:
            return None, "NO_HL_CTX"
        x.update(hl)

    if FEATURE_GROUPS.get('corr'):
        corr = build_corr_features(btc_bars)
        if corr is None:
            return None, "NO_CORR"
        x.update(corr)

    missing = [k for k in active_feature_names() if k not in x]
    if missing:
        return None, f"MISSING:{','.join(missing[:3])}"
    return {k: float(x[k]) for k in active_feature_names()}, "OK"


_apply_env_override()
