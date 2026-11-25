"""HTTP boundary: authentication, bounded inputs, explicit transactions."""
from contextlib import asynccontextmanager
import hashlib
import hmac
import json
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError
from sqlalchemy import text

from .config import load_settings
from .core import (Rejected, available, canonical, comparison_key, correlations, create,
                   default_marking, export_package, import_package, key, latest, merge,
                   new_version, visible)
from .models import (ARTIFACTS, BatchRequest, CreateRequest, ExportRequest, HashArtifact,
                     Marking, Package, VersionRequest)
from .storage import Store

MAX_BODY_BYTES = 2 * 1024 * 1024
security = HTTPBearer(auto_error=False)


class BodyLimit:
    """Enforce actual streamed size, not only Content-Length."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            size += len(body)
            if size > MAX_BODY_BYTES:
                return await JSONResponse({"detail": "Request exceeds 2 MiB"}, status_code=413)(scope, receive, send)
            chunks.append(body)
            if not message.get("more_body", False):
                break
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)


def create_app(settings=None, store=None):
    settings = settings or load_settings()
    store = store or Store(settings.database_url)
    default = default_marking(settings.organization)

    @asynccontextmanager
    async def lifespan(app):
        with store.transaction(write=True) as tx:
            existing = tx.objects()
            if not any(obj.id == default.id for obj in existing):
                merge(existing, [default])
                tx.insert([default])
        yield

    app = FastAPI(title="media-intelligence", version="0.1.0", lifespan=lifespan)
    app.add_middleware(BodyLimit)

    def principal(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)]):
        if credentials is None:
            raise HTTPException(401, "Bearer authentication required", headers={"WWW-Authenticate": "Bearer"})
        token = credentials.credentials
        if hmac.compare_digest(token.encode(), settings.local_token.encode()):
            return settings.organization
        digest = hashlib.sha256(token.encode()).hexdigest()
        for organization, participant in settings.participants.items():
            if hmac.compare_digest(digest, participant.token_sha256) and participant.accept:
                return organization
        raise HTTPException(401, "Invalid credential", headers={"WWW-Authenticate": "Bearer"})

    def local(who: Annotated[str, Depends(principal)]):
        if who != settings.organization:
            raise HTTPException(403, "Local organization access required")
        return who

    @app.exception_handler(Rejected)
    async def rejected(request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ValidationError)
    async def invalid_model(request, exc):
        return JSONResponse(status_code=422, content={"detail": "Publication does not satisfy the profile"})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        errors = [{"location": list(x["loc"]), "type": x["type"]} for x in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": errors})

    def allowed_view(objects):
        accepted = [obj for obj in objects if obj.created_by == settings.organization or
                    (obj.created_by in settings.participants and settings.participants[obj.created_by].accept)]
        return visible(accepted, settings.organization, settings.communities)

    def validate_local_markings(created, objects):
        known = latest(objects)
        for obj in created:
            if isinstance(obj, Marking):
                if any(org not in settings.participants and org != settings.organization for org in obj.recipients):
                    raise Rejected("Marking names an unknown participant")
                if any(group not in settings.communities for group in obj.communities):
                    raise Rejected("Marking names an unknown community")
            else:
                marking = known.get(obj.marking_id)
                if not isinstance(marking, Marking) or marking.created_by != settings.organization:
                    raise Rejected("Use a marking created by this organization")

    def write_created(requests, retry_key=None, scope="objects"):
        if retry_key is not None and not 1 <= len(retry_key) <= 128:
            raise Rejected("Idempotency-Key must contain 1 to 128 characters")
        fingerprint = hashlib.sha256(json.dumps([r.model_dump(mode="json") for r in requests], sort_keys=True).encode()).hexdigest()
        with store.transaction(write=True) as tx:
            cache_key = f"{scope}:{retry_key}" if retry_key else None
            if cache_key:
                cached = tx.retry(cache_key)
                if cached:
                    if cached[0] != fingerprint:
                        raise Rejected("Idempotency-Key was used with a different request")
                    return cached[1]
            objects = tx.objects()
            created = []
            for index, request in enumerate(requests):
                try:
                    created.append(create(request, settings.organization, default.id))
                except (ValidationError, Rejected):
                    raise HTTPException(422, {"index": index, "detail": "Invalid publication"}) from None
            validate_local_markings(created, [*objects, *created])
            combined, _ = merge(objects, created)
            # A local statement may reference only locally visible information.
            view = allowed_view(combined)
            if any(x.id not in view for x in created):
                raise Rejected("Creation references information unavailable to this organization")
            tx.insert(created)
            response = [obj.model_dump(mode="json") for obj in created]
            if cache_key:
                tx.remember(cache_key, fingerprint, response)
            return response

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready():
        with store.transaction() as tx:
            tx.connection.execute(text("SELECT 1 FROM publications LIMIT 1"))
        return {"status": "ready"}

    @app.get("/profile")
    def profile(who: Annotated[str, Depends(local)]):
        return {"profile": "media-intelligence", "schema_version": "1.0", "organization": settings.organization,
                "default_marking_id": default.id, "robust_comparison": False}

    @app.get("/schema/package")
    def schema():
        return Package.model_json_schema()

    @app.post("/objects", status_code=201)
    def create_object(body: CreateRequest, who: Annotated[str, Depends(local)],
                      idempotency_key: Annotated[str | None, Header()] = None):
        return write_created([body], idempotency_key)[0]

    # Convenience routes use the same creation path; they cannot bypass model rules.
    def convenience(kind, scope):
        def endpoint(body: dict, who: Annotated[str, Depends(local)],
                     idempotency_key: Annotated[str | None, Header()] = None):
            fields = dict(body)
            marking_id = fields.pop("marking_id", None)
            return write_created([CreateRequest(type=kind, marking_id=marking_id, data=fields)], idempotency_key, scope)[0]
        return endpoint

    routes = {"artifacts/hashes": "HashArtifact", "artifacts/metadata": "MetadataArtifact",
              "artifacts/keywords": "KeywordArtifact", "artifacts/hosting-references": "HostingReference",
              "media-instances": "MediaInstance", "cases": "Case", "series": "Series",
              "infrastructures": "Infrastructure", "markings": "Marking", "relations": "Relation"}
    for path, kind in routes.items():
        endpoint = convenience(kind, path)
        endpoint.__name__ = "create_" + kind
        app.add_api_route("/" + path, endpoint, methods=["POST"], status_code=201, name=endpoint.__name__)

    @app.post("/artifacts/batch", status_code=201)
    def batch(body: BatchRequest, who: Annotated[str, Depends(local)],
              idempotency_key: Annotated[str | None, Header()] = None):
        requests = [item.model_copy(update={"marking_id": item.marking_id or body.marking_id}) for item in body.items]
        return write_created(requests, idempotency_key, "batch")

    @app.get("/objects")
    def list_objects(who: Annotated[str, Depends(local)], type: str | None = None,
                     created_by: str | None = None, algorithm: str | None = None, value: str | None = None,
                     offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)):
        with store.transaction() as tx:
            objects = sorted(allowed_view(tx.objects()).values(), key=lambda x: x.id)
        filtered = [x for x in objects if (type is None or x.type == type) and
                    (created_by is None or x.created_by == created_by) and
                    (algorithm is None or getattr(x, "algorithm", None) == algorithm) and
                    (value is None or getattr(x, "value", None) == value)]
        return {"items": filtered[offset:offset+limit], "total": len(filtered), "offset": offset, "limit": limit}

    @app.get("/objects/{object_id}")
    def get_object(object_id: str, who: Annotated[str, Depends(local)]):
        with store.transaction() as tx:
            obj = allowed_view(tx.objects()).get(object_id)
        if obj is None:
            raise HTTPException(404, "Object unavailable")
        return obj

    @app.get("/objects/{object_id}/versions")
    def history(object_id: str, who: Annotated[str, Depends(local)],
                offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)):
        with store.transaction() as tx:
            objects = tx.objects()
        if object_id not in allowed_view(objects):
            raise HTTPException(404, "Object unavailable")
        history = [x for x in objects if x.id == object_id and available(x, objects, settings.organization, settings.communities)]
        # Re-evaluate historical relation endpoints too, not just its marking.
        safe = []
        for obj in history:
            candidate = [x for x in objects if x.id != object_id] + [obj]
            if object_id in allowed_view(candidate):
                safe.append(obj)
        return {"items": safe[offset:offset+limit], "total": len(safe)}

    @app.post("/objects/{object_id}/versions", status_code=201)
    def version(object_id: str, body: VersionRequest, who: Annotated[str, Depends(local)]):
        with store.transaction(write=True) as tx:
            objects = tx.objects()
            obj = new_version(objects, object_id, body, settings.organization)
            validate_local_markings([obj], objects)
            combined, _ = merge(objects, [obj])
            if obj.id not in allowed_view(combined):
                raise Rejected("Version references unavailable information")
            tx.insert([obj])
        return obj

    @app.get("/relations")
    def relations(who: Annotated[str, Depends(local)], source_id: str | None = None,
                  target_id: str | None = None, include_inactive: bool = False,
                  offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)):
        with store.transaction() as tx:
            objects = allowed_view(tx.objects())
        matches = [x for x in objects.values() if x.type == "Relation" and
                   (include_inactive or x.status == "active") and
                   (source_id is None or x.source.id == source_id or (x.relation_type == "related-to" and x.target.id == source_id)) and
                   (target_id is None or x.target.id == target_id or (x.relation_type == "related-to" and x.source.id == target_id))]
        matches.sort(key=lambda x: x.id)
        return {"items": matches[offset:offset+limit], "total": len(matches)}

    @app.get("/correlations")
    def matches(who: Annotated[str, Depends(local)], offset: int = Query(0, ge=0),
                limit: int = Query(100, ge=1, le=500)):
        with store.transaction() as tx:
            all_objects = tx.objects()
            objects = [x for x in all_objects if x.created_by == settings.organization or
                       (x.created_by in settings.participants and settings.participants[x.created_by].accept)]
        items, total = [], 0
        for match in correlations(objects, settings.organization, settings.communities):
            if offset <= total < offset + limit:
                items.append(match)
            total += 1
        unsupported = sorted(x.id for x in allowed_view(objects).values() if isinstance(x, HashArtifact) and comparison_key(x) is None)
        return {"items": items, "total": total, "unsupported_observations": unsupported[offset:offset+limit],
                "unsupported_total": len(unsupported), "unsupported_reason": "Robust hash comparison is not implemented"}

    @app.post("/exchange/export")
    def export(body: ExportRequest, who: Annotated[str, Depends(local)]):
        if body.recipient not in settings.participants or not settings.participants[body.recipient].accept:
            raise Rejected("Recipient is not an accepted participant")
        with store.transaction() as tx:
            objects = tx.objects()
        package = export_package(objects, body, settings.organization, settings.participants, settings.communities)
        if len(package.model_dump_json().encode()) > MAX_BODY_BYTES:
            raise Rejected("Export exceeds 2 MiB; select fewer publications")
        return package

    @app.post("/exchange/import")
    def receive(body: Package, who: Annotated[str, Depends(principal)],
                verified_sender: Annotated[str | None, Header(alias="X-Verified-Sender")] = None):
        if who == settings.organization:
            if not verified_sender:
                raise HTTPException(400, "File import requires X-Verified-Sender attestation by the local operator")
            sender = verified_sender
        else:
            if verified_sender is not None:
                raise HTTPException(400, "Remote callers cannot override their authenticated sender")
            sender = who
        if sender not in settings.participants or not settings.participants[sender].accept:
            raise Rejected("Sender is not an accepted participant")
        if any(group not in settings.communities for obj in body.objects if isinstance(obj, Marking) for group in obj.communities):
            raise Rejected("A marking refers to an unverified community")
        with store.transaction(write=True) as tx:
            objects = tx.objects()
            combined, count = import_package(objects, body, sender, settings.organization, settings.participants, settings.communities)
            tx.insert(combined)
            tx.log_import(body.package_id, sender, count)
        return {"status": "accepted" if count else "already-present", "inserted": count, "package_id": str(body.package_id)}

    return app
