/*
 * Puts EXIF data and an ICC colour profile into a JPEG made by the browser
 * (browsers' JPEG encoders write neither).
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
      tiff.setInteger(tiff.find(ifd0, TAG_ORIENTATION), 1);

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

  /**
   * Insert metadata into a JPEG: SOI, JFIF (if present), Exif, ICC, then the rest.
   * Any Exif/ICC segments the browser wrote are replaced.
   */
  function addJpegMetadata(jpeg, metadata) {
    const bytes = jpeg instanceof Uint8Array ? jpeg : new Uint8Array(jpeg);
    if (bytes[0] !== 0xff || bytes[1] !== 0xd8) throw new Error("Not a JPEG");
    const head = [bytes.subarray(0, 2)];
    let pos = 2;
    let jfif = null;
    // Walk the header segments up to the image data.
    while (pos + 4 <= bytes.length && bytes[pos] === 0xff) {
      const marker = bytes[pos + 1];
      if (marker === 0xda || marker === 0xd9) break; // start of scan / end of image
      const length = (bytes[pos + 2] << 8) | bytes[pos + 3];
      const seg = bytes.subarray(pos, pos + 2 + length);
      const id = String.fromCharCode.apply(null, seg.subarray(4, 15));
      if (marker === 0xe0 && id.startsWith("JFIF") && !jfif) jfif = seg;
      else if (!(marker === 0xe1 && id.startsWith("Exif")) && !(marker === 0xe2 && id.startsWith("ICC_PROFILE"))) break;
      pos += 2 + length;
    }
    if (jfif) head.push(jfif);
    if (metadata.exif) {
      const app1 = exifSegment(metadata.exif);
      if (app1) head.push(app1);
    }
    if (metadata.icc) head.push.apply(head, iccSegments(metadata.icc));
    head.push(bytes.subarray(pos));
    return concat(head);
  }

  const api = { prepareExif: prepareExif, addJpegMetadata: addJpegMetadata };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.JpegMeta = api;
})(typeof self !== "undefined" ? self : this);
