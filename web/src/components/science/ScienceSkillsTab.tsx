import { cn } from "@/lib/utils";
import { useScienceSkills } from "@/hooks/useScience";
import { ScienceEmpty, ScienceLoading, ScienceQueryError } from "./SciencePanel";

/**
 * Skills tab: installed science skills and project enablement (spec §14.2).
 */
export function ScienceSkillsTab({ project }: { project: string }) {
  const skillsQuery = useScienceSkills(project);
  if (skillsQuery.isPending) return <ScienceLoading />;
  if (skillsQuery.isError) return <ScienceQueryError error={skillsQuery.error} />;
  const skills = skillsQuery.data;
  if (skills.length === 0) {
    return <ScienceEmpty>No skills installed.</ScienceEmpty>;
  }
  return (
    <ul className="px-2 py-2">
      {skills.map((skill) => (
        <li key={skill.name} className="flex items-start gap-2 rounded px-1.5 py-1.5 text-xs">
          <span className="min-w-0 flex-1">
            <span className="block break-words leading-snug">
              {skill.name}
              {skill.version && <span className="text-muted-foreground"> · v{skill.version}</span>}
            </span>
            {skill.description && (
              <span className="block truncate text-[10px] text-muted-foreground">
                {skill.description}
              </span>
            )}
          </span>
          {skill.enabled !== null && (
            <span
              className={cn(
                "mt-0.5 inline-flex shrink-0 items-center rounded-full px-1.5 py-0.5 text-[9px] font-medium leading-none",
                skill.enabled ? "bg-success/15 text-success" : "bg-muted text-muted-foreground",
              )}
            >
              {skill.enabled ? "Enabled" : "Disabled"}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}
