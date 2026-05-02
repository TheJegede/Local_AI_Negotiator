import anthropic
import json
import logging
import os
import re
from typing import Dict, Any
from prompts import MASTER_PROMPT_TEMPLATE
import logic
import evaluator

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

logger = logging.getLogger(__name__)

# --- LLM EXTRACTION PROMPT ---
EXTRACTION_SYS_PROMPT = """
You are a Negotiation Data Extractor. Extract information from user messages and return ONLY valid JSON.

INSTRUCTIONS:
1. Extract the price if user proposes a new one: look for "$X" or "X dollars"
2. Extract delivery days if user proposes new ones: look for "X days" or "X day delivery"
3. Detect if user accepts a parameter:
   - "the price works" / "price is fine" / "price works" → price_accepted = true
   - "the delivery works" / "delivery is fine" / "days works" → delivery_accepted = true
4. If user proposes ANY new number, agreement_status = false (it's a counter-offer)
5. If user says "deal", "I agree", "I accept" with NO new numbers → agreement_status = true
6. Return ONLY a JSON object with no extra text

OUTPUT MUST BE VALID JSON:
{
  "current_price": null or <number>,
  "current_delivery_days": null or <number>,
  "agreement_status": true or false,
  "price_accepted": true or false,
  "delivery_accepted": true or false,
  "rejected_terms": []
}

EXAMPLES:
- User: "the price works for me but lets do 30 days"
  Output: {"current_price": null, "current_delivery_days": 30, "agreement_status": false, "price_accepted": true, "delivery_accepted": false, "rejected_terms": []}

- User: "I can pay 300 for 20 days"
  Output: {"current_price": 300, "current_delivery_days": 20, "agreement_status": false, "price_accepted": false, "delivery_accepted": false, "rejected_terms": []}

- User: "deal"
  Output: {"current_price": null, "current_delivery_days": null, "agreement_status": true, "price_accepted": false, "delivery_accepted": false, "rejected_terms": []}
"""

def extract_negotiation_state(user_input: str, history: list) -> dict:
    """
    Uses Claude Haiku to safely extract numbers from TEXT with CONTEXT AWARENESS.
    Now includes conversation history so extraction can handle implicit references.
    Includes regex fallback for increased robustness.
    """
    try:
        
        # Build context: current message + last AI offer for context
        context_str = f"Current user message: {user_input}"
        
        # Include the last AI message for context (helps with "for that delivery" references)
        if history:
            for msg in reversed(history):
                if msg.get('role') == 'assistant':
                    context_str += f"\n\nLast AI offer (for reference): {msg.get('content', '')}"
                    break

        messages = [
            {"role": "user", "content": f"{EXTRACTION_SYS_PROMPT}\n\nDATA:\n{context_str}"}
        ]

        response = client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=150,
            temperature=0.0,
            messages=messages
        )

        response_text = response.content[0].text.strip()
        # Strip <thinking> tags that Claude sometimes emits
        response_text = re.sub(r'<thinking>.*?</thinking>', '', response_text, flags=re.DOTALL).strip()
        
        # Multiple cleanup strategies for JSON parsing robustness
        clean_json = re.sub(r'```json\s*|\s*```', '', response_text)  # Remove markdown
        clean_json = re.sub(r'```\s*|\s*```', '', clean_json)  # Remove bare backticks
        clean_json = clean_json.strip()  # Strip whitespace
        
        # Try to extract JSON if wrapped in other text
        if not clean_json.startswith('{'):
            json_match = re.search(r'\{.*\}', clean_json, re.DOTALL)
            if json_match:
                clean_json = json_match.group(0)
        
        extracted = json.loads(clean_json)
        
        # Ensure all required keys exist with defaults
        if 'price_accepted' not in extracted:
            extracted['price_accepted'] = False
        if 'delivery_accepted' not in extracted:
            extracted['delivery_accepted'] = False
        if 'rejected_terms' not in extracted:
            extracted['rejected_terms'] = []
        
        # VALIDATION: If LLM extraction returns None, attempt regex fallback
        if extracted.get('current_price') is None:
            logger.warning("LLM extraction returned None for price, attempting regex fallback")
            # Require explicit $ sign to prevent false positives (e.g. '30 days' → 30)
            price_match = re.search(r'\$\s*(\d+(?:\.\d{2})?)', user_input)
            if price_match:
                extracted['current_price'] = float(price_match.group(1))
                logger.info(f"Regex fallback extracted price: ${extracted['current_price']}")
        
        if extracted.get('current_delivery_days') is None:
            delivery_match = re.search(r'(\d+)\s*day', user_input, re.IGNORECASE)
            if delivery_match:
                extracted['current_delivery_days'] = int(delivery_match.group(1))
                logger.info(f"Regex fallback extracted delivery: {extracted['current_delivery_days']} days")
        
        logger.info(f"Extracted state - Price: {extracted.get('current_price')}, Delivery: {extracted.get('current_delivery_days')}, Agreement: {extracted.get('agreement_status')}, Price Accepted: {extracted.get('price_accepted')}")
        return extracted

    except Exception as e:
        logger.error(f"Extraction Error: {e}, Response text: {response_text if 'response_text' in locals() else 'N/A'}")
        # Last resort regex extraction
        try:
            user_input_lower = user_input.lower()
            price_match = re.search(r'\$\s*(\d+(?:\.\d{2})?)', user_input)
            delivery_match = re.search(r'(\d+)\s*day', user_input, re.IGNORECASE)
            
            # Check for partial acceptance phrases
            price_accepted = any(phrase in user_input_lower for phrase in ["price works", "price is fine", "that price"])
            delivery_accepted = any(phrase in user_input_lower for phrase in ["delivery works", "delivery is fine", "timeline works"])
            
            # Determine agreement status - only if no new numbers proposed
            agreement_status = (
                any(word in user_input_lower for word in ["deal", "agree", "accept"])
                and price_match is None
                and delivery_match is None
            )
            
            logger.warning(f"Using regex fallback - Price: {price_match.group(1) if price_match else None}, Delivery: {delivery_match.group(1) if delivery_match else None}")
            
            return {
                "current_price": float(price_match.group(1)) if price_match else None,
                "current_delivery_days": int(delivery_match.group(1)) if delivery_match else None,
                "agreement_status": agreement_status,
                "price_accepted": price_accepted,
                "delivery_accepted": delivery_accepted,
                "rejected_terms": []
            }
        except Exception as fallback_error:
            logger.error(f"Fallback extraction also failed: {fallback_error}")
            return {
                "current_price": None, 
                "current_delivery_days": None, 
                "agreement_status": False,
                "price_accepted": False,
                "delivery_accepted": False,
                "rejected_terms": []
            }


def create_negotiation_prompt(
    user_input: str,
    history: list,
    deal_params: Dict[str, Any],
    last_ai_price: float = None,
    last_ai_delivery: float = None,
    previous_user_price: float = None,
    locked_parameters: Dict = None,
    guardrail_context: Dict = None
) -> tuple:
    """
    Three-phase pipeline: Extract -> Calculate (with guardrails) -> Prompt.
    
    Args:
        guardrail_context: Session-tracked guardrail state from main.py:
            - 'consecutive_identical_count': int
            - 'user_offer_history': list of past user prices
    
    Returns: (prompt_string, next_price, next_delivery, agreement_reached)
        agreement_reached: True if strategy engine confirmed a valid deal,
                          False if explicitly rejected, None if not applicable
    """
    
    # --- STEP 1: EXTRACT USER OFFER ---
    extraction = extract_negotiation_state(user_input, history)
    user_price_float = extraction.get('current_price')
    user_delivery_float = extraction.get('current_delivery_days')
    
    # --- STEP 2: CALCULATE STRATEGY (guardrails run first, then concession math) ---
    try:
        strategy = logic.generate_turn_strategy(
            extracted_user_price=user_price_float,
            last_ai_price=last_ai_price,
            deal_params=deal_params,
            user_input_text=user_input,
            extracted_user_delivery=user_delivery_float,
            last_ai_delivery=last_ai_delivery,
            previous_user_price=previous_user_price,
            locked_parameters=locked_parameters,
            guardrail_context=guardrail_context
        )
        
        # VALIDATE strategy response has all required fields
        strategy = logic._validate_strategy_response(
            strategy,
            fallback_price=last_ai_price if last_ai_price else deal_params['price']['opening'],
            fallback_delivery=last_ai_delivery if last_ai_delivery else deal_params['delivery']['opening']
        )
        
        guidance = strategy['guidance']
        next_price = strategy['next_price']
        next_delivery = strategy['next_delivery']
        # Extract the authoritative agreement flag from strategy engine
        agreement_reached = strategy.get('agreement_reached', None)
    except Exception as e:
        logger.error(f"Strategy Gen Failed: {e}", exc_info=True)
        fallback_price = last_ai_price if last_ai_price else deal_params['price']['opening']
        fallback_delivery = last_ai_delivery if last_ai_delivery else deal_params['delivery']['opening']
        guidance = "Negotiate professionally."
        next_price = fallback_price
        next_delivery = fallback_delivery
        agreement_reached = None

    # --- STEP 3: FORMAT PROMPT ---
    deal_params_str = logic.format_deal_parameters(deal_params)
    
    history_str = ""
    if history:
        history_str = "\n".join([f"{msg.get('role', 'unknown').title()}: {msg.get('content', '')}" for msg in history[-6:]])
    
    prompt = MASTER_PROMPT_TEMPLATE.format(
        deal_parameters=deal_params_str,
        conversation_history=history_str,
        user_input=user_input,
        turn_guidance=guidance,
        next_price=next_price,
        next_delivery=next_delivery
    )
    
    return prompt, next_price, next_delivery, agreement_reached

def get_bedrock_response(prompt: str) -> str:
    try:
        response = client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=500,
            temperature=0.2,
            messages=[{"role": "user", "content": prompt}]
        )
        text = response.content[0].text.strip()
        text = re.sub(r'<thinking>.*?</thinking>', '', text, flags=re.DOTALL).strip()
        return text
    except Exception as e:
        return f"Error: {str(e)[:50]}"


def get_evaluation(history: list, deal_params: Dict, final_terms: Dict) -> str:
    """
    Two-layer evaluation pipeline:
      Layer 1: Deterministic NLP analysis (evaluator.py) → concrete metrics
      Layer 2: Claude + metrics + rubric → evidence-based qualitative report
    """
    # Safe getters
    final_price = final_terms.get('price', 'N/A')
    final_delivery = final_terms.get('delivery', 'N/A')
    final_volume = final_terms.get('volume', 1000)

    # ── LAYER 1: Run NLP Analysis ──
    try:
        analysis = evaluator.run_full_analysis(history, deal_params, final_terms)
        metrics_summary = analysis["metrics_summary"]
        overall_score = analysis["overall_score"]
        overall_grade = analysis["overall_grade"]
        raw_scores = analysis["raw_scores"]
        logger.info(f"NLP analysis complete: {overall_score}/100 ({overall_grade})")
    except Exception as e:
        logger.error(f"NLP analysis failed, falling back: {e}", exc_info=True)
        metrics_summary = "NLP analysis unavailable — please evaluate from conversation only."
        overall_score = None
        overall_grade = None
        raw_scores = {}

    # ── Format conversation log ──
    conversation_log = ""
    if history:
        conversation_log = "\n".join(
            [f"{m.get('role','').upper()}: {m.get('content','')}" for m in history]
        )

    # ── LAYER 2: Claude prompt with injected metrics ──
    eval_prompt = f"""
You are an expert negotiation coach writing a detailed evaluation report for a university student.
You have TWO sources of information:
  1. The NLP ANALYSIS REPORT below — these are MEASURED FACTS computed from the conversation.
     USE these numbers as the foundation for your scores. Do NOT invent different scores.
  2. The full conversation — use this for qualitative color, specific examples, and nuance.

Your job is to EXPLAIN WHY the scores are what they are, citing specific moments from the conversation.
Do NOT override the measured scores unless you find a clear analytical error.

--- SELLER'S (AI's) SECRET PARAMETERS ---
Price Opening: ${deal_params['price']['opening']}
Price Target (AI's Ideal): ${deal_params['price']['target']}
Price Reservation (AI's Walk-away): ${deal_params['price']['reservation']}
Delivery Opening: {deal_params['delivery']['opening']} days
Delivery Target: {deal_params['delivery']['target']} days
Delivery Reservation (Fastest): {deal_params['delivery']['reservation']} days

--- NLP ANALYSIS REPORT (MEASURED METRICS) ---
{metrics_summary}

--- FULL NEGOTIATION CONVERSATION ---
{conversation_log}

--- EVALUATION RUBRIC ---

1. Deal Quality & Outcome (Weight: 33%)
   - NLP Score: {raw_scores.get('deal_quality', 'N/A')}/100
   - How far did the user push below the AI's opening? Did they reach near the reservation?
   - Grade: A (90-100) near reservation, B (80-89) strong, C (70-79) moderate, D/F (<70) weak

2. Trade-off Strategy & Analytical Reasoning (Weight: 20%)
   - NLP Score: {raw_scores.get('trade_off', 'N/A')}/100
   - Did user link price to delivery? Propose conditional offers?
   - Quote specific trade-off attempts (or note their absence)

3. Anchoring & Opening Strategy (Weight: 12%)
   - NLP Score: {raw_scores.get('anchoring', 'N/A')}/100
   - Where did user open relative to AI's range? Was the anchor effective?

4. Persuasion & Reasoning (Weight: 12%)
   - NLP Score: {raw_scores.get('persuasion', 'N/A')}/100
   - Did user justify offers with business logic, data, or strategic reasoning?
   - Quote specific justification examples (or note generic "how about $X" offers)

5. Professional Tone & Communication (Weight: 8%)
   - NLP Score: {raw_scores.get('sentiment', 'N/A')}/100
   - Was user professional, respectful, and clear throughout?

6. Negotiation Efficiency (Weight: 8%)
   - NLP Score: {raw_scores.get('efficiency', 'N/A')}/100
   - How many turns to close? Was the pace appropriate?

7. Concession Pattern (Weight: 7%)
   - NLP Score: {raw_scores.get('concession_pattern', 'N/A')}/100
   - Pattern: gradual (best), aggressive_start, firm, or erratic?

--- OUTPUT FORMAT (Follow this exactly) ---

📊 FINAL EVALUATION REPORT

━━━━━━━━━━━━━━━━━━━━━━━━━━
Final Deal Achieved:
  Price: ${final_price} per unit
  Delivery: {final_delivery} days
  Volume: {final_volume} units
━━━━━━━━━━━━━━━━━━━━━━━━━━

Category Scores:
  1. Deal Quality: [score]/100 (Weight: 33%) — [1-2 sentence explanation citing numbers]
  2. Trade-off Strategy: [score]/100 (Weight: 20%) — [cite specific trade-off attempts or absence]
  3. Anchoring: [score]/100 (Weight: 12%) — [cite opening offer and its effectiveness]
  4. Persuasion & Reasoning: [score]/100 (Weight: 12%) — [cite specific justifications used]
  5. Professional Tone: [score]/100 (Weight: 8%) — [cite tone observations]
  6. Efficiency: [score]/100 (Weight: 8%) — [cite turn count]
  7. Concession Pattern: [score]/100 (Weight: 7%) — [cite the pattern type and trajectory]

━━━━━━━━━━━━━━━━━━━━━━━━━━
Overall Weighted Score: {overall_score if overall_score else '[calculate]'}/100
Grade: {overall_grade if overall_grade else '[determine]'}
━━━━━━━━━━━━━━━━━━━━━━━━━━

Key Strengths:
  • [Specific strength #1 — cite a moment from the conversation]
  • [Specific strength #2]

Areas for Improvement:
  • [Specific area #1 — what should the user have done differently, with an example]
  • [Specific area #2]

Detailed Feedback:
[2-3 paragraphs of constructive, specific feedback. Reference exact quotes from the conversation.
Explain what the user did well, what they missed, and provide actionable advice for future negotiations.
Mention specific techniques: anchoring, BATNA, trade-offs, concession pacing, closing techniques.]
"""

    try:
        response = client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=2000,
            temperature=0.3,
            messages=[{"role": "user", "content": eval_prompt}]
        )
        return response.content[0].text.strip()

    except Exception as e:
        logger.error(f"Evaluation error: {e}")
        # If Claude fails, return the NLP metrics as a standalone report
        if metrics_summary and overall_score:
            return f"AI evaluation unavailable. NLP Analysis Report:\n{metrics_summary}"
        return f"Error generating evaluation: {str(e)}"
