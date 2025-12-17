#!/usr/bin/env python3
"""
CodeLingerK - AI-Powered Code Review System
Main entry point for Step 1: Code Ingestion & Parsing

This demo shows how to extract and structure code changes from a Git repository.
"""

import argparse
import json
import sys
from pathlib import Path

from core.logging_config import setup_logging, get_logger
from core.change_extractor import ChangeExtractor

logger = get_logger(__name__)

def print_change_unit(unit, index: int):
    """Pretty print a ChangeUnit"""
    print(f"\n{'='*80}")
    print(f"Change Unit #{index + 1}")
    print(f"{'='*80}")
    print(f"File: {unit.file_path}")
    print(f"Change Type: {unit.change_type.upper()}")

    if unit.old_symbol:
        print(f"\n--- OLD SYMBOL ---")
        print(f"Name: {unit.old_symbol.name}")
        print(f"Type: {unit.old_symbol.type}")
        print(f"Lines: {unit.old_symbol.line_start}-{unit.old_symbol.line_end}")
        print(f"Hash: {unit.old_symbol.body_hash[:12]}...")

    if unit.new_symbol:
        print(f"\n--- NEW SYMBOL ---")
        print(f"Name: {unit.new_symbol.name}")
        print(f"Type: {unit.new_symbol.type}")
        print(f"Lines: {unit.new_symbol.line_start}-{unit.new_symbol.line_end}")
        print(f"Hash: {unit.new_symbol.body_hash[:12]}...")

    print(f"\n--- DIFF HUNK ---")
    # Show only first 10 lines of diff
    hunk_lines = unit.diff_hunk.split('\n')[:10]
    print('\n'.join(hunk_lines))
    if len(unit.diff_hunk.split('\n')) > 10:
        print("... (truncated)")

def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="CodeLingerK - Extract and structure code changes (Step 1 Demo)"
    )

    parser.add_argument(
        "repo_path",
        nargs="?",
        default=".",
        help="Path to Git repository (default: current directory)"
    )

    parser.add_argument(
        "--mode",
        choices=["staged", "unstaged", "all", "commit", "branch"],
        default="staged",
        help="Type of changes to extract (default: staged)"
    )

    parser.add_argument(
        "--commit",
        help="Commit SHA (required if mode=commit)"
    )

    parser.add_argument(
        "--base",
        help="Base branch name (required if mode=branch)"
    )

    parser.add_argument(
        "--compare",
        default="HEAD",
        help="Compare branch name (default: HEAD, used with mode=branch)"
    )

    parser.add_argument(
        "--output",
        "-o",
        help="Output JSON file path (optional)"
    )

    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging"
    )

    parser.add_argument(
        "--log-file",
        help="Log file path (optional)"
    )

    args = parser.parse_args()

    # Setup logging
    log_level = "DEBUG" if args.verbose else "INFO"
    setup_logging(level=log_level, log_file=args.log_file)

    logger.info("=" * 80)
    logger.info("CodeLingerK - Step 1: Code Ingestion & Parsing")
    logger.info("=" * 80)

    try:
        # Initialize extractor
        repo_path = Path(args.repo_path).resolve()
        logger.info(f"Repository: {repo_path}")

        extractor = ChangeExtractor(str(repo_path))

        # Extract changes
        logger.info(f"Extraction mode: {args.mode}")

        change_units = extractor.extract_changes(
            mode=args.mode,
            commit_sha=args.commit,
            base_branch=args.base,
            compare_branch=args.compare
        )

        # Print summary
        summary = extractor.get_summary(change_units)
        print(f"\n{'='*80}")
        print("EXTRACTION SUMMARY")
        print(f"{'='*80}")
        print(f"Total Changes: {summary['total_changes']}")
        print(f"Files Affected: {summary['num_files']}")
        print(f"\nBy Change Type:")
        for change_type, count in summary['by_type'].items():
            if count > 0:
                print(f"  {change_type.capitalize()}: {count}")

        print(f"\nBy Symbol Type:")
        for symbol_type, count in summary['by_symbol_type'].items():
            print(f"  {symbol_type}: {count}")

        print(f"\nAffected Files:")
        for file_path in summary['files_affected']:
            print(f"  - {file_path}")

        # Print detailed change units
        if change_units:
            print(f"\n{'='*80}")
            print("DETAILED CHANGE UNITS")
            print(f"{'='*80}")

            for i, unit in enumerate(change_units):
                print_change_unit(unit, i)

        # Export to JSON if requested
        if args.output:
            output_data = {
                "summary": summary,
                "changes": [unit.model_dump() for unit in change_units]
            }

            output_path = Path(args.output)
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(output_data, f, indent=2, ensure_ascii=False)

            logger.info(f"\nResults exported to: {output_path}")

        logger.info("\n" + "=" * 80)
        logger.info("Extraction completed successfully!")
        logger.info("=" * 80)

        return 0

    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        return 1

if __name__ == "__main__":
    sys.exit(main())
