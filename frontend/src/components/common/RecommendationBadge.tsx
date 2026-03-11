import clsx from "clsx";
import { ThumbsUp, ThumbsDown, Minus, CheckCircle2, XCircle } from "lucide-react";

interface RecommendationBadgeProps {
  recommendation: string;
  size?: "sm" | "md" | "lg";
}

const recConfig: Record<string, { label: string; className: string; Icon: typeof ThumbsUp }> = {
  strong_yes: { label: "Strong Yes", className: "bg-emerald-50 text-emerald-700 border-emerald-200", Icon: CheckCircle2 },
  yes: { label: "Recommended", className: "bg-blue-50 text-blue-700 border-blue-200", Icon: ThumbsUp },
  maybe: { label: "Maybe", className: "bg-amber-50 text-amber-700 border-amber-200", Icon: Minus },
  no: { label: "Not Recommended", className: "bg-red-50 text-red-600 border-red-200", Icon: ThumbsDown },
  strong_no: { label: "Strong No", className: "bg-red-100 text-red-700 border-red-300", Icon: XCircle },
  pending: { label: "Pending", className: "bg-gray-50 text-gray-500 border-gray-200", Icon: Minus },
};

export default function RecommendationBadge({ recommendation, size = "md" }: RecommendationBadgeProps) {
  const config = recConfig[recommendation] ?? recConfig.pending!;
  const { label, className, Icon } = config!;

  return (
    <div
      className={clsx(
        "inline-flex items-center gap-2 font-semibold rounded-xl border",
        className,
        size === "sm" && "px-2.5 py-1 text-xs",
        size === "md" && "px-3.5 py-1.5 text-sm",
        size === "lg" && "px-5 py-2.5 text-base"
      )}
    >
      <Icon size={size === "sm" ? 14 : size === "lg" ? 20 : 16} />
      {label}
    </div>
  );
}
