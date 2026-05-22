from __future__ import annotations

import logging
import sys
from abc import ABC, abstractmethod
from typing import Any, Callable

LOGGER = logging.getLogger(__name__)


class ContextEngine(ABC):
    @abstractmethod
    def compress(self, messages: list[dict], max_tokens: int) -> list[dict]:
        pass

    @abstractmethod
    def get_tool_schemas(self) -> list[dict]:
        pass


class BaseContextEngine(ContextEngine):
    def compress(self, messages: list[dict], max_tokens: int) -> list[dict]:
        return messages

    def get_tool_schemas(self) -> list[dict]:
        return []


class AgentModule:
    ContextEngine = ContextEngine


def get_hermes_home() -> str:
    import os
    return os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))


class HermesCLIConfigModule:
    get_hermes_home = staticmethod(get_hermes_home)


def install_hermes_shims():
    if "agent" not in sys.modules:
        agent_module = type(sys)("agent")
        context_engine_module = type(sys)("agent.context_engine")
        context_engine_module.ContextEngine = ContextEngine
        agent_module.context_engine = context_engine_module
        sys.modules["agent"] = agent_module
        sys.modules["agent.context_engine"] = context_engine_module
        LOGGER.debug("Installed agent shim module")

    if "hermes_cli" not in sys.modules:
        hermes_cli = type(sys)("hermes_cli")
        config_module = type(sys)("hermes_cli.config")
        config_module.get_hermes_home = get_hermes_home
        hermes_cli.config = config_module
        sys.modules["hermes_cli"] = hermes_cli
        sys.modules["hermes_cli.config"] = config_module
        LOGGER.debug("Installed hermes_cli shim module")


def uninstall_hermes_shims():
    for mod in ["agent", "agent.context_engine", "hermes_cli", "hermes_cli.config"]:
        if mod in sys.modules:
            del sys.modules[mod]
