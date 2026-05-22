/*
 * Copyright 2026 Firefly Software Solutions Inc.
 * Licensed under the Apache License, Version 2.0.
 */
package com.firefly.flycanon.sdk;

import com.firefly.flycanon.sdk.model.Models;

import java.util.List;
import java.util.Map;

/**
 * ``400 missing_idempotency_key`` -- an agent-tier POST was sent
 * without the mandatory ``Idempotency-Key`` header.
 *
 * <p>The SDK validates the argument locally before the round-trip
 * so the typical path is a {@link IllegalArgumentException} thrown
 * from {@link CanonClient.AgentSurface} -- this exception type
 * surfaces when the service rejects a request that bypassed the
 * client-side check (e.g., a header dropped by an intermediary).
 */
public class MissingIdempotencyKey extends CanonAPIException {

    public MissingIdempotencyKey(int statusCode,
                                 String code,
                                 String title,
                                 String detail,
                                 Map<String, Object> extensions,
                                 List<Models.FieldError> errors) {
        super(statusCode, code, title, detail, extensions, errors);
    }
}
