import json
from pathlib import Path
from typing import Any


DEFAULT_STATE = {
    "target": "",
    "summary": "",
    "nodes": [],
    "links": [],
    "news": [],
    "sentiment": [],
    "finance": {},
    "comparison": {},
}


class KnowledgeRepository:
    """JSON-backed state repository for the current investigation."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.save(DEFAULT_STATE.copy())

    def load(self) -> dict[str, Any]:
        try:
            with self.path.open("r", encoding="utf-8") as file:
                value = json.load(file)
            return value if isinstance(value, dict) else DEFAULT_STATE.copy()
        except (OSError, json.JSONDecodeError):
            return DEFAULT_STATE.copy()

    def save(self, value: dict[str, Any]) -> None:
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(value, file, indent=2)
        temporary.replace(self.path)


class DossierRepository:
    """Local text-file repository with path traversal protection."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def path_for(self, name: str) -> Path:
        base = Path(name.strip()).name
        if not base:
            raise ValueError("Dossier name must not be empty")
        if not base.endswith(".txt"):
            base += ".txt"
        if base in {".txt", "..txt"} or base.startswith("."):
            raise ValueError(f"Invalid dossier name: {name!r}")
        return self.directory / base

    def exists(self, name: str) -> bool:
        return self.path_for(name).exists()

    def write(self, name: str, content: str) -> tuple[Path, str]:
        path = self.path_for(name)
        operation = "updated" if path.exists() else "created"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
        return path, operation

    def read(self, name: str) -> tuple[Path, str]:
        path = self.path_for(name)
        if not path.exists():
            raise ValueError(f"Dossier {path.name!r} does not exist")
        return path, path.read_text(encoding="utf-8")

    def update(self, name: str, content: str) -> Path:
        path = self.path_for(name)
        if not path.exists():
            raise ValueError(f"Dossier {path.name!r} does not exist")
        temporary = path.with_suffix(".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
        return path

    def delete(self, name: str) -> Path:
        path = self.path_for(name)
        if not path.exists():
            raise ValueError(f"Dossier {path.name!r} does not exist")
        path.unlink()
        return path

    def list(self) -> list[str]:
        return sorted(path.name for path in self.directory.glob("*.txt"))
