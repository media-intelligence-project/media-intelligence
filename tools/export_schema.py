"""Regenerate the versioned package schema from the Pydantic profile."""
import json
from pathlib import Path
from media_intelligence.models import Package

path = Path(__file__).resolve().parents[1] / "schemas/package.schema.json"
path.parent.mkdir(exist_ok=True)
path.write_text(json.dumps(Package.model_json_schema(), indent=2) + "\n")
