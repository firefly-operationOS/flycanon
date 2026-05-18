/*
 * Copyright 2026 Firefly Software Solutions Inc.
 * Licensed under the Apache License, Version 2.0.
 */
package com.firefly.flycanon.sdk.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;

/**
 * Co-located records mirroring the wire shapes flycanon ships. Java
 * records play well with Jackson once the parameter-names module is
 * registered (the {@link com.firefly.flycanon.sdk.CanonClient} does
 * that). {@link JsonIgnoreProperties} keeps the client forward-
 * compatible: new fields added by the service on a minor version do
 * not break deserialisation.
 */
public final class Models {

    private Models() {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record ProblemDetails(
            String type,
            String title,
            int status,
            String code,
            String detail,
            String instance,
            Map<String, Object> extensions) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record SubmitSourceJsonPayload(
            String kind,
            String uri,
            Map<String, Object> metadata,
            @JsonProperty("content_base64") String contentBase64,
            String filename,
            @JsonProperty("content_type") String contentType) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record SourceRecord(
            String id,
            String kind,
            String status,
            String filename,
            String uri,
            @JsonProperty("content_sha256") String contentSha256,
            @JsonProperty("content_bytes") long contentBytes,
            @JsonProperty("n_chunks") int nChunks,
            Map<String, Object> metadata,
            @JsonProperty("error_code") String errorCode,
            @JsonProperty("error_message") String errorMessage,
            @JsonProperty("created_at") OffsetDateTime createdAt,
            @JsonProperty("ingested_at") OffsetDateTime ingestedAt,
            @JsonProperty("updated_at") OffsetDateTime updatedAt) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record SourcesPage(List<SourceRecord> items, int total, int offset, int limit) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Citation(
            @JsonProperty("source_id") String sourceId,
            @JsonProperty("chunk_id") String chunkId,
            String quote,
            Double relevance,
            Integer page) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record KnowledgeItem(
            String id,
            String status,
            @JsonProperty("current_version") int currentVersion,
            String title,
            String domain,
            String jurisdiction,
            List<String> tags,
            @JsonProperty("created_at") OffsetDateTime createdAt,
            @JsonProperty("updated_at") OffsetDateTime updatedAt,
            @JsonProperty("retired_at") OffsetDateTime retiredAt,
            String summary) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record KnowledgeVersion(
            @JsonProperty("knowledge_item_id") String knowledgeItemId,
            int version,
            String status,
            String title,
            String summary,
            String body,
            String domain,
            String jurisdiction,
            List<String> tags,
            List<Citation> citations,
            @JsonProperty("supersedes_version") Integer supersedesVersion,
            @JsonProperty("superseded_by_version") Integer supersededByVersion,
            @JsonProperty("created_by") String createdBy,
            @JsonProperty("created_at") OffsetDateTime createdAt,
            Map<String, Object> metadata) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record KnowledgeItemsPage(List<KnowledgeItem> items, int total, int offset, int limit) {
    }

    /**
     * Single retrieval hit. Returned by {@code /api/v1/search} as the
     * page-level list and by {@code /api/v1/query} as the citation
     * list.
     *
     * <p>The structured source-side fields ({@code sourceFilename},
     * {@code sourceTitle}, {@code sourceKind}, {@code sourceUri},
     * {@code sectionPath}, {@code page}) are hydrated by flycanon so
     * the SDK consumer can render citation labels without a second
     * {@code GET /api/v1/sources/{id}} round-trip.
     */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Hit(
            @JsonProperty("chunk_id") String chunkId,
            @JsonProperty("source_id") String sourceId,
            @JsonProperty("source_filename") String sourceFilename,
            @JsonProperty("source_title") String sourceTitle,
            @JsonProperty("source_kind") String sourceKind,
            @JsonProperty("source_uri") String sourceUri,
            @JsonProperty("section_path") String sectionPath,
            Integer page,
            @JsonProperty("knowledge_item_id") String knowledgeItemId,
            @JsonProperty("knowledge_version") Integer knowledgeVersion,
            String content,
            double score,
            @JsonProperty("bm25_rank") Integer bm25Rank,
            @JsonProperty("vector_rank") Integer vectorRank,
            Map<String, String> metadata) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record SearchRequest(
            String query,
            @JsonProperty("top_k") int topK) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record SearchResponse(
            List<Hit> hits,
            @JsonProperty("elapsed_ms") int elapsedMs) {
    }

    /**
     * One Server-Sent Events frame, as emitted by the streaming
     * endpoints ({@code /api/v1/jobs/{id}/stream},
     * {@code /api/v1/query/stream}).
     *
     * <p>The reactive client materialises each SSE frame as one of
     * these records. {@code event} carries the SSE event type
     * ({@code stage} / {@code token} / {@code completed} / ...),
     * {@code data} carries the parsed JSON payload, and
     * {@code cursor} mirrors the {@code data.cursor} field when
     * present (job streams) or a monotonic counter otherwise
     * (query streams).
     */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record StreamFrame(long cursor, String event, Map<String, Object> data) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record AnswerRequest(
            String question,
            @JsonProperty("top_k") int topK,
            String instructions,
            String model) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record AnswerResponse(
            String answer,
            List<Hit> citations,
            String model,
            @JsonProperty("elapsed_ms") int elapsedMs,
            @JsonProperty("no_answer") boolean noAnswer) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record VersionInfo(
            String service,
            String version,
            @JsonProperty("embedding_model") String embeddingModel,
            @JsonProperty("answer_model") String answerModel,
            @JsonProperty("answer_fallback_model") String answerFallbackModel,
            @JsonProperty("vector_store") String vectorStore,
            @JsonProperty("eda_adapter") String edaAdapter) {
    }

    // ------------------------------------------------------------------
    // Tier 1 / Tier 2 extensions
    // ------------------------------------------------------------------

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record BulkSourcesRequest(List<SubmitSourceJsonPayload> items) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record BulkSourceResult(
            int index,
            boolean ok,
            @JsonProperty("source_id") String sourceId,
            @JsonProperty("error_code") String errorCode,
            @JsonProperty("error_message") String errorMessage) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record BulkSourcesResponse(
            List<BulkSourceResult> items,
            int total,
            int succeeded,
            int failed) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record IngestJob(
            String id,
            String status,
            double progress,
            String stage,
            @JsonProperty("source_id") String sourceId,
            @JsonProperty("error_code") String errorCode,
            @JsonProperty("error_message") String errorMessage,
            @JsonProperty("created_at") OffsetDateTime createdAt,
            @JsonProperty("updated_at") OffsetDateTime updatedAt) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record KnowledgeFieldChange(
            String field,
            @JsonProperty("from") Object fromValue,
            @JsonProperty("to") Object toValue) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record KnowledgeDiff(
            @JsonProperty("knowledge_item_id") String knowledgeItemId,
            @JsonProperty("from_version") int fromVersion,
            @JsonProperty("to_version") int toVersion,
            @JsonProperty("unified_diff") String unifiedDiff,
            @JsonProperty("field_changes") List<KnowledgeFieldChange> fieldChanges,
            @JsonProperty("added_citations") List<String> addedCitations,
            @JsonProperty("removed_citations") List<String> removedCitations) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record KnowledgeRelation(
            String id,
            @JsonProperty("from_item_id") String fromItemId,
            @JsonProperty("to_item_id") String toItemId,
            String kind,
            @JsonProperty("since_version") Integer sinceVersion,
            String note,
            String actor,
            @JsonProperty("created_at") OffsetDateTime createdAt) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record RelationsList(
            List<KnowledgeRelation> outgoing,
            List<KnowledgeRelation> incoming) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record CreateRelationRequest(
            @JsonProperty("to_item_id") String toItemId,
            String kind,
            @JsonProperty("since_version") Integer sinceVersion,
            String note,
            String actor) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record GraphNode(
            String id,
            String label,
            String kind,
            String domain) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record GraphEdge(
            @JsonProperty("from") String from,
            @JsonProperty("to") String to,
            String kind) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record KnowledgeGraph(List<GraphNode> nodes, List<GraphEdge> edges) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record ConversationTurn(
            String id,
            @JsonProperty("conversation_id") String conversationId,
            String query,
            String answer,
            List<Hit> citations,
            String model,
            @JsonProperty("created_at") OffsetDateTime createdAt) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Conversation(
            String id,
            String title,
            String summary,
            List<ConversationTurn> turns,
            @JsonProperty("created_at") OffsetDateTime createdAt,
            @JsonProperty("updated_at") OffsetDateTime updatedAt) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record CreateConversationRequest(String title, String actor) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record CreateConversationTurnRequest(
            String query,
            @JsonProperty("max_chunks") Integer maxChunks,
            @JsonProperty("hybrid_mode") String hybridMode,
            String actor) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record SuggestionsResponse(List<String> questions) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record StaleItem(
            @JsonProperty("knowledge_item_id") String knowledgeItemId,
            String title,
            String domain,
            Double score,
            @JsonProperty("max_similarity") Double maxSimilarity,
            @JsonProperty("sample_size") int sampleSize,
            @JsonProperty("computed_at") String computedAt) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record StaleReport(List<StaleItem> items, int total) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record ConflictScanRequest(
            String domain,
            @JsonProperty("min_similarity") double minSimilarity,
            @JsonProperty("max_items") int maxItems,
            String actor) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record ConflictScanResponse(
            @JsonProperty("pairs_evaluated") int pairsEvaluated,
            @JsonProperty("conflicts_found") int conflictsFound,
            @JsonProperty("candidate_ids") List<String> candidateIds,
            @JsonProperty("relation_ids") List<String> relationIds) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record BillingRow(
            Map<String, Object> group,
            @JsonProperty("input_tokens") long inputTokens,
            @JsonProperty("output_tokens") long outputTokens,
            @JsonProperty("total_tokens") long totalTokens,
            @JsonProperty("cost_usd") String costUsd,
            int calls) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record BillingReport(
            List<BillingRow> rows,
            @JsonProperty("total_cost_usd") String totalCostUsd,
            @JsonProperty("total_calls") int totalCalls) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record CostEvent(
            long id,
            @JsonProperty("agent_name") String agentName,
            String model,
            @JsonProperty("input_tokens") long inputTokens,
            @JsonProperty("output_tokens") long outputTokens,
            @JsonProperty("total_tokens") long totalTokens,
            @JsonProperty("cost_usd") String costUsd,
            @JsonProperty("latency_ms") Integer latencyMs,
            String actor,
            @JsonProperty("correlation_id") String correlationId,
            @JsonProperty("subject_kind") String subjectKind,
            @JsonProperty("subject_id") String subjectId,
            @JsonProperty("occurred_at") OffsetDateTime occurredAt) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record CostEventsPage(List<CostEvent> rows, int limit, int offset) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record BillingWindow(
            @JsonProperty("since") OffsetDateTime since,
            int calls,
            @JsonProperty("input_tokens") long inputTokens,
            @JsonProperty("output_tokens") long outputTokens,
            @JsonProperty("total_tokens") long totalTokens,
            @JsonProperty("cost_usd") String costUsd,
            @JsonProperty("top_model") String topModel,
            @JsonProperty("top_model_cost_usd") String topModelCostUsd,
            @JsonProperty("top_actor") String topActor,
            @JsonProperty("top_actor_cost_usd") String topActorCostUsd) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record BillingSummary(
            @JsonProperty("generated_at") OffsetDateTime generatedAt,
            @JsonProperty("last_24h") BillingWindow last24h,
            @JsonProperty("last_7d") BillingWindow last7d,
            @JsonProperty("last_30d") BillingWindow last30d) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record TopConsumerRow(
            String dimension,
            String value,
            @JsonProperty("input_tokens") long inputTokens,
            @JsonProperty("output_tokens") long outputTokens,
            @JsonProperty("total_tokens") long totalTokens,
            @JsonProperty("cost_usd") String costUsd,
            int calls) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record TopConsumersReport(String dimension, List<TopConsumerRow> rows) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record SubjectCostRow(
            @JsonProperty("subject_kind") String subjectKind,
            @JsonProperty("subject_id") String subjectId,
            @JsonProperty("input_tokens") long inputTokens,
            @JsonProperty("output_tokens") long outputTokens,
            @JsonProperty("total_tokens") long totalTokens,
            @JsonProperty("cost_usd") String costUsd,
            int calls) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record SubjectCostReport(List<SubjectCostRow> rows) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record LatencyRow(
            Map<String, String> group,
            int count,
            @JsonProperty("avg_ms") int avgMs,
            @JsonProperty("p50_ms") int p50Ms,
            @JsonProperty("p95_ms") int p95Ms,
            @JsonProperty("p99_ms") int p99Ms,
            @JsonProperty("max_ms") int maxMs) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record LatencyReport(List<LatencyRow> rows) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record SourceStats(
            int total,
            @JsonProperty("by_kind") Map<String, Integer> byKind,
            @JsonProperty("by_status") Map<String, Integer> byStatus,
            @JsonProperty("total_bytes") long totalBytes) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record KnowledgeItemStats(
            int total,
            @JsonProperty("by_status") Map<String, Integer> byStatus,
            @JsonProperty("by_domain") Map<String, Integer> byDomain) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record CandidateStats(
            int total,
            @JsonProperty("by_status") Map<String, Integer> byStatus) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record ChunkStats(
            int total,
            int embedded,
            @JsonProperty("embedded_pct") double embeddedPct) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record JobStats(
            int total,
            @JsonProperty("by_status") Map<String, Integer> byStatus,
            @JsonProperty("avg_attempts") double avgAttempts) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record CostStreamStats(
            @JsonProperty("total_events") long totalEvents,
            @JsonProperty("cost_usd_24h") String costUsd24h,
            @JsonProperty("cost_usd_30d") String costUsd30d) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record CorpusStats(
            @JsonProperty("generated_at") OffsetDateTime generatedAt,
            SourceStats sources,
            @JsonProperty("knowledge_items") KnowledgeItemStats knowledgeItems,
            @JsonProperty("knowledge_versions") int knowledgeVersions,
            CandidateStats candidates,
            ChunkStats chunks,
            @JsonProperty("ingest_jobs") JobStats ingestJobs,
            CostStreamStats cost) {
    }
}
