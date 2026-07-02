import json
import os
import sys
from typing import Dict, List, Tuple

# Severity weights for Severity-Weighted Recall
SEVERITY_WEIGHTS = {
    "critical": 4.0,
    "high": 3.0,
    "medium": 2.0,
    "low": 1.0
}

def load_json(filepath: str):
    if not os.path.exists(filepath):
        return []
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)

def get_match_score(expected: dict, actual: dict) -> float:
    # 1. File must match
    if expected["file"] != actual["file"]:
        return 0.0
    # 2. Category must match
    if expected["category"] != actual["category"]:
        return 0.0
        
    # Check lines overlap
    e_start, e_end = expected["line_start"], expected["line_end"]
    a_start, a_end = actual["line_start"], actual["line_end"]
    
    overlap = max(e_start, a_start) <= min(e_end, a_end)
    if not overlap:
        return 0.0
        
    # Check if exact match
    if e_start == a_start and e_end == a_end:
        return 1.0
    
    return 0.5

def score_sample(expected_list: List[dict], actual_list: List[dict]) -> Tuple[float, List[dict], List[dict], List[dict]]:
    """
    Perform greedy bipartite matching between expected and actual findings.
    Returns: (tp_score, matched_pairs, unmatched_expected, unmatched_actual)
    """
    candidates = []
    for e_idx, e in enumerate(expected_list):
        for a_idx, a in enumerate(actual_list):
            score = get_match_score(e, a)
            if score > 0:
                candidates.append((score, e_idx, a_idx))
                
    # Sort candidates by score descending
    candidates.sort(key=lambda x: x[0], reverse=True)
    
    matched_expected = set()
    matched_actual = set()
    matched_pairs = []
    
    tp_score = 0.0
    
    for score, e_idx, a_idx in candidates:
        if e_idx not in matched_expected and a_idx not in matched_actual:
            matched_expected.add(e_idx)
            matched_actual.add(a_idx)
            tp_score += score
            matched_pairs.append({
                "expected": expected_list[e_idx],
                "actual": actual_list[a_idx],
                "score": score
            })
            
    # Unmatched
    unmatched_expected = [expected_list[i] for i in range(len(expected_list)) if i not in matched_expected]
    unmatched_actual = [actual_list[i] for i in range(len(actual_list)) if i not in matched_actual]
    
    return tp_score, matched_pairs, unmatched_expected, unmatched_actual

def calculate_metrics(tp: float, total_expected: float, total_actual: float) -> Tuple[float, float, float]:
    precision = (tp / total_actual * 100) if total_actual > 0 else 0.0
    recall = (tp / total_expected * 100) if total_expected > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    return precision, recall, f1

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    datasets_dir = os.path.join(base_dir, "datasets")
    results_dir = os.path.join(base_dir, "results")
    
    if not os.path.exists(results_dir):
        print(f"Error: Results directory '{results_dir}' does not exist. Run the runner first.")
        sys.exit(1)
        
    all_tp = 0.0
    all_expected_count = 0.0
    all_actual_count = 0.0
    
    # Severity weighted scoring metrics
    weighted_tp = 0.0
    weighted_total = 0.0
    
    # Category metrics accumulator
    # Struct: { category_name: { "tp": float, "expected": float, "actual": float } }
    categories = ["security", "logic", "performance", "maintainability", "bug-risk"]
    cat_stats = {cat: {"tp": 0.0, "expected": 0.0, "actual": 0.0} for cat in categories}
    
    top_misses = []
    top_false_positives = []
    sample_reports = []
    
    # Iterate through all dataset directories
    for folder in sorted(os.listdir(datasets_dir)):
        sample_path = os.path.join(datasets_dir, folder)
        if os.path.isdir(sample_path) and folder.startswith("sample_"):
            # Load metadata
            metadata = load_json(os.path.join(sample_path, "input", "metadata.json"))
            # Load expected (ground truth)
            expected = load_json(os.path.join(sample_path, "expected", "findings.json"))
            # Load actual (AI outputs)
            actual = load_json(os.path.join(results_dir, f"{folder}.json"))
            
            # Score this sample
            tp, matches, unmatched_exp, unmatched_act = score_sample(expected, actual)
            
            # Accumulate global stats
            all_tp += tp
            all_expected_count += len(expected)
            all_actual_count += len(actual)
            
            # Process matches for weighted score and category metrics
            for match in matches:
                score = match["score"]
                exp_finding = match["expected"]
                cat = exp_finding["category"]
                sev = exp_finding["severity"]
                
                # Severity weighted
                weight = SEVERITY_WEIGHTS.get(sev, 1.0)
                weighted_tp += score * weight
                weighted_total += weight
                
                # Category stats
                if cat in cat_stats:
                    cat_stats[cat]["tp"] += score
                    cat_stats[cat]["expected"] += score
                    cat_stats[cat]["actual"] += score
                
                # If partial match, the remaining 0.5 portion is distributed to FP and FN
                if score == 0.5:
                    # Expected portion unmatched (FN)
                    if cat in cat_stats:
                        cat_stats[cat]["expected"] += 0.5
                    # Actual portion unmatched (FP)
                    act_finding = match["actual"]
                    act_cat = act_finding["category"]
                    if act_cat in cat_stats:
                        cat_stats[act_cat]["actual"] += 0.5
                        
            # Accumulate fully unmatched expected (FN)
            for fn in unmatched_exp:
                cat = fn["category"]
                sev = fn["severity"]
                weight = SEVERITY_WEIGHTS.get(sev, 1.0)
                weighted_total += weight  # counts in total but not TP
                if cat in cat_stats:
                    cat_stats[cat]["expected"] += 1.0
                top_misses.append((folder, fn))
                
            # Accumulate fully unmatched actual (FP)
            for fp in unmatched_act:
                cat = fp["category"]
                if cat in cat_stats:
                    cat_stats[cat]["actual"] += 1.0
                top_false_positives.append((folder, fp))
                
            # Calculate sample metrics
            precision, recall, f1 = calculate_metrics(tp, len(expected), len(actual))
            
            sample_reports.append({
                "id": folder,
                "language": metadata.get("language", "unknown"),
                "expected": len(expected),
                "actual": len(actual),
                "tp": tp,
                "precision": precision,
                "recall": recall,
                "f1": f1
            })
            
    # Calculate global metrics
    global_precision, global_recall, global_f1 = calculate_metrics(all_tp, all_expected_count, all_actual_count)
    global_weighted_recall = (weighted_tp / weighted_total * 100) if weighted_total > 0 else 0.0
    
    # Build Dashboard Markdown Output
    print("\n" + "="*50)
    print("           AI CODE REVIEW EVALUATION DASHBOARD")
    print("="*50)
    print(f"Total Samples evaluated: {len(sample_reports)}")
    print(f"Global Precision:        {global_precision:.1f}%")
    print(f"Global Recall:           {global_recall:.1f}%")
    print(f"Global F1 Score:         {global_f1:.1f}%")
    print(f"Severity-Weighted Recall: {global_weighted_recall:.1f}%")
    print("="*50)
    
    # Print Category Table
    print("\n### Category Breakdown")
    print("| Category | Expected Bugs | AI Findings | Matched (TP) | Precision | Recall | F1 Score |")
    print("| :--- | :---: | :---: | :---: | :---: | :---: | :---: |")
    for cat in categories:
        stats = cat_stats[cat]
        cat_p, cat_r, cat_f = calculate_metrics(stats["tp"], stats["expected"], stats["actual"])
        print(f"| {cat} | {stats['expected']:.1f} | {stats['actual']:.1f} | {stats['tp']:.1f} | {cat_p:.1f}% | {cat_r:.1f}% | {cat_f:.1f}% |")
        
    # Print Sample Table
    print("\n### Sample Breakdown")
    print("| Sample ID | Language | Expected | AI Found | Matched (TP) | Precision | Recall | F1 |")
    print("| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |")
    for s in sample_reports:
        print(f"| {s['id']} | {s['language']} | {s['expected']} | {s['actual']} | {s['tp']:.1f} | {s['precision']:.1f}% | {s['recall']:.1f}% | {s['f1']:.1f}% |")
        
    # Print Top Misses
    print("\n### Top Misses (False Negatives)")
    if top_misses:
        for idx, (sample_id, fn) in enumerate(top_misses):
            print(f"{idx+1}. [{sample_id}] {fn['file']}:L{fn['line_start']}-{fn['line_end']} | Category: {fn['category']} | Severity: {fn['severity']}")
            print(f"   Message: {fn['message']}")
            print(f"   Evidence: {fn['evidence']}")
    else:
        print("None! Excellent recall.")
        
    # Print Top False Positives
    print("\n### Top False Positives (FPs)")
    if top_false_positives:
        for idx, (sample_id, fp) in enumerate(top_false_positives):
            print(f"{idx+1}. [{sample_id}] {fp['file']}:L{fp['line_start']}-{fp['line_end']} | Category: {fp['category']}")
            print(f"   Message: {fp['message']}")
    else:
        print("None! Excellent precision.")
    print("\n" + "="*50)

if __name__ == "__main__":
    main()
