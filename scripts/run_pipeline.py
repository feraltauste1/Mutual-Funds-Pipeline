"""Alternativa al comando instalado: python scripts/run_pipeline.py run."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tr_pipeline.cli import main  # noqa: E402

if __name__ == "__main__":
    main()

