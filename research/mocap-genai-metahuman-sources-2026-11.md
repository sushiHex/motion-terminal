# Mocap, generative motion, MetaHuman and Blender: sources and licences (2026-11-16)

Primary sources only. UNVERIFIED marks a page that could not be opened; an unopened page is not a confirmation.

## Body mocap sources
- **CMU Graphics Lab Motion Capture Database.**
  - **Terms:** the FAQ says "The motion capture data may be copied, modified, or redistributed without permission." (http://mocap.cs.cmu.edu/faqs.php). It names no licence and has no attribution or non-commercial clause. Quality is not guaranteed.
  - **Format:** ASF/AMC files hold solved joint angles, not markers. Markers are separate .c3d files (Vicon, 41 markers, 120 Hz).
  - **Units:** ASF lengths are in inches scaled by 0.45, so metres = `(1/0.45) * 2.54 / 100`.
  - **BVH copies:** the BVH conversions hosted at cgspeed are third-party and untested by CMU, and their terms are UNVERIFIED. Convert locally from ASF/AMC instead of redistributing the cgspeed files.
- **Bandai Namco Research Motiondataset 1/2:** BVH data under CC BY-NC 4.0, with MIT-licensed scripts. Non-commercial only. https://github.com/BandaiNamcoResearchInc/Bandai-Namco-Research-Motiondataset
- **Mixamo:**
  - Commercial use: reported as royalty-free for commercial use. UNVERIFIED (the Adobe FAQ returned 403).
  - Redistribution: forum consensus is that the raw files can't be redistributed. Do not commit raw Mixamo files.
- **AMASS:** non-commercial only, and the data may not be made available to third parties (https://amass.is.tue.mpg.de/license.html). Not usable in a public repo.

## Generative motion
- **MDM:**
  - Code is MIT.
  - The checkpoints carry no stated licence.
  - It depends on SMPL, which is free for "non-commercial scientific research" only (https://smpl.is.tue.mpg.de/modellicense.html).
  - Its HumanML3D training data is derived from AMASS.
  - Showing its outputs publicly is neither granted nor forbidden. Ask the SMPL licensors before relying on it.
- No 2025-2026 text-to-motion model was found with permissive licences on both code and weights:
  - NVIDIA Kimodo (code Apache-2.0; weights under NVIDIA model licences, UNVERIFIED).
  - HY-Motion 1.0 (custom licence with territory restrictions).
  - GVHMR and FlowHMR (weights non-commercial).

## MetaHuman
- **Markerless body or face-and-body capture from one camera (5.8+):**
  - **Source:** https://dev.epicgames.com/documentation/metahuman/metahuman-animation-from-mono-video-capture-in-unreal-engine
  - **Requirements:** a webcam or a supported phone (Live Link Face on iOS or Android), and the Markerless Motion Capture plugin from Fab. It is Windows-only and experimental.
  - **Workflow:** footage goes in through Capture Manager and is solved in the MetaHuman Performance Editor, which exports an animation sequence.
  - **Automation:** 5.8 added a batch-processing API covering solve, review and export, and Python `Get/SetFaceModelCoefficients`.
- **MCP:** 5.8 added a `MetaHumanGenerator` toolset for the Unreal MCP server.
- **Still UNVERIFIED:** the 5.9 MetaHuman release notes, the outfit/clothing workflow, and the MetaHuman EULA text on public display.

## Blender
- **Version:** v5.3.0 is tagged (2026-11-09); 5.2 is the LTS. Headless use is `blender -b --python script.py`.
- **FBX exporter (`io_scene_fbx`, v5.3.0 source):**
  - `add_leaf_bones` defaults to True, which appends `_end` bones. Turn it off.
  - `primary_bone_axis` and `secondary_bone_axis` are configurable.
  - `apply_scale_options` changes how units are detected on import.
- **Scale check:** measure root bone scale on the imported result; do not infer it from exporter settings (see `fbx-scale-import-ue58.md`).
- **Licence:** Blender is GPL. Scripts that call bpy and are distributed should be GPL-compatible; MIT is.

## Consequences for the plan
- **Body source:** CMU (redistributable) for the first mocap slice, converted locally from ASF/AMC.
- **Face and body capture:** MetaHuman markerless capture is engine-native. It covers face and body mocap with a webcam and no retargeting between MetaHumans.
- **Generative motion:** blocked on licences for public display. Keep it as a designed seam until a model with clear code and weights terms is found.
