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

import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.boot.autoconfigure.AutoConfiguration;
import org.springframework.boot.autoconfigure.condition.ConditionalOnClass;
import org.springframework.boot.autoconfigure.condition.ConditionalOnMissingBean;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.web.reactive.function.client.WebClient;

/**
 * Spring Boot autoconfiguration for the reactive flycanon client.
 *
 * <p>Off by default. Activates when:
 *
 * <ul>
 *   <li>Spring's {@link WebClient} is on the classpath (consumer
 *       depends on {@code spring-webflux} or
 *       {@code spring-boot-starter-webflux}), <em>and</em></li>
 *   <li>``flycanon.reactive-auto-configure`` is set to ``true``.</li>
 * </ul>
 *
 * Disabled by default so applications that only need the blocking
 * {@link CanonClient} don't pay for a reactor / netty bean they will
 * never use. Coexists peacefully with
 * {@link CanonClientAutoConfiguration} -- callers can inject either
 * (or both) when the corresponding flag is set.
 *
 * <pre>{@code
 *   flycanon:
 *     base-url: https://canon.internal.example.com
 *     reactive-auto-configure: true
 * }</pre>
 */
@AutoConfiguration(after = CanonClientAutoConfiguration.class)
@ConditionalOnClass(WebClient.class)
@EnableConfigurationProperties(CanonClientProperties.class)
public class ReactiveCanonClientAutoConfiguration {

    @Bean
    @ConditionalOnMissingBean
    @ConditionalOnProperty(prefix = "flycanon", name = "reactive-auto-configure", havingValue = "true")
    public ReactiveCanonClient reactiveCanonClient(
            CanonClientProperties properties,
            ObjectMapper objectMapper) {
        return ReactiveCanonClient.builder()
                .baseUrl(properties.getBaseUrl())
                .apiKey(properties.getApiKey())
                .tenantId(properties.getTenantId())
                .workspaceId(properties.getWorkspaceId())
                .correlationId(properties.getCorrelationId())
                .agentToken(properties.getAgentToken())
                .timeout(properties.getTimeout())
                .objectMapper(objectMapper)
                .build();
    }
}
