"""V8.0 dual-folder MLS editor; V7 remains an untouched production baseline."""
from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import tempfile
import time
import base64
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Callable, Iterable

# Ensure V8 has independent Application Support data before importing the V7 engine.
os.environ.setdefault("MYESTATEPICS_APPLICATION_NAME", "MyEstatePics AI Editor - V8.0")
import v7_editor as core
from PIL import Image, ImageCms, ImageOps
import pillow_heif
pillow_heif.register_heif_opener()

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
SUPPORTED_EXTENSIONS = core.SUPPORTED_EXTENSIONS | {".heic", ".heif"}


@dataclass(frozen=True)
class V8Job:
    source: Path
    kind: str  # interior, exterior, twilight

    @property
    def output_name(self) -> str:
        stem = f"{self.source.stem}-TWILIGHT" if self.kind == "twilight" else self.source.stem
        return f"{stem}.JPG" if self.source.suffix.lower() in {".heic", ".heif"} else f"{stem}{self.source.suffix}"


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


def supported_images(folder: Path | None) -> list[Path]:
    if folder is None:
        return []
    folder = Path(folder)
    if not folder.is_dir():
        return []
    return sorted((p.resolve() for p in folder.iterdir()
                   if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS),
                  key=lambda p: p.name.casefold())


def finder_tags(path: Path) -> tuple[str, ...]:
    """Read Finder tags locally; unavailable metadata simply means no tag."""
    try:
        # The frozen macOS Python runtime does not expose os.getxattr, while
        # /usr/bin/xattr is present on supported macOS installations.
        result = subprocess.run(["xattr", "-px", "com.apple.metadata:_kMDItemUserTags", str(path)],
                                capture_output=True, text=True, check=True)
        raw = bytes.fromhex("".join(result.stdout.split()))
        values = plistlib.loads(raw)
        return tuple(str(value).split("\n", 1)[0] for value in values)
    except (OSError, ValueError, subprocess.CalledProcessError, plistlib.InvalidFileException):
        return ()


def red_tagged_images(exterior_folder: Path | None) -> list[Path]:
    return [p for p in supported_images(exterior_folder)
            if any(tag.casefold() == "red" for tag in finder_tags(p))]


def twilight_preflight(exterior_folder: Path | None) -> tuple[bool, list[Path], str]:
    heroes = red_tagged_images(exterior_folder)
    if len(heroes) > 1:
        return False, heroes, "Only one Exterior image may have the RED Finder tag: " + ", ".join(p.name for p in heroes)
    return True, heroes, ""


def validate_output_folder(output_folder: Path | None) -> tuple[bool, str]:
    """Validate the explicit current-job destination without creating a fallback."""
    if output_folder is None:
        return False, "Select a valid Output folder before processing."
    folder = Path(output_folder)
    if not folder.exists() or not folder.is_dir() or not os.access(folder, os.W_OK | os.X_OK):
        return False, "Select a valid Output folder before processing."
    return True, ""


def load_v8_api_key() -> tuple[str | None, str]:
    """Load V8 credentials first, with a read-only V7 compatibility fallback."""
    api_key, message = core.load_project_api_key()
    if api_key:
        return api_key, message
    legacy_env = Path.home() / "Library" / "Application Support" / "MyEstatePics AI Editor - V7.0" / ".env"
    try:
        candidate = str(core.dotenv_values(legacy_env).get("OPENAI_API_KEY", "") or "").strip()
        valid, _legacy_message = core.validate_api_key(candidate)
    except Exception:
        valid, candidate = False, ""
    return (candidate, "OpenAI API key loaded") if valid else (None, message)


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


def prepare_v8_api_upload(input_file: Path) -> Path:
    """Create a temporary RGB/sRGB JPEG for JPEG, HEIC, and HEIF inputs."""
    fd, temporary_name = tempfile.mkstemp(suffix=".jpg", prefix="myestatepics_v8_upload_")
    os.close(fd); upload = Path(temporary_name)
    try:
        with Image.open(input_file) as source:
            image = ImageOps.exif_transpose(source)
            profile_bytes = image.info.get("icc_profile"); srgb_bytes = None
            if profile_bytes:
                try:
                    source_profile = ImageCms.ImageCmsProfile(BytesIO(profile_bytes)); srgb = ImageCms.createProfile("sRGB")
                    image = ImageCms.profileToProfile(image, source_profile, srgb, outputMode="RGB")
                    srgb_bytes = ImageCms.ImageCmsProfile(srgb).tobytes()
                except Exception:
                    image = image.convert("RGB")
            elif image.mode == "RGBA":
                background = Image.new("RGB", image.size, "white"); background.paste(image, mask=image.getchannel("A")); image = background
            else:
                image = image.convert("RGB")
            image.save(upload, "JPEG", quality=95, subsampling=0, icc_profile=srgb_bytes)
        return upload
    except Exception:
        upload.unlink(missing_ok=True); raise


def call_v8_image_editor(client, input_file: Path, prompt: str, quality: str):
    """Exactly one existing direct Images Edit call, using V8's HEIC-safe upload."""
    upload = prepare_v8_api_upload(input_file)
    try:
        with upload.open("rb") as image_file:
            response = client.images.edit(model=MODEL, image=image_file, prompt=prompt,
                size=core.choose_native_size(input_file), quality=quality, output_format=core.API_OUTPUT_FORMAT)
    finally:
        upload.unlink(missing_ok=True)
    return base64.b64decode(response.data[0].b64_json), core.choose_native_size(input_file), core.extract_usage(response)


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


def build_jobs(interior_folder: Path | None, exterior_folder: Path | None, selected: set[Path] | None = None) -> tuple[list[V8Job], list[Path]]:
    selected = {p.resolve() for p in selected} if selected is not None else None
    interiors = [V8Job(p, "interior") for p in supported_images(interior_folder) if selected is None or p in selected]
    exteriors = [V8Job(p, "exterior") for p in supported_images(exterior_folder) if selected is None or p in selected]
    heroes = [p for p in red_tagged_images(exterior_folder) if selected is None or p in selected]
    ok = len(heroes) <= 1; message = "Only one selected Exterior image may have the RED Finder tag: " + ", ".join(p.name for p in heroes) if not ok else ""
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


def process_v8_batch(client, *, interior_folder: Path | None, exterior_folder: Path | None, output_folder: Path | None, selected_files: set[Path] | None = None,
                     quality: str = "medium", landscape: str = "Natural", distractions: str = "Remove",
                     event: Callable[[str, dict], None] = lambda _k, _p: None,
                     cancel_requested: Callable[[], bool] = lambda: False) -> V8Summary:
    """One direct Images Edit call for each normal job, plus one twilight hero call."""
    if quality not in QUALITY_OPTIONS:
        raise ValueError("V8 quality must be medium or high")
    jobs, _heroes = build_jobs(interior_folder, exterior_folder, selected_files)
    if not jobs:
        raise ValueError("Select at least one image before processing.")
    valid_output, output_message = validate_output_folder(output_folder)
    if not valid_output:
        raise ValueError(output_message)
    output_folder = Path(output_folder).resolve()
    cache = ComparisonCache(); summary = V8Summary(cache_root=cache.root)
    for job in jobs:
        cache.snapshot_before(job)
    try:
        for index, job in enumerate(jobs, 1):
            if cancel_requested():
                event("cancelled", {"index": index, "total": len(jobs)})
                break
            # Recheck at the last safe point before every paid Images Edit call.
            valid_output, output_message = validate_output_folder(output_folder)
            if not valid_output:
                raise ValueError(output_message)
            event("started", {"index": index, "total": len(jobs), "filename": job.output_name, "kind": job.kind})
            try:
                destination = output_folder / job.output_name
                if destination.exists():
                    raise FileExistsError(f"Refusing to overwrite existing output: {destination.name}")
                generated, _size, _usage = call_v8_image_editor(client, job.source, prompt_for(job, landscape, distractions), quality)
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
        super().__init__(); self.setWindowTitle("MyEstatePics AI Editor — V8.0"); self.resize(1180, 820)
        self.settings = QSettings(str(core.USER_DATA_DIR / "v8_preferences.ini"), QSettings.IniFormat)
        self.interior = self._restored_input("interior")
        self.exterior = self._restored_input("exterior")
        # Output is deliberately session-only.  Never restore a previous job's destination.
        self.output: Path | None = None
        self.discovered: list[tuple[str, Path]] = []; self.selected_files: set[Path] = set(); self.updating_selection = False; self.cancel_requested = False
        self._build(); self.refresh_twilight()

    def _restored_input(self, name: str) -> Path | None:
        value = str(self.settings.value(f"folders/{name}", "") or "").strip()
        candidate = Path(value) if value else None
        return candidate if candidate and candidate.is_dir() else None

    def _build(self):
        self.setWindowTitle("MyEstatePics AI Editor — V8.0"); self.resize(1180, 820); self.setMinimumSize(1050, 720)
        root = QWidget(); layout = QVBoxLayout(root); layout.setContentsMargins(22, 18, 22, 18); layout.setSpacing(10)
        header = QHBoxLayout(); title = QLabel("MyEstatePics AI Editor V8.0"); title.setStyleSheet("font-size: 21px; font-weight: 700;")
        subtitle = QLabel("MLS Production Editor"); api_key, _message = load_v8_api_key(); model = QLabel(f"Sunburst\nAPI: {'Ready' if api_key else 'Not configured'}")
        header.addWidget(title); header.addWidget(subtitle, 1); header.addWidget(model); layout.addLayout(header)
        setup = QGroupBox("JOB SETUP"); form = QGridLayout(setup); form.setColumnStretch(1, 1)
        self.paths = []
        self.folder_counts = {}
        for row, (label, attr) in enumerate((("Interior", "interior"), ("Exterior", "exterior"), ("Output", "output"))):
            value = QLabel(); value.setTextInteractionFlags(Qt.TextSelectableByMouse); value.setToolTip(str(getattr(self, attr) or "")); value.setMinimumWidth(450)
            value.setStyleSheet("padding: 5px; border: 1px solid palette(mid); border-radius: 4px;")
            choose = QPushButton("Choose"); choose.setFixedWidth(76); open_button = QPushButton("Open"); open_button.setFixedWidth(76)
            choose.clicked.connect(lambda _=False, name=attr: self.browse(name)); open_button.clicked.connect(lambda _=False, name=attr: self.open_folder(name))
            count = QLabel(""); self.folder_counts[attr] = count
            form.addWidget(QLabel(label), row * 2, 0); form.addWidget(value, row * 2, 1); form.addWidget(choose, row * 2, 2); form.addWidget(open_button, row * 2, 3); form.addWidget(count, row * 2 + 1, 1, 1, 3); self.paths.append(value)
            if attr == "output":
                self.clear_output_button = QPushButton("Clear"); self.clear_output_button.setFixedWidth(76); self.clear_output_button.clicked.connect(self.clear_output)
                form.addWidget(self.clear_output_button, row * 2, 4)
        self.rescan_button = QPushButton("Rescan"); self.rescan_button.clicked.connect(self.rescan); form.addWidget(self.rescan_button, 6, 2, 1, 2)
        layout.addWidget(setup)
        workspace = QHBoxLayout(); image_card = QGroupBox("IMAGES"); image_layout = QVBoxLayout(image_card); image_actions = QHBoxLayout()
        self.images_title = QLabel("IMAGES — 0 selected"); self.images_title.setStyleSheet("font-weight: 700;")
        self.select_all_button = QPushButton("Select All"); self.clear_all_button = QPushButton("Clear All")
        self.select_all_button.clicked.connect(self.select_all); self.clear_all_button.clicked.connect(self.clear_all)
        image_actions.addWidget(self.images_title); image_actions.addStretch(1); image_actions.addWidget(self.select_all_button); image_actions.addWidget(self.clear_all_button); image_layout.addLayout(image_actions)
        self.images = QTableWidget(0, 3); self.images.setHorizontalHeaderLabels(["TYPE", "FILE", "SELECTED / STATUS"]); self.images.horizontalHeader().setStretchLastSection(True); self.images.setMinimumHeight(300); self.images.itemChanged.connect(self.selection_changed); image_layout.addWidget(self.images)
        options = QGroupBox("EXTERIOR OPTIONS"); opt = QGridLayout(options); opt.setColumnStretch(1, 1)
        self.natural = QRadioButton("Natural"); self.enhanced = QRadioButton("Enhanced"); self.natural.setChecked(True)
        self.remove = QRadioButton("Remove"); self.keep = QRadioButton("Keep"); self.remove.setChecked(True)
        landscape = QHBoxLayout(); landscape.addWidget(self.natural); landscape.addWidget(self.enhanced); landscape.addStretch(1)
        distractions = QHBoxLayout(); distractions.addWidget(self.remove); distractions.addWidget(self.keep); distractions.addStretch(1)
        opt.addWidget(QLabel("Landscape"), 0, 0); opt.addLayout(landscape, 0, 1); opt.addWidget(QLabel("Preserve existing lawn and landscaping"), 1, 1)
        opt.addWidget(QLabel("Distractions"), 2, 0); opt.addLayout(distractions, 2, 1); opt.addWidget(QLabel("Remove temporary distractions"), 3, 1)
        self.hero = QLabel(); self.hero_note = QLabel("Red Finder tag creates one additional early-twilight image."); self.quality = QComboBox(); self.quality.addItems(["MEDIUM", "HIGH"]); self.quality.setFixedWidth(130)
        opt.addWidget(QLabel("Twilight Hero"), 4, 0); opt.addWidget(self.hero, 4, 1); opt.addWidget(self.hero_note, 5, 1); opt.addWidget(QLabel("Quality"), 6, 0); opt.addWidget(self.quality, 6, 1)
        workspace.addWidget(image_card, 11); workspace.addWidget(options, 9); layout.addLayout(workspace, 1)
        preflight = QGroupBox("PRE-FLIGHT"); pre = QGridLayout(preflight); self.preflight = QLabel(); self.ready = QLabel(); self.start = QPushButton("START PROCESSING"); self.start.setFixedWidth(210); self.start.clicked.connect(self.start_processing)
        pre.addWidget(self.preflight, 0, 0); pre.addWidget(self.ready, 1, 0); pre.addWidget(self.start, 0, 1, 2, 1); layout.addWidget(preflight)
        processing = QGroupBox("PROCESSING"); process = QVBoxLayout(processing); self.progress = QProgressBar(); self.progress.setTextVisible(True); self.status = QLabel("Ready"); self.counts = QLabel("Completed 0    Review 0    Errors 0"); self.cancel_button = QPushButton("Cancel"); self.cancel_button.setEnabled(False); self.cancel_button.clicked.connect(self.request_cancel); self.review_button = QPushButton("Review Results"); self.review_button.setEnabled(False); self.review_button.clicked.connect(self.open_review_results); self.open_output_button = QPushButton("Open Output"); self.open_output_button.clicked.connect(lambda: self.open_folder("output")); buttons = QHBoxLayout(); buttons.addWidget(self.cancel_button); buttons.addWidget(self.review_button); buttons.addWidget(self.open_output_button); buttons.addStretch(1); process.addWidget(self.progress); process.addWidget(self.status); process.addWidget(self.counts); process.addLayout(buttons); layout.addWidget(processing)
        self.details = QGroupBox("DETAILS"); self.details.setCheckable(True); self.details.setChecked(False); al = QVBoxLayout(self.details); self.activity = QTableWidget(0, 3); self.activity.setHorizontalHeaderLabels(["TYPE", "FILE", "STATUS"]); self.activity.horizontalHeader().setStretchLastSection(True); self.activity.setMaximumHeight(170); al.addWidget(self.activity); self.activity.setVisible(False); self.details.toggled.connect(self.activity.setVisible); layout.addWidget(self.details); self.setCentralWidget(root)
        for control in (self.natural, self.enhanced, self.remove, self.keep, self.quality):
            signal = control.toggled if hasattr(control, "toggled") else control.currentTextChanged
            signal.connect(self.refresh_preflight)
        self.refresh_paths(); self.rescan()

    def browse(self, attr: str):
        current = getattr(self, attr)
        value = QFileDialog.getExistingDirectory(self, "Choose folder", str(current or Path.home()))
        if value:
            setattr(self, attr, Path(value))
            if attr != "output":
                self.settings.setValue(f"folders/{attr}", value)
            self.refresh_paths(); self.rescan()

    def clear_output(self):
        self.output = None
        self.refresh_paths(); self.refresh_preflight()

    def open_folder(self, attr: str):
        path = getattr(self, attr)
        if path and Path(path).is_dir(): subprocess.Popen(["open", str(path)])

    def refresh_paths(self):
        for attr, label in zip(("interior", "exterior", "output"), self.paths):
            path = getattr(self, attr)
            selected = path is not None and Path(path).is_dir()
            label.setText(str(path) if selected else "Not selected"); label.setToolTip(str(path or ""))
            self.folder_counts[attr].setText(f"{len(supported_images(Path(path)))} images" if selected and attr != "output" else "")

    def rescan(self, preserve_empty: bool = False):
        previous = set(self.selected_files); self.discovered = [("INTERIOR", p) for p in supported_images(self.interior)] + [("EXTERIOR", p) for p in supported_images(self.exterior)]
        available = {p for _, p in self.discovered}; self.selected_files = (previous & available) if previous or preserve_empty else set(available)
        self.updating_selection = True; self.images.setRowCount(0)
        for kind, path in self.discovered:
            row = self.images.rowCount(); self.images.insertRow(row); self.images.setItem(row, 0, QTableWidgetItem(kind)); self.images.setItem(row, 1, QTableWidgetItem(path.name))
            checked = QTableWidgetItem("Selected" if path in self.selected_files else "Not selected"); checked.setData(Qt.UserRole, str(path)); checked.setFlags(checked.flags() | Qt.ItemIsUserCheckable); checked.setCheckState(Qt.Checked if path in self.selected_files else Qt.Unchecked); self.images.setItem(row, 2, checked)
        self.updating_selection = False; self.refresh_paths(); self.refresh_preflight()

    def select_all(self): self.selected_files = {p for _, p in self.discovered}; self.rescan()
    def clear_all(self): self.selected_files.clear(); self.rescan(preserve_empty=True)
    def selection_changed(self, item):
        if self.updating_selection or item.column() != 2: return
        path = Path(item.data(Qt.UserRole));
        if item.checkState() == Qt.Checked: self.selected_files.add(path)
        else: self.selected_files.discard(path)
        self.refresh_preflight()

    def refresh_twilight(self):
        heroes = [p for p in red_tagged_images(self.exterior) if p in self.selected_files] if self.exterior else []
        message = "Only one selected Exterior image may have the RED Finder tag: " + ", ".join(p.name for p in heroes) if len(heroes) > 1 else ""
        self.hero.setText("🔴 " + heroes[0].name if len(heroes) == 1 else "🔴 No red-tagged exterior detected" if not heroes else "⚠ Multiple red-tagged exterior images detected")
        if message: self.status.setText(message); self.hero_note.setText(message)

    def refresh_preflight(self, *_):
        self.refresh_twilight(); interior = sum(1 for kind, p in self.discovered if kind == "INTERIOR" and p in self.selected_files); exterior = sum(1 for kind, p in self.discovered if kind == "EXTERIOR" and p in self.selected_files)
        heroes = [p for p in red_tagged_images(self.exterior) if p in self.selected_files] if self.exterior else []
        ok = len(heroes) <= 1; message = "Resolve multiple Twilight tags before processing" if not ok else ""
        generations = interior + exterior + len(heroes); cost = generations * core.estimated_cost_per_image(self.quality.currentText().lower())
        self.images_title.setText(f"IMAGES — {len(self.selected_files)} selected")
        output_ok, output_message = validate_output_folder(self.output)
        api_key, api_message = load_v8_api_key()
        output_display = str(self.output) if self.output else "NOT SELECTED"
        self.preflight.setText(f"Interior {interior}    Exterior {exterior}    Twilight {len(heroes)}\nAPI Generations {generations}    Estimated Cost ${cost:.2f}\nOutput: {output_display}")
        ready = bool(interior or exterior) and output_ok and bool(api_key) and ok
        self.start.setEnabled(ready)
        self.ready.setText(f"Ready to process {generations} generations" if ready else message or output_message if not output_ok else api_message if not api_key else "Select at least one image")

    def validate_start(self):
        try:
            jobs, heroes = build_jobs(self.interior, self.exterior)
            self.status.setText(f"Ready — {len(jobs)} generations ({len(heroes)} Twilight hero)")
        except Exception as exc: self.status.setText(str(exc))

    def start_processing(self):
        """Use V7's canonical API-key loader; preflight blocks multiple heroes before any call."""
        try:
            jobs, _heroes = build_jobs(self.interior, self.exterior, self.selected_files)
            if not jobs:
                self.status.setText("Select at least one image before processing."); return
            valid_output, output_message = validate_output_folder(self.output)
            if not valid_output:
                self.status.setText(output_message); return
            api_key, message = load_v8_api_key()
            if not api_key:
                self.status.setText(message); return
            self.progress.setRange(0, len(jobs)); self.start.setEnabled(False); self.cancel_requested = False; self.cancel_button.setEnabled(True)
            for control in (self.natural, self.enhanced, self.remove, self.keep, self.quality): control.setEnabled(False)
            def event(kind, payload):
                if kind == "started":
                    self.status.setText(f"Processing: {payload['kind'].upper()} — {payload['filename']}")
                    self.add_activity(payload['kind'], payload['filename'], "Processing"); self.progress.setValue(payload["index"] - 1)
                elif kind in {"finished", "failed"}:
                    self.add_activity("", payload['filename'], payload['status']); self.progress.setValue(self.progress.value() + 1)
                QApplication.processEvents()
            summary = process_v8_batch(core.OpenAI(api_key=api_key), interior_folder=self.interior,
                exterior_folder=self.exterior, output_folder=self.output, selected_files=self.selected_files,
                quality=self.quality.currentText().lower(), landscape="Enhanced" if self.enhanced.isChecked() else "Natural",
                distractions="Keep" if self.keep.isChecked() else "Remove", event=event,
                cancel_requested=lambda: self.cancel_requested)
            self.status.setText(f"Completed: {summary.completed} | Review: {summary.review} | Error: {summary.errors}")
            self.counts.setText(f"Completed {summary.completed}    Review {summary.review}    Errors {summary.errors}")
            self.review_button.setEnabled(bool(summary.before_pdf and summary.after_pdf))
            QMessageBox.information(self, "PROCESSING COMPLETE", f"Interior processed: {summary.interior}\nExterior processed: {summary.exterior}\nTwilight generated: {summary.twilight}\n\nCompleted: {summary.completed}\nReview: {summary.review}\nErrors: {summary.errors}")
        except Exception as exc:
            self.status.setText(f"ERROR: {exc}")
        finally:
            self.cancel_button.setEnabled(False)
            for control in (self.natural, self.enhanced, self.remove, self.keep, self.quality): control.setEnabled(True)
            self.refresh_preflight()

    def request_cancel(self):
        self.cancel_requested = True
        self.cancel_button.setEnabled(False)
        self.status.setText("Cancellation requested — the current image will finish, then remaining images will be skipped")

    def open_review_results(self):
        review_root = self.output / "Batch Reviews"
        reports = sorted(review_root.glob("V8-*")) if review_root.is_dir() else []
        if self.output.is_dir():
            subprocess.Popen(["open", str(reports[-1] if reports else self.output)])

    def add_activity(self, kind: str, filename: str, status: str):
        row = self.activity.rowCount(); self.activity.insertRow(row)
        for column, value in enumerate((kind.upper(), filename, status.title())): self.activity.setItem(row, column, QTableWidgetItem(value))


def main() -> None:
    app = QApplication.instance() or QApplication([]); window = V8Window(); window.show(); app.exec()


if __name__ == "__main__": main()
