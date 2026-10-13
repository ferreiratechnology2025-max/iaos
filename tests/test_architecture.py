"""Architecture tests (AST).

KNOWN LIMIT: these catch accidental violations only. Python cannot stop
hostile code from calling AuthorizedAction(...) directly or importing around
the checks. Treat them as a lint of intent, not a security boundary.
"""
import ast
import dataclasses
import inspect
import pathlib

import pytest

import governance
from governance import budget, canonicalize, evidence
from governance.contract import CanonicalAction, Proposal
from governance.evidence import InMemoryEvidenceStore
from governance.policy import Policy

PKG = pathlib.Path(governance.__file__).parent
FILES = sorted(PKG.glob("*.py"))


def _tree(name):
    return ast.parse((PKG / name).read_text())


def _imports(tree):
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            out.add(("." * node.level) + (node.module or ""))
            out.update(a.name for a in node.names)
    return out


def test_oracle_imports_nothing_from_governance():
    for name in _imports(_tree("oracle.py")):
        assert "governance" not in name
        assert not name.startswith(".")
    assert not {"policy", "risk", "approval", "budget", "contract"} & _imports(_tree("oracle.py"))


def test_adapter_has_no_policy_imports():
    imported = {i.lstrip(".") for i in _imports(_tree("aep_adapter.py"))}
    for forbidden in ("policy", "risk", "approval", "budget", "authorization", "canonicalize"):
        assert forbidden not in imported, forbidden


def test_authorized_action_constructed_only_in_authorization():
    for path in FILES:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)
                if name == "AuthorizedAction":
                    assert path.name == "authorization.py", path.name


def test_forbidden_names_absent():
    for path in FILES:
        text = path.read_text()
        for word in ("CapabilityToken", "_FACTORY_SECRET", "max_external_calls", "max_actions_per_intent"):
            assert word not in text, (path.name, word)


def test_budget_only_preexec_fields():
    assert {f.name for f in dataclasses.fields(budget.Budget)} == {"forbidden_action_classes"}


def test_evidence_store_is_append_only_by_api():
    public = {n for n, _ in inspect.getmembers(InMemoryEvidenceStore, inspect.isfunction) if not n.startswith("_")}
    assert public == {"append", "events", "verify_chain"}


def test_evidence_events_are_returned_as_copies():
    store = InMemoryEvidenceStore()
    store.append(evidence.Event(evidence.EventKind.AUTHORIZATION, "i", {"final_decision": "DENY"}))
    store.events()[0]["final_decision"] = "ALLOW"
    assert store.events()[0]["final_decision"] == "DENY"
    assert store.verify_chain()


def test_policy_takes_canonical_only():
    p = Policy("/tmp")
    with pytest.raises(TypeError):
        p.evaluate(Proposal("i", "fs.read", {"path": "a"}))
    hints = inspect.signature(Policy.evaluate).parameters
    assert "canon" in hints


def _calls(node):
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            yield (f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", ""))


PATH_RESOLVERS = {"realpath", "lstat", "stat", "abspath", "exists", "isfile", "readlink", "resolve"}


def test_resolution_is_open_first_documented():
    assert "open-first" in inspect.getsource(canonicalize)
    assert "Not covered" in (PKG / "handle_binding.py").read_text()


def test_bind_path_never_resolves_or_stats_before_the_open():
    tree = _tree("canonicalize.py")
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        for fn in (n for n in cls.body if isinstance(n, ast.FunctionDef)):
            if fn.name in ("bind", "canonicalize", "release"):
                # canonicalize() may call resolver.resolve() only for actions that bind no identity
                banned = PATH_RESOLVERS - ({"resolve"} if fn.name == "canonicalize" else set())
                assert not (banned & set(_calls(fn))), (cls.name, fn.name)
    tree = _tree("handle_binding.py")
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in ("open", "take", "release"):
            assert not (PATH_RESOLVERS & set(_calls(node))), node.name


def test_executor_never_touches_a_path():
    banned = PATH_RESOLVERS | {"open", "fdopen", "listdir", "scandir"}
    assert not (banned & set(_calls(_tree("aep_executors.py"))))
    assert not (banned & set(_calls(_tree("aep_runtime.py"))))


def test_raw_handle_never_in_authority_types():
    from governance.contract import AuthorizedAction

    names = {f.name for f in dataclasses.fields(AuthorizedAction)}
    assert "resource_binding_id" in names
    assert not {n for n in names if "handle" in n or n in ("fd", "binding")}


def test_ctypes_and_native_open_only_in_handle_binding():
    for path in FILES:
        if path.name == "handle_binding.py":
            continue
        imps = {i.split(".")[0] for i in _import_names(path)}
        assert not ({"ctypes", "msvcrt"} & imps), path.name
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "os":
                    assert node.func.attr not in {"open", "fdopen"}, path.name


def test_oracle_blind_spot_documented():
    assert "BLIND SPOT" in (PKG / "oracle.py").read_text()


# --- A9: the executor boundary is reachable only through the adapter ----------

def _import_names(path):
    return {i.lstrip(".") for i in _imports(ast.parse(path.read_text()))}


def test_a9_only_runtime_and_adapter_import_executors():
    for path in FILES:
        if path.name in ("aep_runtime.py", "aep_adapter.py", "aep_executors.py", "bound_read.py"):
            continue
        assert "aep_executors" not in _import_names(path), path.name


def test_a9_only_adapter_imports_runtime():
    for path in FILES:
        if path.name in ("aep_adapter.py", "aep_runtime.py", "bound_read.py"):
            continue
        assert "aep_runtime" not in _import_names(path), path.name


def test_a9_executor_run_called_only_from_runtime():
    for path in FILES:
        if path.name in ("aep_runtime.py", "aep_executors.py"):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr != "run", path.name


def test_a9_real_filesystem_access_only_in_executors():
    for path in FILES:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "open", path.name


def test_no_dynamic_fallback_in_product_code():
    banned_calls = {"eval", "exec", "compile", "__import__"}
    banned_imports = {"importlib", "subprocess", "runpy", "pickle", "marshal", "socket"}
    for path in FILES:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in banned_calls, (path.name, node.func.id)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "os":
                    assert node.func.attr not in {"system", "popen", "spawnl", "execv"}, path.name
        assert not (banned_imports & {i.split(".")[0] for i in _import_names(path)}), path.name


def test_registry_has_no_public_mutators():
    from governance.aep_executors import AEPExecutorRegistry

    public = {n for n, _ in inspect.getmembers(AEPExecutorRegistry, inspect.isfunction) if not n.startswith("_")}
    assert public == {"lookup", "actions", "effects"}


def test_registered_executors_are_exactly_the_two_read_actions():
    from governance.aep_executors import default_registry

    assert default_registry().actions() == ("fs.read", "fs.read_bytes")
