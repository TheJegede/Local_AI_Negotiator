# AI Negotiator

A B2B negotiation training simulator for students. Students negotiate chip prices and delivery terms against an AI seller ("Alex" from ChipSource Inc.). After a deal is reached, students receive a graded evaluation report with detailed feedback on their negotiation technique.

---

## How It Works

1. Student enters a student ID (or uses random parameters) → unique deal parameters generated
2. Chat with Alex the AI seller — negotiate price and delivery for 1,000 CS-1000 chips
3. Reach a deal → evaluation report generated with score (0–100) and letter grade
4. Review scores across 7 categories: deal quality, trade-off strategy, anchoring, persuasion, tone, efficiency, and concession pattern

---

## Stack

| Layer | Tech |
|-------|------|
| Backend | Python 3.10+, FastAPI, SQLite |
| AI | Groq API — `llama-3.3-70b-versatile` |
| Frontend | Vanilla HTML/CSS/JS (no framework, no build step) |

---

## Quick Start

### 1. Backend

```bash
cd backend
python -m venv venv
venv\Scripts\Activate.ps1      # Windows PowerShell
# source venv/bin/activate     # macOS/Linux
pip install -r requirements.txt
```

Create `backend/.env`:
```
GROQ_API_KEY=gsk_...
```

Start the server:
```bash
python -m uvicorn main:app --reload
# API available at http://localhost:8000
```

### 2. Frontend

```bash
cd frontend
python -m http.server 8080
# Open http://localhost:8080
```

- Chat UI: `http://localhost:8080/index.html`
- Session viewer (admin): `http://localhost:8080/sessions.html`

---

## Environment Variables

| Variable | File | Description |
|----------|------|-------------|
| `GROQ_API_KEY` | `backend/.env` | Required. Groq API key (`gsk_...`) |
| `DB_PATH` | `backend/.env` | Optional. SQLite path override (default: `sessions.db`) |

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/sessions/new` | Create session. Body: `{"student_id": "optional"}` |
| POST | `/api/chat` | Send message. Body: `{"session_id": "...", "user_input": "..."}` |
| POST | `/api/evaluate` | Evaluate completed deal. Body: `{"session_id": "...", "final_terms": {...}}` |
| GET | `/api/sessions/{id}` | Retrieve session with full conversation history |
| GET | `/api/sessions` | List last 15 sessions (used by chat sidebar) |
| GET | `/api/admin/sessions` | List all sessions with deal outcomes (used by session viewer) |

---

## Architecture

```
backend/
  main.py          # FastAPI routes + SQLite session CRUD
  logic.py         # Negotiation engine: deal parameters, turn strategy, guardrails
  ai_service.py    # Groq API calls: extraction, response generation, evaluation
  evaluator.py     # Pure-Python NLP metrics (no external NLP libraries)
  prompts.py       # MASTER_PROMPT_TEMPLATE — Alex the seller persona

frontend/
  index.html       # Chat UI (two-panel: sidebar + chat)
  sessions.html    # Admin session viewer (read-only transcript browser)
  script.js        # Session state, message flow, deal modal
  api.js           # HTTP client with environment-aware URL resolution
  config.js        # Set API_URL here for production deployments

scripts/
  migrate_dynamo.py  # One-time DynamoDB → SQLite migration utility
```

### Negotiation Engine (single turn)

```
User message
  → extract_negotiation_state()    # Llama extracts price/delivery/intent; regex fallback
  → generate_turn_strategy()       # Guardrails → concession math → next offer
  → get_ai_response()              # Llama generates Alex's reply using exact offer terms
  → detect_deal_readiness()        # Compound check: semantic intent + price ≥ reservation
  → write_session()                # Persist to SQLite
```

### Deal Parameters

Generated per student ID (hash-seeded, reproducible):
- Price: opening $300–$500, target −12%, reservation (walk-away) −20%
- Delivery: opening 25–45 days, target −15%, reservation −30%

Secret from the student — never revealed in responses.

### Guardrails

- **Extreme lowball**: offer ≥50% below opening → hard reject, no concession
- **Repetition stalemate**: same price repeated ≥3 times → concessions frozen
- **Reciprocal concession**: buyer moves >3% toward seller → AI concession rate +50%

### Evaluation Scoring

| Category | Weight |
|----------|--------|
| Deal quality (final price vs. AI's range) | 33% |
| Trade-off strategy (linking price ↔ delivery) | 20% |
| Anchoring (opening offer position) | 12% |
| Persuasion & reasoning | 12% |
| Professional tone | 8% |
| Negotiation efficiency (turns to close) | 8% |
| Concession pattern | 7% |

Two-layer pipeline: deterministic NLP metrics (Layer 1) → Groq generates qualitative report grounded in those metrics (Layer 2).

---

## Session Viewer

Browse all stored sessions at `localhost:8080/sessions.html`:
- Green dot = deal closed, gray = no deal
- Click any session to read full transcript
- Shows deal parameters, final terms, and turn-by-turn conversation

---

## Production Deployment

Set `API_URL` in `frontend/config.js` to your backend URL:

```js
window.APP_CONFIG = {
    API_URL: 'https://your-backend-url.com'
};
```

Frontend: deploy `frontend/` to any static host (Netlify, Vercel, etc.).  
Backend: deploy via Docker/Fly.io/Render — see `HOSTING.md` for recommended free-tier setup.

---

## DynamoDB Migration

If you have existing sessions in DynamoDB:

```bash
# Requires AWS credentials configured (aws configure)
cd backend
python ../scripts/migrate_dynamo.py
# Imports all sessions from NegotiationSessions table (us-east-2) → sessions.db
```
