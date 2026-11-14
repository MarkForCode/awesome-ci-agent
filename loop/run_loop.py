"""Closed-loop orchestrator:
   monitor prod -> analyze -> open tickets -> agent fixes on a branch -> tests gate -> merge
   -> deploy new version -> next round of traffic ...

Usage:
  python loop/run_loop.py                     # offline demo with the scripted MockAgent
  python loop/run_loop.py --agent claude      # real Claude Code (needs `claude` CLI logged in)
  python loop/run_loop.py --require-approval  # feature tickets wait for a human (approved.txt)
"""
import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "loop"))
import analyst  # noqa: E402
import traffic  # noqa: E402
from agents import ClaudeAgent, MockAgent  # noqa: E402

STATE = ROOT / "state"
MAX_ATTEMPTS = 3


def sh(*cmd, check=True):
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"{' '.join(cmd)}\n{r.stdout}\n{r.stderr}")
    return r


def say(icon, msg):
    print(f"{icon}  {msg}", flush=True)


def git_init():
    if not (ROOT / ".git").exists():
        sh("git", "init", "-q", "-b", "main")
        sh("git", "config", "user.email", "loop@demo.local")
        sh("git", "config", "user.name", "agent-loop")
        sh("git", "add", "-A")
        sh("git", "commit", "-qm", "v0: initial checkout service")


def run_tests():
    r = sh(sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests", check=False)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def deploy(version):
    d = STATE / "deploy" / f"v{version}"
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "shop" / "pricing.py", d / "pricing.py")
    commit = sh("git", "rev-parse", "--short", "HEAD").stdout.strip()
    (STATE / "deploy" / "CURRENT").write_text(f"v{version} {commit}\n")
    return d / "pricing.py", commit


def load_json(p, default):
    return json.loads(p.read_text()) if p.exists() else default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", choices=["mock", "claude"], default="mock")
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--require-approval", action="store_true")
    args = ap.parse_args()

    if STATE.exists():
        shutil.rmtree(STATE)
    (STATE / "logs").mkdir(parents=True)
    (STATE / "prs").mkdir()
    git_init()
    agent = ClaudeAgent() if args.agent == "claude" else MockAgent()
    tickets, metrics, pr_no = {}, [], 0
    version = 1
    deployed, commit = deploy(version)
    say("🚀", f"Deployed v{version} ({commit}). Agent = {agent.name}")

    for rnd in range(1, args.rounds + 1):
        print(f"\n{'=' * 64}\nROUND {rnd}  — production running v{version}\n{'=' * 64}")
        # 1. production traffic
        log = STATE / "logs" / f"round{rnd}.jsonl"
        stats = traffic.run(deployed, log, n=300, seed=rnd)
        n = sum(stats.values())
        say("📈", f"Traffic: {n} checkouts | ok {stats['ok']} | crashes {stats['error']} "
                  f"| rejected coupons {stats['rejected']}")
        metrics.append({"round": rnd, "version": version, **stats})

        # 2. monitor + analyze -> tickets
        known = traffic.load_deployed(deployed).COUPONS.keys()
        new = 0
        for f in analyst.analyze(log, set(known)):
            t = next((t for t in tickets.values() if t["signature"] == f["signature"]), None)
            if t is None:
                tid = f"T-{len(tickets) + 1:03d}"
                status = "awaiting-approval" if (f["kind"] == "feature" and args.require_approval) else "open"
                tickets[tid] = {"id": tid, **f, "status": status, "opened_round": rnd}
                new += 1
                say("🎫", f"{tid} [{f['kind']}] {f['title']}  (x{f['count']})")
            elif t["status"] == "done":
                t.update(status="open", reopened=True, count=f["count"])
                say("♻️", f"{t['id']} REGRESSION — still seen in prod, reopened")
        if new == 0 and stats["error"] == 0:
            say("✅", "No crashes and nothing new worth building. Loop is idle.")
            break

        approved = set((ROOT / "approved.txt").read_text().split()) if (ROOT / "approved.txt").exists() else set()
        for t in tickets.values():
            if t["status"] == "awaiting-approval" and t["id"] in approved:
                t["status"] = "open"

        # 3. agent works tickets, highest impact first; tests are the merge gate
        merged_any = False
        for t in sorted(tickets.values(), key=lambda t: -t["count"]):
            if t["status"] != "open":
                continue
            branch = f"agent/{t['id'].lower()}"
            sh("git", "checkout", "-qb", branch)
            feedback, ok = "", False
            for attempt in range(MAX_ATTEMPTS):
                summary = agent.fix(ROOT, t, attempt, feedback)
                ok, out = run_tests()
                last = out.splitlines()[-1] if out else ""
                say("🤖" if ok else "❌", f"{t['id']} attempt {attempt + 1}: {summary} -> tests: {last}")
                if ok:
                    break
                feedback = out
                sh("git", "checkout", "-q", "--", ".")
                if summary == "no idea how to fix this":
                    break
            if ok:
                pr_no += 1
                sh("git", "add", "-A")
                sh("git", "commit", "-qm", f"{t['id']}: {summary}")
                diff = sh("git", "diff", "main", "--", "shop", "tests").stdout
                sh("git", "checkout", "-q", "main")
                sh("git", "merge", "-q", "--no-ff", branch, "-m", f"Merge PR #{pr_no} ({t['id']})")
                (STATE / "prs" / f"PR-{pr_no:03d}-{t['id']}.md").write_text(
                    f"# PR #{pr_no}: {t['title']}\n\nTicket: {t['id']} ({t['kind']})\n"
                    f"Agent: {agent.name}\nChange: {summary}\nVerification: {last}\n\n"
                    f"Evidence:\n```json\n{json.dumps(t['evidence'], indent=2)}\n```\n\n"
                    f"```diff\n{diff}```\n")
                t.update(status="done", pr=pr_no, fixed_round=rnd)
                merged_any = True
                say("🔀", f"PR #{pr_no} merged into main")
            else:
                sh("git", "checkout", "-q", "main")
                t["status"] = "needs-human"
                say("🙋", f"{t['id']} escalated to a human after {MAX_ATTEMPTS} failed attempts")
            sh("git", "branch", "-qD", branch)

        waiting = [t["id"] for t in tickets.values() if t["status"] == "awaiting-approval"]
        if waiting:
            say("⏸️", f"Waiting for human approval: {', '.join(waiting)} (add ids to approved.txt, then rerun)")

        # 4. deploy
        if merged_any:
            version += 1
            deployed, commit = deploy(version)
            say("🚀", f"Deployed v{version} ({commit})")
        time.sleep(0.2)

    (STATE / "tickets.json").write_text(json.dumps(tickets, indent=2))
    (STATE / "metrics.json").write_text(json.dumps(metrics, indent=2))
    print(f"\n{'=' * 64}\nSUMMARY\n{'=' * 64}")
    print(f"{'round':>5} {'ver':>4} {'ok':>5} {'crash':>6} {'reject':>7}")
    for m in metrics:
        print(f"{m['round']:>5} {'v' + str(m['version']):>4} {m['ok']:>5} {m['error']:>6} {m['rejected']:>7}")
    for t in tickets.values():
        print(f"  {t['id']} {t['status']:<18} {t['title']}")
    print("\nGit history:")
    print(sh("git", "log", "--oneline", "--graph", "-n", "20").stdout)


if __name__ == "__main__":
    main()
