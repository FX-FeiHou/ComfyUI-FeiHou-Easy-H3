"""Time-tagged prompt lines: each sampled window reads only its own actions.

A line of a shot prompt may start with a time range in film time (the same
clock as ``@range``)::

    [00:03.50-00:08.00] 她从右边走进画面，把咖啡杯放到圆台上，坐下。
    [12.8-18.67] 她从圆台上爬下来，坐到长凳上。

When a window of the reference video is encoded (a context window, a
frame-window segment, or a whole block), only the tagged lines whose range
overlaps that window are kept, without their tag; untagged lines (who is who,
appearance, colour) go to every window.  A prompt without tags is unchanged.
Where the whole prompt is encoded at once, the tags are only removed.

Dialogue lines (a ``<d>…</d>`` line) are stricter: a window speaks a tagged
line only when at least half of it lies inside the window, so a sentence is
not started by two windows at once.  When a window's first spoken line starts
well after the window does, or its last one ends well before, a short note
tells the model to keep quiet there (context windows of a talk block are
planned so that their overlaps fall in those pauses, see
``h3_context_windows.plan_speech_windows``).
"""

from __future__ import annotations

import re

# [mm:ss.xx-mm:ss.xx], [hh:mm:ss-...] or [12.5-18] at the start of a line
# (after optional indentation or a list bullet).
_TIME = r"\d+(?::\d{1,2}){0,2}(?:\.\d+)?"
_TAG = re.compile(rf"^(?P<lead>[ \t]*(?:[-*•][ \t]+)?)\[[ \t]*(?P<a>{_TIME})[ \t]*[-–—~～至到][ \t]*(?P<b>{_TIME})[ \t]*\][ \t]*")
# A tagged line is kept when it overlaps the window by at least this much (or
# lies entirely inside it): an action that only grazes the window's edge is
# left to the neighbouring window.
MIN_OVERLAP_SECONDS = 0.5
# A dialogue line is spoken by a window holding at least this share of it.
DIALOGUE_SHARE = 0.5
# Silence at a window's head or tail longer than this gets a note.
QUIET_NOTE_SECONDS = 0.5
_DIALOGUE = re.compile(r"<d[\s>]", re.IGNORECASE)
_CJK = re.compile("[一-鿿]")
_LEAD = re.compile(r"[ \t]*(?:[-*•][ \t]+)?")


def _seconds(value: str) -> float:
    total = 0.0
    for part in value.split(":"):
        total = total * 60 + float(part)
    return total


def _tagged(line: str):
    match = _TAG.match(line)
    if not match:
        return None
    start, end = _seconds(match["a"]), _seconds(match["b"])
    if end <= start:
        return None
    return start, end, match["lead"] + line[match.end():]


def _is_dialogue(body: str) -> bool:
    return bool(_DIALOGUE.search(body))


def dialogue_spans(text) -> list[tuple[float, float]]:
    """(start, end) seconds of every time-tagged dialogue line, in order."""
    spans = []
    for line in str(text or "").splitlines():
        tagged = _tagged(line)
        if tagged and _is_dialogue(tagged[2]):
            spans.append((tagged[0], tagged[1]))
    return sorted(spans)


def has_time_tags(text) -> bool:
    return any(_tagged(line) for line in str(text or "").splitlines())


def strip_time_tags(text):
    """Every line kept, tags removed (the whole film's prompt)."""
    if not isinstance(text, str) or not has_time_tags(text):
        return text
    lines = []
    for line in text.split("\n"):
        tagged = _tagged(line)
        lines.append(tagged[2] if tagged else line)
    return "\n".join(lines)


def window_prompt(text, start_seconds: float, end_seconds: float):
    """The prompt for film time [start_seconds, end_seconds): its tagged lines only."""
    if not isinstance(text, str) or not has_time_tags(text):
        return text
    return _filter(text, start_seconds, end_seconds)[0]


def window_lines(text, start_seconds: float, end_seconds: float) -> tuple[int, int]:
    """(kept, total) tagged lines for a window, for the log."""
    if not isinstance(text, str):
        return 0, 0
    _text, kept, total = _filter(text, start_seconds, end_seconds)
    return kept, total


def _covers(start: float, end: float, start_seconds: float, end_seconds: float, dialogue: bool = False) -> bool:
    overlap = min(end, end_seconds) - max(start, start_seconds)
    inside = start >= start_seconds and end <= end_seconds
    if dialogue:
        return inside or overlap >= DIALOGUE_SHARE * (end - start) - 1e-6
    return inside or overlap >= min(MIN_OVERLAP_SECONDS, end - start)


def _quiet_notes(body: str, head: float, tail: float) -> tuple[str, str]:
    """Notes for ``head`` seconds of silence before a window's first line and ``tail`` after its last."""
    chinese = bool(_CJK.search(body))
    before = after = ""
    if head > QUIET_NOTE_SECONDS:
        before = (f"这一段开头约 {head:.1f} 秒先闭着嘴不说话，之后才开口。" if chinese
                  else f"For the first {head:.1f} seconds of this part there is no speech; the line starts after that.")
    if tail > QUIET_NOTE_SECONDS:
        after = (f"这句说完后闭上嘴，约 {tail:.1f} 秒不再说话，直到这一段结束。" if chinese
                 else f"After this line there is no more speech for the last {tail:.1f} seconds of this part.")
    return before, after


def _filter(text: str, start_seconds: float, end_seconds: float):
    lines, kept, total = [], 0, 0
    spoken = []  # (index in lines, start, end) of the kept dialogue lines
    for line in text.split("\n"):
        tagged = _tagged(line)
        if tagged is None:
            lines.append(line)
            continue
        total += 1
        start, end, body = tagged
        dialogue = _is_dialogue(body)
        if _covers(start, end, start_seconds, end_seconds, dialogue):
            if dialogue:
                spoken.append((len(lines), start, end))
            lines.append(body)
            kept += 1
    if spoken:
        first = min(spoken, key=lambda item: item[1])
        last = max(spoken, key=lambda item: item[2])
        before, after = _quiet_notes(lines[first[0]], first[1] - start_seconds, end_seconds - last[2])
        if after:
            lines[last[0]] = f"{lines[last[0]]} {after}"
        if before:
            lead = _LEAD.match(lines[first[0]]).group(0)
            lines[first[0]] = f"{lead}{before}{lines[first[0]][len(lead):]}"
    return "\n".join(lines), kept, total
