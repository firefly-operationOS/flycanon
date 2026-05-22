/*
 * Copyright 2026 Firefly Software Solutions Inc.
 * Licensed under the Apache License, Version 2.0.
 */
package com.firefly.flycanon.sdk;

import com.github.tomakehurst.wiremock.WireMockServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import static com.github.tomakehurst.wiremock.client.WireMock.aResponse;
import static com.github.tomakehurst.wiremock.client.WireMock.get;
import static com.github.tomakehurst.wiremock.client.WireMock.getRequestedFor;
import static com.github.tomakehurst.wiremock.client.WireMock.post;
import static com.github.tomakehurst.wiremock.client.WireMock.postRequestedFor;
import static com.github.tomakehurst.wiremock.client.WireMock.urlEqualTo;
import static com.github.tomakehurst.wiremock.core.WireMockConfiguration.options;
import static org.assertj.core.api.Assertions.assertThat;

/**
 * Verifies the 26.5.7 path rename: {@code /api/v1/jobs} ->
 * {@code /api/v1/ingest-jobs} (and the matching {@code :cancel}
 * action / {@code /stream} suffix).
 */
class CanonClientPathRenameTest {

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

    @Test
    void getJobUsesIngestJobsPath() {
        wireMock.stubFor(get(urlEqualTo("/api/v1/ingest-jobs/job-1"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"id\":\"job-1\",\"status\":\"running\",\"progress\":0.5,\"stage\":\"chunk\",\"created_at\":\"2026-05-22T10:00:00Z\",\"updated_at\":\"2026-05-22T10:00:00Z\"}")));

        var job = client.getJob("job-1");

        assertThat(job.id()).isEqualTo("job-1");
        wireMock.verify(getRequestedFor(urlEqualTo("/api/v1/ingest-jobs/job-1")));
    }

    @Test
    void cancelJobUsesIngestJobsPath() {
        wireMock.stubFor(post(urlEqualTo("/api/v1/ingest-jobs/job-1:cancel"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"id\":\"job-1\",\"status\":\"cancelled\",\"progress\":0.5,\"created_at\":\"2026-05-22T10:00:00Z\",\"updated_at\":\"2026-05-22T10:00:00Z\"}")));

        var job = client.cancelJob("job-1");

        assertThat(job.status()).isEqualTo("cancelled");
        wireMock.verify(postRequestedFor(urlEqualTo("/api/v1/ingest-jobs/job-1:cancel")));
    }

    @Test
    void jobStreamUrlUsesIngestJobsPath() {
        String url = client.jobStreamUrl("job-1", 42L);
        assertThat(url).isEqualTo("/api/v1/ingest-jobs/job-1/stream?cursor=42");
    }
}
