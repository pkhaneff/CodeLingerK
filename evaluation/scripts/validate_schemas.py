import json
import os
import sys
from typing import List
from pydantic import TypeAdapter, ValidationError

# Add current folder to sys.path to import models
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from models import SampleMetadata, GroundTruthFinding, AIFinding

def validate_sample(sample_dir: str):
    print(f"Validating sample directory: {sample_dir}")
    
    metadata_path = os.path.join(sample_dir, "input", "metadata.json")
    findings_path = os.path.join(sample_dir, "expected", "findings.json")

    # 1. Validate metadata.json
    if not os.path.exists(metadata_path):
        raise FileNotFoundError(f"metadata.json not found at {metadata_path}")
    
    with open(metadata_path, "r") as f:
        try:
            metadata_data = json.load(f)
        except json.JSONDecodeError as e:
            print(f"[-] Invalid JSON in {metadata_path}: {e}")
            raise e
            
    try:
        SampleMetadata.model_validate(metadata_data)
        print("[+] metadata.json is valid.")
    except ValidationError as e:
        print(f"[-] metadata.json validation failed:\n{e}")
        raise e

    # 2. Validate findings.json
    if not os.path.exists(findings_path):
        raise FileNotFoundError(f"findings.json not found at {findings_path}")
        
    with open(findings_path, "r") as f:
        try:
            findings_data = json.load(f)
        except json.JSONDecodeError as e:
            print(f"[-] Invalid JSON in {findings_path}: {e}")
            raise e
            
    try:
        findings_adapter = TypeAdapter(List[GroundTruthFinding])
        findings_adapter.validate_python(findings_data)
        print("[+] findings.json is valid.")
    except ValidationError as e:
        print(f"[-] findings.json validation failed:\n{e}")
        raise e

def run_self_tests():
    print("\nRunning self-tests for schema validation logic...")
    
    # Test 1: Invalid line range (line_start > line_end)
    invalid_finding = {
        "id": "BUG-002",
        "file": "auth.py",
        "line_start": 10,
        "line_end": 5,  # Invalid!
        "severity": "high",
        "category": "logic",
        "message": "Wrong line range",
        "evidence": ["line1", "line2"],
        "confidence": 1.0
    }
    try:
        TypeAdapter(GroundTruthFinding).validate_python(invalid_finding)
        print("[-] Self-test failed: Accepted invalid line range.")
        sys.exit(1)
    except ValidationError as e:
        print("[+] Self-test passed: Rejected invalid line range (line_start > line_end).")

    # Test 2: Invalid bug ID pattern
    invalid_id = {
        "id": "INVALID-ID", # Invalid!
        "file": "auth.py",
        "line_start": 5,
        "line_end": 10,
        "severity": "high",
        "category": "logic",
        "message": "Wrong id pattern",
        "evidence": ["line1"],
        "confidence": 1.0
    }
    try:
        TypeAdapter(GroundTruthFinding).validate_python(invalid_id)
        print("[-] Self-test failed: Accepted invalid bug ID pattern.")
        sys.exit(1)
    except ValidationError as e:
        print("[+] Self-test passed: Rejected invalid bug ID pattern.")

    # Test 3: Invalid category enum
    invalid_category = {
        "id": "BUG-003",
        "file": "auth.py",
        "line_start": 5,
        "line_end": 5,
        "severity": "high",
        "category": "invalid-category", # Invalid!
        "message": "Wrong category",
        "evidence": ["line1"],
        "confidence": 1.0
    }
    try:
        TypeAdapter(GroundTruthFinding).validate_python(invalid_category)
        print("[-] Self-test failed: Accepted invalid category.")
        sys.exit(1)
    except ValidationError as e:
        print("[+] Self-test passed: Rejected invalid category.")

def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    datasets_dir = os.path.join(base_dir, "datasets")
    
    # Validate each sample directory
    samples_found = 0
    for folder in sorted(os.listdir(datasets_dir)):
        sample_path = os.path.join(datasets_dir, folder)
        if os.path.isdir(sample_path) and folder.startswith("sample_"):
            validate_sample(sample_path)
            samples_found += 1
            
    if samples_found == 0:
        print("[-] No sample directories found in datasets/")
        sys.exit(1)
        
    print(f"\n[+] Successfully validated {samples_found} samples.")
    
    # Validate each result file in evaluation/results/
    results_dir = os.path.join(base_dir, "results")
    results_found = 0
    if os.path.exists(results_dir):
        print("\nValidating results directory...")
        for file in sorted(os.listdir(results_dir)):
            if file.endswith(".json"):
                result_path = os.path.join(results_dir, file)
                print(f"Validating result file: {result_path}")
                with open(result_path, "r", encoding="utf-8") as f:
                    try:
                        result_data = json.load(f)
                    except json.JSONDecodeError as e:
                        print(f"[-] Invalid JSON in {result_path}: {e}")
                        raise e
                try:
                    TypeAdapter(List[AIFinding]).validate_python(result_data)
                    print(f"[+] {file} is valid AI output.")
                    results_found += 1
                except ValidationError as e:
                    print(f"[-] {file} validation failed:\n{e}")
                    raise e
        print(f"[+] Successfully validated {results_found} AI results files.")
        
    # Run self-tests to ensure bad datasets can't slip through
    run_self_tests()
    print("[+] All schema validation checks passed successfully.")

if __name__ == "__main__":
    main()
