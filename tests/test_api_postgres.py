"""Real PostgreSQL + API tests. Each test gets an isolated temporary schema."""
import hashlib
import os
from pathlib import Path
from uuid import uuid4
import pytest

# Missing integration dependencies do not prevent running the pure model tests.
pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")
pytest.importorskip("alembic")
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from media_intelligence.api import create_app
from media_intelligence.config import Settings
from media_intelligence.storage import Store

BASE = Path(__file__).resolve().parents[1]
LOCAL = "a" * 64
REMOTE = "b" * 64
AUTH = {"Authorization": "Bearer " + LOCAL}


@pytest.fixture
def client(monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a dedicated PostgreSQL database")
    schema = "test_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = make_url(url).update_query_dict({"options": "-csearch_path=" + schema}).render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", scoped)
    config = Config(str(BASE / "alembic.ini"))
    config.set_main_option("script_location", str(BASE / "migrations"))
    command.upgrade(config, "head")
    settings = Settings(organization="org-a", local_token=LOCAL, database_url=scoped,
                        participants={"org-b":{"token_sha256":hashlib.sha256(REMOTE.encode()).hexdigest()}})
    store = Store(scoped)
    try:
        with TestClient(create_app(settings, store)) as client:
            yield client, store, settings
    finally:
        store.engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def post_hash(client, **changes):
    payload={"algorithm":"sha256","value":"a"*64,"calculated_at":"2026-01-01T00:00:00Z"}
    payload.update(changes)
    return client.post("/artifacts/hashes",headers=AUTH,json=payload)


def test_authentication_and_default_marking(client):
    api,_,_=client
    assert api.get("/objects").status_code==401
    assert api.get("/objects",headers={"Authorization":"Bearer "+REMOTE}).status_code==403
    result=post_hash(api)
    assert result.status_code==201,result.text
    assert result.json()["marking_id"]==api.get("/profile",headers=AUTH).json()["default_marking_id"]


def test_idempotency_key(client):
    api,_,_=client
    payload={"type":"KeywordArtifact","data":{"value":"synthetic","source":"tag"}}
    headers={**AUTH,"Idempotency-Key":"request-one"}
    first=api.post("/objects",headers=headers,json=payload)
    second=api.post("/objects",headers=headers,json=payload)
    assert first.status_code==201 and first.json()==second.json()
    payload["data"]["value"]="changed"
    assert api.post("/objects",headers=headers,json=payload).status_code==409


def test_batch_is_atomic(client):
    api,_,_=client
    before=api.get("/objects",headers=AUTH).json()["total"]
    result=api.post("/artifacts/batch",headers=AUTH,json={"items":[
        {"type":"KeywordArtifact","data":{"value":"synthetic","source":"tag"}},
        {"type":"HashArtifact","data":{"algorithm":"sha256","value":"bad","calculated_at":"2026-01-01T00:00:00Z"}}
    ]})
    assert result.status_code==422
    assert result.json()["detail"]["index"]==1
    assert api.get("/objects",headers=AUTH).json()["total"]==before


def test_versioning_and_restart(client):
    api,store,settings=client
    case=api.post("/cases",headers=AUTH,json={"name":"Synthetic"}).json()
    endpoint=f"/objects/{case['id']}/versions"
    assert api.post(endpoint,headers=AUTH,json={"expected_version":1,"changes":{"name":"Updated"}}).status_code==201
    assert api.post(endpoint,headers=AUTH,json={"expected_version":1,"changes":{"name":"Stale"}}).status_code==409
    with TestClient(create_app(settings,store)) as restarted:
        assert restarted.get(f"/objects/{case['id']}",headers=AUTH).json()["version"]==2
        assert restarted.get(endpoint,headers=AUTH).json()["total"]==2


def test_complete_import_and_repetition(client):
    from media_intelligence.core import create, default_marking
    from media_intelligence.models import CreateRequest, Package, build_package
    api,_,_=client
    marking=create(CreateRequest(type="Marking",data={"distribution":"community","recipients":["org-a"]}),"org-b","")
    artifact=create(CreateRequest(type="HashArtifact",marking_id=marking.id,data={"algorithm":"sha256","value":"a"*64,"calculated_at":"2026-01-01T00:00:00Z"}),"org-b","")
    package=build_package(sender="org-b",recipient="org-a",objects=[marking,artifact])
    assert post_hash(api).status_code==201
    remote={"Authorization":"Bearer "+REMOTE}
    first=api.post("/exchange/import",headers=remote,json=package.model_dump(mode="json"))
    assert first.status_code==200,first.text
    assert first.json()["inserted"]==2
    assert api.get("/correlations",headers=AUTH).json()["total"]==1
    assert api.post("/exchange/import",headers=remote,json=package.model_dump(mode="json")).json()["inserted"]==0
    incomplete=package.model_copy(update={"objects":[artifact]})
    assert api.post("/exchange/import",headers=remote,json=incomplete.model_dump(mode="json")).status_code==409


def test_oversized_input_is_rejected(client):
    api,_,_=client
    response=api.post("/objects",headers={**AUTH,"Content-Type":"application/json"},content=b" "*(2*1024*1024+1))
    assert response.status_code==413
