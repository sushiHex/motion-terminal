#include "HeroDynamicsTools.h"

#include "ChaosCloth/ChaosClothConfig.h"
#include "ChaosCloth/ChaosClothingSimulationInteractor.h"
#include "ClothingAsset.h"
#include "ClothingSimulationInteractor.h"
#include "ClothingSystemRuntimeTypes.h"
#include "Rendering/SkeletalMeshLODRenderData.h"
#include "Rendering/SkeletalMeshRenderData.h"
#include "Components/SkeletalMeshComponent.h"
#include "Engine/SkeletalMesh.h"
#include "HeroToolJson.h"
#include "Materials/MaterialInterface.h"
#include "PhysicsEngine/PhysicsAsset.h"
#include "PointWeightMap.h"
#include "Rendering/RenderCommandPipes.h"
#include "RenderingThread.h"
#include "Rendering/ColorVertexBuffer.h"

#include UE_INLINE_GENERATED_CPP_BY_NAME(HeroDynamicsTools)

using HeroToolJson::Fail;
using HeroToolJson::ToJson;

namespace
{
	/** Each heat-shown mesh's last frame of per-vertex offsets from the animation (ApplyDisplacementHeat's motion). */
	TMap<TWeakObjectPtr<USkeletalMeshComponent>, TArray<FVector3f>> HeatLastOffsets;

	/** The LOD 0 render vertices' final component-space positions, as the GPU skin cache computes them: bone skinning
	    from the component's current bone matrices (ComputeSkinnedPositions: the Dynamics rig's and the reaction's bones
	    included), then each cloth section's vertices rebuilt from the Chaos simulation through the section's mapping and
	    blended in by the cloth weight (GpuSkinCacheComputeShader.usf:258-357). The render path is untouched (CPU
	    skinning crashed Lumen's card capture: docs/specs/2026-10-29-hit-reaction-design.md, the heatmap). Morph targets are not applied (the two compared meshes share them).
	    Skinned (optional): the positions before the cloth, this frame; ClothWeights (optional): each vertex's cloth blend
	    (0 where the cloth does not move it). */
	bool FinalPositions(USkeletalMeshComponent* Mesh, TArray<FVector3f>& Out, FString& Why,
	                    TArray<FVector3f>* Skinned = nullptr, TArray<float>* ClothWeights = nullptr)
	{
		const FSkeletalMeshRenderData* RenderData = Mesh->GetSkeletalMeshRenderData();
		const FSkinWeightVertexBuffer* Weights = Mesh->GetSkinWeightBuffer(0);
		if (!RenderData || RenderData->LODRenderData.Num() == 0 || !Weights)
		{
			Why = TEXT("no LOD 0 render data or skin weights");
			return false;
		}
		const FSkeletalMeshLODRenderData& LOD = RenderData->LODRenderData[0];
		TArray<FMatrix44f> RefToLocal;
		Mesh->GetCurrentRefToLocalMatrices(RefToLocal, 0);
		USkinnedMeshComponent::ComputeSkinnedPositions(Mesh, Out, RefToLocal, LOD, *Weights);
		if (Out.Num() == 0)
		{
			Why = TEXT("no skinned positions");
			return false;
		}
		if (Skinned)
		{
			*Skinned = Out;
		}
		if (ClothWeights)
		{
			ClothWeights->Init(0.f, Out.Num());
		}
		if (Mesh->ClothBlendWeight <= 0.f)
		{
			// renders skinned (as the engine's CPU path skips it, SkeletalMeshComponentPhysics.cpp:2696); reading its
			// cloth data would also make the mesh wait on its cloth task every frame (:3728)
			return true;
		}
		const TMap<int32, FClothSimulData>& Cloth = Mesh->GetCurrentClothingData_GameThread();
		const FVector3f Scale = FVector3f(Mesh->GetComponentTransform().GetScale3D().GetAbs());
		for (const FSkelMeshRenderSection& Section : LOD.RenderSections)
		{
			if (!Section.HasClothingData() || Section.ClothMappingDataLODs.Num() == 0)
			{
				continue;
			}
			const FClothSimulData* Sim = Cloth.Find(Section.CorrespondClothAssetIndex);
			if (!Sim || Sim->LODIndex != 0 || Sim->Positions.Num() == 0)
			{
				// a cloth section with no simulation this frame renders skinned (the GPU's bEnabled false), but a
				// simulating mesh that has none is a missed sample, not a rest pose
				if (Mesh->GetClothingSimulationInteractor())
				{
					Why = FString::Printf(TEXT("no cloth simulation data for asset %d this frame"), Section.CorrespondClothAssetIndex);
					return false;
				}
				continue;
			}
			const TArray<FMeshToMeshVertData>& Map = Section.ClothMappingDataLODs[0];
			const int32 NumVerts = (int32)Section.NumVertices;
			if (NumVerts == 0 || Map.Num() % NumVerts != 0)
			{
				Why = TEXT("a cloth mapping that does not divide into its section's vertices");
				return false;
			}
			const int32 K = Map.Num() / NumVerts;
			const FMatrix44f ClothToLocal(Sim->ComponentRelativeTransform.ToMatrixWithScale());
			for (int32 v = 0; v < NumVerts; ++v)
			{
				FVector3f SimPos = FVector3f::ZeroVector;
				float SumWeights = 0.f, ClothWeight = 0.f;
				for (int32 k = 0; k < K; ++k)
				{
					const FMeshToMeshVertData& M = Map[v * K + k];
					if (M.SourceMeshVertIndices[3] >= 0xFFFF)
					{
						continue;
					}
					const int32 I[3] = {M.SourceMeshVertIndices[0], M.SourceMeshVertIndices[1], M.SourceMeshVertIndices[2]};
					if (I[0] >= Sim->Positions.Num() || I[1] >= Sim->Positions.Num() || I[2] >= Sim->Positions.Num())
					{
						Why = TEXT("a cloth mapping index past the simulation's particles");
						return false;
					}
					ClothWeight += Mesh->ClothBlendWeight * (1.f - M.SourceMeshVertIndices[3] / 65535.f);
					const float W = K > 1 ? M.Weight : 1.f;
					SumWeights = K > 1 ? SumWeights + W : 1.f;
					const FVector4f& B = M.PositionBaryCoordsAndDist;
					const float Bary[3] = {B.X, B.Y, 1.f - B.X - B.Y};
					FVector3f P = FVector3f::ZeroVector;
					for (int32 c = 0; c < 3; ++c)
					{
						P += Bary[c] * (Sim->Positions[I[c]] + Sim->Normals[I[c]] * B.W * Scale[c]);
					}
					SimPos += W * P;
				}
				if (K > 1)
				{
					ClothWeight /= K;
				}
				if (SumWeights > 1e-4f)                 // nonzero only once a valid influence was summed
				{
					const FVector3f Local = ClothToLocal.TransformPosition(SimPos / SumWeights);
					FVector3f& Final = Out[Section.BaseVertexIndex + v];
					Final = FMath::Lerp(Final, Local, ClothWeight);
					if (ClothWeights)
					{
						(*ClothWeights)[Section.BaseVertexIndex + v] = ClothWeight;
					}
				}
			}
		}
		return true;
	}

	/** A LOD 0 render vertex's bone weights by skeleton bone name (normalised), through its section's bone map. */
	TMap<FName, float> VertexBones(const USkeletalMeshComponent* Mesh, const FSkelMeshRenderSection& Section,
	                               const FSkinWeightVertexBuffer& Weights, uint32 Vertex)
	{
		const FReferenceSkeleton& Skeleton = Mesh->GetSkinnedAsset()->GetRefSkeleton();
		TMap<FName, float> Out;
		float Sum = 0.f;
		for (uint32 k = 0; k < Weights.GetMaxBoneInfluences(); ++k)
		{
			const float W = Weights.GetBoneWeight(Vertex, k);
			const uint32 Local = Weights.GetBoneIndex(Vertex, k);
			if (W > 0.f && Section.BoneMap.IsValidIndex(Local))
			{
				Out.FindOrAdd(Skeleton.GetBoneName(Section.BoneMap[Local])) += W;
				Sum += W;
			}
		}
		for (TPair<FName, float>& Pair : Out)
		{
			Pair.Value /= Sum;
		}
		return Out;
	}

	FName Dominant(const TMap<FName, float>& Bones)
	{
		FName Best;
		float BestW = -1.f;
		for (const TPair<FName, float>& Pair : Bones)
		{
			if (Pair.Value > BestW)
			{
				Best = Pair.Key;
				BestW = Pair.Value;
			}
		}
		return Best;
	}

	/** The section owning a LOD 0 render vertex. */
	const FSkelMeshRenderSection* SectionOf(const FSkeletalMeshLODRenderData& LOD, uint32 Vertex)
	{
		for (const FSkelMeshRenderSection& Section : LOD.RenderSections)
		{
			if (Vertex >= Section.BaseVertexIndex && Vertex < Section.BaseVertexIndex + Section.NumVertices)
			{
				return &Section;
			}
		}
		return nullptr;
	}

	/** Closest point to P on triangle ABC (Ericson, Real-Time Collision Detection 5.1.5). */
	FVector3f ClosestOnTriangle(const FVector3f& P, const FVector3f& A, const FVector3f& B, const FVector3f& C)
	{
		const FVector3f AB = B - A, AC = C - A, AP = P - A;
		const float D1 = AB | AP, D2 = AC | AP;
		if (D1 <= 0.f && D2 <= 0.f) return A;
		const FVector3f BP = P - B;
		const float D3 = AB | BP, D4 = AC | BP;
		if (D3 >= 0.f && D4 <= D3) return B;
		const float VC = D1 * D4 - D3 * D2;
		if (VC <= 0.f && D1 >= 0.f && D3 <= 0.f) return A + AB * (D1 / (D1 - D3));
		const FVector3f CP = P - C;
		const float D5 = AB | CP, D6 = AC | CP;
		if (D6 >= 0.f && D5 <= D6) return C;
		const float VB = D5 * D2 - D1 * D6;
		if (VB <= 0.f && D2 >= 0.f && D6 <= 0.f) return A + AC * (D2 / (D2 - D6));
		const float VA = D3 * D6 - D5 * D4;
		if (VA <= 0.f && (D4 - D3) >= 0.f && (D5 - D6) >= 0.f) return B + (C - B) * ((D4 - D3) / ((D4 - D3) + (D5 - D6)));
		const float Denom = 1.f / (VA + VB + VC);
		return A + AB * (VB * Denom) + AC * (VC * Denom);
	}

	/** Each limb bone a cloth collides with (BodyBones[i], the bone of a collision body) and its leaf children (the twist
	    helpers that carry the same limb's skin), mapped to its limb i; never an excluded bone (a Dynamics chain). A solid
	    limb is the only surface whose back side is an inside: an open shell (a blade, an armour plate) read 12 cm
	    "inside" behind its face (Aurora's weapon). */
	TMap<FName, int32> LimbBones(const FReferenceSkeleton& Skeleton, const TArray<FName>& BodyBones, const TSet<FName>& Excluded)
	{
		TMap<FName, int32> Out;
		for (int32 b = 0; b < Skeleton.GetNum(); ++b)
		{
			const FName Name = Skeleton.GetBoneName(b);
			const int32 Parent = Skeleton.GetParentIndex(b);
			TArray<int32> Children;
			int32 Limb = BodyBones.IndexOfByKey(Name);
			if (Limb == INDEX_NONE && Parent != INDEX_NONE && Skeleton.GetDirectChildBones(b, Children) == 0)
			{
				Limb = BodyBones.IndexOfByKey(Skeleton.GetBoneName(Parent));
			}
			if (Limb != INDEX_NONE && !Excluded.Contains(Name))
			{
				Out.Add(Name, Limb);
			}
		}
		return Out;
	}

	/** Distance from P to segment A-B. */
	float SegmentDistance(const FVector3f& P, const FVector3f& A, const FVector3f& B)
	{
		const FVector3f AB = B - A;
		const float T = FMath::Clamp(((P - A) | AB) / FMath::Max(AB.SizeSquared(), 1e-6f), 0.f, 1.f);
		return FVector3f::Distance(P, A + AB * T);
	}

	/** What never changes of the body a cloth can penetrate, per mesh and limb set (built once: rebuilding it every
	    frame was most of a 245 ms frame): the triangles of the enabled non-cloth sections whose three
	    vertices the limb bones dominate (LimbBones), each with its outward orientation (the reference-pose face against
	    the stored vertex normals, so a mirrored or flipped winding still reads outward), its witness bone and its limb,
	    and each limb's vertices. */
	struct FLimbTopology
	{
		TArray<FIntVector> Tris;
		TArray<int8> Outward;
		TArray<FName> Bone;                                 // the triangle's first vertex's dominant bone (a witness)
		TArray<int32> Limb;
		TArray<TArray<int32>> LimbVerts;
	};

	const FLimbTopology* LimbTopology(const USkeletalMeshComponent* Mesh, const FSkeletalMeshLODRenderData& LOD,
	                                  const FSkinWeightVertexBuffer& Weights, const TArray<FName>& BodyBones,
	                                  const TArray<FName>& ExcludeBones, FString& Why)
	{
		static TMap<FString, FLimbTopology> Cache;
		TArray<FString> Excluded;
		for (const FName& B : ExcludeBones)
		{
			Excluded.Add(B.ToString());
		}
		Excluded.Sort();
		// the LOD render data's address: a reimport or a rebuild of the mesh makes new render data, so a new entry
		FString CacheKey = FString::Printf(TEXT("%s|%p|%u|"), *Mesh->GetSkinnedAsset()->GetPathName(), &LOD, LOD.GetNumVertices());
		for (const FName& B : BodyBones)
		{
			CacheKey += B.ToString() + TEXT(",");
		}
		CacheKey += TEXT("|") + FString::Join(Excluded, TEXT(","));
		if (const FLimbTopology* Hit = Cache.Find(CacheKey))
		{
			return Hit;
		}
		TArray<uint32> Indices;
		LOD.MultiSizeIndexContainer.GetIndexBuffer(Indices);
		if (Indices.Num() == 0)
		{
			Why = TEXT("no CPU index buffer for LOD 0");
			return nullptr;
		}
		const TMap<FName, int32> Limbs = LimbBones(Mesh->GetSkinnedAsset()->GetRefSkeleton(), BodyBones, TSet<FName>(ExcludeBones));
		const FPositionVertexBuffer& Ref = LOD.StaticVertexBuffers.PositionVertexBuffer;
		const FStaticMeshVertexBuffer& Tangents = LOD.StaticVertexBuffers.StaticMeshVertexBuffer;
		FLimbTopology Topo;
		Topo.LimbVerts.SetNum(BodyBones.Num());
		TMap<uint32, FName> DominantOf;
		TArray<TSet<int32>> Seen;          // per limb: a vertex two limbs share belongs to both (its radius)
		Seen.SetNum(BodyBones.Num());
		for (const FSkelMeshRenderSection& Section : LOD.RenderSections)
		{
			if (Section.HasClothingData() || Section.bDisabled)
			{
				continue;
			}
			for (uint32 t = 0; t < Section.NumTriangles; ++t)
			{
				const uint32 Base = Section.BaseIndex + 3 * t;
				if (Base + 2 >= (uint32)Indices.Num())
				{
					break;
				}
				const FIntVector T(Indices[Base], Indices[Base + 1], Indices[Base + 2]);
				bool bKeep = true;
				for (int32 c = 0; c < 3 && bKeep; ++c)
				{
					if ((uint32)T[c] >= LOD.GetNumVertices())
					{
						Why = TEXT("an index past the render vertices");
						return nullptr;
					}
					FName* Known = DominantOf.Find(T[c]);
					const FName B = Known ? *Known : DominantOf.Add(T[c], Dominant(VertexBones(Mesh, Section, Weights, T[c])));
					bKeep = Limbs.Contains(B);
				}
				if (!bKeep)
				{
					continue;
				}
				const FVector3f FaceRef = (Ref.VertexPosition(T[1]) - Ref.VertexPosition(T[0]))
					^ (Ref.VertexPosition(T[2]) - Ref.VertexPosition(T[0]));
				const FVector4f N = Tangents.VertexTangentZ(T[0]) + Tangents.VertexTangentZ(T[1]) + Tangents.VertexTangentZ(T[2]);
				const int32 L = Limbs[DominantOf[T[0]]];
				Topo.Tris.Add(T);
				Topo.Outward.Add((FaceRef | FVector3f(N.X, N.Y, N.Z)) >= 0.f ? 1 : -1);
				Topo.Bone.Add(DominantOf[T[0]]);
				Topo.Limb.Add(L);
				for (int32 c = 0; c < 3; ++c)
				{
					if (!Seen[L].Contains(T[c]))
					{
						Seen[L].Add(T[c]);
						Topo.LimbVerts[L].Add(T[c]);
					}
				}
			}
		}
		if (Topo.Tris.Num() == 0)
		{
			Why = TEXT("no body triangles: no enabled non-cloth triangle is all on the limb bones");
			return nullptr;
		}
		return &Cache.Add(CacheKey, MoveTemp(Topo));
	}

	/** The body a cloth can penetrate, this frame: the limb topology at the frame's positions, each limb's axis (its
	    collision body's bone to its aim) and its radius about it (its farthest vertex), the triangles hashed into a
	    uniform grid. */
	struct FBodySurface
	{
		static constexpr float Cell = 3.f;
		const FLimbTopology* Topo = nullptr;
		TArray<TPair<FVector3f, FVector3f>> Axes;           // per limb: its bone and its aim, component space
		TArray<float> Radius;                               // per limb: its farthest vertex from its axis
		TMap<FIntVector, TArray<int32>> Grid;
		const TArray<FVector3f>* Pos = nullptr;

		static FIntVector Key(const FVector3f& P)
		{
			return FIntVector(FMath::FloorToInt(P.X / Cell), FMath::FloorToInt(P.Y / Cell), FMath::FloorToInt(P.Z / Cell));
		}

		void Build(const FLimbTopology& InTopo, const TArray<FVector3f>& Frame)
		{
			Topo = &InTopo;
			Pos = &Frame;
			Radius.Init(0.f, Axes.Num());
			for (int32 L = 0; L < Axes.Num(); ++L)
			{
				for (int32 V : Topo->LimbVerts[L])
				{
					Radius[L] = FMath::Max(Radius[L], SegmentDistance(Frame[V], Axes[L].Key, Axes[L].Value));
				}
			}
			for (int32 i = 0; i < Topo->Tris.Num(); ++i)
			{
				const FIntVector& T = Topo->Tris[i];
				FBox3f Box(ForceInit);
				for (int32 c = 0; c < 3; ++c)
				{
					Box += Frame[T[c]];
				}
				const FIntVector Lo = Key(Box.Min), Hi = Key(Box.Max);
				for (int32 x = Lo.X; x <= Hi.X; ++x)
					for (int32 y = Lo.Y; y <= Hi.Y; ++y)
						for (int32 z = Lo.Z; z <= Hi.Z; ++z)
							Grid.FindOrAdd(FIntVector(x, y, z)).Add(i);
			}
		}

		/** How deep P is behind the closest body triangle (negative: in front), or false when P is clear of every
		    limb: farther from each limb's axis than its radius. An inside needs d(P, axis) < d(Q, axis) <= Radius, so a
		    point outside every limb's capsule cannot be one and is not searched. The search reaches 2 x Radius + the
		    axis's length (two points within Radius of one segment are no farther apart): the radius alone does not bound
		    |P - Q| (a point inside could sit farther than the radius from its closest face). */
		bool Depth(const FVector3f& P, float& OutDepth, int32& OutTri, FVector3f& OutClosest) const
		{
			float SearchCm = 0.f;
			for (int32 L = 0; L < Axes.Num(); ++L)
			{
				if (SegmentDistance(P, Axes[L].Key, Axes[L].Value) <= Radius[L])
				{
					SearchCm = FMath::Max(SearchCm, 2.f * Radius[L] + FVector3f::Distance(Axes[L].Key, Axes[L].Value));
				}
			}
			if (SearchCm <= 0.f)
			{
				return false;
			}
			const TArray<FIntVector>& Tris = Topo->Tris;
			const FIntVector C = Key(P);
			const int32 Rings = FMath::CeilToInt(SearchCm / Cell) + 1;
			float Best = TNumericLimits<float>::Max();
			OutTri = INDEX_NONE;
			for (int32 r = 1; r <= Rings; ++r)
			{
				for (int32 x = -r; x <= r; ++x)
					for (int32 y = -r; y <= r; ++y)
						for (int32 z = -r; z <= r; ++z)
						{
							if (FMath::Max3(FMath::Abs(x), FMath::Abs(y), FMath::Abs(z)) != r && r > 1)
							{
								continue;                   // only the new shell: the inner cells were searched
							}
							const TArray<int32>* Bucket = Grid.Find(C + FIntVector(x, y, z));
							if (!Bucket)
							{
								continue;
							}
							for (int32 i : *Bucket)
							{
								const FIntVector& T = Tris[i];
								const FVector3f Q = ClosestOnTriangle(P, (*Pos)[T.X], (*Pos)[T.Y], (*Pos)[T.Z]);
								const float D = FVector3f::DistSquared(P, Q);
								if (D < Best)
								{
									Best = D;
									OutTri = i;
									OutClosest = Q;
								}
							}
						}
				// every triangle closer than (r - 1) cells has been seen once the shell r is done
				if (OutTri != INDEX_NONE && FMath::Sqrt(Best) <= (r - 1) * Cell)
				{
					break;
				}
			}
			if (OutTri == INDEX_NONE || FMath::Sqrt(Best) > SearchCm)
			{
				return false;
			}
			const FIntVector& T = Tris[OutTri];
			const FVector3f Normal = (((*Pos)[T.Y] - (*Pos)[T.X]) ^ ((*Pos)[T.Z] - (*Pos)[T.X])) * Topo->Outward[OutTri];
			const float Dist = FMath::Sqrt(Best);
			// Inside: behind the face AND nearer the limb's axis than the surface point it was matched to (a limb
			// surrounds its bone). Past a skin section's open end (where a boot or an armour plate begins) the closest
			// feature is an edge whose facing says nothing: a point 20 cm out read 20 cm "inside" a calf.
			const TPair<FVector3f, FVector3f>& Axis = Axes[Topo->Limb[OutTri]];
			const bool bInside = ((P - OutClosest) | Normal) < 0.f
				&& SegmentDistance(P, Axis.Key, Axis.Value) < SegmentDistance(OutClosest, Axis.Key, Axis.Value);
			OutDepth = bInside ? Dist : -Dist;
			return true;
		}
	};
}

FString UHeroDynamicsTools::SetClothCollision(USkeletalMeshComponent* Mesh, bool bContinuous, int32 Substeps)
{
	if (!Mesh || !Mesh->GetSkeletalMeshAsset())
	{
		return Fail(TEXT("no skeletal mesh"));
	}
	UClothingSimulationInteractor* Sim = Mesh->GetClothingSimulationInteractor();
	if (!Sim)
	{
		return Fail(TEXT("the mesh has no clothing simulation interactor (no clothing, or not simulating yet)"));
	}
	const TSharedRef<FJsonObject> Assets = MakeShared<FJsonObject>();
	for (UClothingAssetBase* Base : Mesh->GetSkeletalMeshAsset()->GetMeshClothingAssets())
	{
		const UClothingAssetCommon* Asset = Cast<UClothingAssetCommon>(Base);
		if (!Asset)
		{
			continue;
		}
		const UChaosClothConfig* Config = nullptr;
		for (const TPair<FName, TObjectPtr<UClothConfigBase>>& Pair : Asset->ClothConfigs)
		{
			Config = Config ? Config : Cast<UChaosClothConfig>(Pair.Value.Get());
		}
		UChaosClothingInteractor* Cloth = Cast<UChaosClothingInteractor>(Sim->GetClothingInteractor(Asset->GetFName()));
		if (!Config || !Cloth)
		{
			return Fail(FString::Printf(TEXT("clothing asset %s has no Chaos config or no interactor"), *Asset->GetName()));
		}
		Cloth->SetCollision(Config->CollisionThickness, Config->FrictionCoefficient, bContinuous, Config->SelfCollisionThickness);
		const TSharedRef<FJsonObject> Entry = MakeShared<FJsonObject>();
		Entry->SetNumberField(TEXT("thickness"), Config->CollisionThickness);
		Entry->SetNumberField(TEXT("friction"), Config->FrictionCoefficient);
		Entry->SetNumberField(TEXT("self_thickness"), Config->SelfCollisionThickness);
		Entry->SetBoolField(TEXT("ccd"), bContinuous);
		for (const TPair<FName, TObjectPtr<UClothConfigBase>>& Pair : Asset->ClothConfigs)   // the shared one among them
		{
			if (const UChaosClothSharedSimConfig* Shared = Cast<UChaosClothSharedSimConfig>(Pair.Value.Get()))
			{
				Entry->SetNumberField(TEXT("asset_substeps"), Shared->SubdivisionCount);
				Entry->SetNumberField(TEXT("asset_iterations"), Shared->IterationCount);
			}
		}
		Assets->SetObjectField(Asset->GetName(), Entry);
	}
	if (Assets->Values.Num() == 0)
	{
		return Fail(TEXT("the mesh has no Chaos clothing assets"));
	}
	if (Substeps > 0)
	{
		Sim->SetNumSubsteps(Substeps);                      // the whole solver: every asset on the mesh
	}
	const TSharedRef<FJsonObject> Obj = MakeShared<FJsonObject>();
	Obj->SetBoolField(TEXT("ok"), true);
	Obj->SetNumberField(TEXT("substeps"), Substeps);
	Obj->SetObjectField(TEXT("assets"), Assets);
	return ToJson(Obj);
}

FString UHeroDynamicsTools::BeginDisplacementHeat(USkeletalMeshComponent* Shown, USkeletalMeshComponent* Reference,
                                                  UMaterialInterface* HeatMaterial)
{
	if (!Shown || !Reference || !HeatMaterial)
	{
		return Fail(TEXT("needs two meshes and a heat material"));
	}
	if (Shown->GetSkeletalMeshAsset() != Reference->GetSkeletalMeshAsset())
	{
		return Fail(TEXT("the two meshes use different skeletal mesh assets: their vertices do not correspond"));
	}
	if (Reference->ClothBlendWeight > 0.f && Reference->GetClothingSimulationInteractor())
	{
		return Fail(TEXT("the reference mesh's cloth is not blended out (ClothBlendWeight > 0): it is not the animation"));
	}
	for (USkeletalMeshComponent* Mesh : {Shown, Reference})
	{
		Mesh->SetForcedLOD(1);                              // 1-based: LOD 0, so both have the same render vertices
	}
	// Only the shown mesh's cloth is read (the reference's is blended out): the wait serialises its cloth task with
	// the game thread so the data is this frame's.
	Shown->bWaitForParallelClothTask = true;
	// over the mesh's own materials: the overlay pass reuses each section's vertex factory, so it reads the same vertex
	// colours (SkeletalMeshSceneProxy.cpp:839)
	Shown->SetOverlayMaterial(HeatMaterial);
	// The heat's vertex colour buffer, made ONCE here (it flushes and rebuilds the render state,
	// SkinnedMeshComponent.cpp:5290-5292); each frame then rewrites its contents in place (ApplyDisplacementHeat).
	const FSkeletalMeshRenderData* RenderData = Shown->GetSkeletalMeshRenderData();
	if (!RenderData || RenderData->LODRenderData.Num() == 0)
	{
		return Fail(TEXT("no LOD 0 render data"));
	}
	TArray<FColor> Zeros;
	Zeros.Init(FColor(0, 0, 0, 255), RenderData->LODRenderData[0].StaticVertexBuffers.PositionVertexBuffer.GetNumVertices());
	Shown->SetVertexColorOverride(0, Zeros);
	HeatLastOffsets.Remove(Shown);                      // a new take's motion starts from its own first frame
	const TSharedRef<FJsonObject> Obj = MakeShared<FJsonObject>();
	Obj->SetBoolField(TEXT("ok"), true);
	Obj->SetNumberField(TEXT("vertices"), Zeros.Num());
	return ToJson(Obj);
}

FString UHeroDynamicsTools::ApplyDisplacementHeat(USkeletalMeshComponent* Shown, USkeletalMeshComponent* Reference,
                                                  float ScaleCm)
{
	if (!Shown || !Reference || ScaleCm <= 0.f)
	{
		return Fail(TEXT("needs two meshes and a positive scale"));
	}
	TArray<FVector3f> A, B;
	TArray<float> ClothWeights;
	FString Why;
	// a frame that cannot be measured is NOT neutral heat: it is no measurement
	if (!FinalPositions(Shown, A, Why, nullptr, &ClothWeights) || !FinalPositions(Reference, B, Why))
	{
		return Fail(Why);
	}
	if (A.Num() != B.Num())
	{
		return Fail(FString::Printf(TEXT("the meshes' vertices do not correspond (%d / %d)"), A.Num(), B.Num()));
	}
	// Re-read every frame, never cached: InitLODInfos and ClearVertexColorOverride free it.
	FColorVertexBuffer* Buffer = Shown->LODInfo.IsValidIndex(0) ? Shown->LODInfo[0].OverrideVertexColors : nullptr;
	if (!Buffer || (int32)Buffer->GetNumVertices() != A.Num())
	{
		return Fail(TEXT("the heat colour buffer is missing or does not match the mesh (BeginDisplacementHeat makes it)"));
	}
	TArray<FColor> Colors;
	Colors.SetNumUninitialized(A.Num());
	float Max = 0.f;
	// The cloth's motion relative to the animation: per cloth-moved vertex, how far its offset from the animation moved
	// since the last frame, as an RMS (a sum of squares: panels swinging opposite ways never
	// cancel). The last frame's offsets are kept per shown mesh; BeginDisplacementHeat clears them.
	TArray<FVector3f>& Last = HeatLastOffsets.FindOrAdd(Shown);
	const bool bHasLast = Last.Num() == A.Num();
	Last.SetNum(A.Num());
	// The ripple: that motion less the cloth's common motion this frame (its mean), what is left when the garment
	// moves as one sheet (a damping that kept the swing and lost the folds looked "floaty and rigid").
	double MotionSq = 0.0, OffsetSq = 0.0;
	FVector3d MotionSum = FVector3d::ZeroVector;
	int32 ClothCount = 0;
	for (int32 i = 0; i < A.Num(); ++i)
	{
		const float D = FVector3f::Distance(A[i], B[i]);
		Colors[i] = FColor((uint8)FMath::RoundToInt(255.f * FMath::Clamp(D / ScaleCm, 0.f, 1.f)), 0, 0, 255);
		Max = FMath::Max(Max, D);
		const FVector3f Offset = A[i] - B[i];
		if (ClothWeights.IsValidIndex(i) && ClothWeights[i] > 0.f)
		{
			if (bHasLast)
			{
				const FVector3f Step = Offset - Last[i];
				MotionSq += Step.SizeSquared();
				MotionSum += FVector3d(Step);
			}
			OffsetSq += Offset.SizeSquared();
			++ClothCount;
		}
		Last[i] = Offset;
	}
	// sum |s - m|^2 = sum |s|^2 - n |m|^2 (m the mean step)
	const double RippleSq = ClothCount > 0
		? FMath::Max(0.0, MotionSq - MotionSum.SizeSquared() / ClothCount) : 0.0;
	// Rewritten in place on the render thread, on the pipe the buffer was made on: the same resource, so the vertex
	// factory and skin cache keep it, with no flush and no render state rebuild.
	ENQUEUE_RENDER_COMMAND(MotionTerminalHeatUpload)(UE::RenderCommandPipe::SkeletalMesh,
		[Buffer, Packed = MoveTemp(Colors)](FRHICommandList& RHICmdList)
		{
			if (!Buffer->VertexBufferRHI)
			{
				return;
			}
			const uint32 Bytes = Packed.Num() * sizeof(FColor);
			void* Dest = RHICmdList.LockBuffer(Buffer->VertexBufferRHI, 0, Bytes, RLM_WriteOnly);
			FMemory::Memcpy(Dest, Packed.GetData(), Bytes);
			RHICmdList.UnlockBuffer(Buffer->VertexBufferRHI);
		});
	const TSharedRef<FJsonObject> Obj = MakeShared<FJsonObject>();
	Obj->SetBoolField(TEXT("ok"), true);
	Obj->SetNumberField(TEXT("vertices"), A.Num());
	Obj->SetNumberField(TEXT("max_cm"), Max);
	Obj->SetNumberField(TEXT("cloth_vertices"), ClothCount);
	if (ClothCount > 0)
	{
		Obj->SetNumberField(TEXT("cloth_rms_cm"), FMath::Sqrt(OffsetSq / ClothCount));
		if (bHasLast)
		{
			Obj->SetNumberField(TEXT("cloth_motion_cm"), FMath::Sqrt(MotionSq / ClothCount));
			Obj->SetNumberField(TEXT("cloth_ripple_cm"), FMath::Sqrt(RippleSq / ClothCount));
		}
	}
	return ToJson(Obj);
}

FString UHeroDynamicsTools::MeasureClothPenetration(USkeletalMeshComponent* Mesh, const TArray<FName>& BodyBones,
                                                    const TArray<FName>& Aims, const TArray<FName>& ExcludeBones,
                                                    float TolCm)
{
	if (!Mesh || !Mesh->GetSkinnedAsset() || TolCm < 0.f)
	{
		return Fail(TEXT("needs a skeletal mesh and a tolerance >= 0"));
	}
	TArray<FVector3f> Final, Skinned;
	TArray<float> Cloth;
	FString Why;
	if (!FinalPositions(Mesh, Final, Why, &Skinned, &Cloth))
	{
		return Fail(Why);
	}
	const FSkeletalMeshLODRenderData& LOD = Mesh->GetSkeletalMeshRenderData()->LODRenderData[0];
	FBodySurface Body;
	const FReferenceSkeleton& Skeleton = Mesh->GetSkinnedAsset()->GetRefSkeleton();
	if (BodyBones.Num() != Aims.Num())
	{
		return Fail(TEXT("one aim per body bone"));
	}
	for (int32 i = 0; i < BodyBones.Num(); ++i)
	{
		const int32 A = Skeleton.FindBoneIndex(BodyBones[i]), B = Skeleton.FindBoneIndex(Aims[i]);
		if (A == INDEX_NONE || B == INDEX_NONE || !Mesh->GetComponentSpaceTransforms().IsValidIndex(FMath::Max(A, B)))
		{
			return Fail(FString::Printf(TEXT("limb %s -> %s is not on the skeleton"), *BodyBones[i].ToString(), *Aims[i].ToString()));
		}
		Body.Axes.Emplace(FVector3f(Mesh->GetComponentSpaceTransforms()[A].GetLocation()),
		                  FVector3f(Mesh->GetComponentSpaceTransforms()[B].GetLocation()));
	}
	const FLimbTopology* Topo = LimbTopology(Mesh, LOD, *Mesh->GetSkinWeightBuffer(0), BodyBones, ExcludeBones, Why);
	if (!Topo)
	{
		return Fail(Why);
	}
	Body.Build(*Topo, Final);
	// Per simulated vertex: how deep the cloth is inside the body, less how deep the same vertex already was, skinned
	// before the cloth, against the same body this frame (an overlap the art already has at that vertex is its
	// allowance, and only its own: one overlap never pays for another).
	int32 Tested = 0, Clear = 0, Over = 0, Worst = INDEX_NONE, WorstTri = INDEX_NONE;
	float MaxAdded = 0.f, MaxRaw = 0.f, WorstRaw = 0.f, WorstBase = 0.f;
	FVector3f WorstClosest = FVector3f::ZeroVector;
	for (int32 v = 0; v < Final.Num(); ++v)
	{
		if (Cloth[v] <= 0.f)
		{
			continue;
		}
		++Tested;
		float Raw, Base;
		int32 Tri, BaseTri;
		FVector3f Closest, BaseClosest;
		if (!Body.Depth(Final[v], Raw, Tri, Closest) || Raw <= 0.f)
		{
			++Clear;                    // outside every limb's reach, or in front of the closest surface
			continue;
		}
		// the allowance only for a vertex that is inside: the same vertex skinned before the cloth, same body
		const float Allowed = Body.Depth(Skinned[v], Base, BaseTri, BaseClosest) ? FMath::Max(0.f, Base) : 0.f;
		const float Added = FMath::Max(0.f, Raw - Allowed);
		MaxRaw = FMath::Max(MaxRaw, Raw);
		Over += Added > TolCm ? 1 : 0;
		if (Added > MaxAdded)
		{
			MaxAdded = Added;
			Worst = v;
			WorstTri = Tri;
			WorstRaw = Raw;
			WorstBase = Allowed;
			WorstClosest = Closest;
		}
	}
	const TSharedRef<FJsonObject> Obj = MakeShared<FJsonObject>();
	Obj->SetBoolField(TEXT("ok"), true);
	Obj->SetNumberField(TEXT("tested"), Tested);
	Obj->SetNumberField(TEXT("clear"), Clear);
	Obj->SetNumberField(TEXT("body_triangles"), Topo->Tris.Num());
	TArray<TSharedPtr<FJsonValue>> Radii;                 // each limb's radius this frame: what the admission reads
	for (float R : Body.Radius)
	{
		Radii.Add(MakeShared<FJsonValueNumber>(R));
	}
	Obj->SetArrayField(TEXT("limb_radius_cm"), Radii);
	Obj->SetNumberField(TEXT("over_tol"), Over);
	Obj->SetNumberField(TEXT("max_cm"), MaxAdded);
	Obj->SetNumberField(TEXT("max_raw_cm"), MaxRaw);
	if (Worst != INDEX_NONE)
	{
		const TSharedRef<FJsonObject> W = MakeShared<FJsonObject>();
		const FSkelMeshRenderSection* Section = SectionOf(LOD, Worst);
		auto Vec = [](const FVector3f& P) {
			return TArray<TSharedPtr<FJsonValue>>{MakeShared<FJsonValueNumber>(P.X), MakeShared<FJsonValueNumber>(P.Y),
			                                      MakeShared<FJsonValueNumber>(P.Z)};
		};
		W->SetNumberField(TEXT("vertex"), Worst);
		W->SetNumberField(TEXT("section"), Section ? LOD.RenderSections.IndexOfByPredicate(
			[Section](const FSkelMeshRenderSection& S) { return &S == Section; }) : -1);
		W->SetStringField(TEXT("body_bone"), Topo->Bone[WorstTri].ToString());
		W->SetNumberField(TEXT("raw_cm"), WorstRaw);
		W->SetNumberField(TEXT("allowed_cm"), WorstBase);
		W->SetArrayField(TEXT("point"), Vec(Final[Worst]));
		W->SetArrayField(TEXT("closest"), Vec(WorstClosest));
		Obj->SetObjectField(TEXT("worst"), W);
	}
	return ToJson(Obj);
}

namespace
{
	/** Reference-pose component-space positions of the LOD 0 render vertices of the enabled sections that are not cloth,
	    among them those Keep(Section, vertex) accepts, as a flat [x0, y0, z0, x1, ...] (mm-rounded). */
	template <typename FKeep>
	TArray<TSharedPtr<FJsonValue>> SectionPoints(const FSkeletalMeshLODRenderData& LOD, FKeep Keep)
	{
		TArray<TSharedPtr<FJsonValue>> Points;
		for (const FSkelMeshRenderSection& Section : LOD.RenderSections)
		{
			if (Section.HasClothingData() || Section.bDisabled)
			{
				continue;
			}
			for (uint32 v = Section.BaseVertexIndex; v < Section.BaseVertexIndex + Section.NumVertices; ++v)
			{
				if (Keep(Section, v))
				{
					const FVector3f& P = LOD.StaticVertexBuffers.PositionVertexBuffer.VertexPosition(v);
					for (float c : {P.X, P.Y, P.Z})
					{
						Points.Add(MakeShared<FJsonValueNumber>(FMath::RoundToFloat(c * 1000.f) / 1000.f));
					}
				}
			}
		}
		return Points;
	}

	const FSkeletalMeshLODRenderData* Lod0(USkeletalMesh* SkeletalMesh)
	{
		const FSkeletalMeshRenderData* R = SkeletalMesh ? SkeletalMesh->GetResourceForRendering() : nullptr;
		return R && R->LODRenderData.Num() > 0 ? &R->LODRenderData[0] : nullptr;
	}

	TSharedRef<FJsonObject> PointsJson(const TArray<TSharedPtr<FJsonValue>>& Points)
	{
		const TSharedRef<FJsonObject> Obj = MakeShared<FJsonObject>();
		Obj->SetBoolField(TEXT("ok"), true);
		Obj->SetNumberField(TEXT("count"), Points.Num() / 3);
		Obj->SetArrayField(TEXT("xyz"), Points);
		return Obj;
	}
}

FString UHeroDynamicsTools::SkinPoints(USkeletalMesh* SkeletalMesh, const TArray<FName>& Region, float Dominance)
{
	const FSkeletalMeshLODRenderData* LOD = Lod0(SkeletalMesh);
	if (!LOD)
	{
		return Fail(TEXT("no skeletal mesh with LOD 0 render data"));
	}
	const FReferenceSkeleton& Skeleton = SkeletalMesh->GetRefSkeleton();
	const TSet<FName> Bones(Region);
	// the body's own surface: never a cloth section (otherwise the skin fit takes a dress for skin)
	return ToJson(PointsJson(SectionPoints(*LOD, [&](const FSkelMeshRenderSection& Section, uint32 v)
	{
		float Sum = 0.f, Mine = 0.f;
		for (uint32 k = 0; k < LOD->SkinWeightVertexBuffer.GetMaxBoneInfluences(); ++k)
		{
			const float W = LOD->SkinWeightVertexBuffer.GetBoneWeight(v, k);
			const uint32 Local = LOD->SkinWeightVertexBuffer.GetBoneIndex(v, k);
			if (W > 0.f && Section.BoneMap.IsValidIndex(Local))
			{
				Sum += W;
				Mine += Bones.Contains(Skeleton.GetBoneName(Section.BoneMap[Local])) ? W : 0.f;
			}
		}
		return Sum > 0.f && Mine / Sum >= Dominance;
	})));
}

FString UHeroDynamicsTools::GarmentPoints(USkeletalMesh* SkeletalMesh)
{
	if (!SkeletalMesh)
	{
		return Fail(TEXT("no skeletal mesh"));
	}
	// What Chaos simulates: each clothing asset's LOD 0 physical mesh (ChaosClothingSimulationSkeletalMesh.cpp:93),
	// not the render vertices mapped onto it, with each asset's own thickness and
	// physics asset (the cloth collides with it as well as with any added source, ChaosClothingSimulation.cpp:398)
	TArray<TSharedPtr<FJsonValue>> Assets;
	for (UClothingAssetBase* Base : SkeletalMesh->GetMeshClothingAssets())
	{
		const UClothingAssetCommon* Asset = Cast<UClothingAssetCommon>(Base);
		if (!Asset || Asset->LodData.Num() == 0)
		{
			continue;
		}
		const UChaosClothConfig* Config = nullptr;
		for (const TPair<FName, TObjectPtr<UClothConfigBase>>& Pair : Asset->ClothConfigs)
		{
			Config = Config ? Config : Cast<UChaosClothConfig>(Pair.Value.Get());
		}
		if (!Config)
		{
			return Fail(FString::Printf(TEXT("clothing asset %s has no Chaos config"), *Asset->GetName()));
		}
		const FClothPhysicalMeshData& Phys = Asset->LodData[0].PhysicalMeshData;
		const FPointWeightMap* MaxDistance = Phys.FindWeightMap(EWeightMapTargetCommon::MaxDistance);
		TArray<TSharedPtr<FJsonValue>> Xyz, Max;
		for (int32 i = 0; i < Phys.Vertices.Num(); ++i)
		{
			for (float c : {Phys.Vertices[i].X, Phys.Vertices[i].Y, Phys.Vertices[i].Z})
			{
				Xyz.Add(MakeShared<FJsonValueNumber>(FMath::RoundToFloat(c * 1000.f) / 1000.f));
			}
			const float M = MaxDistance && MaxDistance->Values.IsValidIndex(i) ? MaxDistance->Values[i] : 0.f;
			Max.Add(MakeShared<FJsonValueNumber>(FMath::RoundToFloat(M * 1000.f) / 1000.f));
		}
		const TSharedRef<FJsonObject> A = MakeShared<FJsonObject>();
		A->SetStringField(TEXT("name"), Asset->GetName());
		A->SetNumberField(TEXT("thickness_cm"), Config->CollisionThickness);
		A->SetStringField(TEXT("physics_asset"), Asset->PhysicsAsset ? Asset->PhysicsAsset->GetPathName() : FString());
		A->SetArrayField(TEXT("xyz"), Xyz);
		A->SetArrayField(TEXT("max_distance"), Max);
		Assets.Add(MakeShared<FJsonValueObject>(A));
	}
	const TSharedRef<FJsonObject> Obj = MakeShared<FJsonObject>();
	Obj->SetBoolField(TEXT("ok"), true);
	Obj->SetArrayField(TEXT("assets"), Assets);
	return ToJson(Obj);
}

FString UHeroDynamicsTools::ClothConfigs(USkeletalMesh* SkeletalMesh)
{
	if (!SkeletalMesh)
	{
		return Fail(TEXT("no skeletal mesh"));
	}
	const TSharedRef<FJsonObject> Assets = MakeShared<FJsonObject>();
	for (UClothingAssetBase* Base : SkeletalMesh->GetMeshClothingAssets())
	{
		const UClothingAssetCommon* Asset = Cast<UClothingAssetCommon>(Base);
		if (!Asset)
		{
			continue;
		}
		const TSharedRef<FJsonObject> Configs = MakeShared<FJsonObject>();
		for (const TPair<FName, TObjectPtr<UClothConfigBase>>& Pair : Asset->ClothConfigs)
		{
			const UClothConfigBase* Config = Pair.Value.Get();
			if (!Config)
			{
				continue;
			}
			const TSharedRef<FJsonObject> Props = MakeShared<FJsonObject>();
			for (TFieldIterator<FProperty> It(Config->GetClass()); It; ++It)
			{
				FString Text;
				It->ExportTextItem_Direct(Text, It->ContainerPtrToValuePtr<void>(Config), nullptr, nullptr, PPF_None);
				Props->SetStringField(It->GetName(), Text);
			}
			Configs->SetObjectField(Config->GetClass()->GetName(), Props);
		}
		Assets->SetObjectField(Asset->GetName(), Configs);
	}
	const TSharedRef<FJsonObject> Obj = MakeShared<FJsonObject>();
	Obj->SetBoolField(TEXT("ok"), true);
	Obj->SetObjectField(TEXT("assets"), Assets);
	return ToJson(Obj);
}
