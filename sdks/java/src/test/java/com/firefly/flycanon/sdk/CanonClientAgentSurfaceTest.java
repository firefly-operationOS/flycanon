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
import java.util.Map;

import static com.github.tomakehurst.wiremock.client.WireMock.aResponse;
import static com.github.tomakehurst.wiremock.client.WireMock.equalTo;
import static com.github.tomakehurst.wiremock.client.WireMock.get;
import static com.github.tomakehurst.wiremock.client.WireMock.getRequestedFor;
import static com.github.tomakehurst.wiremock.client.WireMock.post;
import static com.github.tomakehurst.wiremock.client.WireMock.postRequestedFor;
import static com.github.tomakehurst.wiremock.client.WireMock.urlEqualTo;
import static com.github.tomakehurst.wiremock.core.WireMockConfiguration.options;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * Agent-surface coverage for {@link CanonClient#agent()}.
 *
 * <p>Every POST mandates {@code Idempotency-Key}; the SDK rejects
 * empty / blank values locally before the round-trip, and stamps
 * the header verbatim when valid.
 */
class CanonClientAgentSurfaceTest {

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
    void rejectsNullIdempotencyKeyLocally() {
        Models.SubmitSourceJsonPayload payload = new Models.SubmitSourceJsonPayload(
                "url", "http://example.com", Map.of(), null, null, null);
        assertThatThrownBy(() -> client.agent().ingestSource(payload, null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("idempotencyKey");
    }

    @Test
    void rejectsBlankIdempotencyKeyLocally() {
        Models.AnswerRequest request = new Models.AnswerRequest("q?", 8, null, null);
        assertThatThrownBy(() -> client.agent().query(request, "   "))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void ingestSourceSendsIdempotencyKey() {
        wireMock.stubFor(post(urlEqualTo("/api/v1/agent/sources"))
                .willReturn(aResponse()
                        .withStatus(201)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"id\":\"src-1\",\"kind\":\"url\",\"status\":\"ready\",\"content_sha256\":\"abc\",\"content_bytes\":100,\"created_at\":\"2026-05-22T10:00:00Z\",\"updated_at\":\"2026-05-22T10:00:00Z\"}")));

        Models.SubmitSourceJsonPayload payload = new Models.SubmitSourceJsonPayload(
                "url", "http://example.com", Map.of(), null, null, null);
        Models.SourceRecord rec = client.agent().ingestSource(payload, "idem-key-001");

        assertThat(rec.id()).isEqualTo("src-1");
        wireMock.verify(postRequestedFor(urlEqualTo("/api/v1/agent/sources"))
                .withHeader("Idempotency-Key", equalTo("idem-key-001"))
                .withHeader("X-Agent-Token", equalTo("canon_secret")));
    }

    @Test
    void getSourceUsesAgentPath() {
        wireMock.stubFor(get(urlEqualTo("/api/v1/agent/sources/src-1"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"id\":\"src-1\",\"kind\":\"url\",\"status\":\"ready\",\"content_sha256\":\"abc\",\"content_bytes\":100,\"created_at\":\"2026-05-22T10:00:00Z\",\"updated_at\":\"2026-05-22T10:00:00Z\"}")));

        Models.SourceRecord rec = client.agent().getSource("src-1");

        assertThat(rec.id()).isEqualTo("src-1");
        wireMock.verify(getRequestedFor(urlEqualTo("/api/v1/agent/sources/src-1")));
    }

    @Test
    void queryStampsIdempotencyKey() {
        wireMock.stubFor(post(urlEqualTo("/api/v1/agent/query"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"answer\":\"yes\",\"citations\":[],\"model\":\"gpt\",\"elapsed_ms\":12,\"no_answer\":false}")));

        Models.AnswerRequest request = new Models.AnswerRequest("q?", 8, null, null);
        Models.AnswerResponse resp = client.agent().query(request, "idem-key-002");

        assertThat(resp.answer()).isEqualTo("yes");
        wireMock.verify(postRequestedFor(urlEqualTo("/api/v1/agent/query"))
                .withHeader("Idempotency-Key", equalTo("idem-key-002")));
    }

    @Test
    void searchUsesAgentPath() {
        wireMock.stubFor(post(urlEqualTo("/api/v1/agent/search"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"hits\":[],\"elapsed_ms\":7}")));

        Models.SearchResponse resp = client.agent().search(new Models.SearchRequest("hello", 10), "idem-key-003");

        assertThat(resp.hits()).isEmpty();
        wireMock.verify(postRequestedFor(urlEqualTo("/api/v1/agent/search"))
                .withHeader("Idempotency-Key", equalTo("idem-key-003")));
    }

    @Test
    void getKnowledgeReadsViaAgentPath() {
        wireMock.stubFor(get(urlEqualTo("/api/v1/agent/knowledge/item-1"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"id\":\"item-1\",\"status\":\"published\",\"current_version\":1,\"title\":\"x\",\"domain\":\"d\",\"jurisdiction\":\"GLOBAL\",\"tags\":[],\"created_at\":\"2026-05-22T10:00:00Z\",\"updated_at\":\"2026-05-22T10:00:00Z\"}")));

        Models.KnowledgeItem item = client.agent().getKnowledge("item-1");

        assertThat(item.id()).isEqualTo("item-1");
    }

    @Test
    void getProvenanceReturnsCitations() {
        wireMock.stubFor(get(urlEqualTo("/api/v1/agent/knowledge/item-1/provenance"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"knowledge_item_id\":\"item-1\",\"version\":1,\"citations\":[],\"sources\":[],\"history\":[]}")));

        Models.Provenance prov = client.agent().getProvenance("item-1");

        assertThat(prov.knowledgeItemId()).isEqualTo("item-1");
        assertThat(prov.version()).isEqualTo(1);
    }

    @Test
    void proposeCandidatesReturnsListWithIdempotencyKey() {
        wireMock.stubFor(post(urlEqualTo("/api/v1/agent/candidates:propose"))
                .willReturn(aResponse()
                        .withStatus(201)
                        .withHeader("Content-Type", "application/json")
                        .withBody("[{\"id\":\"c-1\",\"status\":\"proposed\",\"source_id\":\"src-1\",\"title\":\"t\",\"body\":\"b\",\"domain\":\"d\",\"jurisdiction\":\"GLOBAL\",\"tags\":[],\"citations\":[],\"created_at\":\"2026-05-22T10:00:00Z\"}]")));

        Models.ProposeCandidatesRequest request = new Models.ProposeCandidatesRequest(
                "src-1", "d", null, null, null, null);
        List<Models.CandidateRecord> rows = client.agent().proposeCandidates(request, "idem-key-004");

        assertThat(rows).hasSize(1);
        assertThat(rows.get(0).id()).isEqualTo("c-1");
        wireMock.verify(postRequestedFor(urlEqualTo("/api/v1/agent/candidates:propose"))
                .withHeader("Idempotency-Key", equalTo("idem-key-004")));
    }

    @Test
    void queryStreamUrlValidatesIdempotencyKey() {
        // The blocking client returns the URL; idempotency key must
        // be non-empty just like the other agent POSTs.
        String url = client.agent().queryStreamUrl("idem-key-005");
        assertThat(url).isEqualTo("/api/v1/agent/query/stream");

        assertThatThrownBy(() -> client.agent().queryStreamUrl(""))
                .isInstanceOf(IllegalArgumentException.class);
    }
}
