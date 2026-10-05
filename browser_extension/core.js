/* Shared by the popup and its Node tests. */
(function (root) {
  function serverAddress(value) {
    let input = value.trim();
    if (!input) throw new Error('Enter your Pantry Keeper server address.');
    if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(input)) input = `http://${input}`;
    const url = new URL(input);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password ||
        url.search || url.hash || (url.pathname !== '/' && url.pathname !== '')) {
      throw new Error('Enter a server origin, such as http://192.168.1.20:8000.');
    }
    if (url.hostname === '0.0.0.0') throw new Error('Use your server’s IP address or hostname, not 0.0.0.0.');
    return { origin: url.origin, permission: `${url.protocol}//${url.hostname}/*` };
  }
  function isMeijerPage(value) {
    try {
      const url = new URL(value);
      return url.protocol === 'https:' && (url.hostname === 'meijer.com' || url.hostname.endsWith('.meijer.com'));
    } catch { return false; }
  }
  function isReceiptPDF(value) {
    try { return isMeijerPage(value) && /\.pdf$/i.test(new URL(value).pathname); }
    catch { return false; }
  }
  root.PantryExtension = { serverAddress, isMeijerPage, isReceiptPDF };
})(globalThis);
