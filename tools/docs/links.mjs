// Keep external source documents intact; only local viewer pages change suffix.
export function documentationHref(href) {
 if (/^https?:\/\/(?:mykol\.com|myk\.sh)\/mykos\/api\/v2\/?$/i.test(href)) return '/mykos/api/v2/docs';
 return /^(?:[a-z][a-z0-9+.-]*:|\/\/)/i.test(href) ? href : href.replace(/\.md(?=$|[?#])/i,'.html');
}
