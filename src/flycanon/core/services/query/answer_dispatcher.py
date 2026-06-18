# Copyright 2024-2026 Firefly Software Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Answer-mode dispatcher -- routes the answer path to RLM or RAG.

``FLYCANON_ANSWER_MODE`` (``settings.answer_mode``) selects the engine:
``rlm`` (the default) routes to :class:`RLMAnswerService`, ``rag`` routes
to the legacy hybrid-retrieval :class:`AnswerService`. Both expose the
identical ``answer()`` contract (``AnswerRequest`` in, ``AnswerResponse``
out), so the dispatcher is a thin pass-through that forwards every
argument verbatim.

RAG is opt-in and deprecated: the dispatcher logs a single deprecation
warning on each RAG-mode answer. The read-only :attr:`mode` /
:attr:`is_rag` accessors let a later PR add a deprecation response header
without re-reading settings.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from flycanon.config import CanonSettings
from flycanon.core.services.query.answer_service import AnswerService
from flycanon.core.services.query.rlm_answer_service import RLMAnswerService
from flycanon.interfaces.dtos.query import AnswerRequest, AnswerResponse

logger = logging.getLogger(__name__)


class AnswerDispatcher:
    """Route the non-streaming answer path to RLM (default) or RAG."""

    def __init__(
        self,
        rag: AnswerService,
        rlm: RLMAnswerService,
        settings: CanonSettings,
    ) -> None:
        self._rag = rag
        self._rlm = rlm
        self._settings = settings

    @property
    def mode(self) -> str:
        """The active answer mode (``rlm`` or ``rag``), read-only."""
        return self._settings.answer_mode

    @property
    def is_rag(self) -> bool:
        """Whether the deprecated RAG engine is selected."""
        return self.mode == "rag"

    async def answer(
        self,
        request: AnswerRequest,
        *,
        prior_turns: list[tuple[str, str]] | None = None,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        on_turn: Callable[[int, list[str]], None] | None = None,
    ) -> AnswerResponse:
        """Delegate to the RLM service by default, RAG when opted in.

        Every argument is forwarded verbatim to the selected service --
        the two engines share :meth:`AnswerService.answer`'s signature.
        ``on_turn`` is RLM-only (the RAG :meth:`AnswerService.answer` has
        no such hook), so it is passed to the RLM branch only.
        """
        if self.is_rag:
            logger.warning(
                "FLYCANON_ANSWER_MODE=rag is deprecated and will be removed in a "
                "future release; RLM is the default"
            )
            return await self._rag.answer(
                request,
                prior_turns=prior_turns,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
            )
        return await self._rlm.answer(
            request,
            prior_turns=prior_turns,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            on_turn=on_turn,
        )


__all__ = ["AnswerDispatcher"]
