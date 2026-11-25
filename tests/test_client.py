import json
from unittest.mock import patch

from media_intelligence.client import MediaIntelligenceClient


def test_create_object_sends_authenticated_idempotent_request_and_decodes_response():
    data = {"algorithm": "sha256", "value": "a" * 64, "calculated_at": "2026-01-01T00:00:00Z"}
    created = {"id": "artifact-1", "type": "HashArtifact", **data}

    with patch("media_intelligence.client.HTTPSConnection", autospec=True) as transport:
        connection = transport.return_value
        response = connection.getresponse.return_value
        response.status = 201
        response.read.return_value = json.dumps(created).encode()

        client = MediaIntelligenceClient("https://media.example/api/", token="test-token")
        result = client.create_object(
            "HashArtifact", data, marking_id="marking-1", idempotency_key="submission-1"
        )

    connection.request.assert_called_once()
    args, kwargs = connection.request.call_args
    assert args == ("POST", "/api/objects")
    assert kwargs["headers"]["Authorization"] == "Bearer test-token"
    assert kwargs["headers"]["Idempotency-Key"] == "submission-1"
    assert kwargs["headers"]["Content-Type"] == "application/json"
    assert json.loads(kwargs["body"]) == {
        "type": "HashArtifact", "data": data, "marking_id": "marking-1"
    }
    assert result == created
