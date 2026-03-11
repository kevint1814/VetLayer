import { Trash2, X, CheckSquare } from "lucide-react";

interface BulkActionBarProps {
  count: number;
  totalCount: number;
  isAllSelected: boolean;
  onToggleAll: () => void;
  onDelete: () => void;
  onClear: () => void;
}

/**
 * Sticky action bar that appears when items are selected.
 * Provides bulk actions (delete, select all, clear).
 */
export default function BulkActionBar({
  count,
  totalCount,
  isAllSelected,
  onToggleAll,
  onDelete,
  onClear,
}: BulkActionBarProps) {
  if (count === 0) return null;

  return (
    <div className="sticky top-0 z-30 mb-4 animate-slide-up">
      <div className="bg-sidebar text-white rounded-lg px-5 py-2.5 flex items-center justify-between shadow-lg">
        <div className="flex items-center gap-4">
          <span className="text-sm font-semibold">
            {count} selected
          </span>
          <button
            onClick={onToggleAll}
            className="inline-flex items-center gap-1.5 text-xs font-medium text-white/80 hover:text-white transition-colors"
          >
            <CheckSquare size={14} />
            {isAllSelected ? "Deselect all" : `Select all ${totalCount}`}
          </button>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={onDelete}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-white/15 hover:bg-white/25 transition-colors"
          >
            <Trash2 size={13} />
            Delete
          </button>
          <button
            onClick={onClear}
            className="p-1.5 rounded-lg text-white/60 hover:text-white hover:bg-white/15 transition-colors"
            title="Clear selection"
          >
            <X size={15} />
          </button>
        </div>
      </div>
    </div>
  );
}
