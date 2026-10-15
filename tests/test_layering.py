from pathlib import Path

CORE_DIR = Path(__file__).resolve().parents[1] / "strategylab" / "core"
FORBIDDEN = ("from strategylab.drivers", "import strategylab.drivers")


def test_core_does_not_import_drivers() -> None:
    offenders = [
        path.name
        for path in sorted(CORE_DIR.glob("*.py"))
        if any(token in path.read_text(encoding="utf-8") for token in FORBIDDEN)
    ]
    assert offenders == []