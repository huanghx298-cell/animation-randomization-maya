from PySide6 import QtWidgets, QtCore
import maya.OpenMayaUI as omui
import maya.cmds as cmds
import shiboken6
import os
import random

from maya.app.general.mayaMixin import MayaQWidgetDockableMixin


class MySquareUI(MayaQWidgetDockableMixin, QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent or get_maya_window())

        try:
            AnimationPipeline.restore_from_scene()
        except Exception:
            pass

        self.setWindowTitle("Square UI")
        self.resize(300, 300)

        self.layout = QtWidgets.QVBoxLayout(self)
        self.layout.addWidget(QtWidgets.QLabel("This is a dockable window"))

        DEBUG_STEPS = True
        if DEBUG_STEPS:

            self._add_button("Create Sample Skeleton",
                             self.create_sample)
            self._add_button("Simplify Sample Curves",
                             self.simplify_sample)
            self._add_button("Zero Sample Key Values",
                             self.zero_sample)
            self._add_button("Randomize Sample Keys",
                             self.randomize_sample)
            self._add_button("Write Back To AnimLayer",
                             self.write_back)
            self._add_button("Delete Sample Skeleton",
                             self.delete_sample)
        self._add_button("Batch Import Mixamo FBX",
                         self.batch_import_dialog)
        self._add_button("Randomize Animation",
                         self.randomize_animation)

        self.layout.addStretch()

    def batch_import_dialog(self):
        input_dir = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select FBX Folder")
        if not input_dir:
            return

        output_dir = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select Output MB Folder")
        if not output_dir:
            return

        try:
            batch_import_fbx_to_mb(input_dir, output_dir)
        except Exception as e:
            cmds.warning(str(e))

    def create_sample(self):
        try:
            AnimationPipeline.create_sample()
        except Exception as e:
            cmds.warning(str(e))

    def delete_sample(self):
        try:
            AnimationPipeline.delete_sample()
        except Exception as e:
            cmds.warning(str(e))

    def simplify_sample(self):
        try:
            AnimationOps.simplify_sample_curves()
            print("✔ Sample curves simplified")
        except Exception as e:
            cmds.warning(str(e))

    def zero_sample(self):
        try:
            AnimationOps.zero_sample_keys()
        except Exception as e:
            cmds.warning(str(e))

    def randomize_sample(self):
        try:
            AnimationOps.randomize_sample_keys(
                min_deg=-20.0, max_deg=20.0)
        except Exception as e:
            cmds.warning(str(e))

    def write_back(self):
        try:
            AnimationPipeline.write_sample_to_anim_layer()
        except Exception as e:
            cmds.warning(str(e))

    def randomize_animation(self):
        try:
            AnimationController.randomize_animation()
        except Exception as e:
            cmds.warning(str(e))

    def _add_button(self, label, callback):
        btn = QtWidgets.QPushButton(label)
        btn.clicked.connect(callback)
        self.layout.addWidget(btn)
        return btn


class AnimationPipelineContext:
    def __init__(self):
        self.original_root = None
        self.sample_root = None

    def clear(self):
        self.original_root = None
        self.sample_root = None


class AnimationPipeline:
    @staticmethod
    def create_sample():
        sel = cmds.ls(sl=True, type="joint")
        if not sel:
            raise RuntimeError("Please select root joint")

        root = sel[0]

        if PIPELINE_CTX.sample_root and cmds.objExists(PIPELINE_CTX.sample_root):
            raise RuntimeError("Sample already exists. Delete it first.")

        dup_roots = cmds.duplicate(root, rr=True, ic=True, un=True)
        if not dup_roots:
            raise RuntimeError("Failed to duplicate skeleton")

        sample_joints = [dup_roots[0]]+(cmds.listRelatives(
            dup_roots[0], allDescendents=True, type="joint", fullPath=True) or [])

        for j in sorted(sample_joints, key=lambda x: x.count("|"), reverse=True):
            short = j.split("|")[-1]
            if not short.endswith("__SAMPLE__"):
                cmds.rename(j, short + "__SAMPLE__")

        sample_root = cmds.ls(dup_roots[0] + "__SAMPLE__", long=True)[0]

        PIPELINE_CTX.original_root = root
        PIPELINE_CTX.sample_root = sample_root

        return sample_root

    @staticmethod
    def delete_sample():
        sample = PIPELINE_CTX.sample_root

        if not sample or not cmds.objExists(sample):
            raise RuntimeError("No sample skeleton to delete")

        cmds.delete(sample)
        PIPELINE_CTX.clear()

    @staticmethod
    def write_sample_to_anim_layer():
        (sample_root, original_root,
         sample_joints, original_joints,
         sample_map, original_map) = AnimationPipeline.get_skeleton_pair()

        layer = cmds.animLayer("Randomized_Layer", override=True)

        cmds.undoInfo(openChunk=True)
        try:
            cmds.select(list(original_map.values()), r=True)
            cmds.animLayer(layer, e=True, addSelectedObjects=True)

            for j in sample_joints:
                attrs = cmds.listAttr(j, keyable=True) or []

            for name, sample_joint in sample_map.items():
                if "__SAMPLE__" in name:
                    base_name = name.replace("__SAMPLE__", "")
                    name = f"mixamorig:{base_name}"

                original_joint = original_map.get(name)
                if not original_joint:
                    continue

                for attr in attrs:
                    sample_plug = f"{sample_joint}.{attr}"
                    if not cmds.listConnections(sample_plug,
                                                type="animCurve", s=True):
                        continue

                    times = cmds.keyframe(sample_plug, q=True, tc=True)

                    if not times:
                        continue

                    for t in times:
                        value = cmds.getAttr(sample_plug, time=t)
                        cmds.setKeyframe(
                            f"{original_joint}.{attr}",
                            t=t, v=value, animLayer=layer)

            cmds.animLayer(layer, edit=True, override=False)
            cmds.animLayer(layer, edit=True, weight=0.20, )
        finally:
            cmds.undoInfo(closeChunk=True)

        print(f"✔ Sample written to animLayer: {layer}")
        return layer

    @staticmethod
    def restore_from_scene():
        joints = cmds.ls(type="joint", l=True)

        for j in joints:
            short = j.split("|")[-1]
            if "__SAMPLE__" not in short:
                continue

            parent = cmds.listRelatives(j, p=True, type="joint")
            if parent:
                continue

            sample = short
            base_name = short.replace("__SAMPLE__", "")
            original = f"mixamorig:{base_name}"

            PIPELINE_CTX.sample_root = sample
            PIPELINE_CTX.original_root = original

    @staticmethod
    def find_anim_time_range(joints):
        for j in joints:
            attrs = cmds.listAttr(j, keyable=True) or []
            for attr in attrs:
                plug = f"{j}.{attr}"
                curves = cmds.listConnections(
                    plug, type="animCurve", s=True) or []
                for curve in curves:
                    times = cmds.keyframe(curve, q=True, tc=True)
                    if times:
                        return min(times), max(times)
        return None, None

    @staticmethod
    def get_skeleton_pair():

        sample_root = PIPELINE_CTX.sample_root
        original_root = PIPELINE_CTX.original_root

        if not sample_root or not cmds.objExists(sample_root):
            raise RuntimeError("No sample skeleton")
        if not original_root or not cmds.objExists(original_root):
            raise RuntimeError("No original skeleton")

        sample_joints = [sample_root] + (
            cmds.listRelatives(sample_root, ad=True, type="joint", f=True) or [])
        original_joints = [original_root] + (
            cmds.listRelatives(original_root, ad=True, type="joint", f=True) or [])

        sample_map = {j.split("|")[-1]: j for j in sample_joints}
        original_map = {j.split("|")[-1]: j for j in original_joints}

        return (sample_root, original_root,
                sample_joints, original_joints,
                sample_map, original_map)


class AnimationOps:
    @staticmethod
    def simplify_sample_curves():
        (sample_root, original_root,
         sample_joints, original_joints,
         sample_map, original_map) = AnimationPipeline.get_skeleton_pair()

        cmds.undoInfo(openChunk=True)
        try:
            for j, attr, curve, plug in iter_joint_anim_curves(sample_joints):
                cmds.filterCurve(curve, f="simplify", timeTolerance=0.1)
        finally:
            cmds.undoInfo(closeChunk=True)

    @staticmethod
    def zero_sample_keys():
        (sample_root, original_root,
         sample_joints, original_joints,
         sample_map, original_map) = AnimationPipeline.get_skeleton_pair()

        start_time, end_time = AnimationPipeline.find_anim_time_range(
            sample_joints)
        if start_time is None or end_time is None:
            raise RuntimeError("No animation time range found")

        TARGET_ATTRS = ("rotateX", "rotateY", "rotateZ",
                        "translateX", "translateY", "translateZ",)

        cmds.undoInfo(openChunk=True)
        try:
            for j in sample_joints:
                attrs = [
                    a for a in (cmds.listAttr(j, keyable=True) or [])
                    if a in TARGET_ATTRS
                ]

                for attr in attrs:
                    plug = f"{j}.{attr}"
                    curves = cmds.listConnections(
                        plug, type="animCurve", s=True) or []

                    if curves:
                        for curve in curves:
                            cmds.keyframe(curve, e=True, vc=0)
                    else:
                        cmds.setKeyframe(plug, t=start_time, v=0)
                        cmds.setKeyframe(plug, t=end_time, v=0)

        finally:
            cmds.undoInfo(closeChunk=True)

        print("✔ Sample values zeroed (keys preserved, static attrs keyed at head/tail)")

    @staticmethod
    def randomize_sample_keys(min_deg=-20, max_deg=20, seed=None):
        (sample_root, original_root,
         sample_joints, original_joints,
         sample_map, original_map) = AnimationPipeline.get_skeleton_pair()

        if seed is not None:
            random.seed(seed)

        start_time, end_time = AnimationPipeline.find_anim_time_range(
            sample_joints)
        if start_time is None or end_time is None:
            raise RuntimeError("No animation time range found")

        TARGET_ATTRS = ("rotateX", "rotateY", "rotateZ")

        cmds.undoInfo(openChunk=True)
        try:
            for j, attr, curve, plug in iter_joint_anim_curves(
                    sample_joints, attrs=TARGET_ATTRS):
                times = cmds.keyframe(curve, q=True, tc=True) or []
                for t in times:
                    if t == start_time or t == end_time:
                        continue

                    value = random.uniform(min_deg, max_deg)
                    cmds.keyframe(curve, e=True, t=(t, t), vc=value)
        finally:
            cmds.undoInfo(closeChunk=True)

        print(
            f"✔ Sample keys randomized (exclude head/tail, {min_deg}, {max_deg})")


class AnimationController:
    @staticmethod
    def randomize_animation():
        cmds.undoInfo(openChunk=True)
        try:
            AnimationPipeline.create_sample()
            AnimationOps.simplify_sample_curves()
            AnimationOps.zero_sample_keys()
            AnimationOps.randomize_sample_keys()
            AnimationPipeline.write_sample_to_anim_layer()
            AnimationPipeline.delete_sample()
        finally:
            cmds.undoInfo(closeChunk=True)


def get_maya_window():
    ptr = omui.MQtUtil.mainWindow()
    return shiboken6.wrapInstance(int(ptr), QtWidgets.QWidget)


def iter_joint_anim_curves(joints, attrs=None):
    for j in joints:
        keyable = cmds.listAttr(j, keyable=True) or []
        use_attrs = attrs if attrs else keyable

        for attr in use_attrs:
            if attr not in keyable:
                continue

            plug = f"{j}.{attr}"
            curves = cmds.listConnections(plug, type="animCurve", s=True) or []

            for curve in curves:
                yield j, attr, curve, plug


def batch_import_fbx_to_mb(input_dir, output_dir):
    if not os.path.isdir(input_dir):
        raise RuntimeError("Invalid input directory")

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    fbx_files = [f for f in os.listdir(input_dir)if f.lower().endswith(".fbx")]

    if not fbx_files:
        cmds.warning("No FBX files found")
        return

    for fbx in fbx_files:
        fbx_path = os.path.join(input_dir, fbx)
        name = os.path.splitext(fbx)[0]
        out_path = os.path.join(output_dir, name + ".mb")

        cmds.file(new=True, force=True)

        cmds.file(fbx_path, i=True, type="FBX",
                  ignoreVersion=True, namespace=":", options="fbx")

        cmds.file(rename=out_path)
        cmds.file(save=True, type="mayaBinary")

        print(f"✔ Imported: {fbx} -> {out_path}")

    print("✔ Batch import finished")


def show_square_ui():
    show_square_ui.instance = MySquareUI()
    show_square_ui.instance.show(dockable=True)
    return show_square_ui.instance


PIPELINE_CTX = AnimationPipelineContext()

show_square_ui()
