"""Monitor + product analyst: turns production logs into tickets (bugs AND new requirements)."""
import json
from collections import Counter

FEATURE_MIN_SHARE = 0.05  # an unknown coupon tried by >=5% of traffic becomes a feature request


def analyze(log_path, known_coupons):
    events = [json.loads(line) for line in open(log_path)]
    total = len(events)
    findings = []

    # 1) crashes -> bug tickets, grouped by signature
    crashes = Counter((e["type"], e["msg"], e["where"]) for e in events if e["event"] == "error")
    examples = {}
    for e in events:
        if e["event"] == "error":
            examples.setdefault((e["type"], e["msg"], e["where"]), e["request"])
    for (typ, msg, where), n in crashes.items():
        findings.append({
            "kind": "bug",
            "signature": f"crash:{typ}:{msg}",
            "title": f"{typ} ({msg}) in {where}",
            "count": n,
            "evidence": {"example_request": examples[(typ, msg, where)], "where": where},
        })

    # 2) rejected coupons -> analysis
    rejected = Counter(e["coupon"] for e in events if e["event"] == "rejected")
    case_variants = {c: n for c, n in rejected.items() if c.upper() in known_coupons}
    if case_variants:
        findings.append({
            "kind": "bug",
            "signature": "ux:coupon-case-sensitive",
            "title": "Valid coupons rejected when typed in lowercase/mixed case",
            "count": sum(case_variants.values()),
            "evidence": {"rejected_variants": case_variants},
        })
    for code, n in rejected.items():
        if code.upper() in known_coupons:
            continue
        share = n / total
        if share >= FEATURE_MIN_SHARE:
            findings.append({
                "kind": "feature",
                "signature": f"feature:coupon:{code}",
                "title": f"Support coupon '{code}' ({share:.0%} of checkouts tried it)",
                "count": n,
                "evidence": {"share": round(share, 3),
                             "hypothesis": "Name suggests free shipping; likely from a marketing campaign"},
            })
    return sorted(findings, key=lambda f: -f["count"])
