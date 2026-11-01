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
Tests: `.\.venv\Scripts\python -m pytest -q` (49 known-answer tests, including "optimizer sends Team A to dadi, greedy does not" and "two lanes of one colony stay two cases").

## The map
Real Dehradun places, with coordinates from OpenStreetMap (Nominatim) in `data/locations.csv`. Two flood zones: the
**Rispana** side (Deep Nagar, Dalanwala, Rispana Bridge, Nehru Colony, Mothrowala) and the **Bindal** side (Majra,
Kanwali). **Which places are flooded is a demo assumption, not real flood data**, and all people are fictional.
Story: dadi in Deep Nagar, food request at a Dalanwala school, roof family in Majra, Ramesh uncle in Kanwali,
Kamla ji near Rispana Bridge, and the fake from Har Ki Pauri (Haridwar, outside the affected area).

## Demo flow (about 4 min)
1. **Simulate IMD alert**: Surge Mode goes to STANDBY and (simulated) proactive callbacks are queued. Real phone/IVR calls are future scope.
2. **Replay burst**: the flood-night messages arrive. Three dadi messages become one CRIT incident, the copy-paste fake gets red flags + VERIFY, noise is filtered out, and a follow-up SMS goes to the vague sender.
3. Open INC (dadi): priority breakdown, confidence, recommended **Boat Team A, ETA 10 min**. Click **Approve**.
4. **RAHAT vs greedy**: greedy gives dadi a 20 min wait; RAHAT gives 10 min.
5. Citizen sim: send Ramesh uncle's lone SMS. It stays CRIT, a boat is recommended, and a (simulated) callback runs in parallel.
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
  **Same place is not enough**: the texts must also be similar (≥ 0.40, place excluded so it is not counted twice), and a named rule keeps two reports apart when they mention different lanes (gali 2 vs gali 4), clearly different written head-counts (200 students vs 3 log), or when one is a real report and the other a suspected copy-paste burst. Every "kept apart" reason is written to the audit log.
  **Status updates** ("aag bujh gayi, sab bahar aa gaye") never create a case and never change someone else's case; they appear as a "please check" note on the open case at that landmark.
- **The LLM never allocates teams.** OR-Tools gives the same answer every time and every cost is shown.

## Before the pitch (still open)
- **Gemini model**: set `RAHAT_LLM_MODEL=gemini-3.5-flash-lite` in `.env` (clean Hinglish voice transcripts in 2–4 s on 1 Nov). The free tier allows **500 requests per day per project per model** (`gemini-3.1-flash-lite` hit it on 1 Nov; a new key in the same project does not help). Each message uses 1–2 calls: do not run big tests on demo day. `gemini-2.5-flash` is closed to new keys. Still test 2–3 models on the 20 Hinglish messages (brief decision #1); free-tier rate limits may slow a 200-message burst, and the system then falls back to rules for the failed calls.
- **SOP documents**: two official NDMA guidelines are used (listed in `data/sop/sources.json`): *Management of Floods* (2008, Chapter 8 Flood Response) and *Management of Urban Flooding* (2010, Chapter 7). The PDFs are not in git; download them once with `.\.venv\Scripts\python -m rag.fetch_sop`. Every checklist step is copied from a response-chapter passage and shows section + PDF page; hazards the documents do not cover (fire, landslide, collapse) return "Not in SOP, escalate to officer". These are policy-level guidelines: they cover medical response and relief camps well, but contain few field-level boat-rescue steps. Add official SDRF/NDRF field SOPs to improve that. We never generate guidance from memory.
- **Speech-to-text**: voice notes (WhatsApp OGG, browser WebM via ffmpeg, WAV/MP3) are transcribed by Gemini (`RAHAT_STT=gemini`, default when the Gemini key is set; `faster_whisper` and `none` also available), then go through the same extractor. If Gemini fails (quota, outage) and `faster-whisper` is installed, local Whisper is tried: it writes the Hindi words plus an English translation in brackets (Hindi alone garbles spellings, English alone drops place names), ~7 s per note on a CPU, much slower when several arrive together. The operator can play the original in the case pop-up. Tested only with a synthetic computer voice so far: test on real Hinglish/Garhwali voice notes before quoting accuracy. If transcription fails the note goes to human review (never dropped).
- **200 labelled messages**: human-written (`data/labelled_messages_TEMPLATE.csv`). Do not use AI-generated messages for evaluation.

## Evaluation
`evaluation/evaluate.py` replays a labelled CSV through the real pipeline (throw-away DB, clock set to each message's time) and scores it against the labels:

| Part | What is measured |
|---|---|
| Extraction | place correct / **wrong place** / missed, need found, people count, vulnerable groups, help vs noise |
| Merging | message pairs about the same real case that end up in one incident (pairwise P/R/F1) |
| Urgency | band per real case, critical recall, **under-triage** (critical/high shown as medium/low) |
| Fake detection | precision, recall, and **real cases wrongly flagged** |
| Team plan | RAHAT vs greedy on the final cases (longest critical wait, critical reached within 15 min) |

```powershell
# 1. Write the messages in data/labelled_messages_locked.csv (same columns as the template; gitignored).
#    split=tune for messages you may look at while improving rules/prompts, split=test for the final number.
# 2. Baseline (rules only, no API calls):
.\.venv\Scripts\python -m evaluation.evaluate --split test
# 3. Rules vs LLM side by side (uses the Gemini key, ~1 call per message):
.\.venv\Scripts\python -m evaluation.evaluate --split test --extractor both
```
Each run prints a report with `(hits/n)` next to every number and saves JSON in `evaluation/results/`.
### Stress test: the team's 200 AI-written messages (1 Nov)
`data/rahat200_labelled_AI.csv` (converted from the team's Excel file with `python -m evaluation.convert_xlsx <xlsx>`; the label mapping is written at the top of that script). 31 places now in `data/locations.csv`; Kesarwala, Bhagat Singh Colony, Kathbangla, Chukkuwala and Mussoorie Road are not on OpenStreetMap and use the dataset's approximate coordinates. Fixes were made on the 49 `tune` messages only; `test` was run once afterwards. **Rules extractor only** (Gemini not yet run on it). AI-written messages: a stress test, not accuracy.

| test split (151 messages, 59 real cases) | rules |
|---|---|
| place correct / wrong place | 94% (111/118) / 3 |
| need found · people count exact | 62.5% (65/104) · 45.5% (46/101) |
| help vs noise | F1 96% |
| merging duplicates | P 61% · R 61% · F1 61% |
| critical shown as critical | 77% (17/22) |
| **under-triage** (critical/high shown as medium/low) | **30% (13/43)** |
| fakes caught · real cases wrongly flagged | 1 of 4 fake cases · 0/55 |
| longest critical wait | RAHAT 15.8 min vs greedy 60.5 min |

Main misses: wording the keyword lists have never seen ("patthar pakad ke latka hai", "malbe me fasa", "deewaron me daraar", "baccha nahi mil raha"), vague messages, and hazards RAHAT has no category for (live wire, cracked bridge). Next: run `--extractor both` on `test` (~150–300 Gemini calls), and have each teammate rewrite 40 test messages in their own words.

Rules: human-written messages only; quote only the `test` split; with ~200 messages one case moves a percentage by about 0.5 points, so give n with every number. The template rows are examples written alongside the demo, so a 100% there means nothing.
- Not built: role-based login, auto-delete after closure, ledger (stretch). Say "future scope".
