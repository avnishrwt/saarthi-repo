"""The evaluation script itself must be right, or every number it prints is wrong."""
from evaluation import evaluate as ev

CSV = """msg_id,split,channel,sender_id,timestamp,raw_text,true_incident_id,location,need,people_count,vulnerable,urgency,is_fake
A1,test,whatsapp,P1,2026-11-10T02:04:11,"Bhai Deep Nagar gali 3 mein meri dadi phasi hai, paani 5 feet hai",C1,DEEP_NAGAR,rescue,1,elderly,critical,0
A2,test,sms,P2,2026-11-10T02:05:40,"deep nagar gali 3 old lady trapped plz",C1,DEEP_NAGAR,rescue,1,elderly,critical,0
A3,test,sms,P3,2026-11-10T02:06:02,"Dalanwala school me 20 log hai khana aur paani chahiye",C2,DALANWALA,food;water,20,,medium,0
A4,tune,sms,P4,2026-11-10T02:08:30,"kab tak barish rukegi?",,,none,,,none,0
"""


def test_pairwise_merge_counts_pairs():
    # true: {0,1} one case, {2} another; predicted wrongly merges 2 into the first incident
    m = ev.pairwise_merge(["C1", "C1", "C2", None], ["I1", "I1", "I1", None])
    assert (m["tp"], m["fp"], m["fn"]) == (1, 2, 0)
    assert m["precision"] == 0.333 and m["recall"] == 1.0
    assert m["true_cases"] == 2 and m["predicted_incidents"] == 1


def test_prf_handles_empty():
    assert ev.prf(0, 0, 0)["precision"] is None


def test_split_filter_and_parsing(tmp_path):
    f = tmp_path / "l.csv"
    f.write_text(CSV, encoding="utf-8")
    rows = ev.load_rows(f, "test")
    assert [r["msg_id"] for r in rows] == ["A1", "A2", "A3"]          # tune row excluded
    assert rows[2]["needs"] == {"food", "water"} and rows[2]["people"] == 20
    assert ev.load_rows(f, "all")[-1]["needs"] == set()               # "none" = not a help request


def test_end_to_end_rules(tmp_path):
    f = tmp_path / "l.csv"
    f.write_text(CSV, encoding="utf-8")
    rows = ev.load_rows(f, "all")
    res = ev.score(rows, ev.replay(rows, use_llm=False))
    assert res["extraction"]["place_correct"]["value"] == 1.0
    assert res["merging"]["recall"] == 1.0 and res["merging"]["fp"] == 0     # two dadi messages -> one incident
    assert res["urgency"]["critical_recall"]["value"] == 1.0
    assert res["extraction"]["help_vs_noise"]["fp"] == 0                    # noise not counted as help
