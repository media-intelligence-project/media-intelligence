from datetime import timedelta
import pytest
from pydantic import ValidationError
from media_intelligence.models import *
from media_intelligence.core import *


def make(kind, organization="org-a", marking=None, **data):
    return create(CreateRequest(type=kind, marking_id=marking, data=data), organization, default_marking(organization).id)


def shared(org="org-a", recipient="org-b"):
    return make("Marking", org, distribution="community", recipients=[recipient])


def hash_object(org="org-a", marking=None, **changes):
    data = dict(algorithm="sha256", value="a"*64, calculated_at=now())
    data.update(changes)
    return make("HashArtifact", org, marking, **data)


def test_defaults_are_internal_and_stable():
    assert default_marking("org-a").id == default_marking("org-a").id
    assert not allows(default_marking("org-a"), "org-b", {})


def test_normalized_hash_and_time():
    obj = hash_object(value="A"*64)
    assert obj.value == "a"*64
    assert obj.calculated_at.tzinfo is not None


@pytest.mark.parametrize("changes", [
    {"value": "short"}, {"value": "z"*64}, {"calculated_at": "2026-01-01T00:00:00"},
    {"surprise": True}, {"encoding": "base64"}, {"value": 42},
])
def test_invalid_hash(changes):
    with pytest.raises((ValidationError, Rejected)):
        hash_object(**changes)


def test_identity_is_validated():
    obj = hash_object().model_dump()
    obj["created_by"] = "org-b"
    with pytest.raises(ValidationError):
        parse(obj)


def test_unknown_type_is_rejected():
    with pytest.raises(ValidationError):
        parse({"type": "Actor"})


def test_robust_is_stored_but_never_compared():
    m = default_marking("org-a")
    a = hash_object(algorithm="pdq", value="opaque", algorithm_version="1", encoding="opaque")
    b = hash_object(algorithm="pdq", value="opaque", algorithm_version="1", encoding="opaque")
    assert comparison_key(a) is None
    assert list(correlations([m, a, b], "org-a", {})) == []


def test_robust_version_required():
    with pytest.raises(ValidationError):
        hash_object(algorithm="pdq", value="opaque", encoding="opaque")


def test_repeated_import_is_idempotent():
    m = shared()
    a = hash_object(marking=m.id)
    package = export_package([m,a], ExportRequest(recipient="org-b", ids=[a.id]), "org-a", {"org-b"}, {})
    b = hash_object("org-b")
    base = [default_marking("org-b"), b]
    imported, count = import_package(base, package, "org-a", "org-b", {"org-a"}, {})
    assert count == 2
    again, count = import_package(imported, package, "org-a", "org-b", {"org-a"}, {})
    assert count == 0 and len(again) == 4
    hits = list(correlations(again, "org-b", {}))
    assert len(hits) == 1
    assert {hits[0]["left_id"], hits[0]["right_id"]} == {a.id, b.id}


def test_package_round_trip():
    m = shared()
    package = build_package(sender="org-a", recipient="org-b", objects=[m])
    assert Package.model_validate_json(package.model_dump_json()) == package


@pytest.mark.parametrize("sender,recipient,verified", [
    ("org-a", "org-c", "org-a"), ("org-a", "org-b", "org-c"), ("org-c", "org-b", "org-a")
])
def test_wrong_exchange_identity(sender, recipient, verified):
    package = build_package(sender=sender, recipient=recipient, objects=[shared()])
    with pytest.raises(Rejected):
        import_package([], package, verified, "org-b", {"org-a"}, {})


def test_third_party_transit_rejected():
    package = build_package(sender="org-a", recipient="org-b", objects=[shared("org-c")])
    with pytest.raises(Rejected):
        import_package([], package, "org-a", "org-b", {"org-a"}, {})


def test_missing_marking_rejected_even_if_receiver_has_it():
    m = shared()
    a = hash_object(marking=m.id)
    package = build_package(sender="org-a", recipient="org-b", objects=[a])
    with pytest.raises(Rejected):
        import_package([m], package, "org-a", "org-b", {"org-a"}, {})


def test_conflict_does_not_overwrite():
    m = default_marking("org-a")
    a = hash_object()
    b = parse({**a.model_dump(), "value": "b"*64})
    existing = [m,a]
    with pytest.raises(Rejected):
        merge(existing, [b])
    assert existing[-1].value == "a"*64


def test_equal_values_are_independent_observations():
    m = default_marking("org-a")
    a,b = hash_object(),hash_object()
    merged, count = merge([m,a], [b])
    assert count == 1 and len(list(correlations(merged,"org-a",{}))) == 1


def test_different_algorithms_never_match():
    a = hash_object(algorithm="md5",value="a"*32)
    b = hash_object(algorithm="sha256",value="a"*64)
    assert list(correlations([default_marking("org-a"),a,b],"org-a",{})) == []


def test_no_self_correlation():
    a = hash_object()
    assert list(correlations([default_marking("org-a"),a],"org-a",{})) == []


def test_internal_export_rejected():
    m = default_marking("org-a")
    a = hash_object()
    with pytest.raises(Rejected):
        export_package([m,a], ExportRequest(recipient="org-b",ids=[a.id]),"org-a",{"org-b"},{})


def test_context_version_and_predecessors():
    m=shared()
    case=make("Case",marking=m.id,name="C1")
    version=new_version([m,case],case.id,VersionRequest(expected_version=1,changes={"name":"C2"}),"org-a")
    merge([m,case],[version])
    package=export_package([m,case,version],ExportRequest(recipient="org-b",ids=[case.id]),"org-a",{"org-b"},{})
    assert len(package.objects)==3
    with pytest.raises(Rejected):
        validate_set([m,version])


def test_foreign_and_stale_versions_rejected():
    m=default_marking("org-a")
    case=make("Case",name="C1")
    for org,expected in [("org-b",1),("org-a",2)]:
        with pytest.raises(Rejected):
            new_version([m,case],case.id,VersionRequest(expected_version=expected,changes={"name":"C2"}),org)


def test_artifact_cannot_be_versioned():
    a=hash_object()
    with pytest.raises(Rejected):
        new_version([a],a.id,VersionRequest(expected_version=1,changes={}),"org-a")


def test_modified_time_cannot_move_backwards():
    m=default_marking("org-a")
    case=make("Case",name="C")
    data=case.model_dump()
    data.update(version=2,previous_version=1,modified_at=case.created_at-timedelta(seconds=1))
    with pytest.raises(ValidationError):parse(data)


def graph():
    m=shared()
    h=hash_object(marking=m.id)
    media=make("MediaInstance",marking=m.id,media_type="image")
    case=make("Case",marking=m.id,name="Synthetic case")
    describes=make("Relation",marking=m.id,relation_type="describes",source={"id":h.id},target={"id":media.id})
    belongs=make("Relation",marking=m.id,relation_type="belongs-to",source={"id":media.id},target={"id":case.id})
    return [m,h,media,case,describes,belongs]


def test_dependency_closure():
    objects=graph()
    package=export_package(objects,ExportRequest(recipient="org-b",ids=[objects[-1].id,objects[-2].id]),"org-a",{"org-b"},{})
    assert len(package.objects)==6
    assert len(validate_set(list(reversed(package.objects))))==6


def test_context_navigation_and_deactivation():
    objects=graph()
    media,case,edge=objects[2],objects[3],objects[-1]
    assert any(x.get("context_id")==case.id for x in context_paths(objects[1].id,visible(objects,"org-a",{})))
    inactive=new_version(objects,edge.id,VersionRequest(expected_version=1,changes={"status":"inactive"}),"org-a")
    merged,_=merge(objects,[inactive])
    assert not any("context_id" in x for x in context_paths(objects[1].id,visible(merged,"org-a",{})))


def test_endpoint_identity_is_immutable():
    objects=graph()
    with pytest.raises(Rejected):
        new_version(objects,objects[-1].id,VersionRequest(expected_version=1,changes={"target":{"id":objects[3].id}}),"org-a")


def test_relation_type_constraints():
    objects=graph()
    with pytest.raises(ValidationError):
        make("Relation",relation_type="belongs-to",source={"id":objects[1].id},target={"id":objects[3].id})


def test_hidden_context_does_not_hide_shared_observation():
    objects=graph()
    internal=default_marking("org-a")
    case=objects[3]
    changed=parse({**case.model_dump(),"marking_id":internal.id})
    objects[3]=changed
    objects.append(internal)
    view=visible(objects,"org-b",{})
    assert objects[1].id in view and case.id not in view and objects[-2].id not in view
    assert not any("context_id" in x for x in context_paths(objects[1].id,view))


def test_latest_hidden_version_does_not_fall_back():
    m=shared(); internal=default_marking("org-a")
    case=make("Case",marking=m.id,name="C")
    changed=new_version([m,case,internal],case.id,VersionRequest(expected_version=1,changes={"marking_id":internal.id}),"org-a")
    assert case.id not in visible([m,internal,case,changed],"org-b",{})


@pytest.mark.parametrize("kind,data",[
    ("MetadataArtifact",{"metadata_type":"image","key":"width","value":640,"reliability":"checked"}),
    ("KeywordArtifact",{"value":"synthetic.png","source":"filename"}),
    ("HostingReference",{"resource":"https://example.invalid/synthetic.png","observed_at":"2026-01-01T00:00:00Z"}),
])
def test_other_artifacts_compare_exactly(kind,data):
    a,b=make(kind,**data),make(kind,**data)
    assert comparison_key(a)==comparison_key(b)
    assert len(list(correlations([default_marking("org-a"),a,b],"org-a",{})))==1


@pytest.mark.parametrize("key,value",[("local_path","/tmp/x"),("width",True),("height","640"),("created_at","2026-01-01")])
def test_metadata_allowlist_and_types(key,value):
    with pytest.raises(ValidationError):
        make("MetadataArtifact",metadata_type="file",key=key,value=value,reliability="unchecked")


def test_timestamp_metadata_normalization():
    a=make("MetadataArtifact",metadata_type="image",key="created_at",value="2026-01-01T01:00:00+01:00",reliability="unknown")
    b=make("MetadataArtifact",metadata_type="image",key="created_at",value="2026-01-01T00:00:00Z",reliability="checked")
    assert comparison_key(a)==comparison_key(b)


def test_communities_do_not_expand_transitively():
    m=make("Marking",distribution="connected_communities",communities=["group-one"])
    assert allows(m,"org-b",{"group-one":["org-a","org-b"]})
    assert not allows(m,"org-c",{"group-two":["org-b","org-c"]})


def test_markings_cannot_be_recursively_protected():
    with pytest.raises(Rejected):
        make("Marking",marking="ignored",distribution="internal")


def test_strict_version_numbers():
    case=make("Case",name="C").model_dump()
    case["version"]="1"
    with pytest.raises(ValidationError):parse(case)


def test_batch_only_accepts_artifacts():
    with pytest.raises(ValidationError):
        BatchRequest(items=[CreateRequest(type="Case",data={"name":"C"})])


def test_generated_package_schema_validates_example():
    jsonschema=pytest.importorskip("jsonschema")
    package=build_package(sender="org-a",recipient="org-b",objects=[shared()])
    jsonschema.Draft202012Validator(Package.model_json_schema()).validate(package.model_dump(mode="json"))


def test_numeric_timestamps_are_not_silently_coerced():
    with pytest.raises(ValidationError):
        hash_object(calculated_at=0)


def test_package_metadata_is_required():
    with pytest.raises(ValidationError):
        Package.model_validate({"sender":"org-a","recipient":"org-b","objects":[shared().model_dump(mode="json")]})


@pytest.mark.parametrize("encoding,value",[("hex","not-hex"),("base64","!invalid!")])
def test_robust_encoding_is_validated_without_comparison(encoding,value):
    with pytest.raises(ValidationError):
        hash_object(algorithm="other-robust",algorithm_version="1",encoding=encoding,value=value)
