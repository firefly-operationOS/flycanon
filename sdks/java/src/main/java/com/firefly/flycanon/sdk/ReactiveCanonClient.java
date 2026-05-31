// Copyright 2024-2026 Firefly Software Foundation
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

package com.firefly.flycanon.sdk;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.JsonNode;
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
 * Events streams ({@code /api/v1/ingest-jobs/{id}/stream},
 * {@code /api/v1/query/stream}, {@code /api/v1/agent/query/stream}).
 *
 * <p>The 26.5.7 unification adds four firefly wire-contract headers
 * ({@code tenantId}, {@code workspaceId}, {@code correlationId},
 * {@code agentToken}); each is optional and only emitted when set.
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
    private final AgentSurface agent;

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
                .defaultHeader(HttpHeaders.USER_AGENT, "flycanon-sdk-java/" + CanonClient.SDK_VERSION);
        Optional.ofNullable(builder.apiKey)
                .filter(k -> !k.isBlank())
                .ifPresent(k -> wcb.defaultHeader(HttpHeaders.AUTHORIZATION, "Bearer " + k));
        Optional.ofNullable(builder.tenantId)
                .filter(s -> !s.isBlank())
                .ifPresent(v -> wcb.defaultHeader(CanonClient.HEADER_TENANT_ID, v));
        Optional.ofNullable(builder.workspaceId)
                .filter(s -> !s.isBlank())
                .ifPresent(v -> wcb.defaultHeader(CanonClient.HEADER_WORKSPACE_ID, v));
        Optional.ofNullable(builder.correlationId)
                .filter(s -> !s.isBlank())
                .ifPresent(v -> wcb.defaultHeader(CanonClient.HEADER_CORRELATION_ID, v));
        Optional.ofNullable(builder.agentToken)
                .filter(s -> !s.isBlank())
                .ifPresent(v -> wcb.defaultHeader(CanonClient.HEADER_AGENT_TOKEN, v));
        this.webClient = wcb.build();
        this.agent = new AgentSurface(this);
    }

    public static Builder builder() {
        return new Builder();
    }

    /**
     * Agent-tier sub-client (reactive variant).
     *
     * <p>Mirrors the eight routes under {@code /api/v1/agent/*}.
     */
    public AgentSurface agent() {
        return agent;
    }

    private static ObjectMapper defaultMapper() {
        return new ObjectMapper()
                .registerModule(new JavaTimeModule())
                .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
    }

    // ---------- Version ----------

    public Mono<Models.VersionInfo> version() {
        return get("/api/v1/version", null, Models.VersionInfo.class);
    }

    // ---------- Sources ----------

    public Mono<Models.SourceRecord> submitSource(Models.SubmitSourceJsonPayload payload) {
        return post("/api/v1/sources", payload, null, Models.SourceRecord.class);
    }

    public Mono<Models.SourceRecord> getSource(String id) {
        return get("/api/v1/sources/" + encode(id), null, Models.SourceRecord.class);
    }

    public Mono<Models.SourcesPage> listSources(Map<String, String> filters) {
        return get("/api/v1/sources" + queryString(filters), null, Models.SourcesPage.class);
    }

    public Mono<Models.BulkSourcesResponse> submitSourcesBulk(List<Models.SubmitSourceJsonPayload> payloads) {
        return post("/api/v1/sources:bulk", new Models.BulkSourcesRequest(payloads),
                null, Models.BulkSourcesResponse.class);
    }

    public Mono<Models.IngestJob> submitSourceAsync(Models.SubmitSourceJsonPayload payload) {
        return post("/api/v1/sources:async", payload, null, Models.IngestJob.class);
    }

    public Mono<Models.SourceRecord> replaceSource(String id, Models.SubmitSourceJsonPayload payload) {
        return put("/api/v1/sources/" + encode(id), payload, null, Models.SourceRecord.class);
    }

    // ---------- Async ingest jobs (renamed 26.5.7: /jobs -> /ingest-jobs) ----------

    public Mono<Models.IngestJob> getJob(String id) {
        return get("/api/v1/ingest-jobs/" + encode(id), null, Models.IngestJob.class);
    }

    public Mono<Models.IngestJob> cancelJob(String id) {
        return post("/api/v1/ingest-jobs/" + encode(id) + ":cancel", null, null, Models.IngestJob.class);
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
                .uri(uri -> uri.path("/api/v1/ingest-jobs/{id}/stream").queryParam("cursor", cursor).build(jobId))
                .accept(MediaType.TEXT_EVENT_STREAM)
                .retrieve()
                .bodyToFlux(SSE_FRAME_TYPE)
                .map(ReactiveCanonClient::toFrame)
                .onErrorMap(WebClientResponseException.class, this::raiseForProblem);
    }

    // ---------- Knowledge ----------

    public Mono<Models.KnowledgeItem> getKnowledge(String id) {
        return get("/api/v1/knowledge/" + encode(id), null, Models.KnowledgeItem.class);
    }

    public Mono<Models.KnowledgeItemsPage> listKnowledge(Map<String, String> filters) {
        return get("/api/v1/knowledge" + queryString(filters), null, Models.KnowledgeItemsPage.class);
    }

    public Mono<Models.KnowledgeDiff> getDiff(String id, int fromVersion, int toVersion) {
        Map<String, String> q = new LinkedHashMap<>();
        q.put("from_version", String.valueOf(fromVersion));
        q.put("to_version", String.valueOf(toVersion));
        return get("/api/v1/knowledge/" + encode(id) + "/diff" + queryString(q), null, Models.KnowledgeDiff.class);
    }

    // ---------- Knowledge graph (relations + graph view) ----------

    public Mono<Models.RelationsList> listRelations(String id) {
        return get("/api/v1/knowledge/" + encode(id) + "/relations", null, Models.RelationsList.class);
    }

    public Mono<Models.KnowledgeRelation> addRelation(String id, Models.CreateRelationRequest req) {
        return post("/api/v1/knowledge/" + encode(id) + "/relations", req, null, Models.KnowledgeRelation.class);
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
        return get("/api/v1/knowledge:graph" + queryString(filters), null, Models.KnowledgeGraph.class);
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
        return get("/api/v1/knowledge:stale", null, Models.StaleReport.class);
    }

    public Mono<Models.ConflictScanResponse> detectConflicts(Models.ConflictScanRequest req) {
        return post("/api/v1/knowledge:detect-conflicts", req, null, Models.ConflictScanResponse.class);
    }

    // ---------- Conversations ----------

    public Mono<Models.Conversation> createConversation(Models.CreateConversationRequest req) {
        return post("/api/v1/conversations", req, null, Models.Conversation.class);
    }

    public Mono<Models.Conversation> getConversation(String id) {
        return get("/api/v1/conversations/" + encode(id), null, Models.Conversation.class);
    }

    public Mono<Models.ConversationTurn> addTurn(String conversationId,
                                                  Models.CreateConversationTurnRequest req) {
        return post("/api/v1/conversations/" + encode(conversationId) + "/turns",
                req, null, Models.ConversationTurn.class);
    }

    public Mono<Models.SuggestionsResponse> suggestQuestions(String conversationId) {
        return post("/api/v1/conversations/" + encode(conversationId) + "/suggest",
                null, null, Models.SuggestionsResponse.class);
    }

    // ---------- Query ----------

    public Mono<Models.SearchResponse> search(String query, int topK) {
        return post("/api/v1/search", new Models.SearchRequest(query, topK), null, Models.SearchResponse.class);
    }

    public Mono<Models.AnswerResponse> answer(String question) {
        return answer(question, 8, null, null);
    }

    public Mono<Models.AnswerResponse> answer(String question, int topK, String instructions, String model) {
        return post("/api/v1/query",
                new Models.AnswerRequest(question, topK, instructions, model),
                null, Models.AnswerResponse.class);
    }

    /**
     * Stream the answer endpoint as Server-Sent Events. Each
     * {@link Models.StreamFrame} is one SSE frame; {@code token}
     * events carry {@code data.text}, the terminal {@code complete}
     * event carries {@code data.answer} + {@code data.citations}.
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
        return get("/api/v1/billing" + queryString(filters), null, Models.BillingReport.class);
    }

    public Mono<Models.CostEventsPage> listCostEvents(Map<String, String> filters) {
        return get("/api/v1/billing/events" + queryString(filters), null, Models.CostEventsPage.class);
    }

    public Mono<Models.BillingSummary> billingSummary(Map<String, String> filters) {
        return get("/api/v1/billing/summary" + queryString(filters), null, Models.BillingSummary.class);
    }

    public Mono<Models.TopConsumersReport> billingTop(Map<String, String> filters) {
        return get("/api/v1/billing/top" + queryString(filters), null, Models.TopConsumersReport.class);
    }

    public Mono<Models.SubjectCostReport> billingBySubject(Map<String, String> filters) {
        return get("/api/v1/billing/by-subject" + queryString(filters), null, Models.SubjectCostReport.class);
    }

    public Mono<Models.LatencyReport> billingLatency(Map<String, String> filters) {
        return get("/api/v1/billing/latency" + queryString(filters), null, Models.LatencyReport.class);
    }

    public Mono<Models.CorpusStats> stats() {
        return get("/api/v1/stats", null, Models.CorpusStats.class);
    }

    // ---------- Workspaces (26.5.7 unification -- user-tier CRUD) ----------

    /**
     * Create a workspace under the caller's tenant. See
     * {@link CanonClient#createWorkspace(Models.WorkspaceCreate)}.
     */
    public Mono<Models.WorkspaceSpec> createWorkspace(Models.WorkspaceCreate spec) {
        return post("/api/v1/workspaces", spec, null, Models.WorkspaceSpec.class);
    }

    /**
     * Return every workspace owned by the caller's tenant. See
     * {@link CanonClient#listWorkspaces()}.
     */
    public Mono<List<Models.WorkspaceSummary>> listWorkspaces() {
        return get("/api/v1/workspaces", null, Models.WorkspaceSummary[].class)
                .map(rows -> rows == null ? List.<Models.WorkspaceSummary>of() : List.of(rows));
    }

    /**
     * Fetch a single workspace by id within the caller's tenant.
     * See {@link CanonClient#getWorkspace(String)}.
     */
    public Mono<Models.WorkspaceSpec> getWorkspace(String workspaceId) {
        return get("/api/v1/workspaces/" + encode(workspaceId), null, Models.WorkspaceSpec.class);
    }

    /**
     * Apply a sparse patch to a workspace row. See
     * {@link CanonClient#updateWorkspace(String, Models.WorkspaceUpdate)}.
     */
    public Mono<Models.WorkspaceSpec> updateWorkspace(String workspaceId, Models.WorkspaceUpdate patch) {
        return patch("/api/v1/workspaces/" + encode(workspaceId), patch, null, Models.WorkspaceSpec.class);
    }

    /**
     * Close a workspace (status='closed' + closed_at=now()). See
     * {@link CanonClient#closeWorkspace(String)}.
     */
    public Mono<Models.WorkspaceSpec> closeWorkspace(String workspaceId) {
        return post("/api/v1/workspaces/" + encode(workspaceId) + ":close", null, null, Models.WorkspaceSpec.class);
    }

    // ---------- Agent tokens (26.5.7 unification -- user-tier CRUD) ----------

    /**
     * Mint a new agent token under the caller's tenant. See
     * {@link CanonClient#mintAgentToken(Models.AgentTokenMintRequest)}.
     */
    public Mono<Models.AgentTokenCreated> mintAgentToken(Models.AgentTokenMintRequest request) {
        return post("/api/v1/agent-tokens", request, null, Models.AgentTokenCreated.class);
    }

    /**
     * List tokens for the caller's tenant. See
     * {@link CanonClient#listAgentTokens()}.
     */
    public Mono<List<Models.AgentTokenSummary>> listAgentTokens() {
        return get("/api/v1/agent-tokens", null, Models.AgentTokenSummary[].class)
                .map(rows -> rows == null ? List.<Models.AgentTokenSummary>of() : List.of(rows));
    }

    /**
     * Revoke a token by id. See
     * {@link CanonClient#revokeAgentToken(String)}.
     */
    public Mono<Void> revokeAgentToken(String tokenId) {
        return webClient.delete()
                .uri("/api/v1/agent-tokens/" + encode(tokenId))
                .retrieve()
                .toBodilessEntity()
                .onErrorMap(WebClientResponseException.class, this::raiseForProblem)
                .then();
    }

    // ---------- Internals ----------

    /**
     * Outbound GET helper -- accepts an optional {@code extraHeaders}
     * map for per-call header overrides.
     */
    <T> Mono<T> get(String path, Map<String, String> extraHeaders, Class<T> type) {
        WebClient.RequestHeadersSpec<?> spec = webClient.get().uri(path);
        spec = applyHeaders(spec, extraHeaders);
        return spec.retrieve().bodyToMono(type)
                .onErrorMap(WebClientResponseException.class, this::raiseForProblem);
    }

    <T> Mono<T> post(String path, Object body, Map<String, String> extraHeaders, Class<T> type) {
        WebClient.RequestBodySpec spec = webClient.post().uri(path).contentType(MediaType.APPLICATION_JSON);
        spec = (WebClient.RequestBodySpec) applyHeaders(spec, extraHeaders);
        WebClient.RequestHeadersSpec<?> outbound = body == null ? spec : spec.bodyValue(body);
        return outbound.retrieve().bodyToMono(type)
                .onErrorMap(WebClientResponseException.class, this::raiseForProblem);
    }

    <T> Mono<T> put(String path, Object body, Map<String, String> extraHeaders, Class<T> type) {
        WebClient.RequestBodySpec spec = webClient.put().uri(path).contentType(MediaType.APPLICATION_JSON);
        spec = (WebClient.RequestBodySpec) applyHeaders(spec, extraHeaders);
        WebClient.RequestHeadersSpec<?> outbound = body == null ? spec : spec.bodyValue(body);
        return outbound.retrieve().bodyToMono(type)
                .onErrorMap(WebClientResponseException.class, this::raiseForProblem);
    }

    <T> Mono<T> patch(String path, Object body, Map<String, String> extraHeaders, Class<T> type) {
        WebClient.RequestBodySpec spec = webClient.patch().uri(path).contentType(MediaType.APPLICATION_JSON);
        spec = (WebClient.RequestBodySpec) applyHeaders(spec, extraHeaders);
        WebClient.RequestHeadersSpec<?> outbound = body == null ? spec : spec.bodyValue(body);
        return outbound.retrieve().bodyToMono(type)
                .onErrorMap(WebClientResponseException.class, this::raiseForProblem);
    }

    private static WebClient.RequestHeadersSpec<?> applyHeaders(WebClient.RequestHeadersSpec<?> spec,
                                                                Map<String, String> extraHeaders) {
        if (extraHeaders == null || extraHeaders.isEmpty()) {
            return spec;
        }
        for (Map.Entry<String, String> entry : extraHeaders.entrySet()) {
            spec = spec.header(entry.getKey(), entry.getValue());
        }
        return spec;
    }

    private CanonAPIException raiseForProblem(WebClientResponseException ex) {
        int status = ex.getStatusCode().value();
        String body = ex.getResponseBodyAsString();
        try {
            JsonNode root = mapper.readTree(body);
            Models.ProblemDetails problem = mapper.treeToValue(root, Models.ProblemDetails.class);
            JsonNode errorsNode = root.path("errors");
            return CanonAPIException.fromProblemDetail(problem, status, errorsNode.isMissingNode() ? null : errorsNode);
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
        private String tenantId;
        private String workspaceId;
        private String correlationId;
        private String agentToken;
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

        /**
         * Set the tenant identifier sent as {@code X-Tenant-Id} on
         * every outbound request. Optional.
         */
        public Builder tenantId(String id) {
            this.tenantId = id;
            return this;
        }

        /**
         * Set the workspace identifier sent as {@code X-Workspace-Id}
         * on every outbound request. Optional.
         */
        public Builder workspaceId(String id) {
            this.workspaceId = id;
            return this;
        }

        /**
         * Set the correlation identifier sent as {@code X-Correlation-Id}
         * on every outbound request. Optional.
         */
        public Builder correlationId(String id) {
            this.correlationId = id;
            return this;
        }

        /**
         * Set the agent bearer token sent as {@code X-Agent-Token}
         * on every outbound request. Optional.
         */
        public Builder agentToken(String token) {
            this.agentToken = token;
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

    // ---------- Agent surface ----------

    /**
     * Agent-tier sub-client (reactive variant).
     *
     * <p>Mirrors the eight routes under {@code /api/v1/agent/*}.
     * Every POST requires an explicit {@code idempotencyKey}
     * argument; the SDK validates the value is non-empty before
     * the request goes out.
     */
    public static final class AgentSurface {

        private final ReactiveCanonClient parent;

        AgentSurface(ReactiveCanonClient parent) {
            this.parent = parent;
        }

        // -- Sources ------------------------------------------------

        /**
         * Submit a source for intake (agent tier).
         *
         * <p>Scope: {@code agent.sources:ingest}. Mandatory
         * {@code idempotencyKey}.
         */
        public Mono<Models.SourceRecord> ingestSource(Models.SubmitSourceJsonPayload spec, String idempotencyKey) {
            CanonClient.requireIdempotencyKey(idempotencyKey);
            return parent.post(
                    "/api/v1/agent/sources",
                    spec,
                    Map.of(CanonClient.HEADER_IDEMPOTENCY_KEY, idempotencyKey),
                    Models.SourceRecord.class);
        }

        /**
         * Read a single source record (agent tier).
         *
         * <p>Scope: {@code agent.sources:read}.
         */
        public Mono<Models.SourceRecord> getSource(String sourceId) {
            return parent.get(
                    "/api/v1/agent/sources/" + encode(sourceId),
                    null,
                    Models.SourceRecord.class);
        }

        // -- Query --------------------------------------------------

        /**
         * Grounded RAG answer with explicit citations (agent tier).
         *
         * <p>Scope: {@code agent.query:run}. Mandatory
         * {@code idempotencyKey}.
         */
        public Mono<Models.AnswerResponse> query(Models.AnswerRequest request, String idempotencyKey) {
            CanonClient.requireIdempotencyKey(idempotencyKey);
            return parent.post(
                    "/api/v1/agent/query",
                    request,
                    Map.of(CanonClient.HEADER_IDEMPOTENCY_KEY, idempotencyKey),
                    Models.AnswerResponse.class);
        }

        /**
         * Stream the agent answer endpoint as Server-Sent Events.
         *
         * <p>Scope: {@code agent.query:run}. Mandatory
         * {@code idempotencyKey}. Two frame types on the wire:
         * {@code hit} (one per retrieved citation) and {@code final}
         * (terminal frame with the full answer).
         */
        public Flux<Models.StreamFrame> queryStream(Models.AnswerRequest request, String idempotencyKey) {
            CanonClient.requireIdempotencyKey(idempotencyKey);
            return parent.webClient.post()
                    .uri("/api/v1/agent/query/stream")
                    .contentType(MediaType.APPLICATION_JSON)
                    .accept(MediaType.TEXT_EVENT_STREAM)
                    .header(CanonClient.HEADER_IDEMPOTENCY_KEY, idempotencyKey)
                    .bodyValue(request)
                    .retrieve()
                    .bodyToFlux(SSE_FRAME_TYPE)
                    .map(ReactiveCanonClient::toFrame)
                    .onErrorMap(WebClientResponseException.class, parent::raiseForProblem);
        }

        /**
         * Hybrid retrieval over the canon corpus (agent tier).
         *
         * <p>Scope: {@code agent.query:run}. Mandatory
         * {@code idempotencyKey}.
         */
        public Mono<Models.SearchResponse> search(Models.SearchRequest request, String idempotencyKey) {
            CanonClient.requireIdempotencyKey(idempotencyKey);
            return parent.post(
                    "/api/v1/agent/search",
                    request,
                    Map.of(CanonClient.HEADER_IDEMPOTENCY_KEY, idempotencyKey),
                    Models.SearchResponse.class);
        }

        // -- Knowledge ----------------------------------------------

        /**
         * Fetch a single knowledge item by id (agent tier).
         *
         * <p>Scope: {@code agent.knowledge:read}.
         */
        public Mono<Models.KnowledgeItem> getKnowledge(String itemId) {
            return parent.get(
                    "/api/v1/agent/knowledge/" + encode(itemId),
                    null,
                    Models.KnowledgeItem.class);
        }

        /**
         * Resolve the citation graph for ({@code itemId}, current version).
         *
         * <p>Scope: {@code agent.knowledge:read}.
         */
        public Mono<Models.Provenance> getProvenance(String itemId) {
            return parent.get(
                    "/api/v1/agent/knowledge/" + encode(itemId) + "/provenance",
                    null,
                    Models.Provenance.class);
        }

        // -- Candidates ---------------------------------------------

        /**
         * Propose candidates from an existing source (agent tier).
         *
         * <p>Scope: {@code agent.candidates:propose}. Mandatory
         * {@code idempotencyKey}.
         */
        public Mono<List<Models.CandidateRecord>> proposeCandidates(Models.ProposeCandidatesRequest request,
                                                                    String idempotencyKey) {
            CanonClient.requireIdempotencyKey(idempotencyKey);
            return parent.post(
                            "/api/v1/agent/candidates:propose",
                            request,
                            Map.of(CanonClient.HEADER_IDEMPOTENCY_KEY, idempotencyKey),
                            Models.CandidateRecord[].class)
                    .map(rows -> rows == null ? List.<Models.CandidateRecord>of() : List.of(rows));
        }
    }
}
