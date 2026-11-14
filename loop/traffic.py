"""Simulated production users hitting the *deployed* version. Writes JSONL logs."""
import importlib.util
import json
import random
import traceback


def load_deployed(path):
    spec = importlib.util.spec_from_file_location("deployed_pricing", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_request(rng):
    r = rng.random()
    items = [{"price": rng.choice([5, 12, 30, 60, 120]), "qty": rng.randint(1, 4)}
             for _ in range(rng.randint(1, 3))]
    coupon = None
    if r < 0.03:
        items = []                                   # empty cart (mobile app bug)
    elif r < 0.08:
        del items[0]["qty"]                          # old API clients omit qty
    elif r < 0.13:
        coupon = rng.choice(["save10", "Save20"])    # users type lowercase
    elif r < 0.22:
        coupon = "FREESHIP"                          # marketing printed a code we don't support
    elif r < 0.25:
        coupon = rng.choice(["HELLO", "TEST123"])    # random noise
    elif r < 0.45:
        coupon = rng.choice(["SAVE10", "SAVE20"])
    return {"items": items, "coupon": coupon}


def run(deployed_path, log_path, n=300, seed=0):
    mod = load_deployed(deployed_path)
    rng = random.Random(seed)
    stats = {"ok": 0, "error": 0, "rejected": 0}
    with open(log_path, "w") as f:
        for _ in range(n):
            req = make_request(rng)
            try:
                total = mod.checkout(req["items"], coupon=req["coupon"])
                ev = {"event": "ok", "total": total, "coupon": req["coupon"]}
            except ValueError as e:
                ev = {"event": "rejected", "reason": "unknown_coupon",
                      "coupon": req["coupon"], "msg": str(e)}
            except Exception as e:
                tb = traceback.extract_tb(e.__traceback__)[-1]
                ev = {"event": "error", "type": type(e).__name__, "msg": str(e),
                      "where": f"{tb.name}:{tb.lineno}", "request": req}
            stats[ev["event"]] += 1
            f.write(json.dumps(ev) + "\n")
    return stats
