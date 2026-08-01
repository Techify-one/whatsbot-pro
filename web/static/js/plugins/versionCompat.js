// Pure, browser/Node-compatible version-range helpers for plugin frontend gates.

function parseVersion(value) {
  const match = String(value || '').trim().match(/^(\d+)(?:\.(\d+))?(?:\.(\d+))?/);
  if (!match) return null;
  return [Number(match[1]), Number(match[2] || 0), Number(match[3] || 0)];
}

function compare(left, right) {
  for (let i = 0; i < 3; i += 1) {
    if (left[i] !== right[i]) return left[i] < right[i] ? -1 : 1;
  }
  return 0;
}

/**
 * Small semver-range subset used by plugin manifests.
 * Supports `*`, exact-major declarations (`1`, `1.0`), comma/space-separated
 * comparators (`>=1.0,<3.0`), caret and tilde ranges.
 */
export function satisfiesVersionRange(version, range) {
  const current = parseVersion(version);
  const text = String(range == null ? '' : range).trim();
  if (!current) return false;
  if (!text || text === '*') return true;

  if (text.startsWith('^') || text.startsWith('~')) {
    if (!/^[\^~]\d+(?:\.\d+){0,2}$/.test(text)) return false;
    const base = parseVersion(text.slice(1));
    if (!base || compare(current, base) < 0) return false;
    return text.startsWith('^')
      ? current[0] === base[0]
      : current[0] === base[0] && current[1] === base[1];
  }

  const comparatorRange = /^(?:>=|<=|>|<|==|=)\s*\d+(?:\.\d+){0,2}(?:\s*(?:,\s*|\s+)(?:>=|<=|>|<|==|=)\s*\d+(?:\.\d+){0,2})*$/;
  if (comparatorRange.test(text)) {
    const comparators = [...text.matchAll(/(>=|<=|>|<|==|=)\s*(\d+(?:\.\d+){0,2})/g)];
    return comparators.every((match) => {
      const target = parseVersion(match[2]);
      if (!target) return false;
      const ordering = compare(current, target);
      return ({
        '>': ordering > 0,
        '>=': ordering >= 0,
        '<': ordering < 0,
        '<=': ordering <= 0,
        '=': ordering === 0,
        '==': ordering === 0,
      })[match[1]];
    });
  }

  if (!/^\d+(?:\.\d+){0,2}$/.test(text)) return false;
  const declared = parseVersion(text);
  return declared ? current[0] === declared[0] : false;
}

/** Select the newest host version accepted by a manifest range. */
export function selectSupportedVersion(range, supportedVersions, { defaultRange = '*' } = {}) {
  const effectiveRange = (range == null || String(range).trim() === '')
    ? defaultRange
    : range;
  return [...(supportedVersions || [])]
    .sort((a, b) => compare(parseVersion(b) || [0, 0, 0], parseVersion(a) || [0, 0, 0]))
    .find((version) => satisfiesVersionRange(version, effectiveRange)) || null;
}
