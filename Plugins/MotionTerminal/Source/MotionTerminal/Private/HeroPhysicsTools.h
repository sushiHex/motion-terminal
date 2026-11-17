#pragma once

#include "ToolsetRegistry/ToolsetDefinition.h"
#include "HeroPhysicsTools.generated.h"

/** Hit reaction (docs/specs/2026-10-29-hit-reaction-design.md): a hero's ragdoll physics asset, built
    from a spec the hero's profile derives (Scripts/hit_reaction_core.py physics_spec). The bodies and joints are decided
    in tested Python; this only builds them, because a physics asset's bodies are not visible to Python
    (PhysicsAsset.h SkeletalBodySetups has no Blueprint/Edit flags). */
UCLASS()
class UHeroPhysicsTools : public UToolsetDefinition
{
	GENERATED_BODY()

public:
	/** Builds a NEW physics asset at DestAssetPath (it must not exist; the caller deletes, with its referencer guard)
	    for the skeletal mesh at MeshPath, previewing that mesh but never assigned to it (the stock mesh and its stock
	    physics asset stay untouched). SpecJson:
	      {"bodies": [{"bone", "physics_type": "simulated"|"kinematic", "elements": [
	                     {"type": "capsule", "center": [x,y,z], "rotation_xyzw": [x,y,z,w], "radius_cm", "length_cm"}
	                   | {"type": "capsule", "aim": bone, "radius_cm"}
	                   | {"type": "sphere", "center": [x,y,z]|null, "radius_cm"}]}],
	       "fixed_step_s": s (the asset's solver step),
	       "constraints": [{"child", "parent", "angular": "free"} | {"child", "parent", "swing1_deg", "swing2_deg",
	                        "twist_deg"}]}
	    Element geometry is in the bone's frame; an aimed capsule runs from its bone to its aim bone in the reference
	    pose. Collision is disabled between every pair of bodies. Each constraint sits at the child's origin
	    (SnapTransformsToDefault, as the engine's own
	    generator does) with limited angular motion and disables collision between its pair.
	    Returns a JSON read-back: {"ok", "path", "bodies": [{"bone", "index", "shape", "radius", "length"}],
	    "constraints": [{"child", "parent"}], "error"?}. */
	UFUNCTION(BlueprintCallable, Category = "MotionTerminal|HitReaction", meta = (AICallable, AIAccessMode = "Write"))
	static FString BuildPhysicsAsset(const FString& MeshPath, const FString& DestAssetPath, const FString& SpecJson);

	/** Read-back of an existing physics asset: its bodies (bone, shapes) and constraints (child, parent, limits). */
	UFUNCTION(BlueprintCallable, Category = "MotionTerminal|HitReaction", meta = (AICallable, AIAccessMode = "Read"))
	static FString DescribePhysicsAsset(const FString& PhysicsAssetPath);
};
