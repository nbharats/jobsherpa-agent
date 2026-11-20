"""Helpers shared by all nodes."""

import functools
import logging
from collections.abc import Callable
from typing import Any

from agent.state import AgentState

logger = logging.getLogger(__name__)

NodeFunc = Callable[[AgentState], dict[str, Any]]

class CriticalError(Exception):
    """Raise when continuing would be unsafe (for example, the database is unavailable, so we could no longer prevent duplicate actions).
    
    safe_node never swallows this; it stops the whole run."""

def safe_node(func: NodeFunc) ->NodeFunc:
    '''Wrap a node so ordinary exceptions are recorded, not fatal.
    
    - CriticalError: logged and re-raised; the runstops.
    - Any other Exception: logged and stored in state["errors"]; the pipline continues because the state keeps its safe defaults.'''
    @functools.wraps(func)
    def wrapper(state: AgentState) -> dict[str, Any]:
        try:
            return func(state)
        except CriticalError:
            logger.critical("Critical failure in node '%s'; stopping", func.__name__)
            raise
        except Exception as exc:
            logger.exception("Node '%s' failed; continuing", func.__name__)
            return {"errors": [*state.get("errors", []), f"{func.__name__}: {exc}"]}
    return wrapper
