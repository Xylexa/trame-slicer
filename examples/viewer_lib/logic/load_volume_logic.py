from tempfile import TemporaryDirectory

from slicer import vtkMRMLVolumeNode
from trame_server import Server
from undo_stack import Signal

from trame_slicer.core import SlicerApp
from trame_slicer.utils import write_client_files_to_dir

from ..ui import (
    LoadVolumeState,
    LoadVolumeUI,
)
from .base_logic import BaseLogic


class LoadVolumeLogic(BaseLogic[LoadVolumeState]):
    volume_loaded = Signal(vtkMRMLVolumeNode)

    def __init__(self, server: Server, slicer_app: SlicerApp):
        super().__init__(server, slicer_app, LoadVolumeState)

    def set_ui(self, ui: LoadVolumeUI):
        ui.on_load_volume.connect(self._on_load_volume)

    def _on_load_volume(self, files: list[dict], is_loading_state_name: str) -> None:
        try:
            self._load_volume_files(files)
        finally:
            self.state[is_loading_state_name] = False

    def _load_volume_files(self, files: list[dict]) -> None:
        if not files:
            return

        with TemporaryDirectory() as tmp_dir:
            loaded_files = write_client_files_to_dir(files, tmp_dir)
            if len(loaded_files) == 1 and loaded_files[0].endswith(".mrb"):
                self._slicer_app.scene.Clear()
                self._on_load_scene(loaded_files[0])
            else:
                self._on_load_mixed_files(loaded_files)

    @classmethod
    def _is_segmentation_file(cls, file_path: str, slicer_app: SlicerApp) -> bool:
        return slicer_app.io_manager.is_segmentation_file(file_path)

    def _on_load_mixed_files(self, loaded_files: list[str]) -> None:
        segmentation_files = [f for f in loaded_files if self._is_segmentation_file(f, self._slicer_app)]
        volume_files = [f for f in loaded_files if not self._is_segmentation_file(f, self._slicer_app)]

        if volume_files:
            self._slicer_app.scene.Clear()
            self._on_load_volume_files(volume_files)

        if segmentation_files:
            self._on_load_segmentation_files(segmentation_files)
            # If a volume is already loaded, re-emit it so segmentation/editor state syncs.
            self._emit_current_volume()

    def _on_load_scene(self, scene_file):
        self._slicer_app.io_manager.load_scene(scene_file)
        self._show_largest_volume(list(self._slicer_app.scene.GetNodesByClass("vtkMRMLVolumeNode")))

    def _on_load_volume_files(self, loaded_files):
        volumes = self._slicer_app.io_manager.load_volumes(loaded_files)
        if not volumes:
            return
        self._show_largest_volume(volumes)

    def _on_load_segmentation_files(self, segmentation_files: list[str]) -> None:
        for segmentation_file in segmentation_files:
            segmentation_node = self._slicer_app.io_manager.load_segmentation(segmentation_file)
            if not segmentation_node:
                continue
            segmentation_node.CreateDefaultDisplayNodes()
            segmentation_node.SetDisplayVisibility(True)

    def _emit_current_volume(self) -> None:
        volumes = list(self._slicer_app.scene.GetNodesByClass("vtkMRMLVolumeNode"))
        if not volumes:
            return
        self._show_largest_volume(volumes)

    def _show_largest_volume(self, volumes):
        if not volumes:
            return

        def bounds_volume(v):
            b = [0] * 6
            v.GetImageData().GetBounds(b)
            return (b[1] - b[0]) * (b[3] - b[2]) * (b[5] - b[4])

        volumes = sorted(volumes, key=bounds_volume)
        volume_node = volumes[-1]

        self._slicer_app.display_manager.show_volume(
            volume_node,
            do_reset_views=True,
        )

        self.volume_loaded(volume_node)
