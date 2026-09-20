from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    base_dir: Path
    database_file: Path
    dossier_dir: Path
    llm_model: str = "gemini-2.5-flash"
    request_timeout_seconds: float = 10.0

    @classmethod
    def from_environment(cls) -> "Settings":
        base_dir = Path(__file__).resolve().parent.parent
        database_file = Path(os.getenv("ASSIGNMENT4_DATABASE_FILE", base_dir / "database" / "graph_db.json"))
        dossier_dir = Path(os.getenv("ASSIGNMENT4_DOSSIER_DIR", base_dir / "dossiers"))
        return cls(
            base_dir=base_dir,
            database_file=database_file,
            dossier_dir=dossier_dir,
            llm_model=os.getenv("ASSIGNMENT4_LLM_MODEL", "gemini-2.5-flash"),
        )

    def ensure_directories(self) -> None:
        self.database_file.parent.mkdir(parents=True, exist_ok=True)
        self.dossier_dir.mkdir(parents=True, exist_ok=True)
