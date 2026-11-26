// A small Markdown renderer for the bot's own reports (headings, tables, lists, quotes,
// bold/italic, code and links). It escapes all HTML first, so nothing in a report can
// inject markup or scripts; links are only allowed for http(s).
(function () {
  function esc(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function inline(s) {
    s = esc(s);
    s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>");
    s = s.replace(/(^|\s)_([^_]+)_(?=\s|$|[.,;:!?])/g, "$1<em>$2</em>");
    return s;
  }

  function cells(line) {
    return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
  }

  function render(md) {
    const lines = String(md || "").replace(/\r/g, "").split("\n");
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) { i++; continue; }
      let m;
      if ((m = /^(#{1,6})\s+(.*)$/.exec(line))) {
        const n = Math.min(m[1].length + 1, 6);
        out.push(`<h${n}>${inline(m[2])}</h${n}>`);
        i++;
      } else if (/^```/.test(line)) {
        const buf = [];
        i++;
        while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]);
        i++;
        out.push(`<pre><code>${esc(buf.join("\n"))}</code></pre>`);
      } else if (/^\s*\|/.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
        const head = cells(line);
        i += 2;
        const rows = [];
        while (i < lines.length && /^\s*\|/.test(lines[i])) rows.push(cells(lines[i++]));
        out.push('<div class="table-wrap"><table><thead><tr>' + head.map((h) => `<th>${inline(h)}</th>`).join("") +
          "</tr></thead><tbody>" + rows.map((r) => "<tr>" + r.map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>").join("") +
          "</tbody></table></div>");
      } else if (/^>\s?/.test(line)) {
        const buf = [];
        while (i < lines.length && /^>\s?/.test(lines[i])) buf.push(lines[i++].replace(/^>\s?/, ""));
        out.push(`<blockquote>${render(buf.join("\n"))}</blockquote>`);
      } else if (/^\s*([-*]|\d+\.)\s+/.test(line)) {
        const ordered = /^\s*\d+\./.test(line);
        const items = [];
        while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) {
          items.push(lines[i].replace(/^\s*([-*]|\d+\.)\s+/, ""));
          i++;
          while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*([-*]|\d+\.)\s+/.test(lines[i])) items[items.length - 1] += " " + lines[i++].trim();
        }
        const tag = ordered ? "ol" : "ul";
        out.push(`<${tag}>` + items.map((t) => `<li>${inline(t)}</li>`).join("") + `</${tag}>`);
      } else if (/^(-{3,}|\*{3,})$/.test(line.trim())) {
        out.push("<hr>");
        i++;
      } else {
        const buf = [];
        while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|```|>|\s*\||\s*([-*]|\d+\.)\s+)/.test(lines[i])) buf.push(lines[i++]);
        if (!buf.length) buf.push(lines[i++]);
        out.push(`<p>${inline(buf.join(" "))}</p>`);
      }
    }
    return out.join("\n");
  }

  window.renderMarkdown = render;
})();
