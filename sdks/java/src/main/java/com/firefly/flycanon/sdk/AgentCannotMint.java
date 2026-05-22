/*
 * Copyright 2026 Firefly Software Solutions Inc.
 * Licensed under the Apache License, Version 2.0.
 */
package com.firefly.flycanon.sdk;

import com.firefly.flycanon.sdk.model.Models;

import java.util.List;
import java.util.Map;

/**
 * ``403 agent_cannot_mint`` -- an agent-tier caller tried to use
 * the user-tier ``/api/v1/agent-tokens`` CRUD surface.
 */
public class AgentCannotMint extends CanonAPIException {

    public AgentCannotMint(int statusCode,
                           String code,
                           String title,
                           String detail,
                           Map<String, Object> extensions,
                           List<Models.FieldError> errors) {
        super(statusCode, code, title, detail, extensions, errors);
    }
}
