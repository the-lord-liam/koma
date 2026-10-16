"""Prompt text for story planning and panel art.

Dialogue, captions, and sound effects are lettered in code on the finished
page. Image prompts forbid written text so the model does not invent it.
"""

from __future__ import annotations

import json
import re

from koma.layouts import LAYOUTS, aspect_for_slot

DEFAULT_STYLE = (
    "black-and-white manga, confident ink lines, screentone shading, "
    "expressive faces, clear silhouettes"
)
ILLUST_STYLE = "polished illustration, coherent light, finished artwork, no comic panels"
ILLUST_ASPECTS = ["2:3", "3:4", "1:1", "4:3", "3:2", "16:9", "9:16", "2:1"]

SHOTS = [
    "extreme close-up",
    "close-up",
    "medium",
    "cowboy",
    "full body",
    "wide",
    "establishing",
]
ANGLES = [
    "eye level",
    "low angle",
    "high angle",
    "bird's eye",
    "worm's eye",
    "over the shoulder",
    "dutch angle",
]
EXPRESSIONS = [
    "neutral",
    "smile",
    "grin",
    "angry",
    "shouting",
    "shocked",
    "sad",
    "crying",
    "determined",
    "smirk",
    "fear",
    "embarrassed",
    "cold",
]
COMPOSITIONS = [
    "rule of thirds",
    "centered",
    "diagonal",
    "negative space",
    "foreground frame",
    "symmetrical",
    "tight crop",
    "deep staging",
]
BALLOONS = [
    {"id": "round", "label": "Speech"},
    {"id": "shout", "label": "Shout"},
    {"id": "thought", "label": "Thought"},
    {"id": "caption", "label": "Caption"},
    {"id": "none", "label": "No balloon"},
]
ANCHORS = [
    {"id": "tl", "label": "Top left"},
    {"id": "tr", "label": "Top right"},
    {"id": "bl", "label": "Bottom left"},
    {"id": "br", "label": "Bottom right"},
]

_NO_TEXT = "No written text, letters, speech balloons, captions, or sound effects."


def choice(value: str | None, options: list[str], default: str) -> str:
    if value in options:
        return value
    folded = (value or "").strip().casefold()
    for option in options:
        if option.casefold() == folded:
            return option
    return default


def vocab() -> dict:
    return {
        "shots": SHOTS,
        "angles": ANGLES,
        "expressions": EXPRESSIONS,
        "compositions": COMPOSITIONS,
        "balloons": BALLOONS,
        "anchors": ANCHORS,
        "illustAspects": ILLUST_ASPECTS,
    }


def illustration_prompt(illust: dict, *, revise: str | None = None) -> str:
    """One finished picture. Lettering stays out of the image."""
    style = (illust.get("art_style") or ILLUST_STYLE).strip()
    subject = (illust.get("prompt") or "").strip()
    if revise:
        return (
            f"<IMAGE_0> is the illustration to edit. {revise.strip()} "
            f"Keep the same subject unless the note says otherwise. "
            f"Style: {style}. {_NO_TEXT}"
        )
    return (
        f"A single finished illustration, not a comic page, no panel borders. {subject} "
        f"Style: {style}. {_NO_TEXT}"
    )


STORY_SYSTEM = """You are the story editor inside Koma, a manga studio.
You plan cast, locations, chapters, pages, and panels that an artist can draw.
Every panel is one visual beat: who is there, what they are doing, the camera, and the expression.
Dialogue is lettered later by the app. Do not depend on words painted into the art.
When you propose or revise structure, end with one json code fence and write nothing after it.
If the user only asks a question, answer in prose and do not include a json fence.
Keep existing names stable unless the user asks to rename someone.

The JSON object has this shape:
{
  "title": "",
  "logline": "",
  "genre": "",
  "tone": "",
  "art_style": "",
  "style_notes": "",
  "cast": [{"name": "", "role": "", "personality": "", "appearance": "", "wardrobe": ""}],
  "locations": [{"name": "", "description": ""}],
  "chapters": [{
    "title": "",
    "synopsis": "",
    "beats": [""],
    "pages": [{
      "layout": "classic",
      "notes": "",
      "panels": [{
        "shot": "medium",
        "angle": "eye level",
        "expression": "neutral",
        "composition": "rule of thirds",
        "action": "",
        "dialogue": "",
        "sfx": "",
        "characters": ["Name"],
        "location": "Name"
      }]
    }]
  }]
}
Use only the layout ids, shots, angles, expressions, and compositions from the user message.
The number of panels on a page must match that layout.
Appearance must be specific and drawable: hair, face, build, and a repeating outfit.
"""


def plan_request(project: dict, chapters: int, pages: int) -> str:
    layouts = ", ".join(f"{key} ({len(slots)} panels)" for key, (_label, slots) in LAYOUTS.items())
    return (
        f"Plan this manga.\n\n"
        f"Premise:\n{project.get('premise') or '(none yet — invent a complete short story from the title)'}\n\n"
        f"Title so far: {project.get('title') or 'Untitled'}\n"
        f"Art style to keep unless the premise asks otherwise: {project.get('art_style') or DEFAULT_STYLE}\n"
        f"Reading direction: {project.get('reading') or 'rtl'}\n"
        f"Target: {chapters} chapters, about {pages} pages each.\n\n"
        f"Layouts: {layouts}\n"
        f"Shots: {', '.join(SHOTS)}\n"
        f"Angles: {', '.join(ANGLES)}\n"
        f"Expressions: {', '.join(EXPRESSIONS)}\n"
        f"Compositions: {', '.join(COMPOSITIONS)}\n"
    )


def project_brief(project: dict, characters: list[dict], locations: list[dict], chapters: list[dict]) -> str:
    lines = [
        f"Title: {project.get('title') or 'Untitled'}",
        f"Logline: {project.get('logline') or ''}",
        f"Genre: {project.get('genre') or ''}",
        f"Tone: {project.get('tone') or ''}",
        f"Art style: {project.get('art_style') or ''}",
        f"Premise: {project.get('premise') or ''}",
        "Cast:",
    ]
    if not characters:
        lines.append("- (none)")
    for person in characters:
        lines.append(
            f"- {person['name']} ({person.get('role') or 'role unset'}): "
            f"{person.get('appearance') or ''} Wardrobe: {person.get('wardrobe') or ''}"
        )
    lines.append("Locations:")
    if not locations:
        lines.append("- (none)")
    for place in locations:
        lines.append(f"- {place['name']}: {place.get('description') or ''}")
    lines.append("Chapters:")
    if not chapters:
        lines.append("- (none)")
    for index, chapter in enumerate(chapters, start=1):
        lines.append(f"- {index}. {chapter['title']}: {chapter.get('synopsis') or ''}")
    return "\n".join(lines)


def extract_plan(text: str) -> tuple[str, dict | None]:
    """Split an editor reply into prose plus an optional plan object."""
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    blob = fence.group(1) if fence else None
    prose = text
    if fence:
        prose = (text[: fence.start()] + text[fence.end() :]).strip()
    elif text.strip().startswith("{"):
        blob = text.strip()
        prose = ""
    if not blob:
        return prose.strip(), None
    try:
        value = json.loads(blob)
    except json.JSONDecodeError:
        return text.strip(), None
    if not isinstance(value, dict):
        return text.strip(), None
    if not prose:
        prose = "Plan ready to apply."
    return prose.strip(), value


def _style(project: dict) -> str:
    style = (project.get("art_style") or DEFAULT_STYLE).strip()
    notes = (project.get("style_notes") or "").strip()
    if notes:
        return f"{style}. {notes}"
    return style


def character_prompt(project: dict, character: dict, kind: str, label: str, has_ref: bool) -> tuple[str, str]:
    """Return (prompt, aspect_ratio) for a character reference."""
    name = character.get("name") or "the character"
    appearance = (character.get("appearance") or "a distinctive original manga character").strip()
    wardrobe = (character.get("wardrobe") or "their usual outfit").strip()
    style = _style(project)
    who = f"{name}, {appearance}. Outfit: {wardrobe}."
    if kind == "portrait":
        if has_ref:
            prompt = (
                f"<IMAGE_0> is {name}. Waist-up front portrait of this same person, "
                f"calm expression, plain light gray background. Match face, hair, and outfit. "
                f"Style: {style}. {_NO_TEXT}"
            )
        else:
            prompt = (
                f"Manga character portrait of {who} "
                f"Front view, waist up, calm expression, plain light gray background. "
                f"Style: {style}. {_NO_TEXT}"
            )
        return prompt, "2:3"
    if kind == "turnaround":
        body = (
            "Draw a turnaround sheet of this same person: front, side, and back, full body, "
            "even spacing, plain light gray background. Match face, hair, and outfit."
            if has_ref
            else f"Character turnaround sheet of {who} Three full-body views side by side: front, side, and back. Plain light gray background."
        )
        prefix = "<IMAGE_0> is the character. " if has_ref else ""
        return f"{prefix}{body} Style: {style}. {_NO_TEXT}", "16:9"
    if kind == "expressions":
        body = (
            "Draw six head-and-shoulders views of this same person in one row: "
            "calm, angry, joyful, shocked, sad, and determined. Same face and hair. "
            "Plain light gray background."
            if has_ref
            else f"Expression sheet of {who} Six headshots in a row: calm, angry, joyful, shocked, sad, determined. Plain light gray background."
        )
        prefix = "<IMAGE_0> is the character. " if has_ref else ""
        return f"{prefix}{body} Style: {style}. {_NO_TEXT}", "16:9"
    pose = label.strip() or "standing, walking, a leap, and sitting"
    if has_ref:
        prompt = (
            f"<IMAGE_0> is the character. Draw full-body poses of this same person: {pose}. "
            f"Match face, hair, and outfit. Plain light gray background. Style: {style}. {_NO_TEXT}"
        )
    else:
        prompt = (
            f"Pose sheet of {who} Full-body poses: {pose}. "
            f"Plain light gray background. Style: {style}. {_NO_TEXT}"
        )
    return prompt, "16:9"


def location_prompt(project: dict, location: dict, has_ref: bool, instruction: str = "") -> tuple[str, str]:
    name = location.get("name") or "the location"
    description = (location.get("description") or "a specific, drawable place").strip()
    style = _style(project)
    if instruction and has_ref:
        prompt = (
            f"<IMAGE_0> is the location called {name}. {instruction} "
            f"Keep it the same place. Wide establishing view, no people. Style: {style}. {_NO_TEXT}"
        )
    elif has_ref:
        prompt = (
            f"<IMAGE_0> is the location. A wide establishing view of {name}: {description}. "
            f"No people. Style: {style}. {_NO_TEXT}"
        )
    else:
        prompt = (
            f"Wide establishing view of {name}: {description}. "
            f"No people. Style: {style}. {_NO_TEXT}"
        )
    return prompt, "16:9"


def reference_line(index: int, name: str, role: str) -> str:
    return f"<IMAGE_{index}> is {name}, the {role}."


def build_panel(
    project: dict,
    panel: dict,
    slot: dict,
    characters: list[dict],
    location: dict | None,
    *,
    revise: str | None = None,
    current_image: str | None = None,
    previous_image: str | None = None,
    page_aspect: float | None = None,
    extra_refs: list | None = None,
) -> dict:
    """Assemble the panel prompt and the reference files that anchor identity.

    `characters` and `location` may include `ref_path` (a filesystem path).
    `previous_image` is the last drawn panel before this one. A revise edits
    the current drawing and does not also attach that earlier panel.
    """
    refs: list[str] = []
    notes: list[str] = []
    warnings: list[str] = []

    if revise:
        if not current_image:
            raise ValueError("Draw the panel before revising it.")
        refs.append(current_image)
        notes.append(reference_line(0, "this picture", "current panel to edit"))

    for person in characters:
        path = person.get("ref_path")
        if path and len(refs) < 5:
            refs.append(path)
            notes.append(
                reference_line(len(refs) - 1, person["name"], "identity reference. Match their face, hair, and outfit")
            )
        elif not path and not (person.get("appearance") or "").strip():
            warnings.append(f"{person['name']} has no portrait yet, so their design may drift.")

    if previous_image and not revise:
        if len(refs) < 5:
            refs.append(previous_image)
            notes.append(
                f"<IMAGE_{len(refs) - 1}> is the previous panel. Continue that scene: "
                "same costumes, lighting, and place, drawn as this new moment."
            )
        else:
            warnings.append("The previous panel was left out because five references are already attached.")

    if location and location.get("ref_path") and len(refs) < 5:
        refs.append(location["ref_path"])
        notes.append(reference_line(len(refs) - 1, location["name"], "location reference"))

    for path, label in extra_refs or []:
        if path and len(refs) < 5:
            refs.append(path)
            notes.append(reference_line(len(refs) - 1, label, "reference"))
        elif path:
            warnings.append(f"The {label} was left out because five references are already attached.")

    people = []
    for person in characters:
        look = (person.get("appearance") or "").strip()
        clothes = (person.get("wardrobe") or "").strip()
        bit = person["name"]
        if look:
            bit += f", {look}"
        if clothes:
            bit += f", wearing {clothes}"
        people.append(bit)
    cast = "; ".join(people) if people else "no named character"
    action = (panel.get("action") or "holding a quiet beat").strip()
    place = ""
    if location:
        place = f" Setting: {location['name']}. {(location.get('description') or '').strip()}"
    camera = (
        f"Shot: {panel.get('shot') or 'medium'}. "
        f"Camera: {panel.get('angle') or 'eye level'}. "
        f"Composition: {panel.get('composition') or 'rule of thirds'}."
    )
    feeling = f"Expression: {panel.get('expression') or 'neutral'}."
    extra = (panel.get("extra") or "").strip()
    creator = (panel.get("prompt") or "").strip()
    vibe = (panel.get("vibe") or "").strip()
    if creator:
        extra = f"{extra} Creator direction: {creator}.".strip()
    if vibe:
        extra = f"{extra} Atmosphere: {vibe}.".strip()
    style = _style(project)

    if revise:
        prompt = (
            f"{' '.join(notes)} "
            f"Edit the panel. {revise.strip()} "
            f"Keep the same characters and the same ink style. "
            f"Cast for identity: {cast}. {feeling} {camera}{place} "
            f"Style: {style}. {extra} {_NO_TEXT}"
        ).strip()
    else:
        lead = (" ".join(notes) + " ") if notes else ""
        prompt = (
            f"{lead}A single manga panel, borderless. "
            f"In frame: {cast}. They are {action}. {feeling} {camera}{place} "
            f"Style: {style}. {extra} {_NO_TEXT}"
        ).strip()
        prompt = re.sub(r"\s+", " ", prompt)

    if revise:
        prompt = re.sub(r"\s+", " ", prompt)

    return {
        "prompt": prompt,
        "refs": refs[:5],
        "aspect": aspect_for_slot(slot, page_aspect),
        "warnings": warnings,
    }
