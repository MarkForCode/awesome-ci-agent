"""Runs inside GitHub Actions.

full mode : replay traffic against the currently deployed version (latest prod-* tag),
            analyze logs, open/refresh/reopen GitHub issues, then pick the next ticket.
pick mode : only pick the next ticket (used when a human approves an issue).

Picking = add the `agent-fix` label to the highest-impact `ready` issue, but only when no
other ticket is in flight. One at a time keeps PRs from conflicting and makes the loop:
deploy -> monitor -> issue -> agent -> PR -> CI -> merge -> deploy -> ...
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "loop"))
import analyst  # noqa: E402
import traffic  # noqa: E402

LABELS = {
    "loop": ("5319e7", "Created by the agent loop"),
    "bug": ("d73a4a", "Bug found in production"),
    "feature": ("0e8a16", "Requirement proposed by the analyst"),
    "ready": ("1d76db", "Approved for the agent to work on"),
    "needs-approval": ("fbca04", "Feature waiting for a human to add `ready`"),
    "agent-fix": ("000000", "Agent is working on this now"),
    "needs-human": ("b60205", "Agent could not fix it"),
    "agent-pr": ("c5def5", "PR opened by the agent"),
}
SIG_RE = re.compile(r"<!-- loop-signature: (.+?) -->")
COUNT_RE = re.compile(r"<!-- loop-count: (\d+) -->")


def gh(*args, capture=True):
    r = subprocess.run(["gh", *args], capture_output=capture, text=True)
    if r.returncode:
        raise RuntimeError(f"gh {' '.join(args)}\n{r.stderr}")
    return r.stdout


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def summary(md):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    print(md)
    if path:
        with open(path, "a") as f:
            f.write(md + "\n")


def issue_body(f):
    ev = json.dumps(f["evidence"], indent=2, ensure_ascii=False)
    who = "monitor" if f["kind"] == "bug" else "product analyst"
    return (f"Opened automatically by the {who}.\n\n"
            f"**Seen in production:** {f['count']} times in the last traffic window\n\n"
            f"**Evidence**\n```json\n{ev}\n```\n\n"
            "Definition of done: fix in `shop/`, add a regression test in `tests/`, all tests pass.\n\n"
            f"<!-- loop-signature: {f['signature']} -->\n<!-- loop-count: {f['count']} -->\n")


def deployed_source():
    tags = git("tag", "-l", "prod-*", "--sort=-v:refname").splitlines()
    ref = tags[0] if tags else "HEAD"
    out = ROOT / "state" / "deployed_pricing.py"
    out.parent.mkdir(exist_ok=True)
    out.write_text(git("show", f"{ref}:shop/pricing.py") + "\n")
    return ref, out


def loop_issues():
    data = gh("issue", "list", "--label", "loop", "--state", "all", "--limit", "300",
              "--json", "number,title,state,body,labels")
    issues = json.loads(data)
    for i in issues:
        m = SIG_RE.search(i["body"] or "")
        i["sig"] = m.group(1) if m else None
        c = COUNT_RE.search(i["body"] or "")
        i["count"] = int(c.group(1)) if c else 0
        i["labelset"] = {lab["name"] for lab in i["labels"]}
    return issues


def full_run():
    for name, (color, desc) in LABELS.items():
        gh("label", "create", name, "--color", color, "--description", desc, "--force")

    ref, path = deployed_source()
    log = ROOT / "state" / "prod.jsonl"
    seed = int(os.environ.get("GITHUB_RUN_NUMBER", "1"))
    stats = traffic.run(path, log, n=300, seed=seed)
    known = set(traffic.load_deployed(path).COUPONS)
    findings = analyst.analyze(log, known)
    auto_features = os.environ.get("AUTO_APPROVE_FEATURES", "false").lower() == "true"

    summary(f"## Production check of `{ref}`\n\n| checkouts | ok | crashes | rejected coupons |\n"
            f"|---|---|---|---|\n| {sum(stats.values())} | {stats['ok']} | {stats['error']} | {stats['rejected']} |\n")

    issues = {i["sig"]: i for i in loop_issues() if i["sig"]}
    rows = []
    for f in findings:
        i = issues.get(f["signature"])
        if i is None:
            labels = ["loop", f["kind"]]
            labels.append("ready" if (f["kind"] == "bug" or auto_features) else "needs-approval")
            url = gh("issue", "create", "--title", f"[{f['kind']}] {f['title']}",
                     "--body", issue_body(f), "--label", ",".join(labels)).strip()
            rows.append(f"| new | {f['kind']} | {f['title']} | {f['count']} | {url} |")
        elif i["state"] == "CLOSED":
            gh("issue", "reopen", str(i["number"]))
            gh("issue", "comment", str(i["number"]), "--body",
               f"♻️ Regression: seen {f['count']} more times on `{ref}` after this was closed.")
            gh("issue", "edit", str(i["number"]), "--body", issue_body(f), "--add-label", "ready")
            rows.append(f"| reopened | {f['kind']} | {f['title']} | {f['count']} | #{i['number']} |")
        else:
            gh("issue", "edit", str(i["number"]), "--body", issue_body(f))
            rows.append(f"| still open | {f['kind']} | {f['title']} | {f['count']} | #{i['number']} |")
    if rows:
        summary("| status | kind | finding | count | issue |\n|---|---|---|---|---|\n" + "\n".join(rows))
    else:
        summary("✅ Nothing new: no crashes and no feature signal above threshold.")


def pick():
    issues = loop_issues()
    open_issues = [i for i in issues if i["state"] == "OPEN"]
    if any("agent-fix" in i["labelset"] for i in open_issues):
        summary("⏳ A ticket is already in flight; not picking another.")
        return
    prs = json.loads(gh("pr", "list", "--label", "agent-pr", "--state", "open", "--json", "number"))
    if prs:
        summary(f"⏳ Agent PR #{prs[0]['number']} is still open; not picking another.")
        return
    candidates = [i for i in open_issues
                  if "ready" in i["labelset"] and "needs-human" not in i["labelset"]]
    if not candidates:
        summary("😴 No approved tickets. Loop is idle.")
        return
    nxt = max(candidates, key=lambda i: i["count"])
    gh("issue", "edit", str(nxt["number"]), "--add-label", "agent-fix")
    summary(f"🤖 Handed #{nxt['number']} ({nxt['title']}) to the agent.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["full", "pick"], default="full")
    mode = ap.parse_args().mode
    if mode == "full":
        full_run()
    pick()
