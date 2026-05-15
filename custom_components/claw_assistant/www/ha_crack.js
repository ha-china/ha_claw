(function() {
    'use strict';
    
    const HACRACK_VERSION = '8.0.0';
    if (window.__hacrackVersion && window.__hacrackVersion !== HACRACK_VERSION) {
        window.__hacrackVersion = HACRACK_VERSION;
        location.reload();
        return;
    }
    window.__hacrackVersion = HACRACK_VERSION;
    let initialized = false;
    let pollInterval = null;
    let hassRef = null;
    
    function getHass() {
        if (hassRef && hassRef.connection) return hassRef;
        const ha = document.querySelector('home-assistant');
        if (ha && ha.hass) {
            hassRef = ha.hass;
            return hassRef;
        }
        return null;
    }

    const HISTORY_TEXT = {
        en: {
            history: 'History',
            back: 'Back',
            unableLoad: 'Unable to load history',
            empty: 'No conversations yet',
            search: 'Search conversations...',
            conversation: 'Conversation',
            delete: 'Delete',
            messages: 'messages',
            pin: 'Pin',
            unpin: 'Unpin',
            today_night: 'Today · Early Morning',
            today_morning: 'Today · Morning',
            today_afternoon: 'Today · Afternoon',
            today_evening: 'Today · Evening',
            yesterday: 'Yesterday',
            this_week: 'This Week',
            this_month: 'This Month',
        },
        zh: {
            history: '历史消息',
            back: '返回',
            unableLoad: '无法加载您的历史消息',
            empty: '还没有对话',
            search: '搜索最近对话...',
            conversation: '对话',
            delete: '删除',
            messages: '条消息',
            pin: '置顶',
            unpin: '取消置顶',
            today_night: '今天 · 凌晨',
            today_morning: '今天 · 上午',
            today_afternoon: '今天 · 下午',
            today_evening: '今天 · 晚上',
            yesterday: '昨天',
            this_week: '本周',
            this_month: '本月',
        },
    };

    function getFrontendLanguage(hass) {
        const liveHass = document.querySelector('home-assistant')?.hass;
        hass = liveHass || hass || hassRef;
        return String(
            hass?.locale?.language ||
            hass?.selectedLanguage ||
            hass?.language ||
            hass?.config?.language ||
            navigator.language ||
            'en'
        ).toLowerCase();
    }

    function historyText(key) {
        const language = getFrontendLanguage();
        const bundle = language.startsWith('zh') ? HISTORY_TEXT.zh : HISTORY_TEXT.en;
        return bundle[key] || HISTORY_TEXT.en[key] || key;
    }

    function getHistoryWindowId() {
        if (!window.__clawHistoryWindowId) {
            const rand = window.crypto?.randomUUID?.() || Math.random().toString(36).slice(2);
            window.__clawHistoryWindowId = 'claw-window-' + Date.now().toString(36) + '-' + rand;
        }
        return window.__clawHistoryWindowId;
    }

    function registerHistoryWindow(hass) {
        if (!hass?.connection) return;
        try {
            hass.connection.sendMessagePromise({
                type: 'ha_crack/chat_history_window',
                window_id: getHistoryWindowId()
            }).catch(() => {});
        } catch(e) {}
    }

    function bindHistoryWindow(hass, conversationId) {
        if (!hass?.connection || !conversationId) return;
        try {
            hass.connection.sendMessagePromise({
                type: 'ha_crack/chat_history_resume',
                conversation_id: conversationId,
                window_id: getHistoryWindowId()
            }).catch(() => {});
        } catch(e) {}
    }
    
    function getMainPanel() {
        return document.querySelector('home-assistant')?.shadowRoot?.querySelector('home-assistant-main')?.shadowRoot;
    }
    
    function getSidebar() {
        return getMainPanel()?.querySelector('ha-sidebar')?.shadowRoot;
    }
    
    let _dockedChat = null;
    function deepQuery(selector, root = document) {
        if (selector === 'ha-assist-chat' && _dockedChat && _dockedChat.isConnected && root === document) {
            return _dockedChat;
        }
        let result = root.querySelector(selector);
        if (result) return result;
        const allElements = root.querySelectorAll('*');
        for (const el of allElements) {
            if (el.shadowRoot) {
                result = deepQuery(selector, el.shadowRoot);
                if (result) return result;
            }
        }
        return null;
    }
    
    function deepQueryAll(selector, root = document, results = []) {
        root.querySelectorAll(selector).forEach(el => results.push(el));
        root.querySelectorAll('*').forEach(el => {
            if (el.shadowRoot) deepQueryAll(selector, el.shadowRoot, results);
        });
        return results;
    }
    
    function getAllClickables() {
        const selectors = 'button, a, [role="button"], [role="menuitem"], [role="tab"], ha-icon-button, mwc-button, mwc-icon-button, ha-list-item, mwc-list-item, paper-item, ha-clickable-list-item';
        return deepQueryAll(selectors).filter(el => {
            const style = getComputedStyle(el);
            return style.display !== 'none' && style.visibility !== 'hidden' && el.offsetParent !== null;
        });
    }
    
    function getAllInputs() {
        const selectors = 'input, textarea, ha-textfield, mwc-textfield, ha-textarea, paper-input, paper-textarea';
        return deepQueryAll(selectors);
    }
    
    function removeOverlays() {
        const overlaySelectors = [
            'ha-dialog-scrim',
            '.mdc-dialog__scrim', 
            '.overlay',
            '[class*="scrim"]',
            '[class*="overlay"]',
            '[class*="backdrop"]',
            'mwc-dialog[open]::before'
        ];
        let removed = 0;
        overlaySelectors.forEach(sel => {
            deepQueryAll(sel).forEach(el => {
                if (el.style) {
                    el.style.pointerEvents = 'none';
                    removed++;
                }
            });
        });
        const highZElements = deepQueryAll('*').filter(el => {
            const z = parseInt(getComputedStyle(el).zIndex) || 0;
            const isOverlay = z > 100 && el.offsetWidth > window.innerWidth * 0.5 && el.offsetHeight > window.innerHeight * 0.5;
            return isOverlay && !el.querySelector('button, input, a');
        });
        highZElements.forEach(el => {
            el.style.pointerEvents = 'none';
            removed++;
        });
        return removed;
    }
    
    function setupEventListeners() {
        if (initialized) return;
        
        const hass = getHass();
        if (!hass || !hass.connection) {
            setTimeout(setupEventListeners, 500);
            return;
        }
        
        initialized = true;
        console.info(`%c</>HACrack%cv${HACRACK_VERSION}`,"background:#03a9f4;color:#fff;padding:2px 6px;font:bold 10px monaco;border-radius:3px 0 0 3px","background:#0288d1;color:#fff;padding:2px 6px;font:bold 10px monaco;border-radius:0 3px 3px 0");
        pollInterval = setInterval(() => pollPendingJS(hass), 2000);
        pollPendingJS(hass);
        exposeGlobalAPI();
        setupGoalContinuationStream(hass);
        registerHistoryWindow(hass);
        setupContinuousConversation(hass);
        setupAssistRightDock(hass);
        setupContextStatusBar(hass);
        setupFileUpload(hass);
        setupFrontendBridge(hass);
        setupSoundNotifications(hass);
        setTimeout(() => {
            if (window.HACrack?.preventAssistDialogClose) {
                window.HACrack.preventAssistDialogClose();
            }
        }, 100);
    }

    function setupSoundNotifications(hass) {
        if (!hass?.connection) return;
        if (window.__clawSoundSetup) return;
        window.__clawSoundSetup = true;

        const MEDIA_BASE = '/local/claw_assistant/media/';
        const SOUNDS = {
            'stream-complete': MEDIA_BASE + 'stream-complete.mp3',
            'permission-required': MEDIA_BASE + 'permission-required.mp3',
            'error': MEDIA_BASE + 'error.mp3',
        };

        let _soundEnabled = true;
        const refreshSoundSetting = async () => {
            try {
                const r = await hass.connection.sendMessagePromise({ type: 'ha_crack/get_settings' });
                _soundEnabled = r?.enable_sound_notifications !== false;
            } catch(e) {}
        };
        refreshSoundSetting();
        hass.connection.subscribeEvents(() => refreshSoundSetting(), 'ha_crack_settings_changed').catch(() => {});

        let _audioCache = {};
        function playSound(name) {
            if (!_soundEnabled) return;
            const url = SOUNDS[name];
            if (!url) return;
            setTimeout(() => {
                try {
                    if (!_audioCache[name]) {
                        _audioCache[name] = new Audio(url);
                        _audioCache[name].volume = 0.5;
                    }
                    const audio = _audioCache[name];
                    audio.currentTime = 0;
                    audio.play().catch(() => {});
                } catch(e) {}
            }, 400);
        }

        // Expose globally so other hooks can call it
        window.__clawPlaySound = playSound;

        // Listen for backend-fired sound events
        hass.connection.subscribeEvents(ev => {
            const sound = ev.data?.sound;
            if (sound && SOUNDS[sound]) playSound(sound);
        }, 'claw_assistant_sound').catch(() => {});
    }

    function setupFrontendBridge(hass) {
        if (!hass?.connection) return;
        const conn = hass.connection;
        if (window.__clawBridgeConn === conn) return;
        window.__clawBridgeConn = conn;

        const seen = new Set();

        function getConn() {
            const ha = document.querySelector('home-assistant');
            return ha?.hass?.connection || conn;
        }

        async function sendResult(execId, result) {
            const c = getConn();
            for (let i = 0; i < 3; i++) {
                try {
                    await c.sendMessagePromise({
                        type: 'ha_crack/frontend_exec_result',
                        exec_id: execId,
                        result: result
                    });
                    return;
                } catch(_) {
                    await new Promise(r => setTimeout(r, 300));
                }
            }
        }

        async function runTask(task) {
            if (!task?.id || !task?.code || seen.has(task.id)) return;
            seen.add(task.id);
            if (seen.size > 200) { const it = seen.values(); seen.delete(it.next().value); }
            let result;
            try {
                try {
                    result = new Function('return (' + task.code + ')')();
                } catch(_) {
                    result = new Function(task.code)();
                }
                if (result instanceof Promise) result = await result;
            } catch(e) {
                result = { error: e.message, stack: (e.stack||'').slice(0,300) };
            }
            if (result === undefined || result === null) result = { value: null };
            else if (typeof result !== 'object') result = { value: result };
            else { try { JSON.stringify(result); } catch(_) { result = { value: String(result) }; } }
            await sendResult(task.id, result);
        }

        let _subActive = false;
        let _subUnsupported = false;
        let _pollTimer = null;
        let _pollDelay = 2000;
        const pollExec = async () => {
            if (_subActive || _subUnsupported === false && !_pollTimer) return;
            try {
                const c = getConn();
                const r = await c.sendMessagePromise({ type: 'ha_crack/frontend_exec_poll' });
                const tasks = r?.tasks || [];
                for (const task of tasks) await runTask(task);
                _pollDelay = tasks.length ? 250 : 2000;
            } catch(_) {
                _pollDelay = 5000;
            } finally {
                if (_subUnsupported) _pollTimer = setTimeout(pollExec, _pollDelay);
            }
        };
        const startPollFallback = () => {
            if (_pollTimer) return;
            _subUnsupported = true;
            _pollDelay = 250;
            _pollTimer = setTimeout(pollExec, 0);
        };
        const startSubscription = () => {
            if (_subActive || _subUnsupported) return;
            _subActive = true;
            const c = getConn();
            c.subscribeMessage(
                (msg) => {
                    if (msg?.type === 'exec' && msg?.task) runTask(msg.task);
                },
                { type: 'ha_crack/frontend_exec_subscribe' }
            ).catch(() => {
                _subActive = false;
                startPollFallback();
            });
        };
        startSubscription();

        conn.subscribeEvents(ev => {
            const d = ev.data;
            if (d?.id && d?.code) runTask(d);
        }, 'claw_frontend_exec').catch(() => {});

        setInterval(() => {
            if (!_subActive) startSubscription();
        }, 10000);

        (function setupDialogObserver() {
            if (window.__clawDialogObsInstalled) return;
            window.__clawDialogObsInstalled = true;

            function dq(root, sel) {
                if (!root) return null;
                let el = root.querySelector?.(sel);
                if (el) return el;
                const sr = root.shadowRoot;
                if (sr) { el = dq(sr, sel); if (el) return el; }
                const ch = root.querySelectorAll?.('*') || [];
                for (let i = 0; i < Math.min(ch.length, 80); i++) {
                    if (ch[i].shadowRoot) { el = dq(ch[i].shadowRoot, sel); if (el) return el; }
                }
                return null;
            }
            function dqAll(root, sel, out, seen) {
                if (!root || seen.has(root)) return out;
                seen.add(root);
                try { root.querySelectorAll?.(sel).forEach(x => out.push(x)); } catch(_) {}
                const ch = root.querySelectorAll?.('*') || [];
                for (let i = 0; i < ch.length; i++) {
                    if (ch[i].shadowRoot) dqAll(ch[i].shadowRoot, sel, out, seen);
                    if (ch[i].tagName === 'SLOT') {
                        const assigned = ch[i].assignedElements?.({ flatten: true }) || [];
                        for (const a of assigned) dqAll(a, sel, out, seen);
                    }
                }
                return out;
            }
            function textOf(el) {
                if (!el) return '';
                return (el.innerText || el.textContent || '').trim().slice(0, 200);
            }

            function extractDialog(haDialog) {
                const result = { type: 'unknown' };
                const parentHost = haDialog.getRootNode?.()?.host;
                if (parentHost) result.type = parentHost.tagName?.toLowerCase() || 'unknown';

                const hdr = dq(haDialog, 'ha-dialog-header');
                if (hdr) {
                    const titleEl = dq(hdr, '[slot="title"], .header-title');
                    const subEl = dq(hdr, '[slot="subtitle"], .header-subtitle');
                    result.title = textOf(titleEl) || haDialog.getAttribute?.('header-title') || '';
                    const sub = textOf(subEl) || haDialog.getAttribute?.('header-subtitle') || '';
                    if (sub) result.subtitle = sub;
                } else {
                    result.title = haDialog.getAttribute?.('header-title') || textOf(dq(haDialog, '.title, h1, h2')) || '';
                }

                const bodyItems = [];
                const seen = new Set();
                const SEL_INPUTS = 'ha-input, ha-textfield, ha-select, ha-combo-box, ha-entity-picker, ha-area-picker, ha-device-picker, ha-selector, ha-form, ha-yaml-editor, ha-code-editor, input, textarea, select, ha-date-input, ha-time-input, ha-icon-picker';
                const inputs = dqAll(haDialog, SEL_INPUTS, [], seen);
                for (const inp of inputs) {
                    const tag = inp.tagName.toLowerCase();
                    const item = { element: tag };
                    const label = inp.getAttribute('label') || inp.getAttribute('aria-label') || inp.getAttribute('placeholder') || '';
                    if (label) item.label = label.slice(0, 80);
                    if (tag === 'select' || tag === 'ha-select') {
                        const nativeSelect = tag === 'select' ? inp : dq(inp, 'select');
                        if (nativeSelect) {
                            item.value = nativeSelect.value || '';
                            item.options = [...nativeSelect.options].slice(0, 20).map(o => ({ value: o.value, text: o.textContent.trim().slice(0, 60), selected: o.selected }));
                        } else {
                            item.value = (inp.value ?? '').toString().slice(0, 200);
                        }
                    } else if (tag === 'ha-form') {
                        item.element = 'ha-form';
                        try {
                            const schema = inp.schema;
                            if (Array.isArray(schema)) {
                                item.fields = schema.slice(0, 20).map(s => ({
                                    name: s.name, type: s.type || s.selector && Object.keys(s.selector)[0] || 'text',
                                    label: s.label || s.name, required: !!s.required
                                }));
                            }
                            const data = inp.data;
                            if (data && typeof data === 'object') item.values = Object.fromEntries(Object.entries(data).slice(0, 20).map(([k,v]) => [k, String(v).slice(0, 100)]));
                        } catch(_) {}
                    } else {
                        const native = (tag.startsWith('ha-') ? dq(inp, 'input, textarea') : inp) || inp;
                        item.value = (native.value ?? '').toString().slice(0, 200);
                        const tp = native.getAttribute?.('type');
                        if (tp) item.input_type = tp;
                    }
                    if (inp.disabled || inp.hasAttribute?.('disabled')) item.disabled = true;
                    if (inp.required || inp.hasAttribute?.('required')) item.required = true;
                    if (inp.readOnly || inp.hasAttribute?.('readonly')) item.readonly = true;
                    if (label) item.hint = 'FrontendInspect type text="' + label.slice(0, 40) + '" value="..."';
                    bodyItems.push(item);
                }

                const SEL_TEXT = 'p, .secondary, [id*="description"], ha-alert, ha-markdown';
                const texts = dqAll(haDialog, SEL_TEXT, [], new Set());
                for (const t of texts) {
                    const txt = textOf(t);
                    if (txt && txt.length > 2) bodyItems.push({ element: 'text', text: txt });
                }

                const SEL_LIST = 'ha-list-item, mwc-list-item, ha-check-list-item, ha-clickable-list-item';
                const listItems = dqAll(haDialog, SEL_LIST, [], new Set());
                if (listItems.length) {
                    result.list_items = listItems.slice(0, 30).map(li => {
                        const o = { text: textOf(li).slice(0, 100) };
                        const rl = li.getAttribute('role');
                        if (rl) o.role = rl;
                        if (li.selected || li.activated || li.hasAttribute?.('selected') || li.hasAttribute?.('activated')) o.selected = true;
                        return o;
                    });
                }

                if (bodyItems.length) result.body = bodyItems;

                const footerBtns = [];
                const SEL_BTNS = 'ha-button, mwc-button, ha-icon-button, button';
                const ftr = dq(haDialog, 'ha-dialog-footer, [slot="footer"], footer');
                const btnRoot = ftr || haDialog;
                const btns = dqAll(btnRoot, SEL_BTNS, [], new Set());
                for (const b of btns) {
                    const txt = textOf(b);
                    const slot = b.getAttribute?.('slot') || '';
                    const item = {};
                    if (txt) item.text = txt.slice(0, 60);
                    if (slot.includes('primary')) item.role = 'primary';
                    else if (slot.includes('secondary')) item.role = 'secondary';
                    if (b.disabled || b.hasAttribute?.('disabled')) item.disabled = true;
                    if (b.getAttribute?.('data-dialog') === 'close') item.action = 'close';
                    const variant = b.getAttribute?.('variant');
                    if (variant === 'danger') item.variant = 'danger';
                    if (txt) item.hint = 'FrontendInspect tap text="' + txt.slice(0, 40) + '"';
                    if (Object.keys(item).length) footerBtns.push(item);
                }
                if (footerBtns.length) result.buttons = footerBtns;

                return result;
            }

            function captureAndSend(haDialogEl) {
                try {
                    const snap = extractDialog(haDialogEl);
                    if (!snap.title && !snap.body && !snap.buttons && !snap.list_items) return;
                    const dialogs = [snap];
                    window.__clawActiveDialogs = dialogs;
                    window.dispatchEvent(new CustomEvent('claw-dialog-opened', { detail: dialogs }));
                    getConn().sendMessagePromise({
                        type: 'ha_crack/dialog_snapshot',
                        dialogs
                    }).catch(() => {});
                } catch(_) {}
            }

            document.addEventListener('opened', (ev) => {
                const path = ev.composedPath?.() || [];
                let haDialog = null;
                for (const el of path) {
                    if (el.tagName?.toLowerCase() === 'ha-dialog') { haDialog = el; break; }
                }
                if (!haDialog) return;
                setTimeout(() => captureAndSend(haDialog), 250);
            }, true);

            document.addEventListener('closed', (ev) => {
                const path = ev.composedPath?.() || [];
                let isDialog = false;
                for (const el of path) {
                    if (el.tagName?.toLowerCase() === 'ha-dialog') { isDialog = true; break; }
                }
                if (!isDialog) return;
                window.__clawActiveDialogs = null;
                window.dispatchEvent(new CustomEvent('claw-dialog-closed'));
                try {
                    getConn().sendMessagePromise({
                        type: 'ha_crack/dialog_snapshot',
                        dialogs: []
                    }).catch(() => {});
                } catch(_) {}
            }, true);
        })();

    }

    function setupGoalContinuationStream() {}

    const _MARKED_CDN = 'https://cdn.jsdelivr.net/npm/marked@15.0.7/lib/marked.esm.js';
    let _markedReady = null;
    const _ensureMarked = () => {
        if (_markedReady) return _markedReady;
        _markedReady = import(_MARKED_CDN).then(mod => {
            const { marked } = mod;
            marked.setOptions({ gfm: true, breaks: true });
            window.__clawMarked = marked;
            return marked;
        }).catch(e => { console.warn('[Claw] marked load failed:', e); _markedReady = null; return null; });
        return _markedReady;
    };
    _ensureMarked();

    const _MD_CSS = `
        ha-markdown-element { word-break: break-word; overflow-wrap: break-word; overflow: hidden; font-size: 16px; line-height: 1.6; }
        .claw-md { font-size: 16px; line-height: 1.6; color: var(--primary-text-color, #1d1d1f); word-wrap: break-word; overflow-wrap: break-word; }
        .claw-md > :first-child { margin-top: 0; }
        .claw-md > :last-child { margin-bottom: 0; }
        .claw-md p { margin: 0.5em 0; }
        .claw-md h1, .claw-md h2, .claw-md h3, .claw-md h4, .claw-md h5, .claw-md h6 {
            margin: 0.8em 0 0.4em; font-weight: 600; line-height: 1.3;
        }
        .claw-md h1 { font-size: 1.25em; }
        .claw-md h2 { font-size: 1.15em; }
        .claw-md h3 { font-size: 1.05em; }
        .claw-md strong { font-weight: 600; }
        .claw-md em { font-style: italic; }
        .claw-md a { color: var(--primary-color, #03a9f4); text-decoration: none; }
        .claw-md a:hover { text-decoration: underline; }
        .claw-md ul, .claw-md ol { padding-left: 1.8em; margin: 0.4em 0; }
        .claw-md li { margin: 0.15em 0; }
        .claw-md li > p { margin: 0.2em 0; }
        .claw-md blockquote {
            margin: 0.5em 0; padding: 0.35em 0.75em;
            border-left: 3px solid var(--accent-color, var(--primary-color, #03a9f4));
            color: var(--secondary-text-color, #6b7280);
            background: var(--secondary-background-color, rgba(128,128,128,0.06));
            border-radius: 0 4px 4px 0;
        }
        .claw-md blockquote > :first-child { margin-top: 0; }
        .claw-md blockquote > :last-child { margin-bottom: 0; }
        .claw-md blockquote p { margin: 0.25em 0; }
        .claw-md code {
            font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', 'JetBrains Mono', ui-monospace, monospace;
            font-size: 0.88em; padding: 0.15em 0.35em;
            background: var(--code-editor-background-color, rgba(0,0,0,0.06));
            border-radius: 4px; color: var(--primary-text-color);
        }
        .claw-md pre {
            max-width: 100%; min-width: 0; box-sizing: border-box;
            margin: 0.5em 0; padding: 0.75em 1em; overflow-x: hidden;
            white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-word;
            background: var(--code-editor-background-color, #1e1e1e);
            color: #d4d4d4; border-radius: 6px; font-size: 0.85em; line-height: 1.5;
        }
        .claw-md pre code {
            display: block; max-width: 100%; white-space: inherit;
            overflow-wrap: anywhere; word-break: break-word;
            background: none; padding: 0; border-radius: 0;
            color: inherit; font-size: inherit;
        }
        .claw-md .table-wrap {
            max-width: 100%;
            min-width: 0;
            overflow-x: hidden;
            margin: 0.5em 0;
            -webkit-overflow-scrolling: touch;
        }
        .claw-md table {
            table-layout: fixed;
            border-collapse: collapse; width: 100%; max-width: 100%; min-width: 0;
            font-size: 0.9em; border: 1px solid var(--divider-color, #e0e0e0);
            border-radius: 4px;
        }
        .claw-md thead { background: var(--table-header-background-color, rgba(128,128,128,0.08)); }
        .claw-md th, .claw-md td {
            border: 1px solid var(--divider-color, #e0e0e0);
            padding: 0.45em 0.75em; text-align: left; white-space: normal;
            overflow-wrap: anywhere; word-break: break-word;
        }
        .claw-md th { font-weight: 600; font-size: 0.88em; }
        .claw-md tr:nth-child(even) { background: rgba(128,128,128,0.04); }
        .claw-md hr { border: none; border-top: 1px solid var(--divider-color, #e0e0e0); margin: 0.8em 0; }
        .claw-md img { max-width: 100%; border-radius: 6px; }
        .claw-user-md {
            max-width: 100%;
            min-width: 0;
            box-sizing: border-box;
            white-space: pre-wrap;
            overflow-wrap: anywhere;
            word-break: break-word;
            line-height: 1.45;
        }
        .claw-user-md code {
            font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', 'JetBrains Mono', ui-monospace, monospace;
            font-size: 0.88em;
            padding: 0.08em 0.28em;
            border-radius: 4px;
            white-space: pre-wrap;
            overflow-wrap: anywhere;
            word-break: break-word;
        }
        .claw-user-md pre {
            max-width: 100%;
            min-width: 0;
            box-sizing: border-box;
            margin: 0.35em 0;
            padding: 0.6em 0.75em;
            border-radius: 6px;
            overflow-x: hidden;
            white-space: pre-wrap;
            overflow-wrap: anywhere;
            word-break: break-word;
        }
        .claw-user-md pre code {
            display: block;
            padding: 0;
            background: none;
            border-radius: 0;
            color: inherit;
            font-size: inherit;
            white-space: inherit;
            overflow-wrap: anywhere;
            word-break: break-word;
        }
    `;

    let _mdRenderTimer = null;
    let _mdStreamActive = false;

    const _STREAM_CSS = ``;

    const _injectMdStyles = (msr) => {
        if (msr.getElementById('claw-md-rich')) return;
        const s = document.createElement('style');
        s.id = 'claw-md-rich';
        s.textContent = _MD_CSS + _STREAM_CSS;
        msr.appendChild(s);
    };

    const _normalize = (s) => s.replace(/\s+/g, '').replace(/[|>*#`~\[\]\-_]/g, '').slice(0, 80);

    const _escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
    }[c]));

    const _sanitizeUserText = (s) => String(s)
        .replace(/(^|\n)[ \t]{0,3}#{1,6}[ \t]*/g, '$1')
        .replace(/#{3,}/g, '')
        .replace(/\*\*/g, '')
        .replace(/__/g, '');

    const _countMarkdownTableCells = (line) => {
        const trimmed = String(line || '').trim();
        if (!trimmed.includes('|')) return 0;
        return trimmed.split('|').slice(1, -1).length;
    };

    const _isMarkdownTableSeparator = (line) => /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$/.test(line || '');
    const _splitLooseTableRow = (line) => String(line || '').trim().split(/\t+| {2,}/).filter(Boolean);
    const _isLooseTableSeparator = (line) => {
        const cells = _splitLooseTableRow(line);
        return cells.length > 1 && cells.every((cell) => /^:?-{1,}:?$/.test(cell));
    };

    const _normalizeAssistantTables = (raw) => {
        const lines = String(raw || '').replace(/\r\n?/g, '\n').split('\n');
        for (let i = 1; i < lines.length; i++) {
            const separator = lines[i].trim();
            const header = lines[i - 1].trim();
            if (_isLooseTableSeparator(separator) && !header.includes('|')) {
                const headerCells = _splitLooseTableRow(header);
                const columnCount = Math.max(headerCells.length, _splitLooseTableRow(separator).length);
                if (columnCount > 1) {
                    const fillCells = (cells) => Array.from({ length: columnCount }, (_, idx) => cells[idx] || (idx === 0 ? '项目' : '内容'));
                    lines[i - 1] = '| ' + fillCells(headerCells).join(' | ') + ' |';
                    lines[i] = '| ' + Array.from({ length: columnCount }, () => '---').join(' | ') + ' |';
                    for (let j = i + 1; j < lines.length; j++) {
                        const rowCells = _splitLooseTableRow(lines[j]);
                        if (rowCells.length < 2) break;
                        lines[j] = '| ' + fillCells(rowCells).join(' | ') + ' |';
                    }
                }
                continue;
            }
            if (!header.includes('|') || !_isMarkdownTableSeparator(separator)) continue;

            let columnCount = Math.max(_countMarkdownTableCells(header), _countMarkdownTableCells(separator));
            for (let j = i + 1; j < lines.length; j++) {
                const row = lines[j].trim();
                if (!row.includes('|') || _isMarkdownTableSeparator(row)) break;
                columnCount = Math.max(columnCount, _countMarkdownTableCells(row));
            }
            if (columnCount < 1) continue;

            const headerCells = _countMarkdownTableCells(header);
            if (/^\|+\s*$/.test(header) || headerCells !== columnCount) {
                lines[i - 1] = '| ' + Array.from({ length: columnCount }, (_, idx) => idx === 0 ? '项目' : '内容').join(' | ') + ' |';
            }
            if (_countMarkdownTableCells(separator) !== columnCount) {
                lines[i] = '| ' + Array.from({ length: columnCount }, () => '---').join(' | ') + ' |';
            }
        }
        return lines.join('\n');
    };

    const _renderUserInline = (text) => {
        const clean = _sanitizeUserText(text);
        let html = '';
        let last = 0;
        const re = /`([^`\n]+)`/g;
        let match;
        while ((match = re.exec(clean))) {
            html += _escapeHtml(clean.slice(last, match.index));
            html += '<code>' + _escapeHtml(match[1]) + '</code>';
            last = re.lastIndex;
        }
        html += _escapeHtml(clean.slice(last));
        return html;
    };

    const _renderUserMessageHtml = (raw) => {
        const text = String(raw || '').replace(/\r\n?/g, '\n');
        const fenceRe = /```([^\n`]*)\n?([\s\S]*?)(?:```|$)/g;
        let html = '';
        let last = 0;
        let match;
        while ((match = fenceRe.exec(text))) {
            const before = text.slice(last, match.index);
            if (_sanitizeUserText(before).trim()) {
                html += '<div>' + _renderUserInline(before) + '</div>';
            }
            html += '<pre><code>' + _escapeHtml(match[2]) + '</code></pre>';
            last = fenceRe.lastIndex;
        }
        const rest = text.slice(last);
        if (_sanitizeUserText(rest).trim() || !html) {
            html += '<div>' + _renderUserInline(rest) + '</div>';
        }
        return '<div class="claw-user-md">' + html + '</div>';
    };

    const _findRawText = (mdContent, hassTexts, used) => {
        if (!mdContent || !hassTexts.length) return null;
        const needle = _normalize(mdContent);
        if (!needle) return null;
        let bestIdx = -1, bestScore = 0;
        for (let i = 0; i < hassTexts.length; i++) {
            if (used.has(i)) continue;
            const hay = _normalize(hassTexts[i]);
            if (!hay) continue;
            if (hay === needle) { bestIdx = i; bestScore = Infinity; break; }
            const short = needle.length < hay.length ? needle : hay;
            const long = needle.length < hay.length ? hay : needle;
            let score = 0;
            for (let j = 0; j < short.length && j < 60; j++) {
                if (short[j] === long[j]) score++;
            }
            if (score > bestScore) { bestScore = score; bestIdx = i; }
        }
        if (bestIdx >= 0 && bestScore >= Math.min(needle.length, 8)) {
            used.add(bestIdx);
            return hassTexts[bestIdx];
        }
        return null;
    };

    let _renderLock = false;

    const _renderFinal = async () => {
        if (_renderLock) return;
        const chat = deepQuery('ha-assist-chat');
        const sr = chat?.shadowRoot;
        if (!sr) return;
        const marked = window.__clawMarked || await _ensureMarked();
        const conv = chat._conversation;
        const hassTexts = [];
        if (Array.isArray(conv)) {
            for (const m of conv) {
                if (m.who === 'hass' && m.text) hassTexts.push(m.text);
            }
        }
        const used = new Set();
        const msgEls = sr.querySelectorAll('.message');
        let isFirst = true;
        const obs = sr.__clawMdObs;
        _renderLock = true;
        if (obs) obs.disconnect();
        try {
        msgEls.forEach(msgEl => {
            const isHass = msgEl.classList.contains('hass');
            const isUser = msgEl.classList.contains('user');
            const md = msgEl.querySelector('ha-markdown');
            if (!md) return;
            const msr = md.shadowRoot;
            if (!msr) return;
            if (isHass && isFirst) {
                isFirst = false;
                return;
            }
            const el = msr.querySelector('ha-markdown-element');
            if (!el) return;
            if (isUser) {
                const rawContent = md.content;
                if (!rawContent || typeof rawContent !== 'string') return;
                const sig = rawContent.length + '_' + rawContent.slice(0, 120);
                if (el.__clawUserMdSig === sig) return;
                el.__clawUserMdSig = sig;
                _injectMdStyles(msr);
                el.innerHTML = _renderUserMessageHtml(rawContent);
                return;
            }
            if (!isHass) return;
            if (!marked) return;
            const displayContent = md.content;
            const rawContent = _findRawText(displayContent, hassTexts, used) || displayContent;
            if (!rawContent || typeof rawContent !== 'string') return;
            if (rawContent.length < 60 && !rawContent.includes('\n') && !rawContent.includes('|') && !/[#*`|~\[\]>-]{2}/.test(rawContent)) return;
            const sig = rawContent.length + '_' + rawContent.slice(0, 120);
            if (el.__clawMdSig === sig) return;
            el.__clawMdSig = sig;
            _injectMdStyles(msr);
            try {
                let html = marked.parse(_normalizeAssistantTables(rawContent));
                html = html.replace(/<table>/g, '<div class="table-wrap"><table>').replace(/<\/table>/g, '</table></div>');
                el.innerHTML = '<div class="claw-md">' + html + '</div>';
            } catch(e) {}
        });
        } finally {
            if (obs) obs.observe(sr, { childList: true, subtree: true });
            _renderLock = false;
        }
    };

    const _markStreaming = () => {
        const chat = deepQuery('ha-assist-chat');
        const sr = chat?.shadowRoot;
        if (!sr) return;
        const mdList = sr.querySelectorAll('ha-markdown');
        if (!mdList.length) return;
        const last = mdList[mdList.length - 1];
        const msr = last.shadowRoot;
        if (!msr) return;
        _injectMdStyles(msr);
        const el = msr.querySelector('ha-markdown-element');
        if (el) el.classList.add('claw-streaming');
    };

    const _scheduleRender = () => {
        if (_mdRenderTimer) clearTimeout(_mdRenderTimer);
        if (_mdStreamActive) {
            _markStreaming();
            _mdRenderTimer = setTimeout(() => _renderFinal(), 600);
        } else {
            _mdRenderTimer = setTimeout(() => _renderFinal(), 80);
        }
    };

    const _injectOnce = (root, id, css) => {
        if (!root || root.getElementById(id)) return;
        const s = document.createElement('style'); s.id = id; s.textContent = css;
        root.appendChild(s);
    };

    const _patchMarkdown = () => {
        const chat = deepQuery('ha-assist-chat');
        const sr = chat?.shadowRoot;
        if (!sr) return;
        _injectOnce(sr, 'claw-chat-font', `
            #message-input::part(wa-input) { font-size: 16px !important; }
            .message { font-size: 16px !important; }
            .message.user {
                min-width: 0 !important;
                max-width: 100% !important;
                overflow-wrap: anywhere !important;
                word-break: break-word !important;
            }
            .message.user ha-markdown {
                min-width: 0 !important;
                max-width: 100% !important;
                overflow: hidden !important;
                overflow-wrap: anywhere !important;
                word-break: break-word !important;
            }
        `);
        _scheduleRender();
        if (!sr.__clawMdObs) {
            sr.__clawMdObs = new MutationObserver(() => {
                if (_mdStreamActive) {
                    _markStreaming();
                    if (_mdRenderTimer) clearTimeout(_mdRenderTimer);
                    _mdRenderTimer = setTimeout(() => _renderFinal(), 600);
                } else {
                    _scheduleRender();
                }
            });
            sr.__clawMdObs.observe(sr, { childList: true, subtree: true });
        }
    };

    window.addEventListener('claw-chat-updated', () => _patchMarkdown());

    const _origStreamHook = window.__clawOnStreamDelta;
    window.__clawOnStreamDelta = (delta) => {
        _mdStreamActive = true;
        if (_mdRenderTimer) clearTimeout(_mdRenderTimer);
        _mdRenderTimer = setTimeout(() => {
            _mdStreamActive = false;
            _renderFinal();
        }, 600);
        _markStreaming();
        if (typeof _origStreamHook === 'function') _origStreamHook(delta);
    };

    const _origStreamEnd = window.__clawOnStreamEnd;
    window.__clawOnStreamEnd = () => {
        _mdStreamActive = false;
        if (_mdRenderTimer) clearTimeout(_mdRenderTimer);
        setTimeout(() => _renderFinal(), 150);
        if (typeof _origStreamEnd === 'function') _origStreamEnd();
        if (typeof window.__clawPlaySound === 'function') window.__clawPlaySound('stream-complete');
    };

    function setupAssistRightDock(hass) {
        if (window.__clawAssistDockInstalled) return;
        window.__clawAssistDockInstalled = true;

        let _sidebarDockEnabled = true;
        let _initialSettingsLoaded = false;
        const refreshDockSetting = async () => {
            try {
                const r = await hass.connection.sendMessagePromise({ type: 'ha_crack/get_settings' });
                const newVal = r?.enable_sidebar_dock !== false;
                if (_initialSettingsLoaded && newVal !== _sidebarDockEnabled) {
                    window.__clawAssistDockInstalled = false;
                    try { sessionStorage.removeItem('clawDockState'); } catch(e) {}
                    location.reload();
                    return;
                }
                _sidebarDockEnabled = newVal;
                _initialSettingsLoaded = true;
            } catch(e) {}
        };
        refreshDockSetting();
        hass.connection.subscribeEvents(() => refreshDockSetting(), 'ha_crack_settings_changed').catch(() => {});

        const DOCK_ID = 'claw-assist-dock';
        const DOCK_STYLE_ID = 'claw-assist-dock-style';
        const DOCK_W = 'min(460px, 38vw)';

        const getMainSR = () => {
            const home = document.querySelector('home-assistant');
            return home?.shadowRoot?.querySelector('home-assistant-main')?.shadowRoot || null;
        };

        const ensureDock = () => {
            const msr = getMainSR();
            if (!msr) return null;

            if (!msr.getElementById(DOCK_STYLE_ID)) {
                const s = document.createElement('style');
                s.id = DOCK_STYLE_ID;
                s.textContent = `
                    :host {
                        --claw-dock-width: ${DOCK_W};
                    }
                    ha-drawer {
                        transition: padding-right .25s cubic-bezier(.4,0,.2,1) !important;
                    }
                    :host([dock-open]) ha-drawer {
                        padding-right: var(--claw-dock-width) !important;
                    }
                    #${DOCK_ID} {
                        position: fixed;
                        top: 0;
                        right: 0;
                        width: 0;
                        height: 100vh;
                        display: flex;
                        flex-direction: column;
                        overflow: hidden;
                        background: var(--card-background-color, var(--primary-background-color, #fff));
                        --primary-background-color: var(--card-background-color, #fff);
                        transition: width .25s cubic-bezier(.4,0,.2,1);
                        box-shadow: -1px 0 0 0 var(--divider-color, rgba(0,0,0,.12));
                        z-index: 100;
                    }
                    #${DOCK_ID}[open] {
                        width: var(--claw-dock-width);
                    }
                    #${DOCK_ID} .dock-header {
                        flex: 0 0 auto;
                        display: flex;
                        align-items: center;
                        height: var(--header-height, 56px);
                        min-height: var(--header-height, 56px);
                        padding: 0 10px 0 16px;
                        background: var(--sidebar-menu-button-background-color, inherit);
                        color: var(--sidebar-menu-button-text-color, var(--primary-text-color));
                        font-family: var(--ha-font-family-body, Roboto, Noto, sans-serif);
                        font-size: var(--ha-font-size-xl, 20px);
                        font-weight: var(--ha-font-weight-normal, 400);
                        line-height: 1;
                        box-sizing: border-box;
                        -webkit-font-smoothing: antialiased;
                        border-bottom: 1px solid var(--divider-color, rgba(0,0,0,.12));
                        touch-action: none;
                        user-select: none;
                        -webkit-user-select: none;
                    }
                    #${DOCK_ID} .dock-header .dock-title {
                        flex: 1 1 auto;
                        min-width: 0;
                        display: flex;
                        align-items: center;
                        gap: 0;
                        overflow: hidden;
                    }
                    #${DOCK_ID} .dock-header .dock-title > [slot="title"] {
                        display: flex;
                        align-items: center;
                        gap: 4px;
                        margin-left: 5px;
                        font-size: inherit;
                        font-weight: inherit;
                        white-space: nowrap;
                        overflow: hidden;
                    }
                    #${DOCK_ID} .dock-header .dock-title.dock-history-title-visible,
                    #${DOCK_ID} .dock-header .dock-title.dock-history-title-visible > [slot="title"] {
                        overflow: visible;
                    }
                    #${DOCK_ID} .dock-header .dock-title ha-dropdown,
                    #${DOCK_ID} .dock-header .dock-title ha-button-menu,
                    #${DOCK_ID} .dock-header .dock-title wa-dropdown {
                        --ha-select-height: 32px;
                    }
                    #${DOCK_ID} .dock-header .dock-title a.claw-history-menu-item {
                        text-decoration: none;
                        color: var(--primary-text-color);
                    }
                    #${DOCK_ID} .dock-header .dock-title a {
                        text-decoration: none;
                        color: var(--primary-text-color);
                    }
                    #${DOCK_ID} .dock-header .dock-title .claw-history-menu-item ha-svg-icon,
                    #${DOCK_ID} .dock-header .dock-title .claw-history-menu-item ha-icon-next {
                        color: var(--secondary-text-color);
                    }
                    #${DOCK_ID} .dock-header .dock-btn {
                        flex: 0 0 auto;
                        width: 40px;
                        height: 40px;
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        cursor: pointer;
                        border-radius: 50%;
                        color: inherit;
                        opacity: 0.85;
                        transition: opacity .15s, background .15s;
                        -webkit-tap-highlight-color: transparent;
                        border: none;
                        background: none;
                        padding: 0;
                    }
                    #${DOCK_ID} .dock-header .dock-btn:hover {
                        opacity: 1;
                        background: var(--secondary-background-color, rgba(255,255,255,.12));
                    }
                    #${DOCK_ID} .dock-header .dock-back-btn {
                        transform: translateX(-8px);
                        position: relative;
                        z-index: 3;
                    }
                    #${DOCK_ID} .dock-header .dock-btn svg {
                        width: 20px;
                        height: 20px;
                        fill: var(--icon-primary-color, currentColor);
                    }
                    #${DOCK_ID} .dock-history-panel {
                        flex: 1 1 auto;
                        min-height: 0;
                        display: flex;
                        flex-direction: column;
                        overflow: hidden;
                    }
                    #${DOCK_ID} .dock-history-panel .hist-search {
                        padding: 16px 18px 10px;
                        flex: 0 0 auto;
                    }
                    #${DOCK_ID} .dock-history-panel .hist-search input {
                        width: 100%;
                        box-sizing: border-box;
                        padding: 10px 14px 10px 40px;
                        border: 1px solid var(--divider-color, rgba(0,0,0,.12));
                        border-radius: 22px;
                        background: var(--input-fill-color, var(--secondary-background-color, #f5f5f5));
                        color: var(--primary-text-color);
                        font-size: 15px;
                        outline: none;
                        font-family: inherit;
                        transition: border-color .2s;
                    }
                    #${DOCK_ID} .dock-history-panel .hist-search input:focus {
                        border-color: var(--primary-color, #03a9f4);
                    }
                    #${DOCK_ID} .dock-history-panel .hist-search-wrap {
                        position: relative;
                    }
                    #${DOCK_ID} .dock-history-panel .hist-search-wrap svg {
                        position: absolute;
                        left: 13px;
                        top: 50%;
                        transform: translateY(-50%);
                        width: 18px;
                        height: 18px;
                        fill: var(--secondary-text-color, #666);
                        pointer-events: none;
                    }
                    #${DOCK_ID} .dock-history-panel .hist-list {
                        flex: 1 1 auto;
                        overflow-y: auto;
                        overscroll-behavior: contain;
                        padding: 8px 12px 18px;
                    }
                    #${DOCK_ID} .dock-history-panel .hist-section {
                        margin-bottom: 4px;
                    }
                    #${DOCK_ID} .dock-history-panel .hist-section-label {
                        padding: 12px 10px 8px;
                        font-size: 12px;
                        font-weight: 500;
                        cursor: pointer;
                        display: flex;
                        align-items: center;
                        gap: 6px;
                        user-select: none;
                        text-transform: uppercase;
                        letter-spacing: 0.05em;
                        color: var(--secondary-text-color, #666);
                        transition: color .15s;
                    }
                    #${DOCK_ID} .dock-history-panel .hist-section-label:hover {
                        color: var(--primary-text-color);
                    }
                    #${DOCK_ID} .dock-history-panel .hist-section-chevron {
                        width: 18px;
                        height: 18px;
                        fill: currentColor;
                        transition: transform .2s ease;
                        flex-shrink: 0;
                    }
                    #${DOCK_ID} .dock-history-panel .hist-section.collapsed .hist-section-chevron {
                        transform: rotate(-90deg);
                    }
                    #${DOCK_ID} .dock-history-panel .hist-section-count {
                        font-weight: 400;
                        opacity: 0.7;
                    }
                    #${DOCK_ID} .dock-history-panel .hist-section-items {
                        overflow: hidden;
                        transition: max-height .25s ease;
                        max-height: 2000px;
                    }
                    #${DOCK_ID} .dock-history-panel .hist-section.collapsed .hist-section-items {
                        max-height: 0;
                    }
                    #${DOCK_ID} .dock-history-panel .hist-item.search-match .hist-item-title {
                        color: var(--primary-color, #03a9f4);
                    }
                    #${DOCK_ID} .dock-history-panel .hist-item.search-match .hist-item-icon::before {
                        background: var(--primary-color, #03a9f4);
                    }
                    #${DOCK_ID} .dock-history-panel .hist-item {
                        padding: 14px 14px;
                        margin: 4px 0;
                        cursor: pointer;
                        position: relative;
                        display: flex;
                        align-items: flex-start;
                        gap: 14px;
                        border-radius: 14px;
                    }
                    #${DOCK_ID} .dock-history-panel .hist-item::before {
                        content: "";
                        position: absolute;
                        inset: 0;
                        border-radius: 14px;
                        background: transparent;
                        transition: background .15s;
                        pointer-events: none;
                    }
                    #${DOCK_ID} .dock-history-panel .hist-item:hover::before {
                        background: color-mix(in srgb, var(--secondary-background-color, rgba(0,0,0,.04)) 88%, var(--primary-color, #03a9f4) 12%);
                    }
                    #${DOCK_ID} .dock-history-panel .hist-item.active::before {
                        background: var(--sidebar-selected-background-color, var(--secondary-background-color));
                    }
                    #${DOCK_ID} .dock-history-panel .hist-item-icon,
                    #${DOCK_ID} .dock-history-panel .hist-item-content,
                    #${DOCK_ID} .dock-history-panel .hist-item-actions {
                        position: relative;
                        z-index: 1;
                    }
                    #${DOCK_ID} .dock-history-panel .hist-item-icon {
                        flex: 0 0 auto;
                        width: 20px;
                        height: 42px;
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        position: relative;
                        color: var(--secondary-text-color, #666);
                        opacity: .68;
                    }
                    #${DOCK_ID} .dock-history-panel .hist-item-icon::before {
                        content: "";
                        width: 2px;
                        height: 28px;
                        border-radius: 999px;
                        background: color-mix(in srgb, var(--primary-color, #03a9f4) 34%, var(--divider-color, rgba(0,0,0,.14)));
                    }
                    #${DOCK_ID} .dock-history-panel .hist-item:hover .hist-item-icon,
                    #${DOCK_ID} .dock-history-panel .hist-item.active .hist-item-icon {
                        opacity: 1;
                    }
                    #${DOCK_ID} .dock-history-panel .hist-item.active .hist-item-icon::before {
                        width: 3px;
                        background: var(--sidebar-selected-icon-color, var(--primary-color));
                    }
                    #${DOCK_ID} .dock-history-panel .hist-item-icon svg {
                        display: none;
                    }
                    #${DOCK_ID} .dock-history-panel .hist-item-content {
                        flex: 1 1 auto;
                        min-width: 0;
                    }
                    #${DOCK_ID} .dock-history-panel .hist-item-title {
                        font-size: 15px;
                        font-weight: 400;
                        color: var(--primary-text-color);
                        white-space: nowrap;
                        overflow: hidden;
                        text-overflow: ellipsis;
                    }
                    #${DOCK_ID} .dock-history-panel .hist-item.active .hist-item-title {
                        color: var(--sidebar-selected-text-color, var(--primary-text-color));
                    }
                    #${DOCK_ID} .dock-history-panel .hist-item-meta {
                        font-size: 13px;
                        color: var(--secondary-text-color, #999);
                        margin-top: 4px;
                    }
                    #${DOCK_ID} .dock-history-panel .hist-item.active .hist-item-meta {
                        color: var(--sidebar-selected-text-color, var(--secondary-text-color));
                        opacity: .72;
                    }
                    #${DOCK_ID} .dock-history-panel .hist-item-actions {
                        flex: 0 0 auto;
                        opacity: 0;
                        display: flex;
                        align-items: center;
                        justify-content: flex-end;
                        gap: 2px;
                        margin-left: 8px;
                        transition: opacity .15s;
                    }
                    #${DOCK_ID} .dock-history-panel .hist-item:hover .hist-item-actions {
                        opacity: 1;
                    }
                    #${DOCK_ID} .dock-history-panel .hist-item-actions button {
                        width: 30px;
                        height: 30px;
                        background: var(--card-background-color, rgba(255,255,255,.06));
                        border: 1px solid var(--divider-color, rgba(0,0,0,.08));
                        cursor: pointer;
                        padding: 0;
                        border-radius: 8px;
                        color: var(--secondary-text-color, #888);
                        display: inline-flex;
                        align-items: center;
                        justify-content: center;
                        transition: all .15s ease;
                        box-shadow: 0 1px 2px rgba(0,0,0,.04);
                    }
                    #${DOCK_ID} .dock-history-panel .hist-item-actions button:active {
                        transform: scale(0.92);
                    }
                    #${DOCK_ID} .dock-history-panel .hist-item-actions .hist-delete-btn:hover {
                        background: color-mix(in srgb, var(--error-color, #db4437) 12%, var(--card-background-color, #fff));
                        border-color: color-mix(in srgb, var(--error-color, #db4437) 30%, transparent);
                        color: var(--error-color, #db4437);
                    }
                    #${DOCK_ID} .dock-history-panel .hist-item-actions .hist-pin-btn:hover {
                        background: color-mix(in srgb, var(--primary-color, #03a9f4) 12%, var(--card-background-color, #fff));
                        border-color: color-mix(in srgb, var(--primary-color, #03a9f4) 30%, transparent);
                        color: var(--primary-color, #03a9f4);
                    }
                    #${DOCK_ID} .dock-history-panel .hist-item-actions .hist-pin-btn.pinned {
                        color: var(--primary-color, #03a9f4);
                        opacity: 1;
                    }
                    #${DOCK_ID} .dock-history-panel .hist-item.pinned .hist-item-icon::before {
                        background: var(--primary-color, #03a9f4);
                    }
                    #${DOCK_ID} .dock-history-panel .hist-item-actions button svg {
                        width: 16px;
                        height: 16px;
                        fill: currentColor;
                        flex-shrink: 0;
                    }
                    #${DOCK_ID} .dock-history-panel .hist-empty {
                        display: flex;
                        flex-direction: column;
                        align-items: center;
                        justify-content: center;
                        padding: 48px 16px;
                        color: var(--secondary-text-color, #999);
                        text-align: center;
                        gap: 8px;
                    }
                    #${DOCK_ID} .dock-history-panel .hist-empty svg {
                        width: 48px;
                        height: 48px;
                        fill: var(--disabled-text-color, #ccc);
                    }
                    #${DOCK_ID} .dock-body {
                        flex: 1 1 auto;
                        min-height: 0;
                        display: flex;
                        flex-direction: column;
                        overflow-y: auto;
                        overflow-x: hidden;
                        overscroll-behavior: contain;
                    }
                    #${DOCK_ID} .dock-body ha-assist-chat {
                        flex: 1 1 auto;
                        width: 100%;
                        margin: 0;
                        min-height: 0;
                        display: flex;
                        flex-direction: column;
                    }
                    #${DOCK_ID} .dock-resize {
                        position: absolute;
                        left: -6px;
                        top: 0;
                        width: 12px;
                        height: 100%;
                        cursor: col-resize;
                        z-index: 1;
                        display: flex;
                        align-items: center;
                        justify-content: center;
                    }
                    #${DOCK_ID} .dock-resize::before {
                        content: '';
                        width: 4px;
                        height: 40px;
                        border-radius: 4px;
                        background: var(--divider-color, rgba(0,0,0,.15));
                        opacity: 0;
                        transition: opacity .25s ease, transform .25s ease, height .25s ease;
                        transform: scaleY(0.6);
                    }
                    #${DOCK_ID} .dock-resize:hover::before,
                    #${DOCK_ID} .dock-resize.active::before {
                        opacity: 1;
                        transform: scaleY(1);
                    }
                    #${DOCK_ID} .dock-resize.active::before {
                        background: var(--primary-color, #03a9f4);
                        height: 56px;
                        box-shadow: 0 0 8px rgba(3,169,244,.3);
                    }
                    #${DOCK_ID}[resizing] {
                        transition: none !important;
                        user-select: none;
                    }
                    :host([dock-resizing]) ha-drawer {
                        transition: none !important;
                    }
                    #${DOCK_ID}[resizing] .dock-resize::before {
                        opacity: 1;
                        transform: scaleY(1);
                        background: var(--primary-color, #03a9f4);
                        height: 56px;
                    }
                    @media (max-width: 870px) {
                        #${DOCK_ID}[open] {
                            position: fixed;
                            inset: 0;
                            width: 100vw;
                            z-index: 100;
                        }
                        #${DOCK_ID} .dock-resize { display: none; }
                    }
                `;
                msr.appendChild(s);
            }

            let dock = msr.getElementById(DOCK_ID);
            if (!dock) {
                dock = document.createElement('div');
                dock.id = DOCK_ID;
                dock.innerHTML = '<div class="dock-resize"></div><div class="dock-header"><div class="dock-title">Assist</div><button class="dock-btn dock-close" title="Close"><svg viewBox="0 0 24 24"><path d="M19,6.41L17.59,5L12,10.59L6.41,5L5,6.41L10.59,12L5,17.59L6.41,19L12,13.41L17.59,19L19,17.59L13.41,12L19,6.41Z"/></svg></button></div><div class="dock-body"></div><div class="dock-history-panel" style="display:none"></div>';
                msr.appendChild(dock);

                const handle = dock.querySelector('.dock-resize');
                let longPressTimer = null;
                let dragging = false;

                const activate = () => { handle.classList.add('active'); };
                const deactivate = () => { if (!dragging) handle.classList.remove('active'); };

                handle.addEventListener('pointerdown', (e) => {
                    longPressTimer = setTimeout(() => {
                        longPressTimer = null;
                        dragging = true;
                        activate();
                        dock.setAttribute('resizing', '');
                        document.body.style.cursor = 'col-resize';
                        document.body.style.userSelect = 'none';

                        const hostEl = document.querySelector('home-assistant')?.shadowRoot?.querySelector('home-assistant-main');
                        if (hostEl) hostEl.setAttribute('dock-resizing', '');
                        const wasNarrow = hostEl?.hasAttribute('narrow') || false;
                        const onMove = (ev) => {
                            const w = Math.max(280, Math.min(window.innerWidth * 0.8, window.innerWidth - ev.clientX));
                            if (hostEl) {
                                hostEl.style.setProperty('--claw-dock-width', w + 'px');
                                hostEl.style.setProperty('--mdc-top-app-bar-width', 'calc(100% - var(--mdc-drawer-width, 0px) - ' + w + 'px)');
                                const remaining = window.innerWidth - w;
                                if (remaining < 870 && !hostEl.narrow) {
                                    hostEl.narrow = true;
                                } else if (remaining >= 870 && !wasNarrow && hostEl.narrow) {
                                    hostEl.narrow = false;
                                }
                            }
                        };
                        const onUp = () => {
                            dragging = false;
                            dock.removeAttribute('resizing');
                            if (hostEl) hostEl.removeAttribute('dock-resizing');
                            document.body.style.cursor = '';
                            document.body.style.userSelect = '';
                            handle.classList.remove('active');
                            document.removeEventListener('pointermove', onMove);
                            document.removeEventListener('pointerup', onUp);
                        };
                        document.addEventListener('pointermove', onMove);
                        document.addEventListener('pointerup', onUp);
                    }, 500);
                });
                handle.addEventListener('pointerup', () => { if (longPressTimer) { clearTimeout(longPressTimer); longPressTimer = null; } });
                handle.addEventListener('pointercancel', () => { if (longPressTimer) { clearTimeout(longPressTimer); longPressTimer = null; } });
                handle.addEventListener('pointerenter', activate);
                handle.addEventListener('pointerleave', deactivate);
            }
            return dock;
        };

        let _dockActive = false;
        let _dockVoiceEl = null;
        let _userClose = false;

        let _fabSyncTimer = null;
        let _historySelectionHighlightConsumed = false;
        let _historySelectedConversationId = '';

        const _nudgeFabs = (offset) => {
            const val = offset ? offset + 'px' : '';
            const msr = getMainSR();
            if (!msr) return;
            const walk = (node) => {
                if (!node?.shadowRoot) return;
                try {
                    const fab = node.shadowRoot.getElementById('fab');
                    if (fab) {
                        fab.style.transition = 'right .25s cubic-bezier(.4,0,.2,1), inset-inline-end .25s cubic-bezier(.4,0,.2,1)';
                        fab.style.right = val || '';
                        fab.style.insetInlineEnd = val || '';
                    }
                    node.shadowRoot.querySelectorAll('*').forEach(c => {
                        if (c.shadowRoot) walk(c);
                    });
                } catch(_) {}
            };
            try {
                msr.querySelectorAll('hass-tabs-subpage, *').forEach(el => {
                    if (el.localName === 'hass-tabs-subpage' || el.shadowRoot) walk(el);
                });
            } catch(_) {}
        };

        const _startFabSync = () => {
            const dockEl = getMainSR()?.getElementById(DOCK_ID);
            const w = (dockEl?.offsetWidth || parseInt(DOCK_W) || 460) + 10;
            _nudgeFabs(w);
            if (_fabSyncTimer) clearInterval(_fabSyncTimer);
            _fabSyncTimer = setInterval(() => {
                if (!_dockActive) return;
                const d = getMainSR()?.getElementById(DOCK_ID);
                _nudgeFabs((d?.offsetWidth || (w - 10)) + 10);
            }, 1500);
        };

        const _stopFabSync = () => {
            if (_fabSyncTimer) { clearInterval(_fabSyncTimer); _fabSyncTimer = null; }
            _nudgeFabs(0);
        };

        window.addEventListener('location-changed', () => {
            if (_dockActive) setTimeout(_startFabSync, 500);
        });

        const neutralizeVoiceDialog = (voiceEl) => {
            const sr = voiceEl?.shadowRoot;
            if (!sr) return;
            const d = sr.querySelector('ha-dialog');
            if (!d) return;
            d.style.display = 'none';
            d.removeAttribute('open');
            try { if (d.open) d.close(); } catch(_) {}
            if (!d.__clawNeutralized) {
                d.__clawNeutralized = true;
                const origShow = d.show;
                d.show = function() {
                    if (_dockActive) return;
                    return origShow?.call(this);
                };
            }
        };

        const _installHistoryMenuItem = (dock, voiceEl) => {
            const titleEl = dock.querySelector('.dock-title');
            const menu = titleEl?.querySelector('wa-dropdown, ha-button-menu, ha-dropdown');
            if (!menu || menu.__clawHistoryMenuBound) return;
            menu.__clawHistoryMenuBound = true;

            let item;
            if (menu.localName === 'ha-dropdown') {
                const wrapper = document.createElement('a');
                wrapper.href = 'javascript:void(0)';
                wrapper.classList.add('claw-history-menu-item');
                item = document.createElement('ha-dropdown-item');
                item.setAttribute('variant', 'default');
                item.setAttribute('size', 'medium');
                item.setAttribute('type', 'normal');
                item.innerHTML = historyText('history') + ' <ha-icon-next slot="details"></ha-icon-next>';
                wrapper.appendChild(item);
                wrapper.addEventListener('click', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    menu.open = false;
                    _toggleHistoryPanel(dock, voiceEl);
                });
                wrapper.addEventListener('wa-select', (e) => e.stopPropagation());
                menu.append(wrapper);
            } else if (menu.localName === 'wa-dropdown') {
                item = document.createElement('wa-dropdown-item');
                item.textContent = historyText('history');
                item.classList.add('claw-history-menu-item');
                item.addEventListener('click', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    menu.open = false;
                    _toggleHistoryPanel(dock, voiceEl);
                });
                item.addEventListener('wa-select', (e) => e.stopPropagation());
                menu.append(item);
            } else {
                item = document.createElement('ha-list-item');
                item.setAttribute('graphic', 'icon');
                item.innerHTML = '<ha-svg-icon slot="graphic" path="M13,3A9,9 0 0,0 4,12H1L4.89,15.89L4.96,16.03L9,12H6A7,7 0 0,1 13,5A7,7 0 0,1 20,12A7,7 0 0,1 13,19C11.07,19 9.32,18.21 8.06,16.94L6.64,18.36C8.27,19.99 10.51,21 13,21A9,9 0 0,0 22,12A9,9 0 0,0 13,3M12.5,8V12.25L16.5,14.33L17.21,13.06L13.75,11.33V8H12.5Z"></ha-svg-icon>' + historyText('history');
                item.classList.add('claw-history-menu-item');
                item.addEventListener('click', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    menu.open = false;
                    _toggleHistoryPanel(dock, voiceEl);
                });
                item.addEventListener('selected', (e) => e.stopPropagation());
                item.addEventListener('request-selected', (e) => e.stopPropagation());
                menu.append(item);
            }
        };

        const grabChat = (voiceEl) => {
            const dock = ensureDock();
            if (!dock) return false;
            const sr = voiceEl?.shadowRoot;
            if (!sr) return false;
            const chat = sr.querySelector('ha-assist-chat');
            if (!chat) return false;
            _historyVisible = false;

            const dockHeader = dock.querySelector('.dock-header');
            const titleEl = dockHeader.querySelector('.dock-title');
            titleEl?.classList.remove('dock-history-title-visible');
            if (titleEl && titleEl.__clawOrigTitleNodes) {
                titleEl.replaceChildren(...titleEl.__clawOrigTitleNodes);
            }
            if (titleEl && !titleEl.__clawOrigTitleNodes && !titleEl.querySelector('[slot="title"]')) {
                const dialogHeader = sr.querySelector('ha-dialog-header');
                if (dialogHeader) {
                    const titleSlot = dialogHeader.querySelector('[slot="title"]');
                    if (titleSlot) {
                        titleEl.replaceChildren(titleSlot);
                    }
                }
            }
            _installHistoryMenuItem(dock, voiceEl);

            const dockBody = dock.querySelector('.dock-body');
            const historyPanel = dock.querySelector('.dock-history-panel');
            if (historyPanel) historyPanel.style.display = 'none';
            if (dockBody) dockBody.style.display = '';
            if (dockBody && dockBody.firstElementChild !== chat) {
                dockBody.innerHTML = '';
                dockBody.appendChild(chat);
            }
            _dockedChat = chat;
            dock.setAttribute('open', '');

            if (dock.__clawHassSync) clearInterval(dock.__clawHassSync);
            dock.__clawHassSync = setInterval(() => {
                const ha = document.querySelector('home-assistant');
                const h = ha?.hass;
                if (h && chat.hass !== h) chat.hass = h;
                if (voiceEl._pipelineId && chat._pipelineId !== voiceEl._pipelineId) {
                    chat._pipelineId = voiceEl._pipelineId;
                }
            }, 500);

            _patchMarkdown();

            neutralizeVoiceDialog(voiceEl);
            voiceEl.style.display = 'none';

            const mainEl = document.querySelector('home-assistant')?.shadowRoot?.querySelector('home-assistant-main');
            if (mainEl) {
                mainEl.setAttribute('dock-open', '');
                mainEl.style.setProperty('--mdc-top-app-bar-width', 'calc(100% - var(--mdc-drawer-width, 0px) - var(--claw-dock-width))');
            }
            document.body.style.overflow = '';

            _startFabSync();

            const closeBtn = dock.querySelector('.dock-close');
            if (closeBtn && !closeBtn.__clawBound) {
                closeBtn.__clawBound = true;
                closeBtn.addEventListener('click', () => {
                    _userClose = true;
                    voiceEl.closeDialog?.();
                });
            }

            return true;
        };

        const waitAndGrab = (voiceEl) => {
            if (grabChat(voiceEl)) return;
            let tries = 0;
            const tid = setInterval(() => {
                if (grabChat(voiceEl) || ++tries > 50) clearInterval(tid);
            }, 50);
        };

        let _historyVisible = false;

        const _formatTimeAgo = (seconds) => {
            if (seconds < 60) return 'Just now';
            if (seconds < 3600) return Math.floor(seconds / 60) + 'm ago';
            if (seconds < 86400) return Math.floor(seconds / 3600) + 'h ago';
            const days = Math.floor(seconds / 86400);
            if (days === 1) return 'Yesterday';
            if (days < 7) return days + 'd ago';
            return new Date(Date.now() - seconds * 1000).toLocaleDateString();
        };

        const _getDateLabel = (timestamp) => {
            const d = new Date(timestamp * 1000);
            const now = new Date();
            const isToday = d.toDateString() === now.toDateString();
            const yesterday = new Date(now);
            yesterday.setDate(yesterday.getDate() - 1);
            const isYesterday = d.toDateString() === yesterday.toDateString();
            
            if (isToday) {
                const h = d.getHours();
                if (h < 6) return { key: 'today_night', label: historyText('today_night'), order: 0 };
                if (h < 12) return { key: 'today_morning', label: historyText('today_morning'), order: 1 };
                if (h < 18) return { key: 'today_afternoon', label: historyText('today_afternoon'), order: 2 };
                return { key: 'today_evening', label: historyText('today_evening'), order: 3 };
            }
            if (isYesterday) return { key: 'yesterday', label: historyText('yesterday'), order: 10 };
            
            const diffDays = Math.floor((now - d) / 86400000);
            if (diffDays < 7) return { key: 'week', label: historyText('this_week'), order: 20 };
            if (diffDays < 30) return { key: 'month', label: historyText('this_month'), order: 30 };
            
            const lang = getFrontendLanguage();
            const isZh = lang.startsWith('zh');
            const monthNamesEn = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
            const monthKey = `${d.getFullYear()}_${d.getMonth()}`;
            const label = isZh ? `${d.getFullYear()}年${d.getMonth() + 1}月` : `${monthNamesEn[d.getMonth()]} ${d.getFullYear()}`;
            return { key: monthKey, label, order: 100 + (2100 - d.getFullYear()) * 12 + (11 - d.getMonth()) };
        };

        const _groupConversationsByDate = (convs) => {
            const groups = {};
            for (const c of convs) {
                const { key, label, order } = _getDateLabel(c.last_message_at);
                if (!groups[key]) groups[key] = { label, order, items: [] };
                groups[key].items.push(c);
            }
            return Object.values(groups).sort((a, b) => a.order - b.order);
        };

        const _renderHistoryPanel = async (dock) => {
            const panel = dock.querySelector('.dock-history-panel');
            if (!panel) return;
            const h = getHass();
            if (!h?.connection) return;

            let convs = [];
            try {
                const r = await h.connection.sendMessagePromise({ type: 'ha_crack/chat_history_list' });
                convs = r?.conversations || [];
            } catch (e) {
                panel.innerHTML = '<div class="hist-empty"><svg viewBox="0 0 24 24"><path d="M12,2C6.48,2 2,6.48 2,12C2,17.52 6.48,22 12,22C17.52,22 22,17.52 22,12C22,6.48 17.52,2 12,2M13,17H11V15H13V17M13,13H11V7H13V13Z"/></svg><span>' + historyText('unableLoad') + '</span></div>';
                return;
            }

            if (!convs.length) {
                panel.innerHTML = '<div class="hist-empty"><svg viewBox="0 0 24 24"><path d="M13,3A9,9 0 0,0 4,12H1L4.89,15.89L4.96,16.03L9,12H6A7,7 0 0,1 13,5A7,7 0 0,1 20,12A7,7 0 0,1 13,19C11.07,19 9.32,18.21 8.06,16.94L6.64,18.36C8.27,19.99 10.51,21 13,21A9,9 0 0,0 22,12A9,9 0 0,0 13,3M12.5,8V12.25L16.5,14.33L17.21,13.06L13.75,11.33V8H12.5Z"/></svg><span>' + historyText('empty') + '</span></div>';
                return;
            }

            const pinnedIds = JSON.parse(localStorage.getItem('claw_pinned_conversations') || '[]');
            const groups = _groupConversationsByDate(convs.filter(c => !pinnedIds.includes(c.conversation_id)));
            const pinned = convs.filter(c => pinnedIds.includes(c.conversation_id));

            let html = '<div class="hist-search"><div class="hist-search-wrap"><svg viewBox="0 0 24 24"><path d="M9.5,3A6.5,6.5 0 0,1 16,9.5C16,11.11 15.41,12.59 14.44,13.73L14.71,14H15.5L20.5,19L19,20.5L14,15.5V14.71L13.73,14.44C12.59,15.41 11.11,16 9.5,16A6.5,6.5 0 0,1 3,9.5A6.5,6.5 0 0,1 9.5,3M9.5,5C7,5 5,7 5,9.5C5,12 7,14 9.5,14C12,14 14,12 14,9.5C14,7 12,5 9.5,5Z"/></svg><input type="text" placeholder="' + historyText('search') + '" /></div></div><div class="hist-list">';

            const chatIcon = '<svg viewBox="0 0 24 24"><path d="M12,3C6.5,3 2,6.58 2,11C2.05,13.15 3.06,15.17 4.75,16.5C4.75,17.1 4.33,18.67 2,21C4.97,20.3 7.58,18.67 8.5,17.65C9.64,17.88 10.82,18 12,18C17.5,18 22,14.42 22,10C22,6.58 17.5,3 12,3Z"/></svg>';
            const pinIcon = '<svg viewBox="0 0 24 24"><path d="M16,12V4H17V2H7V4H8V12L6,14V16H11.2V22H12.8V16H18V14L16,12Z"/></svg>';
            const deleteIcon = '<svg viewBox="0 0 24 24"><path d="M19,4H15.5L14.5,3H9.5L8.5,4H5V6H19M6,19A2,2 0 0,0 8,21H16A2,2 0 0,0 18,19V7H6V19Z"/></svg>';
            const chevronIcon = '<svg class="hist-section-chevron" viewBox="0 0 24 24"><path d="M7.41,8.58L12,13.17L16.59,8.58L18,10L12,16L6,10L7.41,8.58Z"/></svg>';
            const showActiveSelection = _historySelectedConversationId && !_historySelectionHighlightConsumed;

            const renderItem = (c, isPinned) => {
                const activeClass = showActiveSelection && c.conversation_id === _historySelectedConversationId ? ' active' : '';
                const pinnedClass = isPinned ? ' pinned' : '';
                return '<div class="hist-item' + activeClass + pinnedClass + '" data-conv-id="' + c.conversation_id + '">'
                    + '<div class="hist-item-icon">' + chatIcon + '</div>'
                    + '<div class="hist-item-content">'
                    + '<div class="hist-item-title">' + (c.summary || historyText('conversation')).replace(/</g, '&lt;') + '</div>'
                    + '<div class="hist-item-meta">' + c.turn_count + ' ' + historyText('messages') + ' · ' + _formatTimeAgo(c.seconds_ago) + '</div>'
                    + '</div>'
                    + '<div class="hist-item-actions">'
                    + '<button class="hist-pin-btn' + (isPinned ? ' pinned' : '') + '" data-conv-id="' + c.conversation_id + '" title="' + (isPinned ? historyText('unpin') : historyText('pin')) + '">' + pinIcon + '</button>'
                    + '<button class="hist-delete-btn" data-conv-id="' + c.conversation_id + '" title="' + historyText('delete') + '">' + deleteIcon + '</button>'
                    + '</div>'
                    + '</div>';
            };

            if (pinned.length) {
                for (const c of pinned) {
                    html += renderItem(c, true);
                }
            }

            const currentHour = new Date().getHours();
            let currentPeriodOrder = 3;
            if (currentHour < 6) currentPeriodOrder = 0;
            else if (currentHour < 12) currentPeriodOrder = 1;
            else if (currentHour < 18) currentPeriodOrder = 2;

            groups.forEach((group, idx) => {
                const shouldExpand = group.order === currentPeriodOrder;
                const collapsed = !shouldExpand ? ' collapsed' : '';
                html += '<div class="hist-section' + collapsed + '" data-section="' + idx + '">'
                    + '<div class="hist-section-label">' + chevronIcon + group.label + ' <span class="hist-section-count">(' + group.items.length + ')</span></div>'
                    + '<div class="hist-section-items">';
                for (const c of group.items) {
                    html += renderItem(c, pinnedIds.includes(c.conversation_id));
                }
                html += '</div></div>';
            });

            html += '</div>';
            panel.innerHTML = html;
            if (showActiveSelection) _historySelectionHighlightConsumed = true;

            panel.querySelectorAll('.hist-section-label').forEach(label => {
                label.addEventListener('click', () => {
                    const section = label.closest('.hist-section');
                    if (section) section.classList.toggle('collapsed');
                });
            });

            const searchInput = panel.querySelector('.hist-search input');
            if (searchInput) {
                searchInput.addEventListener('input', () => {
                    const q = searchInput.value.toLowerCase().trim();
                    
                    panel.querySelectorAll('.hist-section').forEach(section => {
                        let hasMatch = false;
                        section.querySelectorAll('.hist-item').forEach(item => {
                            const title = item.querySelector('.hist-item-title')?.textContent?.toLowerCase() || '';
                            const match = !q || title.includes(q);
                            item.style.display = match ? '' : 'none';
                            if (match && q) {
                                hasMatch = true;
                                item.classList.add('search-match');
                            } else {
                                item.classList.remove('search-match');
                            }
                        });
                        if (q && hasMatch) {
                            section.classList.remove('collapsed');
                        }
                        section.style.display = (q && !hasMatch) ? 'none' : '';
                    });

                    panel.querySelectorAll('.hist-list > .hist-item').forEach(item => {
                        const title = item.querySelector('.hist-item-title')?.textContent?.toLowerCase() || '';
                        const match = !q || title.includes(q);
                        item.style.display = match ? '' : 'none';
                        if (match && q) {
                            item.classList.add('search-match');
                        } else {
                            item.classList.remove('search-match');
                        }
                    });
                });
            }

            panel.querySelectorAll('.hist-item').forEach(item => {
                item.addEventListener('click', (e) => {
                    if (e.target.closest('.hist-delete-btn') || e.target.closest('.hist-pin-btn')) return;
                    const convId = item.dataset.convId;
                    _resumeConversation(dock, convId);
                });
            });

            panel.querySelectorAll('.hist-pin-btn').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    const convId = btn.dataset.convId;
                    let pins = JSON.parse(localStorage.getItem('claw_pinned_conversations') || '[]');
                    if (pins.includes(convId)) {
                        pins = pins.filter(id => id !== convId);
                    } else {
                        pins.unshift(convId);
                    }
                    localStorage.setItem('claw_pinned_conversations', JSON.stringify(pins));
                    _renderHistoryPanel(dock);
                });
            });

            panel.querySelectorAll('.hist-delete-btn').forEach(btn => {
                btn.addEventListener('click', async (e) => {
                    e.stopPropagation();
                    const convId = btn.dataset.convId;
                    try {
                        await h.connection.sendMessagePromise({ type: 'ha_crack/chat_history_delete', conversation_id: convId });
                        let pins = JSON.parse(localStorage.getItem('claw_pinned_conversations') || '[]');
                        pins = pins.filter(id => id !== convId);
                        localStorage.setItem('claw_pinned_conversations', JSON.stringify(pins));
                    } catch (_) {}
                    _renderHistoryPanel(dock);
                });
            });
        };

        const _resumeConversation = async (dock, convId) => {
            const h = getHass();
            if (!h?.connection) return;
            _historySelectedConversationId = convId;
            _historySelectionHighlightConsumed = false;

            let turns = [];
            let historyTokens = 0;
            try {
                const r = await h.connection.sendMessagePromise({ type: 'ha_crack/chat_history_get', conversation_id: convId });
                turns = r?.turns || [];
                historyTokens = r?.tokens_used || 0;
            } catch (_) {}

            try {
                await h.connection.sendMessagePromise({
                    type: 'ha_crack/chat_history_resume',
                    conversation_id: convId,
                    window_id: getHistoryWindowId()
                });
            } catch (_) {}

            const conversation = [];
            for (const t of turns) {
                if (t.user) {
                    conversation.push({ who: 'user', text: t.user });
                }
                if (t.assistant) {
                    conversation.push({
                        who: 'hass',
                        text: t.assistant_display || t.assistant,
                        agent_id: t.agent_id || '',
                        agent_name: t.agent_name || '',
                    });
                }
            }

            if (!conversation.length) {
                _toggleHistoryPanel(dock, _dockVoiceEl, true);
                return;
            }

            const state = window.__clawAssistChatState;
            if (state) {
                state.resetting = true;
                state.conversation = conversation;
                state.conversationId = convId;
                state.persist?.();
            }

            if (historyTokens > 0 && typeof window.__clawSetHistoryTokens === 'function') {
                window.__clawSetHistoryTokens(historyTokens);
            }

            const chat = _dockedChat || deepQuery('ha-assist-chat');
            if (chat) {
                chat._conversation = conversation;
                chat._conversationId = convId;
                chat.requestUpdate?.('_conversation');
                setTimeout(() => {
                    if (state) state.resetting = false;
                    window.dispatchEvent(new CustomEvent('claw-chat-updated'));
                }, 100);
            } else if (state) {
                state.resetting = false;
            }

            _toggleHistoryPanel(dock, _dockVoiceEl, true);
        };

        const _toggleHistoryPanel = (dock, voiceEl, forceChat) => {
            const body = dock.querySelector('.dock-body');
            const panel = dock.querySelector('.dock-history-panel');
            const titleEl = dock.querySelector('.dock-title');
            if (!body || !panel) return;

            const showChat = () => {
                _historyVisible = false;
                body.style.display = '';
                panel.style.display = 'none';
                if (titleEl && titleEl.__clawOrigTitleNodes) {
                    titleEl.classList.remove('dock-history-title-visible');
                    titleEl.replaceChildren(...titleEl.__clawOrigTitleNodes);
                }
            };

            const showHistory = () => {
                _historyVisible = true;
                body.style.display = 'none';
                panel.style.display = '';
                if (titleEl) {
                    titleEl.classList.add('dock-history-title-visible');
                    if (!titleEl.__clawOrigTitleNodes) {
                        titleEl.__clawOrigTitleNodes = Array.from(titleEl.childNodes);
                    }
                    const historyTitle = document.createElement('span');
                    historyTitle.setAttribute('slot', 'title');
                    historyTitle.style.cssText = 'display:flex;align-items:center;gap:4px;margin-left:5px';
                    historyTitle.innerHTML = '<button class="dock-btn dock-back-btn" title="' + historyText('back') + '" style="width:32px;height:32px;margin-right:4px;flex:0 0 auto;display:flex;align-items:center;justify-content:center;cursor:pointer;border-radius:50%;border:none;padding:0;color:inherit"><svg viewBox="0 0 24 24" style="width:18px;height:18px;fill:currentColor"><path d="M20,11V13H8L13.5,18.5L12.08,19.92L4.16,12L12.08,4.08L13.5,5.5L8,11H20Z"/></svg></button>' + historyText('history');
                    titleEl.replaceChildren(historyTitle);
                    const backBtn = titleEl.querySelector('.dock-back-btn');
                    if (backBtn) {
                        backBtn.addEventListener('click', () => _toggleHistoryPanel(dock, voiceEl, true));
                    }
                }
                _renderHistoryPanel(dock);
            };

            if (forceChat) {
                showChat();
            } else if (_historyVisible) {
                showChat();
            } else {
                showHistory();
            }
        };

        const closeDock = () => {
            _dockActive = false;
            _dockVoiceEl = null;
            _dockedChat = null;
            _historyVisible = false;
            _stopFabSync();
            const msr = getMainSR();
            if (!msr) return;
            const dock = msr.getElementById(DOCK_ID);
            if (dock) {
                if (dock.__clawHassSync) { clearInterval(dock.__clawHassSync); dock.__clawHassSync = null; }
                dock.removeAttribute('open');
                dock.querySelector('.dock-body').innerHTML = '';
                const hp = dock.querySelector('.dock-history-panel');
                if (hp) hp.style.display = 'none';
                const body = dock.querySelector('.dock-body');
                if (body) body.style.display = '';
                const titleEl = dock.querySelector('.dock-title');
                titleEl?.classList.remove('dock-history-title-visible');
                if (titleEl && titleEl.__clawOrigTitleNodes) {
                    titleEl.replaceChildren(...titleEl.__clawOrigTitleNodes);
                }
            }
            const mainEl = document.querySelector('home-assistant')?.shadowRoot?.querySelector('home-assistant-main');
            if (mainEl) {
                mainEl.removeAttribute('dock-open');
                mainEl.style.removeProperty('--mdc-top-app-bar-width');
            }
            document.body.style.overflow = '';
        };

        customElements.whenDefined('ha-voice-command-dialog').then(() => {
            const proto = customElements.get('ha-voice-command-dialog')?.prototype;
            if (!proto || proto.__clawRightDockPatched) return;
            proto.__clawRightDockPatched = true;

            const origShow = proto.showDialog;
            const origClose = proto.closeDialog;
            const origUpdated = proto.updated;

            proto.updated = function(changedProps) {
                if (_dockActive && _dockVoiceEl === this) {
                    if (changedProps.has('_pipelineId')) {
                        requestAnimationFrame(() => waitAndGrab(this));
                    }
                    neutralizeVoiceDialog(this);
                    this.style.display = 'none';
                }
                return origUpdated?.call(this, changedProps);
            };

            proto.showDialog = async function(...args) {
                if (!_sidebarDockEnabled) {
                    return origShow?.apply(this, args);
                }
                if (_dockActive && _dockVoiceEl === this) {
                    _userClose = true;
                    this.closeDialog?.();
                    return;
                }
                _dockActive = true;
                _dockVoiceEl = this;
                const ret = await origShow?.apply(this, args);
                await this.updateComplete;
                waitAndGrab(this);
                return ret;
            };

            proto.closeDialog = async function(...args) {
                if (!_userClose && _dockActive) {
                    return;
                }
                _userClose = false;
                closeDock();
                this.style.display = '';
                return origClose?.apply(this, args);
            };
        }).catch(() => {});
    }

    function setupContinuousConversation(hass) {
        if (!hass?.connection) return;
        if (window.__clawAssistChatPatched) return;
        window.__clawAssistChatPatched = true;
        const STORAGE_KEY = 'claw_assist_chat_state_v1';
        const settings = window.__clawSettings = window.__clawSettings || { continuous_conversation: false };
        let _ccInitialLoaded = false;
        const refreshSettings = async () => {
            try {
                const r = await hass.connection.sendMessagePromise({ type: 'ha_crack/get_settings' });
                const newVal = !!r?.continuous_conversation;
                if (_ccInitialLoaded && newVal !== settings.continuous_conversation) {
                    try { localStorage.removeItem(STORAGE_KEY); } catch(e) {}
                    window.__clawAssistChatPatched = false;
                    location.reload();
                    return;
                }
                settings.continuous_conversation = newVal;
                _ccInitialLoaded = true;
                if (!settings.continuous_conversation) {
                    try { localStorage.removeItem(STORAGE_KEY); } catch(e) {}
                    if (state) {
                        state.conversation = null;
                        state.conversationId = null;
                    }
                }
            } catch (e) {}
        };
        refreshSettings();
        hass.connection.subscribeEvents(() => refreshSettings(), 'ha_crack_settings_changed').catch(() => {});
        const loadPersisted = () => {
            try {
                const raw = localStorage.getItem(STORAGE_KEY);
                if (!raw) return null;
                return JSON.parse(raw);
            } catch (e) { return null; }
        };
        const persist = () => {
            if (!settings.continuous_conversation) return;
            try {
                localStorage.setItem(STORAGE_KEY, JSON.stringify({
                    conversation: state.conversation,
                    conversationId: state.conversationId
                }));
            } catch (e) {}
        };
        if (!window.__clawAssistChatState) {
            const persisted = loadPersisted() || {};
            window.__clawAssistChatState = {
                conversation: Array.isArray(persisted.conversation) ? persisted.conversation : null,
                conversationId: persisted.conversationId || null,
                resetting: false
            };
        }
        const state = window.__clawAssistChatState;
        if (state.conversationId && Array.isArray(state.conversation) && state.conversation.length > 0) {
            bindHistoryWindow(hass, state.conversationId);
        }
        state.persist = persist;
        if (!window.__clawPersistHookInstalled) {
            window.__clawPersistHookInstalled = true;
            window.addEventListener('beforeunload', () => state.persist?.());
            window.addEventListener('pagehide', () => state.persist?.());
            document.addEventListener('visibilitychange', () => {
                if (document.visibilityState === 'hidden') state.persist?.();
            });
        }
        customElements.whenDefined('ha-assist-chat').then(() => {
            const ctor = customElements.get('ha-assist-chat');
            const proto = ctor?.prototype;
            if (!proto || proto.__clawContinuousPatched) return;
            proto.__clawContinuousPatched = true;
            const originalWillUpdate = proto.willUpdate;
            const originalDisconnected = proto.disconnectedCallback;
            const originalAddMessage = proto._addMessage;
            const originalUpdated = proto.updated;
            proto.updated = function(changed) {
                originalUpdated?.call(this, changed);
                if (this.shadowRoot) {
                    try { window.dispatchEvent(new CustomEvent('claw-chat-updated', { detail: this })); } catch(e) {}
                }
                if (state.resetting) return;
                if (!settings.continuous_conversation) return;
                if (Array.isArray(this._conversation) && this._conversation.length > 0) {
                    state.conversation = this._conversation.map(m => ({...m, tool_calls: m.tool_calls || {}}));
                }
                if (this._conversationId) {
                    state.conversationId = this._conversationId;
                }
                state.persist?.();
            };
            const isFreshConversation = (conv) => {
                if (!Array.isArray(conv)) return true;
                if (conv.length === 0) return true;
                if (conv.length === 1 && conv[0]?.who === 'hass' && !conv[0].text?.trim()?.length) return true;
                if (conv.length === 1 && conv[0]?.who === 'hass') return true;
                return false;
            };
            proto.willUpdate = function(changed) {
                originalWillUpdate?.call(this, changed);
                if (state.resetting) return;
                if (!settings.continuous_conversation) return;
                if (!isFreshConversation(this._conversation)) return;
                if (Array.isArray(state.conversation) && state.conversation.length > 0) {
                    this._conversation = state.conversation.map(m => ({...m, tool_calls: m.tool_calls || {}}));
                }
                if (state.conversationId) {
                    this._conversationId = state.conversationId;
                }
            };
            proto.disconnectedCallback = function() {
                if (settings.continuous_conversation) {
                    if (Array.isArray(this._conversation)) {
                        state.conversation = this._conversation.map(m => ({...m, tool_calls: m.tool_calls || {}}));
                    }
                    if (this._conversationId) {
                        state.conversationId = this._conversationId;
                    }
                    state.persist?.();
                } else {
                    window.__clawResetContextStatusBar?.(true);
                }
                originalDisconnected?.call(this);
            };
            const findMessagesEl = (chat) => chat.shadowRoot?.querySelector('.messages');
            const isAtBottom = (el) => el.scrollHeight - el.scrollTop - el.clientHeight < 48;
            const stickToBottom = (el) => { el.scrollTop = el.scrollHeight; };
            const attachScrollWatcher = function() {
                if (this.__clawScrollWatcher) return;
                const el = findMessagesEl(this);
                if (!el) return;
                this.__clawScrollWatcher = true;
                this.__clawUserScrolledUp = false;
                let lastScrollTop = el.scrollTop;
                el.addEventListener('scroll', () => {
                    const goingUp = el.scrollTop < lastScrollTop;
                    lastScrollTop = el.scrollTop;
                    if (goingUp && !isAtBottom(el)) {
                        this.__clawUserScrolledUp = true;
                    } else if (isAtBottom(el)) {
                        this.__clawUserScrolledUp = false;
                    }
                }, { passive: true });
                const ro = new ResizeObserver(() => {
                    if (!this.__clawUserScrolledUp) stickToBottom(el);
                });
                ro.observe(el);
                const observeChildren = () => {
                    el.querySelectorAll(':scope > *').forEach(c => {
                        if (c.__clawObserved) return;
                        c.__clawObserved = true;
                        ro.observe(c);
                    });
                };
                observeChildren();
                const mo = new MutationObserver(() => {
                    observeChildren();
                    if (!this.__clawUserScrolledUp) stickToBottom(el);
                });
                mo.observe(el, { childList: true, subtree: true, characterData: true });
                this.__clawScrollResizeObserver = ro;
                this.__clawScrollMutationObserver = mo;
                stickToBottom(el);
            };
            proto._scrollMessagesBottom = async function() {
                attachScrollWatcher.call(this);
                const el = findMessagesEl(this);
                if (!el) return;
                if (this.__clawUserScrolledUp) return;
                stickToBottom(el);
                requestAnimationFrame(() => {
                    if (!this.__clawUserScrolledUp) stickToBottom(el);
                });
            };
            const originalFirstUpdated = proto.firstUpdated;
            proto.firstUpdated = function(changed) {
                originalFirstUpdated?.call(this, changed);
                attachScrollWatcher.call(this);
            };
            const originalUpdated2 = proto.updated;
            proto.updated = function(changed) {
                originalUpdated2?.call(this, changed);
                attachScrollWatcher.call(this);
            };
            if (typeof originalAddMessage === 'function') {
                proto._addMessage = function(message) {
                    originalAddMessage.call(this, message);
                    const conv = Array.isArray(this._conversation) ? this._conversation : [];
                    const prev = conv[conv.length - 2];
                    if (prev?.who === 'user' && String(prev.text || '').trim() === '/new') {
                        state.resetting = true;
                        const welcomeText = this.hass?.localize?.('ui.dialogs.voice_command.how_can_i_help') || '';
                        const welcome = { who: 'hass', text: welcomeText, thinking: '', tool_calls: {} };
                        state.conversation = null;
                        state.conversationId = null;
                        window.__clawResetContextStatusBar?.(false);
                        this._conversation = [welcome];
                        this._conversationId = null;
                        this.requestUpdate?.('_conversation');
                        state.resetting = false;
                        state.persist?.();
                        return;
                    }
                    if (settings.continuous_conversation) {
                        if (Array.isArray(this._conversation)) {
                            state.conversation = this._conversation.map(m => ({...m, tool_calls: m.tool_calls || {}}));
                        }
                        if (this._conversationId) {
                            state.conversationId = this._conversationId;
                        }
                        state.persist?.();
                    }
                };
            }
        }).catch(() => {});
    }

    function setupFileUpload(hass) {
        if (!hass?.connection) return;
        if (window.__clawFileUploadInstalled) return;
        window.__clawFileUploadInstalled = true;

        let fileUploadEnabled = false;
        const refreshUploadSetting = async () => {
            try {
                const r = await hass.connection.sendMessagePromise({ type: 'ha_crack/get_settings' });
                fileUploadEnabled = !!r?.enable_file_upload;
            } catch(e) {}
            const sr = deepQuery('ha-assist-chat')?.shadowRoot;
            if (!sr) return;
            const slot = sr.querySelector('#claw-attach-slot');
            if (slot) slot.style.display = fileUploadEnabled ? '' : 'none';
            if (!fileUploadEnabled) {
                const zone = sr.querySelector('.claw-upload-zone');
                if (zone) zone.classList.remove('active');
                const popup = sr.querySelector('.claw-upload-popup');
                if (popup) { popup.innerHTML = ''; popup.style.display = 'none'; }
                pendingFiles.length = 0;
                updateAttachBtn(sr);
            }
        };
        refreshUploadSetting();
        hass.connection.subscribeEvents(() => refreshUploadSetting(), 'ha_crack_settings_changed').catch(() => {});

        const MAX_FILE_SIZE = 50 * 1024 * 1024;
        const pendingFiles = [];

        const FILE_ICONS = {
            'image/': '🖼️', 'video/': '🎬', 'audio/': '🎵',
            'application/pdf': '📄', 'text/': '📝',
            'application/msword': '📃', 'application/vnd.openxmlformats-officedocument.wordprocessingml': '📃',
            'application/vnd.ms-excel': '📊', 'application/vnd.openxmlformats-officedocument.spreadsheetml': '📊',
            'application/vnd.ms-powerpoint': '📊', 'application/vnd.openxmlformats-officedocument.presentationml': '📊',
            'application/zip': '📦', 'application/gzip': '📦', 'application/x-tar': '📦',
        };

        const getIcon = (mime) => {
            for (const [k, v] of Object.entries(FILE_ICONS)) {
                if (mime.startsWith(k)) return v;
            }
            return '📎';
        };

        const fmtSize = (n) => {
            if (n >= 1048576) return (n / 1048576).toFixed(1) + ' MB';
            if (n >= 1024) return Math.round(n / 1024) + ' KB';
            return n + ' B';
        };

        const CSS_ID = 'claw-upload-css';
        const injectUploadCSS = (sr) => {
            if (!sr || sr.getElementById(CSS_ID)) return;
            const s = document.createElement('style');
            s.id = CSS_ID;
            s.textContent = `
                .claw-upload-zone {
                    position: fixed; inset: 0; z-index: 9999;
                    display: flex; align-items: center; justify-content: center;
                    background: rgba(220,220,220,0.2);
                    backdrop-filter: blur(3px); -webkit-backdrop-filter: blur(3px);
                    border: 2px dashed var(--divider-color, rgba(0,0,0,0.2));
                    border-radius: 16px; margin: 8px;
                    pointer-events: none;
                    opacity: 0; transition: opacity 0.2s;
                }
                .claw-upload-zone.active { opacity: 1; }
                .claw-upload-zone-text {
                    font: 600 15px/1.4 system-ui, sans-serif;
                    color: var(--secondary-text-color, #666);
                    text-align: center; pointer-events: none;
                }
                .claw-upload-zone-text span { display: block; font-size: 36px; margin-bottom: 6px; }

                .claw-upload-popup {
                    position: absolute; bottom: 100%; left: 0; right: 0;
                    z-index: 50;
                    display: flex; align-items: center; gap: 5px;
                    padding: 5px 5px 0px 15px;
                    overflow-x: auto;
                    scrollbar-width: none;
                    background: var(--card-background-color, #fff);
                }
                .claw-upload-popup::-webkit-scrollbar { display: none; }
                .claw-upload-popup:empty { display: none; }

                .claw-upload-item {
                    position: relative; flex-shrink: 0;
                    display: flex; align-items: center; gap: 5px;
                    padding: 3px 8px 3px 3px;
                    background: var(--secondary-background-color, #f5f5f5);
                    border-radius: 8px;
                    font: 400 11px/1.3 system-ui, sans-serif;
                    color: var(--primary-text-color);
                    max-width: 80px;
                    animation: clawUploadIn 0.2s ease;
                }
                @keyframes clawUploadIn {
                    from { opacity: 0; transform: translateY(4px); }
                    to { opacity: 1; transform: none; }
                }
                .claw-upload-item .claw-thumb {
                    width: 28px; height: 28px; border-radius: 5px;
                    object-fit: cover; flex-shrink: 0;
                }
                .claw-upload-item .claw-icon-thumb {
                    width: 28px; height: 28px; border-radius: 5px;
                    display: flex; align-items: center; justify-content: center;
                    font-size: 15px; flex-shrink: 0;
                    background: var(--divider-color, #e8e8e8);
                }
                .claw-upload-item .claw-file-info { overflow: hidden; min-width: 0; }
                .claw-upload-item .claw-file-name {
                    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
                    font-weight: 500; font-size: 11px;
                }
                .claw-upload-item .claw-file-size { font-size: 10px; opacity: 0.45; }
                .claw-upload-item .claw-file-status { font-size: 10px; font-weight: 500; }
                .claw-upload-item .claw-file-status.uploading { color: var(--primary-color, #03a9f4); }
                .claw-upload-item .claw-file-status.error { color: var(--error-color, #db4437); }
                .claw-upload-item .claw-remove {
                    position: absolute; top: -4px; right: -4px;
                    width: 15px; height: 15px; border-radius: 50%;
                    background: var(--secondary-text-color, #888); color: #fff;
                    border: none; cursor: pointer; font-size: 10px; line-height: 15px;
                    text-align: center; padding: 0;
                    opacity: 0; transition: opacity 0.15s;
                }
                .claw-upload-item:hover .claw-remove { opacity: 1; }
                .claw-upload-progress {
                    position: absolute; bottom: 0; left: 0; right: 0; height: 2px;
                    border-radius: 0 0 8px 8px; overflow: hidden;
                    background: var(--divider-color, #e0e0e0);
                }
                .claw-upload-progress-fill {
                    height: 100%; background: var(--primary-color, #03a9f4);
                    transition: width 0.3s ease;
                }
                .claw-attach-btn {
                    position: relative; isolation: isolate;
                    display: inline-flex; align-items: center; justify-content: center;
                    width: 48px;
                    height: 48px;
                    border: none; border-radius: 50%;
                    background: transparent; cursor: pointer;
                    color: var(--secondary-text-color, #727272);
                    flex-shrink: 0; padding: 0; margin: 0 -20px 0 0; vertical-align: middle;
                    box-sizing: border-box;
                    -webkit-tap-highlight-color: transparent;
                }
                .claw-attach-btn::after {
                    content: ""; position: absolute; inset: 0; z-index: -1;
                    border-radius: 50%;
                    background-color: currentColor;
                    opacity: 0; pointer-events: none;
                    transition: opacity 0.15s;
                }
                @media (hover:hover) {
                    .claw-attach-btn:hover::after { opacity: 0.08; }
                }
                .claw-attach-btn:active::after { opacity: 0.12; }
                .claw-attach-btn.has-files { color: var(--primary-color, #03a9f4); }
            `;
            sr.appendChild(s);
        };

        const renderPreviewBar = (sr) => {
            let popup = sr.querySelector('.claw-upload-popup');
            if (!popup) {
                const inputDiv = sr.querySelector('div.input[slot="primaryAction"]');
                if (!inputDiv) return;
                inputDiv.style.position = 'relative';
                popup = document.createElement('div');
                popup.className = 'claw-upload-popup';
                inputDiv.appendChild(popup);
            }
            if (!popup) return;
            popup.innerHTML = '';
            if (!pendingFiles.length) { popup.style.display = 'none'; return; }
            popup.style.display = '';
            pendingFiles.forEach((f, i) => {
                const item = document.createElement('div');
                item.className = 'claw-upload-item';
                const thumbHTML = f.previewUrl
                    ? `<img class="claw-thumb" src="${f.previewUrl}" alt="">`
                    : `<div class="claw-icon-thumb">${getIcon(f.mime)}</div>`;
                let statusHTML = '';
                if (f.status === 'uploading') {
                    statusHTML = `<div class="claw-upload-progress"><div class="claw-upload-progress-fill" style="width:${f.progress||0}%"></div></div>`;
                } else if (f.status === 'done') {
                    statusHTML = '';
                } else if (f.status === 'error') {
                    statusHTML = '';
                }
                item.innerHTML = `${thumbHTML}<div class="claw-file-info"><div class="claw-file-name" title="${f.name}">${f.name}</div><div class="claw-file-size">${fmtSize(f.size)}</div>${statusHTML}</div>`;
                const rm = document.createElement('button');
                rm.className = 'claw-remove'; rm.textContent = '×';
                rm.onclick = (e) => { e.stopPropagation(); pendingFiles.splice(i,1); renderPreviewBar(sr); updateAttachBtn(sr); };
                item.appendChild(rm);
                popup.appendChild(item);
            });
        };

        const updateAttachBtn = (sr) => {
            const btn = sr.querySelector('.claw-attach-btn');
            if (btn) btn.classList.toggle('has-files', pendingFiles.length > 0);
        };

        const uploadFile = async (fileObj) => {
            const sr = deepQuery('ha-assist-chat')?.shadowRoot;
            fileObj.status = 'uploading'; fileObj.progress = 0;
            if (sr) renderPreviewBar(sr);
            try {
                const form = new FormData();
                form.append('file', fileObj.file, fileObj.name);
                const token = hass.auth?.data?.access_token || '';
                const xhr = new XMLHttpRequest();
                const result = await new Promise((resolve, reject) => {
                    xhr.open('POST', '/api/claw_assistant/upload');
                    xhr.setRequestHeader('Authorization', 'Bearer ' + token);
                    xhr.upload.onprogress = (e) => {
                        if (e.lengthComputable) {
                            fileObj.progress = Math.round((e.loaded / e.total) * 90);
                            if (sr) renderPreviewBar(sr);
                        }
                    };
                    xhr.onload = () => {
                        if (xhr.status >= 200 && xhr.status < 300) {
                            resolve(JSON.parse(xhr.responseText));
                        } else { reject(new Error(xhr.statusText)); }
                    };
                    xhr.onerror = () => reject(new Error('network error'));
                    xhr.send(form);
                });
                fileObj.status = 'done'; fileObj.progress = 100;
                fileObj.serverPath = result.path; fileObj.serverMime = result.mime_type;
            } catch (err) {
                fileObj.status = 'error';
                pendingFiles.splice(pendingFiles.indexOf(fileObj), 1);
            }
            if (sr) { renderPreviewBar(sr); updateAttachBtn(sr); }
        };

        const MAX_ATTACHMENTS = 5;
        const addFiles = (files) => {
            for (const file of files) {
                if (pendingFiles.length >= MAX_ATTACHMENTS) break;
                if (file.size > MAX_FILE_SIZE) continue;
                if (pendingFiles.some(f => f.name === file.name && f.size === file.size)) continue;
                const entry = {
                    file, name: file.name, size: file.size,
                    mime: file.type || 'application/octet-stream',
                    status: 'pending', progress: 0,
                    previewUrl: null, serverPath: null, serverMime: null, error: null,
                };
                if (file.type.startsWith('image/') && file.size < 10*1024*1024) {
                    entry.previewUrl = URL.createObjectURL(file);
                }
                pendingFiles.push(entry);
                uploadFile(entry);
            }
            const sr = deepQuery('ha-assist-chat')?.shadowRoot;
            if (sr) { renderPreviewBar(sr); updateAttachBtn(sr); }
        };

        const buildAttachmentTags = () => {
            return pendingFiles.filter(f => f.status==='done'&&f.serverPath).map(f => `[ATTACHMENT:${f.serverMime}:${f.serverPath}]`).join('');
        };
        const clearPending = () => {
            pendingFiles.forEach(f => { if(f.previewUrl) URL.revokeObjectURL(f.previewUrl); });
            pendingFiles.length = 0;
        };

        let _lastUploadChat = null;
        const installUploadUI = () => {
            const chat = deepQuery('ha-assist-chat');
            if (!chat?.shadowRoot) return;
            const sr = chat.shadowRoot;
            if (chat === _lastUploadChat && sr.querySelector('.claw-attach-btn')) return;
            _lastUploadChat = chat;
            injectUploadCSS(sr);

            const haInput = sr.querySelector('ha-input#message-input');
            if (!haInput) return;

            const fileInput = document.createElement('input');
            fileInput.type = 'file'; fileInput.multiple = true;
            fileInput.style.display = 'none';
            fileInput.accept = 'image/*,video/*,audio/*,.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.md,.csv,.json,.yaml,.yml,.xml,.zip,.gz,.tar';
            fileInput.onchange = () => { if(fileInput.files.length) addFiles(fileInput.files); fileInput.value=''; };
            sr.appendChild(fileInput);

            const attachSlot = document.createElement('div');
            attachSlot.setAttribute('slot', 'end');
            attachSlot.id = 'claw-attach-slot';
            const attachBtn = document.createElement('button');
            attachBtn.className = 'claw-attach-btn';
            attachBtn.title = 'Attach files';
            attachBtn.innerHTML = '<svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M13,2.03C17.73,2.5 21.5,6.25 21.95,11C22.5,16.5 18.5,21.38 13,21.93V19.93C16.64,19.5 19.5,16.61 19.96,12.97C20.5,8.58 17.39,4.59 13,4.05V2.05L13,2.03M11,2.06V4.06C9.57,4.26 8.22,4.84 7.1,5.74L5.67,4.26C7.19,3 9.05,2.25 11,2.06M4.26,5.67L5.69,7.1C4.8,8.23 4.24,9.58 4.05,11H2.05C2.25,9.04 3,7.19 4.26,5.67M2.06,13H4.06C4.24,14.42 4.81,15.77 5.69,16.9L4.27,18.33C3.03,16.81 2.26,14.96 2.06,13M7.1,18.37C8.23,19.25 9.58,19.82 11,20V22C9.04,21.79 7.18,21 5.67,19.74L7.1,18.37M12,7.5L7.5,12H11V16H13V12H16.5L12,7.5Z"/></svg>';
            attachBtn.onclick = () => fileInput.click();
            attachSlot.appendChild(attachBtn);
            const litEndSlot = haInput.querySelector('div[slot="end"]');
            if (litEndSlot) {
                haInput.insertBefore(attachSlot, litEndSlot);
            } else {
                haInput.appendChild(attachSlot);
            }
            attachSlot.style.display = fileUploadEnabled ? '' : 'none';

            const dropZone = document.createElement('div');
            dropZone.className = 'claw-upload-zone';
            dropZone.innerHTML = '<div class="claw-upload-zone-text"><span><svg width="36" height="36" viewBox="0 0 24 24" fill="currentColor"><path d="M16.5,6V17.5A4,4 0 0,1 12.5,21.5A4,4 0 0,1 8.5,17.5V5A2.5,2.5 0 0,1 11,2.5A2.5,2.5 0 0,1 13.5,5V15.5A1,1 0 0,1 12.5,16.5A1,1 0 0,1 11.5,15.5V6H10V15.5A2.5,2.5 0 0,1 12.5,18A2.5,2.5 0 0,1 15,15.5V5A4,4 0 0,1 11,1A4,4 0 0,1 7,5V17.5A5.5,5.5 0 0,0 12.5,23A5.5,5.5 0 0,0 18,17.5V6H16.5Z"/></svg></span>Drop files here</div>';
            sr.appendChild(dropZone);

            let dragCounter = 0;
            const host = sr.host || chat;
            host.addEventListener('dragenter', (e) => { e.preventDefault(); if(!fileUploadEnabled) return; dragCounter++; dropZone.classList.add('active'); });
            host.addEventListener('dragleave', (e) => { e.preventDefault(); dragCounter--; if(dragCounter<=0){dragCounter=0;dropZone.classList.remove('active');} });
            host.addEventListener('dragover', (e) => e.preventDefault());
            host.addEventListener('drop', (e) => { e.preventDefault(); dragCounter=0; dropZone.classList.remove('active'); if(fileUploadEnabled && e.dataTransfer?.files?.length) addFiles(e.dataTransfer.files); });
            host.addEventListener('paste', (e) => {
                if(!fileUploadEnabled) return;
                const items = e.clipboardData?.items; if(!items) return;
                const files = [];
                for(const item of items) { if(item.kind==='file'){const f=item.getAsFile();if(f)files.push(f);} }
                if(files.length){ e.preventDefault(); addFiles(files); }
            });
        };

        customElements.whenDefined('ha-assist-chat').then(() => {
            const ctor = customElements.get('ha-assist-chat');
            const proto = ctor?.prototype;
            if (!proto || proto.__clawUploadPatched) return;
            proto.__clawUploadPatched = true;
            const ATTACH_RE = /\[ATTACHMENT:[^\]]+\]/g;
            const origAddMessage = proto._addMessage;
            if (typeof origAddMessage === 'function') {
                proto._addMessage = function(msg) {
                    if (msg && msg.who === 'user' && typeof msg.text === 'string' && ATTACH_RE.test(msg.text)) {
                        const realLen = msg.text.length;
                        const count = (msg.text.match(ATTACH_RE) || []).length;
                        const clean = msg.text.replace(ATTACH_RE, '').trim();
                        const badge = '+' + count + ' file' + (count > 1 ? 's' : '');
                        msg = {...msg, text: clean ? clean + ' `' + badge + '`' : '`' + badge + '`', _realChars: realLen};
                    }
                    return origAddMessage.call(this, msg);
                };
            }
            const origHandleSend = proto._handleSendMessage;
            if (typeof origHandleSend === 'function') {
                proto._handleSendMessage = function() {
                    if (pendingFiles.length > 0) {
                        if (pendingFiles.some(f => f.status === 'uploading')) return;
                        const tags = buildAttachmentTags();
                        const inp = this._messageInput || this.shadowRoot?.querySelector('ha-input#message-input');
                        const userText = (inp?.value || '').trim();
                        const fullText = (userText ? userText + ' ' : '') + tags;
                        clearPending();
                        const sr = this.shadowRoot;
                        if (sr) { renderPreviewBar(sr); updateAttachBtn(sr); }
                        if (inp) { inp.value = ''; this._showSendButton = false; }
                        if (fullText && typeof this._processText === 'function') {
                            window.__clawLastSendChars = fullText.length;
                            this._processText(fullText);
                            return;
                        }
                    }
                    return origHandleSend.call(this);
                };
            }
            const origKeyUp = proto._handleKeyUp;
            if (typeof origKeyUp === 'function') {
                proto._handleKeyUp = function(e) {
                    if (pendingFiles.length > 0 && e.key === 'Enter') {
                        if (pendingFiles.some(f => f.status === 'uploading')) return;
                        const tags = buildAttachmentTags();
                        const t = e.target;
                        const userText = (t?.value || '').trim();
                        const fullText = (userText ? userText + ' ' : '') + tags;
                        clearPending();
                        const sr = this.shadowRoot;
                        if (sr) { renderPreviewBar(sr); updateAttachBtn(sr); }
                        if (t) { t.value = ''; this._showSendButton = false; }
                        if (fullText && typeof this._processText === 'function') {
                            window.__clawLastSendChars = fullText.length;
                            this._processText(fullText);
                            return;
                        }
                    }
                    return origKeyUp.call(this, e);
                };
            }
        }).catch(() => {});

        window.addEventListener('claw-chat-updated', () => installUploadUI());
        setInterval(() => {
            if (deepQuery('ha-assist-chat')?.shadowRoot) installUploadUI();
        }, 5000);
    }

    function setupContextStatusBar(hass) {
        if (window.__clawStatusBarInstalled) return;
        window.__clawStatusBarInstalled = true;

        const CPT = 3.5;
        const CTX = 262144;
        const BASE_PROMPT_TOKENS = Math.round(4200 / CPT);
        const BAR_ID = 'claw-context-status-bar';
        const S_IDLE = 'idle', S_THINKING = 'thinking', S_TOOL = 'tool_call', S_REPLYING = 'replying';
        const settings = window.__clawSettings = window.__clawSettings || {};
        settings.enable_context_status_bar = true;

        let phase = S_IDLE;
        let turnStart = null;
        let turnEnd = null;
        let tickTimer = null;
        let totalChars = 0;
        let windowStart = Date.now();
        let windowTimeLabel = '0s';
        let hasTurn = false;
        let statusLoop = null;

        const resetState = (removeBar, keepTokens = false) => {
            phase = S_IDLE;
            turnStart = null;
            turnEnd = null;
            if (!keepTokens) {
                totalChars = 0;
                windowStart = Date.now();
                windowTimeLabel = '0s';
                hasTurn = false;
            }
            if (tickTimer) { clearInterval(tickTimer); tickTimer = null; }
            if (removeBar) {
                deepQuery('ha-assist-chat')?.shadowRoot?.getElementById(BAR_ID)?.remove();
            } else {
                render();
            }
        };
        window.__clawResetContextStatusBar = resetState;

        const refreshSettings = async () => {
            try {
                const r = await hass.connection.sendMessagePromise({ type: 'ha_crack/get_settings' });
                settings.enable_context_status_bar = !!r?.enable_context_status_bar;
                if (!settings.enable_context_status_bar) {
                    stopStatusBar();
                    resetState(true);
                } else {
                    startStatusBar();
                }
            } catch(e) {}
        };
        refreshSettings();
        hass.connection.subscribeEvents(() => refreshSettings(), 'ha_crack_settings_changed').catch(() => {});

        const fmt = (n) => {
            if (n >= 1048576) { const v=n/1048576; return (v<10?v.toFixed(1):Math.round(v))+'M'; }
            if (n >= 1024) { const v=n/1024; return (v<10?v.toFixed(1):Math.round(v))+'K'; }
            return ''+n;
        };
        const fmtR = (n) => {
            if (n >= 1048576) return Math.round(n/1048576)+'M';
            if (n >= 1024) return Math.round(n/1024)+'K';
            return ''+n;
        };
        const ftime = (s) => { if(s<60) return s+'s'; const m=Math.floor(s/60),r=s%60; return r?m+'m'+r+'s':m+'m'; };
        const fwindow = (s) => {
            const h = Math.floor(s/3600), m = Math.floor((s%3600)/60);
            if (h > 0) return h+'h '+m+'m';
            if (m > 0) return m+'m';
            return s+'s';
        };
        const pctColor = (p) => p<50?'var(--label-badge-green,#4caf50)':p<80?'var(--label-badge-yellow,#ffb300)':p<90?'var(--error-color,#db4437)':'#b71c1c';

        const agentName = () => {
            const d = deepQuery('ha-voice-command-dialog');
            const p = d?.shadowRoot ? d._pipeline : null;
            if (p?.conversation_engine) { const n = p.conversation_engine; return n.includes('.')?n.split('.').pop():n; }
            return '';
        };

        const calcCurrentChars = () => {
            const chat = deepQuery('ha-assist-chat');
            if (!chat) return 0;
            let c = 0;
            const conv = chat._conversation;
            if (Array.isArray(conv)) {
                for (const m of conv) {
                    const txt = m.text||'';
                    if (m.who==='hass' && /^(how can i help|what would you like|请问需要什么帮助|有什么可以帮)/i.test(txt.trim())) continue;
                    c += m._realChars || txt.length;
                    if (m.thinking) c += m.thinking.length;
                    if (m.tool_calls) try { c += JSON.stringify(m.tool_calls).length; } catch(e){}
                }
            }
            return c;
        };

        const startTurn = (userText) => {
            const extraChars = window.__clawLastSendChars || 0;
            window.__clawLastSendChars = 0;
            totalChars = calcCurrentChars() + Math.max(extraChars, (userText||'').length);
            hasTurn = true;
            windowTimeLabel = fwindow(Math.round((Date.now()-windowStart)/1000));
            turnStart = Date.now();
            turnEnd = null;
            phase = S_THINKING;
            if (tickTimer) clearInterval(tickTimer);
            tickTimer = setInterval(render, 200);
            render();
        };

        let backendTokens = 0;
        let backendCtx = 0;

        window.__clawSetHistoryTokens = (tokens) => {
            if (tokens > 0) {
                backendTokens = tokens;
                hasTurn = true;
                render();
            }
        };

        const fetchBackendTokens = async () => {
            try {
                const r = await hass.connection.sendMessagePromise({ type: 'ha_crack/get_context_status' });
                if (r?.tokens_used) backendTokens = r.tokens_used;
                if (r?.context_window) backendCtx = r.context_window;
            } catch(e) {}
        };

        const endTurn = () => {
            if (phase === S_IDLE) return;
            turnEnd = Date.now();
            phase = S_IDLE;
            if (tickTimer) { clearInterval(tickTimer); tickTimer = null; }
            fetchBackendTokens().then(render);
            render();
        };

        let hooked = false;

        const installHooks = () => {
            if (hooked) return;
            hooked = true;

            const origSubscribe = hass.connection.subscribeMessage.bind(hass.connection);
            hass.connection.subscribeMessage = function(callback, msg, ...rest) {
                if (msg?.type === 'assist_pipeline/run' && msg.start_stage === 'intent') {
                    startTurn(msg.input?.text);
                    const wrappedCb = (ev) => {
                        const t = ev.type, d = ev.data;
                        if (t === 'intent-progress' && d?.chat_log_delta) {
                            const delta = d.chat_log_delta;
                            if (delta.role === 'assistant') phase = S_REPLYING;
                            if (delta.content) {
                                totalChars += delta.content.length;
                                if (typeof window.__clawOnStreamDelta === 'function') window.__clawOnStreamDelta(delta);
                            }
                            if (delta.tool_calls) { phase = S_TOOL; totalChars += JSON.stringify(delta.tool_calls).length; }
                            if (delta.tool_result) {
                                phase = S_TOOL;
                                totalChars += JSON.stringify(delta.tool_result).length;
                                try {
                                    const result = typeof delta.tool_result === 'string' ? JSON.parse(delta.tool_result) : delta.tool_result;
                                    if (result?._navigate_to) {
                                        setTimeout(() => softNavigate(result._navigate_to), 500);
                                    }
                                } catch(e) {}
                            }
                            if (delta.tool_call_id && !delta.tool_calls) phase = S_TOOL;
                            render();
                        } else if (t === 'run-start') {
                            phase = S_THINKING;
                            render();
                        } else if (t === 'intent-end' || t === 'run-end' || t === 'error') {
                            if (!this.__clawSoundPlayed) {
                                let isError = (t === 'error');
                                if (!isError && t === 'intent-end' && d?.intent_output?.response?.response_type === 'error') {
                                    isError = true;
                                }
                                if (isError) {
                                    this.__clawSoundPlayed = true;
                                    if (typeof window.__clawPlaySound === 'function') window.__clawPlaySound('error');
                                } else if (t === 'intent-end' || t === 'run-end') {
                                    this.__clawSoundPlayed = true;
                                    if (typeof window.__clawOnStreamEnd === 'function') window.__clawOnStreamEnd();
                                }
                            }
                            if (t === 'run-end' || t === 'error') {
                                this.__clawSoundPlayed = false;
                                endTurn();
                            }
                        }
                        callback(ev);
                    };
                    return origSubscribe(wrappedCb, msg, ...rest);
                }
                return origSubscribe(callback, msg, ...rest);
            };
        };

        const injectCSS = (sr) => {
            if (!sr || sr.getElementById('claw-sb-css')) return;
            const s = document.createElement('style');
            s.id = 'claw-sb-css';
            s.textContent = `
                #${BAR_ID} {
                    display:flex; align-items:center; gap:7px;
                    padding:0px 16px 0px 30px;
                    font:500 11px/1 'SF Mono','Cascadia Code','Fira Code','Menlo','Consolas',monospace;
                    color:var(--secondary-text-color);
                    background:transparent;
                    user-select:none; min-height:22px; flex-shrink:0; opacity:0.88;
                }
                #${BAR_ID} .sb-sep { opacity:0.3; }
                #${BAR_ID} .sb-tok { font-variant-numeric:tabular-nums; }
                #${BAR_ID} .sb-bar { display:inline-flex; align-items:center; vertical-align:middle; }
                #${BAR_ID} .sb-pct { font-variant-numeric:tabular-nums; font-weight:500; font-size:10px; }
                #${BAR_ID} .sb-time { font-variant-numeric:tabular-nums; opacity:0.35; }
            `;
            sr.appendChild(s);
        };

        const render = () => {
            const chat = deepQuery('ha-assist-chat');
            if (!chat?.shadowRoot) return;
            const sr = chat.shadowRoot;
            if (!settings.enable_context_status_bar) {
                sr.getElementById(BAR_ID)?.remove();
                return;
            }
            injectCSS(sr);
            let bar = sr.getElementById(BAR_ID);
            if (!bar) {
                bar = document.createElement('div');
                bar.id = BAR_ID;
                bar.innerHTML =
                    '<span class="sb-tok" data-r="tok"></span>' +
                    '<span class="sb-sep">│</span>' +
                    '<span class="sb-bar" data-r="bar"></span> <span class="sb-pct" data-r="pct"></span>' +
                    '<span class="sb-sep">│</span>' +
                    '<span class="sb-time" data-r="win"></span>' +
                    '<span class="sb-sep">│</span>' +
                    '<svg width="11" height="11" viewBox="0 0 16 16" style="vertical-align:middle;opacity:0.6"><circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" stroke-width="2"/><path d="M8 4.5V8l2.5 1.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg> <span class="sb-time" data-r="timer"></span>';
                const inp = sr.querySelector('.input')||sr.querySelector('.chatbox')||sr.querySelector('[class*="input"]');
                if (inp) inp.parentNode.insertBefore(bar, inp);
                else { const m=sr.querySelector('.messages'); if(m) m.parentNode.insertBefore(bar,m.nextSibling); else sr.appendChild(bar); }
            }

            if (hasTurn) totalChars = calcCurrentChars();
            const localTk = Math.round(totalChars/CPT * 1.25) + (hasTurn ? BASE_PROMPT_TOKENS : 0);
            const tk = backendTokens > localTk ? backendTokens : localTk;
            const ctxW = backendCtx || CTX;
            const pct = Math.min(100, Math.round(tk/ctxW*100));
            const pc = pctColor(pct);
            const active = phase !== S_IDLE;
            let timer = '--';
            if (active && turnStart) timer = ftime(Math.round((Date.now()-turnStart)/1000));
            else if (turnStart && turnEnd) timer = ftime(Math.round((turnEnd-turnStart)/1000));

            const $ = (r) => bar.querySelector(`[data-r="${r}"]`);
            $('tok').textContent = (hasTurn ? fmt(tk) : '--') + ' / ' + fmtR(ctxW);
            const barW = 14;
            const barEl = $('bar');
            const cellW = 5, cellH = 9, dotSize = 1, dotGap = 2;
            const svgW = barW * cellW;
            let rects = '';
            if (hasTurn) {
                const totalHalf = barW * 2;
                const filledHalf = pct > 0 ? Math.min(totalHalf, Math.max(1, Math.round(pct/100*totalHalf) * 2)) : 0;
                for (let i = 0; i < barW; i++) {
                    const x = i * cellW;
                    const halfIdx = i * 2;
                    if (halfIdx + 1 < filledHalf) {
                        rects += `<rect x="${x}" y="0" width="${cellW}" height="${cellH}" fill="${pc}"/>`;
                    } else if (halfIdx < filledHalf) {
                        rects += `<rect x="${x}" y="0" width="${Math.floor(cellW/2)}" height="${cellH}" fill="${pc}"/>`;
                        for (let py = 0; py < cellH; py += dotGap) {
                            for (let px = Math.floor(cellW/2); px < cellW; px += dotGap) {
                                rects += `<rect x="${x+px}" y="${py}" width="${dotSize}" height="${dotSize}" fill="${pc}" opacity="0.3"/>`;
                            }
                        }
                    } else {
                        for (let py = 0; py < cellH; py += dotGap) {
                            for (let px = 0; px < cellW; px += dotGap) {
                                rects += `<rect x="${x+px}" y="${py}" width="${dotSize}" height="${dotSize}" fill="${pc}" opacity="0.3"/>`;
                            }
                        }
                    }
                }
            } else {
                for (let i = 0; i < barW; i++) {
                    const x = i * cellW;
                    for (let py = 0; py < cellH; py += dotGap) {
                        for (let px = 0; px < cellW; px += dotGap) {
                            rects += `<rect x="${x+px}" y="${py}" width="${dotSize}" height="${dotSize}" fill="var(--secondary-text-color)" opacity="0.3"/>`;
                        }
                    }
                }
            }
            barEl.innerHTML = `<svg width="${svgW}" height="${cellH}" viewBox="0 0 ${svgW} ${cellH}" style="display:block">${rects}</svg>`;
            const barColor = hasTurn ? pc : 'var(--secondary-text-color)';
            const pctEl = $('pct');
            pctEl.textContent = hasTurn ? Math.max(1, pct)+'%' : '--%';
            pctEl.style.color = barColor;
            $('win').textContent = hasTurn ? windowTimeLabel : '--';
            $('timer').textContent = timer;
        };

        window.addEventListener('claw-chat-updated', () => {
            if (settings.enable_context_status_bar) {
                const chars = calcCurrentChars();
                if (chars > 0) {
                    hasTurn = true;
                    totalChars = chars;
                }
                installHooks();
                render();
            }
        });

        const startStatusBar = () => {
            if (statusLoop) return;
            statusLoop = setInterval(() => {
                if (!settings.enable_context_status_bar) return;
                if (deepQuery('ha-assist-chat')?.shadowRoot) {
                    installHooks();
                    render();
                } else if (!settings.continuous_conversation && hasTurn) {
                    resetState(false);
                }
            }, 800);
        };

        const stopStatusBar = () => {
            if (!statusLoop) return;
            clearInterval(statusLoop);
            statusLoop = null;
        };
    }

    function reportState(data) {
        const hass = getHass();
        if (hass?.connection) {
            try {
                hass.connection.sendMessagePromise({ type: 'ha_crack/report_state', data }).catch(() => {});
            } catch(e) {}
        }
    }
    
    function exposeGlobalAPI() {
        window.HACrack = {
            hass: getHass,
            getPanel: getMainPanel,
            getSidebar: getSidebar,
            
            deepQuery: deepQuery,
            deepQueryAll: deepQueryAll,
            
            navigate: navigateTo,
            softNavigate: softNavigate,
            exec: executeJS,
            reportState: reportState,
            
            callService: (domain, service, data) => getHass()?.callService(domain, service, data),
            getStates: () => getHass()?.states,
            getState: (entityId) => getHass()?.states?.[entityId],
            
            clickSidebar: (itemText) => {
                removeOverlays();
                const sidebar = getMainPanel()?.querySelector('ha-sidebar')?.shadowRoot;
                const items = sidebar.querySelectorAll('a, paper-icon-item, ha-icon-button, [role="option"]');
                const textLower = itemText.toLowerCase();
                for (const item of items) {
                    const text = (item.textContent?.trim() || item.getAttribute('aria-label') || '').toLowerCase();
                    if (text.includes(textLower)) {
                        item.click();
                        return true;
                    }
                }
                return false;
            },
            
            getSidebarItems: () => {
                const hass = getHass();
                if (!hass) return [];
                
                const panels = hass.panels || {};
                const items = Object.entries(panels).map(([key, panel]) => ({
                    url_path: key,
                    title: panel.title || key,
                    icon: panel.icon || '',
                    component_name: panel.component_name || '',
                    config: panel.config || {}
                }));
                
                return items.sort((a, b) => (a.title || '').localeCompare(b.title || ''));
            },
            
            getPanels: () => {
                const hass = getHass();
                return hass?.panels || {};
            },
            
            removeOverlays: removeOverlays,
            
            click: (selector) => {
                removeOverlays();
                const el = deepQuery(selector);
                if (el) { 
                    el.focus?.();
                    el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                    el.click?.();
                    return true; 
                }
                return false;
            },
            
            getClickables: () => {
                return getAllClickables().map((el, i) => ({
                    index: i,
                    tag: el.tagName.toLowerCase(),
                    text: (el.textContent?.trim() || el.getAttribute('aria-label') || el.getAttribute('title') || '').slice(0, 80)
                }));
            },
            
            clickByIndex: (index) => {
                removeOverlays();
                const els = getAllClickables();
                if (els[index]) { 
                    const el = els[index];
                    el.focus?.();
                    el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                    el.click?.();
                    return true; 
                }
                return false;
            },
            
            clickByText: (text) => {
                removeOverlays();
                const textLower = text.toLowerCase();
                const els = getAllClickables();
                for (const el of els) {
                    const elText = (el.textContent?.trim() || el.getAttribute('aria-label') || el.getAttribute('title') || '').toLowerCase();
                    if (elText.includes(textLower)) {
                        el.focus?.();
                        el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                        el.click?.();
                        return true;
                    }
                }
                return false;
            },
            
            getInputs: () => {
                return getAllInputs().map((el, i) => ({
                    index: i,
                    type: el.type || el.tagName.toLowerCase(),
                    placeholder: el.placeholder || el.label || '',
                    value: (el.value || '').slice(0, 30)
                }));
            },
            
            fillInput: (indexOrSelector, value) => {
                let el;
                if (typeof indexOrSelector === 'number') {
                    el = getAllInputs()[indexOrSelector];
                } else {
                    el = deepQuery(indexOrSelector);
                }
                if (el) {
                    el.value = value;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }
                return false;
            },
            
            injectCSS: (css) => {
                const style = document.createElement('style');
                style.textContent = css;
                document.head.appendChild(style);
                return true;
            },
            
            injectHTML: (selector, html, position = 'beforeend') => {
                const el = deepQuery(selector) || document.querySelector(selector);
                if (el) { el.insertAdjacentHTML(position, html); return true; }
                return false;
            },
            
            remove: (selector) => {
                const el = deepQuery(selector);
                if (el) { el.remove(); return true; }
                return false;
            },
            
            hide: (selector) => {
                const el = deepQuery(selector);
                if (el) { el.style.display = 'none'; return true; }
                return false;
            },
            
            show: (selector) => {
                const el = deepQuery(selector);
                if (el) { el.style.display = ''; return true; }
                return false;
            },
            
            setStyle: (selector, styles) => {
                const el = deepQuery(selector);
                if (el) { Object.assign(el.style, styles); return true; }
                return false;
            },
            
            getPageInfo: () => ({
                url: location.href,
                path: location.pathname,
                title: document.title,
                buttons: getAllClickables().length,
                inputs: getAllInputs().length,
                cards: deepQueryAll('ha-card').length,
            }),
            
            highlight: (selector, color = 'red', duration = 3000) => {
                const el = deepQuery(selector);
                if (el) {
                    const orig = el.style.outline;
                    el.style.outline = `3px solid ${color}`;
                    setTimeout(() => el.style.outline = orig, duration);
                    return true;
                }
                return false;
            },
            
            pressKey: (key, modifiers = {}) => {
                const opts = { key, bubbles: true, ...modifiers };
                document.activeElement.dispatchEvent(new KeyboardEvent('keydown', opts));
                document.activeElement.dispatchEvent(new KeyboardEvent('keyup', opts));
            },
            
            focus: (selector) => {
                const el = deepQuery(selector);
                if (el) { el.focus(); return true; }
                return false;
            },
            
            blur: () => document.activeElement?.blur(),
            
            scroll: (y) => window.scrollTo({ top: y, behavior: 'smooth' }),
            scrollTo: (selector) => deepQuery(selector)?.scrollIntoView({ behavior: 'smooth' }),
            
            wait: (ms) => new Promise(r => setTimeout(r, ms)),
            
            waitFor: (selector, timeout = 5000) => {
                return new Promise((resolve, reject) => {
                    const start = Date.now();
                    const check = () => {
                        const el = deepQuery(selector);
                        if (el) return resolve(el);
                        if (Date.now() - start > timeout) return reject(new Error('Timeout'));
                        setTimeout(check, 100);
                    };
                    check();
                });
            },
            
            observe: (selector, callback) => {
                const observer = new MutationObserver((mutations) => {
                    const el = deepQuery(selector);
                    if (el) {
                        observer.disconnect();
                        callback(el);
                    }
                });
                observer.observe(document.body, { childList: true, subtree: true });
                return observer;
            },
            
            fireEvent: (eventType, data = {}) => {
                getHass()?.connection?.sendMessage({ type: 'fire_event', event_type: eventType, event_data: data });
            },
            
            subscribe: (eventType, callback) => {
                return getHass()?.connection?.subscribeEvents(callback, eventType);
            },
            
            screenshot: () => window.HACrack.getPageInfo(),
            
            clearEffect: () => {
                const existing = document.getElementById('ha-crack-effect-container');
                if (existing) { existing.remove(); return true; }
                return false;
            },
            
            eval: (code) => {
                try {
                    return eval(code);
                } catch(e) {
                    console.error('[HACrack] eval error:', e);
                    return null;
                }
            },
            
            debug: () => ({
                hass: !!getHass(),
                connection: !!getHass()?.connection,
                states: Object.keys(getHass()?.states || {}).length,
                clickables: getAllClickables().length,
                inputs: getAllInputs().length
            }),
            
            getAssistDialog: () => {
                return deepQuery('.mdc-dialog__surface') || deepQuery('ha-dialog');
            },
            
            getAssistChat: () => {
                return deepQuery('ha-assist-chat');
            },
            
            injectToAssistDialog: (html, position = 'beforeend') => {
                const dialog = deepQuery('.mdc-dialog__content') || deepQuery('#content.mdc-dialog__content');
                if (dialog) {
                    dialog.insertAdjacentHTML(position, html);
                    return true;
                }
                return false;
            },
            
            setAssistTitle: (title) => {
                const titleEl = deepQuery('.mdc-dialog__title') || deepQuery('#title.mdc-dialog__title');
                if (titleEl) {
                    titleEl.textContent = title;
                    return true;
                }
                return false;
            },
            
            addAssistAction: (text, callback) => {
                const footer = deepQuery('#actions.mdc-dialog__actions') || deepQuery('footer#actions');
                if (footer) {
                    const btn = document.createElement('mwc-button');
                    btn.textContent = text;
                    btn.onclick = callback;
                    footer.querySelector('span:last-child')?.appendChild(btn);
                    return true;
                }
                return false;
            },
            
            observeAssistDialog: (callback) => {
                const observer = new MutationObserver(() => {
                    const dialog = deepQuery('.mdc-dialog__surface');
                    if (dialog) {
                        callback(dialog);
                    }
                });
                observer.observe(document.body, { childList: true, subtree: true });
                return observer;
            },
            
            injectShadowCSS: (element, css, id) => {
                if (!element || !element.shadowRoot) return false;
                const existingStyle = element.shadowRoot.getElementById(id);
                if (existingStyle) {
                    existingStyle.textContent = css;
                    return true;
                }
                const style = document.createElement('style');
                style.id = id;
                style.textContent = css;
                element.shadowRoot.appendChild(style);
                return true;
            },
            
            injectGlobalCSS: (css, id = 'ha-crack-global') => {
                let style = document.getElementById(id);
                if (!style) {
                    style = document.createElement('style');
                    style.id = id;
                    document.head.appendChild(style);
                }
                style.textContent = css;
                return true;
            },
            
            injectSidebarCSS: (css) => {
                const sidebar = getMainPanel()?.querySelector('ha-sidebar');
                if (sidebar?.shadowRoot) {
                    return window.HACrack.injectShadowCSS(sidebar, css, 'ha-crack-sidebar');
                }
                return false;
            },
            
            injectPanelCSS: (css) => {
                const panel = getMainPanel()?.querySelector('partial-panel-resolver');
                if (panel?.shadowRoot) {
                    return window.HACrack.injectShadowCSS(panel, css, 'ha-crack-panel');
                }
                return false;
            },
            
            injectDialogCSS: (css) => {
                const dialog = deepQuery('ha-voice-command-dialog');
                if (dialog?.shadowRoot) {
                    window.HACrack.injectShadowCSS(dialog, css, 'ha-crack-dialog');
                    const haDialog = dialog.shadowRoot.querySelector('ha-dialog');
                    if (haDialog?.shadowRoot) {
                        window.HACrack.injectShadowCSS(haDialog, css, 'ha-crack-dialog-inner');
                    }
                    const assistChat = dialog.shadowRoot.querySelector('ha-assist-chat');
                    if (assistChat?.shadowRoot) {
                        window.HACrack.injectShadowCSS(assistChat, css, 'ha-crack-chat');
                    }
                    return true;
                }
                return false;
            },
            
            injectAllCSS: (css) => {
                window.HACrack.injectGlobalCSS(css);
                window.HACrack.injectSidebarCSS(css);
                window.HACrack.injectPanelCSS(css);
                window.HACrack.injectDialogCSS(css);
                deepQueryAll('*').forEach(el => el.shadowRoot && window.HACrack.injectShadowCSS(el, css, 'ha-crack-injected'));
                return true;
            },
            
            injectJS: (code, id = 'ha-crack-js') => {
                let script = document.getElementById(id);
                script && script.remove();
                script = document.createElement('script');
                script.id = id;
                script.textContent = code;
                document.head.appendChild(script);
                return true;
            },
            
            injectShadowJS: (element, code, id = 'ha-crack-shadow-js') => {
                const root = element?.shadowRoot;
                root || (element = null);
                let script = root?.getElementById(id);
                script && script.remove();
                script = document.createElement('script');
                script.id = id;
                script.textContent = code;
                root?.appendChild(script);
                return !!root;
            },
            
            injectModule: (url, id = 'ha-crack-module') => {
                let script = document.getElementById(id);
                script && script.remove();
                script = document.createElement('script');
                script.id = id;
                script.type = 'module';
                script.src = url;
                document.head.appendChild(script);
                return true;
            },
            
            injectLink: (href, rel = 'stylesheet', id = 'ha-crack-link') => {
                let link = document.getElementById(id);
                link && link.remove();
                link = document.createElement('link');
                link.id = id;
                link.rel = rel;
                link.href = href;
                document.head.appendChild(link);
                return true;
            },
            
            injectMeta: (name, content, id) => {
                const metaId = id || `ha-crack-meta-${name}`;
                let meta = document.getElementById(metaId);
                meta && meta.remove();
                meta = document.createElement('meta');
                meta.id = metaId;
                meta.name = name;
                meta.content = content;
                document.head.appendChild(meta);
                return true;
            },
            
            injectFont: (fontFamily, src, id = 'ha-crack-font') => {
                const css = `@font-face { font-family: '${fontFamily}'; src: url('${src}'); }`;
                return window.HACrack.injectGlobalCSS(css, id);
            },
            
            injectAll: (options = {}) => {
                options.css && window.HACrack.injectAllCSS(options.css);
                options.js && window.HACrack.injectJS(options.js);
                options.module && window.HACrack.injectModule(options.module);
                options.link && window.HACrack.injectLink(options.link);
                options.font && window.HACrack.injectFont(options.font.family, options.font.src);
                return true;
            },
            
            preventAssistDialogClose: () => {
                let lastDialogState = false;
                const blockScrimClick = () => {
                    const voiceDialog = deepQuery('ha-voice-command-dialog');
                    if (voiceDialog && !lastDialogState) {
                        const haDialog = voiceDialog.shadowRoot?.querySelector('ha-dialog');
                        if (haDialog?.shadowRoot) {
                            const scrim = haDialog.shadowRoot.querySelector('.mdc-dialog__scrim');
                            if (scrim) scrim.style.pointerEvents = 'none';
                            const mdcDialog = haDialog.shadowRoot.querySelector('.mdc-dialog');
                            if (mdcDialog) {
                                mdcDialog.addEventListener('click', (e) => {
                                    if (e.target === mdcDialog) {
                                        e.stopPropagation();
                                        e.preventDefault();
                                    }
                                }, true);
                            }
                        }
                    }
                    lastDialogState = !!voiceDialog;
                };
                const observer = new MutationObserver(blockScrimClick);
                observer.observe(document.body, { childList: true, subtree: true });
                return observer;
            },
            debugAssistDialog: () => {
                return deepQuery('ha-voice-command-dialog');
            }
        };
    }
    
    async function pollPendingJS(hass) {
        try {
            const result = await hass.connection.sendMessagePromise({
                type: 'ha_crack/get_pending_js'
            });
            
            if (result && result.js_codes && result.js_codes.length > 0) {
                result.js_codes.forEach(code => {
                    const execResult = executeJS(code);
                });
            }
        } catch(e) {
            console.error('[HACrack] Poll error:', e.message);
        }
    }
    
    
    function navigateTo(path) {
        if (!path) return;
        
        if (window.location.pathname !== path) {
            window.location.href = path;
            return;
        }
        
    }
    
    function softNavigate(path) {
        if (!path) return false;
        
        const ha = document.querySelector('home-assistant');
        if (!ha) return false;
        
        if (typeof ha.navigate === 'function') {
            ha.navigate(path);
            return true;
        }
        
        const main = ha.shadowRoot?.querySelector('home-assistant-main');
        if (main && typeof main.navigate === 'function') {
            main.navigate(path);
            return true;
        }
        
        history.pushState(null, '', path);
        window.dispatchEvent(new CustomEvent('location-changed'));
        ha.dispatchEvent(new CustomEvent('location-changed'));
        return true;
    }
    
    function executeJS(code) {
        if (!code || typeof code !== 'string') return { success: false, error: 'Invalid code' };
        
        let processedCode = code.trim();
        
        try {
            processedCode = processedCode.replace(/^\uFEFF/, '').replace(/[\u200B-\u200D\uFEFF]/g, '');
            processedCode = processedCode.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
            processedCode = processedCode.replace(/[""]/g, '"').replace(/['']/g, "'");
            
            if ((processedCode.startsWith('"') && processedCode.endsWith('"')) ||
                (processedCode.startsWith("'") && processedCode.endsWith("'"))) {
                try { processedCode = JSON.parse(processedCode); } catch(e) {}
            }
            
            if (processedCode.startsWith('```')) {
                processedCode = processedCode.replace(/^```\w*\n?/, '').replace(/\n?```$/, '');
            }
            
        } catch(e) {
            console.error('[HACrack] Preprocess error:', e.message);
        }
        
        try {
            console.log('[HACrack] Executing JS:', processedCode.substring(0, 100) + '...');
            
            if (processedCode.includes('await ')) {
                const asyncFn = new Function('return (async () => { ' + processedCode + ' })()');
                asyncFn().catch(e => console.error('[HACrack] Async error:', e.message));
                return { success: true };
            }
            
            const fn = new Function(processedCode);
            fn();
            
            return { success: true };
        } catch(e1) {
            try {
                if (processedCode.includes('await ')) {
                    eval('(async () => { ' + processedCode + ' })()');
                } else {
                    eval(processedCode);
                }
                return { success: true };
            } catch(e2) {
                console.error('[HACrack] Exec failed:', e1.message, e2.message);
                return { success: false, error: e2.message };
            }
        }
    }
    
    function tryInit() {
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', tryInit);
            return;
        }
        
        setupEventListeners();
        
        if (!initialized) {
            setTimeout(tryInit, 100);
        }
    }
    
    tryInit();
    
    window.addEventListener('load', () => {
        if (!initialized) setupEventListeners();
    });
    
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') {
            const hass = getHass();
            if (hass?.connection && !pollInterval) {
                pollInterval = setInterval(() => pollPendingJS(hass), 500);
            }
        }
    });
    
    window.addEventListener('focus', () => {
        const hass = getHass();
        if (hass?.connection && !pollInterval) {
            pollInterval = setInterval(() => pollPendingJS(hass), 500);
        }
    });
    
})();
