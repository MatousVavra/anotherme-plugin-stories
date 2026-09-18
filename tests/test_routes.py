import asyncio
import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import load_plugin_module
from fake_plugin_context import FakePluginContext


class FakeVault:
    def __init__(self, root: Path):
        self.root = root

    def vault_path(self, vn):
        return self.root

    def safe_vault_path(self, vn, rel):
        p = (self.root / rel).resolve()
        p.relative_to(self.root.resolve())
        return p

    def create_note(self, vn, title, content, tags=None, note_type="note",
                    extra_frontmatter=None, subfolder=None):
        target_dir = self.root / (subfolder or "Notes")
        target_dir.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^\w-]+", "-", title).strip("-") or "note"
        filepath = target_dir / f"{name}.md"
        body = f"---\ntitle: {title}\ntype: {note_type}\n---\n\n{content}"
        filepath.write_text(body, encoding="utf-8")
        return filepath.relative_to(self.root)


def _client(tmp_path: Path):
    (tmp_path / "Stories").mkdir(exist_ok=True)
    ctx = FakePluginContext(vault_manager=FakeVault(tmp_path))
    mod = load_plugin_module()
    mod.Plugin().on_load(ctx)
    app = FastAPI()
    for _name, router in ctx.registry.routers:
        app.include_router(router, prefix="/plugins/stories")
    return TestClient(app)


class FakeContextBuilder:
    def build_story_context(self, seed_question, vault_name):
        return [{"role": "user", "content": seed_question or "tell me a story"}]


class FakeLLM:
    def __init__(self, response, delay=0.0):
        self.response = response
        self.delay = delay

    async def chat(self, messages, caller):
        await asyncio.sleep(self.delay)
        return self.response


def test_read_story_rejects_paths_outside_stories(tmp_path):
    (tmp_path / "Secrets.md").write_text("secret", encoding="utf-8")
    client = _client(tmp_path)
    assert client.get("/plugins/stories/Secrets.md").status_code == 403
    assert client.get("/plugins/stories/System/SEEDS.md").status_code == 403


def test_read_story_rejects_non_markdown(tmp_path):
    client = _client(tmp_path)
    assert client.get("/plugins/stories/Stories/audio/x.webm").status_code == 404


def test_read_story_missing_returns_404(tmp_path):
    client = _client(tmp_path)
    assert client.get("/plugins/stories/Stories/none.md").status_code == 404


def test_read_story_returns_content(tmp_path):
    (tmp_path / "Stories").mkdir(exist_ok=True)
    (tmp_path / "Stories" / "s.md").write_text("# Story\n", encoding="utf-8")
    client = _client(tmp_path)
    resp = client.get("/plugins/stories/Stories/s.md")
    assert resp.status_code == 200
    assert resp.json()["content"] == "# Story\n"


def test_list_stories_reports_has_audio(tmp_path):
    stories = tmp_path / "Stories"
    stories.mkdir(exist_ok=True)
    (stories / "a.md").write_text("# A\n\nno audio", encoding="utf-8")
    (stories / "audio").mkdir()
    (stories / "audio" / "x.webm").write_bytes(b"audio")
    (stories / "b.md").write_text("# B\n\n![[Stories/audio/x.webm]]", encoding="utf-8")
    client = _client(tmp_path)
    data = client.get("/plugins/stories").json()
    by_file = {n["filename"]: n for n in data}
    assert by_file["a.md"]["hasAudio"] is False
    assert by_file["b.md"]["hasAudio"] is True


def test_create_story_rejects_non_audio_upload(tmp_path):
    client = _client(tmp_path)
    resp = client.post(
        "/plugins/stories",
        data={"title": "T", "content": "C"},
        files={"audio": ("img.png", b"png", "image/png")},
    )
    assert resp.status_code == 400


def test_create_story_rejects_oversized_audio(tmp_path):
    client = _client(tmp_path)
    resp = client.post(
        "/plugins/stories",
        data={"title": "T", "content": "C"},
        files={"audio": ("big.webm", b"x" * (25 * 1024 * 1024 + 1), "audio/webm")},
    )
    assert resp.status_code == 413


def test_story_audio_serves_saved_file(tmp_path):
    audio_dir = tmp_path / "Stories" / "audio"
    audio_dir.mkdir(parents=True)
    (audio_dir / "clip.webm").write_bytes(b"audio-bytes")
    client = _client(tmp_path)
    resp = client.get("/plugins/stories/audio/clip.webm")
    assert resp.status_code == 200
    assert resp.content == b"audio-bytes"
    assert resp.headers["content-type"].startswith("audio/webm")


def test_story_audio_missing_returns_404(tmp_path):
    client = _client(tmp_path)
    assert client.get("/plugins/stories/audio/none.webm").status_code == 404


def test_story_audio_rejects_path_traversal(tmp_path):
    (tmp_path / "secret.webm").write_bytes(b"secret")
    (tmp_path / "Stories" / "audio").mkdir(parents=True)
    (tmp_path / "Stories" / "audio" / "real.webm").write_bytes(b"real")
    client = _client(tmp_path)
    attacks = [
        "..%2Fsecret.webm",
        "..%2F..%2Fsecret.webm",
        "..%5Csecret.webm",
        "....webm",
        "..real.webm",
        "real.webm%2F..%2F..%2Fsecret.webm",
    ]
    for attack in attacks:
        resp = client.get(f"/plugins/stories/audio/{attack}")
        assert resp.status_code in (400, 403, 404), f"{attack} -> {resp.status_code}"
        assert resp.content != b"secret"


def _generate_client(tmp_path: Path, response, delay=0.0):
    ctx = FakePluginContext(
        vault_manager=FakeVault(tmp_path),
        llm_client=FakeLLM(response, delay),
        context_builder=FakeContextBuilder(),
    )
    mod = load_plugin_module()
    plugin = mod.Plugin()
    plugin.on_load(ctx)
    app = FastAPI()
    for _name, router in ctx.registry.routers:
        app.include_router(router, prefix="/plugins/stories")
    return TestClient(app), plugin


def test_generate_creates_placeholder_story(tmp_path):
    client, _plugin = _generate_client(tmp_path, "Once upon a time.", delay=10)
    resp = client.post("/plugins/stories/generate", json={"title": "AI Memory", "seed_question": "Prague"})
    assert resp.status_code == 202
    data = resp.json()
    assert data["title"] == "AI Memory"
    assert data["path"].replace("\\", "/").startswith("Stories/")
    content = (tmp_path / data["path"]).read_text(encoding="utf-8")
    assert "Generating..." in content


def test_generate_uses_default_title(tmp_path):
    client, _plugin = _generate_client(tmp_path, "Once upon a time.", delay=10)
    resp = client.post("/plugins/stories/generate", json={})
    assert resp.status_code == 202
    assert resp.json()["title"] == "Guided Memory"


def test_fill_generated_story_replaces_placeholder(tmp_path):
    client, plugin = _generate_client(tmp_path, "Once upon a time in Prague.")
    resp = client.post("/plugins/stories/generate", json={"title": "AI Memory", "seed_question": "Prague"})
    rel = resp.json()["path"]
    asyncio.run(plugin._fill_generated_story("main", rel, "Prague"))
    content = (tmp_path / rel).read_text(encoding="utf-8")
    assert "Once upon a time in Prague." in content
    assert "Generating..." not in content
    assert content.startswith("---")


def test_fill_generated_story_failure_leaves_message(tmp_path):
    client, plugin = _generate_client(tmp_path, None)
    resp = client.post("/plugins/stories/generate", json={"title": "AI Memory"})
    rel = resp.json()["path"]
    asyncio.run(plugin._fill_generated_story("main", rel, None))
    content = (tmp_path / rel).read_text(encoding="utf-8")
    assert "generation failed" in content
    assert "Generating..." not in content


def test_fill_generated_story_skips_deleted_note(tmp_path):
    client, plugin = _generate_client(tmp_path, "Once upon a time.")
    resp = client.post("/plugins/stories/generate", json={"title": "AI Memory"})
    rel = resp.json()["path"]
    (tmp_path / rel).unlink()
    asyncio.run(plugin._fill_generated_story("main", rel, None))
