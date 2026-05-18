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

class CanonClientAutoConfigurationTest {

    private final ApplicationContextRunner contextRunner = new ApplicationContextRunner()
            .withConfiguration(AutoConfigurations.of(
                    JacksonAutoConfiguration.class,
                    CanonClientAutoConfiguration.class));

    @Test
    void wiresCanonClientBeanByDefault() {
        contextRunner
                .withPropertyValues("flycanon.base-url=http://localhost:8500")
                .run(context -> {
                    assertThat(context).hasSingleBean(CanonClient.class);
                    assertThat(context).hasSingleBean(CanonClientProperties.class);
                });
    }

    @Test
    void skipsAutowireWhenDisabled() {
        contextRunner
                .withPropertyValues(
                        "flycanon.base-url=http://localhost:8500",
                        "flycanon.auto-configure=false")
                .run(context -> assertThat(context).doesNotHaveBean(CanonClient.class));
    }

    @Test
    void honoursCustomClientBean() {
        contextRunner
                .withUserConfiguration(CustomClientConfiguration.class)
                .withPropertyValues("flycanon.base-url=http://localhost:8500")
                .run(context -> {
                    assertThat(context).hasSingleBean(CanonClient.class);
                    assertThat(context.getBean(CanonClient.class)).isNotNull();
                });
    }

    static class CustomClientConfiguration {
        org.springframework.context.annotation.Bean
        CanonClient canonClient() {
            return CanonClient.builder().baseUrl("http://override").build();
        }
    }
}
