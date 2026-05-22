/*
 * Copyright 2026 Firefly Software Solutions Inc.
 * Licensed under the Apache License, Version 2.0.
 */
package com.firefly.flycanon.sdk;

import com.firefly.flycanon.sdk.model.Models;
import com.github.tomakehurst.wiremock.WireMockServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.util.List;

import static com.github.tomakehurst.wiremock.client.WireMock.aResponse;
import static com.github.tomakehurst.wiremock.client.WireMock.delete;
import static com.github.tomakehurst.wiremock.client.WireMock.deleteRequestedFor;
import static com.github.tomakehurst.wiremock.client.WireMock.equalTo;
import static com.github.tomakehurst.wiremock.client.WireMock.equalToJson;
import static com.github.tomakehurst.wiremock.client.WireMock.get;
import static com.github.tomakehurst.wiremock.client.WireMock.getRequestedFor;
import static com.github.tomakehurst.wiremock.client.WireMock.patch;
import static com.github.tomakehurst.wiremock.client.WireMock.patchRequestedFor;
import static com.github.tomakehurst.wiremock.client.WireMock.post;
import static com.github.tomakehurst.wiremock.client.WireMock.postRequestedFor;
import static com.github.tomakehurst.wiremock.client.WireMock.urlEqualTo;
import static com.github.tomakehurst.wiremock.core.WireMockConfiguration.options;
import static org.assertj.core.api.Assertions.assertThat;

/**
 * Workspace and agent-token user-tier CRUD coverage for
 * {@link CanonClient}. Asserts on URL, HTTP verb, body shape, and
 * the four wire-contract headers.
 */
class CanonClientWorkspaceTest {

    private WireMockServer wireMock;
    private CanonClient client;

    @BeforeEach
    void start() {
        wireMock = new WireMockServer(options().dynamicPort());
        wireMock.start();
        client = CanonClient.builder()
                .baseUrl("http://localhost:" + wireMock.port())
                .tenantId("acme")
                .workspaceId("ws-1")
                .correlationId("corr-abc")
                .build();
    }

    @AfterEach
    void stop() {
        if (wireMock != null) {
            wireMock.stop();
        }
    }

    // -------------------- workspace CRUD --------------------

    @Test
    void createWorkspacePostsSpecBody() {
        wireMock.stubFor(post(urlEqualTo("/api/v1/workspaces"))
                .willReturn(aResponse()
                        .withStatus(201)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"id\":\"ws-1\",\"tenant_id\":\"acme\",\"name\":\"Alpha\",\"status\":\"active\",\"created_at\":\"2026-05-22T10:00:00Z\",\"updated_at\":\"2026-05-22T10:00:00Z\"}")));

        Models.WorkspaceSpec spec = client.createWorkspace(Models.WorkspaceCreate.of("ws-1", "Alpha"));

        assertThat(spec.id()).isEqualTo("ws-1");
        assertThat(spec.tenantId()).isEqualTo("acme");
        assertThat(spec.status()).isEqualTo("active");
        wireMock.verify(postRequestedFor(urlEqualTo("/api/v1/workspaces"))
                .withRequestBody(equalToJson("{\"id\":\"ws-1\",\"name\":\"Alpha\",\"status\":\"active\"}", true, true))
                .withHeader("X-Tenant-Id", equalTo("acme"))
                .withHeader("X-Workspace-Id", equalTo("ws-1")));
    }

    @Test
    void listWorkspacesReturnsRows() {
        wireMock.stubFor(get(urlEqualTo("/api/v1/workspaces"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("[{\"id\":\"ws-1\",\"tenant_id\":\"acme\",\"name\":\"Alpha\",\"status\":\"active\",\"created_at\":\"2026-05-22T10:00:00Z\",\"updated_at\":\"2026-05-22T10:00:00Z\"}]")));

        List<Models.WorkspaceSummary> rows = client.listWorkspaces();

        assertThat(rows).hasSize(1);
        assertThat(rows.get(0).id()).isEqualTo("ws-1");
        wireMock.verify(getRequestedFor(urlEqualTo("/api/v1/workspaces")));
    }

    @Test
    void listWorkspacesHandlesEmptyArray() {
        wireMock.stubFor(get(urlEqualTo("/api/v1/workspaces"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("[]")));

        assertThat(client.listWorkspaces()).isEmpty();
    }

    @Test
    void getWorkspaceFetchesById() {
        wireMock.stubFor(get(urlEqualTo("/api/v1/workspaces/ws-1"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"id\":\"ws-1\",\"tenant_id\":\"acme\",\"name\":\"Alpha\",\"status\":\"active\",\"created_at\":\"2026-05-22T10:00:00Z\",\"updated_at\":\"2026-05-22T10:00:00Z\"}")));

        Models.WorkspaceSpec spec = client.getWorkspace("ws-1");

        assertThat(spec.id()).isEqualTo("ws-1");
        wireMock.verify(getRequestedFor(urlEqualTo("/api/v1/workspaces/ws-1")));
    }

    @Test
    void updateWorkspacePatchesSparsely() {
        wireMock.stubFor(patch(urlEqualTo("/api/v1/workspaces/ws-1"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"id\":\"ws-1\",\"tenant_id\":\"acme\",\"name\":\"Beta\",\"status\":\"active\",\"created_at\":\"2026-05-22T10:00:00Z\",\"updated_at\":\"2026-05-22T11:00:00Z\"}")));

        Models.WorkspaceUpdate patch = new Models.WorkspaceUpdate("Beta", null, null, null, null, null);
        Models.WorkspaceSpec spec = client.updateWorkspace("ws-1", patch);

        assertThat(spec.name()).isEqualTo("Beta");
        // Only ``name`` should appear in the outbound JSON -- null
        // fields are suppressed by @JsonInclude(NON_NULL).
        wireMock.verify(patchRequestedFor(urlEqualTo("/api/v1/workspaces/ws-1"))
                .withRequestBody(equalToJson("{\"name\":\"Beta\"}", true, false)));
    }

    @Test
    void closeWorkspaceSendsCloseAction() {
        wireMock.stubFor(post(urlEqualTo("/api/v1/workspaces/ws-1:close"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"id\":\"ws-1\",\"tenant_id\":\"acme\",\"name\":\"Alpha\",\"status\":\"closed\",\"created_at\":\"2026-05-22T10:00:00Z\",\"updated_at\":\"2026-05-22T11:00:00Z\",\"closed_at\":\"2026-05-22T11:00:00Z\"}")));

        Models.WorkspaceSpec spec = client.closeWorkspace("ws-1");

        assertThat(spec.status()).isEqualTo("closed");
        assertThat(spec.closedAt()).isNotNull();
        wireMock.verify(postRequestedFor(urlEqualTo("/api/v1/workspaces/ws-1:close")));
    }

    // -------------------- agent-token CRUD --------------------

    @Test
    void mintAgentTokenReturnsRawTokenOnce() {
        wireMock.stubFor(post(urlEqualTo("/api/v1/agent-tokens"))
                .willReturn(aResponse()
                        .withStatus(201)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"id\":\"tok-1\",\"name\":\"bot\",\"prefix\":\"canon_abcd\",\"workspace_allowlist\":null,\"scopes\":[\"*\"],\"rate_limit_rpm\":null,\"expires_at\":null,\"created_at\":\"2026-05-22T10:00:00Z\",\"created_by\":\"anonymous\",\"revoked_at\":null,\"last_used_at\":null,\"token\":\"canon_abcd1234efgh5678\"}")));

        Models.AgentTokenCreated created = client.mintAgentToken(Models.AgentTokenMintRequest.of("bot"));

        assertThat(created.id()).isEqualTo("tok-1");
        assertThat(created.token()).isEqualTo("canon_abcd1234efgh5678");
        assertThat(created.prefix()).isEqualTo("canon_abcd");
    }

    @Test
    void listAgentTokensReturnsSummaryRows() {
        wireMock.stubFor(get(urlEqualTo("/api/v1/agent-tokens"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("[{\"id\":\"tok-1\",\"name\":\"bot\",\"prefix\":\"canon_abcd\",\"workspace_allowlist\":null,\"scopes\":[\"*\"],\"rate_limit_rpm\":null,\"expires_at\":null,\"created_at\":\"2026-05-22T10:00:00Z\",\"created_by\":\"anonymous\",\"revoked_at\":null,\"last_used_at\":null}]")));

        List<Models.AgentTokenSummary> rows = client.listAgentTokens();

        assertThat(rows).hasSize(1);
        assertThat(rows.get(0).prefix()).isEqualTo("canon_abcd");
    }

    @Test
    void revokeAgentTokenSendsDelete() {
        wireMock.stubFor(delete(urlEqualTo("/api/v1/agent-tokens/tok-1"))
                .willReturn(aResponse().withStatus(204)));

        client.revokeAgentToken("tok-1");

        wireMock.verify(deleteRequestedFor(urlEqualTo("/api/v1/agent-tokens/tok-1")));
    }
}
