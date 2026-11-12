// Claude MCP bridge -- Sandbox editor plugin.
// Mirrors Code/Sandbox/Plugins/SamplePlugin/StdAfx.h exactly (the
// documented reference plugin) -- EditorCommon.h pulls in IEditor.h,
// which is where IAutoEditorNotifyListener and eNotify_OnIdleUpdate live.
//
// MFC addition: the terrain headers ClaudeBridgePlugin.cpp now includes
// (Terrain/Heightmap.h, Terrain/Layer.h, IEditorImpl.h, and transitively
// EditorCommon/Util/NamedData.h) assume MFC types (CPoint, CRect, CDC,
// CObject, the ASSERT macro, AfxGetMainWnd) are already declared.
// Sandbox.exe's own StdAfx.h gets these via CryAtlMfc.h with
// CRY_USE_XT/CRY_USE_ATL defined -- but CRY_USE_XT also pulls in the
// commercial XtremeToolkit Pro UI library (Sandbox's own dialogs use it;
// we don't). CryAtlMfc.h's MFC block fires on CRY_USE_MFC alone
// (`#if defined(CRY_USE_MFC) || defined(CRY_USE_XT)`), so defining just
// CRY_USE_MFC pulls in afxwin.h et al without ATL or ToolkitPro.
// CryAtlMfc.h also #includes CryWindows.h itself at the end (after
// setting up the MFC-required WIN32_LEAN_AND_MEAN / NOMINMAX / function-
// renaming state) -- so the standalone CryWindows.h include this file
// used to do on its own is folded into that instead of kept alongside
// (both would double-include, and the Windows-API renaming macros CryWindows.h
// sets up need to happen exactly once, before any MFC header sees them).
#pragma once

#include <CryCore/Platform/platform.h>

#ifdef CRY_PLATFORM_WINDOWS
	#define CRY_USE_MFC
	#include <CryCore/Platform/CryAtlMfc.h>
#endif

#include "EditorCommon.h"
