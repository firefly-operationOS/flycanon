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

"""Integration-tier tests.

These tests boot real infra (Postgres + pgvector via Testcontainers)
and verify end-to-end behaviour that the SQLite-backed unit tests
can't exercise -- chiefly Postgres-specific features like row-level
security policies, ``current_setting`` GUCs, and the pgvector
extension.

They auto-skip when Docker is unavailable so CI hosts without a
container runtime stay green.
"""
