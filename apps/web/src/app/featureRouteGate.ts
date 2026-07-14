export type FeatureRouteLoader<T> = () => Promise<T>

export function selectFeatureRouteLoader<T>(
  enabled: boolean,
  loadEnabled: FeatureRouteLoader<T>,
  loadUnavailable: FeatureRouteLoader<T>,
): FeatureRouteLoader<T> {
  return enabled ? loadEnabled : loadUnavailable
}
