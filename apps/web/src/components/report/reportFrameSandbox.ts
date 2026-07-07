export const REPORT_SOURCE_LINK_MESSAGE_TYPE = 'siq:report-source-link'

export const REPORT_IFRAME_SANDBOX = 'allow-scripts allow-popups allow-downloads'

export type ReportSourceLinkMessage = {
  type: typeof REPORT_SOURCE_LINK_MESSAGE_TYPE
  href: string
}

export function isReportSourceLinkMessage(value: unknown): value is ReportSourceLinkMessage {
  if (!value || typeof value !== 'object') return false
  const data = value as Record<string, unknown>
  return data.type === REPORT_SOURCE_LINK_MESSAGE_TYPE && typeof data.href === 'string' && data.href.length > 0
}

export const REPORT_SOURCE_LINK_BRIDGE_SCRIPT = `<script>
(function () {
  var MESSAGE_TYPE = ${JSON.stringify(REPORT_SOURCE_LINK_MESSAGE_TYPE)};
  var SOURCE_LINK_RE = /\\/api\\/(?:(?:source|pdf_page)\\/|downloads\\/report-file|documents\\/(?:source|artifact|figures|download)\\/|pdf\\/(?:source|pdf_page|artifact|download|download_complete|download_corrected|financial|quality|result)\\/)/;
  var SOURCE_ACCESS_RE = /\\/api\\/source_access\\//;

  function shouldBridge(href) {
    if (!href || href.charAt(0) === '#' || href.indexOf('blob:') === 0 || href.indexOf('data:') === 0) return false;
    try {
      var url = new URL(href, document.baseURI);
      return SOURCE_LINK_RE.test(url.pathname) || SOURCE_ACCESS_RE.test(url.pathname);
    } catch (error) {
      return SOURCE_LINK_RE.test(href) || SOURCE_ACCESS_RE.test(href);
    }
  }

  document.addEventListener('click', function (event) {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    var target = event.target;
    if (!target || !target.closest) return;
    var anchor = target.closest('a[href]');
    if (!anchor) return;
    var href = anchor.getAttribute('href') || '';
    if (!shouldBridge(href)) return;
    event.preventDefault();
    try {
      href = new URL(href, document.baseURI).toString();
    } catch (error) {}
    window.parent.postMessage({ type: MESSAGE_TYPE, href: href }, '*');
  }, true);
})();
</script>`
