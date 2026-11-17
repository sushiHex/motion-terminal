#pragma once

#include "Dom/JsonObject.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"

/** The hero toolsets' JSON replies (HeroPhysicsTools, HeroDynamicsTools): the Python callers parse {"ok", ...,
    "error"?}, so the shape lives here once. */
namespace HeroToolJson
{
	inline FString ToJson(const TSharedRef<FJsonObject>& Obj)
	{
		FString Out;
		const TSharedRef<TJsonWriter<>> W = TJsonWriterFactory<>::Create(&Out);
		FJsonSerializer::Serialize(Obj, W);
		return Out;
	}

	inline FString Fail(const FString& Why)
	{
		const TSharedRef<FJsonObject> Obj = MakeShared<FJsonObject>();
		Obj->SetBoolField(TEXT("ok"), false);
		Obj->SetStringField(TEXT("error"), Why);
		return ToJson(Obj);
	}
}
