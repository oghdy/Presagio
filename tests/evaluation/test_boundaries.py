"""Code boundaries the auditor runs as tests (design 9-2 Q4, 9-3 T6 module part and T9).

- T9: no random split, shuffle or cross-validation in features, models or evaluation (AST
  walk of imports, attribute uses and calls). Any call named shuffle / permutation /
  permuted is banned whatever the receiver (np.random, a Generator, random, sklearn.utils),
  so is random.sample, and `.sample(...)` is allowed only with a literal replace=True, so
  the game bootstrap (resampling with replacement) stays allowed (D-049, D-065 item 2).
- Q4: the closing view odds_closing_lines is named nowhere in features or models, and those
  packages never import presagio.evaluation (so a constant cannot smuggle the name in).
- T9 extension (P-033 criterion 5 (d), widened by D-065 item 2 after the P-036 audit showed
  five forms that reached the runner's locals): features, models and evaluation never reach
  around the frozen PredictionContext through stack frames or the object graph: no frame
  attribute (tb_frame, f_back, f_locals, f_globals, gi_frame, cr_frame, ag_frame) on any
  receiver; no sys._getframe / _current_frames / exc_info, inspect.currentframe / stack /
  getouterframes / trace / getframeinfo / getinnerframes, traceback.walk_stack /
  extract_stack / walk_tb, logging.currentframe, gc at all, ctypes; none of those names or
  modules as a string literal in getattr, __dict__[...], vars(...)[...] or sys.modules[...];
  and no dynamic import whose module is not a constant string (or is a banned module).

The AST check is a tripwire, not a proof (D-065 item 3): a name assembled at run time
(getattr(sys, "_get" + "frame")) is not caught. Non-constant getattr stays allowed because
ordinary code uses it (fv1.py sums stat columns with getattr(own, name)).
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
# Calls that reorder rows, banned whatever the receiver (D-065 item 2).
SHUFFLE_CALLS = frozenset({"shuffle", "permutation", "permuted"})
# Stack-frame and object-graph introspection a predictor could use to reach the runner's
# game list (P-032 review 2: a Python limit the frozen context cannot close by itself;
# widened by D-065 item 2 with the frame sources the P-036 audit ran).
INTROSPECTION = {
    "sys": frozenset({"_getframe", "_current_frames", "exc_info"}),
    "inspect": frozenset(
        {"currentframe", "stack", "getouterframes", "trace", "getframeinfo", "getinnerframes"}
    ),
    "gc": frozenset({"get_objects", "get_referrers", "get_referents"}),
    "traceback": frozenset({"walk_stack", "extract_stack", "walk_tb"}),
    "logging": frozenset({"currentframe"}),
}
# Frame-object attributes: whatever produced the frame, its locals are reached through these.
FRAME_ATTRIBUTES = frozenset(
    {"tb_frame", "f_back", "f_locals", "f_globals", "gi_frame", "cr_frame", "ag_frame"}
)
# Modules banned outright (any import of them).
BANNED_MODULES = frozenset({"ctypes", "gc"})
# Modules whose name as a string literal in sys.modules[...] (or a __dict__ / vars / getattr
# lookup) is a violation (D-065 item 2).
LITERAL_BANNED_MODULES = frozenset({"gc", "ctypes", "inspect", "traceback"})
# Names whose string literal in getattr / __dict__[...] / vars(...)[...] is a violation.
BANNED_LITERALS = (
    frozenset().union(*INTROSPECTION.values()) | FRAME_ATTRIBUTES | LITERAL_BANNED_MODULES
)
DYNAMIC_IMPORTERS = frozenset({"__import__", "import_module"})


def _is_true(node: ast.expr) -> bool:
    """A literal True."""
    return isinstance(node, ast.Constant) and node.value is True


def _string(node: ast.expr | None) -> str | None:
    """The value of a constant string node, else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _call_name(func: ast.expr) -> str | None:
    """f for `f(...)` and `x.f(...)`, else None."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def split_violations(source: str, filename: str = "<string>") -> list[str]:
    """T9 findings in one module's source (empty list = clean)."""
    tree = ast.parse(source, filename)
    found: list[str] = []
    cv_aliases: set[str] = set()
    random_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in CV_MODULES:
                    cv_aliases.add(alias.asname or alias.name)
                if alias.name == "random":
                    random_aliases.add(alias.asname or alias.name)
    for node in ast.walk(tree):
        line = f"{filename}:{getattr(node, 'lineno', '?')}"
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = {a.name for a in node.names}
            if module.startswith("sklearn.model_selection") and names - ALLOWED_MODEL_SELECTION:
                found.append(f"{line} imports {sorted(names - ALLOWED_MODEL_SELECTION)}")
            if module.split(".")[0] in CV_MODULES and "cv" in names:
                found.append(f"{line} imports {module}.cv")
            for name in names & FORBIDDEN_NAMES:
                found.append(f"{line} imports {name}")
            for name in names & SHUFFLE_CALLS:
                found.append(f"{line} imports {module}.{name}")
            if module == "random" and names & {"sample", "*"}:
                found.append(f"{line} imports random.sample")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            found.append(f"{line} uses {node.id}")
        elif isinstance(node, ast.Attribute):
            if node.attr in FORBIDDEN_NAMES:
                found.append(f"{line} uses .{node.attr}")
            if node.attr in SHUFFLE_CALLS:
                found.append(f"{line} uses .{node.attr}")
            value = node.value
            if node.attr == "cv" and isinstance(value, ast.Name) and value.id in cv_aliases:
                found.append(f"{line} uses {value.id}.cv")
            if node.attr == "sample" and isinstance(value, ast.Name) and value.id in random_aliases:
                found.append(f"{line} uses random.sample")
        if isinstance(node, ast.Call):
            keywords = {k.arg: k.value for k in node.keywords if k.arg}
            if "shuffle" in keywords and _is_true(keywords["shuffle"]):
                found.append(f"{line} passes shuffle=True")
            func = node.func
            if isinstance(func, ast.Name) and func.id in SHUFFLE_CALLS:
                found.append(f"{line} calls {func.id}()")
            with_replacement = "replace" in keywords and _is_true(keywords["replace"])
            if isinstance(func, ast.Attribute) and func.attr == "sample" and not with_replacement:
                found.append(f"{line} samples without replace=True (.sample)")
    return found


def introspection_violations(source: str, filename: str = "<string>") -> list[str]:
    """Frame / object-graph introspection and ctypes findings in one module (empty = clean)."""
    tree = ast.parse(source, filename)
    aliases: dict[str, str] = {}  # local name -> introspection module
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in INTROSPECTION:
                    aliases[alias.asname or alias.name] = alias.name
    found: list[str] = []
    for node in ast.walk(tree):
        line = f"{filename}:{getattr(node, 'lineno', '?')}"
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in BANNED_MODULES:
                    found.append(f"{line} imports {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").split(".")[0]
            if module in BANNED_MODULES:
                found.append(f"{line} imports from {module}")
            if module in INTROSPECTION:
                for alias in node.names:
                    if alias.name == "*" or alias.name in INTROSPECTION[module]:
                        found.append(f"{line} imports {module}.{alias.name}")
        elif isinstance(node, ast.Attribute):
            if node.attr in FRAME_ATTRIBUTES:
                found.append(f"{line} uses frame attribute .{node.attr}")
            if isinstance(node.value, ast.Name):
                module = aliases.get(node.value.id)
                if module is not None and node.attr in INTROSPECTION[module]:
                    found.append(f"{line} uses {module}.{node.attr}")
        elif isinstance(node, ast.Subscript):
            key = _string(node.slice)
            value = node.value
            is_dict = isinstance(value, ast.Attribute) and value.attr == "__dict__"
            is_vars = (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == "vars"
            )
            is_modules = (isinstance(value, ast.Attribute) and value.attr == "modules") or (
                isinstance(value, ast.Name) and value.id == "modules"
            )
            if key is not None and (is_dict or is_vars) and key in BANNED_LITERALS:
                found.append(f"{line} looks up {key!r} by string")
            if key is not None and is_modules and key.split(".")[0] in LITERAL_BANNED_MODULES:
                found.append(f"{line} reaches module {key!r} through sys.modules")
        elif isinstance(node, ast.Call):
            name = _call_name(node.func)
            target = node.args[0] if node.args else None
            if name == "getattr" and len(node.args) >= 2:
                attr = _string(node.args[1])
                if attr is not None and attr in BANNED_LITERALS:
                    found.append(f"{line} getattr {attr!r} by string")
            if name in DYNAMIC_IMPORTERS:
                module_name = _string(target)
                if module_name is None:
                    found.append(f"{line} imports a non-constant module name dynamically")
                elif module_name.split(".")[0] in BANNED_MODULES | set(INTROSPECTION):
                    found.append(f"{line} imports {module_name!r} dynamically")
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
        "import sklearn.model_selection as ms\nms.ShuffleSplit",
        "from sklearn.model_selection import cross_val_score",
        "import lightgbm as lgb\nlgb.cv({}, None)",
        "import xgboost\nxgboost.cv({}, None)",
        "from lightgbm import cv",
        "df.sample(frac=1)",
        "df.sample(frac=1.0)",
        "df.sample(frac=1.0, random_state=0)",
        "df.sample(frac=1, replace=False)",
        "split(x, shuffle=True)",
        # D-065 item 2: shuffle / permutation / permuted whatever the receiver
        "import numpy as np\nnp.random.shuffle(idx)",
        "import numpy as np\nnp.random.permutation(idx)",
        "import numpy as np\nrng = np.random.default_rng(0)\nrng.permutation(idx)",
        "import numpy as np\nrng = np.random.default_rng(0)\nrng.shuffle(idx)",
        "import numpy as np\nrng = np.random.default_rng(0)\nrng.permuted(x, axis=0)",
        "import numpy as np\nX.iloc[np.random.permutation(len(X))]",
        "import random\nrandom.shuffle(rows)",
        "import sklearn.utils\nsklearn.utils.shuffle(X)",
        "from sklearn.utils import shuffle",
        "from sklearn.utils import shuffle as sk_shuffle\nsk_shuffle(X, y)",
        "from numpy.random import permutation",
        "shuffle(X)",
        # random.sample, and .sample(...) without a literal replace=True
        "import random\nrandom.sample(rows, 3)",
        "import random as r\nr.sample(rows, k=3)",
        "from random import sample",
        "from random import sample as pick\npick(rows, 3)",
        "df.sample(frac=0.999)",
        "df.sample(n=len(df))",
        "df.sample(n=5)",
        "df.sample(frac=1, replace=flag)",
    ],
)
def test_checker_catches_each_forbidden_pattern(snippet: str) -> None:
    """The T9 checker is not vacuous: every banned form is reported."""
    assert split_violations(snippet)


# Ordinary code that must stay clean for both checkers (D-065 item 2 "allowed" and
# "excluded"; the forms marked with a file exist in src/presagio today, P-045 criterion 3).
ALLOWED = [
    "from sklearn.model_selection import TimeSeriesSplit",
    "from sklearn.model_selection import TimeSeriesSplit\nTimeSeriesSplit(n_splits=3)",
    "df.sample(frac=1, replace=True)",  # bootstrap resample (D-049 follow-up)
    "df.sample(n=len(df), replace=True)",
    "LogisticRegression(random_state=0)",
    "split(x, shuffle=False)",
    "import numpy as np\nnp.random.default_rng(0).integers(0, n, size=(1000, n))",
    "import numpy as np\nnp.random.default_rng(seed).integers(0, n, size=(b, n))",  # metrics.py
    "import inspect\ninspect.signature(f)",
    "import numpy as np\nnp.stack([a, b])",
    "import sys\nsys.argv",
    "frames.stack()",
    # features/cli.py, evaluation/cli.py: a keyword argument, not sys.exc_info
    "import logging\nlogger = logging.getLogger(__name__)\n"
    "logger.debug('features compute failed', exc_info=True)",
    # store.py, experiments.py, walkforward.py, fv1.py: no banned name as the literal
    "getattr(predictor, 'trains', False)",
    "getattr(predictor, 'spec', None)",
    "getattr(predictor, 'uses_results', True)",
    "getattr(values, 'total_sd', None)",
    "getattr(own, name)",
    # clv.py
    "Clv(**base.__dict__)",
    "Clv(**{**base.__dict__, **common})",
    "import importlib\nimportlib.import_module('presagio.models')",
    # D-065 "excluded": module globals and code objects do not reach the runner's locals
    "(lambda: 0).__globals__",
    "f.__closure__",
    "f.__code__.co_consts",
    "import pickle\npickle.loads(b)",
    "import inspect\ninspect.getmembers(obj)",
]


@pytest.mark.parametrize("snippet", ALLOWED)
def test_checker_allows_time_splits_and_resampling(snippet: str) -> None:
    """Time-ordered splits, seeded estimators and with-replacement resampling are fine."""
    assert split_violations(snippet) == []


def test_t9_no_random_split_or_cross_validation_in_ml_code() -> None:
    """T9 over src/presagio/features, models and evaluation."""
    files = _modules(*SCANNED)
    assert any(p.parent.name == "evaluation" for p in files)
    found = [v for p in files for v in split_violations(p.read_text(), str(p.relative_to(SRC)))]
    assert found == []


@pytest.mark.parametrize(
    "snippet",
    [
        "import sys\nsys._getframe(1).f_back",
        "import sys as s\ns._getframe()",
        "from sys import _getframe",
        "import inspect\ninspect.currentframe()",
        "import inspect as i\ni.currentframe()",
        "import inspect\ninspect.stack()",
        "import inspect\ninspect.getouterframes(f)",
        "from inspect import stack",
        "from inspect import currentframe as cf",
        "import gc\ngc.get_objects()",
        "import gc\ngc.get_referrers(x)",
        "import gc\ngc.get_referents(x)",
        "from gc import get_objects",
        "import ctypes",
        "import ctypes\nctypes.cast(p, t)",
        "from ctypes import c_int",
        "import importlib\nimportlib.import_module('gc')",
        "import importlib\nimportlib.import_module('inspect').stack()",
        "__import__('ctypes')",
        "from sys import *",
        # (a) frame attributes whatever the receiver
        "try:\n    pass\nexcept Exception as e:\n    e.__traceback__.tb_frame.f_back",
        "import sys\nsys.exc_info()[2].tb_frame.f_back",
        "frame.f_back",
        "frame.f_locals.get('result')",
        "frame.f_globals",
        "generator.gi_frame.f_back",
        "coro.cr_frame.f_back",
        "agen.ag_frame",
        # (b) more frame sources, as module attribute or from-import
        "import sys\nsys._current_frames()",
        "from sys import _current_frames",
        "import sys\nsys.exc_info()",
        "from sys import exc_info",
        "import traceback\ntraceback.walk_stack(None)",
        "import traceback\ntraceback.extract_stack()",
        "import traceback as tb\ntb.walk_tb(t)",
        "from traceback import walk_stack",
        "from traceback import extract_stack as es",
        "import logging\nlogging.currentframe()",
        "from logging import currentframe",
        "import inspect\ninspect.trace()",
        "import inspect\ninspect.getframeinfo(f)",
        "import inspect\ninspect.getinnerframes(t)",
        "from inspect import getinnerframes",
        "from inspect import trace, getframeinfo",
        # (c) the banned names and modules as string literals
        "import sys\ngetattr(sys, '_getframe')(1)",
        "import sys\ngetattr(sys, '_current_frames')()",
        "getattr(frame, 'f_back', None)",
        "getattr(inspect_module, 'currentframe')()",
        "import sys\nsys.__dict__['_getframe'](1)",
        "obj.__dict__['f_locals']",
        "vars(sys)['_getframe'](1)",
        "vars(gc_module)['get_objects']()",
        "import sys\nsys.modules['gc'].get_objects()",
        "import sys\nsys.modules['inspect'].stack()",
        "import sys\nsys.modules['traceback'].walk_stack(None)",
        "import sys\nsys.modules['ctypes']",
        "from sys import modules\nmodules['gc']",
        # (d) a dynamic import whose module is not a constant string
        "__import__('g' + 'c').get_objects()",
        "__import__(name)",
        "import importlib\nimportlib.import_module(name)",
        "import importlib\nimportlib.import_module(f'{base}.x')",
        "import importlib\nimportlib.import_module(name='gc')",
        "import builtins\nbuiltins.__import__('gc').get_objects()",
        "import importlib\nimportlib.import_module('traceback')",
        # (e) gc is banned as a module
        "import gc\ngc.collect()",
        "import gc",
        "import gc as g",
        "from gc import collect",
    ],
)
def test_introspection_checker_catches_each_banned_form(snippet: str) -> None:
    """The (d) checker is not vacuous."""
    assert introspection_violations(snippet)


# The five forms the P-036 audit ran (rogue_predictor.py): each reached run_walk_forward's
# locals and scored 820 predictions with accuracy 1.0 under the old checker. The frame walk
# that follows each of them (f_locals / f_back) is left out on purpose, so each snippet is
# caught by its own form.
ROGUE_FORMS = {
    "getattr(sys, '_getframe')": (
        "import sys\n\ndef frame_getattr():\n    return getattr(sys, '_getframe')(0)\n"
    ),
    "sys._current_frames()": (
        "import sys\nimport threading\n\ndef frame_current_frames():\n"
        "    return sys._current_frames()[threading.get_ident()]\n"
    ),
    "e.__traceback__.tb_frame": (
        "def frame_traceback():\n    try:\n        raise RuntimeError('probe')\n"
        "    except RuntimeError as e:\n        return e.__traceback__.tb_frame\n"
    ),
    "sys.__dict__['_getframe']": (
        "import sys\n\ndef frame_sys_dict():\n    return sys.__dict__['_getframe'](0)\n"
    ),
    "__import__('g' + 'c').get_objects()": (
        "def games_via_gc():\n    for obj in __import__('g' + 'c').get_objects():\n"
        "        if type(obj).__name__ == 'WalkForwardResult':\n            return obj.games\n"
        "    return None\n"
    ),
}


@pytest.mark.parametrize("form", sorted(ROGUE_FORMS))
def test_introspection_checker_catches_the_audited_rogue_forms(form: str) -> None:
    """P-036 criterion 5 (a), D-065 item 2: all five audited bypasses are violations now."""
    assert introspection_violations(ROGUE_FORMS[form])


@pytest.mark.parametrize("snippet", ALLOWED)
def test_introspection_checker_allows_ordinary_code(snippet: str) -> None:
    """numpy.stack, sys.argv, inspect.signature, exc_info=True and plain getattr are fine."""
    assert introspection_violations(snippet) == []


def test_no_frame_or_object_graph_introspection_in_features_models_or_evaluation() -> None:
    """P-033 (d), D-065 item 2: src/presagio/features, models and evaluation use none of the
    banned forms."""
    files = _modules(*SCANNED)
    assert {"baselines.py", "walkforward.py", "fv1.py"} <= {p.name for p in files}
    found = [
        v for p in files for v in introspection_violations(p.read_text(), str(p.relative_to(SRC)))
    ]
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
