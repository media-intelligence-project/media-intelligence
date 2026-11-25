"""The complete wire profile. No database or HTTP dependencies."""
from datetime import datetime, timezone
import re
from typing import Annotated, Literal, Union
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, field_validator, model_validator

PROFILE = "media-intelligence"
SCHEMA_VERSION = "1.0"
Org = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,47}$")]
Text = Annotated[str, Field(min_length=1, max_length=1024)]
KINDS = {"HashArtifact", "MetadataArtifact", "KeywordArtifact", "HostingReference", "MediaInstance", "Case", "Series", "Infrastructure", "Marking", "Relation"}
ARTIFACTS = {"HashArtifact", "MetadataArtifact", "KeywordArtifact", "HostingReference"}
CONTEXTS = {"MediaInstance", "Case", "Series", "Infrastructure"}
VERSIONED = CONTEXTS | {"Relation"}


def now() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timezone is required")
    return value.astimezone(timezone.utc)


def identifier(kind: str, organization: str) -> str:
    return f"{kind}:{organization}:{uuid4()}"


def parse_identifier(value: str) -> tuple[str, str]:
    parts = value.split(":")
    if len(parts) != 3 or parts[0] not in KINDS or not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", parts[1]):
        raise ValueError("Invalid object identifier")
    if str(UUID(parts[2])) != parts[2]:
        raise ValueError("UUID must use canonical lowercase form")
    return parts[0], parts[1]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True, allow_inf_nan=False)

    @field_validator("created_at", "modified_at", "calculated_at", "observed_at", mode="before", check_fields=False)
    @classmethod
    def timestamp_representation(cls, value, info):
        if info.field_name in {"created_at", "modified_at", "calculated_at", "observed_at"}:
            if not isinstance(value, (str, datetime)):
                raise ValueError("Timestamps must be timezone-bearing strings, not epoch numbers")
        return value

    @field_validator("created_at", "modified_at", "calculated_at", "observed_at", check_fields=False)
    @classmethod
    def utc_timestamps(cls, value):
        return aware(value)


class Publication(Model):
    id: str
    type: str
    created_by: Org
    created_at: datetime

    @model_validator(mode="after")
    def identity(self):
        kind, organization = parse_identifier(self.id)
        if kind != self.type or organization != self.created_by:
            raise ValueError("Identifier type and namespace must match the publication")
        return self


class Marking(Publication):
    type: Literal["Marking"] = "Marking"
    distribution: Literal["internal", "community", "connected_communities", "global"]
    recipients: list[Org] = Field(default_factory=list, max_length=100)
    communities: list[Org] = Field(default_factory=list, max_length=100)
    allow_redistribution: StrictBool = False

    @model_validator(mode="after")
    def recipient_rule(self):
        self.recipients = sorted(set(self.recipients))
        self.communities = sorted(set(self.communities))
        if self.distribution in {"internal", "global"} and (self.recipients or self.communities):
            raise ValueError("Internal/global markings cannot contain recipient restrictions")
        if self.distribution in {"community", "connected_communities"} and not (self.recipients or self.communities):
            raise ValueError("Community markings need explicit recipients or communities")
        return self


class Object(Publication):
    marking_id: str

    @field_validator("marking_id")
    @classmethod
    def marking_reference(cls, value):
        if parse_identifier(value)[0] != "Marking":
            raise ValueError("Expected a Marking identifier")
        return value


class HashArtifact(Object):
    type: Literal["HashArtifact"] = "HashArtifact"
    algorithm: Literal["md5", "sha1", "sha256", "sha512", "pdq", "photodna", "other-robust"]
    value: Annotated[str, Field(min_length=1, max_length=8192)]
    calculated_at: datetime
    algorithm_version: Text | None = None
    encoding: Literal["hex", "base64", "opaque"] = "hex"


    @model_validator(mode="after")
    def hash_format(self):
        lengths = {"md5": 32, "sha1": 40, "sha256": 64, "sha512": 128}
        if self.algorithm in lengths:
            if self.encoding != "hex" or not re.fullmatch(r"[a-fA-F0-9]{%d}" % lengths[self.algorithm], self.value):
                raise ValueError("Invalid exact hash representation")
            self.value = self.value.lower()
        else:
            if not self.algorithm_version:
                raise ValueError("Robust representations require an explicit algorithm version")
            if self.encoding == "hex" and (len(self.value) % 2 or not re.fullmatch(r"[a-fA-F0-9]+", self.value)):
                raise ValueError("Invalid hexadecimal representation")
            if self.encoding == "base64":
                import base64
                try:
                    base64.b64decode(self.value, validate=True)
                except (ValueError, base64.binascii.Error):
                    raise ValueError("Invalid base64 representation") from None
        return self


class MetadataArtifact(Object):
    type: Literal["MetadataArtifact"] = "MetadataArtifact"
    metadata_type: Literal["image", "video", "file"]
    key: Literal["width", "height", "duration_seconds", "file_size", "mime_type", "camera_model", "software", "created_at"]
    value: StrictStr | StrictInt | StrictFloat | StrictBool
    reliability: Literal["unknown", "unchecked", "checked"]

    @model_validator(mode="after")
    def metadata_value(self):
        if self.key in {"width", "height", "file_size"}:
            if type(self.value) is not int or self.value < 0:
                raise ValueError("Expected a nonnegative integer")
        elif self.key == "duration_seconds":
            if type(self.value) not in {int, float} or self.value < 0:
                raise ValueError("Expected a nonnegative number")
        elif not isinstance(self.value, str) or not 1 <= len(self.value) <= 1024:
            raise ValueError("Expected bounded text")
        if self.key == "created_at":
            value = datetime.fromisoformat(self.value.replace("Z", "+00:00"))
            aware(value)
        return self


class KeywordArtifact(Object):
    type: Literal["KeywordArtifact"] = "KeywordArtifact"
    value: Text
    source: Literal["filename", "tag", "description"]


class HostingReference(Object):
    type: Literal["HostingReference"] = "HostingReference"
    resource: Annotated[str, Field(min_length=1, max_length=4096)]
    observed_at: datetime


class Versioned(Object):
    version: Annotated[int, Field(strict=True, ge=1)] = 1
    previous_version: Annotated[int, Field(strict=True, ge=1)] | None = None
    modified_at: datetime

    @model_validator(mode="after")
    def version_sequence(self):
        expected = None if self.version == 1 else self.version - 1
        if self.previous_version != expected or self.modified_at < self.created_at:
            raise ValueError("Invalid predecessor or modification time")
        return self


class MediaInstance(Versioned):
    type: Literal["MediaInstance"] = "MediaInstance"
    media_type: Literal["image", "video", "unknown"]


class Case(Versioned):
    type: Literal["Case"] = "Case"
    name: Text


class Series(Versioned):
    type: Literal["Series"] = "Series"
    name: Text
    description: Text


class Infrastructure(Versioned):
    type: Literal["Infrastructure"] = "Infrastructure"
    channel: Literal["p2p", "darknet", "clearweb", "device", "social", "messaging", "other"]
    name: Text
    external_id: Text | None = None


class Reference(Model):
    id: str
    version: Annotated[int, Field(strict=True, ge=1)] | None = None

    @field_validator("id")
    @classmethod
    def valid_id(cls, value):
        parse_identifier(value)
        return value


class Relation(Versioned):
    type: Literal["Relation"] = "Relation"
    relation_type: Literal["describes", "belongs-to", "instance-of", "refers-to", "related-to"]
    source: Reference
    target: Reference
    status: Literal["active", "inactive"] = "active"
    reason: Text | None = None

    @model_validator(mode="after")
    def endpoint_types(self):
        a, b = parse_identifier(self.source.id)[0], parse_identifier(self.target.id)[0]
        allowed = {
            "describes": a in ARTIFACTS and b == "MediaInstance",
            "belongs-to": a == "MediaInstance" and b == "Case",
            "instance-of": a == "MediaInstance" and b == "Series",
            "refers-to": a == "HostingReference" and b == "Infrastructure",
            "related-to": a == b == "Case" and self.source.id != self.target.id,
        }
        if not allowed[self.relation_type]:
            raise ValueError("Invalid relation endpoints")
        return self


PublicationType = Annotated[Union[Marking, HashArtifact, MetadataArtifact, KeywordArtifact, HostingReference, MediaInstance, Case, Series, Infrastructure, Relation], Field(discriminator="type")]


class Package(Model):
    profile: Literal["media-intelligence"]
    schema_version: Literal["1.0"]
    package_id: UUID
    created_at: datetime
    sender: Org
    recipient: Org
    objects: list[PublicationType] = Field(min_length=1, max_length=2000)


class CreateRequest(Model):
    """Convenience input; full type-specific validation happens before persistence."""
    type: Literal["HashArtifact", "MetadataArtifact", "KeywordArtifact", "HostingReference", "MediaInstance", "Case", "Series", "Infrastructure", "Marking", "Relation"]
    marking_id: str | None = None
    data: dict = Field(default_factory=dict)


class BatchRequest(Model):
    marking_id: str | None = None
    items: list[CreateRequest] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def artifacts_only(self):
        if any(item.type not in ARTIFACTS for item in self.items):
            raise ValueError("Batch creation only accepts technical artifacts")
        return self


class VersionRequest(Model):
    expected_version: Annotated[int, Field(strict=True, ge=1)]
    changes: dict


class ExportRequest(Model):
    recipient: Org
    ids: list[str] = Field(min_length=1, max_length=500)


def build_package(**fields):
    return Package(profile=PROFILE, schema_version=SCHEMA_VERSION, package_id=uuid4(), created_at=now(), **fields)
