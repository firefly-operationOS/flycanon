/*
 * Copyright 2026 Firefly Software Solutions Inc.
 * Licensed under the Apache License, Version 2.0.
 */
package com.firefly.flycanon.sdk;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.firefly.flycanon.sdk.model.Models;
import io.netty.channel.ChannelOption;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.codec.ServerSentEvent;
import org.springframework.http.codec.json.Jackson2JsonDecoder;
import org.springframework.http.codec.json.Jackson2JsonEncoder;
import org.springframework.web.reactive.function.client.ExchangeStrategies;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.reactive.function.client.WebClientResponseException;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;
import reactor.netty.http.client.HttpClient;

import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/**
 * Non-blocking reactive client for the flycanon Operational Knowledge
 * Repository service.
 *
 * <p>Built on Spring's {@link WebClient} + Reactor Netty. Mirrors
 * every public endpoint exposed by {@link CanonClient} but returns
 * {@link Mono} for unary calls and {@link Flux} for the Server-Sent
 * Events streams ({@code /api/v1/jobs/&#123;id&#125;/stream},
 * {@code /api/v1/query/stream}).
 *
 * <p>Pick between the two clients based on the calling code:
 *
 * <ul>
 *   <li>If your stack is plain Spring MVC / Servlet, use
 *       {@link CanonClient} (blocking).</li>
 *   <li>If your stack is Spring WebFlux, R2DBC, or any reactive
 *       chain, use {@link ReactiveCanonClient}.</li>
 *   <li>For real SSE streaming (job progress, token streaming),
 *       use {@link ReactiveCanonClient} regardless of the rest of
 *       your stack -- the blocking {@link CanonClient} can only
 *       hand back the SSE URL.</li>
 * </ul>
 *
 * <p>Thread-safe; one instance per service deployment is enough.
 *
 * <p>Auto-wire by depending on {@code spring-webflux} +
 * {@code reactor-netty-http} (or the
 * {@code spring-boot-starter-webflux} starter that bundles both)
 * and setting:
 *
 * <pre>{@code
 *   flycanon:
 *     base-url: https://canon.internal.example.com
 *     reactive-auto-configure: true
 * }</pre>
 *
 * <p>Manual construction stays available via
 * {@link #builder()} for fine-grained control over the underlying
 * {@code WebClient.Builder}.
 */
public final class ReactiveCanonClient {

    private static final ParameterizedTypeReference<ServerSentEvent<Map<String, Object>>>
            SSE_FRAME_TYPE = new ParameterizedTypeReference<>() {};
    private static final TypeReference<Models.ProblemDetails> PROBLEM_TYPE =
            new TypeReference<>() {};

    private final WebClient webClient;
    private final ObjectMapper mapper;

    private ReactiveCanonClient(Builder builder) {
        this.mapper = builder.mapper != null ? builder.mapper : defaultMapper();

        Duration timeout = builder.timeout != null ? builder.timeout : Duration.ofSeconds(60);
        HttpClient httpClient = HttpClient.create()
                .option(ChannelOption.CONNECT_TIMEOUT_MILLIS, (int) Math.min(timeout.toMillis(), Integer.MAX_VALUE))
                .responseTimeout(timeout);

        ExchangeStrategies strategies = ExchangeStrategies.builder()
                .codecs(c -> {
                    c.defaultCodecs().jackson2JsonDecoder(new Jackson2JsonDecoder(mapper));
                    c.defaultCodecs().jackson2JsonEncoder(new Jackson2JsonEncoder(mapper));
                    // SSE response bodies can be large for long job runs;
                    // raise the in-memory ceiling so frames never get
                    // truncated mid-stream.
                    c.defaultCodecs().maxInMemorySize(4 * 1024 * 1024);
                })
                .build();

        WebClient.Builder wcb = (builder.webClientBuilder != null
                ? builder.webClientBuilder
                : WebClient.builder())
                .baseUrl(builder.baseUrl)
                .clientConnector(new org.springframework.http.client.reactive.ReactorClientHttpConnector(httpClient))
                .exchangeStrategies(strategies)
                .defaultHeader(HttpHeaders.ACCEPT, MediaType.APPLICATION_JSON_VALUE)
                .defaultHeader(HttpHeaders.USER_AGENT, "flycanon-sdk-java/26.5.6");
        Optional.ofNullable(builder.apiKey)
                .filter(k -> !k.isBlank())
                .ifPresent(k -> wcb.defaultHeader(HttpHeaders.AUTHORIZATION, "Bearer " + k));
        this.webClient = wcb.build();
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

    public Mono<Models.VersionInfo> version() {
        return get("/api/v1/version", Models.VersionInfo.class);
    }

    // ---------- Sources ----------

    public Mono<Models.SourceRecord> submitSource(Models.SubmitSourceJsonPayload payload) {
        return post("/api/v1/sources", payload, Models.SourceRecord.class);
    }

    public Mono<Models.SourceRecord> getSource(String id) {
        return get("/api/v1/sources/" + encode(id), Models.SourceRecord.class);
    }

    public Mono<Models.SourcesPage> listSources(Map<String, String> filters) {
        return get("/api/v1/sources" + queryString(filters), Models.SourcesPage.class);
    }

    public Mono<Models.BulkSourcesResponse> submitSourcesBulk(List<Models.SubmitSourceJsonPayload> payloads) {
        return post("/api/v1/sources:bulk", new Models.BulkSourcesRequest(payloads),
                Models.BulkSourcesResponse.class);
    }

    public Mono<Models.IngestJob> submitSourceAsync(Models.SubmitSourceJsonPayload payload) {
        return post("/api/v1/sources:async", payload, Models.IngestJob.class);
    }

    public Mono<Models.SourceRecord> replaceSource(String id, Models.SubmitSourceJsonPayload payload) {
        return put("/api/v1/sources/" + encode(id), payload, Models.SourceRecord.class);
    }

    // ---------- Async ingest jobs ----------

    public Mono<Models.IngestJob> getJob(String id) {
        return get("/api/v1/jobs/" + encode(id), Models.IngestJob.class);
    }

    public Mono<Models.IngestJob> cancelJob(String id) {
        return post("/api/v1/jobs/" + encode(id) + ":cancel", null, Models.IngestJob.class);
    }

    /**
     * Stream Server-Sent Events for an async-ingest job. Each
     * {@link Models.StreamFrame} is one SSE frame; the stream
     * terminates when the connection closes (after {@code completed}
     * or {@code failed}). Reconnect with a non-zero {@code cursor} to
     * resume past frames the client has already processed.
     */
    public Flux<Models.StreamFrame> streamJob(String jobId, long cursor) {
        return webClient.get()
                .uri(uri -> uri.path("/api/v1/jobs/{id}/stream").queryParam("cursor", cursor).build(jobId))
                .accept(MediaType.TEXT_EVENT_STREAM)
                .retrieve()
                .bodyToFlux(SSE_FRAME_TYPE)
                .map(ReactiveCanonClient::toFrame)
                .onErrorMap(WebClientResponseException.class, this::raiseForProblem);
    }

    // ---------- Knowledge ----------

    public Mono<Models.KnowledgeItem> getKnowledge(String id) {
        return get("/api/v1/knowledge/" + encode(id), Models.KnowledgeItem.class);
    }

    public Mono<Models.KnowledgeItemsPage> listKnowledge(Map<String, String> filters) {
        return get("/api/v1/knowledge" + queryString(filters), Models.KnowledgeItemsPage.class);
    }

    public Mono<Models.KnowledgeDiff> getDiff(String id, int fromVersion, int toVersion) {
        Map<String, String> q = new LinkedHashMap<>();
        q.put("from_version", String.valueOf(fromVersion));
        q.put("to_version", String.valueOf(toVersion));
        return get("/api/v1/knowledge/" + encode(id) + "/diff" + queryString(q), Models.KnowledgeDiff.class);
    }

    // ---------- Knowledge graph (relations + graph view) ----------

    public Mono<Models.RelationsList> listRelations(String id) {
        return get("/api/v1/knowledge/" + encode(id) + "/relations", Models.RelationsList.class);
    }

    public Mono<Models.KnowledgeRelation> addRelation(String id, Models.CreateRelationRequest req) {
        return post("/api/v1/knowledge/" + encode(id) + "/relations", req, Models.KnowledgeRelation.class);
    }

    public Mono<Void> removeRelation(String relationId) {
        return webClient.delete()
                .uri("/api/v1/knowledge/relations/" + encode(relationId))
                .retrieve()
                .toBodilessEntity()
                .onErrorMap(WebClientResponseException.class, this::raiseForProblem)
                .then();
    }

    public Mono<Models.KnowledgeGraph> getGraph(Map<String, String> filters) {
        return get("/api/v1/knowledge:graph" + queryString(filters), Models.KnowledgeGraph.class);
    }

    /** Fetch the graph as a Mermaid string ({@code graph LR ...}). */
    public Mono<String> getGraphMermaid(Map<String, String> filters) {
        return webClient.get()
                .uri("/api/v1/knowledge:graph" + queryString(filters))
                .header(HttpHeaders.ACCEPT, "text/vnd.mermaid")
                .retrieve()
                .bodyToMono(String.class)
                .onErrorMap(WebClientResponseException.class, this::raiseForProblem);
    }

    // ---------- Knowledge quality scans ----------

    public Mono<Models.StaleReport> scanStale() {
        return get("/api/v1/knowledge:stale", Models.StaleReport.class);
    }

    public Mono<Models.ConflictScanResponse> detectConflicts(Models.ConflictScanRequest req) {
        return post("/api/v1/knowledge:detect-conflicts", req, Models.ConflictScanResponse.class);
    }

    // ---------- Conversations ----------

    public Mono<Models.Conversation> createConversation(Models.CreateConversationRequest req) {
        return post("/api/v1/conversations", req, Models.Conversation.class);
    }

    public Mono<Models.Conversation> getConversation(String id) {
        return get("/api/v1/conversations/" + encode(id), Models.Conversation.class);
    }

    public Mono<Models.ConversationTurn> addTurn(String conversationId,
                                                  Models.CreateConversationTurnRequest req) {
        return post("/api/v1/conversations/" + encode(conversationId) + "/turn",
                req, Models.ConversationTurn.class);
    }

    public Mono<Models.SuggestionsResponse> suggestQuestions(String conversationId) {
        return post("/api/v1/conversations/" + encode(conversationId) + "/suggest",
                null, Models.SuggestionsResponse.class);
    }

    // ---------- Query ----------

    public Mono<Models.SearchResponse> search(String query, int topK) {
        return post("/api/v1/search", new Models.SearchRequest(query, topK), Models.SearchResponse.class);
    }

    public Mono<Models.AnswerResponse> answer(String question) {
        return answer(question, 8, null, null);
    }

    public Mono<Models.AnswerResponse> answer(String question, int topK, String instructions, String model) {
        return post("/api/v1/query",
                new Models.AnswerRequest(question, topK, instructions, model),
                Models.AnswerResponse.class);
    }

    /**
     * Stream the answer endpoint as Server-Sent Events. Each
     * {@link Models.StreamFrame} is one SSE frame; ``token`` events
     * carry {@code data.text}, the terminal ``complete`` event
     * carries {@code data.answer} + {@code data.citations}.
     */
    public Flux<Models.StreamFrame> streamAnswer(Models.AnswerRequest request) {
        return webClient.post()
                .uri("/api/v1/query/stream")
                .contentType(MediaType.APPLICATION_JSON)
                .accept(MediaType.TEXT_EVENT_STREAM)
                .bodyValue(request)
                .retrieve()
                .bodyToFlux(SSE_FRAME_TYPE)
                .map(ReactiveCanonClient::toFrame)
                .onErrorMap(WebClientResponseException.class, this::raiseForProblem);
    }

    // ---------- Billing + corpus inventory ----------

    public Mono<Models.BillingReport> billingReport(Map<String, String> filters) {
        return get("/api/v1/billing" + queryString(filters), Models.BillingReport.class);
    }

    public Mono<Models.CostEventsPage> listCostEvents(Map<String, String> filters) {
        return get("/api/v1/billing/events" + queryString(filters), Models.CostEventsPage.class);
    }

    public Mono<Models.BillingSummary> billingSummary(Map<String, String> filters) {
        return get("/api/v1/billing/summary" + queryString(filters), Models.BillingSummary.class);
    }

    public Mono<Models.TopConsumersReport> billingTop(Map<String, String> filters) {
        return get("/api/v1/billing/top" + queryString(filters), Models.TopConsumersReport.class);
    }

    public Mono<Models.SubjectCostReport> billingBySubject(Map<String, String> filters) {
        return get("/api/v1/billing/by-subject" + queryString(filters), Models.SubjectCostReport.class);
    }

    public Mono<Models.LatencyReport> billingLatency(Map<String, String> filters) {
        return get("/api/v1/billing/latency" + queryString(filters), Models.LatencyReport.class);
    }

    public Mono<Models.CorpusStats> stats() {
        return get("/api/v1/stats", Models.CorpusStats.class);
    }

    // ---------- Internals ----------

    private <T> Mono<T> get(String path, Class<T> type) {
        return webClient.get().uri(path).retrieve().bodyToMono(type)
                .onErrorMap(WebClientResponseException.class, this::raiseForProblem);
    }

    private <T> Mono<T> post(String path, Object body, Class<T> type) {
        var spec = webClient.post().uri(path).contentType(MediaType.APPLICATION_JSON);
        return (body == null ? spec.retrieve() : spec.bodyValue(body).retrieve())
                .bodyToMono(type)
                .onErrorMap(WebClientResponseException.class, this::raiseForProblem);
    }

    private <T> Mono<T> put(String path, Object body, Class<T> type) {
        var spec = webClient.put().uri(path).contentType(MediaType.APPLICATION_JSON);
        return (body == null ? spec.retrieve() : spec.bodyValue(body).retrieve())
                .bodyToMono(type)
                .onErrorMap(WebClientResponseException.class, this::raiseForProblem);
    }

    private CanonAPIException raiseForProblem(WebClientResponseException ex) {
        int status = ex.getStatusCode().value();
        String body = ex.getResponseBodyAsString();
        try {
            Models.ProblemDetails problem = mapper.readValue(body, PROBLEM_TYPE);
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
                    body == null || body.isBlank() ? null : body,
                    null);
        }
    }

    private static Models.StreamFrame toFrame(ServerSentEvent<Map<String, Object>> sse) {
        Map<String, Object> data = sse.data() != null ? sse.data() : Map.of();
        long cursor = 0L;
        Object raw = data.get("cursor");
        if (raw instanceof Number n) {
            cursor = n.longValue();
        }
        String event = sse.event() != null ? sse.event() : "message";
        return new Models.StreamFrame(cursor, event, data);
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
        private WebClient.Builder webClientBuilder;
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

        public Builder webClientBuilder(WebClient.Builder builder) {
            this.webClientBuilder = builder;
            return this;
        }

        public Builder objectMapper(ObjectMapper mapper) {
            this.mapper = mapper;
            return this;
        }

        public ReactiveCanonClient build() {
            if (baseUrl == null || baseUrl.isBlank()) {
                throw new IllegalArgumentException("baseUrl is required");
            }
            URI.create(baseUrl);
            return new ReactiveCanonClient(this);
        }
    }
}
