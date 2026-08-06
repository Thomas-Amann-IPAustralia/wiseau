/*
 * Oh hi Mark(down) — a minimal, dependency-free ZIP writer.
 *
 * Exists so "download every chapter" is one file instead of a dozen separate
 * saves that browsers block as a multi-download. The frontend has no build step
 * and pulls in no third-party script (CLAUDE.md), so rather than adding JSZip
 * this writes the ~80 bytes of header per file that the format actually needs.
 *
 * Scope: stored (uncompressed) entries only. The payload is Markdown a browser
 * has already downloaded once, so the bytes saved by deflating it are not worth
 * a compressor; every unzip tool reads stored entries.
 *
 * Deterministic on purpose (ADR-030): entries carry a fixed 1980-01-01
 * timestamp, so converting the same document twice produces byte-identical
 * archives — matching the rest of the default path, and dodging a timezone
 * making two identical exports look different.
 *
 * Reference: PKWARE APPNOTE.TXT §4.3 (local file header, central directory,
 * end-of-central-directory). Only the always-required fields are written; Zip64
 * is not, so an archive is bounded to 4 GB and 65 535 entries — far above the
 * backend's 25 MB upload ceiling.
 */

(function (global) {
  "use strict";

  const LOCAL_HEADER = 0x04034b50;
  const CENTRAL_HEADER = 0x02014b50;
  const END_OF_CENTRAL = 0x06054b50;
  // Bit 11: filenames (and comments) are UTF-8 rather than the legacy code page.
  const UTF8_FLAG = 0x0800;
  const VERSION = 20; // 2.0 — the version that defined "stored"
  // 1980-01-01 00:00 in DOS date/time: the earliest the format can express.
  const DOS_TIME = 0;
  const DOS_DATE = 0x0021;

  const CRC_TABLE = (function () {
    const table = new Uint32Array(256);
    for (let i = 0; i < 256; i += 1) {
      let value = i;
      for (let bit = 0; bit < 8; bit += 1) {
        value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
      }
      table[i] = value >>> 0;
    }
    return table;
  })();

  /** CRC-32 of a byte array, as the ZIP format defines it. */
  function crc32(bytes) {
    let crc = 0xffffffff;
    for (let i = 0; i < bytes.length; i += 1) {
      crc = CRC_TABLE[(crc ^ bytes[i]) & 0xff] ^ (crc >>> 8);
    }
    return (crc ^ 0xffffffff) >>> 0;
  }

  /** A little-endian writer over a fixed-size buffer. */
  function writer(size) {
    const bytes = new Uint8Array(size);
    const view = new DataView(bytes.buffer);
    let offset = 0;
    return {
      bytes,
      u16(value) {
        view.setUint16(offset, value, true);
        offset += 2;
      },
      u32(value) {
        view.setUint32(offset, value >>> 0, true);
        offset += 4;
      },
      raw(source) {
        bytes.set(source, offset);
        offset += source.length;
      },
    };
  }

  /**
   * Build a ZIP archive from in-memory text files.
   * @param {{name: string, content: string}[]} files
   * @returns {Blob} the archive, ready for a download link.
   */
  function build(files) {
    const encoder = new TextEncoder();
    const parts = [];
    const entries = [];
    let offset = 0;

    files.forEach((file) => {
      const name = encoder.encode(file.name);
      const data = encoder.encode(file.content);
      const crc = crc32(data);

      const header = writer(30 + name.length);
      header.u32(LOCAL_HEADER);
      header.u16(VERSION);
      header.u16(UTF8_FLAG);
      header.u16(0); // stored, no compression
      header.u16(DOS_TIME);
      header.u16(DOS_DATE);
      header.u32(crc);
      header.u32(data.length); // compressed size == uncompressed size
      header.u32(data.length);
      header.u16(name.length);
      header.u16(0); // no extra field
      header.raw(name);

      parts.push(header.bytes, data);
      entries.push({ name, data, crc, offset });
      offset += header.bytes.length + data.length;
    });

    const directory = [];
    let directorySize = 0;
    entries.forEach((entry) => {
      const record = writer(46 + entry.name.length);
      record.u32(CENTRAL_HEADER);
      record.u16(VERSION); // version made by
      record.u16(VERSION); // version needed to extract
      record.u16(UTF8_FLAG);
      record.u16(0); // stored
      record.u16(DOS_TIME);
      record.u16(DOS_DATE);
      record.u32(entry.crc);
      record.u32(entry.data.length);
      record.u32(entry.data.length);
      record.u16(entry.name.length);
      record.u16(0); // extra field
      record.u16(0); // comment
      record.u16(0); // disk number
      record.u16(0); // internal attributes
      record.u32(0); // external attributes
      record.u32(entry.offset);
      record.raw(entry.name);
      directory.push(record.bytes);
      directorySize += record.bytes.length;
    });

    const end = writer(22);
    end.u32(END_OF_CENTRAL);
    end.u16(0); // this disk
    end.u16(0); // disk with the central directory
    end.u16(entries.length);
    end.u16(entries.length);
    end.u32(directorySize);
    end.u32(offset); // where the central directory starts
    end.u16(0); // no archive comment

    return new Blob([...parts, ...directory, end.bytes], { type: "application/zip" });
  }

  global.wiseauZip = { build };
})(window);
