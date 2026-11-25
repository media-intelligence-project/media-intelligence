"""Small, static organization directory; no user or role management."""
import json
import os
from pathlib import Path
from pydantic import Field, field_validator, model_validator
from .models import Model, Org


class Participant(Model):
    token_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    accept: bool = True


class Settings(Model):
    organization: Org
    local_token: str = Field(min_length=32)
    database_url: str
    participants: dict[str, Participant] = Field(default_factory=dict)
    communities: dict[str, list[Org]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def directory(self):
        from pydantic import TypeAdapter
        adapter = TypeAdapter(Org)
        for key in [*self.participants, *self.communities]:
            adapter.validate_python(key)
        if self.organization in self.participants:
            raise ValueError("Do not register this organization as a remote participant")
        hashes = [x.token_sha256 for x in self.participants.values()]
        if len(hashes) != len(set(hashes)):
            raise ValueError("Each participant needs a different incoming token")
        import hashlib
        if hashlib.sha256(self.local_token.encode()).hexdigest() in hashes:
            raise ValueError("Local and remote credentials must differ")
        return self


def load_settings():
    directory = json.loads(Path(os.environ.get("DIRECTORY_FILE", "/config/directory.json")).read_text())
    return Settings(organization=os.environ["ORGANIZATION_ID"], local_token=os.environ["LOCAL_API_TOKEN"],
                    database_url=os.environ["DATABASE_URL"], **directory)
