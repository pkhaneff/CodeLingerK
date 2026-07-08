def render_comment_explanation(
    category: str,
    severity: str,
    title: str,
    problem: str,
    impact: str,
    suggestion: str,
    code_suggestion: str | None = None
) -> str:
    """Pre-render inline comment markdown if new schema is used."""
    severity_icons = {
        'critical': '🔴',
        'major': '🟠',
        'minor': '🟡',
        'info': '🔵',
        'warning': '🟠',
        'error': '🔴'
    }
    icon = severity_icons.get(severity.lower() if severity else 'info', '🔵')

    parts = [
        f"**[{category}] {icon} {title}**",
        "",
        problem,
        "",
        "**Why this matters**",
        "",
        impact,
        "",
        "**Suggested fix**",
        "",
        suggestion
    ]

    if code_suggestion:
        parts.extend([
            "",
            "```suggestion",
            code_suggestion.strip(),
            "```"
        ])

    return "\n".join(parts)
