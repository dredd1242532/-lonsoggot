export function attachAvatarFallback(image) {
  const hide = () => { image.hidden = true; };
  image.addEventListener('error', hide, { once: true });
  if (image.complete && image.naturalWidth === 0) hide();
}
