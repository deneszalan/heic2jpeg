/*
 * Puts EXIF data and an ICC colour profile into a JPEG made by the browser
 * (browsers' JPEG encoders write neither), and reads/edits the metadata of
 * existing JPEGs without re-encoding them.
 *
 * The EXIF block is edited in place:
 *   - Orientation is set to 1 (libheif already rotated the pixels),
 *   - the stored pixel dimensions are updated after resizing,
 *   - the GPS block can be wiped out completely for privacy.
 */
(function (root) {
  "use strict";

  const TYPE_SIZES = { 1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4 };
  const TAG_ORIENTATION = 0x0112;
  const TAG_EXIF_IFD = 0x8769;
  const TAG_GPS_IFD = 0x8825;
  const TAG_PIXEL_X = 0xa002;
  const TAG_PIXEL_Y = 0xa003;
  const MAX_SEGMENT = 65533; // largest payload of one JPEG marker segment

  class Tiff {
    constructor(bytes) {
      this.bytes = bytes;
      this.view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
      const order = this.view.getUint16(0);
      if (order === 0x4949) this.little = true;
      else if (order === 0x4d4d) this.little = false;
      else throw new Error("Not TIFF data");
      if (this.u16(2) !== 42) throw new Error("Not TIFF data");
    }

    u16(pos) { return this.view.getUint16(pos, this.little); }
    u32(pos) { return this.view.getUint32(pos, this.little); }
    setU16(pos, value) { this.view.setUint16(pos, value, this.little); }
    setU32(pos, value) { this.view.setUint32(pos, value, this.little); }

    check(pos, length) {
      if (pos < 8 || pos + length > this.bytes.length) throw new Error("EXIF pointer out of range");
    }

    /** Entries of the IFD at `pos`: [{tag, type, count, at}] where `at` is the entry position. */
    entries(pos) {
      this.check(pos, 2);
      const count = this.u16(pos);
      this.check(pos, 2 + count * 12 + 4);
      const list = [];
      for (let i = 0; i < count; i++) {
        const at = pos + 2 + i * 12;
        list.push({ tag: this.u16(at), type: this.u16(at + 2), count: this.u32(at + 4), at: at });
      }
      return list;
    }

    find(ifd, tag) {
      return this.entries(ifd).find(function (e) { return e.tag === tag; }) || null;
    }

    setInteger(entry, value) {
      if (!entry || entry.count !== 1) return;
      if (entry.type === 3) this.setU16(entry.at + 8, value);
      else if (entry.type === 4) this.setU32(entry.at + 8, value);
    }

    /** Zero an IFD and every value it points to, so no trace of it is left in the file. */
    wipeIfd(pos) {
      const entries = this.entries(pos);
      for (const e of entries) {
        const size = (TYPE_SIZES[e.type] || 1) * e.count;
        if (size > 4) {
          const valuePos = this.u32(e.at + 8);
          if (valuePos >= 8 && valuePos + size <= this.bytes.length) this.bytes.fill(0, valuePos, valuePos + size);
        }
      }
      this.bytes.fill(0, pos, pos + 2 + entries.length * 12 + 4);
    }

    /** Drop one entry from an IFD, shifting the later entries and the next-IFD pointer up. */
    removeEntry(ifd, tag) {
      const entries = this.entries(ifd);
      const index = entries.findIndex(function (e) { return e.tag === tag; });
      if (index < 0) return;
      const end = ifd + 2 + entries.length * 12 + 4;
      const from = entries[index].at;
      this.bytes.copyWithin(from, from + 12, end);
      this.bytes.fill(0, end - 12, end);
      this.setU16(ifd, entries.length - 1);
    }
  }

  /**
   * Returns edited TIFF bytes (a copy), or null if the data can't be handled safely.
   * When removing the location fails we return null, so no GPS data can slip through.
   */
  function prepareExif(tiffBytes, options) {
    try {
      const tiff = new Tiff(tiffBytes.slice());
      const ifd0 = tiff.u32(4);
      if (options.resetOrientation !== false) tiff.setInteger(tiff.find(ifd0, TAG_ORIENTATION), 1);

      const exifPointer = tiff.find(ifd0, TAG_EXIF_IFD);
      if (exifPointer && options.width && options.height) {
        const exifIfd = tiff.u32(exifPointer.at + 8);
        tiff.setInteger(tiff.find(exifIfd, TAG_PIXEL_X), options.width);
        tiff.setInteger(tiff.find(exifIfd, TAG_PIXEL_Y), options.height);
      }

      if (options.removeLocation) {
        const gpsPointer = tiff.find(ifd0, TAG_GPS_IFD);
        if (gpsPointer) {
          tiff.wipeIfd(tiff.u32(gpsPointer.at + 8));
          tiff.removeEntry(ifd0, TAG_GPS_IFD);
        }
      }
      return tiff.bytes;
    } catch (error) {
      return null;
    }
  }

  function segment(marker, payload) {
    const out = new Uint8Array(4 + payload.length);
    out[0] = 0xff;
    out[1] = marker;
    out[2] = (payload.length + 2) >> 8;
    out[3] = (payload.length + 2) & 0xff;
    out.set(payload, 4);
    return out;
  }

  function ascii(text) {
    return Uint8Array.from(text, function (c) { return c.charCodeAt(0); });
  }

  function concat(parts) {
    const total = parts.reduce(function (sum, p) { return sum + p.length; }, 0);
    const out = new Uint8Array(total);
    let at = 0;
    for (const part of parts) { out.set(part, at); at += part.length; }
    return out;
  }

  function exifSegment(tiff) {
    const payload = concat([ascii("Exif\0\0"), tiff]);
    return payload.length <= MAX_SEGMENT ? segment(0xe1, payload) : null; // too big for JPEG: leave it out
  }

  function iccSegments(icc) {
    const header = ascii("ICC_PROFILE\0");
    const chunkSize = MAX_SEGMENT - header.length - 2;
    const count = Math.ceil(icc.length / chunkSize);
    if (count > 255) return [];
    const list = [];
    for (let i = 0; i < count; i++) {
      const chunk = icc.subarray(i * chunkSize, (i + 1) * chunkSize);
      list.push(segment(0xe2, concat([header, Uint8Array.of(i + 1, count), chunk])));
    }
    return list;
  }

  function isJfif(seg) { return seg.marker === 0xe0 && seg.id.startsWith("JFIF"); }
  function isExif(seg) { return seg.marker === 0xe1 && seg.id.startsWith("Exif\0"); }
  function isXmp(seg) { return seg.marker === 0xe1 && seg.id.startsWith("http://ns.adobe.com/xap/1.0/"); }
  function isIcc(seg) { return seg.marker === 0xe2 && seg.id.startsWith("ICC_PROFILE\0"); }

  /** Header segments of a JPEG up to the image data: {segments: [{marker, id, bytes}], dataStart}. */
  function readSegments(bytes) {
    if (bytes[0] !== 0xff || bytes[1] !== 0xd8) throw new Error("Not a JPEG");
    const segments = [];
    let pos = 2;
    while (pos + 4 <= bytes.length && bytes[pos] === 0xff) {
      const marker = bytes[pos + 1];
      if (marker === 0xff) { pos++; continue; } // fill byte
      if (marker === 0xda || marker === 0xd9) break; // start of scan / end of image
      const length = (bytes[pos + 2] << 8) | bytes[pos + 3];
      if (length < 2 || pos + 2 + length > bytes.length) throw new Error("Damaged JPEG");
      const seg = bytes.subarray(pos, pos + 2 + length);
      segments.push({ marker: marker, id: String.fromCharCode.apply(null, seg.subarray(4, 4 + 29)), bytes: seg });
      pos += 2 + length;
    }
    return { segments: segments, dataStart: pos };
  }

  /**
   * Metadata of an existing JPEG: {exif: TIFF bytes|null, icc: Uint8Array|null, width, height}.
   * Used when a phone has already turned the photo into a JPEG.
   */
  function readJpegMetadata(input) {
    const bytes = input instanceof Uint8Array ? input : new Uint8Array(input);
    const result = { exif: null, icc: null, width: 0, height: 0 };
    const chunks = [];
    for (const seg of readSegments(bytes).segments) {
      if (isExif(seg) && !result.exif) result.exif = seg.bytes.slice(10);
      else if (isIcc(seg)) chunks.push({ order: seg.bytes[16], data: seg.bytes.subarray(18) });
      else if (seg.marker >= 0xc0 && seg.marker <= 0xcf && seg.marker !== 0xc4 && seg.marker !== 0xc8 && seg.marker !== 0xcc) {
        result.height = (seg.bytes[5] << 8) | seg.bytes[6];
        result.width = (seg.bytes[7] << 8) | seg.bytes[8];
      }
    }
    if (chunks.length) {
      chunks.sort(function (a, b) { return a.order - b.order; });
      result.icc = concat(chunks.map(function (c) { return c.data; }));
    }
    return result;
  }

  /**
   * Rebuild a JPEG's metadata without touching the image data:
   * SOI, JFIF (if present), Exif, ICC, then every other segment in its original order.
   * Existing Exif/ICC segments are replaced; XMP is kept only if `keepXmp` (it can hold a location too).
   */
  function rewriteJpegMetadata(jpeg, metadata) {
    const bytes = jpeg instanceof Uint8Array ? jpeg : new Uint8Array(jpeg);
    const parsed = readSegments(bytes);
    const jfif = parsed.segments.find(isJfif);
    const out = [bytes.subarray(0, 2)];
    if (jfif) out.push(jfif.bytes);
    if (metadata.exif) {
      const app1 = exifSegment(metadata.exif);
      if (app1) out.push(app1);
    }
    if (metadata.icc) out.push.apply(out, iccSegments(metadata.icc));
    for (const seg of parsed.segments) {
      if (seg === jfif || isExif(seg) || isIcc(seg)) continue;
      if (isXmp(seg) && !metadata.keepXmp) continue;
      out.push(seg.bytes);
    }
    out.push(bytes.subarray(parsed.dataStart));
    return concat(out);
  }

  const api = { prepareExif: prepareExif, readJpegMetadata: readJpegMetadata, rewriteJpegMetadata: rewriteJpegMetadata };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.JpegMeta = api;
})(typeof self !== "undefined" ? self : this);
