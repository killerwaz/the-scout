"""Run every offline self-test and report which ones pass.   python run_checks.py"""
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHECKS = ["check.py", "check_act.py", "check_api.py", "check_scout.py",
          "check_search.py", "check_surfaces.py", "check_tabpool.py", "regress.py"]

env = dict(os.environ, PYTHONIOENCODING="utf-8")
failed = []
for name in CHECKS:
    try:
        r = subprocess.run([sys.executable, name], cwd=HERE, env=env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=300)
        out, ok = (r.stdout or "") + (r.stderr or ""), r.returncode == 0
    except subprocess.TimeoutExpired:
        out, ok = "timed out after 300 seconds\n", False
    print(f"{'ok' if ok else 'FAILED':<8}{name}")
    if not ok:
        failed.append(name)
        print("\n".join(out.rstrip().splitlines()[-15:]))
print(f"\n{len(CHECKS) - len(failed)} of {len(CHECKS)} passed")
sys.exit(1 if failed else 0)
