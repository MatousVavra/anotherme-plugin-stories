function storiesPlugin() {
    return {
        list: [],
        search: '',
        sort: 'newest',
        confirmDelete: null,
        selectedStory: null,
        storyContent: '',
        audioFile: null,
        title: '',
        content: '',
        saving: false,
        audioBlob: null,

        async init() {
            await this.loadStoriesList();
            if (AM.voice) {
                AM.voice.onTranscribe('stories', (text, blob) => {
                    this.content = this.content ? this.content + '\n' + text : text;
                    this.audioBlob = blob;
                    AM.toast('Transcribed — Save Story to keep audio', 'success');
                });
            }
            AM.onCleanup(() => {
                if (AM.voice && AM.voice.isRecording()) AM.voice.stopRecording();
            });
        },

        get filteredStories() {
            const q = this.search.trim().toLowerCase();
            let out = q ? this.list.filter((s) => s.filename.toLowerCase().includes(q)) : this.list.slice();
            if (this.sort === 'az') out.sort((a, b) => a.filename.localeCompare(b.filename));
            else out.sort((a, b) => new Date(a.modified) - new Date(b.modified));
            if (this.sort === 'newest') out.reverse();
            return out;
        },

        async loadStoriesList() {
            try {
                const resp = await AM.fetch('/plugins/stories');
                if (!resp) return;
                if (!resp.ok) {
                    AM.toast('Failed to load stories', 'error');
                    return;
                }
                this.list = await resp.json();
            } catch (e) {
                AM.toast('Failed to load stories', 'error');
            }
        },

        async openStory(story) {
            try {
                const resp = await AM.fetch('/plugins/stories/' + encodeURIComponent(story.path));
                if (!resp) return;
                if (!resp.ok) {
                    AM.toast('Story not found', 'error');
                    return;
                }
                const data = await resp.json();
                this.selectedStory = story;
                this.storyContent = data.content;
                this.audioFile = this.audioFileFromContent(data.content);
            } catch (e) {
                AM.toast('Could not read story', 'error');
            }
        },

        audioFileFromContent(content) {
            const m = (content || '').match(/!\[\[([^\]]+)\]\]/);
            if (!m) return null;
            const rel = m[1].trim();
            if (!rel.startsWith('Stories/audio/')) return null;
            const name = rel.split('/').pop();
            if (!name || name.includes('..') || name.includes('/') || name.includes('\\')) return null;
            return name;
        },

        handleReaderClick(e) {
            const el = e.target.closest('.wikilink');
            if (!el) return;
            e.preventDefault();
            if (window.AM && AM.notes && typeof AM.notes.open === 'function') AM.notes.open(el.textContent);
        },

        closeStory() {
            this.selectedStory = null;
            this.storyContent = '';
            this.audioFile = null;
        },

        requestDelete(story) {
            if (this.confirmDelete === story.path) {
                clearTimeout(this._confirmTimer);
                this.confirmDelete = null;
                this.deleteStory(story);
                return;
            }
            this.confirmDelete = story.path;
            clearTimeout(this._confirmTimer);
            this._confirmTimer = setTimeout(() => { this.confirmDelete = null; }, 3000);
        },

        async deleteStory(story) {
            try {
                const resp = await AM.fetch('/plugins/stories/' + encodeURIComponent(story.path), { method: 'DELETE' });
                if (!resp) return;
                if (!resp.ok) {
                    AM.toast('Failed to delete story', 'error');
                    return;
                }
                AM.toast('Story deleted', 'success');
                if (this.selectedStory && this.selectedStory.path === story.path) this.closeStory();
                await this.loadStoriesList();
            } catch (e) {
                AM.toast(e.message, 'error');
            }
        },

        async saveStory() {
            const title = this.title.trim();
            const content = this.content.trim();
            if (!title && !content) { AM.toast('Title or content required', 'warning'); return false; }

            this.saving = true;
            const formData = new FormData();
            formData.append('title', title || 'Untitled');
            formData.append('content', content);
            if (this.audioBlob) formData.append('audio', this.audioBlob, 'story.webm');

            try {
                const resp = await AM.fetch('/plugins/stories', { method: 'POST', body: formData });
                if (!resp) return false;
                if (!resp.ok) {
                    AM.toast('Failed to save story', 'error');
                    return false;
                }
                AM.toast('Story saved' + (this.audioBlob ? ' with audio' : ''), 'success');
                this.title = '';
                this.content = '';
                this.audioBlob = null;
                await this.loadStoriesList();
                return true;
            } catch (e) { AM.toast('Failed: ' + e.message, 'error'); return false; }
            finally { this.saving = false; }
        },

        trackGeneration(path) {
            this.list.unshift({
                path: path,
                folder: 'Stories',
                filename: path.split('/').pop(),
                size: 0,
                modified: new Date().toISOString(),
                hasAudio: false,
                generating: true,
            });
            this.pollGeneration(path);
        },

        async pollGeneration(path) {
            const started = Date.now();
            while (Date.now() - started < 90000) {
                await new Promise((resolve) => setTimeout(resolve, 4000));
                if (!this.list.some((s) => s.path === path)) return;
                try {
                    const resp = await AM.fetch('/plugins/stories/' + encodeURIComponent(path));
                    if (!resp || !resp.ok) continue;
                    const data = await resp.json();
                    if (!data.content.includes('Generating...')) {
                        const entry = this.list.find((s) => s.path === path);
                        if (entry) entry.generating = false;
                        if (this.selectedStory && this.selectedStory.path === path) this.storyContent = data.content;
                        return;
                    }
                } catch (e) { }
            }
            AM.toast('Still generating — check back shortly', 'warning');
        },

        openComposer(generate = false) {
            this.title = '';
            this.content = '';
            this.audioBlob = null;
            const html = generate ? `
                <div class="settings-modal" style="width: 600px;">
                    <h3>Generate Story</h3>
                    <input class="story-title-input" id="story-title-field" placeholder="Story title" value="Guided Memory">
                    <input class="story-seed-input" id="story-seed-field" placeholder="Optional seed question to guide the memory">
                    <div class="stories-actions">
                        <button class="save-btn stories-save-btn" id="story-generate-btn">Generate Story</button>
                    </div>
                </div>` : `
                <div class="settings-modal" style="width: 600px;">
                    <h3>New Story</h3>
                    <input class="story-title-input" id="story-title-field" placeholder="Story title" value="">
                    <textarea class="story-textarea" id="story-content-field" placeholder="Tell a story..."></textarea>
                    <div class="stories-actions">
                        <button
                            class="input-bar-mic"
                            x-show="AM.isPluginEnabled('stt')"
                            aria-label="Tap to toggle or hold to record"
                            @pointerdown.prevent="AM.voice && AM.voice.handleMicDown('stories', $event)"
                            @pointerup.prevent="AM.voice && AM.voice.handleMicUp('stories')"
                            @pointercancel.prevent="AM.voice && AM.voice.handleMicCancel('stories')"
                            @click.prevent="AM.voice && AM.voice.handleMicClick('stories')"
                            :class="{ recording: $store.voice && $store.voice.target === 'stories' }"
                        >
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/></svg>
                        </button>
                        <span
                            class="recording-timer"
                            style="display:none"
                            x-show="$store.voice && $store.voice.target === 'stories'"
                            x-text="$store.voice ? $store.voice.time : '00:00'"
                        >00:00</span>
                        <button class="save-btn stories-save-btn" id="story-save-btn">Save Story</button>
                    </div>
                </div>`;
            const m = AM.modal(html);
            const titleEl = document.getElementById('story-title-field');
            titleEl.focus();
            if (generate) {
                titleEl.select();
                this.wireGenerateModal(m, titleEl);
            } else {
                this.wireComposeModal(m, titleEl);
            }
        },

        wireComposeModal(m, titleEl) {
            const contentEl = document.getElementById('story-content-field');
            const saveBtn = document.getElementById('story-save-btn');

            const submit = async () => {
                if (saveBtn.disabled) return;
                saveBtn.disabled = true;
                saveBtn.textContent = 'Saving...';
                this.title = titleEl.value;
                this.content = contentEl.value;
                const ok = await this.saveStory();
                saveBtn.disabled = false;
                saveBtn.textContent = 'Save Story';
                if (ok) m.close();
            };

            contentEl.addEventListener('input', (e) => AM.utils.autoResize(e.target));
            titleEl.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });
            saveBtn.addEventListener('click', submit);
        },

        wireGenerateModal(m, titleEl) {
            const seedEl = document.getElementById('story-seed-field');
            const genBtn = document.getElementById('story-generate-btn');

            const submit = async () => {
                if (genBtn.disabled) return;
                const title = titleEl.value.trim() || 'Guided Memory';
                genBtn.disabled = true;
                genBtn.textContent = 'Generating...';
                try {
                    const resp = await AM.fetch('/plugins/stories/generate', {
                        method: 'POST',
                        body: { title: title, seed_question: seedEl.value.trim() || null },
                    });
                    if (!resp) return;
                    if (!resp.ok) {
                        AM.toast('Failed to generate story', 'error');
                        return;
                    }
                    const data = await resp.json();
                    m.close();
                    this.trackGeneration(data.path);
                } catch (e) {
                    AM.toast('Failed: ' + e.message, 'error');
                } finally {
                    genBtn.disabled = false;
                    genBtn.textContent = 'Generate Story';
                }
            };

            titleEl.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });
            seedEl.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });
            genBtn.addEventListener('click', submit);
        },
    };
}
