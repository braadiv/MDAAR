"""
Skill Loader with Progressive Disclosure for MDAAR v2.0.0.
Loads bundled domain skills from mdaar/skills/*/SKILL.md inside the installed package
and optionally discovers sanitized project-specific instructions from the target repo.
"""

from dataclasses import dataclass
from pathlib import Path
import re

from mdaar.security.sanitizer import SecretSanitizer


ROLE_SKILL_MAP = {
    "planner": ["core-grounding-qa", "reverse-engineering", "docs-technical-writer", "architecture-mermaid"],
    "mapper": ["core-grounding-qa", "reverse-engineering"],
    "architecture": ["core-grounding-qa", "architecture-mermaid", "docs-technical-writer"],
    "database": ["core-grounding-qa", "architecture-mermaid", "docs-technical-writer"],
    "api": ["core-grounding-qa", "api-openapi-spec", "docs-technical-writer"],
    "operations": ["core-grounding-qa", "ops-and-user-guides", "docs-technical-writer"],
    "user_guide": ["core-grounding-qa", "ops-and-user-guides", "docs-technical-writer"],
    "governance": ["core-grounding-qa", "docs-technical-writer"],
    "quality": ["core-grounding-qa", "architecture-mermaid"],
    "rag": ["core-grounding-qa", "rag-qa-grounding"],
}


@dataclass
class SkillDoc:
    name: str
    description: str
    body: str
    path: Path


class SkillLoader:
    """Loads bundled MDAAR skills cleanly and supports optional target-repo instruction discovery."""

    def __init__(self, skills_dir=None):
        self.skills_dir = Path(skills_dir) if skills_dir else Path(__file__).resolve().parent
        self._cache = {}

    @staticmethod
    def _parse_skill_markdown(text, default_name, path):
        name = default_name
        description = ""
        body = text.strip()

        fm_match = re.match(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?(.*)$", text, re.DOTALL)
        if fm_match:
            fm_block = fm_match.group(1)
            body = fm_match.group(2).strip()
            for line in fm_block.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    k = k.strip().lower()
                    v = v.strip().strip("'\"")
                    if k == "name" and v:
                        name = v
                    elif k == "description" and v:
                        description = v

        return SkillDoc(name=name, description=description, body=body, path=path)

    def list_bundled_skills(self):
        """Return metadata for all bundled SKILL.md files."""
        skills = []
        if not self.skills_dir.exists():
            return skills
        for child in sorted(self.skills_dir.iterdir()):
            skill_md = child / "SKILL.md"
            if child.is_dir() and skill_md.is_file():
                doc = self.get_skill(child.name)
                if doc:
                    skills.append(doc)
        return skills

    def get_skill(self, skill_name):
        """Load a single bundled skill by directory name."""
        if skill_name in self._cache:
            return self._cache[skill_name]
        skill_md = self.skills_dir / skill_name / "SKILL.md"
        if not skill_md.is_file():
            return None
        raw = skill_md.read_text(encoding="utf-8", errors="replace")
        doc = self._parse_skill_markdown(raw, skill_name, skill_md)
        self._cache[skill_name] = doc
        return doc

    def load_skills(self, skill_names):
        """Render a compact combined prompt block for a specific list of bundled skills."""
        sections = []
        for sname in skill_names:
            doc = self.get_skill(sname)
            if doc:
                sections.append(f"### [MDAAR Skill: {doc.name}]\n{doc.body}")
        return "\n\n".join(sections)

    def load_for_role(self, role, extra_repo_instructions=""):
        """Progressive disclosure: inject only the exact skills needed for an agent role."""
        names = ROLE_SKILL_MAP.get(role, ["core-grounding-qa", "docs-technical-writer"])
        block = self.load_skills(names)
        if extra_repo_instructions:
            block = f"{block}\n\n### [Target Repository Instructions]\n{extra_repo_instructions.strip()}"
        return block

    @staticmethod
    def discover_repo_instructions(repo_dir, max_chars=4000):
        """Read target repo's AGENTS.md / GEMINI.md / .agents/skills cleanly with secret sanitization."""
        repo_path = Path(repo_dir)
        snippets = []

        for fname in ("AGENTS.md", "GEMINI.md", "CLAUDE.md"):
            cand = repo_path / fname
            if cand.is_file():
                raw = cand.read_text(encoding="utf-8", errors="replace")
                clean = SecretSanitizer.sanitize(raw, fname)[:max_chars]
                snippets.append(f"From `{fname}`:\n{clean}")

        skills_root = repo_path / ".agents" / "skills"
        if skills_root.is_dir():
            for child in sorted(skills_root.iterdir())[:10]:
                smd = child / "SKILL.md"
                if smd.is_file():
                    raw = smd.read_text(encoding="utf-8", errors="replace")
                    clean = SecretSanitizer.sanitize(raw, str(smd.relative_to(repo_path)))[:1500]
                    snippets.append(f"From repo skill `{child.name}`:\n{clean}")

        return "\n\n".join(snippets)[:max_chars]
