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
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.firefly.flycanon.sdk.model.Models;
import org.junit.jupiter.api.Test;

import java.time.OffsetDateTime;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * JSON round-trip tests for the workspace lifecycle event DTOs.
 *
 * <p>Mirrors the Python SDK's byte-for-byte field naming so a
 * consumer reading {@code canon.workspaces.v1} from a Kafka topic
 * can deserialise event payloads emitted by either SDK
 * interchangeably.
 */
class WorkspaceEventDtoTest {

    private final ObjectMapper mapper = new ObjectMapper().registerModule(new JavaTimeModule());

    @Test
    void canonWorkspacesTopicMatchesPythonConstant() {
        assertThat(Models.CANON_WORKSPACES_TOPIC).isEqualTo("canon.workspaces.v1");
    }

    @Test
    void workspaceCreatedDeserialisesFromJson() throws Exception {
        String json = "{"
                + "\"event_type\":\"workspace.created\","
                + "\"tenant_id\":\"acme\","
                + "\"workspace_id\":\"ws-1\","
                + "\"occurred_at\":\"2026-05-22T10:00:00Z\","
                + "\"name\":\"Alpha\","
                + "\"scope\":null,"
                + "\"sme_roster\":null,"
                + "\"retention_days\":30,"
                + "\"jurisdiction\":\"EU\"}";

        Models.WorkspaceCreated event = mapper.readValue(json, Models.WorkspaceCreated.class);

        assertThat(event.eventType()).isEqualTo("workspace.created");
        assertThat(event.tenantId()).isEqualTo("acme");
        assertThat(event.workspaceId()).isEqualTo("ws-1");
        assertThat(event.name()).isEqualTo("Alpha");
        assertThat(event.retentionDays()).isEqualTo(30);
        assertThat(event.jurisdiction()).isEqualTo("EU");
        assertThat(event.occurredAt()).isNotNull();
    }

    @Test
    void workspaceCreatedDefaultsEventTypeWhenAbsent() {
        // The event_type literal defaults if a downstream consumer
        // constructs the record programmatically without the field.
        Models.WorkspaceCreated event = new Models.WorkspaceCreated(
                null, "acme", "ws-1", OffsetDateTime.parse("2026-05-22T10:00:00Z"),
                "Alpha", null, null, null, null);

        assertThat(event.eventType()).isEqualTo("workspace.created");
    }

    @Test
    void workspaceUpdatedDeserialisesFromJson() throws Exception {
        String json = "{"
                + "\"event_type\":\"workspace.updated\","
                + "\"tenant_id\":\"acme\","
                + "\"workspace_id\":\"ws-1\","
                + "\"occurred_at\":\"2026-05-22T11:00:00Z\","
                + "\"name\":\"Beta\","
                + "\"scope\":null,"
                + "\"sme_roster\":null,"
                + "\"retention_days\":null,"
                + "\"jurisdiction\":null}";

        Models.WorkspaceUpdated event = mapper.readValue(json, Models.WorkspaceUpdated.class);

        assertThat(event.eventType()).isEqualTo("workspace.updated");
        assertThat(event.name()).isEqualTo("Beta");
    }

    @Test
    void workspaceDeletedDeserialisesFromJson() throws Exception {
        String json = "{"
                + "\"event_type\":\"workspace.deleted\","
                + "\"tenant_id\":\"acme\","
                + "\"workspace_id\":\"ws-1\","
                + "\"occurred_at\":\"2026-05-22T12:00:00Z\"}";

        Models.WorkspaceDeleted event = mapper.readValue(json, Models.WorkspaceDeleted.class);

        assertThat(event.eventType()).isEqualTo("workspace.deleted");
        assertThat(event.tenantId()).isEqualTo("acme");
        assertThat(event.workspaceId()).isEqualTo("ws-1");
    }

    @Test
    void roundTripPreservesAllFields() throws Exception {
        Models.WorkspaceCreated original = new Models.WorkspaceCreated(
                "workspace.created",
                "acme",
                "ws-1",
                OffsetDateTime.parse("2026-05-22T10:00:00Z"),
                "Alpha",
                null,
                null,
                30,
                "EU");

        String json = mapper.writeValueAsString(original);
        Models.WorkspaceCreated parsed = mapper.readValue(json, Models.WorkspaceCreated.class);

        assertThat(parsed.eventType()).isEqualTo(original.eventType());
        assertThat(parsed.tenantId()).isEqualTo(original.tenantId());
        assertThat(parsed.workspaceId()).isEqualTo(original.workspaceId());
        assertThat(parsed.name()).isEqualTo(original.name());
        assertThat(parsed.retentionDays()).isEqualTo(original.retentionDays());
        assertThat(parsed.jurisdiction()).isEqualTo(original.jurisdiction());
    }

    @Test
    void fieldErrorParsesFromJson() throws Exception {
        String json = "{\"code\":\"too_short\",\"path\":\"name\",\"message\":\"min length 1\"}";

        Models.FieldError err = mapper.readValue(json, Models.FieldError.class);

        assertThat(err.code()).isEqualTo("too_short");
        assertThat(err.path()).isEqualTo("name");
        assertThat(err.message()).isEqualTo("min length 1");
    }
}
