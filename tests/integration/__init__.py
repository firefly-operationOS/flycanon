# Copyright 2026 Firefly Software Solutions Inc
"""Integration-tier tests.

These tests boot real infra (Postgres + pgvector via Testcontainers)
and verify end-to-end behaviour that the SQLite-backed unit tests
can't exercise -- chiefly Postgres-specific features like row-level
security policies, ``current_setting`` GUCs, and the pgvector
extension.

They auto-skip when Docker is unavailable so CI hosts without a
container runtime stay green.
"""
