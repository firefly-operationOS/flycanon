# Copyright 2024-2026 Firefly Software Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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
