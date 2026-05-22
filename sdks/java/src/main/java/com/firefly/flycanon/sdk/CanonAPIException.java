/*
 * Copyright 2026 Firefly Software Solutions Inc.
 * Licensed under the Apache License, Version 2.0.
 */
package com.firefly.flycanon.sdk;

import com.fasterxml.jackson.databind.JsonNode;
import com.firefly.flycanon.sdk.model.Models;

import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;
import java.util.Map;

/**
 * Thrown by {@link CanonClient} on any non-2xx response. Carries the
 * service's stable {@code code} from the RFC 7807 ProblemDetails
 * payload plus the raw {@code errors} array (per-field validation
 * rows) so callers can branch without parsing the human-readable
 * detail.
 *
 * <p>The 26.5.7 unification adds typed subclasses keyed by the
 * service's stable RFC 7807 {@code code} field so callers can
 * {@code catch (InvalidAgentToken e)} instead of branching on
 * {@code "invalid_agent_token".equals(ex.code())}. The
 * {@link #fromProblemDetail} dispatcher returns the right subclass
 * (or this base class for unknown codes), so older callers that only
 * catch the base type keep working.
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
 *
 * @see Models.FieldError
 * @see MissingIdempotencyKey
 * @see MissingAgentToken
 * @see InvalidAgentToken
 * @see AgentTokenExpired
 * @see AgentWorkspaceNotInAllowlist
 * @see AgentScopeDenied
 * @see AgentCannotMint
 * @see ValidationError
 */
public class CanonAPIException extends RuntimeException {

    private final int statusCode;
    private final String code;
    private final String title;
    private final String detail;
    private final Map<String, Object> extensions;
    private final List<Models.FieldError> errors;

    /**
     * Construct an exception without per-field {@code errors[]} (the
     * common case -- only {@link ValidationError} normally carries
     * a non-empty list).
     */
    public CanonAPIException(int statusCode,
                             String code,
                             String title,
                             String detail,
                             Map<String, Object> extensions) {
        this(statusCode, code, title, detail, extensions, List.of());
    }

    /**
     * Construct an exception with a structured per-field
     * {@code errors[]} list parsed from the RFC 7807 envelope.
     */
    public CanonAPIException(int statusCode,
                             String code,
                             String title,
                             String detail,
                             Map<String, Object> extensions,
                             List<Models.FieldError> errors) {
        super(format(statusCode, code, title, detail));
        this.statusCode = statusCode;
        this.code = code;
        this.title = title;
        this.detail = detail;
        this.extensions = extensions == null ? Map.of() : Map.copyOf(extensions);
        this.errors = errors == null ? List.of() : List.copyOf(errors);
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

    /**
     * Per-field validation rows parsed from
     * {@code ProblemDetail.errors}. Empty for non-validation
     * errors. Most useful on {@link ValidationError} but available
     * on every typed subclass that may carry the array.
     */
    public List<Models.FieldError> errors() {
        return errors;
    }

    /**
     * Build the right {@link CanonAPIException} subclass from a
     * parsed {@link Models.ProblemDetails} envelope.
     *
     * <p>Known {@code code} values dispatch to typed subclasses
     * (e.g. {@code "invalid_agent_token"} -> {@link InvalidAgentToken});
     * unknown codes fall back to this base class so older clients
     * talking to newer services still get a usable exception. The
     * {@code errors[]} array is parsed via
     * {@link Models#parseFieldErrors(JsonNode)} when present so
     * validation rows are always typed.
     *
     * @param problem the parsed envelope (may be null on a non-JSON
     *                error body -- in that case pass {@code null} and
     *                {@code errorsNode = null}).
     * @param fallbackStatus the HTTP status from the response when
     *                       {@code problem.status()} is zero.
     * @param errorsNode the raw {@code errors} sub-tree (Jackson
     *                   {@link JsonNode}), or {@code null} when the
     *                   envelope did not carry the array.
     */
    public static CanonAPIException fromProblemDetail(Models.ProblemDetails problem,
                                                     int fallbackStatus,
                                                     JsonNode errorsNode) {
        int status = problem != null && problem.status() > 0 ? problem.status() : fallbackStatus;
        String code = problem != null && problem.code() != null ? problem.code() : "http_error";
        String title = problem != null && problem.title() != null ? problem.title() : "HTTP " + status;
        String detail = problem != null ? problem.detail() : null;
        Map<String, Object> extensions = problem != null ? problem.extensions() : null;
        List<Models.FieldError> fieldErrors = Models.parseFieldErrors(errorsNode);
        return switch (code) {
            case "invalid_request" -> new ValidationError(status, code, title, detail, extensions, fieldErrors);
            case "missing_idempotency_key" -> new MissingIdempotencyKey(status, code, title, detail, extensions, fieldErrors);
            case "missing_agent_token" -> new MissingAgentToken(status, code, title, detail, extensions, fieldErrors);
            case "invalid_agent_token" -> new InvalidAgentToken(status, code, title, detail, extensions, fieldErrors);
            case "agent_token_expired" -> new AgentTokenExpired(status, code, title, detail, extensions, fieldErrors);
            case "agent_workspace_not_in_allowlist" -> new AgentWorkspaceNotInAllowlist(status, code, title, detail, extensions, fieldErrors);
            case "agent_scope_denied" -> new AgentScopeDenied(status, code, title, detail, extensions, fieldErrors);
            case "agent_cannot_mint" -> new AgentCannotMint(status, code, title, detail, extensions, fieldErrors);
            default -> new CanonAPIException(status, code, title, detail, extensions, fieldErrors);
        };
    }

    /**
     * Convenience overload: parse the {@code errors[]} array out of
     * a raw {@link JsonNode} tree (the typical case where the
     * caller has the full ProblemDetails JSON in hand).
     */
    static List<Models.FieldError> extractErrors(JsonNode rootNode) {
        if (rootNode == null || !rootNode.has("errors")) {
            return List.of();
        }
        JsonNode errorsNode = rootNode.get("errors");
        if (errorsNode == null || !errorsNode.isArray()) {
            return List.of();
        }
        List<Models.FieldError> out = new ArrayList<>();
        Iterator<JsonNode> it = errorsNode.elements();
        while (it.hasNext()) {
            JsonNode entry = it.next();
            if (entry == null || !entry.isObject()) {
                continue;
            }
            out.add(new Models.FieldError(
                    entry.path("code").asText(""),
                    entry.path("path").asText(""),
                    entry.path("message").asText("")));
        }
        return out;
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
