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
import reactor.test.StepVerifier;

import static com.github.tomakehurst.wiremock.client.WireMock.aResponse;
import static com.github.tomakehurst.wiremock.client.WireMock.equalTo;
import static com.github.tomakehurst.wiremock.client.WireMock.get;
import static com.github.tomakehurst.wiremock.client.WireMock.getRequestedFor;
import static com.github.tomakehurst.wiremock.client.WireMock.patch;
import static com.github.tomakehurst.wiremock.client.WireMock.post;
import static com.github.tomakehurst.wiremock.client.WireMock.postRequestedFor;
import static com.github.tomakehurst.wiremock.client.WireMock.urlEqualTo;
import static com.github.tomakehurst.wiremock.core.WireMockConfiguration.options;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * Reactive sibling coverage for the 26.5.7 unification surface.
 *
 * <p>The reactive client mirrors {@link CanonClient} lock-step --
 * same headers, same paths, same idempotency-key contract -- so we
 * only assert the deltas (Mono-wrapped returns) and one
 * representative method per tier.
 */
class ReactiveCanonClientUnificationTest {

    private WireMockServer wireMock;
    private ReactiveCanonClient client;

    @BeforeEach
    void start() {
        wireMock = new WireMockServer(options().dynamicPort());
        wireMock.start();
        client = ReactiveCanonClient.builder()
                .baseUrl("http://localhost:" + wireMock.port())
                .tenantId("acme")
                .workspaceId("ws-1")
                .correlationId("corr-abc")
                .agentToken("canon_secret")
                .build();
    }

    @AfterEach
    void stop() {
        if (wireMock != null) {
            wireMock.stop();
        }
    }

    @Test
    void emitsAllFourHeaders() {
        wireMock.stubFor(get(urlEqualTo("/api/v1/version"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"service\":\"flycanon\",\"version\":\"26.5.7\",\"embedding_model\":\"e5\",\"answer_model\":\"gpt\",\"answer_fallback_model\":\"gpt\",\"vector_store\":\"pgvector\",\"eda_adapter\":\"kafka\"}")));

        StepVerifier.create(client.version())
                .expectNextCount(1)
                .verifyComplete();

        wireMock.verify(getRequestedFor(urlEqualTo("/api/v1/version"))
                .withHeader("X-Tenant-Id", equalTo("acme"))
                .withHeader("X-Workspace-Id", equalTo("ws-1"))
                .withHeader("X-Correlation-Id", equalTo("corr-abc"))
                .withHeader("X-Agent-Token", equalTo("canon_secret")));
    }

    @Test
    void getJobUsesIngestJobsPath() {
        wireMock.stubFor(get(urlEqualTo("/api/v1/ingest-jobs/j1"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"id\":\"j1\",\"status\":\"running\",\"progress\":0.1,\"created_at\":\"2026-05-22T10:00:00Z\",\"updated_at\":\"2026-05-22T10:00:00Z\"}")));

        StepVerifier.create(client.getJob("j1"))
                .assertNext(job -> assertThat(job.id()).isEqualTo("j1"))
                .verifyComplete();
    }

    @Test
    void createWorkspaceRoundTripsThroughReactiveClient() {
        wireMock.stubFor(post(urlEqualTo("/api/v1/workspaces"))
                .willReturn(aResponse()
                        .withStatus(201)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"id\":\"ws-1\",\"tenant_id\":\"acme\",\"name\":\"Alpha\",\"status\":\"active\",\"created_at\":\"2026-05-22T10:00:00Z\",\"updated_at\":\"2026-05-22T10:00:00Z\"}")));

        StepVerifier.create(client.createWorkspace(Models.WorkspaceCreate.of("ws-1", "Alpha")))
                .assertNext(spec -> {
                    assertThat(spec.id()).isEqualTo("ws-1");
                    assertThat(spec.status()).isEqualTo("active");
                })
                .verifyComplete();
    }

    @Test
    void updateWorkspaceUsesPatchVerb() {
        wireMock.stubFor(patch(urlEqualTo("/api/v1/workspaces/ws-1"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"id\":\"ws-1\",\"tenant_id\":\"acme\",\"name\":\"Beta\",\"status\":\"active\",\"created_at\":\"2026-05-22T10:00:00Z\",\"updated_at\":\"2026-05-22T11:00:00Z\"}")));

        Models.WorkspaceUpdate patch = new Models.WorkspaceUpdate("Beta", null, null, null, null, null);
        StepVerifier.create(client.updateWorkspace("ws-1", patch))
                .assertNext(spec -> assertThat(spec.name()).isEqualTo("Beta"))
                .verifyComplete();
    }

    @Test
    void agentQueryStampsIdempotencyKey() {
        wireMock.stubFor(post(urlEqualTo("/api/v1/agent/query"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"answer\":\"yes\",\"citations\":[],\"model\":\"gpt\",\"elapsed_ms\":12,\"no_answer\":false}")));

        StepVerifier.create(client.agent().query(new Models.AnswerRequest("q?", 8, null, null), "idem-rx-001"))
                .assertNext(resp -> assertThat(resp.answer()).isEqualTo("yes"))
                .verifyComplete();

        wireMock.verify(postRequestedFor(urlEqualTo("/api/v1/agent/query"))
                .withHeader("Idempotency-Key", equalTo("idem-rx-001")));
    }

    @Test
    void agentSurfaceRejectsBlankIdempotencyKeyLocally() {
        assertThatThrownBy(() -> client.agent().search(new Models.SearchRequest("q", 5), ""))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void exceptionDispatchersOnReactiveClient() {
        wireMock.stubFor(get(urlEqualTo("/api/v1/version"))
                .willReturn(aResponse()
                        .withStatus(403)
                        .withHeader("Content-Type", "application/problem+json")
                        .withBody("{\"type\":\"about:blank\",\"title\":\"Forbidden\",\"status\":403,\"code\":\"invalid_agent_token\",\"detail\":\"bad token\"}")));

        StepVerifier.create(client.version())
                .expectError(InvalidAgentToken.class)
                .verify();
    }
}
