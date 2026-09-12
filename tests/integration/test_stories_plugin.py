"""Stories plugin integration tests (moved from the AnotherMe host repo,
tests/test_plugins/test_leaf_plugins.py)."""
import json
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def client(make_client):
    return make_client()


def _get_stories_plugin():
    import src.main
    return src.main.plugin_manager._plugins["stories"]["instance"]


def test_stories_create_and_list(client):
    resp = client.post("/plugins/stories", data={
        "title": "My Childhood", "content": "Once upon a time.", "tags": "family, history",
    })
    assert resp.status_code == 201
    assert resp.json()["title"] == "My Childhood"

    listed = client.get("/plugins/stories")
    assert listed.status_code == 200
    assert any("My-Childhood" in n["filename"] or "My Childhood" in n["filename"] for n in listed.json())


def test_stories_create_with_audio(client):
    resp = client.post(
        "/plugins/stories",
        data={"title": "Audio Tale", "content": "Spoken words."},
        files={"audio": ("tale.webm", b"\x1a\x45\xdf\xa3fakeaudio", "audio/webm")},
    )
    assert resp.status_code == 201
    vault = Path(os.environ["VAULTS_DIR"]) / "test-main"
    assert list((vault / "Stories" / "audio").glob("*.webm"))


def test_stories_create_with_empty_audio_skips_file(client):
    vault = Path(os.environ["VAULTS_DIR"]) / "test-main"
    audio_dir = vault / "Stories" / "audio"
    before = list(audio_dir.glob("*.webm")) if audio_dir.is_dir() else []
    resp = client.post(
        "/plugins/stories",
        data={"title": "Silent Night", "content": "No audio here."},
        files={"audio": ("empty.webm", b"", "audio/webm")},
    )
    assert resp.status_code == 201
    after = list(audio_dir.glob("*.webm")) if audio_dir.is_dir() else []
    assert after == before


def test_stories_ai_analysis_updates_markdown(client):
    analysis_json = json.dumps({
        "tags": ["family", "childhood"],
        "mood": "nostalgic",
        "people": ["Alice"],
        "summary": "A fond memory of [[Alice]].",
    })
    with patch("src.llm.chat", new_callable=AsyncMock, return_value=analysis_json):
        resp = client.post("/plugins/stories", data={
            "title": "Memory Lane", "content": "Walking down the old street.",
        })
        assert resp.status_code == 201

        vault = Path(os.environ["VAULTS_DIR"]) / "test-main"
        story_files = list((vault / "Stories").glob("*.md"))
        assert len(story_files) >= 1
        target = next(f for f in story_files if "Memory-Lane" in f.name or "Memory Lane" in f.name)

        content = ""
        for _ in range(50):
            content = target.read_text(encoding="utf-8")
            if "## AI Analysis" in content:
                break
            client.get("/health")
            time.sleep(0.1)

    assert "## AI Analysis" in content
    assert "nostalgic" in content
    assert "family" in content
    assert "[[Alice]]" in content


def test_stories_read_returns_content(client):
    client.post("/plugins/stories", data={
        "title": "Readable Tale", "content": "Hello world from the story.",
    })
    vault = Path(os.environ["VAULTS_DIR"]) / "test-main"
    story_files = list((vault / "Stories").glob("*.md"))
    target = next(f for f in story_files if "Readable-Tale" in f.name or "Readable Tale" in f.name)
    rel_path = str(target.relative_to(vault))

    resp = client.get(f"/plugins/stories/{rel_path}")
    assert resp.status_code == 200
    assert "Hello world from the story." in resp.json()["content"]
    assert resp.json()["path"] == rel_path


def test_stories_read_404_for_nonexistent(client):
    assert client.get("/plugins/stories/Stories/DoesNotExist.md").status_code == 404


def test_stories_generate_creates_story_with_ai_content(client):
    with patch("src.llm.chat", new_callable=AsyncMock, return_value="Once upon a time in Prague."):
        resp = client.post("/plugins/stories/generate", json={
            "title": "AI Memory",
            "seed_question": "Tell me about Prague",
        })
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "AI Memory"
    assert "Prague" in data["content"]

    vault = Path(os.environ["VAULTS_DIR"]) / "test-main"
    story_files = list((vault / "Stories").glob("*.md"))
    assert any("AI-Memory" in f.name or "AI Memory" in f.name for f in story_files)


def test_delete_story(client):
    client.post("/plugins/stories", data={
        "title": "Delete Me", "content": "Goodbye story.",
    })
    stories = client.get("/plugins/stories").json()
    assert len(stories) > 0
    path = stories[0]["path"]
    resp = client.delete(f"/plugins/stories/{path}")
    assert resp.status_code == 200
    stories = client.get("/plugins/stories").json()
    assert len(stories) == 0
