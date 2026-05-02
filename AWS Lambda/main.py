import json
import re
import logging
import random
import sqlite3
import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, List
import uuid
from datetime import datetime

from ai_service import get_bedrock_response, create_negotiation_prompt, get_evaluation, extract_negotiation_state
from logic import generate_deal_parameters, format_deal_parameters, detect_deal_readiness

DB_PATH = os.environ.get("DB_PATH", "sessions.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            created_at TEXT
        )
    """)
    return conn

def read_session(session_id: str):
    with get_db() as conn:
        row = conn.execute("SELECT data FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    return json.loads(row[0]) if row else None

def write_session(session_id: str, data: dict):
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sessions (session_id, data, created_at) VALUES (?, ?, ?)",
            (session_id, json.dumps(data), data.get("created_at", ""))
        )

def list_sessions_db():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT session_id, created_at FROM sessions ORDER BY created_at DESC LIMIT 15"
        ).fetchall()
    return [{"session_id": row[0], "created_at": row[1]} for row in rows]

app = FastAPI(title="AI Negotiator", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8080",
        "http://localhost:8000",
        "http://localhost:3000",
        "http://127.0.0.1:8080",
        "http://127.0.0.1:8000",
        "https://main.dkqvi8x3kw5qg.amplifyapp.com",
        "null",  # Allows file:// protocol (opening index.html directly)
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

class NewSessionRequest(BaseModel):
    student_id: Optional[str] = None

class ChatRequest(BaseModel):
    session_id: str
    user_input: str

class EvaluateRequest(BaseModel):
    session_id: str
    final_terms: Dict 

@app.post("/api/sessions/new")
def create_session(request: NewSessionRequest):
    session_id = str(uuid.uuid4())
    deal_params = generate_deal_parameters(request.student_id)
    deal_params_str = format_deal_parameters(deal_params)
    
    greeting = (
        f"Hello! I'm Alex from ChipSource Inc. We are looking to sell our CS-1000 chips. "
        f"Our standard opening is ${deal_params['price']['opening']} per unit "
        f"with {deal_params['delivery']['opening']}-day delivery. What works for you?"
    )

    opening_price = float(deal_params['price']['opening'])
    opening_delivery = int(deal_params['delivery']['opening'])

    item = {
        'session_id': session_id,
        'deal_params': deal_params,
        'deal_params_str': deal_params_str,
        'conversation': [{"role": "assistant", "content": greeting}],
        'last_ai_price': opening_price,
        'last_ai_delivery': opening_delivery,
        'agreed_terms': {'price': None, 'delivery': None},
        'locked_parameters': {
            'price_locked': False,
            'delivery_locked': False,
            'locked_price_value': None,
            'locked_delivery_value': None
        },
        'proposed_offer': {'price': opening_price, 'delivery': opening_delivery, 'turn': 0},
        'user_offer_history': [],
        'consecutive_identical_count': 0,
        'last_user_price': None,
        'created_at': datetime.utcnow().isoformat()
    }
    write_session(session_id, item)

    return {
        "session_id": session_id,
        "deal_params": deal_params,
        "greeting": greeting
    }

@app.post("/api/chat")
def chat(request: ChatRequest):
    session = read_session(request.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Add User Input
    session["conversation"].append({"role": "user", "content": request.user_input})
    
    # Retrieve deal parameters
    deal_params_dict = session.get("deal_params", {})
    
    # Retrieve last AI price and delivery from session state (not regex extraction)
    # STATE MANAGEMENT: Prices are stored explicitly in session after each AI response
    last_ai_price = session.get('last_ai_price')
    last_ai_delivery = session.get('last_ai_delivery')
    
    # NEW: Track buyer's previous offer for movement detection
    previous_user_price = session.get('last_user_price')
    
    if last_ai_price is not None:
        last_ai_price = float(last_ai_price)
    if last_ai_delivery is not None:
        last_ai_delivery = int(last_ai_delivery)
    if previous_user_price is not None:
        previous_user_price = float(previous_user_price)

    if last_ai_price is None or last_ai_delivery is None:
        logging.warning(f"Missing price/delivery in session {request.session_id} - using opening fallback")
        last_ai_price = float(deal_params_dict['price']['opening'])
        last_ai_delivery = int(deal_params_dict['delivery']['opening'])
    
    # Extract current user offer BEFORE generating strategy
    random.seed()  # RESET random seed to avoid deterministic behavior
    current_state = extract_negotiation_state(request.user_input, session["conversation"])
    current_user_price = current_state.get('current_price')
    
    # NEW: Detect parameter acceptance and lock them in
    locked_params = session.get('locked_parameters', {
        'price_locked': False,
        'delivery_locked': False,
        'locked_price_value': None,
        'locked_delivery_value': None
    })
    
    # Check if user explicitly accepted price or delivery
    if current_state.get('price_accepted') and not locked_params['price_locked']:
        locked_params['price_locked'] = True
        locked_params['locked_price_value'] = float(last_ai_price)
        logging.info(f"PRICE LOCKED IN at ${last_ai_price}")

    if current_state.get('delivery_accepted') and not locked_params['delivery_locked']:
        locked_params['delivery_locked'] = True
        locked_params['locked_delivery_value'] = int(last_ai_delivery)
        logging.info(f"DELIVERY LOCKED IN at {last_ai_delivery} days")
    
    session['locked_parameters'] = locked_params
    
    locked_params_for_strategy = {
        'price_locked': locked_params['price_locked'],
        'delivery_locked': locked_params['delivery_locked'],
        'locked_price_value': float(locked_params['locked_price_value']) if locked_params['locked_price_value'] is not None else None,
        'locked_delivery_value': int(locked_params['locked_delivery_value']) if locked_params['locked_delivery_value'] is not None else None
    }
    
    # ── GUARDRAIL STATE: Compute consecutive identical count ──
    # Pull existing guardrail state from session
    user_offer_history = session.get('user_offer_history', [])
    prev_consecutive_count = int(session.get('consecutive_identical_count', 0))
    
    # If user offered a price, check if it matches their previous offer
    if current_user_price is not None:
        float_history = [float(p) for p in user_offer_history if p is not None]

        if float_history and abs(current_user_price - float_history[-1]) < 0.01:
            consecutive_identical_count = prev_consecutive_count + 1
        else:
            consecutive_identical_count = 1

        user_offer_history.append(float(current_user_price))
        session['user_offer_history'] = user_offer_history
        session['consecutive_identical_count'] = consecutive_identical_count
    else:
        # No price offered → don't change the counter
        consecutive_identical_count = prev_consecutive_count
    
    # Build guardrail context for the strategy engine
    guardrail_context = {
        'consecutive_identical_count': consecutive_identical_count,
        'user_offer_history': [float(p) for p in user_offer_history if p is not None]
    }
    
    logging.info(f"Guardrail state: consecutive={consecutive_identical_count}, "
                 f"history_len={len(user_offer_history)}")
    
    # Generate Prompt (guardrails + compound verification run inside)
    # Returns 4 values: prompt, price, delivery, agreement_reached
    prompt, next_price, next_delivery, agreement_reached = create_negotiation_prompt(
        request.user_input,
        session["conversation"],
        deal_params_dict,
        last_ai_price=last_ai_price,
        last_ai_delivery=last_ai_delivery,
        previous_user_price=previous_user_price,
        locked_parameters=locked_params_for_strategy,
        guardrail_context=guardrail_context
    )
    
    ai_response = get_bedrock_response(prompt)
    session["conversation"].append({"role": "assistant", "content": ai_response})
    
    session['current_offer'] = {'price': float(next_price), 'delivery': int(next_delivery)}
    session['last_ai_price'] = float(next_price)
    session['last_ai_delivery'] = int(next_delivery)

    if current_user_price is not None:
        session['last_user_price'] = float(current_user_price)
        logging.info(f"Stored user price: ${current_user_price}")

    session['proposed_offer'] = {
        'price': float(next_price),
        'delivery': int(next_delivery),
        'turn': len(session["conversation"]) // 2
    }
    
    # ── DEAL DETECTION: Strategy engine is the AUTHORITATIVE source ──
    # The strategy engine's `agreement_reached` flag has ALREADY done
    # compound verification (semantic intent + price validation).
    # We only fall back to detect_deal_readiness if the strategy engine
    # didn't explicitly set the flag (i.e., it was None).
    
    if agreement_reached is True:
        # Strategy engine confirmed: valid deal with price >= reservation
        is_deal_ready = True
        proposed_terms = {
            "price": float(next_price),
            "delivery": int(next_delivery),
            "volume": 1000
        }
        logging.info(f"DEAL CONFIRMED by strategy engine: ${next_price}, {next_delivery} days")
    elif agreement_reached is False:
        # Strategy engine explicitly rejected: price below reservation
        is_deal_ready = False
        proposed_terms = None
        logging.info(f"DEAL REJECTED by strategy engine: price validation failed")
    else:
        # Strategy engine didn't set the flag (None) — use secondary check
        is_deal_ready, proposed_terms = detect_deal_readiness(
            session["conversation"], 
            session,
            current_extracted_terms=current_state
        )
    
    if is_deal_ready and proposed_terms:
        session['agreed_terms'] = {
            'price': float(proposed_terms.get('price')),
            'delivery': int(proposed_terms.get('delivery')),
            'turn': len(session["conversation"]) // 2
        }

    logging.info(f"Stored offer: ${next_price}, {next_delivery} days")
    write_session(request.session_id, session)
    
    return {
        "ai_response": ai_response,
        "status": "success",
        "deal_ready": is_deal_ready,       
        "proposed_terms": proposed_terms   
    }

# (Evaluate endpoint remains the same as your original)
@app.post("/api/evaluate")
def evaluate_session(request: EvaluateRequest):
    session = read_session(request.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Pass evaluation logic here (using ai_service.get_evaluation)
    # Assumes you kept the function in ai_service.py
    report = get_evaluation(
        session["conversation"],
        session["deal_params"],
        request.final_terms
    )
    return {"evaluation_report": report, "status": "completed"}

# ─── SESSION RETRIEVAL (for "Recent Chats" sidebar) ───

@app.get("/api/sessions/{session_id}")
def get_session(session_id: str):
    session = read_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": session['session_id'],
        "deal_params": session.get('deal_params', {}),
        "history": session.get('conversation', []),
        "created_at": session.get('created_at'),
        "agreed_terms": session.get('agreed_terms', {})
    }

@app.get("/api/sessions")
def list_sessions():
    return {"sessions": list_sessions_db()}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
