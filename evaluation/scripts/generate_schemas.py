import json
import os
import sys
from typing import List
from pydantic import RootModel

# Add current folder to sys.path to import models
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from models import SampleMetadata, GroundTruthFinding, AIFinding

def save_schema(schema_data: dict, filepath: str):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w") as f:
        json.dump(schema_data, f, indent=2)
    print(f"Generated schema at: {filepath}")

def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    schema_dir = os.path.join(base_dir, "schemas")

    # 1. Metadata Schema
    metadata_schema = SampleMetadata.model_json_schema()
    metadata_schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    save_schema(metadata_schema, os.path.join(schema_dir, "metadata.schema.json"))

    # 2. Findings Schema (Root is array of GroundTruthFinding)
    findings_root = RootModel[List[GroundTruthFinding]]
    findings_schema = findings_root.model_json_schema()
    findings_schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    save_schema(findings_schema, os.path.join(schema_dir, "findings.schema.json"))

    # 3. AI Output Schema (Root is array of AIFinding)
    ai_output_root = RootModel[List[AIFinding]]
    ai_output_schema = ai_output_root.model_json_schema()
    ai_output_schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    save_schema(ai_output_schema, os.path.join(schema_dir, "ai_output.schema.json"))

if __name__ == "__main__":
    main()
