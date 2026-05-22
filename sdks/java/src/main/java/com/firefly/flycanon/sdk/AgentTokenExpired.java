/*
 * Copyright 2026 Firefly Software Solutions Inc.
 * Licensed under the Apache License, Version 2.0.
 */
package com.firefly.flycanon.sdk;

import com.firefly.flycanon.sdk.model.Models;

import java.util.List;
import java.util.Map;

/**
 * ``403 agent_token_expired`` -- the token's ``expires_at`` is in
 * the past.
 */
public class AgentTokenExpired extends CanonAPIException {

    public AgentTokenExpired(int statusCode,
                             String code,
                             String title,
                             String detail,
                             Map<String, Object> extensions,
                             List<Models.FieldError> errors) {
        super(statusCode, code, title, detail, extensions, errors);
    }
}
