from markupsafe import Markup, escape


def render_verification_rules(item, depth=0, show_status=False) -> Markup:
    """Recursively render a verification rule tree as HTML.

    show_status=True  — completed task run, shows pass/fail colors and labels
    show_status=False — pending task definition, shows neutral gray indicators
    """
    if not item:
        msg = (
            "No verification details available"
            if show_status
            else "No verification rules defined"
        )
        return Markup(f'<div class="text-sm italic" style="color:#8A8A9A">{msg}</div>')

    indent = depth * 20
    t = item.get("type")

    if show_status:
        passed = item.get("passed")
        color = "#24C875" if passed else "#FF4D4D"
        icon = "✓" if passed else "✗"
    else:
        passed = None
        color = "#8A8A9A"
        icon = "○"

    if t == "url_contains":
        v = escape(item.get("value", ""))
        label = (
            f'<span class="ml-2 text-xs font-semibold" style="color:{color}">{"PASSED" if passed else "FAILED"}</span>'
            if show_status
            else ""
        )
        return Markup(
            f'<div class="flex items-start gap-2 py-2" style="margin-left:{indent}px">'
            f'<span style="color:{color};font-weight:600;font-size:1.125rem">{icon}</span>'
            f'<div><span class="text-sm font-medium" style="color:#2C2C3A">URL contains:</span>'
            f'<code class="ml-2 px-2 py-0.5 rounded text-xs ibm-mono" style="background:#F8F8FA;border:1px solid #E4E4EC">{v}</code>'
            f"{label}</div></div>"
        )

    if t == "text_any":
        texts = escape(", ".join(item.get("texts", [])))
        label = (
            f'<span class="ml-2 text-xs font-semibold" style="color:{color}">{"PASSED" if passed else "FAILED"}</span>'
            if show_status
            else ""
        )
        return Markup(
            f'<div class="flex items-start gap-2 py-2" style="margin-left:{indent}px">'
            f'<span style="color:{color};font-weight:600;font-size:1.125rem">{icon}</span>'
            f'<div><span class="text-sm font-medium" style="color:#2C2C3A">Page shows text:</span>'
            f'<code class="ml-2 px-2 py-0.5 rounded text-xs ibm-mono" style="background:#F8F8FA;border:1px solid #E4E4EC">{texts}</code>'
            f"{label}</div></div>"
        )

    if t in ("any_of", "all_of"):
        group_name = escape(
            item.get("name", "OR Group") if t == "any_of" else "AND Group"
        )
        desc = "(any one must pass)" if t == "any_of" else "(all must pass)"
        title_color = "#0A66FF" if t == "any_of" else "#090947"
        border_color = color if show_status else "#E4E4EC"
        pass_label = (
            f'<span class="text-xs font-semibold" style="color:{color}">{"✓ GROUP PASSED" if passed else "✗ GROUP FAILED"}</span>'
            if show_status
            else ""
        )
        # completed runs use "children", definitions use "conditions"
        children_data = item.get("children") or item.get("conditions") or []
        children = Markup("").join(
            render_verification_rules(child, depth + 1, show_status)
            for child in children_data
        )
        return Markup(
            f'<div class="border-l-2 pl-3 py-2" style="margin-left:{indent}px;border-color:{border_color}">'
            f'<div class="text-sm font-semibold mb-2" style="color:{title_color}">{group_name} '
            f'<span class="text-xs font-normal" style="color:#8A8A9A">{desc}</span> '
            f"{pass_label}</div>"
            f"{children}</div>"
        )

    return Markup(
        '<div class="text-sm italic" style="color:#8A8A9A">Unknown rule type</div>'
    )
