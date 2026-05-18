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

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Hit(
            @JsonProperty("chunk_id") String chunkId,
            @JsonProperty("source_id") String sourceId,
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
}
