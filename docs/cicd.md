<div align="center">

<img src="assets/logo.png" alt="flycanon" width="380" />

### **CI / CD**

</div>

---

flycanon ships three GitHub Actions workflows. They are mutually
exclusive: every commit triggers exactly one of them, every artefact
publish triggers another, and a manual escape hatch covers the rest.

| Workflow | File | Trigger | What it produces |
|----------|------|---------|------------------|
| **PR gate** | [`.github/workflows/pr-gate.yaml`](../.github/workflows/pr-gate.yaml) | `pull_request` against `main`/`develop`, `workflow_dispatch` | Status checks only -- nothing published. |
| **Docker publish (multi-arch)** | [`.github/workflows/docker-publish.yaml`](../.github/workflows/docker-publish.yaml) | `push` to `main`, `push` of a `v*.*.*` tag, `workflow_dispatch` | `ghcr.io/firefly-operationos/flycanon` multi-arch image (`linux/amd64` + `linux/arm64`), provenance + SBOM. |
| **Publish SDKs** | [`.github/workflows/publish-sdks.yaml`](../.github/workflows/publish-sdks.yaml) | `push` of a `v*.*.*` tag, `workflow_dispatch` | Java SDK to GitHub Packages (`com.firefly:flycanon-sdk:<version>`); Python SDK wheel + sdist attached to the GitHub Release. |

The same three workflows ship with every Firefly OperationOS service
(flydocs, flycanon, ...) so the release loop is identical across the
fleet. flycanon's deviations from the shared shape are called out
inline below.

---

## 1. PR gate

`pr-gate.yaml` runs the moment a PR is opened or updated. It is a
**no-publish** workflow -- it never pushes images, never deploys, and
never tags. Its only job is to gate the merge.

| Job | What it runs | Required for merge? |
|-----|--------------|----------------------|
| `lint` | `ruff check` + `ruff format --check` over `src/` and `tests/` | Yes |
| `typecheck` | `pyright` over `src/` (advisory -- failure surfaces as a warning, does not block) | No (advisory) |
| `unit` | `uv sync --extra dev` + `pytest tests/unit` | Yes |
| `docker-build` | Single-arch Docker build of `Dockerfile` against the same sibling-clone matrix CI uses for the publish workflow -- catches Dockerfile drift early. | Yes |
| `sdk-python` | `ruff check` + `pytest` under `sdks/python` | Yes |
| `sdk-java` | `mvn -B -ntp verify` under `sdks/java` with Temurin Java 25 | Yes |

**Sibling repos.** flycanon depends on `fireflyframework-pyfly` and
`fireflyframework-agentic` as **path dependencies** in
`pyproject.toml`. The PR gate clones both into `./vendor/` so `uv
sync` resolves them the same way it does on a developer's laptop.

**uv.lock is gitignored** -- the `unit` and `sdk-python` jobs run
`uv sync` (not `uv sync --frozen`) so the resolver picks the matching
sibling-repo SHAs at CI time.

**Skipped on docs-only commits.** The trigger filter strips `**.md`,
`LICENSE`, `.gitignore`, and `docs/**` -- a typo fix in a `.md` file
does not burn CI minutes.

---

## 2. Docker publish (multi-arch)

`docker-publish.yaml` builds + pushes `ghcr.io/firefly-operationos/flycanon`
for **`linux/amd64` + `linux/arm64`** via QEMU + BuildKit. The GHCR
owner is normalised to lower-case at build time so the
`firefly-operationOS` org name (mixed case in URLs but lower-case
required by GHCR) doesn't bite.

### Triggers and tags

| Trigger | Resulting tags |
|---------|----------------|
| `push` to `main` | `main`, `sha-<short>`, `latest` |
| `push` of `v26.5.4` | `26.5.4`, `26.5`, `26`, `v26.5.4`, `latest` |
| `workflow_dispatch` | `manual-<run-id>` |

Tag computation is delegated to
[`docker/metadata-action@v5`](https://github.com/docker/metadata-action),
configured with `type=semver`, `type=ref,event=branch`,
`type=ref,event=tag`, `type=sha`, and one `type=raw` line for the
manual case. See the inline `meta:` step in
[`.github/workflows/docker-publish.yaml`](../.github/workflows/docker-publish.yaml)
for the exact pattern.

### Build contexts

The Dockerfile uses BuildKit named contexts to pull in the sibling
pyfly + agentic repos without copying them into flycanon's git tree
(see [`Dockerfile`](../Dockerfile) for the consumer side):

```yaml
build-contexts: |
  pyfly=./vendor/pyfly
  fireflyframework-agentic=./vendor/fireflyframework-agentic
```

`./vendor/*` is `git clone --depth=1 --branch $PYFLY_REF / $AGENTIC_REF`
checked out in a CI step. The same Dockerfile drives both the CI
publish path and developers' `docker buildx build --build-context
pyfly=../../fireflyframework/fireflyframework-pyfly ...` invocations.

### Provenance + SBOM

`build-push-action@v6` is invoked with `provenance: true` +
`sbom: true`, so the published image ships SLSA build provenance and
a CycloneDX SBOM by default. The optional
`actions/attest-build-provenance@v2` step is marked
`continue-on-error: true` -- if the org doesn't have GitHub
Attestations enabled (free-tier orgs, private repos without the
feature) the upload skips silently rather than failing the publish.

### Docling extra is **opt-in**, not baked

flycanon's OCR engine is selectable at runtime via
`FLYCANON_PDF_OCR_ENGINE=tesseract|docling`. The published image
ships **Tesseract only**; operators that want layout-aware OCR (the
`docling` engine) install the `docling` extra in a derived image
because the PyTorch + HuggingFace wheels add ~2.5 GB per architecture
(see [deployment.md § OCR engines](deployment.md#ocr-engines)).

### Manifest layout

| Tag | Arch | Notes |
|-----|------|-------|
| `:latest`, `:26.5.4`, `:main` | `linux/amd64` + `linux/arm64` | Default pull on either arch picks the right manifest transparently. |

Pull explicitly with `--platform`:

```bash
docker pull ghcr.io/firefly-operationos/flycanon:latest                       # arm64 + amd64 manifest
docker pull --platform linux/arm64 ghcr.io/firefly-operationos/flycanon:latest
docker pull --platform linux/amd64 ghcr.io/firefly-operationos/flycanon:latest
```

---

## 3. Publish SDKs

`publish-sdks.yaml` runs on every `v*.*.*` tag (or manually via
`workflow_dispatch`). The two SDKs are published to different
registries because their consumer ecosystems prefer different
distribution mechanics:

### Java SDK -> GitHub Packages

* **Coordinates**: `com.firefly:flycanon-sdk:<version>`
* **Registry**: `https://maven.pkg.github.com/firefly-operationOS/flycanon`
* **Java**: Temurin 25 (LTS), same as the SDK's `<java.version>`.

The workflow:

1. Sets up Java 25 via `actions/setup-java@v4` with `server-id: github`
   -- this writes a `~/.m2/settings.xml` containing a `github` server
   entry whose credentials are `${GITHUB_ACTOR}` + `${GITHUB_TOKEN}`.
2. On tag pushes, runs `mvn versions:set -DnewVersion=<tag>` so the
   deployed artefact's version matches the git tag exactly.
3. Runs `mvn -B -ntp deploy`. Maven reads the `github` server entry
   from `settings.xml` and authenticates against GitHub Packages.

Consumer setup (in any project depending on flycanon-sdk):

```xml
<dependency>
  <groupId>com.firefly</groupId>
  <artifactId>flycanon-sdk</artifactId>
  <version>26.5.4</version>
</dependency>

<repositories>
  <repository>
    <id>github-firefly-operationOS</id>
    <url>https://maven.pkg.github.com/firefly-operationOS/flycanon</url>
  </repository>
</repositories>
```

Plus a `~/.m2/settings.xml` entry with a personal access token
scoped `read:packages` -- GitHub Packages requires authentication
even for public reads.

### Python SDK -> GitHub Release assets

* **Package**: `flycanon-sdk`
* **Distribution form**: wheel + sdist attached to the GitHub Release
  (no PyPI publish step -- by design).

The workflow:

1. Sets up Python 3.13 + uv.
2. On tag pushes, rewrites `project.version` in `pyproject.toml`
   **and** `__version__` in `src/flycanon_sdk/__init__.py` so both
   stay in sync with the git tag.
3. Runs `uv build` -- produces a wheel (`flycanon_sdk-<ver>-py3-none-any.whl`)
   and an sdist (`flycanon_sdk-<ver>.tar.gz`) in `sdks/python/dist/`.
4. Uploads both via `softprops/action-gh-release@v2`, attaching them
   as release assets and appending install instructions to the
   auto-generated release notes.

Consumer setup:

```bash
uv add https://github.com/firefly-operationOS/flycanon/releases/download/v26.5.4/flycanon_sdk-26.5.4-py3-none-any.whl
```

Or pin in `pyproject.toml`:

```toml
[project]
dependencies = ["flycanon-sdk"]

[tool.uv.sources]
flycanon-sdk = { url = "https://github.com/firefly-operationOS/flycanon/releases/download/v26.5.4/flycanon_sdk-26.5.4-py3-none-any.whl" }
```

**PEP 440 normalisation.** Tag `v26.05.04` produces the wheel name
`flycanon_sdk-26.5.4-py3-none-any.whl` (leading zeros stripped).
The workflow computes both `TAG_VERSION` (matches the git tag) and
`WHEEL_VERSION` (the PEP 440 form) so the release-notes install
snippet points at the right asset URL.

---

## Release cookbook

The end-to-end release loop is **one tag push**:

```bash
# 1. Bump the version anywhere the team tracks it (here: README badge,
#    sdks/python/pyproject.toml, sdks/python/src/flycanon_sdk/__init__.py,
#    sdks/java/pom.xml).  Conventionally CalVer -- YY.MM.PP.

# 2. Commit, tag, push.
git commit -am "release: 26.5.2"
git tag -a v26.5.2 -m "v26.5.2"
git push origin main --tags
```

The push triggers:

* `docker-publish.yaml` -- the multi-arch image lands on GHCR as
  `:26.5.2`, `:26.5`, `:26`, and `:latest`.
* `publish-sdks.yaml` -- the Java SDK is deployed to GitHub Packages
  as `com.firefly:flycanon-sdk:26.5.2`; the Python wheel is attached
  to the GitHub Release created for the tag.

**Manual re-publish.** Both publishing workflows expose
`workflow_dispatch` so you can re-publish a release if the
auto-triggered run flaked (transient registry failure, runner
preemption, etc.) without retagging.

---

## Required secrets

| Secret | Why | Set where |
|--------|-----|-----------|
| `GITHUB_TOKEN` | Auto-provisioned by Actions. Used to push to GHCR + GitHub Packages + create release assets. | Auto. |
| (none else) | flycanon's publishing surfaces all sit inside GitHub. No PyPI / Docker Hub / Maven Central credentials are needed. | -- |

The runtime stack (Postgres credentials, embedding-provider API
keys, Anthropic API key, ...) is **not** baked into any CI workflow
-- those are deployment-time configuration; see
[deployment.md § Environment](deployment.md#environment).

---

## Troubleshooting CI failures

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `docker-build` job fails on `COPY --from=pyfly`/`agentic` | Sibling clone step skipped or branch ref drifted | Re-trigger; check `PYFLY_REF` / `AGENTIC_REF` env vars match the published sibling branch. |
| `sdk-java` fails with "Failed to deploy artifacts: ... 422 Unprocessable Entity" | Version already published to GitHub Packages -- you can't re-deploy the same coordinates. | Bump the patch version (`v26.5.2 -> v26.5.4`) and retag. |
| `actions/attest-build-provenance` step shows red but the image still landed | Org doesn't have Attestations enabled. The step is advisory (`continue-on-error: true`), the image is valid. | Optional: enable Attestations on the org. |
| `unit` job hits `ModuleNotFoundError: pyfly` / `fireflyframework_agentic` | The sibling-repo clone step failed silently. | Inspect the `Clone sibling firefly framework repos` step's log; verify the public clone URL + branch. |
| Python SDK wheel filename does not match the tag (`v26.05.04` vs `26.5.4`) | PEP 440 normalisation -- expected behaviour. | The release-notes step computes `WHEEL_VERSION` separately; the install URL in the release body is correct. |

For runtime / deployment failures (image runs but service crashes,
embeddings unavailable, ...), see
[troubleshooting.md](troubleshooting.md).
