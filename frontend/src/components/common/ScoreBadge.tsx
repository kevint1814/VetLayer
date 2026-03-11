import clsx from "clsx";

interface ScoreBadgeProps {
  score: number;
  size?: "sm" | "md" | "lg";
  showLabel?: boolean;
}

function getScoreInfo(score: number) {
  if (score >= 0.8) return { label: "Excellent", className: "score-excellent" };
  if (score >= 0.65) return { label: "Good", className: "score-good" };
  if (score >= 0.45) return { label: "Fair", className: "score-fair" };
  return { label: "Needs Review", className: "score-poor" };
}

export default function ScoreBadge({ score, size = "md", showLabel = true }: ScoreBadgeProps) {
  const { label, className } = getScoreInfo(score);
  const pct = Math.round(score * 100);

  return (
    <div
      className={clsx(
        "inline-flex items-center gap-1.5 font-semibold rounded-lg",
        className,
        size === "sm" && "px-2 py-1 text-xs",
        size === "md" && "px-3 py-1.5 text-sm",
        size === "lg" && "px-4 py-2 text-base"
      )}
    >
      <span>{pct}%</span>
      {showLabel && <span className="font-medium opacity-80">· {label}</span>}
    </div>
  );
}
