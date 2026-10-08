// 📷 Send a photo of the table: post it to /api/chat/photo.
//   window.sendTablePhoto(file, by, caption) → Promise<{ok, photo} | {error}>
//
// A JPEG or PNG that fits the server's limit is sent exactly as the camera made it. Shrinking it first (draw to a canvas,
// read it back) turned every photo BLACK in Brave on an iPhone, whose fingerprint protection blanks canvas readback
// (found 2026-10-07 at the first in-person test), and it bought nothing for a phone photo of a few MB. A canvas is
// used only when a picture is too big to send, and its result is checked: a blank frame is never sent, the page says why.
(() => {
  if (window.sendTablePhoto) return;
  const LIMIT = 7_500_000;                                  // the server takes up to 8 MB; keep a margin for the headers
  async function looksBlank(blob) {                         // true when the picture is (nearly) all black
    try {
      const b = await createImageBitmap(blob);
      const c = document.createElement("canvas"); c.width = 16; c.height = 16;
      const x = c.getContext("2d"); x.drawImage(b, 0, 0, 16, 16);
      const d = x.getImageData(0, 0, 16, 16).data;
      let sum = 0; for (let i = 0; i < d.length; i += 4) sum += d[i] + d[i + 1] + d[i + 2];
      return sum / (d.length / 4 * 3) < 3;
    } catch { return false; }                               // cannot tell: do not block
  }
  async function shrink(file) {
    const img = await createImageBitmap(file).catch(() => null);
    if (!img) return null;
    for (const [max, q] of [[1600, 0.85], [1200, 0.7], [800, 0.6]]) {
      const k = Math.min(1, max / Math.max(img.width, img.height));
      const c = document.createElement("canvas"); c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      const blob = await new Promise(ok => c.toBlob(ok, "image/jpeg", q));
      if (blob && blob.size <= LIMIT) return blob;
    }
    return null;
  }
  window.sendTablePhoto = async (file, by, caption = "") => {
    let blob = file;
    const sendable = (file.type === "image/jpeg" || file.type === "image/png") && file.size <= LIMIT;
    if (!sendable) {
      blob = await shrink(file);
      if (!blob) return { error: "couldn't prepare that photo: it's too big to send as it is, and this browser wouldn't resize it. Try Safari, or turn off Shields for this site" };
      if (await looksBlank(blob)) return { error: "this browser blanked the photo while shrinking it (Brave's fingerprint protection does that). Turn Shields off for this site, or use a smaller photo" };
    }
    const r = await fetch("/api/chat/photo", { method: "POST", body: blob,
      headers: { "Content-Type": blob.type || "image/jpeg", "X-By": encodeURIComponent(by || ""), "X-Caption": encodeURIComponent(caption || ""),
               "X-Seat-Key": encodeURIComponent((window.TableSeat && TableSeat.name() === by && TableSeat.body().key) || "") } });
    return r.json().catch(() => ({ error: "upload failed" }));
  };
})();
