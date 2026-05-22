/*
 * Copyright 2026 Firefly Software Solutions Inc.
 * Licensed under the Apache License, Version 2.0.
 */
package com.firefly.flycanon.sdk;

import com.firefly.flycanon.sdk.model.Models;

import java.util.List;
import java.util.Map;

/**
 * ``403 invalid_agent_token`` -- the token shape is malformed, its
 * prefix is unknown, or the stored hash does not match.
 */
public class InvalidAgentToken extends CanonAPIException {

    public InvalidAgentToken(int statusCode,
                             String code,
                             String title,
                             String detail,
                             Map<String, Object> extensions,
                             List<Models.FieldError> errors) {
        super(statusCode, code, title, detail, extensions, errors);
    }
}
