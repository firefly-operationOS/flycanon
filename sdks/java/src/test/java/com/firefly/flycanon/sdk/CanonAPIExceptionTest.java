/*
 * Copyright 2026 Firefly Software Solutions Inc.
 * Licensed under the Apache License, Version 2.0.
 */
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
