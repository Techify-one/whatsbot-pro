// <Slot name="..." ctx=${...}/> — renders every component a plugin registered at
// the named extension point, passing `ctx` as props (+ `pluginId`). Renders
// nothing when empty, so dropping a <Slot> into core UI has zero visual impact
// until a plugin fills it. Re-renders when the registry changes.

import { h } from 'preact';
import { useState, useEffect } from 'preact/hooks';
import { getSlot, subscribe } from './registry.js';

export function Slot({ name, ctx = {} }) {
  const [, force] = useState(0);
  useEffect(() => subscribe(() => force((n) => n + 1)), []);

  const entries = getSlot(name);
  if (!entries.length) return null;
  // Preact renders an array of vnodes fine — no Fragment needed.
  return entries.map(({ pluginId, component: C }, i) =>
    h(C, { key: `${pluginId}:${i}`, ...ctx, pluginId }));
}

