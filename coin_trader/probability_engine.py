"""
Probability Engine
==================
Sits between ML prediction and AI validation.
Takes the raw LightGBM probabilities and enriches them with:

  1. Market context alignment  — does trend/volume/RSI confirm the signal?
  2. Multi-timeframe (MTF) confirmation — does a higher timeframe agree?
  3. Signal quality score — composite 0-100 score combining all factors
  4. Display-ready output — the exact format shown on the dashboard

Architecture position:
  ML Prediction (predict.py)
        ↓
  Probability Engine  ← this module
        ↓
  AI Validation Layer (ai_adapter.py)  — called only if score ≥ min_confidence

Output format (BRD Section 6):
  {
    "symbol":               "BTCINR",
    "prediction":           "BUY",
    "upward_probability":   82.0,
    "downward_probability": 18.0,
    "expected_move_pct":    2.1,
    "confidence_score":     82,          # final composite score
    "confidence_level":     "High",
    "market_context":       {...},       # trend, rsi zone, volume, bb
    "mtf_agreement":        True,
    "mtf_detail":           {...},
    "signal_quality":       "Strong",    # Strong | Moderate | Weak | Noise
    "call_ai":              True,        # whether AI validation should run
    "close":                7835000.0,
    "atr":                  67000.0,
    "suggested_stoploss":   7750000.0,
    "suggested_target":     7997000.0,
  }
"""

import frappe
from frappe import _
from frappe.utils import flt, cint

from coin_trader.market_data import get_market_snapshot, get_multi_timeframe_snapshot


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Higher-timeframe to confirm a signal on the primary interval
_MTF_MAP = {
    "1m":  "15m",
    "5m":  "1h",
    "15m": "1h",
    "30m": "4h",
    "1h":  "4h",
    "4h":  "1d",
    "1d":  "1d",
}

# Weights for composite score components (must sum to 1.0)
_W_ML    = 0.50   # ML probability is the primary signal
_W_CTX   = 0.25   # market context alignment
_W_MTF   = 0.15   # multi-timeframe agreement
_W_VOL   = 0.10   # volume confirmation

_STOPLOSS_ATR_MULT = 1.5   # stoploss = close - ATR × 1.5  (for BUY)
_TARGET_ATR_MULT   = 3.0   # target   = close + ATR × 3.0  (for BUY)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_probability_signal(
    ml_result: dict,
    snapshot: dict,
    min_confidence: int = 80,
) -> dict:
    """
    Enrich a raw ML prediction with market context and produce the
    final probability signal consumed by the AI validation layer and dashboard.

    ml_result:      output of coin_trader.ml.predict.predict()
    snapshot:       output of coin_trader.market_data.get_market_snapshot()
    min_confidence: threshold below which call_ai is False

    Returns the enriched signal dict.
    """
    if not ml_result or not snapshot:
        return {}

    symbol    = ml_result.get("symbol", "")
    interval  = ml_result.get("interval", "1h")
    prediction = ml_result.get("prediction", "HOLD")

    up_prob   = flt(ml_result.get("upward_probability", 50))
    down_prob = flt(ml_result.get("downward_probability", 50))
    ml_conf   = flt(ml_result.get("confidence_score", 50))

    # 1. Market context
    context       = _build_market_context(snapshot, prediction)
    context_score = _score_context(context, prediction)

    # 2. Multi-timeframe confirmation
    mtf_interval    = _MTF_MAP.get(interval, "4h")
    mtf_detail      = _get_mtf_detail(symbol, mtf_interval, prediction)
    mtf_agreement   = mtf_detail.get("agrees", False)
    mtf_score       = 100 if mtf_agreement else 30

    # 3. Volume score
    volume_score = _score_volume(snapshot)

    # 4. Composite confidence score
    composite = int(round(
        _W_ML  * ml_conf +
        _W_CTX * context_score +
        _W_MTF * mtf_score +
        _W_VOL * volume_score
    ))
    composite = max(0, min(100, composite))

    confidence_level = _confidence_level(composite)
    signal_quality   = _signal_quality(composite, context, mtf_agreement)

    # 5. Risk levels — ATR-based
    close = flt(snapshot.get("close"))
    atr   = flt(snapshot.get("atr"))
    suggested_stoploss, suggested_target = _calc_levels(
        close, atr, prediction
    )

    # 6. AI gate — only call AI when it's worth it
    volume_spike = cint(snapshot.get("volume_spike", 0))
    trend        = cint(snapshot.get("trend_direction", 0))
    call_ai = (
        composite >= min_confidence
        and prediction != "HOLD"
        and volume_spike == 1
        and trend in (1, -1)
    )

    return {
        # Core prediction
        "symbol":               symbol,
        "interval":             interval,
        "prediction":           prediction,
        "upward_probability":   round(up_prob, 2),
        "downward_probability": round(down_prob, 2),
        "expected_move_pct":    flt(ml_result.get("expected_move_pct"), 4),
        # Confidence
        "confidence_score":     composite,
        "confidence_level":     confidence_level,
        "ml_confidence_raw":    int(ml_conf),
        # Context
        "market_context":       context,
        "context_score":        int(context_score),
        # MTF
        "mtf_interval":         mtf_interval,
        "mtf_agreement":        mtf_agreement,
        "mtf_detail":           mtf_detail,
        "mtf_score":            int(mtf_score),
        # Volume
        "volume_score":         int(volume_score),
        # Signal quality
        "signal_quality":       signal_quality,
        # Price levels
        "close":                close,
        "atr":                  atr,
        "suggested_stoploss":   suggested_stoploss,
        "suggested_target":     suggested_target,
        # AI gate flag
        "call_ai":              call_ai,
        # Passthrough for downstream
        "features_json":        ml_result.get("features_json", "{}"),
        "model_version":        ml_result.get("model_version", ""),
    }


def run_probability_engine(
    user: str,
    symbol: str,
    interval: str = "1h",
    min_confidence: int = 80,
) -> dict:
    """
    Full pipeline shortcut: fetch snapshot + ML predict + enrich.
    This is the primary entry point called by the scanner.

    Returns {} if model not trained, insufficient data, or HOLD with low confidence.
    """
    from coin_trader.ml.predict import predict, model_exists

    if not model_exists(user):
        return {}

    ml_result = predict(user, symbol, interval)
    if not ml_result:
        return {}

    # Reuse snapshot carried by predict() — avoids a second candle fetch
    snapshot = ml_result.pop("_snapshot", None) or get_market_snapshot(symbol, interval)
    if not snapshot:
        return {}

    signal = build_probability_signal(ml_result, snapshot, min_confidence)
    return signal


# ---------------------------------------------------------------------------
# Market context builder
# ---------------------------------------------------------------------------

def _build_market_context(snapshot: dict, prediction: str) -> dict:
    """
    Extract key context fields from the snapshot and flag alignment
    with the predicted direction.
    """
    rsi            = flt(snapshot.get("rsi", 50))
    trend          = cint(snapshot.get("trend_direction", 0))
    volume_spike   = cint(snapshot.get("volume_spike", 0))
    bb_position    = flt(snapshot.get("bb_width", 0))
    macd_hist      = flt(snapshot.get("macd_hist", 0))
    price_vs_ema20 = flt(snapshot.get("price_vs_ema20", 0))
    price_vs_vwap  = flt(snapshot.get("price_vs_vwap", 0))

    # Alignment checks
    if prediction == "BUY":
        rsi_ok    = rsi < 70           # not overbought
        trend_ok  = trend >= 0         # bullish or mixed (not bearish)
        macd_ok   = macd_hist > 0      # MACD histogram positive
        vwap_ok   = price_vs_vwap > 0  # price above VWAP
    elif prediction == "SELL":
        rsi_ok    = rsi > 30           # not oversold
        trend_ok  = trend <= 0         # bearish or mixed
        macd_ok   = macd_hist < 0
        vwap_ok   = price_vs_vwap < 0
    else:
        rsi_ok = trend_ok = macd_ok = vwap_ok = False

    return {
        "rsi":              round(rsi, 2),
        "rsi_zone":         "oversold" if rsi < 30 else "overbought" if rsi > 70 else "neutral",
        "rsi_ok":           rsi_ok,
        "trend":            trend,
        "trend_label":      "bullish" if trend == 1 else "bearish" if trend == -1 else "mixed",
        "trend_ok":         trend_ok,
        "volume_spike":     bool(volume_spike),
        "macd_hist":        round(macd_hist, 4),
        "macd_ok":          macd_ok,
        "price_vs_ema20":   round(price_vs_ema20, 4),
        "price_vs_vwap":    round(price_vs_vwap, 4),
        "vwap_ok":          vwap_ok,
        "aligned_factors":  sum([rsi_ok, trend_ok, macd_ok, vwap_ok]),
    }


def _score_context(context: dict, prediction: str) -> float:
    """Convert context alignment into a 0-100 score."""
    if prediction == "HOLD":
        return 50.0
    # 4 factors, each worth 25 points
    score = context.get("aligned_factors", 0) * 25
    # Volume spike adds a bonus
    if context.get("volume_spike"):
        score = min(100, score + 10)
    return float(score)


# ---------------------------------------------------------------------------
# Multi-timeframe confirmation
# ---------------------------------------------------------------------------

def _get_mtf_detail(symbol: str, mtf_interval: str, prediction: str) -> dict:
    """
    Fetch the higher-timeframe snapshot and check if trend and MACD agree
    with the primary prediction direction.
    """
    try:
        snap = get_market_snapshot(symbol, mtf_interval)
        if not snap:
            return {"agrees": False, "reason": "no data"}

        htf_trend  = cint(snap.get("trend_direction", 0))
        htf_macd   = flt(snap.get("macd_hist", 0))
        htf_rsi    = flt(snap.get("rsi", 50))

        if prediction == "BUY":
            trend_agrees = htf_trend >= 0
            macd_agrees  = htf_macd > 0
        elif prediction == "SELL":
            trend_agrees = htf_trend <= 0
            macd_agrees  = htf_macd < 0
        else:
            trend_agrees = macd_agrees = False

        agrees = trend_agrees and macd_agrees

        return {
            "agrees":       agrees,
            "interval":     mtf_interval,
            "trend":        htf_trend,
            "trend_label":  "bullish" if htf_trend == 1 else "bearish" if htf_trend == -1 else "mixed",
            "macd_hist":    round(htf_macd, 4),
            "rsi":          round(htf_rsi, 2),
            "trend_agrees": trend_agrees,
            "macd_agrees":  macd_agrees,
        }
    except Exception as e:
        frappe.log_error(title="MTF check failed", message=str(e))
        return {"agrees": False, "reason": str(e)}


# ---------------------------------------------------------------------------
# Volume scoring
# ---------------------------------------------------------------------------

def _score_volume(snapshot: dict) -> float:
    """Score volume strength on a 0-100 scale."""
    volume_ratio = flt(snapshot.get("volume_ratio", 1))
    volume_spike = cint(snapshot.get("volume_spike", 0))

    if volume_spike:
        return 100.0
    # Linearly scale: ratio 0.5 → 25, ratio 1.0 → 50, ratio 2.0 → 100
    score = min(100, volume_ratio * 50)
    return round(float(score), 1)


# ---------------------------------------------------------------------------
# Risk levels
# ---------------------------------------------------------------------------

def _calc_levels(
    close: float, atr: float, prediction: str
) -> tuple[float, float]:
    """
    Calculate ATR-based stoploss and target price.
    Returns (stoploss, target).
    """
    if not close or not atr:
        return 0.0, 0.0

    if prediction == "BUY":
        stoploss = round(close - atr * _STOPLOSS_ATR_MULT, 2)
        target   = round(close + atr * _TARGET_ATR_MULT, 2)
    elif prediction == "SELL":
        stoploss = round(close + atr * _STOPLOSS_ATR_MULT, 2)
        target   = round(close - atr * _TARGET_ATR_MULT, 2)
    else:
        stoploss = 0.0
        target   = 0.0

    return stoploss, target


# ---------------------------------------------------------------------------
# Confidence + quality helpers
# ---------------------------------------------------------------------------

def _confidence_level(score: int) -> str:
    if score >= 90:
        return "Very High"
    if score >= 80:
        return "High"
    if score >= 70:
        return "Medium"
    if score >= 60:
        return "Weak"
    return "Ignore"


def _signal_quality(
    composite: int, context: dict, mtf_agreement: bool
) -> str:
    """
    Human-readable signal quality label used on the dashboard.
    Strong   → composite ≥ 80 AND ≥ 3 aligned context factors AND MTF agrees
    Moderate → composite ≥ 65 AND ≥ 2 aligned factors
    Weak     → composite ≥ 50
    Noise    → below 50
    """
    aligned = context.get("aligned_factors", 0)

    if composite >= 80 and aligned >= 3 and mtf_agreement:
        return "Strong"
    if composite >= 65 and aligned >= 2:
        return "Moderate"
    if composite >= 50:
        return "Weak"
    return "Noise"


# ---------------------------------------------------------------------------
# Whitelisted — dashboard JS
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_probability_signal_api(symbol: str, interval: str = "1h") -> dict:
    """
    Full probability signal for a symbol — called from dashboard realtime panel.
    Uses the current session user's trained model.
    """
    from coin_trader.ml.predict import model_exists

    user = frappe.session.user
    if not model_exists(user):
        return {
            "error": "No trained model found. Please wait for daily training.",
            "symbol": symbol,
        }

    config = frappe.db.get_value(
        "CT Trading Config",
        {"user": user},
        ["min_confidence_pct"],
        as_dict=True,
    )
    min_conf = cint(config.get("min_confidence_pct", 80)) if config else 80

    return run_probability_engine(user, symbol, interval, min_confidence=min_conf)
