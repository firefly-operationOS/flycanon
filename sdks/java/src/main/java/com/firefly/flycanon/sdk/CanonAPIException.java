/*
 * Copyright 2026 Firefly Software Solutions Inc.
 * Licensed under the Apache License, Version 2.0.
 */
package com.firefly.flycanon.sdk;

import java.util.Map;

/**
 * Thrown by {@link CanonClient} on any non-2xx response. Carries the
 * service's stable {@code code} plus the raw ProblemDetails payload
 * so callers can branch without parsing the human-readable detail.
 *
 * <p>Typical usage:
 * <pre>{@code
 *   try {
 *       canonClient.getKnowledge("missing-id");
 *   } catch (CanonAPIException ex) {
 *       if ("knowledge_item_not_found".equals(ex.code())) {
 *           // graceful 404 handling
 *       } else {
 *           throw ex;
 *       }
 *   }
 * }</pre>
 */
public class CanonAPIException extends RuntimeException {

    private final int statusCode;
    private final String code;
    private final String title;
    private final String detail;
    private final Map<String, Object> extensions;

    public CanonAPIException(int statusCode,
                             String code,
                             String title,
                             String detail,
                             Map<String, Object> extensions) {
        super(format(statusCode, code, title, detail));
        this.statusCode = statusCode;
        this.code = code;
        this.title = title;
        this.detail = detail;
        this.extensions = extensions == null ? Map.of() : Map.copyOf(extensions);
    }

    public int statusCode() {
        return statusCode;
    }

    public String code() {
        return code;
    }

    public String title() {
        return title;
    }

    public String detail() {
        return detail;
    }

    public Map<String, Object> extensions() {
        return extensions;
    }

    private static String format(int statusCode, String code, String title, String detail) {
        StringBuilder sb = new StringBuilder()
                .append(statusCode)
                .append(' ')
                .append(code)
                .append(": ")
                .append(title);
        if (detail != null && !detail.isBlank()) {
            sb.append(" -- ").append(detail);
        }
        return sb.toString();
    }
}
