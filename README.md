# media-intelligence

A small organization-scoped API for sharing and correlating media intelligence like hashes, metadata or keywords.

## Start and run

From the repository root, create a local configuration for Docker Compose v2 on first setup:

```sh
cp .env.example .env
```

Set `ORGANIZATION_ID` in `.env` (the examples use `org-a`). Generate two independent secrets by running `python -c "import secrets; print(secrets.token_hex(32))"` twice. Set `POSTGRES_PASSWORD` to one value and `LOCAL_API_TOKEN` to the other.

```sh
docker compose up --build -d
. ./.env
export LOCAL_API_TOKEN
export API_URL="http://localhost:${API_PORT:-8000}"
curl -fsS "$API_URL/health/ready"
```

Compose starts PostgreSQL and runs migrations before starting the API. Once the API responds with `{"status":"ready"}`, run the examples below in the same shell. The interactive API reference is at [localhost:8000/docs](http://localhost:8000/docs) on the default port. To stop the stack and keep the database volume, run `docker compose down`. See the [deployment guide](docs/deployment.md) for updates and backups.

## Ingest artifacts

Each submission records an observation. The service assigns its ID, organization and publication time. The two examples below record separate synthetic observations of the same SHA-256 digest so you can see them match in the correlation section. Each uses its own idempotency key. Retry the same submission with the same key to get the original observation back.

### With the HTTP API

```sh
curl -fsS "$API_URL/artifacts/hashes" \
  -H "Authorization: Bearer $LOCAL_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: test-hash-api' \
  -d '{"algorithm":"sha256","value":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","calculated_at":"2026-01-01T00:00:00Z"}'
```

The response contains the new `HashArtifact`, including its `id`. The other artifact routes are `/artifacts/metadata`, `/artifacts/keywords` and `/artifacts/hosting-references`. Their fields are documented in the [profile guide](docs/profile.md).

### With the Python client

Install the client from the repository root in a Python 3.12+ virtual environment:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

```python
import os
from media_intelligence.client import MediaIntelligenceClient

api = MediaIntelligenceClient(os.environ["API_URL"], os.environ["LOCAL_API_TOKEN"])
observation = api.create_object(
    "HashArtifact",
    {"algorithm": "sha256", "value": "a" * 64, "calculated_at": "2026-01-02T00:00:00Z"},
    idempotency_key="test-hash-python",
)
print(observation["id"])
```

By default, both artifacts receive the organization's internal marking. To ingest an artifact for exchange, supply a sharing marking at creation as shown below.

## Share data

To send an observation from `org-a` to `org-b`, configure each instance's `config/directory.json` with the other organization as an accepted participant. Each receiver issues a distinct incoming bearer token for its peer and stores that token's SHA-256 hex digest. For example, on A, replace the placeholder below with the digest of the token A issued to B. On B, use `org-a` and the digest of the token B issued to A. Recreate each API container with `docker compose up -d --force-recreate api` after editing its directory.

```json
{
  "participants": {
    "org-b": {"accept": true, "token_sha256": "<64 lowercase hex characters>"}
  },
  "communities": {}
}
```

On A, run this Python example in the client environment from the previous section. It creates a marking that permits release to B, assigns it to a new hash observation, then writes the export response to a file. The package includes the marking B needs to interpret the observation.

```python
import json
import os
from pathlib import Path
from media_intelligence.client import MediaIntelligenceClient

api = MediaIntelligenceClient(os.environ["API_URL"], os.environ["LOCAL_API_TOKEN"])
marking = api.create_object(
    "Marking",
    {"distribution": "community", "recipients": ["org-b"], "allow_redistribution": False},
    idempotency_key="readme-org-b-marking",
)
shared = api.create_object(
    "HashArtifact",
    {"algorithm": "sha256", "value": "b" * 64, "calculated_at": "2026-01-03T00:00:00Z"},
    marking_id=marking["id"],
    idempotency_key="readme-org-b-hash",
)
package = api.export_package("org-b", [shared["id"]])
Path("package.json").write_text(json.dumps(package), encoding="utf-8")
```

From the directory containing `package.json`, set `RECIPIENT_API_URL` to B's HTTPS endpoint or trusted tunnel URL and `PEER_TOKEN` to the token B issued to A. Send the package to B:

```sh
curl -fsS "$RECIPIENT_API_URL/exchange/import" \
  -H "Authorization: Bearer $PEER_TOKEN" \
  -H 'Content-Type: application/json' \
  --data-binary @package.json
```

B returns `accepted` when it inserts publications and `already-present` when it already has every version in the package. To include case or media context, select the relevant relation IDs for export as well. See the [exchange guide](docs/exchange.md) for dependency rules and verified file imports.

## Correlate observations

After running both ingestion examples, query A for matching observations. You can run this before configuring a peer:

```sh
curl -fsS "$API_URL/correlations?offset=0&limit=20" \
  -H "Authorization: Bearer $LOCAL_API_TOKEN" | python -m json.tool
```

On a fresh instance, `total` is `1`. The entry in `items` contains the two observation IDs as `left_id` and `right_id`, with `rule` set to `exact-hash` and `match_type` set to `exact-value`. The timestamps differ, but the algorithm and digest match. The context lists are empty until you link the observations to media instances.

Correlations also include visible observations imported from accepted peers. Metadata matches on type, key and typed value, keywords on source and text, and hosting references on the exact resource string. Robust hashes appear in `unsupported_observations`. See the [comparison rules](docs/profile.md#comparison-rules-version-1) for details.
