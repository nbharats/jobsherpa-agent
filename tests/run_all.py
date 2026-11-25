"""Run every test module and print one line each (use before committing).

    python -m tests.run_all
"""
import subprocess
import sys
from pathlib import Path


def main() -> int:
    modules = sorted(path.stem for path in Path(__file__).parent.glob("test_*.py"))
    failed = []
    for name in modules:
        result = subprocess.run([sys.executable, "-m", f"tests.{name}"],
                                capture_output=True, text=True, timeout=600)
        passes = result.stdout.count("PASS  ")
        ok = result.returncode == 0
        detail = f" ({passes} passed)" if passes else ""
        print(f"{'OK  ' if ok else 'FAIL'} {name}{detail}")
        if not ok:
            failed.append(name)
            print((result.stderr or result.stdout)[-1500:])
    print("\nAll test modules passed." if not failed else f"\nFailed: {', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())