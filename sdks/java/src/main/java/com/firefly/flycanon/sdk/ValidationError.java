/*
 * Copyright 2026 Firefly Software Solutions Inc.
 * Licensed under the Apache License, Version 2.0.
 */
package com.firefly.flycanon.sdk;

import com.firefly.flycanon.sdk.model.Models;

import java.util.List;
import java.util.Map;

/**
 * ``400 invalid_request`` -- per-field validation errors.
 *
 * <p>The {@link #errors()} list carries one {@link Models.FieldError}
 * per failing field, parsed from {@code ProblemDetail.errors}.
 * Callers branch on {@code FieldError.path()} / {@code FieldError.code()}
 * to surface field-level messages.
 */
public class ValidationError extends CanonAPIException {

    public ValidationError(int statusCode,
                           String code,
                           String title,
                           String detail,
                           Map<String, Object> extensions,
                           List<Models.FieldError> errors) {
        super(statusCode, code, title, detail, extensions, errors);
    }
}
