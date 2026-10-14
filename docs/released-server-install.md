# Install the released server

The server wheel requires Python 3.13 and explicit framework Git references. Package installers do not apply the repository's `[tool.uv.sources]` settings when installing a wheel. Installing only `flycanon-26.10.1-py3-none-any.whl` may fail because the matching Agentic version is not available from the package index.

Use the released source archive for the operator configuration and migration files, and install the released wheel for the Python application. This keeps the runtime configuration and executable package on the same version.

## Download and verify

Download `flycanon-26.10.1-py3-none-any.whl`, `flycanon-26.10.1.tar.gz`, `runtime-requirements-26.10.1.txt`, and `SHA256SUMS` from the [26.10.1 release](https://github.com/fireflyframework/flycanon/releases/tag/v26.10.1). Verify the downloaded files against their matching checksum entries before extracting or installing them.

```bash
tar -xzf flycanon-26.10.1.tar.gz
cd flycanon-26.10.1
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r ../runtime-requirements-26.10.1.txt
```

The requirements file selects the released server wheel and these canonical framework tags:

```text
pyfly @ git+https://github.com/fireflyframework/fireflyframework-pyfly.git@v26.09.04
fireflyframework-agentic @ git+https://github.com/fireflyframework/fireflyframework-agentic.git@v26.06.16
```

The equivalent installation without a requirements file is:

```bash
uv pip install --python .venv/bin/python \
  'flycanon @ https://github.com/fireflyframework/flycanon/releases/download/v26.10.1/flycanon-26.10.1-py3-none-any.whl' \
  'pyfly @ git+https://github.com/fireflyframework/fireflyframework-pyfly.git@v26.09.04' \
  'fireflyframework-agentic @ git+https://github.com/fireflyframework/fireflyframework-agentic.git@v26.06.16'
```

A fresh installation with these references resolved all runtime dependencies without `--no-deps` or a separate resolver override. Installing optional providers may require additional extras and configuration; this command does not verify cloud accounts or model-provider credentials.

## Configure the runtime

Keep `pyfly.yaml`, `alembic.ini`, `migrations/`, and `env_template` from the source archive in the working directory. The wheel does not supply the root-level `pyfly.yaml`. The release also provides a byte-identical `pyfly-26.10.1.yaml` copy for operators who already have a migration scaffold; install it as `pyfly.yaml` in the runtime working directory.

The configuration selects the PostgreSQL event adapter and separate API/worker consumer groups. Starting without it can activate framework defaults, including an attempted Kafka connection to `localhost:9092` and a separate management listener. Do not rely on those defaults for the released service.

Set the database URL, embedding model and dimensions, provider credentials, platform API keys, object-storage location, and optional Redis settings for your environment. Use [`env_template`](https://github.com/fireflyframework/flycanon/blob/v26.10.1/env_template) and the [deployment guide](https://github.com/fireflyframework/flycanon/blob/v26.10.1/docs/deployment.md) for the exact settings. Store real secrets outside published configuration and supply them through your operator's environment or secret manager.

Provision the PostgreSQL database, required extensions and roles using the deployment guide. Apply migrations as a separate operator step before serving a new database:

```bash
.venv/bin/flycanon migrate
```

Run from the directory containing the reviewed `pyfly.yaml`:

```bash
.venv/bin/uvicorn flycanon.main:app --host 127.0.0.1 --port 8500
```

For asynchronous intake, start the worker from the same configured directory in another process:

```bash
.venv/bin/flycanon worker
```

The standard `flycanon serve` command listens on all interfaces using `FLYCANON_PORT`. The explicit loopback command above is suitable for a local acceptance check; configure your deployment listener and gateway separately.

## Verify the installed service

```bash
.venv/bin/python -c 'from importlib.metadata import version; print(version("flycanon"), version("pyfly"), version("fireflyframework-agentic"))'
curl -fsS http://127.0.0.1:8500/api/v1/version
```

Verify authenticated source ingestion, source retrieval and canonical-memory retrieval in the intended tenant and workspace. A version response confirms the running package, while those operations exercise the configured database, retrieval and authorization boundaries. The release acceptance used a fresh wheel installation and verified source/memory retrieval for two independent project workspaces with no cross-project results; a newly provisioned operator environment still needs its own acceptance checks.
