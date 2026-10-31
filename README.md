# RAHAT: Disaster Surge Mode for 112 / ERSS

Messages in → verified + merged incidents → fair priority → best team assignment → **operator approval** → field action → re-solve.
SAARTHI-26 prototype. All numbers are illustrative until `evaluation/` is run on the human-labelled set.

## Run (Windows, from the repo root)

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env            # add GEMINI_API_KEY (no key = rule-based extractor)
.\.venv\Scripts\python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000

cd frontend; npm install; npm run dev     # http://localhost:5173
```

| Page | URL |
|---|---|
| Operator dashboard | `http://localhost:5173/#/` |
| Team PWA (open on a phone, same Wi-Fi) | `http://<laptop-ip>:5173/#/team` |
| Citizen simulator | `http://localhost:5173/#/citizen` |
| API docs | `http://localhost:8000/docs` |

Or run `npm run build` once; FastAPI then serves everything at `http://localhost:8000/ui/`.
Tests: `.\.venv\Scripts\python -m pytest -q` (11 known-answer tests, including "optimizer sends Team A to dadi, greedy does not").

## The map
Real Dehradun places, with coordinates from OpenStreetMap (Nominatim) in `data/locations.csv`. Two flood zones: the
**Rispana** side (Deep Nagar, Dalanwala, Rispana Bridge, Nehru Colony, Mothrowala) and the **Bindal** side (Majra,
Kanwali). **Which places are flooded is a demo assumption, not real flood data**, and all people are fictional.
Story: dadi in Deep Nagar, food request at a Dalanwala school, roof family in Majra, Ramesh uncle in Kanwali,
Kamla ji near Rispana Bridge, and the fake from Har Ki Pauri (Haridwar, outside the affected area).

## Demo flow (about 4 min)
1. **Simulate IMD alert**: Surge Mode goes to STANDBY and proactive calls start.
2. **Replay burst**: the flood-night messages arrive. Three dadi messages become one CRIT incident, the copy-paste fake gets red flags + VERIFY, noise is filtered out, and a follow-up SMS goes to the vague sender.
3. Open INC (dadi): priority breakdown, confidence, recommended **Boat Team A, ETA 10 min**. Click **Approve**.
4. **RAHAT vs greedy**: greedy gives dadi a 20 min wait; RAHAT gives 10 min.
5. Citizen sim: send Ramesh uncle's lone SMS. It stays CRIT, a boat is recommended, and the callback runs in parallel.
6. Citizen sim: Kamla ji **no_answer** creates a CRIT incident with no message from her.
7. Team PWA (Boat Team A): **Rescued**. The optimizer re-solves and the next assignment appears.

## Live WhatsApp demo (real phones → RAHAT)
Friends or judges send WhatsApp messages to a Meta **test number**. Each one becomes an incident, and RAHAT replies with
an acknowledgement, follow-up questions and "team dispatched" updates. A location pin from the same sender attaches to their incident.

1. developers.facebook.com → My Apps → Create app → type **Business** → add the **WhatsApp** product.
2. WhatsApp → **API Setup**: copy the temporary **access token** and the **Phone number ID** into `.env`
   (`WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`). Under "To", add and verify every phone that will send (max 5 on a test number).
   The temporary token expires in about 24 h; create a System User token for event day.
3. App settings → Basic: copy the **App secret** into `WHATSAPP_APP_SECRET`. Pick any `WHATSAPP_VERIFY_TOKEN`.
4. Public HTTPS URL for the laptop: `winget install --id Cloudflare.cloudflared` then
   `cloudflared tunnel --url http://localhost:8000` (prints `https://xxxx.trycloudflare.com`; it changes on every restart), or ngrok with a free static domain.
5. WhatsApp → **Configuration** → Webhook: callback URL `https://<tunnel>/webhooks/whatsapp`, the same verify token →
   Verify and save → under Webhook fields, **subscribe to `messages`**.
6. Restart the backend and check `http://localhost:8000/webhooks/whatsapp/status` → `"configured": true`.

You **cannot** use your personal number: a number registered on the Cloud API stops working in the normal WhatsApp app.
Venue Wi-Fi can fail, so keep the citizen simulator and a recorded video as backup. The raw WhatsApp number is held in
memory only, to send replies; the database stores only the hashed id.

## Who owns what (one folder each; AI writes it, YOU must explain it)
| Folder | Owner | What |
|---|---|---|
| `shared/` | everyone (change only after telling the team) | `schemas.py` field names, `config.py` every tunable number |
| `agents/` | Mayank | `rules.py` keyword extractor (baseline + fallback), `extractor.py` + `llm.py` Gemini extraction, `verifier.py` merge/confidence/red flags/action matrix, `priority.py`, `dispatcher.py`, `embeddings.py`, `speech.py`, `pipeline.py` (LangGraph) |
| `backend/` | Avnish | `main.py` API + WebSocket, `service.py` core loop + audit, `optimizer.py` OR-Tools min-cost flow + greedy baseline, `db.py` SQLite |
| `frontend/` | Manshi | `Dashboard.jsx`, `MapView.jsx`, `IncidentPanel.jsx`, `TeamApp.jsx` (PWA, offline queue), `Citizen.jsx` |
| `data/`, `simulation/`, `rag/`, `evaluation/` | Hardik | scenario + teams + alert + locations, Surge Mode, SOP RAG, evaluation |

## Design decisions to be able to defend
- **Priority ≠ confidence.** Priority = how serious if true; confidence = how likely true. A lone message starts at a neutral 0.60, not low. Confidence drops only on a *named* red flag (out of area, copy-paste burst, low-reputation sender, serial reporter).
- **Three confidence tiers.** ≥0.50 → dispatch (operator approves); 0.30–0.50 + high priority → standby team *and* callback; <0.30 (two or more red flags) → verify first, no team recommended. The last tier is our addition to the PRD matrix: it is why the demo fake gets no boat. Team: confirm you agree.
- **Optimizer cost is priority-weighted by default** (`COST_MODE=weighted`): `travel × (1 + P/10) − λP + penalty`. With the PRD's `travel − λP`, the λP terms cancel whenever every incident gets a team, so it only minimises total travel and can still make a critical case wait. Set `RAHAT_COST_MODE=prd` for the exact PRD formula.
- **Team A/B numbers are 5/10/6/20 min, not 5/10/6/30.** With straight-line travel, 30 min is geometrically impossible together with the other three numbers. Use 20 in the pitch.
- **Merging** = incremental DBSCAN with min_samples=1 on `0.6·text + 0.25·place + 0.15·time (+ need-mismatch)`. A message with no location joins only its own sender's follow-up.
- **The LLM never allocates teams.** OR-Tools gives the same answer every time and every cost is shown.

## Before the pitch (still open)
- **Gemini model**: default `gemini-3.1-flash-lite` (6/6 demo messages correct, 1–4 s each, tested 31 Oct). `gemini-2.5-flash` is closed to new keys. Still test 2–3 models on the 20 Hinglish messages (brief decision #1); free-tier rate limits may slow a 200-message burst, and the system then falls back to rules for the failed calls.
- **SOP documents**: put OFFICIAL NDMA text in `data/sop/`. It is empty now, so every checklist says "Not in SOP, escalate to officer". We never generate guidance from memory.
- **Speech-to-text**: `RAHAT_STT=faster_whisper` or Bhashini; test on real Hinglish voice notes. Until then voice notes go to human review (never dropped).
- **200 labelled messages**: human-written (`data/labelled_messages_TEMPLATE.csv`). Do not use AI-generated messages for evaluation.
- Not built: role-based login, auto-delete after closure, ledger (stretch). Say "future scope".
