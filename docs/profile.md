# Profile 1.0

Profile name: `media-intelligence`. JSON Schema: `schemas/package.schema.json` or
`GET /schema/package`. Unknown fields, object types and profile versions fail validation.
JSON timestamps require a timezone and are stored in UTC. IDs are
`Type:organization:canonical-lowercase-uuid`. Organization names match `[a-z][a-z0-9-]{0,47}`.
The namespace is exactly the organization ID; it is not a trust proof.

## Types

| Type | Specific fields | Change rule |
|---|---|---|
| HashArtifact | algorithm, value, calculated_at, algorithm_version?, encoding | Immutable |
| MetadataArtifact | metadata_type, key, value, reliability | Immutable |
| KeywordArtifact | value, source | Immutable |
| HostingReference | resource, observed_at | Immutable |
| MediaInstance | media_type | Versioned |
| Case | name | Versioned |
| Series | name, description | Versioned |
| Infrastructure | channel, name, external_id? | Versioned |
| Marking | distribution, recipients, communities, allow_redistribution | Immutable |
| Relation | relation_type, source, target, status, reason? | Versioned |

Every publication has id, type, created_by and created_at. Everything except Marking has
marking_id. Versioned types add version, previous_version and modified_at.
The concrete enum values and length bounds are defined once in `models.py` and in the schema.
No person or organization is a domain object. No original files, account credentials or local
file paths belong in observations. Review free text and resource references before release.

Relations:
- `describes`: any technical artifact → MediaInstance; a resource must identify concrete media.
- `belongs-to`: MediaInstance → Case.
- `instance-of`: MediaInstance → Series.
- `refers-to`: HostingReference → Infrastructure.
- `related-to`: two different Cases; symmetric query behavior, stored provenance retained.

References contain `id` and optional `version`. Artifact and Marking references never contain
versions. Stable identities resolve to the current allowed context; explicit versions do not
fall back to another version. Relation endpoint identities and type cannot change.

## Creation

`POST /objects` accepts `{ "type": "Case", "data": {"name": "Synthetic case"} }`.
An optional top-level `marking_id` overrides the internal default. Server-managed fields
cannot be supplied in data. Names are administrative labels, not personal identifiers.

Convenience routes accept only the type-specific fields plus optional marking_id:
`POST /artifacts/hashes`, `/artifacts/metadata`, `/artifacts/keywords`,
`/artifacts/hosting-references`, `/media-instances`, `/cases`, `/series`,
`/infrastructures`, `/markings`, `/relations`.

```json
{
  "relation_type": "describes",
  "source": {"id": "HashArtifact:org-a:00000000-0000-4000-8000-000000000001"},
  "target": {"id": "MediaInstance:org-a:00000000-0000-4000-8000-000000000002"}
}
```

IDs above are illustrative; use returned IDs. Endpoints and marking must exist.

`POST /artifacts/batch` accepts optional marking_id and 1–500 `items` in the generic
creation shape. Items may override the batch marking. Only technical artifacts are accepted.
The whole batch succeeds or fails. A structural publication error reports the item index,
not the submitted sensitive value.

`Idempotency-Key` (1–128 characters) makes a creation/batch retry return the original result.
Reuse with a different request fails. Omit it to intentionally record independent observations.
Keys are scoped to the endpoint and retained until administrative database cleanup.

## Versions and queries

`POST /objects/{id}/versions`:
```json
{"expected_version": 1, "changes": {"name": "Corrected administrative label"}}
```

Only the original local organization creates versions. Concurrent/stale predecessors fail.
No PATCH or DELETE mutates published data. A wrong relation is deactivated by a new version
with `status: inactive`; a different endpoint needs a new relation identity.
A Marking never changes. A versioned object can reference another marking in a new version.
An immutable artifact requires a new publication to use another marking; this is not revocation.

- `GET /objects`: type, created_by, algorithm and exact value filters; offset/limit.
- `GET /objects/{id}`: current allowed publication.
- `GET /objects/{id}/versions`: allowed history, offset/limit.
- `GET /relations`: source_id, target_id, include_inactive, offset/limit.
- `GET /correlations`: offset/limit; distinct observation pairs and allowed context paths.

All authorized local access has the same organization-wide view. There are no users or
case-specific permissions. Remote exchange credentials cannot read these routes.
Absent and unavailable objects both return 404. Inactive relations remain inspectable but
are not used for current context navigation.

## Comparison rules (version 1)

- MD5, SHA-1, SHA-256, SHA-512: exact algorithm + lowercase hex; fixed lengths 32/40/64/128.
- Robust `pdq`, `photodna`, `other-robust`: preserved representation, explicit version and
  encoding; stored and exchanged, never compared. Listed as unsupported, not as non-matches.
- Metadata: same metadata_type, key, profile-defined value type and value. created_at values
  compare in UTC. duration_seconds treats int and float as the same numeric category.
- Keywords: exact source + text. No case folding, trimming or semantic similarity.
- Resources: exact string, never fetched, normalized or dereferenced.

Metadata allowlist: width, height, file_size (nonnegative integers); duration_seconds
(nonnegative number); mime_type, camera_model, software (bounded strings);
created_at (timezone-bearing timestamp string). Reliability is unknown, unchecked or checked.

Hits report both IDs, rule ID/version and exact-value match type. They never merge media,
create Series membership or create related-to statements. Equal values are not equal records.
