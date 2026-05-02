"""
NLP-Based Negotiation Evaluator

Pure Python conversation analyzer that produces grounded metrics
BEFORE sending to Claude for final evaluation. No external NLP libraries needed.

Two-layer approach:
  Layer 1 (this file): Deterministic analysis → concrete numbers
  Layer 2 (ai_service.py): Claude + metrics + rubric → evidence-based report
"""

import re
import math
import logging
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
#  SENTIMENT LEXICONS (business negotiation context)
# ═══════════════════════════════════════════════════════════════

POSITIVE_TERMS = {
    # Professional / collaborative
    "appreciate", "thank", "thanks", "reasonable", "fair", "understand",
    "agree", "willing", "flexible", "consider", "opportunity", "value",
    "partnership", "collaborate", "mutual", "beneficial", "competitive",
    "excellent", "great", "good", "perfect", "works", "ideal",
    "accommodate", "constructive", "pleased", "confident", "commitment",
    # Persuasion / reasoning
    "because", "therefore", "considering", "given", "based on",
    "data shows", "research", "market rate", "industry standard",
    "cost analysis", "budget", "roi", "investment", "long-term",
    "volume", "relationship", "repeat", "loyalty",
}

NEGATIVE_TERMS = {
    # Aggressive / unprofessional
    "ridiculous", "absurd", "insane", "terrible", "awful", "worst",
    "rip off", "ripoff", "scam", "waste", "unacceptable", "outrageous",
    "joke", "laughable", "pathetic", "stupid", "idiot", "crazy",
    # Ultimatum language
    "final offer", "take it or leave", "walk away", "forget it",
    "no way", "never", "impossible", "won't budge", "non-negotiable",
    "deal breaker", "not interested",
}

TRADE_OFF_PATTERNS = [
    # Conditional language linking two variables
    r"if\s+(?:you|we).+(?:price|delivery|days|faster|cheaper|lower|higher)",
    r"(?:in exchange|in return)\s+for",
    r"(?:lower|reduce|drop).+(?:price|cost).+(?:faster|quicker|sooner|delivery)",
    r"(?:faster|quicker|sooner).+(?:delivery|days).+(?:pay|price|more|higher)",
    r"(?:willing to|can|could)\s+(?:pay|offer)\s+(?:more|higher).+(?:if|for|provided)",
    r"(?:trade|swap|exchange).+(?:price|delivery)",
    r"(?:how about|what if).+(?:price|delivery).+(?:and|but|if)",
    r"(?:accept|ok with).+(?:price|delivery).+(?:but|if|provided)",
]

JUSTIFICATION_PATTERNS = [
    # Data-backed reasoning
    r"(?:because|since|as)\s+(?:the|our|we|it|this)",
    r"(?:based on|according to|given that|considering that)",
    r"(?:market|industry|competitor|benchmark|standard|average)",
    r"(?:budget|cost structure|margin|overhead|operating cost)",
    r"(?:data|research|analysis|report|study|survey)\s+(?:shows?|indicates?|suggests?)",
    r"(?:long.term|strategic|partnership|relationship|future|repeat)",
    r"(?:volume|bulk|quantity|large order|scale)",
    r"\d+\s*%",  # Percentage references indicate analytical thinking
]

PERSUASION_PHRASES = [
    # Structured negotiation techniques
    r"(?:let me|allow me to)\s+(?:explain|clarify|propose)",
    r"(?:i propose|i suggest|my suggestion|my proposal|my offer)",
    r"(?:win.win|mutual benefit|both sides|fair for both)",
    r"(?:i understand your|i see your|i appreciate your)\s+(?:position|point|concern|perspective)",
    r"(?:here'?s? (?:what|how)|(?:what|how) (?:about|if))",
    r"(?:counter.?offer|alternative|another option)",
    r"(?:meet (?:in the|half)|split the difference|compromise)",
]


# ═══════════════════════════════════════════════════════════════
#  CORE ANALYSIS FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def extract_offers_from_conversation(history: List[Dict]) -> Dict[str, List]:
    """
    Extract every price and delivery offer from each message.
    Returns structured turn-by-turn offer data.
    """
    user_offers = []
    ai_offers = []

    for i, msg in enumerate(history):
        role = msg.get("role", "")
        content = msg.get("content", "")
        turn = i // 2  # Approximate turn number

        # Extract price
        price_match = re.search(r'\$\s*([0-9][0-9,]*(?:\.\d+)?)', content)
        price = float(price_match.group(1).replace(',', '')) if price_match else None

        # Extract delivery
        delivery_match = re.search(r'(\d+)\s*[-.]?\s*days?', content.lower())
        delivery = int(delivery_match.group(1)) if delivery_match else None

        offer = {
            "turn": turn,
            "message_index": i,
            "price": price,
            "delivery": delivery,
            "content": content
        }

        if role == "user":
            user_offers.append(offer)
        elif role == "assistant":
            ai_offers.append(offer)

    return {"user": user_offers, "ai": ai_offers}


def analyze_concessions(offers: List[Dict], role: str) -> Dict[str, Any]:
    """
    Analyze the concession pattern for a given role.
    Tracks price movement, delivery movement, and concession rate.
    """
    prices = [(o["turn"], o["price"]) for o in offers if o["price"] is not None]
    deliveries = [(o["turn"], o["delivery"]) for o in offers if o["delivery"] is not None]

    result = {
        "total_price_offers": len(prices),
        "total_delivery_offers": len(deliveries),
        "price_trajectory": [],
        "delivery_trajectory": [],
        "price_concessions": 0,
        "price_total_movement": 0.0,
        "avg_price_concession": 0.0,
        "delivery_concessions": 0,
        "delivery_total_movement": 0,
        "concession_pattern": "none",  # "aggressive_start", "gradual", "firm", "erratic"
    }

    if len(prices) >= 2:
        result["price_trajectory"] = [p for _, p in prices]
        movements = []
        for j in range(1, len(prices)):
            diff = prices[j][1] - prices[j - 1][1]
            movements.append(diff)
            if (role == "user" and diff > 0) or (role == "ai" and diff < 0):
                result["price_concessions"] += 1

        result["price_total_movement"] = round(abs(prices[-1][1] - prices[0][1]), 2)
        result["avg_price_concession"] = (
            round(result["price_total_movement"] / max(result["price_concessions"], 1), 2)
        )

        # Classify concession pattern
        if len(movements) >= 2:
            abs_movements = [abs(m) for m in movements]
            if abs_movements[0] > abs_movements[-1] * 1.5:
                result["concession_pattern"] = "aggressive_start"
            elif all(abs_movements[i] >= abs_movements[i + 1] * 0.8 for i in range(len(abs_movements) - 1)):
                result["concession_pattern"] = "gradual"
            elif max(abs_movements) < 3:
                result["concession_pattern"] = "firm"
            else:
                result["concession_pattern"] = "erratic"
        elif len(movements) == 1:
            result["concession_pattern"] = "single_move"

    if len(deliveries) >= 2:
        result["delivery_trajectory"] = [d for _, d in deliveries]
        for j in range(1, len(deliveries)):
            diff = deliveries[j][1] - deliveries[j - 1][1]
            if (role == "user" and diff > 0) or (role == "ai" and diff < 0):
                result["delivery_concessions"] += 1
        result["delivery_total_movement"] = abs(deliveries[-1][1] - deliveries[0][1])

    return result


def analyze_anchoring(user_offers: List[Dict], deal_params: Dict) -> Dict[str, Any]:
    """
    Evaluate the user's opening anchor relative to the AI's parameters.
    Strong anchoring = opening far below AI's reservation (aggressive but not insulting).
    """
    ai_opening = float(deal_params["price"]["opening"])
    ai_target = float(deal_params["price"]["target"])
    ai_reservation = float(deal_params["price"]["reservation"])

    first_user_price = None
    for offer in user_offers:
        if offer["price"] is not None:
            first_user_price = offer["price"]
            break

    if first_user_price is None:
        return {
            "anchor_price": None,
            "anchor_distance_pct": 0,
            "anchor_quality": "no_anchor",
            "anchor_score": 30,
            "explanation": "User never proposed an opening price — missed the anchoring opportunity."
        }

    distance_from_opening = ai_opening - first_user_price
    distance_pct = round((distance_from_opening / ai_opening) * 100, 1)

    # Score based on where anchor landed relative to AI's range
    if first_user_price <= ai_reservation * 0.8:
        quality = "too_aggressive"
        score = 50
        explanation = (f"User anchored at ${first_user_price} ({distance_pct}% below AI's opening of ${ai_opening}). "
                       f"This is below even the AI's walk-away price of ${ai_reservation} — may be seen as unrealistic and reduce credibility.")
    elif first_user_price <= ai_reservation:
        quality = "strong"
        score = 95
        explanation = (f"User anchored at ${first_user_price} ({distance_pct}% below AI's opening). "
                       f"This is an excellent anchor — below the AI's reservation of ${ai_reservation}, forcing maximum concession pressure.")
    elif first_user_price <= ai_target:
        quality = "good"
        score = 85
        explanation = (f"User anchored at ${first_user_price} ({distance_pct}% below AI's opening). "
                       f"This is between the AI's target (${ai_target}) and reservation (${ai_reservation}) — a solid strategic position.")
    elif first_user_price <= ai_opening * 0.95:
        quality = "moderate"
        score = 65
        explanation = (f"User anchored at ${first_user_price} (only {distance_pct}% below AI's opening). "
                       f"This leaves limited room to negotiate. A lower anchor would have created more leverage.")
    else:
        quality = "weak"
        score = 40
        explanation = (f"User anchored at ${first_user_price} (just {distance_pct}% below AI's opening of ${ai_opening}). "
                       f"This is near the AI's opening price, giving up most negotiation leverage.")

    return {
        "anchor_price": first_user_price,
        "anchor_distance_pct": distance_pct,
        "anchor_quality": quality,
        "anchor_score": score,
        "explanation": explanation
    }


def analyze_sentiment(messages: List[Dict], role_filter: str = "user") -> Dict[str, Any]:
    """
    Keyword-based sentiment analysis tuned for business negotiation context.
    Scores each message and tracks sentiment trajectory.
    """
    scores = []
    per_message = []

    for msg in messages:
        if msg.get("role") != role_filter:
            continue
        content = msg.get("content", "").lower()
        words = set(re.findall(r'\b\w+\b', content))

        # Count positive and negative hits
        pos_count = sum(1 for term in POSITIVE_TERMS if term in content)
        neg_count = sum(1 for term in NEGATIVE_TERMS if term in content)
        total = pos_count + neg_count

        if total == 0:
            score = 0.5  # Neutral
        else:
            score = round(pos_count / total, 2)

        scores.append(score)
        per_message.append({
            "content_preview": content[:80],
            "positive_hits": pos_count,
            "negative_hits": neg_count,
            "sentiment_score": score
        })

    if not scores:
        return {
            "avg_sentiment": 0.5,
            "sentiment_trajectory": [],
            "tone_consistency": 1.0,
            "worst_message_score": 0.5,
            "tone_label": "neutral",
            "per_message": []
        }

    avg = round(sum(scores) / len(scores), 2)
    consistency = round(1.0 - (max(scores) - min(scores)), 2) if len(scores) > 1 else 1.0

    if avg >= 0.75:
        label = "professional_positive"
    elif avg >= 0.55:
        label = "professional_neutral"
    elif avg >= 0.35:
        label = "mixed"
    else:
        label = "negative_aggressive"

    return {
        "avg_sentiment": avg,
        "sentiment_trajectory": scores,
        "tone_consistency": max(consistency, 0),
        "worst_message_score": min(scores),
        "tone_label": label,
        "per_message": per_message
    }


def detect_trade_offs(user_messages: List[Dict]) -> Dict[str, Any]:
    """
    Detect if the user proposed trade-offs linking price to delivery.
    """
    trade_offs_found = []

    for msg in user_messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        for pattern in TRADE_OFF_PATTERNS:
            if re.search(pattern, content, re.IGNORECASE):
                trade_offs_found.append({
                    "turn": msg.get("turn", len(trade_offs_found)),
                    "pattern": pattern,
                    "excerpt": content[:120]
                })
                break  # One match per message is enough

    count = len(trade_offs_found)

    if count >= 3:
        score = 95
        label = "excellent"
        explanation = f"User proposed {count} trade-offs, actively linking price and delivery for win-win outcomes."
    elif count == 2:
        score = 85
        label = "good"
        explanation = f"User proposed {count} trade-offs, showing awareness of multi-variable negotiation."
    elif count == 1:
        score = 70
        label = "developing"
        explanation = "User proposed 1 trade-off. More conditional offers would have strengthened their position."
    else:
        score = 40
        label = "absent"
        explanation = "User made no trade-off proposals — negotiated price and delivery independently, missing leverage opportunities."

    return {
        "count": count,
        "trade_offs": trade_offs_found,
        "score": score,
        "label": label,
        "explanation": explanation
    }


def detect_persuasion_and_justification(user_messages: List[Dict]) -> Dict[str, Any]:
    """
    Detect reasoning, justification, and persuasion techniques in user messages.
    """
    justifications = 0
    persuasion_techniques = 0
    examples = []

    for msg in user_messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")

        for pattern in JUSTIFICATION_PATTERNS:
            if re.search(pattern, content, re.IGNORECASE):
                justifications += 1
                examples.append({"type": "justification", "excerpt": content[:100]})
                break

        for pattern in PERSUASION_PHRASES:
            if re.search(pattern, content, re.IGNORECASE):
                persuasion_techniques += 1
                examples.append({"type": "persuasion", "excerpt": content[:100]})
                break

    total = justifications + persuasion_techniques
    user_msg_count = sum(1 for m in user_messages if m.get("role") == "user")
    ratio = round(total / max(user_msg_count, 1), 2)

    if ratio >= 0.6:
        score = 92
        label = "data_driven"
        explanation = (f"User justified their position in {total} of {user_msg_count} messages ({int(ratio*100)}%). "
                       f"Strong use of reasoning and persuasion techniques.")
    elif ratio >= 0.3:
        score = 75
        label = "moderate_reasoning"
        explanation = (f"User provided justification in {total} of {user_msg_count} messages ({int(ratio*100)}%). "
                       f"Some reasoning present but could be more consistent.")
    elif ratio > 0:
        score = 55
        label = "minimal_reasoning"
        explanation = (f"User justified only {total} of {user_msg_count} messages. "
                       f"Most offers lacked business logic or data backing.")
    else:
        score = 30
        label = "no_reasoning"
        explanation = "User never justified their offers with business logic, data, or strategic reasoning."

    return {
        "justification_count": justifications,
        "persuasion_count": persuasion_techniques,
        "total_techniques": total,
        "ratio": ratio,
        "score": score,
        "label": label,
        "explanation": explanation,
        "examples": examples[:5]  # Limit to top 5
    }


def analyze_turn_efficiency(history: List[Dict], deal_params: Dict,
                            final_terms: Dict) -> Dict[str, Any]:
    """
    Analyze negotiation efficiency — rounds to deal, message patterns.
    """
    user_messages = [m for m in history if m.get("role") == "user"]
    ai_messages = [m for m in history if m.get("role") == "assistant"]
    total_turns = len(user_messages)

    user_avg_length = (
        round(sum(len(m.get("content", "")) for m in user_messages) / max(len(user_messages), 1))
    )
    ai_avg_length = (
        round(sum(len(m.get("content", "")) for m in ai_messages) / max(len(ai_messages), 1))
    )

    if total_turns <= 4:
        efficiency_label = "very_efficient"
        efficiency_score = 90
        explanation = f"Deal reached in {total_turns} turns — concise and decisive negotiation."
    elif total_turns <= 7:
        efficiency_label = "efficient"
        efficiency_score = 80
        explanation = f"Deal reached in {total_turns} turns — good pace with room for thorough discussion."
    elif total_turns <= 12:
        efficiency_label = "moderate"
        efficiency_score = 65
        explanation = f"Took {total_turns} turns to close. Some rounds may have been repetitive."
    else:
        efficiency_label = "prolonged"
        efficiency_score = 45
        explanation = f"Negotiation took {total_turns} turns — may indicate missed opportunities to close earlier."

    return {
        "total_turns": total_turns,
        "user_messages": len(user_messages),
        "ai_messages": len(ai_messages),
        "user_avg_msg_length": user_avg_length,
        "ai_avg_msg_length": ai_avg_length,
        "efficiency_label": efficiency_label,
        "efficiency_score": efficiency_score,
        "explanation": explanation
    }


# ═══════════════════════════════════════════════════════════════
#  DEAL QUALITY SCORING
# ═══════════════════════════════════════════════════════════════

def score_deal_quality(deal_params: Dict, final_terms: Dict) -> Dict[str, Any]:
    """
    Score the final deal relative to the AI's parameters.
    This is pure math — how close did the user get to the AI's limits?
    """
    opening_price = float(deal_params["price"]["opening"])
    target_price = float(deal_params["price"]["target"])
    reservation_price = float(deal_params["price"]["reservation"])

    opening_delivery = float(deal_params["delivery"]["opening"])
    target_delivery = float(deal_params["delivery"]["target"])
    reservation_delivery = float(deal_params["delivery"]["reservation"])

    final_price = float(final_terms.get("price", opening_price))
    final_delivery = float(final_terms.get("delivery", opening_delivery))

    # Price score: how far below opening did user push?
    price_range = opening_price - reservation_price
    price_improvement = opening_price - final_price

    if price_range > 0:
        price_pct = round((price_improvement / price_range) * 100, 1)
    else:
        price_pct = 0

    # Delivery score: how much faster than opening?
    delivery_range = opening_delivery - reservation_delivery
    delivery_improvement = opening_delivery - final_delivery

    if delivery_range > 0:
        delivery_pct = round((delivery_improvement / delivery_range) * 100, 1)
    else:
        delivery_pct = 0

    # Weighted combined score (price weighted higher — it's the primary variable)
    combined_pct = round(price_pct * 0.65 + delivery_pct * 0.35, 1)

    # Map percentage to grade score
    if combined_pct >= 85:
        score = 95
        grade = "A"
        explanation = (f"Exceptional deal. User achieved ${final_price} (pushed {price_pct}% through the price range) "
                       f"and {int(final_delivery)}-day delivery ({delivery_pct}% through delivery range).")
    elif combined_pct >= 65:
        score = 85
        grade = "B"
        explanation = (f"Strong deal. User achieved ${final_price} ({price_pct}% of possible price savings) "
                       f"and {int(final_delivery)}-day delivery ({delivery_pct}% of possible delivery improvement).")
    elif combined_pct >= 40:
        score = 72
        grade = "C"
        explanation = (f"Moderate deal. User achieved ${final_price} ({price_pct}% of possible savings) "
                       f"and {int(final_delivery)}-day delivery. Significant room for improvement.")
    elif combined_pct >= 15:
        score = 58
        grade = "D"
        explanation = (f"Weak deal. User achieved ${final_price} (only {price_pct}% of possible savings). "
                       f"The AI's opening was ${opening_price} — user didn't push hard enough.")
    else:
        score = 40
        grade = "F"
        explanation = (f"Poor deal. Final price ${final_price} is near the AI's opening of ${opening_price}. "
                       f"User accepted terms with minimal improvement.")

    return {
        "final_price": final_price,
        "final_delivery": int(final_delivery),
        "price_improvement_pct": price_pct,
        "delivery_improvement_pct": delivery_pct,
        "combined_pct": combined_pct,
        "score": score,
        "grade": grade,
        "explanation": explanation,
        "price_analysis": {
            "opening": opening_price,
            "target": target_price,
            "reservation": reservation_price,
            "final": final_price,
            "savings": round(opening_price - final_price, 2)
        },
        "delivery_analysis": {
            "opening": int(opening_delivery),
            "target": int(target_delivery),
            "reservation": int(reservation_delivery),
            "final": int(final_delivery),
            "days_saved": int(opening_delivery - final_delivery)
        }
    }


# ═══════════════════════════════════════════════════════════════
#  MASTER EVALUATION FUNCTION
# ═══════════════════════════════════════════════════════════════

def run_full_analysis(history: List[Dict], deal_params: Dict,
                      final_terms: Dict) -> Dict[str, Any]:
    """
    Run all NLP analyses and produce a complete metrics report.
    This is Layer 1 — returns concrete numbers for Layer 2 (Claude).

    Returns a dict with all metrics + a formatted summary string
    ready to inject into Claude's evaluation prompt.
    """
    # 1. Extract all offers
    offers = extract_offers_from_conversation(history)

    # 2. Concession analysis
    user_concessions = analyze_concessions(offers["user"], "user")
    ai_concessions = analyze_concessions(offers["ai"], "ai")

    # 3. Anchoring
    anchoring = analyze_anchoring(offers["user"], deal_params)

    # 4. Sentiment
    sentiment = analyze_sentiment(history, "user")

    # 5. Trade-offs
    trade_offs = detect_trade_offs(history)

    # 6. Persuasion & justification
    persuasion = detect_persuasion_and_justification(history)

    # 7. Turn efficiency
    efficiency = analyze_turn_efficiency(history, deal_params, final_terms)

    # 8. Deal quality (pure math)
    deal_quality = score_deal_quality(deal_params, final_terms)

    # ── Weighted overall score ──
    weights = {
        "deal_quality": 0.33,
        "trade_off": 0.20,
        "anchoring": 0.12,
        "persuasion": 0.12,
        "sentiment": 0.08,
        "efficiency": 0.08,
        "concession_pattern": 0.07,
    }

    # Score concession pattern
    pattern = user_concessions["concession_pattern"]
    if pattern == "gradual":
        concession_score = 90
    elif pattern == "aggressive_start":
        concession_score = 75
    elif pattern == "firm":
        concession_score = 60
    elif pattern == "single_move":
        concession_score = 65
    else:
        concession_score = 40

    # Sentiment score mapped to 0-100
    sent_score = int(sentiment["avg_sentiment"] * 80 + sentiment["tone_consistency"] * 20)

    raw_scores = {
        "deal_quality": deal_quality["score"],
        "trade_off": trade_offs["score"],
        "anchoring": anchoring["anchor_score"],
        "persuasion": persuasion["score"],
        "sentiment": sent_score,
        "efficiency": efficiency["efficiency_score"],
        "concession_pattern": concession_score,
    }

    overall = round(sum(raw_scores[k] * weights[k] for k in weights), 1)

    # Map to letter grade
    if overall >= 90:
        overall_grade = "A"
    elif overall >= 80:
        overall_grade = "B+"
    elif overall >= 70:
        overall_grade = "B"
    elif overall >= 60:
        overall_grade = "C"
    elif overall >= 50:
        overall_grade = "D"
    else:
        overall_grade = "F"

    # ── Build formatted summary for Claude ──
    metrics_summary = _format_metrics_for_prompt(
        deal_quality, user_concessions, anchoring,
        sentiment, trade_offs, persuasion, efficiency,
        raw_scores, overall, overall_grade, concession_score
    )

    return {
        "overall_score": overall,
        "overall_grade": overall_grade,
        "raw_scores": raw_scores,
        "weights": weights,
        "deal_quality": deal_quality,
        "user_concessions": user_concessions,
        "ai_concessions": ai_concessions,
        "anchoring": anchoring,
        "sentiment": sentiment,
        "trade_offs": trade_offs,
        "persuasion": persuasion,
        "efficiency": efficiency,
        "metrics_summary": metrics_summary,
    }


def _format_metrics_for_prompt(
    deal_quality, user_concessions, anchoring,
    sentiment, trade_offs, persuasion, efficiency,
    raw_scores, overall, overall_grade, concession_score
) -> str:
    """Format all metrics into a readable string for injection into the Claude prompt."""

    price_traj = " → ".join(f"${p}" for p in user_concessions["price_trajectory"]) or "No price offers detected"

    return f"""
══════════════════════════════════════════════
  NLP ANALYSIS REPORT (Layer 1 — Measured Facts)
══════════════════════════════════════════════

1. DEAL QUALITY (Weight: 33%) — Score: {raw_scores['deal_quality']}/100
   {deal_quality['explanation']}
   Price: ${deal_quality['price_analysis']['opening']} → ${deal_quality['final_price']} (saved ${deal_quality['price_analysis']['savings']})
   Delivery: {deal_quality['delivery_analysis']['opening']} → {deal_quality['final_delivery']} days (saved {deal_quality['delivery_analysis']['days_saved']} days)
   Price improvement: {deal_quality['price_improvement_pct']}% of possible range
   Delivery improvement: {deal_quality['delivery_improvement_pct']}% of possible range

2. TRADE-OFF STRATEGY (Weight: 20%) — Score: {raw_scores['trade_off']}/100
   {trade_offs['explanation']}
   Trade-offs detected: {trade_offs['count']}

3. ANCHORING (Weight: 12%) — Score: {raw_scores['anchoring']}/100
   {anchoring['explanation']}

4. PERSUASION & REASONING (Weight: 12%) — Score: {raw_scores['persuasion']}/100
   {persuasion['explanation']}
   Justifications: {persuasion['justification_count']}, Persuasion techniques: {persuasion['persuasion_count']}

5. PROFESSIONAL TONE (Weight: 8%) — Score: {raw_scores['sentiment']}/100
   Average sentiment: {sentiment['avg_sentiment']} ({sentiment['tone_label']})
   Tone consistency: {sentiment['tone_consistency']}
   Trajectory: {' → '.join(str(s) for s in sentiment['sentiment_trajectory'][:8])}

6. NEGOTIATION EFFICIENCY (Weight: 8%) — Score: {raw_scores['efficiency']}/100
   {efficiency['explanation']}
   User avg message length: {efficiency['user_avg_msg_length']} chars

7. CONCESSION PATTERN (Weight: 7%) — Score: {concession_score}/100
   Pattern: {user_concessions['concession_pattern']}
   Price trajectory: {price_traj}
   Total price concessions: {user_concessions['price_concessions']}
   Total price movement: ${user_concessions['price_total_movement']}
   Avg concession size: ${user_concessions['avg_price_concession']}

──────────────────────────────────────────────
OVERALL WEIGHTED SCORE: {overall}/100 ({overall_grade})
──────────────────────────────────────────────
"""
