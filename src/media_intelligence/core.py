"""Pure application rules. Lists are a deliberate small-deployment trade-off."""
from collections import defaultdict
from datetime import datetime, timezone
from itertools import combinations
import json
from uuid import NAMESPACE_URL, uuid5

from pydantic import TypeAdapter

from .models import (
    ARTIFACTS, VERSIONED, CreateRequest, HashArtifact, Marking, MetadataArtifact,
    KeywordArtifact, HostingReference, Package, PublicationType, Relation,
    identifier, now, build_package,
)

ADAPTER = TypeAdapter(PublicationType)


class Rejected(ValueError):
    pass


def parse(data):
    return ADAPTER.validate_python(data)


def key(obj):
    return obj.id, getattr(obj, "version", 0)


def canonical(obj):
    return json.dumps(obj.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def default_marking(organization):
    return Marking(
        id=f"Marking:{organization}:{uuid5(NAMESPACE_URL, 'media-intelligence/internal/' + organization)}",
        created_by=organization, created_at=now(),
        distribution="internal",
    )


def create(request: CreateRequest, organization: str, fallback: str):
    data = dict(request.data)
    reserved = {"id", "type", "created_by", "created_at", "version", "previous_version", "modified_at", "marking_id"}
    if reserved & data.keys():
        raise Rejected("Creation data contains server-managed fields")
    timestamp = now()
    data.update(id=identifier(request.type, organization), type=request.type,
                created_by=organization, created_at=timestamp)
    if request.type != "Marking":
        data["marking_id"] = request.marking_id or fallback
    elif request.marking_id is not None:
        raise Rejected("A Marking cannot reference another Marking")
    if request.type in VERSIONED:
        data.update(version=1, previous_version=None, modified_at=timestamp)
    return parse(data)


def latest(objects):
    selected = {}
    for obj in objects:
        if obj.id not in selected or key(obj)[1] > key(selected[obj.id])[1]:
            selected[obj.id] = obj
    return selected


def allows(marking, organization, communities):
    if organization == marking.created_by:
        return True
    if marking.distribution == "internal":
        return False
    if marking.distribution == "global":
        return True
    members = {org for community in marking.communities for org in communities.get(community, [])}
    return organization in set(marking.recipients) | members


def available(obj, objects, organization, communities):
    if isinstance(obj, Marking):
        return allows(obj, organization, communities)
    marking = next((x for x in objects if x.id == obj.marking_id and isinstance(x, Marking)), None)
    return marking is not None and allows(marking, organization, communities)


def visible(objects, organization, communities):
    current = latest(objects)
    result = {i: obj for i, obj in current.items() if available(obj, objects, organization, communities)}
    # Relations never expose endpoints which are unavailable to the organization.
    for i, obj in list(result.items()):
        if isinstance(obj, Relation):
            for ref in (obj.source, obj.target):
                endpoint = result.get(ref.id)
                concrete = next((x for x in objects if key(x) == (ref.id, ref.version)), None) if ref.version else endpoint
                if endpoint is None or concrete is None or not available(concrete, objects, organization, communities):
                    result.pop(i, None)
                    break
    return result


def validate_set(objects):
    """Validate identity, version continuity, references and immutable fields."""
    by_key = {}
    groups = defaultdict(list)
    for obj in objects:
        previous = by_key.get(key(obj))
        if previous:
            if canonical(previous) != canonical(obj):
                raise Rejected("Conflicting publication under the same import key")
            continue
        by_key[key(obj)] = obj
        groups[obj.id].append(obj)
    current = latest(by_key.values())
    for obj in by_key.values():
        if not isinstance(obj, Marking):
            marking = current.get(obj.marking_id)
            if not isinstance(marking, Marking):
                raise Rejected("Required marking is missing")
        if obj.type in VERSIONED:
            chain = sorted(groups[obj.id], key=lambda x: x.version)
            if [x.version for x in chain] != list(range(1, chain[-1].version + 1)):
                raise Rejected("Required predecessor is missing")
            first = chain[0]
            for before, after in zip(chain, chain[1:]):
                if after.created_at != first.created_at or after.created_by != first.created_by or after.type != first.type:
                    raise Rejected("Version changes immutable identity properties")
                if after.modified_at < before.modified_at:
                    raise Rejected("Modification time moves backwards")
                if isinstance(after, Relation) and (
                    after.relation_type != first.relation_type or after.source.id != first.source.id or after.target.id != first.target.id
                ):
                    raise Rejected("Relation endpoint identities and type are immutable")
        if isinstance(obj, Relation):
            for ref in (obj.source, obj.target):
                endpoint = current.get(ref.id)
                if ref.version is not None:
                    endpoint = by_key.get((ref.id, ref.version))
                if endpoint is None:
                    raise Rejected("Required endpoint or endpoint version is missing")
                if ref.version is not None and endpoint.type not in VERSIONED:
                    raise Rejected("Immutable artifacts do not have version references")
    return list(by_key.values())


def merge(existing, incoming):
    combined = validate_set([*existing, *incoming])
    count = len(combined) - len({key(x) for x in existing})
    return combined, count


def new_version(objects, object_id, request, organization):
    current = latest(objects).get(object_id)
    if current is None or current.created_by != organization:
        raise Rejected("Object is not locally editable")
    if current.type not in VERSIONED:
        raise Rejected("This publication is immutable")
    if current.version != request.expected_version:
        raise Rejected("Expected predecessor is no longer current")
    immutable = {"id", "type", "created_by", "created_at", "version", "previous_version", "modified_at"}
    if isinstance(current, Relation):
        immutable |= {"source", "target", "relation_type"}
    if immutable & request.changes.keys():
        raise Rejected("Changes contain immutable fields")
    data = current.model_dump()
    data.update(request.changes)
    data.update(version=current.version + 1, previous_version=current.version, modified_at=now())
    return parse(data)


def export_package(objects, request, organization, participants, communities):
    if request.recipient not in participants or request.recipient == organization:
        raise Rejected("Select a known remote recipient")
    current = visible(objects, organization, communities)
    by_key = {key(x): x for x in objects}
    chosen = {}

    def include(obj):
        if key(obj) in chosen:
            return
        if obj.created_by != organization or not available(obj, objects, request.recipient, communities):
            raise Rejected("Selection has an unavailable dependency")
        chosen[key(obj)] = obj
        if not isinstance(obj, Marking):
            include(current[obj.marking_id])
        if obj.type in VERSIONED:
            for version in range(1, obj.version):
                include(by_key[(obj.id, version)])
        if isinstance(obj, Relation):
            for ref in (obj.source, obj.target):
                endpoint = by_key.get((ref.id, ref.version)) if ref.version else current.get(ref.id)
                if endpoint is None:
                    raise Rejected("Selection has an unavailable dependency")
                include(endpoint)

    for object_id in request.ids:
        obj = current.get(object_id)
        if obj is None:
            raise Rejected("Selection is unavailable")
        include(obj)
    ordered = sorted(chosen.values(), key=key)
    validate_set(ordered)
    return build_package(sender=organization, recipient=request.recipient, objects=ordered)


def import_package(existing, package, verified_sender, organization, participants, communities):
    if verified_sender not in participants or verified_sender == organization:
        raise Rejected("Unknown remote sender")
    if package.sender != verified_sender or package.recipient != organization:
        raise Rejected("Sender or recipient does not match the authenticated exchange")
    if any(obj.created_by != verified_sender for obj in package.objects):
        raise Rejected("Transit of third-party publications is not supported")
    validate_set(package.objects)
    if any(not available(obj, package.objects, organization, communities) for obj in package.objects):
        raise Rejected("Package contains content not released to this organization")
    return merge(existing, package.objects)


def comparison_key(obj):
    if isinstance(obj, HashArtifact):
        if obj.algorithm not in {"md5", "sha1", "sha256", "sha512"}:
            return None
        return ("exact-hash", obj.algorithm, obj.value)
    if isinstance(obj, MetadataArtifact):
        value = obj.value
        if obj.key == "created_at":
            value = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
        # Numeric metadata values have a profile-defined type.
        value_type = "number" if obj.key == "duration_seconds" else type(value).__name__
        return ("exact-metadata", obj.metadata_type, obj.key, value_type, value)
    if isinstance(obj, KeywordArtifact):
        return ("exact-keyword", obj.source, obj.value)
    if isinstance(obj, HostingReference):
        return ("exact-resource", obj.resource)
    return None


def context_paths(object_id, view):
    describes = [x for x in view.values() if isinstance(x, Relation) and x.status == "active" and x.relation_type == "describes" and x.source.id == object_id]
    paths = []
    for edge in describes:
        paths.append({"media_instance_id": edge.target.id, "relation_ids": [edge.id]})
        for membership in view.values():
            if isinstance(membership, Relation) and membership.status == "active" and membership.source.id == edge.target.id and membership.relation_type in {"belongs-to", "instance-of"}:
                paths.append({"media_instance_id": edge.target.id, "context_id": membership.target.id,
                              "relation_ids": [edge.id, membership.id]})
    return paths


def correlations(objects, organization, communities):
    view = visible(objects, organization, communities)
    buckets = defaultdict(list)
    for obj in view.values():
        match = comparison_key(obj)
        if match is not None:
            buckets[match].append(obj)
    for match in sorted(buckets, key=str):
        for left, right in combinations(sorted(buckets[match], key=lambda x: x.id), 2):
            yield {"left_id": left.id, "right_id": right.id, "rule": match[0], "rule_version": "1",
                   "match_type": "exact-value", "left_contexts": context_paths(left.id, view),
                   "right_contexts": context_paths(right.id, view)}
