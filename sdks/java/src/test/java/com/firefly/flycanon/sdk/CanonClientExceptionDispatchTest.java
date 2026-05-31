// Copyright 2024-2026 Firefly Software Foundation
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

package com.firefly.flycanon.sdk;

import com.firefly.flycanon.sdk.model.Models;
import com.github.tomakehurst.wiremock.WireMockServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.util.Map;

import static com.github.tomakehurst.wiremock.client.WireMock.aResponse;
import static com.github.tomakehurst.wiremock.client.WireMock.get;
import static com.github.tomakehurst.wiremock.client.WireMock.post;
import static com.github.tomakehurst.wiremock.client.WireMock.urlEqualTo;
import static com.github.tomakehurst.wiremock.core.WireMockConfiguration.options;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * Exception-dispatch coverage: every known RFC 7807 {@code code}
 * round-trips through the SDK as its typed subclass, and unknown
 * codes fall back to {@link CanonAPIException}.
 *
 * <p>Also exercises the structured {@code errors[]} array parsing
 * on {@link ValidationError}.
 */
class CanonClientExceptionDispatchTest {

    private WireMockServer wireMock;
    private CanonClient client;

    @BeforeEach
    void start() {
        wireMock = new WireMockServer(options().dynamicPort());
        wireMock.start();
        client = CanonClient.builder()
                .baseUrl("http://localhost:" + wireMock.port())
                .build();
    }

    @AfterEach
    void stop() {
        if (wireMock != null) {
            wireMock.stop();
        }
    }

    private void stubError(String urlPath, int status, String code, String title, String detail) {
        stubError(urlPath, status, code, title, detail, "");
    }

    private void stubError(String urlPath, int status, String code, String title, String detail, String errorsArrayJson) {
        String body = String.format(
                "{\"type\":\"about:blank\",\"title\":\"%s\",\"status\":%d,\"code\":\"%s\",\"detail\":\"%s\"%s}",
                title, status, code, detail,
                errorsArrayJson.isEmpty() ? "" : ",\"errors\":" + errorsArrayJson);
        wireMock.stubFor(get(urlEqualTo(urlPath))
                .willReturn(aResponse()
                        .withStatus(status)
                        .withHeader("Content-Type", "application/problem+json")
                        .withBody(body)));
    }

    @Test
    void dispatchesMissingAgentTokenOn401() {
        stubError("/api/v1/version", 401, "missing_agent_token", "Auth required", "X-Agent-Token absent");

        assertThatThrownBy(() -> client.version())
                .isInstanceOf(MissingAgentToken.class)
                .matches(e -> ((CanonAPIException) e).statusCode() == 401)
                .matches(e -> "missing_agent_token".equals(((CanonAPIException) e).code()));
    }

    @Test
    void dispatchesInvalidAgentTokenOn403() {
        stubError("/api/v1/version", 403, "invalid_agent_token", "Forbidden", "bad token");

        assertThatThrownBy(() -> client.version())
                .isInstanceOf(InvalidAgentToken.class)
                .isInstanceOf(CanonAPIException.class);
    }

    @Test
    void dispatchesAgentTokenExpired() {
        stubError("/api/v1/version", 403, "agent_token_expired", "Token expired", "expired at 2026-01-01");

        assertThatThrownBy(() -> client.version())
                .isInstanceOf(AgentTokenExpired.class);
    }

    @Test
    void dispatchesAgentWorkspaceNotInAllowlist() {
        stubError("/api/v1/version", 403, "agent_workspace_not_in_allowlist", "Not allowed", "workspace ws-9 not in allowlist");

        assertThatThrownBy(() -> client.version())
                .isInstanceOf(AgentWorkspaceNotInAllowlist.class);
    }

    @Test
    void dispatchesAgentScopeDenied() {
        stubError("/api/v1/version", 403, "agent_scope_denied", "Scope denied", "missing scope agent.query:run");

        assertThatThrownBy(() -> client.version())
                .isInstanceOf(AgentScopeDenied.class);
    }

    @Test
    void dispatchesAgentCannotMint() {
        stubError("/api/v1/version", 403, "agent_cannot_mint", "Forbidden", "agents cannot mint tokens");

        assertThatThrownBy(() -> client.version())
                .isInstanceOf(AgentCannotMint.class);
    }

    @Test
    void dispatchesMissingIdempotencyKey() {
        stubError("/api/v1/version", 400, "missing_idempotency_key", "Bad request", "Idempotency-Key missing");

        assertThatThrownBy(() -> client.version())
                .isInstanceOf(MissingIdempotencyKey.class);
    }

    @Test
    void dispatchesValidationErrorWithFieldRows() {
        stubError(
                "/api/v1/version",
                400,
                "invalid_request",
                "Bad request",
                "validation failed",
                "[{\"code\":\"too_short\",\"path\":\"name\",\"message\":\"min length 1\"},"
                        + "{\"code\":\"required\",\"path\":\"status\",\"message\":\"status is required\"}]");

        try {
            client.version();
        } catch (ValidationError ex) {
            assertThat(ex.code()).isEqualTo("invalid_request");
            assertThat(ex.errors()).hasSize(2);
            assertThat(ex.errors().get(0).code()).isEqualTo("too_short");
            assertThat(ex.errors().get(0).path()).isEqualTo("name");
            assertThat(ex.errors().get(0).message()).isEqualTo("min length 1");
            assertThat(ex.errors().get(1).path()).isEqualTo("status");
        }
    }

    @Test
    void unknownCodeFallsBackToBaseException() {
        stubError("/api/v1/version", 500, "thermal_runaway", "Unknown", "weird");

        assertThatThrownBy(() -> client.version())
                .isInstanceOf(CanonAPIException.class)
                .isNotInstanceOf(ValidationError.class)
                .matches(e -> "thermal_runaway".equals(((CanonAPIException) e).code()));
    }

    @Test
    void emptyErrorsListWhenServerOmitsArray() {
        stubError("/api/v1/version", 400, "invalid_request", "Bad request", "validation failed");

        try {
            client.version();
        } catch (ValidationError ex) {
            assertThat(ex.errors()).isEmpty();
        }
    }

    @Test
    void missingIdempotencyKeyOnAgentPostMapsByCode() {
        // The service rejects an absent ``Idempotency-Key`` on the
        // agent-tier POST with the matching typed code. Even
        // though the SDK normally validates the key locally, an
        // intermediate proxy that strips the header would surface
        // the same response shape -- verify the dispatch path.
        wireMock.stubFor(post(urlEqualTo("/api/v1/agent/query"))
                .willReturn(aResponse()
                        .withStatus(400)
                        .withHeader("Content-Type", "application/problem+json")
                        .withBody("{\"type\":\"about:blank\",\"title\":\"Bad request\",\"status\":400,\"code\":\"missing_idempotency_key\",\"detail\":\"Idempotency-Key absent\"}")));

        assertThatThrownBy(() ->
                client.agent().query(new Models.AnswerRequest("q?", 8, null, null), "valid-key"))
                .isInstanceOf(MissingIdempotencyKey.class);
    }

    @Test
    void canonApiExceptionCodeAndStatusAreParsed() {
        CanonAPIException ex = new CanonAPIException(
                404,
                "workspace_not_found",
                "Workspace not found",
                "workspace ws-1 not found",
                Map.of("workspace_id", "ws-1"));

        assertThat(ex.statusCode()).isEqualTo(404);
        assertThat(ex.code()).isEqualTo("workspace_not_found");
        assertThat(ex.extensions()).containsEntry("workspace_id", "ws-1");
        assertThat(ex.errors()).isEmpty();
    }
}
