'use strict';

// Keep HAP's scheduler, interpolation, persistence, notifications and manual
// override handling. Only specialize its public transition-point accessor.
function createAdaptiveController(hap, service, mode = 'apple', referenceBrightness = 70) {
  if (!['apple', 'independent'].includes(mode)) {
    throw new Error('adaptiveLightingMode must be apple or independent');
  }
  if (!Number.isFinite(referenceBrightness) || referenceBrightness < 1 || referenceBrightness > 100) {
    throw new Error('adaptiveReferenceBrightness must be a number between 1 and 100');
  }
  const Base = hap.AdaptiveLightingController;
  if (mode === 'apple') {
    return new Base(service, {controllerMode: hap.AdaptiveLightingControllerMode.AUTOMATIC});
  }
  if (typeof Base.prototype.getCurrentAdaptiveLightingTransitionPoint !== 'function'
      || typeof Base.prototype.getAdaptiveLightingBrightnessMultiplierRange !== 'function') {
    throw new Error('This HAP version does not support independent adaptive lighting');
  }
  class IndependentAdaptiveLightingController extends Base {
    getCurrentAdaptiveLightingTransitionPoint() {
      const point = super.getCurrentAdaptiveLightingTransitionPoint();
      if (!point) return point; // HAP handles expiry, including clearing its timer.
      const range = this.getAdaptiveLightingBrightnessMultiplierRange();
      const reference = Math.max(range.minBrightnessValue, Math.min(range.maxBrightnessValue, referenceBrightness));
      const project = entry => ({
        ...entry,
        // Linear interpolation commutes with this fixed-brightness projection:
        // lerp(T) + reference * lerp(F) === lerp(T + reference * F).
        // Do not round here: HAP rounds once after interpolation, in mired.
        temperature: entry.temperature + reference * entry.brightnessAdjustmentFactor,
        brightnessAdjustmentFactor: 0,
      });
      // Never mutate Apple's original curve (also used for persistence/readback).
      // Hold durations, segment offsets and transition times remain unchanged.
      return {...point, lowerBound: project(point.lowerBound), upperBound: project(point.upperBound)};
    }
  }
  return new IndependentAdaptiveLightingController(service, {
    controllerMode: hap.AdaptiveLightingControllerMode.AUTOMATIC,
  });
}

module.exports = {createAdaptiveController};
