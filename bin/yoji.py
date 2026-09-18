import sys
import json
import os
from pathlib import Path
import zipfile
import tempfile

from pet import Pet
from engine.particles.atlas_generator import AtlasGenerator
from app.registrator import register_yoji_file_type
from engine.windows_detector import schedule_update as windows_detector_schedule_update

from engine.logger import app_logger as log
from engine.logger import debug_logger

from PySide6.QtCore import Qt, QTimer, QEvent, QSize
from PySide6.QtGui import QIcon, QPixmap, QFontDatabase, QFont

from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QMessageBox,
    QPlainTextEdit,
    QFileDialog,
    QCheckBox,
    QGridLayout,
    QSizePolicy,
)

class App(QApplication):
    def __init__(self, args):
        super().__init__(args)
        self.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.ChildAdded:
            child = event.child()

            if isinstance(child, QPushButton):
                child.setCursor(Qt.CursorShape.PointingHandCursor)

        return super().eventFilter(obj, event)

root = Path(__file__).resolve().parents[1]

icon_path = root / "resources" / "icons" / "icon.ico"
logo_path = root / "resources" / "icons" / "icon.png"
font_path = root / "resources" / "fonts"
qss_path = root / "resources" / "styles" / "main_widget.qss"
settings_path = root / "settings.json"

LOG_FOLDER = os.path.join(os.environ["LOCALAPPDATA"], "Yoji", "logs")

app_path = Path(__file__).resolve()

class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        log.info("---APP LAUNCHED---")
        self.pet_active = False
        self.files = []
        self.manifest = None
        self.call_in_progress = False
        self.temp_archive_path = None

        self.setWindowTitle("Yoji")
        self.resize(500, 400)

        # opening the app when opening a .yoji file 
        if len(sys.argv) > 1:
            try:
                input_file_path = str(Path(sys.argv[1]))
                self.open_path(input_file_path)
                log.info(f"Opening application with arguments.")
            except Exception as e:
                log.error(f"Could not open a .yoji file.\n{e}")
                self._show_warning_message("Could not open a .yoji file", f"{e}")

        self.settings: dict = {}
        self._load_settings()

        self.register_yoji_format()

        self.load_fonts()

        # --- widgets ---
        self.label = QLabel("Open your yoji file:")

        self.path_edit = QLineEdit()
        placeholder = "Paste file path here!" if not self.settings["last_path"] else self.settings["last_path"]
        self.path_edit.setPlaceholderText(placeholder)

        self.browse_button = QPushButton("Browse:")
        self.browse_button.clicked.connect(self.browse_file)

        self.browse_widget = QWidget()
        self.browse_layout = QHBoxLayout(self.browse_widget)
        self.browse_layout.addWidget(self.path_edit)
        self.browse_layout.addWidget(self.browse_button)

        # info box
        self.make_info_widget()

        self.set_qss_object_names()

        # --- stylesheets ---
        with open(qss_path, "r", encoding="utf-8") as f:
            self.setStyleSheet(f.read())

        # it doesnt work when applied through qss for some reason
        self.info_thumbnail.setStyleSheet("""
                QLabel {
                    max-width: 350px;
                    max-height: 350px;
                }
            """)
        
        self.info_thumbnail.setSizePolicy(
            QSizePolicy.Policy.Maximum,
            QSizePolicy.Policy.Maximum
        )

        # --- main layout  ---

        layout = QVBoxLayout(self)
        layout.addWidget(self.label, alignment=Qt.AlignmentFlag.AlignTop|Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.browse_widget, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.info_widget, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addStretch()

        if self.settings["last_path"]:
            try:
                log.info(f"Last used path was {self.settings["last_path"]}")
                path = self.settings["last_path"]
                self.open_path(path)

            except Exception:
                log.warning(f"Could not load last path: {self.settings["last_path"]}")

        log.info("---APP LOADED SUCCESSFULLY---\n")
        # self.hotkeys = HotkeyManager(self) # not doing anything for now, meh

    def register_yoji_format(self):
        """Registering the .yoji file format"""
        try:
            register_yoji_file_type(exe_path=app_path, icon_path=icon_path)
            log.info(f"Registered .yoji file extention: success.")
        except Exception as e:
            log.warning(f"Could not register .yoji file extension.\n{e}")

    def load_fonts(self):
        try:
            QFontDatabase.addApplicationFont(str(font_path / "Rubik-Regular.ttf"))
            QFontDatabase.addApplicationFont(str(font_path / "Rubik-Italic.ttf"))
            QFontDatabase.addApplicationFont(str(font_path / "Rubik-Bold.ttf"))
            QFontDatabase.addApplicationFont(str(font_path / "Rubik-BoldItalic.ttf"))

            font = QFont("Rubik", 12)
            app.setFont(font)
        except Exception:
            #Debug.error(f"Could not find font 'Rubik' in {font_path}")
            font = QFont("Arial", 12)
            app.setFont(font)

    def make_info_widget(self):
        self.info_widget = QWidget()
        self.info_layout = QHBoxLayout(self.info_widget)

        self.info_thumbnail = QLabel()
        self.info_thumbnail.setPixmap(QPixmap(str(logo_path)))
        self.info_layout.addWidget(self.info_thumbnail, alignment=Qt.AlignmentFlag.AlignTop)

        self.info_layout.addSpacing(10)

        vertical_layout = QVBoxLayout()

        self.info_name = QLabel("Name")
        self.info_name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        vertical_layout.addWidget(self.info_name)

        self.info_author = QLabel("author")
        self.info_author.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        vertical_layout.addWidget(self.info_author)

        self.info_description = QLabel("description")
        self.info_description.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.info_description.setWordWrap(True)
        vertical_layout.addWidget(self.info_description)

        self.info_tags = QLabel("#yoji #pet #cute")
        self.info_tags.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        vertical_layout.addWidget(self.info_tags)

        self.info_widget.hide()

        self.call_box = QWidget()
        call_box_layout = QVBoxLayout(self.call_box)

        call_box_layout.addStretch()

        self.view_logs_button = QPushButton("view log folder")
        self.view_logs_button.clicked.connect(self._open_logs_folder)
        call_box_layout.addWidget(self.view_logs_button, alignment=Qt.AlignmentFlag.AlignRight)
        self.view_logs_button.setVisible(False)

        self.debug_checkbox = QCheckBox("debug mode")
        if self.settings["debug_mode"]:
            self.debug_checkbox.setChecked(True)
            self.view_logs_button.setVisible(True)
        self.debug_checkbox.toggled.connect(self.view_logs_button.setVisible)
        call_box_layout.addWidget(self.debug_checkbox, alignment=Qt.AlignmentFlag.AlignRight)

        self.call_button = QPushButton("Call")
        self.call_button.clicked.connect(self.call_button_clicked)
        call_box_layout.addWidget(self.call_button, alignment=Qt.AlignmentFlag.AlignRight)

        vertical_layout.addStretch()
        vertical_layout.addWidget(self.call_box, alignment=Qt.AlignmentFlag.AlignBaseline)
        self.info_layout.addLayout(vertical_layout)
    
    def _show_warning_message(self, title, message):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning) #type: ignore
        box.setWindowTitle(title)
        box.setText(message)
        box.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        box.exec()
    
    def _open_logs_folder(self):
        os.startfile(LOG_FOLDER)

    #set object names for easy acess in qss
    def set_qss_object_names(self):
        self.info_widget.setObjectName("info_widget")
        self.info_thumbnail.setObjectName("info_thumbnail")
        self.info_name.setObjectName("info_name")
        self.info_author.setObjectName("info_author")
        self.info_description.setObjectName("info_description")
        self.info_tags.setObjectName("info_tags")
        self.browse_button.setObjectName("browse_button")
        self.view_logs_button.setObjectName("view_logs_button")
        self.debug_checkbox.setObjectName("debug_checkbox")
        self.call_button.setObjectName("call_button")
        self.call_box.setObjectName("call_box")


    # region Qt events for triggering windows_detector
    def moveEvent(self, event):
        super().moveEvent(event)
        if self.pet_active:
            windows_detector_schedule_update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.pet_active:
            windows_detector_schedule_update()

    def changeEvent(self, event):
        super().changeEvent(event)
        if self.pet_active and event.type() == QEvent.ActivationChange: #type: ignore
            windows_detector_schedule_update(update_window_list=True)
    #endregion


    def browse_file(self):
        if self.pet_active: return
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Open your Yoji")
        dialog.setText("What would you like to open?")

        icon_size = 20

        archive_button = dialog.addButton(
            "File",
            QMessageBox.ButtonRole.AcceptRole)
        file_icon_path = root / "resources" / "icons" / "icon.png"
        archive_button.setIcon(QIcon(QPixmap(str(file_icon_path))))
        archive_button.setIconSize(QSize(icon_size, icon_size))
        archive_button.setObjectName("browse-file-btn")
        archive_button.setProperty("class", "browse_btn")

        directory_button = dialog.addButton(
            "Folder",
            QMessageBox.ButtonRole.AcceptRole)
        folder_icon_path = root / "resources" / "icons" / "folder.png"
        directory_button.setIcon(QIcon(QPixmap(str(folder_icon_path))))
        archive_button.setIconSize(QSize(icon_size, icon_size))
        directory_button.setObjectName("browse-directory-btn")
        directory_button.setProperty("class", "browse_btn")

        cancel_button = dialog.addButton(
            "Cancel",
            QMessageBox.ButtonRole.RejectRole)
        cancel_button.setObjectName("browse-cancel-btn")
        cancel_button.setProperty("class", "browse_btn")


        dialog.setStyleSheet(self.styleSheet())
        dialog.exec()

        library_dir = root / "library"

        if not library_dir.is_dir():
            library_dir = Path()

        if dialog.clickedButton() == archive_button:
            file_name, _ = QFileDialog.getOpenFileName(
                self,
                "Open your Yoji archive",
                str(library_dir),
                "Yoji/ZIP Files (*.yoji *.zip);;All Files (*)"
            )

            if file_name:
                self.path_edit.setText(file_name)
                print("File selected")
                self.open_path(file_name)

        elif dialog.clickedButton() == directory_button:
            directory = QFileDialog.getExistingDirectory(
                self,
                "Open your Yoji directory",
                str(library_dir)
            )

            if directory:
                self.path_edit.setText(directory)
                print("Directory selected")
                self.open_path(directory)

    def open_path(self, path):
        self._cleanup_temp_archive()
        self.directory_path = None

        path_isok = False
        if Path(path).is_file():
            path_isok = self.load_archive(path)
        elif Path(path).is_dir():
            path_isok = self.load_directory(path)

        if path_isok:
            self.settings["last_path"] = path
            self._save_settings()
            log.debug(f"Last path saved as {path}")
            print("last path saved as", path)


    def load_archive(self, path):
        if not path:
            log.warning(f"Missing Path - archive file path was not valid")
            self._show_warning_message("Missing Path", "Please enter a valid archive file path.")
            return
        
        log.info(f"Opening file {path}")
        
        try:
            with zipfile.ZipFile(path, "r") as archive:
                self.archive_path = path

                manifest_isok = self.read_manifest(archive)

                return manifest_isok

        except Exception as e:
            log.error(f"Could not open {path} as archive.\n{e}")
            self._show_warning_message("Cannot open file", f"Cannot open {path} as archive.\n{e}")


    def load_directory(self, path):
        if not path:
            log.warning("Missing Path - directory path was not valid")
            self._show_warning_message(
                "Missing Path",
                "Please enter a valid directory path.")
            return
        log.info(f"Opening directory {path}")

        directory = Path(path)
        if not directory.is_dir():
            log.error(f"Could not open {path} as directory.")
            self._show_warning_message(
                "Cannot open directory",
                f"Cannot open {path} as directory."
            )
            return

        # Create temporary archive
        temp_archive = tempfile.NamedTemporaryFile(
            suffix=".zip",
            delete=False)
        temp_archive.close()

        log.debug(f"Creating a temporary archive {temp_archive.name}")
        try:
            with zipfile.ZipFile(
                temp_archive.name,
                "w",
                zipfile.ZIP_DEFLATED
            ) as archive:
                for file_path in directory.rglob("*"):
                    if file_path.is_file():
                        archive.write(
                            file_path,
                            file_path.relative_to(directory)
                        )

            archive_isok = self.load_archive(temp_archive.name)

            self.temp_archive_path = temp_archive.name
            self.directory_path = path

            return archive_isok

        except Exception as e:
            log.exception(f"Could not create archive from directory {path}: {e}")
            self._show_warning_message(
                "Cannot open directory",
                f"Could not create temporary archive from {path}.")

    def about_to_quit(self):
        self._cleanup_temp_archive()
        self.settings["size_w"] = self.width()
        self.settings["size_h"] = self.height()
        self.settings["debug_mode"] = self.debug_checkbox.isChecked()
        self._save_settings()
        log.info(f"Application closing\n")

    def read_manifest(self, archive):
        try:
            with archive.open("manifest.json") as f:
                self.manifest = json.load(f)

            print(self.manifest)
            self.info_name.setText(str(" " + self.manifest.get("name", "Unnamed")))
            self.info_author.setText(f" v{self.manifest.get("version", 1)} by {self.manifest.get("author", "unknown")}")
            self.info_description.setText(str(self.manifest.get("description", "")))
            tag_list = self.manifest.get("tags", ["#untagged"])
            tags = " ".join(f"#{tag}" for tag in tag_list)
            self.info_tags.setText(tags)

            print("Manifest opened successfully")
            log.info(f"Manifest opened successfully")

            self.yoji_name = self.manifest.get("name", "unnamed")

            thumbnail = self.manifest.get("thumbnail", "icon.png")

            try:
                with archive.open(thumbnail) as f:
                    image_data = f.read()
                    print("loading image")
                pixmap = QPixmap()
                pixmap.loadFromData(image_data)
                self.info_thumbnail.setPixmap(
                    pixmap.scaled(
                        self.info_thumbnail.size(),
                        Qt.KeepAspectRatio, #type: ignore
                        Qt.SmoothTransformation #type: ignore
                    )
                )
            except Exception: 
                print("Thumbnail not found.")
                image_files = [
                    name for name in archive.namelist()
                    if name.lower().endswith((".png", ".webp"))
                ]
                suggested = image_files[0]
                text = f"Probably meant {suggested}" if suggested else ""
                log.warning(f"Missing thumbnail - You have specified thumbnail as {thumbnail}, but it is not present in archive.\n{text}")
                self._show_warning_message(title="Missing thumbnail", message=f"You have specified thumbnail as {thumbnail},\nbut it is not present in archive.\n{text}")

            self.info_widget.show()
            return True

        except Exception:
            self.manifest = None
            print("Manifest not found.")
            log.warning(f"Missing manifest - Selected archive is missing a yoji manifest.")
            self._show_warning_message("Missing manifest", "Selected archive is missing a yoji manifest.")


    def _load_settings(self):
        log.info("Loading settings...")

        self.settings_file = settings_path

        default_settings = {
            "last_path": "",
            "theme": "light",
            "size_w": 500,
            "size_h": 400,
            "debug_mode": False,
        }

        expected_types = {
            "last_path": str,
            "theme": str,
            "size_w": int,
            "size_h": int,
            "debug_mode": bool,
        }

        try:
            with open(self.settings_file, "r", encoding="utf-8") as f:
                self.settings = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            log.warning("settings.json file missing or invalid. Using defaults.")
            self.settings = default_settings.copy()

        # Add any settings that are missing
        for key, default_value in default_settings.items():
            self.settings.setdefault(key, default_value)

        # Using default settings if its not of expected type
        for key, expected_type in expected_types.items():
            if not isinstance(self.settings[key], expected_type):
                self.settings[key] = default_settings[key]

        # Save the repaired/default settings
        with open(self.settings_file, "w", encoding="utf-8") as f:
            json.dump(self.settings, f, indent=4)

        self.resize(self.settings.get("size_w", 500), self.settings.get("size_h", 400))

    def _save_settings(self):
        with open(self.settings_file, "w", encoding="utf-8") as f:
            json.dump(self.settings, f, indent=4)
    

    def call_button_clicked(self):
        if self.call_in_progress: return

        if self.pet_active:
            self.recall_pet()
            return
        
        self.start_call()

        if self.directory_path:
            self.open_path(self.directory_path)
        else:
            self.load_archive(self.archive_path)

        with zipfile.ZipFile(self.archive_path, "r") as archive:
            try:
                with archive.open("data/particles/assets.json") as f:
                    PARTICLE_ASSETS = json.load(f)
                if not self._check_particle_atlas(PARTICLE_ASSETS=PARTICLE_ASSETS, archive=archive):  return

                # checking audio assets
                # AUDIO_ASSETS = load something something
                # if not self.check_audio_assets(AUDIO_ASSETS): return
            
                print("--Calling pet.py")
                log.info("--Calling pet.py")

                launch_in_debug_mode = self._check_debug_mode_checkbox()

                self.pet = Pet(archive=archive, debug_mode=launch_in_debug_mode)
                self.pet.show()
                self.pet_active = True

                self._save_settings()

            except Exception as e:
                #Debug.error(f"Could not call yoji.\n{e}")
                self._show_warning_message(title="Error", message=f"Could not call yoji.\n{e}")
                try:
                    self.recall_pet()
                except Exception: pass
            finally:
                print("finally")
                archive.close()
                print("archive is", archive)
                QTimer.singleShot(500, self.finish_call)

    def _check_particle_atlas(self, PARTICLE_ASSETS, archive) -> bool:
        atlas_generator = AtlasGenerator(PARTICLE_ASSETS, archive)
        print("Checking particle atlas")
        log.info(f"Checking particle atlas")

        if not atlas_generator.atlas_exists():
            reply = QMessageBox.question(
                self,
                "Atlas missing",
                "Atlas files are missing from generated/atlas. Would you like to generate them?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)

            if reply == QMessageBox.StandardButton.Yes:
                # regenerate/rebuild here
                print("Regenerate atlas - yes")
                print("closing old archive")
                archive.close()
                print("regenerating archive")
                log.info("Regenerating atlas: start")
                atlas_generator.generate_atlas(self.archive_path)
                print("loading zip")
                log.info("--Loading ZIP archive")
                self.load_archive(self.archive_path)
                return True

            else: print("Regenerate atlas - no")
            return False
        
        print("Atlas found: success")
        log.info("Atlas found: success")
        return True

    def _check_debug_mode_checkbox(self) -> bool:
        self.debug_checkbox.setDisabled(True)
        if self.debug_checkbox.isChecked():
            log.info(f"Launching pet in debug mode")
            debug_logger.start_logging(self.yoji_name)
            return True
        else: 
            debug_logger.stop_logging()
            return False

    def start_call(self):
        self.call_in_progress = True
        self.call_button.setText("Calling...")
        self.call_button.setEnabled(False)
        QApplication.processEvents()
        print(f"--Trying to call {self.archive_path}")
        log.info(f"--Trying to call {self.archive_path}")

    def finish_call(self):
        if self.pet_active:
            self.call_button.setText("Recall")
        else:
            self.call_button.setText("Call")

        self.call_button.setEnabled(True)
        self.call_in_progress = False

    def recall_pet(self):
        print("recalling pet")
        log.info("---Recalling pet---\n")
        self.pet_active = False
        if self.pet:
            self.pet.recall()
            self.pet.close()
            self.pet.deleteLater()
        self.pet = None
        self.call_button.setEnabled(True)
        self.call_button.setText("Call")
        self.debug_checkbox.setDisabled(False)
        self._cleanup_temp_archive()

    def _cleanup_temp_archive(self):
        if self.temp_archive_path:
            try:
                Path(self.temp_archive_path).unlink(missing_ok=True)
            except Exception:
                log.warning(f"Could not delete temporary archive: {self.temp_archive_path}")
            finally:
                self.temp_archive_path = None

if __name__ == "__main__":
    app = App(sys.argv)
    app.setWindowIcon(QIcon(str(icon_path)))

    window = MainWindow()
    window.show()
    window.raise_()

    app.aboutToQuit.connect(window.about_to_quit)

    sys.exit(app.exec())
