(function() {
    'use strict';
    
    let initialized = false;
    let pollInterval = null;
    let hassRef = null;
    let thoughtSubscribed = false;
    
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
        console.info(`%c</>HACrack%cv4.2`,"background:#000;color:#f33;padding:1px 4px;font:bold 9px monaco","background:#200;color:#fff;padding:1px 4px;font:9px monaco");
        console.info(`%c[API]%cwindow.HACrack`,"color:#666;font:9px monaco","color:#999;font:9px monaco");
        console.info(`%c[Bubble]%cThought listener`,"color:#666;font:9px monaco","color:#999;font:9px monaco");
        pollInterval = setInterval(() => pollPendingJS(hass), 300);
        pollPendingJS(hass);
        exposeGlobalAPI();
        setupThinkingContentDisplay(hass);
        setTimeout(() => {
            if (window.HACrack?.preventAssistDialogClose) {
                window.HACrack.preventAssistDialogClose();
            }
        }, 100);
    }
    
    function setupThinkingContentDisplay(hass) {
        if (thoughtSubscribed || !hass?.connection) return;
        thoughtSubscribed = true;
        hass.connection.subscribeEvents((event) => {
            const thought = event.data?.thought;
            if (thought) showThoughtBubble(thought);
        }, 'ha_crack_thought');
        
        const observer = new MutationObserver(() => {
            const dialog = deepQuery('ha-voice-command-dialog');
            if (dialog && !dialog._thinkingObserverAttached) {
                dialog._thinkingObserverAttached = true;
                const checkMessages = () => {
                    const assistChat = deepQuery('ha-assist-chat', dialog.shadowRoot || dialog);
                    if (assistChat) {
                        const messagesDiv = assistChat.shadowRoot?.querySelector('.messages');
                        if (messagesDiv) {
                            const messages = messagesDiv.querySelectorAll('.message.hass');
                            messages.forEach(msg => {
                                if (msg._thinkingProcessed) return;
                                msg._thinkingProcessed = true;
                                
                                const content = msg.textContent || '';
                                if (content.includes('💭')) {
                                    msg.style.backgroundColor = '#e3f2fd';
                                    msg.style.borderLeft = '3px solid #2196f3';
                                    msg.style.fontStyle = 'italic';
                                }
                            });
                        }
                    }
                };
                
                const interval = setInterval(checkMessages, 200);
                setTimeout(() => clearInterval(interval), 30000);
            }
        });
        
        observer.observe(document.body, { childList: true, subtree: true });
    }
    
    function showThoughtBubble(thought) {
        const dialog = deepQuery('ha-voice-command-dialog');
        if (!dialog) return;
        const assistChat = deepQuery('ha-assist-chat', dialog.shadowRoot || dialog);
        if (!assistChat) return;
        const messagesDiv = assistChat.shadowRoot?.querySelector('.messages');
        if (!messagesDiv) return;
        
        const lastHassMsg = messagesDiv.querySelector('.message.hass:last-of-type');
        const bubble = document.createElement('ha-markdown');
        bubble.className = 'message hass';
        bubble.setAttribute('breaks', '');
        bubble.setAttribute('cache', '');
        const thoughtText = `💭 ${thought}`;
        bubble.setAttribute('content', thoughtText);
        bubble.content = thoughtText;
        bubble.style.cssText = 'color: #757575; font-style: italic; opacity: 0.8;';
        
        if (lastHassMsg) {
            messagesDiv.insertBefore(bubble, lastHassMsg);
        } else {
            messagesDiv.appendChild(bubble);
        }
        messagesDiv.scrollTop = messagesDiv.scrollHeight;
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
            toast: showToast,
            dialog: showDialog,
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
            
            debug: () => ({
                hass: !!getHass(),
                connection: !!getHass()?.connection,
                states: Object.keys(getHass()?.states || {}).length,
                clickables: getAllClickables().length,
                inputs: getAllInputs().length
            }),
            
            showChoices: (title, choices, callback) => {
                
                const voiceDialog = deepQuery('ha-voice-command-dialog');
                const assistChat = voiceDialog?.shadowRoot?.querySelector('ha-assist-chat');
                const messagesArea = assistChat?.shadowRoot?.querySelector('.messages');
                
                if (!messagesArea) {
                    return null;
                }
                
                const container = document.createElement('div');
                container.id = 'ha-crack-choices';
                container.className = 'message hass';
                container.style.cssText = 'font-size:15px;clear:both;max-width:80%;overflow-wrap:break-word;margin:8px 24px 8px 0;padding:12px 16px;border-radius:20px;border-bottom-left-radius:4px;background-color:var(--secondary-background-color);color:var(--primary-text-color);align-self:flex-start;';
                
                let html = '';
                if (title) {
                    html += `<p style="margin:0 0 10px 0;color:var(--secondary-text-color);font-size:13px;">${title}</p>`;
                }
                html += '<div style="display:flex;flex-wrap:wrap;gap:8px;">';
                choices.forEach((choice, i) => {
                    html += `<button data-choice-id="${choice.id}" data-choice-label="${choice.label}" style="padding:10px 18px;border-radius:18px;border:1px solid var(--primary-color);background:transparent;color:var(--primary-color);cursor:pointer;font-size:14px;transition:all 0.15s;font-weight:500;">${choice.label}</button>`;
                });
                html += '</div>';
                container.innerHTML = html;
                
                inner.querySelectorAll('button').forEach(btn => {
                    btn.onmouseenter = () => { btn.style.background = 'var(--primary-color)'; btn.style.color = 'white'; };
                    btn.onmouseleave = () => { btn.style.background = 'transparent'; btn.style.color = 'var(--primary-color)'; };
                    btn.onclick = () => {
                        const id = btn.dataset.choiceId;
                        const label = btn.dataset.choiceLabel;
                        container.remove();
                        if (callback) callback(id, label);
                        getHass()?.connection?.sendMessage({
                            type: 'fire_event',
                            event_type: 'ha_crack_choice_selected',
                            event_data: { choice_id: id, choice_label: label }
                        });
                    };
                });
                
                messagesArea.querySelector('#ha-crack-choices')?.remove();
                
                messagesArea.appendChild(container);
                container.scrollIntoView({ behavior: 'smooth', block: 'end' });
                
                return container;
            },
            
            showQuickActions: (actions) => {
                const choices = actions.map((a, i) => ({ id: `action_${i}`, label: a }));
                return window.HACrack.showChoices('请选择操作', choices);
            },
            
            showConfirm: (message, onConfirm, onCancel) => {
                return window.HACrack.showChoices(message, [
                    { id: 'confirm', label: '确认' },
                    { id: 'cancel', label: '取消' }
                ], (id) => {
                    if (id === 'confirm' && onConfirm) onConfirm();
                    if (id === 'cancel' && onCancel) onCancel();
                });
            },
            
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
                            if (scrim) {
                                scrim.style.pointerEvents = 'none';
                            }
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
    
    function showToast(message, duration = 3000) {
        const hass = document.querySelector('home-assistant')?.hass;
        if (hass) {
            hass.callService('persistent_notification', 'create', {
                message: message,
                title: 'AI助手'
            });
        }
        
        const toast = document.createElement('div');
        toast.style.cssText = `
            position: fixed;
            bottom: 20px;
            left: 50%;
            transform: translateX(-50%);
            background: #323232;
            color: white;
            padding: 16px 24px;
            border-radius: 8px;
            z-index: 9999;
            font-size: 14px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
            animation: fadeIn 0.3s ease;
            pointer-events: none;
        `;
        toast.textContent = message;
        document.body.appendChild(toast);
        
        setTimeout(() => {
            toast.style.animation = 'fadeOut 0.3s ease';
            setTimeout(() => toast.remove(), 300);
        }, duration);
        
    }
    
    function showDialog(title, message) {
        const dialog = document.createElement('div');
        dialog.style.cssText = `
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0,0,0,0.5);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 9999;
        `;
        
        dialog.innerHTML = `
            <div style="background: white; padding: 24px; border-radius: 12px; max-width: 400px; box-shadow: 0 8px 32px rgba(0,0,0,0.3);">
                <h3 style="margin: 0 0 16px 0; color: #333;">${title}</h3>
                <p style="margin: 0 0 24px 0; color: #666;">${message}</p>
                <button style="background: #03a9f4; color: white; border: none; padding: 10px 24px; border-radius: 6px; cursor: pointer; font-size: 14px;">确定</button>
            </div>
        `;
        
        dialog.querySelector('button').onclick = () => dialog.remove();
        dialog.onclick = (e) => { if (e.target === dialog) dialog.remove(); };
        
        document.body.appendChild(dialog);
    }
    
    function executeJS(code) {
        let processedCode = code;
        
        try {
            processedCode = processedCode.replace(/^\uFEFF/, '').replace(/[\u200B-\u200D\uFEFF]/g, '');
            processedCode = processedCode.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
            processedCode = processedCode.replace(/[""]/g, '"').replace(/['']/g, "'");
            
            if ((processedCode.startsWith('"') && processedCode.endsWith('"')) ||
                (processedCode.startsWith("'") && processedCode.endsWith("'"))) {
                try { processedCode = JSON.parse(processedCode); } catch(e) {}
            }
            
        } catch(e) {
            console.error('[HACrack] Preprocess error:', e.message);
        }
        
        try {
            const script = document.createElement('script');
            script.textContent = `try { ${processedCode} } catch(e) { console.error('[HACrack] Exec error:', e.message); }`;
            document.head.appendChild(script);
            script.remove();
            return { success: true };
        } catch(e) {
            console.error('[HACrack] Exec failed:', e.message);
            return { success: false, error: e.message };
        }
    }
    
    const style = document.createElement('style');
    style.textContent = `
        @keyframes fadeIn {
            from { opacity: 0; transform: translateX(-50%) translateY(20px); }
            to { opacity: 1; transform: translateX(-50%) translateY(0); }
        }
        @keyframes fadeOut {
            from { opacity: 1; }
            to { opacity: 0; }
        }
    `;
    document.head.appendChild(style);
    
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
