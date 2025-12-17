#!/usr/bin/env python3
"""
Basic Usage Examples for CodeLingerK Step 1

This file demonstrates how to use the core components of CodeLingerK
to extract and analyze code changes.
"""

import sys
from pathlib import Path

# Add parent directory to path to import core
sys.path.insert(0, str(Path(__file__).parent.parent))

from core import ChangeExtractor, setup_logging

def example_1_simple_extraction():
    """Example 1: Extract staged changes from current repository"""
    print("\n" + "="*80)
    print("Example 1: Simple Extraction")
    print("="*80)

    # Initialize extractor
    extractor = ChangeExtractor(".")

    # Extract staged changes
    changes = extractor.extract_changes(mode="staged")

    # Print results
    print(f"\nFound {len(changes)} change units:")
    for i, change in enumerate(changes, 1):
        print(f"\n{i}. {change.file_path}")
        print(f"   Type: {change.change_type}")
        if change.new_symbol:
            print(f"   Symbol: {change.new_symbol.name} ({change.new_symbol.type})")

def example_2_get_summary():
    """Example 2: Get summary statistics"""
    print("\n" + "="*80)
    print("Example 2: Summary Statistics")
    print("="*80)

    extractor = ChangeExtractor(".")
    changes = extractor.extract_changes(mode="staged")

    # Get summary
    summary = extractor.get_summary(changes)

    print(f"\nTotal Changes: {summary['total_changes']}")
    print(f"Files Affected: {summary['num_files']}")
    print("\nBy Change Type:")
    for change_type, count in summary['by_type'].items():
        if count > 0:
            print(f"  {change_type}: {count}")

    print("\nBy Symbol Type:")
    for symbol_type, count in summary['by_symbol_type'].items():
        print(f"  {symbol_type}: {count}")

def example_3_detect_modifications():
    """Example 3: Detect modified functions"""
    print("\n" + "="*80)
    print("Example 3: Detect Modified Functions")
    print("="*80)

    extractor = ChangeExtractor(".")
    changes = extractor.extract_changes(mode="staged")

    # Find modified functions
    modified_functions = [
        c for c in changes
        if c.change_type == "modified" and
        c.new_symbol and c.new_symbol.type == "Function"
    ]

    print(f"\nFound {len(modified_functions)} modified functions:")
    for change in modified_functions:
        old_hash = change.old_symbol.body_hash[:8] if change.old_symbol else "N/A"
        new_hash = change.new_symbol.body_hash[:8] if change.new_symbol else "N/A"

        print(f"\nFunction: {change.new_symbol.name}")
        print(f"  File: {change.file_path}")
        print(f"  Old hash: {old_hash}")
        print(f"  New hash: {new_hash}")
        print(f"  Lines: {change.new_symbol.line_start}-{change.new_symbol.line_end}")

def example_4_compare_branches():
    """Example 4: Compare two branches"""
    print("\n" + "="*80)
    print("Example 4: Compare Branches")
    print("="*80)

    try:
        extractor = ChangeExtractor(".")

        # Compare main branch with current HEAD
        changes = extractor.extract_changes(
            mode="branch",
            base_branch="main",
            compare_branch="HEAD"
        )

        print(f"\nChanges between main and HEAD: {len(changes)}")

        # Group by file
        files = {}
        for change in changes:
            if change.file_path not in files:
                files[change.file_path] = []
            files[change.file_path].append(change)

        for file_path, file_changes in files.items():
            print(f"\n{file_path}:")
            for change in file_changes:
                symbol_name = (
                    change.new_symbol.name if change.new_symbol
                    else change.old_symbol.name if change.old_symbol
                    else "unknown"
                )
                print(f"  [{change.change_type}] {symbol_name}")

    except Exception as e:
        print(f"Error: {e}")
        print("(This is normal if you don't have a 'main' branch)")

def example_5_analyze_commit():
    """Example 5: Analyze a specific commit"""
    print("\n" + "="*80)
    print("Example 5: Analyze Specific Commit")
    print("="*80)

    try:
        extractor = ChangeExtractor(".")

        # Get latest commit SHA
        commit_sha = extractor.git_manager.repo.head.commit.hexsha
        print(f"\nAnalyzing commit: {commit_sha[:8]}")

        changes = extractor.extract_changes(
            mode="commit",
            commit_sha=commit_sha
        )

        print(f"Changes in this commit: {len(changes)}")

        for change in changes:
            symbol = change.new_symbol or change.old_symbol
            if symbol:
                print(f"\n[{change.change_type}] {symbol.name}")
                print(f"  Type: {symbol.type}")
                print(f"  File: {change.file_path}")

    except Exception as e:
        print(f"Error: {e}")

def main():
    """Run all examples"""
    # Setup logging
    setup_logging(level="WARNING")  # Quiet mode for examples

    print("\n" + "="*80)
    print("CodeLingerK - Basic Usage Examples")
    print("="*80)

    try:
        example_1_simple_extraction()
        example_2_get_summary()
        example_3_detect_modifications()
        example_4_compare_branches()
        example_5_analyze_commit()

        print("\n" + "="*80)
        print("All examples completed!")
        print("="*80 + "\n")

    except Exception as e:
        print(f"\nError running examples: {e}")
        print("\nMake sure you have some staged changes in your Git repository.")
        print("Try running: git add <some-file>")

if __name__ == "__main__":
    main()
