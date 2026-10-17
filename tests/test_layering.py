import ast
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parents[1] / "strategylab" / "core"
PACKAGE_DIR = Path(__file__).resolve().parents[1] / "strategylab"
FORBIDDEN = ("from strategylab.drivers", "import strategylab.drivers")
ALLOWED_REASONS = {"stop", "take_profit", "limit", "market"}


def test_core_does_not_import_drivers() -> None:
    offenders = [
        path.name
        for path in sorted(CORE_DIR.glob("*.py"))
        if any(token in path.read_text(encoding="utf-8") for token in FORBIDDEN)
    ]
    assert offenders == []


def _fill_reason_literals(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()

    def collect(node: ast.AST) -> None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.add(node.value)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            else:
                continue
            if name not in ("Fill", "_make_fill"):
                continue
            for keyword in node.keywords:
                if keyword.arg == "reason":
                    collect(keyword.value)
            reason_index = 4 if name == "_make_fill" else 5
            if len(node.args) > reason_index:
                collect(node.args[reason_index])
        elif isinstance(node, ast.Assign):
            if any(
                isinstance(target, ast.Name) and target.id == "reason"
                for target in node.targets
            ):
                for child in ast.walk(node.value):
                    collect(child)
    return found


def test_fill_reasons_are_exactly_stop_take_profit_limit_market() -> None:
    found: set[str] = set()
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        found |= _fill_reason_literals(path)
    assert found == ALLOWED_REASONS