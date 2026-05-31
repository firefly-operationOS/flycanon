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

import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class CanonAPIExceptionTest {

    @Test
    void carriesCodeStatusAndExtensions() {
        CanonAPIException ex = new CanonAPIException(
                404,
                "knowledge_item_not_found",
                "Knowledge item not found",
                "knowledge item 'missing' not found",
                Map.of("item_id", "missing"));

        assertEquals(404, ex.statusCode());
        assertEquals("knowledge_item_not_found", ex.code());
        assertEquals("missing", ex.extensions().get("item_id"));
        assertTrue(ex.getMessage().contains("knowledge_item_not_found"));
    }
}
