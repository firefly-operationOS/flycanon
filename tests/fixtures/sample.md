# Sample Operational Document

## Scope

This document covers the canonical knowledge for ingestion testing.
Two scope areas:

* Validate the heading-aware Markdown loader.
* Validate the paragraph-bounded chunker.

## Procedure

Step one is to load the bytes. Step two is to split into sections.
Step three is to emit chunks with the section path attached.

### Edge cases

Very long paragraphs spill over the token budget and produce
multiple chunks bounded by the configured overlap.
