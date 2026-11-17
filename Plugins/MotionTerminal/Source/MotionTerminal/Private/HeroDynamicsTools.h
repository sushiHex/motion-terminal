#pragma once

#include "ToolsetRegistry/ToolsetDefinition.h"
#include "HeroDynamicsTools.generated.h"

class USkeletalMesh;
class USkeletalMeshComponent;
class UMaterialInterface;

/** A hero's secondary motion at runtime: what Python cannot reach. The cloth configs (UChaosClothConfig is not exposed
    to Python: its properties read back as the base class's) and the deformed vertices (skinning + Chaos cloth, computed
    on the game thread as the GPU skin cache does). Used by the PIE dynamics drive (Scripts/pie_dynamics_drive.py). */
UCLASS()
class UHeroDynamicsTools : public UToolsetDefinition
{
	GENERATED_BODY()

public:
	/** The mesh's cloth collision quality, through its clothing interactors: continuous collision on or off for every
	    Chaos clothing asset (UChaosClothingInteractor::SetCollision, each asset keeping its own collision thickness,
	    friction and self collision thickness), and the solver's substeps per frame (Substeps > 0; 0 keeps each asset's
	    own). Returns JSON {"ok", "substeps", "assets": {name: {"thickness", "friction", "self_thickness", "ccd",
	    "asset_substeps", "asset_iterations"}}, "error"?}. */
	UFUNCTION(BlueprintCallable, Category = "MotionTerminal|Dynamics", meta = (AICallable))
	static FString SetClothCollision(USkeletalMeshComponent* Mesh, bool bContinuous, int32 Substeps);

	/** Prepares a displacement heatmap: both meshes on LOD 0, Shown's cloth data readable on the game thread
	    (bWaitForParallelClothTask; Reference's cloth must be blended out, ClothBlendWeight 0), and HeatMaterial set as Shown's overlay material (drawn over its own materials), which
	    shows vertex colour red as the displacement (0 = none, 1 = the scale's top). The render path is untouched (GPU skinning). Shown and
	    Reference must use the same skeletal mesh. */
	UFUNCTION(BlueprintCallable, Category = "MotionTerminal|Dynamics", meta = (AICallable))
	static FString BeginDisplacementHeat(USkeletalMeshComponent* Shown, USkeletalMeshComponent* Reference,
	                                     UMaterialInterface* HeatMaterial);

	/** One frame of the heatmap: per LOD 0 render vertex, the distance between Shown's and Reference's final positions
	    (skinning + Chaos cloth, computed as the GPU skin cache does) in their own component spaces (the same animation,
	    one with the simulation and one without, side by side), over ScaleCm, written in place into the vertex colour
	    buffer BeginDisplacementHeat made. Returns JSON {"ok", "vertices", "max_cm", "cloth_vertices", "cloth_rms_cm"?
	    (the RMS offset of the vertices Shown's cloth moves), "cloth_motion_cm"? (the RMS of how far those offsets moved
	    since the previous call: the garment's motion relative to the animation, how it swings and settles; absent on a
	    take's first frame), "cloth_ripple_cm"? (the same less the cloth's mean motion this frame: how much it moves
	    other than as one sheet), "error"?}. */
	UFUNCTION(BlueprintCallable, Category = "MotionTerminal|Dynamics", meta = (AICallable))
	static FString ApplyDisplacementHeat(USkeletalMeshComponent* Shown, USkeletalMeshComponent* Reference, float ScaleCm);

	/** This frame's cloth penetration into the body: per simulated cloth vertex (LOD 0), its depth behind the closest
	    limb triangle (enabled non-cloth sections, all three vertices on BodyBones or their leaf children, never on
	    ExcludeBones), less the depth the same vertex already had skinned before the cloth (the art's own
	    overlap there). Behind a face counts as inside only nearer the limb's axis (BodyBones[i] -> Aims[i]) than that
	    face; a vertex farther from every limb's axis than that limb's radius (its farthest vertex) is clear, unsearched.
	    Returns JSON {"ok", "tested", "clear", "body_triangles", "over_tol" (vertices added more than TolCm), "max_cm"
	    (the most added), "max_raw_cm", "worst": {"vertex", "section", "body_bone", "raw_cm", "allowed_cm", "point",
	    "closest"}, "error"?}. Vertices only: a triangle interior crossing between them is not seen. */
	UFUNCTION(BlueprintCallable, Category = "MotionTerminal|Dynamics", meta = (AICallable))
	static FString MeasureClothPenetration(USkeletalMeshComponent* Mesh, const TArray<FName>& BodyBones,
	                                       const TArray<FName>& Aims, const TArray<FName>& ExcludeBones, float TolCm);

	/** The reference-pose component-space positions of the mesh's body surface around Region: LOD 0 render vertices of
	    the enabled sections that are not cloth, whose weight on Region's bones is at least Dominance. JSON {"ok", "count",
	    "xyz": [x0, y0, z0, x1, ...], "error"?}: what a garment's collider is fitted to (hero_intake_fit). */
	UFUNCTION(BlueprintCallable, Category = "MotionTerminal|Dynamics", meta = (AICallable))
	static FString SkinPoints(USkeletalMesh* SkeletalMesh, const TArray<FName>& Region, float Dominance);

	/** The garment at rest, as Chaos simulates it: per clothing asset its LOD 0 physical mesh's particles (reference
	    pose, component space) with each one's painted max distance (0: pinned to the skin), the asset's collision
	    thickness and its own physics asset. JSON {"ok", "assets": [{"name", "thickness_cm", "physics_asset", "xyz":
	    [x0, y0, z0, ...], "max_distance": [...]}], "error"?}: what a garment's colliders must leave clear, or they push
	    the cloth off the body where its simulation begins (hero_intake_fit, collider_autofit.skin_capsule). */
	UFUNCTION(BlueprintCallable, Category = "MotionTerminal|Dynamics", meta = (AICallable))
	static FString GarmentPoints(USkeletalMesh* SkeletalMesh);

	/** Every clothing asset's cloth configs as the asset authors them: each config object's every property, exported as
	    its native text (reflection; Python sees almost none of them). JSON {"ok", "assets": {asset: {config class: {
	    property: text}}}, "error"?}: what a garment behaviour (Scripts/dynamics_behaviours.json "garments") overrides. */
	UFUNCTION(BlueprintCallable, Category = "MotionTerminal|Dynamics", meta = (AICallable))
	static FString ClothConfigs(USkeletalMesh* SkeletalMesh);
};
