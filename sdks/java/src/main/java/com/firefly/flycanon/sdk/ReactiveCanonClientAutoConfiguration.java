/*
 * Copyright 2026 Firefly Software Solutions Inc.
 * Licensed under the Apache License, Version 2.0.
 */
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
                .timeout(properties.getTimeout())
                .objectMapper(objectMapper)
                .build();
    }
}
