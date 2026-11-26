from __future__ import annotations

import ast
from pathlib import Path


def test_dashboard_uses_get_only_and_does_not_use_requests() -> None:
    source = Path(__file__).parents[1].joinpath("dashboard.py").read_text()
    tree = ast.parse(source)

    imported_requests = any(
        (isinstance(node, ast.Import) and any(alias.name == "requests" for alias in node.names))
        or (isinstance(node, ast.ImportFrom) and node.module == "requests")
        for node in ast.walk(tree)
    )
    assert not imported_requests
    assert "requests" not in source

    http_methods = {"get", "post", "put", "patch", "delete", "request", "send"}
    called_methods = {
        node.func.attr.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    } & http_methods
    assert called_methods <= {"get"}, f"Dashboard contains non-GET HTTP calls: {called_methods - {'get'}}"
    assert "httpx.get(" in source
