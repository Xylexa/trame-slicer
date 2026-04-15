from __future__ import annotations

import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from zipfile import ZipFile

import numpy as np
import slicer
from slicer import (
    vtkDataIOManagerLogic,
    vtkMRMLApplicationLogic,
    vtkMRMLModelNode,
    vtkMRMLModelStorageNode,
    vtkMRMLRemoteIOLogic,
    vtkMRMLScene,
    vtkMRMLSegmentationNode,
    vtkMRMLStorageNode,
    vtkMRMLVolumeArchetypeStorageNode,
    vtkMRMLVolumeNode,
)
from pydicom import dcmread
from vtkmodules.vtkCommonMath import vtkMatrix4x4

from .segmentation_editor import SegmentationEditor
from .volumes_reader import VolumesReader


class IOManager:
    """
    Class responsible for loading files in the scene.
    """
    _SEGMENTATION_SUFFIXES = {".seg", ".seg.nrrd", ".seg.nii", ".seg.nii.gz", ".stl", ".obj", ".ply"}
    _DICOM_SEG_SOP_CLASS_UIDS = {
        "1.2.840.10008.5.1.4.1.1.66.4",  # Segmentation Storage
        "1.2.840.10008.5.1.4.1.1.66.5",  # Surface Segmentation Storage
    }

    def __init__(
        self,
        scene: vtkMRMLScene,
        app_logic: vtkMRMLApplicationLogic,
        segmentation_editor: SegmentationEditor,
    ):
        self.scene = scene
        self.app_logic = app_logic

        # Configure IO logic to enable loading from MRB format
        self.cache_dir = TemporaryDirectory()

        self.remote_io = vtkMRMLRemoteIOLogic()
        self.remote_io.SetMRMLScene(self.scene)
        self.remote_io.SetMRMLApplicationLogic(self.app_logic)
        self.remote_io.GetCacheManager().SetRemoteCacheDirectory(self.cache_dir.name)

        self.vtk_io_manager_logic = vtkDataIOManagerLogic()
        self.vtk_io_manager_logic.SetMRMLScene(scene)
        self.vtk_io_manager_logic.SetMRMLApplicationLogic(app_logic)
        self.vtk_io_manager_logic.SetAndObserveDataIOManager(self.remote_io.GetDataIOManager())
        self.remote_io.AddDataIOToScene()

        self.segmentation_editor: SegmentationEditor = segmentation_editor

    def load_volumes(
        self,
        volume_files: str | list[str],
    ) -> list[vtkMRMLVolumeNode]:
        return VolumesReader.load_volumes(self.scene, self.app_logic, volume_files)

    @classmethod
    def write_volume(cls, volume_node, volume_file: str | Path) -> None:
        cls.write_node(
            volume_node,
            volume_file,
            vtkMRMLVolumeArchetypeStorageNode,
            False,
        )

    def load_model(
        self,
        model_file: str | Path,
        do_convert_to_slicer_coord: bool = True,
    ) -> vtkMRMLModelNode | None:
        model_file = Path(model_file).resolve()
        if not model_file.is_file():
            return None

        storage_node = vtkMRMLModelStorageNode()
        storage_node.SetFileName(model_file.as_posix())
        model_name = model_file.stem
        model_node: vtkMRMLModelNode = self.scene.AddNewNodeByClass("vtkMRMLModelNode", model_name)
        storage_node.ReadData(model_node)

        # Check if RAS / LPS conversion is required
        # Slicer will read coordinates in the file header during load regarding of preferred load format
        # Check if coordinate change occurred to rollback change if requested
        did_convert_coord = storage_node.GetCoordinateSystem() != vtkMRMLStorageNode.CoordinateSystemRAS
        if not do_convert_to_slicer_coord and did_convert_coord:
            storage_node.ConvertBetweenRASAndLPS(model_node.GetPolyData(), model_node.GetPolyData())

        model_node.CreateDefaultDisplayNodes()
        return model_node

    @classmethod
    def write_model(
        cls,
        model_node,
        model_file: str | Path,
        do_convert_from_slicer_coord: bool = True,
    ) -> None:
        cls.write_node(
            model_node,
            model_file,
            vtkMRMLModelStorageNode,
            do_convert_from_slicer_coord,
        )

    def load_segmentation(
        self, segmentation_file: str | Path, do_convert_to_slicer_coord=True
    ) -> vtkMRMLSegmentationNode | None:
        segmentation_path = Path(segmentation_file)
        if segmentation_path.suffix in [".obj", ".stl", ".ply"]:
            model = self.load_model(segmentation_file, do_convert_to_slicer_coord)
            try:
                return self.segmentation_editor.create_segmentation_node_from_model_node(model)
            finally:
                self.scene.RemoveNode(model)

        if self._is_dicom_seg_file(segmentation_path):
            segmentation_node = self._load_dicom_segmentation(segmentation_path)
            if segmentation_node is not None:
                return segmentation_node

        segmentation_node = self.segmentation_editor.load_segmentation_from_file(segmentation_file)
        if segmentation_node is not None:
            return segmentation_node

        # Some pipelines store Slicer SEG NRRD payload with a plain ".seg" extension.
        # Retry through a temporary ".seg.nrrd" alias to trigger the segmentation reader.
        if segmentation_path.name.lower().endswith(".seg"):
            with NamedTemporaryFile(suffix=".seg.nrrd", delete=False) as temp_file:
                temp_path = Path(temp_file.name)

            try:
                shutil.copyfile(segmentation_path, temp_path)
                return self.segmentation_editor.load_segmentation_from_file(temp_path)
            finally:
                temp_path.unlink(missing_ok=True)

        return None

    @classmethod
    def is_segmentation_file(cls, file_path: str | Path) -> bool:
        path = Path(file_path)
        path_name = path.name.lower()
        if any(path_name.endswith(ext) for ext in cls._SEGMENTATION_SUFFIXES):
            return True
        return cls._is_dicom_seg_file(path)

    @classmethod
    def _is_dicom_seg_file(cls, file_path: str | Path) -> bool:
        path = Path(file_path)
        if not path.is_file():
            return False

        if path.suffix.lower() not in {".dcm", ""}:
            return False

        try:
            dcm = dcmread(path.as_posix(), stop_before_pixels=True, force=True, specific_tags=["SOPClassUID"])
            sop_class_uid = str(getattr(dcm, "SOPClassUID", ""))
            return sop_class_uid in cls._DICOM_SEG_SOP_CLASS_UIDS
        except Exception:
            return False

    def _load_dicom_segmentation(self, segmentation_path: Path) -> vtkMRMLSegmentationNode | None:
        """
        Import a DICOM SEG file while preserving per-segment identity and colors.
        """
        try:
            ds = dcmread(segmentation_path.as_posix(), force=True)
            pixel_array = np.asarray(ds.pixel_array)
        except Exception:
            return None

        if pixel_array.ndim == 2:
            pixel_array = pixel_array[np.newaxis, ...]
        if pixel_array.ndim != 3:
            return None

        volume_node = self._find_reference_volume_node()
        if volume_node is None:
            return self._load_dicom_segmentation_with_itk_fallback(segmentation_path)

        volume_array = slicer.util.arrayFromVolume(volume_node)
        if volume_array.ndim != 3:
            return None

        segment_metadata = self._dicom_segment_metadata(ds)
        per_segment_masks: dict[int, np.ndarray] = {}
        n_slices = volume_array.shape[0]

        for frame_idx in range(pixel_array.shape[0]):
            frame_group = (
                ds.PerFrameFunctionalGroupsSequence[frame_idx]
                if hasattr(ds, "PerFrameFunctionalGroupsSequence")
                and frame_idx < len(ds.PerFrameFunctionalGroupsSequence)
                else None
            )
            segment_number = self._frame_segment_number(frame_group)
            if segment_number not in per_segment_masks:
                per_segment_masks[segment_number] = np.zeros_like(volume_array, dtype=np.uint8)

            k_idx = self._frame_k_index(frame_group, volume_node, n_slices, frame_idx)
            frame_mask = np.asarray(pixel_array[frame_idx]) > 0
            if frame_mask.shape != volume_array.shape[1:]:
                if frame_mask.T.shape == volume_array.shape[1:]:
                    frame_mask = frame_mask.T
                else:
                    continue
            per_segment_masks[segment_number][k_idx][frame_mask] = 1

        segmentation_node = self.segmentation_editor.create_empty_segmentation_node()
        segmentation_node.SetName(segmentation_path.stem)
        segmentation_node.SetReferenceImageGeometryParameterFromVolumeNode(volume_node)

        imported = 0
        for segment_number in sorted(per_segment_masks):
            mask = per_segment_masks[segment_number]
            if not np.any(mask):
                continue

            segment_info = segment_metadata.get(segment_number, {})
            labelmap_node = self.scene.AddNewNodeByClass(
                "vtkMRMLLabelMapVolumeNode",
                f"{segmentation_path.stem}_segment_{segment_number}",
            )
            labelmap_node.CopyOrientation(volume_node)
            slicer.util.updateVolumeFromArray(labelmap_node, mask)

            before_ids = self._segment_ids(segmentation_node)
            try:
                self.segmentation_editor._logic.ImportLabelmapToSegmentationNode(labelmap_node, segmentation_node, "")
            finally:
                self.scene.RemoveNode(labelmap_node)

            after_ids = self._segment_ids(segmentation_node)
            new_ids = [segment_id for segment_id in after_ids if segment_id not in set(before_ids)]
            if not new_ids and after_ids:
                new_ids = [after_ids[-1]]
            if not new_ids:
                continue

            segment = segmentation_node.GetSegmentation().GetSegment(new_ids[0])
            if segment is not None:
                segment_name = str(segment_info.get("name") or f"Segment {segment_number}")
                segment.SetName(segment_name)
                rgb = segment_info.get("rgb")
                if rgb:
                    segment.SetColor(*rgb)

            imported += 1

        if imported > 0:
            segmentation_node.SetDisplayVisibility(True)
            return segmentation_node

        self.scene.RemoveNode(segmentation_node)
        return self._load_dicom_segmentation_with_itk_fallback(segmentation_path)

    def _load_dicom_segmentation_with_itk_fallback(self, segmentation_path: Path) -> vtkMRMLSegmentationNode | None:
        """
        Fallback for environments where frame-based SEG rebuild is not possible.
        """
        try:
            import itk
        except Exception:
            return None

        with TemporaryDirectory() as tmp_dir:
            temp_nrrd = Path(tmp_dir) / f"{segmentation_path.stem}.nrrd"
            itk_image = itk.imread(segmentation_path.as_posix())
            itk.imwrite(itk_image, temp_nrrd.as_posix())

            labelmap_node = self.scene.AddNewNodeByClass("vtkMRMLLabelMapVolumeNode", segmentation_path.stem)
            storage_node = vtkMRMLVolumeArchetypeStorageNode()
            storage_node.SetFileName(temp_nrrd.as_posix())
            if not storage_node.ReadData(labelmap_node):
                self.scene.RemoveNode(labelmap_node)
                return None

            try:
                return self.segmentation_editor.create_segmentation_node_from_labelmap(labelmap_node)
            finally:
                self.scene.RemoveNode(labelmap_node)

    def _find_reference_volume_node(self) -> vtkMRMLVolumeNode | None:
        volumes = list(self.scene.GetNodesByClass("vtkMRMLVolumeNode"))
        if not volumes:
            return None

        def volume_size(node: vtkMRMLVolumeNode) -> int:
            image_data = node.GetImageData()
            if image_data is None:
                return 0
            dims = image_data.GetDimensions()
            return int(dims[0] * dims[1] * dims[2])

        return max(volumes, key=volume_size)

    @staticmethod
    def _segment_ids(segmentation_node: vtkMRMLSegmentationNode) -> list[str]:
        segmentation = segmentation_node.GetSegmentation()
        return [segmentation.GetNthSegmentID(i) for i in range(segmentation.GetNumberOfSegments())]

    @classmethod
    def _dicom_segment_metadata(cls, ds) -> dict[int, dict[str, object]]:
        metadata: dict[int, dict[str, object]] = {}
        if not hasattr(ds, "SegmentSequence"):
            return metadata

        for item in ds.SegmentSequence:
            try:
                number = int(getattr(item, "SegmentNumber"))
            except Exception:
                continue

            entry: dict[str, object] = {
                "name": str(getattr(item, "SegmentLabel", f"Segment {number}")),
            }

            cielab = getattr(item, "RecommendedDisplayCIELabValue", None)
            if cielab and len(cielab) == 3:
                entry["rgb"] = cls._dicom_cielab_to_rgb(cielab)

            metadata[number] = entry

        return metadata

    @staticmethod
    def _frame_segment_number(frame_group) -> int:
        try:
            return int(frame_group.SegmentIdentificationSequence[0].ReferencedSegmentNumber)
        except Exception:
            return 1

    @staticmethod
    def _frame_k_index(frame_group, volume_node: vtkMRMLVolumeNode, n_slices: int, frame_idx: int) -> int:
        if n_slices <= 0:
            return 0

        try:
            lps = frame_group.PlanePositionSequence[0].ImagePositionPatient
            lps_point = [float(lps[0]), float(lps[1]), float(lps[2]), 1.0]
            ras_point = [-lps_point[0], -lps_point[1], lps_point[2], 1.0]

            ras_to_ijk = vtkMatrix4x4()
            volume_node.GetRASToIJKMatrix(ras_to_ijk)
            ijk_point = [0.0, 0.0, 0.0, 1.0]
            ras_to_ijk.MultiplyPoint(ras_point, ijk_point)
            return max(0, min(int(round(ijk_point[2])), n_slices - 1))
        except Exception:
            return frame_idx % n_slices

    @staticmethod
    def _dicom_cielab_to_rgb(cielab_triplet) -> tuple[float, float, float]:
        l_enc, a_enc, b_enc = [float(v) for v in cielab_triplet]

        l_star = (l_enc / 65535.0) * 100.0
        a_star = (a_enc / 65535.0) * 255.0 - 128.0
        b_star = (b_enc / 65535.0) * 255.0 - 128.0

        fy = (l_star + 16.0) / 116.0
        fx = fy + (a_star / 500.0)
        fz = fy - (b_star / 200.0)

        def f_inv(t: float) -> float:
            delta = 6.0 / 29.0
            if t > delta:
                return t**3
            return 3.0 * (delta**2) * (t - 4.0 / 29.0)

        x = 0.95047 * f_inv(fx)
        y = 1.00000 * f_inv(fy)
        z = 1.08883 * f_inv(fz)

        r_lin = 3.2406 * x - 1.5372 * y - 0.4986 * z
        g_lin = -0.9689 * x + 1.8758 * y + 0.0415 * z
        b_lin = 0.0557 * x - 0.2040 * y + 1.0570 * z

        def gamma_correct(c: float) -> float:
            c = max(0.0, min(1.0, c))
            if c <= 0.0031308:
                return 12.92 * c
            return 1.055 * (c ** (1.0 / 2.4)) - 0.055

        return (
            gamma_correct(r_lin),
            gamma_correct(g_lin),
            gamma_correct(b_lin),
        )

    def write_segmentation(self, segmentation_node, segmentation_file: str | Path):
        self.segmentation_editor.export_segmentation_to_file(segmentation_node, segmentation_file)

    @classmethod
    def write_node(
        cls,
        node,
        node_file,
        storage_type: type,
        do_convert_from_slicer_coord: bool,
    ) -> None:
        if not node:
            return

        node_file = Path(node_file).resolve().as_posix()
        storage_node = storage_type()

        if hasattr(storage_node, "SetCoordinateSystem"):
            storage_node.SetCoordinateSystem(
                vtkMRMLStorageNode.CoordinateSystemLPS
                if do_convert_from_slicer_coord
                else vtkMRMLStorageNode.CoordinateSystemRAS
            )
        storage_node.SetFileName(node_file)
        storage_node.WriteData(node)

    def load_scene(self, scene_path) -> bool:
        scene_path = Path(scene_path)
        if not scene_path.is_file():
            return False

        if scene_path.name.endswith("mrb"):
            return self._load_mrb_scene(scene_path)
        return self._load_mrml_scene(scene_path)

    def write_scene(self, scene_path: str | Path) -> bool:
        return self.save_scene(scene_path)

    def save_scene(self, scene_path: str | Path) -> bool:
        scene_path = Path(scene_path)
        scene_path = scene_path.resolve()
        base_dir = scene_path.parent
        base_dir.mkdir(parents=True, exist_ok=True)
        self.scene.SetURL(scene_path.as_posix())
        self.scene.SetRootDirectory(base_dir.as_posix())

        if scene_path.name.endswith(".mrml"):
            return self.scene.Commit()
        return self.scene.WriteToMRB(scene_path.as_posix())

    def _load_mrml_scene(self, scene_path: Path) -> bool:
        if not scene_path.is_file():
            return False
        self.scene.SetURL(scene_path.as_posix())
        return self.scene.Import(None)

    def _load_mrb_scene(self, scene_path: Path) -> bool:
        try:
            with TemporaryDirectory() as tmpdir, ZipFile(scene_path, "r") as zip_file:
                zip_file.extractall(tmpdir)
                return self._load_mrml_scene(next(Path(tmpdir).rglob("*.mrml")))
        except StopIteration:
            return False
