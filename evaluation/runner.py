import argparse
import asyncio
import json
import os
import sys
from typing import List

# Setup sys.path to resolve project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pydantic import TypeAdapter, ValidationError
from evaluation.scripts.models import AIFinding
from evaluation.scripts.mock_llm import MOCK_RESPONSES

# Lazy imports for live LLM mode
ai_client_factory = None
settings = None

def init_live_client():
    global ai_client_factory, settings
    try:
        from apps.ai_reviewer.clients.ai_client import create_ai_client_from_settings
        from infra.config import settings
        ai_client_factory = create_ai_client_from_settings
    except ImportError as e:
        print(f"[-] Could not import AIClient: {e}")
        raise e

async def call_live_llm(diff_content: str, file_context: str) -> List[dict]:
    if ai_client_factory is None:
        init_live_client()
        
    ai_client = ai_client_factory()
    
    system_prompt = (
        "You are an expert AI code reviewer. Review the following Git Diff and return a JSON list of findings. "
        "Each finding must strictly match the following schema:\n"
        "[\n"
        "  {\n"
        "    \"file\": \"string (relative file path)\",\n"
        "    \"line_start\": int (1-indexed start line of the issue),\n"
        "    \"line_end\": int (1-indexed end line of the issue),\n"
        "    \"category\": \"security | logic | performance | maintainability | bug-risk\",\n"
        "    \"message\": \"string (clear explanation of the issue)\"\n"
        "  }\n"
        "]\n"
        "Ensure all line numbers refer to the head commit (the new/modified code after applying the diff).\n"
        "Return an empty list `[]` if no issues are found. Do not include style or formatting issues."
    )
    
    prompt = f"### Git Diff:\n```diff\n{diff_content}\n```\n\n### File Context:\n{file_context}"
    
    print(f"    Sending request to {ai_client.provider}:{ai_client.model}...")
    response_dict = await ai_client.complete_json(prompt=prompt, system_prompt=system_prompt)
    
    if isinstance(response_dict, list):
        return response_dict
    elif isinstance(response_dict, dict) and "findings" in response_dict:
        return response_dict["findings"]
    else:
        # Some models wrap list under a top-level key or string
        raise ValueError(f"Unexpected response structure: {response_dict}")

def load_file_context(sample_dir: str) -> str:
    files_dir = os.path.join(sample_dir, "input", "files")
    context = []
    if os.path.exists(files_dir):
        for root, _, filenames in os.walk(files_dir):
            for fname in filenames:
                fpath = os.path.join(root, fname)
                relpath = os.path.relpath(fpath, files_dir)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        content = f.read()
                    context.append(f"--- File: {relpath} (Head Commit State) ---\n{content}\n")
                except Exception as e:
                    print(f"Warning: Could not read file context {fpath}: {e}")
    return "\n".join(context)

async def process_sample(sample_dir: str, mock_mode: bool):
    sample_id = os.path.basename(sample_dir)
    print(f"\n[{sample_id}] Processing...")
    
    diff_path = os.path.join(sample_dir, "input", "diff.patch")
    if not os.path.exists(diff_path):
        print(f"[-] Diff not found at {diff_path}")
        return
        
    with open(diff_path, "r", encoding="utf-8") as f:
        diff_content = f.read()
        
    file_context = load_file_context(sample_dir)
    
    findings_data = []
    if mock_mode:
        print(f"    Mode: Mock LLM")
        findings_data = MOCK_RESPONSES.get(sample_id, [])
    else:
        if settings is None:
            init_live_client()
        print(f"    Mode: Live LLM ({settings.ai_provider}:{settings.ai_model})")
        try:
            findings_data = await call_live_llm(diff_content, file_context)
        except Exception as e:
            print(f"[-] Live LLM call failed for {sample_id}: {e}")
            return
            
    # Validate findings match schema
    try:
        findings_adapter = TypeAdapter(List[AIFinding])
        validated_findings = findings_adapter.validate_python(findings_data)
        print(f"[+] {sample_id}: Validated {len(validated_findings)} AI findings.")
    except ValidationError as e:
        print(f"[-] {sample_id}: Validation of AI output failed:\n{e}")
        return
        
    # Save output to evaluation/results/
    base_dir = os.path.dirname(os.path.abspath(__file__))
    results_dir = os.path.join(base_dir, "results")
    os.makedirs(results_dir, exist_ok=True)
    
    output_path = os.path.join(results_dir, f"{sample_id}.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(findings_data, f, indent=2)
    print(f"[+] Saved results to {output_path}")

async def main():
    parser = argparse.ArgumentParser(description="AI Code Review Evaluation Runner")
    parser.add_argument("--mock", action="store_true", help="Run in Mock LLM mode")
    parser.add_argument("--sample", type=str, help="Specific sample ID to run (e.g. sample_001)")
    args = parser.parse_args()
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    datasets_dir = os.path.join(base_dir, "datasets")
    
    samples_to_run = []
    if args.sample:
        sample_path = os.path.join(datasets_dir, args.sample)
        if os.path.exists(sample_path) and os.path.isdir(sample_path):
            samples_to_run.append(sample_path)
        else:
            print(f"Error: Sample {args.sample} not found.")
            sys.exit(1)
    else:
        # Load all samples
        for folder in sorted(os.listdir(datasets_dir)):
            sample_path = os.path.join(datasets_dir, folder)
            if os.path.isdir(sample_path) and folder.startswith("sample_"):
                samples_to_run.append(sample_path)
                
    if not samples_to_run:
        print("No samples found to evaluate.")
        sys.exit(1)
        
    print(f"Starting runner with {len(samples_to_run)} samples. Mock mode: {args.mock}")
    for sample in samples_to_run:
        await process_sample(sample, args.mock)

if __name__ == "__main__":
    asyncio.run(main())
