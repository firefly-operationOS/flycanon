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

"""A security-hardened subprocess executor for untrusted REPL code.

The RLM engine ``exec``\\ s model-written Python. This package runs that code in
a child process with a scrubbed environment (no secrets), OS resource limits
(CPU/memory/no file writes), and no inherited parent file descriptors. The
child holds no network or infrastructure objects: every capability the code
touches (``docs``, ``llm``, ``rlm``, ``final``) is an RPC stub that asks the
parent, which services the request with caller-injected handlers.

* :mod:`runner` -- the child entry point (``python -m ...sandbox.runner``).
* :mod:`executor` -- the parent :class:`SandboxExecutor` driving the child.
* :mod:`_proto` -- the length-prefixed JSON framing shared by both sides.
"""
