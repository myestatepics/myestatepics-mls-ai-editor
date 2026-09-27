"""V8.0 dual-folder MLS editor; V7 remains an untouched production baseline."""
from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

# Ensure V8 has independent Application Support data before importing the V7 engine.
os.environ.setdefault("MYESTATEPICS_APPLICATION_NAME", "MyEstatePics AI Editor - V8.0")
import v7_editor as core

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog,
    QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QMainWindow,
    QPlainTextEdit, QProgressBar, QPushButton, QRadioButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget, QMessageBox)

PROGRAM_VERSION = "8.0"
MODEL = "gpt-image-2.5-sunburst"
QUALITY_OPTIONS = ("medium", "high")
INTERIOR_PROMPT = Path(core.PROMPT_FILE)
EXTERIOR_PROMPT = Path(__file__).with_name("prompts") / "v8_exterior.txt"
TWILIGHT_PROMPT = Path(__file__).with_name("prompts") / "v8_twilight.txt"
SUPPORTED_EXTENSIONS = core.SUPPORTED_EXTENSIONS


@dataclass(frozen=True)
class V8Job:
    source: Path
    kind: str  # interior, exterior, twilight

    @property
    def output_name(self) -> str:
        return (f"{self.source.stem}-TWILIGHT{self.source.suffix}" if self.kind == "twilight"
                else self.source.name)


@dataclass
class V8Summary:
    interior: int = 0
    exterior: int = 0
    twilight: int = 0
    completed: int = 0
    review: int = 0
    errors: int = 0
    api_calls: int = 0
    before_pdf: Path | None = None
    after_pdf: Path | None = None
    cache_root: Path | None = None
    errors_by_name: dict[str, str] = field(default_factory=dict)


def supported_images(folder: Path) -> list[Path]:
    return sorted((p.resolve() for p in Path(folder).iterdir()
                   if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS),
                  key=lambda p: p.name.casefold())


def finder_tags(path: Path) -> tuple[str, ...]:
    """Read Finder tags locally; unavailable metadata simply means no tag."""
    try:
        raw = os.getxattr(path, "com.apple.metadata:_kMDItemUserTags")
        values = plistlib.loads(raw)
        return tuple(str(value).split("\n", 1)[0] for value in values)
    except (OSError, ValueError, plistlib.InvalidFileException):
        return ()


def red_tagged_images(exterior_folder: Path) -> list[Path]:
    return [p for p in supported_images(exterior_folder)
            if any(tag.casefold() == "red" for tag in finder_tags(p))]


def twilight_preflight(exterior_folder: Path) -> tuple[bool, list[Path], str]:
    heroes = red_tagged_images(exterior_folder)
    if len(heroes) > 1:
        return False, heroes, "Only one Exterior image may have the RED Finder tag: " + ", ".join(p.name for p in heroes)
    return True, heroes, ""


def exterior_instruction(landscape: str, distractions: str) -> str:
    if landscape not in {"Natural", "Enhanced"} or distractions not in {"Remove", "Keep"}:
        raise ValueError("Unsupported V8 exterior option")
    landscape_text = ("Preserve actual grass and landscaping condition. Do not make grass greener, replace grass, repair dead grass, fill bare areas, or create plants, flowers, or landscaping."
        if landscape == "Natural" else
        "Conservatively polish existing grass and landscaping only. Do not change boundaries, add lawn, flowers, trees, shrubs, mulch beds, or hide permanent defects.")
    distraction_text = ("Conservatively remove only temporary photographic distractions such as trash bins, people, cars, cones, small temporary signs, loose debris, and obvious temporary clutter. Do not remove permanent architecture, infrastructure, neighboring buildings, or permanent landscaping."
        if distractions == "Remove" else "Do not intentionally remove photographic objects.")
    return f"\n\nLANDSCAPE: {landscape_text}\n\nDISTRACTIONS: {distraction_text}"


def prompt_for(job: V8Job, landscape: str, distractions: str) -> str:
    if job.kind == "interior":
        return INTERIOR_PROMPT.read_text(encoding="utf-8")
    base = (TWILIGHT_PROMPT if job.kind == "twilight" else EXTERIOR_PROMPT).read_text(encoding="utf-8")
    return base + exterior_instruction(landscape, distractions)


class ComparisonCache:
    """Private per-batch evidence; never read reports from live deliverables."""
    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="myestatepics_v8_report_"))
        self.before: dict[V8Job, Path] = {}
        self.after: dict[V8Job, Path] = {}

    def snapshot_before(self, job: V8Job) -> None:
        target = self.root / "before" / job.output_name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(job.source, target)
        self.before[job] = target

    def snapshot_after(self, job: V8Job, jpeg: bytes) -> Path:
        target = self.root / "after" / job.output_name
        target.parent.mkdir(parents=True, exist_ok=True)
        core._atomic_write(target, jpeg)
        self.after[job] = target
        return target

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


def build_jobs(interior_folder: Path, exterior_folder: Path) -> tuple[list[V8Job], list[Path]]:
    interiors = [V8Job(p, "interior") for p in supported_images(interior_folder)]
    exteriors = [V8Job(p, "exterior") for p in supported_images(exterior_folder)]
    ok, heroes, message = twilight_preflight(exterior_folder)
    if not ok:
        raise ValueError(message)
    if heroes:
        exteriors.append(V8Job(heroes[0], "twilight"))
    names: set[str] = set()
    for job in interiors + exteriors:
        key = job.output_name.casefold()
        if key in names:
            raise ValueError(f"Output filename collision: {job.output_name}")
        names.add(key)
    return interiors + exteriors, heroes


def generate_v8_reports(cache: ComparisonCache, jobs: list[V8Job], output_folder: Path) -> tuple[Path, Path]:
    report_dir = Path(output_folder) / "Batch Reviews" / f"V8-{int(time.time())}"
    report_dir.mkdir(parents=True, exist_ok=False)
    before_pdf = report_dir / "MyEstatePics_V8.0_BEFORE.pdf"
    after_pdf = report_dir / "MyEstatePics_V8.0_AFTER.pdf"
    # Internal V7 renderer preserves four-per-page layout; both maps use cache files.
    ordered = [cache.before[job] for job in jobs]
    after_map = {cache.before[job].resolve(): cache.after.get(job) for job in jobs}
    core._write_review_contact_sheet(before_pdf, ordered, {}, after=False)
    core._write_review_contact_sheet(after_pdf, ordered, after_map, after=True)
    return before_pdf, after_pdf


def process_v8_batch(client, *, interior_folder: Path, exterior_folder: Path, output_folder: Path,
                     quality: str = "medium", landscape: str = "Natural", distractions: str = "Remove",
                     event: Callable[[str, dict], None] = lambda _k, _p: None) -> V8Summary:
    """One direct Images Edit call for each normal job, plus one twilight hero call."""
    if quality not in QUALITY_OPTIONS:
        raise ValueError("V8 quality must be medium or high")
    jobs, _heroes = build_jobs(interior_folder, exterior_folder)
    output_folder = Path(output_folder).resolve(); output_folder.mkdir(parents=True, exist_ok=True)
    cache = ComparisonCache(); summary = V8Summary(cache_root=cache.root)
    for job in jobs:
        cache.snapshot_before(job)
    try:
        for index, job in enumerate(jobs, 1):
            event("started", {"index": index, "total": len(jobs), "filename": job.output_name, "kind": job.kind})
            try:
                destination = output_folder / job.output_name
                if destination.exists():
                    raise FileExistsError(f"Refusing to overwrite existing output: {destination.name}")
                generated, _size, _usage = core.call_image_editor(client, job.source, prompt_for(job, landscape, distractions), quality)
                summary.api_calls += 1
                with core.Image.open(core.BytesIO(generated)) as image:
                    final = core.apply_premium_finish(image.convert("RGB"))
                jpeg, _jpeg_quality = core.encode_final_jpeg(final, job.source)
                # Snapshot before exposing deliverable so deletion/move cannot affect the report.
                cache.snapshot_after(job, jpeg)
                core._atomic_write(destination, jpeg)
                verification = core.compare_images(job.source, final, False)
                if verification.status in {"REVIEW", "FAIL"}: summary.review += 1; status = "REVIEW"
                else: summary.completed += 1; status = "COMPLETED"
                if job.kind == "interior": summary.interior += 1
                elif job.kind == "exterior": summary.exterior += 1
                else: summary.twilight += 1
                event("finished", {"filename": job.output_name, "status": status, "destination": str(destination)})
            except Exception as exc:
                summary.errors += 1; summary.errors_by_name[job.output_name] = str(exc)
                event("failed", {"filename": job.output_name, "status": "ERROR", "error": str(exc)})
        summary.before_pdf, summary.after_pdf = generate_v8_reports(cache, jobs, output_folder)
        cache.cleanup()
        return summary
    except Exception:
        # Keep snapshots for in-session regeneration after an actual report failure.
        raise


class V8Window(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle("MyEstatePics AI Editor — V8.0"); self.resize(980, 760)
        self.settings = QSettings(str(core.USER_DATA_DIR / "v8_preferences.ini"), QSettings.IniFormat)
        self.interior = Path(self.settings.value("folders/interior", str(core.USER_DATA_DIR / "Interior")))
        self.exterior = Path(self.settings.value("folders/exterior", str(core.USER_DATA_DIR / "Exterior")))
        self.output = Path(self.settings.value("folders/output", str(core.USER_DATA_DIR / "Output")))
        self._build(); self.refresh_twilight()

    def _build(self):
        self.setWindowTitle("MyEstatePics AI Editor — V8.0"); self.resize(1140, 760); self.setMinimumSize(980, 680)
        root = QWidget(); layout = QVBoxLayout(root); layout.setContentsMargins(22, 18, 22, 18); layout.setSpacing(10)
        header = QHBoxLayout(); title = QLabel("MyEstatePics AI Editor\nV8.0"); title.setStyleSheet("font-size: 19px; font-weight: 700;")
        subtitle = QLabel("MLS Production Editor"); model = QLabel("Model: Sunburst\nAPI: Ready")
        header.addWidget(title); header.addWidget(subtitle, 1); header.addWidget(model); layout.addLayout(header)
        setup = QGroupBox("JOB SETUP"); form = QGridLayout(setup); form.setColumnStretch(1, 1)
        self.paths = []
        self.folder_counts = {}
        for row, (label, attr) in enumerate((("Interior", "interior"), ("Exterior", "exterior"), ("Output", "output"))):
            value = QLabel(); value.setTextInteractionFlags(Qt.TextSelectableByMouse); value.setToolTip(str(getattr(self, attr))); value.setMinimumWidth(450)
            value.setStyleSheet("padding: 5px; border: 1px solid palette(mid); border-radius: 4px;")
            choose = QPushButton("Choose"); choose.setFixedWidth(76); open_button = QPushButton("Open"); open_button.setFixedWidth(76)
            choose.clicked.connect(lambda _=False, name=attr: self.browse(name)); open_button.clicked.connect(lambda _=False, name=attr: self.open_folder(name))
            count = QLabel(""); self.folder_counts[attr] = count
            form.addWidget(QLabel(label), row * 2, 0); form.addWidget(value, row * 2, 1); form.addWidget(choose, row * 2, 2); form.addWidget(open_button, row * 2, 3); form.addWidget(count, row * 2 + 1, 1, 1, 3); self.paths.append(value)
        layout.addWidget(setup)
        options = QGroupBox("EXTERIOR OPTIONS"); opt = QGridLayout(options); opt.setColumnStretch(1, 1)
        self.natural = QRadioButton("Natural"); self.enhanced = QRadioButton("Enhanced"); self.natural.setChecked(True)
        self.remove = QRadioButton("Remove"); self.keep = QRadioButton("Keep"); self.remove.setChecked(True)
        landscape = QHBoxLayout(); landscape.addWidget(self.natural); landscape.addWidget(self.enhanced); landscape.addStretch(1)
        distractions = QHBoxLayout(); distractions.addWidget(self.remove); distractions.addWidget(self.keep); distractions.addStretch(1)
        opt.addWidget(QLabel("Landscape"), 0, 0); opt.addLayout(landscape, 0, 1); opt.addWidget(QLabel("Preserve existing lawn and landscaping"), 1, 1)
        opt.addWidget(QLabel("Distractions"), 2, 0); opt.addLayout(distractions, 2, 1); opt.addWidget(QLabel("Remove temporary distractions"), 3, 1)
        self.hero = QLabel(); self.hero_note = QLabel("Red Finder tag creates one additional early-twilight image."); self.quality = QComboBox(); self.quality.addItems(["MEDIUM", "HIGH"]); self.quality.setFixedWidth(130)
        opt.addWidget(QLabel("Twilight Hero"), 4, 0); opt.addWidget(self.hero, 4, 1); opt.addWidget(self.hero_note, 5, 1); opt.addWidget(QLabel("Quality"), 6, 0); opt.addWidget(self.quality, 6, 1); layout.addWidget(options)
        preflight = QGroupBox("PRE-FLIGHT"); pre = QGridLayout(preflight); self.preflight = QLabel(); self.ready = QLabel(); self.start = QPushButton("START PROCESSING"); self.start.setFixedWidth(210); self.start.clicked.connect(self.start_processing)
        pre.addWidget(self.preflight, 0, 0); pre.addWidget(self.ready, 1, 0); pre.addWidget(self.start, 0, 1, 2, 1); layout.addWidget(preflight)
        processing = QGroupBox("PROCESSING"); process = QVBoxLayout(processing); self.progress = QProgressBar(); self.progress.setTextVisible(True); self.status = QLabel("Ready"); self.counts = QLabel("Completed 0    Review 0    Errors 0"); process.addWidget(self.progress); process.addWidget(self.status); process.addWidget(self.counts); layout.addWidget(processing)
        activity = QGroupBox("ACTIVITY"); al = QVBoxLayout(activity); self.activity = QTableWidget(0, 3); self.activity.setHorizontalHeaderLabels(["TYPE", "FILE", "STATUS"]); self.activity.horizontalHeader().setStretchLastSection(True); self.activity.setMaximumHeight(190); al.addWidget(self.activity); layout.addWidget(activity, 1); self.setCentralWidget(root)
        for control in (self.natural, self.enhanced, self.remove, self.keep, self.quality):
            signal = control.toggled if hasattr(control, "toggled") else control.currentTextChanged
            signal.connect(self.refresh_preflight)
        self.refresh_paths(); self.refresh_preflight()

    def browse(self, attr: str):
        value = QFileDialog.getExistingDirectory(self, "Choose folder", str(getattr(self, attr)))
        if value: setattr(self, attr, Path(value)); self.settings.setValue(f"folders/{attr}", value); self.refresh_paths(); self.refresh_preflight()

    def open_folder(self, attr: str):
        path = Path(getattr(self, attr))
        if path.is_dir(): subprocess.Popen(["open", str(path)])

    def refresh_paths(self):
        for attr, label in zip(("interior", "exterior", "output"), self.paths):
            path = Path(getattr(self, attr)); selected = path.is_dir() and path != core.USER_DATA_DIR / attr.title()
            label.setText(str(path) if selected else "Not selected"); label.setToolTip(str(path))
            self.folder_counts[attr].setText(f"{len(supported_images(path))} images" if path.is_dir() and attr != "output" else "")

    def refresh_twilight(self):
        ok, heroes, message = twilight_preflight(self.exterior) if self.exterior.is_dir() else (True, [], "")
        self.hero.setText("🔴 " + heroes[0].name if len(heroes) == 1 else "🔴 No red-tagged exterior detected" if not heroes else "⚠ Multiple red-tagged exterior images detected")
        if message: self.status.setText(message); self.hero_note.setText(message)

    def refresh_preflight(self, *_):
        self.refresh_twilight(); interior = len(supported_images(self.interior)) if self.interior.is_dir() else 0; exterior = len(supported_images(self.exterior)) if self.exterior.is_dir() else 0
        ok, heroes, message = twilight_preflight(self.exterior) if self.exterior.is_dir() else (True, [], "")
        generations = interior + exterior + len(heroes); cost = generations * core.estimated_cost_per_image(self.quality.currentText().lower())
        self.preflight.setText(f"Interior {interior}    Exterior {exterior}    Twilight {len(heroes)}\nAPI Generations {generations}    Estimated Cost ${cost:.2f}")
        ready = bool(interior or exterior) and self.output.is_dir() and ok
        self.start.setEnabled(ready); self.ready.setText(f"Ready to process {generations} generations" if ready else message or "Select an Interior or Exterior folder and an Output folder")

    def validate_start(self):
        try:
            jobs, heroes = build_jobs(self.interior, self.exterior)
            self.status.setText(f"Ready — {len(jobs)} generations ({len(heroes)} Twilight hero)")
        except Exception as exc: self.status.setText(str(exc))

    def start_processing(self):
        """Use V7's canonical API-key loader; preflight blocks multiple heroes before any call."""
        try:
            jobs, _heroes = build_jobs(self.interior, self.exterior)
            api_key, message = core.load_project_api_key()
            if not api_key:
                self.status.setText(message); return
            self.progress.setRange(0, len(jobs)); self.start.setEnabled(False)
            for control in (self.natural, self.enhanced, self.remove, self.keep, self.quality): control.setEnabled(False)
            def event(kind, payload):
                if kind == "started":
                    self.status.setText(f"Processing: {payload['kind'].upper()} — {payload['filename']}")
                    self.add_activity(payload['kind'], payload['filename'], "Processing"); self.progress.setValue(payload["index"] - 1)
                elif kind in {"finished", "failed"}:
                    self.add_activity("", payload['filename'], payload['status']); self.progress.setValue(self.progress.value() + 1)
                QApplication.processEvents()
            summary = process_v8_batch(core.OpenAI(api_key=api_key), interior_folder=self.interior,
                exterior_folder=self.exterior, output_folder=self.output,
                quality=self.quality.currentText().lower(), landscape="Enhanced" if self.enhanced.isChecked() else "Natural",
                distractions="Keep" if self.keep.isChecked() else "Remove", event=event)
            self.status.setText(f"Completed: {summary.completed} | Review: {summary.review} | Error: {summary.errors}")
            self.counts.setText(f"Completed {summary.completed}    Review {summary.review}    Errors {summary.errors}")
            QMessageBox.information(self, "PROCESSING COMPLETE", f"Interior processed: {summary.interior}\nExterior processed: {summary.exterior}\nTwilight generated: {summary.twilight}\n\nCompleted: {summary.completed}\nReview: {summary.review}\nErrors: {summary.errors}")
        except Exception as exc:
            self.status.setText(f"ERROR: {exc}")
        finally:
            for control in (self.natural, self.enhanced, self.remove, self.keep, self.quality): control.setEnabled(True)
            self.refresh_preflight()

    def add_activity(self, kind: str, filename: str, status: str):
        row = self.activity.rowCount(); self.activity.insertRow(row)
        for column, value in enumerate((kind.upper(), filename, status.title())): self.activity.setItem(row, column, QTableWidgetItem(value))


def main() -> None:
    app = QApplication.instance() or QApplication([]); window = V8Window(); window.show(); app.exec()


if __name__ == "__main__": main()
