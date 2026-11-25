# Package exchange

Register remote organizations in `config/directory.json` before sharing content.
Every instance belongs to exactly one organization. No incremental cursor, polling state,
automatic discovery, deletion propagation or delivery guarantee exists.

## Release a selection

Create a local Marking with `distribution: community`, explicit `recipients`, and
`allow_redistribution: false`. Assign it during creation. The immutable internal default
cannot later be edited into a sharing marking.

`POST /exchange/export`, using the local API token:
```json
{"recipient":"org-b","ids":["<returned-object-or-relation-id>"]}
```

The response is a complete JSON package. Save it to a file or send it directly.
Export includes markings, required endpoints, explicit versions and complete predecessor
chains. Every dependency must be exportable. If a selected root has a blocked dependency,
the entire export request fails; select a smaller independent subset instead. It never
silently includes unreleased dependencies. Historical versions with old internal markings
can therefore prevent export of later versions; this is intentional.

Exporting a hash alone does not export its incoming describes relation or its Case.
Selecting the relevant relations includes their endpoints. Only the sender's own
publications are exported; third-party transit is outside this profile implementation.

## Import

Direct authenticated transfer:
```sh
curl -s http://localhost:8001/exchange/import \
  -H 'Authorization: Bearer <incoming-token-issued-by-B-for-A>' \
  -H 'Content-Type: application/json' --data-binary @package.json
```

B's directory maps this token's SHA-256 digest to A. The token holder cannot select a
second sender with a header. `sender`, all object origins and identifier namespaces must
match A; recipient must be B. Use TLS or a trusted private tunnel between hosts.

Local file import by the trusted instance operator:
```sh
curl -s http://localhost:8001/exchange/import \
  -H 'Authorization: Bearer <B-local-token>' \
  -H 'X-Verified-Sender: org-a' \
  -H 'Content-Type: application/json' --data-binary @package.json
```

The header is an operator attestation, not a cryptographic signature. Verify source and
integrity over a trusted handover channel first. A filename or sender field is insufficient.
If this assumption is unsuitable, do not use local file import; use authenticated transfer.
The operator already has full organization authority; there is no separate uploader role.

## Rules

- Validate the entire package before writing any publications.
- Dependencies must be inside the package even if the receiver already has them.
- Exact repeated import keys are harmless; content differences are conflicts.
- Repeated packages can have new package IDs: deduplication uses publication ID/version.
- Roll back all publication changes on failure; no pending dependency queue.
- Keep the highest contiguous validated version; an old packet cannot reactivate an old edge.
- Missing content is not a deletion or withdrawal.
- Compute correlations locally, only from allowed and accepted publications.

Internal release is owner-only. Global release is for authenticated known participants,
not the public Internet. Community rules use explicit recipients and/or locally verified
memberships; unknown communities reject an import. Connected communities are not expanded
transitively. The issuer always retains access. Markings themselves have no recursive marking.

`allow_redistribution` records the policy, but third-party transit is not implemented even
when this flag is true. A receiver cannot relax original markings or force deletion at
another instance. An accepted sender can still publish incorrect information.
Set a participant's `accept` to false and restart to exclude its content from active reads
and reject future exchange; original stored publications remain unchanged.
