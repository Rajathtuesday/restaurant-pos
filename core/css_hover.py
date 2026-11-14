"""
Hover lifts that a touch screen must not get (V15, 14 Nov 2026).

A card that lifts on :hover (transform: translateY(-2px)) stays lifted on a
tablet after a tap, because a touch screen keeps the last tapped element
"hovered". So a :hover rule that moves something belongs inside
``@media (hover: hover)``, where only a mouse gets it; touch screens get a
pressed state instead (static/css/rasova.css, ``@media (hover: none)``).

``loose_hover_lifts`` finds the rules that break this, for the test in
core/tests/test_touch.py; ``wrap`` is how they were fixed.
"""
import re

RULE = re.compile(r"[^{}]+\{[^{}]*\}")
MOVES = re.compile(r"\btransform\s*:")


def _blocks(css):
    """(start, end, selector, body, enclosing at-rule header) of every plain rule."""
    out, stack, i, n = [], [], 0, len(css)
    head_start = 0
    while i < n:
        c = css[i]
        if c == "{":
            header = css[head_start:i].strip()
            close = css.find("}", i)
            nxt = css.find("{", i + 1)
            if header.startswith("@") or (nxt != -1 and nxt < close):
                stack.append(header)
                head_start = i + 1
                i += 1
                continue
            sel_start = head_start + (len(css[head_start:i]) - len(css[head_start:i].lstrip()))
            out.append((sel_start, close + 1, header, css[i + 1:close], stack[-1] if stack else ""))
            i = close + 1
            head_start = i
            continue
        if c == "}":
            if stack:
                stack.pop()
            head_start = i + 1
        i += 1
    return out


def _strip_comments(css):
    return re.sub(r"/\*.*?\*/", lambda m: " " * len(m.group(0)), css, flags=re.S)


def loose_hover_lifts(css):
    """Selectors of :hover rules that move something outside @media (hover: hover)."""
    clean = _strip_comments(css)
    return [sel for _s, _e, sel, body, parent in _blocks(clean)
            if ":hover" in sel and MOVES.search(body) and "hover: hover" not in parent.replace("hover:hover", "hover: hover")]


def wrap(css):
    """The same CSS with each loose hover lift inside @media (hover: hover), on the same lines."""
    clean = _strip_comments(css)
    spans = [(s, e) for s, e, sel, body, parent in _blocks(clean)
             if ":hover" in sel and MOVES.search(body) and "hover: hover" not in parent.replace("hover:hover", "hover: hover")]
    for start, end in reversed(spans):
        css = css[:start] + "@media (hover: hover) { " + css[start:end] + " }" + css[end:]
    return css


def style_sources(text, is_html):
    """The CSS in a file: the whole file, or a template's <style> blocks."""
    if not is_html:
        return [text]
    return re.findall(r"<style[^>]*>(.*?)</style>", text, re.S)
