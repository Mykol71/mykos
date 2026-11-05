/* Trusted local bootstrap for a dashboard bound to exactly one peer. */
(() => {
    'use strict';
    const prefix = __MYKOS_PEER_PREFIX__;
    const compact = !['1','true','on','yes'].includes((new URL(location.href).searchParams.get('full') || '').toLowerCase());
    window.MYKOS_PEER_CONTEXT = {prefix,compact};
    function route(value) {
        const url = new URL(String(value), location.href);
        if (url.origin === location.origin && url.pathname.startsWith('/mykos/') && !url.pathname.startsWith(prefix + '/')) {
            url.pathname = prefix + url.pathname.slice(6);
        }
        return url.href;
    }
    window.mykosNodeURL = route;
    const originalFetch = window.fetch.bind(window);
    window.fetch = (input, options) => originalFetch(input instanceof Request ? new Request(route(input.url), input) : route(input), options);
    const OriginalSocket = window.WebSocket;
    window.WebSocket = class extends OriginalSocket {
        constructor(url, protocols) {
            const parsed = new URL(String(url), location.href);
            const sameHost = parsed.host === location.host;
            if (sameHost && parsed.pathname.startsWith('/mykos/') && !parsed.pathname.startsWith(prefix + '/'))
                parsed.pathname = prefix + parsed.pathname.slice(6);
            super(parsed.href, protocols);
        }
    };
    const originalOpen = window.open.bind(window);
    window.open = (url, ...args) => originalOpen(url && url !== 'about:blank' ? route(url) : url, ...args);
    // Links and popup locations generated after page load use the same node broker.
    document.addEventListener('click', event => {
        const link = event.target.closest?.('a[href]');
        if (!link || link.getAttribute('href').startsWith('#')) return;
        const rewritten = route(link.href);
        if (rewritten !== link.href) link.href = rewritten;
    }, true);
    window.addEventListener('DOMContentLoaded', () => {
        document.getElementById('logout')?.remove();
        if (window.parent !== window) window.parent.postMessage({type:'mykos-peer-ready',prefix},location.origin);
        const report = () => {
            const pane = document.getElementById('mykos-host-pane');
            const footer=document.getElementById('mykos-host-hardware');
            const bottom=Math.max(pane?.getBoundingClientRect().bottom || 0,footer?.getBoundingClientRect().bottom || 0);
            if (pane && window.parent !== window) window.parent.postMessage({type:'mykos-peer-height', prefix, height:Math.ceil(bottom + 10)}, location.origin);
        };
        if (typeof ResizeObserver === 'function') new ResizeObserver(report).observe(document.body);
        report();
    });
})();
