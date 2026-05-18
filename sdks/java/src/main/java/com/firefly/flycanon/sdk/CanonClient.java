/*
 * Copyright 2026 Firefly Software Solutions Inc.
 * Licensed under the Apache License, Version 2.0.
 */
package com.firefly.flycanon.sdk;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.firefly.flycanon.sdk.model.Models;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatusCode;
import org.springframework.http.MediaType;
import org.springframework.web.client.HttpStatusCodeException;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.ResourceAccessException;

import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Optional;

/**
 * Spring-Boot-native Java client for the flycanon Operational
 * Knowledge Repository service.
 *
 * <p>Built on Spring's {@link RestClient} (sync, blocking; new
 * default in Spring Framework 6.1+) and Jackson. Thread-safe; one
 * instance per service deployment is enough.
 *
 * <p>The companion {@link CanonClientAutoConfiguration} wires a
 * fully-configured bean from ``flycanon.*`` properties so most
 * consumers just inject it:
 *
 * <pre>{@code
 *   @Service
 *   class CopilotService {
 *       private final CanonClient canon;
 *       CopilotService(CanonClient canon) { this.canon = canon; }
 *       ...
 *   }
 * }</pre>
 *
 * Manual construction stays available via {@link #builder()} for
 * multi-tenant deployments that point at different bases per tenant.
 */
public final class CanonClient {

    private final RestClient restClient;
    private final ObjectMapper mapper;

    private CanonClient(Builder builder) {
        this.mapper = builder.mapper != null ? builder.mapper : defaultMapper();
        RestClient.Builder rcb = (builder.restClientBuilder != null
                ? builder.restClientBuilder
                : RestClient.builder())
                .baseUrl(builder.baseUrl)
                .defaultHeader(HttpHeaders.ACCEPT, MediaType.APPLICATION_JSON_VALUE)
                .defaultHeader(HttpHeaders.USER_AGENT, "flycanon-sdk-java/26.5.2");
        Optional.ofNullable(builder.apiKey)
                .filter(k -> !k.isBlank())
                .ifPresent(k -> rcb.defaultHeader(HttpHeaders.AUTHORIZATION, "Bearer " + k));
        this.restClient = rcb.build();
    }

    public static Builder builder() {
        return new Builder();
    }

    private static ObjectMapper defaultMapper() {
        return new ObjectMapper()
                .registerModule(new JavaTimeModule())
                .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
    }

    // ---------- Version ----------

    public Models.VersionInfo version() {
        return request("GET", "/api/v1/version", null, Models.VersionInfo.class);
    }

    // ---------- Sources ----------

    public Models.SourceRecord submitSource(Models.SubmitSourceJsonPayload payload) {
        return request("POST", "/api/v1/sources", payload, Models.SourceRecord.class);
    }

    public Models.SourceRecord getSource(String id) {
        return request("GET", "/api/v1/sources/" + encode(id), null, Models.SourceRecord.class);
    }

    public Models.SourcesPage listSources(Map<String, String> filters) {
        return request("GET", "/api/v1/sources" + queryString(filters), null, Models.SourcesPage.class);
    }

    public Models.BulkSourcesResponse submitSourcesBulk(java.util.List<Models.SubmitSourceJsonPayload> payloads) {
        return request(
                "POST",
                "/api/v1/sources:bulk",
                new Models.BulkSourcesRequest(payloads),
                Models.BulkSourcesResponse.class);
    }

    public Models.IngestJob submitSourceAsync(Models.SubmitSourceJsonPayload payload) {
        return request("POST", "/api/v1/sources:async", payload, Models.IngestJob.class);
    }

    public Models.SourceRecord replaceSource(String id, Models.SubmitSourceJsonPayload payload) {
        return request("PUT", "/api/v1/sources/" + encode(id), payload, Models.SourceRecord.class);
    }

    // ---------- Async ingest jobs ----------

    public Models.IngestJob getJob(String id) {
        return request("GET", "/api/v1/jobs/" + encode(id), null, Models.IngestJob.class);
    }

    public Models.IngestJob cancelJob(String id) {
        return request("POST", "/api/v1/jobs/" + encode(id) + ":cancel", null, Models.IngestJob.class);
    }

    /**
     * Build the URL of the SSE stream for a job. The blocking
     * {@link RestClient} does not consume Server-Sent Events well; this
     * helper returns the absolute URL so callers can wire it into
     * Spring's {@code WebClient} (reactive) or any HTTP/2 streaming
     * client of their choice. Pass {@code cursor=N} to resume from a
     * known offset.
     */
    public String jobStreamUrl(String id, long cursor) {
        return "/api/v1/jobs/" + encode(id) + "/stream?cursor=" + cursor;
    }

    // ---------- Knowledge ----------

    public Models.KnowledgeItem getKnowledge(String id) {
        return request("GET", "/api/v1/knowledge/" + encode(id), null, Models.KnowledgeItem.class);
    }

    public Models.KnowledgeItemsPage listKnowledge(Map<String, String> filters) {
        return request("GET", "/api/v1/knowledge" + queryString(filters), null, Models.KnowledgeItemsPage.class);
    }

    public Models.KnowledgeDiff getDiff(String id, int fromVersion, int toVersion) {
        Map<String, String> q = filters();
        q.put("from_version", String.valueOf(fromVersion));
        q.put("to_version", String.valueOf(toVersion));
        return request(
                "GET",
                "/api/v1/knowledge/" + encode(id) + "/diff" + queryString(q),
                null,
                Models.KnowledgeDiff.class);
    }

    // ---------- Knowledge graph (relations + graph view) ----------

    public Models.RelationsList listRelations(String id) {
        return request(
                "GET",
                "/api/v1/knowledge/" + encode(id) + "/relations",
                null,
                Models.RelationsList.class);
    }

    public Models.KnowledgeRelation addRelation(String id, Models.CreateRelationRequest req) {
        return request(
                "POST",
                "/api/v1/knowledge/" + encode(id) + "/relations",
                req,
                Models.KnowledgeRelation.class);
    }

    public void removeRelation(String relationId) {
        try {
            restClient.delete()
                    .uri("/api/v1/knowledge/relations/" + encode(relationId))
                    .retrieve()
                    .toBodilessEntity();
        } catch (HttpStatusCodeException ex) {
            throw raiseForProblem(ex);
        } catch (ResourceAccessException ex) {
            throw new RuntimeException("flycanon request failed: " + ex.getMessage(), ex);
        }
    }

    public Models.KnowledgeGraph getGraph(Map<String, String> filters) {
        return request(
                "GET",
                "/api/v1/knowledge:graph" + queryString(filters),
                null,
                Models.KnowledgeGraph.class);
    }

    /**
     * Fetch the graph as a Mermaid string (``graph LR ...``). Sends
     * ``Accept: text/vnd.mermaid`` so the controller picks the
     * Mermaid view.
     */
    public String getGraphMermaid(Map<String, String> filters) {
        try {
            return restClient.get()
                    .uri("/api/v1/knowledge:graph" + queryString(filters))
                    .header(HttpHeaders.ACCEPT, "text/vnd.mermaid")
                    .retrieve()
                    .body(String.class);
        } catch (HttpStatusCodeException ex) {
            throw raiseForProblem(ex);
        } catch (ResourceAccessException ex) {
            throw new RuntimeException("flycanon request failed: " + ex.getMessage(), ex);
        }
    }

    // ---------- Knowledge quality scans ----------

    public Models.StaleReport scanStale() {
        return request("GET", "/api/v1/knowledge:stale", null, Models.StaleReport.class);
    }

    public Models.ConflictScanResponse detectConflicts(Models.ConflictScanRequest req) {
        return request(
                "POST",
                "/api/v1/knowledge:detect-conflicts",
                req,
                Models.ConflictScanResponse.class);
    }

    // ---------- Conversations ----------

    public Models.Conversation createConversation(Models.CreateConversationRequest req) {
        return request("POST", "/api/v1/conversations", req, Models.Conversation.class);
    }

    public Models.Conversation getConversation(String id) {
        return request("GET", "/api/v1/conversations/" + encode(id), null, Models.Conversation.class);
    }

    public Models.ConversationTurn addTurn(String conversationId, Models.CreateConversationTurnRequest req) {
        return request(
                "POST",
                "/api/v1/conversations/" + encode(conversationId) + "/turns",
                req,
                Models.ConversationTurn.class);
    }

    public Models.SuggestionsResponse suggestQuestions(String conversationId) {
        return request(
                "POST",
                "/api/v1/conversations/" + encode(conversationId) + "/suggest",
                null,
                Models.SuggestionsResponse.class);
    }

    // ---------- Billing ----------

    public Models.BillingReport billingReport(Map<String, String> filters) {
        return request("GET", "/api/v1/billing" + queryString(filters), null, Models.BillingReport.class);
    }

    // ---------- Query ----------

    public Models.SearchResponse search(String query, int topK) {
        return request(
                "POST",
                "/api/v1/search",
                new Models.SearchRequest(query, topK),
                Models.SearchResponse.class);
    }

    public Models.AnswerResponse answer(String question) {
        return answer(question, 8, null, null);
    }

    public Models.AnswerResponse answer(String question, int topK, String instructions, String model) {
        return request(
                "POST",
                "/api/v1/query",
                new Models.AnswerRequest(question, topK, instructions, model),
                Models.AnswerResponse.class);
    }

    // ---------- Internals ----------

    private <T> T request(String method, String path, Object body, Class<T> responseType) {
        var spec = switch (method.toUpperCase()) {
            case "GET" -> restClient.get().uri(path);
            case "POST" -> {
                var post = restClient.post().uri(path);
                if (body != null) {
                    post.contentType(MediaType.APPLICATION_JSON);
                    post.body(body);
                }
                yield post;
            }
            case "PUT" -> {
                var put = restClient.put().uri(path);
                if (body != null) {
                    put.contentType(MediaType.APPLICATION_JSON);
                    put.body(body);
                }
                yield put;
            }
            case "DELETE" -> restClient.delete().uri(path);
            default -> throw new IllegalArgumentException("unsupported method: " + method);
        };
        try {
            return spec.retrieve().body(responseType);
        } catch (HttpStatusCodeException ex) {
            throw raiseForProblem(ex);
        } catch (ResourceAccessException ex) {
            throw new RuntimeException("flycanon request failed: " + ex.getMessage(), ex);
        }
    }

    private CanonAPIException raiseForProblem(HttpStatusCodeException ex) {
        HttpStatusCode status = ex.getStatusCode();
        String body = ex.getResponseBodyAsString();
        try {
            Models.ProblemDetails problem = mapper.readValue(body, Models.ProblemDetails.class);
            return new CanonAPIException(
                    problem.status() > 0 ? problem.status() : status.value(),
                    problem.code() != null ? problem.code() : "http_error",
                    problem.title() != null ? problem.title() : "HTTP " + status.value(),
                    problem.detail(),
                    problem.extensions());
        } catch (IOException ignored) {
            return new CanonAPIException(
                    status.value(),
                    "http_error",
                    "HTTP " + status.value(),
                    body == null || body.isBlank() ? null : body,
                    null);
        }
    }

    private static String queryString(Map<String, String> filters) {
        if (filters == null || filters.isEmpty()) {
            return "";
        }
        StringBuilder sb = new StringBuilder("?");
        boolean first = true;
        for (Map.Entry<String, String> entry : filters.entrySet()) {
            String value = entry.getValue();
            if (value == null || value.isEmpty()) {
                continue;
            }
            if (!first) {
                sb.append('&');
            }
            sb.append(encode(entry.getKey())).append('=').append(encode(value));
            first = false;
        }
        return sb.length() == 1 ? "" : sb.toString();
    }

    private static String encode(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8);
    }

    /** Convenience builder for query-string filters. */
    public static Map<String, String> filters() {
        return new LinkedHashMap<>();
    }

    // ---------- Builder ----------

    public static final class Builder {
        private String baseUrl;
        private String apiKey;
        private Duration timeout = Duration.ofSeconds(60);
        private RestClient.Builder restClientBuilder;
        private ObjectMapper mapper;

        public Builder baseUrl(String url) {
            this.baseUrl = url;
            return this;
        }

        public Builder apiKey(String key) {
            this.apiKey = key;
            return this;
        }

        public Builder timeout(Duration timeout) {
            this.timeout = timeout;
            return this;
        }

        public Builder restClientBuilder(RestClient.Builder builder) {
            this.restClientBuilder = builder;
            return this;
        }

        public Builder objectMapper(ObjectMapper mapper) {
            this.mapper = mapper;
            return this;
        }

        public CanonClient build() {
            if (baseUrl == null || baseUrl.isBlank()) {
                throw new IllegalArgumentException("baseUrl is required");
            }
            // The timeout currently rides on the RestClient.Builder
            // (consumers can override with their own builder for
            // fine-grained control). Stored here for future use.
            URI.create(baseUrl);
            return new CanonClient(this);
        }
    }
}
