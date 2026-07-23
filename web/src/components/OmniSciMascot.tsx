import { cn } from "@/lib/utils";
import mascotUrl from "@/assets/omnisci-scientist.png";

export function OmniSciMascot({ className }: { className?: string }) {
  return (
    <img
      src={mascotUrl}
      alt="OmniSci starfish scientist holding a beaker"
      draggable={false}
      className={cn("select-none object-contain", className)}
    />
  );
}
