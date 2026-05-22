/*
 * Copyright 2026 Firefly Software Solutions Inc.
 * Licensed under the Apache License, Version 2.0.
 */
package com.firefly.flycanon.sdk;

import com.firefly.flycanon.sdk.model.Models;

import java.util.List;
import java.util.Map;

/**
 * ``401 missing_agent_token`` -- ``X-Agent-Token`` is required for
 * ``/api/v1/agent/*`` routes but was not sent.
 */
public class MissingAgentToken extends CanonAPIException {

    public MissingAgentToken(int statusCode,
                             String code,
                             String title,
                             String detail,
                             Map<String, Object> extensions,
                             List<Models.FieldError> errors) {
        super(statusCode, code, title, detail, extensions, errors);
    }
}
