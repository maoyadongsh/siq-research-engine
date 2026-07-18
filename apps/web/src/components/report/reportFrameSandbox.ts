export const REPORT_SOURCE_LINK_MESSAGE_TYPE = 'siq:report-source-link'
export const REPORT_FRAME_HEIGHT_MESSAGE_TYPE = 'siq:report-frame-height'

export const REPORT_IFRAME_SANDBOX = 'allow-scripts allow-popups allow-downloads'

export type ReportSourceLinkMessage = {
  type: typeof REPORT_SOURCE_LINK_MESSAGE_TYPE
  href: string
}

export type ReportFrameHeightMessage = {
  type: typeof REPORT_FRAME_HEIGHT_MESSAGE_TYPE
  height: number
}

export function isReportSourceLinkMessage(value: unknown): value is ReportSourceLinkMessage {
  if (!value || typeof value !== 'object') return false
  const data = value as Record<string, unknown>
  return data.type === REPORT_SOURCE_LINK_MESSAGE_TYPE && typeof data.href === 'string' && data.href.length > 0
}

export function isReportFrameHeightMessage(value: unknown): value is ReportFrameHeightMessage {
  if (!value || typeof value !== 'object') return false
  const data = value as Record<string, unknown>
  return data.type === REPORT_FRAME_HEIGHT_MESSAGE_TYPE
    && typeof data.height === 'number'
    && Number.isFinite(data.height)
    && data.height > 0
}

export const REPORT_SOURCE_LINK_BRIDGE_SCRIPT = `<script>
(function () {
  var LINK_MESSAGE_TYPE = ${JSON.stringify(REPORT_SOURCE_LINK_MESSAGE_TYPE)};
  var HEIGHT_MESSAGE_TYPE = ${JSON.stringify(REPORT_FRAME_HEIGHT_MESSAGE_TYPE)};
  var SOURCE_LINK_RE = /\\/api\\/(?:(?:source|pdf_page)\\/|downloads\\/report-file|documents\\/(?:source|artifact|figures|download)\\/|pdf\\/(?:source|pdf_page|artifact|download|download_complete|download_corrected|financial|quality|result)\\/)/;
  var SOURCE_ACCESS_RE = /\\/api\\/source_access\\//;
  var lastHeight = 0;

  function shouldBridge(href) {
    if (!href || href.charAt(0) === '#' || href.indexOf('blob:') === 0 || href.indexOf('data:') === 0) return false;
    try {
      var url = new URL(href, document.baseURI);
      return SOURCE_LINK_RE.test(url.pathname) || SOURCE_ACCESS_RE.test(url.pathname);
    } catch (error) {
      return SOURCE_LINK_RE.test(href) || SOURCE_ACCESS_RE.test(href);
    }
  }

  function reportHeight() {
    var root = document.documentElement;
    var body = document.body;
    if (!root || !body) return;
    var height = Math.ceil(Math.max(root.scrollHeight, root.offsetHeight, body.scrollHeight, body.offsetHeight));
    if (!height || Math.abs(height - lastHeight) < 2) return;
    lastHeight = height;
    window.parent.postMessage({ type: HEIGHT_MESSAGE_TYPE, height: height }, '*');
  }

  function normalizeStatusMarks() {
    document.querySelectorAll('.verdict-badge.approve,.verdict-badge').forEach(function (node) {
      if ((node.textContent || '').trim().toUpperCase() === 'APPROVE') node.textContent = '通过';
    });
    document.querySelectorAll('.card-status.pass,.status.pass,.badge.pass,.tag.pass,.chip.pass,.pill.pass').forEach(function (node) {
      if ((node.textContent || '').trim().toUpperCase() === 'PASS') node.textContent = '✓ 通过';
    });
    document.querySelectorAll('.verified,.status.verified,.badge.verified,.tag.verified,[class*="verified"]').forEach(function (node) {
      if ((node.textContent || '').trim().toUpperCase() === 'VERIFIED') node.textContent = '✓ 已核验';
    });
  }

  function normalizeFactCheckOrder() {
    document.querySelectorAll('h1,h2,h3').forEach(function (heading) {
      if ((heading.textContent || '').trim() !== '核查明细') return;
      var boundary = heading.nextElementSibling;
      var cards = [];
      while (boundary && !/^H[1-3]$/.test(boundary.tagName)) {
        if (boundary.tagName === 'DIV') cards.push(boundary);
        boundary = boundary.nextElementSibling;
      }
      if (cards.length < 2) return;

      function severity(card) {
        var text = (card.textContent || '').toUpperCase();
        if (/CRITICAL|FAIL|FAILED|严重|失败/.test(text)) return 0;
        if (/WARNING|警告|风险/.test(text)) return 1;
        if (/SUGGESTION|建议/.test(text)) return 2;
        return 3;
      }

      var sorted = cards.map(function (card, index) {
        return { card: card, index: index, severity: severity(card) };
      }).sort(function (left, right) {
        return left.severity - right.severity || left.index - right.index;
      }).map(function (entry) { return entry.card; });

      if (sorted.every(function (card, index) { return card === cards[index]; })) return;
      sorted.forEach(function (card) { heading.parentElement.insertBefore(card, boundary); });
    });
  }

  function normalizeInlineMarkdown() {
    var walker = document.createTreeWalker(document.body, 4);
    var nodes = [];
    var current = walker.nextNode();
    while (current) {
      var parent = current.parentElement;
      if (parent && !parent.closest('script,style,pre,code,textarea,.siq-md-inline-heading')) nodes.push(current);
      current = walker.nextNode();
    }
    nodes.forEach(function (node) {
      var source = node.data || '';
      var heading = source.match(/^(\\s*(?:[^\\n#]{1,40}[：:]\\s*)?)#{1,6}\\s+(.+)$/);
      if (!heading) return;
      var fragment = document.createDocumentFragment();
      if (heading[1]) fragment.append(heading[1]);
      var strong = document.createElement('strong');
      strong.className = 'siq-md-inline-heading';
      strong.textContent = heading[2];
      fragment.append(strong);
      node.replaceWith(fragment);
    });
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
    try { href = new URL(href, document.baseURI).toString(); } catch (error) {}
    window.parent.postMessage({ type: LINK_MESSAGE_TYPE, href: href }, '*');
  }, true);

  function startHeightBridge() {
    normalizeInlineMarkdown();
    normalizeStatusMarks();
    normalizeFactCheckOrder();
    reportHeight();
    requestAnimationFrame(reportHeight);
    setTimeout(reportHeight, 120);
    setTimeout(reportHeight, 500);
    if (window.ResizeObserver) {
      var resizeObserver = new ResizeObserver(reportHeight);
      resizeObserver.observe(document.documentElement);
      resizeObserver.observe(document.body);
    }
    if (window.MutationObserver) {
      new MutationObserver(function () { normalizeInlineMarkdown(); normalizeStatusMarks(); normalizeFactCheckOrder(); requestAnimationFrame(reportHeight); })
        .observe(document.body, { childList: true, subtree: true, attributes: true });
    }
    window.addEventListener('resize', reportHeight);
    document.querySelectorAll('img').forEach(function (image) { image.addEventListener('load', reportHeight); });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', startHeightBridge, { once: true });
  else startHeightBridge();
  window.addEventListener('load', reportHeight);
})();
</script>`
