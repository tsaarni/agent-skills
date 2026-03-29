from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field
from jinja2 import Environment, FileSystemLoader


@dataclass
class ReviewContext:
    """Holds all data needed for the code review template."""

    basedir: str
    metadata: Optional[dict] = None
    metadata_file: Optional[str] = None
    context_file: Optional[str] = None
    diff_file: str = "pr.diff"
    changed_files: List[Dict[str, Any]] = field(default_factory=list)
    impact_scope: List[Dict[str, Any]] = field(default_factory=list)

    def render(self, template_dir: str, template_name: str) -> str:
        env = Environment(loader=FileSystemLoader(template_dir))
        template = env.get_template(template_name)
        return template.render(
            basedir=self.basedir,
            metadata=self.metadata,
            metadata_file=self.metadata_file,
            context_file=self.context_file,
            diff_file=self.diff_file,
            changed_files=self.changed_files,
            impact_scope=self.impact_scope,
        )
