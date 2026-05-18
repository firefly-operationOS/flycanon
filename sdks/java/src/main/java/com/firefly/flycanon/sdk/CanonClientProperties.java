/*
 * Copyright 2026 Firefly Software Solutions Inc.
 * Licensed under the Apache License, Version 2.0.
 */
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
 *     timeout: 60s
 * }</pre>
 *
 * The Spring Boot autoconfiguration ({@link CanonClientAutoConfiguration})
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
     * Per-request timeout. Applies to both connect and read phases of the
     * RestClient.
     */
    private Duration timeout = Duration.ofSeconds(60);

    /**
     * Whether the autoconfiguration registers the CanonClient bean. Set
     * to false when you build the client manually (e.g. for multi-tenant
     * deployments that point at different bases per tenant).
     */
    private boolean autoConfigure = true;

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
}
