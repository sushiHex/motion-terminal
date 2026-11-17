#include "MTDriveLibrary.h"

#include "Misc/App.h"

bool UMTDriveLibrary::SetFixedTimeStep(bool bFixed, float Hz)
{
	if (bFixed)
	{
		if (Hz <= 0.f)
		{
			return false;
		}
		FApp::SetFixedDeltaTime(1.0 / Hz);
	}
	FApp::SetUseFixedTimeStep(bFixed);
	return true;
}

void UMTDriveLibrary::GetFixedTimeStep(bool& bFixed, float& Hz)
{
	bFixed = FApp::UseFixedTimeStep();
	const double Delta = FApp::GetFixedDeltaTime();
	Hz = Delta > 0.0 ? static_cast<float>(1.0 / Delta) : 0.f;
}
