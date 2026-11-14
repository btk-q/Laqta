"""smoke.py <folder with Laqta.exe>: prove the packaged app works before it is published.

Runs the real exe: its version, its self-test (every window, the player, the sound recogniser),
the health check and the laugh/shout recogniser on a real recording. Exits 1 on any failure.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main(folder: str) -> int:
    exe = Path(folder) / "Laqta.exe"
    work = Path(tempfile.mkdtemp(prefix="laqta-smoke-"))
    sys.path.insert(0, str(ROOT))
    import laqta

    def run(*args, timeout=180):
        # From another folder: a friend's copy is not started from the project.
        return subprocess.run([str(exe), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, cwd=work)

    problems = []
    version = run("--version")
    if version.stdout.strip() != f"laqta {laqta.__version__}":
        problems.append(f"--version said {version.stdout.strip()!r}, expected laqta {laqta.__version__}")
    report = work / "selftest.txt"
    test = run("selftest", "--out", str(report))
    text = report.read_text(encoding="utf-8") if report.exists() else test.stdout + test.stderr
    print(text)
    if test.returncode != 0 or "selftest OK" not in text:
        problems.append("selftest failed")
    heard = run("voice", str(ROOT / "tests/fixtures/voice/laugh.ogg"), "--track", "1")
    if "laugh" not in heard.stdout:
        problems.append(f"the recogniser did not hear the laugh: {heard.stdout[-200:]!r} {heard.stderr[-200:]!r}")
    diag = run("diagnose", "--out", str(work / "d.zip"))
    if not (work / "d.zip").exists():
        problems.append(f"diagnose wrote no file: {diag.stderr[-200:]!r}")
    for problem in problems:
        print("SMOKE FAILED:", problem)
    print("smoke test:", "FAILED" if problems else "passed")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
