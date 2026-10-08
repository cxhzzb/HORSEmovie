/**
 * 把 HORSEmovie / comfyui 两个本地 skill 直接注册进 DSH 的 skill 注册表。
 *
 * 为什么需要这个插件：某些部署里文件系统 skill 发现机制是坏的 ——
 * 预设自己的 customSkillDirs（dsh-agent-preset/skills）都发现不了
 * （agent-experience、cordis-plugin-development 均加载失败），
 * 宿主平面的 skill-filesystem 行又被预设 disabled。此时任何"把文件放到某目录"
 * 的做法都无效，只能改用代码注册，绕开整条文件发现链路。
 *
 * skill 正文（instructions）是**运行时从磁盘读取**的，所以改 SKILL.md 后
 * 无需重装插件。源目录按以下优先级解析：
 *   1. 插件配置的 skillsRoot
 *   2. 环境变量 HORSE_SKILLS_ROOT
 *   3. 默认 <当前用户>/.dsh/skills
 */
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const DEFAULT_SKILLS_ROOT =
  process.env.HORSE_SKILLS_ROOT || join(homedir(), ".dsh", "skills");

const SKILLS = [
  {
    dir: "horsemovie",
    source: "user-dsh",
  },
  {
    dir: "comfyui",
    source: "user-dsh",
  },
];

/** 从 SKILL.md 里取 YAML frontmatter 的一个标量字段。 */
function frontmatterField(text, key) {
  const end = text.indexOf("\n---", 3);
  const head = end < 0 ? text : text.slice(0, end);
  const m = head.match(new RegExp(`^${key}:\\s*(.+)$`, "m"));
  if (!m) return undefined;
  let v = m[1].trim();
  if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
    v = v.slice(1, -1);
  }
  return v.length > 0 ? v : undefined;
}

/** 去掉 frontmatter，返回正文。 */
function bodyOf(text) {
  const m = text.match(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/);
  return (m ? text.slice(m[0].length) : text).trim();
}

export const inject = ["skills"];

// 注意：这里不导出 Config。DSH 的 Config 需要 schemastery schema（`z.xxx`），
// 而本包零依赖；不带 schema 时 cordis 仍会把行上的 config 原样传给 apply。
export function apply(ctx, config = {}) {
  const skillsRoot = config.skillsRoot || DEFAULT_SKILLS_ROOT;
  for (const spec of SKILLS) {
    const file = join(skillsRoot, spec.dir, "SKILL.md");
    let raw;
    try {
      raw = readFileSync(file, "utf8");
    } catch (error) {
      ctx.logger?.warn?.(
        `horsemovie-skill: 读不到 ${file}，跳过（${String(error?.message ?? error)}）`,
      );
      continue;
    }
    const name = frontmatterField(raw, "name") ?? spec.dir;
    const description = frontmatterField(raw, "description");
    if (!description) {
      ctx.logger?.warn?.(`horsemovie-skill: ${file} 缺 description，跳过`);
      continue;
    }
    ctx.skills.register({
      name,
      description,
      whenToUse: frontmatterField(raw, "whenToUse"),
      content: bodyOf(raw),
      source: spec.source,
      provider: "horsemovie-local",
      invocation: { modelInvocable: true, userInvocable: true },
      resourceBase: { kind: "directory", path: join(skillsRoot, spec.dir) },
      path: file,
    });
    ctx.logger?.info?.(`horsemovie-skill: 已注册 ${name}（${file}）`);
  }
}
