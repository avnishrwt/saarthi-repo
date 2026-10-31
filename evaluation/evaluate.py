"""
RAHAT evaluation on a human-labelled message set.

    .venv\\Scripts\\python -m evaluation.evaluate --data data/labelled_messages_locked.csv --split test
    .venv\\Scripts\\python -m evaluation.evaluate --extractor both      # rules baseline vs LLM, side by side

What it does
  Replays every labelled message through the REAL pipeline (same code as the live demo) on a
  throw-away database, in timestamp order, with the clock set to each message's timestamp.
  Then it compares what RAHAT produced with the human labels:

  1. Extraction (per message):  place, need, people count, vulnerable groups, help-vs-noise
  2. Merging (per message pair): did messages about the same real case end up in one incident?
  3. Urgency (per real case):    predicted band vs labelled urgency, incl. UNDER-TRIAGE
                                 (a critical/high case shown as medium/low: the dangerous error)
  4. Fake detection (per case):  precision, recall, and how many REAL cases were wrongly flagged
  5. Team plan:                  RAHAT vs greedy ("nearest free team, first come") on the final cases

Label columns (one row per message; see data/labelled_messages_TEMPLATE.csv)
  msg_id            unique id
  split             tune | test   (tune: allowed while improving rules/prompts; test: only for the final number)
  channel           sms | whatsapp | helpline | ...
  sender_id         same value = same phone (fake anonymous ids are fine)
  timestamp         ISO time, e.g. 2026-11-10T02:04:11
  raw_text          the message exactly as written
  true_incident_id  same value for every message about the same real case; empty for noise / info
  location          place_id from data/locations.csv, or empty if the message gives no usable place
  need              rescue | medical | food | water | medicine | shelter | evacuation | information,
                    several allowed with ';'  (empty or "none" = not a help request)
  people_count      number if the message states it, else empty
  vulnerable        elderly;child;disabled;pregnant;alone  (empty or "none")
  urgency           critical | high | medium | low | none   (how serious the REAL case is;
                    fake cases are left out of the urgency score: RAHAT keeps urgency and trust separate)
  source            optional: human | ai_generated  (ai_generated rows trigger a warning in the report)
  is_fake           1 if the report is fake/spam, else 0

Honesty rules (also printed with every report)
  - Use HUMAN-written messages. AI-generated test messages measure the AI against itself.
  - Do not look at or tune on the "test" split. Report test-split numbers only.
  - A small set gives wide error bars: the report prints n next to every number.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Optional

from shared import config

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCKED = ROOT / "data" / "labelled_messages_locked.csv"
TEMPLATE = ROOT / "data" / "labelled_messages_TEMPLATE.csv"
BAND_TO_URGENCY = {"CRIT": "critical", "HIGH": "high", "MED": "medium", "LOW": "low"}
URGENCY_ORDER = ["critical", "high", "medium", "low"]
NONE_WORDS = {"", "none", "null", "-", "na", "n/a"}


# ------------------------------------------------------------------ loading
def _split_list(v: str) -> set[str]:
    return {x.strip().lower() for x in (v or "").replace(",", ";").split(";") if x.strip().lower() not in NONE_WORDS}


def _opt(v: str) -> Optional[str]:
    v = (v or "").strip()
    return None if v.lower() in NONE_WORDS else v


def load_rows(path: Path, split: str) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    need_cols = {"msg_id", "timestamp", "raw_text", "true_incident_id", "location", "need", "urgency", "is_fake"}
    missing = need_cols - set(rows[0].keys() if rows else [])
    if missing:
        raise SystemExit(f"{path}: missing columns {sorted(missing)}")
    out = []
    for r in rows:
        if split != "all" and (r.get("split") or "").strip().lower() != split:
            continue
        ts = datetime.fromisoformat(r["timestamp"].strip())
        out.append({
            "msg_id": r["msg_id"].strip(),
            "channel": (r.get("channel") or "sms").strip().lower() or "sms",
            "sender_id": (r.get("sender_id") or r["msg_id"]).strip(),
            "ts": ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc),
            "text": r["raw_text"],
            "incident": _opt(r["true_incident_id"]),
            "place": _opt(r["location"]),
            "needs": _split_list(r["need"]),
            "people": int(r["people_count"]) if (r.get("people_count") or "").strip().isdigit() else None,
            "vulnerable": _split_list(r.get("vulnerable", "")),
            "urgency": (_opt(r["urgency"]) or "none").lower(),
            "fake": (r["is_fake"] or "0").strip() in ("1", "true", "yes", "y"),
            "source": (r.get("source") or "human").strip().lower(),
        })
    out.sort(key=lambda x: x["ts"])
    return out


# ------------------------------------------------------------------ metric helpers
def ratio(num: int, den: int) -> dict:
    return {"value": round(num / den, 3) if den else None, "n": den, "hits": num}


def prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f = 2 * p * r / (p + r) if p and r else (0.0 if p == 0 or r == 0 else None)
    return {"precision": round(p, 3) if p is not None else None, "recall": round(r, 3) if r is not None else None,
            "f1": round(f, 3) if f is not None else None, "tp": tp, "fp": fp, "fn": fn}


def pairwise_merge(true_ids: list[Optional[str]], pred_ids: list[Optional[str]]) -> dict:
    """Pair counting over messages that belong to a real case: same true case <-> same predicted incident."""
    idx = [k for k, t in enumerate(true_ids) if t]
    tp = fp = fn = 0
    for a, b in combinations(idx, 2):
        same_t = true_ids[a] == true_ids[b]
        same_p = pred_ids[a] is not None and pred_ids[a] == pred_ids[b]
        tp += same_t and same_p
        fp += (not same_t) and same_p
        fn += same_t and not same_p
    res = prf(tp, fp, fn)
    res["true_cases"] = len({true_ids[k] for k in idx})
    res["predicted_incidents"] = len({pred_ids[k] for k in idx if pred_ids[k]})
    return res


# ------------------------------------------------------------------ replay through the real pipeline
def replay(rows: list[dict], use_llm: bool) -> dict:
    from backend.service import Rahat                 # imported late: tests can import helpers cheaply
    from shared.schemas import Channel, MessageIn

    now = [rows[0]["ts"] if rows else datetime.now(timezone.utc)]
    tmp = tempfile.mkdtemp(prefix="rahat-eval-")
    r = Rahat(db_path=os.path.join(tmp, "eval.db"), use_llm=use_llm, workers=1, clock=lambda: now[0])
    ids, t0 = [], time.perf_counter()
    for row in rows:
        now[0] = row["ts"]
        ch = row["channel"] if row["channel"] in Channel.__members__ else "sms"
        m = r.ingest(MessageIn(channel=ch, sender_id=row["sender_id"], raw_text=row["text"], received_at=row["ts"]),
                     wait=True)
        ids.append(m.id)
    elapsed = time.perf_counter() - t0
    msgs = [r.store.get("messages", i) for i in ids]
    incidents = {i.id: i for i in r.store.all("incidents")}
    compare = r.compare()
    r.pool.shutdown(wait=True)
    return {"msgs": msgs, "incidents": incidents, "compare": compare, "seconds": elapsed}


# ------------------------------------------------------------------ scoring
def score(rows: list[dict], run: dict) -> dict:
    msgs, incs = run["msgs"], run["incidents"]
    ex = [m.extraction for m in msgs]
    used = Counter((e.extractor.split(":")[0] if e else "none") for e in ex)

    # 1. extraction ------------------------------------------------------
    help_rows = [k for k, r in enumerate(rows) if r["needs"] and "information" not in r["needs"]]
    place_hit = place_wrong = place_missed = 0
    for k in help_rows:
        pred = ex[k].place_id if ex[k] else None
        true = rows[k]["place"]
        place_hit += pred == true
        place_wrong += pred is not None and pred != true          # worst error: team goes to the wrong place
        place_missed += pred is None and true is not None
    need_hit = sum(1 for k in help_rows if ex[k] and rows[k]["needs"] & {n.value for n in ex[k].needs})
    ppl_rows = [k for k in help_rows if rows[k]["people"] is not None]
    ppl_hit = sum(1 for k in ppl_rows if ex[k] and ex[k].people_count == rows[k]["people"])
    v_tp = v_fp = v_fn = 0
    for k in help_rows:
        pv = {v.value for v in ex[k].vulnerable} if ex[k] else set()
        tv = rows[k]["vulnerable"]
        v_tp += len(pv & tv); v_fp += len(pv - tv); v_fn += len(tv - pv)
    # help request vs noise ("kab tak barish rukegi?")
    h_tp = h_fp = h_fn = 0
    for k, row in enumerate(rows):
        true_help = bool(row["needs"]) and "information" not in row["needs"]
        pred_help = msgs[k].status.value != "info"
        h_tp += true_help and pred_help; h_fp += (not true_help) and pred_help; h_fn += true_help and not pred_help

    # 2. merging ---------------------------------------------------------
    merge = pairwise_merge([r["incident"] for r in rows], [m.incident_id for m in msgs])

    # 3 + 4. per real case: urgency and fake --------------------------------
    by_case: dict[str, list[int]] = defaultdict(list)
    for k, row in enumerate(rows):
        if row["incident"]:
            by_case[row["incident"]].append(k)
    confusion = {t: Counter() for t in URGENCY_ORDER}
    under = []
    f_tp = f_fp = f_fn = f_tn = 0
    wrongly_flagged = []
    for case, ks in by_case.items():
        votes = Counter(msgs[k].incident_id for k in ks if msgs[k].incident_id)
        inc = incs.get(votes.most_common(1)[0][0]) if votes else None
        true_u = Counter(rows[k]["urgency"] for k in ks).most_common(1)[0][0]
        pred_u = BAND_TO_URGENCY[inc.band.value] if inc else "none"
        true_fake = sum(rows[k]["fake"] for k in ks) * 2 > len(ks)
        if true_u in confusion and not true_fake:      # fakes are scored under fake detection, not urgency
            confusion[true_u][pred_u] += 1
            if true_u in ("critical", "high") and pred_u in ("medium", "low", "none"):
                under.append({"case": case, "true": true_u, "predicted": pred_u, "text": rows[ks[0]]["text"][:90]})
        pred_fake = bool(inc) and (inc.action == "verify_first" or inc.confidence < config.CONF_SUSPECT)
        f_tp += true_fake and pred_fake; f_fp += (not true_fake) and pred_fake
        f_fn += true_fake and not pred_fake; f_tn += (not true_fake) and not pred_fake
        if pred_fake and not true_fake:
            wrongly_flagged.append({"case": case, "urgency": true_u, "flags": inc.red_flags, "text": rows[ks[0]]["text"][:90]})
    labelled = [c for c in URGENCY_ORDER if sum(confusion[c].values())]
    exact = sum(confusion[c][c] for c in URGENCY_ORDER)
    n_cases = sum(sum(confusion[c].values()) for c in URGENCY_ORDER)
    crit_n = sum(confusion["critical"].values())

    cmp = run["compare"]
    return {
        "messages": len(rows), "real_cases": len(by_case),
        "extractor_used": dict(used),
        "extraction": {
            "place_correct": ratio(place_hit, len(help_rows)),
            "place_wrong_place": ratio(place_wrong, len(help_rows)),
            "place_missed": ratio(place_missed, len(help_rows)),
            "need_found": ratio(need_hit, len(help_rows)),
            "people_exact": ratio(ppl_hit, len(ppl_rows)),
            "vulnerable": prf(v_tp, v_fp, v_fn),
            "help_vs_noise": prf(h_tp, h_fp, h_fn),
        },
        "merging": merge,
        "urgency": {
            "exact": ratio(exact, n_cases),
            "critical_recall": ratio(confusion["critical"]["critical"], crit_n),
            "under_triage": ratio(len(under), sum(sum(confusion[c].values()) for c in ("critical", "high"))),
            "confusion": {t: dict(confusion[t]) for t in labelled},
            "under_triage_cases": under,
        },
        "fake": {**prf(f_tp, f_fp, f_fn),
                 "real_cases_wrongly_flagged": ratio(f_fp, f_fp + f_tn),
                 "wrongly_flagged_cases": wrongly_flagged},
        "team_plan": {k: {x: cmp[k][x] for x in ("longest_critical_wait_min", "critical_within_target",
                                                  "critical_incidents", "total_travel_min")}
                      for k in ("greedy", "rahat")},
        "pipeline_seconds_per_message": round(run["seconds"] / max(1, len(rows)), 3),
    }


# ------------------------------------------------------------------ per-message sheet (for Excel)
def rows_table(rows: list[dict], run: dict) -> list[dict]:
    """One line per message: the human label next to what RAHAT did, with ✓/✗, to open in Excel."""
    msgs, incs = run["msgs"], run["incidents"]
    ok = lambda b: "✓" if b else "✗"
    out = []
    for row, m in zip(rows, msgs):
        e = m.extraction
        inc = incs.get(m.incident_id) if m.incident_id else None
        help_msg = bool(row["needs"]) and "information" not in row["needs"]
        p_place = e.place_id if e else None
        p_needs = {n.value for n in e.needs} if e else set()
        p_fake = bool(inc) and (inc.action == "verify_first" or inc.confidence < config.CONF_SUSPECT)
        p_urg = BAND_TO_URGENCY[inc.band.value] if inc else "none"
        out.append({
            "msg_id": row["msg_id"], "message": row["text"],
            "label_place": row["place"] or "", "rahat_place": p_place or "",
            "place_ok": ok(p_place == row["place"]) if help_msg else "",
            "label_need": ";".join(sorted(row["needs"])) or "none", "rahat_need": ";".join(sorted(p_needs)) or "none",
            "need_ok": ok(bool(row["needs"] & p_needs)) if help_msg else ok(m.status.value == "info"),
            "label_people": row["people"] if row["people"] is not None else "",
            "rahat_people": (e.people_count if e and e.people_count is not None else ""),
            "people_ok": ok(e is not None and e.people_count == row["people"]) if row["people"] is not None else "",
            "label_case": row["incident"] or "", "rahat_incident": m.incident_id or "(not a case)",
            "label_urgency": row["urgency"], "rahat_urgency": p_urg,
            "urgency_ok": ok(p_urg == row["urgency"]) if row["incident"] else "",
            "label_fake": "yes" if row["fake"] else "no", "rahat_flagged": "yes" if p_fake else "no",
            "fake_ok": ok(p_fake == row["fake"]) if row["incident"] else "",
            "extractor": e.extractor if e else "",
        })
    return out


def write_sheet(path: Path, table: list[dict]) -> None:
    # utf-8-sig (with BOM) so Excel shows Hindi / Devanagari correctly on double-click
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(table[0].keys()) if table else ["msg_id"])
        w.writeheader()
        w.writerows(table)


# ------------------------------------------------------------------ report
def _fmt(m) -> str:
    if isinstance(m, dict) and "value" in m:
        return "  n/a   (n=0)" if m["value"] is None else f"{m['value'] * 100:5.1f}%  ({m['hits']}/{m['n']})"
    if isinstance(m, dict) and "f1" in m:
        f = lambda v: "n/a" if v is None else f"{v * 100:.0f}%"
        return f"P {f(m['precision'])} · R {f(m['recall'])} · F1 {f(m['f1'])}  (tp {m['tp']}, fp {m['fp']}, fn {m['fn']})"
    return str(m)


def report(results: dict[str, dict], meta: dict) -> str:
    modes = list(results)
    lines = [f"RAHAT evaluation · {meta['data']} · split={meta['split']} · {meta['messages']} messages, "
             f"{meta['cases']} real cases", ""]

    def row(label, path):
        vals = []
        for m in modes:
            v = results[m]
            for p in path:
                v = v[p]
            vals.append(_fmt(v))
        lines.append(f"  {label:<38}" + "   |   ".join(f"{x:<40}" for x in vals))

    lines.append("  " + " " * 38 + "   |   ".join(f"{m:<40}" for m in modes))
    lines.append("  extractor actually used: " + " | ".join(str(results[m]["extractor_used"]) for m in modes))
    lines.append("\n 1. Extraction (help messages)")
    row("place correct", ["extraction", "place_correct"])
    row("WRONG place (worst error)", ["extraction", "place_wrong_place"])
    row("place missed (asks follow-up)", ["extraction", "place_missed"])
    row("need found", ["extraction", "need_found"])
    row("people count exact", ["extraction", "people_exact"])
    row("vulnerable groups", ["extraction", "vulnerable"])
    row("help request vs noise", ["extraction", "help_vs_noise"])
    lines.append("\n 2. Merging duplicates (message pairs)")
    row("same case -> same incident", ["merging"])
    lines.append("     true cases vs predicted incidents: " + " | ".join(
        f"{results[m]['merging']['true_cases']} vs {results[m]['merging']['predicted_incidents']}" for m in modes))
    lines.append("\n 3. Urgency (per real case)")
    row("band exactly right", ["urgency", "exact"])
    row("critical shown as critical", ["urgency", "critical_recall"])
    row("UNDER-TRIAGE (crit/high -> med/low)", ["urgency", "under_triage"])
    for m in modes:
        conf = results[m]["urgency"]["confusion"]
        lines.append(f"     [{m}] labelled -> predicted: " + "; ".join(
            f"{t}: " + ", ".join(f"{p} {n}" for p, n in sorted(c.items())) for t, c in conf.items()))
    lines.append("\n 4. Fake detection (per real case)")
    row("fake flagged", ["fake"])
    row("REAL cases wrongly flagged", ["fake", "real_cases_wrongly_flagged"])
    lines.append("\n 5. Team plan on the final cases (all teams at base)")
    for m in modes:
        tp = results[m]["team_plan"]
        lines.append(f"  [{m}] longest critical wait: greedy {tp['greedy']['longest_critical_wait_min']} min vs "
                     f"RAHAT {tp['rahat']['longest_critical_wait_min']} min · critical within 15 min: greedy "
                     f"{tp['greedy']['critical_within_target']}/{tp['greedy']['critical_incidents']} vs RAHAT "
                     f"{tp['rahat']['critical_within_target']}/{tp['rahat']['critical_incidents']}")
    for m in modes:
        u = results[m]["urgency"]["under_triage_cases"]
        w = results[m]["fake"]["wrongly_flagged_cases"]
        if u or w:
            lines.append(f"\n Cases to look at [{m}]:")
            lines += [f"   under-triage {x['case']}: {x['true']} -> {x['predicted']} · {x['text']}" for x in u]
            lines += [f"   real case flagged {x['case']} ({x['urgency']}): {x['flags']} · {x['text']}" for x in w]
    lines += ["", " Notes:",
              "  - Travel times are straight line x road factor / speed (prototype), not real road routing.",
              "  - Numbers on the 'tune' split are optimistic. Quote only the 'test' split.",
              "  - Every number shows (hits/n). With small n, one case moves the percentage a lot."]
    if meta.get("warning"):
        lines.insert(1, "  !! " + meta["warning"])
    return "\n".join(lines)


# ------------------------------------------------------------------ CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Evaluate RAHAT on human-labelled messages")
    ap.add_argument("--data", help="labelled CSV (default: data/labelled_messages_locked.csv)")
    ap.add_argument("--split", default="test", choices=["test", "tune", "all"])
    ap.add_argument("--extractor", default="rules", choices=["rules", "llm", "both"],
                    help="rules = baseline (no API calls); llm/both use the LLM key from .env")
    ap.add_argument("--out", default=str(ROOT / "evaluation" / "results"), help="folder for the JSON results")
    ap.add_argument("--export", action="store_true",
                    help="also write a per-message spreadsheet (label vs RAHAT, ✓/✗) to open in Excel")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")          # Windows console: keep the report readable
    except (AttributeError, ValueError):
        pass

    warning = None
    path = Path(a.data) if a.data else DEFAULT_LOCKED
    if not path.exists():
        if a.data:
            raise SystemExit(f"not found: {path}")
        path, warning = TEMPLATE, "Only the TEMPLATE was found (example rows). This is a smoke test, NOT an evaluation."
        a.split = "all"
    if a.split != "test" and not warning:
        warning = f"split={a.split}: these numbers are optimistic (tuning data). Quote only --split test."
    rows = load_rows(path, a.split)
    if not rows:
        raise SystemExit(f"no rows with split={a.split} in {path}")
    n_ai = sum("ai" in r["source"] for r in rows)
    if n_ai or "synthetic" in path.name.lower():
        warning = (f"{n_ai or len(rows)}/{len(rows)} messages are synthetic (AI-generated) demo data. This is a "
                   f"stress test, NOT a real-world accuracy number. Do not quote it as accuracy.")

    modes = ["rules", "llm"] if a.extractor == "both" else [a.extractor]
    if "llm" in modes:
        from agents import llm
        if not llm.enabled():
            raise SystemExit("LLM mode needs GEMINI_API_KEY (or ANTHROPIC_API_KEY) in .env")
        print(f"LLM mode: about {len(rows)} API calls to {llm.name()}", file=sys.stderr)
    runs = {m: replay(rows, use_llm=(m == "llm")) for m in modes}
    results = {m: score(rows, runs[m]) for m in modes}

    meta = {"data": str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path), "split": a.split,
            "messages": len(rows), "cases": results[modes[0]]["real_cases"], "warning": warning,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "cost_mode": config.COST_MODE, "llm_model": config.LLM_MODEL}
    print(report(results, meta))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    f = out / f"eval_{a.split}_{a.extractor}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    f.write_text(json.dumps({"meta": meta, "results": results}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nsaved: {f}")
    if a.export:
        for m in modes:
            sheet = f.with_name(f.stem + f"_{m}_messages.csv")
            write_sheet(sheet, rows_table(rows, runs[m]))
            print(f"spreadsheet ({m}): {sheet}   <- double-click to open in Excel")
    return 0


if __name__ == "__main__":
    sys.exit(main())
