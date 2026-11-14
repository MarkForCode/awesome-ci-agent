"""Offline stand-in for Claude Code inside GitHub Actions (AGENT_MODE=mock).
Applies scripted edits for a ticket signature, running tests after each attempt and
reverting failed attempts, so the whole GitHub loop can be tried without an API key.

Usage: python loop/mock_agent.py "<signature>"
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agents import MockAgent  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main():
    sig = sys.argv[1]
    agent = MockAgent()
    for attempt in range(3):
        summary = agent.fix(ROOT, {"signature": sig}, attempt)
        if summary == "no idea how to fix this":
            print("mock agent: no scripted fix for", sig)
            sys.exit(1)
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
                           cwd=ROOT, capture_output=True, text=True)
        tail = (r.stdout + r.stderr).strip().splitlines()[-1]
        print(f"attempt {attempt + 1}: {summary} -> {tail}")
        if r.returncode == 0:
            Path(ROOT / ".agent-summary").write_text(summary)
            return
        subprocess.run(["git", "checkout", "-q", "--", "shop", "tests"], cwd=ROOT, check=True)
    sys.exit(1)


if __name__ == "__main__":
    main()
