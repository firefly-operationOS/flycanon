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

import com.firefly.flycanon.sdk.model.Models;

import java.util.List;
import java.util.Map;

/**
 * ``403 agent_workspace_not_in_allowlist`` -- the token's allowlist
 * is non-empty and does not include the ``X-Workspace-Id`` header.
 */
public class AgentWorkspaceNotInAllowlist extends CanonAPIException {

    public AgentWorkspaceNotInAllowlist(int statusCode,
                                        String code,
                                        String title,
                                        String detail,
                                        Map<String, Object> extensions,
                                        List<Models.FieldError> errors) {
        super(statusCode, code, title, detail, extensions, errors);
    }
}
