"""AST-based verification that FallbackConversationAgent class is intact.

This catches the class-structure regression where a module-level def
accidentally placed inside the class body causes methods to fall out of scope.
py_compile and HTTP checks cannot detect this — only AST can.
"""

import ast


def _find_class_ast():
    src = open(
        "custom_components/claw_assistant/conversation.py",
        encoding="utf-8",
    ).read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "FallbackConversationAgent":
            return node
    return None


def test_class_has_async_process():
    cls = _find_class_ast()
    assert cls is not None, "FallbackConversationAgent class not found"
    methods = {n.name for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    required = [
        "async_process",
        "_resolve_user_key",
        "_record_simple_history_turn",
        "_maybe_handle_native_intent",
        "_salvage_partial_turn",
        "_finalize_result",
    ]
    missing = [m for m in required if m not in methods]
    assert not missing, f"Missing class methods: {missing}"
    extra = [m for m in methods if m.startswith(("async_process", "_resolve_user_key")) and m not in required]
    print(f"Class methods ({len(methods)}): {sorted(methods)}")


def test_resolve_user_key_is_module_level():
    src = open(
        "custom_components/claw_assistant/conversation.py",
        encoding="utf-8",
    ).read()
    tree = ast.parse(src)
    mod_funcs = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "resolve_user_key" in mod_funcs, "resolve_user_key should be a module-level function"
    assert "async_process" not in mod_funcs, "async_process leaked out of class!"
