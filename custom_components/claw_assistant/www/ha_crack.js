(function() {
    'use strict';
    
    const HACRACK_VERSION = '20260501-toggle-v18';
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
        setupContinuousConversation(hass);
        setTimeout(() => {
            if (window.HACrack?.preventAssistDialogClose) {
                window.HACrack.preventAssistDialogClose();
            }
        }, 100);
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
