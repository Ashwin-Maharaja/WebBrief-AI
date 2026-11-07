/**
 * WebBrief AI (v1.5) — Deployment-Aware Storage & Hosted BYOK Adapter
 *
 * ARCHITECTURE:
 * - Local Self-Hosted: Uses local SQLite database backend (/api/history).
 * - Hosted Serverless (Vercel): Uses browser IndexedDB ('WebBriefDB').
 *   Visitor summaries are kept strictly within their own browser.
 *   Zero cross-user leakage, zero server-side database requirements.
 *
 * CREDENTIAL SECURITY (PART B):
 * - Hosted API keys are kept strictly in JavaScript variable closure (RAM only).
 * - NEVER stored in localStorage, sessionStorage, IndexedDB, or cookies.
 * - Automatically purged on tab close/unload or user reset.
 */

(function(window) {
    'use strict';

    // Private in-memory key storage (never written to web storage)
    let _inMemoryApiKey = '';
    let _inMemoryProviderConfig = {
        primary_provider: 'cloudflare',
        cloudflare_account_id: '',
        cloudflare_api_token: '',
        cloudflare_model: '@cf/meta/llama-3.1-8b-instruct',
        groq_api_key: '',
        groq_model: 'llama-3.3-70b-versatile',
        fallback_enabled: false,
        fallback_provider: 'groq',
        fallback_model: 'llama-3.3-70b-versatile'
    };

    // Database configuration for Hosted IndexedDB mode
    const DB_NAME = 'WebBriefDB';
    const DB_VERSION = 2;
    const STORE_NAME = 'summaries';
    const JOBS_STORE = 'jobs';
    const CHUNKS_STORE = 'job_chunks';

    let _dbPromise = null;

    /**
     * Initializes and returns the IndexedDB instance for hosted browser storage.
     * Gracefully handles private browsing restrictions and storage blockades.
     */
    function getIndexedDB() {
        if (_dbPromise) return _dbPromise;

        _dbPromise = new Promise((resolve, reject) => {
            if (!window.indexedDB) {
                console.warn('[WebBrief AI] IndexedDB not supported by this browser. Falling back to in-memory history.');
                resolve(null);
                return;
            }

            try {
                const request = window.indexedDB.open(DB_NAME, DB_VERSION);

                request.onupgradeneeded = function(event) {
                    const db = event.target.result;
                    if (!db.objectStoreNames.contains(STORE_NAME)) {
                        const store = db.createObjectStore(STORE_NAME, { keyPath: 'id' });
                        store.createIndex('created_at', 'created_at', { unique: false });
                        store.createIndex('mode', 'mode', { unique: false });
                        store.createIndex('entry_type', 'entry_type', { unique: false });
                        store.createIndex('url', 'url', { unique: false });
                    }
                    if (!db.objectStoreNames.contains(JOBS_STORE)) {
                        const jobStore = db.createObjectStore(JOBS_STORE, { keyPath: 'id' });
                        jobStore.createIndex('updated_at', 'updated_at', { unique: false });
                        jobStore.createIndex('status', 'status', { unique: false });
                    }
                    if (!db.objectStoreNames.contains(CHUNKS_STORE)) {
                        const chunkStore = db.createObjectStore(CHUNKS_STORE, { keyPath: 'id' });
                        chunkStore.createIndex('job_id', 'job_id', { unique: false });
                        chunkStore.createIndex('job_and_index', ['job_id', 'chunk_index'], { unique: true });
                    }
                };

                request.onsuccess = function(event) {
                    resolve(event.target.result);
                };

                request.onerror = function(event) {
                    console.warn('[WebBrief AI] IndexedDB open error:', event.target.error);
                    resolve(null);
                };
            } catch (err) {
                console.warn('[WebBrief AI] IndexedDB access restricted:', err);
                resolve(null);
            }
        });

        return _dbPromise;
    }

    /**
     * Computes mathematical cosine similarity between two float vectors.
     */
    function computeCosineSimilarity(v1, v2) {
        if (!v1 || !v2 || v1.length !== v2.length || v1.length === 0) return 0.0;
        let dot = 0.0, n1 = 0.0, n2 = 0.0;
        for (let i = 0; i < v1.length; i++) {
            dot += v1[i] * v2[i];
            n1 += v1[i] * v1[i];
            n2 += v2[i] * v2[i];
        }
        if (n1 <= 0.0 || n2 <= 0.0) return 0.0;
        return dot / (Math.sqrt(n1) * Math.sqrt(n2));
    }

    /**
     * Extracts a relevant excerpt from text for search snippet display.
     */
    function extractSnippet(text, query, maxLen = 220) {
        if (!text) return '';
        const words = (query || '').toLowerCase().split(/\s+/).filter(w => w.length > 2);
        const paragraphs = text.split('\n').map(p => p.trim()).filter(Boolean);
        let best = paragraphs[0] || text;
        let bestScore = -1;

        for (const p of paragraphs) {
            const pLower = p.toLowerCase();
            let score = 0;
            for (const w of words) {
                if (pLower.includes(w)) score++;
            }
            if (score > bestScore) {
                bestScore = score;
                best = p;
            }
        }

        const clean = best.replace(/^(Overview|Key Takeaways|Important Details|Conclusion|Synthesis):\s*/i, '');
        if (clean.length > maxLen) {
            return clean.substring(0, maxLen).replace(/\s\S*$/, '') + '...';
        }
        return clean;
    }

    // Public Storage & Credential Adapter API
    const StorageAdapter = {
        /**
         * Checks whether the current deployment is running in hosted serverless mode.
         */
        isHosted: function() {
            return Boolean(window.__WEBBRIEF_IS_HOSTED);
        },

        // -------------------------------------------------------------
        // In-Memory Key Management (PART A & B)
        // -------------------------------------------------------------
        setHostedKey: function(key) {
            if (typeof key === 'string') {
                _inMemoryApiKey = key.trim();
            }
        },

        getHostedKey: function() {
            return _inMemoryApiKey;
        },

        clearHostedKey: function() {
            _inMemoryApiKey = '';
        },

        hasHostedKey: function() {
            return this.hasValidCredentials();
        },

        setProviderConfig: function(cfg) {
            if (cfg && typeof cfg === 'object') {
                _inMemoryProviderConfig = Object.assign({}, _inMemoryProviderConfig, cfg);
                if (cfg.groq_api_key) _inMemoryApiKey = cfg.groq_api_key;
                else if (cfg.cloudflare_api_token) _inMemoryApiKey = cfg.cloudflare_api_token;
            }
        },

        getProviderConfig: function() {
            return Object.assign({}, _inMemoryProviderConfig);
        },

        clearCredentials: function() {
            _inMemoryApiKey = '';
            _inMemoryProviderConfig.cloudflare_account_id = '';
            _inMemoryProviderConfig.cloudflare_api_token = '';
            _inMemoryProviderConfig.groq_api_key = '';
        },

        hasValidCredentials: function() {
            const p = _inMemoryProviderConfig.primary_provider || 'cloudflare';
            if (p === 'cloudflare') {
                return Boolean(_inMemoryProviderConfig.cloudflare_account_id && _inMemoryProviderConfig.cloudflare_api_token);
            } else if (p === 'groq') {
                return Boolean(_inMemoryProviderConfig.groq_api_key);
            }
            return Boolean(_inMemoryApiKey && _inMemoryApiKey.length >= 10);
        },

        /**
         * Returns the active API key to send with an API request.
         * In hosted mode: reads from memory.
         * In local mode: checks local override input or returns null (uses server .env).
         */
        getActiveApiKey: function(fallbackInputId = 'api-key-input') {
            if (this.isHosted()) {
                if (_inMemoryApiKey) return _inMemoryApiKey;
                const hostedInput = document.getElementById('hosted-api-key-input');
                if (hostedInput && hostedInput.value.trim()) {
                    return hostedInput.value.trim();
                }
            }
            const localInput = document.getElementById(fallbackInputId);
            if (localInput && localInput.value.trim()) {
                return localInput.value.trim();
            }
            return _inMemoryApiKey || null;
        },

        // -------------------------------------------------------------
        // Summary History Storage (PART C & D)
        // -------------------------------------------------------------
        /**
         * Saves a generated summary or comparative brief into history.
         * In Hosted mode: persists into browser IndexedDB.
         * In Local mode: handled by Flask backend into SQLite.
         */
        saveSummary: async function(item) {
            if (!this.isHosted()) {
                return item; // In local mode, backend already recorded into SQLite
            }

            try {
                const db = await getIndexedDB();
                if (!db) return item;

                // Ensure required schema fields
                const entryId = item.history_id || item.id || ('idb_' + Date.now() + '_' + Math.random().toString(36).substring(2, 9));
                const now = new Date();
                const record = {
                    id: entryId,
                    title: item.title || (item.summary && item.summary.title) || 'Webpage Summary',
                    url: item.url || '',
                    mode: item.mode || 'quick',
                    mode_name: item.mode_name || 'Summary',
                    summary: item.summary || item,
                    created_at: now.toISOString(),
                    created_at_formatted: item.created_at_formatted || now.toLocaleDateString('en-US', {
                        month: 'short', day: 'numeric', year: 'numeric'
                    }) + ' • ' + now.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
                    entry_type: item.entry_type || 'single',
                    url_b: item.url_b || null,
                    title_b: item.title_b || null,
                    vector: item.vector || null // Optional cached vector
                };

                return new Promise((resolve) => {
                    const tx = db.transaction(STORE_NAME, 'readwrite');
                    const store = tx.objectStore(STORE_NAME);
                    store.put(record);
                    tx.oncomplete = () => resolve(record);
                    tx.onerror = () => resolve(item);
                });
            } catch (err) {
                console.warn('[WebBrief AI] Failed to save summary to IndexedDB:', err);
                return item;
            }
        },

        /**
         * Retrieves the list of saved summaries.
         */
        getHistory: async function(limit = 50) {
            if (!this.isHosted()) {
                const res = await fetch('/api/history?limit=' + limit);
                if (!res.ok) return [];
                const data = await res.json();
                return data.history || [];
            }

            try {
                const db = await getIndexedDB();
                if (!db) return [];

                return new Promise((resolve) => {
                    const tx = db.transaction(STORE_NAME, 'readonly');
                    const store = tx.objectStore(STORE_NAME);
                    const req = store.getAll();

                    req.onsuccess = function() {
                        const items = req.result || [];
                        items.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
                        resolve(items.slice(0, limit));
                    };
                    req.onerror = function() {
                        resolve([]);
                    };
                });
            } catch (err) {
                console.warn('[WebBrief AI] Failed to read IndexedDB history:', err);
                return [];
            }
        },

        /**
         * Retrieves an individual summary by ID.
         */
        getEntry: async function(id) {
            if (!this.isHosted()) {
                const res = await fetch('/api/history/' + encodeURIComponent(id));
                if (!res.ok) return null;
                const data = await res.json();
                return data.entry || null;
            }

            try {
                const db = await getIndexedDB();
                if (!db) return null;

                return new Promise((resolve) => {
                    const tx = db.transaction(STORE_NAME, 'readonly');
                    const store = tx.objectStore(STORE_NAME);
                    const req = store.get(id);

                    req.onsuccess = function() {
                        resolve(req.result || null);
                    };
                    req.onerror = function() {
                        resolve(null);
                    };
                });
            } catch (err) {
                return null;
            }
        },

        /**
         * Deletes an individual summary by ID.
         */
        deleteEntry: async function(id) {
            if (!this.isHosted()) {
                const res = await fetch('/api/history/' + encodeURIComponent(id), { method: 'DELETE' });
                return res.ok;
            }

            try {
                const db = await getIndexedDB();
                if (!db) return false;

                return new Promise((resolve) => {
                    const tx = db.transaction(STORE_NAME, 'readwrite');
                    const store = tx.objectStore(STORE_NAME);
                    store.delete(id);
                    tx.oncomplete = () => resolve(true);
                    tx.onerror = () => resolve(false);
                });
            } catch (err) {
                return false;
            }
        },

        /**
         * Clears all saved summaries.
         */
        clearHistory: async function() {
            if (!this.isHosted()) {
                const res = await fetch('/api/history/clear', { method: 'POST' });
                return res.ok;
            }

            try {
                const db = await getIndexedDB();
                if (!db) return false;

                return new Promise((resolve) => {
                    const tx = db.transaction(STORE_NAME, 'readwrite');
                    const store = tx.objectStore(STORE_NAME);
                    store.clear();
                    tx.oncomplete = () => resolve(true);
                    tx.onerror = () => resolve(false);
                });
            } catch (err) {
                return false;
            }
        },

        /**
         * Returns total count of saved summaries.
         */
        count: async function() {
            if (!this.isHosted()) {
                const items = await this.getHistory(1);
                return items.length;
            }

            try {
                const db = await getIndexedDB();
                if (!db) return 0;

                return new Promise((resolve) => {
                    const tx = db.transaction(STORE_NAME, 'readonly');
                    const store = tx.objectStore(STORE_NAME);
                    const req = store.count();
                    req.onsuccess = () => resolve(req.result || 0);
                    req.onerror = () => resolve(0);
                });
            } catch (err) {
                return 0;
            }
        },

        /**
         * Unified semantic & keyword search across saved summaries.
         * In Local mode: executes via /api/search/semantic.
         * In Hosted mode: performs client-side search over IndexedDB. If an API key is available,
         * embeds the query via /api/search/embed and ranks by cosine similarity.
         */
        search: async function(query, threshold = 0.35, filterType = 'all', apiKey = null) {
            const cleanQuery = (query || '').trim();
            if (!cleanQuery) return { count: 0, results: [] };

            if (!this.isHosted()) {
                const res = await fetch('/api/search/semantic', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        query: cleanQuery,
                        threshold: threshold,
                        filter_type: filterType,
                        api_key: apiKey
                    })
                });
                if (!res.ok) {
                    const err = await res.json();
                    throw new Error(err.error || 'Search failed');
                }
                return await res.json();
            }

            // Hosted Mode: Query IndexedDB records
            const items = await this.getHistory(500);
            if (items.length === 0) {
                return { count: 0, query: cleanQuery, threshold: threshold, results: [] };
            }

            // Filter items by type
            const filtered = items.filter(it => {
                if (filterType && filterType !== 'all') {
                    return (it.entry_type || 'single') === filterType;
                }
                return true;
            });

            // Try vector search if user has key configured
            const activeKey = apiKey || this.getActiveApiKey();
            let queryVector = null;

            if (activeKey) {
                try {
                    const embedRes = await fetch('/api/search/embed', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ text: cleanQuery, api_key: activeKey })
                    });
                    if (embedRes.ok) {
                        const embedData = await embedRes.json();
                        queryVector = embedData.embedding;
                    }
                } catch (e) {
                    console.debug('[WebBrief AI] Could not embed query for hosted vector search:', e);
                }
            }

            const scored = [];
            for (const item of filtered) {
                const sum = item.summary || {};
                const docText = [
                    item.title || '',
                    sum.overview || '',
                    Array.isArray(sum.key_points) ? sum.key_points.join(' ') : '',
                    Array.isArray(sum.important_details) ? sum.important_details.join(' ') : '',
                    sum.conclusion || '',
                    sum.synthesis || ''
                ].join('\n');

                let sim = 0.0;
                if (queryVector && item.vector && Array.isArray(item.vector)) {
                    sim = computeCosineSimilarity(queryVector, item.vector);
                } else {
                    // Keyword / Lexical relevance scoring fallback
                    const qWords = cleanQuery.toLowerCase().split(/\s+/).filter(w => w.length > 2);
                    const docLower = docText.toLowerCase();
                    let matches = 0;
                    for (const w of qWords) {
                        if (docLower.includes(w)) matches++;
                    }
                    sim = qWords.length > 0 ? (matches / qWords.length) * 0.75 : 0.0;
                    if (docLower.includes(cleanQuery.toLowerCase())) sim = Math.max(sim, 0.85);
                }

                if (sim >= threshold) {
                    scored.push({
                        id: item.id,
                        title: item.title,
                        url: item.url,
                        url_b: item.url_b,
                        title_b: item.title_b,
                        mode: item.mode,
                        mode_name: item.mode_name,
                        entry_type: item.entry_type || 'single',
                        created_at_formatted: item.created_at_formatted,
                        score: Math.round(sim * 1000) / 1000,
                        similarity: Math.round(sim * 1000) / 1000,
                        match_percentage: Math.round(sim * 100),
                        relevant_snippet: extractSnippet(docText, cleanQuery),
                        summary: sum
                    });
                }
            }

            scored.sort((a, b) => b.score - a.score);

            return {
                success: true,
                count: scored.length,
                query: cleanQuery,
                threshold: threshold,
                results: scored.slice(0, 10)
            };
        },

        /**
         * Exports all saved summaries as a JSON backup file.
         */
        exportBackupJSON: async function() {
            const items = await this.getHistory(500);
            const blob = new Blob([JSON.stringify(items, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `WebBrief_History_Backup_${new Date().toISOString().slice(0, 10)}.json`;
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);
        },

        // -------------------------------------------------------------
        // Resumable Jobs & Checkpoint Recovery (v1.7.0)
        // -------------------------------------------------------------

        /**
         * Saves or updates a summarization job record in IndexedDB.
         * Enforces credential exclusion: never stores API keys or tokens.
         */
        saveJob: async function(job) {
            if (!job || !job.id) return false;
            // Defensive sanitization: ensure no credentials leak into client DB
            const safeJob = Object.assign({}, job);
            delete safeJob.api_key;
            delete safeJob.cloudflare_api_token;
            delete safeJob.groq_api_key;

            const db = await getIndexedDB();
            if (!db) return false;

            return new Promise((resolve) => {
                try {
                    const tx = db.transaction([JOBS_STORE], 'readwrite');
                    const store = tx.objectStore(JOBS_STORE);
                    safeJob.updated_at = new Date().toISOString();
                    const req = store.put(safeJob);
                    req.onsuccess = () => resolve(true);
                    req.onerror = () => resolve(false);
                } catch (e) {
                    console.warn('[WebBrief AI] Failed to save job to IndexedDB:', e);
                    resolve(false);
                }
            });
        },

        /**
         * Fetches a job record from IndexedDB by ID.
         */
        getJob: async function(jobId) {
            const db = await getIndexedDB();
            if (!db) return null;

            return new Promise((resolve) => {
                try {
                    const tx = db.transaction([JOBS_STORE], 'readonly');
                    const store = tx.objectStore(JOBS_STORE);
                    const req = store.get(jobId);
                    req.onsuccess = () => resolve(req.result || null);
                    req.onerror = () => resolve(null);
                } catch (e) {
                    resolve(null);
                }
            });
        },

        /**
         * Lists all saved jobs from IndexedDB ordered by updated_at descending.
         */
        listJobs: async function() {
            const db = await getIndexedDB();
            if (!db) return [];

            return new Promise((resolve) => {
                try {
                    const tx = db.transaction([JOBS_STORE], 'readonly');
                    const store = tx.objectStore(JOBS_STORE);
                    const req = store.getAll();
                    req.onsuccess = () => {
                        const jobs = req.result || [];
                        jobs.sort((a, b) => new Date(b.updated_at || 0) - new Date(a.updated_at || 0));
                        resolve(jobs);
                    };
                    req.onerror = () => resolve([]);
                } catch (e) {
                    resolve([]);
                }
            });
        },

        /**
         * Deletes a job and its associated chunk checkpoints from IndexedDB.
         */
        deleteJob: async function(jobId) {
            const db = await getIndexedDB();
            if (!db) return false;

            return new Promise((resolve) => {
                try {
                    const tx = db.transaction([JOBS_STORE, CHUNKS_STORE], 'readwrite');
                    tx.objectStore(JOBS_STORE).delete(jobId);

                    // Delete associated chunks
                    const chunkStore = tx.objectStore(CHUNKS_STORE);
                    const index = chunkStore.index('job_id');
                    const cursorReq = index.openCursor(IDBKeyRange.only(jobId));
                    cursorReq.onsuccess = function(e) {
                        const cursor = e.target.result;
                        if (cursor) {
                            cursor.delete();
                            cursor.continue();
                        }
                    };
                    tx.oncomplete = () => resolve(true);
                    tx.onerror = () => resolve(false);
                } catch (e) {
                    resolve(false);
                }
            });
        },

        /**
         * Saves an individual chunk checkpoint immediately upon completion.
         * CORE RULE: Persist every successful chunk before moving to the next.
         */
        saveJobChunk: async function(chunk) {
            if (!chunk || !chunk.job_id) return false;
            const db = await getIndexedDB();
            if (!db) return false;

            chunk.id = chunk.id || `${chunk.job_id}_chunk_${chunk.chunk_index}`;
            chunk.updated_at = new Date().toISOString();

            return new Promise((resolve) => {
                try {
                    const tx = db.transaction([CHUNKS_STORE], 'readwrite');
                    const store = tx.objectStore(CHUNKS_STORE);
                    const req = store.put(chunk);
                    req.onsuccess = () => resolve(true);
                    req.onerror = () => resolve(false);
                } catch (e) {
                    resolve(false);
                }
            });
        },

        /**
         * Retrieves all saved chunk checkpoints for a job, sorted by chunk_index.
         */
        getJobChunks: async function(jobId) {
            const db = await getIndexedDB();
            if (!db) return [];

            return new Promise((resolve) => {
                try {
                    const tx = db.transaction([CHUNKS_STORE], 'readonly');
                    const store = tx.objectStore(CHUNKS_STORE);
                    const index = store.index('job_id');
                    const req = index.getAll(IDBKeyRange.only(jobId));
                    req.onsuccess = () => {
                        const chunks = req.result || [];
                        chunks.sort((a, b) => (a.chunk_index || 0) - (b.chunk_index || 0));
                        resolve(chunks);
                    };
                    req.onerror = () => resolve([]);
                } catch (e) {
                    resolve([]);
                }
            });
        },

        /**
         * Bounded chunk-by-chunk client orchestrator (v1.7.0).
         * Processes each chunk individually with intermediate checkpointing,
         * keeping execution bounded within serverless timeout limits.
         */
        orchestrateJob: async function(jobData, options) {
            options = options || {};
            const onProgress = options.onProgress || function() {};
            const jobId = jobData.id;

            // 1. Retrieve existing chunk checkpoints
            const existingChunks = await this.getJobChunks(jobId);
            const completedMap = {};
            existingChunks.forEach(c => {
                if (c.status === 'completed' && c.summary_output) {
                    completedMap[c.chunk_index] = c.summary_output;
                }
            });

            const totalChunks = jobData.total_chunks || (jobData.chunks ? jobData.chunks.length : 1);
            const intermediateSummaries = [];

            // 2. Process each chunk
            for (let idx = 1; idx <= totalChunks; idx++) {
                if (completedMap[idx]) {
                    intermediateSummaries.push([idx, completedMap[idx]]);
                    onProgress({
                        event: 'chunk_completed',
                        chunk_index: idx,
                        total_chunks: totalChunks,
                        resumed_from_cache: true
                    });
                    continue;
                }

                // Check for pause/cancellation
                const latestJob = await this.getJob(jobId);
                if (latestJob && (latestJob.status === 'Paused' || latestJob.status === 'Cancelled')) {
                    throw new Error(`Job execution ${latestJob.status.toLowerCase()} by user.`);
                }

                // Call bounded server endpoint for this single chunk
                const chunkDef = (jobData.chunks && jobData.chunks[idx - 1]) ? jobData.chunks[idx - 1] : {};
                const chunkPayload = {
                    chunk_text: chunkDef.chunk_text || '',
                    total_chunks: totalChunks,
                    mode: jobData.mode || 'detailed'
                };

                const res = await this.fetchWithCredentials(`/api/jobs/${jobId}/chunk/${idx}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(chunkPayload)
                });

                const data = await res.json();
                if (!res.ok || !data.success) {
                    const errMsg = data.error || `Chunk ${idx} processing failed.`;
                    await this.saveJob(Object.assign({}, jobData, {
                        status: 'Interrupted',
                        recovery_message: `${errMsg} Completed chunks preserved.`
                    }));
                    throw new Error(errMsg);
                }

                // Immediately checkpoint completed chunk to IndexedDB
                const chunkRecord = {
                    id: `${jobId}_chunk_${idx}`,
                    job_id: jobId,
                    chunk_index: idx,
                    status: 'completed',
                    summary_output: data.summary_output,
                    provider: data.provider,
                    model: data.model
                };
                await this.saveJobChunk(chunkRecord);
                intermediateSummaries.push([idx, data.summary_output]);

                // Update job progress
                await this.saveJob(Object.assign({}, jobData, {
                    completed_chunks: intermediateSummaries.length,
                    current_position: idx,
                    status: 'Processing'
                }));

                onProgress({
                    event: 'chunk_completed',
                    chunk_index: idx,
                    total_chunks: totalChunks,
                    provider: data.provider,
                    model: data.model,
                    resumed_from_cache: false
                });
            }

            // 3. Synthesize final summary
            await this.saveJob(Object.assign({}, jobData, { status: 'Synthesizing' }));
            onProgress({ event: 'synthesizing', total_chunks: totalChunks });

            const synthRes = await this.fetchWithCredentials(`/api/jobs/${jobId}/synthesize`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    intermediate_summaries: intermediateSummaries,
                    total_chunks: totalChunks,
                    mode: jobData.mode || 'detailed',
                    title: jobData.title,
                    url: jobData.source_url
                })
            });

            const synthData = await synthRes.json();
            if (!synthRes.ok || !synthData.success) {
                const synthErr = synthData.error || 'Final synthesis failed.';
                await this.saveJob(Object.assign({}, jobData, {
                    status: 'Interrupted',
                    recovery_message: `Synthesis failed: ${synthErr}. All chunks preserved.`
                }));
                throw new Error(synthErr);
            }

            // 4. Mark completed and store in history
            const finalSummary = synthData.summary;
            await this.saveJob(Object.assign({}, jobData, {
                status: 'Completed',
                completed_chunks: totalChunks,
                final_summary: finalSummary,
                history_id: synthData.history_id,
                recovery_message: 'Summarization completed successfully.'
            }));

            if (this.isHosted() && finalSummary) {
                await this.saveSummary({
                    title: finalSummary.title || jobData.title,
                    url: jobData.source_url,
                    mode: jobData.mode || 'detailed',
                    mode_name: finalSummary.mode_name || 'Detailed',
                    summary: finalSummary,
                    entry_type: 'single'
                });
            }

            return finalSummary;
        }
    };

    // Auto-clear key on page unload / refresh for maximum security
    window.addEventListener('beforeunload', function() {
        StorageAdapter.clearHostedKey();
    });

    // Attach to global window
    window.StorageAdapter = StorageAdapter;

})(window);
