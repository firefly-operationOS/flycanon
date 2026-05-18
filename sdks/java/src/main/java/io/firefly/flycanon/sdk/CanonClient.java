/*
 * Copyright 2026 Firefly Software Solutions Inc.
 * Licensed under the Apache License, Version 2.0.
 */
package io.firefly.flycanon.sdk;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import io.firefly.flycanon.sdk.model.Models;

import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Optional;

/**
 * Java client for the flycanon Operational Knowledge Repository.
 *
 * <p>Built on {@code java.net.http.HttpClient} and Jackson. Thread-
 * safe; one instance per service deployment is enough.
 */
public final class CanonClient implements AutoCloseable {

    private final HttpClient http;
    private final ObjectMapper mapper;
    private final URI baseUrl;
    private final Optional<String> apiKey;
    private final Duration timeout;

    private CanonClient(Builder builder) {
        this.baseUrl = URI.create(builder.baseUrl);
        this.apiKey = Optional.ofNullable(builder.apiKey);
        this.timeout = builder.timeout;
        this.http = builder.http != null
                ? builder.http
                : HttpClient.newBuilder().connectTimeout(timeout).build();
        this.mapper = builder.mapper != null ? builder.mapper : defaultMapper();
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

    // ---------- Knowledge ----------

    public Models.KnowledgeItem getKnowledge(String id) {
        return request("GET", "/api/v1/knowledge/" + encode(id), null, Models.KnowledgeItem.class);
    }

    public Models.KnowledgeItemsPage listKnowledge(Map<String, String> filters) {
        return request("GET", "/api/v1/knowledge" + queryString(filters), null, Models.KnowledgeItemsPage.class);
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
        HttpRequest.Builder rb = HttpRequest.newBuilder()
                .uri(baseUrl.resolve(path))
                .timeout(timeout)
                .header("Accept", "application/json")
                .header("User-Agent", "flycanon-sdk-java/26.5.1");
        apiKey.ifPresent(k -> rb.header("Authorization", "Bearer " + k));
        if (body != null) {
            try {
                rb.header("Content-Type", "application/json")
                        .method(method, HttpRequest.BodyPublishers.ofByteArray(mapper.writeValueAsBytes(body)));
            } catch (IOException e) {
                throw new RuntimeException("could not serialise request body", e);
            }
        } else {
            rb.method(method, HttpRequest.BodyPublishers.noBody());
        }

        HttpResponse<byte[]> response;
        try {
            response = http.send(rb.build(), HttpResponse.BodyHandlers.ofByteArray());
        } catch (IOException | InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new RuntimeException("flycanon request failed: " + e.getMessage(), e);
        }

        int status = response.statusCode();
        if (status >= 200 && status < 300) {
            if (status == 204 || response.body() == null || response.body().length == 0) {
                return null;
            }
            try {
                return mapper.readValue(response.body(), responseType);
            } catch (IOException e) {
                throw new RuntimeException("could not deserialise response body", e);
            }
        }
        throw raiseForProblem(status, response.body());
    }

    private CanonAPIException raiseForProblem(int status, byte[] body) {
        try {
            Models.ProblemDetails problem = mapper.readValue(body, Models.ProblemDetails.class);
            return new CanonAPIException(
                    problem.status() > 0 ? problem.status() : status,
                    problem.code() != null ? problem.code() : "http_error",
                    problem.title() != null ? problem.title() : "HTTP " + status,
                    problem.detail(),
                    problem.extensions());
        } catch (IOException ignored) {
            return new CanonAPIException(
                    status,
                    "http_error",
                    "HTTP " + status,
                    body == null ? null : new String(body, StandardCharsets.UTF_8),
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

    @Override
    public void close() {
        // HttpClient has no explicit close; reserved for future
        // implementations that own a pool we need to drain.
    }

    // ---------- Builder ----------

    public static final class Builder {
        private String baseUrl;
        private String apiKey;
        private Duration timeout = Duration.ofSeconds(60);
        private HttpClient http;
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

        public Builder httpClient(HttpClient client) {
            this.http = client;
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
            return new CanonClient(this);
        }
    }

    /** Convenience builder for query-string filters. */
    public static Map<String, String> filters() {
        return new LinkedHashMap<>();
    }
}
