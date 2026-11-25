import hashlib
import pytest
from pydantic import ValidationError
from media_intelligence.config import Settings


def test_no_self_participant():
    with pytest.raises(ValidationError):
        Settings(organization="org-a",local_token="a"*32,database_url="unused",participants={"org-a":{"token_sha256":"b"*64}})


def test_do_not_reuse_local_credential():
    with pytest.raises(ValidationError):
        Settings(organization="org-a",local_token="a"*32,database_url="unused",participants={"org-b":{"token_sha256":hashlib.sha256(b"a"*32).hexdigest()}})


def test_unique_remote_credentials():
    with pytest.raises(ValidationError):
        Settings(organization="org-a",local_token="a"*32,database_url="unused",participants={x:{"token_sha256":"b"*64} for x in ["org-b","org-c"]})


def test_participant_names_match_identifier_namespaces():
    with pytest.raises(ValidationError):
        Settings(organization="org-a",local_token="a"*32,database_url="unused",participants={"NOT A NAMESPACE":{"token_sha256":"b"*64}})
