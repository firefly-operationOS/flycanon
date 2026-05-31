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

import com.fasterxml.jackson.databind.JsonNode;
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
import java.util.List;
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
 * <p>The 26.5.7 unification adds four firefly wire-contract headers
 * to the builder ({@code tenantId}, {@code workspaceId},
 * {@code correlationId}, {@code agentToken}); each is optional, and
 * the SDK only emits the corresponding {@code X-*} header when the
 * caller supplied a value. The service rejects missing tenant /
 * workspace headers at the boundary, so unset properties surface as
 * a typed exception rather than a silent default.
 *
 * <p>The agent-tier surface is exposed via {@link #agent()}:
 *
 * <pre>{@code
 *   CanonClient client = CanonClient.builder()
 *       .baseUrl("http://localhost:8500")
 *       .agentToken("canon_...")
 *       .build();
 *   var answer = client.agent().query(request, "idem-key-123");
 * }</pre>
 *
 * <p>All five agent POSTs require a non-empty {@code idempotencyKey}
 * (the service enforces {@code 400 missing_idempotency_key} on a
 * missing key) -- the SDK validates locally before the round-trip
 * so callers see a fast {@link IllegalArgumentException}.
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

    /** Canonical firefly wire-contract header names. */
    static final String HEADER_TENANT_ID = "X-Tenant-Id";
    static final String HEADER_WORKSPACE_ID = "X-Workspace-Id";
    static final String HEADER_CORRELATION_ID = "X-Correlation-Id";
    static final String HEADER_AGENT_TOKEN = "X-Agent-Token";
    static final String HEADER_IDEMPOTENCY_KEY = "Idempotency-Key";

    /** SDK version. Mirrors {@code pom.xml}. */
    public static final String SDK_VERSION = "26.5.7";

    private final RestClient restClient;
    private final ObjectMapper mapper;
    private final AgentSurface agent;

    private CanonClient(Builder builder) {
        this.mapper = builder.mapper != null ? builder.mapper : defaultMapper();
        RestClient.Builder rcb = (builder.restClientBuilder != null
                ? builder.restClientBuilder
                : RestClient.builder())
                .baseUrl(builder.baseUrl)
                .defaultHeader(HttpHeaders.ACCEPT, MediaType.APPLICATION_JSON_VALUE)
                .defaultHeader(HttpHeaders.USER_AGENT, "flycanon-sdk-java/" + SDK_VERSION);
        Optional.ofNullable(builder.apiKey)
                .filter(k -> !k.isBlank())
                .ifPresent(k -> rcb.defaultHeader(HttpHeaders.AUTHORIZATION, "Bearer " + k));
        Optional.ofNullable(builder.tenantId)
                .filter(s -> !s.isBlank())
                .ifPresent(v -> rcb.defaultHeader(HEADER_TENANT_ID, v));
        Optional.ofNullable(builder.workspaceId)
                .filter(s -> !s.isBlank())
                .ifPresent(v -> rcb.defaultHeader(HEADER_WORKSPACE_ID, v));
        Optional.ofNullable(builder.correlationId)
                .filter(s -> !s.isBlank())
                .ifPresent(v -> rcb.defaultHeader(HEADER_CORRELATION_ID, v));
        Optional.ofNullable(builder.agentToken)
                .filter(s -> !s.isBlank())
                .ifPresent(v -> rcb.defaultHeader(HEADER_AGENT_TOKEN, v));
        this.restClient = rcb.build();
        this.agent = new AgentSurface(this);
    }

    public static Builder builder() {
        return new Builder();
    }

    /**
     * Agent-tier sub-client.
     *
     * <p>Mirrors the eight routes under {@code /api/v1/agent/*}.
     * Every POST requires an explicit {@code idempotencyKey}
     * argument (service-side requirement; the SDK validates the
     * value is non-empty before the request goes out so callers get
     * a fast local error). Agent-tier calls typically authenticate
     * with {@code X-Agent-Token} -- set the token on the builder
     * ({@link Builder#agentToken(String)}).
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

    public Models.VersionInfo version() {
        return request("GET", "/api/v1/version", null, null, Models.VersionInfo.class);
    }

    // ---------- Sources ----------

    public Models.SourceRecord submitSource(Models.SubmitSourceJsonPayload payload) {
        return request("POST", "/api/v1/sources", payload, null, Models.SourceRecord.class);
    }

    public Models.SourceRecord getSource(String id) {
        return request("GET", "/api/v1/sources/" + encode(id), null, null, Models.SourceRecord.class);
    }

    public Models.SourcesPage listSources(Map<String, String> filters) {
        return request("GET", "/api/v1/sources" + queryString(filters), null, null, Models.SourcesPage.class);
    }

    public Models.BulkSourcesResponse submitSourcesBulk(java.util.List<Models.SubmitSourceJsonPayload> payloads) {
        return request(
                "POST",
                "/api/v1/sources:bulk",
                new Models.BulkSourcesRequest(payloads),
                null,
                Models.BulkSourcesResponse.class);
    }

    public Models.IngestJob submitSourceAsync(Models.SubmitSourceJsonPayload payload) {
        return request("POST", "/api/v1/sources:async", payload, null, Models.IngestJob.class);
    }

    public Models.SourceRecord replaceSource(String id, Models.SubmitSourceJsonPayload payload) {
        return request("PUT", "/api/v1/sources/" + encode(id), payload, null, Models.SourceRecord.class);
    }

    // ---------- Async ingest jobs (renamed 26.5.7: /jobs -> /ingest-jobs) ----------

    public Models.IngestJob getJob(String id) {
        return request("GET", "/api/v1/ingest-jobs/" + encode(id), null, null, Models.IngestJob.class);
    }

    public Models.IngestJob cancelJob(String id) {
        return request("POST", "/api/v1/ingest-jobs/" + encode(id) + ":cancel", null, null, Models.IngestJob.class);
    }

    /**
     * Build the URL of the SSE stream for a job. The blocking
     * {@link RestClient} does not consume Server-Sent Events well; this
     * helper returns the relative URL so callers can wire it into
     * Spring's {@code WebClient} (reactive) or any HTTP/2 streaming
     * client of their choice. Pass {@code cursor=N} to resume from a
     * known offset.
     */
    public String jobStreamUrl(String id, long cursor) {
        return "/api/v1/ingest-jobs/" + encode(id) + "/stream?cursor=" + cursor;
    }

    // ---------- Knowledge ----------

    public Models.KnowledgeItem getKnowledge(String id) {
        return request("GET", "/api/v1/knowledge/" + encode(id), null, null, Models.KnowledgeItem.class);
    }

    public Models.KnowledgeItemsPage listKnowledge(Map<String, String> filters) {
        return request("GET", "/api/v1/knowledge" + queryString(filters), null, null, Models.KnowledgeItemsPage.class);
    }

    public Models.KnowledgeDiff getDiff(String id, int fromVersion, int toVersion) {
        Map<String, String> q = filters();
        q.put("from_version", String.valueOf(fromVersion));
        q.put("to_version", String.valueOf(toVersion));
        return request(
                "GET",
                "/api/v1/knowledge/" + encode(id) + "/diff" + queryString(q),
                null,
                null,
                Models.KnowledgeDiff.class);
    }

    // ---------- Knowledge graph (relations + graph view) ----------

    public Models.RelationsList listRelations(String id) {
        return request(
                "GET",
                "/api/v1/knowledge/" + encode(id) + "/relations",
                null,
                null,
                Models.RelationsList.class);
    }

    public Models.KnowledgeRelation addRelation(String id, Models.CreateRelationRequest req) {
        return request(
                "POST",
                "/api/v1/knowledge/" + encode(id) + "/relations",
                req,
                null,
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
        return request("GET", "/api/v1/knowledge:stale", null, null, Models.StaleReport.class);
    }

    public Models.ConflictScanResponse detectConflicts(Models.ConflictScanRequest req) {
        return request(
                "POST",
                "/api/v1/knowledge:detect-conflicts",
                req,
                null,
                Models.ConflictScanResponse.class);
    }

    // ---------- Conversations ----------

    public Models.Conversation createConversation(Models.CreateConversationRequest req) {
        return request("POST", "/api/v1/conversations", req, null, Models.Conversation.class);
    }

    public Models.Conversation getConversation(String id) {
        return request("GET", "/api/v1/conversations/" + encode(id), null, null, Models.Conversation.class);
    }

    public Models.ConversationTurn addTurn(String conversationId, Models.CreateConversationTurnRequest req) {
        return request(
                "POST",
                "/api/v1/conversations/" + encode(conversationId) + "/turns",
                req,
                null,
                Models.ConversationTurn.class);
    }

    public Models.SuggestionsResponse suggestQuestions(String conversationId) {
        return request(
                "POST",
                "/api/v1/conversations/" + encode(conversationId) + "/suggest",
                null,
                null,
                Models.SuggestionsResponse.class);
    }

    // ---------- Billing ----------

    public Models.BillingReport billingReport(Map<String, String> filters) {
        return request("GET", "/api/v1/billing" + queryString(filters), null, null, Models.BillingReport.class);
    }

    public Models.CostEventsPage listCostEvents(Map<String, String> filters) {
        return request("GET", "/api/v1/billing/events" + queryString(filters), null, null, Models.CostEventsPage.class);
    }

    public Models.BillingSummary billingSummary(Map<String, String> filters) {
        return request("GET", "/api/v1/billing/summary" + queryString(filters), null, null, Models.BillingSummary.class);
    }

    public Models.TopConsumersReport billingTop(Map<String, String> filters) {
        return request("GET", "/api/v1/billing/top" + queryString(filters), null, null, Models.TopConsumersReport.class);
    }

    public Models.SubjectCostReport billingBySubject(Map<String, String> filters) {
        return request("GET", "/api/v1/billing/by-subject" + queryString(filters), null, null, Models.SubjectCostReport.class);
    }

    public Models.LatencyReport billingLatency(Map<String, String> filters) {
        return request("GET", "/api/v1/billing/latency" + queryString(filters), null, null, Models.LatencyReport.class);
    }

    // ---------- Corpus inventory ----------

    public Models.CorpusStats stats() {
        return request("GET", "/api/v1/stats", null, null, Models.CorpusStats.class);
    }

    // ---------- Query ----------

    public Models.SearchResponse search(String query, int topK) {
        return request(
                "POST",
                "/api/v1/search",
                new Models.SearchRequest(query, topK),
                null,
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
                null,
                Models.AnswerResponse.class);
    }

    // ---------- Workspaces (26.5.7 unification -- user-tier CRUD) ----------

    /**
     * Create a workspace under the caller's tenant.
     *
     * <p>The id comes from {@code spec.id()} (caller-chosen slug);
     * the tenant comes from the {@code X-Tenant-Id} header. Returns
     * {@code 201 Created} with the persisted {@link Models.WorkspaceSpec}.
     */
    public Models.WorkspaceSpec createWorkspace(Models.WorkspaceCreate spec) {
        return request("POST", "/api/v1/workspaces", spec, null, Models.WorkspaceSpec.class);
    }

    /**
     * Return every workspace owned by the caller's tenant.
     *
     * <p>Ordered {@code created_at DESC} by the repository contract;
     * the most-recently-opened workspaces appear first.
     */
    public List<Models.WorkspaceSummary> listWorkspaces() {
        Models.WorkspaceSummary[] rows = request(
                "GET",
                "/api/v1/workspaces",
                null,
                null,
                Models.WorkspaceSummary[].class);
        return rows == null ? List.of() : List.of(rows);
    }

    /**
     * Fetch a single workspace by id within the caller's tenant.
     *
     * <p>Raises {@link CanonAPIException} with code
     * {@code "workspace_not_found"} when the
     * {@code (tenant_id, workspace_id)} pair does not exist.
     */
    public Models.WorkspaceSpec getWorkspace(String workspaceId) {
        return request("GET", "/api/v1/workspaces/" + encode(workspaceId), null, null, Models.WorkspaceSpec.class);
    }

    /**
     * Apply a sparse patch to a workspace row.
     *
     * <p>Only fields populated on {@code patch} (non-null per
     * Jackson's {@code @JsonInclude(NON_NULL)}) are applied;
     * everything else is preserved server-side.
     */
    public Models.WorkspaceSpec updateWorkspace(String workspaceId, Models.WorkspaceUpdate patch) {
        return request("PATCH", "/api/v1/workspaces/" + encode(workspaceId), patch, null, Models.WorkspaceSpec.class);
    }

    /**
     * Close a workspace ({@code status='closed'} + {@code closed_at=now()}).
     *
     * <p>Idempotent at the row level: closing an already-closed
     * workspace rewrites the same terminal state with a fresh
     * {@code closed_at} and returns the current row. The close call
     * is flycanon's terminal lifecycle transition -- downstream
     * event consumers see this as the workspace "delete" signal.
     */
    public Models.WorkspaceSpec closeWorkspace(String workspaceId) {
        return request("POST", "/api/v1/workspaces/" + encode(workspaceId) + ":close", null, null, Models.WorkspaceSpec.class);
    }

    // ---------- Agent tokens (26.5.7 unification -- user-tier CRUD) ----------

    /**
     * Mint a new agent token under the caller's tenant.
     *
     * <p>The full {@code token} is returned <strong>once</strong> in
     * the response; subsequent reads ({@link #listAgentTokens()})
     * only expose the public {@code prefix}. Callers MUST capture
     * the token here -- there is no recovery path.
     *
     * <p>Refuses agent-tier callers ({@code X-Agent-Token}-
     * authenticated) with {@code 403 agent_cannot_mint}
     * ({@link AgentCannotMint}).
     */
    public Models.AgentTokenCreated mintAgentToken(Models.AgentTokenMintRequest request) {
        return request("POST", "/api/v1/agent-tokens", request, null, Models.AgentTokenCreated.class);
    }

    /**
     * List tokens for the caller's tenant (newest first).
     *
     * <p>Returns the summary shape only -- the raw secret is never
     * round-tripped on this endpoint.
     */
    public List<Models.AgentTokenSummary> listAgentTokens() {
        Models.AgentTokenSummary[] rows = request(
                "GET",
                "/api/v1/agent-tokens",
                null,
                null,
                Models.AgentTokenSummary[].class);
        return rows == null ? List.of() : List.of(rows);
    }

    /**
     * Revoke a token by id.
     *
     * <p>Idempotent -- revoking an already-revoked token is a no-op
     * and still returns 204. Unknown {@code tokenId} raises
     * {@link CanonAPIException} with code {@code "resource_not_found"}.
     */
    public void revokeAgentToken(String tokenId) {
        try {
            restClient.delete()
                    .uri("/api/v1/agent-tokens/" + encode(tokenId))
                    .retrieve()
                    .toBodilessEntity();
        } catch (HttpStatusCodeException ex) {
            throw raiseForProblem(ex);
        } catch (ResourceAccessException ex) {
            throw new RuntimeException("flycanon request failed: " + ex.getMessage(), ex);
        }
    }

    // ---------- Internals ----------

    /**
     * Reject an empty or blank idempotency key with a fast local
     * error. Agent-tier POSTs MUST carry an {@code Idempotency-Key}
     * header (the service returns {@code 400 missing_idempotency_key}
     * otherwise); the SDK enforces the same shape locally so callers
     * see an {@link IllegalArgumentException} before the round-trip
     * when they forget to supply one.
     */
    static void requireIdempotencyKey(String value) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(
                    "idempotencyKey is required for agent-tier POSTs and must be a non-empty string");
        }
    }

    /**
     * Outbound request helper. Accepts an optional {@code extraHeaders}
     * map for per-call header overrides (used to attach
     * {@code Idempotency-Key} on agent POSTs without polluting the
     * client's default headers).
     */
    <T> T request(String method, String path, Object body, Map<String, String> extraHeaders, Class<T> responseType) {
        try {
            return buildSpec(method, path, body, extraHeaders).retrieve().body(responseType);
        } catch (HttpStatusCodeException ex) {
            throw raiseForProblem(ex);
        } catch (ResourceAccessException ex) {
            throw new RuntimeException("flycanon request failed: " + ex.getMessage(), ex);
        }
    }

    private RestClient.RequestHeadersSpec<?> buildSpec(String method,
                                                        String path,
                                                        Object body,
                                                        Map<String, String> extraHeaders) {
        RestClient.RequestHeadersSpec<?> spec = switch (method.toUpperCase()) {
            case "GET" -> restClient.get().uri(path);
            case "POST" -> {
                RestClient.RequestBodySpec post = restClient.post().uri(path);
                if (body != null) {
                    post.contentType(MediaType.APPLICATION_JSON);
                    post.body(body);
                }
                yield post;
            }
            case "PUT" -> {
                RestClient.RequestBodySpec put = restClient.put().uri(path);
                if (body != null) {
                    put.contentType(MediaType.APPLICATION_JSON);
                    put.body(body);
                }
                yield put;
            }
            case "PATCH" -> {
                RestClient.RequestBodySpec patch = restClient.patch().uri(path);
                if (body != null) {
                    patch.contentType(MediaType.APPLICATION_JSON);
                    patch.body(body);
                }
                yield patch;
            }
            case "DELETE" -> restClient.delete().uri(path);
            default -> throw new IllegalArgumentException("unsupported method: " + method);
        };
        if (extraHeaders != null && !extraHeaders.isEmpty()) {
            for (Map.Entry<String, String> entry : extraHeaders.entrySet()) {
                spec = spec.header(entry.getKey(), entry.getValue());
            }
        }
        return spec;
    }

    private CanonAPIException raiseForProblem(HttpStatusCodeException ex) {
        HttpStatusCode status = ex.getStatusCode();
        String body = ex.getResponseBodyAsString();
        try {
            JsonNode root = mapper.readTree(body);
            Models.ProblemDetails problem = mapper.treeToValue(root, Models.ProblemDetails.class);
            JsonNode errorsNode = root.path("errors");
            return CanonAPIException.fromProblemDetail(problem, status.value(), errorsNode.isMissingNode() ? null : errorsNode);
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
        private String tenantId;
        private String workspaceId;
        private String correlationId;
        private String agentToken;
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

        /**
         * Set the tenant identifier sent as {@code X-Tenant-Id} on
         * every outbound request. Optional -- the service rejects
         * missing headers at the boundary.
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
         * on every outbound request. Optional. Most callers rotate
         * this per call rather than fixing it on the builder.
         */
        public Builder correlationId(String id) {
            this.correlationId = id;
            return this;
        }

        /**
         * Set the agent bearer token sent as {@code X-Agent-Token}
         * on every outbound request. Optional. Only meaningful for
         * {@code /api/v1/agent/*} routes.
         */
        public Builder agentToken(String token) {
            this.agentToken = token;
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

    // ---------- Agent surface ----------

    /**
     * Agent-tier sub-client -- {@code client.agent().<method>(...)}.
     *
     * <p>Mirrors the eight routes under {@code /api/v1/agent/*}.
     * Every POST requires an explicit {@code idempotencyKey}
     * argument (service-side requirement; the SDK validates the
     * value is non-empty before the request goes out so callers get
     * a fast {@link IllegalArgumentException}). Agent-tier calls
     * typically authenticate with {@code X-Agent-Token} -- set the
     * token on the parent {@link CanonClient} via
     * {@link Builder#agentToken(String)}.
     */
    public static final class AgentSurface {

        private final CanonClient parent;

        AgentSurface(CanonClient parent) {
            this.parent = parent;
        }

        // -- Sources ------------------------------------------------

        /**
         * Submit a source for intake (agent tier).
         *
         * <p>Scope: {@code agent.sources:ingest}. {@code idempotencyKey}
         * is mandatory on the wire ({@code 400 missing_idempotency_key}
         * on the service if absent) -- the SDK rejects an empty
         * value locally to surface the requirement before the
         * round-trip.
         */
        public Models.SourceRecord ingestSource(Models.SubmitSourceJsonPayload spec, String idempotencyKey) {
            requireIdempotencyKey(idempotencyKey);
            return parent.request(
                    "POST",
                    "/api/v1/agent/sources",
                    spec,
                    Map.of(HEADER_IDEMPOTENCY_KEY, idempotencyKey),
                    Models.SourceRecord.class);
        }

        /**
         * Read a single source record (agent tier).
         *
         * <p>Scope: {@code agent.sources:read}.
         */
        public Models.SourceRecord getSource(String sourceId) {
            return parent.request(
                    "GET",
                    "/api/v1/agent/sources/" + encode(sourceId),
                    null,
                    null,
                    Models.SourceRecord.class);
        }

        // -- Query --------------------------------------------------

        /**
         * Grounded RAG answer with explicit citations (agent tier).
         *
         * <p>Scope: {@code agent.query:run}. Mandatory
         * {@code idempotencyKey}. Identical wire shape to the
         * user-tier {@code POST /api/v1/query}; the only differences
         * are the auth gate and the required idempotency key.
         */
        public Models.AnswerResponse query(Models.AnswerRequest request, String idempotencyKey) {
            requireIdempotencyKey(idempotencyKey);
            return parent.request(
                    "POST",
                    "/api/v1/agent/query",
                    request,
                    Map.of(HEADER_IDEMPOTENCY_KEY, idempotencyKey),
                    Models.AnswerResponse.class);
        }

        /**
         * Build the URL of the SSE answer stream for the agent
         * tier. The blocking {@link RestClient} does not consume
         * Server-Sent Events well; this helper returns the relative
         * URL so callers can wire it into the reactive client (see
         * {@link ReactiveCanonClient#agent} -- agent stream lives
         * on the reactive client only).
         *
         * <p>Scope: {@code agent.query:run}. Mandatory
         * {@code idempotencyKey} -- enforce it on the caller side
         * via {@link #requireIdempotencyKeyExternal(String)} and
         * attach the header when issuing the stream request.
         */
        public String queryStreamUrl(String idempotencyKey) {
            requireIdempotencyKey(idempotencyKey);
            return "/api/v1/agent/query/stream";
        }

        /**
         * Hybrid retrieval over the canon corpus (agent tier).
         *
         * <p>Scope: {@code agent.query:run}. Mandatory
         * {@code idempotencyKey}. No LLM call -- just BM25 + dense
         * retrieval fused via RRF.
         */
        public Models.SearchResponse search(Models.SearchRequest request, String idempotencyKey) {
            requireIdempotencyKey(idempotencyKey);
            return parent.request(
                    "POST",
                    "/api/v1/agent/search",
                    request,
                    Map.of(HEADER_IDEMPOTENCY_KEY, idempotencyKey),
                    Models.SearchResponse.class);
        }

        // -- Knowledge ----------------------------------------------

        /**
         * Fetch a single knowledge item by id (agent tier).
         *
         * <p>Scope: {@code agent.knowledge:read}. Returns the
         * pointer view (current version metadata), not the version
         * body.
         */
        public Models.KnowledgeItem getKnowledge(String itemId) {
            return parent.request(
                    "GET",
                    "/api/v1/agent/knowledge/" + encode(itemId),
                    null,
                    null,
                    Models.KnowledgeItem.class);
        }

        /**
         * Resolve the citation graph for ({@code itemId}, current version).
         *
         * <p>Scope: {@code agent.knowledge:read}. The current
         * version is resolved server-side, matching the user-tier
         * surface.
         */
        public Models.Provenance getProvenance(String itemId) {
            return parent.request(
                    "GET",
                    "/api/v1/agent/knowledge/" + encode(itemId) + "/provenance",
                    null,
                    null,
                    Models.Provenance.class);
        }

        // -- Candidates ---------------------------------------------

        /**
         * Propose candidates from an existing source (agent tier).
         *
         * <p>Scope: {@code agent.candidates:propose}. Mandatory
         * {@code idempotencyKey}. Returns the persisted candidate
         * list (status {@code proposed} -- a human still
         * adjudicates).
         */
        public List<Models.CandidateRecord> proposeCandidates(Models.ProposeCandidatesRequest request,
                                                              String idempotencyKey) {
            requireIdempotencyKey(idempotencyKey);
            Models.CandidateRecord[] rows = parent.request(
                    "POST",
                    "/api/v1/agent/candidates:propose",
                    request,
                    Map.of(HEADER_IDEMPOTENCY_KEY, idempotencyKey),
                    Models.CandidateRecord[].class);
            return rows == null ? List.of() : List.of(rows);
        }
    }
}
