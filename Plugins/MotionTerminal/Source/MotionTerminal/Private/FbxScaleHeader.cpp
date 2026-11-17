#include "FbxScaleHeader.h"

#include "HAL/FileManager.h"
#include "Misc/FileHelper.h"

namespace
{
	using namespace MotionTerminal;

	// How far an FBX-declared scale may sit from 1.0 before it counts as scaled. Deliberately looser
	// than ImportScaleCheck's 1e-4 ref-pose tolerance: these are exporter-written numbers that survive
	// a Maya->FBX round trip, and the tight check still runs post-import on the real reference pose.
	constexpr double kFbxScaleTolerance = 1e-3;

	// The enclosing Model record, so a scaled node can be reported by name rather than as an anonymous
	// number ("Lcl Scaling 1.5 on 'root'" is actionable in Maya; "some node is scaled" is not).
	struct FModelCtx
	{
		FString Name;
		FString Type;
	};

	bool IsIdentityScale(const FVector& S)
	{
		return FMath::Abs(S.X - 1.0) <= kFbxScaleTolerance
			&& FMath::Abs(S.Y - 1.0) <= kFbxScaleTolerance
			&& FMath::Abs(S.Z - 1.0) <= kFbxScaleTolerance;
	}

	bool IsNonUniformScale(const FVector& S)
	{
		return (FMath::Max3(S.X, S.Y, S.Z) - FMath::Min3(S.X, S.Y, S.Z)) > kFbxScaleTolerance;
	}

	// A scaled camera or light says nothing about rig hygiene — a Maya scene routinely carries both, and
	// flagging them would refuse a perfectly good skeletal export. Everything else (LimbNode, Mesh, Null,
	// or an untyped node) can carry baked joint scale or a rig-driver root scale, so it counts.
	bool IsSkeletalRelevantNodeType(const FString& Type)
	{
		return Type != TEXT("Camera") && Type != TEXT("Light");
	}

	void RecordScaledNode(FFbxScaleMetadata& Out, const FModelCtx& Model, const FVector& Scale)
	{
		if (IsIdentityScale(Scale)) { return; }
		if (!IsSkeletalRelevantNodeType(Model.Type)) { return; }
		FFbxScaledNode& Node = Out.ScaledNodes.AddDefaulted_GetRef();
		Node.Name = Model.Name.IsEmpty() ? TEXT("<unnamed>") : Model.Name;
		Node.Type = Model.Type;
		Node.Scale = Scale;
		Node.bNonUniform = IsNonUniformScale(Scale);
	}

	// FCString::Atod happily returns 0.0 for "A+" or "Number", which would silently read a garbage scale.
	// Validate the token shape first so a non-numeric field is *rejected*, never coerced to zero.
	bool TryParseDouble(const FString& Token, double& Out)
	{
		const FString T = Token.TrimStartAndEnd();
		if (T.IsEmpty()) { return false; }

		int32 i = (T[0] == TEXT('+') || T[0] == TEXT('-')) ? 1 : 0;
		bool bDigit = false, bDot = false, bExp = false;
		for (; i < T.Len(); ++i)
		{
			const TCHAR C = T[i];
			if (FChar::IsDigit(C)) { bDigit = true; continue; }
			if (C == TEXT('.') && !bDot && !bExp) { bDot = true; continue; }
			if ((C == TEXT('e') || C == TEXT('E')) && bDigit && !bExp)
			{
				bExp = true;
				if (i + 1 < T.Len() && (T[i + 1] == TEXT('+') || T[i + 1] == TEXT('-'))) { ++i; }
				continue;
			}
			return false;
		}
		if (!bDigit) { return false; }
		Out = FCString::Atod(*T);
		return true;
	}

	// Shared by both readers: apply one "P: <name> ... <values>" property record.
	void ApplyPropertyRecord(const FString& PropName, const TArray<double>& Numbers,
		const FModelCtx& Model, FFbxScaleMetadata& Out)
	{
		if (PropName == TEXT("UnitScaleFactor"))
		{
			if (Numbers.Num() >= 1)
			{
				Out.bHasUnitScaleFactor = true;
				Out.UnitScaleFactor = Numbers.Last();
			}
		}
		else if (PropName == TEXT("Lcl Scaling"))
		{
			if (Numbers.Num() >= 3)
			{
				const int32 N = Numbers.Num();
				RecordScaledNode(Out, Model, FVector(Numbers[N - 3], Numbers[N - 2], Numbers[N - 1]));
			}
		}
	}

	// ---------------------------------------------------------------------------------------------
	// Binary FBX ("Kaydara FBX Binary")
	// ---------------------------------------------------------------------------------------------

	struct FByteReader
	{
		const uint8* Data = nullptr;
		int64 Size = 0;
		int64 Pos = 0;
		bool bOk = true;

		bool Ensure(int64 Count)
		{
			if (!bOk || Count < 0 || Pos + Count > Size) { bOk = false; }
			return bOk;
		}
		template <typename T> T Read()
		{
			T V{};
			if (Ensure((int64)sizeof(T))) { FMemory::Memcpy(&V, Data + Pos, sizeof(T)); Pos += sizeof(T); }
			return V;
		}
		void Skip(int64 Count) { if (Ensure(Count)) { Pos += Count; } }

		// FBX writes a node's display name as "name\0\x01Type"; truncating at the first NUL yields the
		// display name and keeps embedded NULs out of the FString.
		FString Ansi(int64 Count)
		{
			if (!Ensure(Count)) { return FString(); }
			const ANSICHAR* Start = reinterpret_cast<const ANSICHAR*>(Data + Pos);
			int64 Len = 0;
			while (Len < Count && Start[Len] != '\0') { ++Len; }
			FString S = FString::ConstructFromPtrSize(Start, (int32)Len);
			Pos += Count;
			return S;
		}
	};

	struct FBinProp
	{
		bool bNumeric = false;
		double Num = 0.0;
		FString Str;
	};

	bool ReadBinaryProperty(FByteReader& R, FBinProp& Out)
	{
		const uint8 Code = R.Read<uint8>();
		if (!R.bOk) { return false; }
		switch (Code)
		{
		case 'Y': Out.Num = R.Read<int16>();          Out.bNumeric = true; break;
		case 'C': Out.Num = R.Read<uint8>();          Out.bNumeric = true; break;
		case 'I': Out.Num = R.Read<int32>();          Out.bNumeric = true; break;
		case 'F': Out.Num = R.Read<float>();          Out.bNumeric = true; break;
		case 'D': Out.Num = R.Read<double>();         Out.bNumeric = true; break;
		case 'L': Out.Num = (double)R.Read<int64>();  Out.bNumeric = true; break;

		case 'S':
		case 'R':
		{
			const uint32 Len = R.Read<uint32>();
			if (Code == 'S') { Out.Str = R.Ansi(Len); } else { R.Skip(Len); }
			break;
		}

		// Array payloads (geometry, keyframes). Skipped wholesale — the scale check reads declarations, not data.
		case 'f': case 'd': case 'l': case 'i': case 'b':
		{
			const uint32 ArrayLength = R.Read<uint32>();
			const uint32 Encoding = R.Read<uint32>();
			const uint32 CompressedLength = R.Read<uint32>();
			const int64 ElemSize = (Code == 'f' || Code == 'i') ? 4 : (Code == 'd' || Code == 'l') ? 8 : 1;
			R.Skip(Encoding != 0 ? (int64)CompressedLength : (int64)ArrayLength * ElemSize);
			break;
		}

		default:
			// Unknown typecode: the property walk is off the rails. The caller resyncs to the record's
			// declared property-list length, so this costs one node's properties, not the whole file.
			R.bOk = false;
			return false;
		}
		return R.bOk;
	}

	// A STRUCTURAL abort — the reader could not stay on the record grid.
	//
	// This is a different animal from the property-walk abort a few lines below, and the difference is
	// the whole reason this exists. A property abort is SURVIVABLE: the walk resyncs from the record's
	// own declared PropertyListLen, so one unparsed property costs one record and the rest of the file
	// is still visited. A structural abort has no resync — the reader does not know where the next
	// record begins, so EVERY record after this point is unvisited, including every Model that might
	// carry an Lcl Scaling. Without this counter nothing counted that, `ParseBinary` returned true as long as it had seen
	// one Model or a UnitScaleFactor before the break, and a truncated FBX was therefore
	// observationally identical to a clean one: ScaledNodes empty, both abort counters 0,
	// FixImportScale's two guards satisfied, rig imported unvetted.
	bool FailStructural(FByteReader& R, FFbxScaleMetadata& Out)
	{
		R.bOk = false;
		++Out.AbortedStructuralWalks;
		return false;
	}

	// Reads one node record and its children. Returns false at the null record that ends a node list
	// (or on a read error) — the caller stops iterating on false.
	bool ReadBinaryNode(FByteReader& R, bool b64, int32 Depth, const FModelCtx& Model, FFbxScaleMetadata& Out)
	{
		const int64 RecordStart = R.Pos;
		const uint64 EndOffset = b64 ? R.Read<uint64>() : (uint64)R.Read<uint32>();
		const uint64 NumProperties = b64 ? R.Read<uint64>() : (uint64)R.Read<uint32>();
		const uint64 PropertyListLen = b64 ? R.Read<uint64>() : (uint64)R.Read<uint32>();
		const uint8 NameLen = R.Read<uint8>();
		// EndOffset == 0 is checked FIRST among the failure-shaped exits below, but AFTER this one, and
		// that ordering is load-bearing: a header that could not be read at all has no EndOffset to
		// test, so it is a structural failure, not a list terminator.
		if (!R.bOk) { return FailStructural(R, Out); }
		if (EndOffset == 0) { return false; }                                  // null record: end of list
		if ((int64)EndOffset <= RecordStart || (int64)EndOffset > R.Size) { return FailStructural(R, Out); }

		const FString Name = R.Ansi(NameLen);
		if (!R.bOk) { return FailStructural(R, Out); }

		const int64 PropsStart = R.Pos;
		TArray<FBinProp> Props;
		// 64 is far above what any header record carries; it keeps a corrupt count from allocating wildly.
		if (NumProperties <= 64)
		{
			Props.Reserve((int32)NumProperties);
			for (uint64 i = 0; i < NumProperties; ++i)
			{
				FBinProp P;
				if (!ReadBinaryProperty(R, P)) { break; }
				Props.Add(MoveTemp(P));
			}
		}

		// COUNT WHAT WAS NOT READ. The resync below is what makes an aborted property walk survivable;
		// it is also what made it invisible, because after it the reader is indistinguishable from one
		// that read everything. Record the abort BEFORE resyncing, while the evidence still exists.
		// A `P` record is called out separately: its property 0 is the property name, so an abort at
		// property 1 drops it below the `Props.Num() >= 2` gate below and a real "Lcl Scaling" is lost
		// with no other trace. See FFbxScaleMetadata's declaration for the full reasoning.
		if ((uint64)Props.Num() != NumProperties)
		{
			++Out.AbortedPropertyWalks;
			if (Name == TEXT("P")) { ++Out.AbortedPropertyRecordWalks; }
		}

		// Resync from the record's own declared length rather than from where the property walk landed,
		// so an unparsed property type can never desynchronise the structural walk.
		R.bOk = true;
		R.Pos = PropsStart + (int64)PropertyListLen;
		if (R.Pos > R.Size || R.Pos > (int64)EndOffset) { return FailStructural(R, Out); }

		FModelCtx ChildModel = Model;
		if (Name == TEXT("Model"))
		{
			++Out.ModelCount;
			ChildModel.Name = Props.IsValidIndex(1) ? Props[1].Str : FString();
			ChildModel.Type = Props.IsValidIndex(2) ? Props[2].Str : FString();
		}
		else if (Name == TEXT("P") && Props.Num() >= 2 && !Props[0].Str.IsEmpty())
		{
			TArray<double> Numbers;
			for (int32 i = 1; i < Props.Num(); ++i)
			{
				if (Props[i].bNumeric) { Numbers.Add(Props[i].Num); }
			}
			ApplyPropertyRecord(Props[0].Str, Numbers, Model, Out);
		}

		// A rig FBX nests ~6 deep; 32 is a corrupt-data backstop, not a real limit.
		if (Depth < 32)
		{
			while (R.bOk && R.Pos < (int64)EndOffset)
			{
				if (!ReadBinaryNode(R, b64, Depth + 1, ChildModel, Out)) { break; }
			}
		}
		// ...AND THE BACKSTOP IS AN ABORT, NOT A SHRUG. Hitting the cap used to
		// skip the whole child subtree SILENTLY, count nothing, and return true — so a `Model` carrying
		// `Lcl Scaling` below depth 32 was unread while the walk reported as completed. That is the
		// structural-abort fault (FailStructural) exactly, one door along, and it is worse here for being
		// in a branch that exists FOR corrupt data, i.e. for that fault's own scenario. The header's "a
		// true return means the walk completed" is load-bearing, so this door has to be closed
		// too.
		//
		// Guarded on `R.Pos < EndOffset` rather than on the depth alone: a record that is legitimately
		// at depth 32 and simply has no children has lost nothing, and counting that would be a red
		// with no fault behind it. Real rigs nest ~6, so this is unreachable in practice — which is the
		// argument for making it honest, not the argument for leaving it.
		else if (R.Pos < (int64)EndOffset)
		{
			return FailStructural(R, Out);
		}
		if (!R.bOk) { return false; }
		R.Pos = (int64)EndOffset;
		return true;
	}

	bool ParseBinary(const TArray<uint8>& Bytes, FFbxScaleMetadata& Out, FString& OutError)
	{
		// "Kaydara FBX Binary  \0" (21) + 0x1A 0x00 (2) + uint32 version (4) = 27-byte preamble.
		if (Bytes.Num() < 27) { OutError = TEXT("binary FBX is truncated (< 27 bytes)"); return false; }

		FByteReader R;
		R.Data = Bytes.GetData();
		R.Size = Bytes.Num();
		R.Pos = 23;                                   // past "Kaydara FBX Binary  \0" + 0x1A 0x00
		Out.bBinary = true;
		Out.FormatVersion = (int32)R.Read<uint32>();
		const bool b64 = Out.FormatVersion >= 7500;   // 7.5 widened the record offsets to 64-bit

		const FModelCtx Root;
		while (R.bOk && R.Pos < R.Size)
		{
			if (!ReadBinaryNode(R, b64, 0, Root, Out)) { break; }
		}

		// A structurally broken file that yielded nothing is an error, not a clean read — refusing to
		// report "no scale problems" for a file we could not actually walk.
		if (Out.ModelCount == 0 && !Out.bHasUnitScaleFactor)
		{
			OutError = FString::Printf(
				TEXT("binary FBX (version %d) yielded neither a UnitScaleFactor nor any Model node — unreadable"),
				Out.FormatVersion);
			return false;
		}

		// AND NEITHER IS A FILE THAT YIELDED SOMETHING AND *THEN* BROKE. The
		// check above was accurate about what it did and was exactly the gap: it only catches a walk
		// that produced nothing at all. A file whose Objects list is truncated partway through parses
		// its first few Models (so ModelCount > 0), aborts at a bad record, and never visits the rest —
		// including a Model carrying Lcl Scaling. ScaledNodes came back empty, both property-abort
		// counters read 0, this returned true, and FixImportScale's `ScaledNodes.Num() > 0` and
		// `AbortedPropertyRecordWalks > 0` guards were both satisfied by a rig nobody had read.
		//
		// FALSE, not "true with a counter set". FbxScaleHeader.h states the contract as "a true return
		// means the walk completed", and on this path it did not; the honest repair is to make the
		// contract true rather than to add a second thing every caller must remember to consult. The
		// counter is still recorded, because the error line has to be able to say what happened, and
		// because a test can assert on a number where it cannot assert on a sentence.
		if (Out.AbortedStructuralWalks > 0)
		{
			OutError = FString::Printf(
				TEXT("binary FBX (version %d) is structurally broken: the record walk aborted after %d Model ")
				TEXT("node(s) and could not resynchronise, so every record after that point is UNREAD and a ")
				TEXT("'Lcl Scaling' may be present and unseen. Refusing to report an incomplete walk as a ")
				TEXT("completed one. Re-export from the authoring app, or convert to ASCII FBX, and retry."),
				Out.FormatVersion, Out.ModelCount);
			return false;
		}
		return true;
	}

	// ---------------------------------------------------------------------------------------------
	// ASCII FBX
	// ---------------------------------------------------------------------------------------------

	// Splits a line into its quoted fields and the tail that follows the last closing quote. ASCII FBX
	// writes values after the quoted type descriptors: P: "Lcl Scaling", "Lcl Scaling", "", "A",1,1,1
	void SplitQuoted(const FString& Line, TArray<FString>& OutQuoted, FString& OutTail)
	{
		int32 Cursor = 0;
		int32 LastClose = INDEX_NONE;
		while (Cursor < Line.Len())
		{
			int32 Open = INDEX_NONE;
			for (int32 i = Cursor; i < Line.Len(); ++i) { if (Line[i] == TEXT('"')) { Open = i; break; } }
			if (Open == INDEX_NONE) { break; }
			int32 Close = INDEX_NONE;
			for (int32 i = Open + 1; i < Line.Len(); ++i) { if (Line[i] == TEXT('"')) { Close = i; break; } }
			if (Close == INDEX_NONE) { break; }
			OutQuoted.Add(Line.Mid(Open + 1, Close - Open - 1));
			LastClose = Close;
			Cursor = Close + 1;
		}
		OutTail = (LastClose == INDEX_NONE) ? Line : Line.Mid(LastClose + 1);
	}

	void TrailingNumbers(const FString& Tail, TArray<double>& Out)
	{
		TArray<FString> Tokens;
		Tail.ParseIntoArray(Tokens, TEXT(","), /*CullEmpty*/ true);
		for (const FString& Token : Tokens)
		{
			// Stop at the first non-numeric token: a value list is contiguous, and continuing past a
			// stray word would pick up numbers that belong to something else on the line.
			double V = 0.0;
			if (!TryParseDouble(Token, V)) { break; }
			Out.Add(V);
		}
	}

	int32 CountChar(const FString& S, TCHAR C)
	{
		int32 N = 0;
		for (int32 i = 0; i < S.Len(); ++i) { if (S[i] == C) { ++N; } }
		return N;
	}

	bool ParseAscii(const FString& Text, FFbxScaleMetadata& Out, FString& OutError)
	{
		TArray<FString> Lines;
		Text.ParseIntoArrayLines(Lines, /*CullEmpty*/ false);

		int32 BraceDepth = 0;
		TArray<TPair<int32, FModelCtx>> ModelStack;   // (brace depth the Model opened at, its identity)
		TOptional<FModelCtx> Pending;

		for (const FString& Raw : Lines)
		{
			const FString Line = Raw.TrimStartAndEnd();
			if (Line.IsEmpty() || Line.StartsWith(TEXT(";"))) { continue; }   // ';' is the ASCII-FBX comment

			TArray<FString> Quoted;
			FString Tail;

			if (Line.StartsWith(TEXT("Model:")))
			{
				++Out.ModelCount;
				SplitQuoted(Line, Quoted, Tail);
				FModelCtx Ctx;
				// "Model::root" in 6.x-style output; a bare name in 7.x. Either way take what follows "::".
				Ctx.Name = Quoted.IsValidIndex(0) ? Quoted[0] : FString();
				const int32 Sep = Ctx.Name.Find(TEXT("::"), ESearchCase::CaseSensitive, ESearchDir::FromEnd);
				if (Sep != INDEX_NONE) { Ctx.Name = Ctx.Name.Mid(Sep + 2); }
				Ctx.Type = Quoted.IsValidIndex(1) ? Quoted[1] : FString();
				Pending = Ctx;
			}
			else if (Line.StartsWith(TEXT("P:")))
			{
				SplitQuoted(Line, Quoted, Tail);
				if (Quoted.Num() >= 1)
				{
					TArray<double> Numbers;
					TrailingNumbers(Tail, Numbers);
					const FModelCtx Model = ModelStack.Num() > 0 ? ModelStack.Last().Value : FModelCtx();
					ApplyPropertyRecord(Quoted[0], Numbers, Model, Out);
				}
			}

			// Brace bookkeeping last, so a "P:" line is attributed to the block it sits inside.
			const int32 Opens = CountChar(Line, TEXT('{'));
			const int32 Closes = CountChar(Line, TEXT('}'));
			for (int32 i = 0; i < Opens; ++i)
			{
				++BraceDepth;
				if (i == 0 && Pending.IsSet())
				{
					ModelStack.Emplace(BraceDepth, Pending.GetValue());
					Pending.Reset();
				}
			}
			for (int32 i = 0; i < Closes; ++i)
			{
				if (ModelStack.Num() > 0 && ModelStack.Last().Key == BraceDepth) { ModelStack.Pop(); }
				--BraceDepth;
			}
		}

		if (Out.ModelCount == 0 && !Out.bHasUnitScaleFactor)
		{
			OutError = TEXT("ASCII FBX yielded neither a UnitScaleFactor nor any Model node — not an FBX?");
			return false;
		}
		return true;
	}
}

namespace MotionTerminal
{
	FString FFbxScaleMetadata::DescribeUnit() const
	{
		if (!bHasUnitScaleFactor) { return TEXT("undeclared"); }
		const TCHAR* Label =
			FMath::IsNearlyEqual(UnitScaleFactor, 1.0, 1e-6) ? TEXT("cm") :
			FMath::IsNearlyEqual(UnitScaleFactor, 100.0, 1e-6) ? TEXT("m") :
			FMath::IsNearlyEqual(UnitScaleFactor, 0.1, 1e-6) ? TEXT("mm") :
			FMath::IsNearlyEqual(UnitScaleFactor, 2.54, 1e-6) ? TEXT("in") :
			FMath::IsNearlyEqual(UnitScaleFactor, 30.48, 1e-6) ? TEXT("ft") : TEXT("non-standard");
		return FString::Printf(TEXT("%.6g (%s)"), UnitScaleFactor, Label);
	}

	bool ParseFbxScaleMetadata(const FString& FbxPath, FFbxScaleMetadata& Out, FString& OutError)
	{
		if (FbxPath.IsEmpty())
		{
			OutError = TEXT("FBX path is empty");
			return false;
		}
		if (!IFileManager::Get().FileExists(*FbxPath))
		{
			OutError = FString::Printf(TEXT("no file at '%s'"), *FbxPath);
			return false;
		}

		TArray<uint8> Bytes;
		if (!FFileHelper::LoadFileToArray(Bytes, *FbxPath))
		{
			OutError = FString::Printf(TEXT("could not read '%s'"), *FbxPath);
			return false;
		}

		static const ANSICHAR* kBinaryMagic = "Kaydara FBX Binary";
		const int32 MagicLen = FCStringAnsi::Strlen(kBinaryMagic);
		const bool bBinary = Bytes.Num() > MagicLen
			&& FMemory::Memcmp(Bytes.GetData(), kBinaryMagic, MagicLen) == 0;

		if (bBinary)
		{
			return ParseBinary(Bytes, Out, OutError);
		}

		Bytes.Add(0);   // NUL-terminate for the ANSI->TCHAR conversion
		const FString Text(UTF8_TO_TCHAR(reinterpret_cast<const ANSICHAR*>(Bytes.GetData())));
		return ParseAscii(Text, Out, OutError);
	}
}
