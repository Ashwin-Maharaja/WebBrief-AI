/**
 * WebBrief AI — Deployment-Aware Storage & Hosted BYOK Adapter
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
    let _vaultActive = false;
    // Explicit credential states: 'loading', 'configured', 'not_configured', 'unavailable', 'expired'
    let _authStatus = window.__WEBBRIEF_VAULT_ENABLED ? 'loading' : 'not_configured';
    let _authReadyResolve = null;
    const _authReadyPromise = new Promise((resolve) => {
        _authReadyResolve = resolve;
    });
    if (!window.__WEBBRIEF_VAULT_ENABLED) {
        _authReadyResolve('not_configured');
    }

    let _inMemoryProviderConfig = {
        primary_provider: 'gemini',
        gemini_api_key: '',
        gemini_model: 'gemini-3.8-flash'
    };

    // Restore non-sensitive preferences from tab session storage if available
    try {
        if (window.sessionStorage) {
            const rawPrefs = window.sessionStorage.getItem('webbrief_provider_prefs');
            if (rawPrefs) {
                const parsed = JSON.parse(rawPrefs);
                if (parsed && typeof parsed === 'object') {
                    _inMemoryProviderConfig = Object.assign({}, _inMemoryProviderConfig, parsed);
                }
            }
        }
    } catch (_) {}

    // Restore the anonymous server-side vault session after a page reload.
    if (window.__WEBBRIEF_VAULT_ENABLED) {
        const initVaultStatus = async () => {
            try {
                const res = await fetch('/api/credentials/status');
                if (!res.ok) {
                    _authStatus = res.status === 401 ? 'expired' : 'unavailable';
                    _authReadyResolve(_authStatus);
                    return;
                }
                const status = await res.json();
                if (status.configured) {
                    _vaultActive = true;
                    _authStatus = 'configured';
                    _inMemoryProviderConfig.primary_provider = status.provider || 'gemini';
                    if (status.gemini_model || status.primary_model) {
                        _inMemoryProviderConfig.gemini_model = status.gemini_model || status.primary_model;
                    }
                    const label = document.getElementById('hosted-status-label');
                    if (label) {
                        label.textContent = '● Temporary Encrypted Session Active';
                        label.style.color = 'var(--accent-teal, #10b981)';
                        label.style.fontWeight = '600';
                    }
                    const clear = document.getElementById('hosted-clear-btn');
                    if (clear) clear.style.display = 'inline-flex';
                } else {
                    _authStatus = 'not_configured';
                }
            } catch (_) {
                _authStatus = 'unavailable';
            } finally {
                _authReadyResolve(_authStatus);
            }
        };

        if (document.readyState === 'loading') {
            window.addEventListener('DOMContentLoaded', initVaultStatus);
        } else {
            initVaultStatus();
        }
    }

    // Database configuration for Hosted IndexedDB mode
    const DB_NAME = 'WebBriefDB';
    const DB_VERSION = 2;
    const STORE_NAME = 'summaries';
    const PREFS_STORE_NAME = 'preferences';

    let _dbPromise = null;

    // Directives & preferences in-memory cache
    let _directives = {
        custom_directive: '',
        custom_directive_enabled: false
    };
    let _directivesPromise = null;

    /**
     * Initializes and returns the IndexedDB instance for hosted browser storage.
     * Gracefully handles private browsing restrictions, storage blockades, and lifecycle events.
     * Failed initializations are never permanently cached.
     */
    function getIndexedDB() {
        if (_dbPromise) return _dbPromise;

        _dbPromise = new Promise((resolve, reject) => {
            if (!window.indexedDB) {
                console.warn('[WebBrief AI] IndexedDB not supported by this browser. Falling back to in-memory history.');
                _dbPromise = null;
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
                    if (!db.objectStoreNames.contains(PREFS_STORE_NAME)) {
                        db.createObjectStore(PREFS_STORE_NAME, { keyPath: 'key' });
                    }
                };

                let isBlocked = false;

                request.onblocked = function(event) {
                    console.warn('[WebBrief AI] IndexedDB open blocked by another open tab holding older schema.');
                    isBlocked = true;
                    _dbPromise = null; // Do not cache blocked state permanently
                    resolve(null);
                };

                request.onsuccess = function(event) {
                    const db = event.target.result;
                    if (isBlocked) {
                        // The blocked promise was already resolved to null; close this late connection to prevent leak
                        try { db.close(); } catch (_) {}
                        return;
                    }
                    db.onversionchange = function() {
                        console.warn('[WebBrief AI] IndexedDB version change detected in another tab. Closing connection.');
                        db.close();
                        _dbPromise = null;
                    };
                    if ('onclose' in db) {
                        db.onclose = function() {
                            _dbPromise = null;
                        };
                    }
                    resolve(db);
                };

                request.onerror = function(event) {
                    console.warn('[WebBrief AI] IndexedDB open error:', event.target.error);
                    _dbPromise = null; // Reset promise so future attempts can retry cleanly
                    resolve(null);
                };
            } catch (err) {
                console.warn('[WebBrief AI] IndexedDB access restricted:', err);
                _dbPromise = null;
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
     * Standard synchronous SHA-256 implementation matching Python hashlib.sha256.
     */
    function sha256Sync(ascii) {
        function rightRotate(value, amount) {
            return (value >>> amount) | (value << (32 - amount));
        }
        const words = [];
        let hash = [
            0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
            0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19
        ];
        const k = [
            0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
            0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
            0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
            0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
            0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
            0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
            0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
            0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
        ];
        const utf8 = unescape(encodeURIComponent(ascii || ''));
        for (let i = 0; i < utf8.length; i++) {
            words[i >> 2] |= (utf8.charCodeAt(i) & 0xff) << (24 - (i % 4) * 8);
        }
        words[utf8.length >> 2] |= 0x80 << (24 - (utf8.length % 4) * 8);
        words[(((utf8.length + 8) >> 6) << 4) + 15] = utf8.length * 8;
        const w = new Array(64);
        for (let i = 0; i < words.length; i += 16) {
            const h = hash.slice(0);
            for (let j = 0; j < 64; j++) {
                if (j < 16) {
                    w[j] = words[i + j] | 0;
                } else {
                    const s0 = rightRotate(w[j - 15], 7) ^ rightRotate(w[j - 15], 18) ^ (w[j - 15] >>> 3);
                    const s1 = rightRotate(w[j - 2], 17) ^ rightRotate(w[j - 2], 19) ^ (w[j - 2] >>> 10);
                    w[j] = (w[j - 16] + s0 + w[j - 7] + s1) | 0;
                }
                const s1 = rightRotate(h[4], 6) ^ rightRotate(h[4], 11) ^ rightRotate(h[4], 25);
                const ch = (h[4] & h[5]) ^ (~h[4] & h[6]);
                const temp1 = (h[7] + s1 + ch + k[j] + w[j]) | 0;
                const s0 = rightRotate(h[0], 2) ^ rightRotate(h[0], 13) ^ rightRotate(h[0], 22);
                const maj = (h[0] & h[1]) ^ (h[0] & h[2]) ^ (h[1] & h[2]);
                const temp2 = (s0 + maj) | 0;
                h[7] = h[6];
                h[6] = h[5];
                h[5] = h[4];
                h[4] = (h[3] + temp1) | 0;
                h[3] = h[2];
                h[2] = h[1];
                h[1] = h[0];
                h[0] = (temp1 + temp2) | 0;
            }
            for (let j = 0; j < 8; j++) {
                hash[j] = (hash[j] + h[j]) | 0;
            }
        }
        let result = '';
        for (let i = 0; i < 8; i++) {
            const hex = (hash[i] >>> 0).toString(16).padStart(8, '0');
            result += hex;
        }
        return result;
    }

    /**
     * Constructs rich search document matching vector_store.py::prepare_search_text.
     */
    function prepareSearchText(entry) {
        if (!entry) return '';
        const title = entry.title || (entry.summary && entry.summary.title) || 'Webpage Summary';
        const url = entry.url || '';
        const summary = entry.summary || entry || {};
        const entryType = entry.entry_type || 'single';

        const parts = [`Title: ${title}`, `Source URL: ${url}`];

        if (entryType === 'comparison') {
            const artA = summary.article_a || {};
            const artB = summary.article_b || {};
            parts.push(`Source A: ${artA.title || 'Article A'} (${artA.url || ''})`);
            if (artA.overview) parts.push(`Overview A: ${artA.overview}`);

            parts.push(`Source B: ${artB.title || 'Article B'} (${artB.url || ''})`);
            if (artB.overview) parts.push(`Overview B: ${artB.overview}`);

            const common = summary.common_points || [];
            if (common.length) parts.push(`Common Points: ${common.join('; ')}`);

            const contradictions = summary.contradictions || [];
            if (contradictions.length) parts.push(`Contradictions & Disagreements: ${contradictions.join('; ')}`);

            const perspectives = summary.differing_perspectives || [];
            if (perspectives.length) parts.push(`Differing Perspectives: ${perspectives.join('; ')}`);

            const claims = summary.key_facts_claims || [];
            if (claims.length) {
                const claimsStr = claims.map(c => `${c.topic}: Source A claims '${c.article_a_claim}', Source B claims '${c.article_b_claim}'`).join('; ');
                parts.push(`Key Claims: ${claimsStr}`);
            }

            if (summary.synthesis) parts.push(`Synthesis: ${summary.synthesis}`);
        } else {
            const overview = summary.overview || summary.tldr;
            if (overview) parts.push(`Overview: ${overview}`);

            const keyPoints = summary.key_points || summary.key_takeaways || [];
            if (keyPoints.length) parts.push(`Key Takeaways: ${keyPoints.join('; ')}`);

            const details = summary.important_details || summary.core_summary || [];
            if (Array.isArray(details) && details.length) {
                parts.push(`Important Details: ${details.join('; ')}`);
            } else if (typeof details === 'string' && details) {
                parts.push(`Important Details: ${details}`);
            }

            if (summary.conclusion) parts.push(`Conclusion: ${summary.conclusion}`);
        }

        return parts.join('\n');
    }

    /**
     * Computes a SHA-256 hash of the normalized search text, matching vector_store.py::compute_content_hash.
     */
    function computeContentHash(text) {
        if (!text) return null;
        const normalized = text.replace(/\s+/g, ' ').trim();
        return sha256Sync(normalized);
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
                if (window.__WEBBRIEF_VAULT_ENABLED) {
                    _vaultActive = true;
                    _inMemoryProviderConfig.gemini_api_key = '';
                    _inMemoryApiKey = '';
                } else if (cfg.gemini_api_key) {
                    _inMemoryApiKey = cfg.gemini_api_key;
                } else if (cfg.api_key) {
                    _inMemoryApiKey = cfg.api_key;
                }

                // Safely save non-sensitive preferences across tab navigation
                try {
                    if (window.sessionStorage) {
                        const prefs = {
                            primary_provider: _inMemoryProviderConfig.primary_provider || 'gemini',
                            gemini_model: _inMemoryProviderConfig.gemini_model || 'gemini-3.8-flash'
                        };
                        window.sessionStorage.setItem('webbrief_provider_prefs', JSON.stringify(prefs));
                    }
                } catch (_) {}
            }
        },

        getProviderConfig: function() {
            const cfg = Object.assign({}, _inMemoryProviderConfig);
            if (window.__WEBBRIEF_VAULT_ENABLED) {
                delete cfg.gemini_api_key;
                delete cfg.api_key;
            }
            return cfg;
        },

        clearCredentials: function() {
            _vaultActive = false;
            _authStatus = 'not_configured';
            _inMemoryApiKey = '';
            _inMemoryProviderConfig.gemini_api_key = '';
        },

        getAuthStatus: function() {
            return _authStatus;
        },

        waitForAuthReady: function() {
            return _authReadyPromise;
        },

        hasValidCredentials: function() {
            if (window.__WEBBRIEF_VAULT_ENABLED) return _vaultActive;
            return Boolean(
                _inMemoryProviderConfig.gemini_api_key ||
                (_inMemoryApiKey && _inMemoryApiKey.length >= 10)
            );
        },

        /**
         * Returns the active API key to send with an API request.
         * In hosted mode: reads from memory.
         * In local mode: checks local override input or returns null (uses server .env).
         */
        getActiveApiKey: function(fallbackInputId = 'hosted-gemini-key') {
            if (window.__WEBBRIEF_VAULT_ENABLED) return null;
            if (this.isHosted()) {
                if (_inMemoryApiKey) return _inMemoryApiKey;
                const hostedInput = document.getElementById('hosted-gemini-key') || document.getElementById('hosted-api-key-input');
                if (hostedInput && hostedInput.value.trim()) {
                    return hostedInput.value.trim();
                }
            }
            const localInput = document.getElementById(fallbackInputId) || document.getElementById('api-key-input');
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
         * In Hosted mode: persists into browser IndexedDB with background vector embedding.
         * In Local mode: handled by Flask backend into SQLite.
         */
        saveSummary: async function(item) {
            if (!this.isHosted()) {
                return item; // In local mode, backend already recorded into SQLite
            }

            const db = await getIndexedDB();
            if (!db) {
                console.error('[WebBrief AI] IndexedDB is unavailable; unable to persist summary.');
                const err = new Error('IndexedDB is unavailable. Summary could not be saved to local history.');
                err.code = 'STORAGE_UNAVAILABLE';
                throw err;
            }

            // Ensure required schema fields
            const entryId = item.history_id || item.id || ('idb_' + Date.now() + '_' + Math.random().toString(36).substring(2, 9));
            const now = new Date();

            // Compute content hash to detect changes and invalidate stale cached vectors
            const sumText = prepareSearchText(item);
            const contentHash = computeContentHash(sumText);

            // Invalidate cached vector if content has been updated
            let cachedVector = item.vector || null;
            let cachedModel = item.vector_model || null;
            let cachedDimensions = item.vector_dimensions || null;
            if (item.content_hash && contentHash && item.content_hash !== contentHash) {
                cachedVector = null;
                cachedModel = null;
                cachedDimensions = null;
            }

            const isPartial = Boolean(item.is_partial || item.partial || (item.summary && (item.summary.is_partial || item.summary.partial)));
            const warningMsg = item.warning || (item.summary && item.summary.warning) || null;
            const providerName = item.provider || (item.summary && item.summary.provider) || _inMemoryProviderConfig.primary_provider || 'gemini';
            const modelName = item.model || (item.summary && item.summary.model) || _inMemoryProviderConfig.gemini_model || 'gemini-3.8-flash';

            const record = {
                id: entryId,
                title: item.title || (item.summary && item.summary.title) || 'Webpage Summary',
                url: item.url || '',
                mode: item.mode || 'quick',
                mode_name: item.mode_name || 'Summary',
                summary: item.summary || item,
                created_at: item.created_at || now.toISOString(),
                created_at_formatted: item.created_at_formatted || now.toLocaleDateString('en-US', {
                    month: 'short', day: 'numeric', year: 'numeric'
                }) + ' • ' + now.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
                entry_type: item.entry_type || 'single',
                url_b: item.url_b || null,
                title_b: item.title_b || null,
                is_partial: isPartial,
                warning: warningMsg,
                provider: providerName,
                model: modelName,
                content_hash: contentHash,
                vector: cachedVector, // Fresh or invalidated cached vector
                vector_model: cachedModel,
                vector_dimensions: cachedDimensions
            };

            const savedRecord = await new Promise((resolve, reject) => {
                try {
                    const tx = db.transaction(STORE_NAME, 'readwrite');
                    const store = tx.objectStore(STORE_NAME);
                    const putReq = store.put(record);
                    putReq.onerror = function(e) {
                        const err = putReq.error || (e.target && e.target.error);
                        if (err && (err.name === 'QuotaExceededError' || err.code === 22)) {
                            console.error('[WebBrief AI] IndexedDB storage quota exceeded.');
                        }
                    };
                    tx.oncomplete = () => resolve(record);
                    tx.onerror = () => {
                        const err = tx.error || (putReq && putReq.error) || new Error('Failed to commit IndexedDB transaction');
                        if (err && (err.name === 'QuotaExceededError' || err.name === 'QuotaError' || err.code === 22)) {
                            err.code = 'QUOTA_EXCEEDED';
                        }
                        reject(err);
                    };
                    tx.onabort = () => {
                        const err = tx.error || (putReq && putReq.error) || new Error('IndexedDB transaction was aborted.');
                        if (err && (err.name === 'QuotaExceededError' || err.name === 'QuotaError' || err.code === 22)) {
                            err.code = 'QUOTA_EXCEEDED';
                        }
                        reject(err);
                    };
                } catch (err) {
                    if (err && (err.name === 'QuotaExceededError' || err.name === 'QuotaError' || err.code === 22)) {
                        err.code = 'QUOTA_EXCEEDED';
                    }
                    reject(err);
                }
            });

            // Asynchronously generate vector embedding in background for hosted semantic search
            // (IDB-01): Protect against clobbering concurrent user edits or resurrecting deleted records
            if (!savedRecord.vector) {
                const activeKey = this.getActiveApiKey();
                if (sumText && (activeKey || window.__WEBBRIEF_VAULT_ENABLED)) {
                    fetch('/api/search/embed', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ text: sumText, api_key: activeKey })
                    })
                    .then(r => r.ok ? r.json() : null)
                    .then(data => {
                        if (data && Array.isArray(data.embedding)) {
                            getIndexedDB().then(dbInstance => {
                                if (!dbInstance) return;
                                try {
                                    const updateTx = dbInstance.transaction(STORE_NAME, 'readwrite');
                                    const store = updateTx.objectStore(STORE_NAME);
                                    const getReq = store.get(entryId);
                                    getReq.onsuccess = function() {
                                        const freshRecord = getReq.result;
                                        // Do not resurrect if entry was deleted while embedding was generating
                                        if (!freshRecord) return;
                                        // Do not overwrite if entry content changed while embedding was generating
                                        if (freshRecord.content_hash !== contentHash) return;
                                        freshRecord.vector = data.embedding;
                                        freshRecord.vector_model = data.model || 'gemini-embedding-001';
                                        freshRecord.vector_dimensions = data.dimensions || data.embedding.length;
                                        freshRecord.content_hash = contentHash;
                                        store.put(freshRecord);
                                    };
                                } catch (_) {}
                            });
                        }
                    })
                    .catch(() => {});
                }
            }

            return savedRecord;
        },

        /**
         * Retrieves the list of saved summaries.
         * Leverages reverse cursor over created_at index for O(limit) memory efficiency.
         */
        getHistory: async function(limit = 50) {
            if (!this.isHosted()) {
                const res = await fetch('/api/history?limit=' + limit);
                if (!res.ok) {
                    throw new Error(`Failed to load history from server (HTTP ${res.status}).`);
                }
                const data = await res.json();
                return data.history || [];
            }

            const db = await getIndexedDB();
            if (!db) {
                throw new Error('IndexedDB is unavailable. History could not be loaded.');
            }

            return new Promise((resolve, reject) => {
                try {
                    const tx = db.transaction(STORE_NAME, 'readonly');
                    tx.onerror = () => reject(tx.error || new Error('IndexedDB read transaction failed.'));
                    tx.onabort = () => reject(tx.error || new Error('IndexedDB read transaction was aborted.'));

                    const store = tx.objectStore(STORE_NAME);
                    const items = [];

                    // Fast reverse cursor over created_at index (bounded memory footprint)
                    if (store.indexNames && store.indexNames.contains('created_at')) {
                        const index = store.index('created_at');
                        const cursorReq = index.openCursor(null, 'prev');
                        cursorReq.onsuccess = function(e) {
                            const cursor = e.target.result;
                            if (cursor && items.length < limit) {
                                items.push(cursor.value);
                                cursor.continue();
                            } else {
                                // Guarantee strict created_at descending sort (P2-IDB-03)
                                items.sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0));
                                resolve(items);
                            }
                        };
                        cursorReq.onerror = () => reject(cursorReq.error || new Error('Failed to traverse IndexedDB cursor.'));
                    } else {
                        // Fallback if index not yet initialized
                        const req = store.getAll();
                        req.onsuccess = function() {
                            const all = req.result || [];
                            all.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
                            resolve(all.slice(0, limit));
                        };
                        req.onerror = () => reject(req.error || new Error('Failed to read all IndexedDB records.'));
                    }
                } catch (err) {
                    reject(err);
                }
            });
        },

        /**
         * Retrieves an individual summary by ID.
         */
        getEntry: async function(id) {
            if (!this.isHosted()) {
                const res = await fetch('/api/history/' + encodeURIComponent(id));
                if (res.status === 404) return null;
                if (!res.ok) {
                    const err = new Error(`Failed to load history entry from server (HTTP ${res.status}).`);
                    err.status = res.status;
                    throw err;
                }
                const data = await res.json();
                return data.entry || null;
            }

            const db = await getIndexedDB();
            if (!db) {
                const err = new Error('IndexedDB is unavailable. Entry could not be loaded.');
                err.code = 'STORAGE_UNAVAILABLE';
                throw err;
            }

            return new Promise((resolve, reject) => {
                try {
                    const tx = db.transaction(STORE_NAME, 'readonly');
                    const store = tx.objectStore(STORE_NAME);
                    const req = store.get(id);

                    req.onsuccess = function() {
                        resolve(req.result || null);
                    };
                    req.onerror = function() {
                        reject(req.error || new Error('Failed to read entry from IndexedDB.'));
                    };
                } catch (err) {
                    reject(err);
                }
            });
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
                    const delReq = store.delete(id);
                    delReq.onerror = () => {
                        console.error('[WebBrief AI] Delete request failed:', delReq.error);
                    };
                    tx.oncomplete = () => resolve(true);
                    tx.onerror = () => resolve(false);
                    tx.onabort = () => resolve(false);
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
                    tx.onabort = () => resolve(false);
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
                const res = await fetch('/api/history/count');
                if (!res.ok) {
                    throw new Error(`Failed to load history count from server (HTTP ${res.status}).`);
                }
                const data = await res.json();
                return typeof data.count === 'number' ? data.count : 0;
            }

            const db = await getIndexedDB();
            if (!db) {
                throw new Error('IndexedDB is unavailable. History count could not be retrieved.');
            }

            return new Promise((resolve, reject) => {
                try {
                    const tx = db.transaction(STORE_NAME, 'readonly');
                    const store = tx.objectStore(STORE_NAME);
                    const req = store.count();
                    req.onsuccess = () => resolve(req.result || 0);
                    req.onerror = () => reject(req.error || new Error('Failed to count IndexedDB records.'));
                } catch (err) {
                    reject(err);
                }
            });
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

            // Hosted Mode: Query all IndexedDB records (no 500-record truncation)
            const items = await this.getAllHistory();
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
            let embedModel = null;
            let searchMode = 'lexical_fallback';

            if (activeKey || window.__WEBRIEF_VAULT_ENABLED || window.__WEBBRIEF_VAULT_ENABLED) {
                try {
                    const embedRes = await fetch('/api/search/embed', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ text: cleanQuery, api_key: activeKey })
                    });
                    if (embedRes.ok) {
                        const embedData = await embedRes.json();
                        queryVector = embedData.embedding;
                        embedModel = embedData.model;
                        if (queryVector) {
                            searchMode = (embedModel === 'deterministic-hash-fallback') ? 'hash_vector' : 'semantic';
                        }
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
                const isModelMatch = (!item.vector_model || !embedModel || item.vector_model === embedModel);
                const isDimMatch = (item.vector && queryVector && item.vector.length === queryVector.length);
                if (queryVector && item.vector && Array.isArray(item.vector) && isModelMatch && isDimMatch) {
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
                search_mode: searchMode,
                embedding_model: embedModel || null,
                threshold: threshold,
                results: scored.slice(0, 10)
            };
        },

        /**
         * Retrieves all historical records without truncation for complete backup exports.
         */
        getAllHistory: async function() {
            if (!this.isHosted()) {
                const res = await fetch('/api/history?limit=-1');
                if (!res.ok) {
                    throw new Error(`Failed to load history from server (HTTP ${res.status}).`);
                }
                const data = await res.json();
                return data.history || [];
            }

            const db = await getIndexedDB();
            if (!db) {
                throw new Error('IndexedDB is unavailable. History could not be loaded.');
            }

            return new Promise((resolve, reject) => {
                try {
                    const tx = db.transaction(STORE_NAME, 'readonly');
                    tx.onerror = () => reject(tx.error || new Error('IndexedDB read transaction failed.'));
                    tx.onabort = () => reject(tx.error || new Error('IndexedDB read transaction was aborted.'));

                    const store = tx.objectStore(STORE_NAME);
                    const items = [];

                    if (store.indexNames && store.indexNames.contains('created_at')) {
                        const index = store.index('created_at');
                        const cursorReq = index.openCursor(null, 'prev');
                        cursorReq.onsuccess = function(e) {
                            const cursor = e.target.result;
                            if (cursor) {
                                items.push(cursor.value);
                                cursor.continue();
                            } else {
                                resolve(items);
                            }
                        };
                        cursorReq.onerror = () => reject(cursorReq.error || new Error('Failed to traverse IndexedDB cursor.'));
                    } else {
                        const req = store.getAll();
                        req.onsuccess = function() {
                            const all = req.result || [];
                            all.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
                            resolve(all);
                        };
                        req.onerror = () => reject(req.error || new Error('Failed to read all IndexedDB records.'));
                    }
                } catch (err) {
                    reject(err);
                }
            });
        },

        /**
         * Exports all saved summaries as a JSON backup file.
         */
        exportBackupJSON: async function() {
            const items = await this.getAllHistory();
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
        // Custom Prompt Directives & Preferences Storage
        // -------------------------------------------------------------
        /**
         * Loads preferences asynchronously into the cache.
         * In Hosted mode: loads from browser IndexedDB ('preferences' store).
         * In Local mode: loads from SQLite backend via /api/preferences.
         */
        loadPreferences: function() {
            if (_directivesPromise) return _directivesPromise;

            _directivesPromise = (async () => {
                let success = false;
                try {
                    if (this.isHosted()) {
                        const db = await getIndexedDB();
                        if (db && db.objectStoreNames.contains(PREFS_STORE_NAME)) {
                            const record = await new Promise((resolve, reject) => {
                                const tx = db.transaction(PREFS_STORE_NAME, 'readonly');
                                const store = tx.objectStore(PREFS_STORE_NAME);
                                const req = store.get('custom_directives');
                                req.onsuccess = () => resolve(req.result || null);
                                req.onerror = () => reject(req.error || new Error('Failed to read preferences'));
                            });
                            if (record) {
                                _directives = {
                                    custom_directive: record.custom_directive || '',
                                    custom_directive_enabled: Boolean(record.custom_directive_enabled)
                                };
                            }
                            success = true;
                        } else if (!db) {
                            // IndexedDB unavailable
                            success = false;
                        } else {
                            success = true;
                        }
                    } else {
                        // Local Flask: query SQLite preferences via /api/preferences
                        const res = await fetch('/api/preferences');
                        if (res.ok) {
                            const data = await res.json();
                            _directives = {
                                custom_directive: data.custom_directive || '',
                                custom_directive_enabled: Boolean(data.custom_directive_enabled)
                            };
                            success = true;
                        } else {
                            console.warn(`[WebBrief AI] Server returned HTTP ${res.status} when loading preferences.`);
                            success = false;
                        }
                    }
                } catch (err) {
                    console.warn('[WebBrief AI] Failed to load preferences:', err);
                    success = false;
                } finally {
                    if (!success) {
                        _directivesPromise = null; // IDB-06: allow retry on subsequent attempts
                    }
                }
                return Object.assign({}, _directives);
            })();

            return _directivesPromise;
        },

        /**
         * Returns saved preferences object { custom_directive, custom_directive_enabled }.
         * Guarantees preference loading has completed before resolving.
         */
        getPreferences: async function() {
            await this.loadPreferences();
            return Object.assign({}, _directives);
        },

        /**
         * Synchronously returns currently cached preferences.
         */
        getDirectivesSync: function() {
            return Object.assign({}, _directives);
        },

        /**
         * Persists custom prompt directives and enabled state.
         * In Hosted mode: stores into IndexedDB (never credentials).
         * In Local mode: stores into SQLite via /api/preferences.
         */
        savePreferences: async function(prefs) {
            const directive = (prefs && prefs.custom_directive ? String(prefs.custom_directive).trim() : '');
            const enabled = Boolean(prefs && prefs.custom_directive_enabled);

            if (this.isHosted()) {
                try {
                    const db = await getIndexedDB();
                    if (!db) throw new Error('IndexedDB not available');
                    await new Promise((resolve, reject) => {
                        const tx = db.transaction(PREFS_STORE_NAME, 'readwrite');
                        const store = tx.objectStore(PREFS_STORE_NAME);
                        store.put({
                            key: 'custom_directives',
                            custom_directive: directive,
                            custom_directive_enabled: enabled,
                            updated_at: new Date().toISOString()
                        });
                        tx.oncomplete = () => resolve();
                        tx.onerror = () => reject(tx.error || new Error('Failed to save to IndexedDB'));
                        tx.onabort = () => reject(tx.error || new Error('IndexedDB save transaction aborted'));
                    });
                    _directives = {
                        custom_directive: directive,
                        custom_directive_enabled: enabled
                    };
                    _directivesPromise = Promise.resolve(Object.assign({}, _directives));
                    return { success: true, preferences: Object.assign({}, _directives) };
                } catch (err) {
                    console.error('[WebBrief AI] Failed to save preferences to IndexedDB:', err);
                    return { success: false, error: err.message || 'Failed to save preferences to browser storage.' };
                }
            } else {
                // Local mode: POST /api/preferences
                try {
                    const res = await fetch('/api/preferences', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            custom_directive: directive,
                            custom_directive_enabled: enabled
                        })
                    });
                    const data = await res.json();
                    if (res.ok && data.success) {
                        _directives = {
                            custom_directive: directive,
                            custom_directive_enabled: enabled
                        };
                        _directivesPromise = Promise.resolve(Object.assign({}, _directives));
                        return { success: true, preferences: Object.assign({}, _directives) };
                    }
                    return { success: false, error: data.message || 'Failed to save preferences.' };
                } catch (err) {
                    return { success: false, error: 'Network error saving preferences.' };
                }
            }
        },

        /**
         * Resets custom prompt directives to empty and disabled.
         * In Hosted mode: updates IndexedDB.
         * In Local mode: calls /api/preferences/reset.
         */
        resetPreferences: async function() {
            if (this.isHosted()) {
                try {
                    const db = await getIndexedDB();
                    if (!db) throw new Error('IndexedDB not available');
                    await new Promise((resolve, reject) => {
                        const tx = db.transaction(PREFS_STORE_NAME, 'readwrite');
                        const store = tx.objectStore(PREFS_STORE_NAME);
                        store.put({
                            key: 'custom_directives',
                            custom_directive: '',
                            custom_directive_enabled: false,
                            updated_at: new Date().toISOString()
                        });
                        tx.oncomplete = () => resolve();
                        tx.onerror = () => reject(tx.error || new Error('Failed to reset in IndexedDB'));
                        tx.onabort = () => reject(tx.error || new Error('IndexedDB reset transaction aborted'));
                    });
                    _directives = {
                        custom_directive: '',
                        custom_directive_enabled: false
                    };
                    _directivesPromise = Promise.resolve(Object.assign({}, _directives));
                    return { success: true, preferences: Object.assign({}, _directives) };
                } catch (err) {
                    return { success: false, error: err.message || 'Failed to reset preferences in browser storage.' };
                }
            } else {
                try {
                    const res = await fetch('/api/preferences/reset', { method: 'POST' });
                    const data = await res.json();
                    if (res.ok && data.success) {
                        _directives = {
                            custom_directive: '',
                            custom_directive_enabled: false
                        };
                        _directivesPromise = Promise.resolve(Object.assign({}, _directives));
                        return { success: true, preferences: Object.assign({}, _directives) };
                    }
                    return { success: false, error: data.message || 'Failed to reset preferences.' };
                } catch (err) {
                    return { success: false, error: 'Network error resetting preferences.' };
                }
            }
        }
    };

    // Auto-load preferences on startup
    if (typeof document !== 'undefined') {
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', function() {
                StorageAdapter.loadPreferences();
            });
        } else {
            StorageAdapter.loadPreferences();
        }
    }

    // Auto-clear key on page unload / refresh for maximum security
    window.addEventListener('beforeunload', function() {
        StorageAdapter.clearHostedKey();
    });

    // Attach to global window
    window.StorageAdapter = StorageAdapter;

})(window);
