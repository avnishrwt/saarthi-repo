"""
Convert the team's labelled Excel test set ("RAHAT 200 messages testing.xlsx", sheet "Messages")
into the two formats RAHAT uses:

  data/rahat200_labelled_AI.csv               -> python -m evaluation.evaluate --data <this> --split tune|test
  data/demo_scenario_rahat200_synthetic.json  -> replay on the dashboard (Scenario menu)

    .venv\\Scripts\\python -m evaluation.convert_xlsx "C:\\path\\RAHAT 200 messages testing.xlsx"

Re-run it after teammates rewrite messages in their own words (only raw_text changes).

Label mapping (the Excel labels -> RAHAT's categories). Written down so nobody has to guess:
  location   place name -> place_id in data/locations.csv ("unknown" / "n/a" -> empty)
  need       rescue, evacuation, medical, shelter -> same
             food_water     -> food;water
             missing_person -> welfare_check   (RAHAT's "check on someone we cannot reach")
             hazard, other, unknown -> kept as is: RAHAT has no such category, so these rows are
                               left out of "need found" (the evaluator says how many)
             info, volunteer, status_update -> information (not a help request)
             none, n/a      -> empty
  vulnerable infant -> child; none / n/a -> empty
  people     number, else empty ("unknown", "n/a")
  urgency    critical/high/medium/low; n/a (fakes) -> none
  is_fake    yes -> 1
  channel    voice_note -> voice (the Excel holds the text of the note; no audio)
  source     ai_generated (the Excel README says the messages were drafted by an AI)
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_CSV = ROOT / "data" / "rahat200_labelled_AI.csv"
OUT_JSON = ROOT / "data" / "demo_scenario_rahat200_synthetic.json"
SPEEDUP = 15          # dashboard replay: 30 minutes of messages in 2 minutes

PLACE = {
    "Sahastradhara": "SAHASTRADHARA", "Maldevta (Baldi nadi pul)": "MALDEVTA", "Kesarwala": "KESARWALA",
    "Tapkeshwar Mandir (Tamsa nadi)": "TAPKESHWAR", "Bhagat Singh Colony (Rispana kinara)": "BHAGAT_SINGH_COLONY",
    "Deepnagar (Rispana)": "DEEP_NAGAR", "Rispana Pul (Haridwar Road)": "RISPANA_BRIDGE", "Bindal Pul": "BINDAL_PUL",
    "Kanwali Road": "KANWALI", "Prem Nagar (Tons nadi side)": "PREM_NAGAR", "Clement Town": "CLEMENT_TOWN",
    "Mothrowala": "MOTHROWALA", "Raipur (Song nadi side)": "RAIPUR", "Kathbangla": "KATHBANGLA",
    "Chukkuwala": "CHUKKUWALA", "Jakhan (Rajpur Road)": "JAKHAN", "Majra": "MAJRA", "Ghantaghar": "CLOCK_TOWER",
    "Doon Hospital": "DOON_HOSPITAL", "Parade Ground relief camp": "PARADE_GROUND",
    "Rishikesh": "RISHIKESH", "Haridwar": "HAR_KI_PAURI", "Vikasnagar": "VIKASNAGAR", "Mussoorie road": "MUSSOORIE_ROAD",
}
NEED = {"rescue": "rescue", "evacuation": "evacuation", "medical": "medical", "shelter": "shelter",
        "food_water": "food;water", "missing_person": "welfare_check",
        "hazard": "hazard", "other": "other", "unknown": "unknown",
        "info": "information", "volunteer": "information", "status_update": "information",
        "none": "", "n/a": ""}
CHANNEL = {"voice_note": "voice"}
COLS = ["msg_id", "split", "channel", "sender_id", "timestamp", "raw_text", "true_incident_id", "location", "need",
        "people_count", "vulnerable", "urgency", "is_fake", "source",
        "label_need_original", "msg_type", "fake_type", "needs_followup", "notes"]


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def convert(xlsx: Path) -> tuple[int, int]:
    import openpyxl
    ws = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)["Messages"]
    rows = [r for r in ws.iter_rows(values_only=True) if r and r[0]]
    head, rows = list(rows[0]), [dict(zip(rows[0], r)) for r in rows[1:]]
    out, problems = [], []
    for r in rows:
        loc = _s(r["location"])
        if loc.lower() in ("unknown", "n/a", ""):
            place = ""
        elif loc in PLACE:
            place = PLACE[loc]
        else:
            problems.append(f"{r['msg_id']}: unknown place label {loc!r}")
            place = ""
        need_l = _s(r["need"]).lower()
        if need_l not in NEED:
            problems.append(f"{r['msg_id']}: unknown need label {need_l!r}")
        ts = r["timestamp"]
        ts = ts.isoformat() if isinstance(ts, datetime) else _s(ts)
        vul = _s(r["vulnerable"]).lower()
        out.append({
            "msg_id": _s(r["msg_id"]), "split": _s(r["split"]).lower(),
            "channel": CHANNEL.get(_s(r["channel"]).lower(), _s(r["channel"]).lower()),
            "sender_id": _s(r["sender_id"]), "timestamp": ts, "raw_text": _s(r["raw_text"]),
            "true_incident_id": "" if _s(r["true_incident_id"]).upper() == "NONE" else _s(r["true_incident_id"]),
            "location": place, "need": NEED.get(need_l, need_l),
            "people_count": _s(r["people_count"]) if _s(r["people_count"]).isdigit() else "",
            "vulnerable": "" if vul in ("none", "n/a") else ("child" if vul == "infant" else vul),
            "urgency": "none" if _s(r["urgency"]).lower() in ("n/a", "") else _s(r["urgency"]).lower(),
            "is_fake": "1" if _s(r["is_fake"]).lower() in ("yes", "1", "true") else "0",
            "source": "ai_generated",
            "label_need_original": need_l, "msg_type": _s(r["msg_type"]), "fake_type": _s(r["fake_type"]),
            "needs_followup": _s(r["needs_followup"]), "notes": _s(r["notes"]),
        })
    if problems:
        raise SystemExit("cannot convert:\n  " + "\n  ".join(problems))
    out.sort(key=lambda x: x["timestamp"])
    with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(out)

    t0 = datetime.fromisoformat(out[0]["timestamp"])
    scen = {
        "_note": ("AI-GENERATED synthetic messages (team's 200-message labelled test set, converted by "
                  "evaluation/convert_xlsx.py; msg_id in 'ref'). Fictional scenario: monsoon night 14 Aug 2026, "
                  "02:00-02:30. Places are real Dehradun areas; Kesarwala, Bhagat Singh Colony, Kathbangla, "
                  "Chukkuwala and Mussoorie Road use the dataset's APPROXIMATE coordinates (not found on "
                  f"OpenStreetMap). Timings compressed {SPEEDUP}x. NOT real-world accuracy."),
        "name": "rahat200_synthetic",
        "messages": [{"t": round((datetime.fromisoformat(m["timestamp"]) - t0).total_seconds() / SPEEDUP, 1),
                      "channel": m["channel"], "sender": m["sender_id"], "text": m["raw_text"], "ref": m["msg_id"]}
                     for m in out],
    }
    OUT_JSON.write_text(json.dumps(scen, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(out), sum(m["split"] == "test" for m in out)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    n, n_test = convert(Path(sys.argv[1]))
    print(f"{n} messages ({n_test} test, {n - n_test} tune)\n -> {OUT_CSV.relative_to(ROOT)}\n -> {OUT_JSON.relative_to(ROOT)}")
