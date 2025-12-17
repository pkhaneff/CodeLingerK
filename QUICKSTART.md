# CodeLingerK - Quick Start Guide

Hướng dẫn nhanh để bắt đầu sử dụng CodeLingerK trong 5 phút.

## Bước 1: Cài đặt Dependencies

```bash
pip install -r requirements.txt
```

## Bước 2: Chạy Demo

### Option A: CLI Demo

```bash
# Xem những gì đang staged
python main.py

# Xem tất cả thay đổi (staged + unstaged)
python main.py --mode all

# Verbose mode với chi tiết
python main.py -v

# Export ra JSON
python main.py --output results.json
```

### Option B: Python API

```python
from core import ChangeExtractor

# Khởi tạo
extractor = ChangeExtractor(".")

# Lấy thay đổi
changes = extractor.extract_changes(mode="staged")

# In ra
for change in changes:
    print(f"{change.change_type}: {change.file_path}")
    if change.new_symbol:
        print(f"  → {change.new_symbol.name} ({change.new_symbol.type})")
```

### Option C: Chạy Examples

```bash
python examples/basic_usage.py
```

## Bước 3: Hiểu Output

### ChangeUnit Structure

```
ChangeUnit {
    file_path: "src/example.py"
    change_type: "modified"        # added/modified/deleted

    old_symbol: {                  # Version cũ
        name: "calculate"
        type: "Function"
        line_start: 10
        line_end: 15
        body_hash: "abc123..."
    }

    new_symbol: {                  # Version mới
        name: "calculate"
        type: "Function"
        line_start: 10
        line_end: 20              # Dài hơn!
        body_hash: "def456..."    # Hash khác!
    }

    diff_hunk: "@@ -10,5 +10,10 @@\n..."
}
```

## Bước 4: Các Use Cases Phổ biến

### Use Case 1: Tìm tất cả functions bị modified

```python
from core import ChangeExtractor

extractor = ChangeExtractor(".")
changes = extractor.extract_changes(mode="staged")

modified_funcs = [
    c for c in changes
    if c.change_type == "modified" and
       c.new_symbol and c.new_symbol.type == "Function"
]

for change in modified_funcs:
    print(f"Modified: {change.new_symbol.name} in {change.file_path}")
```

### Use Case 2: So sánh hai branches

```python
changes = extractor.extract_changes(
    mode="branch",
    base_branch="main",
    compare_branch="feature-xyz"
)

print(f"Total changes: {len(changes)}")
```

### Use Case 3: Phân tích một commit

```python
changes = extractor.extract_changes(
    mode="commit",
    commit_sha="abc123"
)

summary = extractor.get_summary(changes)
print(f"Files changed: {summary['num_files']}")
print(f"Functions modified: {summary['by_symbol_type'].get('Function', 0)}")
```

### Use Case 4: Export data để process tiếp

```python
import json

changes = extractor.extract_changes(mode="all")

# Convert to dict
data = {
    "summary": extractor.get_summary(changes),
    "changes": [c.model_dump() for c in changes]
}

# Save
with open("analysis.json", "w") as f:
    json.dump(data, f, indent=2)
```

## Bước 5: Chạy Tests

```bash
# Tất cả tests
pytest tests/ -v

# Một test cụ thể
pytest tests/test_diff_parser.py::TestDiffParser::test_parse_simple_diff -v

# Với coverage
pytest tests/ --cov=core --cov-report=term-missing
```

## Các Modes Hỗ trợ

| Mode | Mô tả | Example |
|------|-------|---------|
| `staged` | Staged changes (git add) | `python main.py --mode staged` |
| `unstaged` | Working directory changes | `python main.py --mode unstaged` |
| `all` | Staged + Unstaged | `python main.py --mode all` |
| `commit` | Một commit cụ thể | `python main.py --mode commit --commit abc123` |
| `branch` | So sánh branches | `python main.py --mode branch --base main` |

## CLI Options

```bash
python main.py [OPTIONS] [REPO_PATH]

Options:
  --mode {staged,unstaged,all,commit,branch}
  --commit SHA              # Cho mode=commit
  --base BRANCH            # Cho mode=branch
  --compare BRANCH         # Cho mode=branch (default: HEAD)
  --output, -o FILE        # Export JSON
  --verbose, -v            # Debug logging
  --log-file FILE          # Log to file
```

## Troubleshooting

### Lỗi: "Not a git repository"
```bash
# Đảm bảo bạn ở trong Git repo
git status

# Hoặc chỉ định đường dẫn cụ thể
python main.py /path/to/repo
```

### Lỗi: "No changes found"
```bash
# Tạo một số thay đổi
git add <file>

# Hoặc dùng mode khác
python main.py --mode unstaged
python main.py --mode all
```

### Lỗi: Import errors
```bash
# Reinstall dependencies
pip install -r requirements.txt

# Hoặc install cụ thể
pip install gitpython tree-sitter tree-sitter-python pydantic
```

### Performance chậm với repo lớn?
```bash
# Chỉ analyze staged changes thay vì all
python main.py --mode staged

# Hoặc analyze một commit cụ thể
python main.py --mode commit --commit HEAD
```

## Next Steps

1. Đọc [README.md](README.md) để hiểu toàn bộ tính năng
2. Xem [ARCHITECTURE.md](ARCHITECTURE.md) để hiểu cách hoạt động
3. Explore [examples/basic_usage.py](examples/basic_usage.py) cho more examples
4. Đóng góp tính năng mới!

## Tips & Tricks

### Tip 1: Combine với jq để filter JSON
```bash
python main.py --output results.json
cat results.json | jq '.changes[] | select(.change_type == "modified")'
```

### Tip 2: Sử dụng trong CI/CD
```bash
# Check nếu có critical functions bị modify
python main.py --mode staged --output changes.json
python -c "
import json
with open('changes.json') as f:
    data = json.load(f)
    critical = [c for c in data['changes'] if 'auth' in c['file_path']]
    if critical:
        print('WARNING: Critical files modified!')
        exit(1)
"
```

### Tip 3: Watch mode (với entr hoặc watchdog)
```bash
# Auto-run khi có file thay đổi
ls **/*.py | entr python main.py
```

## Liên hệ & Hỗ trợ

- GitHub Issues: [Link đến repo]
- Documentation: [README.md](README.md)
- Architecture: [ARCHITECTURE.md](ARCHITECTURE.md)
