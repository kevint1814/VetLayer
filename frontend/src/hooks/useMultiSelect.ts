import { useState, useCallback, useMemo } from "react";

/**
 * Reusable multi-select state management hook.
 * Provides selection tracking, toggle, select-all, and keyboard-friendly helpers.
 */
export function useMultiSelect<T extends { id: string }>(items: T[]) {
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const toggleItem = useCallback((id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const toggleAll = useCallback(() => {
    setSelected((prev) => {
      if (prev.size === items.length && items.length > 0) {
        return new Set();
      }
      return new Set(items.map((item) => item.id));
    });
  }, [items]);

  const clear = useCallback(() => setSelected(new Set()), []);

  const isSelected = useCallback((id: string) => selected.has(id), [selected]);

  const isAllSelected = useMemo(
    () => selected.size === items.length && items.length > 0,
    [selected.size, items.length]
  );

  const isSomeSelected = useMemo(() => selected.size > 0, [selected.size]);

  const count = selected.size;

  const selectedIds = useMemo(() => Array.from(selected), [selected]);

  return {
    selected,
    selectedIds,
    toggleItem,
    toggleAll,
    clear,
    isSelected,
    isAllSelected,
    isSomeSelected,
    count,
  };
}
