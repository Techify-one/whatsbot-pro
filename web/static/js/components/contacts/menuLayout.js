// Pure geometry helpers for the conversation context menu (ContextMenu.js) — kept
// dependency-free (no preact/htm) so they can be unit-tested with `node --test`.

// Vertical placement for a submenu flyout (Tags / Atribuir atendente).
//
// The flyout is `position: absolute` inside its `relative` trigger row, so the value
// returned here is the `top` offset (px) to apply RELATIVE TO THE ROW's top edge.
// Default behaviour: align the flyout's top with the row (offset 0). When the row is
// near the bottom of the screen and the flyout would overflow the bottom edge, shift
// it up (negative offset) so its bottom sits at the edge — this is the "open upward"
// the user sees near the bottom of the list. It never rises above the top margin.
//
// The flyout's own height is capped (max-h) below the viewport height, so once
// clamped it always fits whole; a long list just scrolls inside that fixed box.
//
//   rowTop         — the trigger row's top, in viewport coords (getBoundingClientRect)
//   flyoutHeight   — the flyout's rendered height (px)
//   viewportHeight — window.innerHeight
//   margin         — min gap kept from the top/bottom edges
export function clampFlyoutOffset(rowTop, flyoutHeight, viewportHeight, margin = 8) {
  let top = rowTop;
  if (top + flyoutHeight + margin > viewportHeight) {
    top = viewportHeight - flyoutHeight - margin;
  }
  if (top < margin) top = margin;
  return top - rowTop;
}
