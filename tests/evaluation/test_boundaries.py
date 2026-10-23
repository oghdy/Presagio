"""Code boundaries the auditor runs as tests (design 9-2 Q4, 9-3 T6 module part and T9).

- T9: no random split or cross-validation in features, models or evaluation (AST walk of
  imports, attribute uses and calls). `.sample(frac=1)` is banned only without
  replace=True, so the game bootstrap (resampling with replacement) stays allowed (D-049).
- Q4: the closing view odds_closing_lines is named nowhere in features or models, and those
  packages never import presagio.evaluation (so a constant cannot smuggle the name in).
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "presagio"
SCANNED = ("features", "models", "evaluation")
FORBIDDEN_NAMES = frozenset(
    {
        "train_test_split",
        "KFold",
        "StratifiedKFold",
        "GroupKFold",
        "ShuffleSplit",
        "cross_val_score",
        "cross_validate",
        "GridSearchCV",
        "RandomizedSearchCV",
    }
)
CV_MODULES = ("lightgbm", "xgboost")
ALLOWED_MODEL_SELECTION = frozenset({"TimeSeriesSplit"})


def _is_true(node: ast.expr) -> bool:
    """A literal True."""
    return isinstance(node, ast.Constant) and node.value is True


def split_violations(source: str, filename: str = "<string>") -> list[str]:
    """T9 findings in one module's source (empty list = clean)."""
    tree = ast.parse(source, filename)
    found: list[str] = []
    cv_aliases: set[str] = set()
    for node in ast.walk(tree):
        line = f"{filename}:{getattr(node, 'lineno', '?')}"
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in CV_MODULES:
                    cv_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = {a.name for a in node.names}
            if module.startswith("sklearn.model_selection") and names - ALLOWED_MODEL_SELECTION:
                found.append(f"{line} imports {sorted(names - ALLOWED_MODEL_SELECTION)}")
            if module.split(".")[0] in CV_MODULES and "cv" in names:
                found.append(f"{line} imports {module}.cv")
            for name in names & FORBIDDEN_NAMES:
                found.append(f"{line} imports {name}")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            found.append(f"{line} uses {node.id}")
        elif isinstance(node, ast.Attribute):
            if node.attr in FORBIDDEN_NAMES:
                found.append(f"{line} uses .{node.attr}")
            value = node.value
            if node.attr == "cv" and isinstance(value, ast.Name) and value.id in cv_aliases:
                found.append(f"{line} uses {value.id}.cv")
        if isinstance(node, ast.Call):
            keywords = {k.arg: k.value for k in node.keywords if k.arg}
            if "shuffle" in keywords and _is_true(keywords["shuffle"]):
                found.append(f"{line} passes shuffle=True")
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "sample":
                frac = keywords.get("frac")
                full = isinstance(frac, ast.Constant) and frac.value in (1, 1.0)
                if full and not ("replace" in keywords and _is_true(keywords["replace"])):
                    found.append(f"{line} shuffles with .sample(frac=1)")
    return found


def _modules(*packages: str) -> list[Path]:
    """Python files of the given presagio subpackages."""
    files: list[Path] = []
    for package in packages:
        files += sorted((SRC / package).rglob("*.py"))
    return files


@pytest.mark.parametrize(
    "snippet",
    [
        "from sklearn.model_selection import KFold",
        "from sklearn.model_selection import train_test_split as tts",
        "import sklearn.model_selection as ms\nms.GridSearchCV()",
        "from sklearn.model_selection import cross_val_score",
        "import lightgbm as lgb\nlgb.cv({}, None)",
        "import xgboost\nxgboost.cv({}, None)",
        "from lightgbm import cv",
        "df.sample(frac=1)",
        "df.sample(frac=1.0, random_state=0)",
        "df.sample(frac=1, replace=False)",
        "split(x, shuffle=True)",
    ],
)
def test_checker_catches_each_forbidden_pattern(snippet: str) -> None:
    """The T9 checker is not vacuous: every banned form is reported."""
    assert split_violations(snippet)


@pytest.mark.parametrize(
    "snippet",
    [
        "from sklearn.model_selection import TimeSeriesSplit",
        "df.sample(frac=1, replace=True)",  # bootstrap resample (D-049 follow-up)
        "df.sample(n=5)",
        "LogisticRegression(random_state=0)",
        "np.random.default_rng(0).integers(0, n, size=(1000, n))",
        "split(x, shuffle=False)",
    ],
)
def test_checker_allows_time_splits_and_resampling(snippet: str) -> None:
    """Time-ordered splits, seeded estimators and with-replacement resampling are fine."""
    assert split_violations(snippet) == []


def test_t9_no_random_split_or_cross_validation_in_ml_code() -> None:
    """T9 over src/presagio/features, models and evaluation."""
    files = _modules(*SCANNED)
    assert any(p.parent.name == "evaluation" for p in files)
    found = [v for p in files for v in split_violations(p.read_text(), str(p.relative_to(SRC)))]
    assert found == []


def test_q4_closing_view_is_not_named_in_features_or_models() -> None:
    """grep odds_closing_lines src/presagio/features src/presagio/models -> 0 lines."""
    hits = [
        f"{p.relative_to(SRC)}:{n}"
        for p in _modules("features", "models")
        for n, line in enumerate(p.read_text().splitlines(), 1)
        if "odds_closing_lines" in line
    ]
    assert hits == []


def test_q4_closing_view_is_read_only_by_the_clv_module() -> None:
    """Inside evaluation only clv.py holds the SQL that reads the view."""
    readers = [p.name for p in _modules("evaluation") if "FROM odds_closing_lines" in p.read_text()]
    assert readers == ["clv.py"]


def test_t6_features_and_models_never_import_evaluation() -> None:
    """AST import check: no `import presagio.evaluation` / `from presagio.evaluation ...`."""
    found = []
    for path in _modules("features", "models"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            found += [
                f"{path.relative_to(SRC)}: {n}"
                for n in names
                if n.startswith("presagio.evaluation")
            ]
    assert found == []
