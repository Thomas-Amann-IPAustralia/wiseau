/*
 * Oh hi Mark(down) — a small, dependency-free Markdown renderer.
 *
 * Exists so the output panel can show converted documents *rendered* as well as
 * raw. The frontend has no build step and pulls in no third-party script (see
 * CLAUDE.md), so this covers the subset of Markdown the engine actually emits:
 * ATX headings, paragraphs, fenced code, lists, blockquotes, pipe tables, rules,
 * and the usual inline marks.
 *
 * Security note: the input is text extracted from an arbitrary web page or
 * document, so it is untrusted. Every fragment of it is HTML-escaped *before*
 * any markup is added, meaning raw HTML in the Markdown is displayed as text
 * rather than executed, and link/image targets are restricted to safe schemes.
 * `data:` images (a stripped payload from the backend, or anything else) render
 * as a placeholder chip rather than being loaded.
 */

(function (global) {
  "use strict";

  const SAFE_SCHEME = /^(https?:|mailto:|#|\/|\.{0,2}\/)/i;
  // A control character that cannot appear in extracted Markdown, so parked
  // code spans can never collide with the document's own text.
  const CODE_TOKEN = "\u0000";

  function escapeHtml(text) {
    return text
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /** True for a link target we are willing to put in an href. */
  function isSafeUrl(url) {
    const trimmed = url.trim();
    if (!trimmed) return false;
    if (/^[a-z][a-z0-9+.-]*:/i.test(trimmed)) return SAFE_SCHEME.test(trimmed);
    return true; // relative path or fragment
  }

  /** Media type of a data URI, for the image placeholder chip. */
  function dataUriKind(url) {
    const match = /^data:([^;,]*)/i.exec(url.trim());
    return match && match[1] ? match[1] : "image";
  }

  function imageHtml(alt, url) {
    const label = alt.trim();
    if (!isSafeUrl(url)) {
      // An inlined image whose payload the backend elided, or an unsafe target:
      // show that something was here instead of a broken-image icon.
      const kind = url.trim().toLowerCase().startsWith("data:") ? dataUriKind(url) : "image";
      const caption = label ? `${escapeHtml(kind)}: ${label}` : escapeHtml(kind);
      return `<span class="md-image" title="${escapeHtml(url.trim().slice(0, 60))}">${caption}</span>`;
    }
    return `<img src="${url.trim()}" alt="${label}" loading="lazy" />`;
  }

  function linkHtml(label, url) {
    if (!isSafeUrl(url)) return label;
    return `<a href="${url.trim()}" target="_blank" rel="noopener noreferrer">${label}</a>`;
  }

  /**
   * Inline marks for one already-block-classified line.
   * `text` is raw Markdown; it is escaped here, before any tag is introduced.
   */
  function inline(text) {
    const codes = [];
    let s = escapeHtml(text);

    // Code spans first, parked as tokens so no other rule rewrites their body.
    s = s.replace(/`([^`]+)`/g, (_, code) => {
      codes.push(code);
      return CODE_TOKEN + (codes.length - 1) + CODE_TOKEN;
    });

    s = s.replace(/!\[([^\]]*)\]\(\s*([^)\s]*)(?:\s+"[^"]*")?\s*\)/g, (_, alt, url) => imageHtml(alt, url));
    s = s.replace(/\[([^\]]+)\]\(\s*([^)\s]*)(?:\s+"[^"]*")?\s*\)/g, (_, label, url) => linkHtml(label, url));
    s = s.replace(/&lt;(https?:\/\/[^\s&]+)&gt;/g, (_, url) => linkHtml(url, url));

    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/__([^_]+)__/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^*\w])\*([^*\n]+)\*/g, "$1<em>$2</em>");
    s = s.replace(/(^|[^_\w])_([^_\n]+)_(?![\w_])/g, "$1<em>$2</em>");
    s = s.replace(/~~([^~]+)~~/g, "<del>$1</del>");

    return s.replace(new RegExp(CODE_TOKEN + "(\\d+)" + CODE_TOKEN, "g"), (_, i) => `<code>${codes[i]}</code>`);
  }

  const HEADING = /^(#{1,6})\s+(.*)$/;
  const FENCE = /^\s*(```|~~~)(.*)$/;
  const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;
  const QUOTE = /^\s*>\s?(.*)$/;
  const BULLET = /^(\s*)[-*+]\s+(.*)$/;
  const ORDERED = /^(\s*)(\d+)[.)]\s+(.*)$/;
  const TABLE_ROW = /^\s*\|(.*)\|\s*$/;
  const TABLE_DIVIDER = /^\s*\|?[\s:|-]+\|[\s:|-]*$/;

  function splitRow(line) {
    return line
      .replace(/^\s*\|/, "")
      .replace(/\|\s*$/, "")
      .split("|")
      .map((cell) => cell.trim());
  }

  /** Render a run of list items, recursing on deeper indentation. */
  function renderList(lines, start, ordered, indent, out) {
    const tag = ordered ? "ol" : "ul";
    out.push(`<${tag}>`);
    let i = start;
    while (i < lines.length) {
      const match = ordered ? ORDERED.exec(lines[i]) : BULLET.exec(lines[i]);
      const otherMatch = ordered ? BULLET.exec(lines[i]) : ORDERED.exec(lines[i]);
      const current = match || otherMatch;
      if (!current || current[1].length < indent) break;

      if (current[1].length > indent) {
        // A nested list belongs inside the item that opened it.
        const nested = [];
        i = renderList(lines, i, Boolean(ORDERED.exec(lines[i])), current[1].length, nested);
        out[out.length - 1] = out[out.length - 1].replace(/<\/li>$/, nested.join("") + "</li>");
        continue;
      }
      if (!match) break; // same depth, different list type — let the caller reopen

      out.push(`<li>${inline(ordered ? match[3] : match[2])}</li>`);
      i += 1;
    }
    out.push(`</${tag}>`);
    return i;
  }

  /**
   * Convert Markdown to an HTML string safe to assign to `innerHTML`.
   * @param {string} markdown
   * @returns {string}
   */
  function render(markdown) {
    const lines = String(markdown || "").replace(/\r\n?/g, "\n").split("\n");
    const out = [];
    let i = 0;

    while (i < lines.length) {
      const line = lines[i];

      if (!line.trim()) {
        i += 1;
        continue;
      }

      const fence = FENCE.exec(line);
      if (fence) {
        const body = [];
        i += 1;
        while (i < lines.length && !FENCE.test(lines[i])) body.push(lines[i++]);
        i += 1; // closing fence (or end of input)
        const language = fence[2].trim();
        out.push(
          `<pre class="md-code"${language ? ` data-lang="${escapeHtml(language)}"` : ""}><code>${escapeHtml(
            body.join("\n")
          )}</code></pre>`
        );
        continue;
      }

      if (RULE.test(line)) {
        out.push("<hr />");
        i += 1;
        continue;
      }

      const heading = HEADING.exec(line);
      if (heading) {
        const level = heading[1].length;
        out.push(`<h${level}>${inline(heading[2].trim())}</h${level}>`);
        i += 1;
        continue;
      }

      if (QUOTE.test(line)) {
        const body = [];
        while (i < lines.length && QUOTE.test(lines[i])) body.push(QUOTE.exec(lines[i++])[1]);
        out.push(`<blockquote>${render(body.join("\n"))}</blockquote>`);
        continue;
      }

      // A pipe table needs its delimiter row; without one it is just text.
      if (TABLE_ROW.test(line) && i + 1 < lines.length && TABLE_DIVIDER.test(lines[i + 1])) {
        const head = splitRow(line);
        i += 2;
        const rows = [];
        while (i < lines.length && TABLE_ROW.test(lines[i])) rows.push(splitRow(lines[i++]));
        out.push(
          '<div class="md-table-wrap"><table><thead><tr>' +
            head.map((cell) => `<th>${inline(cell)}</th>`).join("") +
            "</tr></thead><tbody>" +
            rows
              .map((row) => `<tr>${row.map((cell) => `<td>${inline(cell)}</td>`).join("")}</tr>`)
              .join("") +
            "</tbody></table></div>"
        );
        continue;
      }

      const bullet = BULLET.exec(line);
      const ordered = ORDERED.exec(line);
      if (bullet || ordered) {
        i = renderList(lines, i, Boolean(ordered), (ordered || bullet)[1].length, out);
        continue;
      }

      // Paragraph: consecutive non-blank lines that start no other block.
      const paragraph = [];
      while (
        i < lines.length &&
        lines[i].trim() &&
        !FENCE.test(lines[i]) &&
        !HEADING.test(lines[i]) &&
        !RULE.test(lines[i]) &&
        !QUOTE.test(lines[i]) &&
        !BULLET.test(lines[i]) &&
        !ORDERED.test(lines[i]) &&
        !TABLE_ROW.test(lines[i])
      ) {
        paragraph.push(lines[i++]);
      }
      if (paragraph.length) {
        out.push(`<p>${paragraph.map(inline).join("<br />")}</p>`);
      } else {
        // A table-looking line with no delimiter row: keep it as plain text.
        out.push(`<p>${inline(lines[i++])}</p>`);
      }
    }

    return out.join("\n");
  }

  /**
   * The document's title: the first H1, else the first H2, else "".
   * Headings inside fenced code blocks are not titles.
   * @param {string} markdown
   * @returns {string}
   */
  function firstHeading(markdown) {
    const lines = String(markdown || "").split("\n");
    let inFence = false;
    let h2 = "";
    for (const line of lines) {
      if (FENCE.test(line)) {
        inFence = !inFence;
        continue;
      }
      if (inFence) continue;
      const heading = HEADING.exec(line);
      if (!heading) continue;
      const text = heading[2].replace(/#+\s*$/, "").trim();
      if (!text) continue;
      if (heading[1].length === 1) return text;
      if (heading[1].length === 2 && !h2) h2 = text;
    }
    return h2;
  }

  global.wiseauMarkdown = { render, firstHeading };
})(window);
