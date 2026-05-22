/*
 * Copyright 2026 Firefly Software Solutions Inc.
 * Licensed under the Apache License, Version 2.0.
 */
package com.firefly.flycanon.sdk;

import com.firefly.flycanon.sdk.model.Models;

import java.util.List;
import java.util.Map;

/**
 * ``403 agent_workspace_not_in_allowlist`` -- the token's allowlist
 * is non-empty and does not include the ``X-Workspace-Id`` header.
 */
public class AgentWorkspaceNotInAllowlist extends CanonAPIException {

    public AgentWorkspaceNotInAllowlist(int statusCode,
                                        String code,
                                        String title,
                                        String detail,
                                        Map<String, Object> extensions,
                                        List<Models.FieldError> errors) {
        super(statusCode, code, title, detail, extensions, errors);
    }
}
