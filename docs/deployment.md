# Deployment

## Setup

1. Copy `.env.example` to `.env` and set the organization ID.
2. Generate separate random local-token and database-password values (64 hexadecimal characters).
3. Configure known participants and optional communities; see `config/README.md`.
4. Run `docker compose up --build -d`.
5. Check `/health/ready` and authenticated `/profile`.

The one-shot `migrate` service runs Alembic before the API starts. Schema version 0001
creates publications, request_retries and import_records. The initial internal marking
is created once at startup. Startup never overwrites existing publications.
The API runs as a non-root user with a read-only root filesystem; PostgreSQL has no host port.
Health probes and schema/docs routes are public and return no observation content.

Protect `.env`, database volumes and backups with host permissions and disk encryption.
Do not commit real directory membership or captured package files. The empty directory
file in the repository is a starting template. Disable broad access to the Docker host.
Credentials are bearer secrets. Rotate them by replacing values and restarting the API.
Revoking a remote incoming token does not delete already received content.

## Remote access

Default binding is localhost. Put a TLS reverse proxy or private tunnel in front of the API
when remote access is required. Do not expose the development HTTP port on a public interface.
Apply connection/request limits at that boundary. Application requests are limited to 2 MiB
and the server limits concurrency to 32; these are not a complete abuse-prevention system.
Never put bearer tokens in URLs. Access logging is disabled in the container command.
Do not use synthetic samples as reference determinations about real content.

## Backup and restore

```sh
mkdir -p backups
chmod 700 backups
docker compose exec -T db pg_dump -U media_intelligence -d media_intelligence -Fc > backups/database.dump
```

Back up configuration and credentials separately and securely. Stop writers before restore:

```sh
docker compose stop api
docker compose exec -T db pg_restore -U media_intelligence -d media_intelligence \
  --clean --if-exists --no-owner < backups/database.dump
docker compose up -d api
```

Test restore on a separate deployment. `docker compose down` keeps the volume;
`docker compose down -v` permanently removes the database volume.

## Update

Back up first. Stop API writers, build the new image and run migrations explicitly:

```sh
docker compose stop api
docker compose build
docker compose run --rm migrate
docker compose up -d api
```

Pydantic is pinned to the version used for model tests and schema generation.
Other dependencies and base images use bounded compatible ranges, not a reproducible lock.
Before a controlled deployment, resolve and record exact image digests/dependencies, review
vulnerabilities and run the full tests. This archive does not claim a security audit.

## Development and tests

Use Python 3.12+ and `pip install -e '.[test]'` for local development. Set DATABASE_URL,
ORGANIZATION_ID, LOCAL_API_TOKEN and DIRECTORY_FILE, then run `alembic upgrade head`
and `uvicorn media_intelligence.main:app`.

For integration tests use Compose test service, or set TEST_DATABASE_URL to a dedicated
PostgreSQL database and run pytest. Tests create/drop temporary schemas in that database.
No production credentials, database or real sensitive content should be used for tests.
