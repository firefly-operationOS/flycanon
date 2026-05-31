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

    @org.springframework.context.annotation.Configuration
    static class CustomClientConfiguration {
        @org.springframework.context.annotation.Bean
        CanonClient canonClient() {
            return CanonClient.builder().baseUrl("http://override").build();
        }
    }
}
