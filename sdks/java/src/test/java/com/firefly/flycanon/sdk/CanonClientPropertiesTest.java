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
import org.springframework.boot.autoconfigure.AutoConfigurations;
import org.springframework.boot.autoconfigure.jackson.JacksonAutoConfiguration;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;

import static com.github.tomakehurst.wiremock.client.WireMock.aResponse;
import static com.github.tomakehurst.wiremock.client.WireMock.equalTo;
import static com.github.tomakehurst.wiremock.client.WireMock.get;
import static com.github.tomakehurst.wiremock.client.WireMock.getRequestedFor;
import static com.github.tomakehurst.wiremock.client.WireMock.urlEqualTo;
import static com.github.tomakehurst.wiremock.core.WireMockConfiguration.options;
import static org.assertj.core.api.Assertions.assertThat;

/**
 * Property-binding coverage for the four new {@link CanonClientProperties}
 * fields. Verifies that ``flycanon.tenant-id`` /
 * ``flycanon.workspace-id`` / ``flycanon.correlation-id`` /
 * ``flycanon.agent-token`` end up on outbound requests via the
 * autoconfigured bean.
 */
class CanonClientPropertiesTest {

    private WireMockServer wireMock;
    private final ApplicationContextRunner contextRunner = new ApplicationContextRunner()
            .withConfiguration(AutoConfigurations.of(
                    JacksonAutoConfiguration.class,
                    CanonClientAutoConfiguration.class));

    @BeforeEach
    void start() {
        wireMock = new WireMockServer(options().dynamicPort());
        wireMock.start();
        wireMock.stubFor(get(urlEqualTo("/api/v1/version"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"service\":\"flycanon\",\"version\":\"26.5.7\",\"embedding_model\":\"e5\",\"answer_model\":\"gpt\",\"answer_fallback_model\":\"gpt\",\"vector_store\":\"pgvector\",\"eda_adapter\":\"kafka\"}")));
    }

    @AfterEach
    void stop() {
        if (wireMock != null) {
            wireMock.stop();
        }
    }

    @Test
    void exposesAllNewPropertiesViaGetters() {
        CanonClientProperties props = new CanonClientProperties();
        props.setTenantId("acme");
        props.setWorkspaceId("ws-1");
        props.setCorrelationId("corr-abc");
        props.setAgentToken("canon_secret");

        assertThat(props.getTenantId()).isEqualTo("acme");
        assertThat(props.getWorkspaceId()).isEqualTo("ws-1");
        assertThat(props.getCorrelationId()).isEqualTo("corr-abc");
        assertThat(props.getAgentToken()).isEqualTo("canon_secret");
    }

    @Test
    void autoConfiguredBeanForwardsAllFourHeadersOnOutboundRequest() {
        contextRunner
                .withPropertyValues(
                        "flycanon.base-url=http://localhost:" + wireMock.port(),
                        "flycanon.tenant-id=acme",
                        "flycanon.workspace-id=ws-1",
                        "flycanon.correlation-id=corr-abc",
                        "flycanon.agent-token=canon_secret")
                .run(context -> {
                    CanonClient client = context.getBean(CanonClient.class);
                    client.version();
                    wireMock.verify(getRequestedFor(urlEqualTo("/api/v1/version"))
                            .withHeader("X-Tenant-Id", equalTo("acme"))
                            .withHeader("X-Workspace-Id", equalTo("ws-1"))
                            .withHeader("X-Correlation-Id", equalTo("corr-abc"))
                            .withHeader("X-Agent-Token", equalTo("canon_secret")));
                });
    }
}
