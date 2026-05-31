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

import com.github.tomakehurst.wiremock.WireMockServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import static com.github.tomakehurst.wiremock.client.WireMock.absent;
import static com.github.tomakehurst.wiremock.client.WireMock.aResponse;
import static com.github.tomakehurst.wiremock.client.WireMock.equalTo;
import static com.github.tomakehurst.wiremock.client.WireMock.get;
import static com.github.tomakehurst.wiremock.client.WireMock.getRequestedFor;
import static com.github.tomakehurst.wiremock.client.WireMock.matching;
import static com.github.tomakehurst.wiremock.client.WireMock.urlEqualTo;
import static com.github.tomakehurst.wiremock.core.WireMockConfiguration.options;

/**
 * Header-injection contract tests for {@link CanonClient}.
 *
 * <p>Each test stubs a 200 response and asserts the exact set of
 * outbound headers the SDK sent. Headers are gated on builder
 * properties -- absent properties produce no header on the wire.
 */
class CanonClientHeadersTest {

    private WireMockServer wireMock;
    private String baseUrl;

    @BeforeEach
    void start() {
        wireMock = new WireMockServer(options().dynamicPort());
        wireMock.start();
        baseUrl = "http://localhost:" + wireMock.port();
    }

    @AfterEach
    void stop() {
        if (wireMock != null) {
            wireMock.stop();
        }
    }

    @Test
    void omitsAllOptionalHeadersWhenUnset() {
        wireMock.stubFor(get(urlEqualTo("/api/v1/version"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"service\":\"flycanon\",\"version\":\"26.5.7\",\"embedding_model\":\"e5\",\"answer_model\":\"gpt\",\"answer_fallback_model\":\"gpt\",\"vector_store\":\"pgvector\",\"eda_adapter\":\"kafka\"}")));

        CanonClient client = CanonClient.builder().baseUrl(baseUrl).build();
        client.version();

        wireMock.verify(getRequestedFor(urlEqualTo("/api/v1/version"))
                .withHeader("X-Tenant-Id", absent())
                .withHeader("X-Workspace-Id", absent())
                .withHeader("X-Correlation-Id", absent())
                .withHeader("X-Agent-Token", absent()));
    }

    @Test
    void emitsAllFourHeadersWhenSetOnBuilder() {
        wireMock.stubFor(get(urlEqualTo("/api/v1/version"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"service\":\"flycanon\",\"version\":\"26.5.7\",\"embedding_model\":\"e5\",\"answer_model\":\"gpt\",\"answer_fallback_model\":\"gpt\",\"vector_store\":\"pgvector\",\"eda_adapter\":\"kafka\"}")));

        CanonClient client = CanonClient.builder()
                .baseUrl(baseUrl)
                .tenantId("acme")
                .workspaceId("ws-1")
                .correlationId("corr-abc")
                .agentToken("canon_secret")
                .build();
        client.version();

        wireMock.verify(getRequestedFor(urlEqualTo("/api/v1/version"))
                .withHeader("X-Tenant-Id", equalTo("acme"))
                .withHeader("X-Workspace-Id", equalTo("ws-1"))
                .withHeader("X-Correlation-Id", equalTo("corr-abc"))
                .withHeader("X-Agent-Token", equalTo("canon_secret")));
    }

    @Test
    void emitsTenantOnlyWhenOthersAreBlank() {
        wireMock.stubFor(get(urlEqualTo("/api/v1/version"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"service\":\"flycanon\",\"version\":\"26.5.7\",\"embedding_model\":\"e5\",\"answer_model\":\"gpt\",\"answer_fallback_model\":\"gpt\",\"vector_store\":\"pgvector\",\"eda_adapter\":\"kafka\"}")));

        CanonClient client = CanonClient.builder()
                .baseUrl(baseUrl)
                .tenantId("acme")
                .workspaceId("")    // blank -- skip
                .correlationId(null) // null -- skip
                .agentToken("   ")   // whitespace -- skip
                .build();
        client.version();

        wireMock.verify(getRequestedFor(urlEqualTo("/api/v1/version"))
                .withHeader("X-Tenant-Id", equalTo("acme"))
                .withHeader("X-Workspace-Id", absent())
                .withHeader("X-Correlation-Id", absent())
                .withHeader("X-Agent-Token", absent()));
    }

    @Test
    void advertisesSdkVersionInUserAgent() {
        wireMock.stubFor(get(urlEqualTo("/api/v1/version"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"service\":\"flycanon\",\"version\":\"26.5.7\",\"embedding_model\":\"e5\",\"answer_model\":\"gpt\",\"answer_fallback_model\":\"gpt\",\"vector_store\":\"pgvector\",\"eda_adapter\":\"kafka\"}")));

        CanonClient client = CanonClient.builder().baseUrl(baseUrl).build();
        client.version();

        wireMock.verify(getRequestedFor(urlEqualTo("/api/v1/version"))
                .withHeader("User-Agent", matching("flycanon-sdk-java/26\\.5\\.7")));
    }

    @Test
    void retainsApiKeyBearerWhenSet() {
        wireMock.stubFor(get(urlEqualTo("/api/v1/version"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"service\":\"flycanon\",\"version\":\"26.5.7\",\"embedding_model\":\"e5\",\"answer_model\":\"gpt\",\"answer_fallback_model\":\"gpt\",\"vector_store\":\"pgvector\",\"eda_adapter\":\"kafka\"}")));

        CanonClient client = CanonClient.builder()
                .baseUrl(baseUrl)
                .apiKey("legacy-key")
                .tenantId("acme")
                .build();
        client.version();

        wireMock.verify(getRequestedFor(urlEqualTo("/api/v1/version"))
                .withHeader("Authorization", equalTo("Bearer legacy-key"))
                .withHeader("X-Tenant-Id", equalTo("acme")));
    }
}
