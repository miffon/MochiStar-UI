from __future__ import annotations

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from external_tools import DependencyReport, InstallGuide, ToolStatus
from i18n import tr


class DependencyDialog(QDialog):
    """顯示缺少或過舊依賴的跨平台安裝指引"""

    ignored_requested = Signal(list)

    def __init__(
        self,
        report: DependencyReport,
        missing_ids: set[str],
        guides: dict[str, InstallGuide],
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.missing_ids = set(missing_ids)
        self.setWindowTitle(tr("External Dependencies Required"))
        self.setModal(True)
        self.setMinimumWidth(620)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        layout = QVBoxLayout(self)
        intro = QLabel(tr("The following dependencies were not detected and may affect the full experience:"))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        if "ffmpeg" in missing_ids:
            statuses = (report.ffmpeg, report.ffprobe)
            layout.addWidget(self._dependency_group(
                "FFmpeg and FFprobe", "Media conversion, file analysis, and some downloads are unavailable",
                statuses, guides["ffmpeg"],
            ))
        if "js_runtime" in missing_ids:
            layout.addWidget(self._dependency_group(
                "JavaScript runtime", "Some websites may fail JavaScript challenges. Deno is recommended",
                report.runtimes, guides["js_runtime"],
            ))

        self.ignore_checkbox = QCheckBox(tr("Do not remind me about these items again"))
        layout.addWidget(self.ignore_checkbox)
        self.copy_status_label = QLabel()
        self.copy_status_label.setWordWrap(True)
        self.copy_status_label.hide()
        layout.addWidget(self.copy_status_label)
        actions = QHBoxLayout()
        actions.addStretch()
        close_button = QPushButton(tr("Close"))
        close_button.clicked.connect(self.accept)
        actions.addWidget(close_button)
        layout.addLayout(actions)
        self.finished.connect(self._dialog_finished)

    def _dependency_group(
        self,
        title: str,
        impact: str,
        statuses: tuple[ToolStatus, ...],
        guide: InstallGuide,
    ) -> QGroupBox:
        group = QGroupBox(tr(title))
        layout = QVBoxLayout(group)
        impact_label = QLabel(tr(impact))
        impact_label.setWordWrap(True)
        layout.addWidget(impact_label)
        details = QLabel("\n".join(self._status_text(status) for status in statuses if not status.available))
        details.setWordWrap(True)
        layout.addWidget(details)
        if guide.command:
            command_layout = QHBoxLayout()
            command_edit = QLineEdit(guide.command)
            command_edit.setReadOnly(True)
            copy_button = QPushButton(tr("Copy Command"))
            copy_button.clicked.connect(lambda _checked=False, text=guide.command: self._copy_command(text))
            command_layout.addWidget(command_edit, 1)
            command_layout.addWidget(copy_button)
            layout.addLayout(command_layout)
            if guide.requires_admin:
                permission_label = QLabel(tr("This command uses sudo and may ask for an administrator password"))
                permission_label.setWordWrap(True)
                layout.addWidget(permission_label)
        else:
            unavailable_label = QLabel(tr("No supported package manager was found. Follow the official instructions"))
            unavailable_label.setWordWrap(True)
            layout.addWidget(unavailable_label)
        link = QLabel(f'<a href="{guide.url}">{tr("Open Official Installation Guide")}</a>')
        link.setOpenExternalLinks(True)
        layout.addWidget(link)
        if guide.package_manager_url:
            package_manager_link = QLabel(
                f'<a href="{guide.package_manager_url}">{tr("Open Homebrew Website")}</a>'
            )
            package_manager_link.setOpenExternalLinks(True)
            layout.addWidget(package_manager_link)
        return group

    @staticmethod
    def _status_text(status: ToolStatus) -> str:
        if status.state == "missing": return tr("{name}: not found", name=status.name)
        if status.state == "outdated":
            return tr(
                "{name}: version {version} is unsupported; required {minimum}",
                name=status.name, version=status.version or tr("Unknown"), minimum=status.minimum_version,
            )
        return tr("{name}: unable to run or read version", name=status.name)

    def _copy_command(self, command: str) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None: clipboard.setText(command)
        self.copy_status_label.setText(tr("Command copied. Run it in Terminal, then restart MochiStar"))
        self.copy_status_label.show()

    def _dialog_finished(self, _result: int) -> None:
        if self.ignore_checkbox.isChecked(): self.ignored_requested.emit(sorted(self.missing_ids))
