function storiesPlugin() {
    return {
        title: '',
        content: '',
        list: [],
        saving: false,
        audioBlob: null,
        selectedStory: null,
        storyContent: '',

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
                AM.toast('Story saved' + (this.audioBlob ? ' with audio' : ''), 'success');
                this.title = '';
                this.content = '';
                this.audioBlob = null;
                await this.loadStoriesList();
                return true;
            } catch (e) { AM.toast('Failed: ' + e.message, 'error'); return false; }
            finally { this.saving = false; }
        },

        async loadStoriesList() {
            try {
                const resp = await AM.fetch('/plugins/stories');
                if (resp) this.list = await resp.json();
            } catch (e) { console.error('Stories loadList', e); }
        },

        async openStory(story) {
            try {
                const resp = await AM.fetch('/plugins/stories/' + encodeURIComponent(story.path));
                if (resp) {
                    const data = await resp.json();
                    this.selectedStory = story;
                    this.storyContent = data.content;
                }
            } catch (e) {
                AM.toast('Could not read story', 'error');
            }
        },

        closeStory() {
            this.selectedStory = null;
            this.storyContent = '';
        },

        async deleteStory(story) {
            if (!confirm('Delete this story?')) return;
            try {
                await AM.fetch('/plugins/stories/' + encodeURIComponent(story.path), { method: 'DELETE' });
                AM.toast('Story deleted', 'success');
                await this.loadStoriesList();
            } catch (e) { AM.toast(e.message, 'error'); }
        },

        openComposer() {
            this.title = '';
            this.content = '';
            this.audioBlob = null;
            const html = `
                <div class="settings-modal" style="width: 600px;">
                    <h3>New Story</h3>
                    <input class="story-title-input" id="story-title-field" placeholder="Story title" value="">
                    <textarea class="story-textarea" id="story-content-field" placeholder="Tell a story..."></textarea>
                    <div class="stories-actions">
                        <button class="input-bar-mic" id="story-mic-btn" aria-label="Tap to toggle or hold to record">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/></svg>
                        </button>
                        <span class="recording-timer" id="story-rec-timer" style="display:none">00:00</span>
                        <button class="save-btn stories-save-btn" id="story-save-btn">Save Story</button>
                    </div>
                </div>`;
            const m = AM.modal(html);

            const titleEl = document.getElementById('story-title-field');
            const contentEl = document.getElementById('story-content-field');
            const saveBtn = document.getElementById('story-save-btn');
            const micBtn = document.getElementById('story-mic-btn');
            const timerEl = document.getElementById('story-rec-timer');

            contentEl.addEventListener('input', (e) => AM.utils.autoResize(e.target));

            saveBtn.addEventListener('click', async () => {
                this.title = titleEl.value;
                this.content = contentEl.value;
                const ok = await this.saveStory();
                if (ok) m.close();
            });

            if (AM.voice) {
                micBtn.addEventListener('pointerdown', (e) => { e.preventDefault(); AM.voice.handleMicDown('stories', e); });
                micBtn.addEventListener('pointerup', (e) => { e.preventDefault(); AM.voice.handleMicUp('stories'); });
                micBtn.addEventListener('pointercancel', (e) => { e.preventDefault(); AM.voice.handleMicCancel('stories'); });
                micBtn.addEventListener('click', (e) => { e.preventDefault(); AM.voice.handleMicClick('stories'); });
            }

            const checkRecording = setInterval(() => {
                const root = document.getElementById('modal-root');
                if (!root || root.style.display === 'none') {
                    clearInterval(checkRecording);
                    return;
                }
                const store = Alpine.store('voice');
                if (!store) return;
                const isRec = store.target === 'stories';
                micBtn.classList.toggle('recording', isRec);
                timerEl.style.display = isRec ? '' : 'none';
                timerEl.textContent = store.time;
            }, 200);
        },
    };
}
