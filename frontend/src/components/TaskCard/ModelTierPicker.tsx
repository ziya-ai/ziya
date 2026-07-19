/**
 * ModelTierPicker — the model-selection control for a Task Card block
 * (and, later, delegates).  Reads/writes the four model fields on a
 * TaskScope: model_tier (the recommended, portable choice) plus the
 * model_name / model_id_override / model_endpoint escape hatches.
 *
 * Two modes, toggled by a link:
 *   - Tier mode (default, recommended): a segmented set of the five
 *     portable rungs.  Each rung shows what it currently resolves to on
 *     the active endpoint (fetched from /api/model-tiers), so the user
 *     sees "small → nova-lite" without leaving the editor.  Portable:
 *     the tier follows the model as models are added/retired.
 *   - Specific mode (advanced): a dropdown of concrete models for the
 *     active endpoint (from /api/available-models).  Explicitly labelled
 *     as non-portable, since a literal name breaks when the model is
 *     retired or the endpoint changes.
 *
 * "Inherit" (all fields null) is always an option and the default — the
 * block then runs on whatever the deck/card/ancestor scope or the
 * top-level conversation selected (see merge_scopes precedence).
 */

import React, { useEffect, useState, useCallback } from 'react';
import type { TaskScope, ModelTier } from '../../types/task_card';
import './task-card-editor.css';

interface TierInfo {
  tier: ModelTier;
  resolved_model: string;
  exact: boolean;
}

interface Props {
  scope: TaskScope;
  onChange: (next: TaskScope) => void;
}

const TIER_LABELS: Record<ModelTier, string> = {
  xsmall: 'XS',
  small: 'S',
  medium: 'M',
  large: 'L',
  frontier: 'Frontier',
};

// Cheapest → most capable. 'medium' is the center = default model.
// 'frontier' is the rarely-warranted top (~20x cost, throttled).
const TIER_ORDER: ModelTier[] = ['xsmall', 'small', 'medium', 'large', 'frontier'];

// Module-scoped caches so re-mounting a block editor (common in the
// drag/drop tree) doesn't refetch the same static-per-session data.
const _tierCache: Record<string, TierInfo[]> = {};
const _modelsCache: Record<string, string[]> = {};

export const ModelTierPicker: React.FC<Props> = ({ scope, onChange }) => {
  const [tiers, setTiers] = useState<TierInfo[]>([]);
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [endpoint, setEndpoint] = useState<string>('bedrock');
  // "specific" mode is implied when a concrete model/ARN is set.
  const hasSpecific = !!(scope.model_name || scope.model_id_override);
  const [specificMode, setSpecificMode] = useState<boolean>(hasSpecific);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // Resolve the active endpoint first (config is cached server-side).
        let ep = endpoint;
        const cfg = await fetch('/api/config').then(r => (r.ok ? r.json() : null)).catch(() => null);
        if (cfg?.endpoint) ep = cfg.endpoint;
        if (!cancelled) setEndpoint(ep);

        if (_tierCache[ep]) {
          if (!cancelled) setTiers(_tierCache[ep]);
        } else {
          const data = await fetch(`/api/model-tiers?endpoint=${encodeURIComponent(ep)}`)
            .then(r => (r.ok ? r.json() : null)).catch(() => null);
          if (data?.tiers) {
            _tierCache[ep] = data.tiers;
            if (!cancelled) setTiers(data.tiers);
          }
        }
      } catch { /* non-fatal — picker degrades to labels only */ }
    })();
    return () => { cancelled = true; };
  }, []); // once per mount; endpoint rarely changes mid-edit

  const loadModels = useCallback(async (ep: string) => {
    if (_modelsCache[ep]) { setAvailableModels(_modelsCache[ep]); return; }
    try {
      const data = await fetch(`/api/available-models?endpoint=${encodeURIComponent(ep)}`)
        .then(r => (r.ok ? r.json() : []));
      const names = Array.isArray(data) ? data.map((m: any) => m.name ?? m.id).filter(Boolean) : [];
      _modelsCache[ep] = names;
      setAvailableModels(names);
    } catch { setAvailableModels([]); }
  }, []);

  useEffect(() => {
    if (specificMode) loadModels(endpoint);
  }, [specificMode, endpoint, loadModels]);

  const setTier = (tier: ModelTier | null) => {
    // Selecting a tier clears any specific-model choice (mutually
    // exclusive per merge/resolve precedence: model_id_override >
    // model_name > model_tier).
    onChange({
      ...scope,
      model_tier: tier,
      model_name: null,
      model_id_override: null,
    });
  };

  const setSpecificModel = (name: string | null) => {
    onChange({ ...scope, model_name: name || null, model_tier: null, model_id_override: null });
  };

  const resolvedFor = (tier: ModelTier): TierInfo | undefined =>
    tiers.find(t => t.tier === tier);

  const currentTier = scope.model_tier ?? null;

  return (
    <div className="tc-model-row">
      <span className="tc-cwd-label" title="Which model this task (and everything beneath it) runs on. Tiers are portable across endpoints; a specific model is not.">
        🧠 Model:
      </span>

      {!specificMode ? (
        <>
          <div className="tc-tier-seg" role="radiogroup" aria-label="Model tier">
            <button
              type="button"
              className={`tc-tier-btn${currentTier === null ? ' tc-tier-btn-active' : ''}`}
              onClick={() => setTier(null)}
              title="Inherit the model from the card / deck / conversation (default)"
            >
              Inherit
            </button>
            {TIER_ORDER.map(tier => {
              const info = resolvedFor(tier);
              const suffix = tier === 'medium' ? ' — default/average' : '';
              const title = info
                ? `${tier}${suffix} → ${info.resolved_model}${info.exact ? '' : ' (rounded up — no exact model at this rung)'}`
                : `${tier}${suffix}`;
              return (
                <button
                  key={tier}
                  type="button"
                  className={`tc-tier-btn${currentTier === tier ? ' tc-tier-btn-active' : ''}`}
                  onClick={() => setTier(tier)}
                  title={title}
                >
                  {TIER_LABELS[tier]}
                </button>
              );
            })}
          </div>
          {currentTier && (
            <span className="tc-tier-resolved" title="What this tier currently resolves to on the active endpoint">
              {(() => {
                const info = resolvedFor(currentTier);
                if (!info) return '';
                return `→ ${info.resolved_model}${info.exact ? '' : ' (≈)'}`;
              })()}
            </span>
          )}
          <button
            type="button"
            className="tc-model-mode-link"
            onClick={() => setSpecificMode(true)}
            title="Pin a specific model instead of a portable tier (not portable across endpoints or over time)"
          >
            specific model…
          </button>
        </>
      ) : (
        <>
          <select
            className="tc-model-select"
            value={scope.model_name ?? ''}
            onChange={e => setSpecificModel(e.target.value || null)}
          >
            <option value="">(inherit)</option>
            {availableModels.map(name => (
              <option key={name} value={name}>{name}</option>
            ))}
          </select>
          <span className="tc-model-warn" title="A specific model name is not portable — it breaks if the model is retired or you switch endpoint. Prefer a tier.">
            ⚠ not portable
          </span>
          <button
            type="button"
            className="tc-model-mode-link"
            onClick={() => { setSpecificMode(false); if (hasSpecific) setTier(null); }}
            title="Switch back to portable tiers"
          >
            use a tier instead
          </button>
        </>
      )}
    </div>
  );
};

export default ModelTierPicker;
