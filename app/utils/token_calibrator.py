"""
Self-calibrating token estimator that learns from actual Bedrock responses.

Key Insights:
1. Bedrock tells us EXACT token counts in every response (zero cost)
2. Different models have different tokenizers (Claude vs Nova vs Gemini)
3. Different file types have different token densities (.py vs .json vs .md)
4. We can learn from every request and continuously improve estimates

Strategy:
- Start with reasonable defaults per model family
- Record actual usage from Bedrock responses
- Build statistics per (model_family, file_type) pair
- Use learned ratios for estimation, fall back to defaults
- Export aggregate stats for baking into releases
"""

import json
import os
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from app.utils.logging_utils import logger


@dataclass
class CalibrationSample:
    """A single calibration data point from actual Bedrock usage."""
    file_path: str
    content_length: int      # Character count
    actual_tokens: int       # Actual tokens from Bedrock
    file_type: str          # File extension (.py, .ts, etc.)
    model_id: str           # Full model ID
    model_family: str       # Model family (claude, nova, gemini, etc.)
    timestamp: float = field(default_factory=time.time)
    
    @property
    def chars_per_token(self) -> float:
        """Calculate chars/token ratio for this sample."""
        if self.actual_tokens == 0:
            return 4.1  # Fallback
        return self.content_length / self.actual_tokens


class TokenCalibrator:
    """
    Self-calibrating token estimator that learns from actual Bedrock responses.
    
    GENERIC: Learns from ANY file type it encounters.
    MODEL-AWARE: Tracks separately for Claude, Nova, Gemini, etc.
    SELF-IMPROVING: Gets more accurate with every request.
    """
    
    def __init__(self, cache_file: str = None):
        # Fixed overhead baselines (measured once from first request)
        self.baseline_overhead_tokens = {}  # model_family -> overhead_tokens
        self.baseline_mcp_tokens_per_tool = 0  # Average tokens per MCP tool
        self.baselines_measured = set()  # Set of model families with measured baselines
        
        # Persistence strategy
        if cache_file:
            self.cache_file = cache_file
        else:
            # Both CLI and server are single-user - store in user home
            # This allows learning to accumulate across CLI and web usage
            cache_dir = os.path.expanduser("~/.ziya")
            os.makedirs(cache_dir, exist_ok=True)
            self.cache_file = os.path.join(cache_dir, 'token_calibration.json')
            
        # Create lock file for safe concurrent access
        # Increase timeout to 10 seconds to handle high concurrency
        from filelock import FileLock
        self.file_lock = FileLock(self.cache_file + '.lock', timeout=10)
        
        # Track if we have pending unsaved data
        self.has_unsaved_data = False
        logger.debug(f"📊 Token calibration storage: {self.cache_file}")
        
        self.lock = threading.Lock()
        
        # GENERIC STORAGE: model_family -> file_type -> [samples]
        # This automatically learns ANY file type it encounters
        self.samples_by_model_and_type: Dict[str, Dict[str, List[CalibrationSample]]] = defaultdict(lambda: defaultdict(list))
        
        # Computed statistics: model_family -> file_type -> stats
        self.stats_by_model_and_type: Dict[str, Dict[str, Dict[str, float]]] = defaultdict(dict)
        
        # Global ratio per model family
        self.global_by_model: Dict[str, float] = {}
        
        # Ultimate fallback
        self.global_fallback = 4.1
        
        # RELEASE DEFAULTS: Starting point before we have data
        # These get better with each release as we bake in learnings
        # GENERIC: Only provides fallbacks, learns everything else dynamically
        self.release_defaults = {
            'claude': {'default': 4.1},   # Claude models use cl100k_base
            'nova': {'default': 4.0},     # Amazon Nova tokenizer (TBD)
            'gemini': {'default': 4.2},   # Google Gemini tokenizer (TBD)
            'deepseek': {'default': 4.0}, # DeepSeek tokenizer (TBD)
            'default': {'default': 4.1}   # Ultimate fallback
        }
        
        # Load existing calibration data
        self._load_calibration_data()
    
    def _load_calibration_data(self):
        """Load previously calibrated data from disk with safe concurrent access."""
        try:
            if os.path.exists(self.cache_file):
                # Safe concurrent read
                with self.file_lock:
                    with open(self.cache_file, 'r') as f:
                        data = json.load(f)
                
                    # Load nested structure
                    self.stats_by_model_and_type = defaultdict(dict, data.get('stats_by_model_and_type', {}))
                    self.global_by_model = data.get('global_by_model', {})
                    self.global_fallback = data.get('global_fallback', 4.1)
                    
                    # Load baselines
                    self.baseline_overhead_tokens = data.get('baseline_overhead_tokens', {})
                    self.baselines_measured = set(data.get('baselines_measured', []))
                
                    # Count total samples and types
                    total_models = len(self.stats_by_model_and_type)
                    total_types = sum(len(types) for types in self.stats_by_model_and_type.values())
                
                    logger.info(f"📊 Loaded calibration: {total_models} models, {total_types} file types")
                
                    # Show what we know
                    for model_family, types_dict in list(self.stats_by_model_and_type.items())[:3]:
                        logger.debug(f"   {model_family}: {len(types_dict)} types, "
                                   f"ratio: {self.global_by_model.get(model_family, 'N/A'):.2f}")
                    
                    # Verify what we loaded
                    logger.info(f"📊 LOADED: global_by_model = {self.global_by_model}")
                    
                    # Log baseline info
                    if self.baseline_overhead_tokens:
                        logger.info(f"📊 LOADED BASELINES: {len(self.baseline_overhead_tokens)} models")
                        for model, overhead in self.baseline_overhead_tokens.items():
                            logger.info(f"   {model}: {overhead:,} tokens overhead")
                
        except Exception as e:
            logger.debug(f"Could not load calibration data: {e}")
            # Continue with defaults if load fails
    
    def _save_calibration_data(self):
        """Save calibration data to disk with safe concurrent access."""
        try:
            logger.info(f"📊 SAVE: Attempting to save to {self.cache_file}")
            temp_file = self.cache_file + '.tmp'
            
            data = {
                'stats_by_model_and_type': dict(self.stats_by_model_and_type),
                'global_by_model': self.global_by_model,
                'global_fallback': self.global_fallback,
                'baseline_overhead_tokens': self.baseline_overhead_tokens,
                'baselines_measured': list(self.baselines_measured),
                'last_updated': time.time(),
                'version': '1.0'
            }
            
            # Safe concurrent write with atomic rename and retry logic
            with self.file_lock:
                with open(temp_file, 'w') as f:
                    json.dump(data, f, indent=2)
                    f.flush()  # Ensure buffered data is written
                
                
                # Atomic rename (prevents partial reads)
                os.replace(temp_file, self.cache_file)
                self.has_unsaved_data = False
                
            logger.debug(f"📊 Saved calibration data to {self.cache_file}")
            logger.info(f"📊 SAVE: Successfully saved to {self.cache_file}")
                
        except Exception as e:
            logger.debug(f"Could not save calibration data: {e}")
            logger.error(f"📊 SAVE ERROR: Failed to save: {e}")
            # Clean up temp file if it exists
            try:
                temp_file = self.cache_file + '.tmp'
                if temp_file and os.path.exists(temp_file):
                    os.remove(temp_file)
            except:
                pass
    
    def establish_baseline_if_needed(
        self,
        model_family: str,
        total_tokens: int,
        file_chars: int,
        mcp_tool_count: int
    ):
        """
        Establish baseline overhead from first request.
        
        Args:
            model_family: Model family being used
            total_tokens: Total tokens from Bedrock
            file_chars: Total characters in file content
            mcp_tool_count: Number of MCP tools in the request
        """
        if model_family in self.baselines_measured:
            return  # Already measured
        
        with self.lock:
            # Use naive 4.0 ratio for initial file estimate
            estimated_file_tokens = file_chars // 4
            
            # The difference is our baseline overhead
            baseline_overhead = total_tokens - estimated_file_tokens
            
            # Store baseline
            self.baseline_overhead_tokens[model_family] = baseline_overhead
            self.baselines_measured.add(model_family)
            
            # Also estimate per-tool cost for future reference
            if mcp_tool_count > 0:
                # Assume ~1500 tokens is base system, rest is MCP tools
                mcp_portion = max(0, baseline_overhead - 1500)
                self.baseline_mcp_tokens_per_tool = mcp_portion / mcp_tool_count
            
            logger.info(f"📊 BASELINE ESTABLISHED for {model_family}: {baseline_overhead:,} tokens "
                       f"(~{self.baseline_mcp_tokens_per_tool:.0f} per tool)")
            
            self._save_calibration_data()
    
    def get_baseline_overhead(self, model_family: str) -> int:
        """
        Get the baseline overhead for a model family.
        
        Args:
            model_family: Model family (claude, nova, etc.)
            
        Returns:
            Baseline overhead tokens, or 0 if not measured yet
        """
        return self.baseline_overhead_tokens.get(model_family, 0)
    
    def record_actual_usage(
        self,
        conversation_id: str,
        file_contents: Dict[str, str],
        actual_tokens: int,
        model_id: str = None,
        model_family: str = None
    ):
        """
        Record actual token usage from Bedrock to improve estimates.
        
        GENERIC: Learns from ANY file type it encounters.
        
        Args:
            conversation_id: Conversation ID
            file_contents: Dict mapping file_path -> content
            actual_tokens: Actual token count from Bedrock
            model_id: Model ID (e.g., "us.anthropic.claude-sonnet-4-...")
            model_family: Model family (e.g., "claude", "nova", "gemini")
        """
        # Infer model family if not provided
        if not model_family and model_id:
            model_family = self._infer_model_family(model_id)
        elif not model_family:
            model_family = self._get_current_model_family()
        
        with self.lock:
            total_chars = sum(len(content) for content in file_contents.values())
            
            if actual_tokens == 0 or total_chars == 0:
                return
            
            # Calculate actual chars/token ratio for this batch
            actual_ratio = total_chars / actual_tokens
            
            # Record sample for EACH file type encountered
            # This is GENERIC - learns whatever file types appear
            for file_path, content in file_contents.items():
                # Extract file extension (handle files with no extension)
                ext = Path(file_path).suffix.lower()
                if not ext:
                    # Try to infer from filename patterns
                    if 'Makefile' in file_path or 'Dockerfile' in file_path:
                        ext = '.makefile'
                    elif 'README' in file_path:
                        ext = '.txt'
                    else:
                        ext = '.unknown'
                
                # Estimate this file's contribution to total tokens
                file_chars = len(content)
                estimated_file_tokens = int((file_chars / total_chars) * actual_tokens)
                
                # Skip if we got a nonsensical result
                if estimated_file_tokens == 0:
                    continue
                
                sample = CalibrationSample(
                    file_path=file_path,
                    content_length=file_chars,
                    actual_tokens=estimated_file_tokens,
                    file_type=ext,
                    model_id=model_id or 'unknown',
                    model_family=model_family
                )
                
                # Store under model_family -> file_type (GENERIC!)
                self.samples_by_model_and_type[model_family][ext].append(sample)
                
                # Keep only recent samples (last 100 per model+type)
                if len(self.samples_by_model_and_type[model_family][ext]) > 100:
                    self.samples_by_model_and_type[model_family][ext] = \
                        self.samples_by_model_and_type[model_family][ext][-100:]
            
            # Recalculate statistics
            self._recalculate_stats(model_family)
            
            logger.info(f"📊 CALIBRATION: {model_family} now has {sum(len(s) for s in self.samples_by_model_and_type[model_family].values())} total samples")
            self.has_unsaved_data = True
            
            # Save more frequently - every 5 samples or immediately on first few
            total_samples = sum(
                len(samples) 
                for model_dict in self.samples_by_model_and_type.values()
                for samples in model_dict.values()
            )
            
            # Save immediately for first 20 samples (to get quick feedback)
            # Then save every 5 samples after that
            should_save = (total_samples <= 20) or (total_samples % 5 == 0)
            
            if should_save:
                logger.info(f"📊 CALIBRATION: Saving to disk (total_samples={total_samples})")
                self._save_calibration_data()
            else:
                logger.debug(f"📊 CALIBRATION: Data recorded but not saved yet ({total_samples} samples, will save at next multiple of 5)")
            
            # Log the recording
            logger.info(f"📊 CALIBRATION ({model_family}): Recorded {len(file_contents)} files, "
                       f"{total_chars:,} chars = {actual_tokens:,} tokens "
                       f"(ratio: {actual_ratio:.2f})")
            
            # Periodic detailed logging (every 50 samples for this model)
            model_samples = sum(len(s) for s in self.samples_by_model_and_type[model_family].values())
            if model_samples % 50 == 0 and model_samples > 0:
                self._log_calibration_update(model_family)
    
    def _infer_model_family(self, model_id: str) -> str:
        """Infer model family from model ID."""
        model_id_lower = model_id.lower()
        
        if 'claude' in model_id_lower or 'anthropic' in model_id_lower:
            return 'claude'
        elif 'nova' in model_id_lower:
            return 'nova'
        elif 'gemini' in model_id_lower:
            return 'gemini'
        elif 'deepseek' in model_id_lower:
            return 'deepseek'
        elif 'openai' in model_id_lower or 'gpt' in model_id_lower:
            return 'openai'
        else:
            return 'default'
    
    def _get_current_model_family(self) -> str:
        """Get the current model family being used."""
        try:
            from app.agents.models import ModelManager
            
            endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
            model_name = os.environ.get("ZIYA_MODEL")
            
            if not model_name:
                return 'default'
            
            model_config = ModelManager.get_model_config(endpoint, model_name)
            family = model_config.get('family', 'default')
            
            # Map config families to tokenizer families
            # Multiple config families may use the same tokenizer
            family_mapping = {
                'claude': 'claude',
                'nova': 'nova',
                'nova-pro': 'nova',
                'nova-lite': 'nova',
                'nova-premier': 'nova',
                'gemini-3': 'gemini',
                'gemini-2': 'gemini',
                'gemini-1': 'gemini',
                'deepseek': 'deepseek',
            }
            
            return family_mapping.get(family, 'default')
        except Exception as e:
            logger.debug(f"Could not determine model family: {e}")
            return 'default'
    
    def _recalculate_stats(self, model_family: str = None):
        """
        Recalculate statistics from samples.
        
        Args:
            model_family: If provided, only recalculate for this model.
                         Otherwise recalculate all.
        """
        models_to_calc = [model_family] if model_family else list(self.samples_by_model_and_type.keys())
        
        for model in models_to_calc:
            if model not in self.samples_by_model_and_type:
                continue
            
            model_ratios = []  # For calculating model-wide global
            
            for file_type, samples in self.samples_by_model_and_type[model].items():
                if not samples:
                    continue
                
                # Extract ratios from samples
                ratios = [s.chars_per_token for s in samples]
                ratios.sort()
                
                n = len(ratios)
                if n == 0:
                    continue
                
                # Calculate percentiles
                stats = {
                    'mean': sum(ratios) / n,
                    'median': ratios[n // 2],
                    'p50': ratios[n // 2],
                    'p95': ratios[int(n * 0.95)] if n > 20 else ratios[-1],
                    'p99': ratios[int(n * 0.99)] if n > 100 else ratios[-1],
                    'sample_count': n,
                    'min': ratios[0],
                    'max': ratios[-1]
                }
                
                self.stats_by_model_and_type[model][file_type] = stats
                model_ratios.extend(ratios)
            
            # Calculate model-wide global ratio
            if model_ratios:
                model_ratios.sort()
                self.global_by_model[model] = model_ratios[len(model_ratios) // 2]  # Median
                
                logger.debug(f"📊 {model}: {self.global_by_model[model]:.2f} chars/token "
                           f"({len(model_ratios)} samples, {len(self.samples_by_model_and_type[model])} types)")
    
    def _log_calibration_update(self, model_family: str):
        """Log a detailed calibration update for a specific model."""
        model_stats = self.stats_by_model_and_type.get(model_family, {})
        model_samples = sum(len(s) for s in self.samples_by_model_and_type[model_family].values())
        
        logger.info("\n" + "=" * 80)
        logger.info(f"📊 TOKEN CALIBRATION UPDATE - {model_family.upper()}")
        logger.info("=" * 80)
        logger.info(f"Samples Collected: {model_samples:,}")
        logger.info(f"File Types Learned: {len(model_stats)}")
        logger.info(f"Model Global Ratio: {self.global_by_model.get(model_family, 'N/A'):.2f} chars/token")
        logger.info("")
        
        # Show top 10 most-sampled types
        if model_stats:
            logger.info("Top File Types:")
            sorted_types = sorted(model_stats.items(), 
                                key=lambda x: x[1]['sample_count'], reverse=True)
            
            for ext, stats in sorted_types[:10]:
                logger.info(f"  {ext:12s}: {stats['median']:.2f} chars/token "
                          f"(p95: {stats['p95']:.2f}, samples: {stats['sample_count']})")
        
        logger.info("=" * 80 + "\n")
    
    def estimate_tokens(
        self, 
        content: str, 
        file_path: Optional[str] = None,
        model_family: Optional[str] = None
    ) -> int:
        """
        Estimate tokens using calibrated data.
        
        Falls back gracefully through multiple tiers:
        1. Calibrated data for this model + file type (most accurate)
        2. Release default for this model + file type
        3. Calibrated global ratio for this model
        4. Release default for this model
        5. Global fallback (4.1 chars/token)
        
        Args:
            content: Text content to estimate
            file_path: Optional file path for type-specific estimation
            model_family: Optional model family (inferred if not provided)
            
        Returns:
            Estimated token count
        """
        if not model_family:
            model_family = self._get_current_model_family()
            logger.info(f"📊 ESTIMATE: No model_family provided, inferred: '{model_family}'")
        
        # CRITICAL DEBUG: Log what we have available
        logger.info(f"📊 ESTIMATE-STATE: model_family='{model_family}', "
                   f"global_by_model keys={list(self.global_by_model.keys())}, "
                   f"stats keys={list(self.stats_by_model_and_type.keys())}")
        
        logger.debug(f"📊 ESTIMATE: Estimating {len(content):,} chars for model_family={model_family}, file_path={file_path}")
        
        content_length = len(content)
        if content_length == 0:
            return 0
        
        # Extract file type if path provided
        file_type = None
        if file_path:
            file_type = Path(file_path).suffix.lower() or '.unknown'
        
        # Tier 1: Calibrated model+type specific (BEST)
        if file_type and model_family in self.stats_by_model_and_type:
            if file_type in self.stats_by_model_and_type[model_family]:
                stats = self.stats_by_model_and_type[model_family][file_type]
                
                # Use p95 for conservative estimates (handles outliers)
                chars_per_token = stats['p95']
                estimated = int(content_length / chars_per_token)
                
                logger.debug(f"📊 [{model_family}] Calibrated {file_type}: {estimated:,} tokens "
                           f"(ratio: {chars_per_token:.2f}, {stats['sample_count']} samples)")
                
                return estimated
        
        # Tier 2: Release defaults for model+type
        if file_type and model_family in self.release_defaults:
            if file_type in self.release_defaults[model_family]:
                chars_per_token = self.release_defaults[model_family][file_type]
                estimated = int(content_length / chars_per_token)
                
                logger.debug(f"📊 [{model_family}] Release default {file_type}: {estimated:,} tokens")
                return estimated
        
        # Tier 3: Calibrated model-wide global
        if model_family in self.global_by_model:
            chars_per_token = self.global_by_model[model_family]
            estimated = int(content_length / chars_per_token)
            
            logger.info(f"📊 [{model_family}] Using model global: {estimated:,} tokens (ratio: {chars_per_token:.2f})")
        else:
            logger.warning(f"📊 [{model_family}] NOT FOUND in global_by_model! Keys available: {list(self.global_by_model.keys())}")
            logger.debug(f"📊 [{model_family}] Model global: {estimated:,} tokens")
            return estimated
        
        # Tier 4: Release default for model
        if model_family in self.release_defaults:
            chars_per_token = self.release_defaults[model_family].get('default', self.global_fallback)
            estimated = int(content_length / chars_per_token)
            
            logger.debug(f"📊 [{model_family}] Model default: {estimated:,} tokens")
            return estimated
        
        # Tier 5: Ultimate fallback
        estimated = int(content_length / self.global_fallback)
        logger.debug(f"📊 [fallback] Global: {estimated:,} tokens (ratio: {self.global_fallback:.2f})")
        
        return estimated
    
    def get_stats(self) -> Dict[str, Any]:
        """Get calibration statistics."""
        with self.lock:
            # Count total samples across all models and types
            total_samples = 0
            for model_dict in self.samples_by_model_and_type.values():
                for samples_list in model_dict.values():
                    total_samples += len(samples_list)
            
            return {
                'total_samples': total_samples,
                'calibrated_models': list(self.stats_by_model_and_type.keys()),
                'global_by_model': self.global_by_model,
                'global_fallback': self.global_fallback,
                'stats_by_model_and_type': dict(self.stats_by_model_and_type)
            }
    
    def get_accuracy_report(self) -> str:
        """Generate human-readable accuracy report."""
        stats = self.get_stats()
        
        lines = [
            "📊 Token Estimation Calibration Report",
            "=" * 60,
            f"Total Samples: {stats['total_samples']:,}",
            f"Calibrated Models: {', '.join(stats['calibrated_models']) if stats['calibrated_models'] else 'None yet'}",
            ""
        ]
        
        # Show statistics per model
        for model_family in sorted(stats['calibrated_models']):
            model_global = stats['global_by_model'].get(model_family, 'N/A')
            lines.append(f"Model: {model_family} (global: {model_global:.2f} chars/token)")
            
            model_stats = stats['stats_by_model_and_type'].get(model_family, {})
            
            # Show top 10 file types for this model
            sorted_types = sorted(
                model_stats.items(),
                key=lambda x: x[1]['sample_count'],
                reverse=True
            )[:10]
            
            for file_type, type_stats in sorted_types:
                lines.append(
                    f"  {file_type:12s}: {type_stats['median']:.2f} chars/token "
                    f"(mean: {type_stats['mean']:.2f}, "
                    f"p95: {type_stats['p95']:.2f}, "
                    f"samples: {type_stats['sample_count']})"
                )
            
            lines.append("")
        
        if not stats['calibrated_models']:
            lines.append("No calibration data yet. Data will be collected automatically.")
        
        return "\n".join(lines)
    
    def export_for_release(self) -> Dict[str, Dict[str, float]]:
        """
        Export calibration data for baking into next release.
        
        Returns:
            Nested dict: model_family -> file_type -> chars_per_token
        """
        with self.lock:
            export_data = {}
            
            for model_family, types_dict in self.stats_by_model_and_type.items():
                export_data[model_family] = {}
                
                for file_type, stats in types_dict.items():
                    # Only export if we have enough samples (20+)
                    if stats['sample_count'] >= 20:
                        # Use median as the recommended value (robust to outliers)
                        export_data[model_family][file_type] = round(stats['median'], 2)
            
            return export_data
    
    def get_aggregated_stats_for_release(self) -> str:
        """
        Generate Python code for updating release defaults.
        Makes it easy to copy-paste improved defaults into code.
        """
        export_data = self.export_for_release()
        
        lines = [
            "# RELEASE DEFAULTS: Model-aware token estimation",
            "# Auto-generated from aggregate production data",
            "# Last updated: " + time.strftime("%Y-%m-%d %H:%M:%S"),
            "",
            "self.release_defaults = {"
        ]
        
        for model_family, types_dict in sorted(export_data.items()):
            lines.append(f"    '{model_family}': {{")
            
            for file_type, ratio in sorted(types_dict.items()):
                sample_count = self.stats_by_model_and_type[model_family][file_type]['sample_count']
                lines.append(f"        '{file_type}': {ratio:.2f},  # {sample_count} samples")
            
            lines.append("        'default': " + f"{export_data[model_family].get('default', 4.1):.2f}")
            lines.append("    },")
        
        lines.append("}")
        
        return "\n".join(lines)


# Global singleton
_calibrator_instance = None
_calibrator_lock = threading.Lock()


def get_token_calibrator() -> TokenCalibrator:
    """Get or create the global token calibrator singleton."""
    global _calibrator_instance
    with _calibrator_lock:
        if _calibrator_instance is None:
            _calibrator_instance = TokenCalibrator()
        return _calibrator_instance
