# Copyright 2026 Firefly Software Solutions Inc
"""``@configuration`` class -- exposes every cross-cutting service as a pyfly bean.

Pyfly's container scans this module, sees the ``@configuration`` class,
instantiates it once, then calls each ``@bean`` method to produce the
beans. The return-type annotations determine the bean's interface, so
constructor-injection works on every consumer (controller, command /
query handler, worker).

This is the **single** declaration point for everything that is not
picked up by a stereotype decorator
(``@service``/``@rest_controller``/``@command_handler``/
``@query_handler``/``@repository``).
"""

from __future__ import annotations

from pyfly.container import bean, configuration

from flycanon.config import CanonSettings, get_settings


@configuration
class CanonCoreConfiguration:
    """Wiring for everything outside the pyfly stereotype decorators.

    The skeleton produces just the settings bean. Subsequent modules
    (ingestion, embeddings, retrieval, knowledge, query, workers) add
    their bean factories here as they come online.
    """

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    @bean
    def settings(self) -> CanonSettings:
        return get_settings()
