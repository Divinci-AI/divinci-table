// 📷 Send a photo of the table: shrink it on the device (max 1600 px, JPEG) and post it to /api/chat/photo.
//   window.sendTablePhoto(file, by, caption) → Promise<{ok, photo} | {error}>
(() => {
  if (window.sendTablePhoto) return;
  window.sendTablePhoto = async (file, by, caption = "") => {
    const img = await createImageBitmap(file).catch(() => null);
    let blob = file;
    if (img) {
      const k = Math.min(1, 1600 / Math.max(img.width, img.height));
      const c = document.createElement("canvas"); c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      blob = await new Promise(ok => c.toBlob(ok, "image/jpeg", 0.85));
    }
    const r = await fetch("/api/chat/photo", { method: "POST", body: blob,
      headers: { "Content-Type": "image/jpeg", "X-By": encodeURIComponent(by || ""), "X-Caption": encodeURIComponent(caption || "") } });
    return r.json().catch(() => ({ error: "upload failed" }));
  };
})();
