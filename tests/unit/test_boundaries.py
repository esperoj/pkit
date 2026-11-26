"""The modular layering as an executable contract.

One package, four liftable parts, one direction of dependencies:

    lib       kernel: imports stdlib and pkit.version only
    wayback   standalone SDK: lib (+ pkit.version)
    meta      records data layer: lib (+ pkit.version)
    archive   CAS + location log: lib, wayback and meta (+ pkit.version)

No tool imports a peer except archive, the top composer, reaching down to
meta (the records database holds the location log) and wayback (captures
and CDX listings go through its client) — and nothing imports upward.
Keeping imports inside these rules is what makes each part independently
liftable out of pkit — the README's layering section is this test in prose.
"""

import ast
from pathlib import Path

PKIT = Path(__file__).resolve().parents[2] / "pkit"

# What each package may import from outside itself (own subpackages and a
# bare `pkit` are always fine; relative imports are always fine).
ALLOWED = {
    "lib": frozenset({"pkit.version"}),
    "meta": frozenset({"pkit.version", "pkit.lib"}),
    "wayback": frozenset({"pkit.version", "pkit.lib"}),
    "archive": frozenset({"pkit.version", "pkit.lib", "pkit.meta", "pkit.wayback"}),
    "": frozenset({"pkit.version"}),  # pkit/ root modules other than the wiring
}
# The BusyBox dispatcher and module entry points wire everything together.
WIRING = {"cli.py", "__main__.py"}


def _prefix(name: str) -> str:
    """One import target as its pkit-namespace prefix ("pkit.lib")."""
    parts = name.split(".")
    return ".".join(parts[:2]) if parts[0] == "pkit" and len(parts) > 1 else parts[0]


def _pkit_imports(module: ast.Module) -> set[str]:
    """Every absolute pkit import in one parsed module, as prefixes."""
    found: set[str] = set()
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "pkit":
                    found.add(_prefix(alias.name))
        elif (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module
            and node.module.split(".")[0] == "pkit"
        ):
            found.add(_prefix(node.module))
            if node.module == "pkit":
                for alias in node.names:  # from pkit import archive, meta, ...
                    found.add(_prefix(f"pkit.{alias.name}"))
    return found


def _violations() -> list[str]:
    issues: list[str] = []
    for path in sorted(PKIT.rglob("*.py")):
        rel = path.relative_to(PKIT)
        parts = rel.with_suffix("").parts
        package = "" if len(parts) == 1 else parts[0]
        if rel.name in WIRING and len(parts) == 1:
            continue
        allowed = ALLOWED.get(package, frozenset()) | {f"pkit.{package}"} | {"pkit"}
        illegal = sorted(_pkit_imports(ast.parse(path.read_text(encoding="utf-8"))) - allowed)
        if illegal:
            issues.append(f"{rel}: imports {illegal}")
    return issues


def test_pkit_layering():
    """Every module imports only what its layer allows."""
    assert not _violations(), "layering violations:\n" + "\n".join(_violations())


def test_pkit_version_is_a_leaf():
    """pkit.version imports nothing from pkit — the kernel may lean on it."""
    assert _pkit_imports(ast.parse((PKIT / "version.py").read_text(encoding="utf-8"))) == set()
