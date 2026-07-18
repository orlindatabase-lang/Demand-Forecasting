import { useQuery } from "@tanstack/react-query";
import { dataService } from "@/services/dataService";

const REFRESH_MS = 60_000;

export function usePlanningTables() {
  return useQuery({
    queryKey: ["planningTables"],
    queryFn: () => dataService.planningTables(),
    refetchInterval: REFRESH_MS,
  });
}

export function useSkuBreakdown(sku: string | null, weeks: number) {
  return useQuery({
    queryKey: ["breakdown", sku, weeks],
    queryFn: () => dataService.skuBreakdown(sku as string, weeks),
    enabled: !!sku,
  });
}

export function usePlanSearch(query: string) {
  const q = query.trim();
  return useQuery({
    queryKey: ["planSearch", q],
    queryFn: () => dataService.searchPlan(q),
    enabled: q.length > 0,
  });
}

export function useNewDesigns(enabled: boolean, maxAgeDays = 90) {
  return useQuery({
    queryKey: ["newDesigns", maxAgeDays],
    queryFn: () => dataService.newDesigns(maxAgeDays),
    enabled,
  });
}

export function useNewDesignFestivalSpikes(enabled: boolean, maxAgeDays = 90) {
  return useQuery({
    queryKey: ["newDesignFestivalSpikes", maxAgeDays],
    queryFn: () => dataService.newDesignFestivalSpikes(maxAgeDays),
    enabled,
  });
}

export function useVerticalRollup() {
  return useQuery({
    queryKey: ["verticalRollup"],
    queryFn: () => dataService.verticalRollup(),
    refetchInterval: REFRESH_MS,
  });
}

export function useVerticalTopDownForecast() {
  return useQuery({
    queryKey: ["verticalTopDownForecast"],
    queryFn: () => dataService.verticalTopDownForecast(),
    refetchInterval: REFRESH_MS,
  });
}

export function useInhouseLots(process?: string) {
  return useQuery({
    queryKey: ["inhouseLots", process],
    queryFn: () => dataService.inhouseLots(5000, process),
    refetchInterval: REFRESH_MS,
  });
}

export function useJobWorkLots(process?: string) {
  return useQuery({
    queryKey: ["jobWorkLots", process],
    queryFn: () => dataService.jobWorkLots(10000, process),
    refetchInterval: REFRESH_MS,
  });
}

export function usePurchaseOrderLots() {
  return useQuery({
    queryKey: ["purchaseOrderLots"],
    queryFn: () => dataService.purchaseOrderLots(5000),
    refetchInterval: REFRESH_MS,
  });
}

export function useEmbroideryLots() {
  return useQuery({
    queryKey: ["embroideryLots"],
    queryFn: () => dataService.embroideryLots(5000),
    refetchInterval: REFRESH_MS,
  });
}

export function useFobLots() {
  return useQuery({
    queryKey: ["fobLots"],
    queryFn: () => dataService.fobLots(5000),
    refetchInterval: REFRESH_MS,
  });
}

export function useBottlenecks() {
  return useQuery({
    queryKey: ["bottlenecks"],
    queryFn: () => dataService.bottlenecks(),
    refetchInterval: REFRESH_MS,
  });
}

export function useInventoryPlanning() {
  return useQuery({
    queryKey: ["inventoryPlanning"],
    queryFn: () => dataService.inventoryPlanning(500),
    refetchInterval: REFRESH_MS,
  });
}
