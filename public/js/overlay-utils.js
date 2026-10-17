/** Bild laden über onload – img.decode() kann in Hintergrund-Tabs bzw. verdeckten Browserquellen hängen. */
export function loadImage(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`Bild konnte nicht geladen werden: ${src.slice(0, 80)}`));
    img.src = src;
  });
}
