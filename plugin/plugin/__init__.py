import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, UploadFile
from pydantic import BaseModel


class NoteType(str, Enum):
    NOTE = "note"
    PROJECT = "project"
    PERSON = "person"
    DIARY = "diary"


class NoteInfo(BaseModel):
    path: str
    folder: str
    filename: str
    size: int
    modified: str


class NoteResponse(BaseModel):
    title: str
    path: str
    type: NoteType
    url: str

STORY_ANALYZE_SYSTEM = """You are AnotherMe's story analyzer. Analyze this personal story/memory.

Respond ONLY with a JSON object:
{
  "tags": ["lowercase-tags"],
  "mood": "one word or null",
  "people": ["names mentioned"],
  "summary": "first-person reflection paragraph with [[wikilinks]]"
}"""


def _parse_json(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```\s*$", "", text)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}


class StoriesApi:
    """Stories domain methods (moved from src/vault.py). Registered as 'stories' API."""

    def __init__(self, vault_manager):
        self._vault = vault_manager

    def create_story(self, vault_name, title, content, tags=None, audio_path=None):
        parts = [f"# {title}", "", content]
        if audio_path:
            parts.append(f"\n## Audio\n![[{audio_path}]]")
        return self._vault.create_note(vault_name, title, "\n".join(parts), tags, "story", {}, "Stories")

    def save_story_audio(self, vault_name, audio_bytes, filename):
        vault = self._vault.vault_path(vault_name)
        audio_dir = vault / "Stories" / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
        ext = Path(filename).suffix or ".webm"
        filepath = audio_dir / f"{ts}{ext}"
        filepath.write_bytes(audio_bytes)
        return str(filepath.relative_to(vault))

    def consume_seeds(self, vault_name, content):
        seeds_path = self._vault.vault_path(vault_name) / "System" / "SEEDS.md"
        if not seeds_path.is_file():
            return []

        text = seeds_path.read_text(encoding="utf-8")
        content_lower = content.lower()
        resolved = []
        lines = text.split("\n")
        changed = False

        for i, line in enumerate(lines):
            if "- [ ] " in line:
                question = line.split("- [ ] ", 1)[1].strip()
                keywords = [w for w in question.lower().split() if len(w) > 3]
                if keywords and all(kw in content_lower for kw in keywords[:3]):
                    lines[i] = line.replace("- [ ] ", "- [x] ")
                    resolved.append(question)
                    changed = True

        if changed:
            seeds_path.write_text("\n".join(lines), encoding="utf-8")

        return resolved


class Plugin:
    def on_load(self, ctx):
        self._vault = ctx.vault_manager
        self._llm = ctx.llm_client
        self._ctx = ctx
        vault = self._vault

        self._api = StoriesApi(vault)
        ctx.register_api("stories", self._api)

        router = APIRouter()

        @router.post("", status_code=201, response_model=NoteResponse)
        async def create_story(
            title: str = Form(..., min_length=1),
            content: str = Form(...),
            tags: str | None = Form(None),
            audio: UploadFile | None = None,
        ):
            vn = self._ctx.vault_name
            tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
            audio_path = None
            if audio and audio.filename:
                audio_bytes = await audio.read()
                if len(audio_bytes) > 0:
                    audio_path = self._api.save_story_audio(vn, audio_bytes, audio.filename)
            rel = self._api.create_story(vn, title, content, tag_list, audio_path)
            asyncio.create_task(self._analyze_and_update_story(vn, title, content, str(rel)))
            return NoteResponse(title=title, path=str(rel), type="note", url=f"/stories/{title}")

        @router.get("", response_model=list[NoteInfo])
        async def list_stories():
            v = vault.vault_path(self._ctx.vault_name)
            stories_dir = v / "Stories"
            if not stories_dir.is_dir():
                return []
            notes = []
            for f in sorted(stories_dir.glob("*.md"), reverse=True):
                st = f.stat()
                notes.append({"path": str(f.relative_to(v)), "folder": "Stories", "filename": f.name,
                              "size": st.st_size,
                              "modified": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()})
            return [NoteInfo(**n) for n in notes]

        @router.post("/generate", status_code=201)
        async def generate_story(body: dict = None):
            vn = self._ctx.vault_name
            seed_question = (body or {}).get("seed_question")
            messages = ctx.context_builder.build_story_context(seed_question, vn)
            response = await ctx.llm_client.chat(messages=messages, caller="stories")
            title = (body or {}).get("title", "Guided Memory")
            rel = self._api.create_story(vn, title, response, [])
            return {"title": title, "path": str(rel), "content": response}

        @router.get("/{story_path:path}")
        async def read_story(story_path: str):
            vn = self._ctx.vault_name
            try:
                safe_path = vault.safe_vault_path(vn, story_path)
            except ValueError:
                raise HTTPException(404, "Story not found")
            if not safe_path.is_file():
                raise HTTPException(404, "Story not found")
            content = safe_path.read_text(encoding="utf-8")
            return {"content": content, "path": story_path}

        @router.delete("/{story_path:path}")
        async def delete_story(story_path: str):
            vn = self._ctx.vault_name
            if not story_path.startswith("Stories/"):
                raise HTTPException(403, "Can only delete stories")
            try:
                safe_path = vault.safe_vault_path(vn, story_path)
            except ValueError:
                raise HTTPException(404, "Story not found")
            if not safe_path.is_file():
                raise HTTPException(404, "Story not found")
            safe_path.unlink()
            return {"ok": True}

        ctx.register_router(router)

    async def _analyze_and_update_story(self, vault_name, title, content, filepath):
        try:
            response_text = await self._llm.chat(
                messages=[
                    {"role": "system", "content": STORY_ANALYZE_SYSTEM},
                    {"role": "user", "content": f"Title: {title}\n\nContent:\n{content}"},
                ],
                caller="stories",
            )
            analysis = _parse_json(response_text)
            await asyncio.to_thread(self._apply_story_analysis, vault_name, filepath, analysis)
        except Exception:
            logging.getLogger(__name__).exception("Story analysis failed")

    def _apply_story_analysis(self, vault_name, rel_path, analysis):
        vault = self._vault.vault_path(vault_name)
        filepath = vault / rel_path
        if not filepath.is_file():
            return

        text = filepath.read_text(encoding="utf-8")

        analysis_parts = ["\n\n## AI Analysis\n"]
        if analysis.get("mood"):
            analysis_parts.append(f"**Mood:** {analysis['mood']}\n\n")
        if analysis.get("tags"):
            analysis_parts.append(f"**Tags:** {', '.join(analysis['tags'])}\n\n")
        if analysis.get("summary"):
            analysis_parts.append(analysis["summary"])
        else:
            analysis_parts.append("_No summary extracted._")

        filepath.write_text(text + "".join(analysis_parts), encoding="utf-8")

        memory_api = self._ctx.get_plugin_api("memory")
        if memory_api:
            for person_name in analysis.get("people") or []:
                if person_name and isinstance(person_name, str):
                    memory_api.save_person(vault_name, person_name, "", "", [])
