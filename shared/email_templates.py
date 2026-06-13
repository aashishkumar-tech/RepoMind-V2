"""
shared/email_templates.py — Premium HTML email templates for RepoMind

DESIGN PRINCIPLES:
    • Linear / Vercel / GitHub notification aesthetic
    • Table-based layout (Outlook + Gmail safe)
    • Inline CSS only (Gmail strips <style>)
    • Bulletproof buttons (no JS, no background-image)
    • System font stack — no external font loads
    • Components: stat grids, code blocks, timelines, status badges
    • Plain-text alternative auto-generated for spam/accessibility

CONTEXT FIELDS (all optional except `repo`):
    Common:
        repo                  e.g. "user/repo"
        branch                e.g. "main"
        event_id              correlation ID for support
        timestamp             ISO-8601 string (auto-fills to "now" if missing)

    ci_failed extras:
        run_id, workflow_name, failed_step, commit_sha, commit_message,
        author, triggered_by, error_excerpt

    pr_review_needed extras:
        pr_number, pr_url, failure_type, confidence (0.0-1.0 or 0-100),
        playbook_id, files_changed (list), lines_added, lines_deleted,
        diff_preview, risk_level

    pr_merged extras:
        pr_number, pr_url, reviewer, merge_sha, time_to_merge,
        files_changed_count

    pr_rejected extras:
        pr_number, pr_url, reviewer, reason

    rollback extras:
        branch, reason, attempts

    pipeline_error extras:
        event_id, stage, error, error_type
"""

from datetime import datetime, timezone
from typing import List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────
# DESIGN TOKENS
# ─────────────────────────────────────────────────────────────────────────
COLORS = {
    "primary":     "#6366f1",   # Indigo
    "primary_dk":  "#4f46e5",
    "success":     "#10b981",   # Emerald
    "warning":     "#f59e0b",   # Amber
    "danger":      "#ef4444",   # Red
    "neutral":     "#6b7280",
    "bg":          "#f1f5f9",   # Slate-100
    "card":        "#ffffff",
    "subcard":     "#f8fafc",   # Slate-50
    "text":        "#0f172a",   # Slate-900
    "text2":       "#334155",   # Slate-700
    "muted":       "#64748b",   # Slate-500
    "border":      "#e2e8f0",   # Slate-200
    "border_dk":   "#cbd5e1",
    "code_bg":     "#0f172a",   # Slate-900 (dark mode code)
    "code_text":   "#e2e8f0",
    "code_accent": "#f87171",   # Red-400 for error highlight
}

EVENT_STYLE = {
    "ci_failed": {
        "icon": "🚨",
        "color": COLORS["danger"],
        "label": "CI Failure Detected",
        "tagline": "Auto-fix pipeline activated",
        "summary": (
            "We've detected a CI failure in your repository. The autonomous "
            "repair pipeline is now analyzing the issue and will propose a fix."
        ),
        "timeline_step": 1,
    },
    "pr_review_needed": {
        "icon": "🔔",
        "color": COLORS["primary"],
        "label": "Review Required",
        "tagline": "Pull request awaiting your decision",
        "summary": (
            "RepoMind has analyzed the failure, identified a fix, and opened "
            "a pull request. Approve to auto-merge, or reject to discard."
        ),
        "timeline_step": 3,
    },
    "pr_merged": {
        "icon": "✅",
        "color": COLORS["success"],
        "label": "Fix Successfully Merged",
        "tagline": "Your repository is back to green",
        "summary": (
            "The auto-fix pull request has been approved and merged. CI is "
            "now passing and your codebase is restored."
        ),
        "timeline_step": 5,
    },
    "pr_rejected": {
        "icon": "❌",
        "color": COLORS["neutral"],
        "label": "Pull Request Rejected",
        "tagline": "Fix discarded — branch cleaned up",
        "summary": (
            "The auto-fix was rejected. The PR has been closed, the working "
            "branch deleted, and an apology comment posted."
        ),
        "timeline_step": 5,
    },
    "rollback": {
        "icon": "⚠️",
        "color": COLORS["warning"],
        "label": "Rollback Triggered",
        "tagline": "Safety net activated",
        "summary": (
            "The proposed fix failed CI verification. RepoMind automatically "
            "rolled back changes to preserve the integrity of your branch."
        ),
        "timeline_step": 5,
    },
    "pipeline_error": {
        "icon": "💥",
        "color": COLORS["danger"],
        "label": "Pipeline Error",
        "tagline": "Unexpected issue requires attention",
        "summary": (
            "The pipeline encountered an unexpected error and could not "
            "complete. Reference the event ID below if filing a bug report."
        ),
        "timeline_step": -1,
    },
}

SUBJECTS = {
    "ci_failed":         "[RepoMind] CI failure detected in {repo}",
    "pr_review_needed":  "[RepoMind] PR #{pr_number} ready for your review",
    "pr_merged":         "[RepoMind] PR #{pr_number} merged into {repo}",
    "pr_rejected":       "[RepoMind] PR #{pr_number} closed in {repo}",
    "rollback":          "[RepoMind] Rollback completed for {repo}",
    "pipeline_error":    "[RepoMind] Pipeline error in {repo}",
}

TIMELINE_STAGES = [
    ("📡", "Detected"),
    ("🔎", "Analyzing"),
    ("🛠️", "Proposing"),
    ("👀", "Review"),
    ("🎯", "Resolved"),
]


# ─────────────────────────────────────────────────────────────────────────
# COMPONENT BUILDERS (return HTML strings)
# ─────────────────────────────────────────────────────────────────────────

def _stat_card(label: str, value: str, mono: bool = False) -> str:
    font_family = (
        "Menlo,Monaco,Consolas,monospace" if mono
        else "-apple-system,BlinkMacSystemFont,sans-serif"
    )
    return f"""
    <td valign="top" style="padding:0 6px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
             style="background:{COLORS['subcard']};border:1px solid {COLORS['border']};
                    border-radius:8px;">
        <tr><td style="padding:14px 16px;">
          <div style="font-size:10px;font-weight:700;letter-spacing:0.8px;
                      color:{COLORS['muted']};text-transform:uppercase;
                      margin-bottom:6px;">{label}</div>
          <div style="font-size:15px;font-weight:600;color:{COLORS['text']};
                      font-family:{font_family};line-height:1.3;
                      word-break:break-all;">{value}</div>
        </td></tr>
      </table>
    </td>"""


def _stat_grid(items: List[Tuple[str, str, bool]]) -> str:
    """items = [(label, value, is_mono), ...] — auto-wraps to rows of 2."""
    if not items:
        return ""
    rows_html = []
    # Render in rows of 2 for mobile-safety (looks great even on phones)
    for i in range(0, len(items), 2):
        pair = items[i:i + 2]
        cells = "".join(_stat_card(lbl, val, mono) for lbl, val, mono in pair)
        # If odd number, pad with empty cell
        if len(pair) == 1:
            cells += '<td style="padding:0 6px;"></td>'
        rows_html.append(f"<tr>{cells}</tr>")
        rows_html.append('<tr><td colspan="2" style="height:12px;"></td></tr>')

    inner = "".join(rows_html)
    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
           style="margin:0 -6px;">
      {inner}
    </table>"""


def _code_block(content: str, max_lines: int = 12,
                title: Optional[str] = None) -> str:
    if not content:
        return ""
    lines = content.strip().splitlines()
    truncated = len(lines) > max_lines
    if truncated:
        lines = lines[:max_lines]
    body = "\n".join(lines)
    # Escape HTML to prevent injection
    body = (body.replace("&", "&amp;")
                 .replace("<", "&lt;")
                 .replace(">", "&gt;"))
    suffix = (f'\n... ({len(content.splitlines()) - max_lines} more lines)'
              if truncated else "")
    title_html = ""
    if title:
        title_html = f"""
        <div style="background:#020617;color:{COLORS['muted']};
                    padding:8px 14px;font-size:11px;font-weight:600;
                    letter-spacing:0.5px;text-transform:uppercase;
                    font-family:Menlo,Consolas,monospace;
                    border-bottom:1px solid #1e293b;">
          {title}
        </div>"""
    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
           style="background:{COLORS['code_bg']};border-radius:8px;
                  overflow:hidden;margin:0;">
      {title_html}
      <tr><td style="padding:14px 16px;">
        <pre style="margin:0;padding:0;color:{COLORS['code_text']};
                    font-family:Menlo,Monaco,Consolas,'Courier New',monospace;
                    font-size:12px;line-height:1.55;
                    white-space:pre-wrap;word-break:break-word;">{body}{suffix}</pre>
      </td></tr>
    </table>"""


def _badge(text: str, color: str, light: bool = True) -> str:
    bg = f"{color}15" if light else color   # 15 = ~8% alpha hex
    fg = color if light else "#ffffff"
    return (
        f'<span style="display:inline-block;padding:3px 10px;'
        f'background:{bg};color:{fg};border-radius:999px;'
        f'font-size:11px;font-weight:600;letter-spacing:0.3px;'
        f'border:1px solid {color}30;">{text}</span>'
    )


def _confidence_bar(confidence) -> str:
    """confidence: float 0–1 OR int/str 0–100 → renders a bar."""
    try:
        v = float(confidence)
        if v <= 1.0:
            v *= 100
        v = max(0, min(100, v))
    except (ValueError, TypeError):
        v = 0
    if v >= 80:
        color, label = COLORS["success"], "High"
    elif v >= 60:
        color, label = COLORS["warning"], "Medium"
    else:
        color, label = COLORS["danger"], "Low"
    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td style="padding-bottom:6px;">
          <span style="font-size:13px;font-weight:600;color:{COLORS['text']};">
            {v:.0f}%
          </span>
          <span style="font-size:11px;color:{COLORS['muted']};margin-left:6px;">
            confidence ({label})
          </span>
        </td>
      </tr>
      <tr>
        <td style="background:{COLORS['border']};height:6px;border-radius:999px;
                   line-height:6px;font-size:0;">
          <table role="presentation" width="{v:.0f}%" cellpadding="0" cellspacing="0" border="0">
            <tr><td style="background:{color};height:6px;border-radius:999px;
                          line-height:6px;font-size:0;">&nbsp;</td></tr>
          </table>
        </td>
      </tr>
    </table>"""


def _timeline(current_step: int) -> str:
    """current_step: 1-based index (1=Detected, 5=Resolved); -1 hides timeline."""
    if current_step < 1:
        return ""
    cells = []
    for i, (icon, label) in enumerate(TIMELINE_STAGES, start=1):
        if i < current_step:
            dot_color, txt_color, ic = COLORS["success"], COLORS["text2"], "✓"
        elif i == current_step:
            dot_color, txt_color, ic = COLORS["primary"], COLORS["primary_dk"], icon
        else:
            dot_color, txt_color, ic = COLORS["border_dk"], COLORS["muted"], icon
        is_active = (i == current_step)
        dot_size = "28" if is_active else "22"
        font_weight = "700" if is_active else "500"
        cells.append(f"""
        <td align="center" valign="top" width="20%" style="padding:0;">
          <div style="display:inline-block;width:{dot_size}px;height:{dot_size}px;
                      line-height:{dot_size}px;background:{dot_color};
                      color:#ffffff;border-radius:999px;
                      font-size:12px;font-weight:700;text-align:center;
                      box-shadow:0 0 0 4px {dot_color}25;">{ic}</div>
          <div style="margin-top:6px;font-size:11px;font-weight:{font_weight};
                      color:{txt_color};letter-spacing:0.2px;">{label}</div>
        </td>""")
    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>{"".join(cells)}</tr>
    </table>"""


def _button(label: str, url: str, color: str = None) -> str:
    color = color or COLORS["primary"]
    return f"""
    <table role="presentation" cellpadding="0" cellspacing="0" border="0"
           style="margin:0 auto;">
      <tr><td style="border-radius:8px;background:{color};
                     box-shadow:0 2px 8px {color}40;">
        <a href="{url}" target="_blank"
           style="display:inline-block;padding:13px 28px;color:#ffffff;
                  text-decoration:none;font-weight:600;font-size:14px;
                  border-radius:8px;letter-spacing:0.2px;">
          {label}
        </a>
      </td></tr>
    </table>"""


def _section_title(text: str) -> str:
    return f"""
    <div style="font-size:11px;font-weight:700;letter-spacing:1px;
                color:{COLORS['muted']};text-transform:uppercase;
                margin:0 0 12px 0;">
      {text}
    </div>"""


def _chip(icon: str, text: str) -> str:
    return (
        f'<span style="display:inline-block;padding:4px 10px;margin-right:6px;'
        f'background:rgba(255,255,255,0.18);color:#ffffff;border-radius:6px;'
        f'font-size:12px;font-weight:500;">{icon} {text}</span>'
    )


# ─────────────────────────────────────────────────────────────────────────
# PER-EVENT CONTENT BUILDERS
# ─────────────────────────────────────────────────────────────────────────

def _g(ctx: dict, key: str, default: str = "") -> str:
    v = ctx.get(key)
    return "" if v in (None, "") else str(v)


def _content_ci_failed(ctx: dict) -> str:
    parts = []

    # Stat grid
    stats = []
    if _g(ctx, "commit_sha"):
        stats.append(("Commit", _g(ctx, "commit_sha")[:8], True))
    if _g(ctx, "author"):
        stats.append(("Author", _g(ctx, "author"), False))
    if _g(ctx, "run_id"):
        stats.append(("Run ID", _g(ctx, "run_id"), True))
    if _g(ctx, "workflow_name"):
        stats.append(("Workflow", _g(ctx, "workflow_name"), False))
    if _g(ctx, "failed_step"):
        stats.append(("Failed Step", _g(ctx, "failed_step"), False))
    if _g(ctx, "triggered_by"):
        stats.append(("Triggered By", _g(ctx, "triggered_by"), False))
    if stats:
        parts.append(_section_title("Run Details"))
        parts.append(_stat_grid(stats))
        parts.append('<div style="height:24px;"></div>')

    # Commit message
    if _g(ctx, "commit_message"):
        parts.append(_section_title("Commit Message"))
        parts.append(f"""
        <div style="background:{COLORS['subcard']};border-left:3px solid {COLORS['primary']};
                    padding:12px 16px;border-radius:4px;font-size:13px;
                    color:{COLORS['text2']};line-height:1.5;font-style:italic;">
          "{_g(ctx, 'commit_message')}"
        </div>
        <div style="height:24px;"></div>""")

    # Error excerpt
    if _g(ctx, "error_excerpt"):
        parts.append(_section_title("Error Excerpt"))
        parts.append(_code_block(_g(ctx, "error_excerpt"),
                                 title="❌ Failure output"))
        parts.append('<div style="height:24px;"></div>')

    return "".join(parts)


def _content_pr_review_needed(ctx: dict) -> str:
    parts = []

    # Confidence + risk badges row
    badges = []
    if _g(ctx, "risk_level"):
        risk = _g(ctx, "risk_level").lower()
        risk_color = {
            "low": COLORS["success"],
            "medium": COLORS["warning"],
            "high": COLORS["danger"],
        }.get(risk, COLORS["neutral"])
        badges.append(_badge(f"Risk: {risk.title()}", risk_color))
    if _g(ctx, "playbook_id"):
        badges.append(_badge(f"📘 {_g(ctx, 'playbook_id')}", COLORS["primary"]))
    if _g(ctx, "failure_type"):
        badges.append(_badge(_g(ctx, "failure_type"), COLORS["neutral"]))
    if badges:
        parts.append(f"<div style='margin-bottom:20px;'>{' '.join(badges)}</div>")

    # Confidence bar (if provided)
    if _g(ctx, "confidence"):
        parts.append(_section_title("Fix Confidence"))
        parts.append(_confidence_bar(ctx.get("confidence")))
        parts.append('<div style="height:24px;"></div>')

    # Stat grid
    stats = []
    if _g(ctx, "pr_number"):
        stats.append(("PR Number", f"#{_g(ctx, 'pr_number')}", True))
    if ctx.get("lines_added") is not None or ctx.get("lines_deleted") is not None:
        added = ctx.get("lines_added", 0)
        deleted = ctx.get("lines_deleted", 0)
        stats.append((
            "Changes",
            f'<span style="color:{COLORS["success"]};">+{added}</span> '
            f'<span style="color:{COLORS["danger"]};">-{deleted}</span>',
            False,
        ))
    if ctx.get("files_changed"):
        files = ctx["files_changed"]
        count = len(files) if isinstance(files, list) else int(files)
        stats.append(("Files Changed", str(count), False))
    if stats:
        parts.append(_section_title("Change Summary"))
        parts.append(_stat_grid(stats))
        parts.append('<div style="height:24px;"></div>')

    # File list
    if isinstance(ctx.get("files_changed"), list) and ctx["files_changed"]:
        parts.append(_section_title("Modified Files"))
        files_html = "".join(
            f'<div style="padding:6px 12px;background:{COLORS["subcard"]};'
            f'border-bottom:1px solid {COLORS["border"]};'
            f'font-family:Menlo,Consolas,monospace;font-size:12px;'
            f'color:{COLORS["text2"]};">📄 {f}</div>'
            for f in ctx["files_changed"][:10]
        )
        parts.append(f"""
        <div style="border:1px solid {COLORS['border']};border-radius:8px;
                    overflow:hidden;">{files_html}</div>
        <div style="height:24px;"></div>""")

    # Diff preview
    if _g(ctx, "diff_preview"):
        parts.append(_section_title("Diff Preview"))
        parts.append(_code_block(_g(ctx, "diff_preview"),
                                 title="📝 Proposed changes", max_lines=15))
        parts.append('<div style="height:24px;"></div>')

    # Approval instructions
    parts.append(f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
           style="background:{COLORS['subcard']};border:1px solid {COLORS['border']};
                  border-radius:8px;margin-bottom:24px;">
      <tr><td style="padding:16px 18px;">
        <div style="font-size:12px;font-weight:700;color:{COLORS['text']};
                    margin-bottom:8px;">⚡ How to respond</div>
        <div style="font-size:13px;color:{COLORS['text2']};line-height:1.6;">
          <strong style="color:{COLORS['success']};">✓ Approve</strong>
          on the PR → RepoMind auto-merges within seconds.<br>
          <strong style="color:{COLORS['danger']};">✗ Request changes</strong>
          on the PR → RepoMind closes PR &amp; cleans up the branch.
        </div>
      </td></tr>
    </table>""")

    return "".join(parts)


def _content_pr_merged(ctx: dict) -> str:
    parts = []
    stats = []
    if _g(ctx, "pr_number"):
        stats.append(("PR Number", f"#{_g(ctx, 'pr_number')}", True))
    if _g(ctx, "merge_sha"):
        stats.append(("Merge SHA", _g(ctx, "merge_sha")[:8], True))
    if _g(ctx, "reviewer"):
        stats.append(("Approved By", _g(ctx, "reviewer"), False))
    if _g(ctx, "time_to_merge"):
        stats.append(("Time to Merge", _g(ctx, "time_to_merge"), False))
    if _g(ctx, "files_changed_count"):
        stats.append(("Files Changed", _g(ctx, "files_changed_count"), False))
    if stats:
        parts.append(_section_title("Merge Details"))
        parts.append(_stat_grid(stats))
        parts.append('<div style="height:24px;"></div>')

    parts.append(f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
           style="background:{COLORS['success']}10;border:1px solid {COLORS['success']}40;
                  border-radius:8px;margin-bottom:24px;">
      <tr><td style="padding:16px 18px;">
        <div style="font-size:13px;color:{COLORS['text']};line-height:1.6;">
          🎉 <strong>Nice work!</strong> Your repository is now back to a healthy
          state. RepoMind will continue monitoring future CI runs.
        </div>
      </td></tr>
    </table>""")
    return "".join(parts)


def _content_pr_rejected(ctx: dict) -> str:
    parts = []
    stats = []
    if _g(ctx, "pr_number"):
        stats.append(("PR Number", f"#{_g(ctx, 'pr_number')}", True))
    if _g(ctx, "reviewer"):
        stats.append(("Rejected By", _g(ctx, "reviewer"), False))
    if _g(ctx, "reason"):
        stats.append(("Reason", _g(ctx, "reason"), False))
    if stats:
        parts.append(_section_title("Rejection Details"))
        parts.append(_stat_grid(stats))
        parts.append('<div style="height:24px;"></div>')

    parts.append(f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
           style="background:{COLORS['subcard']};border:1px solid {COLORS['border']};
                  border-radius:8px;margin-bottom:24px;">
      <tr><td style="padding:16px 18px;">
        <div style="font-size:12px;font-weight:700;color:{COLORS['text']};
                    margin-bottom:8px;">🧹 Cleanup performed</div>
        <ul style="margin:0;padding-left:18px;font-size:13px;
                   color:{COLORS['text2']};line-height:1.7;">
          <li>Pull request closed</li>
          <li>Working branch deleted</li>
          <li>Apology comment posted on the PR</li>
        </ul>
      </td></tr>
    </table>""")
    return "".join(parts)


def _content_rollback(ctx: dict) -> str:
    parts = []
    stats = []
    if _g(ctx, "branch"):
        stats.append(("Branch", _g(ctx, "branch"), True))
    if _g(ctx, "attempts"):
        stats.append(("Attempts", _g(ctx, "attempts"), False))
    if _g(ctx, "reason"):
        stats.append(("Reason", _g(ctx, "reason"), False))
    if stats:
        parts.append(_section_title("Rollback Details"))
        parts.append(_stat_grid(stats))
        parts.append('<div style="height:24px;"></div>')

    parts.append(f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
           style="background:{COLORS['warning']}10;border:1px solid {COLORS['warning']}40;
                  border-radius:8px;margin-bottom:24px;">
      <tr><td style="padding:16px 18px;">
        <div style="font-size:13px;color:{COLORS['text']};line-height:1.6;">
          ⚠️ <strong>Manual intervention may be needed.</strong> The original
          failure remains — please investigate or adjust the playbook.
        </div>
      </td></tr>
    </table>""")
    return "".join(parts)


def _content_pipeline_error(ctx: dict) -> str:
    parts = []
    stats = []
    if _g(ctx, "event_id"):
        stats.append(("Event ID", _g(ctx, "event_id"), True))
    if _g(ctx, "stage"):
        stats.append(("Failed Stage", _g(ctx, "stage"), False))
    if _g(ctx, "error_type"):
        stats.append(("Error Type", _g(ctx, "error_type"), True))
    if stats:
        parts.append(_section_title("Error Context"))
        parts.append(_stat_grid(stats))
        parts.append('<div style="height:24px;"></div>')

    if _g(ctx, "error"):
        parts.append(_section_title("Error Message"))
        parts.append(_code_block(_g(ctx, "error"), title="💥 Stack trace"))
        parts.append('<div style="height:24px;"></div>')
    return "".join(parts)


_CONTENT_BUILDERS = {
    "ci_failed":        _content_ci_failed,
    "pr_review_needed": _content_pr_review_needed,
    "pr_merged":        _content_pr_merged,
    "pr_rejected":      _content_pr_rejected,
    "rollback":         _content_rollback,
    "pipeline_error":   _content_pipeline_error,
}


# ─────────────────────────────────────────────────────────────────────────
# CTAs
# ─────────────────────────────────────────────────────────────────────────

def _cta(event: str, ctx: dict) -> Optional[dict]:
    pr_url = ctx.get("pr_url")
    repo = ctx.get("repo")
    if event == "pr_review_needed" and pr_url:
        return {"label": "Review Pull Request →", "url": pr_url,
                "color": COLORS["primary"]}
    if event == "pr_merged" and pr_url:
        return {"label": "View Merged PR →", "url": pr_url,
                "color": COLORS["success"]}
    if event == "pr_rejected" and pr_url:
        return {"label": "View Closed PR →", "url": pr_url,
                "color": COLORS["neutral"]}
    if event == "ci_failed" and repo and ctx.get("run_id"):
        return {"label": "View CI Run on GitHub →",
                "url": f"https://github.com/{repo}/actions/runs/{ctx['run_id']}",
                "color": COLORS["danger"]}
    if event in ("rollback", "pipeline_error") and repo:
        return {"label": "View Repository →",
                "url": f"https://github.com/{repo}",
                "color": COLORS["primary"]}
    return None


# ─────────────────────────────────────────────────────────────────────────
# MAIN HTML RENDERER
# ─────────────────────────────────────────────────────────────────────────

def _html(event: str, ctx: dict) -> str:
    style = EVENT_STYLE.get(event, EVENT_STYLE["pipeline_error"])
    repo = _g(ctx, "repo", "unknown/repo")
    branch = _g(ctx, "branch") or "main"
    timestamp = _g(ctx, "timestamp") or datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M UTC"
    )
    event_id = _g(ctx, "event_id", "—")

    # Header chips
    chips = [_chip("📁", repo), _chip("🌿", branch)]
    if _g(ctx, "pr_number"):
        chips.append(_chip("🔢", f"PR #{_g(ctx, 'pr_number')}"))
    chips_html = "".join(chips)

    # Event-specific body
    builder = _CONTENT_BUILDERS.get(event, _content_pipeline_error)
    content_html = builder(ctx)

    # Timeline
    timeline_html = ""
    if style["timeline_step"] > 0:
        timeline_html = f"""
        <tr><td style="padding:20px 28px 4px 28px;">
          {_section_title("Pipeline Status")}
          {_timeline(style['timeline_step'])}
        </td></tr>
        <tr><td style="padding:0 28px;"><div style="height:20px;"></div></td></tr>"""

    # CTA
    cta = _cta(event, ctx)
    cta_html = ""
    if cta:
        cta_html = f"""
        <tr><td align="center" style="padding:8px 28px 24px 28px;">
          {_button(cta["label"], cta["url"], cta["color"])}
        </td></tr>"""

    # Preheader (invisible inbox preview text)
    preheader = f"{style['label']} · {repo} · {style['tagline']}"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<meta name="color-scheme" content="light only">
<title>{style['label']}</title>
</head>
<body style="margin:0;padding:0;background:{COLORS['bg']};
             font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,
                         Helvetica,Arial,sans-serif;color:{COLORS['text']};
             -webkit-font-smoothing:antialiased;">

  <div style="display:none;max-height:0;overflow:hidden;color:transparent;
              font-size:1px;line-height:1px;">
    {preheader}
  </div>

  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background:{COLORS['bg']};padding:32px 12px;">
    <tr><td align="center">

      <table role="presentation" width="640" cellpadding="0" cellspacing="0" border="0"
             style="max-width:640px;background:{COLORS['card']};border-radius:12px;
                    box-shadow:0 4px 16px rgba(15,23,42,0.06),
                               0 1px 3px rgba(15,23,42,0.04);overflow:hidden;">

        <!-- HEADER -->
        <tr><td style="background:{style['color']};padding:24px 28px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
            <tr>
              <td valign="top" style="font-size:36px;line-height:1;width:52px;">
                {style['icon']}
              </td>
              <td valign="top" style="padding-left:6px;color:#ffffff;">
                <div style="font-size:20px;font-weight:700;line-height:1.2;
                            letter-spacing:-0.2px;">{style['label']}</div>
                <div style="font-size:13px;opacity:0.92;margin-top:4px;">
                  {style['tagline']}
                </div>
              </td>
              <td valign="top" align="right">
                <div style="color:#ffffff;font-size:11px;opacity:0.85;
                            font-weight:700;letter-spacing:1.2px;">REPOMIND</div>
              </td>
            </tr>
            <tr><td colspan="3" style="padding-top:16px;">
              {chips_html}
            </td></tr>
          </table>
        </td></tr>

        <!-- SUMMARY -->
        <tr><td style="padding:24px 28px 4px 28px;">
          <div style="font-size:14px;color:{COLORS['text2']};line-height:1.65;">
            {style['summary']}
          </div>
        </td></tr>

        <!-- DIVIDER -->
        <tr><td style="padding:20px 28px 0 28px;">
          <div style="border-top:1px solid {COLORS['border']};"></div>
        </td></tr>

        <!-- EVENT-SPECIFIC CONTENT -->
        <tr><td style="padding:24px 28px 0 28px;">
          {content_html}
        </td></tr>

        {timeline_html}

        <!-- CTA -->
        {cta_html}

        <!-- FOOTER -->
        <tr><td style="padding:18px 28px 22px 28px;background:{COLORS['subcard']};
                       border-top:1px solid {COLORS['border']};
                       color:{COLORS['muted']};font-size:11px;line-height:1.7;
                       text-align:center;">
          <div style="margin-bottom:4px;">
            Event <code style="background:{COLORS['border']};padding:2px 6px;
                              border-radius:4px;font-size:11px;
                              color:{COLORS['text2']};">{event_id}</code>
            &nbsp;·&nbsp; {timestamp}
          </div>
          <div>
            Automated by
            <strong style="color:{COLORS['primary']};">RepoMind</strong>
            — autonomous CI/CD repair agent
          </div>
          <div style="margin-top:10px;opacity:0.85;">
            <a href="mailto:repomind@noreply.invalid?subject=unsubscribe"
               style="color:{COLORS['muted']};text-decoration:underline;">
              Unsubscribe
            </a>
            &nbsp;·&nbsp;
            <a href="https://github.com/{repo}"
               style="color:{COLORS['muted']};text-decoration:underline;">
              View repo
            </a>
          </div>
        </td></tr>

      </table>

    </td></tr>
  </table>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────────────────
# PLAIN-TEXT RENDERER (spam-score boost + accessibility)
# ─────────────────────────────────────────────────────────────────────────

def _text(event: str, ctx: dict) -> str:
    style = EVENT_STYLE.get(event, EVENT_STYLE["pipeline_error"])
    repo = _g(ctx, "repo", "unknown/repo")
    lines = [
        f"{style['icon']}  {style['label'].upper()}",
        style["tagline"],
        "",
        "=" * 60,
        f"  Repository : {repo}",
    ]
    for key in ("branch", "pr_number", "run_id", "commit_sha", "author",
                "workflow_name", "failed_step", "reviewer", "merge_sha",
                "reason", "failure_type", "playbook_id", "confidence"):
        v = _g(ctx, key)
        if v:
            label = key.replace("_", " ").title()
            lines.append(f"  {label:14s}: {v}")
    lines.append("=" * 60)
    lines.append("")
    lines.append(style["summary"])
    if _g(ctx, "error_excerpt") or _g(ctx, "error"):
        lines += ["", "ERROR:", "-" * 60,
                  _g(ctx, "error_excerpt") or _g(ctx, "error"), "-" * 60]
    cta = _cta(event, ctx)
    if cta:
        lines += ["", f"→ {cta['label']}", f"  {cta['url']}"]
    lines += ["", "—",
              f"Event ID: {_g(ctx, 'event_id', 'n/a')}",
              "Automated by RepoMind — autonomous CI/CD repair agent"]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────

class _SafeDict(dict):
    def __missing__(self, key):
        return f"<{key}>"


def render_email(event: str, context: dict) -> tuple:
    """Returns (subject, html_body, text_body)."""
    safe = _SafeDict(context)
    subject = SUBJECTS.get(event, "[RepoMind] Notification").format_map(safe)
    return subject, _html(event, context), _text(event, context)