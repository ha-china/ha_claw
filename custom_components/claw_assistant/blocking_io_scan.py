from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent

BLOCKING_ATTRS = {
    "exists",
    "is_file",
    "is_dir",
    "stat",
    "resolve",
    "read_text",
    "write_text",
    "read_bytes",
    "write_bytes",
    "open",
    "mkdir",
    "unlink",
    "rmdir",
    "rename",
    "iterdir",
    "glob",
    "rglob",
}
BLOCKING_NAMES = {"open"}
BLOCKING_MODULE_CALLS = {
    ("subprocess", "run"),
    ("subprocess", "Popen"),
    ("sqlite3", "connect"),
    ("requests", "get"),
    ("requests", "post"),
    ("urllib.request", "urlopen"),
    ("time", "sleep"),
    ("shutil", "which"),
    ("importlib", "invalidate_caches"),
}


def _call_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        parts = [func.attr]
        value = func.value
        while isinstance(value, ast.Attribute):
            parts.append(value.attr)
            value = value.value
        if isinstance(value, ast.Name):
            parts.append(value.id)
        return ".".join(reversed(parts))
    return None


def _is_executor_call(call: ast.Call) -> bool:
    name = _call_name(call.func) or ""
    return name.endswith("async_add_executor_job")


def _blocking_call_label(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name) and func.id in BLOCKING_NAMES:
        return func.id
    if isinstance(func, ast.Attribute):
        attr = func.attr
        if attr in BLOCKING_ATTRS:
            return attr
        name = _call_name(func) or ""
        parts = name.rsplit(".", 1)
        if len(parts) == 2 and tuple(parts) in BLOCKING_MODULE_CALLS:
            return name
    return None


class DirectAsyncBlockingVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.hits: list[tuple[int, str]] = []
        self._executor_depth = 0

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_Call(self, node: ast.Call) -> None:
        is_executor = _is_executor_call(node)
        if is_executor:
            self._executor_depth += 1
        try:
            label = _blocking_call_label(node)
            if label and self._executor_depth == 0:
                self.hits.append((node.lineno, label))
            self.generic_visit(node)
        finally:
            if is_executor:
                self._executor_depth -= 1


class DirectSyncCallVisitor(ast.NodeVisitor):
    def __init__(self, blocking_sync_funcs: set[str]) -> None:
        self.blocking_sync_funcs = blocking_sync_funcs
        self.hits: list[tuple[int, str]] = []
        self._executor_depth = 0

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_Call(self, node: ast.Call) -> None:
        is_executor = _is_executor_call(node)
        if is_executor:
            self._executor_depth += 1
        try:
            if (
                self._executor_depth == 0
                and isinstance(node.func, ast.Name)
                and node.func.id in self.blocking_sync_funcs
            ):
                self.hits.append((node.lineno, node.func.id))
            self.generic_visit(node)
        finally:
            if is_executor:
                self._executor_depth -= 1


def _function_contains_blocking_io(node: ast.FunctionDef) -> bool:
    for child in ast.walk(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) and child is not node:
            continue
        if isinstance(child, ast.Call) and _blocking_call_label(child):
            return True
    return False


def scan() -> int:
    direct_hits: list[str] = []
    sync_call_hits: list[str] = []

    for path in sorted(ROOT.rglob("*.py")):
        if path.name == Path(__file__).name:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError) as err:
            direct_hits.append(f"{path.relative_to(ROOT)}: parse failed: {err}")
            continue

        blocking_sync_funcs = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and _function_contains_blocking_io(node)
        }

        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue

            visitor = DirectAsyncBlockingVisitor()
            for stmt in node.body:
                visitor.visit(stmt)
            for line, label in visitor.hits:
                direct_hits.append(f"{path.relative_to(ROOT)}:{line}: async {node.name} direct {label}")

            sync_visitor = DirectSyncCallVisitor(blocking_sync_funcs)
            for stmt in node.body:
                sync_visitor.visit(stmt)
            for line, func_name in sync_visitor.hits:
                sync_call_hits.append(
                    f"{path.relative_to(ROOT)}:{line}: async {node.name} calls sync blocking {func_name}"
                )

    print("DIRECT_BLOCKING_IN_ASYNC")
    if direct_hits:
        print("\n".join(direct_hits))
    else:
        print("none")

    print("\nSYNC_BLOCKING_FUNCTION_CALLED_FROM_ASYNC")
    if sync_call_hits:
        print("\n".join(sync_call_hits))
    else:
        print("none")

    return 1 if direct_hits or sync_call_hits else 0


if __name__ == "__main__":
    raise SystemExit(scan())
