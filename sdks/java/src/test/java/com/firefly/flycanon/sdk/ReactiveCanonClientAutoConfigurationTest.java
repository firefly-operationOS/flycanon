/*
 * Copyright 2026 Firefly Software Solutions Inc.
 * Licensed under the Apache License, Version 2.0.
 */
package com.firefly.flycanon.sdk;

import org.junit.jupiter.api.Test;
import org.springframework.boot.autoconfigure.AutoConfigurations;
import org.springframework.boot.autoconfigure.jackson.JacksonAutoConfiguration;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;

import static org.assertj.core.api.Assertions.assertThat;

class ReactiveCanonClientAutoConfigurationTest {

    private final ApplicationContextRunner contextRunner = new ApplicationContextRunner()
            .withConfiguration(AutoConfigurations.of(
                    JacksonAutoConfiguration.class,
                    ReactiveCanonClientAutoConfiguration.class));

    @Test
    void doesNotWireReactiveClientByDefault() {
        contextRunner
                .withPropertyValues("flycanon.base-url=http://localhost:8500")
                .run(context -> {
                    // Reactive auto-config is opt-in -- consumers who only
                    // want the blocking CanonClient should not pay for a
                    // reactor / netty bean by accident.
                    assertThat(context).doesNotHaveBean(ReactiveCanonClient.class);
                });
    }

    @Test
    void wiresReactiveClientWhenEnabled() {
        contextRunner
                .withPropertyValues(
                        "flycanon.base-url=http://localhost:8500",
                        "flycanon.reactive-auto-configure=true")
                .run(context -> {
                    assertThat(context).hasSingleBean(ReactiveCanonClient.class);
                    assertThat(context).hasSingleBean(CanonClientProperties.class);
                });
    }

    @Test
    void honoursCustomReactiveClientBean() {
        contextRunner
                .withUserConfiguration(CustomReactiveClientConfiguration.class)
                .withPropertyValues(
                        "flycanon.base-url=http://localhost:8500",
                        "flycanon.reactive-auto-configure=true")
                .run(context -> {
                    assertThat(context).hasSingleBean(ReactiveCanonClient.class);
                    assertThat(context.getBean(ReactiveCanonClient.class)).isNotNull();
                });
    }

    @org.springframework.context.annotation.Configuration
    static class CustomReactiveClientConfiguration {
        @org.springframework.context.annotation.Bean
        ReactiveCanonClient reactiveCanonClient() {
            return ReactiveCanonClient.builder().baseUrl("http://override").build();
        }
    }
}
