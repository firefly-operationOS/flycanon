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

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.time.Duration;

/**
 * Configuration properties for the flycanon SDK.
 *
 * <p>Bound to ``flycanon.*`` in ``application.yml`` /
 * ``application.properties``:
 *
 * <pre>{@code
 *   flycanon:
 *     base-url: http://localhost:8500
 *     api-key: ${FLYCANON_API_KEY:}
 *     tenant-id: ${TENANT_ID:}
 *     workspace-id: ${WORKSPACE_ID:}
 *     correlation-id: ${CORRELATION_ID:}
 *     agent-token: ${FLYCANON_AGENT_TOKEN:}
 *     timeout: 60s
 * }</pre>
 *
 * <p>The 26.5.7 unification adds four firefly wire-contract headers
 * (``tenant-id`` -> ``X-Tenant-Id``, ``workspace-id`` ->
 * ``X-Workspace-Id``, ``correlation-id`` -> ``X-Correlation-Id``,
 * ``agent-token`` -> ``X-Agent-Token``). Every property is optional:
 * the SDK only emits the corresponding header when the value is set,
 * and the service rejects missing tenant/workspace headers at the
 * boundary so callers see a typed error instead of a silent default.
 *
 * <p>The Spring Boot autoconfiguration ({@link CanonClientAutoConfiguration})
 * builds a {@link CanonClient} from these values automatically when the
 * SDK is on the classpath.
 */
@ConfigurationProperties(prefix = "flycanon")
public class CanonClientProperties {

    /**
     * Base URL of the running flycanon service.
     * Example: ``http://localhost:8500`` or ``https://canon.example.com``.
     */
    private String baseUrl = "http://localhost:8500";

    /**
     * Optional API key. When set, every request carries
     * ``Authorization: Bearer <api-key>``.
     */
    private String apiKey;

    /**
     * Optional tenant identifier. When set, every request carries
     * ``X-Tenant-Id: <tenant-id>``. The service rejects missing
     * tenant headers at the boundary for tenant-scoped routes; the
     * SDK does not pre-validate -- it forwards whatever the caller
     * supplies.
     */
    private String tenantId;

    /**
     * Optional workspace identifier. When set, every request carries
     * ``X-Workspace-Id: <workspace-id>``. The service rejects missing
     * workspace headers at the boundary for workspace-scoped routes.
     */
    private String workspaceId;

    /**
     * Optional correlation identifier. When set, every request carries
     * ``X-Correlation-Id: <correlation-id>``. Most callers rotate the
     * correlation id per call rather than fix it on the client
     * properties -- the SDK accepts it here for static-id deployments
     * (e.g., a batch worker tagging every outbound request).
     */
    private String correlationId;

    /**
     * Optional agent bearer token. When set, every request carries
     * ``X-Agent-Token: <agent-token>``. Only meaningful for
     * ``/api/v1/agent/*`` routes -- the user-tier surface ignores
     * the header. Refused on the user-tier
     * ``POST/GET/DELETE /api/v1/agent-tokens`` CRUD via
     * ``403 agent_cannot_mint``.
     */
    private String agentToken;

    /**
     * Per-request timeout. Applies to both connect and read phases of the
     * RestClient.
     */
    private Duration timeout = Duration.ofSeconds(60);

    /**
     * Whether the autoconfiguration registers the (blocking)
     * {@link CanonClient} bean. Set to false when you build the
     * client manually (e.g. for multi-tenant deployments that point
     * at different bases per tenant).
     */
    private boolean autoConfigure = true;

    /**
     * Whether the autoconfiguration registers the reactive
     * {@link ReactiveCanonClient} bean. Off by default so consumers
     * that don't depend on Spring WebFlux don't pay for the bean.
     * Set to ``true`` (and depend on ``spring-webflux`` +
     * ``reactor-netty-http``) to opt in.
     */
    private boolean reactiveAutoConfigure = false;

    public String getBaseUrl() {
        return baseUrl;
    }

    public void setBaseUrl(String baseUrl) {
        this.baseUrl = baseUrl;
    }

    public String getApiKey() {
        return apiKey;
    }

    public void setApiKey(String apiKey) {
        this.apiKey = apiKey;
    }

    public String getTenantId() {
        return tenantId;
    }

    public void setTenantId(String tenantId) {
        this.tenantId = tenantId;
    }

    public String getWorkspaceId() {
        return workspaceId;
    }

    public void setWorkspaceId(String workspaceId) {
        this.workspaceId = workspaceId;
    }

    public String getCorrelationId() {
        return correlationId;
    }

    public void setCorrelationId(String correlationId) {
        this.correlationId = correlationId;
    }

    public String getAgentToken() {
        return agentToken;
    }

    public void setAgentToken(String agentToken) {
        this.agentToken = agentToken;
    }

    public Duration getTimeout() {
        return timeout;
    }

    public void setTimeout(Duration timeout) {
        this.timeout = timeout;
    }

    public boolean isAutoConfigure() {
        return autoConfigure;
    }

    public void setAutoConfigure(boolean autoConfigure) {
        this.autoConfigure = autoConfigure;
    }

    public boolean isReactiveAutoConfigure() {
        return reactiveAutoConfigure;
    }

    public void setReactiveAutoConfigure(boolean reactiveAutoConfigure) {
        this.reactiveAutoConfigure = reactiveAutoConfigure;
    }
}
