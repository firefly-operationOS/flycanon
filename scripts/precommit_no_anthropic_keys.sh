#!/usr/bin/env sh
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

#
# Reject staged content that looks like a live Anthropic API key. Run
# from pre-commit; the hook stages the filenames as positional args.
set -eu

# Anthropic live key prefix. Catches anyone copy-pasting a real key into
# an example, a script, or a test fixture.
PATTERN='sk-ant-api[0-9]{2}-'

bad=0
for path in "$@"; do
  if grep -E -l "$PATTERN" "$path" >/dev/null 2>&1; then
    echo "error: $path contains an Anthropic API key prefix." >&2
    bad=1
  fi
done

exit "$bad"
