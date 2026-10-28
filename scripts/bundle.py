"""Bundle the minimum files into a submission-ready directory (default dist/bot).

  python scripts/bundle.py [--out dist/bot]

Contents: core/ (cards, equity, preflop + table, state, bot, safety), bots/main_bot.py,
bots/opp_model.py, adapter/template.py, main.py entry point, requirements.txt and a
vendored copy of treys (pure Python) so the bot runs even without eval7.
"""
import argparse
import os
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = [
    "core/__init__.py", "core/cards.py", "core/equity.py", "core/preflop.py", "core/state.py",
    "core/bot.py", "core/safety.py", "core/data/preflop_equity.json",
    "bots/main_bot.py", "bots/opp_model.py",
    "adapter/__init__.py", "adapter/template.py", "requirements.txt",
]
MAIN = '''"""Submission entry point: python main.py [--tcp HOST PORT | --jsonl HOST PORT] [--debug]"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adapter.template import main  # noqa: E402

if __name__ == "__main__":
    main()
'''


def bundle(out):
    out = os.path.abspath(out)
    if os.path.exists(out):
        shutil.rmtree(out)
    for f in FILES:
        dst = os.path.join(out, f)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(ROOT, f), dst)
    with open(os.path.join(out, "bots", "__init__.py"), "w") as fh:
        fh.write("")   # no zoo / registry in the submission
    with open(os.path.join(out, "main.py"), "w") as fh:
        fh.write(MAIN)
    try:
        import treys
        shutil.copytree(os.path.dirname(treys.__file__), os.path.join(out, "vendor", "treys"),
                        ignore=shutil.ignore_patterns("__pycache__"))
    except ImportError:
        print("warning: treys not installed, no evaluator fallback vendored")
    n = sum(len(fs) for _, _, fs in os.walk(out))
    print(f"bundled {n} files into {out}")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "dist", "bot"))
    bundle(ap.parse_args().out)
