import logging
import random
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# --- 1. DEAL GENERATION (Keep as is) ---
def generate_deal_parameters(seed=None):
    """Generate random deal parameters. Uses local Random instance for thread safety."""
    # Determine seed — convert student ID string to int if needed
    if seed is not None:
        try:
            if isinstance(seed, str):
                seed = hash(seed) % (2**32)
        except Exception:
            seed = None

    # Local Random instance — avoids mutating global random state
    # (critical when multiple requests run concurrently)
    rng = random.Random(seed)

    opening_price = round(rng.uniform(30, 50) * 10, 2)
    target_price = round(opening_price * (1 - 0.12), 2)
    reservation_price = round(opening_price * (1 - 0.20), 2)

    opening_delivery = rng.randint(25, 45)
    target_delivery = int(opening_delivery * 0.85)
    reservation_delivery = int(opening_delivery * 0.70)

    return {
        "price": { "opening": opening_price, "target": target_price, "reservation": reservation_price },
        "delivery": { "opening": opening_delivery, "target": target_delivery, "reservation": reservation_delivery },
        "volume": { "standard": 1000 }
    }

def format_deal_parameters(params):
    """Format deal parameters into a string for the AI's context window."""
    return f"""
--- NEGOTIATION DATA ---
1. Opening Price: ${params['price']['opening']}
2. Target Price: ${params['price']['target']}
3. Walk-away Price: ${params['price']['reservation']}
4. Opening Delivery: {params['delivery']['opening']} days
5. Target Delivery: {params['delivery']['target']} days
6. Fastest Delivery: {params['delivery']['reservation']} days
"""

def _validate_strategy_response(response: Dict[str, Any], fallback_price: float, fallback_delivery: int) -> Dict[str, Any]:
    """Ensure strategy response has all required valid fields"""
    if not response:
        response = {}
    
    # Ensure all required keys exist
    if 'next_price' not in response or response['next_price'] is None:
        logger.warning(f"Strategy response missing next_price, using fallback {fallback_price}")
        response['next_price'] = fallback_price
    
    if 'next_delivery' not in response or response['next_delivery'] is None:
        logger.warning(f"Strategy response missing next_delivery, using fallback {fallback_delivery}")
        response['next_delivery'] = fallback_delivery
    
    if 'guidance' not in response:
        response['guidance'] = 'Please let me know your thoughts.'
    
    # Convert types to ensure JSON serialization
    response['next_price'] = float(response['next_price'])
    response['next_delivery'] = int(response['next_delivery'])
    
    return response


# ═══════════════════════════════════════════════════════════════
#  GUARDRAIL FUNCTIONS — Prevent AI exploitation
#  These run BEFORE any concession math, acting as short-circuit
#  gates that block unreasonable inputs from entering the
#  negotiation pipeline.
# ═══════════════════════════════════════════════════════════════

# Threshold: offers this far below AI's opening are instant rejections
LOWBALL_THRESHOLD_PCT = 0.50  # 50% below opening → hard reject


def check_extreme_lowball(
    user_price: Optional[float],
    ai_opening_price: float,
    last_ai_price: float,
    last_ai_delivery: float
) -> Optional[Dict[str, Any]]:
    """
    GUARDRAIL 1: Extreme Lowball Protection
    
    Rejects offers that are 50% or more below the AI's INITIAL opening price.
    This catches:
      - Absurd offers like $1, $0, $5
      - Exploitative lowballs like $150 when opening is $400
    
    The check is against the OPENING price (not current price), ensuring
    the threshold remains consistent throughout the negotiation regardless
    of how far the AI has already conceded.
    
    Returns:
        None if offer passes the guardrail (continue to normal logic)
        Strategy dict if offer is rejected (short-circuit — no concession)
    """
    # No price offered → not a lowball, let other logic handle it
    if user_price is None:
        return None

    # Calculate how far below opening the offer is
    # e.g., opening=$400, offer=$180 → gap_pct=0.55 → REJECT
    gap_from_opening = ai_opening_price - user_price
    gap_pct = gap_from_opening / ai_opening_price if ai_opening_price > 0 else 0

    if gap_pct >= LOWBALL_THRESHOLD_PCT:
        logger.warning(
            f"GUARDRAIL: Extreme lowball detected. "
            f"User offered ${user_price} which is {gap_pct*100:.0f}% below "
            f"opening of ${ai_opening_price} (threshold: {LOWBALL_THRESHOLD_PCT*100:.0f}%)"
        )
        return {
            'guidance': (
                f"The user offered ${user_price}, which is {gap_pct*100:.0f}% below our "
                f"opening price of ${ai_opening_price}. This is far too low to begin "
                f"meaningful negotiations. "
                f"ACTION: Firmly but professionally reject this offer. Say something like: "
                f"'I appreciate your interest, but ${user_price} is significantly below "
                f"what we can consider. Our opening rate is ${ai_opening_price} for "
                f"{int(last_ai_delivery)}-day delivery. Please provide a more realistic "
                f"counteroffer so we can work toward an agreement.' "
                f"Do NOT make any concession. Hold firm at ${last_ai_price}."
            ),
            # CRITICAL: No price drop. AI holds its current position.
            'next_price': last_ai_price,
            'next_delivery': last_ai_delivery,
            'guardrail_triggered': 'extreme_lowball'
        }

    # Offer is within acceptable range → continue to normal logic
    return None


def check_repetition_stalemate(
    user_price: Optional[float],
    consecutive_identical_count: int,
    last_ai_price: float,
    last_ai_delivery: float
) -> Optional[Dict[str, Any]]:
    """
    GUARDRAIL 2: Repetition Pushback (Stalemate Detection)
    
    If the user submits the exact same price offer 3 or more times
    consecutively, the AI halts ALL price concessions and pushes back.
    
    This prevents a strategy where the user simply repeats the same
    lowball hoping the AI's gradual concession algorithm will
    eventually reach their price.
    
    The count is tracked in session state by main.py and passed in
    via the guardrail_context parameter.
    
    Args:
        user_price: The extracted price from the current message
        consecutive_identical_count: How many times this exact price
            has been submitted in a row (tracked by main.py)
        last_ai_price: AI's current price position
        last_ai_delivery: AI's current delivery position
    
    Returns:
        None if count < 3 (continue normal logic)
        Strategy dict if count >= 3 (freeze concessions)
    """
    # No price or not enough repeats → let normal logic handle
    if user_price is None or consecutive_identical_count < 3:
        return None

    logger.warning(
        f"GUARDRAIL: Stalemate detected. User has repeated ${user_price} "
        f"{consecutive_identical_count} times consecutively. Freezing concessions."
    )
    return {
        'guidance': (
            f"The user has submitted the same offer of ${user_price} for the "
            f"{consecutive_identical_count}{'rd' if consecutive_identical_count == 3 else 'th'} "
            f"consecutive time. We will NOT make any further concessions. "
            f"ACTION: Politely but firmly push back on the stagnation. Say something like: "
            f"'I notice we seem to be at an impasse at ${user_price}. I've already shown "
            f"significant flexibility from our opening of [opening price]. My current offer "
            f"of ${last_ai_price} with {int(last_ai_delivery)}-day delivery represents real "
            f"value. Can you meet me closer to this, or would you like to explore adjusting "
            f"the delivery timeline instead?' "
            f"Do NOT reduce the price below ${last_ai_price}."
        ),
        # CRITICAL: No concession. AI holds firm.
        'next_price': last_ai_price,
        'next_delivery': last_ai_delivery,
        'guardrail_triggered': 'repetition_stalemate'
    }


# ═══════════════════════════════════════════════════════════════
#  STRATEGY ENGINE (Pure Math, No Extraction)
# ═══════════════════════════════════════════════════════════════

def generate_turn_strategy(
    extracted_user_price: float, 
    last_ai_price: float, 
    deal_params: Dict[str, Any],
    user_input_text: str,
    extracted_user_delivery: float = None,
    last_ai_delivery: float = None,
    previous_user_price: float = None,  # Track buyer movement
    locked_parameters: Dict = None,  # Track locked-in parameters
    guardrail_context: Dict = None   # Session-tracked guardrail state
) -> Dict[str, Any]:
    """
    Decides the next move and EXPLICITLY RETURNS the offer terms.
    Includes guardrails, movement detection, and reciprocal concessions.
    
    Args:
        extracted_user_price: Price detected in user's current message
        last_ai_price: AI's most recent price offer
        deal_params: Full deal parameter dict (opening/target/reservation)
        user_input_text: Raw user message text
        extracted_user_delivery: Delivery days detected in user message
        last_ai_delivery: AI's most recent delivery offer
        previous_user_price: User's price from the PREVIOUS turn
        locked_parameters: Which params are locked in by mutual agreement
        guardrail_context: Session state for guardrails, containing:
            - 'consecutive_identical_count': int
            - 'user_offer_history': list of past user prices
    
    Returns: {
        'guidance': str,    # Instruction text for the AI persona
        'next_price': float,
        'next_delivery': int,
        'guardrail_triggered': str or absent  # Which guardrail fired, if any
    }
    """
    try:
        price_reservation = float(deal_params['price']['reservation'])
        price_opening = float(deal_params['price']['opening'])
        delivery_reservation = float(deal_params['delivery']['reservation'])
        delivery_opening = float(deal_params['delivery']['opening'])
        delivery_target = float(deal_params['delivery']['target'])
        
        # CRITICAL FIX: Only default to opening price on FIRST turn (None check only)
        if last_ai_price is None:
            logger.info("First turn - using opening price as baseline")
            last_ai_price = price_opening
        if last_ai_delivery is None:
            logger.info("First turn - using opening delivery as baseline")
            last_ai_delivery = delivery_opening
        
        # NEW: If parameters are locked, enforce them
        if locked_parameters is None:
            locked_parameters = {
                'price_locked': False,
                'delivery_locked': False,
                'locked_price_value': None,
                'locked_delivery_value': None
            }
        
        # If price is locked, use locked value (don't negotiate it)
        if locked_parameters.get('price_locked'):
            last_ai_price = locked_parameters.get('locked_price_value', last_ai_price)
            logger.info(f"🔒 Price is LOCKED at ${last_ai_price} - will not negotiate further")
        
        # If delivery is locked, use locked value (don't negotiate it)
        if locked_parameters.get('delivery_locked'):
            last_ai_delivery = locked_parameters.get('locked_delivery_value', last_ai_delivery)
            logger.info(f"🔒 Delivery is LOCKED at {last_ai_delivery} days - will not negotiate further")

    except Exception as e:
        logger.error(f"Logic Error: {e}", exc_info=True)
        # Ensure we always return valid values
        safe_price = last_ai_price if last_ai_price is not None else deal_params['price']['opening']
        safe_delivery = last_ai_delivery if last_ai_delivery is not None else deal_params['delivery']['opening']
        return {
            'guidance': 'Negotiate professionally.',
            'next_price': safe_price,
            'next_delivery': safe_delivery
        }

    # ── GUARDRAIL CHECKS (run before ANY concession logic) ──
    # These act as short-circuit gates: if triggered, the function
    # returns immediately with zero concession.

    # Default guardrail context if not provided by main.py
    if guardrail_context is None:
        guardrail_context = {
            'consecutive_identical_count': 0,
            'user_offer_history': []
        }

    # GUARDRAIL 1: Extreme lowball / absurd offer check
    lowball_result = check_extreme_lowball(
        user_price=extracted_user_price,
        ai_opening_price=price_opening,
        last_ai_price=last_ai_price,
        last_ai_delivery=last_ai_delivery
    )
    if lowball_result is not None:
        return lowball_result

    # GUARDRAIL 2: Repetition stalemate check
    repetition_result = check_repetition_stalemate(
        user_price=extracted_user_price,
        consecutive_identical_count=guardrail_context.get('consecutive_identical_count', 0),
        last_ai_price=last_ai_price,
        last_ai_delivery=last_ai_delivery
    )
    if repetition_result is not None:
        return repetition_result

    # --- A. DEAL CLOSING CHECK (Compound Verification) ---
    # Two conditions must BOTH be met:
    #   1. SEMANTIC INTENT: User uses agreement language
    #   2. PRICE VALIDATION: Price is within AI's acceptable range
    # This prevents false positives like "$250 works for me" when $250 < reservation.
    
    deal_keywords = ["deal", "agree", "works for me", "accept", "sold", "i accept", "we have a deal"]
    # Also detect negation — "I agree to disagree" is NOT an agreement
    negation_patterns = ["disagree", "don't agree", "not accept", "can't accept", "won't accept",
                         "no deal", "not a deal", "unable to"]
    
    user_lower = user_input_text.lower()
    has_deal_keyword = any(word in user_lower for word in deal_keywords)
    has_negation = any(neg in user_lower for neg in negation_patterns)
    
    # Negation overrides deal keywords — "I agree to disagree" is NOT a deal
    is_deal_signal = has_deal_keyword and not has_negation

    if is_deal_signal:
        # CASE 1: User agrees with NO specific price → accepts AI's last offer
        # This is always valid — they're accepting what we already proposed
        if extracted_user_price is None:
            return {
                'guidance': (f"User AGREED to your price of ${last_ai_price}. "
                            f"ACTION: State 'Deal Finalized at ${last_ai_price} with {int(last_ai_delivery)}-day delivery.' "
                            f"Do not ask further questions."),
                'next_price': last_ai_price,
                'next_delivery': last_ai_delivery,
                'agreement_reached': True  # AUTHORITATIVE FLAG
            }

        # CASE 2: User agrees with a specific price → MUST validate against reservation
        # "$330 works for me" where reservation=$320 → VALID deal
        # "$250 works for me" where reservation=$320 → INVALID, counteroffer
        if extracted_user_price >= price_reservation:
            return {
                'guidance': (f"User AGREED to valid price ${extracted_user_price}. "
                            f"ACTION: State 'Deal Finalized at ${extracted_user_price} with {int(last_ai_delivery)}-day delivery.' "
                            f"Do not ask further questions."),
                'next_price': extracted_user_price,
                'next_delivery': last_ai_delivery,
                'agreement_reached': True  # AUTHORITATIVE FLAG
            }
        else:
            # COMPOUND VERIFICATION FAILED: User said "deal" but price is unacceptable
            # Do NOT finalize — treat as a counteroffer below reservation
            logger.warning(
                f"AGREEMENT REJECTED: User said '{user_input_text[:50]}' but "
                f"${extracted_user_price} is below reservation ${price_reservation}"
            )
            return {
                'guidance': (
                    f"The user expressed agreement but at ${extracted_user_price}, which is "
                    f"below our walk-away price of ${price_reservation}. "
                    f"ACTION: Acknowledge their willingness to close but explain you cannot "
                    f"meet that price. Say: 'I appreciate you wanting to finalize, but "
                    f"${extracted_user_price} is below what I can offer. My best available "
                    f"price is ${last_ai_price} with {int(last_ai_delivery)}-day delivery. "
                    f"Can you work with that?' "
                    f"Do NOT close the deal. Do NOT reduce the price."
                ),
                'next_price': last_ai_price,
                'next_delivery': last_ai_delivery,
                'agreement_reached': False  # EXPLICITLY NOT A DEAL
            }

    # --- B. CASUAL/UNCLEAR INPUT HANDLING ---
    
    # If user just says "hi", "ok", "hmm" etc. with NO OFFER, don't react to old prices
    casual_words = ["hi", "hello", "hmm", "okay", "sure", "lol", "thanks", "no", "nope"]
    is_casual = user_input_text.lower().strip() in casual_words
    
    if is_casual and extracted_user_price is None:
        return {
            'guidance': (f"User sent casual input with no price offer. "
                        f"ACTION: Respond conversationally and ask what price they had in mind. "
                        f"Do not make assumptions about their offer. "
                        f"Say: 'What price range were you thinking for the {int(last_ai_delivery)}-day delivery?'"),
            'next_price': last_ai_price,
            'next_delivery': last_ai_delivery
        }

    # --- C. PRICE NEGOTIATION LOGIC ---
    
    # SPECIAL CASE: Both price AND delivery are locked - no more negotiation
    if locked_parameters.get('price_locked') and locked_parameters.get('delivery_locked'):
        logger.info(f"Both price and delivery are LOCKED - negotiation complete")
        return {
            'guidance': f"Perfect! We have a complete agreement at ${last_ai_price} for {int(last_ai_delivery)}-day delivery.",
            'next_price': last_ai_price,
            'next_delivery': last_ai_delivery
        }
    
    # SPECIAL CASE: Price is locked - only negotiate delivery
    if locked_parameters.get('price_locked') and not locked_parameters.get('delivery_locked'):
        logger.info(f"Price is LOCKED at ${last_ai_price}, negotiating ONLY delivery")
        
        # User can only propose changes to delivery
        if extracted_user_delivery and extracted_user_delivery < last_ai_delivery:
            # User wants faster delivery
            delivery_gap = last_ai_delivery - delivery_reservation
            delivery_reduction = int(delivery_gap * random.uniform(0.05, 0.10))
            delivery_reduction = max(delivery_reduction, 1)
            next_delivery = max(last_ai_delivery - delivery_reduction, delivery_reservation)
            
            return {
                'guidance': (f"I appreciate your interest in faster delivery ({int(extracted_user_delivery)} days). "
                            f"The price remains at ${last_ai_price} as we agreed. "
                            f"For delivery, how about {int(next_delivery)} days? That's a meaningful move toward your request."),
                'next_price': last_ai_price,  # Price stays locked
                'next_delivery': next_delivery
            }
        elif extracted_user_delivery and extracted_user_delivery == last_ai_delivery:
            # User accepted the delivery too
            return {
                'guidance': f"Perfect! We have an agreement at ${last_ai_price} for {int(last_ai_delivery)}-day delivery.",
                'next_price': last_ai_price,
                'next_delivery': last_ai_delivery
            }
        else:
            # No delivery change proposed - stay firm
            return {
                'guidance': f"As discussed, the price is ${last_ai_price}. What timeline works best for {int(last_ai_delivery)}-day delivery?",
                'next_price': last_ai_price,
                'next_delivery': last_ai_delivery
            }
    
    # SPECIAL CASE: Delivery is locked - only negotiate price
    if locked_parameters.get('delivery_locked') and not locked_parameters.get('price_locked'):
        logger.info(f"Delivery is LOCKED at {last_ai_delivery} days, negotiating ONLY price")
        
        if extracted_user_price and extracted_user_price >= price_reservation:
            if extracted_user_price >= last_ai_price:
                # User is offering more or equal - accept
                return {
                    'guidance': f"Excellent! We have an agreement at ${extracted_user_price} for {int(last_ai_delivery)}-day delivery.",
                    'next_price': extracted_user_price,
                    'next_delivery': last_ai_delivery
                }
            else:
                # User offering less - negotiate down from current price
                remaining_spread = last_ai_price - price_reservation
                drop_amount = remaining_spread * random.uniform(0.05, 0.10)
                drop_amount = max(drop_amount, 2.0)
                next_price = round(last_ai_price - drop_amount, 2)
                next_price = max(next_price, price_reservation)
                
                return {
                    'guidance': (f"I appreciate your offer of ${extracted_user_price}. "
                                f"The delivery is locked at {int(last_ai_delivery)} days as agreed. "
                                f"For the price, how about ${next_price}? That's a meaningful concession on our end."),
                    'next_price': next_price,
                    'next_delivery': last_ai_delivery
                }
        else:
            # No price proposal - stay firm
            return {
                'guidance': f"As discussed, delivery is {int(last_ai_delivery)} days. What price would work for you?",
                'next_price': last_ai_price,
                'next_delivery': last_ai_delivery
            }
    
    # Case 1: No number given by user
    if extracted_user_price is None:
        logger.warning(f"EXTRACTION FAILURE: No price detected in '{user_input_text[:50]}...'")
        return {
            'guidance': f"User did not give a clear price. Hold firm at ${last_ai_price} and ask for their specific number.",
            'next_price': last_ai_price,
            'next_delivery': last_ai_delivery
        }

    # Case 2: Valid Offer (Above Reservation)
    if extracted_user_price >= price_reservation:
        # NEW: Detect if buyer is moving toward seller (reciprocal concession logic)
        buyer_concession_multiplier = 1.0
        if previous_user_price is not None and extracted_user_price > previous_user_price:
            # Buyer increased their offer - reward this with larger concession
            buyer_movement = extracted_user_price - previous_user_price
            buyer_movement_pct = buyer_movement / previous_user_price
            # If buyer moved significantly (>3%), increase our concession rate
            if buyer_movement_pct > 0.03:
                buyer_concession_multiplier = 1.5
                logger.info(f"Buyer showed flexibility (+${buyer_movement:.2f}), increasing AI concession rate by 50%")
        
        # Check if offer is close enough to current position (within 2%)
        gap_percentage = abs(extracted_user_price - last_ai_price) / last_ai_price
        if gap_percentage < 0.02:  # Within 2% - essentially an agreement
            # But still negotiate delivery if user asked for something different
            next_delivery = last_ai_delivery
            if extracted_user_delivery and extracted_user_delivery < last_ai_delivery:
                # User wants faster delivery, move toward it
                next_delivery = int((last_ai_delivery + extracted_user_delivery) / 2)
                next_delivery = max(next_delivery, delivery_reservation)
            
            return {
                'guidance': f"Offer ${extracted_user_price} is acceptable and close to your last price. Accept it and close the deal.",
                'next_price': extracted_user_price,
                'next_delivery': next_delivery
            }
        
        # GRADUAL REDUCTION STRATEGY: Drop 5-10% each turn, amplified by buyer movement
        remaining_spread = last_ai_price - price_reservation
        base_reduction_percentage = random.uniform(0.05, 0.10)
        adjusted_reduction_percentage = base_reduction_percentage * buyer_concession_multiplier
        drop_amount = remaining_spread * adjusted_reduction_percentage
        drop_amount = max(drop_amount, 2.0)  # Minimum $2 drop
        
        next_price = round(last_ai_price - drop_amount, 2)
        next_price = max(next_price, price_reservation)  # Safety clamp
        
        # CRITICAL FIX: Also move delivery toward user's request
        next_delivery = last_ai_delivery
        if extracted_user_delivery and extracted_user_delivery < last_ai_delivery:
            # User wants faster delivery, reduce by 5-10% of the delivery gap
            delivery_gap = last_ai_delivery - delivery_reservation
            delivery_reduction = int(delivery_gap * adjusted_reduction_percentage)
            delivery_reduction = max(delivery_reduction, 1)  # At least 1 day reduction
            next_delivery = max(last_ai_delivery - delivery_reduction, delivery_reservation)
        
        logger.info(f"Valid offer: User ${extracted_user_price}/{int(extracted_user_delivery) if extracted_user_delivery else 'any'} days, AI ${last_ai_price}/{int(last_ai_delivery)} days -> ${next_price}/{int(next_delivery)} days")
        
        return {
            'guidance': (f"User offer: ${extracted_user_price} (above floor). "
                        f"I appreciate you moving in the right direction. "
                        f"Let me see what I can do—how about ${next_price} for {int(next_delivery)}-day delivery? "
                        f"That represents a meaningful concession on our end."),
            'next_price': next_price,
            'next_delivery': next_delivery
        }

    # Case 3: Lowball (Below Reservation)
    remaining_spread = last_ai_price - price_reservation
    
    if remaining_spread < 2:
        return {
            'guidance': (f"At absolute floor (${price_reservation}). User offer ${extracted_user_price} is below reservation. "
                        f"Politely but firmly reject. STATE: This is our final price for {int(last_ai_delivery)}-day delivery."),
            'next_price': last_ai_price,
            'next_delivery': last_ai_delivery
        }

    # --- D. TRADE-OFF CHECK ---
    # If user is at lowball price, check if they offered faster delivery
    if extracted_user_delivery and extracted_user_delivery < last_ai_delivery:
        # Trade: Faster delivery for lower price
        trade_price = round(last_ai_price * 0.95, 2)
        trade_delivery = int(last_ai_delivery - 5)
        return {
            'guidance': (f"User offered faster delivery ({int(extracted_user_delivery)} days) at lower price (${extracted_user_price}). "
                        f"TRADE-OFF opportunity: You could accept if price increase offsets delivery speed. "
                        f"Calculate: current gap is ${last_ai_price - extracted_user_price} for {int(last_ai_delivery - extracted_user_delivery)} days saved. "
                        f"Counter: Can you do ${trade_price} for {trade_delivery} day delivery?"),
            'next_price': trade_price,
            'next_delivery': trade_delivery
        }

    # Standard Gradual Drop Logic (5-10% of remaining spread per turn)
    drop_amount = remaining_spread * random.uniform(0.05, 0.10)
    drop_amount = max(drop_amount, 2.0)
    
    next_price = round(last_ai_price - drop_amount, 2)
    next_price = max(next_price, price_reservation) # Safety Clamp
    
    # CRITICAL FIX: Also move delivery toward user's request for lowball offers
    next_delivery = last_ai_delivery
    if extracted_user_delivery and extracted_user_delivery < last_ai_delivery:
        # User wants faster delivery, reduce by percentage
        delivery_gap = last_ai_delivery - delivery_reservation
        delivery_reduction = int(delivery_gap * random.uniform(0.05, 0.10))
        delivery_reduction = max(delivery_reduction, 1)
        next_delivery = max(last_ai_delivery - delivery_reduction, delivery_reservation)
    
    return {
        'guidance': (f"User offer: ${extracted_user_price} (below floor of ${price_reservation}). "
                    f"That's below what we can accept. "
                    f"Counter: ${next_price} for {int(next_delivery)}-day delivery. "
                    f"SAY: 'I appreciate the offer, but I need at least ${next_price}. Can you work with that?'"),
        'next_price': next_price,
        'next_delivery': next_delivery
    }

# --- 3. DEAL DETECTION HELPER ---
def detect_deal_readiness(history, session, current_extracted_terms=None):
    """
    Detect if deal is reached. Requires COMPOUND VERIFICATION:
      1. Semantic intent (agreement_status from extraction)
      2. Price validation (final price >= reservation price)
    
    If the strategy engine already set agreement_reached=True on this turn,
    main.py should use THAT flag instead of calling this function.
    This function exists as a secondary check for edge cases.
    """
    if not current_extracted_terms: 
        return False, None
    
    # CRITICAL: Check for explicit rejection
    rejected_terms = current_extracted_terms.get('rejected_terms', [])
    if rejected_terms:
        logger.warning(f"User explicitly rejected: {rejected_terms}")
        return False, None  # Cannot have deal if user rejected a term
    
    is_agreed = current_extracted_terms.get('agreement_status', False)
    
    if is_agreed:
        final_price = current_extracted_terms.get('current_price')
        final_delivery = current_extracted_terms.get('current_delivery_days')
        
        # If user agrees without proposing new numbers, use AI's last offer from session
        if final_price is None and session and session.get('proposed_offer'):
            final_price = session['proposed_offer'].get('price')
            logger.info(f"Price extracted implicitly from proposed_offer: ${final_price}")
        
        if final_delivery is None and session and session.get('proposed_offer'):
            final_delivery = session['proposed_offer'].get('delivery')
            logger.info(f"Delivery extracted implicitly from proposed_offer: {final_delivery} days")
        
        if final_price and final_delivery:
            # ── COMPOUND VERIFICATION: Price must be >= reservation ──
            deal_params = session.get('deal_params', {})
            price_params = deal_params.get('price', {})
            reservation_price = price_params.get('reservation')
            
            if reservation_price is not None:
                if final_price < float(reservation_price):
                    logger.warning(
                        f"DEAL REJECTED by compound verification: "
                        f"agreed price ${final_price} < reservation ${reservation_price}"
                    )
                    return False, None
            
            logger.info(f"Deal VERIFIED: ${final_price} for {final_delivery} days (passed price validation)")
            return True, {
                "price": final_price,
                "delivery": final_delivery,
                "volume": 1000
            }
        else:
            logger.warning(f"Incomplete agreement - Price: {final_price}, Delivery: {final_delivery}")
            return False, None
    
    return False, None
