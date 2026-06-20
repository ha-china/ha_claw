(function() {
    'use strict';

    window.addEventListener('unhandledrejection', e => {
        if (e.reason?.message?.includes('nextSibling')) e.preventDefault();
    });
    
    const HACRACK_VERSION = '9.1.0';
    if (window.__hacrackVersion && window.__hacrackVersion !== HACRACK_VERSION) {
        const reloadKey = '__hacrackReloadCount';
        const reloads = parseInt(sessionStorage.getItem(reloadKey) || '0', 10);
        if (reloads < 1) {
            sessionStorage.setItem(reloadKey, String(reloads + 1));
            window.__hacrackVersion = HACRACK_VERSION;
            location.reload();
            return;
        }
        sessionStorage.removeItem(reloadKey);
    }
    window.__hacrackVersion = HACRACK_VERSION;
    let initialized = false;
    let pollInterval = null;
    let hassRef = null;
    
    function getHass() {
        const ha = document.querySelector('home-assistant');
        const live = ha?.hass;
        if (live?.connection) {
            hassRef = live;
            return live;
        }
        if (hassRef?.connection) return hassRef;
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


    function clawIsLiveSnapshot(snap) {
        if (!snap || !snap.active) return false;
        return !snap.recovered;
    }

    function getMainPanel() {
        return document.querySelector('home-assistant')?.shadowRoot?.querySelector('home-assistant-main')?.shadowRoot;
    }
    
    function getSidebar() {
        return getMainPanel()?.querySelector('ha-sidebar')?.shadowRoot;
    }
    
    let _dockedChat = null;
    function deepQuery(selector, root = document, depth = 0) {
        if (selector === 'ha-assist-chat' && _dockedChat && _dockedChat.isConnected && root === document) {
            return _dockedChat;
        }
        if (depth > 10) return null;
        let result = root.querySelector(selector);
        if (result) return result;
        const allElements = root.querySelectorAll('*');
        for (const el of allElements) {
            if (el.shadowRoot) {
                result = deepQuery(selector, el.shadowRoot, depth + 1);
                if (result) return result;
            }
        }
        return null;
    }
    
    function deepQueryAll(selector, root = document, results = [], depth = 0) {
        if (depth > 10) return results;
        root.querySelectorAll(selector).forEach(el => results.push(el));
        root.querySelectorAll('*').forEach(el => {
            if (el.shadowRoot) deepQueryAll(selector, el.shadowRoot, results, depth + 1);
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
        const hpcOverlay = document.getElementById('html-pro-card-overlay');
        let removed = 0;
        overlaySelectors.forEach(sel => {
            deepQueryAll(sel).forEach(el => {
                if (el.style && el.id !== 'html-pro-card-overlay' && !hpcOverlay?.contains(el)) {
                    el.style.pointerEvents = 'none';
                    removed++;
                }
            });
        });
        const highZElements = deepQueryAll('*').filter(el => {
            if (el.id === 'html-pro-card-overlay' || hpcOverlay?.contains(el)) return false;
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
        pollInterval = setInterval(() => pollPendingJS(hass), 1500);
        pollPendingJS(hass);
        exposeGlobalAPI();
        registerHistoryWindow(hass);
        setupContinuousConversation(hass);
        setupAssistRightDock(hass);
        setupContextStatusBar(hass);
        setupFileUpload(hass);
        setupFrontendBridge(hass);
        setupSoundNotifications(hass);
        setupSlashCommands(hass);
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
            'new': MEDIA_BASE + 'new.mp3',
        };

        let _soundEnabled = true;
        window.__clawToolDetailsEnabled = false;
        window.__clawToolProgressEnabled = true;
        try { window.__clawToolDetailsEnabled = localStorage.getItem('claw_tool_details') === '1'; } catch(e) {}
        try { window.__clawToolProgressEnabled = localStorage.getItem('claw_tool_progress') !== '0'; } catch(e) {}
        let _toolDetailsInitialLoaded = false;
        const refreshSoundSetting = async () => {
            try {
                const r = await hass.connection.sendMessagePromise({ type: 'ha_crack/get_settings' });
                _soundEnabled = r?.enable_sound_notifications !== false;
                const newDetails = r?.enable_tool_details === true;
                const newProgress = r?.enable_tool_progress !== false;
                if (_toolDetailsInitialLoaded && newDetails !== window.__clawToolDetailsEnabled) {
                    location.reload();
                    return;
                }
                window.__clawToolDetailsEnabled = newDetails;
                window.__clawToolProgressEnabled = newProgress;
                _toolDetailsInitialLoaded = true;
                try { localStorage.setItem('claw_tool_details', newDetails ? '1' : '0'); } catch(e) {}
                try { localStorage.setItem('claw_tool_progress', newProgress ? '1' : '0'); } catch(e) {}
                if (newDetails && window.__clawToolActivities?.length) {
                    window.dispatchEvent(new Event('claw-tool-details-changed'));
                }
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

        const _NAV_KEY = '__claw_user_path';
        const _AI_NAV_KEY = '__claw_ai_navigated';
        try {
            const aiNav = sessionStorage.getItem(_AI_NAV_KEY);
            const userPath = sessionStorage.getItem(_NAV_KEY);
            if (aiNav && userPath && window.location.pathname !== userPath) {
                sessionStorage.removeItem(_AI_NAV_KEY);
                setTimeout(() => {
                    history.replaceState(null, '', userPath);
                    window.dispatchEvent(new CustomEvent('location-changed'));
                }, 300);
            } else {
                sessionStorage.removeItem(_AI_NAV_KEY);
            }
        } catch(_) {}
        let _aiNavActive = false;
        window.addEventListener('location-changed', () => {
            if (_aiNavActive) return;
            try { sessionStorage.setItem(_NAV_KEY, window.location.pathname + window.location.search); } catch(_) {}
        });
        try { if (!sessionStorage.getItem(_NAV_KEY)) sessionStorage.setItem(_NAV_KEY, window.location.pathname + window.location.search); } catch(_) {}

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
            const isNav = /pushState|replaceState|location-changed|location\.href|navigate/.test(task.code);
            if (isNav) {
                _aiNavActive = true;
                try { sessionStorage.setItem(_AI_NAV_KEY, '1'); } catch(_) {}
            }
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
            if (isNav) { setTimeout(() => { _aiNavActive = false; }, 500); }
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

        // --- User Activity Tracker ---
        if (!window.__clawActivityInstalled) {
            let _actTrackingEnabled = true;
            conn.sendMessagePromise({ type: 'ha_crack/get_settings' }).then(r => {
                _actTrackingEnabled = r?.enable_activity_tracking !== false;
            }).catch(() => {});
            window.__clawActivityInstalled = true;
            const _actRing = [];
            const _ACT_MAX = 10;
            const _pushAct = (type, detail, extra) => {
                if (!_actTrackingEnabled) return;
                const entry = { type, detail: String(detail || '').slice(0, 200), path: location.pathname };
                if (extra) Object.assign(entry, extra);
                _actRing.push(entry);
                if (_actRing.length > _ACT_MAX) _actRing.splice(0, _actRing.length - _ACT_MAX);
            };
            let _lastNavPath = location.pathname;
            const _scrapePageContext = () => {
                const ctx = [];
                const ha = document.querySelector('home-assistant');
                const root = ha?.shadowRoot;
                const toolbar = root?.querySelector('app-toolbar, .toolbar, ha-top-app-bar-fixed');
                const title = toolbar?.querySelector?.('.title, [main-title]')?.textContent?.trim()
                    || root?.querySelector?.('ha-panel-lovelace')?.shadowRoot?.querySelector?.('.toolbar .title')?.textContent?.trim()
                    || document.title || '';
                if (title) ctx.push(title.slice(0, 80));
                const pageHeader = root?.querySelector?.('h1, .page-title, .header-title, [slot="header"]');
                const headerText = pageHeader?.textContent?.trim();
                if (headerText && headerText !== title) ctx.push(headerText.slice(0, 80));
                const panel = root?.querySelector?.('[panel]');
                if (panel) {
                    const pName = panel.getAttribute?.('panel') || '';
                    if (pName && !ctx.some(c => c.includes(pName))) ctx.push(pName);
                }
                const deepEl = root?.querySelector?.('hass-subpage, ha-config-section, hc-lovelace');
                const deepHeader = deepEl?.shadowRoot?.querySelector?.('.header, h1, .title');
                const deepText = deepHeader?.textContent?.trim();
                if (deepText && !ctx.includes(deepText)) ctx.push(deepText.slice(0, 80));
                return ctx.filter(Boolean).join(' > ').slice(0, 200);
            };
            window.addEventListener('location-changed', () => {
                const p = location.pathname;
                if (p !== _lastNavPath) {
                    _lastNavPath = p;
                    setTimeout(() => {
                        const pageCtx = _scrapePageContext();
                        _pushAct('navigate', pageCtx ? `${p} (${pageCtx})` : p);
                    }, 500);
                    const dirty = window.__clawDirtyDashboards;
                    if (dirty) {
                        for (const dashUrl of Object.keys(dirty)) {
                            if (p.indexOf('/' + dashUrl) === 0) {
                                delete dirty[dashUrl];
                                setTimeout(() => {
                                    try {
                                        const ha = document.querySelector('home-assistant');
                                        const main = ha?.shadowRoot?.querySelector('home-assistant-main');
                                        const msr = main?.shadowRoot;
                                        const drawer = msr?.querySelector('ha-drawer');
                                        const dsr = drawer?.shadowRoot;
                                        const app = dsr?.querySelector('.mdc-drawer-app-content');
                                        const pr = app?.querySelector('partial-panel-resolver');
                                        const panel = pr?.querySelector('ha-panel-lovelace');
                                        if (panel) {
                                            const ll = panel.lovelace || panel._lovelace;
                                            if (ll?.fetchConfig) ll.fetchConfig(true);
                                            else if (ll?.loadConfig) ll.loadConfig(true);
                                        }
                                    } catch(e) {}
                                }, 1000);
                                break;
                            }
                        }
                    }
                }
            });
            window.addEventListener('claw-dialog-opened', (ev) => {
                const d = ev.detail?.[0];
                _pushAct('dialog_open', d?.title || 'dialog');
            });
            window.addEventListener('claw-dialog-closed', () => {
                _pushAct('dialog_close', '');
            });
            const _inDock = (ev) => {
                const p = ev.composedPath?.() || [];
                for (const n of p) {
                    if (n.id === 'claw-assist-dock') return true;
                    if (n.tagName === 'HA-ASSIST-CHAT' || n.tagName === 'HA-VOICE-COMMAND-DIALOG') return true;
                }
                return false;
            };
            let _ptrDownTs = 0;
            document.addEventListener('pointerdown', () => { _ptrDownTs = Date.now(); }, true);
            document.addEventListener('pointerup', (ev) => {
                if (_inDock(ev)) { _ptrDownTs = 0; return; }
                const dur = Date.now() - _ptrDownTs;
                if (dur > 500) {
                    const t = ev.composedPath?.()?.[0] || ev.target;
                    const text = (t?.textContent || '').trim().slice(0, 60);
                    _pushAct('long_press', text || location.pathname);
                }
                _ptrDownTs = 0;
            }, true);
            document.addEventListener('dblclick', (ev) => {
                if (_inDock(ev)) return;
                const t = ev.composedPath?.()?.[0] || ev.target;
                const text = (t?.textContent || '').trim().slice(0, 60);
                _pushAct('double_click', text || location.pathname);
            }, true);
            document.addEventListener('click', (ev) => {
                if (_inDock(ev)) return;
                const path = ev.composedPath?.() || [];
                const t = path[0] || ev.target;
                if (!t) return;
                const _findInPath = (sel) => {
                    for (const n of path) {
                        if (n.matches?.(sel)) return n;
                        if (n.closest?.(sel)) return n.closest(sel);
                    }
                    return null;
                };
                const _entityId = (el) => {
                    if (!el) return '';
                    for (const n of path) {
                        const e = n.getAttribute?.('data-entity-id') || '';
                        if (e) return e;
                    }
                    const cfg = el.__config || el.config || el._config;
                    return (cfg?.entity || cfg?.entity_id || '');
                };
                const _label = (el) => {
                    if (!el) return '';
                    const lbl = el.getAttribute?.('aria-label') || el.getAttribute?.('label')
                        || el.closest?.('[aria-label]')?.getAttribute?.('aria-label') || '';
                    return lbl.trim().slice(0, 60);
                };
                const _nearLabel = (el) => {
                    if (!el) return '';
                    const prev = el.previousElementSibling;
                    if (prev?.tagName === 'LABEL' || prev?.classList?.contains?.('label'))
                        return prev.textContent?.trim()?.slice(0, 60) || '';
                    const parent = el.parentElement;
                    const lbl = parent?.querySelector?.('label, .label, .title')?.textContent?.trim();
                    return (lbl || '').slice(0, 60);
                };
                const row = _findInPath('ha-state-icon, state-badge, .entity, [data-entity-id]');
                if (row) {
                    const eid = _entityId(row);
                    const toggle = _findInPath('ha-switch, mwc-switch');
                    const action = toggle ? 'entity_toggle' : 'entity_click';
                    if (eid) _pushAct(action, eid, { entity_id: eid });
                    return;
                }
                const listItem = _findInPath('ha-list-item, mwc-list-item, ha-settings-row, ha-clickable-list-item');
                if (listItem) {
                    const text = listItem.textContent?.trim()?.slice(0, 80) || '';
                    _pushAct('list_item', text);
                    return;
                }
                const standaloneToggle = _findInPath('ha-switch, ha-formfield, mwc-switch');
                if (standaloneToggle) {
                    const name = _label(standaloneToggle) || _nearLabel(standaloneToggle) || '';
                    _pushAct('toggle', name || 'switch');
                    return;
                }
                const select = _findInPath('ha-select, ha-combo-box, mwc-select, ha-dropdown-menu, select');
                if (select) {
                    const val = select.value || select.getAttribute?.('value') || '';
                    const name = _label(select) || _nearLabel(select) || '';
                    _pushAct('select', `${name}: ${val}`.trim().slice(0, 200));
                    return;
                }
                const card = _findInPath('ha-card');
                if (card) {
                    const parent = card.parentElement?.closest?.('[data-entity-id]') || card.parentElement;
                    const cfg = parent?.__config || parent?.config || parent?._config || {};
                    const eid = _entityId(card) || cfg.entity || '';
                    const cardType = cfg.type || card.getAttribute?.('data-card-type') || '';
                    const title = cfg.title || card.querySelector?.('.card-header')?.textContent?.trim() || '';
                    const clickedText = (t.textContent || '').trim().slice(0, 60);
                    const detail = [cardType, title, eid, clickedText].filter(Boolean).join(' | ');
                    _pushAct('card_click', detail.slice(0, 200), eid ? { entity_id: eid } : undefined);
                    return;
                }
                const panel = _findInPath('hass-subpage, ha-panel-iframe, ha-panel-custom, [panel]');
                if (panel) {
                    const panelName = panel.getAttribute?.('panel') || panel.tagName?.toLowerCase() || '';
                    const innerText = (t.textContent || '').trim().slice(0, 60);
                    _pushAct('panel_click', `${panelName}: ${innerText}`.slice(0, 200));
                    return;
                }
                const btn = _findInPath('mwc-button, ha-button, button, a[href]');
                if (btn) {
                    const label = (btn.textContent || '').trim().slice(0, 60);
                    if (label) _pushAct('button_click', label);
                }
            }, true);
            const _flushAct = () => {
                if (!_actRing.length) return;
                const c = getConn();
                if (!c) return;
                const batch = _actRing.splice(0, _actRing.length);
                c.sendMessagePromise({ type: 'ha_crack/user_activity', actions: batch }).catch(() => {
                    _actRing.unshift(...batch.slice(-_ACT_MAX));
                });
            };
            setInterval(_flushAct, 8000);
            window.addEventListener('claw-chat-updated', _flushAct);
        }

    }

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
            margin: 0.5em 0; font-weight: normal; line-height: 1.6; font-size: 1em; display: block;
        }
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

    const _CODE_PANEL_CSS = `
        .claw-code-panel {
            margin: 0.5em 0;
            overflow: hidden;
            border: none;
            background: none;
            max-width: 100%;
            min-width: 0;
            box-sizing: border-box;
        }
        .claw-code-panel .claw-cp-header {
            display: flex;
            align-items: center;
            padding: 6px 4px;
            gap: 8px;
            background: none;
            border: none;
            position: relative;
            cursor: pointer;
            user-select: none;
            -webkit-user-select: none;
            min-height: 28px;
        }
        .claw-code-panel .claw-cp-header::after {
            content: '';
            position: absolute;
            bottom: 0;
            left: 8px;
            right: 8px;
            height: 1px;
            background: var(--divider-color, rgba(0,0,0,.08));
        }
        .claw-code-panel .claw-cp-header:hover {
            opacity: 0.8;
        }
        .claw-code-panel .claw-cp-lang {
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
            padding: 2px 6px;
            border-radius: 4px;
            background: var(--primary-color, #03a9f4);
            color: #fff;
            line-height: 1;
            flex-shrink: 0;
        }
        .claw-code-panel .claw-cp-filename {
            flex: 1;
            font-size: 13px;
            font-family: 'SF Mono', ui-monospace, monospace;
            color: var(--primary-text-color);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .claw-code-panel .claw-cp-stats {
            font-size: 12px;
            font-family: 'SF Mono', ui-monospace, monospace;
            flex-shrink: 0;
            display: flex;
            gap: 6px;
        }
        .claw-code-panel .claw-cp-stats .cp-add { color: #22863a; }
        .claw-code-panel .claw-cp-stats .cp-del { color: #cb2431; }
        .claw-code-panel .claw-cp-chevron {
            width: 16px;
            height: 16px;
            fill: var(--secondary-text-color, #666);
            transition: transform .2s ease;
            flex-shrink: 0;
        }
        .claw-code-panel.collapsed .claw-cp-chevron {
            transform: rotate(-90deg);
        }
        .claw-code-panel .claw-cp-body {
            overflow: auto;
            transition: max-height .25s ease;
            max-height: 600px;
        }
        .claw-code-panel.collapsed .claw-cp-body {
            max-height: 0;
        }
        .claw-code-panel .claw-cp-body pre {
            margin: 0;
            padding: 10px 14px;
            font-size: 12.5px;
            line-height: 1.5;
            overflow-x: hidden;
            white-space: pre-wrap;
            overflow-wrap: anywhere;
            word-break: break-word;
            background: var(--code-editor-background-color, #1e1e1e);
            color: #d4d4d4;
            border-radius: 0;
            max-width: 100%;
            max-height: 420px;
            overflow: auto;
            box-sizing: border-box;
        }
        .claw-code-panel .claw-cp-body pre code {
            background: none;
            padding: 0;
            border-radius: 0;
            color: inherit;
            font-size: inherit;
            display: block;
        }
        .claw-code-panel .claw-cp-body .diff-line {
            display: block;
            padding: 0 4px;
            margin: 0 -14px;
            padding-left: 14px;
            padding-right: 14px;
        }
        .claw-code-panel .claw-cp-body .diff-add {
            background: rgba(46, 160, 67, 0.15);
            color: #3fb950;
        }
        .claw-code-panel .claw-cp-body .diff-del {
            background: rgba(248, 81, 73, 0.15);
            color: #f85149;
        }
        .claw-code-panel .claw-cp-body .diff-hunk {
            background: rgba(56, 139, 253, 0.1);
            color: #79c0ff;
        }
    `;

    const _TOOL_ACTIVITY_CSS = `
        .claw-tool-activities {
            display: flex; flex-direction: column; gap: 4px;
            margin: 6px 0 2px; max-width: 100%; min-width: 0;
        }
        .claw-ta-card {
            border-radius: 0;
            background: var(--secondary-background-color, rgba(128,128,128,0.06));
            overflow: hidden;
            animation: clawTaIn 0.2s ease;
            max-width: 100%; min-width: 0;
            box-sizing: border-box;
            margin: 4px 0;
        }
        ha-markdown-element { position: relative; }
        ha-markdown-element.claw-rerender::after {
            content: '';
            position: absolute; inset: 0;
            background: var(--card-background-color, #fff);
            opacity: 0;
            animation: clawFadeOut .15s ease forwards;
            pointer-events: none;
            z-index: 1;
        }
        @keyframes clawFadeOut {
            0% { opacity: .85; }
            100% { opacity: 0; }
        }
        @keyframes clawTaIn {
            from { opacity: 0; transform: translateY(4px); }
            to { opacity: 1; transform: none; }
        }
        .claw-ta-header {
            display: flex; align-items: center; gap: 4px;
            padding: 6px 10px;
            cursor: pointer; user-select: none; -webkit-user-select: none;
            min-height: 24px;
            overflow: hidden;
        }
        .claw-ta-header:hover { opacity: 0.7; }
        .claw-ta-icon {
            width: 16px; height: 16px; flex-shrink: 0;
            color: var(--primary-text-color);
            display: inline-flex; align-items: center;
            opacity: 0.7;
        }
        .claw-ta-icon svg { width: 100%; height: 100%; display: block; }
        .claw-ta-chevron {
            width: 16px; height: 16px; flex-shrink: 0;
            fill: var(--primary-text-color);
            transition: transform .15s ease;
            display: none;
        }
        .claw-ta-header:hover .claw-ta-icon { display: none; }
        .claw-ta-header:hover .claw-ta-chevron { display: inline-flex; }
        .claw-ta-card.collapsed .claw-ta-chevron { transform: rotate(-90deg); }
        .claw-ta-error .claw-ta-icon, .claw-ta-error .claw-ta-name b { color: #e53935; }
        .claw-ta-error .claw-ta-icon svg { fill: #e53935; }
        .claw-ta-warning .claw-ta-icon, .claw-ta-warning .claw-ta-name b { color: #f9a825; }
        .claw-ta-warning .claw-ta-icon svg { fill: #f9a825; }
        
        .claw-ta-name {
            flex: 1; min-width: 0;
            font: 400 12px/1.4 system-ui, sans-serif;
            color: var(--primary-text-color);
            overflow: hidden; text-overflow: ellipsis;
            white-space: nowrap;
        }
        .claw-ta-name b { font-weight: 600; font-size: 12px; }
        .claw-ta-time {
            font: 400 12px/1 'SF Mono', ui-monospace, monospace;
            color: var(--secondary-text-color, #888);
            margin-left: 4px;
        }
        .claw-ta-summary {
            font: 400 11px/1.3 'SF Mono', ui-monospace, monospace;
            color: var(--secondary-text-color, #999);
            margin-left: 4px;
        }
        .claw-ta-spinner {
            width: 12px; height: 12px; flex-shrink: 0;
            border: 2px solid var(--secondary-text-color, #888);
            border-top-color: transparent;
            border-radius: 50%;
            animation: clawSpin .6s linear infinite;
        }
        @keyframes clawSpin { to { transform: rotate(360deg); } }
        .claw-ta-think { cursor: pointer; }
        .claw-ta-think .claw-ta-summary { font-style: italic; color: var(--secondary-text-color, #999); }
        .claw-ta-body {
            overflow: hidden; transition: max-height .25s ease;
            max-height: 400px;
        }
        .claw-ta-card.collapsed .claw-ta-body { max-height: 0; }
        .claw-ta-body-inner {
            padding: 0 8px 8px;
            font: 400 11px/1.4 'SF Mono','Fira Code','Cascadia Code',ui-monospace,monospace;
            color: var(--primary-text-color);
            white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-word;
            max-height: 100px; overflow-y: auto;
            box-sizing: border-box;
        }
        .claw-ta-args {
            padding: 4px 10px;
            background: var(--code-editor-background-color, #1e1e1e);
            color: #d4d4d4; border-radius: 0;
            font: 400 11px/1.4 'SF Mono',ui-monospace,monospace;
            white-space: pre-wrap; overflow-wrap: anywhere;
            max-height: 120px; overflow-y: auto;
        }
        .claw-ta-result {
            padding: 0;
            font: 400 11px/1.4 'SF Mono',ui-monospace,monospace;
            color: var(--primary-text-color);
            max-height: 250px; overflow-y: auto;
        }
        .claw-ta-result pre {
            margin: 0; padding: 6px 10px;
            background: var(--code-editor-background-color, #1e1e1e);
            border-radius: 0;
            overflow-x: auto;
        }
        .claw-ta-result code {
            font: inherit; color: #d4d4d4;
            background: none; padding: 0; border-radius: 0;
            white-space: pre-wrap; overflow-wrap: anywhere;
        }
        .claw-ta-card code,
        .claw-ta-card pre code,
        .claw-ta-card .claw-ta-args code,
        .claw-ta-card .claw-ta-result code,
        .claw-ta-card .claw-ta-cmd code {
            background: none !important; background-color: transparent !important;
            padding: 0 !important; border-radius: 0 !important;
            border: none !important; box-shadow: none !important;
            color: #d4d4d4 !important;
        }
        .claw-ta-card blockquote {
            border: none !important; margin: 0 !important; padding: 0 !important;
            background: none !important; color: #d4d4d4 !important;
        }
        .claw-ta-card p { margin: 0 !important; }
        .claw-ta-result.ta-error-result { color: var(--error-color, #db4437); padding: 6px 10px; }
        .claw-ta-diff { margin: 0; }
        .claw-ta-diff .diff-line { display: block; padding: 0 10px; }
        .claw-ta-diff .diff-add { background: rgba(46,160,67,0.15); color: #3fb950; }
        .claw-ta-diff .diff-del { background: rgba(248,81,73,0.15); color: #f85149; }
        .claw-ta-diff .diff-hunk { background: rgba(56,139,253,0.1); color: #79c0ff; }
        .claw-ta-cmd {
            display: flex; gap: 6px;
            padding: 6px 10px;
            background: var(--code-editor-background-color, #1e1e1e);
            color: #d4d4d4;
            font: 400 11px/1.4 'SF Mono',ui-monospace,monospace;
            white-space: pre-wrap; overflow-wrap: anywhere;
            max-height: 250px; overflow: auto;
            border-radius: 0;
        }
        .claw-ta-cmd-prompt { color: #6a9955; flex-shrink: 0; user-select: none; }
        .claw-ta-panel { margin-top: 4px; padding: 0 2px; overflow: hidden; max-width: 100%; box-sizing: border-box; }
        .claw-ta-panel.collapsed .claw-ta-panel-body { max-height: 0; }
        .claw-ta-panel-header {
            display: flex; align-items: center; gap: 6px;
            cursor: pointer; user-select: none; -webkit-user-select: none;
            margin: 10px 0;
            background: var(--secondary-background-color, rgba(128,128,128,0.06));
            font: 400 12px/1.4 system-ui, sans-serif;
            color: var(--primary-text-color);
        }
        .claw-ta-panel-header b { font-weight: 600; }
        .claw-ta-panel-count { color: var(--secondary-text-color, #888); font: 400 11px/1.3 'SF Mono', ui-monospace, monospace; }
        .claw-ta-panel-body { overflow: hidden; transition: max-height .25s ease; max-height: 600px; }
        .claw-ta-panel-chevron { width: 16px; height: 16px; fill: currentColor; transition: transform .15s ease; }
        .claw-ta-panel.collapsed .claw-ta-panel-chevron { transform: rotate(-90deg); }
    `;

    const _EXT_LANG = {js:'JS',ts:'TS',py:'PY',yaml:'YAML',yml:'YAML',json:'JSON',html:'HTML',css:'CSS',sh:'SH',bash:'BASH',md:'MD',txt:'TXT',jsx:'JSX',tsx:'TSX',rs:'RS',go:'GO',rb:'RB',java:'JAVA',c:'C',cpp:'CPP',h:'H',sql:'SQL',xml:'XML',toml:'TOML',ini:'INI'};

    const _transformCodeBlocks = (container) => {
        const pres = container.querySelectorAll('pre');
        pres.forEach(pre => {
            if (pre.closest('.claw-code-panel') || pre.closest('.claw-ta-card')) return;
            const code = pre.querySelector('code');
            if (!code) return;
            const raw = code.textContent || '';
            const lines = raw.split('\n');
            if (lines.length < 3) return;

            let lang = '';
            const cls = code.className || '';
            const langMatch = cls.match(/language-(\w+)/);
            if (langMatch) lang = langMatch[1];

            let filename = '';
            let isDiff = false;
            let addCount = 0, delCount = 0;

            if (lines[0] && /^[\/\\]|^\w+[\\/.]/.test(lines[0].trim()) && lines[0].trim().length < 120 && !lines[0].trim().includes(' ')) {
                filename = lines[0].trim();
                if (!lang) {
                    const ext = filename.split('.').pop()?.toLowerCase();
                    if (ext && _EXT_LANG[ext]) lang = ext;
                }
            }

            const diffIndicators = lines.filter(l => /^[+-]/.test(l) && !/^[+-]{3}/.test(l)).length;
            if (diffIndicators > 1 || lang === 'diff') {
                isDiff = true;
                lines.forEach(l => {
                    if (/^\+[^+]/.test(l) || (l === '+')) addCount++;
                    else if (/^-[^-]/.test(l) || (l === '-')) delCount++;
                });
            }

            const panel = document.createElement('div');
            panel.className = 'claw-code-panel';

            const header = document.createElement('div');
            header.className = 'claw-cp-header';
            let headerHtml = '';
            if (lang) headerHtml += '<span class="claw-cp-lang">' + ((_EXT_LANG[lang] || lang).toUpperCase()) + '</span>';
            headerHtml += '<span class="claw-cp-filename">' + (filename || (lang ? lang + ' code' : 'Code')) + '</span>';
            if (isDiff && (addCount || delCount)) {
                headerHtml += '<span class="claw-cp-stats">';
                if (addCount) headerHtml += '<span class="cp-add">+' + addCount + '</span>';
                if (delCount) headerHtml += '<span class="cp-del">-' + delCount + '</span>';
                headerHtml += '</span>';
            }
            headerHtml += '<svg class="claw-cp-chevron" viewBox="0 0 24 24"><path d="M11.9999 13.1714L16.9497 8.22168L18.3639 9.63589L11.9999 15.9999L5.63599 9.63589L7.0502 8.22168L11.9999 13.1714Z" fill="currentColor"/></svg>';
            header.innerHTML = headerHtml;
            header.addEventListener('click', () => { panel.classList.toggle('collapsed'); });

            const body = document.createElement('div');
            body.className = 'claw-cp-body';
            const newPre = document.createElement('pre');
            const newCode = document.createElement('code');

            if (isDiff) {
                const contentLines = filename ? lines.slice(1) : lines;
                newCode.innerHTML = contentLines.map(l => {
                    const escaped = l.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
                    if (/^\+[^+]/.test(l) || l === '+') return '<span class="diff-line diff-add">' + escaped + '</span>';
                    if (/^-[^-]/.test(l) || l === '-') return '<span class="diff-line diff-del">' + escaped + '</span>';
                    if (/^@@/.test(l)) return '<span class="diff-line diff-hunk">' + escaped + '</span>';
                    return '<span class="diff-line">' + escaped + '</span>';
                }).join('\n');
            } else {
                newCode.textContent = filename ? lines.slice(1).join('\n') : raw;
            }
            newPre.appendChild(newCode);
            body.appendChild(newPre);
            panel.appendChild(header);
            panel.appendChild(body);
            pre.replaceWith(panel);
        });
    };

    window.__clawToolActivities = [];
    window.__clawToolActivitySeen = false;
    window.__clawTurnParts = [];
    let _taRenderPending = false;
    let _turnEnded = false;

    const _TA_ICONS = {
        'add-circle': '<svg viewBox="0 0 24 24"><path d="M11 11V7H13V11H17V13H13V17H11V13H7V11H11ZM12 22C6.47715 22 2 17.5228 2 12C2 6.47715 6.47715 2 12 2C17.5228 2 22 6.47715 22 12C22 17.5228 17.5228 22 12 22ZM12 20C16.4183 20 20 16.4183 20 12C20 7.58172 16.4183 4 12 4C7.58172 4 4 7.58172 4 12C4 16.4183 7.58172 20 12 20Z" fill="currentColor"/></svg>',
        'add': '<svg viewBox="0 0 24 24"><path d="M11 11V5H13V11H19V13H13V19H11V13H5V11H11Z" fill="currentColor"/></svg>',
        'ai-agent-fill': '<svg viewBox="0 0 24 24"><path d="M12 2C17.5228 2 22 6.47715 22 12C22 17.5228 17.5228 22 12 22C6.47715 22 2 17.5228 2 12C2 6.47715 6.47715 2 12 2ZM12 15C9.71266 15 7.65042 15.961 6.19238 17.5C7.65042 19.039 9.71266 20 12 20C14.2871 20 16.3486 19.0387 17.8066 17.5C16.3486 15.9613 14.2871 15 12 15ZM12.4707 5.31934C12.2943 4.89337 11.7058 4.89339 11.5293 5.31934L11.2764 5.93066C10.8445 6.97341 10.0384 7.80621 9.02539 8.25684L8.30762 8.57617C7.89751 8.75905 7.89744 9.35625 8.30762 9.53906L9.06738 9.87695C10.0551 10.3163 10.8476 11.1193 11.2871 12.1279L11.5332 12.6934C11.7138 13.1073 12.2863 13.1073 12.4668 12.6934L12.7139 12.1279C13.1534 11.1194 13.9449 10.3163 14.9326 9.87695L15.6924 9.53906C16.1026 9.35624 16.1025 8.75907 15.6924 8.57617L14.9746 8.25684C13.9616 7.8062 13.1556 6.9734 12.7236 5.93066L12.4707 5.31934Z" fill="currentColor"/></svg>',
        'ai-agent': '<svg viewBox="0 0 24 24"><path d="M12 2C17.5228 2 22 6.47715 22 12C22 14.7096 20.9205 17.1697 19.1709 18.9697C17.3551 20.8376 14.8124 22 12 22C9.18756 22 6.64488 20.8376 4.8291 18.9697C3.07949 17.1697 2 14.7096 2 12C2 6.47715 6.47715 2 12 2ZM12 16C10.0022 16 8.20124 16.8375 6.9248 18.1816C8.30642 19.3175 10.0724 20 12 20C13.9274 20 15.6927 19.3173 17.0742 18.1816C15.7978 16.8377 13.9975 16 12 16ZM12 4C7.58172 4 4 7.58172 4 12C4 13.7701 4.57462 15.4044 5.54785 16.7295C7.1822 15.0483 9.46797 14 12 14C14.5318 14 16.8169 15.0485 18.4512 16.7295C19.4246 15.4043 20 13.7703 20 12C20 7.58172 16.4183 4 12 4ZM11.5293 5.31934C11.7058 4.89329 12.2943 4.89329 12.4707 5.31934L12.7236 5.93066C13.1556 6.97343 13.9615 7.80622 14.9746 8.25684L15.6924 8.5752C16.1029 8.75796 16.1028 9.35627 15.6924 9.53906L14.9326 9.87695C13.9448 10.3163 13.1534 11.1193 12.7139 12.1279L12.4668 12.6934C12.2864 13.1074 11.7137 13.1074 11.5332 12.6934L11.2871 12.1279C10.8476 11.1193 10.0552 10.3163 9.06738 9.87695L8.30762 9.53906C7.89719 9.35628 7.89717 8.75795 8.30762 8.5752L9.02539 8.25684C10.0385 7.80623 10.8445 6.97345 11.2764 5.93066L11.5293 5.31934Z" fill="currentColor"/></svg>',
        'ai-generate-2': '<svg viewBox="0 0 24 24"><path d="M20.4668 8.69379L20.7134 8.12811C21.1529 7.11947 21.9445 6.31641 22.9323 5.87708L23.6919 5.53922C24.1027 5.35653 24.1027 4.75881 23.6919 4.57612L22.9748 4.25714C21.9616 3.80651 21.1558 2.97373 20.7238 1.93083L20.4706 1.31953C20.2942 0.893489 19.7058 0.893489 19.5293 1.31953L19.2761 1.93083C18.8442 2.97373 18.0384 3.80651 17.0252 4.25714L16.308 4.57612C15.8973 4.75881 15.8973 5.35653 16.308 5.53922L17.0677 5.87708C18.0555 6.31641 18.8471 7.11947 19.2866 8.12811L19.5331 8.69379C19.7136 9.10792 20.2864 9.10792 20.4668 8.69379ZM5.79993 16H7.95399L8.55399 14.5H11.4459L12.0459 16H14.1999L10.9999 8H8.99993L5.79993 16ZM9.99993 10.8852L10.6459 12.5H9.35399L9.99993 10.8852ZM15 16V8H17V16H15ZM3 3C2.44772 3 2 3.44772 2 4V20C2 20.5523 2.44772 21 3 21H21C21.5523 21 22 20.5523 22 20V11H20V19H4V5H14V3H3Z" fill="currentColor"/></svg>',
        'alert': '<svg viewBox="0 0 24 24"><path d="M12.8659 3.00017L22.3922 19.5002C22.6684 19.9785 22.5045 20.5901 22.0262 20.8662C21.8742 20.954 21.7017 21.0002 21.5262 21.0002H2.47363C1.92135 21.0002 1.47363 20.5525 1.47363 20.0002C1.47363 19.8246 1.51984 19.6522 1.60761 19.5002L11.1339 3.00017C11.41 2.52187 12.0216 2.358 12.4999 2.63414C12.6519 2.72191 12.7782 2.84815 12.8659 3.00017ZM4.20568 19.0002H19.7941L11.9999 5.50017L4.20568 19.0002ZM10.9999 16.0002H12.9999V18.0002H10.9999V16.0002ZM10.9999 9.00017H12.9999V14.0002H10.9999V9.00017Z" fill="currentColor"/></svg>',
        'align-justify': '<svg viewBox="0 0 24 24"><path d="M3 4H21V6H3V4ZM3 19H21V21H3V19ZM3 14H21V16H3V14ZM3 9H21V11H3V9Z" fill="currentColor"/></svg>',
        'apple': '<svg viewBox="0 0 24 24"><path d="M15.778 8.20793C15.3053 8.1711 14.7974 8.28434 14.0197 8.58067C14.085 8.55577 13.2775 8.87173 13.0511 8.95077C12.5494 9.12593 12.1364 9.22198 11.6734 9.22198C11.2151 9.22198 10.7925 9.13042 10.3078 8.96683C10.1524 8.91441 9.99616 8.8564 9.80283 8.7809C9.71993 8.74852 9.41997 8.62947 9.3544 8.60379C8.70626 8.34996 8.34154 8.25434 8.03885 8.26181C6.88626 8.2765 5.79557 8.9421 5.16246 10.0442C3.87037 12.2875 4.58583 16.3428 6.47459 19.075C7.4802 20.5189 8.03062 21.035 8.25199 21.0279C8.4743 21.0183 8.63777 20.9713 9.03567 20.8026C9.11485 20.7689 9.11485 20.7689 9.202 20.7317C10.2077 20.3032 10.9118 20.114 11.9734 20.114C12.9944 20.114 13.6763 20.2997 14.6416 20.7159C14.7302 20.7542 14.7302 20.7542 14.8097 20.7884C15.2074 20.9588 15.3509 20.9962 15.6016 20.9902C15.9591 20.9846 16.4003 20.5726 17.3791 19.1362C17.6471 18.7447 17.884 18.3333 18.0895 17.9168C17.9573 17.8077 17.826 17.6917 17.6975 17.5693C16.4086 16.3408 15.6114 14.6845 15.5895 12.6391C15.5756 11.0186 16.1057 9.61487 16.999 8.45797C16.6293 8.3142 16.2216 8.23805 15.778 8.20793ZM15.9334 6.21398C16.6414 6.26198 18.6694 6.47798 19.9894 8.40998C19.8814 8.46998 17.5654 9.81397 17.5894 12.622C17.6254 15.982 20.5294 17.098 20.5654 17.11C20.5414 17.194 20.0974 18.706 19.0294 20.266C18.1054 21.622 17.1454 22.966 15.6334 22.99C14.1454 23.026 13.6654 22.114 11.9734 22.114C10.2694 22.114 9.74138 22.966 8.33738 23.026C6.87338 23.074 5.76938 21.562 4.83338 20.218C2.92538 17.458 1.47338 12.442 3.42938 9.04597C4.40138 7.35397 6.12938 6.28598 8.01338 6.26198C9.44138 6.22598 10.7974 7.22198 11.6734 7.22198C12.5374 7.22198 14.0854 6.06998 15.9334 6.21398ZM14.7934 4.38998C14.0134 5.32598 12.7414 6.05798 11.5054 5.96198C11.3374 4.68998 11.9614 3.35798 12.6814 2.52998C13.4854 1.59398 14.8294 0.897976 15.9454 0.849976C16.0894 2.14598 15.5734 3.45398 14.7934 4.38998Z" fill="currentColor"/></svg>',
        'archive': '<svg viewBox="0 0 24 24"><path d="M3 10H2V4.00293C2 3.44903 2.45531 3 2.9918 3H21.0082C21.556 3 22 3.43788 22 4.00293V10H21V20.0015C21 20.553 20.5551 21 20.0066 21H3.9934C3.44476 21 3 20.5525 3 20.0015V10ZM19 10H5V19H19V10ZM4 5V8H20V5H4ZM9 12H15V14H9V12Z" fill="currentColor"/></svg>',
        'archive-stack': '<svg viewBox="0 0 24 24"><path d="M4 5H20V3H4V5ZM20 9H4V7H20V9ZM3 11H10V13H14V11H21V20C21 20.5523 20.5523 21 20 21H4C3.44772 21 3 20.5523 3 20V11ZM16 13V15H8V13H5V19H19V13H16Z" fill="currentColor"/></svg>',
        'arrow-down': '<svg viewBox="0 0 24 24"><path d="M13.0001 16.1716L18.3641 10.8076L19.7783 12.2218L12.0001 20L4.22192 12.2218L5.63614 10.8076L11.0001 16.1716V4H13.0001V16.1716Z" fill="currentColor"/></svg>',
        'arrow-down-s': '<svg viewBox="0 0 24 24"><path d="M11.9999 13.1714L16.9497 8.22168L18.3639 9.63589L11.9999 15.9999L5.63599 9.63589L7.0502 8.22168L11.9999 13.1714Z" fill="currentColor"/></svg>',
        'arrow-go-back': '<svg viewBox="0 0 24 24"><path d="M5.82843 6.99955L8.36396 9.53509L6.94975 10.9493L2 5.99955L6.94975 1.0498L8.36396 2.46402L5.82843 4.99955H13C17.4183 4.99955 21 8.58127 21 12.9996C21 17.4178 17.4183 20.9996 13 20.9996H4V18.9996H13C16.3137 18.9996 19 16.3133 19 12.9996C19 9.68584 16.3137 6.99955 13 6.99955H5.82843Z" fill="currentColor"/></svg>',
        'arrow-go-forward': '<svg viewBox="0 0 24 24"><path d="M18.1716 6.99955H11C7.68629 6.99955 5 9.68584 5 12.9996C5 16.3133 7.68629 18.9996 11 18.9996H20V20.9996H11C6.58172 20.9996 3 17.4178 3 12.9996C3 8.58127 6.58172 4.99955 11 4.99955H18.1716L15.636 2.46402L17.0503 1.0498L22 5.99955L17.0503 10.9493L15.636 9.53509L18.1716 6.99955Z" fill="currentColor"/></svg>',
        'arrow-left': '<svg viewBox="0 0 24 24"><path d="M7.82843 10.9999H20V12.9999H7.82843L13.1924 18.3638L11.7782 19.778L4 11.9999L11.7782 4.22168L13.1924 5.63589L7.82843 10.9999Z" fill="currentColor"/></svg>',
        'arrow-left-long': '<svg viewBox="0 0 24 24"><path d="M22.0003 13.0001L22.0004 11.0002L5.82845 11.0002L9.77817 7.05044L8.36396 5.63623L2 12.0002L8.36396 18.3642L9.77817 16.9499L5.8284 13.0002L22.0003 13.0001Z" fill="currentColor"/></svg>',
        'arrow-left-right': '<svg viewBox="0 0 24 24"><path d="M16.0503 12.0498L21 16.9996L16.0503 21.9493L14.636 20.5351L17.172 17.9988L4 17.9996V15.9996L17.172 15.9988L14.636 13.464L16.0503 12.0498ZM7.94975 2.0498L9.36396 3.46402L6.828 5.9988L20 5.99955V7.99955L6.828 7.9988L9.36396 10.5351L7.94975 11.9493L3 6.99955L7.94975 2.0498Z" fill="currentColor"/></svg>',
        'arrow-left-s': '<svg viewBox="0 0 24 24"><path d="M10.8284 12.0007L15.7782 16.9504L14.364 18.3646L8 12.0007L14.364 5.63672L15.7782 7.05093L10.8284 12.0007Z" fill="currentColor"/></svg>',
        'arrow-right': '<svg viewBox="0 0 24 24"><path d="M16.1716 10.9999L10.8076 5.63589L12.2218 4.22168L20 11.9999L12.2218 19.778L10.8076 18.3638L16.1716 12.9999H4V10.9999H16.1716Z" fill="currentColor"/></svg>',
        'arrow-right-s': '<svg viewBox="0 0 24 24"><path d="M13.1717 12.0007L8.22192 7.05093L9.63614 5.63672L16.0001 12.0007L9.63614 18.3646L8.22192 16.9504L13.1717 12.0007Z" fill="currentColor"/></svg>',
        'arrow-up-double': '<svg viewBox="0 0 24 24"><path d="M12 4.83582L5.79291 11.0429L7.20712 12.4571L12 7.66424L16.7929 12.4571L18.2071 11.0429L12 4.83582ZM12 10.4857L5.79291 16.6928L7.20712 18.107L12 13.3141L16.7929 18.107L18.2071 16.6928L12 10.4857Z" fill="currentColor"/></svg>',
        'arrow-up': '<svg viewBox="0 0 24 24"><path d="M13.0001 7.82843V20H11.0001V7.82843L5.63614 13.1924L4.22192 11.7782L12.0001 4L19.7783 11.7782L18.3641 13.1924L13.0001 7.82843Z" fill="currentColor"/></svg>',
        'arrow-up-s': '<svg viewBox="0 0 24 24"><path d="M11.9999 10.8284L7.0502 15.7782L5.63599 14.364L11.9999 8L18.3639 14.364L16.9497 15.7782L11.9999 10.8284Z" fill="currentColor"/></svg>',
        'attachment-2': '<svg viewBox="0 0 24 24"><path d="M14.8287 7.75737L9.1718 13.4142C8.78127 13.8047 8.78127 14.4379 9.1718 14.8284C9.56232 15.219 10.1955 15.219 10.586 14.8284L16.2429 9.17158C17.4144 8.00001 17.4144 6.10052 16.2429 4.92894C15.0713 3.75737 13.1718 3.75737 12.0002 4.92894L6.34337 10.5858C4.39075 12.5384 4.39075 15.7042 6.34337 17.6569C8.29599 19.6095 11.4618 19.6095 13.4144 17.6569L19.0713 12L20.4855 13.4142L14.8287 19.0711C12.095 21.8047 7.66283 21.8047 4.92916 19.0711C2.19549 16.3374 2.19549 11.9053 4.92916 9.17158L10.586 3.51473C12.5386 1.56211 15.7045 1.56211 17.6571 3.51473C19.6097 5.46735 19.6097 8.63317 17.6571 10.5858L12.0002 16.2427C10.8287 17.4142 8.92916 17.4142 7.75759 16.2427C6.58601 15.0711 6.58601 13.1716 7.75759 12L13.4144 6.34316L14.8287 7.75737Z" fill="currentColor"/></svg>',
        'bar-chart-2': '<svg viewBox="0 0 24 24"><path d="M2 13H8V21H2V13ZM16 8H22V21H16V8ZM9 3H15V21H9V3ZM4 15V19H6V15H4ZM11 5V19H13V5H11ZM18 10V19H20V10H18Z" fill="currentColor"/></svg>',
        'bar-chart-box': '<svg viewBox="0 0 24 24"><path d="M3 3H21C21.5523 3 22 3.44772 22 4V20C22 20.5523 21.5523 21 21 21H3C2.44772 21 2 20.5523 2 20V4C2 3.44772 2.44772 3 3 3ZM4 5V19H20V5H4ZM7 13H9V17H7V13ZM11 7H13V17H11V7ZM15 10H17V17H15V10Z" fill="currentColor"/></svg>',
        'book': '<svg viewBox="0 0 24 24"><path d="M3 18.5V5C3 3.34315 4.34315 2 6 2H20C20.5523 2 21 2.44772 21 3V21C21 21.5523 20.5523 22 20 22H6.5C4.567 22 3 20.433 3 18.5ZM19 20V17H6.5C5.67157 17 5 17.6716 5 18.5C5 19.3284 5.67157 20 6.5 20H19ZM5 15.3368C5.45463 15.1208 5.9632 15 6.5 15H19V4H6C5.44772 4 5 4.44772 5 5V15.3368Z" fill="currentColor"/></svg>',
        'book-open': '<svg viewBox="0 0 24 24"><path d="M13 21V23H11V21H3C2.44772 21 2 20.5523 2 20V4C2 3.44772 2.44772 3 3 3H9C10.1947 3 11.2671 3.52375 12 4.35418C12.7329 3.52375 13.8053 3 15 3H21C21.5523 3 22 3.44772 22 4V20C22 20.5523 21.5523 21 21 21H13ZM20 19V5H15C13.8954 5 13 5.89543 13 7V19H20ZM11 19V7C11 5.89543 10.1046 5 9 5H4V19H11Z" fill="currentColor"/></svg>',
        'booklet': '<svg viewBox="0 0 24 24"><path d="M20.0049 2C21.1068 2 22 2.89821 22 3.9908V20.0092C22 21.1087 21.1074 22 20.0049 22H4V18H2V16H4V13H2V11H4V8H2V6H4V2H20.0049ZM8 4H6V20H8V4ZM20 4H10V20H20V4Z" fill="currentColor"/></svg>',
        'brain-ai-3': '<svg viewBox="0 0 24 24"><path d="M19.5 4.7832V7.6709L22 9.11426V14.8867L19.499 16.3311L19.5 19.2178L14.5 22.1045L12 20.6611L9.5 22.1045L4.5 19.2178V16.3311L2 14.8877L2.00098 9.11328L4.5 7.66992V4.78418L9.5 1.89746L11.999 3.34082L14.501 1.89648L19.5 4.7832ZM13 5.07227V7H11V5.07324L9.5 4.20703L6.49902 5.93848V8.8252L4 10.2676V13.7334L6.5 15.1768V18.0635L9.5 19.7959L11 18.9287V17H13V18.9297L14.5 19.7959L17.5 18.0625V15.1768L20 13.7324V10.2695L17.499 8.8252L17.5 5.9375L14.501 4.20605L13 5.07227ZM14.2646 13.1602C14.3529 12.9473 14.6472 12.9473 14.7354 13.1602L14.8623 13.4648C15.0783 13.986 15.4807 14.4027 15.9873 14.6279L16.3457 14.7871C16.5511 14.8784 16.5511 15.1773 16.3457 15.2686L15.9658 15.4375C15.4721 15.6571 15.0761 16.0586 14.8564 16.5625L14.7334 16.8447C14.6432 17.0517 14.3569 17.0517 14.2666 16.8447L14.1436 16.5625C13.9239 16.0586 13.5279 15.6571 13.0342 15.4375L12.6543 15.2686C12.4489 15.1773 12.4489 14.8784 12.6543 14.7871L13.0127 14.6279C13.5193 14.4027 13.9217 13.986 14.1377 13.4648L14.2646 13.1602ZM9.58789 7.7793C9.74239 7.40671 10.2577 7.4067 10.4121 7.7793L10.6338 8.31445C11.0118 9.22695 11.7161 9.95624 12.6025 10.3506L13.2305 10.6289C13.5899 10.7887 13.5897 11.3117 13.2305 11.4717L12.5654 11.7676C11.7013 12.152 11.0086 12.8548 10.624 13.7373L10.4082 14.2324C10.2504 14.5948 9.74973 14.5948 9.5918 14.2324L9.37598 13.7373C8.99143 12.8548 8.29875 12.152 7.43457 11.7676L6.76953 11.4717C6.41033 11.3117 6.41022 10.7887 6.76953 10.6289L7.39746 10.3506C8.2839 9.95624 8.98832 9.22697 9.36621 8.31445L9.58789 7.7793Z" fill="currentColor"/></svg>',
        'brain': '<svg viewBox="0 0 24 24"><path d="M9 4C10.1046 4 11 4.89543 11 6V12.8271C10.1058 12.1373 8.96602 11.7305 7.6644 11.5136L7.3356 13.4864C8.71622 13.7165 9.59743 14.1528 10.1402 14.7408C10.67 15.3147 11 16.167 11 17.5C11 18.8807 9.88071 20 8.5 20C7.11929 20 6 18.8807 6 17.5V17.1493C6.43007 17.2926 6.87634 17.4099 7.3356 17.4864L7.6644 15.5136C6.92149 15.3898 6.1752 15.1144 5.42909 14.7599C4.58157 14.3573 4 13.499 4 12.5C4 11.6653 4.20761 11.0085 4.55874 10.5257C4.90441 10.0504 5.4419 9.6703 6.24254 9.47014L7 9.28078V6C7 4.89543 7.89543 4 9 4ZM12 3.35418C11.2671 2.52376 10.1947 2 9 2C6.79086 2 5 3.79086 5 6V7.77422C4.14895 8.11644 3.45143 8.64785 2.94126 9.34933C2.29239 10.2415 2 11.3347 2 12.5C2 14.0652 2.79565 15.4367 4 16.2422V17.5C4 19.9853 6.01472 22 8.5 22C9.91363 22 11.175 21.3482 12 20.3287C12.825 21.3482 14.0864 22 15.5 22C17.9853 22 20 19.9853 20 17.5V16.2422C21.2044 15.4367 22 14.0652 22 12.5C22 11.3347 21.7076 10.2415 21.0587 9.34933C20.5486 8.64785 19.8511 8.11644 19 7.77422V6C19 3.79086 17.2091 2 15 2C13.8053 2 12.7329 2.52376 12 3.35418ZM18 17.1493V17.5C18 18.8807 16.8807 20 15.5 20C14.1193 20 13 18.8807 13 17.5C13 16.167 13.33 15.3147 13.8598 14.7408C14.4026 14.1528 15.2838 13.7165 16.6644 13.4864L16.3356 11.5136C15.034 11.7305 13.8942 12.1373 13 12.8271V6C13 4.89543 13.8954 4 15 4C16.1046 4 17 4.89543 17 6V9.28078L17.7575 9.47014C18.5581 9.6703 19.0956 10.0504 19.4413 10.5257C19.7924 11.0085 20 11.6653 20 12.5C20 13.499 19.4184 14.3573 18.5709 14.7599C17.8248 15.1144 17.0785 15.3898 16.3356 15.5136L16.6644 17.4864C17.1237 17.4099 17.5699 17.2926 18 17.1493Z" fill="currentColor"/></svg>',
        'briefcase': '<svg viewBox="0 0 24 24"><path d="M7 5V2C7 1.44772 7.44772 1 8 1H16C16.5523 1 17 1.44772 17 2V5H21C21.5523 5 22 5.44772 22 6V20C22 20.5523 21.5523 21 21 21H3C2.44772 21 2 20.5523 2 20V6C2 5.44772 2.44772 5 3 5H7ZM4 16V19H20V16H4ZM4 14H20V7H4V14ZM9 3V5H15V3H9ZM11 11H13V13H11V11Z" fill="currentColor"/></svg>',
        'bug': '<svg viewBox="0 0 24 24"><path d="M13 19.9C15.2822 19.4367 17 17.419 17 15V12C17 11.299 16.8564 10.6219 16.5846 10H7.41538C7.14358 10.6219 7 11.299 7 12V15C7 17.419 8.71776 19.4367 11 19.9V14H13V19.9ZM5.5358 17.6907C5.19061 16.8623 5 15.9534 5 15H2V13H5V12C5 11.3573 5.08661 10.7348 5.2488 10.1436L3.0359 8.86602L4.0359 7.13397L6.05636 8.30049C6.11995 8.19854 6.18609 8.09835 6.25469 8H17.7453C17.8139 8.09835 17.88 8.19854 17.9436 8.30049L19.9641 7.13397L20.9641 8.86602L18.7512 10.1436C18.9134 10.7348 19 11.3573 19 12V13H22V15H19C19 15.9534 18.8094 16.8623 18.4642 17.6907L20.9641 19.134L19.9641 20.866L17.4383 19.4077C16.1549 20.9893 14.1955 22 12 22C9.80453 22 7.84512 20.9893 6.56171 19.4077L4.0359 20.866L3.0359 19.134L5.5358 17.6907ZM8 6C8 3.79086 9.79086 2 12 2C14.2091 2 16 3.79086 16 6H8Z" fill="currentColor"/></svg>',
        'calendar': '<svg viewBox="0 0 24 24"><path d="M9 1V3H15V1H17V3H21C21.5523 3 22 3.44772 22 4V20C22 20.5523 21.5523 21 21 21H3C2.44772 21 2 20.5523 2 20V4C2 3.44772 2.44772 3 3 3H7V1H9ZM20 11H4V19H20V11ZM7 5H4V9H20V5H17V7H15V5H9V7H7V5Z" fill="currentColor"/></svg>',
        'calendar-schedule': '<svg viewBox="0 0 24 24"><path d="M7 3V1H9V3H15V1H17V3H21C21.5523 3 22 3.44772 22 4V9H20V5H17V7H15V5H9V7H7V5H4V19H10V21H3C2.44772 21 2 20.5523 2 20V4C2 3.44772 2.44772 3 3 3H7ZM17 12C14.7909 12 13 13.7909 13 16C13 18.2091 14.7909 20 17 20C19.2091 20 21 18.2091 21 16C21 13.7909 19.2091 12 17 12ZM11 16C11 12.6863 13.6863 10 17 10C20.3137 10 23 12.6863 23 16C23 19.3137 20.3137 22 17 22C13.6863 22 11 19.3137 11 16ZM16 13V16.4142L18.2929 18.7071L19.7071 17.2929L18 15.5858V13H16Z" fill="currentColor"/></svg>',
        'camera': '<svg viewBox="0 0 24 24"><path d="M9.82843 5L7.82843 7H4V19H20V7H16.1716L14.1716 5H9.82843ZM9 3H15L17 5H21C21.5523 5 22 5.44772 22 6V20C22 20.5523 21.5523 21 21 21H3C2.44772 21 2 20.5523 2 20V6C2 5.44772 2.44772 5 3 5H7L9 3ZM12 18C8.96243 18 6.5 15.5376 6.5 12.5C6.5 9.46243 8.96243 7 12 7C15.0376 7 17.5 9.46243 17.5 12.5C17.5 15.5376 15.0376 18 12 18ZM12 16C13.933 16 15.5 14.433 15.5 12.5C15.5 10.567 13.933 9 12 9C10.067 9 8.5 10.567 8.5 12.5C8.5 14.433 10.067 16 12 16Z" fill="currentColor"/></svg>',
        'chat-1': '<svg viewBox="0 0 24 24"><path d="M10 3H14C18.4183 3 22 6.58172 22 11C22 15.4183 18.4183 19 14 19V22.5C9 20.5 2 17.5 2 11C2 6.58172 5.58172 3 10 3ZM12 17H14C17.3137 17 20 14.3137 20 11C20 7.68629 17.3137 5 14 5H10C6.68629 5 4 7.68629 4 11C4 14.61 6.46208 16.9656 12 19.4798V17Z" fill="currentColor"/></svg>',
        'chat-3': '<svg viewBox="0 0 24 24"><path d="M7.29117 20.8242L2 22L3.17581 16.7088C2.42544 15.3056 2 13.7025 2 12C2 6.47715 6.47715 2 12 2C17.5228 2 22 6.47715 22 12C22 17.5228 17.5228 22 12 22C10.2975 22 8.6944 21.5746 7.29117 20.8242ZM7.58075 18.711L8.23428 19.0605C9.38248 19.6745 10.6655 20 12 20C16.4183 20 20 16.4183 20 12C20 7.58172 16.4183 4 12 4C7.58172 4 4 7.58172 4 12C4 13.3345 4.32549 14.6175 4.93949 15.7657L5.28896 16.4192L4.63416 19.3658L7.58075 18.711Z" fill="currentColor"/></svg>',
        'chat-4': '<svg viewBox="0 0 24 24"><path d="M5.76282 17H20V5H4V18.3851L5.76282 17ZM6.45455 19L2 22.5V4C2 3.44772 2.44772 3 3 3H21C21.5523 3 22 3.44772 22 4V18C22 18.5523 21.5523 19 21 19H6.45455Z" fill="currentColor"/></svg>',
        'chat-ai-3': '<svg viewBox="0 0 24 24"><path d="M12 1.99996C12.8632 1.99996 13.701 2.10973 14.5 2.31539L14 4.25192C13.3608 4.0874 12.6906 3.99997 12 3.99997C7.58174 3.99997 4.00002 7.58172 4 12C4 13.3344 4.3255 14.6174 4.93945 15.7656L5.28906 16.4189L4.63379 19.3662L7.58105 18.7109L8.23438 19.0605C9.38255 19.6745 10.6656 20 12 20C16.4183 20 20 16.4183 20 12C20 11.6771 19.9805 11.3587 19.9434 11.0459L21.9297 10.8095C21.976 11.1999 22 11.5972 22 12C22 17.5228 17.5228 22 12 22C10.2975 22 8.69425 21.5746 7.29102 20.8242L2 22L3.17578 16.709C2.42541 15.3057 2 13.7025 2 12C2.00002 6.47714 6.47717 1.99996 12 1.99996ZM19.5293 1.3193C19.7058 0.893513 20.2942 0.8935 20.4707 1.3193L20.7236 1.93063C21.1555 2.97343 21.9615 3.80614 22.9746 4.2568L23.6914 4.57614C24.1022 4.75882 24.1022 5.35635 23.6914 5.53903L22.9326 5.87692C21.945 6.3162 21.1534 7.11943 20.7139 8.1279L20.4668 8.69333C20.2863 9.10747 19.7136 9.10747 19.5332 8.69333L19.2861 8.1279C18.8466 7.11942 18.0551 6.3162 17.0674 5.87692L16.3076 5.53903C15.8974 5.35618 15.8974 4.75895 16.3076 4.57614L17.0254 4.2568C18.0384 3.80614 18.8445 2.97343 19.2764 1.93063L19.5293 1.3193Z" fill="currentColor"/></svg>',
        'chat-history': '<svg viewBox="0 0 24 24"><path d="M12 2C17.5228 2 22 6.47715 22 12C22 17.5228 17.5228 22 12 22C10.298 22 8.69525 21.5748 7.29229 20.8248L2 22L3.17629 16.7097C2.42562 15.3063 2 13.7028 2 12C2 6.47715 6.47715 2 12 2ZM12 4C7.58172 4 4 7.58172 4 12C4 13.3347 4.32563 14.6181 4.93987 15.7664L5.28952 16.4201L4.63445 19.3663L7.58189 18.7118L8.23518 19.061C9.38315 19.6747 10.6659 20 12 20C16.4183 20 20 16.4183 20 12C20 7.58172 16.4183 4 12 4ZM13 7V12H17V14H11V7H13Z" fill="currentColor"/></svg>',
        'chat-new': '<svg viewBox="0 0 24 24"><path d="M14 3V5H4V18.3851L5.76282 17H20V10H22V18C22 18.5523 21.5523 19 21 19H6.45455L2 22.5V4C2 3.44772 2.44772 3 3 3H14ZM19 3V0H21V3H24V5H21V8H19V5H16V3H19Z" fill="currentColor"/></svg>',
        'check': '<svg viewBox="0 0 24 24"><path d="M9.9997 15.1709L19.1921 5.97852L20.6063 7.39273L9.9997 17.9993L3.63574 11.6354L5.04996 10.2212L9.9997 15.1709Z" fill="currentColor"/></svg>',
        'checkbox-blank-circle-fill': '<svg viewBox="0 0 24 24"><path d="M12 22C17.5228 22 22 17.5228 22 12C22 6.47715 17.5228 2 12 2C6.47715 2 2 6.47715 2 12C2 17.5228 6.47715 22 12 22Z" fill="currentColor"/></svg>',
        'checkbox-blank': '<svg viewBox="0 0 24 24"><path d="M4 3H20C20.5523 3 21 3.44772 21 4V20C21 20.5523 20.5523 21 20 21H4C3.44772 21 3 20.5523 3 20V4C3 3.44772 3.44772 3 4 3ZM5 5V19H19V5H5Z" fill="currentColor"/></svg>',
        'checkbox-circle': '<svg viewBox="0 0 24 24"><path d="M4 12C4 7.58172 7.58172 4 12 4C16.4183 4 20 7.58172 20 12C20 16.4183 16.4183 20 12 20C7.58172 20 4 16.4183 4 12ZM12 2C6.47715 2 2 6.47715 2 12C2 17.5228 6.47715 22 12 22C17.5228 22 22 17.5228 22 12C22 6.47715 17.5228 2 12 2ZM17.4571 9.45711L16.0429 8.04289L11 13.0858L8.20711 10.2929L6.79289 11.7071L11 15.9142L17.4571 9.45711Z" fill="currentColor"/></svg>',
        'checkbox': '<svg viewBox="0 0 24 24"><path d="M4 3H20C20.5523 3 21 3.44772 21 4V20C21 20.5523 20.5523 21 20 21H4C3.44772 21 3 20.5523 3 20V4C3 3.44772 3.44772 3 4 3ZM5 5V19H19V5H5ZM11.0026 16L6.75999 11.7574L8.17421 10.3431L11.0026 13.1716L16.6595 7.51472L18.0737 8.92893L11.0026 16Z" fill="currentColor"/></svg>',
        'checkbox-multiple': '<svg viewBox="0 0 24 24"><path d="M6.99979 7V3C6.99979 2.44772 7.4475 2 7.99979 2H20.9998C21.5521 2 21.9998 2.44772 21.9998 3V16C21.9998 16.5523 21.5521 17 20.9998 17H17V20.9925C17 21.5489 16.551 22 15.9925 22H3.00728C2.45086 22 2 21.5511 2 20.9925L2.00276 8.00748C2.00288 7.45107 2.4518 7 3.01025 7H6.99979ZM8.99979 7H15.9927C16.549 7 17 7.44892 17 8.00748V15H19.9998V4H8.99979V7ZM15 9H4.00255L4.00021 20H15V9ZM8.50242 18L4.96689 14.4645L6.3811 13.0503L8.50242 15.1716L12.7451 10.9289L14.1593 12.3431L8.50242 18Z" fill="currentColor"/></svg>',
        'clipboard': '<svg viewBox="0 0 24 24"><path d="M7 4V2H17V4H20.0066C20.5552 4 21 4.44495 21 4.9934V21.0066C21 21.5552 20.5551 22 20.0066 22H3.9934C3.44476 22 3 21.5551 3 21.0066V4.9934C3 4.44476 3.44495 4 3.9934 4H7ZM7 6H5V20H19V6H17V8H7V6ZM9 4V6H15V4H9Z" fill="currentColor"/></svg>',
        'close-circle': '<svg viewBox="0 0 24 24"><path d="M12 22C6.47715 22 2 17.5228 2 12C2 6.47715 6.47715 2 12 2C17.5228 2 22 6.47715 22 12C22 17.5228 17.5228 22 12 22ZM12 20C16.4183 20 20 16.4183 20 12C20 7.58172 16.4183 4 12 4C7.58172 4 4 7.58172 4 12C4 16.4183 7.58172 20 12 20ZM12 10.5858L14.8284 7.75736L16.2426 9.17157L13.4142 12L16.2426 14.8284L14.8284 16.2426L12 13.4142L9.17157 16.2426L7.75736 14.8284L10.5858 12L7.75736 9.17157L9.17157 7.75736L12 10.5858Z" fill="currentColor"/></svg>',
        'close': '<svg viewBox="0 0 24 24"><path d="M11.9997 10.5865L16.9495 5.63672L18.3637 7.05093L13.4139 12.0007L18.3637 16.9504L16.9495 18.3646L11.9997 13.4149L7.04996 18.3646L5.63574 16.9504L10.5855 12.0007L5.63574 7.05093L7.04996 5.63672L11.9997 10.5865Z" fill="currentColor"/></svg>',
        'cloud': '<svg viewBox="0 0 24 24"><path d="M12 2C15.866 2 19 5.13401 19 9C19 9.11351 18.9973 9.22639 18.992 9.33857C21.3265 10.16 23 12.3846 23 15C23 18.3137 20.3137 21 17 21H7C3.68629 21 1 18.3137 1 15C1 12.3846 2.67346 10.16 5.00804 9.33857C5.0027 9.22639 5 9.11351 5 9C5 5.13401 8.13401 2 12 2ZM12 4C9.23858 4 7 6.23858 7 9C7 9.08147 7.00193 9.16263 7.00578 9.24344L7.07662 10.7309L5.67183 11.2252C4.0844 11.7837 3 13.2889 3 15C3 17.2091 4.79086 19 7 19H17C19.2091 19 21 17.2091 21 15C21 12.79 19.21 11 17 11C15.233 11 13.7337 12.1457 13.2042 13.7347L11.3064 13.1021C12.1005 10.7185 14.35 9 17 9C17 6.23858 14.7614 4 12 4Z" fill="currentColor"/></svg>',
        'cloud-off': '<svg viewBox="0 0 24 24"><path d="M3.51472 2.10051L22.6066 21.1924L21.1924 22.6066L19.1782 20.5924C18.503 20.8556 17.7684 21 17 21H7C3.68629 21 1 18.3137 1 15C1 12.3846 2.67346 10.16 5.00804 9.33857C5.0027 9.22639 5 9.11351 5 9C5 8.22228 5.12683 7.47418 5.36094 6.77527L2.10051 3.51472L3.51472 2.10051ZM7 9C7 9.08147 7.00193 9.16263 7.00578 9.24344L7.07662 10.7309L5.67183 11.2252C4.0844 11.7837 3 13.2889 3 15C3 17.2091 4.79086 19 7 19H17C17.1858 19 17.3687 18.9873 17.5478 18.9628L7.03043 8.44519C7.01032 8.62736 7 8.81247 7 9ZM12 2C15.866 2 19 5.13401 19 9C19 9.11351 18.9973 9.22639 18.992 9.33857C21.3265 10.16 23 12.3846 23 15C23 16.0883 22.7103 17.1089 22.2037 17.9889L20.7111 16.4955C20.8974 16.0335 21 15.5287 21 15C21 12.79 19.21 11 17 11C16.4711 11 15.9661 11.1027 15.5039 11.2892L14.0111 9.7964C14.8912 9.28978 15.9118 9 17 9C17 6.23858 14.7614 4 12 4C10.9295 4 9.93766 4.33639 9.12428 4.90922L7.69418 3.48056C8.88169 2.55284 10.3763 2 12 2Z" fill="currentColor"/></svg>',
        'code-ai': '<svg viewBox="0 0 24 24"><path d="M17.7134 10.1281L17.4668 10.6938C17.2864 11.1079 16.7136 11.1079 16.5331 10.6938L16.2866 10.1281C15.8471 9.11947 15.0555 8.31641 14.0677 7.87708L13.308 7.53922C12.8973 7.35653 12.8973 6.75881 13.308 6.57612L14.0252 6.25714C15.0384 5.80651 15.8442 4.97373 16.2761 3.93083L16.5293 3.31953C16.7058 2.89349 17.2942 2.89349 17.4706 3.31953L17.7238 3.93083C18.1558 4.97373 18.9616 5.80651 19.9748 6.25714L20.6919 6.57612C21.1027 6.75881 21.1027 7.35653 20.6919 7.53922L19.9323 7.87708C18.9445 8.31641 18.1529 9.11947 17.7134 10.1281ZM2.82843 12.0001L7.07107 16.2428L5.65685 17.657L0 12.0001L5.65685 6.34326L7.07107 7.75748L2.82843 12.0001ZM18.3429 17.6572L23.9998 12.0003L21.1714 9.17188L19.7571 10.5861L21.1714 12.0003L16.9287 16.2429L18.3429 17.6572Z" fill="currentColor"/></svg>',
        'code-box': '<svg viewBox="0 0 24 24"><path d="M3 3H21C21.5523 3 22 3.44772 22 4V20C22 20.5523 21.5523 21 21 21H3C2.44772 21 2 20.5523 2 20V4C2 3.44772 2.44772 3 3 3ZM4 5V19H20V5H4ZM20 12L16.4645 15.5355L15.0503 14.1213L17.1716 12L15.0503 9.87868L16.4645 8.46447L20 12ZM6.82843 12L8.94975 14.1213L7.53553 15.5355L4 12L7.53553 8.46447L8.94975 9.87868L6.82843 12ZM11.2443 17H9.11597L12.7557 7H14.884L11.2443 17Z" fill="currentColor"/></svg>',
        'code': '<svg viewBox="0 0 24 24"><path d="M23 12L15.9289 19.0711L14.5147 17.6569L20.1716 12L14.5147 6.34317L15.9289 4.92896L23 12ZM3.82843 12L9.48528 17.6569L8.07107 19.0711L1 12L8.07107 4.92896L9.48528 6.34317L3.82843 12Z" fill="currentColor"/></svg>',
        'code-sslash': '<svg viewBox="0 0 24 24"><path d="M24 12L18.3431 17.6569L16.9289 16.2426L21.1716 12L16.9289 7.75736L18.3431 6.34315L24 12ZM2.82843 12L7.07107 16.2426L5.65685 17.6569L0 12L5.65685 6.34315L7.07107 7.75736L2.82843 12ZM9.78845 21H7.66009L14.2116 3H16.3399L9.78845 21Z" fill="currentColor"/></svg>',
        'command': '<svg viewBox="0 0 24 24"><path d="M10 8H14V6.5C14 4.567 15.567 3 17.5 3C19.433 3 21 4.567 21 6.5C21 8.433 19.433 10 17.5 10H16V14H17.5C19.433 14 21 15.567 21 17.5C21 19.433 19.433 21 17.5 21C15.567 21 14 19.433 14 17.5V16H10V17.5C10 19.433 8.433 21 6.5 21C4.567 21 3 19.433 3 17.5C3 15.567 4.567 14 6.5 14H8V10H6.5C4.567 10 3 8.433 3 6.5C3 4.567 4.567 3 6.5 3C8.433 3 10 4.567 10 6.5V8ZM8 8V6.5C8 5.67157 7.32843 5 6.5 5C5.67157 5 5 5.67157 5 6.5C5 7.32843 5.67157 8 6.5 8H8ZM8 16H6.5C5.67157 16 5 16.6716 5 17.5C5 18.3284 5.67157 19 6.5 19C7.32843 19 8 18.3284 8 17.5V16ZM16 8H17.5C18.3284 8 19 7.32843 19 6.5C19 5.67157 18.3284 5 17.5 5C16.6716 5 16 5.67157 16 6.5V8ZM16 16V17.5C16 18.3284 16.6716 19 17.5 19C18.3284 19 19 18.3284 19 17.5C19 16.6716 18.3284 16 17.5 16H16ZM10 10V14H14V10H10Z" fill="currentColor"/></svg>',
        'computer': '<svg viewBox="0 0 24 24"><path d="M4 16H20V5H4V16ZM13 18V20H17V22H7V20H11V18H2.9918C2.44405 18 2 17.5511 2 16.9925V4.00748C2 3.45107 2.45531 3 2.9918 3H21.0082C21.556 3 22 3.44892 22 4.00748V16.9925C22 17.5489 21.5447 18 21.0082 18H13Z" fill="currentColor"/></svg>',
        'contract-up-down': '<svg viewBox="0 0 24 24"><path d="M5.79285 5.20718 12 11.4143 18.2071 5.20718 16.7928 3.79297 12 8.58586 7.20706 3.79297 5.79285 5.20718ZM18.2072 18.7928 12.0001 12.5857 5.793 18.7928 7.20721 20.207 12.0001 15.4141 16.793 20.207 18.2072 18.7928Z" fill="currentColor"/></svg>',
        'corner-down-left': '<svg viewBox="0 0 24 24"><path d="M19.0001 13.9999L19.0002 5L17.0002 4.99997L17.0001 11.9999L6.8283 12L10.778 8.05024L9.36382 6.63603L2.99986 13L9.36382 19.364L10.778 17.9497L6.82826 14L19.0001 13.9999Z" fill="currentColor"/></svg>',
        'cursor': '<svg viewBox="0 0 24 24"><path d="M15.3873 13.4975L17.9403 20.5117L13.2418 22.2218L10.6889 15.2076L6.79004 17.6529L8.4086 1.63318L19.9457 12.8646L15.3873 13.4975ZM15.3768 19.3163L12.6618 11.8568L15.6212 11.4459L9.98201 5.9561L9.19088 13.7863L11.7221 12.1988L14.4371 19.6583L15.3768 19.3163Z" fill="currentColor"/></svg>',
        'database-2': '<svg viewBox="0 0 24 24"><path d="M5 12.5C5 12.8134 5.46101 13.3584 6.53047 13.8931C7.91405 14.5849 9.87677 15 12 15C14.1232 15 16.0859 14.5849 17.4695 13.8931C18.539 13.3584 19 12.8134 19 12.5V10.3287C17.35 11.3482 14.8273 12 12 12C9.17273 12 6.64996 11.3482 5 10.3287V12.5ZM19 15.3287C17.35 16.3482 14.8273 17 12 17C9.17273 17 6.64996 16.3482 5 15.3287V17.5C5 17.8134 5.46101 18.3584 6.53047 18.8931C7.91405 19.5849 9.87677 20 12 20C14.1232 20 16.0859 19.5849 17.4695 18.8931C18.539 18.3584 19 17.8134 19 17.5V15.3287ZM3 17.5V7.5C3 5.01472 7.02944 3 12 3C16.9706 3 21 5.01472 21 7.5V17.5C21 19.9853 16.9706 22 12 22C7.02944 22 3 19.9853 3 17.5ZM12 10C14.1232 10 16.0859 9.58492 17.4695 8.89313C18.539 8.3584 19 7.81342 19 7.5C19 7.18658 18.539 6.6416 17.4695 6.10687C16.0859 5.41508 14.1232 5 12 5C9.87677 5 7.91405 5.41508 6.53047 6.10687C5.46101 6.6416 5 7.18658 5 7.5C5 7.81342 5.46101 8.3584 6.53047 8.89313C7.91405 9.58492 9.87677 10 12 10Z" fill="currentColor"/></svg>',
        'delete-bin': '<svg viewBox="0 0 24 24"><path d="M17 6H22V8H20V21C20 21.5523 19.5523 22 19 22H5C4.44772 22 4 21.5523 4 21V8H2V6H7V3C7 2.44772 7.44772 2 8 2H16C16.5523 2 17 2.44772 17 3V6ZM18 8H6V20H18V8ZM9 11H11V17H9V11ZM13 11H15V17H13V11ZM9 4V6H15V4H9Z" fill="currentColor"/></svg>',
        'discord-fill': '<svg viewBox="0 0 24 24"><path d="M19.3034 5.33716C17.9344 4.71103 16.4805 4.2547 14.9629 4C14.7719 4.32899 14.5596 4.77471 14.411 5.12492C12.7969 4.89144 11.1944 4.89144 9.60255 5.12492C9.45397 4.77471 9.2311 4.32899 9.05068 4C7.52251 4.2547 6.06861 4.71103 4.70915 5.33716C1.96053 9.39111 1.21766 13.3495 1.5891 17.2549C3.41443 18.5815 5.17612 19.388 6.90701 19.9187C7.33151 19.3456 7.71356 18.73 8.04255 18.0827C7.41641 17.8492 6.82211 17.5627 6.24904 17.2231C6.39762 17.117 6.5462 17.0003 6.68416 16.8835C10.1438 18.4648 13.8911 18.4648 17.3082 16.8835C17.4568 17.0003 17.5948 17.117 17.7434 17.2231C17.1703 17.5627 16.576 17.8492 15.9499 18.0827C16.2789 18.73 16.6609 19.3456 17.0854 19.9187C18.8152 19.388 20.5875 18.5815 22.4033 17.2549C22.8596 12.7341 21.6806 8.80747 19.3034 5.33716ZM8.5201 14.8459C7.48007 14.8459 6.63107 13.9014 6.63107 12.7447C6.63107 11.5879 7.45884 10.6434 8.5201 10.6434C9.57071 10.6434 10.4303 11.5879 10.4091 12.7447C10.4091 13.9014 9.57071 14.8459 8.5201 14.8459ZM15.4936 14.8459C14.4535 14.8459 13.6034 13.9014 13.6034 12.7447C13.6034 11.5879 14.4323 10.6434 15.4936 10.6434C16.5442 10.6434 17.4038 11.5879 17.3825 12.7447C17.3825 13.9014 16.5548 14.8459 15.4936 14.8459Z" fill="currentColor"/></svg>',
        'donut-chart-fill': '<svg viewBox="0 0 24 24"><path d="M10.9999 2.04938L11 5.07088C7.6077 5.55612 5 8.47352 5 12C5 15.866 8.13401 19 12 19C13.5723 19 15.0236 18.4816 16.1922 17.6064L18.3289 19.7428C16.605 21.1536 14.4014 22 12 22C6.47715 22 2 17.5228 2 12C2 6.81468 5.94662 2.55115 10.9999 2.04938ZM21.9506 13.0001C21.7509 15.0111 20.9555 16.8468 19.7433 18.3283L17.6064 16.1922C18.2926 15.2759 18.7595 14.1859 18.9291 13L21.9506 13.0001ZM13.0011 2.04948C17.725 2.51902 21.4815 6.27589 21.9506 10.9999L18.9291 10.9998C18.4905 7.93452 16.0661 5.50992 13.001 5.07103L13.0011 2.04948Z" fill="currentColor"/></svg>',
        'donut-chart': '<svg viewBox="0 0 24 24"><path d="M10.9999 2.04938L11 4.06188C7.05371 4.55396 4 7.92036 4 12C4 16.4183 7.58172 20 12 20C13.8487 20 15.5509 19.3729 16.9055 18.3199L18.3289 19.7428C16.605 21.1536 14.4014 22 12 22C6.47715 22 2 17.5228 2 12C2 6.81468 5.94662 2.55115 10.9999 2.04938ZM21.9506 13.0001C21.7509 15.0111 20.9555 16.8468 19.7433 18.3283L18.3199 16.9055C19.1801 15.799 19.756 14.4606 19.9381 12.9999L21.9506 13.0001ZM13.0011 2.04948C17.725 2.51902 21.4815 6.27589 21.9506 10.9999L19.9381 11C19.4869 7.38162 16.6192 4.51364 13.001 4.062L13.0011 2.04948Z" fill="currentColor"/></svg>',
        'download-cloud': '<svg viewBox="0 0 24 24"><path d="M1 14.5C1 12.1716 2.22429 10.1291 4.06426 8.9812C4.56469 5.044 7.92686 2 12 2C16.0731 2 19.4353 5.044 19.9357 8.9812C21.7757 10.1291 23 12.1716 23 14.5C23 17.9216 20.3562 20.7257 17 20.9811L7 21C3.64378 20.7257 1 17.9216 1 14.5ZM16.8483 18.9868C19.1817 18.8093 21 16.8561 21 14.5C21 12.927 20.1884 11.4962 18.8771 10.6781L18.0714 10.1754L17.9517 9.23338C17.5735 6.25803 15.0288 4 12 4C8.97116 4 6.42647 6.25803 6.0483 9.23338L5.92856 10.1754L5.12288 10.6781C3.81156 11.4962 3 12.927 3 14.5C3 16.8561 4.81833 18.8093 7.1517 18.9868L7.325 19H16.675L16.8483 18.9868ZM13 12H16L12 17L8 12H11V8H13V12Z" fill="currentColor"/></svg>',
        'download': '<svg viewBox="0 0 24 24"><path d="M3 19H21V21H3V19ZM13 13.1716L19.0711 7.1005L20.4853 8.51472L12 17L3.51472 8.51472L4.92893 7.1005L11 13.1716V2H13V13.1716Z" fill="currentColor"/></svg>',
        'drag-move-2': '<svg viewBox="0 0 24 24"><path d="M11 11V5.82843L9.17157 7.65685L7.75736 6.24264L12 2L16.2426 6.24264L14.8284 7.65685L13 5.82843V11H18.1716L16.3431 9.17157L17.7574 7.75736L22 12L17.7574 16.2426L16.3431 14.8284L18.1716 13H13V18.1716L14.8284 16.3431L16.2426 17.7574L12 22L7.75736 17.7574L9.17157 16.3431L11 18.1716V13H5.82843L7.65685 14.8284L6.24264 16.2426L2 12L6.24264 7.75736L7.65685 9.17157L5.82843 11H11Z" fill="currentColor"/></svg>',
        'draggable': '<svg viewBox="0 0 24 24"><path d="M8.5 7C9.32843 7 10 6.32843 10 5.5C10 4.67157 9.32843 4 8.5 4C7.67157 4 7 4.67157 7 5.5C7 6.32843 7.67157 7 8.5 7ZM8.5 13.5C9.32843 13.5 10 12.8284 10 12C10 11.1716 9.32843 10.5 8.5 10.5C7.67157 10.5 7 11.1716 7 12C7 12.8284 7.67157 13.5 8.5 13.5ZM10 18.5C10 19.3284 9.32843 20 8.5 20C7.67157 20 7 19.3284 7 18.5C7 17.6716 7.67157 17 8.5 17C9.32843 17 10 17.6716 10 18.5ZM15.5 7C16.3284 7 17 6.32843 17 5.5C17 4.67157 16.3284 4 15.5 4C14.6716 4 14 4.67157 14 5.5C14 6.32843 14.6716 7 15.5 7ZM17 12C17 12.8284 16.3284 13.5 15.5 13.5C14.6716 13.5 14 12.8284 14 12C14 11.1716 14.6716 10.5 15.5 10.5C16.3284 10.5 17 11.1716 17 12ZM15.5 20C16.3284 20 17 19.3284 17 18.5C17 17.6716 16.3284 17 15.5 17C14.6716 17 14 17.6716 14 18.5C14 19.3284 14.6716 20 15.5 20Z" fill="currentColor"/></svg>',
        'earth': '<svg viewBox="0 0 24 24"><path d="M6.23509 6.45329C4.85101 7.89148 4 9.84636 4 12C4 16.4183 7.58172 20 12 20C13.0808 20 14.1116 19.7857 15.0521 19.3972C15.1671 18.6467 14.9148 17.9266 14.8116 17.6746C14.582 17.115 13.8241 16.1582 12.5589 14.8308C12.2212 14.4758 12.2429 14.2035 12.3636 13.3943L12.3775 13.3029C12.4595 12.7486 12.5971 12.4209 14.4622 12.1248C15.4097 11.9746 15.6589 12.3533 16.0043 12.8777C16.0425 12.9358 16.0807 12.9928 16.1198 13.0499C16.4479 13.5297 16.691 13.6394 17.0582 13.8064C17.2227 13.881 17.428 13.9751 17.7031 14.1314C18.3551 14.504 18.3551 14.9247 18.3551 15.8472V15.9518C18.3551 16.3434 18.3168 16.6872 18.2566 16.9859C19.3478 15.6185 20 13.8854 20 12C20 8.70089 18.003 5.8682 15.1519 4.64482C14.5987 5.01813 13.8398 5.54726 13.575 5.91C13.4396 6.09538 13.2482 7.04166 12.6257 7.11976C12.4626 7.14023 12.2438 7.12589 12.012 7.11097C11.3905 7.07058 10.5402 7.01606 10.268 7.75495C10.0952 8.2232 10.0648 9.49445 10.6239 10.1543C10.7134 10.2597 10.7307 10.4547 10.6699 10.6735C10.59 10.9608 10.4286 11.1356 10.3783 11.1717C10.2819 11.1163 10.0896 10.8931 9.95938 10.7412C9.64554 10.3765 9.25405 9.92233 8.74797 9.78176C8.56395 9.73083 8.36166 9.68867 8.16548 9.64736C7.6164 9.53227 6.99443 9.40134 6.84992 9.09302C6.74442 8.8672 6.74488 8.55621 6.74529 8.22764C6.74529 7.8112 6.74529 7.34029 6.54129 6.88256C6.46246 6.70541 6.35689 6.56446 6.23509 6.45329ZM12 22C6.47715 22 2 17.5228 2 12C2 6.47715 6.47715 2 12 2C17.5228 2 22 6.47715 22 12C22 17.5228 17.5228 22 12 22Z" fill="currentColor"/></svg>',
        'edit-2': '<svg viewBox="0 0 24 24"><path d="M5 18.89H6.41421L15.7279 9.57627L14.3137 8.16206L5 17.4758V18.89ZM21 20.89H3V16.6473L16.435 3.21231C16.8256 2.82179 17.4587 2.82179 17.8492 3.21231L20.6777 6.04074C21.0682 6.43126 21.0682 7.06443 20.6777 7.45495L9.24264 18.89H21V20.89ZM15.7279 6.74785L17.1421 8.16206L18.5563 6.74785L17.1421 5.33363L15.7279 6.74785Z" fill="currentColor"/></svg>',
        'edit': '<svg viewBox="0 0 24 24"><path d="M6.41421 15.89L16.5563 5.74785L15.1421 4.33363L5 14.4758V15.89H6.41421ZM7.24264 17.89H3V13.6473L14.435 2.21231C14.8256 1.82179 15.4587 1.82179 15.8492 2.21231L18.6777 5.04074C19.0682 5.43126 19.0682 6.06443 18.6777 6.45495L7.24264 17.89ZM3 19.89H21V21.89H3V19.89Z" fill="currentColor"/></svg>',
        'emotion-happy': '<svg viewBox="0 0 24 24"><path d="M12 22C6.47715 22 2 17.5228 2 12C2 6.47715 6.47715 2 12 2C17.5228 2 22 6.47715 22 12C22 17.5228 17.5228 22 12 22ZM12 20C16.4183 20 20 16.4183 20 12C20 7.58172 16.4183 4 12 4C7.58172 4 4 7.58172 4 12C4 16.4183 7.58172 20 12 20ZM7 13H9C9 14.6569 10.3431 16 12 16C13.6569 16 15 14.6569 15 13H17C17 15.7614 14.7614 18 12 18C9.23858 18 7 15.7614 7 13ZM8 11C7.17157 11 6.5 10.3284 6.5 9.5C6.5 8.67157 7.17157 8 8 8C8.82843 8 9.5 8.67157 9.5 9.5C9.5 10.3284 8.82843 11 8 11ZM16 11C15.1716 11 14.5 10.3284 14.5 9.5C14.5 8.67157 15.1716 8 16 8C16.8284 8 17.5 8.67157 17.5 9.5C17.5 10.3284 16.8284 11 16 11Z" fill="currentColor"/></svg>',
        'equalizer-2': '<svg viewBox="0 0 24 24"><path d="M5 7C5 6.17157 5.67157 5.5 6.5 5.5C7.32843 5.5 8 6.17157 8 7C8 7.82843 7.32843 8.5 6.5 8.5C5.67157 8.5 5 7.82843 5 7ZM6.5 3.5C4.567 3.5 3 5.067 3 7C3 8.933 4.567 10.5 6.5 10.5C8.433 10.5 10 8.933 10 7C10 5.067 8.433 3.5 6.5 3.5ZM12 8H20V6H12V8ZM16 17C16 16.1716 16.6716 15.5 17.5 15.5C18.3284 15.5 19 16.1716 19 17C19 17.8284 18.3284 18.5 17.5 18.5C16.6716 18.5 16 17.8284 16 17ZM17.5 13.5C15.567 13.5 14 15.067 14 17C14 18.933 15.567 20.5 17.5 20.5C19.433 20.5 21 18.933 21 17C21 15.067 19.433 13.5 17.5 13.5ZM4 16V18H12V16H4Z" fill="currentColor"/></svg>',
        'error-warning': '<svg viewBox="0 0 24 24"><path d="M12 22C6.47715 22 2 17.5228 2 12C2 6.47715 6.47715 2 12 2C17.5228 2 22 6.47715 22 12C22 17.5228 17.5228 22 12 22ZM12 20C16.4183 20 20 16.4183 20 12C20 7.58172 16.4183 4 12 4C7.58172 4 4 7.58172 4 12C4 16.4183 7.58172 20 12 20ZM11 15H13V17H11V15ZM11 7H13V13H11V7Z" fill="currentColor"/></svg>',
        'expand-up-down': '<svg viewBox="0 0 24 24"><path d="M18.2072 9.0428 12.0001 2.83569 5.793 9.0428 7.20721 10.457 12.0001 5.66412 16.793 10.457 18.2072 9.0428ZM5.79285 14.9572 12 21.1643 18.2071 14.9572 16.7928 13.543 12 18.3359 7.20706 13.543 5.79285 14.9572Z" fill="currentColor"/></svg>',
        'external-link': '<svg viewBox="0 0 24 24"><path d="M10 6V8H5V19H16V14H18V20C18 20.5523 17.5523 21 17 21H4C3.44772 21 3 20.5523 3 20V7C3 6.44772 3.44772 6 4 6H10ZM21 3V11H19L18.9999 6.413L11.2071 14.2071L9.79289 12.7929L17.5849 5H13V3H21Z" fill="currentColor"/></svg>',
        'eye': '<svg viewBox="0 0 24 24"><path d="M12.0003 3C17.3924 3 21.8784 6.87976 22.8189 12C21.8784 17.1202 17.3924 21 12.0003 21C6.60812 21 2.12215 17.1202 1.18164 12C2.12215 6.87976 6.60812 3 12.0003 3ZM12.0003 19C16.2359 19 19.8603 16.052 20.7777 12C19.8603 7.94803 16.2359 5 12.0003 5C7.7646 5 4.14022 7.94803 3.22278 12C4.14022 16.052 7.7646 19 12.0003 19ZM12.0003 16.5C9.51498 16.5 7.50026 14.4853 7.50026 12C7.50026 9.51472 9.51498 7.5 12.0003 7.5C14.4855 7.5 16.5003 9.51472 16.5003 12C16.5003 14.4853 14.4855 16.5 12.0003 16.5ZM12.0003 14.5C13.381 14.5 14.5003 13.3807 14.5003 12C14.5003 10.6193 13.381 9.5 12.0003 9.5C10.6196 9.5 9.50026 10.6193 9.50026 12C9.50026 13.3807 10.6196 14.5 12.0003 14.5Z" fill="currentColor"/></svg>',
        'eye-off': '<svg viewBox="0 0 24 24"><path d="M17.8827 19.2968C16.1814 20.3755 14.1638 21.0002 12.0003 21.0002C6.60812 21.0002 2.12215 17.1204 1.18164 12.0002C1.61832 9.62282 2.81932 7.5129 4.52047 5.93457L1.39366 2.80777L2.80788 1.39355L22.6069 21.1925L21.1927 22.6068L17.8827 19.2968ZM5.9356 7.3497C4.60673 8.56015 3.6378 10.1672 3.22278 12.0002C4.14022 16.0521 7.7646 19.0002 12.0003 19.0002C13.5997 19.0002 15.112 18.5798 16.4243 17.8384L14.396 15.8101C13.7023 16.2472 12.8808 16.5002 12.0003 16.5002C9.51498 16.5002 7.50026 14.4854 7.50026 12.0002C7.50026 11.1196 7.75317 10.2981 8.19031 9.60442L5.9356 7.3497ZM12.9139 14.328L9.67246 11.0866C9.5613 11.3696 9.50026 11.6777 9.50026 12.0002C9.50026 13.3809 10.6196 14.5002 12.0003 14.5002C12.3227 14.5002 12.6309 14.4391 12.9139 14.328ZM20.8068 16.5925L19.376 15.1617C20.0319 14.2268 20.5154 13.1586 20.7777 12.0002C19.8603 7.94818 16.2359 5.00016 12.0003 5.00016C11.1544 5.00016 10.3329 5.11773 9.55249 5.33818L7.97446 3.76015C9.22127 3.26959 10.5793 3.00016 12.0003 3.00016C17.3924 3.00016 21.8784 6.87992 22.8189 12.0002C22.5067 13.6998 21.8038 15.2628 20.8068 16.5925ZM11.7229 7.50857C11.8146 7.50299 11.9071 7.50016 12.0003 7.50016C14.4855 7.50016 16.5003 9.51488 16.5003 12.0002C16.5003 12.0933 16.4974 12.1858 16.4919 12.2775L11.7229 7.50857Z" fill="currentColor"/></svg>',
        'file-add': '<svg viewBox="0 0 24 24"><path d="M15 4H5V20H19V8H15V4ZM3 2.9918C3 2.44405 3.44749 2 3.9985 2H16L20.9997 7L21 20.9925C21 21.5489 20.5551 22 20.0066 22H3.9934C3.44476 22 3 21.5447 3 21.0082V2.9918ZM11 11V8H13V11H16V13H13V16H11V13H8V11H11Z" fill="currentColor"/></svg>',
        'file-check-fill': '<svg viewBox="0 0 24 24"><path d="M20.9998 7L16 2H3.9985C3.44749 2 3 2.44405 3 2.9918V21.0082C3 21.5447 3.44476 22 3.9934 22H12.3414C12.1203 21.3744 12 20.7013 12 20C12 16.6863 14.6863 14 18 14C19.0928 14 20.1174 14.2922 20.9999 14.8026L20.9998 7ZM14.4646 19.4647L18.0001 23.0002L22.9498 18.0505L21.5356 16.6362L18.0001 20.1718L15.8788 18.0505L14.4646 19.4647Z" fill="currentColor"/></svg>',
        'file-check': '<svg viewBox="0 0 24 24"><path d="M12 20V22H3.9934C3.44476 22 3 21.5447 3 21.0082V2.9918C3 2.44405 3.44749 2 3.9985 2H16L20.9998 7V14H19V8H15V4H5V20H12ZM14.4646 19.4647L18.0001 23.0002L22.9498 18.0505L21.5356 16.6362L18.0001 20.1718L15.8788 18.0505L14.4646 19.4647Z" fill="currentColor"/></svg>',
        'file-code': '<svg viewBox="0 0 24 24"><path d="M15 4H5V20H19V8H15V4ZM3 2.9918C3 2.44405 3.44749 2 3.9985 2H16L20.9997 7L21 20.9925C21 21.5489 20.5551 22 20.0066 22H3.9934C3.44476 22 3 21.5447 3 21.0082V2.9918ZM17.6569 12L14.1213 15.5355L12.7071 14.1213L14.8284 12L12.7071 9.87868L14.1213 8.46447L17.6569 12ZM6.34315 12L9.87868 8.46447L11.2929 9.87868L9.17157 12L11.2929 14.1213L9.87868 15.5355L6.34315 12Z" fill="currentColor"/></svg>',
        'file-copy-2': '<svg viewBox="0 0 24 24"><path d="M6.9998 6V3C6.9998 2.44772 7.44752 2 7.9998 2H19.9998C20.5521 2 20.9998 2.44772 20.9998 3V17C20.9998 17.5523 20.5521 18 19.9998 18H16.9998V20.9991C16.9998 21.5519 16.5499 22 15.993 22H4.00666C3.45059 22 3 21.5554 3 20.9991L3.0026 7.00087C3.0027 6.44811 3.45264 6 4.00942 6H6.9998ZM5.00242 8L5.00019 20H14.9998V8H5.00242ZM8.9998 6H16.9998V16H18.9998V4H8.9998V6ZM7 11H13V13H7V11ZM7 15H13V17H7V15Z" fill="currentColor"/></svg>',
        'file-copy': '<svg viewBox="0 0 24 24"><path d="M6.9998 6V3C6.9998 2.44772 7.44752 2 7.9998 2H19.9998C20.5521 2 20.9998 2.44772 20.9998 3V17C20.9998 17.5523 20.5521 18 19.9998 18H16.9998V20.9991C16.9998 21.5519 16.5499 22 15.993 22H4.00666C3.45059 22 3 21.5554 3 20.9991L3.0026 7.00087C3.0027 6.44811 3.45264 6 4.00942 6H6.9998ZM5.00242 8L5.00019 20H14.9998V8H5.00242ZM8.9998 6H16.9998V16H18.9998V4H8.9998V6Z" fill="currentColor"/></svg>',
        'file-edit': '<svg viewBox="0 0 24 24"><path d="M21 6.75736L19 8.75736V4H10V9H5V20H19V17.2426L21 15.2426V21.0082C21 21.556 20.5551 22 20.0066 22H3.9934C3.44476 22 3 21.5501 3 20.9932V8L9.00319 2H19.9978C20.5513 2 21 2.45531 21 2.9918V6.75736ZM21.7782 8.80761L23.1924 10.2218L15.4142 18L13.9979 17.9979L14 16.5858L21.7782 8.80761Z" fill="currentColor"/></svg>',
        'file-image': '<svg viewBox="0 0 24 24"><path d="M15 8V4H5V20H19V8H15ZM3 2.9918C3 2.44405 3.44749 2 3.9985 2H16L20.9997 7L21 20.9925C21 21.5489 20.5551 22 20.0066 22H3.9934C3.44476 22 3 21.5447 3 21.0082V2.9918ZM11 9.5C11 10.3284 10.3284 11 9.5 11C8.67157 11 8 10.3284 8 9.5C8 8.67157 8.67157 8 9.5 8C10.3284 8 11 8.67157 11 9.5ZM17.5 17L13.5 10L8 17H17.5Z" fill="currentColor"/></svg>',
        'file': '<svg viewBox="0 0 24 24"><path d="M9 2.00318V2H19.9978C20.5513 2 21 2.45531 21 2.9918V21.0082C21 21.556 20.5551 22 20.0066 22H3.9934C3.44476 22 3 21.5501 3 20.9932V8L9 2.00318ZM5.82918 8H9V4.83086L5.82918 8ZM11 4V9C11 9.55228 10.5523 10 10 10H5V20H19V4H11Z" fill="currentColor"/></svg>',
        'file-list-2': '<svg viewBox="0 0 24 24"><path d="M20 22H4C3.44772 22 3 21.5523 3 21V3C3 2.44772 3.44772 2 4 2H20C20.5523 2 21 2.44772 21 3V21C21 21.5523 20.5523 22 20 22ZM19 20V4H5V20H19ZM8 7H16V9H8V7ZM8 11H16V13H8V11ZM8 15H13V17H8V15Z" fill="currentColor"/></svg>',
        'file-music': '<svg viewBox="0 0 24 24"><path d="M16 8V10H13V14.5C13 15.8807 11.8807 17 10.5 17C9.11929 17 8 15.8807 8 14.5C8 13.1193 9.11929 12 10.5 12C10.6712 12 10.8384 12.0172 11 12.05V8H15V4H5V20H19V8H16ZM3 2.9918C3 2.44405 3.44749 2 3.9985 2H16L20.9997 7L21 20.9925C21 21.5489 20.5551 22 20.0066 22H3.9934C3.44476 22 3 21.5447 3 21.0082V2.9918Z" fill="currentColor"/></svg>',
        'file-pdf': '<svg viewBox="0 0 24 24"><path d="M12 16H8V8H12C14.2091 8 16 9.79086 16 12C16 14.2091 14.2091 16 12 16ZM10 10V14H12C13.1046 14 14 13.1046 14 12C14 10.8954 13.1046 10 12 10H10ZM15 4H5V20H19V8H15V4ZM3 2.9918C3 2.44405 3.44749 2 3.9985 2H16L20.9997 7L21 20.9925C21 21.5489 20.5551 22 20.0066 22H3.9934C3.44476 22 3 21.5447 3 21.0082V2.9918Z" fill="currentColor"/></svg>',
        'file-search': '<svg viewBox="0 0 24 24"><path d="M15 4H5V20H19V8H15V4ZM3 2.9918C3 2.44405 3.44749 2 3.9985 2H16L20.9997 7L21 20.9925C21 21.5489 20.5551 22 20.0066 22H3.9934C3.44476 22 3 21.5447 3 21.0082V2.9918ZM13.529 14.4464C11.9951 15.3524 9.98633 15.1464 8.66839 13.8284C7.1063 12.2663 7.1063 9.73367 8.66839 8.17157C10.2305 6.60948 12.7631 6.60948 14.3252 8.17157C15.6432 9.48951 15.8492 11.4983 14.9432 13.0322L17.1537 15.2426L15.7395 16.6569L13.529 14.4464ZM12.911 12.4142C13.6921 11.6332 13.6921 10.3668 12.911 9.58579C12.13 8.80474 10.8637 8.80474 10.0826 9.58579C9.30156 10.3668 9.30156 11.6332 10.0826 12.4142C10.8637 13.1953 12.13 13.1953 12.911 12.4142Z" fill="currentColor"/></svg>',
        'file-text': '<svg viewBox="0 0 24 24"><path d="M21 8V20.9932C21 21.5501 20.5552 22 20.0066 22H3.9934C3.44495 22 3 21.556 3 21.0082V2.9918C3 2.45531 3.4487 2 4.00221 2H14.9968L21 8ZM19 9H14V4H5V20H19V9ZM8 7H11V9H8V7ZM8 11H16V13H8V11ZM8 15H16V17H8V15Z" fill="currentColor"/></svg>',
        'file-transfer': '<svg viewBox="0 0 24 24"><path d="M15 4H5V20H19V8H15V4ZM3 2.9918C3 2.44405 3.44749 2 3.9985 2H16L20.9997 7L21 20.9925C21 21.5489 20.5551 22 20.0066 22H3.9934C3.44476 22 3 21.5447 3 21.0082V2.9918ZM12 11V8L16 12L12 16V13H8V11H12Z" fill="currentColor"/></svg>',
        'file-video': '<svg viewBox="0 0 24 24"><path d="M15 4V8H19V20H5V4H15ZM3.9985 2C3.44749 2 3 2.44405 3 2.9918V21.0082C3 21.5447 3.44476 22 3.9934 22H20.0066C20.5551 22 21 21.5489 21 20.9925L20.9997 7L16 2H3.9985ZM15.0008 11.667L10.1219 8.41435C10.0562 8.37054 9.979 8.34717 9.9 8.34717C9.6791 8.34717 9.5 8.52625 9.5 8.74717V15.2524C9.5 15.3314 9.5234 15.4086 9.5672 15.4743C9.6897 15.6581 9.9381 15.7078 10.1219 15.5852L15.0008 12.3326C15.0447 12.3033 15.0824 12.2656 15.1117 12.2217C15.2343 12.0379 15.1846 11.7895 15.0008 11.667Z" fill="currentColor"/></svg>',
        'flashlight': '<svg viewBox="0 0 24 24"><path d="M13 9H21L11 24V15H4L13 0V9ZM11 11V7.22063L7.53238 13H13V17.3944L17.263 11H11Z" fill="currentColor"/></svg>',
        'flask': '<svg viewBox="0 0 24 24"><path d="M15.9994 2V4H14.9994V7.24291C14.9994 8.40051 15.2506 9.54432 15.7357 10.5954L20.017 19.8714C20.3641 20.6236 20.0358 21.5148 19.2836 21.8619C19.0865 21.9529 18.8721 22 18.655 22H5.34375C4.51532 22 3.84375 21.3284 3.84375 20.5C3.84375 20.2829 3.89085 20.0685 3.98181 19.8714L8.26306 10.5954C8.74816 9.54432 8.99939 8.40051 8.99939 7.24291V4H7.99939V2H15.9994ZM13.3873 10.0012H10.6115C10.5072 10.3644 10.3823 10.7221 10.2371 11.0724L10.079 11.4335L6.12439 20H17.8734L13.9198 11.4335C13.7054 10.9691 13.5276 10.4902 13.3873 10.0012ZM10.9994 7.24291C10.9994 7.49626 10.9898 7.7491 10.9706 8.00087H13.0282C13.0189 7.87982 13.0119 7.75852 13.0072 7.63704L12.9994 7.24291V4H10.9994V7.24291Z" fill="currentColor"/></svg>',
        'folder-3-fill': '<svg viewBox="0 0 24 24"><path d="M22 8V20C22 20.5523 21.5523 21 21 21H3C2.44772 21 2 20.5523 2 20V7H21C21.5523 7 22 7.44772 22 8ZM12.4142 5H2V4C2 3.44772 2.44772 3 3 3H10.4142L12.4142 5Z" fill="currentColor"/></svg>',
        'folder-3': '<svg viewBox="0 0 24 24"><path d="M12.4142 5H21C21.5523 5 22 5.44772 22 6V20C22 20.5523 21.5523 21 21 21H3C2.44772 21 2 20.5523 2 20V4C2 3.44772 2.44772 3 3 3H10.4142L12.4142 5ZM4 7V19H20V7H4Z" fill="currentColor"/></svg>',
        'folder-6': '<svg viewBox="0 0 24 24"><path d="M2 4C2 3.44772 2.44772 3 3 3H10.4142L12.4142 5H21C21.5523 5 22 5.44772 22 6V20C22 20.5523 21.5523 21 21 21L3 21C2.45 21 2 20.55 2 20V4ZM10.5858 6L9.58579 5H4V7H9.58579L10.5858 6ZM4 9V19L20 19V7H12.4142L10.4142 9H4Z" fill="currentColor"/></svg>',
        'folder-add': '<svg viewBox="0 0 24 24"><path d="M12.4142 5H21C21.5523 5 22 5.44772 22 6V20C22 20.5523 21.5523 21 21 21H3C2.44772 21 2 20.5523 2 20V4C2 3.44772 2.44772 3 3 3H10.4142L12.4142 5ZM4 5V19H20V7H11.5858L9.58579 5H4ZM11 12V9H13V12H16V14H13V17H11V14H8V12H11Z" fill="currentColor"/></svg>',
        'folder': '<svg viewBox="0 0 24 24"><path d="M4 5V19H20V7H11.5858L9.58579 5H4ZM12.4142 5H21C21.5523 5 22 5.44772 22 6V20C22 20.5523 21.5523 21 21 21H3C2.44772 21 2 20.5523 2 20V4C2 3.44772 2.44772 3 3 3H10.4142L12.4142 5Z" fill="currentColor"/></svg>',
        'folder-open-fill': '<svg viewBox="0 0 24 24"><path d="M3 21C2.44772 21 2 20.5523 2 20V4C2 3.44772 2.44772 3 3 3H10.4142L12.4142 5H20C20.5523 5 21 5.44772 21 6V9H4V18.996L6 11H22.5L20.1894 20.2425C20.0781 20.6877 19.6781 21 19.2192 21H3Z" fill="currentColor"/></svg>',
        'folder-open': '<svg viewBox="0 0 24 24"><path d="M3 21C2.44772 21 2 20.5523 2 20V4C2 3.44772 2.44772 3 3 3H10.4142L12.4142 5H20C20.5523 5 21 5.44772 21 6V9H19V7H11.5858L9.58579 5H4V16.998L5.5 11H22.5L20.1894 20.2425C20.0781 20.6877 19.6781 21 19.2192 21H3ZM19.9384 13H7.06155L5.56155 19H18.4384L19.9384 13Z" fill="currentColor"/></svg>',
        'folder-received': '<svg viewBox="0 0 24 24"><path d="M22 13H20V7H11.5858L9.58579 5H4V19H13V21H3C2.44772 21 2 20.5523 2 20V4C2 3.44772 2.44772 3 3 3H10.4142L12.4142 5H21C21.5523 5 22 5.44772 22 6V13ZM20 17H23V19H20V22.5L15 18L20 13.5V17Z" fill="currentColor"/></svg>',
        'folders': '<svg viewBox="0 0 24 24"><path d="M6 7V4C6 3.44772 6.44772 3 7 3H13.4142L15.4142 5H21C21.5523 5 22 5.44772 22 6V16C22 16.5523 21.5523 17 21 17H18V20C18 20.5523 17.5523 21 17 21H3C2.44772 21 2 20.5523 2 20V8C2 7.44772 2.44772 7 3 7H6ZM6 9H4V19H16V17H6V9ZM8 5V15H20V7H14.5858L12.5858 5H8Z" fill="currentColor"/></svg>',
        'fullscreen-exit': '<svg viewBox="0 0 24 24"><path d="M18 7H22V9H16V3H18V7ZM8 9H2V7H6V3H8V9ZM18 17V21H16V15H22V17H18ZM8 15V21H6V17H2V15H8Z" fill="currentColor"/></svg>',
        'fullscreen': '<svg viewBox="0 0 24 24"><path d="M8 3V5H4V9H2V3H8ZM2 21V15H4V19H8V21H2ZM22 21H16V19H20V15H22V21ZM22 9H20V5H16V3H22V9Z" fill="currentColor"/></svg>',
        'gamepad': '<svg viewBox="0 0 24 24"><path d="M17 4C20.3137 4 23 6.68629 23 10V14C23 17.3137 20.3137 20 17 20H7C3.68629 20 1 17.3137 1 14V10C1 6.68629 3.68629 4 7 4H17ZM17 6H7C4.8578 6 3.10892 7.68397 3.0049 9.80036L3 10V14C3 16.1422 4.68397 17.8911 6.80036 17.9951L7 18H17C19.1422 18 20.8911 16.316 20.9951 14.1996L21 14V10C21 7.8578 19.316 6.10892 17.1996 6.0049L17 6ZM10 9V11H12V13H9.999L10 15H8L7.999 13H6V11H8V9H10ZM18 13V15H16V13H18ZM16 9V11H14V9H16Z" fill="currentColor"/></svg>',
        'git-branch': '<svg viewBox="0 0 24 24"><path d="M7.10508 15.2101C8.21506 15.6501 9 16.7334 9 18C9 19.6569 7.65685 21 6 21C4.34315 21 3 19.6569 3 18C3 16.6938 3.83481 15.5825 5 15.1707V8.82929C3.83481 8.41746 3 7.30622 3 6C3 4.34315 4.34315 3 6 3C7.65685 3 9 4.34315 9 6C9 7.30622 8.16519 8.41746 7 8.82929V11.9996C7.83566 11.3719 8.87439 11 10 11H14C15.3835 11 16.5482 10.0635 16.8949 8.78991C15.7849 8.34988 15 7.26661 15 6C15 4.34315 16.3431 3 18 3C19.6569 3 21 4.34315 21 6C21 7.3332 20.1303 8.46329 18.9274 8.85392C18.5222 11.2085 16.4703 13 14 13H10C8.61653 13 7.45179 13.9365 7.10508 15.2101ZM6 17C5.44772 17 5 17.4477 5 18C5 18.5523 5.44772 19 6 19C6.55228 19 7 18.5523 7 18C7 17.4477 6.55228 17 6 17ZM6 5C5.44772 5 5 5.44772 5 6C5 6.55228 5.44772 7 6 7C6.55228 7 7 6.55228 7 6C7 5.44772 6.55228 5 6 5ZM18 5C17.4477 5 17 5.44772 17 6C17 6.55228 17.4477 7 18 7C18.5523 7 19 6.55228 19 6C19 5.44772 18.5523 5 18 5Z" fill="currentColor"/></svg>',
        'git-close-pull-request': '<svg viewBox="0 0 24 24"><path d="M6 5C5.44772 5 5 5.44772 5 6C5 6.55228 5.44772 7 6 7C6.55228 7 7 6.55228 7 6C7 5.44772 6.55228 5 6 5ZM3 6C3 4.34315 4.34315 3 6 3C7.65685 3 9 4.34315 9 6C9 7.30622 8.16519 8.41746 7 8.82929V15.1707C8.16519 15.5825 9 16.6938 9 18C9 19.6569 7.65685 21 6 21C4.34315 21 3 19.6569 3 18C3 16.6938 3.83481 15.5825 5 15.1707V8.82929C3.83481 8.41746 3 7.30622 3 6ZM15.2929 3.29289C15.6834 2.90237 16.3166 2.90237 16.7071 3.29289L18 4.58579L19.2929 3.29289C19.6834 2.90237 20.3166 2.90237 20.7071 3.29289C21.0976 3.68342 21.0976 4.31658 20.7071 4.70711L19.4142 6L20.7071 7.29289C21.0976 7.68342 21.0976 8.31658 20.7071 8.70711C20.3166 9.09763 19.6834 9.09763 19.2929 8.70711L18 7.41421L16.7071 8.70711C16.3166 9.09763 15.6834 9.09763 15.2929 8.70711C14.9024 8.31658 14.9024 7.68342 15.2929 7.29289L16.5858 6L15.2929 4.70711C14.9024 4.31658 14.9024 3.68342 15.2929 3.29289ZM18 10C18.5523 10 19 10.4477 19 11V15.1707C20.1652 15.5825 21 16.6938 21 18C21 19.6569 19.6569 21 18 21C16.3431 21 15 19.6569 15 18C15 16.6938 15.8348 15.5825 17 15.1707V11C17 10.4477 17.4477 10 18 10ZM6 17C5.44772 17 5 17.4477 5 18C5 18.5523 5.44772 19 6 19C6.55228 19 7 18.5523 7 18C7 17.4477 6.55228 17 6 17ZM18 17C17.4477 17 17 17.4477 17 18C17 18.5523 17.4477 19 18 19C18.5523 19 19 18.5523 19 18C19 17.4477 18.5523 17 18 17Z" fill="currentColor"/></svg>',
        'git-commit': '<svg viewBox="0 0 24 24"><path d="M15.874 13C15.4299 14.7252 13.8638 16 12 16C10.1362 16 8.57006 14.7252 8.12602 13H3V11H8.12602C8.57006 9.27477 10.1362 8 12 8C13.8638 8 15.4299 9.27477 15.874 11H21V13H15.874ZM12 14C13.1046 14 14 13.1046 14 12C14 10.8954 13.1046 10 12 10C10.8954 10 10 10.8954 10 12C10 13.1046 10.8954 14 12 14Z" fill="currentColor"/></svg>',
        'git-merge': '<svg viewBox="0 0 24 24"><path d="M7.10508 8.78991C7.45179 10.0635 8.61653 11 10 11H14C16.4703 11 18.5222 12.7915 18.9274 15.1461C20.1303 15.5367 21 16.6668 21 18C21 19.6569 19.6569 21 18 21C16.3431 21 15 19.6569 15 18C15 16.7334 15.7849 15.6501 16.8949 15.2101C16.5482 13.9365 15.3835 13 14 13H10C8.87439 13 7.83566 12.6281 7 12.0004V15.1707C8.16519 15.5825 9 16.6938 9 18C9 19.6569 7.65685 21 6 21C4.34315 21 3 19.6569 3 18C3 16.6938 3.83481 15.5825 5 15.1707V8.82929C3.83481 8.41746 3 7.30622 3 6C3 4.34315 4.34315 3 6 3C7.65685 3 9 4.34315 9 6C9 7.26661 8.21506 8.34988 7.10508 8.78991ZM6 7C6.55228 7 7 6.55228 7 6C7 5.44772 6.55228 5 6 5C5.44772 5 5 5.44772 5 6C5 6.55228 5.44772 7 6 7ZM6 19C6.55228 19 7 18.5523 7 18C7 17.4477 6.55228 17 6 17C5.44772 17 5 17.4477 5 18C5 18.5523 5.44772 19 6 19ZM18 19C18.5523 19 19 18.5523 19 18C19 17.4477 18.5523 17 18 17C17.4477 17 17 17.4477 17 18C17 18.5523 17.4477 19 18 19Z" fill="currentColor"/></svg>',
        'git-pr-draft': '<svg viewBox="0 0 24 24"><path d="M5 6C5 5.44772 5.44772 5 6 5C6.55228 5 7 5.44772 7 6C7 6.55228 6.55228 7 6 7C5.44772 7 5 6.55228 5 6ZM6 3C4.34315 3 3 4.34315 3 6C3 7.30622 3.83481 8.41746 5 8.82929V15.1707C3.83481 15.5825 3 16.6938 3 18C3 19.6569 4.34315 21 6 21C7.65685 21 9 19.6569 9 18C9 16.6938 8.16519 15.5825 7 15.1707V8.82929C8.16519 8.41746 9 7.30622 9 6C9 4.34315 7.65685 3 6 3ZM5 18C5 17.4477 5.44772 17 6 17C6.55228 17 7 17.4477 7 18C7 18.5523 6.55228 19 6 19C5.44772 19 5 18.5523 5 18ZM18 17C17.4477 17 17 17.4477 17 18C17 18.5523 17.4477 19 18 19C18.5523 19 19 18.5523 19 18C19 17.4477 18.5523 17 18 17ZM15 18C15 16.3431 16.3431 15 18 15C19.6569 15 21 16.3431 21 18C21 19.6569 19.6569 21 18 21C16.3431 21 15 19.6569 15 18ZM18 7.5C18.8284 7.5 19.5 6.82843 19.5 6C19.5 5.17157 18.8284 4.5 18 4.5C17.1716 4.5 16.5 5.17157 16.5 6C16.5 6.82843 17.1716 7.5 18 7.5ZM19.5 11.5C19.5 12.3284 18.8284 13 18 13C17.1716 13 16.5 12.3284 16.5 11.5C16.5 10.6716 17.1716 10 18 10C18.8284 10 19.5 10.6716 19.5 11.5Z" fill="currentColor"/></svg>',
        'git-pull-request': '<svg viewBox="0 0 24 24"><path d="M15 5H17C18.1046 5 19 5.89543 19 7V15.1707C20.1652 15.5825 21 16.6938 21 18C21 19.6569 19.6569 21 18 21C16.3431 21 15 19.6569 15 18C15 16.6938 15.8348 15.5825 17 15.1707V7H15V10L10.5 6L15 2V5ZM5 8.82929C3.83481 8.41746 3 7.30622 3 6C3 4.34315 4.34315 3 6 3C7.65685 3 9 4.34315 9 6C9 7.30622 8.16519 8.41746 7 8.82929V15.1707C8.16519 15.5825 9 16.6938 9 18C9 19.6569 7.65685 21 6 21C4.34315 21 3 19.6569 3 18C3 16.6938 3.83481 15.5825 5 15.1707V8.82929ZM6 7C6.55228 7 7 6.55228 7 6C7 5.44772 6.55228 5 6 5C5.44772 5 5 5.44772 5 6C5 6.55228 5.44772 7 6 7ZM6 19C6.55228 19 7 18.5523 7 18C7 17.4477 6.55228 17 6 17C5.44772 17 5 17.4477 5 18C5 18.5523 5.44772 19 6 19ZM18 19C18.5523 19 19 18.5523 19 18C19 17.4477 18.5523 17 18 17C17.4477 17 17 17.4477 17 18C17 18.5523 17.4477 19 18 19Z" fill="currentColor"/></svg>',
        'git-repository': '<svg viewBox="0 0 24 24"><path d="M13 21V23.5L10 21.5L7 23.5V21H6.5C4.567 21 3 19.433 3 17.5V5C3 3.34315 4.34315 2 6 2H20C20.5523 2 21 2.44772 21 3V20C21 20.5523 20.5523 21 20 21H13ZM13 19H19V16H6.5C5.67157 16 5 16.6716 5 17.5C5 18.3284 5.67157 19 6.5 19H7V17H13V19ZM19 14V4H6V14.0354C6.1633 14.0121 6.33024 14 6.5 14H19ZM7 5H9V7H7V5ZM7 8H9V10H7V8ZM7 11H9V13H7V11Z" fill="currentColor"/></svg>',
        'github-fill': '<svg viewBox="0 0 24 24"><path d="M12.001 2C6.47598 2 2.00098 6.475 2.00098 12C2.00098 16.425 4.86348 20.1625 8.83848 21.4875C9.33848 21.575 9.52598 21.275 9.52598 21.0125C9.52598 20.775 9.51348 19.9875 9.51348 19.15C7.00098 19.6125 6.35098 18.5375 6.15098 17.975C6.03848 17.6875 5.55098 16.8 5.12598 16.5625C4.77598 16.375 4.27598 15.9125 5.11348 15.9C5.90098 15.8875 6.46348 16.625 6.65098 16.925C7.55098 18.4375 8.98848 18.0125 9.56348 17.75C9.65098 17.1 9.91348 16.6625 10.201 16.4125C7.97598 16.1625 5.65098 15.3 5.65098 11.475C5.65098 10.3875 6.03848 9.4875 6.67598 8.7875C6.57598 8.5375 6.22598 7.5125 6.77598 6.1375C6.77598 6.1375 7.61348 5.875 9.52598 7.1625C10.326 6.9375 11.176 6.825 12.026 6.825C12.876 6.825 13.726 6.9375 14.526 7.1625C16.4385 5.8625 17.276 6.1375 17.276 6.1375C17.826 7.5125 17.476 8.5375 17.376 8.7875C18.0135 9.4875 18.401 10.375 18.401 11.475C18.401 15.3125 16.0635 16.1625 13.8385 16.4125C14.201 16.725 14.5135 17.325 14.5135 18.2625C14.5135 19.6 14.501 20.675 14.501 21.0125C14.501 21.275 14.6885 21.5875 15.1885 21.4875C19.259 20.1133 21.9999 16.2963 22.001 12C22.001 6.475 17.526 2 12.001 2Z" fill="currentColor"/></svg>',
        'github': '<svg viewBox="0 0 24 24"><path d="M5.88401 18.6533C5.58404 18.4526 5.32587 18.1975 5.0239 17.8369C4.91473 17.7065 4.47283 17.1524 4.55811 17.2583C4.09533 16.6833 3.80296 16.417 3.50156 16.3089C2.9817 16.1225 2.7114 15.5499 2.89784 15.0301C3.08428 14.5102 3.65685 14.2399 4.17672 14.4263C4.92936 14.6963 5.43847 15.1611 6.12425 16.0143C6.03025 15.8974 6.46364 16.441 6.55731 16.5529C6.74784 16.7804 6.88732 16.9182 6.99629 16.9911C7.20118 17.1283 7.58451 17.1874 8.14709 17.1311C8.17065 16.7489 8.24136 16.3783 8.34919 16.0358C5.38097 15.3104 3.70116 13.3952 3.70116 9.63971C3.70116 8.40085 4.0704 7.28393 4.75917 6.3478C4.5415 5.45392 4.57433 4.37284 5.06092 3.15636C5.1725 2.87739 5.40361 2.66338 5.69031 2.57352C5.77242 2.54973 5.81791 2.53915 5.89878 2.52673C6.70167 2.40343 7.83573 2.69705 9.31449 3.62336C10.181 3.41879 11.0885 3.315 12.0012 3.315C12.9129 3.315 13.8196 3.4186 14.6854 3.62277C16.1619 2.69 17.2986 2.39649 18.1072 2.52651C18.1919 2.54013 18.2645 2.55783 18.3249 2.57766C18.6059 2.66991 18.8316 2.88179 18.9414 3.15636C19.4279 4.37256 19.4608 5.45344 19.2433 6.3472C19.9342 7.28337 20.3012 8.39208 20.3012 9.63971C20.3012 13.3968 18.627 15.3048 15.6588 16.032C15.7837 16.447 15.8496 16.9105 15.8496 17.4121C15.8496 18.0765 15.8471 18.711 15.8424 19.4225C15.8412 19.6127 15.8397 19.8159 15.8375 20.1281C16.2129 20.2109 16.5229 20.5077 16.6031 20.9089C16.7114 21.4504 16.3602 21.9773 15.8186 22.0856C14.6794 22.3134 13.8353 21.5538 13.8353 20.5611C13.8353 20.4708 13.836 20.3417 13.8375 20.1145C13.8398 19.8015 13.8412 19.599 13.8425 19.4094C13.8471 18.7019 13.8496 18.0716 13.8496 17.4121C13.8496 16.7148 13.6664 16.2602 13.4237 16.051C12.7627 15.4812 13.0977 14.3973 13.965 14.2999C16.9314 13.9666 18.3012 12.8177 18.3012 9.63971C18.3012 8.68508 17.9893 7.89571 17.3881 7.23559C17.1301 6.95233 17.0567 6.54659 17.199 6.19087C17.3647 5.77663 17.4354 5.23384 17.2941 4.57702L17.2847 4.57968C16.7928 4.71886 16.1744 5.0198 15.4261 5.5285C15.182 5.69438 14.8772 5.74401 14.5932 5.66413C13.7729 5.43343 12.8913 5.315 12.0012 5.315C11.111 5.315 10.2294 5.43343 9.40916 5.66413C9.12662 5.74359 8.82344 5.69492 8.57997 5.53101C7.8274 5.02439 7.2056 4.72379 6.71079 4.58376C6.56735 5.23696 6.63814 5.77782 6.80336 6.19087C6.94565 6.54659 6.87219 6.95233 6.61423 7.23559C6.01715 7.8912 5.70116 8.69376 5.70116 9.63971C5.70116 12.8116 7.07225 13.9683 10.023 14.2999C10.8883 14.3971 11.2246 15.4769 10.5675 16.0482C10.3751 16.2156 10.1384 16.7802 10.1384 17.4121V20.5611C10.1384 21.5474 9.30356 22.2869 8.17878 22.09C7.63476 21.9948 7.27093 21.4766 7.36613 20.9326C7.43827 20.5204 7.75331 20.2116 8.13841 20.1276V19.1381C7.22829 19.1994 6.47656 19.0498 5.88401 18.6533Z" fill="currentColor"/></svg>',
        'global': '<svg viewBox="0 0 24 24"><path d="M12 22C6.47715 22 2 17.5228 2 12C2 6.47715 6.47715 2 12 2C17.5228 2 22 6.47715 22 12C22 17.5228 17.5228 22 12 22ZM9.71002 19.6674C8.74743 17.6259 8.15732 15.3742 8.02731 13H4.06189C4.458 16.1765 6.71639 18.7747 9.71002 19.6674ZM10.0307 13C10.1811 15.4388 10.8778 17.7297 12 19.752C13.1222 17.7297 13.8189 15.4388 13.9693 13H10.0307ZM19.9381 13H15.9727C15.8427 15.3742 15.2526 17.6259 14.29 19.6674C17.2836 18.7747 19.542 16.1765 19.9381 13ZM4.06189 11H8.02731C8.15732 8.62577 8.74743 6.37407 9.71002 4.33256C6.71639 5.22533 4.458 7.8235 4.06189 11ZM10.0307 11H13.9693C13.8189 8.56122 13.1222 6.27025 12 4.24799C10.8778 6.27025 10.1811 8.56122 10.0307 11ZM14.29 4.33256C15.2526 6.37407 15.8427 8.62577 15.9727 11H19.9381C19.542 7.8235 17.2836 5.22533 14.29 4.33256Z" fill="currentColor"/></svg>',
        'graduation-cap': '<svg viewBox="0 0 24 24"><path d="M4 11.3333L0 9L12 2L24 9V17.5H22V10.1667L20 11.3333V18.0113L19.7774 18.2864C17.9457 20.5499 15.1418 22 12 22C8.85817 22 6.05429 20.5499 4.22263 18.2864L4 18.0113V11.3333ZM6 12.5V17.2917C7.46721 18.954 9.61112 20 12 20C14.3889 20 16.5328 18.954 18 17.2917V12.5L12 16L6 12.5ZM3.96927 9L12 13.6846L20.0307 9L12 4.31541L3.96927 9Z" fill="currentColor"/></svg>',
        'hammer': '<svg viewBox="0 0 24 24"><path d="M20 2C20.5523 2 21 2.44772 21 3V8C21 8.55228 20.5523 9 20 9H15V22C15 22.5523 14.5523 23 14 23H10C9.44772 23 9 22.5523 9 22V9H3.5C2.94772 9 2.5 8.55228 2.5 8V5.61803C2.5 5.23926 2.714 4.893 3.05279 4.72361L8.5 2H20ZM15 4H8.97214L4.5 6.23607V7H11V21H13V7H15V4ZM19 4H17V7H19V4Z" fill="currentColor"/></svg>',
        'heart': '<svg viewBox="0 0 24 24"><path d="M12.001 4.52853C14.35 2.42 17.98 2.49 20.2426 4.75736C22.5053 7.02472 22.583 10.637 20.4786 12.993L11.9999 21.485L3.52138 12.993C1.41705 10.637 1.49571 7.01901 3.75736 4.75736C6.02157 2.49315 9.64519 2.41687 12.001 4.52853ZM18.827 6.1701C17.3279 4.66794 14.9076 4.60701 13.337 6.01687L12.0019 7.21524L10.6661 6.01781C9.09098 4.60597 6.67506 4.66808 5.17157 6.17157C3.68183 7.66131 3.60704 10.0473 4.97993 11.6232L11.9999 18.6543L19.0201 11.6232C20.3935 10.0467 20.319 7.66525 18.827 6.1701Z" fill="currentColor"/></svg>',
        'history': '<svg viewBox="0 0 24 24"><path d="M12 2C17.5228 2 22 6.47715 22 12C22 17.5228 17.5228 22 12 22C6.47715 22 2 17.5228 2 12H4C4 16.4183 7.58172 20 12 20C16.4183 20 20 16.4183 20 12C20 7.58172 16.4183 4 12 4C9.25022 4 6.82447 5.38734 5.38451 7.50024L8 7.5V9.5H2V3.5H4L3.99989 5.99918C5.82434 3.57075 8.72873 2 12 2ZM13 7L12.9998 11.585L16.2426 14.8284L14.8284 16.2426L10.9998 12.413L11 7H13Z" fill="currentColor"/></svg>',
        'home': '<svg viewBox="0 0 24 24"><path d="M21 20C21 20.5523 20.5523 21 20 21H4C3.44772 21 3 20.5523 3 20V9.48907C3 9.18048 3.14247 8.88917 3.38606 8.69972L11.3861 2.47749C11.7472 2.19663 12.2528 2.19663 12.6139 2.47749L20.6139 8.69972C20.8575 8.88917 21 9.18048 21 9.48907V20ZM19 19V9.97815L12 4.53371L5 9.97815V19H19Z" fill="currentColor"/></svg>',
        'home-assistant': '<svg viewBox="0 0 24 24"><path d="M12 2a2 2 0 0 0-1.4.6l-7.9 7.8c-.4.5-.7 1.1-.7 1.8V20c0 1.1.9 2 2 2h16a2 2 0 0 0 2-2v-7.8c0-.7-.3-1.3-.7-1.8l-7.9-7.8A2 2 0 0 0 12 2z" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M12 11.5V20.8M12 21L7.7 17M12 17.5L16.6 13.6" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/><circle cx="12" cy="10.2" r="1.5" fill="currentColor"/><circle cx="7.7" cy="17" r="1.4" fill="currentColor"/><circle cx="16.6" cy="13.6" r="1.4" fill="currentColor"/></svg>',
        'layout-grid': '<svg viewBox="0 0 24 24"><path d="M14 10V14H10V10H14ZM16 10H21V14H16V10ZM14 21H10V16H14V21ZM16 21V16H21V20C21 20.5523 20.5523 21 20 21H16ZM14 3V8H10V3H14ZM16 3H20C20.5523 3 21 3.44772 21 4V8H16V3ZM8 10V14H3V10H8ZM8 21H4C3.44772 21 3 20.5523 3 20V16H8V21ZM8 3V8H3V4C3 3.44772 3.44772 3 4 3H8Z" fill="currentColor"/></svg>',
        'hourglass-fill': '<svg viewBox="0 0 24 24"><path d="M6 4H4V2H20V4H18V6C18 7.61543 17.1838 8.91468 16.1561 9.97667C15.4532 10.703 14.598 11.372 13.7309 12C14.598 12.628 15.4532 13.297 16.1561 14.0233C17.1838 15.0853 18 16.3846 18 18V20H20V22H4V20H6V18C6 16.3846 6.81616 15.0853 7.8439 14.0233C8.54682 13.297 9.40202 12.628 10.2691 12C9.40202 11.372 8.54682 10.703 7.8439 9.97667C6.81616 8.91468 6 7.61543 6 6V4ZM8 4V6C8 6.68514 8.26026 7.33499 8.77131 8H15.2287C15.7397 7.33499 16 6.68514 16 6V4H8ZM12 13.2219C10.9548 13.9602 10.008 14.663 9.2811 15.4142C9.09008 15.6116 8.92007 15.8064 8.77131 16H15.2287C15.0799 15.8064 14.9099 15.6116 14.7189 15.4142C13.992 14.663 13.0452 13.9602 12 13.2219Z" fill="currentColor"/></svg>',
        'hourglass': '<svg viewBox="0 0 24 24"><path d="M6 4H4V2H20V4H18V6C18 7.61543 17.1838 8.91468 16.1561 9.97667C15.4532 10.703 14.598 11.372 13.7309 12C14.598 12.628 15.4532 13.297 16.1561 14.0233C17.1838 15.0853 18 16.3846 18 18V20H20V22H4V20H6V18C6 16.3846 6.81616 15.0853 7.8439 14.0233C8.54682 13.297 9.40202 12.628 10.2691 12C9.40202 11.372 8.54682 10.703 7.8439 9.97667C6.81616 8.91468 6 7.61543 6 6V4ZM8 4V6C8 6.88457 8.43384 7.71032 9.2811 8.58583C10.008 9.33699 10.9548 10.0398 12 10.7781C13.0452 10.0398 13.992 9.33699 14.7189 8.58583C15.5662 7.71032 16 6.88457 16 6V4H8ZM12 13.2219C10.9548 13.9602 10.008 14.663 9.2811 15.4142C8.43384 16.2897 8 17.1154 8 18V20H16V18C16 17.1154 15.5662 16.2897 14.7189 15.4142C13.992 14.663 13.0452 13.9602 12 13.2219Z" fill="currentColor"/></svg>',
        'image-download': '<svg viewBox="0 0 24 24"><path d="M21 15V19H24L20 23L16 19H19V15H21ZM21.0078 3C21.5555 3 21.9999 3.44482 22 3.99316V13H20V5H4V18.999L14 9L17 12V14.8291L14 11.8281L6.82715 19H14V21H2.99219C2.44451 21 2.00013 20.5552 2 20.0068V3.99316C2.00013 3.44463 2.45577 3 2.99219 3H21.0078ZM8 7C9.10457 7 10 7.89543 10 9C10 10.1046 9.10457 11 8 11C6.89543 11 6 10.1046 6 9C6 7.89543 6.89543 7 8 7Z" fill="currentColor"/></svg>',
        'inbox-archive': '<svg viewBox="0 0 24 24"><path d="M20 3L22 7V20C22 20.5523 21.5523 21 21 21H3C2.44772 21 2 20.5523 2 20V7.00353L4 3H20ZM20 9H4V19H20V9ZM13 10V14H16L12 18L8 14H11V10H13ZM18.7639 5H5.23656L4.23744 7H19.7639L18.7639 5Z" fill="currentColor"/></svg>',
        'inbox-unarchive-fill': '<svg viewBox="0 0 24 24"><path d="M20 3L22 7V20C22 20.5523 21.5523 21 21 21H3C2.44772 21 2 20.5523 2 20V7.00353L4 3H20ZM12 10L8 14H11V18H13V14H16L12 10ZM18.764 5H5.236L4.237 7H19.764L18.764 5Z" fill="currentColor"/></svg>',
        'inbox-unarchive': '<svg viewBox="0 0 24 24"><path d="M20 3L22 7V20C22 20.5523 21.5523 21 21 21H3C2.44772 21 2 20.5523 2 20V7.00353L4 3H20ZM20 9H4V19H20V9ZM12 10L16 14H13V18H11V14H8L12 10ZM18.764 5H5.236L4.237 7H19.764L18.764 5Z" fill="currentColor"/></svg>',
        'information': '<svg viewBox="0 0 24 24"><path d="M12 22C6.47715 22 2 17.5228 2 12C2 6.47715 6.47715 2 12 2C17.5228 2 22 6.47715 22 12C22 17.5228 17.5228 22 12 22ZM12 20C16.4183 20 20 16.4183 20 12C20 7.58172 16.4183 4 12 4C7.58172 4 4 7.58172 4 12C4 16.4183 7.58172 20 12 20ZM11 7H13V9H11V7ZM11 11H13V17H11V11Z" fill="currentColor"/></svg>',
        'key': '<svg viewBox="0 0 24 24"><path d="M12.917 13C12.441 15.8377 9.973 18 7 18C3.68629 18 1 15.3137 1 12C1 8.68629 3.68629 6 7 6C9.973 6 12.441 8.16229 12.917 11H23V13H21V17H19V13H17V17H15V13H12.917ZM7 16C9.20914 16 11 14.2091 11 12C11 9.79086 9.20914 8 7 8C4.79086 8 3 9.79086 3 12C3 14.2091 4.79086 16 7 16Z" fill="currentColor"/></svg>',
        'layout-column': '<svg viewBox="0 0 24 24"><path d="M11 5H5V19H11V5ZM13 5V19H19V5H13ZM4 3H20C20.5523 3 21 3.44772 21 4V20C21 20.5523 20.5523 21 20 21H4C3.44772 21 3 20.5523 3 20V4C3 3.44772 3.44772 3 4 3Z" fill="currentColor"/></svg>',
        'layout-left': '<svg viewBox="0 0 24 24"><path d="M21 3C21.5523 3 22 3.44772 22 4V20C22 20.5523 21.5523 21 21 21H3C2.44772 21 2 20.5523 2 20V4C2 3.44772 2.44772 3 3 3H21ZM7 5H4V19H7V5ZM20 5H9V19H20V5Z" fill="currentColor"/></svg>',
        'layout-right': '<svg viewBox="0 0 24 24"><path d="M21 3C21.5523 3 22 3.44772 22 4V20C22 20.5523 21.5523 21 21 21H3C2.44772 21 2 20.5523 2 20V4C2 3.44772 2.44772 3 3 3H21ZM15 5H4V19H15V5ZM20 5H17V19H20V5Z" fill="currentColor"/></svg>',
        'leaf': '<svg viewBox="0 0 24 24"><path d="M20.998 3V5C20.998 14.6274 15.6255 19 8.99805 19L5.24077 18.9999C5.0786 19.912 4.99805 20.907 4.99805 22H2.99805C2.99805 20.6373 3.11376 19.3997 3.34381 18.2682C3.1133 16.9741 2.99805 15.2176 2.99805 13C2.99805 7.47715 7.4752 3 12.998 3C14.998 3 16.998 4 20.998 3ZM12.998 5C8.57977 5 4.99805 8.58172 4.99805 13C4.99805 13.3624 5.00125 13.7111 5.00759 14.0459C6.26198 12.0684 8.09902 10.5048 10.5019 9.13176L11.4942 10.8682C8.6393 12.4996 6.74554 14.3535 5.77329 16.9998L8.99805 17C15.0132 17 18.8692 13.0269 18.9949 5.38766C17.6229 5.52113 16.3481 5.436 14.7754 5.20009C13.6243 5.02742 13.3988 5 12.998 5Z" fill="currentColor"/></svg>',
        'lightbulb': '<svg viewBox="0 0 24 24"><path d="M9.97308 18H11V13H13V18H14.0269C14.1589 16.7984 14.7721 15.8065 15.7676 14.7226C15.8797 14.6006 16.5988 13.8564 16.6841 13.7501C17.5318 12.6931 18 11.385 18 10C18 6.68629 15.3137 4 12 4C8.68629 4 6 6.68629 6 10C6 11.3843 6.46774 12.6917 7.31462 13.7484C7.40004 13.855 8.12081 14.6012 8.23154 14.7218C9.22766 15.8064 9.84103 16.7984 9.97308 18ZM10 20V21H14V20H10ZM5.75395 14.9992C4.65645 13.6297 4 11.8915 4 10C4 5.58172 7.58172 2 12 2C16.4183 2 20 5.58172 20 10C20 11.8925 19.3428 13.6315 18.2443 15.0014C17.624 15.7748 16 17 16 18.5V21C16 22.1046 15.1046 23 14 23H10C8.89543 23 8 22.1046 8 21V18.5C8 17 6.37458 15.7736 5.75395 14.9992Z" fill="currentColor"/></svg>',
        'link-unlink-m': '<svg viewBox="0 0 24 24"><path d="M17.657 14.8284L16.2428 13.4142L17.657 12C19.2191 10.4379 19.2191 7.90526 17.657 6.34316C16.0949 4.78106 13.5622 4.78106 12.0001 6.34316L10.5859 7.75737L9.17171 6.34316L10.5859 4.92895C12.9291 2.5858 16.7281 2.5858 19.0712 4.92895C21.4143 7.27209 21.4143 11.0711 19.0712 13.4142L17.657 14.8284ZM14.8286 17.6569L13.4143 19.0711C11.0712 21.4142 7.27221 21.4142 4.92907 19.0711C2.58592 16.7279 2.58592 12.9289 4.92907 10.5858L6.34328 9.17159L7.75749 10.5858L6.34328 12C4.78118 13.5621 4.78118 16.0948 6.34328 17.6569C7.90538 19.219 10.438 19.219 12.0001 17.6569L13.4143 16.2427L14.8286 17.6569ZM14.8286 7.75737L16.2428 9.17159L9.17171 16.2427L7.75749 14.8284L14.8286 7.75737ZM5.77539 2.29291L7.70724 1.77527L8.74252 5.63897L6.81067 6.15661L5.77539 2.29291ZM15.2578 18.3611L17.1896 17.8434L18.2249 21.7071L16.293 22.2248L15.2578 18.3611ZM2.29303 5.77527L6.15673 6.81054L5.63909 8.7424L1.77539 7.70712L2.29303 5.77527ZM18.3612 15.2576L22.2249 16.2929L21.7072 18.2248L17.8435 17.1895L18.3612 15.2576Z" fill="currentColor"/></svg>',
        'list-check-2': '<svg viewBox="0 0 24 24"><path d="M11 4H21V6H11V4ZM11 8H17V10H11V8ZM11 14H21V16H11V14ZM11 18H17V20H11V18ZM3 4H9V10H3V4ZM5 6V8H7V6H5ZM3 14H9V20H3V14ZM5 16V18H7V16H5Z" fill="currentColor"/></svg>',
        'list-check-3': '<svg viewBox="0 0 24 24"><path d="M8.00008 6V9H5.00008V6H8.00008ZM3.00008 4V11H10.0001V4H3.00008ZM13.0001 4H21.0001V6H13.0001V4ZM13.0001 11H21.0001V13H13.0001V11ZM13.0001 18H21.0001V20H13.0001V18ZM10.7072 16.2071L9.29297 14.7929L6.00008 18.0858L4.20718 16.2929L2.79297 17.7071L6.00008 20.9142L10.7072 16.2071Z" fill="currentColor"/></svg>',
        'list-unordered': '<svg viewBox="0 0 24 24"><path d="M8 4H21V6H8V4ZM4.5 6.5C3.67157 6.5 3 5.82843 3 5C3 4.17157 3.67157 3.5 4.5 3.5C5.32843 3.5 6 4.17157 6 5C6 5.82843 5.32843 6.5 4.5 6.5ZM4.5 13.5C3.67157 13.5 3 12.8284 3 12C3 11.1716 3.67157 10.5 4.5 10.5C5.32843 10.5 6 11.1716 6 12C6 12.8284 5.32843 13.5 4.5 13.5ZM4.5 20.4C3.67157 20.4 3 19.7284 3 18.9C3 18.0716 3.67157 17.4 4.5 17.4C5.32843 17.4 6 18.0716 6 18.9C6 19.7284 5.32843 20.4 4.5 20.4ZM8 11H21V13H8V11ZM8 18H21V20H8V18Z" fill="currentColor"/></svg>',
        'loader-4': '<svg viewBox="0 0 24 24"><path d="M18.364 5.63604L16.9497 7.05025C15.683 5.7835 13.933 5 12 5C8.13401 5 5 8.13401 5 12C5 15.866 8.13401 19 12 19C15.866 19 19 15.866 19 12H21C21 16.9706 16.9706 21 12 21C7.02944 21 3 16.9706 3 12C3 7.02944 7.02944 3 12 3C14.4853 3 16.7353 4.00736 18.364 5.63604Z" fill="currentColor"/></svg>',
        'loader': '<svg viewBox="0 0 24 24"><path d="M11.9995 2C12.5518 2 12.9995 2.44772 12.9995 3V6C12.9995 6.55228 12.5518 7 11.9995 7C11.4472 7 10.9995 6.55228 10.9995 6V3C10.9995 2.44772 11.4472 2 11.9995 2ZM11.9995 17C12.5518 17 12.9995 17.4477 12.9995 18V21C12.9995 21.5523 12.5518 22 11.9995 22C11.4472 22 10.9995 21.5523 10.9995 21V18C10.9995 17.4477 11.4472 17 11.9995 17ZM20.6597 7C20.9359 7.47829 20.772 8.08988 20.2937 8.36602L17.6956 9.86602C17.2173 10.1422 16.6057 9.97829 16.3296 9.5C16.0535 9.02171 16.2173 8.41012 16.6956 8.13398L19.2937 6.63397C19.772 6.35783 20.3836 6.52171 20.6597 7ZM7.66935 14.5C7.94549 14.9783 7.78161 15.5899 7.30332 15.866L4.70525 17.366C4.22695 17.6422 3.61536 17.4783 3.33922 17C3.06308 16.5217 3.22695 15.9101 3.70525 15.634L6.30332 14.134C6.78161 13.8578 7.3932 14.0217 7.66935 14.5ZM20.6597 17C20.3836 17.4783 19.772 17.6422 19.2937 17.366L16.6956 15.866C16.2173 15.5899 16.0535 14.9783 16.3296 14.5C16.6057 14.0217 17.2173 13.8578 17.6956 14.134L20.2937 15.634C20.772 15.9101 20.9359 16.5217 20.6597 17ZM7.66935 9.5C7.3932 9.97829 6.78161 10.1422 6.30332 9.86602L3.70525 8.36602C3.22695 8.08988 3.06308 7.47829 3.33922 7C3.61536 6.52171 4.22695 6.35783 4.70525 6.63397L7.30332 8.13398C7.78161 8.41012 7.94549 9.02171 7.66935 9.5Z" fill="currentColor"/></svg>',
        'lock-2': '<svg viewBox="0 0 24 24"><path d="M6 8V7C6 3.68629 8.68629 1 12 1C15.3137 1 18 3.68629 18 7V8H20C20.5523 8 21 8.44772 21 9V21C21 21.5523 20.5523 22 20 22H4C3.44772 22 3 21.5523 3 21V9C3 8.44772 3.44772 8 4 8H6ZM19 10H5V20H19V10ZM11 15.7324C10.4022 15.3866 10 14.7403 10 14C10 12.8954 10.8954 12 12 12C13.1046 12 14 12.8954 14 14C14 14.7403 13.5978 15.3866 13 15.7324V18H11V15.7324ZM8 8H16V7C16 4.79086 14.2091 3 12 3C9.79086 3 8 4.79086 8 7V8Z" fill="currentColor"/></svg>',
        'lock': '<svg viewBox="0 0 24 24"><path d="M19 10H20C20.5523 10 21 10.4477 21 11V21C21 21.5523 20.5523 22 20 22H4C3.44772 22 3 21.5523 3 21V11C3 10.4477 3.44772 10 4 10H5V9C5 5.13401 8.13401 2 12 2C15.866 2 19 5.13401 19 9V10ZM5 12V20H19V12H5ZM11 14H13V18H11V14ZM17 10V9C17 6.23858 14.7614 4 12 4C9.23858 4 7 6.23858 7 9V10H17Z" fill="currentColor"/></svg>',
        'lock-unlock': '<svg viewBox="0 0 24 24"><path d="M7 10H20C20.5523 10 21 10.4477 21 11V21C21 21.5523 20.5523 22 20 22H4C3.44772 22 3 21.5523 3 21V11C3 10.4477 3.44772 10 4 10H5V9C5 5.13401 8.13401 2 12 2C14.7405 2 17.1131 3.5748 18.2624 5.86882L16.4731 6.76344C15.6522 5.12486 13.9575 4 12 4C9.23858 4 7 6.23858 7 9V10ZM5 12V20H19V12H5ZM10 15H14V17H10V15Z" fill="currentColor"/></svg>',
        'loop-right-ai': '<svg viewBox="0 0 24 24"><path d="M22 12C22 17.5228 17.5228 22 12 22C8.72774 22 5.82382 20.4286 4 18.001V20.5H2V14.5H8V16.5H5.38477C6.82543 18.6137 9.25151 20 12 20C16.4183 20 20 16.4183 20 12H22ZM11.5293 8.31934C11.7059 7.8935 12.2943 7.89349 12.4707 8.31934L12.7236 8.93066C13.1556 9.97346 13.9615 10.8062 14.9746 11.2568L15.6924 11.5762C16.1026 11.759 16.1026 12.3562 15.6924 12.5391L14.9326 12.877C13.9449 13.3162 13.1534 14.1194 12.7139 15.1279L12.4668 15.6934C12.2864 16.1075 11.7137 16.1075 11.5332 15.6934L11.2871 15.1279C10.8476 14.1193 10.0552 13.3163 9.06738 12.877L8.30762 12.5391C7.89744 12.3562 7.89741 11.759 8.30762 11.5762L9.02539 11.2568C10.0385 10.8062 10.8445 9.97348 11.2764 8.93066L11.5293 8.31934ZM12 2C15.2723 2 18.1762 3.57144 20 5.99902V3.5H22V9.5H16V7.5H18.6152C17.1746 5.38634 14.7485 4 12 4C7.58172 4 4 7.58172 4 12H2C2 6.47715 6.47715 2 12 2Z" fill="currentColor"/></svg>',
        'macbook': '<svg viewBox="0 0 24 24"><path d="M4 5V16H20V5H4ZM2 4.00748C2 3.45107 2.45531 3 2.9918 3H21.0082C21.556 3 22 3.44892 22 4.00748V18H2V4.00748ZM1 19H23V21H1V19Z" fill="currentColor"/></svg>',
        'menu-fold-2': '<svg viewBox="0 0 24 24"><path d="M4.40347 3.90332L2.98926 5.31753L6.17124 8.49951L2.98926 11.6815L4.40347 13.0957L8.99967 8.49951L4.40347 3.90332ZM20.9997 19.9995V17.9995H2.99967V19.9995H20.9997ZM20.9997 12.9995V10.9995H11.9997V12.9995H20.9997ZM20.9997 5.99951V3.99951H11.9997V5.99951H20.9997Z" fill="currentColor"/></svg>',
        'menu-search': '<svg viewBox="0 0 24 24"><path d="M15.5 5C13.567 5 12 6.567 12 8.5C12 10.433 13.567 12 15.5 12C17.433 12 19 10.433 19 8.5C19 6.567 17.433 5 15.5 5ZM10 8.5C10 5.46243 12.4624 3 15.5 3C18.5376 3 21 5.46243 21 8.5C21 9.6575 20.6424 10.7315 20.0317 11.6175L22.7071 14.2929L21.2929 15.7071L18.6175 13.0317C17.7315 13.6424 16.6575 14 15.5 14C12.4624 14 10 11.5376 10 8.5ZM3 4H8V6H3V4ZM3 11H8V13H3V11ZM21 18V20H3V18H21Z" fill="currentColor"/></svg>',
        'message-2': '<svg viewBox="0 0 24 24"><path d="M6.45455 19L2 22.5V4C2 3.44772 2.44772 3 3 3H21C21.5523 3 22 3.44772 22 4V18C22 18.5523 21.5523 19 21 19H6.45455ZM5.76282 17H20V5H4V18.3851L5.76282 17ZM11 10H13V12H11V10ZM7 10H9V12H7V10ZM15 10H17V12H15V10Z" fill="currentColor"/></svg>',
        'mic': '<svg viewBox="0 0 24 24"><path d="M11.9998 3C10.3429 3 8.99976 4.34315 8.99976 6V10C8.99976 11.6569 10.3429 13 11.9998 13C13.6566 13 14.9998 11.6569 14.9998 10V6C14.9998 4.34315 13.6566 3 11.9998 3ZM11.9998 1C14.7612 1 16.9998 3.23858 16.9998 6V10C16.9998 12.7614 14.7612 15 11.9998 15C9.23833 15 6.99976 12.7614 6.99976 10V6C6.99976 3.23858 9.23833 1 11.9998 1ZM3.05469 11H5.07065C5.55588 14.3923 8.47329 17 11.9998 17C15.5262 17 18.4436 14.3923 18.9289 11H20.9448C20.4837 15.1716 17.1714 18.4839 12.9998 18.9451V23H10.9998V18.9451C6.82814 18.4839 3.51584 15.1716 3.05469 11Z" fill="currentColor"/></svg>',
        'mic-off': '<svg viewBox="0 0 24 24"><path d="M16.4249 17.839L21.1925 22.6066L22.6068 21.1924L2.80777 1.3934L1.39355 2.80761L7.00016 8.41421V10C7.00016 12.7614 9.23873 15 12.0002 15C12.4825 15 12.9489 14.9317 13.3902 14.8042L14.9404 16.3544C14.0464 16.7688 13.0503 17 12.0002 17C8.47368 17 5.55627 14.3923 5.07105 11H3.05509C3.51623 15.1716 6.82854 18.4839 11.0002 18.9451V23H13.0002V18.9451C14.2341 18.8087 15.3929 18.4228 16.4249 17.839ZM11.5528 12.9669C10.2541 12.7727 9.22745 11.7461 9.03328 10.4473L11.5528 12.9669ZM19.3747 15.1604L17.9323 13.7179C18.4407 12.9084 18.788 11.9874 18.9293 11H20.9452C20.7754 12.5366 20.2187 13.9565 19.3747 15.1604ZM16.4658 12.2514L14.9173 10.703C14.9715 10.4775 15.0002 10.2421 15.0002 10V6C15.0002 4.34315 13.657 3 12.0002 3C10.7059 3 9.6031 3.81956 9.18237 4.96802L7.68575 3.47139C8.55427 1.99268 10.1613 1 12.0002 1C14.7616 1 17.0002 3.23858 17.0002 6V10C17.0002 10.8099 16.8076 11.5748 16.4658 12.2514Z" fill="currentColor"/></svg>',
        'more-2-fill': '<svg viewBox="0 0 24 24"><path d="M12 3C10.9 3 10 3.9 10 5C10 6.1 10.9 7 12 7C13.1 7 14 6.1 14 5C14 3.9 13.1 3 12 3ZM12 17C10.9 17 10 17.9 10 19C10 20.1 10.9 21 12 21C13.1 21 14 20.1 14 19C14 17.9 13.1 17 12 17ZM12 10C10.9 10 10 10.9 10 12C10 13.1 10.9 14 12 14C13.1 14 14 13.1 14 12C14 10.9 13.1 10 12 10Z" fill="currentColor"/></svg>',
        'more-2': '<svg viewBox="0 0 24 24"><path d="M12 3C11.175 3 10.5 3.675 10.5 4.5C10.5 5.325 11.175 6 12 6C12.825 6 13.5 5.325 13.5 4.5C13.5 3.675 12.825 3 12 3ZM12 18C11.175 18 10.5 18.675 10.5 19.5C10.5 20.325 11.175 21 12 21C12.825 21 13.5 20.325 13.5 19.5C13.5 18.675 12.825 18 12 18ZM12 10.5C11.175 10.5 10.5 11.175 10.5 12C10.5 12.825 11.175 13.5 12 13.5C12.825 13.5 13.5 12.825 13.5 12C13.5 11.175 12.825 10.5 12 10.5Z" fill="currentColor"/></svg>',
        'more': '<svg viewBox="0 0 24 24"><path d="M4.5 10.5C3.675 10.5 3 11.175 3 12C3 12.825 3.675 13.5 4.5 13.5C5.325 13.5 6 12.825 6 12C6 11.175 5.325 10.5 4.5 10.5ZM19.5 10.5C18.675 10.5 18 11.175 18 12C18 12.825 18.675 13.5 19.5 13.5C20.325 13.5 21 12.825 21 12C21 11.175 20.325 10.5 19.5 10.5ZM12 10.5C11.175 10.5 10.5 11.175 10.5 12C10.5 12.825 11.175 13.5 12 13.5C12.825 13.5 13.5 12.825 13.5 12C13.5 11.175 12.825 10.5 12 10.5Z" fill="currentColor"/></svg>',
        'music': '<svg viewBox="0 0 24 24"><path d="M12 13.5351V3H20V5H14V17C14 19.2091 12.2091 21 10 21C7.79086 21 6 19.2091 6 17C6 14.7909 7.79086 13 10 13C10.7286 13 11.4117 13.1948 12 13.5351ZM10 19C11.1046 19 12 18.1046 12 17C12 15.8954 11.1046 15 10 15C8.89543 15 8 15.8954 8 17C8 18.1046 8.89543 19 10 19Z" fill="currentColor"/></svg>',
        'node-tree': '<svg viewBox="0 0 24 24"><path d="M10 2C10.5523 2 11 2.44772 11 3V7C11 7.55228 10.5523 8 10 8H8V10H13V9C13 8.44772 13.4477 8 14 8H20C20.5523 8 21 8.44772 21 9V13C21 13.5523 20.5523 14 20 14H14C13.4477 14 13 13.5523 13 13V12H8V18H13V17C13 16.4477 13.4477 16 14 16H20C20.5523 16 21 16.4477 21 17V21C21 21.5523 20.5523 22 20 22H14C13.4477 22 13 21.5523 13 21V20H7C6.44772 20 6 19.5523 6 19V8H4C3.44772 8 3 7.55228 3 7V3C3 2.44772 3.44772 2 4 2H10ZM19 18H15V20H19V18ZM19 10H15V12H19V10ZM9 4H5V6H9V4Z" fill="currentColor"/></svg>',
        'notification-3': '<svg viewBox="0 0 24 24"><path d="M20 17H22V19H2V17H4V10C4 5.58172 7.58172 2 12 2C16.4183 2 20 5.58172 20 10V17ZM18 17V10C18 6.68629 15.3137 4 12 4C8.68629 4 6 6.68629 6 10V17H18ZM9 21H15V23H9V21Z" fill="currentColor"/></svg>',
        'palette': '<svg viewBox="0 0 24 24"><path d="M12 2C17.5222 2 22 5.97778 22 10.8889C22 13.9556 19.5111 16.4444 16.4444 16.4444H14.4778C13.5556 16.4444 12.8111 17.1889 12.8111 18.1111C12.8111 18.5333 12.9778 18.9111 13.2222 19.2C13.4667 19.5333 13.6333 19.9111 13.6333 20.3333C13.6333 21.2556 12.8889 22 11.9667 22C6.47778 22 2 17.5222 2 12C2 6.47778 6.47778 2 12 2ZM10.8111 18.1111C10.8111 16.0667 12.4333 14.4444 14.4778 14.4444H16.4444C18.4 14.4444 20 12.8444 20 10.8889C20 7.1 16.4222 4 12 4C7.58889 4 4 7.58889 4 12C4 16.0222 7.02222 19.3556 10.8778 19.9333C10.8333 19.6667 10.8111 19.3889 10.8111 19.1111V18.1111ZM7.5 12C6.67157 12 6 11.3284 6 10.5C6 9.67157 6.67157 9 7.5 9C8.32843 9 9 9.67157 9 10.5C9 11.3284 8.32843 12 7.5 12ZM16.5 12C15.6716 12 15 11.3284 15 10.5C15 9.67157 15.6716 9 16.5 9C17.3284 9 18 9.67157 18 10.5C18 11.3284 17.3284 12 16.5 12ZM12 9C11.1716 9 10.5 8.32843 10.5 7.5C10.5 6.67157 11.1716 6 12 6C12.8284 6 13.5 6.67157 13.5 7.5C13.5 8.32843 12.8284 9 12 9Z" fill="currentColor"/></svg>',
        'pencil-ai': '<svg viewBox="0 0 24 24"><path d="M16.4356 3.21188C16.8261 2.82185 17.4592 2.82157 17.8496 3.21188L20.6777 6.04099C21.0681 6.43152 21.0682 7.06457 20.6777 7.45505L7.2422 20.8896H3.00001V16.6475L16.4356 3.21188ZM5.00001 17.4756V18.8896H6.41407L15.7276 9.57615L14.3135 8.16208L5.00001 17.4756ZM4.5293 1.3193C4.70583 0.893505 5.29418 0.893508 5.47071 1.3193L5.72364 1.93063C6.15555 2.97342 6.96155 3.80613 7.97462 4.2568L8.69239 4.57614C9.10267 4.75896 9.10262 5.35616 8.69239 5.53903L7.93263 5.87692C6.94497 6.3162 6.15339 7.11943 5.71387 8.1279L5.4668 8.69334C5.28636 9.10747 4.71366 9.10747 4.53321 8.69334L4.28614 8.1279C3.84661 7.11943 3.05506 6.3162 2.06739 5.87692L1.30762 5.53903C0.897483 5.35617 0.897435 4.75896 1.30762 4.57614L2.0254 4.2568C3.03845 3.80614 3.84446 2.97344 4.27637 1.93063L4.5293 1.3193ZM15.7276 6.74802L17.1426 8.16208L18.5567 6.74802L17.1426 5.33395L15.7276 6.74802Z" fill="currentColor"/></svg>',
        'pencil': '<svg viewBox="0 0 24 24"><path d="M15.7279 9.57627L14.3137 8.16206L5 17.4758V18.89H6.41421L15.7279 9.57627ZM17.1421 8.16206L18.5563 6.74785L17.1421 5.33363L15.7279 6.74785L17.1421 8.16206ZM7.24264 20.89H3V16.6473L16.435 3.21231C16.8256 2.82179 17.4587 2.82179 17.8492 3.21231L20.6777 6.04074C21.0682 6.43126 21.0682 7.06443 20.6777 7.45495L7.24264 20.89Z" fill="currentColor"/></svg>',
        'picture-in-picture-2': '<svg viewBox="0 0 24 24"><path d="M21 3C21.5523 3 22 3.44772 22 4V11H20V5H4V19H10V21H3C2.44772 21 2 20.5523 2 20V4C2 3.44772 2.44772 3 3 3H21ZM21 13C21.5523 13 22 13.4477 22 14V20C22 20.5523 21.5523 21 21 21H13C12.4477 21 12 20.5523 12 20V14C12 13.4477 12.4477 13 13 13H21ZM20 15H14V19H20V15ZM6.70711 6.29289L8.95689 8.54289L11 6.5V12H5.5L7.54289 9.95689L5.29289 7.70711L6.70711 6.29289Z" fill="currentColor"/></svg>',
        'pie-chart': '<svg viewBox="0 0 24 24"><path d="M9 2.4578V4.58152C6.06817 5.76829 4 8.64262 4 12C4 16.4183 7.58172 20 12 20C15.3574 20 18.2317 17.9318 19.4185 15H21.5422C20.2679 19.0571 16.4776 22 12 22C6.47715 22 2 17.5228 2 12C2 7.52236 4.94289 3.73207 9 2.4578ZM12 2C17.5228 2 22 6.47715 22 12C22 12.3375 21.9833 12.6711 21.9506 13H11V2.04938C11.3289 2.01672 11.6625 2 12 2ZM13 4.06189V11H19.9381C19.4869 7.38128 16.6187 4.51314 13 4.06189Z" fill="currentColor"/></svg>',
        'play': '<svg viewBox="0 0 24 24"><path d="M16.3944 12.0001L10 7.7371V16.263L16.3944 12.0001ZM19.376 12.4161L8.77735 19.4818C8.54759 19.635 8.23715 19.5729 8.08397 19.3432C8.02922 19.261 8 19.1645 8 19.0658V4.93433C8 4.65818 8.22386 4.43433 8.5 4.43433C8.59871 4.43433 8.69522 4.46355 8.77735 4.5183L19.376 11.584C19.6057 11.7372 19.6678 12.0477 19.5146 12.2774C19.478 12.3323 19.4309 12.3795 19.376 12.4161Z" fill="currentColor"/></svg>',
        'play-list-add': '<svg viewBox="0 0 24 24"><path d="M2 18H12V20H2V18ZM2 11H22V13H2V11ZM2 4H22V6H2V4ZM18 18V15H20V18H23V20H20V23H18V20H15V18H18Z" fill="currentColor"/></svg>',
        'plug-2': '<svg viewBox="0 0 24 24"><path d="M13 18V20H19V22H13C11.8954 22 11 21.1046 11 20V18H8C5.79086 18 4 16.2091 4 14V7C4 6.44772 4.44772 6 5 6H7V2H9V6H15V2H17V6H19C19.5523 6 20 6.44772 20 7V14C20 16.2091 18.2091 18 16 18H13ZM8 16H16C17.1046 16 18 15.1046 18 14V11H6V14C6 15.1046 6.89543 16 8 16ZM18 8H6V9H18V8ZM12 14.5C11.4477 14.5 11 14.0523 11 13.5C11 12.9477 11.4477 12.5 12 12.5C12.5523 12.5 13 12.9477 13 13.5C13 14.0523 12.5523 14.5 12 14.5ZM11 2H13V5H11V2Z" fill="currentColor"/></svg>',
        'plug': '<svg viewBox="0 0 24 24"><path d="M13 18V20H19V22H13C11.8954 22 11 21.1046 11 20V18H8C5.79086 18 4 16.2091 4 14V7C4 6.44772 4.44772 6 5 6H8V2H10V6H14V2H16V6H19C19.5523 6 20 6.44772 20 7V14C20 16.2091 18.2091 18 16 18H13ZM8 16H16C17.1046 16 18 15.1046 18 14V11H6V14C6 15.1046 6.89543 16 8 16ZM18 8H6V9H18V8ZM12 14.5C11.4477 14.5 11 14.0523 11 13.5C11 12.9477 11.4477 12.5 12 12.5C12.5523 12.5 13 12.9477 13 13.5C13 14.0523 12.5523 14.5 12 14.5Z" fill="currentColor"/></svg>',
        'pulse': '<svg viewBox="0 0 24 24"><path d="M9 7.53861L15 21.5386L18.6594 13H23V11H17.3406L15 16.4614L9 2.46143L5.3406 11H1V13H6.6594L9 7.53861Z" fill="currentColor"/></svg>',
        'pushpin-2-fill': '<svg viewBox="0 0 24 24"><path d="M18 3V5H17V11L19 14V16H13V23H11V16H5V14L7 11V5H6V3H18Z" fill="currentColor"/></svg>',
        'pushpin-2': '<svg viewBox="0 0 24 24"><path d="M18 3V5H17V11L19 14V16H13V23H11V16H5V14L7 11V5H6V3H18ZM9 5V11.6056L7.4037 14H16.5963L15 11.6056V5H9Z" fill="currentColor"/></svg>',
        'pushpin': '<svg viewBox="0 0 24 24"><path d="M13.8273 1.69L22.3126 10.1753L20.8984 11.5895L20.1913 10.8824L15.9486 15.125L15.2415 18.6606L13.8273 20.0748L9.58466 15.8321L4.63492 20.7819L3.2207 19.3677L8.17045 14.4179L3.92781 10.1753L5.34202 8.76107L8.87756 8.05396L13.1202 3.81132L12.4131 3.10422L13.8273 1.69ZM14.5344 5.22554L9.86358 9.89637L7.0417 10.4607L13.5418 16.9609L14.1062 14.139L18.7771 9.46818L14.5344 5.22554Z" fill="currentColor"/></svg>',
        'question': '<svg viewBox="0 0 24 24"><path d="M12 22C6.47715 22 2 17.5228 2 12C2 6.47715 6.47715 2 12 2C17.5228 2 22 6.47715 22 12C22 17.5228 17.5228 22 12 22ZM12 20C16.4183 20 20 16.4183 20 12C20 7.58172 16.4183 4 12 4C7.58172 4 4 7.58172 4 12C4 16.4183 7.58172 20 12 20ZM11 15H13V17H11V15ZM13 13.3551V14H11V12.5C11 11.9477 11.4477 11.5 12 11.5C12.8284 11.5 13.5 10.8284 13.5 10C13.5 9.17157 12.8284 8.5 12 8.5C11.2723 8.5 10.6656 9.01823 10.5288 9.70577L8.56731 9.31346C8.88637 7.70919 10.302 6.5 12 6.5C13.933 6.5 15.5 8.067 15.5 10C15.5 11.5855 14.4457 12.9248 13 13.3551Z" fill="currentColor"/></svg>',
        'record-circle': '<svg viewBox="0 0 24 24"><path d="M12 22C6.47715 22 2 17.5228 2 12C2 6.47715 6.47715 2 12 2C17.5228 2 22 6.47715 22 12C22 17.5228 17.5228 22 12 22ZM12 20C16.4183 20 20 16.4183 20 12C20 7.58172 16.4183 4 12 4C7.58172 4 4 7.58172 4 12C4 16.4183 7.58172 20 12 20ZM12 15C10.3431 15 9 13.6569 9 12C9 10.3431 10.3431 9 12 9C13.6569 9 15 10.3431 15 12C15 13.6569 13.6569 15 12 15Z" fill="currentColor"/></svg>',
        'refresh': '<svg viewBox="0 0 24 24"><path d="M5.46257 4.43262C7.21556 2.91688 9.5007 2 12 2C17.5228 2 22 6.47715 22 12C22 14.1361 21.3302 16.1158 20.1892 17.7406L17 12H20C20 7.58172 16.4183 4 12 4C9.84982 4 7.89777 4.84827 6.46023 6.22842L5.46257 4.43262ZM18.5374 19.5674C16.7844 21.0831 14.4993 22 12 22C6.47715 22 2 17.5228 2 12C2 9.86386 2.66979 7.88416 3.8108 6.25944L7 12H4C4 16.4183 7.58172 20 12 20C14.1502 20 16.1022 19.1517 17.5398 17.7716L18.5374 19.5674Z" fill="currentColor"/></svg>',
        'restart': '<svg viewBox="0 0 24 24"><path d="M18.5374 19.5674C16.7844 21.0831 14.4993 22 12 22C6.47715 22 2 17.5228 2 12C2 6.47715 6.47715 2 12 2C17.5228 2 22 6.47715 22 12C22 14.1361 21.3302 16.1158 20.1892 17.7406L17 12H20C20 7.58172 16.4183 4 12 4C7.58172 4 4 7.58172 4 12C4 16.4183 7.58172 20 12 20C14.1502 20 16.1022 19.1517 17.5398 17.7716L18.5374 19.5674Z" fill="currentColor"/></svg>',
        'loop': '<svg viewBox="0 0 24 24"><path d="M12 4C14.7486 4 17.1749 5.38626 18.6156 7.5H16V9.5H22V3.5H20V5.99936C18.1762 3.57166 15.2724 2 12 2C6.47715 2 2 6.47715 2 12H4C4 7.58172 7.58172 4 12 4ZM20 12C20 16.4183 16.4183 20 12 20C9.25144 20 6.82508 18.6137 5.38443 16.5H8V14.5H2V20.5H4V18.0006C5.82381 20.4283 8.72764 22 12 22C17.5228 22 22 17.5228 22 12H20Z" fill="currentColor"/></svg>',
        'robot-2': '<svg viewBox="0 0 24 24"><path d="M13.5 2C13.5 2.44425 13.3069 2.84339 13 3.11805V5H18C19.6569 5 21 6.34315 21 8V18C21 19.6569 19.6569 21 18 21H6C4.34315 21 3 19.6569 3 18V8C3 6.34315 4.34315 5 6 5H11V3.11805C10.6931 2.84339 10.5 2.44425 10.5 2C10.5 1.17157 11.1716 0.5 12 0.5C12.8284 0.5 13.5 1.17157 13.5 2ZM6 7C5.44772 7 5 7.44772 5 8V18C5 18.5523 5.44772 19 6 19H18C18.5523 19 19 18.5523 19 18V8C19 7.44772 18.5523 7 18 7H13H11H6ZM2 10H0V16H2V10ZM22 10H24V16H22V10ZM9 14.5C9.82843 14.5 10.5 13.8284 10.5 13C10.5 12.1716 9.82843 11.5 9 11.5C8.17157 11.5 7.5 12.1716 7.5 13C7.5 13.8284 8.17157 14.5 9 14.5ZM15 14.5C15.8284 14.5 16.5 13.8284 16.5 13C16.5 12.1716 15.8284 11.5 15 11.5C14.1716 11.5 13.5 12.1716 13.5 13C13.5 13.8284 14.1716 14.5 15 14.5Z" fill="currentColor"/></svg>',
        'robot': '<svg viewBox="0 0 24 24"><path d="M13 4.05493C17.5 4.55237 21 8.36745 21 13V22H3V13C3 8.36745 6.50005 4.55237 11 4.05493V1H13V4.05493ZM19 20V13C19 9.13401 15.866 6 12 6C8.13401 6 5 9.13401 5 13V20H19ZM12 18C9.23858 18 7 15.7614 7 13C7 10.2386 9.23858 8 12 8C14.7614 8 17 10.2386 17 13C17 15.7614 14.7614 18 12 18ZM12 16C13.6569 16 15 14.6569 15 13C15 11.3431 13.6569 10 12 10C10.3431 10 9 11.3431 9 13C9 14.6569 10.3431 16 12 16ZM12 14C11.4477 14 11 13.5523 11 13C11 12.4477 11.4477 12 12 12C12.5523 12 13 12.4477 13 13C13 13.5523 12.5523 14 12 14Z" fill="currentColor"/></svg>',
        'rocket': '<svg viewBox="0 0 24 24"><path d="M4.99958 12.9999C4.99958 7.91198 7.90222 3.5636 11.9996 1.81799C16.0969 3.5636 18.9996 7.91198 18.9996 12.9999C18.9996 13.8229 18.9236 14.6264 18.779 15.4027L20.7194 17.2353C20.8845 17.3913 20.9238 17.6389 20.815 17.8383L18.3196 22.4133C18.1873 22.6557 17.8836 22.7451 17.6412 22.6128C17.5993 22.59 17.5608 22.5612 17.5271 22.5274L15.2925 20.2928C15.1049 20.1053 14.8506 19.9999 14.5854 19.9999H9.41379C9.14857 19.9999 8.89422 20.1053 8.70668 20.2928L6.47209 22.5274C6.27683 22.7227 5.96025 22.7227 5.76498 22.5274C5.73122 22.4937 5.70246 22.4552 5.67959 22.4133L3.18412 17.8383C3.07537 17.6389 3.11464 17.3913 3.27975 17.2353L5.22014 15.4027C5.07551 14.6264 4.99958 13.8229 4.99958 12.9999ZM6.47542 19.6957L7.29247 18.8786C7.85508 18.316 8.61814 17.9999 9.41379 17.9999H14.5854C15.381 17.9999 16.1441 18.316 16.7067 18.8786L17.5237 19.6957L18.5056 17.8955L17.4058 16.8568C16.9117 16.3901 16.6884 15.7045 16.8128 15.0364C16.9366 14.3722 16.9996 13.6911 16.9996 12.9999C16.9996 9.13037 15.0045 5.69965 11.9996 4.04033C8.99462 5.69965 6.99958 9.13037 6.99958 12.9999C6.99958 13.6911 7.06255 14.3722 7.18631 15.0364C7.31078 15.7045 7.08746 16.3901 6.59338 16.8568L5.49353 17.8955L6.47542 19.6957ZM11.9996 12.9999C10.895 12.9999 9.99958 12.1045 9.99958 10.9999C9.99958 9.89537 10.895 8.99994 11.9996 8.99994C13.1041 8.99994 13.9996 9.89537 13.9996 10.9999C13.9996 12.1045 13.1041 12.9999 11.9996 12.9999Z" fill="currentColor"/></svg>',
        'save-3': '<svg viewBox="0 0 24 24"><path d="M18 19H19V6.82843L17.1716 5H16V9H7V5H5V19H6V12H18V19ZM4 3H18L20.7071 5.70711C20.8946 5.89464 21 6.149 21 6.41421V20C21 20.5523 20.5523 21 20 21H4C3.44772 21 3 20.5523 3 20V4C3 3.44772 3.44772 3 4 3ZM8 14V19H16V14H8Z" fill="currentColor"/></svg>',
        'scissors': '<svg viewBox="0 0 24 24"><path d="M9.44618 8.02867L12 10.5825L18.7279 3.85457C19.509 3.07352 20.7753 3.07352 21.5563 3.85457L9.44618 15.9647C9.79807 16.5603 10 17.2549 10 17.9967C10 20.2058 8.20914 21.9967 6 21.9967C3.79086 21.9967 2 20.2058 2 17.9967C2 15.7876 3.79086 13.9967 6 13.9967C6.74181 13.9967 7.43645 14.1986 8.03197 14.5505L10.5858 11.9967L8.03197 9.44289C7.43645 9.79478 6.74181 9.9967 6 9.9967C3.79086 9.9967 2 8.20584 2 5.9967C2 3.78756 3.79086 1.9967 6 1.9967C8.20914 1.9967 10 3.78756 10 5.9967C10 6.73851 9.79807 7.43316 9.44618 8.02867ZM14.8255 13.408L21.5563 20.1388C20.7753 20.9199 19.509 20.9199 18.7279 20.1388L13.4113 14.8222L14.8255 13.408ZM7.41421 16.5825C7.05228 16.2206 6.55228 15.9967 6 15.9967C4.89543 15.9967 4 16.8921 4 17.9967C4 19.1013 4.89543 19.9967 6 19.9967C7.10457 19.9967 8 19.1013 8 17.9967C8 17.4444 7.77614 16.9444 7.41421 16.5825ZM7.41421 7.41092C7.77614 7.04899 8 6.54899 8 5.9967C8 4.89213 7.10457 3.9967 6 3.9967C4.89543 3.9967 4 4.89213 4 5.9967C4 7.10127 4.89543 7.9967 6 7.9967C6.55228 7.9967 7.05228 7.77285 7.41421 7.41092Z" fill="currentColor"/></svg>',
        'search-eye': '<svg viewBox="0 0 24 24"><path d="M18.031 16.6168L22.3137 20.8995L20.8995 22.3137L16.6168 18.031C15.0769 19.263 13.124 20 11 20C6.032 20 2 15.968 2 11C2 6.032 6.032 2 11 2C15.968 2 20 6.032 20 11C20 13.124 19.263 15.0769 18.031 16.6168ZM16.0247 15.8748C17.2475 14.6146 18 12.8956 18 11C18 7.1325 14.8675 4 11 4C7.1325 4 4 7.1325 4 11C4 14.8675 7.1325 18 11 18C12.8956 18 14.6146 17.2475 15.8748 16.0247L16.0247 15.8748ZM12.1779 7.17624C11.4834 7.48982 11 8.18846 11 9C11 10.1046 11.8954 11 13 11C13.8115 11 14.5102 10.5166 14.8238 9.82212C14.9383 10.1945 15 10.59 15 11C15 13.2091 13.2091 15 11 15C8.79086 15 7 13.2091 7 11C7 8.79086 8.79086 7 11 7C11.41 7 11.8055 7.06167 12.1779 7.17624Z" fill="currentColor"/></svg>',
        'search': '<svg viewBox="0 0 24 24"><path d="M18.031 16.6168L22.3137 20.8995L20.8995 22.3137L16.6168 18.031C15.0769 19.263 13.124 20 11 20C6.032 20 2 15.968 2 11C2 6.032 6.032 2 11 2C15.968 2 20 6.032 20 11C20 13.124 19.263 15.0769 18.031 16.6168ZM16.0247 15.8748C17.2475 14.6146 18 12.8956 18 11C18 7.1325 14.8675 4 11 4C7.1325 4 4 7.1325 4 11C4 14.8675 7.1325 18 11 18C12.8956 18 14.6146 17.2475 15.8748 16.0247L16.0247 15.8748Z" fill="currentColor"/></svg>',
        'send-plane-2': '<svg viewBox="0 0 24 24"><path d="M3.5 1.34558C3.58425 1.34558 3.66714 1.36687 3.74096 1.40747L22.2034 11.5618C22.4454 11.6949 22.5337 11.9989 22.4006 12.2409C22.3549 12.324 22.2865 12.3924 22.2034 12.4381L3.74096 22.5924C3.499 22.7255 3.19497 22.6372 3.06189 22.3953C3.02129 22.3214 3 22.2386 3 22.1543V1.84558C3 1.56944 3.22386 1.34558 3.5 1.34558ZM5 4.38249V10.9999H10V12.9999H5V19.6174L18.8499 11.9999L5 4.38249Z" fill="currentColor"/></svg>',
        'send-plane': '<svg viewBox="0 0 24 24"><path d="M21.7267 2.95694L16.2734 22.0432C16.1225 22.5716 15.7979 22.5956 15.5563 22.1126L11 13L1.9229 9.36919C1.41322 9.16532 1.41953 8.86022 1.95695 8.68108L21.0432 2.31901C21.5716 2.14285 21.8747 2.43866 21.7267 2.95694ZM19.0353 5.09647L6.81221 9.17085L12.4488 11.4255L15.4895 17.5068L19.0353 5.09647Z" fill="currentColor"/></svg>',
        'server': '<svg viewBox="0 0 24 24"><path d="M5 11H19V5H5V11ZM21 4V20C21 20.5523 20.5523 21 20 21H4C3.44772 21 3 20.5523 3 20V4C3 3.44772 3.44772 3 4 3H20C20.5523 3 21 3.44772 21 4ZM19 13H5V19H19V13ZM7 15H10V17H7V15ZM7 7H10V9H7V7Z" fill="currentColor"/></svg>',
        'settings-3': '<svg viewBox="0 0 24 24"><path d="M3.33946 17.0002C2.90721 16.2515 2.58277 15.4702 2.36133 14.6741C3.3338 14.1779 3.99972 13.1668 3.99972 12.0002C3.99972 10.8345 3.3348 9.824 2.36353 9.32741C2.81025 7.71651 3.65857 6.21627 4.86474 4.99001C5.7807 5.58416 6.98935 5.65534 7.99972 5.072C9.01009 4.48866 9.55277 3.40635 9.4962 2.31604C11.1613 1.8846 12.8847 1.90004 14.5031 2.31862C14.4475 3.40806 14.9901 4.48912 15.9997 5.072C17.0101 5.65532 18.2187 5.58416 19.1346 4.99007C19.7133 5.57986 20.2277 6.25151 20.66 7.00021C21.0922 7.7489 21.4167 8.53025 21.6381 9.32628C20.6656 9.82247 19.9997 10.8336 19.9997 12.0002C19.9997 13.166 20.6646 14.1764 21.6359 14.673C21.1892 16.2839 20.3409 17.7841 19.1347 19.0104C18.2187 18.4163 17.0101 18.3451 15.9997 18.9284C14.9893 19.5117 14.4467 20.5941 14.5032 21.6844C12.8382 22.1158 11.1148 22.1004 9.49633 21.6818C9.55191 20.5923 9.00929 19.5113 7.99972 18.9284C6.98938 18.3451 5.78079 18.4162 4.86484 19.0103C4.28617 18.4205 3.77172 17.7489 3.33946 17.0002ZM8.99972 17.1964C10.0911 17.8265 10.8749 18.8227 11.2503 19.9659C11.7486 20.0133 12.2502 20.014 12.7486 19.9675C13.1238 18.8237 13.9078 17.8268 14.9997 17.1964C16.0916 16.5659 17.347 16.3855 18.5252 16.6324C18.8146 16.224 19.0648 15.7892 19.2729 15.334C18.4706 14.4373 17.9997 13.2604 17.9997 12.0002C17.9997 10.74 18.4706 9.5632 19.2729 8.6665C19.1688 8.4405 19.0538 8.21822 18.9279 8.00021C18.802 7.78219 18.667 7.57148 18.5233 7.36842C17.3457 7.61476 16.0911 7.43414 14.9997 6.80405C13.9083 6.17395 13.1246 5.17768 12.7491 4.03455C12.2509 3.98714 11.7492 3.98646 11.2509 4.03292C10.8756 5.17671 10.0916 6.17364 8.99972 6.80405C7.9078 7.43447 6.65245 7.61494 5.47428 7.36803C5.18485 7.77641 4.93463 8.21117 4.72656 8.66637C5.52881 9.56311 5.99972 10.74 5.99972 12.0002C5.99972 13.2604 5.52883 14.4372 4.72656 15.3339C4.83067 15.5599 4.94564 15.7822 5.07152 16.0002C5.19739 16.2182 5.3324 16.4289 5.47612 16.632C6.65377 16.3857 7.90838 16.5663 8.99972 17.1964ZM11.9997 15.0002C10.3429 15.0002 8.99972 13.6571 8.99972 12.0002C8.99972 10.3434 10.3429 9.00021 11.9997 9.00021C13.6566 9.00021 14.9997 10.3434 14.9997 12.0002C14.9997 13.6571 13.6566 15.0002 11.9997 15.0002ZM11.9997 13.0002C12.552 13.0002 12.9997 12.5525 12.9997 12.0002C12.9997 11.4479 12.552 11.0002 11.9997 11.0002C11.4474 11.0002 10.9997 11.4479 10.9997 12.0002C10.9997 12.5525 11.4474 13.0002 11.9997 13.0002Z" fill="currentColor"/></svg>',
        'share-2': '<svg viewBox="0 0 24 24"><path d="M12 2.58582L18.2071 8.79292L16.7929 10.2071L13 6.41424V16H11V6.41424L7.20711 10.2071L5.79289 8.79292L12 2.58582ZM3 18V14H5V18C5 18.5523 5.44772 19 6 19H18C18.5523 19 19 18.5523 19 18V14H21V18C21 19.6569 19.6569 21 18 21H6C4.34315 21 3 19.6569 3 18Z" fill="currentColor"/></svg>',
        'shield-check': '<svg viewBox="0 0 24 24"><path d="M12 1L20.2169 2.82598C20.6745 2.92766 21 3.33347 21 3.80217V13.7889C21 15.795 19.9974 17.6684 18.3282 18.7812L12 23L5.6718 18.7812C4.00261 17.6684 3 15.795 3 13.7889V3.80217C3 3.33347 3.32553 2.92766 3.78307 2.82598L12 1ZM12 3.04879L5 4.60434V13.7889C5 15.1263 5.6684 16.3752 6.7812 17.1171L12 20.5963L17.2188 17.1171C18.3316 16.3752 19 15.1263 19 13.7889V4.60434L12 3.04879ZM16.4524 8.22183L17.8666 9.63604L11.5026 16L7.25999 11.7574L8.67421 10.3431L11.5019 13.1709L16.4524 8.22183Z" fill="currentColor"/></svg>',
        'shield-keyhole': '<svg viewBox="0 0 24 24"><path d="M12 1L20.2169 2.82598C20.6745 2.92766 21 3.33347 21 3.80217V13.7889C21 15.795 19.9974 17.6684 18.3282 18.7812L12 23L5.6718 18.7812C4.00261 17.6684 3 15.795 3 13.7889V3.80217C3 3.33347 3.32553 2.92766 3.78307 2.82598L12 1ZM12 3.04879L5 4.60434V13.7889C5 15.1263 5.6684 16.3752 6.7812 17.1171L12 20.5963L17.2188 17.1171C18.3316 16.3752 19 15.1263 19 13.7889V4.60434L12 3.04879ZM12 7C13.1046 7 14 7.89543 14 9C14 9.73984 13.5983 10.3858 13.0011 10.7318L13 15H11L10.9999 10.7324C10.4022 10.3866 10 9.74025 10 9C10 7.89543 10.8954 7 12 7Z" fill="currentColor"/></svg>',
        'shield': '<svg viewBox="0 0 24 24"><path d="M3.78307 2.82598L12 1L20.2169 2.82598C20.6745 2.92766 21 3.33347 21 3.80217V13.7889C21 15.795 19.9974 17.6684 18.3282 18.7812L12 23L5.6718 18.7812C4.00261 17.6684 3 15.795 3 13.7889V3.80217C3 3.33347 3.32553 2.92766 3.78307 2.82598ZM5 4.60434V13.7889C5 15.1263 5.6684 16.3752 6.7812 17.1171L12 20.5963L17.2188 17.1171C18.3316 16.3752 19 15.1263 19 13.7889V4.60434L12 3.04879L5 4.60434Z" fill="currentColor"/></svg>',
        'shield-user': '<svg viewBox="0 0 24 24"><path d="M3.78307 2.82598L12 1L20.2169 2.82598C20.6745 2.92766 21 3.33347 21 3.80217V13.7889C21 15.795 19.9974 17.6684 18.3282 18.7812L12 23L5.6718 18.7812C4.00261 17.6684 3 15.795 3 13.7889V3.80217C3 3.33347 3.32553 2.92766 3.78307 2.82598ZM5 4.60434V13.7889C5 15.1263 5.6684 16.3752 6.7812 17.1171L12 20.5963L17.2188 17.1171C18.3316 16.3752 19 15.1263 19 13.7889V4.60434L12 3.04879L5 4.60434ZM12 11C10.6193 11 9.5 9.88071 9.5 8.5C9.5 7.11929 10.6193 6 12 6C13.3807 6 14.5 7.11929 14.5 8.5C14.5 9.88071 13.3807 11 12 11ZM7.52746 16C7.77619 13.75 9.68372 12 12 12C14.3163 12 16.2238 13.75 16.4725 16H7.52746Z" fill="currentColor"/></svg>',
        'shuffle': '<svg viewBox="0 0 24 24"><path d="M18 17.8832V16L23 19L18 22V19.9095C14.9224 19.4698 12.2513 17.4584 11.0029 14.5453L11 14.5386L10.9971 14.5453C9.57893 17.8544 6.32508 20 2.72483 20H2V18H2.72483C5.52503 18 8.05579 16.3312 9.15885 13.7574L9.91203 12L9.15885 10.2426C8.05579 7.66878 5.52503 6 2.72483 6H2V4H2.72483C6.32508 4 9.57893 6.14557 10.9971 9.45473L11 9.46141L11.0029 9.45473C12.2513 6.5416 14.9224 4.53022 18 4.09051V2L23 5L18 8V6.11684C15.7266 6.53763 13.7737 8.0667 12.8412 10.2426L12.088 12L12.8412 13.7574C13.7737 15.9333 15.7266 17.4624 18 17.8832Z" fill="currentColor"/></svg>',
        'slash-commands-2': '<svg viewBox="0 0 24 24"><path d="M5 2C3.34315 2 2 3.34315 2 5V19C2 20.6569 3.34315 22 5 22H19C20.6569 22 22 20.6569 22 19V5C22 3.34315 20.6569 2 19 2H5ZM4 5C4 4.44772 4.44772 4 5 4H19C19.5523 4 20 4.44772 20 5V19C20 19.5523 19.5523 20 19 20H5C4.44772 20 4 19.5523 4 19V5ZM9.72318 18L16.5803 6H14.2768L7.41968 18H9.72318Z" fill="currentColor"/></svg>',
        'smartphone': '<svg viewBox="0 0 24 24"><path d="M7 4V20H17V4H7ZM6 2H18C18.5523 2 19 2.44772 19 3V21C19 21.5523 18.5523 22 18 22H6C5.44772 22 5 21.5523 5 21V3C5 2.44772 5.44772 2 6 2ZM12 17C12.5523 17 13 17.4477 13 18C13 18.5523 12.5523 19 12 19C11.4477 19 11 18.5523 11 18C11 17.4477 11.4477 17 12 17Z" fill="currentColor"/></svg>',
        'sparkling': '<svg viewBox="0 0 24 24"><path d="M14 4.4375C15.3462 4.4375 16.4375 3.34619 16.4375 2H17.5625C17.5625 3.34619 18.6538 4.4375 20 4.4375V5.5625C18.6538 5.5625 17.5625 6.65381 17.5625 8H16.4375C16.4375 6.65381 15.3462 5.5625 14 5.5625V4.4375ZM1 11C4.31371 11 7 8.31371 7 5H9C9 8.31371 11.6863 11 15 11V13C11.6863 13 9 15.6863 9 19H7C7 15.6863 4.31371 13 1 13V11ZM4.87601 12C6.18717 12.7276 7.27243 13.8128 8 15.124 8.72757 13.8128 9.81283 12.7276 11.124 12 9.81283 11.2724 8.72757 10.1872 8 8.87601 7.27243 10.1872 6.18717 11.2724 4.87601 12ZM17.25 14C17.25 15.7949 15.7949 17.25 14 17.25V18.75C15.7949 18.75 17.25 20.2051 17.25 22H18.75C18.75 20.2051 20.2051 18.75 22 18.75V17.25C20.2051 17.25 18.75 15.7949 18.75 14H17.25Z" fill="currentColor"/></svg>',
        'split-cells-horizontal': '<svg viewBox="0 0 24 24"><path d="M20 3C20.5523 3 21 3.44772 21 4V20C21 20.5523 20.5523 21 20 21H4C3.44772 21 3 20.5523 3 20V4C3 3.44772 3.44772 3 4 3H20ZM11 5H5V19H11V15H13V19H19V5H13V9H11V5ZM15 9L18 12L15 15V13H9V15L6 12L9 9V11H15V9Z" fill="currentColor"/></svg>',
        'stack': '<svg viewBox="0 0 24 24"><path d="M20.0833 15.1999L21.2854 15.9212C21.5221 16.0633 21.5989 16.3704 21.4569 16.6072C21.4146 16.6776 21.3557 16.7365 21.2854 16.7787L12.5144 22.0412C12.1977 22.2313 11.8021 22.2313 11.4854 22.0412L2.71451 16.7787C2.47772 16.6366 2.40093 16.3295 2.54301 16.0927C2.58523 16.0223 2.64413 15.9634 2.71451 15.9212L3.9166 15.1999L11.9999 20.0499L20.0833 15.1999ZM20.0833 10.4999L21.2854 11.2212C21.5221 11.3633 21.5989 11.6704 21.4569 11.9072C21.4146 11.9776 21.3557 12.0365 21.2854 12.0787L11.9999 17.6499L2.71451 12.0787C2.47772 11.9366 2.40093 11.6295 2.54301 11.3927C2.58523 11.3223 2.64413 11.2634 2.71451 11.2212L3.9166 10.4999L11.9999 15.3499L20.0833 10.4999ZM12.5144 1.30864L21.2854 6.5712C21.5221 6.71327 21.5989 7.0204 21.4569 7.25719C21.4146 7.32757 21.3557 7.38647 21.2854 7.42869L11.9999 12.9999L2.71451 7.42869C2.47772 7.28662 2.40093 6.97949 2.54301 6.7427C2.58523 6.67232 2.64413 6.61343 2.71451 6.5712L11.4854 1.30864C11.8021 1.11864 12.1977 1.11864 12.5144 1.30864ZM11.9999 3.33233L5.88723 6.99995L11.9999 10.6676L18.1126 6.99995L11.9999 3.33233Z" fill="currentColor"/></svg>',
        'star-fill': '<svg viewBox="0 0 24 24"><path d="M12.0006 18.26L4.94715 22.2082L6.52248 14.2799L0.587891 8.7918L8.61493 7.84006L12.0006 0.5L15.3862 7.84006L23.4132 8.7918L17.4787 14.2799L19.054 22.2082L12.0006 18.26Z" fill="currentColor"/></svg>',
        'star': '<svg viewBox="0 0 24 24"><path d="M12.0006 18.26L4.94715 22.2082L6.52248 14.2799L0.587891 8.7918L8.61493 7.84006L12.0006 0.5L15.3862 7.84006L23.4132 8.7918L17.4787 14.2799L19.054 22.2082L12.0006 18.26ZM12.0006 15.968L16.2473 18.3451L15.2988 13.5717L18.8719 10.2674L14.039 9.69434L12.0006 5.27502L9.96214 9.69434L5.12921 10.2674L8.70231 13.5717L7.75383 18.3451L12.0006 15.968Z" fill="currentColor"/></svg>',
        'sticky-note': '<svg viewBox="0 0 24 24"><path d="M21 15L15 20.996L4.00221 21C3.4487 21 3 20.5551 3 20.0066V3.9934C3 3.44476 3.44495 3 3.9934 3H20.0066C20.5552 3 21 3.45576 21 4.00247V15ZM19 5H5V19H13V14C13 13.4872 13.386 13.0645 13.8834 13.0067L14 13L19 12.999V5ZM18.171 14.999L15 15V18.169L18.171 14.999Z" fill="currentColor"/></svg>',
        'stop-circle': '<svg viewBox="0 0 24 24"><path d="M12 22C6.47715 22 2 17.5228 2 12C2 6.47715 6.47715 2 12 2C17.5228 2 22 6.47715 22 12C22 17.5228 17.5228 22 12 22ZM12 20C16.4183 20 20 16.4183 20 12C20 7.58172 16.4183 4 12 4C7.58172 4 4 7.58172 4 12C4 16.4183 7.58172 20 12 20ZM9 9H15V15H9V9Z" fill="currentColor"/></svg>',
        'stop': '<svg viewBox="0 0 24 24"><path d="M7 7V17H17V7H7ZM6 5H18C18.5523 5 19 5.44772 19 6V18C19 18.5523 18.5523 19 18 19H6C5.44772 19 5 18.5523 5 18V6C5 5.44772 5.44772 5 6 5Z" fill="currentColor"/></svg>',
        'subtract': '<svg viewBox="0 0 24 24"><path d="M5 11V13H19V11H5Z" fill="currentColor"/></svg>',
        'survey': '<svg viewBox="0 0 24 24"><path d="M17 2V4H20.0066C20.5552 4 21 4.44495 21 4.9934V21.0066C21 21.5552 20.5551 22 20.0066 22H3.9934C3.44476 22 3 21.5551 3 21.0066V4.9934C3 4.44476 3.44495 4 3.9934 4H7V2H17ZM7 6H5V20H19V6H17V8H7V6ZM9 16V18H7V16H9ZM9 13V15H7V13H9ZM9 10V12H7V10H9ZM15 4H9V6H15V4Z" fill="currentColor"/></svg>',
        'task': '<svg viewBox="0 0 24 24"><path d="M19 4H5V20H19V4ZM3 2.9918C3 2.44405 3.44749 2 3.9985 2H19.9997C20.5519 2 20.9996 2.44772 20.9997 3L21 20.9925C21 21.5489 20.5551 22 20.0066 22H3.9934C3.44476 22 3 21.5447 3 21.0082V2.9918ZM11.2929 13.1213L15.5355 8.87868L16.9497 10.2929L11.2929 15.9497L7.40381 12.0607L8.81802 10.6464L11.2929 13.1213Z" fill="currentColor"/></svg>',
        'terminal-box': '<svg viewBox="0 0 24 24"><path d="M3 3H21C21.5523 3 22 3.44772 22 4V20C22 20.5523 21.5523 21 21 21H3C2.44772 21 2 20.5523 2 20V4C2 3.44772 2.44772 3 3 3ZM4 5V19H20V5H4ZM12 15H18V17H12V15ZM8.66685 12L5.83842 9.17157L7.25264 7.75736L11.4953 12L7.25264 16.2426L5.83842 14.8284L8.66685 12Z" fill="currentColor"/></svg>',
        'terminal': '<svg viewBox="0 0 24 24"><path d="M10.9999 12L3.92886 19.0711L2.51465 17.6569L8.1715 12L2.51465 6.34317L3.92886 4.92896L10.9999 12ZM10.9999 19H20.9999V21H10.9999V19Z" fill="currentColor"/></svg>',
        'terminal-window': '<svg viewBox="0 0 24 24"><path d="M20 9V5H4V9H20ZM20 11H4V19H20V11ZM3 3H21C21.5523 3 22 3.44772 22 4V20C22 20.5523 21.5523 21 21 21H3C2.44772 21 2 20.5523 2 20V4C2 3.44772 2.44772 3 3 3ZM5 12H8V17H5V12ZM5 6H7V8H5V6ZM9 6H11V8H9V6Z" fill="currentColor"/></svg>',
        'text': '<svg viewBox="0 0 24 24"><path d="M13 6V21H11V6H5V4H19V6H13Z" fill="currentColor"/></svg>',
        'text-wrap': '<svg viewBox="0 0 24 24"><path d="M15 18H16.5C17.8807 18 19 16.8807 19 15.5C19 14.1193 17.8807 13 16.5 13H3V11H16.5C18.9853 11 21 13.0147 21 15.5C21 17.9853 18.9853 20 16.5 20H15V22L11 19L15 16V18ZM3 4H21V6H3V4ZM9 18V20H3V18H9Z" fill="currentColor"/></svg>',
        'time': '<svg viewBox="0 0 24 24"><path d="M12 22C6.47715 22 2 17.5228 2 12C2 6.47715 6.47715 2 12 2C17.5228 2 22 6.47715 22 12C22 17.5228 17.5228 22 12 22ZM12 20C16.4183 20 20 16.4183 20 12C20 7.58172 16.4183 4 12 4C7.58172 4 4 7.58172 4 12C4 16.4183 7.58172 20 12 20ZM13 12H17V14H11V7H13V12Z" fill="currentColor"/></svg>',
        'timer': '<svg viewBox="0 0 24 24"><path d="M17.6177 5.9681L19.0711 4.51472L20.4853 5.92893L19.0319 7.38231C20.2635 8.92199 21 10.875 21 13C21 17.9706 16.9706 22 12 22C7.02944 22 3 17.9706 3 13C3 8.02944 7.02944 4 12 4C14.125 4 16.078 4.73647 17.6177 5.9681ZM12 20C15.866 20 19 16.866 19 13C19 9.13401 15.866 6 12 6C8.13401 6 5 9.13401 5 13C5 16.866 8.13401 20 12 20ZM11 8H13V14H11V8ZM8 1H16V3H8V1Z" fill="currentColor"/></svg>',
        'tools': '<svg viewBox="0 0 24 24"><path d="M5.32943 3.27158C6.56252 2.8332 7.9923 3.10749 8.97927 4.09446C10.1002 5.21537 10.3019 6.90741 9.5843 8.23385L20.293 18.9437L18.8788 20.3579L8.16982 9.64875C6.84325 10.3669 5.15069 10.1654 4.02952 9.04421C3.04227 8.05696 2.7681 6.62665 3.20701 5.39332L5.44373 7.63C6.02952 8.21578 6.97927 8.21578 7.56505 7.63C8.15084 7.04421 8.15084 6.09446 7.56505 5.50868L5.32943 3.27158ZM15.6968 5.15512L18.8788 3.38736L20.293 4.80157L18.5252 7.98355L16.7574 8.3371L14.6361 10.4584L13.2219 9.04421L15.3432 6.92289L15.6968 5.15512ZM8.97927 13.2868L10.3935 14.7011L5.09018 20.0044C4.69966 20.3949 4.06649 20.3949 3.67597 20.0044C3.31334 19.6417 3.28744 19.0699 3.59826 18.6774L3.67597 18.5902L8.97927 13.2868Z" fill="currentColor"/></svg>',
        'twitter-xfill': '<svg viewBox="0 0 24 24"><path d="M17.6874 3.0625L12.6907 8.77425L8.37045 3.0625H2.11328L9.58961 12.8387L2.50378 20.9375H5.53795L11.0068 14.6886L15.7863 20.9375H21.8885L14.095 10.6342L20.7198 3.0625H17.6874ZM16.6232 19.1225L5.65436 4.78217H7.45745L18.3034 19.1225H16.6232Z" fill="currentColor"/></svg>',
        'unpin': '<svg viewBox="0 0 24 24"><path d="M20.9701 17.1716 19.5559 18.5858 16.0214 15.0513 15.9476 15.1251 15.2405 18.6606 13.8263 20.0748 9.58369 15.8322 4.63394 20.7819 3.21973 19.3677 8.16947 14.418 3.92683 10.1753 5.34105 8.7611 8.87658 8.05399 8.95029 7.98028 5.41373 4.44371 6.82794 3.0295 20.9701 17.1716ZM10.3645 9.39449 9.86261 9.8964 7.04072 10.4608 13.5409 16.9609 14.1052 14.139 14.6071 13.6371 10.3645 9.39449ZM18.7761 9.46821 17.4356 10.8087 18.8498 12.2229 20.1903 10.8824 20.8974 11.5895 22.3116 10.1753 13.8263 1.69003 12.4121 3.10425 13.1192 3.81135 11.7787 5.15185 13.1929 6.56607 14.5334 5.22557 18.7761 9.46821Z" fill="currentColor"/></svg>',
        'user-3': '<svg viewBox="0 0 24 24"><path d="M20 22H18V20C18 18.3431 16.6569 17 15 17H9C7.34315 17 6 18.3431 6 20V22H4V20C4 17.2386 6.23858 15 9 15H15C17.7614 15 20 17.2386 20 20V22ZM12 13C8.68629 13 6 10.3137 6 7C6 3.68629 8.68629 1 12 1C15.3137 1 18 3.68629 18 7C18 10.3137 15.3137 13 12 13ZM12 11C14.2091 11 16 9.20914 16 7C16 4.79086 14.2091 3 12 3C9.79086 3 8 4.79086 8 7C8 9.20914 9.79086 11 12 11Z" fill="currentColor"/></svg>',
        'user': '<svg viewBox="0 0 24 24"><path d="M4 22C4 17.5817 7.58172 14 12 14C16.4183 14 20 17.5817 20 22H18C18 18.6863 15.3137 16 12 16C8.68629 16 6 18.6863 6 22H4ZM12 13C8.685 13 6 10.315 6 7C6 3.685 8.685 1 12 1C15.315 1 18 3.685 18 7C18 10.315 15.315 13 12 13ZM12 11C14.21 11 16 9.21 16 7C16 4.79 14.21 3 12 3C9.79 3 8 4.79 8 7C8 9.21 9.79 11 12 11Z" fill="currentColor"/></svg>',
        'voice-recognition': '<svg viewBox="0 0 24 24"><path d="M4.99805 15V19H8.99805V21H2.99805V15H4.99805ZM20.998 15V21H14.998V19H18.998V15H20.998ZM12.998 6V18H10.998V6H12.998ZM8.99805 9V15H6.99805V9H8.99805ZM16.998 9V15H14.998V9H16.998ZM8.99805 3V5H4.99805V9H2.99805V3H8.99805ZM20.998 3V9H18.998V5H14.998V3H20.998Z" fill="currentColor"/></svg>',
        'volume-up': '<svg viewBox="0 0 24 24"><path d="M6.60282 10.0001L10 7.22056V16.7796L6.60282 14.0001H3V10.0001H6.60282ZM2 16.0001H5.88889L11.1834 20.3319C11.2727 20.405 11.3846 20.4449 11.5 20.4449C11.7761 20.4449 12 20.2211 12 19.9449V4.05519C12 3.93977 11.9601 3.8279 11.887 3.73857C11.7121 3.52485 11.3971 3.49335 11.1834 3.66821L5.88889 8.00007H2C1.44772 8.00007 1 8.44778 1 9.00007V15.0001C1 15.5524 1.44772 16.0001 2 16.0001ZM23 12C23 15.292 21.5539 18.2463 19.2622 20.2622L17.8445 18.8444C19.7758 17.1937 21 14.7398 21 12C21 9.26016 19.7758 6.80629 17.8445 5.15557L19.2622 3.73779C21.5539 5.75368 23 8.70795 23 12ZM18 12C18 10.0883 17.106 8.38548 15.7133 7.28673L14.2842 8.71584C15.3213 9.43855 16 10.64 16 12C16 13.36 15.3213 14.5614 14.2842 15.2841L15.7133 16.7132C17.106 15.6145 18 13.9116 18 12Z" fill="currentColor"/></svg>',
        'window': '<svg viewBox="0 0 24 24"><path d="M21 3C21.5523 3 22 3.44772 22 4V20C22 20.5523 21.5523 21 21 21H3C2.44772 21 2 20.5523 2 20V4C2 3.44772 2.44772 3 3 3H21ZM20 11H4V19H20V11ZM20 5H4V9H20V5ZM11 6V8H9V6H11ZM7 6V8H5V6H7Z" fill="currentColor"/></svg>',
        'file-py': '<svg viewBox="0 0 256 256"><path d="M213.66,82.34l-56-56A8,8,0,0,0,152,24H56A16,16,0,0,0,40,40v72a8,8,0,0,0,16,0V40h88V88a8,8,0,0,0,8,8h48V216H168a8,8,0,0,0,0,16h32a16,16,0,0,0,16-16V88A8,8,0,0,0,213.66,82.34ZM160,51.31,188.69,80H160ZM64,144H48a8,8,0,0,0-8,8v56a8,8,0,0,0,16,0v-8h8a28,28,0,0,0,0-56Zm0,40H56V160h8a12,12,0,0,1,0,24Zm90.78-27.76-18.78,30V208a8,8,0,0,1-16,0V186.29l-18.78-30a8,8,0,1,1,13.56-8.48L128,168.91l13.22-21.15a8,8,0,1,1,13.56,8.48Z" fill="currentColor"/></svg>',
        'toggle': '<svg viewBox="0 0 24 24"><path d="M8 5C4.13401 5 1 8.13401 1 12C1 15.866 4.13401 19 8 19H16C19.866 19 23 15.866 23 12C23 8.13401 19.866 5 16 5H8ZM8 7H16C18.7614 7 21 9.23858 21 12C21 14.7614 18.7614 17 16 17H8C5.23858 17 3 14.7614 3 12C3 9.23858 5.23858 7 8 7ZM16 15C17.6569 15 19 13.6569 19 12C19 10.3431 17.6569 9 16 9C14.3431 9 13 10.3431 13 12C13 13.6569 14.3431 15 16 15Z" fill="currentColor"/></svg>',
        'stock': '<svg viewBox="0 0 24 24"><path d="M5 3V19H21V21H3V3H5ZM19.9393 5.93934L22.0607 8.06066L16 14.1213L13 11.121L8.81066 15.3107L6.68934 13.1893L13 6.87868L16 9.879L19.9393 5.93934Z" fill="currentColor"/></svg>',
    };

    const _TOOL_DISPLAY = {
        'ExecutePython': { icon: 'file-py', label: 'Python' },
        'HAControl': { icon: 'survey', label: 'HA Query' },
        'ServiceCall': { icon: 'play', label: 'Service Call' },
        'ListServices': { icon: 'list-unordered', label: 'List Services' },
        'ServiceHelp': { icon: 'question', label: 'Service Help' },
        'ValidateService': { icon: 'checkbox-circle', label: 'Validate Service' },
        'IntentCall': { icon: 'command', label: 'Intent Call' },
        'EntityQuery': { icon: 'search', label: 'Entity Query' },
        'HistoryQuery': { icon: 'time', label: 'History Query' },
        'AreaDevices': { icon: 'layout-grid', label: 'Area Devices' },
        'Registry': { icon: 'database-2', label: 'Registry' },
        'ConfigEntries': { icon: 'file', label: 'Config Entries' },
        'ConfigFile': { icon: 'file-text', label: 'Config File' },
        'SystemControl': { icon: 'restart', label: 'System Control' },
        'BootstrapControl': { icon: 'brain-ai-3', label: 'Bootstrap' },
        'ExposeEntity': { icon: 'eye', label: 'Expose Entity' },
        'Automation': { icon: 'loop', label: 'Automation' },
        'Script': { icon: 'terminal', label: 'Script' },
        'ScriptExecute': { icon: 'rocket', label: 'Script Run' },
        'BatchControl': { icon: 'checkbox-multiple', label: 'Batch Control' },
        'DashboardCard': { icon: 'palette', label: 'Dashboard Card' },
        'FrontendInspect': { icon: 'ai-generate-2', label: 'Frontend Inspect' },
        'HelperManager': { icon: 'equalizer-2', label: 'Helper Manager' },
        'CustomEntityManager': { icon: 'plug-2', label: 'Custom Entity' },
        'HeartbeatManager': { icon: 'pulse', label: 'Heartbeat' },
        'SmartDiscovery': { icon: 'search-eye', label: 'Smart Discovery' },
        'GetLiveContext': { icon: 'flashlight', label: 'Live Context' },
        'GetSystemIndex': { icon: 'list-check-2', label: 'System Index' },
        'WebSearch': { icon: 'earth', label: 'Web Search' },
        'UrlFetch': { icon: 'external-link', label: 'URL Fetch' },
        'WebReadChunk': { icon: 'file-text', label: 'Web Read Chunk' },
        'ReadRuntimeArtifact': { icon: 'file', label: 'Read Artifact' },
        'CameraCapture': { icon: 'camera', label: 'Camera Capture' },
        'MediaAnalyze': { icon: 'file-image', label: 'Media Analyze' },
        'Notify': { icon: 'notification-3', label: 'Notify' },
        'GetSkillIndex': { icon: 'book', label: 'Skill Index' },
        'InstallSkill': { icon: 'download', label: 'Install Skill' },
        'DeleteSkill': { icon: 'delete-bin', label: 'Delete Skill' },
        'ListInstalledSkills': { icon: 'list-check-3', label: 'List Skills' },
        'GetInstalledSkill': { icon: 'file-code', label: 'Get Skill' },
        'ReviewSelfSkills': { icon: 'eye', label: 'Review Skills' },
        'ProposeSelfEdit': { icon: 'pencil-ai', label: 'Propose Edit' },
        'GetProposal': { icon: 'file-search', label: 'Get Proposal' },
        'ListProposals': { icon: 'file-list-2', label: 'List Proposals' },
        'ApplyProposal': { icon: 'checkbox', label: 'Apply Proposal' },
        'DiscardProposal': { icon: 'close-circle', label: 'Discard Proposal' },
        'GetSelfChangelog': { icon: 'bug', label: 'Changelog' },
        'GetWorkspaceDoc': { icon: 'book-open', label: 'Get Workspace Doc' },
        'SetWorkspaceDoc': { icon: 'edit', label: 'Set Workspace Doc' },
        'ListWorkspaceDocs': { icon: 'file-list-2', label: 'List Workspace Docs' },
        'HomeAssistantGuide': { icon: 'home-assistant', label: 'HA Guide' },
        'UpsertGuideDoc': { icon: 'file-add', label: 'Upsert Guide' },
        'DeleteGuideDoc': { icon: 'delete-bin', label: 'Delete Guide' },
        'ConversationMemory': { icon: 'brain', label: 'Memory' },
        'MemoryGraph': { icon: 'node-tree', label: 'Memory Graph' },
        'GetConversationHistory': { icon: 'chat-history', label: 'Chat History' },
        'SetConversationState': { icon: 'chat-history', label: 'Set State' },
        'GetMasterPrompt': { icon: 'file-text', label: 'Get Prompt' },
        'SetMasterPrompt': { icon: 'edit-2', label: 'Set Prompt' },
        'AgentHandoff': { icon: 'ai-agent', label: 'Agent Handoff' },
        'NextAgentHandoff': { icon: 'arrow-right', label: 'Next Agent' },
        'ThinkContinue': { icon: 'sparkling', label: 'Think Continue' },
        'ParallelToolCall': { icon: 'split-cells-horizontal', label: 'Parallel Call' },
        'HACS': { icon: 'tools', label: 'HACS' },
        'StockQuery': { icon: 'stock', label: 'Stock Query' },
        'ShellCommand': { icon: 'terminal', label: 'Shell Command' },
        'FindFiles': { icon: 'folder', label: 'Find Files' },
        '_thinking': { icon: 'chat-ai-3', label: 'Justification' },
    };

    const _normalizeToolName = (name) => {
        const raw = String(name || '');
        if (_TOOL_DISPLAY[raw]) return raw;
        const short = raw.split(/[.:/]/).pop();
        if (_TOOL_DISPLAY[short]) return short;
        const lower = short.toLowerCase();
        for (const key of Object.keys(_TOOL_DISPLAY)) {
            if (key.toLowerCase() === lower) return key;
        }
        return raw;
    };

    const _escHtml = (s) => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

    const _normalizeToolArgs = (args) => {
        if (typeof args !== 'string') return args || {};
        try {
            const parsed = JSON.parse(args);
            return parsed && typeof parsed === 'object' ? parsed : {};
        } catch(e) {
            return { input: args };
        }
    };

    const _renderToolMarkers = (html) => html.replace(
        /<!--\s*CLAW_TOOL:([A-Za-z0-9_.:-]+)\s*-->/g,
        '<span class="claw-tool-marker" data-tool-id="$1"></span>'
    );

    const _prepareToolMarkers = (text) => String(text || '').replace(
        /<!--\s*CLAW_TOOL:([A-Za-z0-9_.:-]+)\s*-->/g,
        '<span class="claw-tool-marker" data-tool-id="$1"></span>'
    );

    const _pushTurnText = (text) => {
        const value = String(text || '').replace(/<!--\s*CLAW_TOOL:[A-Za-z0-9_.:-]+\s*-->/g, '');
        if (!value) return;
        const parts = window.__clawTurnParts || (window.__clawTurnParts = []);
        const last = parts[parts.length - 1];
        if (last && last.type === 'text') last.text += value;
        else parts.push({ type: 'text', text: value });
    };

    const _pushTurnTool = (id) => {
        if (!id) return;
        const parts = window.__clawTurnParts || (window.__clawTurnParts = []);
        if (!parts.some(p => p.type === 'tool' && p.id === id)) parts.push({ type: 'tool', id });
    };

    const _isDiffText = (text) => {
        if (typeof text !== 'string') return false;
        const lines = text.split('\n');
        if (lines.length < 3) return false;
        const indicators = lines.filter(l => /^[+-]/.test(l) && !/^[+-]{3}/.test(l)).length;
        return indicators > 1 || lines.some(l => /^@@\s/.test(l));
    };

    const _renderDiffHtml = (text) => {
        return '<pre class="claw-ta-diff">' + text.split('\n').map(l => {
            const e = _escHtml(l);
            if (/^\+[^+]/.test(l) || l === '+') return '<span class="diff-line diff-add">' + e + '</span>';
            if (/^-[^-]/.test(l) || l === '-') return '<span class="diff-line diff-del">' + e + '</span>';
            if (/^@@/.test(l)) return '<span class="diff-line diff-hunk">' + e + '</span>';
            return '<span class="diff-line">' + e + '</span>';
        }).join('\n') + '</pre>';
    };

    const _formatToolArgs = (args) => {
        if (!args || typeof args !== 'object') return '';
        const entries = Object.entries(args);
        if (!entries.length) return '';
        const parts = [];
        for (const [k, v] of entries) {
            let val = v;
            if (typeof val === 'string' && val.length > 300) val = val.slice(0, 300) + '…';
            else if (typeof val === 'object') {
                try { val = JSON.stringify(val); if (val.length > 300) val = val.slice(0, 300) + '…'; } catch(e) { val = String(val); }
            }
            parts.push(_escHtml(k) + ': ' + _escHtml(val));
        }
        return parts.join('\n');
    };

    const _extractDiff = (obj) => {
        if (!obj || typeof obj !== 'object') return null;
        if (typeof obj.diff === 'string' && _isDiffText(obj.diff)) return obj.diff;
        if (obj.patch_report && typeof obj.patch_report.diff === 'string' && _isDiffText(obj.patch_report.diff)) return obj.patch_report.diff;
        if (obj.result && typeof obj.result === 'object') return _extractDiff(obj.result);
        return null;
    };

    const _renderToolResultBody = (result) => {
        if (result === null || result === undefined) return '';
        let text = '';
        if (typeof result === 'string') text = result;
        else if (typeof result === 'object') {
            const diffText = _extractDiff(result);
            if (diffText) return _renderDiffHtml(diffText);
            const inner = result.result;
            if (inner && typeof inner === 'object') {
                const innerDiff = _extractDiff(inner);
                if (innerDiff) return _renderDiffHtml(innerDiff);
            }
            if (result.stdout || result.output) {
                const output = result.stdout || result.output;
                return '<div class="claw-ta-cmd"><span class="claw-ta-cmd-prompt">$</span>' + _escHtml(String(output)) + '</div>';
            }
            if (inner && typeof inner === 'object' && (inner.stdout || inner.output)) {
                const output = inner.stdout || inner.output;
                return '<div class="claw-ta-cmd"><span class="claw-ta-cmd-prompt">$</span>' + _escHtml(String(output)) + '</div>';
            }
            try { text = JSON.stringify(result, null, 2); } catch(e) { text = String(result); }
        } else text = String(result);
        if (_isDiffText(text)) return _renderDiffHtml(text);
        return '<div class="claw-ta-result"><pre><code>' + _escHtml(text) + '</code></pre></div>';
    };

    const _PROGRESS_RE = /┊/;

    const _getHAControlDisplay = (args, result) => {
        const base = { icon: 'survey', label: 'HA Query' };
        if (!args || typeof args !== 'object') return base;
        const action = String(args.action || '').toLowerCase();
        const byAction = {
            shell: { icon: 'terminal-box', label: 'Shell' },
            ssh: { icon: 'terminal-window', label: 'SSH' },
            list_integrations: { icon: 'list-unordered', label: 'Integrations' },
            get_integration: { icon: 'information', label: 'Integration' },
            list_entities_by_integration: { icon: 'search', label: 'Entities' },
            list_devices: { icon: 'layout-grid', label: 'Devices' },
            reload_integration: { icon: 'restart', label: 'Reload Integration' },
            rename_entry: { icon: 'edit', label: 'Rename Entry' },
            reload_themes: { icon: 'restart', label: 'Reload Themes' },
            reload_resources: { icon: 'restart', label: 'Reload Resources' },
            reload_scripts: { icon: 'restart', label: 'Reload Scripts' },
            reload_automations: { icon: 'restart', label: 'Reload Automations' },
            check_config: { icon: 'checkbox-circle', label: 'Check Config' },
            get_system_log: { icon: 'file-list-2', label: 'System Log' },
            get_error_log: { icon: 'file-list-2', label: 'Error Log' },
            get_diagnostics: { icon: 'pulse', label: 'Diagnostics' },
            navigate: { icon: 'arrow-right', label: 'Navigate' },
            show_toast: { icon: 'information', label: 'Toast' },
            show_dialog: { icon: 'information', label: 'Dialog' },
        };
        if (byAction[action]) return byAction[action];
        const seen = new Set();
        const visit = (value) => {
            if (!value) return false;
            if (typeof value === 'string') {
                const s = value.trim();
                if (/^(shell|terminal|command|cmd)$/i.test(s)) return true;
                if ((s.startsWith('{') && s.endsWith('}')) || (s.startsWith('[') && s.endsWith(']'))) {
                    try { return visit(JSON.parse(s)); } catch(e) {}
                }
                return false;
            }
            if (typeof value !== 'object' || seen.has(value)) return false;
            seen.add(value);
            for (const [key, val] of Object.entries(value)) {
                const k = key.toLowerCase();
                if (['command', 'cmd', 'shell_command', 'shell'].includes(k) && val) return true;
                if (['action', 'type', 'mode', 'tool', 'name'].includes(k) && /^(shell|terminal|command|cmd)$/i.test(String(val))) return true;
                if (visit(val)) return true;
            }
            return false;
        };
        if (visit(args) || visit(result)) return { icon: 'terminal-box', label: 'Shell' };
        return base;
    };

    const _getHAControlSummary = (args) => {
        if (!args || typeof args !== 'object') return '';
        let params = args.params;
        if (typeof params === 'string') {
            try { params = JSON.parse(params); } catch(e) {}
        }
        if (!params || typeof params !== 'object') params = args;
        const action = String(args.action || '').toLowerCase();
        const short = (value, max = 32) => {
            let text = typeof value === 'object' ? JSON.stringify(value) : String(value);
            text = text.replace(/\s+/g, ' ').trim();
            return text.length > max ? text.slice(0, max) + '...' : text;
        };
        if (action === 'ssh') return [params.host && short(params.host), params.command && short(params.command, 48)].filter(Boolean).join(' ');
        if (params.command) return short(params.command, 80);
        if (params.domain && params.name) return short(params.domain) + ' → ' + short(params.name);
        if (params.domain) return short(params.domain);
        if (params.entry_id) return short(params.entry_id);
        if (params.name) return short(params.name);
        if (params.lines) return String(params.lines) + ' lines';
        if (params.limit) return String(params.limit) + ' items';
        const skip = new Set(['action', 'params', 'timeout', 'password', 'token', 'key', 'api_key']);
        const parts = Object.entries(params)
            .filter(([key, value]) => value !== undefined && value !== null && value !== '' && !skip.has(key.toLowerCase()))
            .slice(0, 2)
            .map(([key, value]) => key + '=' + short(value));
        return parts.join(' ');
    };

    const _shortToolSummary = (value, max = 48) => {
        if (value === undefined || value === null) return '';
        let text = typeof value === 'object' ? JSON.stringify(value) : String(value);
        text = text.replace(/\s+/g, ' ').trim();
        return text.length > max ? text.slice(0, max) + '...' : text;
    };

    const _buildCardHtml = (act) => {
        const toolName = _normalizeToolName(act.tool_name);
        const taId = act.id || (toolName + '_' + (act.tool_call_id || Math.random().toString(36).slice(2)));
        if (!act.id) act.id = taId;
        const display = { ...(_TOOL_DISPLAY[toolName] || { icon: 'tools', label: act.tool_name }) };
        if (toolName === 'HAControl') Object.assign(display, _getHAControlDisplay(act.tool_args, act.result));
        const isPending = act.result === undefined && !act.error;
        const collapsed = true;
        let summary = '';
        if (act._thinkText) {
            summary = act._thinkText;
        } else if (act.tool_args) {
            const a = act.tool_args;
            if (toolName === 'HAControl') summary = _getHAControlSummary(a).slice(0, 80);
            else if (a.command) summary = _shortToolSummary(a.command, 80);
            else if (a.code) summary = _shortToolSummary(a.code, 80);
            else if (a.entity_id) summary = _shortToolSummary(a.entity_id);
            else if (a.service) summary = _shortToolSummary(a.service);
            else if (a.action) summary = _shortToolSummary(String(a.action) + (a.target ? ' → ' + JSON.stringify(a.target) : ''), 80);
            else {
                const keys = Object.keys(a);
                if (keys.length) summary = keys.map(k => {
                    const v = a[k];
                    if (v === null || v === undefined) return k;
                    return k + '=' + _shortToolSummary(v, 30);
                }).join(' ').slice(0, 80);
            }
        }
        if (summary.length > 20) summary = summary.slice(0, 20) + '...';
        let timeStr = '';
        if (act._startTime && !isPending) {
            const elapsed = ((act._endTime || Date.now()) - act._startTime) / 1000;
            timeStr = elapsed < 1 ? '<0.1s' : elapsed.toFixed(1) + 's';
        }
        const iconSvg = _TA_ICONS[display.icon] || _TA_ICONS.tool;
        if (act._thinkText) {
            return { taId, collapsed: true, html:
                '<div class="claw-ta-header claw-ta-think">' +
                    '<span class="claw-ta-icon">' + iconSvg + '</span>' +
                    '<svg class="claw-ta-chevron" viewBox="0 0 24 24"><path d="M11.9999 13.1714L16.9497 8.22168L18.3639 9.63589L11.9999 15.9999L5.63599 9.63589L7.0502 8.22168L11.9999 13.1714Z" fill="currentColor"/></svg>' +
                    '<span class="claw-ta-name"><b>' + _escHtml(display.label) + '</b>' +
                        ' <span class="claw-ta-summary">' + _escHtml((() => { const idx = summary.search(/[。.]/); return idx > 0 && idx < 25 ? summary.slice(0, idx) : summary.slice(0, 25) + '...'; })()) + '</span>' +
                    '</span>' +
                '</div>' +
                '<div class="claw-ta-body"><div class="claw-ta-body-inner"><div class="claw-ta-args">' + _escHtml(act._thinkText) + '</div></div></div>'
            };
        }
        let bodyContent = '';
        if (act.tool_args && Object.keys(act.tool_args).length) {
            bodyContent += '<div class="claw-ta-args">' + _formatToolArgs(act.tool_args) + '</div>';
        }
        if (act.result !== undefined || act.error) {
            bodyContent += act.error
                ? ''
                : _renderToolResultBody(act.result);
        }
        const hasError = !!act.error;
        const hasWarning = !!act.warning;
        const headerClass = hasError ? 'claw-ta-header claw-ta-error' : hasWarning ? 'claw-ta-header claw-ta-warning' : 'claw-ta-header';
        return { taId, collapsed, html:
            '<div class="' + headerClass + '">' +
                '<span class="claw-ta-icon">' + iconSvg + '</span>' +
                '<svg class="claw-ta-chevron" viewBox="0 0 24 24"><path d="M11.9999 13.1714L16.9497 8.22168L18.3639 9.63589L11.9999 15.9999L5.63599 9.63589L7.0502 8.22168L11.9999 13.1714Z" fill="currentColor"/></svg>' +
                '<span class="claw-ta-name"><b>' + _escHtml(display.label) + '</b>' +
                    (timeStr ? ' <span class="claw-ta-time">' + timeStr + '</span>' : '') +
                    (summary ? ' <span class="claw-ta-summary">' + _escHtml(summary) + '</span>' : '') +
                '</span>' +
                (isPending ? '<span class="claw-ta-spinner"></span>' : '') +
            '</div>' +
            '<div class="claw-ta-body"><div class="claw-ta-body-inner">' + bodyContent + '</div></div>'
        };
    };

    const _updateExistingCard = (card, act) => {
        if (!card || act.result === undefined || card.dataset.hasResult) return;
        card.dataset.hasResult = '1';
        const header = card.querySelector('.claw-ta-header');
        if (act.error && header) {
            header.classList.add('claw-ta-error');
        } else if (act.warning && header) {
            header.classList.add('claw-ta-warning');
        }
        const bodyInner = card.querySelector('.claw-ta-body-inner');
        if (bodyInner && !act.error) {
            const resultHtml = _renderToolResultBody(act.result);
            if (resultHtml) bodyInner.insertAdjacentHTML('beforeend', resultHtml);
        }
        const spinner = card.querySelector('.claw-ta-spinner');
        if (spinner) spinner.remove();
        const nameEl = card.querySelector('.claw-ta-name');
        if (nameEl && act._startTime) {
            const elapsed = ((act._endTime || Date.now()) - act._startTime) / 1000;
            const timeStr = elapsed < 1 ? '<0.1s' : elapsed.toFixed(1) + 's';
            let existingTime = nameEl.querySelector('.claw-ta-time');
            if (!existingTime) {
                const b = nameEl.querySelector('b');
                if (b) b.insertAdjacentHTML('afterend', ' <span class="claw-ta-time">' + timeStr + '</span>');
            } else {
                existingTime.textContent = timeStr;
            }
        }
    };

    let _taPatching = false;

    const _renderThinkingText = (msr) => {
        const text = window.__clawThinkingText;
        if (!text || window.__clawToolDetailsEnabled) {
            const existing = msr.querySelector('.claw-thinking-text');
            if (existing) existing.remove();
            return;
        }
        let el = msr.querySelector('.claw-thinking-text');
        if (!el) {
            el = document.createElement('div');
            el.className = 'claw-thinking-text';
            el.style.cssText = 'padding:8px 12px;margin:8px 0;background:var(--secondary-background-color,#f5f5f5);border-radius:8px;font-style:italic;color:var(--secondary-text-color,#666);';
            const mdEl = msr.querySelector('ha-markdown-element');
            if (mdEl) mdEl.parentNode.insertBefore(el, mdEl);
            else msr.appendChild(el);
        }
        const truncated = text.length > 100 ? text.slice(0, 100) + '...' : text;
        el.textContent = '💭 ' + truncated;
    };

    const _renderToolActivities = () => {
        const chat = deepQuery('ha-assist-chat');
        const sr = chat?.shadowRoot;
        if (!sr) return;
        const msgEls = sr.querySelectorAll('.message.hass');
        if (!msgEls.length) return;
        const lastMsg = msgEls[msgEls.length - 1];
        const md = lastMsg.querySelector('ha-markdown');
        if (!md) return;
        const msr = md.shadowRoot;
        if (!msr) return;
        _injectMdStyles(msr);
        _renderThinkingText(msr);
        const activities = window.__clawToolActivities;
        if (!activities || !activities.length) return;
        const el = msr.querySelector('ha-markdown-element');
        const parts = window.__clawTurnParts || [];
        const marked = window.__clawMarked;
        if (_mdStreamActive && el && marked && parts.length) {
            if (!el.__clawRenderBlocked) {
                el.__clawRenderBlocked = true;
            }
            let renderParts = parts;
            const toolParts = parts.filter(p => p.type === 'tool');
            const isOnlyThinking = toolParts.length === 1 && toolParts[0].id === '_thinking_singleton';
            let thinkingInPanel = false;
            if (isOnlyThinking) {
                const textParts = parts.filter(p => p.type === 'text');
                const totalText = textParts.map(p => p.text || '').join('').trim();
                if (totalText.length >= 10) {
                    thinkingInPanel = true;
                    renderParts = [...textParts];
                    _mdStreamActive = false;
                    _turnEnded = true;
                    if (typeof window.__clawOnStreamEnd === 'function') {
                        setTimeout(() => window.__clawOnStreamEnd(), 50);
                    }
                } else {
                    const thinkingPart = toolParts[0];
                    renderParts = [thinkingPart, ...textParts];
                }
            }
            let html = '';
            for (let idx = 0; idx < renderParts.length; idx++) {
                const part = renderParts[idx];
                if (part.type === 'text') {
                    html += '<div class="claw-md-text" data-part-idx="' + idx + '">' + marked.parse(_prepareToolMarkers(_normalizeAssistantTables(part.text || ''))) + '</div>';
                    continue;
                }
                if (part.type !== 'tool') continue;
                const act = activities.find(a => a.tool_call_id === part.id || a.marker_id === part.id);
                if (!act) continue;
                const built = _buildCardHtml(act);
                const cardClass = (isOnlyThinking || !_mdStreamActive) ? 'claw-ta-card collapsed' : 'claw-ta-card';
                html += '<div class="' + cardClass + '" data-ta-id="' + _escHtml(built.taId) + '"' + (act.result !== undefined ? ' data-has-result="1"' : '') + '>' + built.html + '</div>';
            }
            if (_mdStreamActive) {
                html += '<div class="claw-md-text claw-streaming-tail"><p>...</p></div>';
            }
            if (html) {
                const seqSig = parts.map(p => p.type === 'text' ? 't' : ('x:' + p.id)).join('|');
                const sig = (_mdStreamActive ? 's|' : 'e|') + parts.map(p => p.type === 'text' ? ('t:' + (p.text || '').length) : ('x:' + p.id)).join('|') + '|' + activities.map(a => a.tool_call_id + ':' + (a.result !== undefined ? '1' : '0') + ':' + (a._thinkText?.length || 0)).join('|');
                const mixed = el.querySelector('.claw-md-mixed');
                if (mixed && el.__clawMixedSeqSig === seqSig) {
                    for (let idx = 0; idx < parts.length; idx++) {
                        const part = parts[idx];
                        if (part.type !== 'text') continue;
                        const node = mixed.querySelector('.claw-md-text[data-part-idx="' + idx + '"]');
                        if (!node) continue;
                        const textSig = (part.text || '').length;
                        if (node.dataset.textSig === String(textSig)) continue;
                        node.dataset.textSig = textSig;
                        node.innerHTML = marked.parse(_prepareToolMarkers(_normalizeAssistantTables(part.text || '')));
                    }
                    const existingTail = mixed.querySelector('.claw-streaming-tail');
                    if (!_mdStreamActive) {
                        if (existingTail) existingTail.remove();
                    } else if (!existingTail) {
                        const ph = document.createElement('div');
                        ph.className = 'claw-md-text claw-streaming-tail';
                        ph.innerHTML = '<p>...</p>';
                        mixed.appendChild(ph);
                    } else {
                        mixed.appendChild(existingTail);
                    }
                    activities.forEach(act => {
                        const taId = act.id || (_normalizeToolName(act.tool_name) + '_' + (act.tool_call_id || ''));
                        const card = mixed.querySelector('.claw-ta-card[data-ta-id="' + taId + '"]');
                        if (!card) return;
                        if (act._thinkText && act.tool_name === '_thinking') {
                            const summary = card.querySelector('.claw-ta-summary');
                            const body = card.querySelector('.claw-ta-args');
                            if (summary) {
                                const idx = act._thinkText.search(/[。.]/);
                                summary.textContent = idx > 0 && idx < 25 ? act._thinkText.slice(0, idx) : act._thinkText.slice(0, 25) + '...';
                            }
                            if (body) body.textContent = act._thinkText;
                        }
                        if ((act.result !== undefined || act.error) && !card.dataset.hasResult) _updateExistingCard(card, act);
                    });
                    el.__clawMixedSig = sig;
                    return;
                }
                if (el.__clawMixedSig !== sig) {
                    el.__clawMixedSig = sig;
                    el.__clawMixedSeqSig = seqSig;
                    el.classList.add('claw-rerender');
                    el.innerHTML = '<div class="claw-md claw-md-mixed">' + html + '</div>';
                    requestAnimationFrame(() => el.classList.remove('claw-rerender'));
                    _transformCodeBlocks(el);
                    el.querySelectorAll('.claw-ta-card .claw-ta-header').forEach(h => h.addEventListener('click', () => { const c = h.closest('.claw-ta-card'); if (c) { c.classList.toggle('collapsed'); c.dataset.userToggled = '1'; } }));
                }
                let oldPanel = msr.querySelector('.claw-ta-panel');
                if (oldPanel && !thinkingInPanel) oldPanel.remove();
                if (thinkingInPanel) {
                    const thinkingAct = activities.find(a => a.tool_name === '_thinking');
                    if (thinkingAct && !oldPanel) {
                        const built = _buildCardHtml(thinkingAct);
                        const panel = document.createElement('div');
                        panel.className = 'claw-ta-panel collapsed';
                        panel.innerHTML =
                            '<div class="claw-ta-panel-header">' +
                                '<svg class="claw-ta-panel-chevron" viewBox="0 0 24 24"><path d="M11.9999 13.1714L16.9497 8.22168L18.3639 9.63589L11.9999 15.9999L5.63599 9.63589L7.0502 8.22168L11.9999 13.1714Z"/></svg>' +
                                '<b>Justification</b>' +
                                '<span class="claw-ta-panel-count">1</span>' +
                            '</div>' +
                            '<div class="claw-ta-panel-body">' +
                                '<div class="claw-ta-card collapsed" data-ta-id="' + _escHtml(built.taId) + '" data-has-result="1">' + built.html + '</div>' +
                            '</div>';
                        panel.querySelector('.claw-ta-panel-header')?.addEventListener('click', () => panel.classList.toggle('collapsed'));
                        panel.querySelector('.claw-ta-card .claw-ta-header')?.addEventListener('click', (e) => { e.stopPropagation(); const c = e.target.closest('.claw-ta-card'); if (c) { c.classList.toggle('collapsed'); c.dataset.userToggled = '1'; } });
                        const mixedEl = el.querySelector('.claw-md-mixed');
                        if (mixedEl) mixedEl.prepend(panel);
                    }
                }
                return;
            }
        }
        let fallback = msr.querySelector('.claw-ta-panel');
        if (!_turnEnded) {
            if (fallback) { fallback.remove(); fallback = null; }
        }
        const mixed = el?.querySelector('.claw-md-mixed');
        if (mixed && mixed.querySelector('.claw-ta-card')) {
            if (_turnEnded) {
                const mixedCards = mixed.querySelectorAll('.claw-ta-card');
                mixedCards.forEach(card => {
                    if (!card.dataset.userToggled) card.classList.add('collapsed');
                });
            }
            return;
        }
        activities.forEach(act => {
            const toolName = _normalizeToolName(act.tool_name);
            const taId = act.id || (toolName + '_' + (act.tool_call_id || Math.random().toString(36).slice(2)));
            if (!act.id) act.id = taId;
            let card = msr.querySelector('.claw-ta-card[data-ta-id="' + taId + '"]');
            if (card) {
                if ((act.result !== undefined || act.error) && !card.dataset.hasResult) {
                    _updateExistingCard(card, act);
                }
                if (_turnEnded) {
                    if (!card.dataset.userToggled) card.classList.add('collapsed');
                    if (!card.closest('.claw-ta-panel')) { card.__clawNeedsPanel = true; }
                }
                return;
            }
            const built = _buildCardHtml(act);
            card = document.createElement('div');
            card.className = _turnEnded ? 'claw-ta-card collapsed' : 'claw-ta-card';
            card.dataset.taId = built.taId;
            if (act.result !== undefined) card.dataset.hasResult = '1';
            card.innerHTML = built.html;
            card.querySelector('.claw-ta-header').addEventListener('click', () => { card.classList.toggle('collapsed'); card.dataset.userToggled = '1'; });
            const markerId = act.marker_id || act.tool_call_id;
            const marker = markerId && el ? el.querySelector('.claw-tool-marker[data-tool-id="' + CSS.escape(markerId) + '"]') : null;
            if (marker) {
                marker.replaceWith(card);
                return;
            }
            if (_turnEnded) {
                if (!fallback) {
                    fallback = document.createElement('div');
                    fallback.className = 'claw-ta-panel collapsed';
                    fallback.innerHTML =
                        '<div class="claw-ta-panel-header">' +
                            '<svg class="claw-ta-panel-chevron" viewBox="0 0 24 24"><path d="M11.9999 13.1714L16.9497 8.22168L18.3639 9.63589L11.9999 15.9999L5.63599 9.63589L7.0502 8.22168L11.9999 13.1714Z"/></svg>' +
                            '<b>Tool Activity</b>' +
                            '<span class="claw-ta-panel-count"></span>' +
                        '</div>' +
                        '<div class="claw-ta-panel-body"></div>';
                    fallback.querySelector('.claw-ta-panel-header')?.addEventListener('click', () => fallback.classList.toggle('collapsed'));
                    if (el) msr.insertBefore(fallback, el);
                    else msr.appendChild(fallback);
                }
                const body = fallback.querySelector('.claw-ta-panel-body') || fallback;
                body.appendChild(card);
            } else {
                if (el) msr.insertBefore(card, el);
                else msr.appendChild(card);
            }
        });
        if (_turnEnded) {
            const orphans = msr.querySelectorAll('.claw-ta-card[data-ta-id]');
            orphans.forEach(card => {
                if (card.__clawNeedsPanel || !card.closest('.claw-ta-panel')) {
                    if (!fallback) {
                        fallback = document.createElement('div');
                        fallback.className = 'claw-ta-panel collapsed';
                        fallback.innerHTML =
                            '<div class="claw-ta-panel-header">' +
                                '<svg class="claw-ta-panel-chevron" viewBox="0 0 24 24"><path d="M11.9999 13.1714L16.9497 8.22168L18.3639 9.63589L11.9999 15.9999L5.63599 9.63589L7.0502 8.22168L11.9999 13.1714Z"/></svg>' +
                                '<b>Tool Activity</b>' +
                                '<span class="claw-ta-panel-count"></span>' +
                            '</div>' +
                            '<div class="claw-ta-panel-body"></div>';
                        fallback.querySelector('.claw-ta-panel-header')?.addEventListener('click', () => fallback.classList.toggle('collapsed'));
                        if (el) msr.insertBefore(fallback, el);
                        else msr.appendChild(fallback);
                    }
                    const body = fallback.querySelector('.claw-ta-panel-body') || fallback;
                    body.appendChild(card);
                    delete card.__clawNeedsPanel;
                }
            });
        }
        if (fallback) {
            const pBody = fallback.querySelector('.claw-ta-panel-body') || fallback;
            const count = fallback.querySelector('.claw-ta-panel-count');
            if (count) count.textContent = String(pBody.querySelectorAll('.claw-ta-card').length);
        }
    };

    const _scheduleToolRender = () => {
        if (_taRenderPending) return;
        _taRenderPending = true;
        requestAnimationFrame(() => {
            _taRenderPending = false;
            _renderToolActivities();
        });
    };

    window.addEventListener('claw-tool-details-changed', () => { try { _renderToolActivities(); } catch(e) {} });

    const _findToolActivity = (callId, toolName) => {
        const activities = window.__clawToolActivities || [];
        if (callId) {
            const byId = activities.find(a => a.tool_call_id === callId);
            if (byId) return byId;
        }
        const normalized = _normalizeToolName(toolName);
        if (normalized && normalized !== 'tool') {
            return activities.find(a => _normalizeToolName(a.tool_name) === normalized && a.result === undefined && !a.error) || null;
        }
        return null;
    };

    const _upsertToolActivity = (info) => {
        const callId = info.tool_call_id || ('tc_' + Math.random().toString(36).slice(2));
        let act = _findToolActivity(callId, info.tool_name);
        if (!act) {
            act = {
                tool_call_id: callId,
                marker_id: info.marker_id || callId,
                tool_name: info.tool_name || 'tool',
                tool_args: _normalizeToolArgs(info.tool_args),
                result: undefined,
                error: null,
                _startTime: Date.now(),
            };
            window.__clawToolActivities.push(act);
        } else {
            act.tool_call_id = act.tool_call_id || callId;
            act.marker_id = info.marker_id || act.marker_id || act.tool_call_id;
            act.tool_name = info.tool_name || act.tool_name;
            act.tool_args = _normalizeToolArgs(info.tool_args || act.tool_args);
            if (!act._startTime) act._startTime = Date.now();
        }
        return act;
    };

    const _completeToolActivity = (info) => {
        let parsed = info.tool_result;
        try { if (typeof parsed === 'string') parsed = JSON.parse(parsed); } catch(e) {}
        const callId = info.tool_call_id || '';
        let act = _findToolActivity(callId, info.tool_name);
        if (!act && (callId || info.tool_name)) {
            act = _upsertToolActivity({
                tool_call_id: callId || ('tr_' + Math.random().toString(36).slice(2)),
                tool_name: info.tool_name || 'tool',
                tool_args: info.tool_args || {},
            });
        }
        if (!act) return null;
        act.result = parsed;
        act._endTime = Date.now();
        if (typeof parsed === 'object' && parsed && parsed.success === false) {
            if (parsed.success_count > 0) {
                act.warning = true;
            } else {
                act.error = parsed.error || 'Failed';
            }
        }
        return act;
    };

    let _mdRenderTimer = null;
    let _mdStreamActive = false;

    const _STREAM_CSS = ``;

    const _injectMdStyles = (msr) => {
        if (msr.getElementById('claw-md-rich')) return;
        const s = document.createElement('style');
        s.id = 'claw-md-rich';
        s.textContent = _MD_CSS + _CODE_PANEL_CSS + _TOOL_ACTIVITY_CSS + _STREAM_CSS;
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

    const _parsePipeCells = (line) => {
        const t = String(line || '').trim();
        if (!t.includes('|')) return null;
        const stripped = t.replace(/^\|/, '').replace(/\|$/, '');
        return stripped.split('|').map(c => c.trim());
    };
    const _isSepCell = (c) => /^:?-{1,}:?$/.test(c.trim());
    const _looksLikeSeparatorLine = (line) => {
        const t = String(line || '').trim();
        if (!t.includes('-')) return false;
        const cells = _parsePipeCells(t);
        if (!cells) {
            return /^:?-{2,}:?(\s*\|?\s*:?-{2,}:?\s*)*$/.test(t) || /^-{2,}(\s*\|\s*-{2,})*$/.test(t);
        }
        const sepCells = cells.filter(_isSepCell);
        if (sepCells.length >= 1 && sepCells.length >= cells.length * 0.5) return true;
        if (sepCells.length >= 1 && cells.length > 0 && _isSepCell(cells[0])) return true;
        return false;
    };
    const _normalizeAssistantTables = (raw) => {
        const lines = String(raw || '').replace(/\r\n?/g, '\n').split('\n');
        let i = 0;
        while (i < lines.length) {
            if (_isLooseTableSeparator(lines[i]) && i > 0 && !lines[i - 1].includes('|')) {
                const headerCells = _splitLooseTableRow(lines[i - 1]);
                const columnCount = Math.max(headerCells.length, _splitLooseTableRow(lines[i]).length);
                if (columnCount > 1) {
                    const fill = (cells) => Array.from({ length: columnCount }, (_, idx) => cells[idx] || '');
                    lines[i - 1] = '| ' + fill(headerCells).join(' | ') + ' |';
                    lines[i] = '| ' + Array.from({ length: columnCount }, () => '---').join(' | ') + ' |';
                    for (let j = i + 1; j < lines.length; j++) {
                        const rowCells = _splitLooseTableRow(lines[j]);
                        if (rowCells.length < 2) break;
                        lines[j] = '| ' + fill(rowCells).join(' | ') + ' |';
                    }
                }
                i++;
                continue;
            }
            if (!_looksLikeSeparatorLine(lines[i]) || i < 1) { i++; continue; }
            const sepIdx = i;
            let headerIdx = sepIdx - 1;
            while (headerIdx >= 0 && !lines[headerIdx].trim()) headerIdx--;
            if (headerIdx < 0) { i++; continue; }
            const sepLine = lines[sepIdx].trim();
            const sepCells = _parsePipeCells(sepLine);
            const sepOnlySepCells = sepCells ? sepCells.filter(_isSepCell) : [];
            const sepHasData = sepCells ? sepCells.filter(c => c && !_isSepCell(c)) : [];
            if (headerIdx < sepIdx - 1) {
                lines.splice(headerIdx + 1, sepIdx - headerIdx - 1);
                i = headerIdx + 1;
                continue;
            }
            const headerRaw = lines[headerIdx].trim();
            if (headerRaw.includes('|')) {
                const pipePos = headerRaw.indexOf('|');
                if (pipePos > 0 && !/^\|/.test(headerRaw)) {
                    const prefix = headerRaw.slice(0, pipePos).trim();
                    lines[headerIdx] = headerRaw.slice(pipePos);
                    if (prefix) lines.splice(headerIdx, 0, prefix);
                    i = headerIdx + 1;
                    continue;
                }
            }
            let spliced = false;
            if (sepHasData.length > 0 && sepOnlySepCells.length >= 1) {
                const dataRow = '| ' + sepHasData.join(' | ') + ' |';
                lines.splice(headerIdx + 1, 1, '| --- |', dataRow);
                spliced = true;
            }
            let tableStart = headerIdx;
            let tableEnd = headerIdx + 1 + (spliced ? 2 : 1);
            while (tableEnd < lines.length) {
                const row = lines[tableEnd].trim();
                if (!row || (!row.includes('|') && _splitLooseTableRow(row).length < 2)) break;
                if (_looksLikeSeparatorLine(row) && !_parsePipeCells(row)?.some(c => c && !_isSepCell(c))) break;
                tableEnd++;
            }
            let columnCount = 0;
            const sepActualIdx = tableStart + 1;
            for (let j = tableStart; j < tableEnd; j++) {
                if (j === sepActualIdx) continue;
                const cells = _parsePipeCells(lines[j]);
                if (cells) columnCount = Math.max(columnCount, cells.length);
            }
            if (columnCount < 2) { i = tableEnd; continue; }
            const normRow = (line) => {
                let cells = _parsePipeCells(line);
                if (!cells || cells.length === 0) cells = [];
                while (cells.length < columnCount) cells.push('');
                if (cells.length > columnCount) cells = cells.slice(0, columnCount);
                return '| ' + cells.join(' | ') + ' |';
            };
            const headerCells = _parsePipeCells(lines[tableStart]);
            if (!headerCells || headerCells.length === 0 || /^\|+\s*$/.test(lines[tableStart].trim())) {
                lines[tableStart] = '| ' + Array.from({ length: columnCount }, (_, idx) => idx === 0 ? '项目' : '内容').join(' | ') + ' |';
            } else {
                lines[tableStart] = normRow(lines[tableStart]);
            }
            lines[sepActualIdx] = '| ' + Array.from({ length: columnCount }, () => '---').join(' | ') + ' |';
            for (let j = sepActualIdx + 1; j < tableEnd; j++) {
                lines[j] = normRow(lines[j]);
            }
            i = tableEnd;
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
    let _renderPending = false;

    const _renderFinal = async () => {
        if (_renderLock) { _renderPending = true; return; }
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
                const _cleanExtraNodes = () => {
                    Array.from(el.childNodes).forEach(n => {
                        if (n.nodeType === 1 && !n.classList?.contains('claw-user-md')) n.remove();
                        else if (n.nodeType === 3 || n.nodeType === 8) n.remove();
                    });
                };
                _cleanExtraNodes();
                if (!el.__clawCleanObs) {
                    el.__clawCleanObs = new MutationObserver(_cleanExtraNodes);
                    el.__clawCleanObs.observe(el, { childList: true });
                }
                return;
            }
            if (!isHass) return;
            if (!marked) return;
            const displayContent = md.content;
            let rawContent = _findRawText(displayContent, hassTexts, used) || displayContent;
            if (rawContent && displayContent && rawContent.length < displayContent.length * 0.5 && displayContent.includes('┊')) {
                rawContent = displayContent;
            }
            if (!rawContent || typeof rawContent !== 'string') return;
            if (!rawContent.includes('CLAW_TOOL') && rawContent.length < 60 && !rawContent.includes('\n') && !rawContent.includes('|') && !/[#*`|~\[\]>-]{2}/.test(rawContent)) return;
            const sig = rawContent.length + '_' + rawContent.slice(0, 120);
            if (el.__clawMdSig === sig) return;
            el.__clawMdSig = sig;
            _injectMdStyles(msr);
            try {
                let html = marked.parse(_prepareToolMarkers(_normalizeAssistantTables(rawContent)));
                html = _renderToolMarkers(html);
                html = html.replace(/<table>/g, '<div class="table-wrap"><table>').replace(/<\/table>/g, '</table></div>');
                const clawMd = el.querySelector('.claw-md');
                if (clawMd) {
                    clawMd.innerHTML = html;
                } else {
                    el.classList.add('claw-rerender');
                    el.innerHTML = '<div class="claw-md">' + html + '</div>';
                    requestAnimationFrame(() => el.classList.remove('claw-rerender'));
                }
                const _cleanExtraNodes = () => {
                    Array.from(el.childNodes).forEach(n => {
                        if (n.nodeType === 1 && !n.classList?.contains('claw-md')) n.remove();
                        else if (n.nodeType === 3 || n.nodeType === 8) n.remove();
                    });
                };
                _cleanExtraNodes();
                if (!el.__clawCleanObs) {
                    el.__clawCleanObs = new MutationObserver(_cleanExtraNodes);
                    el.__clawCleanObs.observe(el, { childList: true });
                }
                _transformCodeBlocks(el);
                sr.querySelectorAll('.message').forEach(otherMsg => {
                    if (otherMsg === msgEl) return;
                    const otherMd = otherMsg.querySelector('ha-markdown');
                    const otherMsr = otherMd?.shadowRoot;
                    if (!otherMsr) return;
                    otherMsr.querySelectorAll('.claw-code-panel:not(.collapsed)').forEach(p => p.classList.add('collapsed'));
                });
            } catch(e) {}
        });
        } finally {
            if (obs) obs.observe(sr, { childList: true, subtree: true });
            _renderLock = false;
        }
        _renderToolActivities();
        if (_renderPending) {
            _renderPending = false;
            _renderFinal();
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
            .message-container { padding: 0 12px !important; max-width: 100% !important; box-sizing: border-box !important; }
            .message.user, .message.hass {
                min-width: 0 !important;
                max-width: 100% !important;
                overflow-x: hidden !important;
                overflow-wrap: anywhere !important;
                word-break: break-word !important;
                box-sizing: border-box !important;
            }
            .message.user ha-markdown, .message.hass ha-markdown {
                min-width: 0 !important;
                max-width: 100% !important;
                overflow-x: hidden !important;
                overflow-wrap: anywhere !important;
                word-break: break-word !important;
                display: block !important;
            }
            .message.hass .claw-tool-activities,
            .message.hass .claw-ta-card,
            .message.hass .claw-ta-panel {
                max-width: 100% !important;
                min-width: 0 !important;
                overflow-x: hidden !important;
                box-sizing: border-box !important;
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
        _turnEnded = true;
        if (_mdRenderTimer) clearTimeout(_mdRenderTimer);
        const chat = deepQuery('ha-assist-chat');
        const sr = chat?.shadowRoot;
        const lastMd = sr ? Array.from(sr.querySelectorAll('.message.hass ha-markdown')).pop() : null;
        const el = lastMd?.shadowRoot?.querySelector('ha-markdown-element');
        if (el) {
            el.__clawMixedSig = '';
            el.__clawMixedSeqSig = '';
            el.__clawMdSig = '';
            el.__clawRenderBlocked = false;
            delete el.__clawOriginalRender;
        }
        delete window.__clawThinkingText;
        const thinkEl = lastMd?.shadowRoot?.querySelector('.claw-thinking-text');
        if (thinkEl) thinkEl.remove();
        setTimeout(() => { _renderFinal(); _scheduleToolRender(); }, 150);
        if (typeof _origStreamEnd === 'function') _origStreamEnd();
        if (window.__clawIsNewReset) { delete window.__clawIsNewReset; }
        else if (typeof window.__clawPlaySound === 'function') window.__clawPlaySound('stream-complete');
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
        const DOCK_TOP_APP_BAR_STYLE_ID = 'claw-dock-top-app-bar-style';
        const DOCK_TOP_APP_BAR_OFFSET = '--claw-dock-top-app-bar-offset';
        const DOCK_TOP_APP_BAR_TARGET = 'claw-dock-top-app-bar-target';
        const DOCK_HEADER_TARGET = 'claw-dock-header-target';
        const DOCK_TRANSITION = '.25s cubic-bezier(.4,0,.2,1)';

        const getMainSR = () => {
            const home = document.querySelector('home-assistant');
            return home?.shadowRoot?.querySelector('home-assistant-main')?.shadowRoot || null;
        };

        const syncDockTopAppBars = () => {
            const msr = getMainSR();
            if (!msr) return;
            const css = `
                .${DOCK_TOP_APP_BAR_TARGET},
                .${DOCK_HEADER_TARGET} {
                    inset-inline-start: var(--ha-sidebar-width, 0px) !important;
                    inset-inline-end: var(${DOCK_TOP_APP_BAR_OFFSET}, 0px) !important;
                    width: auto !important;
                    transition: inset-inline-end ${DOCK_TRANSITION} !important;
                }
            `;
            deepQueryAll('.top-app-bar, .header', msr).forEach((el) => {
                const styleInfo = getComputedStyle(el);
                if (styleInfo.position !== 'fixed') return;
                if (el.classList.contains('top-app-bar')) {
                    el.classList.add(DOCK_TOP_APP_BAR_TARGET);
                } else {
                    el.classList.add(DOCK_HEADER_TARGET);
                }
                const root = el.getRootNode();
                if (!root?.getElementById) return;
                let style = root.getElementById(DOCK_TOP_APP_BAR_STYLE_ID);
                if (!style) {
                    style = document.createElement('style');
                    style.id = DOCK_TOP_APP_BAR_STYLE_ID;
                    root.appendChild(style);
                }
                style.textContent = css;
            });
        };

        const clearDockTopAppBars = () => {
            const msr = getMainSR();
            if (!msr) return;
            deepQueryAll('.' + DOCK_TOP_APP_BAR_TARGET + ', .' + DOCK_HEADER_TARGET, msr).forEach((el) => {
                el.classList.remove(DOCK_TOP_APP_BAR_TARGET, DOCK_HEADER_TARGET);
            });
            deepQueryAll('*', msr).forEach((el) => {
                el.shadowRoot?.getElementById(DOCK_TOP_APP_BAR_STYLE_ID)?.remove();
            });
        };

        const setDockTopAppBarWidth = (hostEl, dockWidth) => {
            if (!hostEl) return;
            hostEl.style.setProperty('--mdc-top-app-bar-width', 'calc(100% - var(--mdc-drawer-width, 0px) - ' + dockWidth + ')');
            syncDockTopAppBars();
            requestAnimationFrame(() => {
                hostEl.style.setProperty(DOCK_TOP_APP_BAR_OFFSET, dockWidth);
            });
        };

        const hasNativeTopAppBarWidth = (hostEl) => {
            if (!hostEl) return false;
            return getComputedStyle(hostEl).getPropertyValue('--ha-top-app-bar-width').trim() !== '';
        };

        const clearDockTopAppBarWidth = (hostEl) => {
            if (!hostEl) return;
            hostEl.style.removeProperty('--mdc-top-app-bar-width');
            hostEl.style.removeProperty(DOCK_TOP_APP_BAR_OFFSET);
            clearDockTopAppBars();
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
                        --claw-dock-transition: .25s cubic-bezier(.4,0,.2,1);
                    }
                    ha-drawer {
                        transition: padding-right var(--claw-dock-transition) !important;
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
                        transition: width var(--claw-dock-transition);
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
                        display: flex;
                        align-items: center;
                        gap: 8px;
                    }
                    #${DOCK_ID} .dock-history-panel .hist-channel-tag {
                        flex-shrink: 0;
                        font-size: 10px;
                        font-weight: 500;
                        padding: 2px 6px;
                        border-radius: 4px;
                        border: 1px solid currentColor;
                        background: transparent;
                        opacity: 0.8;
                    }
                    #${DOCK_ID} .dock-history-panel .hist-channel-tag[data-channel="WeChat"] { color: #07c160; }
                    #${DOCK_ID} .dock-history-panel .hist-channel-tag[data-channel="Feishu"] { color: #3370ff; }
                    #${DOCK_ID} .dock-history-panel .hist-channel-tag[data-channel="QQ"] { color: #12b7f5; }
                    #${DOCK_ID} .dock-history-panel .hist-channel-tag[data-channel="DingTalk"] { color: #0089ff; }
                    #${DOCK_ID} .dock-history-panel .hist-channel-tag[data-channel="WeCom"] { color: #0082ef; }
                    #${DOCK_ID} .dock-history-panel .hist-channel-tag[data-channel="XiaoYi"] { color: #ff6a00; }
                    #${DOCK_ID} .dock-history-panel .hist-channel-tag[data-channel="Voice"] { color: #18bcf2; }
                    #${DOCK_ID} .dock-history-panel .hist-channel-tag[data-channel="Desktop"] { color: #18bcf2; }
                    #${DOCK_ID} .dock-history-panel .hist-channel-tag[data-channel="Mobile"] { color: #18bcf2; }
                    #${DOCK_ID} .dock-history-panel .hist-channel-tag[data-channel="Legacy"] { color: #9e9e9e; }
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
                        position: absolute;
                        right: 8px;
                        top: 50%;
                        transform: translateY(-50%);
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        gap: 4px;
                        opacity: 0;
                        transition: opacity .15s ease;
                    }
                    #${DOCK_ID} .dock-history-panel .hist-item:hover .hist-item-actions {
                        opacity: 1;
                    }
                    #${DOCK_ID} .dock-history-panel .hist-item-actions button {
                        width: 28px;
                        height: 28px;
                        background: transparent;
                        border: none;
                        cursor: pointer;
                        padding: 0;
                        margin: 0;
                        border-radius: 6px;
                        color: var(--secondary-text-color, #888);
                        display: inline-flex;
                        align-items: center;
                        justify-content: center;
                        transition: background .15s ease, color .15s ease;
                    }
                    #${DOCK_ID} .dock-history-panel .hist-item-actions button:hover {
                        background: transparent;
                    }
                    #${DOCK_ID} .dock-history-panel .hist-item-actions button:active {
                        opacity: 0.7;
                    }
                    #${DOCK_ID} .dock-history-panel .hist-item-actions .hist-delete-btn:hover {
                        background: transparent;
                        color: var(--error-color, #db4437);
                    }
                    #${DOCK_ID} .dock-history-panel .hist-item-actions .hist-pin-btn:hover {
                        background: transparent;
                        color: var(--primary-color, #03a9f4);
                    }
                    #${DOCK_ID} .dock-history-panel .hist-item-actions .hist-pin-btn.pinned {
                        color: var(--primary-color, #03a9f4);
                    }
                    #${DOCK_ID} .dock-history-panel .hist-item:hover .hist-item-actions .hist-pin-btn.pinned {
                        opacity: 1;
                    }
                    #${DOCK_ID} .dock-history-panel .hist-item.pinned .hist-item-icon::before {
                        background: var(--primary-color, #03a9f4);
                    }
                    #${DOCK_ID} .dock-history-panel .hist-item-actions button svg {
                        width: 18px;
                        height: 18px;
                        fill: currentColor;
                    }
                    #${DOCK_ID} .dock-history-panel .hist-item-actions .hist-pin-btn svg {
                        transform: translateY(1px);
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
                    :host([dock-resizing]) #${DOCK_ID},
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
                                setDockTopAppBarWidth(hostEl, w + 'px');
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
            const trans = 'right .25s cubic-bezier(.4,0,.2,1), inset-inline-end .25s cubic-bezier(.4,0,.2,1)';
            const applyOffset = (el) => {
                if (!el) return;
                el.style.transition = trans;
                el.style.right = val || '';
                el.style.insetInlineEnd = val || '';
            };
            const msr = getMainSR();
            if (!msr) return;
            const walk = (node) => {
                if (!node?.shadowRoot) return;
                try {
                    const sr = node.shadowRoot;
                    applyOffset(sr.getElementById('fab'));
                    sr.querySelectorAll('ha-fab, ha-button-group, .fab, [class*="fab"]').forEach(applyOffset);
                    sr.querySelectorAll('ha-button').forEach(btn => {
                        const cs = getComputedStyle(btn);
                        if ((cs.position === 'fixed' || cs.position === 'absolute') && parseInt(cs.bottom) < 120) applyOffset(btn);
                    });
                    sr.querySelectorAll('*').forEach(c => {
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
            if (_dockActive) setTimeout(() => { syncDockTopAppBars(); _startFabSync(); }, 500);
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
                setDockTopAppBarWidth(mainEl, 'var(--claw-dock-width)');
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
            if (grabChat(voiceEl)) {
                _tryAutoResumeLiveTurn();
                return;
            }
            let tries = 0;
            const tid = setInterval(() => {
                if (grabChat(voiceEl) || ++tries > 50) {
                    clearInterval(tid);
                    if (tries <= 50) _tryAutoResumeLiveTurn();
                }
            }, 50);
        };
        
        let _autoResumeAttempted = false;
        const _tryAutoResumeLiveTurn = async () => {
            if (_autoResumeAttempted) return;
            _autoResumeAttempted = true;
            const settings = window.__clawSettings || {};
            if (settings.continuous_conversation) return;
            const h = getHass();
            if (!h?.connection) return;
            try {
                const snap = await h.connection.sendMessagePromise({ type: 'ha_crack/live_turn_snapshot' });
                if (clawIsLiveSnapshot(snap) && snap.conversation_id) {
                    const dock = ensureDock();
                    if (dock) {
                        _resumeConversation(dock, snap.conversation_id);
                    }
                }
            } catch(_) {}
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
            const lang = getFrontendLanguage();
            const isZh = lang.startsWith('zh');
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
            
            const diffDays = Math.floor((now.setHours(0,0,0,0) - d.setHours(0,0,0,0)) / 86400000);
            const weekdayNamesZh = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
            const weekdayNamesEn = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
            const dateKey = `day_${d.getFullYear()}_${d.getMonth()}_${d.getDate()}`;
            const weekday = isZh ? weekdayNamesZh[d.getDay()] : weekdayNamesEn[d.getDay()];
            const dateLabel = isZh 
                ? `${d.getMonth() + 1}月${d.getDate()}日 ${weekday}`
                : `${weekday}, ${d.getMonth() + 1}/${d.getDate()}`;
            return { key: dateKey, label: dateLabel, order: 10 + diffDays };
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
            const pinIcon = '<svg viewBox="0 0 24 24"><path d="M14 4v6.5l2 2V14H8v-1.5l2-2V4m4 0h-4m2 10v6" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';
            const deleteIcon = '<svg viewBox="0 0 24 24"><path d="M6 7h12m-9 0V5h6v2m-7 3v8m4-8v8m4-11-.8 13H8.8L8 7" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';
            const chevronIcon = '<svg class="hist-section-chevron" viewBox="0 0 24 24"><path d="M11.9999 13.1714L16.9497 8.22168L18.3639 9.63589L11.9999 15.9999L5.63599 9.63589L7.0502 8.22168L11.9999 13.1714Z" fill="currentColor"/></svg>';
            const showActiveSelection = _historySelectedConversationId && !_historySelectionHighlightConsumed;

            const renderItem = (c, isPinned) => {
                const activeClass = showActiveSelection && c.conversation_id === _historySelectedConversationId ? ' active' : '';
                const pinnedClass = isPinned ? ' pinned' : '';
                const channelTag = c.channel ? '<span class="hist-channel-tag" data-channel="' + c.channel + '">' + c.channel + '</span>' : '';
                const rawTitle = (c.summary || historyText('conversation')).replace(/</g, '&lt;');
                const cleanTitle = rawTitle.replace(/^[\s\p{P}\p{S}]+/u, '').replace(/[\s\p{P}\p{S}]+$/u, '');
                const truncTitle = cleanTitle.length > 20 ? cleanTitle.slice(0, 20).replace(/[\s\p{P}\p{S}]+$/u, '') + '…' : cleanTitle;
                return '<div class="hist-item' + activeClass + pinnedClass + '" data-conv-id="' + c.conversation_id + '">'
                    + '<div class="hist-item-icon">' + chatIcon + '</div>'
                    + '<div class="hist-item-content">'
                    + '<div class="hist-item-title">' + truncTitle + channelTag + '</div>'
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

        let _resumeToken = 0;

        const _resumeConversation = async (dock, convId) => {
            const h = getHass();
            if (!h?.connection) return;
            const token = ++_resumeToken;
            _historySelectedConversationId = convId;
            _historySelectionHighlightConsumed = false;
            window.__clawResumeInProgress = true;

            let turns = [];
            let historyTokens = 0;
            try {
                const r = await h.connection.sendMessagePromise({ type: 'ha_crack/chat_history_get', conversation_id: convId, max_turns: 50, display_depth: 0 });
                if (token !== _resumeToken) return;
                turns = r?.turns || [];
                historyTokens = r?.tokens_used || 0;
            } catch (_) {}

            if (token !== _resumeToken) return;

            try {
                await h.connection.sendMessagePromise({
                    type: 'ha_crack/chat_history_resume',
                    conversation_id: convId,
                    window_id: getHistoryWindowId()
                });
            } catch (_) {}

            if (token !== _resumeToken) return;

            const conversation = [];
            const welcomeText = h.localize?.('ui.dialogs.voice_command.how_can_i_help') || '';
            conversation.push({ who: 'hass', text: welcomeText, thinking: '', tool_calls: {} });
            for (const t of turns) {
                if (t.user) {
                    conversation.push({ who: 'user', text: t.user, thinking: '', tool_calls: {} });
                }
                if (t.assistant) {
                    conversation.push({
                        who: 'hass',
                        text: t.assistant_display || t.assistant,
                        agent_id: t.agent_id || '',
                        agent_name: t.agent_name || '',
                        thinking: '',
                        tool_calls: {},
                    });
                }
            }

            if (conversation.length <= 1) {
                window.__clawResumeInProgress = false;
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
                requestAnimationFrame(() => {
                    requestAnimationFrame(() => {
                        if (token !== _resumeToken) return;
                        if (state) state.resetting = false;
                        window.__clawResumeInProgress = false;
                        window.dispatchEvent(new CustomEvent('claw-chat-updated'));
                    });
                });
            } else if (state) {
                state.resetting = false;
                window.__clawResumeInProgress = false;
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
            _autoResumeAttempted = false;
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
                clearDockTopAppBarWidth(mainEl);
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
            proto.updated = function(changed) {
                originalUpdated?.call(this, changed);
                attachScrollWatcher.call(this);
                if (this.shadowRoot) {
                    try { window.dispatchEvent(new CustomEvent('claw-chat-updated', { detail: this })); } catch(e) {}
                }
                if (state.resetting) return;
                if (!settings.continuous_conversation) return;
                if (Array.isArray(this._conversation) && this._conversation.length > 0 && !isFreshConversation(this._conversation)) {
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
                    const restored = state.conversation.map(m => ({
                        ...m,
                        tool_calls: {},
                        thinking: ''
                    }));
                    if (restored[0]?.who !== 'hass') {
                        const wt = this.hass?.localize?.('ui.dialogs.voice_command.how_can_i_help') || '';
                        restored.unshift({ who: 'hass', text: wt, thinking: '', tool_calls: {} });
                    }
                    this._conversation = restored;
                }
                if (state.conversationId) {
                    this._conversationId = state.conversationId;
                }
            };
            proto.disconnectedCallback = function() {
                if (settings.continuous_conversation) {
                    if (Array.isArray(this._conversation) && this._conversation.length > 0 && !isFreshConversation(this._conversation)) {
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
            if (typeof originalAddMessage === 'function') {
                proto._addMessage = function(message) {
                    if (typeof window.__clawTransformMessage === 'function') {
                        message = window.__clawTransformMessage(message);
                    }
                    originalAddMessage.call(this, message);
                    const conv = Array.isArray(this._conversation) ? this._conversation : [];
                    const prev = conv[conv.length - 2];
                    if (prev?.who === 'user' && String(prev.text || '').trim() === '/new') {
                        state.resetting = true;
                        window.__clawIsNewReset = true;
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
                        if (typeof window.__clawPlaySound === 'function') window.__clawPlaySound('new');
                        return;
                    }
                    if (settings.continuous_conversation) {
                        if (Array.isArray(this._conversation) && this._conversation.length > 0 && !isFreshConversation(this._conversation)) {
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
            if (!fileUploadEnabled) pendingFiles.length = 0;
            deepQueryAll('ha-assist-chat').forEach((chat) => {
                const sr = chat.shadowRoot;
                if (!sr) return;
                const slot = sr.querySelector('#claw-attach-slot');
                if (slot) slot.style.display = fileUploadEnabled ? '' : 'none';
                if (!fileUploadEnabled) {
                    const zone = sr.querySelector('.claw-upload-zone');
                    if (zone) zone.classList.remove('active');
                    const popup = sr.querySelector('.claw-upload-popup');
                    if (popup) { popup.innerHTML = ''; popup.style.display = 'none'; }
                    updateAttachBtn(sr);
                }
            });
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

        // Render the pending-files preview/badge into EVERY open chat window
        // (sidebar dock + integration more-info), so the feature is mirrored
        // rather than living in whichever window happened to be first.
        const renderAllPreviews = () => {
            deepQueryAll('ha-assist-chat').forEach((chat) => {
                const sr = chat.shadowRoot;
                if (sr) { renderPreviewBar(sr); updateAttachBtn(sr); }
            });
        };

        const uploadFile = async (fileObj) => {
            fileObj.status = 'uploading'; fileObj.progress = 0;
            renderAllPreviews();
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
                            renderAllPreviews();
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
            renderAllPreviews();
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
            renderAllPreviews();
        };

        const buildAttachmentTags = () => {
            return pendingFiles.filter(f => f.status==='done'&&f.serverPath).map(f => `[ATTACHMENT:${f.serverMime}:${f.serverPath}]`).join('');
        };
        const clearPending = () => {
            pendingFiles.forEach(f => { if(f.previewUrl) URL.revokeObjectURL(f.previewUrl); });
            pendingFiles.length = 0;
        };

        const installUploadUI = () => {
          deepQueryAll('ha-assist-chat').forEach((chat) => {
            if (!chat?.shadowRoot) return;
            const sr = chat.shadowRoot;
            if (sr.querySelector('.claw-attach-btn')) return;
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
          });
        };

        const ATTACH_RE = /\[ATTACHMENT:[^\]]+\]/g;
        window.__clawTransformMessage = function(msg) {
            if (msg && msg.who === 'user' && typeof msg.text === 'string' && ATTACH_RE.test(msg.text)) {
                const realLen = msg.text.length;
                const count = (msg.text.match(ATTACH_RE) || []).length;
                const clean = msg.text.replace(ATTACH_RE, '').trim();
                const badge = '+' + count + ' file' + (count > 1 ? 's' : '');
                return {...msg, text: clean ? clean + ' `' + badge + '`' : '`' + badge + '`', _realChars: realLen};
            }
            return msg;
        };

        customElements.whenDefined('ha-assist-chat').then(() => {
            const ctor = customElements.get('ha-assist-chat');
            const proto = ctor?.prototype;
            if (!proto || proto.__clawUploadPatched) return;
            proto.__clawUploadPatched = true;
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
                        renderAllPreviews();
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
                        renderAllPreviews();
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
        let liveEndCheckPending = false;
        let liveEndCheckAt = 0;

        const resetState = (removeBar, keepTokens = false) => {
            phase = S_IDLE;
            turnStart = null;
            turnEnd = null;
            window.__clawLiveStreamSubscribed = false;
            window.__clawLiveStreamChecked = false;
            window.__clawLiveConvId = null;
            window.__clawLiveText = '';
            if (!keepTokens) {
                totalChars = 0;
                windowStart = Date.now();
                windowTimeLabel = '0s';
                hasTurn = false;
                window.__clawToolActivities = [];
                window.__clawToolActivitySeen = false;
                window.__clawTurnParts = [];
                _mdStreamActive = false;
                _turnEnded = false;
            }
            if (tickTimer) { clearInterval(tickTimer); tickTimer = null; }
            if (removeBar) {
                deepQueryAll('ha-assist-chat').forEach(c => c.shadowRoot?.getElementById(BAR_ID)?.remove());
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
                    // Tool results are truncated/compressed before sending to LLM
                    // Cap each message contribution to reduce anxiety from large tool outputs
                    const rawChars = m._realChars || txt.length;
                    const isToolResult = m.who === 'user' && txt.includes('"tool_result"');
                    const msgChars = isToolResult ? Math.min(rawChars, 2000) : Math.min(rawChars, 8000);
                    c += msgChars;
                    // Thinking is usually not sent back, count minimally
                    if (m.thinking) c += Math.min(m.thinking.length, 500);
                    // Tool calls are compact, but cap anyway
                    if (m.tool_calls) try { c += Math.min(JSON.stringify(m.tool_calls).length, 1000); } catch(e){}
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


        const endTurn = () => {
            if (phase === S_IDLE) return;
            turnEnd = Date.now();
            phase = S_IDLE;
            window.__clawLiveStreamSubscribed = false;
            window.__clawLiveStreamChecked = false;
            window.__clawLiveConvId = null;
            window.__clawLiveText = '';
            if (tickTimer) { clearInterval(tickTimer); tickTimer = null; }
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
                    window.__clawToolActivities = [];
                    window.__clawToolActivitySeen = false;
                    window.__clawTurnParts = [];
                    _turnEnded = false;
                    const _lastMsr = deepQuery('ha-assist-chat')?.shadowRoot?.querySelector('.message.hass:last-of-type ha-markdown')?.shadowRoot;
                    if (_lastMsr) {
                        _lastMsr.querySelectorAll('.claw-ta-panel, .claw-ta-card').forEach(n => n.remove());
                    }
                    const deliver = (ev) => {
                        const t = ev.type, d = ev.data;
                        if (t === 'intent-progress' && d?.chat_log_delta) {
                            clawApplyLiveDelta(d.chat_log_delta);
                            render();
                        } else if (t === 'run-start') {
                            phase = S_THINKING;
                            render();
                        } else if (t === 'intent-end' || t === 'run-end' || t === 'error' || t === 'stream_end') {
                            if (!this.__clawSoundPlayed) {
                                let isError = (t === 'error');
                                if (!isError && t === 'intent-end' && d?.intent_output?.response?.response_type === 'error') {
                                    isError = true;
                                }
                                if (isError) {
                                    this.__clawSoundPlayed = true;
                                    if (typeof window.__clawPlaySound === 'function') window.__clawPlaySound('error');
                                } else if (t === 'intent-end' || t === 'run-end' || t === 'stream_end') {
                                    this.__clawSoundPlayed = true;
                                    if (typeof window.__clawOnStreamEnd === 'function') window.__clawOnStreamEnd();
                                }
                            }
                            if (t === 'intent-end' || t === 'run-end' || t === 'error' || t === 'stream_end') {
                                this.__clawSoundPlayed = false;
                                endTurn();
                            }
                        }
                        // ha-assist-chat's own handler does `const unsub = await
                        // runAssistPipeline(...)` and calls unsub() from inside this
                        // callback on intent-end/error. Any throw there (e.g. the unsub
                        // TDZ when an event lands before the await resolves) must NOT
                        // escape into connection._handleMessage's forEach, or it kills
                        // the entire live WS dispatch for every subscriber.
                        try {
                            callback(ev);
                        } catch (err) {
                            console.debug('[claw] assist-chat callback threw, isolated from WS dispatch', err);
                        }
                    };
                    // Hold delivery until the subscription promise has resolved so that
                    // ha-assist-chat's `unsub` binding is initialized before we feed it
                    // any event (otherwise intent-end on refresh hits a TDZ and crashes
                    // the stream). The setTimeout(0) guarantees we run *after* the
                    // component's await-continuation that assigns unsub.
                    let _subReady = false;
                    const _pending = [];
                    const wrappedCb = (ev) => {
                        if (!_subReady) { _pending.push(ev); return; }
                        deliver(ev);
                    };
                    const _subP = origSubscribe(wrappedCb, msg, ...rest);
                    Promise.resolve(_subP).catch(() => {}).then(() => {
                        setTimeout(() => {
                            _subReady = true;
                            while (_pending.length) deliver(_pending.shift());
                        }, 0);
                    });
                    return _subP;
                }
                return origSubscribe(callback, msg, ...rest);
            };

            hass.connection.subscribeEvents(ev => {
                const d = ev.data;
                if (d?.phase) {
                    hasTurn = true;
                    render();
                }
            }, 'ha_crack_live_progress').catch(() => {});
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

        const renderBarInto = (sr, vals) => {
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
            const $ = (r) => bar.querySelector(`[data-r="${r}"]`);
            $('tok').textContent = (hasTurn ? fmt(vals.tk) : '--') + ' / ' + fmtR(vals.ctxW);
            $('bar').innerHTML = vals.barSvg;
            const pctEl = $('pct');
            pctEl.textContent = hasTurn ? Math.max(1, vals.pct)+'%' : '--%';
            pctEl.style.color = vals.barColor;
            $('win').textContent = hasTurn ? vals.windowTimer : '--';
            $('timer').textContent = vals.timer;
        };

        const render = () => {
            const chats = deepQueryAll('ha-assist-chat').filter(c => c?.shadowRoot);
            if (!chats.length) return;
            if (!settings.enable_context_status_bar) {
                chats.forEach(c => c.shadowRoot.getElementById(BAR_ID)?.remove());
                return;
            }

            const currentChars = calcCurrentChars();
            if (currentChars > totalChars) totalChars = currentChars;
            const tk = Math.round(totalChars/CPT*0.25) + (hasTurn ? BASE_PROMPT_TOKENS : 0);
            const ctxW = CTX;
            const pct = Math.min(100, Math.round(tk/ctxW*100));
            const pc = pctColor(pct);
            const active = phase !== S_IDLE;
            const now = Date.now();
            let timer = '--';
            let windowTimer = windowTimeLabel;
            if (hasTurn && active && windowStart) windowTimer = fwindow(Math.round((now - windowStart) / 1000));
            else if (hasTurn && turnEnd && windowStart) windowTimer = fwindow(Math.round((turnEnd - windowStart) / 1000));
            if (active && turnStart) timer = ftime(Math.round((now-turnStart)/1000));
            else if (turnStart && turnEnd) timer = ftime(Math.round((turnEnd-turnStart)/1000));

            const barW = 14;
            const cellW = 5, cellH = 9, dotSize = 1, dotGap = 2;
            const svgW = barW * cellW;
            let rects = '';
            if (hasTurn) {
                const totalHalf = barW * 2;
                const filledHalf = pct > 0 ? Math.min(totalHalf, Math.max(1, Math.round(pct/100*totalHalf))) : 0;
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
            const barSvg = `<svg width="${svgW}" height="${cellH}" viewBox="0 0 ${svgW} ${cellH}" style="display:block">${rects}</svg>`;
            const barColor = hasTurn ? pc : 'var(--secondary-text-color)';
            const vals = { tk, ctxW, pct, barSvg, barColor, windowTimer, timer };
            chats.forEach((chat) => renderBarInto(chat.shadowRoot, vals));
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

        const probeLiveEnd = async () => {
            const convId = window.__clawLiveConvId;
            if (!window.__clawLiveStreamSubscribed || !convId || phase === S_IDLE) return;
            const now = Date.now();
            if (liveEndCheckPending || now - liveEndCheckAt < 2500) return;
            liveEndCheckPending = true;
            liveEndCheckAt = now;
            try {
                const snapshot = await hass.connection.sendMessagePromise({ type: 'ha_crack/live_turn_snapshot' });
                if (!clawIsLiveSnapshot(snapshot) || snapshot.conversation_id !== convId) {
                    window.__clawLiveStreamSubscribed = false;
                    window.__clawLiveStreamChecked = false;
                    endTurn();
                }
            } catch(e) {
            } finally {
                liveEndCheckPending = false;
            }
        };

        const clawApplyLiveDelta = (delta) => {
            if (!delta) return;
            if (delta.role === 'assistant') phase = S_REPLYING;
            if ((delta.content || delta._tts_skip_content) && !delta._claw_thinking) {
                const streamText = delta.content || delta._tts_skip_content || '';
                _mdStreamActive = true;
                totalChars += streamText.length;
                _pushTurnText(streamText);
                if (typeof window.__clawOnStreamDelta === 'function') window.__clawOnStreamDelta(delta);
                _scheduleToolRender();
            }
            if (delta._claw_thinking) {
                if (window.__clawToolDetailsEnabled) {
                    const markerId = '_thinking_singleton';
                    const parts = window.__clawTurnParts || (window.__clawTurnParts = []);
                    if (!parts.some(p => p.type === 'tool' && p.id === markerId)) {
                        parts.push({ type: 'tool', id: markerId });
                    }
                    let existing = window.__clawToolActivities.find(a => a.tool_name === '_thinking');
                    if (existing) {
                        existing._thinkText = delta._claw_thinking;
                        existing._endTime = Date.now();
                    } else {
                        window.__clawToolActivities.push({
                            id: markerId,
                            tool_call_id: markerId,
                            marker_id: markerId,
                            tool_name: '_thinking',
                            tool_args: {},
                            result: 'ok',
                            error: null,
                            _thinkText: delta._claw_thinking,
                            _startTime: Date.now(),
                            _endTime: Date.now(),
                        });
                    }
                    _scheduleToolRender();
                } else {
                    window.__clawThinkingText = delta._claw_thinking;
                    _scheduleToolRender();
                }
            }
            if (delta._claw_tool_info) {
                _mdStreamActive = true;
                const ti = delta._claw_tool_info;
                window.__clawToolActivitySeen = true;
                const act = _upsertToolActivity(ti);
                _pushTurnTool(act.marker_id || act.tool_call_id);
                phase = S_TOOL;
                _scheduleToolRender();
            }
            if (delta._claw_tool_result) {
                _mdStreamActive = true;
                const tr = delta._claw_tool_result;
                window.__clawToolActivitySeen = true;
                const completed = _completeToolActivity(tr);
                const parsed = completed ? completed.result : tr.tool_result;
                if (parsed?._navigate_to) setTimeout(() => softNavigate(parsed._navigate_to), 500);
                _scheduleToolRender();
            }
            if (delta.tool_calls) {
                _mdStreamActive = true;
                if (window.__clawToolActivitySeen) return;
                phase = S_TOOL; totalChars += JSON.stringify(delta.tool_calls).length;
                const calls = Array.isArray(delta.tool_calls) ? delta.tool_calls : [delta.tool_calls];
                for (const tc of calls) {
                    const act = _upsertToolActivity({
                        tool_call_id: tc.id || tc.tool_call_id,
                        tool_name: tc.tool_name || tc.name || 'tool',
                        tool_args: tc.tool_args || tc.arguments || tc.tool_input,
                    });
                    _pushTurnTool(act.marker_id || act.tool_call_id);
                }
                _scheduleToolRender();
            }
            if (delta.tool_result !== undefined) {
                if (window.__clawToolActivitySeen) return;
                phase = S_TOOL;
                totalChars += JSON.stringify(delta.tool_result).length;
                const completed = _completeToolActivity(delta);
                const parsed = completed ? completed.result : delta.tool_result;
                if (parsed?._navigate_to) {
                    setTimeout(() => softNavigate(parsed._navigate_to), 500);
                }
                _scheduleToolRender();
            }
            if (delta.tool_call_id && !delta.tool_calls && delta.tool_result === undefined) phase = S_TOOL;
        };

        const startStatusBar = () => {
            if (statusLoop) return;
            statusLoop = setInterval(async () => {
                if (!settings.enable_context_status_bar) return;
                const chat = deepQuery('ha-assist-chat');
                if (chat?.shadowRoot) {
                    installHooks();
                    render();
                    probeLiveEnd();
                    
                    if (!window.__clawLiveStreamSubscribed && !window.__clawLiveStreamChecked && !window.__clawResumeInProgress) {
                        window.__clawLiveStreamChecked = true;
                        try {
                            const snapshot = await hass.connection.sendMessagePromise({ type: 'ha_crack/live_turn_snapshot' });
                            
                            if (!clawIsLiveSnapshot(snapshot) && snapshot?.conversation_id && settings.continuous_conversation) {
                                const state = window.__clawAssistChatState;
                                if (state?.conversationId === snapshot.conversation_id && !window.__clawResumeInProgress) {
                                    try {
                                        const histResp = await hass.connection.sendMessagePromise({
                                            type: 'ha_crack/chat_history_get',
                                            conversation_id: snapshot.conversation_id,
                                            max_turns: 50,
                                            display_depth: 0
                                        });
                                        if (histResp?.turns?.length > 0) {
                                            const newConv = [{ who: 'hass', text: chat.hass?.localize?.('ui.dialogs.voice_command.how_can_i_help') || '', thinking: '', tool_calls: {} }];
                                            for (const t of histResp.turns) {
                                                if (t.user) newConv.push({ who: 'user', text: t.user, thinking: '', tool_calls: {} });
                                                if (t.assistant) newConv.push({ who: 'hass', text: t.assistant_display || t.assistant, thinking: '', tool_calls: {} });
                                            }
                                            chat._conversation = newConv;
                                            chat.requestUpdate?.('_conversation');
                                            state.conversation = newConv;
                                            state.persist?.();
                                        }
                                    } catch(e) {}
                                }
                            }
                            
                            if (clawIsLiveSnapshot(snapshot) && snapshot.conversation_id) {
                                window.__clawLiveStreamSubscribed = true;
                                window.__clawLiveConvId = snapshot.conversation_id;
                                window.__clawTurnParts = [];
                                window.__clawToolActivities = [];
                                window.__clawToolActivitySeen = false;
                                window.__clawThinkingText = '';
                                window.__clawLiveText = '';
                                _turnEnded = false;
                                _mdStreamActive = true;

                                const _lastMsr = chat.shadowRoot?.querySelector('.message.hass:last-of-type ha-markdown')?.shadowRoot;
                                if (_lastMsr) _lastMsr.querySelectorAll('.claw-ta-panel, .claw-ta-card').forEach(n => n.remove());

                                hasTurn = true;
                                turnStart = snapshot.turn_start_time ? (snapshot.turn_start_time * 1000) : Date.now();
                                turnEnd = null;
                                if (snapshot.window_start_time) {
                                    windowStart = snapshot.window_start_time * 1000;
                                    windowTimeLabel = fwindow(Math.round((Date.now() - windowStart) / 1000));
                                }
                                const snapshotPhase = snapshot.phase || 'thinking';
                                phase = snapshotPhase === 'replying' ? S_REPLYING : snapshotPhase === 'tool_call' ? S_TOOL : S_THINKING;
                                totalChars = calcCurrentChars();
                                if (!tickTimer) tickTimer = setInterval(render, 200);
                                _scheduleToolRender();
                                render();

                                hass.connection.subscribeMessage(
                                    (evt) => {
                                        if (!evt) return;
                                        const evtType = evt.event_type || evt.type;
                                        const evtData = evt.data || {};

                                        if (evtType === 'stream_end' || evtType === 'run-end' || evtType === 'intent-end') {
                                            window.__clawLiveStreamSubscribed = false;
                                            if (evtType === 'intent-end') {
                                                const resp = evtData.intent_output?.response?.speech?.plain?.speech;
                                                const hasText = (window.__clawTurnParts || []).some(p => p.type === 'text' && p.text);
                                                if (resp && !hasText) _pushTurnText(resp);
                                            }
                                            _mdStreamActive = false;
                                            _turnEnded = true;
                                            _scheduleToolRender();
                                            endTurn();
                                            return;
                                        }

                                        if (evtType === 'intent-progress') {
                                            const delta = evtData.chat_log_delta || evtData.delta || evtData;
                                            clawApplyLiveDelta(delta);
                                            render();
                                        }
                                    },
                                    { type: 'ha_crack/subscribe_live_stream', conversation_id: snapshot.conversation_id }
                                );
                            }
                        } catch(e) {}
                    }
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
                const safeFamily = String(fontFamily).replace(/[\\'";{}]/g, '');
                const safeSrc = String(src).replace(/[\\'";{}]/g, '');
                const css = `@font-face { font-family: '${safeFamily}'; src: url('${safeSrc}'); }`;
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
            if (!pollInterval) {
                const hass = getHass();
                if (hass?.connection) {
                    pollInterval = setInterval(() => pollPendingJS(getHass() || hass), 1500);
                    pollPendingJS(hass);
                }
            }
        } else {
            if (pollInterval) {
                clearInterval(pollInterval);
                pollInterval = null;
            }
        }
    });

    function setupSlashCommands(hass) {
        if (!hass?.connection) return;
        if (window.__clawSlashSetup) return;
        window.__clawSlashSetup = true;

        let commands = [];
        let popup = null;
        let selectedIndex = 0;
        let currentInput = null;

        const fetchCommands = async () => {
            try {
                const r = await hass.connection.sendMessagePromise({ type: 'ha_crack/get_commands' });
                commands = r?.commands || [];
            } catch(e) {}
        };
        fetchCommands();
        setInterval(fetchCommands, 60000);

        const isZh = () => {
            try {
                const lang = hass?.language || 
                             hass?.locale?.language ||
                             localStorage.getItem('selectedLanguage') || 
                             document.documentElement.lang || 
                             navigator.language || '';
                return lang.toLowerCase().startsWith('zh');
            } catch(e) { return false; }
        };

        const createPopup = (container, haInput) => {
            if (popup) popup.remove();
            popup = document.createElement('div');
            popup.id = 'claw-slash-popup';
            popup.style.cssText = 'position:absolute;max-height:280px;overflow-y:auto;background:var(--card-background-color,#fff);border:1px solid var(--divider-color,#e0e0e0);border-radius:5px 5px 0 0;box-shadow:0 -2px 8px rgba(0,0,0,0.1);z-index:9999;display:none;';
            container.appendChild(popup);
            popup._haInput = haInput;
            popup.addEventListener('mousedown', (e) => {
                e.preventDefault();
                e.stopPropagation();
                const item = e.target.closest('.claw-slash-item');
                if (item) selectCommand(item.dataset.name);
            });
            popup.addEventListener('mouseover', (e) => {
                const item = e.target.closest('.claw-slash-item');
                if (item) {
                    const idx = parseInt(item.dataset.index);
                    if (idx !== selectedIndex) {
                        selectedIndex = idx;
                        updateSelection();
                    }
                }
            });
            return popup;
        };
        
        const positionPopup = () => {
            if (!popup || !popup._haInput) return;
            const rect = popup._haInput.getBoundingClientRect();
            const containerRect = popup.parentElement.getBoundingClientRect();
            popup.style.left = (rect.left - containerRect.left) + 'px';
            popup.style.width = rect.width + 'px';
            popup.style.bottom = (containerRect.bottom - rect.top + 4) + 'px';
        };

        const updateSelection = () => {
            if (!popup) return;
            popup.querySelectorAll('.claw-slash-item').forEach((el, i) => {
                el.style.background = i === selectedIndex ? 'var(--primary-color,#03a9f4)' : '';
                el.style.color = i === selectedIndex ? '#fff' : '';
            });
        };

        const renderItems = (filter) => {
            if (!popup) return;
            const zh = isZh();
            const filtered = commands.filter(c => {
                const q = filter.toLowerCase();
                return c.name.toLowerCase().includes(q) || 
                       (c.aliases || []).some(a => a.toLowerCase().includes(q)) ||
                       c.description.toLowerCase().includes(q) ||
                       (c.description_zh || '').toLowerCase().includes(q);
            });
            if (filtered.length === 0) {
                popup.style.display = 'none';
                return;
            }
            selectedIndex = Math.min(selectedIndex, filtered.length - 1);
            popup.innerHTML = filtered.map((c, i) => {
                const desc = zh && c.description_zh ? c.description_zh : c.description;
                const sel = i === selectedIndex ? 'background:var(--primary-color,#03a9f4);color:#fff;' : '';
                return `<div class="claw-slash-item" data-index="${i}" data-name="${c.name}" style="padding:8px 12px;cursor:pointer;display:flex;align-items:center;gap:16px;${sel}"><span style="font-weight:500;white-space:nowrap;flex-shrink:0;">/${c.name}</span><span style="opacity:0.7;font-size:0.9em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0;">${desc}</span></div>`;
            }).join('');
            popup.style.display = 'block';
            positionPopup();
        };

        let chatRef = null;
        let nativeInputRef = null;

        const selectCommand = (name) => {
            const val = '/' + name + ' ';
            hidePopup();
            const native = nativeInputRef || chatRef?._messageInput?.shadowRoot?.querySelector('input');
            if (native) {
                native.focus();
                native.value = val;
                native.dispatchEvent(new InputEvent('input', { bubbles: true, composed: true, data: val }));
                native.setSelectionRange(val.length, val.length);
            }
        };

        const hidePopup = () => {
            if (popup) popup.style.display = 'none';
            selectedIndex = 0;
        };

        const handleInput = (e) => {
            const inp = e.target;
            const val = inp.value || '';
            if (val.startsWith('/') && !val.includes(' ')) {
                currentInput = inp;
                const chat = chatRef || deepQuery('ha-assist-chat');
                const sr = chat?.shadowRoot;
                if (!sr) return;
                const inputDiv = sr.querySelector('div.input[slot="primaryAction"]');
                const haInput = sr.querySelector('ha-input#message-input');
                if (!popup || !sr.contains(popup)) {
                    if (inputDiv && haInput) {
                        inputDiv.style.position = 'relative';
                        popup = createPopup(inputDiv, haInput);
                    }
                }
                renderItems(val.slice(1));
            } else {
                hidePopup();
            }
        };

        const handleKeydown = (e) => {
            if (!popup || popup.style.display === 'none') return;
            const items = popup.querySelectorAll('.claw-slash-item');
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                selectedIndex = (selectedIndex + 1) % items.length;
                renderItems(currentInput?.value?.slice(1) || '');
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                selectedIndex = (selectedIndex - 1 + items.length) % items.length;
                renderItems(currentInput?.value?.slice(1) || '');
            } else if (e.key === 'Enter' || e.key === 'Tab') {
                const sel = items[selectedIndex];
                if (sel) {
                    e.preventDefault();
                    e.stopPropagation();
                    selectCommand(sel.dataset.name);
                }
            } else if (e.key === 'Escape') {
                hidePopup();
            }
        };

        const installHandler = () => {
            const chat = deepQuery('ha-assist-chat');
            if (!chat?.shadowRoot) return;
            chatRef = chat;
            const sr = chat.shadowRoot;
            const haInput = sr.querySelector('ha-input#message-input');
            if (!haInput || haInput.__clawSlashBound) return;
            haInput.__clawSlashBound = true;
            const native = haInput.shadowRoot?.querySelector('input') || haInput.querySelector('input');
            nativeInputRef = native;
            const onInput = (e) => {
                currentInput = e.target;
                nativeInputRef = e.target;
                handleInput(e);
            };
            const checkHide = () => {
                const val = native?.value || haInput?.value || '';
                if (!val || !val.startsWith('/') || val.includes(' ')) {
                    hidePopup();
                }
            };
            const onDocumentClick = (e) => {
                if (!popup || popup.style.display === 'none') return;
                if (!popup.contains(e.target) && e.target !== native && !haInput.contains(e.target)) {
                    hidePopup();
                }
            };
            if (native) {
                native.addEventListener('input', onInput);
                native.addEventListener('keydown', handleKeydown, true);
            }
            haInput.addEventListener('input', onInput);
            haInput.addEventListener('keydown', handleKeydown, true);
            document.addEventListener('click', onDocumentClick);
            setInterval(checkHide, 300);
        };

        window.addEventListener('claw-chat-updated', installHandler);
        setInterval(installHandler, 3000);
        installHandler();
    }
    
})();
