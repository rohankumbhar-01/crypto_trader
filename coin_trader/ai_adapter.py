"""
AI Validation Layer
===================
Called ONLY after the probability engine clears the confidence threshold
and sets call_ai=True. Never called directly for trade decisions.

Pipeline position:
  Probability Engine (call_ai=True)
        ↓
  AI Adapter  ← this module
        ↓
  Risk Engine

Supported providers (CT AI Provider DocType):
  Groq          — fastest, best for realtime validation
  DeepSeek      — best reasoning, cost-effective
  Gemini Flash  — large context, good for market analysis
  OpenRouter    — routes to multiple models
  OpenAI        — GPT-4o / GPT-4o-mini
  Claude        — Anthropic claude-* models

AI task:
  Given the ML signal (direction, probabilities, indicators, market context),
  validate whether the trade should proceed and return:
    - signal:     BUY | SELL | HOLD | NO TRADE
    - confidence: 0-100
    - stop_loss:  price level
    - target:     price level
    - reason:     brief explanation
    - risk_flags: list of concerns

Cost tracking:
  Every AI call updates CT AI Provider (total_api_calls, cost_this_month).
  If cost_this_month >= monthly_budget, the provider is skipped.
"""

import json
import re

import frappe
from frappe import _
from frappe.utils import flt, cint, now_datetime
from frappe.utils.password import get_decrypted_password


# ---------------------------------------------------------------------------
# Provider cost per 1K tokens (USD) — approximate, for tracking
# ---------------------------------------------------------------------------
_COST_PER_1K = {
    "Groq":         0.0001,   # llama-3.3-70b
    "DeepSeek":     0.00014,  # deepseek-chat
    "Gemini Flash": 0.000075, # gemini-2.0-flash
    "OpenRouter":   0.0005,   # varies
    "OpenAI":       0.0025,   # gpt-4o-mini
    "Claude":       0.0008,   # claude-haiku-4-5
}

# Default models if CT AI Provider.model_name is blank
_DEFAULT_MODELS = {
    "Groq":         "llama-3.3-70b-versatile",
    "DeepSeek":     "deepseek-chat",
    "Gemini Flash": "gemini-2.0-flash",
    "OpenRouter":   "openai/gpt-4o-mini",
    "OpenAI":       "gpt-4o-mini",
    "Claude":       "claude-haiku-4-5-20251001",
}

# Max tokens for AI validation response (keep it tight)
_MAX_TOKENS = 512


# ---------------------------------------------------------------------------
# Provider loader
# ---------------------------------------------------------------------------

def _get_provider(user: str) -> dict:
    """
    Return the active CT AI Provider config for the user.
    Picks the first active provider that is within budget.
    Raises frappe.ValidationError if none available.
    """
    providers = frappe.get_all(
        "CT AI Provider",
        filters={"user": user, "is_active": 1},
        fields=["name", "provider", "model_name", "api_key",
                "monthly_budget", "cost_this_month", "total_api_calls"],
        order_by="creation asc",
    )

    for p in providers:
        budget  = flt(p.get("monthly_budget", 0))
        spent   = flt(p.get("cost_this_month", 0))
        if budget > 0 and spent >= budget:
            continue  # over budget — skip

        api_key = get_decrypted_password("CT AI Provider", p["name"], "api_key")
        return {
            "name":       p["name"],
            "provider":   p["provider"],
            "model":      p.get("model_name") or _DEFAULT_MODELS.get(p["provider"], ""),
            "api_key":    api_key,
            "budget":     budget,
            "spent":      spent,
            "calls":      cint(p.get("total_api_calls", 0)),
        }

    frappe.throw(
        _("No active AI provider with remaining budget found for user {0}. "
          "Configure a CT AI Provider record.").format(user)
    )


def _update_provider_usage(name: str, tokens: int, provider: str) -> None:
    """Increment usage counters on CT AI Provider after a call."""
    cost = flt(tokens / 1000 * _COST_PER_1K.get(provider, 0.001), 6)
    doc = frappe.get_doc("CT AI Provider", name)
    doc.total_api_calls = cint(doc.total_api_calls) + 1
    doc.cost_this_month = flt(doc.cost_this_month) + cost
    doc.flags.ignore_permissions = True
    doc.save()
    frappe.db.commit()


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _build_prompt(signal: dict) -> str:
    """
    Build the structured validation prompt from a probability engine signal.
    Keeps token count minimal — concise market data only.
    """
    ctx  = signal.get("market_context", {})
    mtf  = signal.get("mtf_detail", {})
    pred = signal.get("prediction", "?")

    prompt = f"""You are a professional crypto trading risk analyst.

A machine learning model has generated the following trade signal. Your job is to validate it and decide if it is safe to execute.

## Signal
Symbol:         {signal.get('symbol')}
Prediction:     {pred}
UP Probability: {signal.get('upward_probability')}%
DOWN Probability: {signal.get('downward_probability')}%
Expected Move:  {signal.get('expected_move_pct', 0):+.3f}%
ML Confidence:  {signal.get('ml_confidence_raw')}%
Composite Score:{signal.get('confidence_score')}%

## Price
Close:     {signal.get('close')}
ATR:       {signal.get('atr')}
Stoploss:  {signal.get('suggested_stoploss')}
Target:    {signal.get('suggested_target')}

## Market Context
RSI:          {ctx.get('rsi')} ({ctx.get('rsi_zone')})
Trend:        {ctx.get('trend_label')}
MACD Hist:    {ctx.get('macd_hist')}
Price vs VWAP:{ctx.get('price_vs_vwap')}%
Volume Spike: {ctx.get('volume_spike')}
Aligned Factors: {ctx.get('aligned_factors')}/4

## Multi-Timeframe ({mtf.get('interval', '?')})
Trend:    {mtf.get('trend_label', '?')}
MACD Hist:{mtf.get('macd_hist', '?')}
RSI:      {mtf.get('rsi', '?')}
Agrees:   {mtf.get('agrees', '?')}

## Your Task
Validate this signal. Consider:
1. Is the risk/reward acceptable?
2. Are there any red flags (overbought, divergence, low volume)?
3. Does the market context support the ML signal?
4. Is the suggested stoploss reasonable given ATR?

Respond ONLY with valid JSON (no markdown, no explanation outside JSON):
{{
  "signal": "BUY" | "SELL" | "HOLD" | "NO TRADE",
  "confidence": <integer 0-100>,
  "stop_loss": <number>,
  "target": <number>,
  "reason": "<one concise sentence>",
  "risk_flags": ["<flag1>", "<flag2>"]
}}"""

    return prompt


# ---------------------------------------------------------------------------
# Provider adapters
# ---------------------------------------------------------------------------

def _call_groq(api_key: str, model: str, prompt: str) -> tuple[str, int]:
    """Returns (response_text, tokens_used)."""
    from groq import Groq
    client = Groq(api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=_MAX_TOKENS,
        temperature=0.1,
    )
    text   = resp.choices[0].message.content or ""
    tokens = resp.usage.total_tokens if resp.usage else len(prompt.split())
    return text, tokens


def _call_openai_compat(api_key: str, model: str, prompt: str,
                         base_url: str | None = None) -> tuple[str, int]:
    """Handles OpenAI, DeepSeek, OpenRouter (all OpenAI-compatible)."""
    from openai import OpenAI
    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    client = OpenAI(**kwargs)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=_MAX_TOKENS,
        temperature=0.1,
    )
    text   = resp.choices[0].message.content or ""
    tokens = resp.usage.total_tokens if resp.usage else len(prompt.split())
    return text, tokens


def _call_gemini(api_key: str, model: str, prompt: str) -> tuple[str, int]:
    from google import genai
    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(
        model=model,
        contents=prompt,
        config=genai.types.GenerateContentConfig(
            temperature=0.1,
            max_output_tokens=_MAX_TOKENS,
        ),
    )
    text   = resp.text or ""
    tokens = (resp.usage_metadata.total_token_count
              if resp.usage_metadata else len(prompt.split()))
    return text, tokens


def _call_claude(api_key: str, model: str, prompt: str) -> tuple[str, int]:
    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=model,
        max_tokens=_MAX_TOKENS,
        temperature=0.1,
        messages=[{"role": "user", "content": prompt}],
    )
    text   = resp.content[0].text if resp.content else ""
    tokens = (resp.usage.input_tokens + resp.usage.output_tokens
              if resp.usage else len(prompt.split()))
    return text, tokens


def _dispatch(provider_cfg: dict, prompt: str) -> tuple[str, int]:
    """Route to the correct provider adapter."""
    provider = provider_cfg["provider"]
    api_key  = provider_cfg["api_key"]
    model    = provider_cfg["model"]

    if provider == "Groq":
        return _call_groq(api_key, model, prompt)

    if provider == "DeepSeek":
        return _call_openai_compat(
            api_key, model, prompt,
            base_url="https://api.deepseek.com/v1",
        )

    if provider == "OpenRouter":
        return _call_openai_compat(
            api_key, model, prompt,
            base_url="https://openrouter.ai/api/v1",
        )

    if provider == "OpenAI":
        return _call_openai_compat(api_key, model, prompt)

    if provider == "Gemini Flash":
        return _call_gemini(api_key, model, prompt)

    if provider == "Claude":
        return _call_claude(api_key, model, prompt)

    frappe.throw(_("Unsupported AI provider: {0}").format(provider))


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------

def _parse_response(text: str, signal: dict) -> dict:
    """
    Extract JSON from AI response text.
    Falls back to the ML signal values if parsing fails.
    """
    # Strip markdown code fences if present
    text = re.sub(r"```(?:json)?", "", text).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try extracting first {...} block
        match = re.search(r"\{.*?\}", text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
            except json.JSONDecodeError:
                data = {}
        else:
            data = {}

    # Normalise and validate fields
    valid_signals = {"BUY", "SELL", "HOLD", "NO TRADE"}
    ai_signal = str(data.get("signal", "HOLD")).upper()
    if ai_signal not in valid_signals:
        ai_signal = "HOLD"

    return {
        "signal":      ai_signal,
        "confidence":  max(0, min(100, cint(data.get("confidence", 50)))),
        "stop_loss":   flt(data.get("stop_loss") or signal.get("suggested_stoploss", 0)),
        "target":      flt(data.get("target") or signal.get("suggested_target", 0)),
        "reason":      str(data.get("reason", "AI validation completed."))[:500],
        "risk_flags":  data.get("risk_flags", []) if isinstance(data.get("risk_flags"), list) else [],
    }


# ---------------------------------------------------------------------------
# Main validation entry point
# ---------------------------------------------------------------------------

def validate(user: str, signal: dict) -> dict:
    """
    Run AI validation on a probability engine signal.

    Only call this when signal['call_ai'] is True.

    Returns an enriched dict merging the AI result into the signal:
      {
        ...original signal fields...
        "ai_signal":      "BUY" | "SELL" | "HOLD" | "NO TRADE",
        "ai_confidence":  int,
        "ai_stop_loss":   float,
        "ai_target":      float,
        "ai_reason":      str,
        "ai_risk_flags":  list,
        "ai_provider":    str,
        "tokens_used":    int,
        "ai_cost_usd":    float,
        "ai_validated":   True,
      }
    """
    provider_cfg = _get_provider(user)
    prompt       = _build_prompt(signal)

    try:
        raw_text, tokens = _dispatch(provider_cfg, prompt)
    except Exception as e:
        frappe.log_error(
            title=f"AI validation error ({provider_cfg['provider']})",
            message=str(e),
        )
        # Return signal unchanged — do not block trading on AI failure
        return {**signal, "ai_validated": False, "ai_error": str(e)}

    parsed = _parse_response(raw_text, signal)
    cost   = flt(tokens / 1000 * _COST_PER_1K.get(provider_cfg["provider"], 0.001), 6)

    # Update provider usage counters
    try:
        _update_provider_usage(provider_cfg["name"], tokens, provider_cfg["provider"])
    except Exception as e:
        frappe.log_error(title="AI provider usage update failed", message=str(e))

    # Save to CT AI Scan Result
    _save_scan_result(signal, parsed, provider_cfg, tokens, cost)

    return {
        **signal,
        "ai_signal":     parsed["signal"],
        "ai_confidence": parsed["confidence"],
        "ai_stop_loss":  parsed["stop_loss"],
        "ai_target":     parsed["target"],
        "ai_reason":     parsed["reason"],
        "ai_risk_flags": parsed["risk_flags"],
        "ai_provider":   provider_cfg["provider"],
        "tokens_used":   tokens,
        "ai_cost_usd":   cost,
        "ai_validated":  True,
    }


def _save_scan_result(
    signal: dict, parsed: dict, provider_cfg: dict, tokens: int, cost: float
) -> None:
    """Persist result to CT AI Scan Result DocType."""
    try:
        doc = frappe.get_doc({
            "doctype":      "CT AI Scan Result",
            "symbol":       signal.get("symbol"),
            "signal":       parsed["signal"],
            "confidence":   parsed["confidence"],
            "price":        flt(signal.get("close")),
            "target_price": flt(parsed["target"]),
            "stop_loss":    flt(parsed["stop_loss"]),
            "reason":       parsed["reason"],
            "provider_used":provider_cfg["provider"],
            "tokens_used":  tokens,
            "ai_cost_usd":  cost,
        })
        doc.flags.ignore_permissions = True
        doc.insert()
        frappe.db.commit()
    except Exception as e:
        frappe.log_error(title="CT AI Scan Result save failed", message=str(e))


# ---------------------------------------------------------------------------
# Full pipeline shortcut
# ---------------------------------------------------------------------------

def validate_if_needed(user: str, signal: dict) -> dict:
    """
    Check call_ai flag and run validation only if warranted.
    This is the entry point called by the scanner.
    """
    if not signal.get("call_ai"):
        return {**signal, "ai_validated": False}
    return validate(user, signal)


# ---------------------------------------------------------------------------
# Whitelisted — manual trigger from desk
# ---------------------------------------------------------------------------

@frappe.whitelist()
def run_ai_validation_api(symbol: str, interval: str = "1h") -> dict:
    """
    Manually trigger AI validation for a symbol from the Frappe desk.
    Runs the full stack: probability engine → AI validation.
    """
    from coin_trader.probability_engine import run_probability_engine
    from coin_trader.ml.predict import model_exists

    user = frappe.session.user

    if not model_exists(user):
        return {"error": "No trained model found."}

    config = frappe.db.get_value(
        "CT Trading Config",
        {"user": user},
        ["min_confidence_pct"],
        as_dict=True,
    )
    min_conf = cint(config.get("min_confidence_pct", 80)) if config else 80

    signal = run_probability_engine(user, symbol, interval, min_confidence=min_conf)
    if not signal:
        return {"error": "Insufficient market data or model not trained."}

    return validate_if_needed(user, signal)
