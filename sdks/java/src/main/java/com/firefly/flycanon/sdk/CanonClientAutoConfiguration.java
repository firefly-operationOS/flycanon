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
import org.springframework.web.client.RestClient;

/**
 * Spring Boot autoconfiguration for the flycanon SDK.
 *
 * <p>Registers a {@link CanonClient} bean from the ``flycanon.*``
 * properties when the SDK is on the classpath and
 * ``flycanon.auto-configure`` is not explicitly set to ``false``.
 *
 * <p>Disabling the autoconfig (per-tenant builders, mock clients in
 * tests) is one line of YAML:
 *
 * <pre>{@code
 *   flycanon:
 *     auto-configure: false
 * }</pre>
 *
 * Consumers can then build their own {@link CanonClient} instances
 * via {@link CanonClient#builder()}.
 */
@AutoConfiguration
@ConditionalOnClass(RestClient.class)
@EnableConfigurationProperties(CanonClientProperties.class)
public class CanonClientAutoConfiguration {

    @Bean
    @ConditionalOnMissingBean
    @ConditionalOnProperty(prefix = "flycanon", name = "auto-configure", havingValue = "true", matchIfMissing = true)
    public CanonClient canonClient(
            CanonClientProperties properties,
            ObjectMapper objectMapper) {
        return CanonClient.builder()
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
