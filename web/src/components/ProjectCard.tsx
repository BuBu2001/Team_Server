import { Link } from "react-router-dom";
import { FolderGit2, Image, FileText, GitBranch, Lock, User } from "lucide-react";
import type { Project } from "../api";
import { Badge, formatDate } from "./ui";

export function ProjectCard({ project }: { project: Project }) {
  return (
    <Link
      to={`/projects/${project.id}`}
      className="card card-hover flex flex-col gap-3 p-4"
    >
      <div className="flex items-start gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-ink-700 bg-ink-800 text-dragon-500">
          <FolderGit2 size={20} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="truncate font-semibold text-white">{project.name}</h3>
            {!project.access && <Lock size={13} className="shrink-0 text-ink-400" />}
          </div>
          <p className="mt-0.5 flex items-center gap-1.5 truncate text-xs text-ink-300">
            <User size={12} /> {project.owner}
            {project.mine && <span className="text-dragon-500">· это вы</span>}
          </p>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        {project.analyzed ? (
          <Badge tone="green">проанализирован</Badge>
        ) : (
          <Badge tone="amber">не проанализирован</Badge>
        )}
        {project.has_diagram && (
          <Badge tone="red">
            <GitBranch size={11} /> диаграмма
          </Badge>
        )}
        {project.has_png && (
          <Badge tone="neutral">
            <Image size={11} /> PNG
          </Badge>
        )}
        {project.has_digest && (
          <Badge tone="neutral">
            <FileText size={11} /> digest
          </Badge>
        )}
      </div>

      <p className="mt-auto text-xs text-ink-400">добавлен {formatDate(project.added_at)}</p>
    </Link>
  );
}
