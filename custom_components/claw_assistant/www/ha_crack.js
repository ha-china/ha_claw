(function() {
    'use strict';
    
    const HACRACK_VERSION = '20260509-assist-dock-v30';
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
    
    function getMainPanel() {
        return document.querySelector('home-assistant')?.shadowRoot?.querySelector('home-assistant-main')?.shadowRoot;
    }
    
    function getSidebar() {
        return getMainPanel()?.querySelector('ha-sidebar')?.shadowRoot;
    }
    
    function deepQuery(selector, root = document) {
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
        setupContinuousConversation(hass);
        setupAssistRightDock(hass);
        setupContextStatusBar(hass);
        setupFileUpload(hass);
        setupFrontendBridge(hass);
        setTimeout(() => {
            if (window.HACrack?.preventAssistDialogClose) {
                window.HACrack.preventAssistDialogClose();
            }
        }, 100);
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

        conn.subscribeEvents(ev => {
            const d = ev.data;
            if (d?.id && d?.code) runTask(d);
        }, 'claw_frontend_exec').catch(() => {});

        setInterval(async () => {
            try {
                const c = getConn();
                const r = await c.sendMessagePromise({ type: 'ha_crack/frontend_exec_poll' });
                if (!r?.tasks?.length) return;
                for (const task of r.tasks) await runTask(task);
            } catch(_) {}
        }, 2000);

    }

    function setupGoalContinuationStream() {}

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
                        background: var(--primary-background-color, #fff);
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
                    #${DOCK_ID} .dock-header .dock-title ha-dropdown {
                        --ha-select-height: 32px;
                    }
                    #${DOCK_ID} .dock-header .dock-close {
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
                    }
                    #${DOCK_ID} .dock-header .dock-close:hover {
                        opacity: 1;
                        background: rgba(255,255,255,.12);
                    }
                    #${DOCK_ID} .dock-header .dock-close svg {
                        width: 20px;
                        height: 20px;
                        fill: var(--icon-primary-color, currentColor);
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
                dock.innerHTML = '<div class="dock-resize"></div><div class="dock-header"><div class="dock-title">Assist</div><div class="dock-close" title="Close"><svg viewBox="0 0 24 24"><path d="M19,6.41L17.59,5L12,10.59L6.41,5L5,6.41L10.59,12L5,17.59L6.41,19L12,13.41L17.59,19L19,17.59L13.41,12L19,6.41Z"/></svg></div></div><div class="dock-body"></div>';
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

        const grabChat = (voiceEl) => {
            const dock = ensureDock();
            if (!dock) return false;
            const sr = voiceEl?.shadowRoot;
            if (!sr) return false;
            const chat = sr.querySelector('ha-assist-chat');
            if (!chat) return false;

            const dockHeader = dock.querySelector('.dock-header');
            const titleEl = dockHeader.querySelector('.dock-title');
            if (titleEl && !titleEl.querySelector('[slot="title"]')) {
                const dialogHeader = sr.querySelector('ha-dialog-header');
                if (dialogHeader) {
                    const titleSlot = dialogHeader.querySelector('[slot="title"]');
                    if (titleSlot) {
                        titleEl.innerHTML = '';
                        titleEl.appendChild(titleSlot);
                    }
                }
            }

            const dockBody = dock.querySelector('.dock-body');
            dockBody.innerHTML = '';
            dockBody.appendChild(chat);
            dock.setAttribute('open', '');

            const patchMarkdownIndent = (root) => {
                if (!root) return;
                root.querySelectorAll('ha-markdown').forEach(md => {
                    if (md.shadowRoot && !md.shadowRoot.getElementById('claw-md-fix')) {
                        const s = document.createElement('style');
                        s.id = 'claw-md-fix';
                        s.textContent = 'ha-markdown-element > :is(ol, ul) { padding-inline-start: 2.15em !important; }';
                        md.shadowRoot.appendChild(s);
                    }
                });
            };
            const chatSR = chat.shadowRoot;
            if (chatSR) {
                patchMarkdownIndent(chatSR);
                if (!chatSR.__clawMdObs) {
                    chatSR.__clawMdObs = new MutationObserver(() => patchMarkdownIndent(chatSR));
                    chatSR.__clawMdObs.observe(chatSR, { childList: true, subtree: true });
                }
            }

            neutralizeVoiceDialog(voiceEl);
            voiceEl.style.display = 'none';

            const mainEl = document.querySelector('home-assistant')?.shadowRoot?.querySelector('home-assistant-main');
            if (mainEl) {
                mainEl.setAttribute('dock-open', '');
                mainEl.style.setProperty('--mdc-top-app-bar-width', 'calc(100% - var(--mdc-drawer-width, 0px) - var(--claw-dock-width))');
            }
            document.body.style.overflow = '';

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

        const closeDock = () => {
            _dockActive = false;
            _dockVoiceEl = null;
            const msr = getMainSR();
            if (!msr) return;
            const dock = msr.getElementById(DOCK_ID);
            if (dock) {
                dock.removeAttribute('open');
                dock.querySelector('.dock-body').innerHTML = '';
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
        const refreshSettings = async () => {
            try {
                const r = await hass.connection.sendMessagePromise({ type: 'ha_crack/get_settings' });
                settings.continuous_conversation = !!r?.continuous_conversation;
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

        const installUploadUI = () => {
            const chat = deepQuery('ha-assist-chat');
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

        setInterval(() => {
            if (deepQuery('ha-assist-chat')?.shadowRoot) installUploadUI();
        }, 1500);
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
        settings.enable_context_status_bar = false;

        let phase = S_IDLE;
        let turnStart = null;
        let turnEnd = null;
        let tickTimer = null;
        let totalChars = 0;
        let windowStart = Date.now();
        let windowTimeLabel = '0s';
        let hasTurn = false;
        let statusLoop = null;

        const resetState = (removeBar) => {
            phase = S_IDLE;
            turnStart = null;
            turnEnd = null;
            totalChars = 0;
            windowStart = Date.now();
            windowTimeLabel = '0s';
            hasTurn = false;
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
                            if (delta.content) totalChars += delta.content.length;
                            if (delta.tool_calls) { phase = S_TOOL; totalChars += JSON.stringify(delta.tool_calls).length; }
                            if (delta.tool_result) { phase = S_TOOL; totalChars += JSON.stringify(delta.tool_result).length; }
                            if (delta.tool_call_id && !delta.tool_calls) phase = S_TOOL;
                            render();
                        } else if (t === 'run-start') {
                            phase = S_THINKING;
                            render();
                        } else if (t === 'intent-end' || t === 'run-end' || t === 'error') {
                            endTurn();
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
                #${BAR_ID} .sb-bar { letter-spacing:-0.5px; font-size:10px; }
                #${BAR_ID} .sb-pct { font-variant-numeric:tabular-nums; font-weight:600; font-size:10px; }
                #${BAR_ID} .sb-time { font-variant-numeric:tabular-nums; opacity:0.6; }
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
                const inp = sr.querySelector('.input')||sr.querySelector('.chatbox')||sr.querySelector('[class*="input"]');
                if (inp) inp.parentNode.insertBefore(bar, inp);
                else { const m=sr.querySelector('.messages'); if(m) m.parentNode.insertBefore(bar,m.nextSibling); else sr.appendChild(bar); }
            }

            if (hasTurn) totalChars = calcCurrentChars();
            const localTk = Math.round(totalChars/CPT) + (hasTurn ? BASE_PROMPT_TOKENS : 0);
            const tk = backendTokens > localTk ? backendTokens : localTk;
            const ctxW = backendCtx || CTX;
            const pct = Math.min(100, Math.round(tk/ctxW*100));
            const pc = pctColor(pct);
            const active = phase !== S_IDLE;
            let timer = '--';
            if (active && turnStart) timer = ftime(Math.round((Date.now()-turnStart)/1000));
            else if (turnStart && turnEnd) timer = ftime(Math.round((turnEnd-turnStart)/1000));
            const tkLabel = hasTurn ? fmt(tk) : '--';
            const pctLabel = hasTurn ? pct+'%' : '--%';
            const winLabel = hasTurn ? windowTimeLabel : '--';
            const barW=12, filled=Math.round(pct/100*barW);
            const barStr = hasTurn ? '█'.repeat(filled)+'░'.repeat(barW-filled) : '░'.repeat(barW);
            const barColor = hasTurn ? pc : 'var(--secondary-text-color)';
            bar.innerHTML =
                `<span class="sb-tok">${tkLabel} / ${fmtR(ctxW)}</span>` +
                '<span class="sb-sep">│</span>' +
                `<span class="sb-bar" style="color:${barColor}">${barStr}</span> <span class="sb-pct" style="color:${barColor}">${pctLabel}</span>` +
                '<span class="sb-sep">│</span>' +
                `<span class="sb-time">${winLabel}</span>` +
                '<span class="sb-sep">│</span>' +
                `⏲ <span class="sb-time">${timer}</span>`;
        };

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
