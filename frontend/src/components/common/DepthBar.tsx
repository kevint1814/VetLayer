import clsx from "clsx";

interface DepthBarProps {
  depth: number;       // 1-5
  maxDepth?: number;   // default 5
  required?: number;   // optional requirement line
  label?: string;
  confidence?: number; // 0-1
}

const depthColors: Record<number, string> = {
  1: "bg-red-400",
  2: "bg-orange-400",
  3: "bg-yellow-400",
  4: "bg-blue-500",
  5: "bg-emerald-500",
};

const depthLabels: Record<number, string> = {
  1: "Awareness",
  2: "Beginner",
  3: "Intermediate",
  4: "Advanced",
  5: "Expert",
};

export default function DepthBar({ depth, maxDepth = 5, required, label, confidence }: DepthBarProps) {
  const pct = (depth / maxDepth) * 100;
  const reqPct = required ? (required / maxDepth) * 100 : null;
  const meetsReq = required ? depth >= required : true;

  return (
    <div className="space-y-1.5">
      {label && (
        <div className="flex items-center justify-between">
          <span className="text-sm font-medium text-text-primary">{label}</span>
          <div className="flex items-center gap-2">
            <span className={clsx(
              "text-xs font-semibold",
              meetsReq ? "text-status-success" : "text-status-danger"
            )}>
              {depth}/{maxDepth}
            </span>
            <span className="text-2xs text-text-tertiary">
              {depthLabels[depth] || "Unknown"}
            </span>
          </div>
        </div>
      )}
      <div className="depth-bar relative">
        {/* Required marker */}
        {reqPct && (
          <div
            className="absolute top-0 bottom-0 w-0.5 bg-text-primary/30 z-10"
            style={{ left: `${reqPct}%` }}
          />
        )}
        {/* Fill */}
        <div
          className={clsx("depth-fill", depthColors[depth] || "bg-gray-400")}
          style={{ width: `${pct}%` }}
        />
      </div>
      {confidence !== undefined && (
        <p className="text-2xs text-text-tertiary">
          {Math.round(confidence * 100)}% confidence
        </p>
      )}
    </div>
  );
}
