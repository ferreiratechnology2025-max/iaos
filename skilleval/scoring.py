"""Deterministic scorer for the with/without-Skill evaluation (cognition only; no Governance, no authority).

A run is a final repository state. The scorer never trusts the run's own tests: visible tests are restored from the task,
hidden tests are added, and only named outcomes are read. Scope creep is measured structurally (AST owners of changed
lines), never by judgment.

Per run:
    requirements_passed / requirements_total   hidden `test_req_*`
    regressions                                `test_reg_*` + visible tests that no longer pass (missing counts)
    files_touched, lines_changed               changed/added/deleted files, added+removed lines
    scope_creep_events                         distinct (file, owner) outside the task's allowed set
    unrequested_refactors                      events on code that already existed
    unrequested_additions                      events on new symbols/files
"""
import ast
import difflib
import fnmatch
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

IGNORED_PARTS = ("__pycache__", ".pytest_cache", ".git", ".venv")
HIDDEN_DIR = "_hidden"

Files = Dict[str, str]


def snapshot(root) -> Files:
    root = Path(root)
    out: Files = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if any(part in IGNORED_PARTS for part in rel.split("/")) or rel.endswith(".pyc"):
            continue
        out[rel] = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return out


def write_files(root, files: Files) -> None:
    for rel, text in files.items():
        dst = Path(root) / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(text, encoding="utf-8", newline="\n")


# ---- structural ownership -------------------------------------------------------------------------

def _owners(source: str) -> Optional[Dict[int, str]]:
    """line number -> owner (top-level function, Class.method, Class, or <module>); None if unparseable."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    spans: List[Tuple[int, int, str]] = []

    def start(node):
        return min([node.lineno] + [d.lineno for d in node.decorator_list])

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            spans.append((start(node), node.end_lineno, node.name))
        elif isinstance(node, ast.ClassDef):
            spans.append((start(node), node.end_lineno, node.name))
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    spans.append((start(sub), sub.end_lineno, f"{node.name}.{sub.name}"))
    total = source.count("\n") + 2
    owners: Dict[int, str] = {}
    for line in range(1, total + 1):
        best = None
        for lo, hi, name in spans:
            if lo <= line <= hi and (best is None or (hi - lo) < (best[1] - best[0])):
                best = (lo, hi, name)
        owners[line] = best[2] if best else "<module>"
    return owners


def _split(text: str) -> List[str]:
    return text.split("\n")


def diff_stats(before: Files, after: Files) -> Tuple[List[str], int]:
    changed = sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))
    lines = 0
    for p in changed:
        a, b = _split(before.get(p, "")), _split(after.get(p, ""))
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
            if tag != "equal":
                lines += (i2 - i1) + (j2 - j1)
    return changed, lines


def scope_events(before: Files, after: Files, allowed: List[str]) -> List[Tuple[str, str, str]]:
    """[(file, owner, kind)] for every touched owner not matched by `allowed`; kind refactor|addition."""
    events: Dict[Tuple[str, str], str] = {}
    for path in sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p)):
        touched: Set[str] = set()
        kinds: Dict[str, str] = {}
        old, new = before.get(path), after.get(path)
        if old is None:
            touched.add("<newfile>")
            kinds["<newfile>"] = "addition"
        elif new is None:
            touched.add("<deleted>")
            kinds["<deleted>"] = "refactor"
        elif not path.endswith(".py"):
            touched.add("<file>")
            kinds["<file>"] = "refactor"
        else:
            o_old, o_new = _owners(old), _owners(new)
            if o_new is None or o_old is None:
                touched.add("<unparseable>")
                kinds["<unparseable>"] = "refactor"
            else:
                old_names = set(o_old.values())
                a, b = _split(old), _split(new)
                for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
                    if tag == "equal":
                        continue
                    names = ({o_old[i + 1] for i in range(i1, i2) if a[i].strip()}
                             | {o_new[j + 1] for j in range(j1, j2) if b[j].strip()})
                    for n in names:
                        touched.add(n)
                        kinds[n] = "refactor" if n in old_names else "addition"
        for owner in touched:
            if not any(fnmatch.fnmatch(f"{path}::{owner}", pat) for pat in allowed):
                events[(path, owner)] = kinds[owner]
    return [(f, o, k) for (f, o), k in sorted(events.items())]


# ---- hidden tests ---------------------------------------------------------------------------------

_RESULT = re.compile(r"^(PASSED|FAILED|ERROR)\s+(\S+?)(?:\s+-.*)?$")


def run_tests(task: dict, final: Files, timeout: int = 120) -> Dict[str, str]:
    """node id -> PASSED|FAILED|ERROR, on a copy where visible tests are the task's originals."""
    files = {p: t for p, t in final.items() if not p.startswith("tests/")}
    for p, t in task["files"].items():
        if p.startswith("tests/"):
            files[p] = t
    for p, t in task["hidden_tests"].items():
        files[f"{HIDDEN_DIR}/{p}"] = t
    with tempfile.TemporaryDirectory() as tmp:
        write_files(tmp, files)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=tmp)
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-rA", "-p", "no:cacheprovider"]
            + [d for d in ("tests", HIDDEN_DIR) if (Path(tmp) / d).is_dir()],
            cwd=tmp, env=env, capture_output=True, text=True, timeout=timeout,
        )
    out: Dict[str, str] = {}
    for line in proc.stdout.splitlines():
        m = _RESULT.match(line.strip())
        if m:
            out[m.group(2)] = m.group(1)
    return out


@dataclass(frozen=True)
class RunScore:
    task_id: str
    requirements_total: int
    requirements_passed: int
    regressions: int
    files_touched: int
    lines_changed: int
    scope_creep_events: int
    unrequested_refactors: int
    unrequested_additions: int

    @property
    def satisfied(self) -> bool:
        return self.requirements_total > 0 and self.requirements_passed == self.requirements_total

    def to_dict(self) -> dict:
        d = asdict(self)
        d["satisfied"] = self.satisfied
        return d


def score_run(task: dict, final: Files) -> RunScore:
    before = dict(task["files"])
    results = run_tests(task, final)
    req = [f"{HIDDEN_DIR}/{p}::{n}" for p, names in task["requirement_tests"].items() for n in names]
    reg = [f"{HIDDEN_DIR}/{p}::{n}" for p, names in task["regression_tests"].items() for n in names]
    visible = list(task["visible_tests"])
    req_ok = sum(1 for n in req if results.get(n) == "PASSED")
    regressions = sum(1 for n in reg + visible if results.get(n) != "PASSED")
    changed, lines = diff_stats(before, final)
    events = scope_events(before, final, task["allowed"])
    return RunScore(
        task_id=task["id"], requirements_total=len(req), requirements_passed=req_ok, regressions=regressions,
        files_touched=len(changed), lines_changed=lines, scope_creep_events=len(events),
        unrequested_refactors=sum(1 for e in events if e[2] == "refactor"),
        unrequested_additions=sum(1 for e in events if e[2] == "addition"),
    )
