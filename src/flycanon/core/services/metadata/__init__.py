# Copyright 2026 Firefly Software Solutions Inc
"""Document metadata extraction.

Pulls the metadata flycanon needs to enrich every ingested source --
title, author, dates, language, page count, structural facets -- via
format-specific extractors and one umbrella :class:`MetadataExtractor`
service. The extracted fields land on ``SourceRow.metadata_json``
(under the ``extracted`` key) so the search + filter surfaces have
useful facets without re-parsing the bytes.
"""

from flycanon.core.services.metadata.extractor import (
    ExtractedMetadata,
    MetadataExtractor,
)

__all__ = ["ExtractedMetadata", "MetadataExtractor"]
