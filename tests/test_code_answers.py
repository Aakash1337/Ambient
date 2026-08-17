from __future__ import annotations

from ambientqa.answer import ClaudeAnswerer
from ambientqa.config import AnswerConfig
from ambientqa.ui import plain_text

DECORATOR = '''Sure. You wrap a function and return the wrapper:

```python
def my_decorator(func):
    def wrapper(*args, **kwargs):
        print("before")
        result = func(*args, **kwargs)
        print("after")
        return result
    return wrapper
```

Then put @my_decorator above any function you want wrapped.'''


def test_code_block_keeps_newlines_and_indentation() -> None:
    out = plain_text(DECORATOR)
    lines = out.splitlines()
    assert "def my_decorator(func):" in lines
    # Indentation is load-bearing in Python and must survive verbatim.
    assert "    def wrapper(*args, **kwargs):" in lines
    assert "        result = func(*args, **kwargs)" in lines
    # Not collapsed into a single semicolon-joined line, which was the bug.
    assert len(lines) > 6
    assert ";" not in out


def test_python_star_args_survive_markdown_stripping() -> None:
    """`*args, **kwargs` matches the bold/italic patterns exactly."""
    out = plain_text(DECORATOR)
    assert "*args, **kwargs" in out
    assert out.count("**kwargs") == 2


def test_code_lines_starting_with_operators_are_not_eaten_as_bullets() -> None:
    raw = "Example:\n\n```python\n- 1\n* 2\n+ 3\n```\n"
    out = plain_text(raw)
    for line in ("- 1", "* 2", "+ 3"):
        assert line in out.splitlines()


def test_fences_themselves_are_removed() -> None:
    assert "```" not in plain_text(DECORATOR)


def test_prose_markdown_still_stripped_outside_code() -> None:
    out = plain_text("Use **bold** here.\n\n```py\nx = 1\n```\n")
    assert "**bold**" not in out and "bold" in out
    assert "x = 1" in out


def test_unclosed_fence_still_preserves_code() -> None:
    out = plain_text("Here:\n\n```python\ndef f():\n    return 1\n")
    assert "def f():" in out
    assert "    return 1" in out.splitlines()
    assert "```" not in out


def test_prompt_carries_the_code_exception() -> None:
    prompt = ClaudeAnswerer(AnswerConfig(style="interview")).system_prompt
    lowered = prompt.lower()
    assert "code exception" in lowered
    assert "fenced" in lowered
    # The word cap must be scoped to prose so code is never truncated to fit.
    assert "applies only to your prose" in lowered
    # And the spoken-prose rules must still be present for non-code answers.
    assert "two to four sentences" in lowered
