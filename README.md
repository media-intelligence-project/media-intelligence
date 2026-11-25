# media-intelligence

A small organization-scoped API for media observations, explicit context and JSON package exchange.
No original media, user accounts, automatic synchronization or robust-hash comparison.

## Run

Requires Docker with Compose v2 and Python 3.12+ only for the optional local workflow.

```sh
cp .env.example .env
# Set ORGANIZATION_ID and two independent random secrets in .env.
# Generate each secret: python -c "import secrets; print(secrets.token_hex(32))"
docker compose up --build -d
curl http://localhost:8000/health/ready
```

The API binds to localhost. Interactive API reference: `http://localhost:8000/docs`.
PostgreSQL is private to the Compose network. Data survives container replacement.

## Create a hash

```sh
export TOKEN='your LOCAL_API_TOKEN'
curl -s http://localhost:8000/artifacts/hashes \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: first-synthetic-observation' \
  -d '{"algorithm":"sha256","value":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","calculated_at":"2026-01-01T00:00:00Z"}'
```

The API assigns identity, organization and publication time. An omitted `marking_id` uses
an internal marking created on first startup. Supply an explicit sharing marking to export later.


```sh
curl -s http://localhost:8000/objects -H "Authorization: Bearer $TOKEN"
curl -s http://localhost:8000/correlations -H "Authorization: Bearer $TOKEN"
```

## Python client

Install the package with `python -m pip install -e .` in a Python 3.12+ virtual environment.
Use the token exported above to list SHA-256 observations.

```python
import os
from media_intelligence.client import MediaIntelligenceClient

api = MediaIntelligenceClient("http://localhost:8000", os.environ["TOKEN"])
page = api.list_objects(type="HashArtifact", algorithm="sha256", limit=20)
for observation in page["items"]:
    print(observation["id"], observation["value"])
```


## Short guides

- [Profile and API](docs/profile.md)
- [Package exchange](docs/exchange.md)
- [Deployment, updates and backup](docs/deployment.md)
