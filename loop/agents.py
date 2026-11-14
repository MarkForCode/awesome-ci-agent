"""Coding agents. ClaudeAgent is the real thing; MockAgent replays scripted edits so the loop
can be demonstrated offline (including a deliberately wrong first attempt)."""
import json
import subprocess
from pathlib import Path

PROMPT = """You are a coding agent working in this repository. Fix the following ticket.

TICKET:
{ticket}

Rules:
- Make the smallest change in shop/ that resolves the ticket.
- Add at least one regression test at the end of tests/test_pricing.py that fails before your fix and passes after.
- Run `python -m pytest -q` and make sure everything passes.
- Do not modify files outside shop/ and tests/. Do not commit; the orchestrator handles git.
"""


class ClaudeAgent:
    name = "claude-code"

    def fix(self, repo: Path, ticket: dict, attempt: int, feedback: str = "") -> str:
        prompt = PROMPT.format(ticket=json.dumps(ticket, indent=2))
        if feedback:
            prompt += f"\nYour previous attempt failed verification:\n{feedback[-3000:]}\n"
        r = subprocess.run(
            ["claude", "-p", prompt, "--permission-mode", "acceptEdits",
             "--allowedTools", "Read,Edit,Write,Bash(python -m pytest*)"],
            cwd=repo, capture_output=True, text=True, timeout=900)
        return r.stdout.strip()[-500:] or r.stderr.strip()[-500:]


# signature -> list of attempts; each attempt = (summary, [(file, old, new)])
_PLAYBOOK = {
    "crash:ZeroDivisionError:division by zero": [
        ("Guard empty cart (returns None)", [
            ("shop/pricing.py", "def checkout(items, coupon=None):\n",
             "def checkout(items, coupon=None):\n    if not items:\n        return None\n"),
            ("tests/test_pricing.py", None,
             "\n\ndef test_empty_cart_is_free():\n    assert checkout([]) == 0.0\n"),
        ]),
        ("Guard empty cart (returns 0.0)", [
            ("shop/pricing.py", "def checkout(items, coupon=None):\n",
             "def checkout(items, coupon=None):\n    if not items:\n        return 0.0\n"),
            ("tests/test_pricing.py", None,
             "\n\ndef test_empty_cart_is_free():\n    assert checkout([]) == 0.0\n"),
        ]),
    ],
    "crash:KeyError:'qty'": [
        ("Default qty to 1 for legacy clients", [
            ("shop/pricing.py", 'subtotal = sum(i["price"] * i["qty"] for i in items)\n    avg = subtotal / sum(i["qty"] for i in items)',
             'subtotal = sum(i["price"] * i.get("qty", 1) for i in items)\n    avg = subtotal / sum(i.get("qty", 1) for i in items)'),
            ("tests/test_pricing.py", None,
             '\n\ndef test_missing_qty_defaults_to_one():\n    assert checkout([{"price": 10}]) == 18.0\n'),
        ]),
    ],
    "ux:coupon-case-sensitive": [
        ("Normalize coupon codes", [
            ("shop/pricing.py", "    if coupon:\n        if coupon not in COUPONS:",
             "    if coupon:\n        coupon = coupon.strip().upper()\n        if coupon not in COUPONS:"),
            ("tests/test_pricing.py", None,
             '\n\ndef test_coupon_case_insensitive():\n    assert checkout([{"price": 50, "qty": 2}], coupon="save10") == 98.0\n'),
        ]),
    ],
    "feature:coupon:FREESHIP": [
        ("Add FREESHIP coupon (waives shipping)", [
            ("shop/pricing.py", 'COUPONS = {"SAVE10": 0.10, "SAVE20": 0.20}',
             'COUPONS = {"SAVE10": 0.10, "SAVE20": 0.20, "FREESHIP": 0.0}\nSHIPPING_WAIVERS = {"FREESHIP"}'),
            ("shop/pricing.py", "    shipping = 0 if subtotal >= FREE_SHIPPING_MIN else SHIPPING_FEE",
             "    shipping = 0 if subtotal >= FREE_SHIPPING_MIN else SHIPPING_FEE\n"
             "    if coupon in SHIPPING_WAIVERS:\n        shipping = 0"),
            ("tests/test_pricing.py", None,
             '\n\ndef test_freeship_coupon():\n    assert checkout([{"price": 10, "qty": 1}], coupon="FREESHIP") == 10.0\n'),
        ]),
    ],
}


class MockAgent:
    name = "mock-agent (scripted, offline)"

    def fix(self, repo: Path, ticket: dict, attempt: int, feedback: str = "") -> str:
        attempts = _PLAYBOOK.get(ticket["signature"])
        if not attempts or attempt >= len(attempts):
            return "no idea how to fix this"
        summary, edits = attempts[attempt]
        for file, old, new in edits:
            p = repo / file
            text = p.read_text()
            if old is None:
                text += new
            else:
                assert old in text, f"anchor not found in {file}"
                text = text.replace(old, new, 1)
            p.write_text(text)
        return summary
