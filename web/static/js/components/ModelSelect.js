import { h } from 'preact';
import { useState, useEffect, useCallback } from 'preact/hooks';
import htm from 'htm';
import { getModels } from '../services/api.js';
import { SearchableSelect } from './SearchableSelect.js';

const html = htm.bind(h);

// Shared cache across all ModelSelect instances
let _modelsCache = null;
let _modelsFetching = false;
let _modelsFetchCallbacks = [];

async function fetchModelsOnce() {
  if (_modelsCache) return _modelsCache;
  if (_modelsFetching) {
    return new Promise(resolve => _modelsFetchCallbacks.push(resolve));
  }
  _modelsFetching = true;
  try {
    const res = await getModels();
    if (res.ok) {
      _modelsCache = res.data;
    } else {
      _modelsCache = [];
    }
  } catch {
    _modelsCache = [];
  }
  _modelsFetching = false;
  _modelsFetchCallbacks.forEach(cb => cb(_modelsCache));
  _modelsFetchCallbacks = [];
  return _modelsCache;
}

/**
 * Searchable model selector dropdown. Thin wrapper over <SearchableSelect>:
 * it owns the model fetch (cached) + modality filter, materializes the options
 * and delegates rendering/keyboard/search to the generic component.
 *
 * @param {object} props
 * @param {string} props.value - Current model ID
 * @param {function} props.onChange - Called with new model ID ('' clears it)
 * @param {string} [props.filterModality] - Filter by input modality ("audio", "image", or null for all)
 * @param {string} [props.placeholder] - Placeholder text
 * @param {boolean} [props.allowEmpty] - Show a "clear" row that resets to the app default
 * @param {string} [props.emptyLabel] - Label for the clear row (e.g. "— padrão do app —")
 * @param {boolean} [props.disabled]
 */
export function ModelSelect({ value, onChange, filterModality, placeholder, allowEmpty, emptyLabel, disabled }) {
  const [models, setModels] = useState([]);

  const loadModels = useCallback(async () => {
    const data = await fetchModelsOnce();
    let filtered = data;
    if (filterModality) {
      filtered = data.filter(m =>
        m.input_modalities && m.input_modalities.includes(filterModality)
      );
    }
    setModels(filtered);
  }, [filterModality]);

  useEffect(() => { loadModels(); }, [loadModels]);

  const options = models.map(m => ({ value: m.id, label: m.name || m.id, sublabel: m.id }));

  return html`
    <${SearchableSelect}
      value=${value}
      onChange=${onChange}
      options=${options}
      placeholder=${placeholder || 'Selecione um modelo...'}
      searchPlaceholder="Buscar modelo..."
      allowEmpty=${allowEmpty}
      emptyLabel=${emptyLabel}
      disabled=${disabled}
      inputClass="bg-wa-panel text-wa-text w-full px-3 py-2 rounded-lg text-sm border border-wa-border focus:border-wa-teal focus:outline-none disabled:opacity-50"
    />
  `;
}

export default ModelSelect;
