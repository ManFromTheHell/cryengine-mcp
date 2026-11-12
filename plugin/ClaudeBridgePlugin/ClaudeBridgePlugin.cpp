// Claude MCP bridge -- Sandbox editor plugin implementation.
#include "StdAfx.h"
#include "ClaudeBridgePlugin.h"

// Included only once per DLL module -- same requirement/placement as in
// every other CryEngine module (see GamePlugin.cpp, SamplePlugin.cpp).
#include <CryCore/Platform/platform_impl.inl>

#include <CrySystem/ISystem.h>
#include <CrySystem/IStreamEngine.h>
#include <CrySystem/ILog.h>
#include <CrySystem/IConsole.h>
#include <CryRenderer/IRenderer.h>
#include <CryEntitySystem/IEntitySystem.h>
#include <CryEntitySystem/IEntity.h>

// Needed for GetIEditor()->GetIPythonManager() -- a real Python interpreter
// entry point, distinct from both IConsole and ICommandManager. A large
// class of Sandbox commands (general.new_object, general.create_object, ...)
// are registered via REGISTER_ONLY_PYTHON_COMMAND_WITH_EXAMPLE, which is
// Python-interpreter-only and never reaches ICommandManager at all --
// confirmed empirically: bridge.execute_command routed through
// IEditor::ExecuteCommand reports "Unknown command: general.new_object" even
// though the function genuinely exists (see BoostPythonMacros.h).
#include <BoostPythonMacros.h>

// Needed for direct terrain height/texture access -- CHeightmap, CLayer and
// CTerrainManager are all EditorQt-internal (Sandbox.exe) classes with no
// abstract-interface or Python-scripted equivalent (confirmed empirically:
// no REGISTER_PYTHON_COMMAND anywhere generates terrain height or assigns a
// custom texture to a layer -- TerrainCommands.cpp only exposes
// create/flood/delete/move on layers, and CHeightmap::GenerateTerrain is a
// GUI-dialog-only call). IEditor::GetHeightmap() already returns the
// concrete CHeightmap*, so that half needs no new linkage -- but
// CTerrainManager (needed for CLayer::LoadTexture) is reachable only via
// CEditorImpl::GetTerrainManager(), which means linking against Sandbox.exe's
// own import library (Sandbox.lib) rather than just EditorCommon.lib -- see
// the AdditionalIncludeDirectories/AdditionalDependencies changes in
// ClaudeBridgePlugin.vcxproj.
#include <IEditorImpl.h>
#include <Terrain/Heightmap.h>
#include <Vegetation/VegetationMap.h>
#include <Vegetation/VegetationObject.h>
#include <Terrain/Layer.h>
#include <Terrain/Noise.h>
#include <Terrain/TerrainManager.h>
#include <GameExporter.h>

// Editor viewport: CViewport is EDITOR_COMMON_API (already linked), and
// IEditor::GetActiveView() hands one straight over -- so camera control needs
// no Sandbox.exe linkage at all. This matters because the console's `goto`
// only exists in game mode (CCryAction registers it), leaving the editor
// camera otherwise undriveable from a script.
#include <Viewport.h>

// Objects and environment, bound directly rather than driven through the GUI.
// IObjectManager and CBaseObject are reachable from the plain IEditor
// interface, and ITimeOfDay hangs off gEnv->p3DEngine -- so neither needs
// Sandbox.exe linkage. ITimeOfDay in particular exposes every sun/fog/exposure
// variable by name through IVariables, which is the whole Environment Editor's
// worth of control without a single synthetic click.
#include <IObjectManager.h>
#include <Objects/BaseObject.h>
#include <Objects/ObjectLoader.h>
#include <Objects/ClassDesc.h>
#include <Objects/ShapeObject.h>
#include <CryAISystem/IAISystem.h>
#include <CryAISystem/INavigationSystem.h>
#include <CrySerialization/IArchiveHost.h>
#include <CrySchematyc/Reflection/TypeDesc.h>
#include <CrySchematyc/Utils/ClassProperties.h>

// bridge.schematyc.dump: the whole Schematyc environment, read from the registry the engine itself fills (gEnv->pSchematyc->GetEnvRegistry()).
#include <CrySchematyc/ICore.h>
#include <CrySchematyc/Env/IEnvRegistry.h>
#include <CrySchematyc/Env/Elements/IEnvComponent.h>
#include <CrySchematyc/Env/Elements/IEnvFunction.h>
#include <CrySchematyc/Env/Elements/IEnvSignal.h>
#include <CrySchematyc/Env/Elements/IEnvDataType.h>
#include <CrySchematyc/Env/Elements/IEnvClass.h>
#include <CrySchematyc/Env/Elements/IEnvAction.h>
#include <CrySchematyc/Utils/Any.h>
#include <CryParticleSystem/IParticlesPfx2.h>
#include <CryMovie/IMovieSystem.h>
#include <CryGame/IGameFramework.h>
#include <CryFlowGraph/IFlowSystem.h>
#include <CryDynamicResponseSystem/IDynamicResponseSystem.h>
#include <CryDynamicResponseSystem/IDynamicResponseAction.h>
#include <CryDynamicResponseSystem/IDynamicResponseCondition.h>
#include <CrySystem/IProjectManager.h>
#include <CrySystem/Scaleform/IFlashUI.h>
#include <Notifications/NotificationCenter.h>
#include <IUndoManager.h>
#include <Cry3DEngine/I3DEngine.h>
#include <Cry3DEngine/IMaterial.h>
#include <Cry3DEngine/ISurfaceType.h>
#include <CryRenderer/IShader.h>
#include <CryRenderer/ITexture.h>
#include <CryAnimation/ICryAnimation.h>
#include <CryAnimation/ICryMannequin.h>
#include <CryAnimation/ICryMannequinEditor.h>
#include <CryAISystem/BehaviorTree/IBehaviorTree.h>
#include <CryAISystem/BehaviorTree/XmlLoader.h>
#include "../../../CryPlugins/CryUQS/Interfaces/InterfacesIncludes.h"

// The editor's item databases. IEditor::GetDBItemManager() is a plain
// abstract interface, and one manager per EDataBaseItemType covers FIVE of
// the systems that have no Python module at all -- lens flares, particle
// effects, entity archetypes, gametokens and materials. Items serialize
// through an XmlNodeRef (IDataBaseItem::SerializeContext), so a single
// export/import pair gives full read/write of any of them without needing a
// bespoke binding per system.
#include <LensFlareEditor/LensFlareManager.h>
#include <IDataBaseManager.h>
#include <IDataBaseItem.h>
#include <IDataBaseLibrary.h>

// Vegetation. The Python vegetation module only ACTIVATES the paint/erase
// brush tools, which then need mouse input in the viewport -- so scattering
// vegetation is not scriptable through it at all. CVegetationMap is
// SANDBOX_API and exposes both single placement and area scattering directly,
// which is what makes vegetation reachable from here without a mouse.
#include <Vegetation/VegetationMap.h>
#include <Vegetation/VegetationObject.h>
#include <Cry3DEngine/ITimeOfDay.h>
#include <CryMath/Bezier.h>

// Asset Browser: "New > Environment" goes through the asset type (bridge.env.new_asset).
#include <AssetSystem/AssetManager.h>
#include <AssetSystem/AssetType.h>

// The dialog verbs (bridge.dialog.*) read and click the editor's Qt dialogs, on the GUI thread only.
#include <QApplication>
#include <QAbstractButton>
#include <QDialog>
#include <QDialogButtonBox>
#include <QLabel>
#include <QPushButton>

#include <chrono>
#include <cmath>
#include <cctype>
#include <cfloat>
#include <cstdio>
#include <fstream>
#include <iomanip>
#include <cstdlib>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace
{
	// The editor behind the plugin interface. Crytek's Sandbox.exe does not export GetIEditorImpl();
	// a local SANDBOX_API edit did, and the plugin needed a locally built Sandbox.exe because of that one
	// import. The IEditor a plugin is handed IS the CEditorImpl -- IEditor is its first
	// base (IEditorImpl.h:73) -- so a static_cast reaches it without the export.
	CEditorImpl* EditorImpl()
	{
		return static_cast<CEditorImpl*>(GetIEditor());
	}

	// Captures whatever a console command produces while it runs, via both
	// of CryEngine's separate output paths. Copied verbatim from the
	// Game.dll bridge (GamePlugin.cpp), where hooking only ILogCallback was
	// empirically confirmed to miss most command output (cvar-change
	// echoes etc. go through IOutputPrintSink instead) -- so both are
	// hooked here too rather than re-deriving that the hard way again.
	class CScopedLogCapture : public ILogCallback, public IOutputPrintSink
	{
	public:
		CScopedLogCapture()
		{
			if (gEnv && gEnv->pLog) gEnv->pLog->AddCallback(this);
			if (gEnv && gEnv->pConsole) gEnv->pConsole->AddOutputPrintSink(this);
		}
		~CScopedLogCapture()
		{
			if (gEnv && gEnv->pLog) gEnv->pLog->RemoveCallback(this);
			if (gEnv && gEnv->pConsole) gEnv->pConsole->RemoveOutputPrintSink(this);
		}

		// ILogCallback
		void OnWriteToConsole(const char* sText, bool bNewLine) override
		{
			m_buffer << sText;
			if (bNewLine) m_buffer << "\n";
		}
		void OnWriteToFile(const char* sText, bool bNewLine) override {}

		// IOutputPrintSink
		void Print(const char* inszText) override
		{
			m_buffer << inszText << "\n";
		}

		// Console output arrives twice -- once through ILogCallback and once
		// through IOutputPrintSink, which both see the same text. Callers that
		// only want to read the result pass true here; the raw doubled form is
		// kept as the default so nothing that already parses it changes shape.
		std::string TakeOutput(bool bDedupeLines = false)
		{
			const std::string raw = m_buffer.str();
			if (!bDedupeLines)
				return raw;

			std::string out;
			out.reserve(raw.size());
			std::string prev;
			size_t pos = 0;
			while (pos <= raw.size())
			{
				const size_t nl = raw.find('\n', pos);
				const std::string line = raw.substr(pos, (nl == std::string::npos ? raw.size() : nl) - pos);
				if (line != prev || line.empty())
				{
					out += line;
					out += '\n';
				}
				prev = line;
				if (nl == std::string::npos)
					break;
				pos = nl + 1;
			}
			return out;
		}

	private:
		std::ostringstream m_buffer;
	};

	// Standard base64. Exists so Python source can cross the wire as a single
	// whitespace-free token: the command line is split on whitespace, which
	// otherwise flattens every newline and every level of indentation in a
	// script before it ever reaches the interpreter.
	std::string Base64Encode(const std::string& in)
	{
		static const char* const tbl =
			"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
		std::string out;
		out.reserve(((in.size() + 2) / 3) * 4);

		size_t i = 0;
		for (; i + 2 < in.size(); i += 3)
		{
			const unsigned v = ((unsigned)(unsigned char)in[i] << 16)
			                 | ((unsigned)(unsigned char)in[i + 1] << 8)
			                 |  (unsigned)(unsigned char)in[i + 2];
			out += tbl[(v >> 18) & 63];
			out += tbl[(v >> 12) & 63];
			out += tbl[(v >> 6) & 63];
			out += tbl[v & 63];
		}

		const size_t rem = in.size() - i;
		if (rem == 1)
		{
			const unsigned v = (unsigned)(unsigned char)in[i] << 16;
			out += tbl[(v >> 18) & 63];
			out += tbl[(v >> 12) & 63];
			out += "==";
		}
		else if (rem == 2)
		{
			const unsigned v = ((unsigned)(unsigned char)in[i] << 16)
			                 | ((unsigned)(unsigned char)in[i + 1] << 8);
			out += tbl[(v >> 18) & 63];
			out += tbl[(v >> 12) & 63];
			out += tbl[(v >> 6) & 63];
			out += '=';
		}
		return out;
	}

	// Ignores '=' padding and any embedded whitespace, so a payload that
	// picked up a line break in transit still decodes.
	std::string Base64Decode(const std::string& in)
	{
		auto value = [](char c) -> int
		{
			if (c >= 'A' && c <= 'Z') return c - 'A';
			if (c >= 'a' && c <= 'z') return c - 'a' + 26;
			if (c >= '0' && c <= '9') return c - '0' + 52;
			if (c == '+') return 62;
			if (c == '/') return 63;
			return -1;
		};

		std::string out;
		out.reserve((in.size() / 4) * 3);
		int acc = 0;
		int bits = 0;
		for (char c : in)
		{
			const int v = value(c);
			if (v < 0)
				continue;
			acc = (acc << 6) | v;
			bits += 6;
			if (bits >= 8)
			{
				bits -= 8;
				out += (char)((acc >> bits) & 0xFF);
			}
		}
		return out;
	}

	std::string BridgeTempFile(const char* name)
	{
		char buf[MAX_PATH];
		const DWORD n = GetTempPathA(MAX_PATH, buf);
		std::string dir = (n > 0 && n < MAX_PATH) ? std::string(buf, n) : std::string("C:\\Windows\\Temp\\");
		if (dir.empty() || dir[dir.size() - 1] != '\\')
			dir += '\\';
		return dir + name;
	}

	void WriteWholeFile(const std::string& path, const std::string& contents)
	{
		std::ofstream f(path.c_str(), std::ios::binary | std::ios::trunc);
		if (!f)
			throw std::runtime_error("could not open for writing: " + path);
		if (!contents.empty())
			f.write(contents.data(), (std::streamsize)contents.size());
	}

	std::string ReadWholeFile(const std::string& path)
	{
		std::ifstream f(path.c_str(), std::ios::binary);
		if (!f)
			return std::string();
		std::ostringstream ss;
		ss << f.rdbuf();
		return ss.str();
	}

	// Splits a command line into whitespace-separated tokens. Good enough
	// for the fixed "bridge.thing arg1 arg2 ..." shape used below; entity
	// names with spaces aren't supported (same limitation as the Game.dll
	// bridge).
	std::vector<std::string> Tokenize(const std::string& cmd)
	{
		std::vector<std::string> tokens;
		std::istringstream iss(cmd);
		std::string tok;
		while (iss >> tok) tokens.push_back(tok);
		return tokens;
	}

	// The wire protocol double-encodes: TcpCommandServer.cpp JSON-escapes
	// whatever DispatchBridgeCommand() returns into the outer envelope's
	// "output" field as a JSON *string*, and server.py's _send_structured()
	// then json.loads()s that string as a second layer. That only works if
	// DispatchBridgeCommand's own return value is itself valid JSON text for
	// every "bridge.*" command -- exactly the contract GamePlugin.cpp's
	// bridge.* handlers follow (see its JsonEscapeBridge). This is the same
	// escaping logic, duplicated here for the same reason GamePlugin.cpp
	// duplicates it from TcpCommandServer.cpp: not worth restructuring
	// headers for something this small.
	std::string JsonEscape(const std::string& s)
	{
		std::string out;
		out.reserve(s.size() + 8);
		for (char c : s)
		{
			switch (c)
			{
			case '"': out += "\\\""; break;
			case '\\': out += "\\\\"; break;
			case '\n': out += "\\n"; break;
			case '\r': out += "\\r"; break;
			case '\t': out += "\\t"; break;
			default:
				if (static_cast<unsigned char>(c) < 0x20)
				{
					char buf[8];
					snprintf(buf, sizeof(buf), "\\u%04x", c);
					out += buf;
				}
				else
				{
					out += c;
				}
			}
		}
		return out;
	}

	// Resolves a terrain layer given either a 0-based index ("0", "2", ...) or
	// an exact layer name (first match). Layer names are NOT unique --
	// terrain.create_layer names every layer it makes "NewLayer" -- so an index
	// is the reliable form; bridge.terrain.list_layers reports both.
	int ResolveLayerIndex(CTerrainManager* pTerrainManager, const std::string& arg)
	{
		const int count = pTerrainManager->GetLayerCount();

		char* endptr = nullptr;
		const long parsed = std::strtol(arg.c_str(), &endptr, 10);
		if (endptr != arg.c_str() && *endptr == '\0')
		{
			const int index = static_cast<int>(parsed);
			if (index < 0 || index >= count)
				throw std::runtime_error("terrain layer index out of range: " + arg);
			return index;
		}

		for (int i = 0; i < count; ++i)
		{
			CLayer* pLayer = pTerrainManager->GetLayer(i);
			if (pLayer && std::string((const char*)pLayer->GetLayerName()) == arg)
				return i;
		}
		throw std::runtime_error("no such terrain layer: " + arg);
	}

	// 1.0 well inside [lo,hi], 0.0 outside, smoothly ramped across a margin at
	// each end. CLayer's own altitude/slope test is a hard in-or-out compare,
	// which leaves visibly stair-stepped edges where one layer meets another;
	// feathering the boundary is what makes a blended base texture look like
	// ground rather than a stencil.
	// maxMargin caps the feather in the units of the quantity being banded.
	//
	// A purely relative feather (15% of the band) is fine for a narrow band and
	// catastrophic for a deliberately open-ended one. A layer set to "slope 26
	// to 90 degrees" has a band of tan(26deg)..tan(90deg) -- roughly 0.49 to
	// 573 -- so a 15% feather ramps from 0 at 26 degrees to full only at about
	// 89 degrees. Real ground never gets there, so the layer came out at a
	// weight of ~0.0001 everywhere and the baked terrain texture was 100% base
	// layer: reported as blended, visibly absent. Measured, not guessed --
	// sampling the baked tiles gave green 100.0%, brown 0.0%.
	float SmoothBand(float value, float lo, float hi, float maxMargin = 0.0f)
	{
		if (hi <= lo)
			return (value >= lo) ? 1.0f : 0.0f;
		if (value <= lo || value >= hi)
			return 0.0f;

		float margin = (hi - lo) * 0.15f;
		if (maxMargin > 0.0f && margin > maxMargin)
			margin = maxMargin;
		if (margin <= 0.0f)
			return 1.0f;

		float t = 1.0f;
		if (value < lo + margin)
			t = (value - lo) / margin;
		else if (value > hi - margin)
			t = (hi - value) / margin;

		if (t <= 0.0f)
			return 0.0f;
		if (t >= 1.0f)
			return 1.0f;
		// smoothstep
		return t * t * (3.0f - 2.0f * t);
	}

	//////////////////////////////////////////////////////////////////////////
	// Out-of-band commands.
	//
	// Everything else in this plugin is queued onto the editor idle tick,
	// because touching gEnv from a background thread is not safe. But that
	// makes the bridge helpless in exactly the situation where you most need
	// it: while a modal dialog is up, the idle tick never runs, so every
	// queued command times out -- including whatever you would use to close
	// the dialog. The session is then stuck until a human clicks it.
	//
	// The handlers below touch ONLY Win32 (GDI capture, synthetic input) and
	// never engine state, so they are safe to run straight on the TCP client
	// thread. That is what turns a modal dialog from a dead end into
	// something recoverable: screenshot it, read it, click it away.
	//////////////////////////////////////////////////////////////////////////

	// Largest visible unowned top-level window belonging to this process.
	// Deliberately not AfxGetMainWnd(): inside a plugin DLL the MFC module
	// state may not resolve to Sandbox's own main window.
	HWND FindSandboxWindow()
	{
		struct SFind
		{
			DWORD pid;
			HWND best;
			long long bestArea;
		} find = { GetCurrentProcessId(), nullptr, 0 };

		EnumWindows([](HWND hwnd, LPARAM lp) -> BOOL
		{
			SFind* f = reinterpret_cast<SFind*>(lp);
			DWORD pid = 0;
			GetWindowThreadProcessId(hwnd, &pid);
			if (pid != f->pid || !IsWindowVisible(hwnd) || GetWindow(hwnd, GW_OWNER) != nullptr)
				return TRUE;
			RECT r;
			if (!GetWindowRect(hwnd, &r))
				return TRUE;
			// the main window is the one titled 'CRYENGINE Sandbox ...'; a floating tool window (the Particle Editor, a detached pane) can be larger, and picking the largest closed THAT on a deploy
			// and left the editor running. Prefer the titled window, and fall back to the largest.
			// InternalGetWindowText reads the title from the window's own structure; GetWindowText on a window of THIS process sends it WM_GETTEXT, which a wedged main thread never answers
			wchar_t title[256] = {};
			InternalGetWindowText(hwnd, title, 255);
			const long long area = (long long)(r.right - r.left) * (r.bottom - r.top) + (wcsstr(title, L"CRYENGINE Sandbox") ? (1LL << 40) : 0);
			if (area > f->bestArea)
			{
				f->bestArea = area;
				f->best = hwnd;
			}
			return TRUE;
		}, reinterpret_cast<LPARAM>(&find));

		return find.best;
	}

	// Captures the Sandbox window to a 24-bit BMP.
	//
	// Neither engine path works in the editor: IRenderer::ScreenShot returns
	// false in edit mode and general.take_screenshot() writes no file (both
	// confirmed empirically), so this goes around the renderer at the OS
	// level. Side benefit: it captures the whole editor -- panels, tool state,
	// and the text of any dialog -- not just the 3D view.
	//
	// Two methods, because neither is reliable alone. PrintWindow with
	// PW_RENDERFULLCONTENT works even when the window is occluded but can
	// return an empty frame for D3D swapchain content; a BitBlt from the
	// screen DC always sees what is really on screen but needs the window
	// unobstructed. Try the first, measure it, fall back when it comes back
	// blank, and report which one produced the image.
	// settleMs: keep the editor drawing for this long before grabbing.
	//
	// Sandbox renders lazily -- it draws when something pokes it and otherwise
	// sits still. Capture straight after a camera move and you get whatever was
	// on screen before, or worse, a half-finished frame: one grab after a large
	// jump came back as ribbons of terrain with the LOD still resolving.
	// Nudging the cursor a pixel at a time is what keeps frames coming, because
	// real input is the thing the editor reliably wakes for.
	std::string CaptureSandboxWindow(const std::string& path, HWND target = nullptr, int settleMs = 0)
	{
		HWND hwnd = target ? target : FindSandboxWindow();
		if (!hwnd)
			throw std::runtime_error("could not find the Sandbox main window");

		// Restore rather than refuse. A minimised window has no client pixels
		// to read, but refusing left the bridge with no way to see anything at
		// all until a human touched the machine -- and not being able to look
		// is exactly the situation where looking matters. The result reports
		// "restored" so the state change is never silent.
		bool restored = false;
		if (IsIconic(hwnd))
		{
			ShowWindow(hwnd, SW_RESTORE);
			restored = true;
			// Let the swapchain repaint: the first grab straight after a
			// restore otherwise comes back blank.
			Sleep(400);
		}

		if (settleMs > 0)
		{
			// SendInput, not SetCursorPos. The first version of this used
			// SetCursorPos and did nothing at all: four consecutive captures
			// came back byte-identical while the editor sat on a stale frame,
			// and the verb cheerfully reported settleMs back. Sandbox wakes for
			// real input-queue events; warping the pointer is not one.
			//
			// A relative one-pixel jitter needs no screen-coordinate maths and
			// leaves the cursor where it started after an even number of steps.
			const int steps = settleMs / 100 + 1;
			for (int i = 0; i < steps; ++i)
			{
				INPUT in;
				ZeroStruct(in);
				in.type = INPUT_MOUSE;
				in.mi.dwFlags = MOUSEEVENTF_MOVE;
				in.mi.dx = (i & 1) ? 1 : -1;
				in.mi.dy = 0;
				SendInput(1, &in, sizeof(INPUT));

				// Deliberately NOT RedrawWindow(..., RDW_UPDATENOW). That is a
				// synchronous cross-thread paint: called from this TCP thread it
				// blocks until the UI thread services it, and if the UI thread is
				// itself waiting on the render thread the whole editor wedges.
				// It did exactly that once -- Sandbox hung hard enough that even
				// the out-of-band verbs stopped answering, because GetWindowRect
				// on a hung window blocks too. The input jitter above is enough
				// on its own: Sandbox draws when it receives real input.
				Sleep(100);
			}
		}

		RECT rc;
		GetWindowRect(hwnd, &rc);
		const int w = rc.right - rc.left;
		const int h = rc.bottom - rc.top;
		if (w <= 0 || h <= 0)
			throw std::runtime_error("Sandbox window has zero size");

		HDC hdcScreen = GetDC(nullptr);
		HDC hdcMem = CreateCompatibleDC(hdcScreen);

		BITMAPINFO bi;
		ZeroStruct(bi);
		bi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
		bi.bmiHeader.biWidth = w;
		bi.bmiHeader.biHeight = -h;   // negative = top-down rows
		bi.bmiHeader.biPlanes = 1;
		bi.bmiHeader.biBitCount = 32;
		bi.bmiHeader.biCompression = BI_RGB;

		void* pBits = nullptr;
		HBITMAP hbm = CreateDIBSection(hdcScreen, &bi, DIB_RGB_COLORS, &pBits, nullptr, 0);
		if (!hbm || !pBits)
		{
			DeleteDC(hdcMem);
			ReleaseDC(nullptr, hdcScreen);
			throw std::runtime_error("CreateDIBSection failed");
		}
		HGDIOBJ hOld = SelectObject(hdcMem, hbm);

		// A PrintWindow that missed the D3D surface comes back essentially
		// empty; that is the signal to fall back rather than save a black
		// image and call it a success.
		auto FractionLit = [&]() -> double
		{
			const uint8* p = static_cast<const uint8*>(pBits);
			const size_t total = (size_t)w * (size_t)h;
			const size_t step = total > 200000 ? total / 200000 : 1;
			size_t lit = 0, seen = 0;
			for (size_t i = 0; i < total; i += step, ++seen)
			{
				const uint8* px = p + i * 4;
				if (px[0] > 8 || px[1] > 8 || px[2] > 8)
					++lit;
			}
			return seen ? (double)lit / (double)seen : 0.0;
		};

		// Mean chroma over the frame. A drawn 3D viewport always has colour in
		// it; the "editor never repainted" frame is flat editor grey and scores
		// essentially zero. fractionLit cannot tell them apart -- grey is lit --
		// and the checksum only catches an exact repeat, which misses the case
		// where alternate captures come back grey with different noise. This is
		// the signal that actually separates a drawn frame from an undrawn one.
		auto MeanSaturation = [&]() -> double
		{
			const uint8* p = static_cast<const uint8*>(pBits);
			const size_t total = (size_t)w * (size_t)h;
			const size_t step = total > 200000 ? total / 200000 : 1;
			double sum = 0.0;
			size_t seen = 0;
			for (size_t i = 0; i < total; i += step, ++seen)
			{
				const uint8* px = p + i * 4;
				const uint8 mx = max(px[0], max(px[1], px[2]));
				const uint8 mn = min(px[0], min(px[1], px[2]));
				sum += (double)(mx - mn);
			}
			return seen ? sum / (double)seen : 0.0;
		};

		// A cheap content checksum. Two captures that return the same value are
		// the same frame -- which is how a stale capture gets noticed instead of
		// being mistaken for "the scene really is that dark".
		auto Checksum = [&]() -> unsigned long long
		{
			const uint8* p = static_cast<const uint8*>(pBits);
			const size_t total = (size_t)w * (size_t)h;
			const size_t step = total > 100000 ? total / 100000 : 1;
			unsigned long long sum = 1469598103934665603ULL;
			for (size_t i = 0; i < total; i += step)
			{
				const uint8* px = p + i * 4;
				sum ^= (unsigned long long)(px[0] + 3u * px[1] + 7u * px[2]);
				sum *= 1099511628211ULL;
			}
			return sum;
		};

		const UINT kRenderFullContent = 0x00000002;
		std::string method = "PrintWindow";
		BOOL ok = PrintWindow(hwnd, hdcMem, kRenderFullContent);
		double lit = ok ? FractionLit() : 0.0;

		if (!ok || lit < 0.02)
		{
			method = "BitBlt";
			BitBlt(hdcMem, 0, 0, w, h, hdcScreen, rc.left, rc.top, SRCCOPY);
			lit = FractionLit();
		}

		// 24-bit BMP: bottom-up rows, each padded to a 4-byte boundary.
		const int rowBytes = ((w * 3 + 3) / 4) * 4;
		const uint32 pixelBytes = (uint32)rowBytes * (uint32)h;

		BITMAPFILEHEADER fh;
		ZeroStruct(fh);
		fh.bfType = 0x4D42;   // 'BM'
		fh.bfOffBits = sizeof(BITMAPFILEHEADER) + sizeof(BITMAPINFOHEADER);
		fh.bfSize = fh.bfOffBits + pixelBytes;

		BITMAPINFOHEADER ih;
		ZeroStruct(ih);
		ih.biSize = sizeof(BITMAPINFOHEADER);
		ih.biWidth = w;
		ih.biHeight = h;
		ih.biPlanes = 1;
		ih.biBitCount = 24;
		ih.biCompression = BI_RGB;
		ih.biSizeImage = pixelBytes;

		FILE* f = nullptr;
		fopen_s(&f, path.c_str(), "wb");
		if (!f)
		{
			SelectObject(hdcMem, hOld);
			DeleteObject(hbm);
			DeleteDC(hdcMem);
			ReleaseDC(nullptr, hdcScreen);
			throw std::runtime_error("could not open output file: " + path);
		}
		fwrite(&fh, sizeof(fh), 1, f);
		fwrite(&ih, sizeof(ih), 1, f);

		std::vector<uint8> row((size_t)rowBytes, 0);
		const uint8* src = static_cast<const uint8*>(pBits);
		for (int y = h - 1; y >= 0; --y)   // BMP stores rows bottom-up
		{
			const uint8* s = src + (size_t)y * (size_t)w * 4;
			for (int x = 0; x < w; ++x)
			{
				row[(size_t)x * 3 + 0] = s[(size_t)x * 4 + 0];
				row[(size_t)x * 3 + 1] = s[(size_t)x * 4 + 1];
				row[(size_t)x * 3 + 2] = s[(size_t)x * 4 + 2];
			}
			fwrite(row.data(), 1, (size_t)rowBytes, f);
		}
		fclose(f);

		// Compute this BEFORE the DIB section is destroyed. Emitting it in the
		// JSON below instead was a use-after-free on pBits: the pixels are
		// released by DeleteObject(hbm), and reading them afterwards took the
		// whole editor down mid-screenshot.
		const unsigned long long checksum = Checksum();
		const double saturation = MeanSaturation();

		SelectObject(hdcMem, hOld);
		DeleteObject(hbm);
		DeleteDC(hdcMem);
		ReleaseDC(nullptr, hdcScreen);

		std::ostringstream oss;
		oss << "{\"ok\": true, \"path\": \"" << JsonEscape(path) << "\""
		    << ", \"width\": " << w << ", \"height\": " << h
		    << ", \"method\": \"" << method << "\""
		    << ", \"restored\": " << (restored ? "true" : "false")
		    << ", \"settleMs\": " << settleMs
		    << ", \"checksum\": " << checksum
		    << ", \"saturation\": " << saturation
		    << ", \"fractionLit\": " << lit << "}";
		return oss.str();
	}

	//////////////////////////////////////////////////////////////////////////
	// Input targeting (2026-09-25).
	//
	// Synthetic input goes through SendInput rather than PostMessage: Qt widgets
	// ignore posted mouse messages (they rely on the real input queue for
	// hit-testing and focus). But SendInput delivers to whatever is under the
	// pointer, and keys to whatever has the foreground -- not to Sandbox. The
	// verbs used to aim at the main window, call SetForegroundWindow without
	// checking it, and answer ok. An export's Yes/No prompt was missed that way
	// twice: the Windows Firewall prompt had the foreground, so the keys went to
	// it (or nowhere) and the refusal went unnoticed. Now each
	// verb works out what its input will actually reach, and refuses -- naming
	// that window -- when it is not ours. Everything here reads z-order and
	// rectangles only: no message is sent to any window, so a busy UI thread
	// cannot block these out-of-band verbs.
	//////////////////////////////////////////////////////////////////////////

	typedef HANDLE (WINAPI* PFN_SetThreadDpiAwarenessContext)(HANDLE);
	typedef int (WINAPI* PFN_InternalGetWindowText)(HWND, LPWSTR, int);
	typedef HRESULT (WINAPI* PFN_DwmGetWindowAttribute)(HWND, DWORD, PVOID, DWORD);

	// Physical pixels for this thread, whatever it inherited. The TCP threads
	// happen to inherit Qt's per-monitor awareness; a system-aware thread would
	// read the 120-dpi screen at 0.8x and every click would land short.
	// _WIN32_WINNT is 0x0600 here, so the Windows 10 call is looked up at run time.
	class CScopedPhysicalPixels
	{
	public:
		CScopedPhysicalPixels()
		{
			static const PFN_SetThreadDpiAwarenessContext s_pfn =
				(PFN_SetThreadDpiAwarenessContext)GetProcAddress(GetModuleHandleA("user32.dll"), "SetThreadDpiAwarenessContext");
			m_pfn = s_pfn;
			if (m_pfn)
				m_prev = m_pfn((HANDLE)(INT_PTR)-4);   // DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
		}
		~CScopedPhysicalPixels()
		{
			if (m_pfn && m_prev)
				m_pfn(m_prev);
		}
	private:
		PFN_SetThreadDpiAwarenessContext m_pfn = nullptr;
		HANDLE                           m_prev = nullptr;
	};

	bool IsOurs(HWND h)
	{
		DWORD pid = 0;
		if (h)
			GetWindowThreadProcessId(h, &pid);
		return pid != 0 && pid == GetCurrentProcessId();
	}

	// A window's title without WM_GETTEXT. GetWindowText SENDS that message to a
	// window of another thread and waits for as long as that thread is busy --
	// which is exactly when these verbs are needed.
	std::string WindowTitle(HWND h)
	{
		static const PFN_InternalGetWindowText s_pfn =
			(PFN_InternalGetWindowText)GetProcAddress(GetModuleHandleA("user32.dll"), "InternalGetWindowText");
		wchar_t wide[512];
		wide[0] = 0;
		const int n = (s_pfn && h) ? s_pfn(h, wide, 511) : 0;
		if (n <= 0)
			return std::string();
		char utf8[1600];
		const int m = WideCharToMultiByte(CP_UTF8, 0, wide, n, utf8, (int)sizeof(utf8) - 1, nullptr, nullptr);
		return std::string(utf8, m > 0 ? (size_t)m : 0);
	}

	std::string DescribeWindow(HWND h)
	{
		if (!h)
			return "{\"hwnd\": 0}";
		DWORD pid = 0;
		GetWindowThreadProcessId(h, &pid);
		char cls[256];
		cls[0] = 0;
		GetClassNameA(h, cls, (int)sizeof(cls) - 1);
		std::ostringstream oss;
		oss << "{\"hwnd\": " << (unsigned long long)(uintptr_t)h << ", \"pid\": " << pid
		    << ", \"ours\": " << (pid == GetCurrentProcessId() ? "true" : "false")
		    << ", \"enabled\": " << (IsWindowEnabled(h) ? "true" : "false")
		    << ", \"title\": \"" << JsonEscape(WindowTitle(h)) << "\""
		    << ", \"class\": \"" << JsonEscape(cls) << "\"}";
		return oss.str();
	}

	PFN_DwmGetWindowAttribute DwmAttribute()
	{
		static const PFN_DwmGetWindowAttribute s_pfn = []() -> PFN_DwmGetWindowAttribute
		{
			HMODULE hDwm = LoadLibraryA("dwmapi.dll");
			return hDwm ? (PFN_DwmGetWindowAttribute)GetProcAddress(hDwm, "DwmGetWindowAttribute") : nullptr;
		}();
		return s_pfn;
	}

	// Cloaked windows (suspended store apps, other virtual desktops) are
	// "visible" to Win32 but not on screen and take no clicks.
	bool IsCloaked(HWND h)
	{
		DWORD cloaked = 0;
		const PFN_DwmGetWindowAttribute pfn = DwmAttribute();
		return pfn && SUCCEEDED(pfn(h, 14 /* DWMWA_CLOAKED */, &cloaked, sizeof(cloaked))) && cloaked != 0;
	}

	// The rectangle a window visibly covers: without the invisible resize
	// borders Windows 10+ adds around ordinary frames, which would otherwise
	// make a neighbouring window "cover" the first 7 pixels of ours.
	bool VisibleRect(HWND h, RECT& r)
	{
		const PFN_DwmGetWindowAttribute pfn = DwmAttribute();
		if (pfn && SUCCEEDED(pfn(h, 9 /* DWMWA_EXTENDED_FRAME_BOUNDS */, &r, sizeof(r))) && r.right > r.left)
			return true;
		return GetWindowRect(h, &r) != FALSE;
	}

	// The enabled window of ours that is modal over the main window. A window
	// blocked by a modal is disabled (Qt's QWindowsWindow sets WS_DISABLED on
	// WindowBlocked; MFC's DoModal disables its owner). Found by z-order rather
	// than ownership: a question dialog raised while Sandbox was not the active
	// app has no owner (EditorDialog.cpp:20 falls back to activeWindow()).
	HWND FindActiveModal(HWND main)
	{
		if (!main || IsWindowEnabled(main))
			return nullptr;
		for (HWND h = GetTopWindow(nullptr); h; h = GetWindow(h, GW_HWNDNEXT))
		{
			RECT r;
			if (h != main && IsOurs(h) && IsWindowVisible(h) && IsWindowEnabled(h) && GetWindowRect(h, &r) && r.right > r.left)
				return h;
		}
		return nullptr;
	}

	// The top-level window a click at pt would reach: the first visible,
	// uncloaked, hit-testable one in z-order. From rectangles, not
	// WindowFromPoint, which sends WM_NCHITTEST and can block.
	HWND TopLevelAt(POINT pt)
	{
		for (HWND h = GetTopWindow(nullptr); h; h = GetWindow(h, GW_HWNDNEXT))
		{
			if (!IsWindowVisible(h) || IsIconic(h) || (GetWindowLongA(h, GWL_EXSTYLE) & WS_EX_TRANSPARENT) || IsCloaked(h))
				continue;
			RECT r;
			if (VisibleRect(h, r) && PtInRect(&r, pt))
				return h;
		}
		return nullptr;
	}

	// SendInput's absolute coordinates are 0..65535 across the virtual desktop.
	// Aim at the CENTRE of pixel p, so the result maps back to p whether
	// Windows rounds or floors (the old (p*65535)/(extent-1) could land one low).
	LONG ToVirtualDesk(int p, int origin, int extent)
	{
		if (extent < 1)
			extent = 1;
		return (LONG)((((long long)(p - origin) * 2 + 1) * 65536LL) / (2LL * extent));
	}

	// Moves the pointer to a physical screen point and returns where it went.
	// The caller holds CScopedPhysicalPixels.
	POINT MoveToScreen(POINT pt)
	{
		INPUT in;
		ZeroStruct(in);
		in.type = INPUT_MOUSE;
		in.mi.dx = ToVirtualDesk(pt.x, GetSystemMetrics(SM_XVIRTUALSCREEN), GetSystemMetrics(SM_CXVIRTUALSCREEN));
		in.mi.dy = ToVirtualDesk(pt.y, GetSystemMetrics(SM_YVIRTUALSCREEN), GetSystemMetrics(SM_CYVIRTUALSCREEN));
		in.mi.dwFlags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK;
		SendInput(1, &in, sizeof(INPUT));

		// The input thread applies the move asynchronously.
		POINT got = { 0, 0 };
		for (int i = 0; i < 10; ++i)
		{
			GetCursorPos(&got);
			if (std::abs(got.x - pt.x) <= 1 && std::abs(got.y - pt.y) <= 1)
				return got;
			Sleep(5);
		}
		SetCursorPos(pt.x, pt.y);
		GetCursorPos(&got);
		return got;
	}

	// Where an input verb's coordinates are measured from, and what it raises.
	//   (default)   relative to the main window's top-left, the screenshot's
	//               space; a modal dialog, if one is up, is raised above it
	//   hwnd=<n>    relative to that window of ours (from bridge.window.list)
	//   modal       relative to the active modal dialog
	//   screen      x y are physical screen pixels
	// Options come before the coordinates, as in the screenshot verb.
	struct SInputTarget
	{
		HWND   main = nullptr;
		HWND   modal = nullptr;
		HWND   origin = nullptr;
		HWND   raise = nullptr;
		bool   screen = false;
		size_t firstArg = 1;
	};

	SInputTarget ParseInputTarget(const std::vector<std::string>& tokens)
	{
		SInputTarget t;
		t.main = FindSandboxWindow();
		if (!t.main)
			throw std::runtime_error("could not find the Sandbox main window");
		t.modal = FindActiveModal(t.main);
		t.origin = t.main;
		t.raise = t.modal ? t.modal : t.main;
		for (; t.firstArg < tokens.size(); ++t.firstArg)
		{
			const std::string& opt = tokens[t.firstArg];
			if (opt.compare(0, 5, "hwnd=") == 0)
			{
				HWND h = (HWND)(uintptr_t)_strtoui64(opt.c_str() + 5, nullptr, 10);
				if (!IsWindow(h) || !IsOurs(h))
					throw std::runtime_error("not a window of this process: " + opt + " (list them with bridge.window.list)");
				t.origin = t.raise = h;
			}
			else if (opt == "modal")
			{
				if (!t.modal)
					throw std::runtime_error("'modal' given, but no modal dialog is up (the main window is enabled)");
				t.origin = t.raise = t.modal;
			}
			else if (opt == "screen")
			{
				t.screen = true;
			}
			else
			{
				break;
			}
		}
		return t;
	}

	POINT ToScreen(const SInputTarget& t, int x, int y)
	{
		POINT p = { x, y };
		if (!t.screen)
		{
			RECT rc;
			ZeroStruct(rc);
			GetWindowRect(t.origin, &rc);
			p.x += rc.left;
			p.y += rc.top;
		}
		return p;
	}

	// Brings the target to the top without activating anything (the foreground
	// lock refuses activation, not z-order; async, so a hung UI thread cannot
	// block this one), then checks what a click at pt would reach. Throws,
	// naming the window, when that is another program's window or one of ours
	// that a modal dialog blocks.
	HWND CheckClickTarget(const SInputTarget& t, POINT pt)
	{
		if (IsIconic(t.main))
			ShowWindowAsync(t.main, SW_RESTORE);
		SetWindowPos(t.raise, HWND_TOP, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS);
		HWND hit = nullptr;
		for (int i = 0; i < 15; ++i)
		{
			hit = TopLevelAt(pt);
			if (hit && IsOurs(hit) && IsWindowEnabled(hit))
				return hit;
			Sleep(30);
		}
		std::ostringstream where;
		where << " at screen (" << pt.x << ", " << pt.y << ")";
		if (!hit)
			throw std::runtime_error("not clicking: no window" + where.str());
		if (!IsOurs(hit))
			throw std::runtime_error("not clicking" + where.str() + ": it is covered by another program's window "
			                         + DescribeWindow(hit) + ", which is not the bridge's to click");
		throw std::runtime_error("not clicking" + where.str() + ": it is on " + DescribeWindow(hit)
		                         + ", which the modal dialog " + DescribeWindow(t.modal) + " blocks -- use bridge.dialog.click, "
		                         "or 'modal' with coordinates inside the dialog");
	}

	// Keys go to the foreground window, whatever it is -- so they are sent only
	// when that is ours: the modal, when one is up, else the main window.
	HWND EnsureKeyTarget()
	{
		HWND main = FindSandboxWindow();
		if (!main)
			throw std::runtime_error("could not find the Sandbox main window");
		HWND want = FindActiveModal(main);
		if (!want)
			want = main;
		if (IsIconic(main))
			ShowWindowAsync(main, SW_RESTORE);
		if (GetForegroundWindow() != want)
			SetForegroundWindow(want);
		for (int i = 0; i < 10 && GetForegroundWindow() != want; ++i)
			Sleep(20);
		HWND fg = GetForegroundWindow();
		if (!IsOurs(fg) || !IsWindowEnabled(fg))
			throw std::runtime_error("keys NOT sent: the foreground window is " + DescribeWindow(fg)
			                         + ", and Windows refused to give the foreground to " + DescribeWindow(want)
			                         + " -- for a dialog use bridge.dialog.click, which needs no focus");
		return fg;
	}

	WORD MouseDownFlag(const std::string& b)
	{
		if (b == "right") return MOUSEEVENTF_RIGHTDOWN;
		if (b == "middle") return MOUSEEVENTF_MIDDLEDOWN;
		return MOUSEEVENTF_LEFTDOWN;
	}
	WORD MouseUpFlag(const std::string& b)
	{
		if (b == "right") return MOUSEEVENTF_RIGHTUP;
		if (b == "middle") return MOUSEEVENTF_MIDDLEUP;
		return MOUSEEVENTF_LEFTUP;
	}

	UINT MouseButton(WORD flag)
	{
		INPUT in;
		ZeroStruct(in);
		in.type = INPUT_MOUSE;
		in.mi.dwFlags = flag;
		return SendInput(1, &in, sizeof(INPUT));
	}

	// Named keys, so callers can say "escape" or "return" instead of a magic
	// number. Anything not named here falls through to a single character.
	WORD NamedKey(const std::string& k)
	{
		if (k == "escape" || k == "esc") return VK_ESCAPE;
		if (k == "enter" || k == "return") return VK_RETURN;
		if (k == "tab") return VK_TAB;
		if (k == "space") return VK_SPACE;
		if (k == "backspace") return VK_BACK;
		if (k == "delete" || k == "del") return VK_DELETE;
		if (k == "up") return VK_UP;
		if (k == "down") return VK_DOWN;
		if (k == "left") return VK_LEFT;
		if (k == "right") return VK_RIGHT;
		if (k == "home") return VK_HOME;
		if (k == "end") return VK_END;
		if (k == "pageup") return VK_PRIOR;
		if (k == "pagedown") return VK_NEXT;
		if (k == "insert") return VK_INSERT;
		if (k.size() >= 2 && k[0] == 'f')
		{
			const int n = std::atoi(k.c_str() + 1);
			if (n >= 1 && n <= 12) return (WORD)(VK_F1 + n - 1);
		}
		return 0;
	}

	// With the scan code filled in, and the extended flag for the keys that
	// have one: without it an arrow arrives as its numeric-keypad twin.
	UINT KeyEvent(WORD vk, bool down)
	{
		INPUT in;
		ZeroStruct(in);
		in.type = INPUT_KEYBOARD;
		in.ki.wVk = vk;
		in.ki.wScan = (WORD)MapVirtualKeyW(vk, MAPVK_VK_TO_VSC);
		in.ki.dwFlags = down ? 0 : KEYEVENTF_KEYUP;
		switch (vk)
		{
		case VK_UP: case VK_DOWN: case VK_LEFT: case VK_RIGHT:
		case VK_HOME: case VK_END: case VK_PRIOR: case VK_NEXT:
		case VK_INSERT: case VK_DELETE:
			in.ki.dwFlags |= KEYEVENTF_EXTENDEDKEY;
			break;
		default:
			break;
		}
		return SendInput(1, &in, sizeof(INPUT));
	}

	// Unicode injection, so typed text does not depend on the active keyboard
	// layout the way virtual-key codes do.
	UINT TypeChar(wchar_t ch)
	{
		INPUT in;
		ZeroStruct(in);
		in.type = INPUT_KEYBOARD;
		in.ki.wScan = (WORD)ch;
		in.ki.dwFlags = KEYEVENTF_UNICODE;
		UINT sent = SendInput(1, &in, sizeof(INPUT));
		in.ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP;
		sent += SendInput(1, &in, sizeof(INPUT));
		return sent;
	}

	// Returns true if cmd was an out-of-band verb and fills 'out'.
	bool TryDispatchOutOfBand(const std::string& cmd, std::string& out)
	{
		const std::vector<std::string> tokens = Tokenize(cmd);
		if (tokens.empty())
			return false;
		const std::string& verb = tokens[0];

		if (verb == "bridge.viewport.screenshot")
		{
			if (tokens.size() < 2)
				throw std::runtime_error("bridge.viewport.screenshot requires an output path, e.g. C:/path/shot.bmp "
				                         "(optionally preceded by hwnd=<decimal> from bridge.window.list)");

			// The window handle, when given, comes FIRST: a path may contain
			// spaces and is rebuilt from the remaining tokens, so a trailing
			// option would be ambiguous.
			size_t firstPathToken = 1;
			HWND target = nullptr;
			int settleMs = 0;
			while (firstPathToken < tokens.size())
			{
				const std::string& opt = tokens[firstPathToken];
				if (opt.compare(0, 5, "hwnd=") == 0)
				{
					target = (HWND)(uintptr_t)_strtoui64(opt.c_str() + 5, nullptr, 10);
					if (!IsWindow(target))
						throw std::runtime_error("no such window: " + opt + " (list them with bridge.window.list)");
					++firstPathToken;
					continue;
				}
				if (opt.compare(0, 7, "settle=") == 0)
				{
					settleMs = std::atoi(opt.c_str() + 7);
					if (settleMs < 0) settleMs = 0;
					if (settleMs > 10000) settleMs = 10000;
					++firstPathToken;
					continue;
				}
				break;
			}
			if (tokens.size() <= firstPathToken)
				throw std::runtime_error("bridge.viewport.screenshot requires an output path after any options");

			std::string path = tokens[firstPathToken];
			for (size_t i = firstPathToken + 1; i < tokens.size(); ++i)
				path += " " + tokens[i];
			out = CaptureSandboxWindow(path, target, settleMs);
			return true;
		}

		// Window state. Out-of-band like the rest of this block: these exist
		// precisely for the case where the editor is not drawing, so routing
		// them through the queue would defeat the point.
		if (verb.compare(0, 14, "bridge.window.") == 0)
		{
			HWND wnd = FindSandboxWindow();
			if (!wnd)
				throw std::runtime_error("could not find the Sandbox main window");
			const std::string what = verb.substr(14);

			// Every visible top-level window this process owns. Modal dialogs are
			// separate top-level windows, and the screenshot verb only ever grabs
			// the largest one -- so without this a dialog is invisible from here,
			// which is precisely the situation that stalls an unattended run. A
			// "Save changes?" prompt on shutdown was caught this way.
			if (what == "list")
			{
				struct SEnum
				{
					DWORD              pid;
					HWND               main;
					HWND               modal;
					std::ostringstream oss;
					int                count;
				};
				// Assigned field by field rather than aggregate-initialised:
				// std::ostringstream is not copyable, and brace-init of a struct
				// holding one is a needless portability gamble.
				SEnum e;
				e.pid = GetCurrentProcessId();
				e.main = FindSandboxWindow();
				e.modal = FindActiveModal(e.main);
				e.count = 0;

				EnumWindows([](HWND hwnd, LPARAM lp) -> BOOL
				{
					SEnum* s = reinterpret_cast<SEnum*>(lp);
					DWORD pid = 0;
					GetWindowThreadProcessId(hwnd, &pid);
					if (pid != s->pid || !IsWindowVisible(hwnd))
						return TRUE;

					char cls[256];
					cls[0] = 0;
					GetClassNameA(hwnd, cls, (int)sizeof(cls) - 1);

					RECT r;
					ZeroStruct(r);
					GetWindowRect(hwnd, &r);

					if (s->count > 0)
						s->oss << ", ";
					s->oss << "{\"hwnd\": " << (unsigned long long)(uintptr_t)hwnd
					       << ", \"title\": \"" << JsonEscape(WindowTitle(hwnd)) << "\""
					       << ", \"class\": \"" << JsonEscape(cls) << "\""
					       << ", \"isMain\": " << (hwnd == s->main ? "true" : "false")
					       << ", \"modal\": " << (hwnd == s->modal ? "true" : "false")
					       << ", \"enabled\": " << (IsWindowEnabled(hwnd) ? "true" : "false")
					       << ", \"owned\": " << (GetWindow(hwnd, GW_OWNER) ? "true" : "false")
					       << ", \"owner\": " << (unsigned long long)(uintptr_t)GetWindow(hwnd, GW_OWNER)
					       << ", \"foreground\": " << (GetForegroundWindow() == hwnd ? "true" : "false")
					       << ", \"rect\": [" << r.left << ", " << r.top << ", " << r.right << ", " << r.bottom << "]}";
					++s->count;
					return TRUE;
				}, reinterpret_cast<LPARAM>(&e));

				// The real foreground window, whoever owns it: a foreign prompt in front
				// of Sandbox (the Firewall's, say) is what makes input go astray, and the
				// list above holds only our own windows.
				std::ostringstream oss;
				oss << "{\"ok\": true, \"verb\": \"list\", \"count\": " << e.count
				    << ", \"activeModal\": " << (unsigned long long)(uintptr_t)e.modal
				    << ", \"foreground\": " << DescribeWindow(GetForegroundWindow())
				    << ", \"windows\": [" << e.oss.str() << "]}";
				out = oss.str();
				return true;
			}

			bool bPosted = false;
			if (what == "restore" || what == "focus")
			{
				if (IsIconic(wnd))
					ShowWindow(wnd, SW_RESTORE);
				SetForegroundWindow(wnd);
				for (int i = 0; i < 10 && GetForegroundWindow() != wnd; ++i)
					Sleep(20);
			}
			else if (what == "minimise" || what == "minimize")
			{
				ShowWindow(wnd, SW_MINIMIZE);
			}
			else if (what == "close")
			{
				// The same path as Alt+F4 or the title bar's X: Qt turns WM_CLOSE into
				// CEditorMainFrame::closeEvent, which asks about unsaved changes first.
				// Posted, so it needs no focus and cannot land in another program's
				// window the way a synthetic Alt+F4 can. Answer a save prompt with
				// bridge.dialog.click.
				bPosted = PostMessageA(wnd, WM_CLOSE, 0, 0) != FALSE;
				if (!bPosted)
					throw std::runtime_error("could not post WM_CLOSE to the main window");
			}
			else if (what != "info")
			{
				throw std::runtime_error("unknown window verb: " + verb + " (info, list, restore, focus, minimise, close)");
			}

			RECT rc;
			ZeroStruct(rc);
			GetWindowRect(wnd, &rc);
			RECT rcClient;
			ZeroStruct(rcClient);
			GetClientRect(wnd, &rcClient);

			std::ostringstream oss;
			oss << "{\"ok\": true, \"verb\": \"" << JsonEscape(what) << "\""
			    << ", \"title\": \"" << JsonEscape(WindowTitle(wnd)) << "\""
			    << ", \"minimised\": " << (IsIconic(wnd) ? "true" : "false")
			    << ", \"foreground\": " << (GetForegroundWindow() == wnd ? "true" : "false")
			    << ", \"foregroundWindow\": " << DescribeWindow(GetForegroundWindow())
			    << ", \"activeModal\": " << (unsigned long long)(uintptr_t)FindActiveModal(wnd)
			    << ", \"closePosted\": " << (bPosted ? "true" : "false")
			    << ", \"rect\": [" << rc.left << ", " << rc.top << ", " << rc.right << ", " << rc.bottom << "]"
			    << ", \"client\": [" << (rcClient.right - rcClient.left) << ", " << (rcClient.bottom - rcClient.top) << "]}";
			out = oss.str();
			return true;
		}

		if (verb.compare(0, 13, "bridge.input.") != 0)
			return false;

		const std::string action = verb.substr(13);
		CScopedPhysicalPixels physicalPixels;

		auto PointJson = [](const POINT& p)
		{
			std::ostringstream oss;
			oss << "[" << p.x << ", " << p.y << "]";
			return oss.str();
		};

		if (action == "click" || action == "move" || action == "doubleclick")
		{
			const SInputTarget target = ParseInputTarget(tokens);
			const size_t a = target.firstArg;
			if (tokens.size() < a + 2)
				throw std::runtime_error(verb + " requires [hwnd=<n>|modal|screen] <x> <y> [button] "
				                         "(window-relative by default: the screenshot's coordinates)");
			const int x = std::atoi(tokens[a].c_str());
			const int y = std::atoi(tokens[a + 1].c_str());
			const std::string button = tokens.size() > a + 2 ? tokens[a + 2] : std::string("left");
			const POINT pt = ToScreen(target, x, y);
			const HWND fgBefore = GetForegroundWindow();

			// A move goes anywhere; a click must reach one of our enabled windows.
			// No SetForegroundWindow for mouse verbs: a real click activates the
			// window it lands on.
			const HWND hit = (action == "move") ? TopLevelAt(pt) : CheckClickTarget(target, pt);
			const POINT got = MoveToScreen(pt);
			if (std::abs(got.x - pt.x) > 1 || std::abs(got.y - pt.y) > 1)
				throw std::runtime_error("the pointer landed at " + PointJson(got) + ", not " + PointJson(pt) + " -- nothing clicked");
			UINT sent = 0;
			if (action != "move")
			{
				const int clicks = (action == "doubleclick") ? 2 : 1;
				for (int i = 0; i < clicks; ++i)
				{
					sent += MouseButton(MouseDownFlag(button));
					sent += MouseButton(MouseUpFlag(button));
				}
				if (sent != (UINT)(clicks * 2))
					throw std::runtime_error("SendInput delivered " + std::to_string(sent) + " of " + std::to_string(clicks * 2)
					                         + " button events (blocked by UIPI or a secure desktop?)");
			}
			std::ostringstream oss;
			oss << "{\"ok\": true, \"action\": \"" << JsonEscape(action) << "\""
			    << ", \"x\": " << x << ", \"y\": " << y
			    << ", \"button\": \"" << JsonEscape(button) << "\""
			    << ", \"screen\": " << PointJson(pt) << ", \"cursor\": " << PointJson(got)
			    << ", \"hit\": " << DescribeWindow(hit)
			    << ", \"foregroundBefore\": " << DescribeWindow(fgBefore) << "}";
			out = oss.str();
			return true;
		}

		if (action == "drag")
		{
			const SInputTarget target = ParseInputTarget(tokens);
			const size_t a = target.firstArg;
			if (tokens.size() < a + 4)
				throw std::runtime_error("bridge.input.drag requires [hwnd=<n>|modal|screen] <x1> <y1> <x2> <y2> [button]");
			const int x1 = std::atoi(tokens[a].c_str());
			const int y1 = std::atoi(tokens[a + 1].c_str());
			const int x2 = std::atoi(tokens[a + 2].c_str());
			const int y2 = std::atoi(tokens[a + 3].c_str());
			const std::string button = tokens.size() > a + 4 ? tokens[a + 4] : std::string("left");
			const POINT from = ToScreen(target, x1, y1);
			const POINT to = ToScreen(target, x2, y2);

			const HWND hit = CheckClickTarget(target, from);
			const POINT got = MoveToScreen(from);
			if (std::abs(got.x - from.x) > 1 || std::abs(got.y - from.y) > 1)
				throw std::runtime_error("the pointer landed at " + PointJson(got) + ", not " + PointJson(from) + " -- nothing dragged");
			MouseButton(MouseDownFlag(button));
			// Interpolate: a single jump often reads as a click rather than a
			// drag, because widgets track intermediate move events.
			const int steps = 16;
			for (int i = 1; i <= steps; ++i)
			{
				POINT p = { from.x + (to.x - from.x) * i / steps, from.y + (to.y - from.y) * i / steps };
				MoveToScreen(p);
			}
			MouseButton(MouseUpFlag(button));

			std::ostringstream oss;
			oss << "{\"ok\": true, \"from\": [" << x1 << ", " << y1 << "]"
			    << ", \"to\": [" << x2 << ", " << y2 << "]"
			    << ", \"screenFrom\": " << PointJson(from) << ", \"screenTo\": " << PointJson(to)
			    << ", \"hit\": " << DescribeWindow(hit) << "}";
			out = oss.str();
			return true;
		}

		if (action == "scroll")
		{
			const SInputTarget target = ParseInputTarget(tokens);
			const size_t a = target.firstArg;
			if (tokens.size() < a + 3)
				throw std::runtime_error("bridge.input.scroll requires [hwnd=<n>|modal|screen] <x> <y> <delta> (delta 120 = one notch up)");
			const int x = std::atoi(tokens[a].c_str());
			const int y = std::atoi(tokens[a + 1].c_str());
			const int delta = std::atoi(tokens[a + 2].c_str());
			const POINT pt = ToScreen(target, x, y);

			const HWND hit = CheckClickTarget(target, pt);
			const POINT got = MoveToScreen(pt);
			if (std::abs(got.x - pt.x) > 1 || std::abs(got.y - pt.y) > 1)
				throw std::runtime_error("the pointer landed at " + PointJson(got) + ", not " + PointJson(pt) + " -- nothing scrolled");
			INPUT in;
			ZeroStruct(in);
			in.type = INPUT_MOUSE;
			in.mi.dwFlags = MOUSEEVENTF_WHEEL;
			in.mi.mouseData = (DWORD)delta;
			SendInput(1, &in, sizeof(INPUT));

			std::ostringstream oss;
			oss << "{\"ok\": true, \"x\": " << x << ", \"y\": " << y << ", \"delta\": " << delta
			    << ", \"screen\": " << PointJson(pt) << ", \"hit\": " << DescribeWindow(hit) << "}";
			out = oss.str();
			return true;
		}

		if (action == "key")
		{
			if (tokens.size() < 2)
				throw std::runtime_error("bridge.input.key requires a key name, e.g. escape / return / f2 / a  (optional trailing ctrl shift alt)");
			std::string key = tokens[1];
			for (char& c : key) c = (char)tolower((unsigned char)c);

			bool ctrl = false, shift = false, alt = false;
			for (size_t i = 2; i < tokens.size(); ++i)
			{
				std::string m = tokens[i];
				for (char& c : m) c = (char)tolower((unsigned char)c);
				if (m == "ctrl" || m == "control") ctrl = true;
				else if (m == "shift") shift = true;
				else if (m == "alt") alt = true;
			}

			WORD vk = NamedKey(key);
			if (!vk && key.size() == 1)
			{
				const char c = (char)toupper((unsigned char)key[0]);
				if ((c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9'))
					vk = (WORD)c;
			}
			if (!vk)
				throw std::runtime_error("unrecognised key: " + key);

			const HWND fg = EnsureKeyTarget();
			UINT sent = 0;
			if (ctrl) sent += KeyEvent(VK_CONTROL, true);
			if (shift) sent += KeyEvent(VK_SHIFT, true);
			if (alt) sent += KeyEvent(VK_MENU, true);
			sent += KeyEvent(vk, true);
			sent += KeyEvent(vk, false);
			if (alt) sent += KeyEvent(VK_MENU, false);
			if (shift) sent += KeyEvent(VK_SHIFT, false);
			if (ctrl) sent += KeyEvent(VK_CONTROL, false);
			const UINT expected = 2u + (ctrl ? 2u : 0u) + (shift ? 2u : 0u) + (alt ? 2u : 0u);
			if (sent != expected)
				throw std::runtime_error("SendInput delivered " + std::to_string(sent) + " of " + std::to_string(expected) + " key events");

			std::ostringstream oss;
			oss << "{\"ok\": true, \"key\": \"" << JsonEscape(key) << "\""
			    << ", \"ctrl\": " << (ctrl ? "true" : "false")
			    << ", \"shift\": " << (shift ? "true" : "false")
			    << ", \"alt\": " << (alt ? "true" : "false")
			    << ", \"to\": " << DescribeWindow(fg) << "}";
			out = oss.str();
			return true;
		}

		if (action == "text")
		{
			if (tokens.size() < 2)
				throw std::runtime_error("bridge.input.text requires text to type");
			std::string text = tokens[1];
			for (size_t i = 2; i < tokens.size(); ++i)
				text += " " + tokens[i];

			const HWND fg = EnsureKeyTarget();
			UINT sent = 0;
			for (char c : text)
				sent += TypeChar((wchar_t)(unsigned char)c);
			if (sent != (UINT)(text.size() * 2))
				throw std::runtime_error("SendInput delivered " + std::to_string(sent) + " of " + std::to_string(text.size() * 2) + " key events");

			std::ostringstream oss;
			oss << "{\"ok\": true, \"typed\": \"" << JsonEscape(text) << "\", \"to\": " << DescribeWindow(fg) << "}";
			out = oss.str();
			return true;
		}

		throw std::runtime_error("unknown input action: " + action
		                         + " (click, doubleclick, move, drag, scroll, key, text)");
	}
}

//////////////////////////////////////////////////////////////////////////
// Dialogs, answered by name (bridge.dialog.list / bridge.dialog.click).
//
// Synthetic input cannot answer a modal reliably: it needs the foreground,
// which Windows refuses to a background process, and keys go to whatever has
// it (8aa-M: the Firewall prompt had it). Qt's buttons are alien widgets with
// no HWND, so there is nothing to send BM_CLICK to either. What does work is
// asking Qt itself, on the GUI thread: find the dialog, find the button, and
// queue a click() -- exactly what a mouse click does, so CQuestionDialog's
// ButtonClicked records the StandardButton and Execute() returns it
// (QuestionDialog.cpp:124-128). accept()/done() would not: Execute() would
// then report NoButton or Cancel (QuestionDialog.cpp:142-150).
//
// These run from a dialog-only queue that the WM_TIMER pump drains even while
// the ordinary queue is held by the command that opened the dialog.
//////////////////////////////////////////////////////////////////////////
namespace
{
	std::string QtUtf8(const QString& s)
	{
		const QByteArray bytes = s.toUtf8();
		return std::string(bytes.constData(), (size_t)bytes.size());
	}

	std::string Lower(std::string s)
	{
		for (char& c : s)
			c = (char)tolower((unsigned char)c);
		return s;
	}

	// "&Yes" -> "yes", "Don't Save" -> "don'tsave": case, mnemonics and spaces
	// do not matter when naming a button.
	std::string NormaliseButtonLabel(const std::string& s)
	{
		std::string r;
		for (char c : s)
			if (c != '&' && c != ' ')
				r += (char)tolower((unsigned char)c);
		return r;
	}

	// Locale-independent names for the standard buttons.
	const char* StandardButtonName(QDialogButtonBox::StandardButton b)
	{
		switch (b)
		{
		case QDialogButtonBox::Ok:              return "ok";
		case QDialogButtonBox::Save:            return "save";
		case QDialogButtonBox::SaveAll:         return "saveall";
		case QDialogButtonBox::Open:            return "open";
		case QDialogButtonBox::Yes:             return "yes";
		case QDialogButtonBox::YesToAll:        return "yestoall";
		case QDialogButtonBox::No:              return "no";
		case QDialogButtonBox::NoToAll:         return "notoall";
		case QDialogButtonBox::Abort:           return "abort";
		case QDialogButtonBox::Retry:           return "retry";
		case QDialogButtonBox::Ignore:          return "ignore";
		case QDialogButtonBox::Close:           return "close";
		case QDialogButtonBox::Cancel:          return "cancel";
		case QDialogButtonBox::Discard:         return "discard";
		case QDialogButtonBox::Help:            return "help";
		case QDialogButtonBox::Apply:           return "apply";
		case QDialogButtonBox::Reset:           return "reset";
		case QDialogButtonBox::RestoreDefaults: return "restoredefaults";
		default:                                return "";
		}
	}

	struct SDialogButton
	{
		QAbstractButton* pQt = nullptr;
		HWND             hWin = nullptr;
		std::string      text;
		std::string      standard;
		bool             enabled = false;
		bool             isDefault = false;
	};

	struct SDialog
	{
		QWidget*                   pQt = nullptr;
		HWND                       hWin = nullptr;
		std::string                title;
		std::string                className;
		bool                       modal = false;
		bool                       active = false;
		std::vector<std::string>   labels;
		std::vector<SDialogButton> buttons;
	};

	void CollectQtDialog(QWidget* pDialog, SDialog& d)
	{
		for (QAbstractButton* pButton : pDialog->findChildren<QAbstractButton*>())
		{
			// Only this dialog's own buttons: a dialog it opened is a QObject child too.
			if (!pButton->isVisible() || pButton->window() != pDialog)
				continue;
			SDialogButton b;
			b.pQt = pButton;
			b.text = QtUtf8(pButton->text());
			if (QDialogButtonBox* pBox = qobject_cast<QDialogButtonBox*>(pButton->parentWidget()))
				b.standard = StandardButtonName(pBox->standardButton(pButton));
			b.enabled = pButton->isEnabled();
			QPushButton* pPush = qobject_cast<QPushButton*>(pButton);
			b.isDefault = pPush != nullptr && pPush->isDefault();
			d.buttons.push_back(b);
		}
		for (QLabel* pLabel : pDialog->findChildren<QLabel*>())
		{
			if (pLabel->isVisible() && pLabel->window() == pDialog && !pLabel->text().isEmpty())
				d.labels.push_back(QtUtf8(pLabel->text()).substr(0, 400));
		}
	}

	BOOL CALLBACK CollectWin32DialogChild(HWND child, LPARAM lp)
	{
		SDialog* d = reinterpret_cast<SDialog*>(lp);
		if (!IsWindowVisible(child))
			return TRUE;
		char cls[64];
		cls[0] = 0;
		GetClassNameA(child, cls, (int)sizeof(cls) - 1);
		if (_stricmp(cls, "Button") == 0)
		{
			const LONG type = GetWindowLongA(child, GWL_STYLE) & BS_TYPEMASK;
			if (type == BS_PUSHBUTTON || type == BS_DEFPUSHBUTTON)
			{
				SDialogButton b;
				b.hWin = child;
				b.text = WindowTitle(child);
				b.enabled = IsWindowEnabled(child) != FALSE;
				b.isDefault = (type == BS_DEFPUSHBUTTON);
				switch (GetDlgCtrlID(child))
				{
				case IDOK:     b.standard = "ok"; break;
				case IDCANCEL: b.standard = "cancel"; break;
				case IDYES:    b.standard = "yes"; break;
				case IDNO:     b.standard = "no"; break;
				case IDABORT:  b.standard = "abort"; break;
				case IDRETRY:  b.standard = "retry"; break;
				case IDIGNORE: b.standard = "ignore"; break;
				default:       break;
				}
				d->buttons.push_back(b);
			}
		}
		else if (_stricmp(cls, "Static") == 0)
		{
			const std::string text = WindowTitle(child);
			if (!text.empty())
				d->labels.push_back(text.substr(0, 400));
		}
		return TRUE;
	}

	BOOL CALLBACK CollectWin32Dialog(HWND h, LPARAM lp)
	{
		std::vector<SDialog>* pOut = reinterpret_cast<std::vector<SDialog>*>(lp);
		if (!IsOurs(h) || !IsWindowVisible(h))
			return TRUE;
		char cls[64];
		cls[0] = 0;
		GetClassNameA(h, cls, (int)sizeof(cls) - 1);
		if (strcmp(cls, "#32770") != 0)
			return TRUE;
		SDialog d;
		d.hWin = h;
		d.title = WindowTitle(h);
		d.className = cls;
		d.modal = true;
		d.active = (GetForegroundWindow() == h);
		EnumChildWindows(h, &CollectWin32DialogChild, reinterpret_cast<LPARAM>(&d));
		pOut->push_back(d);
		return TRUE;
	}

	// Every visible dialog of the editor: Qt dialogs (and any modal top-level
	// widget), then the Win32 #32770 ones -- message boxes and the few MFC
	// prompts left. GUI thread only.
	std::vector<SDialog> CollectDialogs()
	{
		std::vector<SDialog> result;
		QWidget* pModal = QApplication::activeModalWidget();
		QWidget* pActive = QApplication::activeWindow();
		for (QWidget* w : QApplication::topLevelWidgets())
		{
			if (!w->isVisible() || (qobject_cast<QDialog*>(w) == nullptr && !w->isModal()))
				continue;
			SDialog d;
			d.pQt = w;
			d.hWin = (HWND)w->internalWinId();
			d.title = QtUtf8(w->windowTitle());
			d.className = w->metaObject()->className();
			d.modal = (w == pModal) || w->isModal();
			d.active = (w == pActive);
			CollectQtDialog(w, d);
			result.push_back(d);
		}
		EnumWindows(&CollectWin32Dialog, reinterpret_cast<LPARAM>(&result));
		return result;
	}

	std::string ButtonsJson(const SDialog& d)
	{
		std::ostringstream oss;
		oss << "[";
		for (size_t i = 0; i < d.buttons.size(); ++i)
		{
			const SDialogButton& b = d.buttons[i];
			oss << (i ? ", " : "") << "{\"text\": \"" << JsonEscape(b.text) << "\""
			    << ", \"standard\": \"" << JsonEscape(b.standard) << "\""
			    << ", \"enabled\": " << (b.enabled ? "true" : "false")
			    << ", \"default\": " << (b.isDefault ? "true" : "false") << "}";
		}
		oss << "]";
		return oss.str();
	}

	std::string DialogJson(const SDialog& d)
	{
		std::ostringstream oss;
		oss << "{\"kind\": \"" << (d.pQt ? "qt" : "win32") << "\""
		    << ", \"title\": \"" << JsonEscape(d.title) << "\""
		    << ", \"class\": \"" << JsonEscape(d.className) << "\""
		    << ", \"hwnd\": " << (unsigned long long)(uintptr_t)d.hWin
		    << ", \"modal\": " << (d.modal ? "true" : "false")
		    << ", \"active\": " << (d.active ? "true" : "false")
		    << ", \"labels\": [";
		for (size_t i = 0; i < d.labels.size(); ++i)
			oss << (i ? ", " : "") << "\"" << JsonEscape(d.labels[i]) << "\"";
		oss << "], \"buttons\": " << ButtonsJson(d) << "}";
		return oss.str();
	}

	std::string DialogTitles(const std::vector<SDialog>& dialogs)
	{
		std::string titles;
		for (const SDialog& d : dialogs)
			titles += (titles.empty() ? "'" : ", '") + d.title + "'";
		return titles.empty() ? std::string("none") : titles;
	}

	// bridge.dialog.click <button> [title substring...]
	std::string DialogClick(const std::vector<std::string>& tokens)
	{
		if (tokens.size() < 2)
			throw std::runtime_error("bridge.dialog.click requires a button: yes | no | ok | cancel | save | discard | default | "
			                         "<its text without spaces>, optionally followed by part of the dialog's title");
		const std::string want = NormaliseButtonLabel(tokens[1]);
		std::string titleFilter;
		for (size_t i = 2; i < tokens.size(); ++i)
			titleFilter += (titleFilter.empty() ? "" : " ") + tokens[i];
		titleFilter = Lower(titleFilter);

		const std::vector<SDialog> dialogs = CollectDialogs();
		const SDialog* pDialog = nullptr;
		if (!titleFilter.empty())
		{
			for (const SDialog& d : dialogs)
			{
				if (Lower(d.title).find(titleFilter) != std::string::npos)
				{
					pDialog = &d;
					break;
				}
			}
			if (!pDialog)
				throw std::runtime_error("no open dialog's title contains '" + titleFilter + "'; open: " + DialogTitles(dialogs));
		}
		else
		{
			// The active modal by default; else the one Win32 dialog; else the only dialog.
			QWidget* pModal = QApplication::activeModalWidget();
			const SDialog* pWin32 = nullptr;
			int win32Count = 0;
			for (const SDialog& d : dialogs)
			{
				if (pModal != nullptr && d.pQt == pModal)
					pDialog = &d;
				if (!d.pQt)
				{
					pWin32 = &d;
					++win32Count;
				}
			}
			if (!pDialog && win32Count == 1)
				pDialog = pWin32;
			if (!pDialog && dialogs.size() == 1)
				pDialog = &dialogs[0];
			if (!pDialog)
				throw std::runtime_error(dialogs.empty() ? std::string("no dialog is open")
				                         : "several dialogs are open and none is the active modal -- add part of a title: " + DialogTitles(dialogs));
		}

		std::vector<const SDialogButton*> hits;
		for (const SDialogButton& b : pDialog->buttons)
		{
			const bool match = (want == "default") ? b.isDefault
			                   : (NormaliseButtonLabel(b.text) == want || (!b.standard.empty() && b.standard == want));
			if (match)
				hits.push_back(&b);
		}
		if (hits.size() != 1)
			throw std::runtime_error(std::string(hits.empty() ? "no" : "more than one") + " button '" + tokens[1]
			                         + "' in '" + pDialog->title + "'; its buttons: " + ButtonsJson(*pDialog));
		const SDialogButton& b = *hits[0];
		if (!b.enabled)
			throw std::runtime_error("button '" + b.text + "' in '" + pDialog->title + "' is disabled");

		// Queued: if the button opens ANOTHER modal, its loop runs later, from the
		// event loop, instead of inside this verb. Confirm with bridge.dialog.list.
		bool queued = false;
		if (b.pQt)
			queued = QMetaObject::invokeMethod(b.pQt, "click", Qt::QueuedConnection);
		else
			queued = PostMessageA(pDialog->hWin, WM_COMMAND, MAKEWPARAM(GetDlgCtrlID(b.hWin), BN_CLICKED), (LPARAM)b.hWin) != FALSE;
		if (!queued)
			throw std::runtime_error("could not queue the click on '" + b.text + "'");

		std::ostringstream oss;
		oss << "{\"ok\": true, \"verb\": \"click\", \"dialog\": \"" << JsonEscape(pDialog->title) << "\""
		    << ", \"kind\": \"" << (pDialog->pQt ? "qt" : "win32") << "\""
		    << ", \"button\": \"" << JsonEscape(b.text) << "\""
		    << ", \"standard\": \"" << JsonEscape(b.standard) << "\", \"queued\": true}";
		return oss.str();
	}
}

std::string CClaudeBridgePlugin::DispatchDialogCommand(const std::string& cmd)
{
	const std::vector<std::string> tokens = Tokenize(cmd);
	const std::string what = tokens.empty() ? std::string() : tokens[0].substr(14);
	if (what == "list")
	{
		const std::vector<SDialog> dialogs = CollectDialogs();
		QWidget* pModal = QApplication::activeModalWidget();
		std::ostringstream oss;
		oss << "{\"ok\": true, \"verb\": \"list\", \"count\": " << dialogs.size()
		    << ", \"activeModal\": \"" << JsonEscape(pModal ? QtUtf8(pModal->windowTitle()) : std::string()) << "\""
		    << ", \"dialogs\": [";
		for (size_t i = 0; i < dialogs.size(); ++i)
			oss << (i ? ", " : "") << DialogJson(dialogs[i]);
		oss << "]}";
		return oss.str();
	}
	if (what == "click")
		return DialogClick(tokens);
	throw std::runtime_error("unknown dialog verb: " + (tokens.empty() ? cmd : tokens[0]) + " (list, click)");
}

void CClaudeBridgePlugin::PumpDialogTick()
{
	std::deque<std::unique_ptr<SPendingBridgeCommand>> toRun;
	{
		std::lock_guard<std::mutex> lock(m_dialogQueueMutex);
		toRun.swap(m_dialogQueue);
	}
	for (auto& pending : toRun)
	{
		try
		{
			pending->result.set_value(DispatchDialogCommand(pending->cmd));
		}
		catch (...)
		{
			pending->result.set_exception(std::current_exception());
		}
	}
}

//////////////////////////////////////////////////////////////////////////
// Main-thread pump.
//
// eNotify_OnIdleUpdate only fires while the editor is drawing. It stops when
// the window is minimised, while a menu is open, and for the whole life of a
// modal dialog -- each of which runs its own nested message loop. Every
// queued command then times out, which is the worst possible moment to lose
// the bridge: the command needed to get *out* of that state is queued behind
// the same stall. Observed repeatedly -- a minimised window alone was enough
// to make every non-out-of-band verb time out.
//
// A message-only window with a WM_TIMER fixes it at the root. Windows posts
// WM_TIMER to the owning thread's message queue, and EVERY message loop
// dispatches it -- Qt's, the menu loop's, a modal dialog's, and a minimised
// app's. So the queue drains on the main thread whatever the editor happens
// to be doing. The idle-tick path is left exactly as it was; this is a floor
// underneath it, not a replacement.
//////////////////////////////////////////////////////////////////////////
namespace
{
	const char* const kPumpWindowClass = "ClaudeBridgePumpWindow";
	const UINT_PTR    kPumpTimerId = 1;
	const UINT        kPumpIntervalMs = 25;

	CClaudeBridgePlugin* g_pPumpOwner = nullptr;
	HWND                 g_pumpWindow = nullptr;
	bool                 g_bDraining = false;
	bool                 g_bDialogDraining = false;

	LRESULT CALLBACK PumpWndProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
	{
		if (msg == WM_TIMER && wp == kPumpTimerId)
		{
			// Dialog verbs first, and regardless of g_bDraining: when a queued
			// command has opened a modal, this tick is running inside that
			// dialog's loop, and a dialog verb is the way out of it. Dialog
			// verbs never open anything themselves (their click is queued).
			if (g_pPumpOwner && !g_bDialogDraining)
			{
				g_bDialogDraining = true;
				g_pPumpOwner->PumpDialogTick();
				g_bDialogDraining = false;
			}

			// A dispatched command can itself pump messages (anything that
			// opens a dialog does), which would re-enter this handler. The
			// guard keeps one drain in flight at a time; the queue is not
			// lost, just deferred to the next tick.
			if (g_pPumpOwner && !g_bDraining)
			{
				g_bDraining = true;
				g_pPumpOwner->PumpTick();
				g_bDraining = false;
			}
			return 0;
		}
		return DefWindowProcA(hwnd, msg, wp, lp);
	}

	void StartMainThreadPump(CClaudeBridgePlugin* pOwner)
	{
		g_pPumpOwner = pOwner;

		WNDCLASSEXA wc;
		ZeroStruct(wc);
		wc.cbSize = sizeof(wc);
		wc.lpfnWndProc = &PumpWndProc;
		wc.hInstance = GetModuleHandle(nullptr);
		wc.lpszClassName = kPumpWindowClass;
		// A failure here is almost always ERROR_CLASS_ALREADY_EXISTS from a
		// previous load, which is harmless -- CreateWindowEx below is the
		// call whose result actually matters.
		RegisterClassExA(&wc);

		g_pumpWindow = CreateWindowExA(0, kPumpWindowClass, "", 0, 0, 0, 0, 0,
		                               HWND_MESSAGE, nullptr, wc.hInstance, nullptr);
		if (!g_pumpWindow)
		{
			if (gEnv && gEnv->pLog)
				gEnv->pLog->LogWarning("[ClaudeBridgePlugin] Could not create the main-thread pump window (error %u) -- "
				                       "falling back to the idle tick alone, which stalls while minimised or in a dialog",
				                       GetLastError());
			return;
		}

		SetTimer(g_pumpWindow, kPumpTimerId, kPumpIntervalMs, nullptr);
	}

	void StopMainThreadPump()
	{
		if (g_pumpWindow)
		{
			KillTimer(g_pumpWindow, kPumpTimerId);
			DestroyWindow(g_pumpWindow);
			g_pumpWindow = nullptr;
		}
		g_pPumpOwner = nullptr;
	}
}

REGISTER_PLUGIN(CClaudeBridgePlugin);

CClaudeBridgePlugin::CClaudeBridgePlugin()
{
	m_bridgeServer = std::make_unique<TcpCommandServer>(
		kBridgePort,
		[this](const std::string& cmd) { return ExecuteAndCaptureOutput(cmd); });

	// Before the listener, so the very first command already has a pump to
	// run on even if the editor is minimised at startup.
	StartMainThreadPump(this);

	if (!m_bridgeServer->Start())
	{
		if (gEnv && gEnv->pLog)
			gEnv->pLog->LogError("[ClaudeBridgePlugin] Failed to start TCP listener on 127.0.0.1:%u", kBridgePort);
	}
	else
	{
		if (gEnv && gEnv->pLog)
			gEnv->pLog->Log("[ClaudeBridgePlugin] Listening on 127.0.0.1:%u (idle tick + WM_TIMER pump; "
			                "works outside Play mode, while minimised, and inside modal dialogs)", kBridgePort);
	}
}

CClaudeBridgePlugin::~CClaudeBridgePlugin()
{
	if (m_bridgeServer)
		m_bridgeServer->Stop();

	StopMainThreadPump();
}

std::string CClaudeBridgePlugin::ExecuteAndCaptureOutput(const std::string& cmd)
{
	// Screenshot and synthetic input touch only Win32, never engine state, so
	// they run right here on the TCP client thread instead of being queued.
	// This is what keeps the bridge usable when a modal dialog has stalled the
	// editor: the idle tick is not running, so anything queued would time out,
	// including the very click needed to dismiss the dialog.
	{
		std::string oob;
		if (TryDispatchOutOfBand(cmd, oob))
			return oob;
	}

	// Dialog verbs have their own queue: the ordinary one is held for the whole
	// life of a modal opened by the command it is running.
	if (cmd.compare(0, 14, "bridge.dialog.") == 0)
	{
		auto pending = std::make_unique<SPendingBridgeCommand>();
		pending->cmd = cmd;
		std::future<std::string> future = pending->result.get_future();
		{
			std::lock_guard<std::mutex> lock(m_dialogQueueMutex);
			m_dialogQueue.push_back(std::move(pending));
		}
		if (future.wait_for(std::chrono::seconds(kDialogTimeoutSeconds)) != std::future_status::ready)
			throw std::runtime_error("timed out: the editor main thread ran no message loop for 10 s -- "
			                         "busy inside a long command, or hung (a modal dialog alone does not cause this)");
		return future.get();
	}

	auto pending = std::make_unique<SPendingBridgeCommand>();
	pending->cmd = cmd;
	std::future<std::string> future = pending->result.get_future();

	{
		std::lock_guard<std::mutex> lock(m_bridgeQueueMutex);
		m_bridgeQueue.push_back(std::move(pending));
	}

	if (future.wait_for(std::chrono::seconds(kCommandTimeoutSeconds)) != std::future_status::ready)
		throw std::runtime_error("timed out waiting for the editor main thread to run this command "
		                         "(both the idle tick and the WM_TIMER pump failed to drain the queue -- "
		                         "the main thread is genuinely blocked, not merely not drawing)");

	return future.get();
}

void CClaudeBridgePlugin::OnEditorNotifyEvent(EEditorNotifyEvent event)
{
	// Deliberately only eNotify_OnIdleUpdate -- that's the one guaranteed
	// to fire every frame regardless of Play/edit mode (see IEditor.h).
	if (event != eNotify_OnIdleUpdate)
		return;

	DrainQueue();
}

void CClaudeBridgePlugin::PumpTick()
{
	DrainQueue();
}

void CClaudeBridgePlugin::DrainQueue()
{
	std::deque<std::unique_ptr<SPendingBridgeCommand>> toRun;
	{
		std::lock_guard<std::mutex> lock(m_bridgeQueueMutex);
		toRun.swap(m_bridgeQueue);
	}

	for (auto& pending : toRun)
	{
		try
		{
			pending->result.set_value(DispatchBridgeCommand(pending->cmd));
		}
		catch (...)
		{
			pending->result.set_exception(std::current_exception());
		}
	}
}

namespace
{
	// ---- bridge.schematyc.dump -------------------------------------------------------------------------------------------------
	// Everything the engine and the game registered with Schematyc's environment: modules, data types, signals, functions, classes, interfaces, components and actions, each
	// with its GUID, name, the module path it sits under, the description the registrant gave, its flags and the source file and line it was registered from; components with
	// their label, editor category, icon, flags and every member (name, label, description, type); functions and signals with their inputs and outputs. It is the engine's own
	// list of what the Schematyc editor offers and what an entity component is made of, read from IEnvRegistry rather than scraped from the source.
	const char* EnvTypeName(Schematyc::EEnvElementType t)
	{
		switch (t)
		{
		case Schematyc::EEnvElementType::Root:              return "Root";
		case Schematyc::EEnvElementType::Module:            return "Module";
		case Schematyc::EEnvElementType::DataType:          return "DataType";
		case Schematyc::EEnvElementType::Signal:            return "Signal";
		case Schematyc::EEnvElementType::Function:          return "Function";
		case Schematyc::EEnvElementType::Class:             return "Class";
		case Schematyc::EEnvElementType::Interface:         return "Interface";
		case Schematyc::EEnvElementType::InterfaceFunction: return "InterfaceFunction";
		case Schematyc::EEnvElementType::Component:         return "Component";
		case Schematyc::EEnvElementType::Action:            return "Action";
		}
		return "Unknown";
	}

	// Whether the object verbs (create, transform, material, delete) record an editor undo step (bridge.undo.mode on|off). Off by default: the test tools make and delete scratch objects, and the user's Ctrl+Z
	// should not bring those back; the tools that place or delete something the user asked for switch it on around the call.
	static bool s_claudeUndo = false;

	// Text from the engine's registrants is not always UTF-8 (a degree sign in Latin-1 broke the first dump for a JSON reader): keep valid UTF-8 sequences, replace any other byte with '?'.
	std::string EnvStr(const char* s)
	{
		std::string in(s ? s : ""), out;
		out.reserve(in.size());
		for (size_t i = 0; i < in.size();)
		{
			const unsigned char c = static_cast<unsigned char>(in[i]);
			size_t len = c < 0x80 ? 1 : (c >= 0xC2 && c <= 0xDF) ? 2 : (c >= 0xE0 && c <= 0xEF) ? 3 : (c >= 0xF0 && c <= 0xF4) ? 4 : 0;
			bool ok = len > 0 && i + len <= in.size();
			for (size_t k = 1; ok && k < len; ++k)
				ok = (static_cast<unsigned char>(in[i + k]) & 0xC0) == 0x80;
			if (ok)
			{
				out.append(in, i, len);
				i += len;
			}
			else
			{
				out += '?';
				++i;
			}
		}
		return JsonEscape(out);
	}

	std::string EnvPath(const Schematyc::IEnvElement& e)
	{
		std::vector<std::string> parts;
		for (const Schematyc::IEnvElement* p = e.GetParent(); p; p = p->GetParent())
		{
			if (p->GetType() != Schematyc::EEnvElementType::Root && p->GetName() && p->GetName()[0])
				parts.push_back(p->GetName());
		}
		std::string path;
		for (size_t i = parts.size(); i-- > 0;)
			path += (path.empty() ? "" : "/") + parts[i];
		return path;
	}

	void EnvDumpMembers(std::ostringstream& o, const Schematyc::CClassDesc& desc)
	{
		o << "[";
		bool first = true;
		for (const Schematyc::CClassMemberDesc& m : desc.GetMembers())
		{
			o << (first ? "" : ",");
			first = false;
			const Schematyc::CCommonTypeDesc& td = m.GetTypeDesc();
			o << "{\"id\":" << m.GetId()
			  << ",\"name\":\"" << EnvStr(m.GetName()) << "\""
			  << ",\"label\":\"" << EnvStr(m.GetLabel()) << "\""
			  << ",\"description\":\"" << EnvStr(m.GetDescription()) << "\""
			  << ",\"type\":\"" << EnvStr(td.GetName().c_str()) << "\""
			  << ",\"type_label\":\"" << EnvStr(td.GetLabel()) << "\"}";
		}
		o << "]";
	}

	std::string EnvTypeOf(const Schematyc::CAnyConstPtr& data)
	{
		if (!data)
			return std::string();
		return std::string(data->GetTypeDesc().GetName().c_str());
	}

	std::string SchematycDump(const std::string& outPath)
	{
		if (!gEnv || !gEnv->pSchematyc)
			throw std::runtime_error("Schematyc is not loaded (gEnv->pSchematyc is null)");
		const Schematyc::IEnvRegistry& registry = gEnv->pSchematyc->GetEnvRegistry();

		std::ostringstream items;
		std::map<std::string, int> counts;
		int total = 0;

		auto visit = [&](const Schematyc::IEnvElement& e) -> Schematyc::EVisitStatus
		{
			const Schematyc::EEnvElementType type = e.GetType();
			const Schematyc::SSourceFileInfo src = e.GetSourceFileInfo();
			items << (total ? ",\n" : "") << "{\"type\":\"" << EnvTypeName(type) << "\""
			      << ",\"guid\":\"" << e.GetGUID().ToString().c_str() << "\""
			      << ",\"name\":\"" << EnvStr(e.GetName()) << "\""
			      << ",\"path\":\"" << EnvStr(EnvPath(e).c_str()) << "\""
			      << ",\"description\":\"" << EnvStr(e.GetDescription()) << "\""
			      << ",\"deprecated\":" << (e.GetFlags().Check(Schematyc::EEnvElementFlags::Deprecated) ? "true" : "false")
			      << ",\"file\":\"" << EnvStr(src.szFileName) << "\",\"line\":" << src.lineNumber;
			++total;
			++counts[EnvTypeName(type)];

			switch (type)
			{
			case Schematyc::EEnvElementType::Component:
				{
					if (const Schematyc::IEnvComponent* pComponent = Schematyc::DynamicCast<Schematyc::IEnvComponent>(&e))
					{
						const CEntityComponentClassDesc& d = pComponent->GetDesc();
						struct SFlag { const char* name; EEntityComponentFlags flag; };
						static const SFlag flags[] = {
							{ "Singleton", EEntityComponentFlags::Singleton }, { "Legacy", EEntityComponentFlags::Legacy }, { "Transform", EEntityComponentFlags::Transform },
							{ "Socket", EEntityComponentFlags::Socket }, { "Attach", EEntityComponentFlags::Attach }, { "Schematyc", EEntityComponentFlags::Schematyc },
							{ "SchematycEditable", EEntityComponentFlags::SchematycEditable }, { "UserAdded", EEntityComponentFlags::UserAdded }, { "NoSave", EEntityComponentFlags::NoSave },
							{ "NetNotReplicate", EEntityComponentFlags::NetNotReplicate }, { "HideFromInspector", EEntityComponentFlags::HideFromInspector },
							{ "ServerOnly", EEntityComponentFlags::ServerOnly }, { "ClientOnly", EEntityComponentFlags::ClientOnly }, { "HiddenFromUser", EEntityComponentFlags::HiddenFromUser },
							{ "NoCreationOffset", EEntityComponentFlags::NoCreationOffset } };
						items << ",\"component\":{\"label\":\"" << EnvStr(d.GetLabel()) << "\",\"editor_category\":\"" << EnvStr(d.GetEditorCategory()) << "\",\"icon\":\"" << EnvStr(d.GetIcon())
						      << "\",\"size\":" << pComponent->GetSize() << ",\"flags\":[";
						bool first = true;
						for (const SFlag& f : flags)
						{
							if (d.GetComponentFlags().Check(f.flag))
							{
								items << (first ? "" : ",") << "\"" << f.name << "\"";
								first = false;
							}
						}
						items << "],\"interactions\":[";
						first = true;
						for (const SEntityComponentRequirements& r : d.GetComponentInteractions())
						{
							items << (first ? "" : ",") << "{\"kind\":" << static_cast<int>(r.type) << ",\"guid\":\"" << r.guid.ToString().c_str() << "\"}";
							first = false;
						}
						items << "],\"members\":";
						EnvDumpMembers(items, d);
						items << "}";
					}
					break;
				}
			case Schematyc::EEnvElementType::Signal:
				{
					if (const Schematyc::IEnvSignal* pSignal = Schematyc::DynamicCast<Schematyc::IEnvSignal>(&e))
					{
						items << ",\"signal\":{\"members\":";
						EnvDumpMembers(items, pSignal->GetDesc());
						items << "}";
					}
					break;
				}
			case Schematyc::EEnvElementType::DataType:
				{
					if (const Schematyc::IEnvDataType* pData = Schematyc::DynamicCast<Schematyc::IEnvDataType>(&e))
					{
						const Schematyc::CCommonTypeDesc& td = pData->GetDesc();
						items << ",\"datatype\":{\"label\":\"" << EnvStr(td.GetLabel()) << "\",\"type\":\"" << EnvStr(td.GetName().c_str()) << "\",\"size\":" << td.GetSize()
						      << ",\"category\":" << static_cast<int>(td.GetCategory()) << ",\"icon\":\"" << EnvStr(td.GetIcon()) << "\"}";
					}
					break;
				}
			case Schematyc::EEnvElementType::Function:
				{
					if (const Schematyc::IEnvFunction* pFunction = Schematyc::DynamicCast<Schematyc::IEnvFunction>(&e))
					{
						const Schematyc::EnvFunctionFlags ff = pFunction->GetFunctionFlags();
						items << ",\"function\":{\"member\":" << (ff.Check(Schematyc::EEnvFunctionFlags::Member) ? "true" : "false")
						      << ",\"pure\":" << (ff.Check(Schematyc::EEnvFunctionFlags::Pure) ? "true" : "false")
						      << ",\"const\":" << (ff.Check(Schematyc::EEnvFunctionFlags::Const) ? "true" : "false")
						      << ",\"construction\":" << (ff.Check(Schematyc::EEnvFunctionFlags::Construction) ? "true" : "false");
						if (const Schematyc::CCommonTypeDesc* pObjectType = pFunction->GetObjectTypeDesc())
							items << ",\"object_type\":\"" << EnvStr(pObjectType->GetName().c_str()) << "\"";
						items << ",\"inputs\":[";
						for (uint32 i = 0; i < pFunction->GetInputCount(); ++i)
							items << (i ? "," : "") << "{\"name\":\"" << EnvStr(pFunction->GetInputName(i)) << "\",\"description\":\"" << EnvStr(pFunction->GetInputDescription(i))
							      << "\",\"type\":\"" << EnvStr(EnvTypeOf(pFunction->GetInputData(i)).c_str()) << "\"}";
						items << "],\"outputs\":[";
						for (uint32 i = 0; i < pFunction->GetOutputCount(); ++i)
							items << (i ? "," : "") << "{\"name\":\"" << EnvStr(pFunction->GetOutputName(i)) << "\",\"description\":\"" << EnvStr(pFunction->GetOutputDescription(i))
							      << "\",\"type\":\"" << EnvStr(EnvTypeOf(pFunction->GetOutputData(i)).c_str()) << "\"}";
						items << "]}";
					}
					break;
				}
			case Schematyc::EEnvElementType::Class:
				{
					if (const Schematyc::IEnvClass* pClass = Schematyc::DynamicCast<Schematyc::IEnvClass>(&e))
						items << ",\"class\":{\"components\":" << pClass->GetComponentCount() << ",\"interfaces\":" << pClass->GetInterfaceCount() << "}";
					break;
				}
			default:
				break;
			}
			items << "}";
			return Schematyc::EVisitStatus::Recurse;
		};
		registry.GetRoot().VisitChildren(visit);

		std::ostringstream summary;
		summary << "{";
		bool first = true;
		for (const auto& kv : counts)
		{
			summary << (first ? "" : ",") << "\"" << kv.first << "\":" << kv.second;
			first = false;
		}
		summary << "}";

		std::ofstream file(outPath, std::ios::binary | std::ios::trunc);
		if (!file)
			throw std::runtime_error("cannot write " + outPath);
		file << "{\"elements\":" << total << ",\"counts\":" << summary.str() << ",\"items\":[\n" << items.str() << "\n]}\n";
		file.close();

		std::ostringstream reply;
		reply << "{\"ok\": true, \"path\": \"" << JsonEscape(outPath) << "\", \"elements\": " << total << ", \"counts\": " << summary.str() << "}";
		return reply.str();
	}
}

std::string CClaudeBridgePlugin::DispatchBridgeCommand(const std::string& cmd)
{
	if (!gEnv || !gEnv->pConsole)
		throw std::runtime_error("gEnv->pConsole not available");

	std::vector<std::string> tokens = Tokenize(cmd);
	const std::string& verb = tokens.empty() ? cmd : tokens[0];

	// bridge.particles.features <output file>
	// Every particle feature the engine registered (pfx2::IParticleSystem::GetFeatureParam), each added to a fresh effect and saved as a .pfx file would hold it (with its DEFAULT values):
	// the complete catalog of what an effect can contain (group, name, whether it connects to a component, its parameters and defaults). Returns {"ok", "path", "features": n}.
	if (verb == "bridge.particles.features")
	{
		if (tokens.size() < 2)
			throw std::runtime_error("bridge.particles.features requires an output file path");
		std::string path = cmd.substr(cmd.find(verb) + verb.size());
		const size_t pb = path.find_first_not_of(" \t");
		const size_t pe = path.find_last_not_of(" \t\r\n");
		path = (pb == std::string::npos) ? std::string() : path.substr(pb, pe - pb + 1);
		std::shared_ptr<pfx2::IParticleSystem> pSystem = pfx2::GetIParticleSystem();
		if (!pSystem)
			throw std::runtime_error("the pfx2 particle system is not loaded");
		std::ostringstream out;
		out << "[\n";
		const uint n = pSystem->GetNumFeatureParams();
		for (uint i = 0; i < n; ++i)
		{
			const pfx2::SParticleFeatureParams& fp = pSystem->GetFeatureParam(i);
			out << (i ? ",\n" : "") << "{\"full\":\"" << EnvStr(fp.m_fullName) << "\",\"group\":\"" << EnvStr(fp.m_groupName) << "\",\"feature\":\"" << EnvStr(fp.m_featureName)
			    << "\",\"connector\":" << (fp.m_hasComponentConnector ? "true" : "false") << ",\"default_for_type\":" << fp.m_defaultForType;
			if (fp.m_pFactory)
			{
				// saved the way a .pfx file holds it: the feature goes into a fresh effect's component and the EFFECT is serialised, so the feature is written under the name the file uses
					// (13 features have a legacy alias registered after the canonical name, and the file then holds the alias: "FieldOpacity" for Appearance: Opacity)
					pfx2::PParticleEffect pEffect = pSystem->CreateEffect();
					pfx2::IParticleComponent* pComp = pEffect ? pEffect->AddComponent() : nullptr;
					pfx2::IParticleFeature* pFeature = pComp ? pComp->AddFeature(0, fp) : nullptr;
				if (pFeature)
				{
					DynArray<char> buf;
						const bool ok = gEnv->pSystem->GetArchiveHost()->SaveJsonBuffer(buf, Serialization::SStruct(*pEffect));
					const std::string json(buf.data(), buf.size());
					out << ",\"json\":\"" << EnvStr(ok ? json.c_str() : "") << "\",\"connectors\":[";
					for (uint c = 0; c < pFeature->GetNumConnectors(); ++c)
						out << (c ? "," : "") << "\"" << EnvStr(pFeature->GetConnectorName(c)) << "\"";
					out << "],\"resources\":[";
					for (uint r = 0; r < pFeature->GetNumResources(); ++r)
						out << (r ? "," : "") << "\"" << EnvStr(pFeature->GetResourceName(r)) << "\"";
					out << "]";
				}
			}
			out << "}";
		}
		out << "\n]\n";
		std::ofstream file(path, std::ios::binary | std::ios::trunc);
		if (!file)
			throw std::runtime_error("cannot write " + path);
		file << out.str();
		file.close();
		std::ostringstream reply;
		reply << "{\"ok\": true, \"path\": \"" << JsonEscape(path) << "\", \"features\": " << n << "}";
		return reply.str();
	}

	// bridge.particles.roundtrip <input .pfx file> | <output file>
	// Loads a .pfx (JSON) through the engine's own effect loader and saves what the engine now HOLDS: every parameter you left out is filled with its default, every feature or
	// modifier or key the engine does not know is gone, a value outside its range is clamped. Loading COMPILES the effect: a second Life/Motion/Render feature in a component is
	// switched off and saved as "Enabled": false. The two paths are separated by `|` (the project path has a space). Reports each component
	// and the features the engine kept. Returns {"ok", "loaded", "version", "components": [{"name", "parent", "features": [full names]}], "bytes_in", "bytes_out"}.
	if (verb == "bridge.particles.roundtrip")
	{
		std::string rest = cmd.substr(cmd.find(verb) + verb.size());
		const size_t bar = rest.find('|');
		if (bar == std::string::npos)
			throw std::runtime_error("bridge.particles.roundtrip requires <input> | <output>");
		auto trim = [](std::string s)
		{
			const size_t b = s.find_first_not_of(" \t\r\n");
			const size_t e = s.find_last_not_of(" \t\r\n");
			return b == std::string::npos ? std::string() : s.substr(b, e - b + 1);
		};
		const std::string inPath = trim(rest.substr(0, bar)), outPath = trim(rest.substr(bar + 1));
		std::shared_ptr<pfx2::IParticleSystem> pSystem = pfx2::GetIParticleSystem();
		if (!pSystem)
			throw std::runtime_error("the pfx2 particle system is not loaded");
		std::ifstream in(inPath, std::ios::binary);
		if (!in)
			throw std::runtime_error("cannot read " + inPath);
		const std::string text((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
		pfx2::PParticleEffect pEffect = pSystem->CreateEffect();
		if (!pEffect)
			throw std::runtime_error("the particle system created no effect");
		const bool loaded = gEnv->pSystem->GetArchiveHost()->LoadJsonBuffer(Serialization::SStruct(*pEffect), text.c_str(), text.size());
		DynArray<char> buf;
		const bool saved = gEnv->pSystem->GetArchiveHost()->SaveJsonBuffer(buf, Serialization::SStruct(*pEffect));
		if (!outPath.empty())
		{
			std::ofstream file(outPath, std::ios::binary | std::ios::trunc);
			if (!file)
				throw std::runtime_error("cannot write " + outPath);
			file.write(buf.data(), buf.size());
		}
		std::ostringstream reply;
		reply << "{\"ok\": true, \"loaded\": " << (loaded ? "true" : "false") << ", \"saved\": " << (saved ? "true" : "false") << ", \"bytes_in\": " << text.size() << ", \"bytes_out\": " << buf.size()
		      << ", \"components\": [";
		for (uint c = 0; c < pEffect->GetNumComponents(); ++c)
		{
			pfx2::IParticleComponent* pComp = pEffect->GetComponent(c);
			if (!pComp)
				continue;
			pfx2::IParticleComponent* pParent = pComp->GetParent();
			reply << (c ? "," : "") << "{\"name\": \"" << EnvStr(pComp->GetName()) << "\", \"parent\": \"" << EnvStr(pParent ? pParent->GetName() : "") << "\", \"enabled\": "
			      << (pComp->IsEnabled() ? "true" : "false") << ", \"features\": [";
			for (uint f = 0; f < pComp->GetNumFeatures(); ++f)
			{
				pfx2::IParticleFeature* pFeat = pComp->GetFeature(f);
				reply << (f ? "," : "") << "{\"full\": \"" << EnvStr(pFeat ? pFeat->GetFeatureParams().m_fullName : "") << "\", \"enabled\": " << (pFeat && pFeat->IsEnabled() ? "true" : "false") << "}";
			}
			reply << "]}";
		}
		reply << "]}";
		return reply.str();
	}

	// bridge.nav.query <agent type name, or - for the first one> <base64 of lines "label x1 y1 z1 x2 y2 z2">
	// The navigation system's own questions, no game needed: the agent types and their meshes (id, name, whether each is loaded: GetMeshIDsForAgentType), and for every line the engine's cheap
	// connectivity test (INavigationSystem::IsPointReachableFromPosition: "a cheap test to see if two points are connected, without the expense of a full path") with the default snapping metrics
	// (vertical, then box), the mesh volume each end lies in (FindEnclosingMeshID, IsLocationInMeshVolume) and a NavMesh ray cast between them. It does not know about dynamic obstacles.
	// Returns {"ok", "agent_types": [{"id", "name", "meshes": [{"id", "name"}]}], "agent", "segments": [{"label", "reachable", "start_mesh", "end_mesh", "start_in_volume", "end_in_volume", "ray"}]}.
	if (verb == "bridge.nav.query")
	{
		if (!gEnv || !gEnv->pAISystem || !gEnv->pAISystem->GetNavigationSystem())
			throw std::runtime_error("the navigation system is not available");
		INavigationSystem* pNav = gEnv->pAISystem->GetNavigationSystem();
		std::ostringstream reply;
		reply << "{\"ok\": true, \"agent_types\": [";
		for (size_t a = 0; a < pNav->GetAgentTypeCount(); ++a)
		{
			const NavigationAgentTypeID id = pNav->GetAgentTypeID(a);
			reply << (a ? "," : "") << "{\"id\": " << id.GetValue() << ", \"name\": \"" << EnvStr(pNav->GetAgentTypeName(id)) << "\", \"meshes\": [";
			const DynArray<NavigationMeshID> meshes = pNav->GetMeshIDsForAgentType(id);
			for (size_t m = 0; m < meshes.size(); ++m)
				reply << (m ? "," : "") << "{\"id\": " << meshes[m].GetValue() << ", \"name\": \"" << EnvStr(pNav->GetMeshName(meshes[m])) << "\"}";
			reply << "]}";
		}
		reply << "]";
		if (tokens.size() >= 3)
		{
			NavigationAgentTypeID agent;
			for (size_t a = 0; a < pNav->GetAgentTypeCount(); ++a)
			{
				const NavigationAgentTypeID id = pNav->GetAgentTypeID(a);
				if (tokens[1] == "-" ? a == 0 : tokens[1] == pNav->GetAgentTypeName(id))
					agent = id;
			}
			if (!agent.IsValid())
				throw std::runtime_error("no navigation agent type named " + tokens[1]);
			reply << ", \"agent\": \"" << EnvStr(pNav->GetAgentTypeName(agent)) << "\", \"segments\": [";
			const std::string lines = Base64Decode(tokens[2]);
			MNM::SOrderedSnappingMetrics snapping;
			snapping.CreateDefault();
			size_t pos = 0;
			bool firstSeg = true;
			while (pos < lines.size())
			{
				size_t end = lines.find('\n', pos);
				if (end == std::string::npos)
					end = lines.size();
				std::istringstream line(lines.substr(pos, end - pos));
				pos = end + 1;
				std::string label;
				float v[6];
				if (!(line >> label >> v[0] >> v[1] >> v[2] >> v[3] >> v[4] >> v[5]))
					continue;
				const Vec3 a(v[0], v[1], v[2]), b(v[3], v[4], v[5]);
				const NavigationMeshID ma = pNav->FindEnclosingMeshID(agent, a, snapping), mb = pNav->FindEnclosingMeshID(agent, b, snapping);
				const bool reachable = pNav->IsPointReachableFromPosition(agent, nullptr, a, b, snapping, nullptr);
				const bool inA = ma.IsValid() && pNav->IsLocationInMeshVolume(ma, a, snapping), inB = mb.IsValid() && pNav->IsLocationInMeshVolume(mb, b, snapping);
				const MNM::ERayCastResult ray = pNav->NavMeshRayCast(agent, a, b, nullptr, nullptr);
				reply << (firstSeg ? "" : ",") << "{\"label\": \"" << EnvStr(label.c_str()) << "\", \"reachable\": " << (reachable ? "true" : "false") << ", \"start_mesh\": " << (ma.IsValid() ? (int)ma.GetValue() : -1)
				      << ", \"end_mesh\": " << (mb.IsValid() ? (int)mb.GetValue() : -1) << ", \"start_in_volume\": " << (inA ? "true" : "false") << ", \"end_in_volume\": " << (inB ? "true" : "false") << ", \"ray\": " << static_cast<int>(ray) << "}";
				firstSeg = false;
			}
			reply << "]";
		}
		reply << "}";
		return reply.str();
	}

	// bridge.commands.dump <output file>
	// Every command the editor's own command manager (ICommandManager) holds: module, name, description, parameters (name, description), whether it is also available to Python scripting,
	// whether it can be a UI command (no arguments), whether it is a polled key or a custom command. This is a different list from the Python API's (editor_api): the Python functions are
	// the commands flagged "available in scripting" plus the module functions; the rest (menu and toolbar actions) can only be run through ICommandManager::Execute (bridge.execute_command).
	// Returns {"ok", "path", "commands": n}.
	if (verb == "bridge.commands.dump")
	{
		if (tokens.size() < 2)
			throw std::runtime_error("bridge.commands.dump requires an output file path");
		std::string path = cmd.substr(cmd.find(verb) + verb.size());
		const size_t pb = path.find_first_not_of(" \t");
		const size_t pe = path.find_last_not_of(" \t\r\n");
		path = (pb == std::string::npos) ? std::string() : path.substr(pb, pe - pb + 1);
		ICommandManager* pMgr = GetIEditor() ? GetIEditor()->GetICommandManager() : nullptr;
		if (!pMgr)
			throw std::runtime_error("the command manager is not available");
		std::vector<CCommand*> commands;
		pMgr->GetCommandList(commands);
		std::ostringstream out;
		out << "[\n";
		bool first = true;
		for (CCommand* pCmd : commands)
		{
			if (!pCmd)
				continue;
			out << (first ? "" : ",\n") << "{\"module\":\"" << EnvStr(pCmd->GetModule().c_str()) << "\",\"name\":\"" << EnvStr(pCmd->GetName().c_str()) << "\",\"description\":\"" << EnvStr(pCmd->GetDescription().c_str())
			    << "\",\"scripting\":" << (pCmd->IsAvailableInScripting() ? "true" : "false") << ",\"ui\":" << (pCmd->CanBeUICommand() ? "true" : "false") << ",\"custom\":" << (pCmd->IsCustomCommand() ? "true" : "false")
			    << ",\"polled_key\":" << (pCmd->IsPolledKey() ? "true" : "false") << ",\"deprecated\":" << (pMgr->IsCommandDeprecated(pCmd->GetCommandString().c_str()) ? "true" : "false") << ",\"params\":[";
			const CCommandDescription::TParams& params = pCmd->GetParameters();
			for (size_t i = 0; i < params.size(); ++i)
				out << (i ? "," : "") << "{\"name\":\"" << EnvStr(params[i].GetName().c_str()) << "\",\"description\":\"" << EnvStr(params[i].GetDescription().c_str()) << "\"}";
			out << "]}";
			first = false;
		}
		out << "\n]\n";
		std::ofstream file(path, std::ios::binary | std::ios::trunc);
		if (!file)
			throw std::runtime_error("cannot write " + path);
		file << out.str();
		file.close();
		std::ostringstream reply;
		reply << "{\"ok\": true, \"path\": \"" << JsonEscape(path) << "\", \"commands\": " << commands.size() << "}";
		return reply.str();
	}

	// bridge.sequence.roundtrip <input sequence .xml> | <output file>
	// Loads a Track View sequence (the XML a level or libs/<game>/sequences/*.xml holds) through the engine's own CMovieSystem::LoadSequence and saves what the engine now HOLDS with
	// IAnimSequence::Serialize: what it kept of the nodes, tracks and keys, with its defaults filled in. The copy is loaded under a throw-away name, GUID and id (LoadSequence REPLACES a
	// sequence of the same name, which would destroy the level's own in-memory sequence) and removed again afterwards; the saved XML gets the original name, GUID and id back so the two
	// documents can be compared. Returns {"ok", "loaded", "name", "flags", "start", "end", "nodes": [{"name", "type", "tracks": [{"param", "keys"}]}], "bytes_in", "bytes_out"}.
	if (verb == "bridge.sequence.roundtrip")
	{
		std::string rest = cmd.substr(cmd.find(verb) + verb.size());
		const size_t bar = rest.find('|');
		if (bar == std::string::npos)
			throw std::runtime_error("bridge.sequence.roundtrip requires <input> | <output>");
		auto trim = [](std::string s)
		{
			const size_t b = s.find_first_not_of(" \t\r\n");
			const size_t e = s.find_last_not_of(" \t\r\n");
			return b == std::string::npos ? std::string() : s.substr(b, e - b + 1);
		};
		const std::string inPath = trim(rest.substr(0, bar)), outPath = trim(rest.substr(bar + 1));
		if (!gEnv || !gEnv->pMovieSystem)
			throw std::runtime_error("the movie system is not available");
		std::ifstream in(inPath, std::ios::binary);
		if (!in)
			throw std::runtime_error("cannot read " + inPath);
		const std::string text((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
		XmlNodeRef root = gEnv->pSystem->LoadXmlFromBuffer(text.c_str(), text.size());
		if (!root)
			throw std::runtime_error("the file is not well-formed XML (an XML comment containing -- is enough)");
		const string origName = root->getAttr("Name");
		uint64 origLo = 0, origHi = 0;
		uint32 origId = 0;
		root->getAttr("GUIDLo", origLo);
		root->getAttr("GUIDHi", origHi);
		root->getAttr("ID", origId);
		static uint32 s_counter = 0;
		char temp[64];
		cry_sprintf(temp, "zz_claude_seq_%u", ++s_counter);
		root->setAttr("Name", temp);
		root->setAttr("GUIDLo", (uint64)0x7A5A000000000000ULL + s_counter);
		root->setAttr("GUIDHi", (uint64)0x7A5A000000000001ULL);
		root->setAttr("ID", (uint32)(900000 + s_counter));
		IAnimSequence* pSeq = gEnv->pMovieSystem->LoadSequence(root, true);
		if (!pSeq)
			throw std::runtime_error("the engine returned no sequence");
		std::ostringstream reply;
		reply << "{\"ok\": true, \"loaded\": true, \"name\": \"" << JsonEscape(origName.c_str()) << "\", \"flags\": " << pSeq->GetFlags() << ", \"start\": " << pSeq->GetTimeRange().start.ToFloat()
		      << ", \"end\": " << pSeq->GetTimeRange().end.ToFloat() << ", \"nodes\": [";
		for (int n = 0; n < pSeq->GetNodeCount(); ++n)
		{
			IAnimNode* pNode = pSeq->GetNode(n);
			if (!pNode)
				continue;
			reply << (n ? "," : "") << "{\"name\": \"" << EnvStr(pNode->GetName()) << "\", \"type\": " << static_cast<int>(pNode->GetType()) << ", \"tracks\": [";
			for (int t = 0; t < pNode->GetTrackCount(); ++t)
			{
				IAnimTrack* pTrack = pNode->GetTrackByIndex(t);
				if (!pTrack)
					continue;
				reply << (t ? "," : "") << "{\"param\": \"" << EnvStr(pNode->GetParamName(pTrack->GetParameterType())) << "\", \"keys\": " << pTrack->GetNumKeys() << "}";
			}
			reply << "]}";
		}
		reply << "]";
		XmlNodeRef outXml = gEnv->pSystem->CreateXmlNode("Sequence");
		pSeq->Serialize(outXml, false, true);
		gEnv->pMovieSystem->RemoveSequence(pSeq);
		outXml->setAttr("Name", origName.c_str());
		outXml->setAttr("GUIDLo", origLo);
		outXml->setAttr("GUIDHi", origHi);
		outXml->setAttr("ID", origId);
		const string saved = outXml->getXML();
		if (!outPath.empty())
		{
			std::ofstream file(outPath, std::ios::binary | std::ios::trunc);
			if (!file)
				throw std::runtime_error("cannot write " + outPath);
			file.write(saved.c_str(), saved.size());
		}
		reply << ", \"bytes_in\": " << text.size() << ", \"bytes_out\": " << saved.size() << "}";
		return reply.str();
	}

	// bridge.mannequin.clips <output file>
	// The procedural clip types Mannequin knows (IProceduralClipFactory, the registry the Mannequin editor's clip list is built from) and, for each, the parameters its IProceduralParams
	// serializes with their defaults (written through the engine's own JSON archive). Returns {"ok", "path", "clips": n}; the file holds [{"name", "has_params", "json"}].
	if (verb == "bridge.mannequin.clips")
	{
		if (tokens.size() < 2)
			throw std::runtime_error("bridge.mannequin.clips requires an output file path");
		std::string path = cmd.substr(cmd.find(verb) + verb.size());
		const size_t pb = path.find_first_not_of(" \t");
		const size_t pe = path.find_last_not_of(" \t\r\n");
		path = (pb == std::string::npos) ? std::string() : path.substr(pb, pe - pb + 1);
		if (!gEnv || !gEnv->pGameFramework)
			throw std::runtime_error("the game framework (CryAction, which owns Mannequin) is not available");
		IProceduralClipFactory& factory = gEnv->pGameFramework->GetMannequinInterface().GetProceduralClipFactory();
		const size_t count = factory.GetRegisteredCount();
		std::ostringstream out;
		out << "[\n";
		for (size_t i = 0; i < count; ++i)
		{
			const char* name = factory.GetTypeNameByIndex(i);
			std::string json;
			IProceduralParamsPtr pParams = name ? factory.CreateProceduralClipParams(name) : IProceduralParamsPtr();
			if (pParams)
			{
				DynArray<char> buf;
				if (gEnv->pSystem->GetArchiveHost()->SaveJsonBuffer(buf, Serialization::SStruct(*pParams)))
					json.assign(buf.data(), buf.size());
			}
			out << (i ? ",\n" : "") << "{\"name\":\"" << EnvStr(name ? name : "") << "\",\"has_params\":" << (pParams ? "true" : "false") << ",\"json\":\"" << EnvStr(json.c_str()) << "\"}";
		}
		out << "\n]\n";
		std::ofstream file(path, std::ios::binary | std::ios::trunc);
		if (!file)
			throw std::runtime_error("cannot write " + path);
		file << out.str();
		file.close();
		std::ostringstream reply;
		reply << "{\"ok\": true, \"path\": \"" << JsonEscape(path) << "\", \"clips\": " << count << "}";
		return reply.str();
	}

	// bridge.mannequin.check <output file> | <database .adb or -> | <controller definition .xml or -> | <character .cdf or -> | <flags: reload or ->
	// Mannequin files read by the engine's OWN loaders (IAnimationDatabaseManager::Load / LoadControllerDef), then written back out with the editor manager's own Save* functions into
	// memory (nothing is written to the project), so the caller can diff the file against what the engine kept. The loader drops a fragment whose FragmentID or tags it does not know with
	// a CryLog line only, so those lines are captured while the load runs (log verbosity is raised to 4 for that moment and put back). With a character the engine's own
	// IAnimationDatabase::Validate and EnumerateAnimAssets run against the character's animation set (the clips Mannequin can really play). Flag "reload" re-reads the named files (RevertTagDef / RevertControllerDef / RevertDatabase): the manager
	// caches a loaded database by file name, and a file edited since would otherwise be read from the cache.
	// The output file holds {"database", "loaded", "already_loaded", "controller", "controller_loaded", "log": [...], "tags": [...], "fragments": [...], "validate": {...}, "anims": [...], "saved": [{"file", "kind", "xml"}], "scopes": ...}.
	if (verb == "bridge.mannequin.check")
	{
		std::string rest = cmd.substr(cmd.find(verb) + verb.size());
		std::vector<std::string> parts;
		{
			size_t start = 0;
			for (;;)
			{
				const size_t bar = rest.find('|', start);
				std::string s = rest.substr(start, bar == std::string::npos ? std::string::npos : bar - start);
				const size_t b = s.find_first_not_of(" \t\r\n");
				const size_t e = s.find_last_not_of(" \t\r\n");
				parts.push_back(b == std::string::npos ? std::string() : s.substr(b, e - b + 1));
				if (bar == std::string::npos)
					break;
				start = bar + 1;
			}
		}
		if (parts.size() < 4 || parts[0].empty())
			throw std::runtime_error("bridge.mannequin.check requires <output> | <adb or -> | <controller def or -> | <cdf or -> [| reload]");
		const std::string outPath = parts[0];
		const std::string adbPath = parts[1] == "-" ? std::string() : parts[1];
		const std::string defPath = parts[2] == "-" ? std::string() : parts[2];
		const std::string chrPath = parts[3] == "-" ? std::string() : parts[3];
		const bool reload = parts.size() > 4 && parts[4] == "reload";
		if (!gEnv || !gEnv->pGameFramework)
			throw std::runtime_error("the game framework (CryAction, which owns Mannequin) is not available");
		IMannequin& mann = gEnv->pGameFramework->GetMannequinInterface();
		IAnimationDatabaseManager& mgr = mann.GetAnimationDatabaseManager();
		IMannequinEditorManager* pEd = mann.GetMannequinEditorManager();
		IProceduralClipFactory& factory = mann.GetProceduralClipFactory();

		// what the engine log says while the files load, the verbosity put back by the destructor; the character is released by it too
		struct Guard : public ILogCallback
		{
			std::vector<std::string> lines;
			ICVar* v1 = nullptr;
			ICVar* v2 = nullptr;
			int o1 = 0, o2 = 0;
			bool added = false;
			ICharacterInstance* pChar = nullptr;
			void OnWriteToConsole(const char* s, bool) override { if (s) lines.push_back(s); }
			void OnWriteToFile(const char*, bool) override {}
			void Begin()
			{
				v1 = gEnv->pConsole->GetCVar("log_Verbosity");
				v2 = gEnv->pConsole->GetCVar("log_WriteToFileVerbosity");
				o1 = v1 ? v1->GetIVal() : 0;
				o2 = v2 ? v2->GetIVal() : 0;
				if (v1) v1->Set(4);
				if (v2) v2->Set(4);
				gEnv->pLog->AddCallback(this);
				added = true;
			}
			void End()
			{
				if (added) { gEnv->pLog->RemoveCallback(this); added = false; }
				if (v1) v1->Set(o1);
				if (v2) v2->Set(o2);
				v1 = v2 = nullptr;
			}
			~Guard() { End(); if (pChar) pChar->Release(); }
		} guard;

		struct Writer : public IMannequinWriter
		{
			std::vector<std::string> names, xmls;
			std::vector<int> kinds;
			void SaveFile(const char* name, XmlNodeRef node, EFileEntryType kind) override
			{
				names.push_back(name ? name : "");
				xmls.push_back(node ? node->getXML().c_str() : "");
				kinds.push_back((int)kind);
			}
			void WriteModifiedFiles() override {}
		} writer;

		struct Report
		{
			std::vector<std::string> errors, warnings;
			static void OnError(const SMannequinErrorReport& r, void* c) { ((Report*)c)->errors.push_back(Describe(r)); }
			static void OnWarning(const SMannequinErrorReport& r, void* c) { ((Report*)c)->warnings.push_back(Describe(r)); }
			static std::string Describe(const SMannequinErrorReport& r)
			{
				char head[96];
				cry_sprintf(head, "fragment %d option %u: ", (int)r.fragID, (unsigned)r.fragOptionID);
				return std::string(head) + r.error;
			}
		} report;

		struct Anim
		{
			std::string name, path;
			int animId, frag;
			unsigned option;
		};
		std::vector<Anim> anims;
		struct AnimCollect
		{
			static void On(const SAnimAssetReport& a, void* c)
			{
				Anim an;
				an.name = a.pAnimName ? a.pAnimName : "";
				an.path = a.pAnimPath ? a.pAnimPath : "";
				an.animId = a.animID;
				an.frag = (int)a.fragID;
				an.option = (unsigned)a.fragOptionID;
				((std::vector<Anim>*)c)->push_back(an);
			}
		};

		std::ostringstream out;
		auto tagNames = [](const CTagDefinition& td) -> std::string
		{
			std::ostringstream s;
			s << "[";
			for (TagID i = 0; i < td.GetNum(); ++i)
				s << (i ? "," : "") << "\"" << EnvStr(td.GetTagName(i)) << "\"";
			s << "]";
			return s.str();
		};
		auto normalize = [](std::string s)
		{
			for (char& ch : s)
			{
				if (ch == '\\') ch = '/';
				ch = (char)tolower((unsigned char)ch);
			}
			return s;
		};

		// a character first: the animation set Validate checks against
		const IAnimationSet* pSet = nullptr;
		if (!chrPath.empty() && gEnv->pCharacterManager)
		{
			// CreateInstance hands back an instance with a reference count of ZERO (callers keep it in a smart pointer, which adds the first reference): releasing it without AddRef drives
			// the count to -1 and CCharInstance::Release raises a modal CryFatalError ("CSkelInstance::Release"), which blocks the editor's main thread
			guard.pChar = gEnv->pCharacterManager->CreateInstance(chrPath.c_str());
			if (guard.pChar)
			{
				guard.pChar->AddRef();
				pSet = guard.pChar->GetIAnimationSet();
			}
		}
		bool already = false;
		if (!adbPath.empty())
		{
			const std::string want = normalize(adbPath);
			for (int i = 0; i < mgr.GetTotalDatabases(); ++i)
			{
				const IAnimationDatabase* d = mgr.GetDatabase(i);
				if (d && d->GetFilename() && normalize(d->GetFilename()) == want)
					already = true;
			}
		}

		guard.Begin();
		const IAnimationDatabase* pDb = adbPath.empty() ? nullptr : mgr.Load(adbPath.c_str());
		const SControllerDef* pDef = defPath.empty() ? nullptr : mgr.LoadControllerDef(defPath.c_str());
		if (reload && pEd)
		{
			// the files these names stand for, read again (the editor manager's own Revert calls, the ones the Mannequin editor's "revert" uses): tag definitions first, then the
			// controller definition, then the database, so each reads the fresh one below it. Only these files: ReloadAll would re-read every database the manager holds.
			if (pDb)
			{
				pEd->RevertTagDef(pDb->GetTagDefs().GetFilename());
				pEd->RevertTagDef(pDb->GetFragmentDefs().GetFilename());
			}
			if (pDef)
			{
				pEd->RevertTagDef(pDef->m_tags.GetFilename());
				pEd->RevertTagDef(pDef->m_fragmentIDs.GetFilename());
				pEd->RevertControllerDef(pDef->m_filename.c_str());
			}
			if (pDb)
				pEd->RevertDatabase(pDb->GetFilename());
		}
		guard.End();

		out << "{\"database\":\"" << EnvStr(adbPath.c_str()) << "\",\"loaded\":" << (pDb ? "true" : "false") << ",\"already_loaded\":" << (already ? "true" : "false") << ",\"reloaded\":" << (reload ? "true" : "false")
		    << ",\"controller\":\"" << EnvStr(defPath.c_str()) << "\",\"controller_loaded\":" << (pDef ? "true" : "false")
		    << ",\"character\":\"" << EnvStr(chrPath.c_str()) << "\",\"character_loaded\":" << (guard.pChar ? "true" : "false") << ",\"animation_set\":" << (pSet ? "true" : "false")
		    << ",\"animation_count\":" << (pSet ? (int)pSet->GetAnimationCount() : -1) << ",\"log\":[";
		for (size_t i = 0; i < guard.lines.size(); ++i)
			out << (i ? "," : "") << "\"" << EnvStr(guard.lines[i].c_str()) << "\"";
		out << "]";

		if (pDb)
		{
			const CTagDefinition& tags = pDb->GetTagDefs();
			const CTagDefinition& frags = pDb->GetFragmentDefs();
			out << ",\"tags\":" << tagNames(tags) << ",\"fragments\":[";
			for (TagID f = 0; f < frags.GetNum(); ++f)
			{
				const CTagDefinition* pSub = frags.GetSubTagDefinition(f);
				out << (f ? "," : "") << "{\"id\":" << (int)f << ",\"name\":\"" << EnvStr(frags.GetTagName(f)) << "\",\"frag_tags\":" << (pSub ? tagNames(*pSub) : std::string("[]")) << ",\"tag_sets\":[";
				const uint32 sets = pDb->GetTotalTagSets(f);
				for (uint32 k = 0; k < sets; ++k)
				{
					SFragTagState ts;
					const uint32 options = pDb->GetTagSetInfo(f, k, ts);
					CryStackStringT<char, 512> g, ft;
					tags.FlagsToTagList(ts.globalTags, g);
					if (pSub)
						pSub->FlagsToTagList(ts.fragmentTags, ft);
					out << (k ? "," : "") << "{\"tags\":\"" << EnvStr(g.c_str()) << "\",\"frag_tags\":\"" << EnvStr(ft.c_str()) << "\",\"options\":[";
					for (uint32 o = 0; o < options; ++o)
					{
						const CFragment* pf = pDb->GetEntry(f, k, o);
						out << (o ? "," : "") << "{\"blend_out\":" << (pf ? pf->m_blendOutDuration : 0.0f) << ",\"anim_layers\":[";
						if (pf)
						{
							for (size_t l = 0; l < pf->m_animLayers.size(); ++l)
							{
								out << (l ? "," : "") << "[";
								for (size_t c = 0; c < pf->m_animLayers[l].size(); ++c)
								{
									const SAnimClip& clip = pf->m_animLayers[l][c];
									const char* an = clip.animation.animRef.c_str();
									out << (c ? "," : "") << "{\"name\":\"" << EnvStr(an ? an : "") << "\",\"flags\":" << clip.animation.flags << ",\"speed\":" << clip.animation.playbackSpeed << ",\"blend\":" << clip.blend.duration << "}";
								}
								out << "]";
							}
						}
						out << "],\"proc_layers\":[";
						if (pf)
						{
							for (size_t l = 0; l < pf->m_procLayers.size(); ++l)
							{
								out << (l ? "," : "") << "[";
								for (size_t c = 0; c < pf->m_procLayers[l].size(); ++c)
								{
									const SProceduralEntry& pe2 = pf->m_procLayers[l][c];
									const char* tn = pe2.IsNoneType() ? "" : factory.FindTypeName(pe2.typeNameHash);
									out << (c ? "," : "") << "{\"type\":\"" << EnvStr(tn ? tn : "?") << "\",\"known\":" << (pe2.IsNoneType() || tn ? "true" : "false") << "}";
								}
								out << "]";
							}
						}
						out << "]}";
					}
					out << "]}";
				}
				out << "]}";
			}
			out << "]";
			// the engine's own validation, and its list of every animation the database names
			bool validOk = true;
			validOk = pDb->Validate(pSet, &Report::OnError, &Report::OnWarning, &report);
			if (pSet)
				pDb->EnumerateAnimAssets(pSet, &AnimCollect::On, &anims);
			out << ",\"validate\":{\"ran_with_animation_set\":" << (pSet ? "true" : "false") << ",\"ok\":" << (validOk ? "true" : "false") << ",\"errors\":[";
			for (size_t i = 0; i < report.errors.size(); ++i)
				out << (i ? "," : "") << "\"" << EnvStr(report.errors[i].c_str()) << "\"";
			out << "],\"warnings\":[";
			for (size_t i = 0; i < report.warnings.size(); ++i)
				out << (i ? "," : "") << "\"" << EnvStr(report.warnings[i].c_str()) << "\"";
			out << "]},\"anims\":[";
			for (size_t i = 0; i < anims.size(); ++i)
				out << (i ? "," : "") << "{\"name\":\"" << EnvStr(anims[i].name.c_str()) << "\",\"path\":\"" << EnvStr(anims[i].path.c_str()) << "\",\"anim_id\":" << anims[i].animId << ",\"fragment\":" << anims[i].frag << ",\"option\":" << anims[i].option << "}";
			out << "]";
			if (pEd)
			{
				pEd->SaveDatabase(&writer, pDb);
				pEd->SaveTagDefinition(&writer, &tags);
				pEd->SaveTagDefinition(&writer, &frags);
			}
		}
		if (pDef)
		{
			out << ",\"scopes\":{\"fragments\":[";
			const CTagDefinition& ids = pDef->m_fragmentIDs;
			auto scopeList = [&](ActionScopes mask)
			{
				std::ostringstream s;
				s << "[";
				bool first = true;
				for (TagID i = 0; i < pDef->m_scopeIDs.GetNum(); ++i)
					if (mask & ((ActionScopes)1 << i))
					{
						s << (first ? "" : ",") << "\"" << EnvStr(pDef->m_scopeIDs.GetTagName(i)) << "\"";
						first = false;
					}
				s << "]";
				return s.str();
			};
			for (TagID f = 0; f < ids.GetNum(); ++f)
			{
				const SFragmentDef& fd = pDef->m_fragmentDef[f];
				const ActionScopes mask = pDef->GetScopeMask(f, SFragTagState());
				out << (f ? "," : "") << "{\"name\":\"" << EnvStr(ids.GetTagName(f)) << "\",\"scopes\":" << scopeList(mask) << ",\"flags\":" << (unsigned)fd.flags << ",\"mask_entries\":" << (int)fd.scopeMaskList.Size() << "}";
			}
			out << "],\"scope_defs\":[";
			for (TagID s = 0; s < pDef->m_scopeIDs.GetNum(); ++s)
			{
				const SScopeDef& sd = pDef->m_scopeDef[s];
				out << (s ? "," : "") << "{\"name\":\"" << EnvStr(pDef->m_scopeIDs.GetTagName(s)) << "\",\"layer\":" << sd.layer << ",\"num_layers\":" << sd.numLayers << ",\"context\":\"" << EnvStr(pDef->m_scopeContexts.GetTagName((TagID)sd.context)) << "\"}";
			}
			out << "],\"contexts\":" << tagNames(pDef->m_scopeContexts) << ",\"sub_contexts\":" << tagNames(pDef->m_subContextIDs) << ",\"tags\":" << tagNames(pDef->m_tags) << "}";
			if (pEd)
				pEd->SaveControllerDef(&writer, pDef);
		}
		out << ",\"saved\":[";
		for (size_t i = 0; i < writer.names.size(); ++i)
			out << (i ? "," : "") << "{\"file\":\"" << EnvStr(writer.names[i].c_str()) << "\",\"kind\":" << writer.kinds[i] << ",\"xml\":\"" << EnvStr(writer.xmls[i].c_str()) << "\"}";
		out << "]}\n";
		std::ofstream file(outPath, std::ios::binary | std::ios::trunc);
		if (!file)
			throw std::runtime_error("cannot write " + outPath);
		file << out.str();
		file.close();
		std::ostringstream reply;
		reply << "{\"ok\": true, \"path\": \"" << JsonEscape(outPath) << "\", \"loaded\": " << (pDb ? "true" : "false") << ", \"controller_loaded\": " << (pDef ? "true" : "false") << ", \"bytes\": " << out.str().size() << "}";
		return reply.str();
	}

	// bridge.character.check <output file> | <names file, one .cdf / .chr / .skin path per line>
	// Each character is created the way the game and the Character Tool create it (ICharacterManager::CreateInstance) and the engine's own resolved state is written out: the skeleton file and joint count,
	// every attachment (name, type, joint, whether an object is bound, which file it is) and the size of the animation set, plus the log lines the load produced (verbosity is raised to 4 for the load and
	// put back). CreateInstance hands back an instance with a reference count of ZERO: AddRef, then Release when done (releasing without it is a fatal error that freezes the editor).
	// The file holds [{"file", "created", "skeleton", "joints", "attachments": [{"name", "type", "joint", "bound", "object_type", "object_file"}], "animations", "log": [...]}].
	if (verb == "bridge.character.check")
	{
		std::string rest = cmd.substr(cmd.find(verb) + verb.size());
		const size_t bar = rest.find('|');
		if (bar == std::string::npos)
			throw std::runtime_error("bridge.character.check requires <output file> | <names file>");
		auto trim = [](std::string s)
		{
			const size_t b = s.find_first_not_of(" \t\r\n");
			const size_t e = s.find_last_not_of(" \t\r\n");
			return b == std::string::npos ? std::string() : s.substr(b, e - b + 1);
		};
		const std::string outPath = trim(rest.substr(0, bar)), namesPath = trim(rest.substr(bar + 1));
		if (!gEnv || !gEnv->pCharacterManager)
			throw std::runtime_error("the character manager is not available");
		std::ifstream in(namesPath, std::ios::binary);
		if (!in)
			throw std::runtime_error("cannot read " + namesPath);

		struct LogGrab : public ILogCallback
		{
			std::vector<std::string> lines;
			ICVar* v1 = nullptr;
			ICVar* v2 = nullptr;
			int o1 = 0, o2 = 0;
			bool added = false;
			void OnWriteToConsole(const char* s, bool) override { if (s) lines.push_back(s); }
			void OnWriteToFile(const char*, bool) override {}
			void Begin()
			{
				v1 = gEnv->pConsole->GetCVar("log_Verbosity");
				v2 = gEnv->pConsole->GetCVar("log_WriteToFileVerbosity");
				o1 = v1 ? v1->GetIVal() : 0;
				o2 = v2 ? v2->GetIVal() : 0;
				if (v1) v1->Set(4);
				if (v2) v2->Set(4);
				gEnv->pLog->AddCallback(this);
				added = true;
			}
			~LogGrab()
			{
				if (added) gEnv->pLog->RemoveCallback(this);
				if (v1) v1->Set(o1);
				if (v2) v2->Set(o2);
			}
		};
		static const char* const kTypes[] = { "CA_BONE", "CA_FACE", "CA_SKIN", "CA_PROX", "CA_PROW", "CA_VCLOTH" };
		static const char* const kObjects[] = { "unknown", "static", "skeleton", "skin", "entity", "light", "effect" };

		std::ostringstream out;
		out << "[\n";
		std::string line;
		int count = 0, created = 0;
		while (std::getline(in, line))
		{
			line = trim(line);
			if (line.empty())
				continue;
			LogGrab grab;
			grab.Begin();
			ICharacterInstance* pChar = gEnv->pCharacterManager->CreateInstance(line.c_str());
			std::ostringstream one;
			one << "{\"file\":\"" << EnvStr(line.c_str()) << "\",\"created\":" << (pChar ? "true" : "false");
			if (pChar)
			{
				pChar->AddRef();
				++created;
				const IDefaultSkeleton& skel = pChar->GetIDefaultSkeleton();
				one << ",\"skeleton\":\"" << EnvStr(skel.GetModelFilePath()) << "\",\"joints\":" << skel.GetJointCount() << ",\"attachments\":[";
				IAttachmentManager* pMgr = pChar->GetIAttachmentManager();
				const int n = pMgr ? pMgr->GetAttachmentCount() : 0;
				for (int i = 0; i < n; ++i)
				{
					IAttachment* pAtt = pMgr->GetInterfaceByIndex(i);
					if (!pAtt)
						continue;
					IAttachmentObject* pObj = pAtt->GetIAttachmentObject();
					const uint32 type = pAtt->GetType();
					const int joint = (int)pAtt->GetJointID();
					const char* jointName = (joint >= 0 && joint < (int)skel.GetJointCount()) ? skel.GetJointNameByID(joint) : "";
					const int objType = pObj ? (int)pObj->GetAttachmentType() : -1;
					one << (i ? "," : "") << "{\"name\":\"" << EnvStr(pAtt->GetName()) << "\",\"type\":\"" << (type < 6 ? kTypes[type] : "?") << "\",\"joint\":\"" << EnvStr(jointName ? jointName : "") << "\",\"bound\":" << (pObj ? "true" : "false")
					    << ",\"object_type\":\"" << ((objType >= 0 && objType < 7) ? kObjects[objType] : "none") << "\",\"object_file\":\"" << EnvStr(pObj && pObj->GetObjectFilePath() ? pObj->GetObjectFilePath() : "") << "\"}";
				}
				one << "],\"animations\":" << (pChar->GetIAnimationSet() ? (int)pChar->GetIAnimationSet()->GetAnimationCount() : -1);
				pChar->Release();
			}
			one << ",\"log\":[";
			for (size_t i = 0; i < grab.lines.size(); ++i)
				one << (i ? "," : "") << "\"" << EnvStr(grab.lines[i].c_str()) << "\"";
			one << "]}";
			out << (count ? ",\n" : "") << one.str();
			++count;
		}
		out << "\n]\n";
		std::ofstream file(outPath, std::ios::binary | std::ios::trunc);
		if (!file)
			throw std::runtime_error("cannot write " + outPath);
		file << out.str();
		file.close();
		std::ostringstream reply;
		reply << "{\"ok\": true, \"path\": \"" << JsonEscape(outPath) << "\", \"asked\": " << count << ", \"created\": " << created << "}";
		return reply.str();
	}

	// bridge.material.check <output file> | <names file, one material path per line (with or without .mtl)>
	// Each material is loaded through the engine's own material manager (I3DEngine::GetMaterialManager()->LoadMaterial, which in the editor goes through the editor's material library) and the engine's
	// resolved state is written out: for the material itself or for every sub-material the surface type the engine ended up with (an undefined name resolves to mat_default), the shader it loaded, the
	// shader's generation mask, which texture slots the shader reads and, per slot, the texture file and the texture the engine bound; plus the log lines the load produced (verbosity raised to 4 for the load
	// and put back). Whether the material was already loaded is reported too: the engine warns about an undefined surface type only on the FIRST load, so a second look is quieter.
	// The file holds [{"file", "found", "already_loaded", "is_default", "name", "flags", "subs": [{"slot", "name", "surface", "surface_id", "shader", "shader_flags", "gen_mask", "used_textures",
	// "textures": [{"slot", "file", "bound", "bound_file", "w", "h", "loaded"}]}], "log": [...]}].
	if (verb == "bridge.material.check")
	{
		std::string rest = cmd.substr(cmd.find(verb) + verb.size());
		const size_t bar = rest.find('|');
		if (bar == std::string::npos)
			throw std::runtime_error("bridge.material.check requires <output file> | <names file>");
		auto trim = [](std::string s)
		{
			const size_t b = s.find_first_not_of(" \t\r\n");
			const size_t e = s.find_last_not_of(" \t\r\n");
			return b == std::string::npos ? std::string() : s.substr(b, e - b + 1);
		};
		const std::string outPath = trim(rest.substr(0, bar)), namesPath = trim(rest.substr(bar + 1));
		if (!gEnv || !gEnv->p3DEngine || !gEnv->p3DEngine->GetMaterialManager())
			throw std::runtime_error("the material manager is not available");
		IMaterialManager* pMatMan = gEnv->p3DEngine->GetMaterialManager();
		std::ifstream in(namesPath, std::ios::binary);
		if (!in)
			throw std::runtime_error("cannot read " + namesPath);

		struct LogGrab : public ILogCallback
		{
			std::vector<std::string> lines;
			ICVar* v1 = nullptr;
			ICVar* v2 = nullptr;
			int o1 = 0, o2 = 0;
			bool added = false;
			void OnWriteToConsole(const char* s, bool) override { if (s) lines.push_back(s); }
			void OnWriteToFile(const char*, bool) override {}
			void Begin()
			{
				v1 = gEnv->pConsole->GetCVar("log_Verbosity");
				v2 = gEnv->pConsole->GetCVar("log_WriteToFileVerbosity");
				o1 = v1 ? v1->GetIVal() : 0;
				o2 = v2 ? v2->GetIVal() : 0;
				if (v1) v1->Set(4);
				if (v2) v2->Set(4);
				gEnv->pLog->AddCallback(this);
				added = true;
			}
			~LogGrab()
			{
				if (added) gEnv->pLog->RemoveCallback(this);
				if (v1) v1->Set(o1);
				if (v2) v2->Set(o2);
			}
		};
		static const char* const kSlots[] = { "diffuse", "normals", "specular", "env", "detail_overlay", "smoothness", "height", "decal_overlay", "subsurface", "custom", "custom_secondary", "opacity", "translucency", "emittance" };

		auto describe = [&](IMaterial* pSub, int slot, std::ostringstream& one)
		{
			SShaderItem& item = pSub->GetShaderItem();
			ISurfaceType* pSurf = pSub->GetSurfaceType();
			one << "{\"slot\":" << slot << ",\"name\":\"" << EnvStr(pSub->GetName()) << "\",\"surface\":\"" << EnvStr(pSurf ? pSurf->GetName() : "") << "\",\"surface_id\":" << pSub->GetSurfaceTypeId()
			    << ",\"shader\":\"" << EnvStr(item.m_pShader ? item.m_pShader->GetName() : "") << "\",\"shader_flags\":" << (item.m_pShader ? item.m_pShader->GetFlags() : -1)
			    << ",\"gen_mask\":\"" << std::hex << (item.m_pShader ? (unsigned long long)item.m_pShader->GetGenerationMask() : 0ull) << std::dec << "\""
			    << ",\"used_textures\":" << (item.m_pShader ? (unsigned int)item.m_pShader->GetUsedTextureTypes() : 0u) << ",\"textures\":[";
			bool firstTex = true;
			if (item.m_pShaderResources)
			{
				for (int t = 0; t < EFTT_MAX; ++t)
				{
					SEfResTexture* pTex = item.m_pShaderResources->GetTexture(t);
					if (!pTex || pTex->m_Name.empty())
						continue;
					ITexture* pBound = pTex->m_Sampler.m_pITex;
					one << (firstTex ? "" : ",") << "{\"slot\":\"" << kSlots[t] << "\",\"file\":\"" << EnvStr(pTex->m_Name.c_str()) << "\",\"bound\":" << (pBound ? "true" : "false")
					    << ",\"bound_file\":\"" << EnvStr(pBound ? pBound->GetName() : "") << "\",\"w\":" << (pBound ? pBound->GetWidth() : 0) << ",\"h\":" << (pBound ? pBound->GetHeight() : 0)
					    << ",\"loaded\":" << (pBound && pBound->IsTextureLoaded() ? "true" : "false") << "}";
					firstTex = false;
				}
			}
			one << "]}";
		};

		std::ostringstream out;
		out << "[\n";
		std::string line;
		int count = 0, found = 0;
		while (std::getline(in, line))
		{
			line = trim(line);
			if (line.empty())
				continue;
			LogGrab grab;
			grab.Begin();
			const bool already = pMatMan->FindMaterial(line.c_str()) != nullptr;
			_smart_ptr<IMaterial> pMtl = pMatMan->LoadMaterial(line.c_str(), false, false);
			std::ostringstream one;
			one << "{\"file\":\"" << EnvStr(line.c_str()) << "\",\"found\":" << (pMtl ? "true" : "false") << ",\"already_loaded\":" << (already ? "true" : "false");
			if (pMtl)
			{
				++found;
				one << ",\"is_default\":" << (pMtl->IsDefault() ? "true" : "false") << ",\"name\":\"" << EnvStr(pMtl->GetName()) << "\",\"flags\":" << pMtl->GetFlags() << ",\"subs\":[";
				const int subs = pMtl->GetSubMtlCount();
				if (subs <= 0)
					describe(pMtl, -1, one);
				for (int i = 0; i < subs; ++i)
				{
					IMaterial* pSub = pMtl->GetSubMtl(i);
					one << (i ? "," : "");
					if (pSub)
						describe(pSub, i, one);
					else
						one << "{\"slot\":" << i << ",\"missing\":true}";
				}
				one << "]";
			}
			one << ",\"log\":[";
			for (size_t i = 0; i < grab.lines.size(); ++i)
				one << (i ? "," : "") << "\"" << EnvStr(grab.lines[i].c_str()) << "\"";
			one << "]}";
			out << (count ? ",\n" : "") << one.str();
			++count;
		}
		out << "\n]\n";
		std::ofstream file(outPath, std::ios::binary | std::ios::trunc);
		if (!file)
			throw std::runtime_error("cannot write " + outPath);
		file << out.str();
		file.close();
		std::ostringstream reply;
		reply << "{\"ok\": true, \"path\": \"" << JsonEscape(outPath) << "\", \"asked\": " << count << ", \"found\": " << found << "}";
		return reply.str();
	}

	// bridge.drs.dump <output file> [| reload [| <signal name> ...]]
	// The Dynamic Response System as the ENGINE holds it. With "reload" the system is re-initialised first (IDynamicResponseSystem::ReInit, which the game's own Dialogue.cpp calls too; the editor's DRS state is
	// reset, nothing else) and the log lines it wrote are kept. The file holds {"available", "reloaded", "reinit_result", "log": [...], "actions": [{"name", "label"}], "conditions": [{"name", "label"}],
	// "lines": [{"id", "priority", "flags", "queue", "variations": [{"text", "start", "stop", "lipsync", "pause", "custom"}]}], "responses": {"<signal>": "<the response tree as the engine serialises it, JSON text>" | null}}.
	// The actions and conditions are the types the engine's class factories know (what a .response file may use); the response text is what the engine KEPT from the file.
	if (verb == "bridge.drs.dump")
	{
		std::string rest = cmd.substr(cmd.find(verb) + verb.size());
		std::vector<std::string> parts;
		{
			size_t from = 0;
			while (true)
			{
				const size_t bar = rest.find('|', from);
				std::string piece = rest.substr(from, bar == std::string::npos ? std::string::npos : bar - from);
				const size_t b = piece.find_first_not_of(" \t\r\n"), e = piece.find_last_not_of(" \t\r\n");
				parts.push_back(b == std::string::npos ? std::string() : piece.substr(b, e - b + 1));
				if (bar == std::string::npos)
					break;
				from = bar + 1;
			}
		}
		if (parts.empty() || parts[0].empty())
			throw std::runtime_error("bridge.drs.dump requires <output file> [| reload [| signal ...]]");
		const std::string outPath = parts[0];
		bool reload = false;
		std::vector<std::string> sigNames;
		for (size_t i = 1; i < parts.size(); ++i)
		{
			if (parts[i] == "reload")
				reload = true;
			else if (!parts[i].empty())
				sigNames.push_back(parts[i]);
		}
		struct LogGrab : public ILogCallback
		{
			std::vector<std::string> lines;
			ICVar* v1 = nullptr;
			ICVar* v2 = nullptr;
			int o1 = 0, o2 = 0;
			bool added = false;
			void OnWriteToConsole(const char* s, bool) override { if (s) lines.push_back(s); }
			void OnWriteToFile(const char*, bool) override {}
			void Begin()
			{
				v1 = gEnv->pConsole->GetCVar("log_Verbosity");
				v2 = gEnv->pConsole->GetCVar("log_WriteToFileVerbosity");
				o1 = v1 ? v1->GetIVal() : 0;
				o2 = v2 ? v2->GetIVal() : 0;
				if (v1) v1->Set(4);
				if (v2) v2->Set(4);
				gEnv->pLog->AddCallback(this);
				added = true;
			}
			~LogGrab()
			{
				if (added) gEnv->pLog->RemoveCallback(this);
				if (v1) v1->Set(o1);
				if (v2) v2->Set(o2);
			}
		};
		struct SResponseSerializer
		{
			DRS::IResponseManager* pManager = nullptr;
			DynArray<stack_string> names;
			void Serialize(Serialization::IArchive& ar) { pManager->SerializeResponse(ar, names); }
		};
		std::ostringstream out;
		DRS::IDynamicResponseSystem* pDRS = gEnv ? gEnv->pDynamicResponseSystem : nullptr;
		out << "{\"available\":" << (pDRS ? "true" : "false");
		if (pDRS)
		{
			LogGrab grab;
			bool reinit = false;
			if (reload)
			{
				grab.Begin();
				reinit = pDRS->ReInit();
			}
			out << ",\"reloaded\":" << (reload ? "true" : "false") << ",\"reinit_result\":" << (reinit ? "true" : "false") << ",\"log\":[";
			for (size_t i = 0; i < grab.lines.size(); ++i)
				out << (i ? "," : "") << "\"" << EnvStr(grab.lines[i].c_str()) << "\"";
			out << "],\"actions\":[";
			{
				DRS::ActionSerializationClassFactory& f = pDRS->GetActionSerializationFactory();
				for (size_t i = 0; i < f.size(); ++i)
				{
					const yasli::TypeDescription* d = f.descriptionByIndex((int)i);
					out << (i ? "," : "") << "{\"name\":\"" << EnvStr(d ? d->name() : "") << "\",\"label\":\"" << EnvStr(d ? d->label() : "") << "\"}";
				}
			}
			out << "],\"conditions\":[";
			{
				DRS::ConditionSerializationClassFactory& f = pDRS->GetConditionSerializationFactory();
				for (size_t i = 0; i < f.size(); ++i)
				{
					const yasli::TypeDescription* d = f.descriptionByIndex((int)i);
					out << (i ? "," : "") << "{\"name\":\"" << EnvStr(d ? d->name() : "") << "\",\"label\":\"" << EnvStr(d ? d->label() : "") << "\"}";
				}
			}
			out << "],\"lines\":[";
			if (DRS::IDialogLineDatabase* pDb = pDRS->GetDialogLineDatabase())
			{
				const uint32 n = pDb->GetLineSetCount();
				for (uint32 i = 0; i < n; ++i)
				{
					DRS::IDialogLineSet* pSet = pDb->GetLineSetByIndex(i);
					if (!pSet)
						continue;
					out << (i ? "," : "") << "{\"id\":\"" << EnvStr(pSet->GetLineId().GetText().c_str()) << "\",\"priority\":" << pSet->GetPriority() << ",\"flags\":" << pSet->GetFlags() << ",\"queue\":" << pSet->GetMaxQueuingDuration() << ",\"variations\":[";
					for (uint32 v = 0; v < pSet->GetLineCount(); ++v)
					{
						DRS::IDialogLine* pLine = pSet->GetLineByIndex(v);
						if (!pLine)
							continue;
						out << (v ? "," : "") << "{\"text\":\"" << EnvStr(pLine->GetText().c_str()) << "\",\"start\":\"" << EnvStr(pLine->GetStartAudioTrigger().c_str()) << "\",\"stop\":\"" << EnvStr(pLine->GetEndAudioTrigger().c_str())
						    << "\",\"lipsync\":\"" << EnvStr(pLine->GetLipsyncAnimation().c_str()) << "\",\"pause\":" << pLine->GetPauseLength() << ",\"custom\":\"" << EnvStr(pLine->GetCustomData().c_str()) << "\"}";
					}
					out << "]}";
				}
			}
			out << "],\"responses\":{";
			DRS::IResponseManager* pMgr = pDRS->GetResponseManager();
			for (size_t i = 0; i < sigNames.size(); ++i)
			{
				out << (i ? "," : "") << "\"" << EnvStr(sigNames[i].c_str()) << "\":";
				bool wrote = false;
				if (pMgr)
				{
					SResponseSerializer ser;
					ser.pManager = pMgr;
					ser.names.push_back(stack_string(sigNames[i].c_str()));
					DynArray<char> buf;
					if (gEnv->pSystem->GetArchiveHost()->SaveJsonBuffer(buf, Serialization::SStruct(ser)) && !buf.empty())
					{
						out << "\"" << EnvStr(std::string(buf.begin(), buf.end()).c_str()) << "\"";
						wrote = true;
					}
				}
				if (!wrote)
					out << "null";
			}
			out << "}";
		}
		out << "}\n";
		std::ofstream file(outPath, std::ios::binary | std::ios::trunc);
		if (!file)
			throw std::runtime_error("cannot write " + outPath);
		file << out.str();
		file.close();
		std::ostringstream reply;
		reply << "{\"ok\": true, \"path\": \"" << JsonEscape(outPath) << "\", \"available\": " << (pDRS ? "true" : "false") << "}";
		return reply.str();
	}

	// bridge.ui.dump <output file> [| init [| <UI element> ...]]
	// The UI system (FlashUI, gEnv->pFlashUI) as the ENGINE holds it: every UI element read from Libs/UI/UIElements/*.xml with its flash file, layer, flags, constraints and the functions, events, variables, arrays and
	// movie clips it KEPT (display name, the name in the movie, type, parameters), the UI actions (flow graph / Lua) and the event systems. With "init" the named elements (all when none is named) are loaded in the
	// editor (IUIElement::Init, the call the game makes when a screen is shown), every kept name is asked of the movie (IFlashPlayer::IsAvailable) and the element is unloaded again unless it was loaded before.
	// The file holds {"available", "elements": [...], "actions": [...], "event_systems": [...]}.
	if (verb == "bridge.ui.dump")
	{
		std::string rest = cmd.substr(cmd.find(verb) + verb.size());
		std::vector<std::string> parts;
		{
			size_t from = 0;
			while (true)
			{
				const size_t bar = rest.find('|', from);
				std::string piece = rest.substr(from, bar == std::string::npos ? std::string::npos : bar - from);
				const size_t b = piece.find_first_not_of(" \t\r\n"), e = piece.find_last_not_of(" \t\r\n");
				parts.push_back(b == std::string::npos ? std::string() : piece.substr(b, e - b + 1));
				if (bar == std::string::npos)
					break;
				from = bar + 1;
			}
		}
		if (parts.empty() || parts[0].empty())
			throw std::runtime_error("bridge.ui.dump requires <output file> [| init [| element ...]]");
		const std::string outPath = parts[0];
		bool doInit = false;
		std::vector<std::string> only;
		for (size_t i = 1; i < parts.size(); ++i)
		{
			if (parts[i] == "init")
				doInit = true;
			else if (!parts[i].empty())
				only.push_back(parts[i]);
		}
		std::ostringstream out;
		IFlashUI* pUI = gEnv ? gEnv->pFlashUI : nullptr;
		out << "{\"available\":" << (pUI ? "true" : "false");
		int elementCount = 0, loaded = 0;
		if (pUI)
		{
			auto typeName = [](SUIParameterDesc::EUIParameterType t) -> const char*
			{
				switch (t)
				{
				case SUIParameterDesc::eUIPT_Bool: return "bool";
				case SUIParameterDesc::eUIPT_Int: return "int";
				case SUIParameterDesc::eUIPT_Float: return "float";
				case SUIParameterDesc::eUIPT_Vec3: return "vec3";
				case SUIParameterDesc::eUIPT_String: return "string";
				case SUIParameterDesc::eUIPT_WString: return "wstring";
				default: return "any";
				}
			};
			auto pathOf = [](const SUIParameterDesc* d)
			{
				string s;
				d->GetInstanceString(s);
				return std::string(s.c_str());
			};
			struct Movie
			{
				bool loaded = false;
				IFlashPlayer* pPlayer = nullptr;
			};
			auto askMovie = [](const Movie& m, const std::string& path, std::ostringstream& o)
			{
				if (m.loaded && m.pPlayer)
					o << ",\"in_movie\":" << (m.pPlayer->IsAvailable(path.c_str()) ? "true" : "false");
			};
			auto writeParams = [&](const char* key, const TUIParams& params, std::ostringstream& o)
			{
				o << ",\"" << key << "\":[";
				for (int p = 0; p < params.size(); ++p)
					o << (p ? "," : "") << "{\"name\":\"" << EnvStr(params[p].sDisplayName) << "\",\"movie_name\":\"" << EnvStr(params[p].sName) << "\",\"type\":\"" << typeName(params[p].eType) << "\"}";
				o << "]";
			};
			auto wanted = [&](const char* name)
			{
				if (only.empty())
					return true;
				for (const std::string& n : only)
					if (_stricmp(n.c_str(), name) == 0)
						return true;
				return false;
			};
			struct FlagName
			{
				const char* name;
				IUIElement::EFlashUIFlags flag;
			};
			static const FlagName flagNames[] = {
				{ "hardware_cursor", IUIElement::eFUI_HARDWARECURSOR }, { "mouse_events", IUIElement::eFUI_MOUSEEVENTS }, { "key_events", IUIElement::eFUI_KEYEVENTS },
				{ "console_mouse", IUIElement::eFUI_CONSOLE_MOUSE }, { "console_cursor", IUIElement::eFUI_CONSOLE_CURSOR }, { "controller_input", IUIElement::eFUI_CONTROLLER_INPUT },
				{ "events_exclusive", IUIElement::eFUI_EVENTS_EXCLUSIVE }, { "render_lockless", IUIElement::eFUI_RENDER_LOCKLESS }, { "fixed_proj_depth", IUIElement::eFUI_FIXED_PROJ_DEPTH },
				{ "is_hud", IUIElement::eFUI_IS_HUD }, { "shared_rt", IUIElement::eFUI_SHARED_RT }, { "no_auto_update", IUIElement::eFUI_NO_AUTO_UPDATE },
				{ "show_at_loading_time", IUIElement::eFUI_SHOW_AT_LOADING_TIME }, { "force_no_unload", IUIElement::eFUI_FORCE_NO_UNLOAD } };
			static const char* const positionTypes[] = { "fixed", "fullscreen", "dynamic", "fixdyntexsize" };
			static const char* const alignTypes[] = { "lower", "mid", "upper" };
			elementCount = pUI->GetUIElementCount();
			out << ",\"element_count\":" << elementCount << ",\"elements\":[";
			bool firstElement = true;
			for (int i = 0; i < elementCount; ++i)
			{
				IUIElement* pElem = pUI->GetUIElement(i);
				if (!pElem)
					continue;
				out << (firstElement ? "" : ",\n");
				firstElement = false;
				out << "{\"name\":\"" << EnvStr(pElem->GetName()) << "\",\"group\":\"" << EnvStr(pElem->GetGroupName()) << "\",\"file\":\"" << EnvStr(pElem->GetFlashFile()) << "\",\"layer\":" << pElem->GetLayer()
				    << ",\"alpha\":" << pElem->GetAlpha() << ",\"valid\":" << (pElem->IsValid() ? "true" : "false") << ",\"was_init\":" << (pElem->IsInit() ? "true" : "false");
				const IUIElement::SUIConstraints& c = pElem->GetConstraints();
				out << ",\"constraints\":{\"type\":\"" << positionTypes[(int)c.eType < 4 ? (int)c.eType : 2] << "\",\"halign\":\"" << alignTypes[(int)c.eHAlign < 3 ? (int)c.eHAlign : 1] << "\",\"valign\":\""
				    << alignTypes[(int)c.eVAlign < 3 ? (int)c.eVAlign : 1] << "\",\"scale\":" << (c.bScale ? "true" : "false") << ",\"max\":" << (c.bMax ? "true" : "false") << ",\"left\":" << c.iLeft << ",\"top\":" << c.iTop
				    << ",\"width\":" << c.iWidth << ",\"height\":" << c.iHeight << "},\"flags\":[";
				bool firstFlag = true;
				for (const FlagName& f : flagNames)
					if (pElem->HasFlag(f.flag))
					{
						out << (firstFlag ? "" : ",") << "\"" << f.name << "\"";
						firstFlag = false;
					}
				out << "]";
				Movie movie;
				const bool wasInit = pElem->IsInit();
				if (doInit && wanted(pElem->GetName()))
				{
					out << ",\"init_requested\":true";
					if (pElem->Init(true))
					{
						std::shared_ptr<IFlashPlayer> pPlayer = pElem->GetFlashPlayer();
						movie.loaded = pPlayer != nullptr;
						movie.pPlayer = pPlayer.get();
						if (movie.loaded)
						{
							++loaded;
							out << ",\"movie\":{\"width\":" << pPlayer->GetWidth() << ",\"height\":" << pPlayer->GetHeight() << ",\"frames\":" << pPlayer->GetFrameCount() << ",\"frame_rate\":" << pPlayer->GetFrameRate()
							    << ",\"control_root\":" << (pPlayer->IsAvailable("_root") ? "true" : "false") << ",\"control_absent\":" << (pPlayer->IsAvailable("_root.zz_mcp_no_such_name") ? "true" : "false") << "}";
						}
					}
					out << ",\"movie_loaded\":" << (movie.loaded ? "true" : "false");
				}
				// the descriptions are read while the movie is up, so each can be asked of it
				out << ",\"functions\":[";
				for (int k = 0; k < pElem->GetFunctionCount(); ++k)
				{
					const SUIEventDesc* d = pElem->GetFunctionDesc(k);
					if (!d)
						continue;
					out << (k ? "," : "") << "{\"name\":\"" << EnvStr(d->sDisplayName) << "\",\"movie_name\":\"" << EnvStr(d->sName) << "\",\"desc\":\"" << EnvStr(d->sDesc) << "\",\"dynamic\":" << (d->InputParams.IsDynamic ? "true" : "false");
					writeParams("params", d->InputParams.Params, out);
					askMovie(movie, pathOf(d), out);
					out << "}";
				}
				out << "],\"events\":[";
				for (int k = 0; k < pElem->GetEventCount(); ++k)
				{
					const SUIEventDesc* d = pElem->GetEventDesc(k);
					if (!d)
						continue;
					out << (k ? "," : "") << "{\"name\":\"" << EnvStr(d->sDisplayName) << "\",\"fscommand\":\"" << EnvStr(d->sName) << "\",\"desc\":\"" << EnvStr(d->sDesc) << "\",\"dynamic\":" << (d->InputParams.IsDynamic ? "true" : "false");
					writeParams("params", d->InputParams.Params, out);
					out << "}";
				}
				out << "],\"variables\":[";
				for (int k = 0; k < pElem->GetVariableCount(); ++k)
				{
					const SUIParameterDesc* d = pElem->GetVariableDesc(k);
					if (!d)
						continue;
					out << (k ? "," : "") << "{\"name\":\"" << EnvStr(d->sDisplayName) << "\",\"movie_name\":\"" << EnvStr(d->sName) << "\",\"type\":\"" << typeName(d->eType) << "\"";
					askMovie(movie, pathOf(d), out);
					out << "}";
				}
				out << "],\"arrays\":[";
				for (int k = 0; k < pElem->GetArrayCount(); ++k)
				{
					const SUIParameterDesc* d = pElem->GetArrayDesc(k);
					if (!d)
						continue;
					out << (k ? "," : "") << "{\"name\":\"" << EnvStr(d->sDisplayName) << "\",\"movie_name\":\"" << EnvStr(d->sName) << "\"";
					askMovie(movie, pathOf(d), out);
					out << "}";
				}
				out << "],\"movieclips\":[";
				for (int k = 0; k < pElem->GetMovieClipCount(); ++k)
				{
					const SUIMovieClipDesc* d = pElem->GetMovieClipDesc(k);
					if (!d)
						continue;
					out << (k ? "," : "") << "{\"name\":\"" << EnvStr(d->sDisplayName) << "\",\"movie_name\":\"" << EnvStr(d->sName) << "\"";
					askMovie(movie, pathOf(d), out);
					out << "}";
				}
				out << "],\"templates\":" << pElem->GetMovieClipTmplCount();
				if (movie.loaded && !wasInit)
					pElem->Unload();
				out << "}";
			}
			out << "],\"action_count\":" << pUI->GetUIActionCount() << ",\"actions\":[";
			for (int i = 0; i < pUI->GetUIActionCount(); ++i)
			{
				IUIAction* pAct = pUI->GetUIAction(i);
				if (!pAct)
					continue;
				out << (i ? "," : "") << "{\"name\":\"" << EnvStr(pAct->GetName()) << "\",\"type\":\"" << (pAct->GetType() == IUIAction::eUIAT_FlowGraph ? "flowgraph" : "lua") << "\",\"valid\":" << (pAct->IsValid() ? "true" : "false")
				    << ",\"enabled\":" << (pAct->IsEnabled() ? "true" : "false") << "}";
			}
			out << "],\"event_systems\":[";
			bool firstSystem = true;
			for (int dir = 0; dir < 2; ++dir)
			{
				const IUIEventSystem::EEventSystemType type = dir == 0 ? IUIEventSystem::eEST_UI_TO_SYSTEM : IUIEventSystem::eEST_SYSTEM_TO_UI;
				IUIEventSystemIteratorPtr it = pUI->CreateEventSystemIterator(type);
				if (!it)
					continue;
				string sysName;
				while (IUIEventSystem* pSys = it->Next(sysName))
				{
					out << (firstSystem ? "" : ",") << "{\"name\":\"" << EnvStr(sysName.c_str()) << "\",\"direction\":\"" << (dir == 0 ? "ui_to_system" : "system_to_ui") << "\",\"events\":[";
					firstSystem = false;
					for (int k = 0; k < pSys->GetEventCount(); ++k)
					{
						const SUIEventDesc* d = pSys->GetEventDesc(k);
						if (d)
							out << (k ? "," : "") << "{\"name\":\"" << EnvStr(d->sDisplayName) << "\",\"inputs\":" << d->InputParams.Params.size() << ",\"outputs\":" << d->OutputParams.Params.size() << "}";
					}
					out << "]}";
				}
			}
			out << "]";
		}
		out << "}\n";
		std::ofstream file(outPath, std::ios::binary | std::ios::trunc);
		if (!file)
			throw std::runtime_error("cannot write " + outPath);
		file << out.str();
		file.close();
		std::ostringstream reply;
		reply << "{\"ok\": true, \"path\": \"" << JsonEscape(outPath) << "\", \"available\": " << (pUI ? "true" : "false") << ", \"elements\": " << elementCount << ", \"movies_loaded\": " << loaded << "}";
		return reply.str();
	}

	// bridge.visarea.dump <output file> [| x y z [x y z ...]]
	// The vis areas and portals the 3D ENGINE holds (I3DEngine::GetIVisAreaManager: GetNumberOfVisArea / GetVisAreaById; occluder areas are kept elsewhere and are not listed): name (the engine keeps it lower case;
	// the editor names portals "Portal_<object>"), portal or not, connected to the outdoors, the IClipVolume flags (affected by sun, ignores GI / outdoor AO), bounding box, shape points, ambient colour and the
	// names of the areas it connects to (all portals, and with the disabled ones skipped). After the bar: for every position the area that contains it (I3DEngine::GetVisAreaFromPos), null outdoors.
	// The file holds {"available", "count", "areas": [...], "probes": [...]}.
	if (verb == "bridge.visarea.dump")
	{
		std::string rest = cmd.substr(cmd.find(verb) + verb.size());
		auto trimmed = [](std::string s)
		{
			const size_t b = s.find_first_not_of(" \t\r\n"), e = s.find_last_not_of(" \t\r\n");
			return b == std::string::npos ? std::string() : s.substr(b, e - b + 1);
		};
		const size_t bar = rest.find('|');
		const std::string outPath = trimmed(bar == std::string::npos ? rest : rest.substr(0, bar));
		if (outPath.empty())
			throw std::runtime_error("bridge.visarea.dump requires <output file> [| x y z ...]");
		std::vector<Vec3> probes;
		if (bar != std::string::npos)
		{
			std::istringstream nums(rest.substr(bar + 1));
			std::string tok;
			float c[3] = { 0, 0, 0 };
			int k = 0;
			while (nums >> tok)
			{
				c[k++] = static_cast<float>(std::atof(tok.c_str()));
				if (k == 3)
				{
					probes.push_back(Vec3(c[0], c[1], c[2]));
					k = 0;
				}
			}
			if (k != 0)
				throw std::runtime_error("bridge.visarea.dump: positions come in threes (x y z)");
		}
		I3DEngine* p3D = gEnv ? gEnv->p3DEngine : nullptr;
		IVisAreaManager* pMan = p3D ? p3D->GetIVisAreaManager() : nullptr;
		auto num = [](float f)
		{
			char b[48];
			snprintf(b, sizeof(b), "%.3f", f);
			return std::string(b);
		};
		auto vec3 = [&](const Vec3& v) { return "[" + num(v.x) + "," + num(v.y) + "," + num(v.z) + "]"; };
		auto box = [&](const AABB& b) { return "[" + vec3(b.min) + "," + vec3(b.max) + "]"; };
		std::ostringstream out;
		const int count = pMan ? pMan->GetNumberOfVisArea() : 0;
		out << "{\"available\":" << (pMan ? "true" : "false") << ",\"count\":" << count << ",\"areas\":[";
		bool first = true;
		for (int i = 0; i < count; ++i)
		{
			IVisArea* pArea = pMan->GetVisAreaById(i);
			if (!pArea)
				continue;
			out << (first ? "" : ",");
			first = false;
			const uint flags = pArea->GetClipVolumeFlags();
			out << "{\"id\":" << i << ",\"name\":\"" << EnvStr(pArea->GetName()) << "\",\"portal\":" << (pArea->IsPortal() ? "true" : "false")
			    << ",\"connected_to_outdoor\":" << (pArea->IsConnectedToOutdoor() ? "true" : "false")
			    << ",\"affected_by_sun\":" << (pArea->IsAffectedByOutLights() ? "true" : "false")
			    << ",\"ignore_gi\":" << (pArea->IsIgnoringGI() ? "true" : "false")
			    << ",\"ignore_outdoor_ao\":" << (pArea->IsIgnoringOutdoorAO() ? "true" : "false")
			    << ",\"flags\":" << flags << ",\"stencil_ref\":" << static_cast<int>(pArea->GetStencilRef());
			if (const AABB* pBox = pArea->GetAABBox())
				out << ",\"aabb\":" << box(*pBox);
			if (const AABB* pStatic = pArea->GetStaticObjectAABBox())
				out << ",\"static_aabb\":" << box(*pStatic);
			out << ",\"ambient\":" << vec3(pArea->GetFinalAmbientColor());
			const Vec3* pPoints = nullptr;
			size_t nPoints = 0;
			pArea->GetShapePoints(pPoints, nPoints);
			out << ",\"points\":[";
			for (size_t p = 0; pPoints && p < nPoints; ++p)
				out << (p ? "," : "") << vec3(pPoints[p]);
			out << "]";
			for (int pass = 0; pass < 2; ++pass)
			{
				IVisArea* conn[32] = {};
				const int n = pArea->GetVisAreaConnections(conn, 32, pass == 1);
				out << (pass == 0 ? ",\"connections\":[" : ",\"connections_enabled\":[");
				for (int c2 = 0; c2 < n && c2 < 32; ++c2)
					out << (c2 ? "," : "") << "\"" << EnvStr(conn[c2] ? conn[c2]->GetName() : "") << "\"";
				out << "]";
			}
			out << "}";
		}
		out << "],\"probes\":[";
		// GetVisAreaFromPos reads the area tree, which every area edit deletes and only a rendered frame (SetCurAreas) or an intersection query rebuilds: asked cold it answers null, which looks like "outdoors".
		// An intersection test with a tiny box rebuilds it. "inside" is the direct per-area test (IsPointInsideVisArea), which does not depend on the tree.
		if (p3D && !probes.empty())
			p3D->IntersectsVisAreas(AABB(Vec3(0, 0, 0), Vec3(0.01f, 0.01f, 0.01f)));
		for (size_t i = 0; i < probes.size(); ++i)
		{
			IVisArea* pArea = p3D ? p3D->GetVisAreaFromPos(probes[i]) : nullptr;
			out << (i ? "," : "") << "{\"pos\":" << vec3(probes[i]) << ",\"area\":";
			if (pArea)
				out << "\"" << EnvStr(pArea->GetName()) << "\",\"portal\":" << (pArea->IsPortal() ? "true" : "false");
			else
				out << "null,\"portal\":false";
			out << ",\"inside\":[";
			bool firstInside = true;
			for (int a = 0; pMan && a < count; ++a)
			{
				IVisArea* pTest = pMan->GetVisAreaById(a);
				if (pTest && pTest->IsPointInsideVisArea(probes[i]))
				{
					out << (firstInside ? "" : ",") << "\"" << EnvStr(pTest->GetName()) << "\"";
					firstInside = false;
				}
			}
			out << "]}";
		}
		out << "]}\n";
		std::ofstream file(outPath, std::ios::binary | std::ios::trunc);
		if (!file)
			throw std::runtime_error("cannot write " + outPath);
		file << out.str();
		file.close();
		std::ostringstream reply;
		reply << "{\"ok\": true, \"path\": \"" << JsonEscape(outPath) << "\", \"available\": " << (pMan ? "true" : "false") << ", \"count\": " << count << ", \"probes\": " << probes.size() << "}";
		return reply.str();
	}

	// bridge.undo.mode [on|off]       whether the object verbs record an editor undo step (default off; see s_claudeUndo). Answers the mode.
	// bridge.undo.info                {"mode", "undo_steps", "redo_steps", "latest": [the last 10 undo step names, newest first]} from the editor's own IUndoManager
	// bridge.undo.step <undo|redo> [n]  the editor's Undo / Redo (what Ctrl+Z / Ctrl+Y do), n steps (1-50); answers the stack sizes after
	if (verb == "bridge.undo.mode" || verb == "bridge.undo.info" || verb == "bridge.undo.step")
	{
		IUndoManager* pUndoMan = GetIEditor() ? GetIEditor()->GetIUndoManager() : nullptr;
		if (!pUndoMan)
			throw std::runtime_error("the editor's undo manager is not available");
		std::ostringstream oss;
		if (verb == "bridge.undo.mode" && tokens.size() > 1)
		{
			if (tokens[1] != "on" && tokens[1] != "off")
				throw std::runtime_error("bridge.undo.mode takes on or off");
			s_claudeUndo = (tokens[1] == "on");
		}
		if (verb == "bridge.undo.step")
		{
			if (tokens.size() < 2 || (tokens[1] != "undo" && tokens[1] != "redo"))
				throw std::runtime_error("bridge.undo.step requires undo or redo [n]");
			int n = tokens.size() > 2 ? std::atoi(tokens[2].c_str()) : 1;
			n = n < 1 ? 1 : (n > 50 ? 50 : n);
			if (tokens[1] == "undo")
				pUndoMan->Undo(n);
			else
				pUndoMan->Redo(n);
		}
		oss << "{\"ok\": true, \"mode\": \"" << (s_claudeUndo ? "on" : "off") << "\", \"undo_steps\": " << pUndoMan->GetUndoStackLen() << ", \"redo_steps\": " << pUndoMan->GetRedoStackLen() << ", \"latest\": [";
		const std::list<CUndoStep*>& stack = pUndoMan->GetUndoStack();
		int shown = 0;
		for (auto it = stack.rbegin(); it != stack.rend() && shown < 10; ++it, ++shown)
		{
			string stepName;
			pUndoMan->GetCommandName(*it, stepName);
			oss << (shown ? "," : "") << "\"" << EnvStr((const char*)stepName) << "\"";
		}
		oss << "]}";
		return oss.str();
	}

	// bridge.notify <info|warning|critical> <title> | <message>
	// A non-modal message in the editor's Notification Center (INotificationCenter::ShowInfo / ShowWarning / ShowCritical: the manual's preferred way to tell the user something; no dialog, no focus).
	if (verb == "bridge.notify")
	{
		std::string rest = cmd.substr(cmd.find(verb) + verb.size());
		auto trimmed = [](std::string s)
		{
			const size_t b = s.find_first_not_of(" \t\r\n"), e = s.find_last_not_of(" \t\r\n");
			return b == std::string::npos ? std::string() : s.substr(b, e - b + 1);
		};
		rest = trimmed(rest);
		const size_t space = rest.find_first_of(" \t");
		const std::string level = rest.substr(0, space);
		const std::string body = space == std::string::npos ? std::string() : trimmed(rest.substr(space));
		const size_t bar = body.find('|');
		const std::string title = trimmed(bar == std::string::npos ? body : body.substr(0, bar));
		const std::string message = bar == std::string::npos ? std::string() : trimmed(body.substr(bar + 1));
		if ((level != "info" && level != "warning" && level != "critical") || title.empty())
			throw std::runtime_error("bridge.notify requires <info|warning|critical> <title> | <message>");
		INotificationCenter* pCenter = GetIEditor() ? GetIEditor()->GetNotificationCenter() : nullptr;
		if (!pCenter)
			throw std::runtime_error("the editor's notification center is not available");
		const QString qTitle = QString::fromUtf8(title.c_str()), qMessage = QString::fromUtf8(message.c_str());
		const int id = level == "info" ? pCenter->ShowInfo(qTitle, qMessage) : (level == "warning" ? pCenter->ShowWarning(qTitle, qMessage) : pCenter->ShowCritical(qTitle, qMessage));
		std::ostringstream oss;
		oss << "{\"ok\": true, \"id\": " << id << ", \"level\": \"" << level << "\"}";
		return oss.str();
	}

	// bridge.character.names <output file> | <character .cdf / .chr path>
	// The names the ENGINE resolved for one character: every joint of its default skeleton (GetJointNameByID) and every animation of its animation set (GetNameByAnimID), the lists a chrparams, a blend space
	// or a Mannequin database is checked against. The character is created and released at once (AddRef first: CreateInstance returns a reference count of zero).
	// The file holds {"file", "created", "skeleton", "joints": [...], "animations": [...]}.
	if (verb == "bridge.character.names")
	{
		std::string rest = cmd.substr(cmd.find(verb) + verb.size());
		const size_t bar = rest.find('|');
		if (bar == std::string::npos)
			throw std::runtime_error("bridge.character.names requires <output file> | <character path>");
		auto trim = [](std::string s)
		{
			const size_t b = s.find_first_not_of(" \t\r\n");
			const size_t e = s.find_last_not_of(" \t\r\n");
			return b == std::string::npos ? std::string() : s.substr(b, e - b + 1);
		};
		const std::string outPath = trim(rest.substr(0, bar)), charPath = trim(rest.substr(bar + 1));
		if (!gEnv || !gEnv->pCharacterManager)
			throw std::runtime_error("the character manager is not available");
		std::ostringstream out;
		out << "{\"file\":\"" << EnvStr(charPath.c_str()) << "\"";
		ICharacterInstance* pChar = gEnv->pCharacterManager->CreateInstance(charPath.c_str());
		out << ",\"created\":" << (pChar ? "true" : "false");
		int joints = 0, anims = 0;
		if (pChar)
		{
			pChar->AddRef();
			const IDefaultSkeleton& skel = pChar->GetIDefaultSkeleton();
			out << ",\"skeleton\":\"" << EnvStr(skel.GetModelFilePath()) << "\",\"joints\":[";
			joints = (int)skel.GetJointCount();
			for (int i = 0; i < joints; ++i)
			{
				const char* n = skel.GetJointNameByID(i);
				out << (i ? "," : "") << "\"" << EnvStr(n ? n : "") << "\"";
			}
			out << "],\"animations\":[";
			if (IAnimationSet* pSet = pChar->GetIAnimationSet())
			{
				anims = (int)pSet->GetAnimationCount();
				for (int i = 0; i < anims; ++i)
				{
					const char* n = pSet->GetNameByAnimID(i);
					out << (i ? "," : "") << "\"" << EnvStr(n ? n : "") << "\"";
				}
			}
			out << "]";
			pChar->Release();
		}
		out << "}\n";
		std::ofstream file(outPath, std::ios::binary | std::ios::trunc);
		if (!file)
			throw std::runtime_error("cannot write " + outPath);
		file << out.str();
		file.close();
		std::ostringstream reply;
		reply << "{\"ok\": true, \"path\": \"" << JsonEscape(outPath) << "\", \"created\": " << (pChar ? "true" : "false") << ", \"joints\": " << joints << ", \"animations\": " << anims << "}";
		return reply.str();
	}

	// bridge.character.bindpose <output file> | <character .cdf / .chr path>
	// The BIND (default) pose of one character's skeleton as the engine holds it: for every joint its name, parent index, absolute transform in character space (GetDefaultAbsJointByID: quaternion w x y z and
	// translation) and parent-relative transform (GetDefaultRelJointByID). Retargeting motion onto the rig needs the rig's own local axes, which an animation file alone does not give (2026-10-15, MotionBricks).
	// The file holds {"file", "created", "skeleton", "joints": [{"n","p","aq","at","rq","rt"}, ...]}.
	if (verb == "bridge.character.bindpose")
	{
		std::string rest = cmd.substr(cmd.find(verb) + verb.size());
		const size_t bar = rest.find('|');
		if (bar == std::string::npos)
			throw std::runtime_error("bridge.character.bindpose requires <output file> | <character path>");
		auto trim = [](std::string s)
		{
			const size_t b = s.find_first_not_of(" \t\r\n");
			const size_t e = s.find_last_not_of(" \t\r\n");
			return b == std::string::npos ? std::string() : s.substr(b, e - b + 1);
		};
		const std::string outPath = trim(rest.substr(0, bar)), charPath = trim(rest.substr(bar + 1));
		if (!gEnv || !gEnv->pCharacterManager)
			throw std::runtime_error("the character manager is not available");
		std::ostringstream out;
		out << std::setprecision(9);
		out << "{\"file\":\"" << EnvStr(charPath.c_str()) << "\"";
		ICharacterInstance* pChar = gEnv->pCharacterManager->CreateInstance(charPath.c_str());
		out << ",\"created\":" << (pChar ? "true" : "false");
		int joints = 0;
		if (pChar)
		{
			pChar->AddRef();
			const IDefaultSkeleton& skel = pChar->GetIDefaultSkeleton();
			out << ",\"skeleton\":\"" << EnvStr(skel.GetModelFilePath()) << "\",\"joints\":[";
			joints = (int)skel.GetJointCount();
			auto quat = [&](const Quat& q) { out << "[" << q.w << "," << q.v.x << "," << q.v.y << "," << q.v.z << "]"; };
			auto vec = [&](const Vec3& v) { out << "[" << v.x << "," << v.y << "," << v.z << "]"; };
			for (int i = 0; i < joints; ++i)
			{
				const char* n = skel.GetJointNameByID(i);
				const QuatT& a = skel.GetDefaultAbsJointByID((uint32)i);
				const QuatT& r = skel.GetDefaultRelJointByID((uint32)i);
				out << (i ? "," : "") << "{\"n\":\"" << EnvStr(n ? n : "") << "\",\"p\":" << skel.GetJointParentIDByID(i) << ",\"aq\":";
				quat(a.q);
				out << ",\"at\":";
				vec(a.t);
				out << ",\"rq\":";
				quat(r.q);
				out << ",\"rt\":";
				vec(r.t);
				out << "}";
			}
			out << "]";
			pChar->Release();
		}
		out << "}\n";
		std::ofstream file(outPath, std::ios::binary | std::ios::trunc);
		if (!file)
			throw std::runtime_error("cannot write " + outPath);
		file << out.str();
		file.close();
		std::ostringstream reply;
		reply << "{\"ok\": true, \"path\": \"" << JsonEscape(outPath) << "\", \"created\": " << (pChar ? "true" : "false") << ", \"joints\": " << joints << "}";
		return reply.str();
	}

	// bridge.character.samplepose <output file> | <character path> | <animation name> | <normalized times, comma separated> [| <job delta time, default 1/30> [| reload]]
	// The pose the ENGINE computes for one clip: the clip is started on layer 0 of a fresh instance with CA_MANUAL_UPDATE, stepped to each normalized time (SetLayerNormalizedTime, no real time passes), the animation
	// job is run and finished (StartAnimationProcessing / FinishAnimationComputations) and every joint's ABSOLUTE transform is read (GetAbsJointByID: character space, metres, Z up). Used to prove that motion converted
	// by RC from an FBX lands where the source says it should, with no game run (2026-10-15, MotionBricks).
	// The file holds {"anim", "found", "loaded", "duration", "joints": [names], "samples": [{"t": n, "queued": n, "read_back": n, "pose": [[w,x,y,z,tx,ty,tz, w,x,y,z,tx,ty,tz] per joint: absolute, then parent-relative]}]}.
// Found the hard way (2026-10-15): CSkeletonAnim::ProcessAnimations skips a second update of the same character in one engine frame ('several pre-updates') unless m_CharEditMode is set, so without
// SetCharEditMode every sample was the first pose; a .caf written after the skeleton loaded is only in its animation set after ReloadCHRPARAMS (the last argument).
	if (verb == "bridge.character.samplepose")
	{
		std::string rest = cmd.substr(cmd.find(verb) + verb.size());
		std::vector<std::string> parts;
		{
			size_t pos = 0;
			while (true)
			{
				const size_t bar = rest.find('|', pos);
				std::string piece = rest.substr(pos, bar == std::string::npos ? std::string::npos : bar - pos);
				const size_t b = piece.find_first_not_of(" \t\r\n");
				const size_t e = piece.find_last_not_of(" \t\r\n");
				parts.push_back(b == std::string::npos ? std::string() : piece.substr(b, e - b + 1));
				if (bar == std::string::npos)
					break;
				pos = bar + 1;
			}
		}
		if (parts.size() < 4)
			throw std::runtime_error("bridge.character.samplepose requires <output file> | <character path> | <animation name> | <normalized times>");
		const std::string outPath = parts[0], charPath = parts[1], animName = parts[2];
		const float stepDt = parts.size() > 4 && !parts[4].empty() ? (float)atof(parts[4].c_str()) : 1.0f / 30.0f;   // the delta time every job runs with: a job given 0 s may be treated as 'no time passed, nothing to compute'
		std::vector<float> times;
		{
			std::stringstream ss(parts[3]);
			std::string tok;
			while (std::getline(ss, tok, ','))
				times.push_back((float)atof(tok.c_str()));
		}
		if (!gEnv || !gEnv->pCharacterManager)
			throw std::runtime_error("the character manager is not available");
		ICharacterInstance* pChar = gEnv->pCharacterManager->CreateInstance(charPath.c_str());
		if (!pChar)
			throw std::runtime_error("the character could not be created: " + charPath);
		pChar->AddRef();
		std::ostringstream out;
		out << std::setprecision(9);
		out << "{\"anim\":\"" << EnvStr(animName.c_str()) << "\"";
		if (parts.size() > 5 && parts[5] == "reload")
			pChar->ReloadCHRPARAMS();                                  // re-scan the animation folders (the character tool's own button): a .caf written since the skeleton was first loaded is not in its animation set until then
		IAnimationSet* pSet = pChar->GetIAnimationSet();
		const int animId = pSet ? pSet->GetAnimIDByName(animName.c_str()) : -1;
		out << ",\"found\":" << (animId >= 0 ? "true" : "false");
		bool loaded = false;
		if (animId >= 0)
		{
			ISkeletonAnim* pAnim = pChar->GetISkeletonAnim();
			ISkeletonPose* pPose = pChar->GetISkeletonPose();
			pAnim->SetAnimationDrivenMotion(0);                       // keep the clip's own travel in the pose: it is compared with the source's
			pChar->SetCharEditMode(CA_CharacterAuxEditor);                // CSkeletonAnim::ProcessAnimations skips every second update of a character in the same engine frame ('several pre-updates') unless it is in an edit mode: without this only the first job ran and every sample was the first pose (found 2026-10-15)
			pChar->SetFlags(pChar->GetFlags() | CS_FLAG_UPDATE_ALWAYS);   // CryAnimation only advances a character that is drawn or flagged so (CCharInstance::PerFrameUpdate); this one is neither
			CryCharAnimationParams ap;
			ap.m_nLayerID = 0;
			ap.m_nFlags = CA_MANUAL_UPDATE | CA_FORCE_SKELETON_UPDATE;
			ap.m_fTransTime = 0.0f;
			pAnim->StartAnimation(animName.c_str(), ap);
			for (int i = 0; i < 300 && !pSet->IsAnimLoaded(animId); ++i)   // the clip streams in from its pak: pump the stream engine
			{
				if (gEnv->pSystem->GetStreamEngine())
					gEnv->pSystem->GetStreamEngine()->UpdateAndWait(false);
				CrySleep(10);
			}
			loaded = pSet->IsAnimLoaded(animId);
			out << ",\"loaded\":" << (loaded ? "true" : "false") << ",\"duration\":" << pSet->GetDuration_sec(animId);
			const IDefaultSkeleton& skel = pChar->GetIDefaultSkeleton();
			const int joints = (int)skel.GetJointCount();
			out << ",\"joints\":[";
			for (int i = 0; i < joints; ++i)
				out << (i ? "," : "") << "\"" << EnvStr(skel.GetJointNameByID(i)) << "\"";
			out << "],\"samples\":[";
			{
				// let the started clip take hold once (the first job installs it in the layer's queue); restarting it for every sample would put it back at time 0
				SAnimationProcessParams first;
				first.overrideDeltaTime = stepDt;
				pChar->StartAnimationProcessing(first);
				pChar->FinishAnimationComputations();
			}
			for (size_t s = 0; s < times.size() && loaded; ++s)
			{
				SAnimationProcessParams pp;
				pp.overrideDeltaTime = stepDt;
				const int queued = pAnim->GetNumAnimsInFIFO(0);
				float readBack = -1.0f;
				for (int pass = 0; pass < 2; ++pass)                       // the first job applies the new time, the second computes the pose at it
				{
					if (queued >= 1)
						pAnim->SetAnimationNormalizedTime(&pAnim->GetAnimFromFIFO(0, 0), times[s]);   // by the queued entry itself: SetLayerNormalizedTime does nothing unless the layer holds exactly one
					pChar->StartAnimationProcessing(pp);
					pChar->FinishAnimationComputations();
				}
				if (queued >= 1)
					readBack = pAnim->GetAnimationNormalizedTime(&pAnim->GetAnimFromFIFO(0, 0));
				out << (s ? "," : "") << "{\"t\":" << times[s] << ",\"queued\":" << queued << ",\"read_back\":" << readBack << ",\"pose\":[";
				for (int i = 0; i < joints; ++i)
				{
					const QuatT& a = pPose->GetAbsJointByID(i);
					out << (i ? "," : "") << "[" << a.q.w << "," << a.q.v.x << "," << a.q.v.y << "," << a.q.v.z << "," << a.t.x << "," << a.t.y << "," << a.t.z << "," << pPose->GetRelJointByID(i).q.w << "," << pPose->GetRelJointByID(i).q.v.x << "," << pPose->GetRelJointByID(i).q.v.y << "," << pPose->GetRelJointByID(i).q.v.z << "," << pPose->GetRelJointByID(i).t.x << "," << pPose->GetRelJointByID(i).t.y << "," << pPose->GetRelJointByID(i).t.z << "]";   // 14 numbers per joint: absolute (w x y z, tx ty tz) then parent-relative
				}
				out << "]}";
			}
			out << "]";
		}
		out << "}\n";
		pChar->Release();
		std::ofstream file(outPath, std::ios::binary | std::ios::trunc);
		if (!file)
			throw std::runtime_error("cannot write " + outPath);
		file << out.str();
		file.close();
		std::ostringstream reply;
		reply << "{\"ok\": true, \"path\": \"" << JsonEscape(outPath) << "\", \"found\": " << (animId >= 0 ? "true" : "false") << ", \"loaded\": " << (loaded ? "true" : "false") << ", \"samples\": " << times.size() << "}";
		return reply.str();
	}

	// bridge.project.info <output file>
	// The project as the engine's project manager parsed it (Cry::IProjectManager): name, GUID, engine id, directories, the .cryproject path and every plug-in entry (type, path, platforms). Compared with the
	// .cryproject file by the Python side, which finds a plug-in the engine dropped or read differently.
	if (verb == "bridge.project.info")
	{
		std::string path = cmd.substr(cmd.find(verb) + verb.size());
		const size_t pb = path.find_first_not_of(" \t");
		const size_t pe = path.find_last_not_of(" \t\r\n");
		path = pb == std::string::npos ? std::string() : path.substr(pb, pe - pb + 1);
		if (path.empty())
			throw std::runtime_error("bridge.project.info requires <output file>");
		if (!gEnv || !gEnv->pSystem || !gEnv->pSystem->GetIProjectManager())
			throw std::runtime_error("the project manager is not available");
		Cry::IProjectManager* pPM = gEnv->pSystem->GetIProjectManager();
		std::ostringstream out;
		out << "{\"name\":\"" << EnvStr(pPM->GetCurrentProjectName()) << "\",\"guid\":\"" << EnvStr(pPM->GetCurrentProjectGUID().ToDebugString()) << "\",\"engine\":\"" << EnvStr(pPM->GetCurrentEngineID())
		    << "\",\"project_dir\":\"" << EnvStr(pPM->GetCurrentProjectDirectoryAbsolute()) << "\",\"asset_dir\":\"" << EnvStr(pPM->GetCurrentAssetDirectoryRelative()) << "\",\"asset_dir_abs\":\""
		    << EnvStr(pPM->GetCurrentAssetDirectoryAbsolute()) << "\",\"file\":\"" << EnvStr(pPM->GetProjectFilePath()) << "\",\"plugins\":[";
		const uint16 n = pPM->GetPluginCount();
		for (uint16 i = 0; i < n; ++i)
		{
			Cry::IPluginManager::EType type = Cry::IPluginManager::EType::Native;
			string pluginPath;
			DynArray<EPlatform> platforms;
			pPM->GetPluginInfo(i, type, pluginPath, platforms);
			out << (i ? "," : "") << "{\"type\":" << (int)type << ",\"path\":\"" << EnvStr(pluginPath.c_str()) << "\",\"platforms\":[";
			for (size_t p = 0; p < platforms.size(); ++p)
				out << (p ? "," : "") << (int)platforms[p];
			out << "]}";
		}
		out << "]}\n";
		std::ofstream file(path, std::ios::binary | std::ios::trunc);
		if (!file)
			throw std::runtime_error("cannot write " + path);
		file << out.str();
		file.close();
		std::ostringstream reply;
		reply << "{\"ok\": true, \"path\": \"" << JsonEscape(path) << "\", \"plugins\": " << n << "}";
		return reply.str();
	}

	// bridge.flow.catalog <output file>
	// Every node type the Flow System has registered (IFlowSystem::CreateNodeTypeIterator), each created in a scratch graph the way the editor's node list creates it, then removed, and its configuration read:
	// category and usage flags, description, every input port (name, human name, description, UI config, the default value's data type, whether the type is locked, the default as text) and every output port.
	// The file holds [{"type", "id", "created", "flags", "description", "inputs": [...], "outputs": [...]}]. Nothing is added to the level.
	if (verb == "bridge.flow.catalog")
	{
		std::string path = cmd.substr(cmd.find(verb) + verb.size());
		const size_t pb = path.find_first_not_of(" \t");
		const size_t pe = path.find_last_not_of(" \t\r\n");
		path = pb == std::string::npos ? std::string() : path.substr(pb, pe - pb + 1);
		if (path.empty())
			throw std::runtime_error("bridge.flow.catalog requires <output file>");
		if (!gEnv || !gEnv->pFlowSystem)
			throw std::runtime_error("the flow system is not available");
		IFlowSystem* pFS = gEnv->pFlowSystem;
		IFlowGraphPtr pGraph = pFS->CreateFlowGraph();
		if (!pGraph)
			throw std::runtime_error("CreateFlowGraph failed");
		pGraph->SetDebugName("claude_flow_catalog");
		IFlowNodeTypeIteratorPtr it = pFS->CreateNodeTypeIterator();
		IFlowNodeTypeIterator::SNodeType nt;
		std::ostringstream out;
		out << "[\n";
		int count = 0, created = 0;
		while (it && it->Next(nt))
		{
			std::ostringstream one;
			char probe[48];
			cry_sprintf(probe, "claude_probe_%d", count);
			const TFlowNodeId node = pGraph->CreateNode(nt.typeId, probe);
			one << "{\"type\":\"" << EnvStr(nt.typeName) << "\",\"id\":" << (unsigned)nt.typeId << ",\"created\":" << (node != InvalidFlowNodeId ? "true" : "false");
			if (node != InvalidFlowNodeId)
			{
				++created;
				SFlowNodeConfig cfg;
				pGraph->GetNodeConfiguration(node, cfg);
				one << ",\"flags\":" << cfg.nFlags << ",\"description\":\"" << EnvStr(cfg.sDescription ? cfg.sDescription : "") << "\",\"inputs\":[";
				for (int i = 0; cfg.pInputPorts && cfg.pInputPorts[i].name; ++i)
				{
					const SInputPortConfig& p = cfg.pInputPorts[i];
					string def;
					p.defaultData.GetValueWithConversion(def);
					one << (i ? "," : "") << "{\"name\":\"" << EnvStr(p.name) << "\",\"human\":\"" << EnvStr(p.humanName ? p.humanName : "") << "\",\"description\":\"" << EnvStr(p.description ? p.description : "")
					    << "\",\"ui\":\"" << EnvStr(p.sUIConfig ? p.sUIConfig : "") << "\",\"type\":" << (int)p.defaultData.GetType() << ",\"locked\":" << (p.defaultData.IsLocked() ? "true" : "false")
					    << ",\"default\":\"" << EnvStr(def.c_str()) << "\"}";
				}
				one << "],\"outputs\":[";
				for (int i = 0; cfg.pOutputPorts && cfg.pOutputPorts[i].name; ++i)
				{
					const SOutputPortConfig& p = cfg.pOutputPorts[i];
					one << (i ? "," : "") << "{\"name\":\"" << EnvStr(p.name) << "\",\"human\":\"" << EnvStr(p.humanName ? p.humanName : "") << "\",\"description\":\"" << EnvStr(p.description ? p.description : "")
					    << "\",\"type\":" << p.type << "}";
				}
				one << "]";
				pGraph->RemoveNode(node);
			}
			one << "}";
			out << (count ? ",\n" : "") << one.str();
			++count;
		}
		out << "\n]\n";
		std::ofstream file(path, std::ios::binary | std::ios::trunc);
		if (!file)
			throw std::runtime_error("cannot write " + path);
		file << out.str();
		file.close();
		std::ostringstream reply;
		reply << "{\"ok\": true, \"path\": \"" << JsonEscape(path) << "\", \"types\": " << count << ", \"created\": " << created << "}";
		return reply.str();
	}

	// bridge.flow.check <output file> | <graph XML, a CryPak path such as libs/flowgraphs/claude_farm.xml>
	// A flow graph file is loaded the way claude_flowgraph and the level loader load it (IFlowGraph::SerializeXML, reading) into a scratch graph that is NOT enabled and NOT initialised (no node runs), and what the
	// engine kept is written out: whether the XML was read, whether the graph was accepted (a bad node type or edge makes the engine DISCARD the whole graph and log "FLOWGRAPH DISCARDED"), every node (id, type,
	// each input port with the value it holds) and every edge by node and port NAME, plus the log lines the load wrote (verbosity raised to 4 for the load and put back).
	// The file holds {"xml_loaded", "ok", "nodes": [{"id", "name", "type", "inputs": [{"name", "value"}], "outputs": [names]}], "edges": [{"from", "out", "to", "in"}], "log": [...]}.
	if (verb == "bridge.flow.check")
	{
		std::string rest = cmd.substr(cmd.find(verb) + verb.size());
		const size_t bar = rest.find('|');
		if (bar == std::string::npos)
			throw std::runtime_error("bridge.flow.check requires <output file> | <graph xml>");
		auto trim = [](std::string s)
		{
			const size_t b = s.find_first_not_of(" \t\r\n");
			const size_t e = s.find_last_not_of(" \t\r\n");
			return b == std::string::npos ? std::string() : s.substr(b, e - b + 1);
		};
		const std::string outPath = trim(rest.substr(0, bar)), graphPath = trim(rest.substr(bar + 1));
		if (!gEnv || !gEnv->pFlowSystem || !gEnv->pSystem)
			throw std::runtime_error("the flow system is not available");
		struct LogGrab : public ILogCallback
		{
			std::vector<std::string> lines;
			ICVar* v1 = nullptr;
			ICVar* v2 = nullptr;
			int o1 = 0, o2 = 0;
			bool added = false;
			void OnWriteToConsole(const char* s, bool) override { if (s) lines.push_back(s); }
			void OnWriteToFile(const char*, bool) override {}
			void Begin()
			{
				v1 = gEnv->pConsole->GetCVar("log_Verbosity");
				v2 = gEnv->pConsole->GetCVar("log_WriteToFileVerbosity");
				o1 = v1 ? v1->GetIVal() : 0;
				o2 = v2 ? v2->GetIVal() : 0;
				if (v1) v1->Set(4);
				if (v2) v2->Set(4);
				gEnv->pLog->AddCallback(this);
				added = true;
			}
			~LogGrab()
			{
				if (added) gEnv->pLog->RemoveCallback(this);
				if (v1) v1->Set(o1);
				if (v2) v2->Set(o2);
			}
		};
		LogGrab grab;
		grab.Begin();
		XmlNodeRef root = gEnv->pSystem->LoadXmlFromFile(graphPath.c_str());
		IFlowGraphPtr pGraph = gEnv->pFlowSystem->CreateFlowGraph();
		if (!pGraph)
			throw std::runtime_error("CreateFlowGraph failed");
		pGraph->SetDebugName("claude_flow_check");
		const bool ok = root ? pGraph->SerializeXML(root, true) : false;
		std::ostringstream out;
		out << "{\"xml_loaded\":" << (root ? "true" : "false") << ",\"ok\":" << (ok ? "true" : "false") << ",\"nodes\":[";
		int nodes = 0, edges = 0;
		if (ok)
		{
			IFlowNodeIteratorPtr nit = pGraph->CreateNodeIterator();
			TFlowNodeId id;
			while (nit && nit->Next(id))
			{
				SFlowNodeConfig cfg;
				pGraph->GetNodeConfiguration(id, cfg);
				out << (nodes ? "," : "") << "{\"id\":" << (unsigned)id << ",\"name\":\"" << EnvStr(pGraph->GetNodeName(id)) << "\",\"type\":\"" << EnvStr(pGraph->GetNodeTypeName(id)) << "\",\"inputs\":[";
				for (int i = 0; cfg.pInputPorts && cfg.pInputPorts[i].name; ++i)
				{
					const TFlowInputData* pv = pGraph->GetInputValue(id, (TFlowPortId)i);
					string text;
					if (pv)
						pv->GetValueWithConversion(text);
					out << (i ? "," : "") << "{\"name\":\"" << EnvStr(cfg.pInputPorts[i].name) << "\",\"value\":\"" << EnvStr(text.c_str()) << "\"}";
				}
				out << "],\"outputs\":[";
				for (int i = 0; cfg.pOutputPorts && cfg.pOutputPorts[i].name; ++i)
					out << (i ? "," : "") << "\"" << EnvStr(cfg.pOutputPorts[i].name) << "\"";
				out << "]}";
				++nodes;
			}
		}
		out << "],\"edges\":[";
		if (ok)
		{
			IFlowEdgeIteratorPtr eit = pGraph->CreateEdgeIterator();
			IFlowEdgeIterator::Edge e;
			while (eit && eit->Next(e))
			{
				SFlowNodeConfig from, to;
				pGraph->GetNodeConfiguration(e.fromNodeId, from);
				pGraph->GetNodeConfiguration(e.toNodeId, to);
				const char* outName = (from.pOutputPorts && e.fromPortId != InvalidFlowPortId) ? from.pOutputPorts[e.fromPortId].name : "";
				const char* inName = (to.pInputPorts && e.toPortId != InvalidFlowPortId) ? to.pInputPorts[e.toPortId].name : "";
				out << (edges ? "," : "") << "{\"from\":\"" << EnvStr(pGraph->GetNodeName(e.fromNodeId)) << "\",\"out\":\"" << EnvStr(outName ? outName : "") << "\",\"to\":\"" << EnvStr(pGraph->GetNodeName(e.toNodeId))
				    << "\",\"in\":\"" << EnvStr(inName ? inName : "") << "\"}";
				++edges;
			}
		}
		out << "],\"log\":[";
		for (size_t i = 0; i < grab.lines.size(); ++i)
			out << (i ? "," : "") << "\"" << EnvStr(grab.lines[i].c_str()) << "\"";
		out << "]}\n";
		std::ofstream file(outPath, std::ios::binary | std::ios::trunc);
		if (!file)
			throw std::runtime_error("cannot write " + outPath);
		file << out.str();
		file.close();
		std::ostringstream reply;
		reply << "{\"ok\": true, \"path\": \"" << JsonEscape(outPath) << "\", \"loaded\": " << (ok ? "true" : "false") << ", \"nodes\": " << nodes << ", \"edges\": " << edges << "}";
		return reply.str();
	}

	// bridge.uqs.catalog <output file>
	// Everything the Universal Query System has registered with its hub (UQS::Core::IHubPlugin::GetHub): item types, functions, generators, instant and deferred evaluators, score transforms and query
	// factories, each with its name, description and, for functions, generators and evaluators, the input parameters (name, type, description) the engine's own input parameter registry lists. The file holds
	// {"items": [...], "functions": [...], "generators": [...], "instant_evaluators": [...], "deferred_evaluators": [...], "score_transforms": [...], "query_factories": [...], "queries": [...]}; "queries" are the
	// blueprints the library holds (loaded from libs/ai/uqs at startup).
	if (verb == "bridge.uqs.catalog")
	{
		std::string path = cmd.substr(cmd.find(verb) + verb.size());
		const size_t pb = path.find_first_not_of(" \t");
		const size_t pe = path.find_last_not_of(" \t\r\n");
		path = (pb == std::string::npos) ? std::string() : path.substr(pb, pe - pb + 1);
		if (path.empty())
			throw std::runtime_error("bridge.uqs.catalog requires an output file path");
		UQS::Core::IHub* pHub = UQS::Core::IHubPlugin::GetHubPtr();
		if (!pHub)
			throw std::runtime_error("the UQS hub is not installed (the CryUQS_Core plugin is not loaded)");
		std::ostringstream out;
		auto inputs = [](const UQS::Client::IInputParameterRegistry& reg)
		{
			std::ostringstream s;
			s << "[";
			for (size_t i = 0; i < reg.GetParameterCount(); ++i)
			{
				const UQS::Client::IInputParameterRegistry::SParameterInfo info = reg.GetParameter(i);
				char idText[5];
				info.id.ToString(idText);
				s << (i ? "," : "") << "{\"name\":\"" << EnvStr(info.szName) << "\",\"id\":\"" << EnvStr(idText) << "\",\"type\":\"" << EnvStr(info.type.name()) << "\",\"description\":\"" << EnvStr(info.szDescription) << "\"}";
			}
			s << "]";
			return s.str();
		};
		auto head = [](const char* name, const CryGUID& guid, const char* desc)
		{
			std::ostringstream s;
			s << "\"name\":\"" << EnvStr(name) << "\",\"guid\":\"" << EnvStr(guid.ToString().c_str()) << "\",\"description\":\"" << EnvStr(desc ? desc : "") << "\"";
			return s.str();
		};
		out << "{\"items\":[";
		{
			UQS::Core::IItemFactoryDatabase& db = pHub->GetItemFactoryDatabase();
			for (size_t i = 0; i < db.GetFactoryCount(); ++i)
			{
				UQS::Client::IItemFactory& f = db.GetFactory(i);
				out << (i ? "," : "") << "{" << head(f.GetName(), f.GetGUID(), f.GetDescription()) << ",\"type\":\"" << EnvStr(f.GetItemType().name()) << "\",\"size\":" << f.GetItemSize()
				    << ",\"container_for_shuttled_items\":" << (f.IsContainerForShuttledItems() ? "true" : "false") << ",\"debug_renderable\":" << (f.CanBeRepresentedInDebugRenderWorld() ? "true" : "false")
				    << ",\"serializable\":" << (f.CanBePersistantlySerialized() ? "true" : "false") << "}";
			}
		}
		out << "],\"functions\":[";
		{
			UQS::Core::IFunctionFactoryDatabase& db = pHub->GetFunctionFactoryDatabase();
			for (size_t i = 0; i < db.GetFactoryCount(); ++i)
			{
				UQS::Client::IFunctionFactory& f = db.GetFactory(i);
				const UQS::Shared::CTypeInfo* pContained = f.GetContainedType();
				out << (i ? "," : "") << "{" << head(f.GetName(), f.GetGUID(), f.GetDescription()) << ",\"returns\":\"" << EnvStr(f.GetReturnType().name()) << "\",\"contained\":\"" << EnvStr(pContained ? pContained->name() : "")
				    << "\",\"leaf_kind\":" << static_cast<int>(f.GetLeafFunctionKind()) << ",\"inputs\":" << inputs(f.GetInputParameterRegistry()) << "}";
			}
		}
		out << "],\"generators\":[";
		{
			UQS::Core::IGeneratorFactoryDatabase& db = pHub->GetGeneratorFactoryDatabase();
			for (size_t i = 0; i < db.GetFactoryCount(); ++i)
			{
				UQS::Client::IGeneratorFactory& f = db.GetFactory(i);
				const UQS::Shared::CTypeInfo* pShuttled = f.GetTypeOfShuttledItemsToExpect();
				out << (i ? "," : "") << "{" << head(f.GetName(), f.GetGUID(), f.GetDescription()) << ",\"generates\":\"" << EnvStr(f.GetTypeOfItemsToGenerate().name()) << "\",\"expects_shuttled\":\"" << EnvStr(pShuttled ? pShuttled->name() : "")
				    << "\",\"inputs\":" << inputs(f.GetInputParameterRegistry()) << "}";
			}
		}
		out << "],\"instant_evaluators\":[";
		{
			UQS::Core::IInstantEvaluatorFactoryDatabase& db = pHub->GetInstantEvaluatorFactoryDatabase();
			for (size_t i = 0; i < db.GetFactoryCount(); ++i)
			{
				UQS::Client::IInstantEvaluatorFactory& f = db.GetFactory(i);
				out << (i ? "," : "") << "{" << head(f.GetName(), f.GetGUID(), f.GetDescription()) << ",\"cost\":\"" << (f.GetCostCategory() == UQS::Client::IInstantEvaluatorFactory::ECostCategory::Cheap ? "cheap" : "expensive")
				    << "\",\"modality\":\"" << (f.GetEvaluationModality() == UQS::Client::IInstantEvaluatorFactory::EEvaluationModality::Testing ? "testing" : "scoring") << "\",\"inputs\":" << inputs(f.GetInputParameterRegistry()) << "}";
			}
		}
		out << "],\"deferred_evaluators\":[";
		{
			UQS::Core::IDeferredEvaluatorFactoryDatabase& db = pHub->GetDeferredEvaluatorFactoryDatabase();
			for (size_t i = 0; i < db.GetFactoryCount(); ++i)
			{
				UQS::Client::IDeferredEvaluatorFactory& f = db.GetFactory(i);
				out << (i ? "," : "") << "{" << head(f.GetName(), f.GetGUID(), f.GetDescription()) << ",\"inputs\":" << inputs(f.GetInputParameterRegistry()) << "}";
			}
		}
		out << "],\"score_transforms\":[";
		{
			UQS::Core::IScoreTransformFactoryDatabase& db = pHub->GetScoreTransformFactoryDatabase();
			for (size_t i = 0; i < db.GetFactoryCount(); ++i)
			{
				UQS::Core::IScoreTransformFactory& f = db.GetFactory(i);
				out << (i ? "," : "") << "{" << head(f.GetName(), f.GetGUID(), f.GetDescription()) << ",\"type\":" << static_cast<int>(f.GetScoreTransformType()) << "}";
			}
		}
		out << "],\"query_factories\":[";
		{
			UQS::Core::IQueryFactoryDatabase& db = pHub->GetQueryFactoryDatabase();
			for (size_t i = 0; i < db.GetFactoryCount(); ++i)
			{
				UQS::Core::IQueryFactory& f = db.GetFactory(i);
				out << (i ? "," : "") << "{" << head(f.GetName(), f.GetGUID(), f.GetDescription()) << ",\"supports_parameters\":" << (f.SupportsParameters() ? "true" : "false") << ",\"requires_generator\":" << (f.RequiresGenerator() ? "true" : "false")
				    << ",\"supports_evaluators\":" << (f.SupportsEvaluators() ? "true" : "false") << ",\"min_children\":" << f.GetMinRequiredChildren() << ",\"max_children\":" << f.GetMaxAllowedChildren() << "}";
			}
		}
		out << "],\"queries\":[";
		{
			UQS::Core::IQueryBlueprintLibrary& lib = pHub->GetQueryBlueprintLibrary();
			for (size_t i = 0; i < lib.GetQueryBlueprintCount(); ++i)
			{
				const UQS::Core::IQueryBlueprint* pBp = lib.GetQueryBlueprintByID(lib.GetQueryBlueprintID(i));
				out << (i ? "," : "") << "{\"name\":\"" << EnvStr(pBp ? pBp->GetName() : "") << "\",\"output_type\":\"" << EnvStr(pBp ? pBp->GetOutputType().name() : "") << "\"}";
			}
		}
		out << "]}\n";
		std::ofstream file(path, std::ios::binary | std::ios::trunc);
		if (!file)
			throw std::runtime_error("cannot write " + path);
		file << out.str();
		file.close();
		std::ostringstream reply;
		reply << "{\"ok\": true, \"path\": \"" << JsonEscape(path) << "\", \"bytes\": " << out.str().size() << "}";
		return reply.str();
	}

	// bridge.uqs.check <output json> | <query name> [| register]
	// (register: the query is a zz_mcp_* copy written after startup; its record is created in memory for the check and removed again, which deletes the copy.)
	// A query file through the UQS hub's own EDITOR SERVICE (the one the Query Editor uses): LoadTextualQueryBlueprint reads libs/ai/uqs/<name>.uqs with the XML data source, and
	// ValidateTextualQueryBlueprint resolves it against the registered generators, evaluators, functions, item types and score transforms, reporting every problem through the syntax error collectors
	// attached to the element it concerns (nothing is stored in the library, nothing runs). The loaded blueprint exists only inside the load callback, so it is copied into a plain structure there and
	// handed back, element by element, in the validate callback. The file holds {"loaded": bool, "load_error", "valid": bool, "errors": ["Generator/Input[size]: ..."], "query": {...}}.
	if (verb == "bridge.uqs.check")
	{
		std::string rest = cmd.substr(cmd.find(verb) + verb.size());
		const size_t bar = rest.find('|');
		if (bar == std::string::npos)
			throw std::runtime_error("bridge.uqs.check requires <output json> | <query name>");
		auto trim = [](std::string s)
		{
			const size_t b = s.find_first_not_of(" \t\r\n");
			const size_t e = s.find_last_not_of(" \t\r\n");
			return b == std::string::npos ? std::string() : s.substr(b, e - b + 1);
		};
		const std::string outPath = trim(rest.substr(0, bar));
		std::string queryName = trim(rest.substr(bar + 1));
		bool registerRecord = false;
		const size_t bar2 = queryName.find('|');
		if (bar2 != std::string::npos)
		{
			registerRecord = trim(queryName.substr(bar2 + 1)) == "register";
			queryName = trim(queryName.substr(0, bar2));
		}
		UQS::Core::IHub* pHub = UQS::Core::IHubPlugin::GetHubPtr();
		if (!pHub)
			throw std::runtime_error("the UQS hub is not installed (the CryUQS_Core plugin is not loaded)");
		if (registerRecord && queryName.compare(0, 7, "zz_mcp_") != 0)
			throw std::runtime_error("only a query named zz_mcp_* may be registered for a check (the engine deletes a removed record's file)");

		struct StrOut : public UQS::Shared::IUqsString
		{
			std::string text;
			void Set(const char* s) override { text = s ? s : ""; }
			void Format(const char* fmt, ...) override
			{
				char buf[2048];
				va_list args;
				va_start(args, fmt);
				vsnprintf(buf, sizeof(buf), fmt, args);
				va_end(args);
				text = buf;
			}
			const char* c_str() const override { return text.c_str(); }
		};
		struct Collector : public UQS::DataSource::ISyntaxErrorCollector
		{
			std::vector<std::string>* sink;
			std::string label;
			void AddErrorMessage(const char* fmt, ...) override
			{
				char buf[2048];
				va_list args;
				va_start(args, fmt);
				vsnprintf(buf, sizeof(buf), fmt, args);
				va_end(args);
				sink->push_back(label + ": " + buf);
			}
		};
		struct Plain
		{
			struct In { std::string param, func, literal; UQS::Client::CInputParameterID id = UQS::Client::CInputParameterID::CreateEmpty(); CryGUID funcGuid; bool dbg = false; std::vector<In> kids; };
			struct Ev { std::string name, scoreTransform; CryGUID guid, stGuid; float weight = 1.0f; bool negate = false; In root; };
			struct CP { std::string name, type, value; CryGUID typeGuid; bool dbg = false; };
			struct Q
			{
				std::string name, factory;
				CryGUID factoryGuid;
				size_t maxItems = 0;
				std::vector<CP> constants, runtimes;
				bool hasGen = false;
				std::string genName;
				CryGUID genGuid;
				In genRoot;
				std::vector<Ev> instant, deferred;
				std::vector<Q> kids;
			};
			static void ReadIn(const UQS::Core::ITextualInputBlueprint& s, In& d)
			{
				d.param = s.GetParamName();
				d.id = s.GetParamID();
				d.func = s.GetFuncName();
				d.funcGuid = s.GetFuncGUID();
				d.literal = s.GetFuncReturnValueLiteral();
				d.dbg = s.GetAddReturnValueToDebugRenderWorldUponExecution();
				for (size_t i = 0; i < s.GetChildCount(); ++i)
				{
					d.kids.emplace_back();
					ReadIn(s.GetChild(i), d.kids.back());
				}
			}
			static void ReadEv(const UQS::Core::ITextualEvaluatorBlueprint& s, Ev& d)
			{
				d.name = s.GetEvaluatorName();
				d.guid = s.GetEvaluatorGUID();
				d.weight = s.GetWeight();
				d.scoreTransform = s.GetScoreTransformName();
				d.stGuid = s.GetScoreTransformGUID();
				d.negate = s.GetNegateDiscard();
				ReadIn(s.GetInputRoot(), d.root);
			}
			static void ReadQ(const UQS::Core::ITextualQueryBlueprint& s, Q& d)
			{
				d.name = s.GetName();
				d.factory = s.GetQueryFactoryName();
				d.factoryGuid = s.GetQueryFactoryGUID();
				d.maxItems = s.GetMaxItemsToKeepInResultSet();
				for (size_t i = 0; i < s.GetGlobalConstantParams().GetParameterCount(); ++i)
				{
					const auto p = s.GetGlobalConstantParams().GetParameter(i);
					d.constants.push_back(CP{ p.szName ? p.szName : "", p.szTypeName ? p.szTypeName : "", p.szValue ? p.szValue : "", p.typeGUID, p.bAddToDebugRenderWorld });
				}
				for (size_t i = 0; i < s.GetGlobalRuntimeParams().GetParameterCount(); ++i)
				{
					const auto p = s.GetGlobalRuntimeParams().GetParameter(i);
					d.runtimes.push_back(CP{ p.szName ? p.szName : "", p.szTypeName ? p.szTypeName : "", "", p.typeGUID, p.bAddToDebugRenderWorld });
				}
				if (const UQS::Core::ITextualGeneratorBlueprint* g = s.GetGenerator())
				{
					d.hasGen = true;
					d.genName = g->GetGeneratorName();
					d.genGuid = g->GetGeneratorGUID();
					ReadIn(g->GetInputRoot(), d.genRoot);
				}
				for (size_t i = 0; i < s.GetInstantEvaluatorCount(); ++i)
				{
					d.instant.emplace_back();
					ReadEv(s.GetInstantEvaluator(i), d.instant.back());
				}
				for (size_t i = 0; i < s.GetDeferredEvaluatorCount(); ++i)
				{
					d.deferred.emplace_back();
					ReadEv(s.GetDeferredEvaluator(i), d.deferred.back());
				}
				for (size_t i = 0; i < s.GetChildCount(); ++i)
				{
					d.kids.emplace_back();
					ReadQ(s.GetChild(i), d.kids.back());
				}
			}
		};
		struct Fill
		{
			std::vector<std::string>* sink;
			static void DeleteCollector(UQS::DataSource::ISyntaxErrorCollector* p) { delete static_cast<Collector*>(p); }
			UQS::DataSource::SyntaxErrorCollectorUniquePtr Make(const std::string& label) const
			{
				Collector* c = new Collector();
				c->sink = sink;
				c->label = label;
				return UQS::DataSource::SyntaxErrorCollectorUniquePtr(c, UQS::DataSource::CSyntaxErrorCollectorDeleter(&DeleteCollector));
			}
			void FillIn(UQS::Core::ITextualInputBlueprint& d, const Plain::In& s, const std::string& label) const
			{
				d.SetParamName(s.param.c_str());
				d.SetParamID(s.id);
				d.SetFuncName(s.func.c_str());
				d.SetFuncGUID(s.funcGuid);
				d.SetFuncReturnValueLiteral(s.literal.c_str());
				d.SetAddReturnValueToDebugRenderWorldUponExecution(s.dbg);
				d.SetSyntaxErrorCollector(Make(label));
				for (const Plain::In& k : s.kids)
				{
					UQS::Core::ITextualInputBlueprint& c = d.AddChild(k.param.c_str(), k.id, k.func.c_str(), k.funcGuid, k.literal.c_str(), k.dbg);
					FillIn(c, k, label + "/" + k.param);
				}
			}
			void FillEv(UQS::Core::ITextualEvaluatorBlueprint& d, const Plain::Ev& s, const std::string& label) const
			{
				d.SetEvaluatorName(s.name.c_str());
				d.SetEvaluatorGUID(s.guid);
				d.SetWeight(s.weight);
				d.SetScoreTransformName(s.scoreTransform.c_str());
				d.SetScoreTransformGUID(s.stGuid);
				d.SetNegateDiscard(s.negate);
				d.SetSyntaxErrorCollector(Make(label));
				FillIn(d.GetInputRoot(), s.root, label + "/Input");
			}
			void FillQ(UQS::Core::ITextualQueryBlueprint& d, const Plain::Q& s, const std::string& label) const
			{
				d.SetName(s.name.c_str());
				d.SetQueryFactoryName(s.factory.c_str());
				d.SetQueryFactoryGUID(s.factoryGuid);
				d.SetMaxItemsToKeepInResultSet(s.maxItems);
				d.SetSyntaxErrorCollector(Make(label));
				for (const Plain::CP& p : s.constants)
					d.GetGlobalConstantParams().AddParameter(p.name.c_str(), p.type.c_str(), p.typeGuid, p.value.c_str(), p.dbg, Make(label + "/ConstantParam[" + p.name + "]"));
				for (const Plain::CP& p : s.runtimes)
					d.GetGlobalRuntimeParams().AddParameter(p.name.c_str(), p.type.c_str(), p.typeGuid, p.dbg, Make(label + "/RuntimeParam[" + p.name + "]"));
				if (s.hasGen)
				{
					UQS::Core::ITextualGeneratorBlueprint& g = d.SetGenerator();
					g.SetGeneratorName(s.genName.c_str());
					g.SetGeneratorGUID(s.genGuid);
					g.SetSyntaxErrorCollector(Make(label + "/Generator " + s.genName));
					FillIn(g.GetInputRoot(), s.genRoot, label + "/Generator " + s.genName + "/Input");
				}
				int n = 0;
				for (const Plain::Ev& e : s.instant)
					FillEv(d.AddInstantEvaluator(), e, label + "/InstantEvaluator[" + std::to_string(n++) + "] " + e.name);
				n = 0;
				for (const Plain::Ev& e : s.deferred)
					FillEv(d.AddDeferredEvaluator(), e, label + "/DeferredEvaluator[" + std::to_string(n++) + "] " + e.name);
				n = 0;
				for (const Plain::Q& k : s.kids)
					FillQ(d.AddChild(), k, label + "/Child[" + std::to_string(n++) + "]");
			}
		};
		struct Consumer : public UQS::Editor::IEditorServiceConsumer
		{
			Plain::Q query;
			bool got = false;
			std::vector<std::string> errors;
			void OnTextualQueryBlueprintLoaded(const UQS::Core::ITextualQueryBlueprint& loaded) override
			{
				Plain::ReadQ(loaded, query);
				got = true;
			}
			void OnTextualQueryBlueprintGettingSaved(UQS::Core::ITextualQueryBlueprint&) override {}
			void OnTextualQueryBlueprintGettingValidated(UQS::Core::ITextualQueryBlueprint& toFill) override
			{
				Fill f;
				f.sink = &errors;
				f.FillQ(toFill, query, "Query");
			}
		} consumer;

		StrOut loadError;
		bool registered = false;
		if (registerRecord)
		{
			// the XML data source enumerates libs/ai/uqs once at startup; a copy written since is registered as a new record (in memory: the file at the record's path is what the loader then reads)
			StrOut createError;
			registered = pHub->GetEditorService().CreateNewTextualQueryBlueprint(queryName.c_str(), consumer, createError);
			consumer.got = false;
			consumer.query = Plain::Q();
			if (!registered)
				loadError.text = "could not register the record: " + createError.text;
		}
		const bool loaded = (registerRecord && !registered) ? false : (pHub->GetEditorService().LoadTextualQueryBlueprint(queryName.c_str(), consumer, loadError) && consumer.got);
		bool valid = false;
		if (loaded)
			valid = pHub->GetEditorService().ValidateTextualQueryBlueprint(consumer);
		if (registered)
		{
			StrOut removeError;
			pHub->GetEditorService().RemoveQueryBlueprint(queryName.c_str(), removeError);       // also deletes the file the check read (a zz_mcp_ copy)
		}

		auto inJson = [](const Plain::In& in)
		{
			std::function<std::string(const Plain::In&)> rec = [&](const Plain::In& i) -> std::string
			{
				std::ostringstream s;
				char idText[5];
				i.id.ToString(idText);
				s << "{\"param\":\"" << EnvStr(i.param.c_str()) << "\",\"param_id\":\"" << EnvStr(idText) << "\",\"func\":\"" << EnvStr(i.func.c_str()) << "\",\"literal\":\"" << EnvStr(i.literal.c_str()) << "\",\"children\":[";
				for (size_t k = 0; k < i.kids.size(); ++k)
					s << (k ? "," : "") << rec(i.kids[k]);
				s << "]}";
				return s.str();
			};
			return rec(in);
		};
		std::ostringstream out;
		out << "{\"loaded\":" << (loaded ? "true" : "false") << ",\"load_error\":\"" << EnvStr(loadError.text.c_str()) << "\",\"valid\":" << (valid ? "true" : "false") << ",\"errors\":[";
		for (size_t i = 0; i < consumer.errors.size(); ++i)
			out << (i ? "," : "") << "\"" << EnvStr(consumer.errors[i].c_str()) << "\"";
		out << "],\"query\":{";
		if (loaded)
		{
			const Plain::Q& q = consumer.query;
			out << "\"name\":\"" << EnvStr(q.name.c_str()) << "\",\"factory\":\"" << EnvStr(q.factory.c_str()) << "\",\"max_items\":" << q.maxItems << ",\"generator\":\"" << EnvStr(q.hasGen ? q.genName.c_str() : "") << "\",\"generator_input\":" << (q.hasGen ? inJson(q.genRoot) : std::string("null")) << ",\"runtime_params\":[";
			for (size_t i = 0; i < q.runtimes.size(); ++i)
				out << (i ? "," : "") << "{\"name\":\"" << EnvStr(q.runtimes[i].name.c_str()) << "\",\"type\":\"" << EnvStr(q.runtimes[i].type.c_str()) << "\"}";
			out << "],\"constant_params\":[";
			for (size_t i = 0; i < q.constants.size(); ++i)
				out << (i ? "," : "") << "{\"name\":\"" << EnvStr(q.constants[i].name.c_str()) << "\",\"type\":\"" << EnvStr(q.constants[i].type.c_str()) << "\",\"value\":\"" << EnvStr(q.constants[i].value.c_str()) << "\"}";
			out << "],\"instant_evaluators\":[";
			for (size_t i = 0; i < q.instant.size(); ++i)
				out << (i ? "," : "") << "{\"name\":\"" << EnvStr(q.instant[i].name.c_str()) << "\",\"weight\":" << q.instant[i].weight << ",\"score_transform\":\"" << EnvStr(q.instant[i].scoreTransform.c_str()) << "\",\"negate_discard\":" << (q.instant[i].negate ? "true" : "false") << ",\"input\":" << inJson(q.instant[i].root) << "}";
			out << "],\"deferred_evaluators\":" << q.deferred.size() << ",\"children\":" << q.kids.size();
		}
		out << "}}\n";
		std::ofstream file(outPath, std::ios::binary | std::ios::trunc);
		if (!file)
			throw std::runtime_error("cannot write " + outPath);
		file << out.str();
		file.close();
		std::ostringstream reply;
		reply << "{\"ok\": true, \"path\": \"" << JsonEscape(outPath) << "\", \"loaded\": " << (loaded ? "true" : "false") << ", \"valid\": " << (valid ? "true" : "false") << "}";
		return reply.str();
	}

	// bridge.bt.nodes <output file> | <names file, one node type per line>
	// Asks the behaviour tree manager's node factory (the engine's own registry: CryAISystem's Core, AI and Basic nodes, plus whatever the game plugin registers) to create each named node type and
	// writes the XML description the node gives of itself (CreateXmlDescription: its tag and every attribute with its default). A name the factory does not know is created=false (the engine logs
	// "Failed to create behavior tree node of type"). The file holds [{"name", "created", "xml"}].
	if (verb == "bridge.bt.nodes")
	{
		std::string rest = cmd.substr(cmd.find(verb) + verb.size());
		const size_t bar = rest.find('|');
		if (bar == std::string::npos)
			throw std::runtime_error("bridge.bt.nodes requires <output file> | <names file>");
		auto trim = [](std::string s)
		{
			const size_t b = s.find_first_not_of(" \t\r\n");
			const size_t e = s.find_last_not_of(" \t\r\n");
			return b == std::string::npos ? std::string() : s.substr(b, e - b + 1);
		};
		const std::string outPath = trim(rest.substr(0, bar)), namesPath = trim(rest.substr(bar + 1));
		if (!gEnv || !gEnv->pAISystem || !gEnv->pAISystem->GetIBehaviorTreeManager())
			throw std::runtime_error("the AI system's behavior tree manager is not available");
		std::ifstream in(namesPath, std::ios::binary);
		if (!in)
			throw std::runtime_error("cannot read " + namesPath);
		BehaviorTree::INodeFactory& factory = gEnv->pAISystem->GetIBehaviorTreeManager()->GetNodeFactory();
		std::ostringstream out;
		out << "[\n";
		std::string line;
		int count = 0, created = 0;
		while (std::getline(in, line))
		{
			line = trim(line);
			if (line.empty())
				continue;
			BehaviorTree::INodePtr pNode = factory.CreateNodeOfType(line.c_str());
			std::string xml;
			if (pNode)
			{
				++created;
				XmlNodeRef x = pNode->CreateXmlDescription();
				if (x)
					xml = x->getXML().c_str();
			}
			out << (count ? ",\n" : "") << "{\"name\":\"" << EnvStr(line.c_str()) << "\",\"created\":" << (pNode ? "true" : "false") << ",\"xml\":\"" << EnvStr(xml.c_str()) << "\"}";
			++count;
		}
		out << "\n]\n";
		std::ofstream file(outPath, std::ios::binary | std::ios::trunc);
		if (!file)
			throw std::runtime_error("cannot write " + outPath);
		file << out.str();
		file.close();
		std::ostringstream reply;
		reply << "{\"ok\": true, \"path\": \"" << JsonEscape(outPath) << "\", \"asked\": " << count << ", \"created\": " << created << "}";
		return reply.str();
	}

	// bridge.bt.load <output json> | <behavior tree .xml file>
	// A behaviour tree file through the engine's own loader steps (the same sequence as BehaviorTreeManager::LoadBehaviorTreeTemplate: MetaExtensions, Variables, Events, SignalVariables,
	// Timestamps, then the Root node through XmlLoader and the engine's node factory), twice: STRICT (isLoadingFromEditor=false: what the game does; the tree is refused on any error) and
	// LENIENT (isLoadingFromEditor=true: what the Behavior Tree Editor does; the tree loads even with errors). The nodes are only created and dropped again: no tree is cached or started.
	// For the lenient load the engine's own description of the loaded tree (INode::CreateXmlDescription) is returned so the caller can diff it with the file, and the log lines both loads
	// produced are captured (log verbosity is raised to 4 for the load and put back). The file holds {"strict": {"ok", "log"}, "lenient": {"ok", "log", "xml"}, "variables_ok", "events": [...]}.
	if (verb == "bridge.bt.load")
	{
		std::string rest = cmd.substr(cmd.find(verb) + verb.size());
		const size_t bar = rest.find('|');
		if (bar == std::string::npos)
			throw std::runtime_error("bridge.bt.load requires <output json> | <tree xml file>");
		auto trim = [](std::string s)
		{
			const size_t b = s.find_first_not_of(" \t\r\n");
			const size_t e = s.find_last_not_of(" \t\r\n");
			return b == std::string::npos ? std::string() : s.substr(b, e - b + 1);
		};
		const std::string outPath = trim(rest.substr(0, bar)), treePath = trim(rest.substr(bar + 1));
		if (!gEnv || !gEnv->pAISystem || !gEnv->pAISystem->GetIBehaviorTreeManager())
			throw std::runtime_error("the AI system's behavior tree manager is not available");
		std::ifstream in(treePath, std::ios::binary);
		if (!in)
			throw std::runtime_error("cannot read " + treePath);
		const std::string text((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
		XmlNodeRef xmlRoot = gEnv->pSystem->LoadXmlFromBuffer(text.c_str(), text.size());
		if (!xmlRoot)
			throw std::runtime_error("the file is not well-formed XML (an XML comment containing -- is enough)");
		BehaviorTree::IBehaviorTreeManager* pMgr = gEnv->pAISystem->GetIBehaviorTreeManager();

		struct LogGrab : public ILogCallback
		{
			std::vector<std::string> lines;
			ICVar* v1 = nullptr;
			ICVar* v2 = nullptr;
			int o1 = 0, o2 = 0;
			bool added = false;
			void OnWriteToConsole(const char* s, bool) override { if (s) lines.push_back(s); }
			void OnWriteToFile(const char*, bool) override {}
			void Begin()
			{
				v1 = gEnv->pConsole->GetCVar("log_Verbosity");
				v2 = gEnv->pConsole->GetCVar("log_WriteToFileVerbosity");
				o1 = v1 ? v1->GetIVal() : 0;
				o2 = v2 ? v2->GetIVal() : 0;
				if (v1) v1->Set(4);
				if (v2) v2->Set(4);
				gEnv->pLog->AddCallback(this);
				added = true;
			}
			~LogGrab()
			{
				if (added) gEnv->pLog->RemoveCallback(this);
				if (v1) v1->Set(o1);
				if (v2) v2->Set(o2);
			}
		};

		const std::string treeName = "zz_claude_bt_check";
		struct Result
		{
			bool ok = false;
			bool variablesOk = true;
			std::vector<std::string> log;
			std::string xml;
			std::vector<std::string> events;
		};
		auto load = [&](bool fromEditor) -> Result
		{
			Result r;
			LogGrab grab;
			grab.Begin();
			BehaviorTree::MetaExtensionTable metas;
			Variables::Declarations decls;
			Variables::EventsDeclaration events;
			Variables::SignalHandler signalHandler;
			BehaviorTree::TimestampCollection stamps;
			bool good = true;
			pMgr->GetMetaExtensionFactory().CreateMetaExtensions(metas);
			if (XmlNodeRef x = xmlRoot->findChild("MetaExtensions"))
				good = good && metas.LoadFromXml(x);
			if (XmlNodeRef x = xmlRoot->findChild("Variables"))
				good = good && decls.LoadFromXML(x, treeName.c_str());
			if (XmlNodeRef x = xmlRoot->findChild("Events"))
				good = good && events.LoadFromXML(x, treeName.c_str());
			if (XmlNodeRef x = xmlRoot->findChild("SignalVariables"))
				good = good && signalHandler.LoadFromXML(decls, events, x, treeName.c_str(), fromEditor);
			if (XmlNodeRef x = xmlRoot->findChild("Timestamps"))
				good = good && stamps.LoadFromXml(events, x, treeName.c_str(), fromEditor);
			r.variablesOk = good;
			BehaviorTree::LoadContext ctx(pMgr->GetNodeFactory(), treeName.c_str(), decls, events, stamps);
			BehaviorTree::INodePtr pRoot = good ? BehaviorTree::XmlLoader().CreateBehaviorTreeRootNodeFromBehaviorTreeXml(xmlRoot, ctx, fromEditor) : BehaviorTree::INodePtr();
			r.ok = good && pRoot != nullptr;
			if (pRoot)
			{
				XmlNodeRef desc = pRoot->CreateXmlDescription();
				if (desc)
					r.xml = desc->getXML().c_str();
			}
			for (const Variables::Event& e : events.GetGameEvents())
				r.events.push_back(e.name.c_str());
			r.log = grab.lines;
			return r;
		};
		const Result strict = load(false);
		const Result lenient = load(true);

		auto logJson = [](const std::vector<std::string>& lines)
		{
			std::ostringstream s;
			s << "[";
			for (size_t i = 0; i < lines.size(); ++i)
				s << (i ? "," : "") << "\"" << EnvStr(lines[i].c_str()) << "\"";
			s << "]";
			return s.str();
		};
		std::ostringstream out;
		out << "{\"strict\":{\"ok\":" << (strict.ok ? "true" : "false") << ",\"variables_ok\":" << (strict.variablesOk ? "true" : "false") << ",\"log\":" << logJson(strict.log)
		    << "},\"lenient\":{\"ok\":" << (lenient.ok ? "true" : "false") << ",\"variables_ok\":" << (lenient.variablesOk ? "true" : "false") << ",\"log\":" << logJson(lenient.log) << ",\"xml\":\"" << EnvStr(lenient.xml.c_str()) << "\"}"
		    << ",\"events\":" << logJson(strict.events.empty() ? lenient.events : strict.events) << "}\n";
		std::ofstream file(outPath, std::ios::binary | std::ios::trunc);
		if (!file)
			throw std::runtime_error("cannot write " + outPath);
		file << out.str();
		file.close();
		std::ostringstream reply;
		reply << "{\"ok\": true, \"path\": \"" << JsonEscape(outPath) << "\", \"strict\": " << (strict.ok ? "true" : "false") << ", \"lenient\": " << (lenient.ok ? "true" : "false") << "}";
		return reply.str();
	}

	// bridge.object.xml <object name>
	// The object as the editor itself saves it: its own CBaseObject::Serialize into a CObjectArchive, which is what a layer file holds (Type, Pos, Rotate, Scale, Material, and every
	// property of the type: a River's Width, a Road's StepSize, a Rope's segments ...). The Python API has no property setter for these objects; this is the generic reader.
	// Returns {"ok", "name", "xml": "<Object .../>"}.
	if (verb == "bridge.object.xml")
	{
		if (tokens.size() < 2)
			throw std::runtime_error("bridge.object.xml requires <object name>");
		IObjectManager* pObjMan = GetIEditor() ? GetIEditor()->GetObjectManager() : nullptr;
		if (!pObjMan)
			throw std::runtime_error("object manager not available");
		CBaseObject* pObj = pObjMan->FindObject(tokens[1].c_str());
		if (!pObj)
			throw std::runtime_error("no object named: " + tokens[1]);
		XmlNodeRef root = gEnv->pSystem->CreateXmlNode("Objects");
		CObjectArchive ar(pObjMan, root, false);
		ar.node = root->newChild("Object");
		pObj->Serialize(ar);
		std::ostringstream reply;
		reply << "{\"ok\": true, \"name\": \"" << JsonEscape(tokens[1]) << "\", \"xml\": \"" << EnvStr(ar.node->getXML().c_str()) << "\"}";
		return reply.str();
	}

	// bridge.object.set <object name> <base64 of "key=value" lines>
	// Sets properties through the object's own Serialize, the way a layer load does: the object is saved to XML, the given attributes are written over it, and the XML is loaded
	// back into the SAME object, inside one undo step ("Claude: set properties"). A key is an attribute of the Object node, or `Child/attr` for an attribute of a child node
	// (Child is created if missing). Returns {"ok", "name", "changed": [keys], "xml": the object after}. Nothing is saved: the level file is the caller's decision.
	if (verb == "bridge.object.set")
	{
		if (tokens.size() < 3)
			throw std::runtime_error("bridge.object.set requires <object name> <base64 key=value lines>");
		IObjectManager* pObjMan = GetIEditor() ? GetIEditor()->GetObjectManager() : nullptr;
		if (!pObjMan)
			throw std::runtime_error("object manager not available");
		CBaseObject* pObj = pObjMan->FindObject(tokens[1].c_str());
		if (!pObj)
			throw std::runtime_error("no object named: " + tokens[1]);
		const std::string lines = Base64Decode(tokens[2]);
		XmlNodeRef root = gEnv->pSystem->CreateXmlNode("Objects");
		CObjectArchive saveAr(pObjMan, root, false);
		saveAr.node = root->newChild("Object");
		pObj->Serialize(saveAr);
		XmlNodeRef node = saveAr.node;
		std::vector<std::string> changed;
		size_t pos = 0;
		while (pos < lines.size())
		{
			size_t end = lines.find('\n', pos);
			if (end == std::string::npos)
				end = lines.size();
			std::string line = lines.substr(pos, end - pos);
			pos = end + 1;
			while (!line.empty() && (line.back() == '\r' || line.back() == ' '))
				line.pop_back();
			if (!line.empty() && line[0] == '@')
			{
				// `@Tag=<Tag ...>...</Tag>`: replace the object's child node `Tag` with this XML (a spline's <Points>, a rope's <Rope .../>). One line of XML.
				const size_t at = line.find('=');
				if (at == std::string::npos || at < 2)
					continue;
				const std::string tag = line.substr(1, at - 1);
				const std::string xmlText = line.substr(at + 1);
				XmlNodeRef frag = gEnv->pSystem->LoadXmlFromBuffer(xmlText.c_str(), xmlText.size());
				if (!frag)
					throw std::runtime_error("the XML after @" + tag + "= does not parse");
				if (XmlNodeRef old = node->findChild(tag.c_str()))
					node->removeChild(old);
				node->addChild(frag);
				changed.push_back("@" + tag);
				continue;
			}
			const size_t eq = line.find('=');
			if (line.empty() || eq == std::string::npos || eq == 0)
				continue;
			std::string key = line.substr(0, eq);
			const std::string value = line.substr(eq + 1);
			XmlNodeRef target = node;
			const size_t slash = key.rfind('/');
			if (slash != std::string::npos)
			{
				size_t from = 0;
				while (from <= slash)
				{
					size_t cut = key.find('/', from);
					const std::string child = key.substr(from, cut - from);
					XmlNodeRef next = target->findChild(child.c_str());
					target = next ? next : target->newChild(child.c_str());
					from = cut + 1;
				}
				key = key.substr(slash + 1);
			}
			target->setAttr(key.c_str(), value.c_str());
			changed.push_back(line.substr(0, eq));
		}
		{
			CUndo undo("Claude: set properties");
			CObjectArchive loadAr(pObjMan, root, true);
			loadAr.node = node;
			pObj->Serialize(loadAr);
			// An entity's flow graph is read in PostLoad ("after loading of everything", EntityObject.cpp), not in Serialize: without this a `@FlowGraph=<FlowGraph ...>` child was written
			// into the XML and silently ignored (measured 2026-10-25: nodes read back 0).
			for (const std::string& c : changed)
				if (c == "@FlowGraph")
				{
					pObj->PostLoad(loadAr);
					break;
				}
			// Loading the XML back is not an edit as far as the editor's save is concerned: the layer was not flagged modified, so a save_level right after wrote nothing and the
			// change lived only in memory (measured 2026-10-25: BoxMin/BoxMax of an AreaBox set, level 'saved', layer file unchanged). Flag the object (and so its layer) and the level.
			pObj->SetLayerModified();      // what the editor's own undo does after it loads an object back (CUndoBaseObject): without it the layer is written by no save
			pObj->SetModified(false, true);
			if (GetIEditor())
				GetIEditor()->SetModifiedFlag();
		}
		XmlNodeRef after = gEnv->pSystem->CreateXmlNode("Objects");
		CObjectArchive afterAr(pObjMan, after, false);
		afterAr.node = after->newChild("Object");
		pObj->Serialize(afterAr);
		std::ostringstream reply;
		reply << "{\"ok\": true, \"name\": \"" << JsonEscape(tokens[1]) << "\", \"changed\": [";
		for (size_t i = 0; i < changed.size(); ++i)
			reply << (i ? "," : "") << "\"" << EnvStr(changed[i].c_str()) << "\"";
		reply << "], \"xml\": \"" << EnvStr(afterAr.node->getXML().c_str()) << "\"}";
		return reply.str();
	}

	// bridge.schematyc.dump <output file>
	// Writes the whole Schematyc environment as JSON (see SchematycDump above) and returns {"ok", "path", "elements", "counts": {type: n}}.
	if (verb == "bridge.schematyc.dump")
	{
		if (tokens.size() < 2)
			throw std::runtime_error("bridge.schematyc.dump requires an output file path");
		// the path is the rest of the line: a project folder can have a space in its name ("My Project")
		std::string path = cmd.substr(cmd.find(verb) + verb.size());
		const size_t b = path.find_first_not_of(" \t");
		const size_t e = path.find_last_not_of(" \t\r\n");
		path = (b == std::string::npos) ? std::string() : path.substr(b, e - b + 1);
		return SchematycDump(path);
	}

	// bridge.cvar_get <name>
	// Returns JSON: {"name":.., "value":.., "help":..} -- matches
	// GamePlugin.cpp's bridge.cvar_get exactly, since server.py's
	// _send_structured() json.loads()s whatever this returns as a second
	// layer (see JsonEscape's comment above).
	if (verb == "bridge.cvar_get")
	{
		if (tokens.size() < 2)
			throw std::runtime_error("bridge.cvar_get requires a cvar name");
		ICVar* pVar = gEnv->pConsole->GetCVar(tokens[1].c_str());
		if (!pVar)
			throw std::runtime_error("unknown cvar: " + tokens[1]);
		std::ostringstream oss;
		oss << "{\"name\": \"" << JsonEscape(tokens[1])
		    << "\", \"value\": \"" << JsonEscape(pVar->GetString())
		    << "\", \"help\": \"" << JsonEscape(pVar->GetHelp() ? pVar->GetHelp() : "")
		    << "\"}";
		return oss.str();
	}

	// bridge.cvar_set <name> <value>
	// Returns JSON: {"name":.., "value":..} -- matches GamePlugin.cpp.
	if (verb == "bridge.cvar_set")
	{
		if (tokens.size() < 3)
			throw std::runtime_error("bridge.cvar_set requires a cvar name and value");
		ICVar* pVar = gEnv->pConsole->GetCVar(tokens[1].c_str());
		if (!pVar)
			throw std::runtime_error("unknown cvar: " + tokens[1]);
		// Reconstruct the value from the remaining tokens in case it had
		// embedded spaces (e.g. a string cvar); good enough for our needs.
		std::string value = tokens[2];
		for (size_t i = 3; i < tokens.size(); ++i)
			value += " " + tokens[i];
		pVar->Set(value.c_str());
		std::ostringstream oss;
		oss << "{\"name\": \"" << JsonEscape(tokens[1])
		    << "\", \"value\": \"" << JsonEscape(pVar->GetString()) << "\"}";
		return oss.str();
	}

	// bridge.screenshot [filename]
	// Returns JSON: {"ok": true} -- matches GamePlugin.cpp.
	if (verb == "bridge.screenshot")
	{
		if (!gEnv->pRenderer)
			throw std::runtime_error("gEnv->pRenderer not available");
		const char* filename = tokens.size() >= 2 ? tokens[1].c_str() : nullptr;
		if (!gEnv->pRenderer->ScreenShot(filename))
			throw std::runtime_error("ScreenShot() returned false");
		return "{\"ok\": true}";
	}

	// bridge.entities.list [filter]
	// Returns a JSON array of {"id":.., "name":.., "pos":[x,y,z]} -- matches
	// GamePlugin.cpp exactly (previously this returned tab-separated plain
	// text, which broke server.py's list_entities() since _send_structured()
	// json.loads()s the response).
	if (verb == "bridge.entities.list")
	{
		if (!gEnv->pEntitySystem)
			throw std::runtime_error("gEnv->pEntitySystem not available");
		const std::string filter = tokens.size() >= 2 ? tokens[1] : std::string();
		std::ostringstream out;
		out << "[";
		bool first = true;
		int count = 0;
		const int kMaxEntities = 200;
		IEntityItPtr it = gEnv->pEntitySystem->GetEntityIterator();
		it->MoveFirst();
		while (!it->IsEnd() && count < kMaxEntities)
		{
			IEntity* pEntity = it->Next();
			if (!pEntity) continue;
			const char* name = pEntity->GetName();
			if (!filter.empty() && (!name || std::string(name).find(filter) == std::string::npos))
				continue;
			const Vec3 pos = pEntity->GetWorldPos();
			if (!first) out << ", ";
			first = false;
			out << "{\"id\": " << pEntity->GetId()
			    << ", \"name\": \"" << JsonEscape(name ? name : "") << "\""
			    << ", \"pos\": [" << pos.x << ", " << pos.y << ", " << pos.z << "]}";
			++count;
		}
		out << "]";
		return out.str();
	}

	// bridge.entities.physics <name...>
	// Live physical state of one entity, for diagnosing character movement and
	// collisions without writing game code: physics type, collision class,
	// bbox, parts (geometry type, flags), and for PE_LIVING the full
	// pe_player_dimensions / pe_player_dynamics / pe_status_living. Nothing in
	// Sandbox's Python or console exposes these. Slope angles are DEGREES: the
	// comment in physinterface.h says radians, but CLivingEntity::SetParams
	// converts from degrees and GetParams converts back (livingentity.cpp
	// ~440/~493). Unset ("unused") floats come back as null rather than as NaN,
	// which is not valid JSON.
	if (verb == "bridge.entities.physics")
	{
		if (tokens.size() < 2)
			throw std::runtime_error("bridge.entities.physics requires <entity name>");
		if (!gEnv->pEntitySystem)
			throw std::runtime_error("gEnv->pEntitySystem not available");
		std::string name = tokens[1];
		for (size_t i = 2; i < tokens.size(); ++i)
			name += " " + tokens[i];
		IEntity* pEntity = gEnv->pEntitySystem->FindEntityByName(name.c_str());
		if (!pEntity)
			throw std::runtime_error("no entity named: " + name);

		auto num = [](float f) -> std::string
		{
			if (!std::isfinite(f) || f > 1e30f || f < -1e30f)
				return "null";
			std::ostringstream s;
			s << f;
			return s.str();
		};
		auto vec = [&num](const Vec3& v) -> std::string
		{
			return "[" + num(v.x) + ", " + num(v.y) + ", " + num(v.z) + "]";
		};

		std::ostringstream oss;
		oss << "{\"ok\": true, \"name\": \"" << JsonEscape(name) << "\""
		    << ", \"id\": " << pEntity->GetId()
		    << ", \"class\": \"" << JsonEscape(std::string(pEntity->GetClass() ? pEntity->GetClass()->GetName() : "")) << "\""
		    << ", \"entityPos\": " << vec(pEntity->GetWorldPos());

		IPhysicalEntity* pPhys = pEntity->GetPhysics();
		if (!pPhys)
		{
			oss << ", \"physics\": null}";
			return oss.str();
		}

		const int physType = (int)pPhys->GetType();
		oss << ", \"physType\": " << physType
		    << ", \"physTypeName\": \"" << (physType == PE_LIVING ? "PE_LIVING" : physType == PE_RIGID ? "PE_RIGID" :
		                                     physType == PE_STATIC ? "PE_STATIC" : physType == PE_ARTICULATED ? "PE_ARTICULATED" :
		                                     physType == PE_WHEELEDVEHICLE ? "PE_WHEELEDVEHICLE" : physType == PE_AREA ? "PE_AREA" : "other") << "\"";

		pe_status_pos sp;
		if (pPhys->GetStatus(&sp))
			oss << ", \"physPos\": " << vec(sp.pos) << ", \"bboxMin\": " << vec(sp.BBox[0]) << ", \"bboxMax\": " << vec(sp.BBox[1]);

		pe_status_dynamics sd;
		if (pPhys->GetStatus(&sd))
			oss << ", \"mass\": " << num(sd.mass) << ", \"velocity\": " << vec(sd.v);

		pe_status_awake sa;
		oss << ", \"awake\": " << pPhys->GetStatus(&sa);

		pe_params_collision_class cc;
		if (pPhys->GetParams(&cc))
			oss << ", \"collisionClass\": {\"type\": " << cc.collisionClassOR.type << ", \"ignore\": " << cc.collisionClassOR.ignore << "}";

		pe_status_nparts np;
		const int nParts = pPhys->GetStatus(&np);
		oss << ", \"parts\": [";
		bool firstPart = true;
		for (int i = 0; i < nParts && i < 32; ++i)
		{
			pe_params_part pp;
			pp.ipart = i;
			if (!pPhys->GetParams(&pp))
				continue;
			const int geomType = (pp.pPhysGeom && pp.pPhysGeom->pGeom) ? pp.pPhysGeom->pGeom->GetType() : -1;
			oss << (firstPart ? "" : ", ")
			    << "{\"partid\": " << pp.partid << ", \"geomType\": " << geomType
			    << ", \"pos\": " << vec(pp.pos) << ", \"scale\": " << num(pp.scale)
			    << ", \"flags\": " << pp.flagsOR << ", \"flagsCollider\": " << pp.flagsColliderOR
			    << ", \"mass\": " << num(pp.mass) << "}";
			firstPart = false;
		}
		oss << "]";

		if (physType == PE_LIVING)
		{
			pe_player_dimensions pd;
			if (pPhys->GetParams(&pd))
			{
				oss << ", \"dimensions\": {\"heightPivot\": " << num(pd.heightPivot)
				    << ", \"heightEye\": " << num(pd.heightEye)
				    << ", \"sizeCollider\": " << vec(pd.sizeCollider)
				    << ", \"heightCollider\": " << num(pd.heightCollider)
				    << ", \"headRadius\": " << num(pd.headRadius)
				    << ", \"heightHead\": " << num(pd.heightHead)
				    << ", \"maxUnproj\": " << num(pd.maxUnproj)
				    << ", \"bUseCapsule\": " << pd.bUseCapsule
				    << ", \"groundContactEps\": " << num(pd.groundContactEps);
				// Derived, in metres above the entity pivot. CLivingEntity::Step lets the
				// character step up by heightCollider - size.z - size.x*bUseCapsule
				// (livingentity.cpp ~1414, "maxUpStep") and shoots its ground ray from
				// heightCollider - size.z downwards; a collider whose bottom touches the
				// pivot therefore has zero step-up height.
				const float capR = pd.bUseCapsule ? pd.sizeCollider.x : 0.0f;
				oss << ", \"colliderBottom\": " << num(pd.heightCollider - pd.heightPivot - pd.sizeCollider.z - capR)
				    << ", \"colliderTop\": " << num(pd.heightCollider - pd.heightPivot + pd.sizeCollider.z + capR)
				    << ", \"stepUpHeight\": " << num(pd.heightCollider - pd.sizeCollider.z - capR) << "}";
			}
			pe_player_dynamics dyn;
			if (pPhys->GetParams(&dyn))
			{
				oss << ", \"dynamics\": {\"kInertia\": " << num(dyn.kInertia)
				    << ", \"kInertiaAccel\": " << num(dyn.kInertiaAccel)
				    << ", \"kAirControl\": " << num(dyn.kAirControl)
				    << ", \"kAirResistance\": " << num(dyn.kAirResistance)
				    << ", \"gravity\": " << vec(dyn.gravity)
				    << ", \"nodSpeed\": " << num(dyn.nodSpeed)
				    << ", \"bSwimming\": " << dyn.bSwimming
				    << ", \"mass\": " << num(dyn.mass)
				    << ", \"surface_idx\": " << dyn.surface_idx
				    << ", \"minSlideAngleDeg\": " << num(dyn.minSlideAngle)
				    << ", \"maxClimbAngleDeg\": " << num(dyn.maxClimbAngle)
				    << ", \"maxJumpAngleDeg\": " << num(dyn.maxJumpAngle)
				    << ", \"minFallAngleDeg\": " << num(dyn.minFallAngle)
				    << ", \"maxVelGround\": " << num(dyn.maxVelGround)
				    << ", \"timeImpulseRecover\": " << num(dyn.timeImpulseRecover)
				    << ", \"collTypes\": " << dyn.collTypes
				    << ", \"bActive\": " << dyn.bActive << "}";
			}
			pe_status_living sl;
			if (pPhys->GetStatus(&sl))
			{
				oss << ", \"living\": {\"bFlying\": " << sl.bFlying
				    << ", \"timeFlying\": " << num(sl.timeFlying)
				    << ", \"vel\": " << vec(sl.vel)
				    << ", \"velUnconstrained\": " << vec(sl.velUnconstrained)
				    << ", \"velRequested\": " << vec(sl.velRequested)
				    << ", \"velGround\": " << vec(sl.velGround)
				    << ", \"groundHeight\": " << num(sl.groundHeight)
				    << ", \"groundSlope\": " << vec(sl.groundSlope)
				    << ", \"groundSurfaceIdx\": " << sl.groundSurfaceIdx
				    << ", \"hasDynamicGroundCollider\": " << (sl.pGroundCollider ? "true" : "false")
				    << ", \"camOffset\": " << vec(sl.camOffset)
				    << ", \"timeSinceStanceChange\": " << num(sl.timeSinceStanceChange)
				    << ", \"bStuck\": " << sl.bStuck
				    << ", \"bSquashed\": " << sl.bSquashed << "}";
			}
		}
		oss << "}";
		return oss.str();
	}

	// bridge.entity.add_component <entity name> <component GUID, class name or label>
	// Adds a component to an entity the way the Inspector's "Add Component" button does (CUndoAddComponent::AddComponentInternal: CreateComponentByInterfaceID with the class description from
	// the Schematyc environment, flagged UserAdded). Everything after the entity name is the component, so a label with spaces works ("Particle Emitter"). There is no undo step; the level is
	// flagged modified. Returns {"ok", "entity", "component", "label", "guid": the new instance's GUID}.
	if (verb == "bridge.entity.add_component")
	{
		if (tokens.size() < 3)
			throw std::runtime_error("bridge.entity.add_component requires <entity name> <component>");
		IEntity* pEntity = gEnv->pEntitySystem ? gEnv->pEntitySystem->FindEntityByName(tokens[1].c_str()) : nullptr;
		if (!pEntity)
			throw std::runtime_error("no entity named: " + tokens[1]);
		if (!gEnv->pSchematyc)
			throw std::runtime_error("Schematyc is not loaded");
		auto lower = [](std::string s) { for (char& c : s) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c))); return s; };
		std::string wanted = cmd.substr(cmd.find(tokens[1], cmd.find(verb) + verb.size()) + tokens[1].size());
		const size_t wb = wanted.find_first_not_of(" \t");
		const size_t we = wanted.find_last_not_of(" \t\r\n");
		wanted = lower(wb == std::string::npos ? std::string() : wanted.substr(wb, we - wb + 1));
		const CEntityComponentClassDesc* pDesc = nullptr;
		gEnv->pSchematyc->GetEnvRegistry().VisitComponents([&](const Schematyc::IEnvComponent& component) -> Schematyc::EVisitStatus
		{
			const CEntityComponentClassDesc& d = component.GetDesc();
			const std::string name = d.GetName().c_str();
			const size_t scope = name.rfind("::");
			const std::string shortName = scope == std::string::npos ? name : name.substr(scope + 2);
			const std::string label = d.GetLabel() ? d.GetLabel() : "";
			if (lower(d.GetGUID().ToString().c_str()) == wanted || lower(name) == wanted || lower(shortName) == wanted || (!label.empty() && lower(label) == wanted))
			{
				pDesc = &d;
				return Schematyc::EVisitStatus::Stop;
			}
			return Schematyc::EVisitStatus::Continue;
		});
		if (!pDesc)
			throw std::runtime_error("no component '" + wanted + "' in the Schematyc environment (schematyc_search lists them)");
		EntityComponentFlags flags = pDesc->GetComponentFlags();
		flags.Add(EEntityComponentFlags::UserAdded);
		IEntityComponent::SInitParams initParams(pEntity, CryGUID::Create(), "", pDesc, flags, nullptr, nullptr);
		IEntityComponent* pComponent = pEntity->CreateComponentByInterfaceID(pDesc->GetGUID(), &initParams);
		if (!pComponent)
			throw std::runtime_error("the entity system refused to add the component (a singleton the entity already has?)");
		if (GetIEditor())
			GetIEditor()->SetModifiedFlag();
		std::ostringstream reply;
		reply << "{\"ok\": true, \"entity\": \"" << JsonEscape(tokens[1]) << "\", \"component\": \"" << JsonEscape(pDesc->GetName().c_str()) << "\", \"label\": \"" << JsonEscape(pDesc->GetLabel() ? pDesc->GetLabel() : "")
		      << "\", \"guid\": \"" << pComponent->GetGUID().ToString().c_str() << "\"}";
		return reply.str();
	}

	// bridge.component.properties <entity name> <component> [<json>]
	// Reads, and optionally writes, the reflected properties of one entity
	// component -- the values the Inspector shows under a component. Sandbox's
	// general.set_entity_param only reaches legacy Lua entity properties
	// ("is an invalid parameter" for a C++ component member), so without this a
	// component property could only be set by hand.
	// <component> matches the component class name or label, case-insensitive
	// (e.g. CNpcComponent, CCharacterControllerComponent); add #N to pick the
	// N-th instance. Arguments are split on whitespace, so a label containing
	// spaces ("Patrolling NPC") cannot be matched -- use the class name.
	// <json> is an object of members by their serialized names as they appear in
	// the layer file, e.g. {"PatrolPath":"VillagerRoute"}.
	// WARNING: send the COMPLETE property set. The JSON input archive resets
	// string members that the JSON omits (writing only PatrolPath blanked the
	// villager's AgentType); numeric members happened to survive. Read first
	// (no <json>), merge, write back -- the MCP tool component_properties does. This mirrors CreateComponentWidget in
	// EntityObject.cpp: Schematyc::Utils::CClassSerializer over the component,
	// then ENTITY_EVENT_COMPONENT_PROPERTY_CHANGED, and the level is flagged
	// modified so a save writes it.
	// Returns JSON: {"ok", "entity", "component", "changed", "properties": {...}}
	if (verb == "bridge.component.properties")
	{
		if (tokens.size() < 3)
			throw std::runtime_error("bridge.component.properties requires <entity name> <component> [<json>]");
		if (!gEnv->pEntitySystem)
			throw std::runtime_error("gEnv->pEntitySystem not available");
		IEntity* pEntity = gEnv->pEntitySystem->FindEntityByName(tokens[1].c_str());
		if (!pEntity)
			throw std::runtime_error("no entity named: " + tokens[1]);

		std::string wanted = tokens[2];
		int wantedIndex = 0;
		const size_t hashPos = wanted.find('#');
		if (hashPos != std::string::npos)
		{
			wantedIndex = std::atoi(wanted.c_str() + hashPos + 1);
			wanted = wanted.substr(0, hashPos);
		}
		auto lower = [](std::string s) { for (char& c : s) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c))); return s; };
		const std::string wantedLower = lower(wanted);

		DynArray<IEntityComponent*> components;
		pEntity->GetComponents(components);
		IEntityComponent* pComponent = nullptr;
		int seen = 0;
		std::ostringstream available;
		for (IEntityComponent* pCandidate : components)
		{
			const Schematyc::CClassDesc& desc = pCandidate->GetClassDesc();
			const std::string name = desc.GetName().c_str();
			const std::string label = desc.GetLabel() ? desc.GetLabel() : "";
			if (available.tellp() > 0)
				available << ", ";
			available << name;
			// Default components are registered with their namespace
			// ("Cry::DefaultComponents::CCharacterControllerComponent"), so also
			// accept the bare class name after the last "::".
			const size_t scopePos = name.rfind("::");
			const std::string shortName = scopePos == std::string::npos ? name : name.substr(scopePos + 2);
			if (lower(name) == wantedLower || lower(shortName) == wantedLower || (!label.empty() && lower(label) == wantedLower))
			{
				if (seen++ == wantedIndex)
				{
					pComponent = pCandidate;
					break;
				}
			}
		}
		if (!pComponent)
			throw std::runtime_error("no component '" + tokens[2] + "' on " + tokens[1] + " (has: " + available.str() + ")");
		if (!gEnv->pSystem || !gEnv->pSystem->GetArchiveHost())
			throw std::runtime_error("archive host not available");

		Schematyc::Utils::CClassSerializer serializer(pComponent->GetClassDesc(), pComponent);
		bool bChanged = false;
		if (tokens.size() > 3)
		{
			std::string json = tokens[3];
			for (size_t i = 4; i < tokens.size(); ++i)
				json += " " + tokens[i];
			Schematyc::CClassProperties before;
			before.Read(pComponent->GetClassDesc(), pComponent);
			if (!gEnv->pSystem->GetArchiveHost()->LoadJsonBuffer(Serialization::SStruct(serializer), json.c_str(), json.size()))
				throw std::runtime_error("could not parse the JSON for " + tokens[2]);
			Schematyc::CClassProperties after;
			after.Read(pComponent->GetClassDesc(), pComponent);
			bChanged = !before.Compare(after);
			SEntityEvent changedEvent(ENTITY_EVENT_COMPONENT_PROPERTY_CHANGED);
			pComponent->SendEvent(changedEvent);
			if (bChanged && GetIEditor())
				GetIEditor()->SetModifiedFlag();
		}

		DynArray<char> out;
		gEnv->pSystem->GetArchiveHost()->SaveJsonBuffer(out, Serialization::SStruct(serializer));
		std::string properties(out.begin(), out.end());
		while (!properties.empty() && (properties.back() == '\0' || std::isspace(static_cast<unsigned char>(properties.back()))))
			properties.pop_back();
		if (properties.empty())
			properties = "{}";

		std::ostringstream oss;
		oss << "{\"ok\": true, \"entity\": \"" << JsonEscape(tokens[1])
		    << "\", \"component\": \"" << JsonEscape(pComponent->GetClassDesc().GetName().c_str())
		    << "\", \"changed\": " << (bChanged ? "true" : "false")
		    << ", \"properties\": " << properties << "}";
		return oss.str();
	}

	// bridge.shape.add_points <object name> <agentType|-> <x> <y> <z> [<x> <y> <z> ...]
	// Appends WORLD-space points to any shape object (AIPath, Shape, AIShape...).
	// general.nav_insert_point only accepts NavigationArea objects
	// (ShapeObject.cpp PyInsertPoint's IsKindOf check), so there is no script
	// route to build an AI Path. CAIPathObject::InsertPoint re-registers the
	// path with the AI system itself (UpdateGameArea ->
	// IAISystem::CreateNavigationShape), which is what makes it visible to
	// INavigation::GetPointOnPathBySegNo in game code.
	// With an agent type name, each point is also tested against that agent's
	// navmesh (IsLocationValidInNavigationMesh, 2 m down / 1 m up), so a path
	// laid outside the navigation area is reported instead of silently failing
	// every reachability test later.
	// Returns JSON: {"ok", "name", "class", "pointCount", "added", "agentType",
	//                "agentTypeValid", "offMesh", "points":[{"pos", "onNavmesh"}]}
	if (verb == "bridge.shape.add_points")
	{
		if (tokens.size() < 6 || (tokens.size() - 3) % 3 != 0)
			throw std::runtime_error("bridge.shape.add_points requires <object name> <agentType|-> <x> <y> <z> [...]");
		IObjectManager* pObjMan = GetIEditor() ? GetIEditor()->GetObjectManager() : nullptr;
		if (!pObjMan)
			throw std::runtime_error("object manager not available");
		CBaseObject* pObj = pObjMan->FindObject(tokens[1].c_str());
		if (!pObj)
			throw std::runtime_error("no object named: " + tokens[1]);
		if (!pObj->IsKindOf(RUNTIME_CLASS(CShapeObject)))
			throw std::runtime_error("object is not a shape: " + tokens[1]);
		CShapeObject* pShape = static_cast<CShapeObject*>(pObj);

		INavigationSystem* pNavSys = (gEnv->pAISystem != nullptr) ? gEnv->pAISystem->GetNavigationSystem() : nullptr;
		const bool bCheckMesh = tokens[2] != "-" && pNavSys != nullptr;
		NavigationAgentTypeID agentId;
		if (bCheckMesh)
			agentId = pNavSys->GetAgentTypeID(tokens[2].c_str());

		std::ostringstream pts;
		int added = 0;
		int offMesh = 0;
		for (size_t i = 3; i + 2 < tokens.size(); i += 3)
		{
			const Vec3 world(
				static_cast<float>(std::atof(tokens[i].c_str())),
				static_cast<float>(std::atof(tokens[i + 1].c_str())),
				static_cast<float>(std::atof(tokens[i + 2].c_str())));
			// The shape re-centres itself on its first point after every insertion (CShapeObject::CalcBBox moves the object to the first point), so the world-to-local transform is taken per point:
			// taken once before the loop, an object that was not created ON its first point got every later point displaced by the first point's offset (measured 2026-10-13).
			pShape->InsertPoint(-1, pShape->GetWorldTM().GetInverted().TransformPoint(world), false);
			if (added > 0)
				pts << ", ";
			pts << "{\"pos\": [" << world.x << ", " << world.y << ", " << world.z << "]";
			if (bCheckMesh && agentId.IsValid())
			{
				const bool bOnMesh = pNavSys->IsLocationValidInNavigationMesh(agentId, world, nullptr, 2.0f, 1.0f);
				if (!bOnMesh)
					++offMesh;
				pts << ", \"onNavmesh\": " << (bOnMesh ? "true" : "false");
			}
			pts << "}";
			++added;
		}

		std::ostringstream oss;
		oss << "{\"ok\": true, \"name\": \"" << JsonEscape(tokens[1])
		    << "\", \"class\": \"" << JsonEscape(pObj->GetClassDesc() ? pObj->GetClassDesc()->ClassName() : "")
		    << "\", \"pointCount\": " << pShape->GetPointCount()
		    << ", \"added\": " << added
		    << ", \"agentType\": \"" << JsonEscape(tokens[2]) << "\""
		    << ", \"agentTypeValid\": " << ((bCheckMesh && agentId.IsValid()) ? "true" : "false")
		    << ", \"offMesh\": " << offMesh
		    << ", \"points\": [" << pts.str() << "]}";
		return oss.str();
	}

	// bridge.entities.teleport <name> <x> <y> <z>
	// Returns JSON: {"ok": true, "name":.., "pos":[x,y,z]} -- matches
	// GamePlugin.cpp.

	if (verb == "bridge.entities.teleport")
	{
		if (tokens.size() < 5)
			throw std::runtime_error("bridge.entities.teleport requires <name> <x> <y> <z>");
		if (!gEnv->pEntitySystem)
			throw std::runtime_error("gEnv->pEntitySystem not available");
		IEntity* pEntity = gEnv->pEntitySystem->FindEntityByName(tokens[1].c_str());
		if (!pEntity)
			throw std::runtime_error("no entity named: " + tokens[1]);
		const Vec3 newPos(
			static_cast<float>(std::atof(tokens[2].c_str())),
			static_cast<float>(std::atof(tokens[3].c_str())),
			static_cast<float>(std::atof(tokens[4].c_str())));
		pEntity->SetPos(newPos);
		std::ostringstream oss;
		oss << "{\"ok\": true, \"name\": \"" << JsonEscape(tokens[1])
		    << "\", \"pos\": [" << newPos.x << ", " << newPos.y << ", " << newPos.z << "]}";
		return oss.str();
	}

	// bridge.execute_command <editor command...>
	// Routes through Sandbox's own ICommandManager (IEditor::ExecuteCommand),
	// a completely separate registry from IConsole. The fallback below
	// (IConsole::ExecuteString) only knows CVars and true console commands --
	// empirically confirmed it reports "Unknown command" for anything
	// registered via REGISTER_EDITOR_AND_SCRIPT_COMMAND / REGISTER_PYTHON_COMMAND
	// (the "module.command" namespace: general.*, asset.*, level.*, etc, e.g.
	// asset.edit <path> -- the actual supported way to open a level asset
	// from outside the editor UI, since CCryEditApp::LoadLevel isn't itself
	// console/python-exposed). ExecuteCommand is printf-style under the hood
	// (IEditorImpl.cpp: cry_vsprintf into a fixed buffer, then
	// ICommandManager::Execute) -- passing "%s" here avoids treating a
	// caller-supplied command string as a format string itself.
	if (verb == "bridge.execute_command")
	{
		if (tokens.size() < 2)
			throw std::runtime_error("bridge.execute_command requires a command string");
		std::string fullCmd = tokens[1];
		for (size_t i = 2; i < tokens.size(); ++i)
			fullCmd += " " + tokens[i];
		if (!GetIEditor())
			throw std::runtime_error("GetIEditor() not available");
		// ExecuteCommand itself is fire-and-forget -- it doesn't report back
		// whether the underlying ICommandManager command actually succeeded
		// (e.g. an unknown object typeName just logs a warning and silently
		// no-ops). Capture the log/console output around the call so failures
		// are visible here instead of only in Sandbox's own log window.
		CScopedLogCapture capture;
		GetIEditor()->ExecuteCommand("%s", fullCmd.c_str());
		std::ostringstream oss;
		oss << "{\"ok\": true, \"log\": \"" << JsonEscape(capture.TakeOutput()) << "\"}";
		return oss.str();
	}

	// bridge.execute_python <python statement...>
	// Runs a line through Sandbox's actual Python interpreter
	// (IEditor::GetIPythonManager()->Execute(), the same one the in-editor
	// Python console uses) -- reaches REGISTER_ONLY_PYTHON_COMMAND_WITH_EXAMPLE
	// functions like general.new_object/general.create_object that
	// bridge.execute_command's ICommandManager path cannot see at all.
	// Modules are pre-imported as "from sandbox import <module>" (see
	// PythonManager.cpp), so call e.g.
	// "general.new_object('EntityWithStaticMeshComponent', 'objects/sphere.cgf', 'Ball', 512, 512, 50)".
	// bridge.file.read_b64 <path...>
	// bridge.file.write_b64 <base64> <path...>
	// File access straight from the editor process, with the bytes base64'd so
	// binaries and arbitrary text both survive the line-oriented protocol.
	// This exists because the desktop file-transfer path has a reproducible
	// silent-failure mode: it reports the file as written, updates its mtime,
	// and leaves the OLD CONTENT in place, roughly every other call. Every
	// write through it has to be verified and usually retried. This route
	// reads and writes in one hop and reports the byte count it actually
	// moved, so there is nothing to second-guess.
	//
	// Argument order differs between the two on purpose: base64 is always a
	// single whitespace-free token, so putting it first leaves the rest of the
	// line free to be a path with spaces in it.
	if (verb == "bridge.file.read_b64" || verb == "bridge.file.write_b64")
	{
		const bool bWriting = (verb == "bridge.file.write_b64");
		const size_t firstPathToken = bWriting ? 2 : 1;
		if (tokens.size() <= firstPathToken)
			throw std::runtime_error(verb + (bWriting ? " requires <base64> <path>" : " requires <path>"));

		std::string path = tokens[firstPathToken];
		for (size_t i = firstPathToken + 1; i < tokens.size(); ++i)
			path += " " + tokens[i];

		std::ostringstream oss;
		if (bWriting)
		{
			const std::string bytes = Base64Decode(tokens[1]);
			WriteWholeFile(path, bytes);
			// Read it straight back rather than trusting the write: that is the
			// exact failure this verb exists to avoid, and it costs nothing.
			const std::string verify = ReadWholeFile(path);
			oss << "{\"ok\": true, \"path\": \"" << JsonEscape(path) << "\""
			    << ", \"written\": " << bytes.size()
			    << ", \"verified\": " << verify.size()
			    << ", \"match\": " << ((verify == bytes) ? "true" : "false") << "}";
		}
		else
		{
			const std::string bytes = ReadWholeFile(path);
			oss << "{\"ok\": true, \"path\": \"" << JsonEscape(path) << "\""
			    << ", \"bytes\": " << bytes.size()
			    << ", \"b64\": \"" << Base64Encode(bytes) << "\"}";
		}
		return oss.str();
	}

	// bridge.execute_python.b64 <base64-of-utf8-source>
	// Same interpreter, but the source arrives base64-encoded, so newlines and
	// indentation survive the whitespace-splitting command line and real
	// multi-line scripts become possible.
	if (verb == "bridge.execute_python" || verb == "bridge.execute_python.b64")
	{
		const bool bAlreadyEncoded = (verb == "bridge.execute_python.b64");
		if (tokens.size() < 2)
			throw std::runtime_error(verb + (bAlreadyEncoded ? " requires base64-encoded source" : " requires a python statement"));

		std::string code = tokens[1];
		for (size_t i = 2; i < tokens.size(); ++i)
			code += (bAlreadyEncoded ? "" : " ") + tokens[i];

		if (!GetIEditor())
			throw std::runtime_error("GetIEditor() not available");
		IPythonManager* pPython = GetIEditor()->GetIPythonManager();
		if (!pPython)
			throw std::runtime_error("GetIPythonManager() not available");

		// Two separate hazards live between here and the interpreter, and both
		// are solved the same way -- by putting the source in a FILE and
		// sending Python a short line that reads it.
		//
		// First: the engine funnels Python's stdout back through CryLog, and
		// CryLog treats the text it is handed AS A PRINTF FORMAT STRING. A
		// printed "%s:%d" came back as ":1", and printing anything containing
		// "%n" killed the editor outright -- %n is the specifier that writes
		// through a pointer that was never passed. Echoing a batch file that
		// happened to contain "%n" took Sandbox down three times in a row
		// before that was understood. So the script runs with sys.stdout and
		// sys.stderr redirected into a StringIO which the runner writes to a
		// file; nothing it prints ever reaches the logger.
		//
		// Second: IPythonManager::Execute truncates its argument somewhere
		// around a thousand characters -- silently, mid-string, which surfaces
		// as a baffling SyntaxError about an unterminated literal. An earlier
		// version of this passed the whole wrapper inline as base64 and died
		// on exactly that. The line sent below is a fixed ~120 characters no
		// matter how long the script is.
		const std::string inPath  = BridgeTempFile("claude_bridge_python_in.py");
		const std::string runPath = BridgeTempFile("claude_bridge_python_run.py");
		const std::string outPath = BridgeTempFile("claude_bridge_python_out.txt");
		DeleteFileA(outPath.c_str());

		WriteWholeFile(inPath, bAlreadyEncoded ? Base64Decode(code) : code);

		std::string runner;
		runner += "import sys, io, traceback\n";
		runner += "_bb = io.StringIO()\n";
		runner += "_bo, _be = sys.stdout, sys.stderr\n";
		runner += "sys.stdout = _bb\n";
		runner += "sys.stderr = _bb\n";
		runner += "try:\n";
		runner += "    _src = open(r\"" + inPath + "\", \"r\", encoding=\"utf-8\").read()\n";
		runner += "    exec(compile(_src, \"<bridge>\", \"exec\"), globals())\n";
		runner += "except BaseException:\n";
		runner += "    traceback.print_exc(file=_bb)\n";
		runner += "finally:\n";
		runner += "    sys.stdout = _bo\n";
		runner += "    sys.stderr = _be\n";
		runner += "    _f = open(r\"" + outPath + "\", \"w\", encoding=\"utf-8\")\n";
		runner += "    _f.write(_bb.getvalue())\n";
		runner += "    _f.close()\n";
		WriteWholeFile(runPath, runner);

		const std::string oneLiner =
			"exec(compile(open(r\"" + runPath + "\").read(), \"<bridgerun>\", \"exec\"), globals())";

		// The engine's own log is still worth having -- "Level successfully
		// saved" and friends originate in C++, not in the script -- so it is
		// captured too, but reported separately and deduped.
		CScopedLogCapture capture;
		pPython->Execute(oneLiner.c_str());
		const std::string engineLog = capture.TakeOutput(true);

		const std::string scriptOut = ReadWholeFile(outPath);
		DeleteFileA(outPath.c_str());

		std::ostringstream oss;
		oss << "{\"ok\": true, \"log\": \"" << JsonEscape(scriptOut) << "\""
		    << ", \"engine_log\": \"" << JsonEscape(engineLog) << "\""
		    << ", \"sent_chars\": " << oneLiner.size() << "}";
		return oss.str();
	}

	// bridge.execute_python.raw <python statement...>
	// The pre-wrapper behaviour, kept deliberately: if the wrapper above ever
	// fails to run at all, this is the way back in to diagnose it.
	if (verb == "bridge.execute_python.raw")
	{
		if (tokens.size() < 2)
			throw std::runtime_error("bridge.execute_python.raw requires a python statement");
		std::string code = tokens[1];
		for (size_t i = 2; i < tokens.size(); ++i)
			code += " " + tokens[i];
		if (!GetIEditor())
			throw std::runtime_error("GetIEditor() not available");
		IPythonManager* pPython = GetIEditor()->GetIPythonManager();
		if (!pPython)
			throw std::runtime_error("GetIPythonManager() not available");
		CScopedLogCapture capture;
		pPython->Execute(code.c_str());
		std::ostringstream oss;
		oss << "{\"ok\": true, \"log\": \"" << JsonEscape(capture.TakeOutput()) << "\"}";
		return oss.str();
	}

	// bridge.terrain.generate_height [passes] [frequency] [frequencyStep] [fade] [cover] [seed] [sharpness] [smoothness]
	// Runs the same fractal-noise generator as Sandbox's "Generate Terrain"
	// dialog (CHeightmap::GenerateTerrain -- see Heightmap.cpp), replacing
	// the current flat heightmap with real height variation. All args are
	// optional; defaults are a moderate, natural-looking setting. Valid
	// ranges (see GenerationParam.cpp, the dialog this mirrors):
	// passes 1-10, frequency 1.1-10.0, frequencyStep 1.0-2.5, fade 0.1-2.0,
	// cover 0-255, seed 0-32, sharpness 0.990-0.999, smoothness(blur) 0-3.
	// Width/height are taken from the heightmap's own current resolution
	// (GenerateTerrain asserts they match -- it doesn't resize anything).
	if (verb == "bridge.terrain.generate_height")
	{
		if (!GetIEditor())
			throw std::runtime_error("GetIEditor() not available");
		CHeightmap* pHeightmap = GetIEditor()->GetHeightmap();
		if (!pHeightmap)
			throw std::runtime_error("GetHeightmap() not available");

		SNoiseParams params;
		params.iWidth = pHeightmap->GetWidth();
		params.iHeight = pHeightmap->GetHeight();
		params.iPasses = tokens.size() > 1 ? std::atoi(tokens[1].c_str()) : 4;
		params.fFrequency = tokens.size() > 2 ? static_cast<float>(std::atof(tokens[2].c_str())) : 3.0f;
		params.fFrequencyStep = tokens.size() > 3 ? static_cast<float>(std::atof(tokens[3].c_str())) : 1.7f;
		params.fFade = tokens.size() > 4 ? static_cast<float>(std::atof(tokens[4].c_str())) : 0.8f;
		params.iCover = tokens.size() > 5 ? std::atoi(tokens[5].c_str()) : 128;
		params.iRandom = tokens.size() > 6 ? std::atoi(tokens[6].c_str()) : 17;
		params.iSharpness = tokens.size() > 7 ? static_cast<float>(std::atof(tokens[7].c_str())) : 0.995f;
		params.iSmoothness = tokens.size() > 8 ? std::atoi(tokens[8].c_str()) : 1;
		params.bBlueSky = false;
		params.bValid = true;

		CScopedLogCapture capture;
		pHeightmap->GenerateTerrain(params);
		std::ostringstream oss;
		oss << "{\"ok\": true, \"width\": " << params.iWidth << ", \"height\": " << params.iHeight
		    << ", \"log\": \"" << JsonEscape(capture.TakeOutput()) << "\"}";
		return oss.str();
	}

	// bridge.terrain.set_layer_texture <path>
	// Points the currently-selected terrain layer (see terrain.create_layer /
	// a prior terrain.move_layer_* selection) at a custom diffuse texture
	// (CLayer::LoadTexture -- Layer.cpp), instead of the
	// %ENGINE%/EngineAssets/Textures/white.dds every new layer starts with.
	// Path is resolved through CryPak like any other asset reference (e.g.
	// "textures/terrain_grass_dirt.bmp", relative to the project's Assets
	// folder). Must be a power-of-2 sized image (Layer.cpp rejects anything
	// else) -- CImageUtil::LoadImage handles the actual decoding, so BMP/TIF/
	// DDS should all work. Requires CTerrainManager, which -- unlike
	// CHeightmap -- has no getter on the abstract IEditor interface, hence
	// the CEditorImpl/Sandbox.lib linkage (see the include block above).
	if (verb == "bridge.terrain.set_layer_texture")
	{
		if (tokens.size() < 2)
			throw std::runtime_error("bridge.terrain.set_layer_texture requires a texture path");
		std::string path = tokens[1];
		for (size_t i = 2; i < tokens.size(); ++i)
			path += " " + tokens[i];

		CEditorImpl* pImpl = EditorImpl();
		if (!pImpl)
			throw std::runtime_error("the editor is not available");
		CTerrainManager* pTerrainManager = pImpl->GetTerrainManager();
		if (!pTerrainManager)
			throw std::runtime_error("GetTerrainManager() not available");
		CLayer* pLayer = pTerrainManager->GetSelectedLayer();
		if (!pLayer)
			throw std::runtime_error("no terrain layer is currently selected -- select one first (e.g. terrain.create_layer selects the new layer)");

		CScopedLogCapture capture;
		bool loaded = pLayer->LoadTexture(path.c_str());
		// Same call GenerateTerrain uses to force the terrain surface
		// texture to be recomputed from the (now-changed) layer data,
		// rather than waiting on whatever triggers the UI normally uses.
		pTerrainManager->InvalidateLayers();
		std::ostringstream oss;
		oss << "{\"ok\": " << (loaded ? "true" : "false")
		    << ", \"layer\": \"" << JsonEscape(std::string((const char*)pLayer->GetLayerName())) << "\""
		    << ", \"log\": \"" << JsonEscape(capture.TakeOutput()) << "\"}";
		return oss.str();
	}

	// bridge.terrain.get_heights_raw <path>
	// bridge.terrain.set_heights_raw <path> [maxHeight]
	//
	// The heightmap as raw little-endian float32, row-major, width*height
	// values in metres -- the same layout CHeightmap keeps internally.
	//
	// This exists because GenerateTerrain, the only other way in, is a fractal
	// dialog in disguise: it always calls Normalize() and resets max height to
	// terrainSize/4 + waterLevel, and its iCover/iSharpness arguments are
	// accepted and then never used. You can ask it for a different dome; you
	// cannot ask it for a specific landscape. Writing the array directly means
	// the terrain can be designed anywhere -- numpy, a DEM, a hand-drawn
	// mask -- and simply installed here.
	//
	// Heights are clamped into [0, maxHeight]: values above the ceiling are not
	// stored, they are silently flattened by the encoding, which would look
	// like a mesa appearing out of nowhere.
	if (verb == "bridge.terrain.get_heights_raw" || verb == "bridge.terrain.set_heights_raw")
	{
		const bool bWriting = (verb == "bridge.terrain.set_heights_raw");
		if (tokens.size() < 2)
			throw std::runtime_error(verb + " requires a path to a raw float32 heightmap");

		if (!GetIEditor())
			throw std::runtime_error("GetIEditor() not available");
		CHeightmap* pHeightmap = GetIEditor()->GetHeightmap();
		if (!pHeightmap)
			throw std::runtime_error("no heightmap -- is a level loaded?");

		const uint32 w = (uint32)pHeightmap->GetWidth();
		const uint32 h = (uint32)pHeightmap->GetHeight();
		const size_t count = (size_t)w * (size_t)h;
		t_hmap* pData = pHeightmap->GetData();
		if (!pData || count == 0)
			throw std::runtime_error("heightmap has no data");

		// An optional trailing max-height only applies to the write, and the
		// path is everything between.
		float requestedMax = -1.0f;
		size_t lastPathToken = tokens.size() - 1;
		if (bWriting && tokens.size() >= 3)
		{
			const std::string& tail = tokens[lastPathToken];
			char* end = nullptr;
			const float v = (float)strtod(tail.c_str(), &end);
			if (end && *end == 0 && v > 0.0f)
			{
				requestedMax = v;
				--lastPathToken;
			}
		}
		std::string path = tokens[1];
		for (size_t i = 2; i <= lastPathToken; ++i)
			path += " " + tokens[i];

		if (!bWriting)
		{
			std::string bytes((const char*)pData, count * sizeof(float));
			WriteWholeFile(path, bytes);
			std::ostringstream oss;
			oss << "{\"ok\": true, \"path\": \"" << JsonEscape(path) << "\""
			    << ", \"width\": " << w << ", \"height\": " << h
			    << ", \"bytes\": " << (count * sizeof(float))
			    << ", \"maxHeightSetting\": " << pHeightmap->GetMaxHeight() << "}";
			return oss.str();
		}

		const std::string bytes = ReadWholeFile(path);
		if (bytes.size() != count * sizeof(float))
		{
			std::ostringstream err;
			err << "size mismatch: " << path << " holds " << bytes.size()
			    << " bytes but this heightmap is " << w << "x" << h
			    << " = " << (count * sizeof(float)) << " bytes of float32";
			throw std::runtime_error(err.str());
		}

		if (requestedMax > 0.0f)
			pHeightmap->SetMaxHeight(requestedMax);
		const float ceiling = pHeightmap->GetMaxHeight();

		const float* pIn = reinterpret_cast<const float*>(bytes.data());
		size_t clampedLow = 0, clampedHigh = 0;
		float lo = FLT_MAX, hi = -FLT_MAX;
		double sum = 0.0;
		for (size_t i = 0; i < count; ++i)
		{
			float v = pIn[i];
			if (!(v == v))          // NaN in, flat terrain out -- catch it here
				v = 0.0f;
			if (v < 0.0f) { v = 0.0f; ++clampedLow; }
			else if (v > ceiling) { v = ceiling; ++clampedHigh; }
			pData[i] = v;
			lo = min(lo, v);
			hi = max(hi, v);
			sum += v;
		}

		pHeightmap->UpdateEngineTerrain(false, true);
		pHeightmap->CalcSurfaceTypes();

		std::ostringstream oss;
		oss << "{\"ok\": true, \"path\": \"" << JsonEscape(path) << "\""
		    << ", \"width\": " << w << ", \"height\": " << h
		    << ", \"maxHeightSetting\": " << ceiling
		    << ", \"elevationMin\": " << lo
		    << ", \"elevationMax\": " << hi
		    << ", \"elevationAvg\": " << (float)(sum / (double)count)
		    // Non-zero clamp counts mean the source data did not fit the
		    // ceiling, so the shape on screen is not the shape supplied.
		    << ", \"clampedBelowZero\": " << (unsigned long long)clampedLow
		    << ", \"clampedAtCeiling\": " << (unsigned long long)clampedHigh << "}";
		return oss.str();
	}

	// bridge.terrain.info
	// Objective read-out of the terrain's actual state -- so terrain work can
	// be verified with numbers instead of "does it look right?". Reports the
	// heightmap's real min/max/average elevation (not just the max-height
	// SETTING, which is only the encoding ceiling), the sector layout, whether
	// the RGB layer (the baked base colour texture) is allocated, and the
	// engine's own view of the terrain.
	//
	// engine.terrainTextureNodeSizeMeters is the one to watch: both
	// CHeightmap::UpdateSectorTexture and CTerrainTexturePainter::PaintLayer
	// bail out EARLY AND SILENTLY when it is 0 ("you need to calculate the
	// surface texture first"), which is one of the two ways a terrain texture
	// paint can report success while doing nothing at all.
	// bridge.terrain.hole <x> <y> <width> <height> <1|0>
	// Makes (1) or fills (0) a rectangular hole in the terrain, in WORLD METRES (x, y = the corner with the smaller coordinates): CHeightmap::MakeHole, the call the editor's "Make Holes"
	// brush ends in. Holes are stored in the heightmap's layer-id bitmap, so they are saved with the level and exported to terrain.dat; the engine's terrain gets them through
	// UpdateEngineHole. There was no verb: the only way was the mouse brush (editor_api: terrain.make_holes_tool). Returns {"ok", "cells": [x1,y1,w,h], "holes_in_rect"}.
	if (verb == "bridge.terrain.hole")
	{
		if (tokens.size() < 6)
			throw std::runtime_error("bridge.terrain.hole requires <x> <y> <width> <height> <1|0> (world metres)");
		CEditorImpl* pImpl = EditorImpl();
		CTerrainManager* pTerrainManager = pImpl ? pImpl->GetTerrainManager() : nullptr;
		CHeightmap* pHeightmap = pTerrainManager ? pTerrainManager->GetHeightmap() : nullptr;
		if (!pHeightmap)
			throw std::runtime_error("no heightmap (is a level with terrain open?)");
		const float unit = pHeightmap->GetUnitSize() > 0.0f ? pHeightmap->GetUnitSize() : 1.0f;
		const int x1 = (int)floorf((float)atof(tokens[1].c_str()) / unit);
		const int y1 = (int)floorf((float)atof(tokens[2].c_str()) / unit);
		const int w = (int)ceilf((float)atof(tokens[3].c_str()) / unit);
		const int h = (int)ceilf((float)atof(tokens[4].c_str()) / unit);
		const bool bMake = atoi(tokens[5].c_str()) != 0;
		const int hmW = (int)pHeightmap->GetWidth();
		const int hmH = (int)pHeightmap->GetHeight();
		if (w <= 0 || h <= 0 || x1 < 0 || y1 < 0 || x1 + w > hmW || y1 + h > hmH)
			throw std::runtime_error("the rectangle (in cells " + std::to_string(x1) + "," + std::to_string(y1) + " " + std::to_string(w) + "x" + std::to_string(h) + ") is not inside the heightmap");
		pHeightmap->MakeHole(x1, y1, w, h, bMake);
		if (GetIEditor())
			GetIEditor()->SetModifiedFlag();
		int holes = 0;
		for (int y = y1; y < y1 + h; ++y)
			for (int x = x1; x < x1 + w; ++x)
				holes += pHeightmap->IsHoleAt(x, y) ? 1 : 0;
		std::ostringstream oss;
		oss << "{\"ok\": true, \"cells\": [" << x1 << ", " << y1 << ", " << w << ", " << h << "], \"holes_in_rect\": " << holes << ", \"cells_in_rect\": " << (w * h) << "}";
		return oss.str();
	}

	// bridge.terrain.is_hole <x> <y>   world metres: {"hole": true|false}
	if (verb == "bridge.terrain.is_hole")
	{
		if (tokens.size() < 3)
			throw std::runtime_error("bridge.terrain.is_hole requires <x> <y> (world metres)");
		CEditorImpl* pImpl = EditorImpl();
		CTerrainManager* pTerrainManager = pImpl ? pImpl->GetTerrainManager() : nullptr;
		CHeightmap* pHeightmap = pTerrainManager ? pTerrainManager->GetHeightmap() : nullptr;
		if (!pHeightmap)
			throw std::runtime_error("no heightmap (is a level with terrain open?)");
		const float unit = pHeightmap->GetUnitSize() > 0.0f ? pHeightmap->GetUnitSize() : 1.0f;
		const int x = (int)floorf((float)atof(tokens[1].c_str()) / unit);
		const int y = (int)floorf((float)atof(tokens[2].c_str()) / unit);
		const bool bHole = pHeightmap->IsHoleAt(x, y);
		return std::string("{\"hole\": ") + (bHole ? "true" : "false") + "}";
	}

	if (verb == "bridge.terrain.info")
	{
		CEditorImpl* pImpl = EditorImpl();
		if (!pImpl)
			throw std::runtime_error("the editor is not available");
		CTerrainManager* pTerrainManager = pImpl->GetTerrainManager();
		if (!pTerrainManager)
			throw std::runtime_error("GetTerrainManager() not available");
		CHeightmap* pHeightmap = pTerrainManager->GetHeightmap();
		if (!pHeightmap)
			throw std::runtime_error("GetHeightmap() not available");

		const int hmW = (int)pHeightmap->GetWidth();
		const int hmH = (int)pHeightmap->GetHeight();

		float fMin = 3.0e38f, fMax = -3.0e38f;
		double sum = 0.0;
		for (int y = 0; y < hmH; ++y)
		{
			for (int x = 0; x < hmW; ++x)
			{
				const float h = pHeightmap->GetXY((uint32)x, (uint32)y);
				if (h < fMin) fMin = h;
				if (h > fMax) fMax = h;
				sum += h;
			}
		}
		const double avg = (hmW > 0 && hmH > 0) ? sum / ((double)hmW * (double)hmH) : 0.0;
		if (hmW <= 0 || hmH <= 0) { fMin = 0.0f; fMax = 0.0f; }

		SSectorInfo si;
		pHeightmap->GetSectorsInfo(si);

		CRGBLayer* pRGB = pTerrainManager->GetRGBLayer();
		const int terrainSize = gEnv->p3DEngine ? gEnv->p3DEngine->GetTerrainSize() : 0;
		const int texNodeSize = gEnv->p3DEngine ? gEnv->p3DEngine->GetTerrainTextureNodeSizeMeters() : 0;

		std::ostringstream oss;
		oss << "{\"heightmap\": {\"width\": " << hmW
		    << ", \"height\": " << hmH
		    << ", \"unitSize\": " << pHeightmap->GetUnitSize()
		    << ", \"maxHeightSetting\": " << pHeightmap->GetMaxHeight()
		    << ", \"waterLevel\": " << pHeightmap->GetWaterLevel()
		    << ", \"elevationMin\": " << fMin
		    << ", \"elevationMax\": " << fMax
		    << ", \"elevationAvg\": " << avg << "}"
		    << ", \"sectors\": {\"unitSize\": " << si.unitSize
		    << ", \"sectorSizeMeters\": " << si.sectorSize
		    << ", \"numSectors\": " << si.numSectors
		    << ", \"sectorTexSize\": " << si.sectorTexSize
		    << ", \"surfaceTextureSize\": " << si.surfaceTextureSize << "}"
		    << ", \"rgbLayer\": {\"allocated\": " << ((pRGB && pRGB->IsAllocated()) ? "true" : "false");
		if (pRGB)
		{
			oss << ", \"tileCountX\": " << pRGB->GetTileCountX()
			    << ", \"tileCountY\": " << pRGB->GetTileCountY()
			    << ", \"minRequiredExtend\": " << pRGB->CalcMinRequiredTextureExtend();
		}
		oss << "}"
		    << ", \"engine\": {\"terrainSizeMeters\": " << terrainSize
		    << ", \"terrainTextureNodeSizeMeters\": " << texNodeSize << "}"
		    << ", \"layerCount\": " << pTerrainManager->GetLayerCount() << "}";
		return oss.str();
	}

	// bridge.terrain.list_layers
	// Enumerates CTerrainManager's layer list. "selected" is CLayer::IsSelected(),
	// which is what CTerrainTexturePainter::GetSelectedLayer() (and therefore the
	// ICommandManager terrain.flood_layer / terrain.delete_layer commands) actually
	// act on -- and it does NOT survive a level reload, so check here rather than
	// assuming an earlier selection still holds. "inUse"/"hasTexture" are the two
	// conditions CTerrainLayerTexGen::GenerateSectorTexture skips a layer on.
	if (verb == "bridge.terrain.list_layers")
	{
		CEditorImpl* pImpl = EditorImpl();
		if (!pImpl)
			throw std::runtime_error("the editor is not available");
		CTerrainManager* pTerrainManager = pImpl->GetTerrainManager();
		if (!pTerrainManager)
			throw std::runtime_error("GetTerrainManager() not available");

		std::ostringstream oss;
		oss << "[";
		const int count = pTerrainManager->GetLayerCount();
		for (int i = 0; i < count; ++i)
		{
			CLayer* pLayer = pTerrainManager->GetLayer(i);
			if (i > 0)
				oss << ", ";
			if (!pLayer)
			{
				oss << "{\"index\": " << i << ", \"error\": \"null layer\"}";
				continue;
			}
			oss << "{\"index\": " << i
			    << ", \"id\": " << pLayer->GetCurrentLayerId()
			    << ", \"name\": \"" << JsonEscape(std::string((const char*)pLayer->GetLayerName())) << "\""
			    << ", \"selected\": " << (pLayer->IsSelected() ? "true" : "false")
			    << ", \"inUse\": " << (pLayer->IsInUse() ? "true" : "false")
			    << ", \"hasTexture\": " << (pLayer->HasTexture() ? "true" : "false")
			    << ", \"textureWidth\": " << pLayer->GetTextureWidth()
			    << ", \"textureHeight\": " << pLayer->GetTextureHeight()
			    << ", \"material\": \"" << JsonEscape(std::string((const char*)pLayer->GetMaterialName())) << "\""
			    << ", \"altitudeStart\": " << pLayer->GetLayerStart()
			    << ", \"altitudeEnd\": " << pLayer->GetLayerEnd()
			    << ", \"slopeMinAngle\": " << pLayer->GetLayerMinSlopeAngle()
			    << ", \"slopeMaxAngle\": " << pLayer->GetLayerMaxSlopeAngle()
			    << ", \"surfaceTypeId\": " << pLayer->GetEngineSurfaceTypeId() << "}";
		}
		oss << "]";
		return oss.str();
	}

	// bridge.terrain.set_layer_material <index-or-name> <materialName>
	// The DETAIL material a terrain layer wears close up. Layers created from
	// here start on %ENGINE%/EngineAssets/Materials/material_terrain_default,
	// which is the green-and-black checkerboard -- the baked surface texture
	// still looks right from a distance, so the level can look finished from
	// the air and be a checkerboard under the player's feet. There is no
	// Python or ICommandManager equivalent; CLayer::AssignMaterial is it.
	if (verb == "bridge.terrain.set_layer_material")
	{
		if (tokens.size() < 3)
			throw std::runtime_error("bridge.terrain.set_layer_material requires <index-or-name> <materialName>");

		CEditorImpl* pImpl = EditorImpl();
		if (!pImpl)
			throw std::runtime_error("the editor is not available");
		CTerrainManager* pTerrainManager = pImpl->GetTerrainManager();
		if (!pTerrainManager)
			throw std::runtime_error("no terrain manager -- is a level loaded?");

		const int index = ResolveLayerIndex(pTerrainManager, tokens[1]);
		CLayer* pLayer = pTerrainManager->GetLayer(index);
		if (!pLayer)
			throw std::runtime_error("no terrain layer at index " + std::to_string(index));

		CScopedLogCapture capture;
		pLayer->AssignMaterial(string(tokens[2].c_str()));
		pTerrainManager->ReloadSurfaceTypes();

		std::ostringstream oss;
		oss << "{\"ok\": true, \"index\": " << index
		    << ", \"material\": \"" << JsonEscape(std::string((const char*)pLayer->GetMaterialName())) << "\""
		    << ", \"surfaceTypeId\": " << pLayer->GetEngineSurfaceTypeId()
		    << ", \"log\": \"" << JsonEscape(capture.TakeOutput()) << "\"}";
		return oss.str();
	}

	// bridge.terrain.select_layer <index-or-name>
	// CTerrainManager::SelectLayer is the ONLY way to (re)select an existing
	// layer from here -- ICommandManager/Python expose no such command
	// (terrain.create_layer only auto-selects the layer it just made).
	if (verb == "bridge.terrain.select_layer" || verb == "bridge.terrain.delete_layer")
	{
		const bool bDelete = (verb == "bridge.terrain.delete_layer");
		if (tokens.size() < 2)
			throw std::runtime_error(verb + " requires a layer index or name");

		CEditorImpl* pImpl = EditorImpl();
		if (!pImpl)
			throw std::runtime_error("the editor is not available");
		CTerrainManager* pTerrainManager = pImpl->GetTerrainManager();
		if (!pTerrainManager)
			throw std::runtime_error("GetTerrainManager() not available");

		const int index = ResolveLayerIndex(pTerrainManager, tokens[1]);
		pTerrainManager->SelectLayer(index);

		std::ostringstream oss;
		if (bDelete)
		{
			CLayer* pDoomed = pTerrainManager->GetLayer(index);
			const std::string name = pDoomed ? std::string((const char*)pDoomed->GetLayerName()) : std::string();
			CScopedLogCapture capture;
			pTerrainManager->RemoveSelectedLayer();
			pTerrainManager->InvalidateLayers();
			oss << "{\"ok\": true, \"deleted\": \"" << JsonEscape(name) << "\""
			    << ", \"remainingLayers\": " << pTerrainManager->GetLayerCount()
			    << ", \"log\": \"" << JsonEscape(capture.TakeOutput()) << "\"}";
		}
		else
		{
			CLayer* pSelected = pTerrainManager->GetSelectedLayer();
			oss << "{\"ok\": true, \"index\": " << index
			    << ", \"layer\": \"" << (pSelected ? JsonEscape(std::string((const char*)pSelected->GetLayerName())) : "")
			    << "\"}";
		}
		return oss.str();
	}

	// bridge.terrain.set_layer_params <index-or-name> <altStart> <altEnd> <slopeMinAngle> <slopeMaxAngle>
	// The altitude (metres) and slope (degrees, 0=flat..90=vertical) rules that
	// decide where a layer shows up. These are the same fields
	// CLayer::AutogenLayerMask uses, and they are what
	// bridge.terrain.generate_surface_texture blends layers by -- e.g. give a
	// dirt/rock layer slopeMinAngle 25 so it only appears on steep ground while
	// grass stays on the flats.
	if (verb == "bridge.terrain.set_layer_params")
	{
		if (tokens.size() < 6)
			throw std::runtime_error("bridge.terrain.set_layer_params requires <layer> <altStart> <altEnd> <slopeMinAngle> <slopeMaxAngle>");

		CEditorImpl* pImpl = EditorImpl();
		if (!pImpl)
			throw std::runtime_error("the editor is not available");
		CTerrainManager* pTerrainManager = pImpl->GetTerrainManager();
		if (!pTerrainManager)
			throw std::runtime_error("GetTerrainManager() not available");

		const int index = ResolveLayerIndex(pTerrainManager, tokens[1]);
		CLayer* pLayer = pTerrainManager->GetLayer(index);
		if (!pLayer)
			throw std::runtime_error("no such terrain layer");

		pLayer->SetLayerStart((float)std::atof(tokens[2].c_str()));
		pLayer->SetLayerEnd((float)std::atof(tokens[3].c_str()));
		pLayer->SetLayerMinSlopeAngle((float)std::atof(tokens[4].c_str()));
		pLayer->SetLayerMaxSlopeAngle((float)std::atof(tokens[5].c_str()));
		pLayer->SetInUse(true);
		pTerrainManager->InvalidateLayers();

		std::ostringstream oss;
		oss << "{\"ok\": true, \"index\": " << index
		    << ", \"layer\": \"" << JsonEscape(std::string((const char*)pLayer->GetLayerName())) << "\""
		    << ", \"altitudeStart\": " << pLayer->GetLayerStart()
		    << ", \"altitudeEnd\": " << pLayer->GetLayerEnd()
		    << ", \"slopeMinAngle\": " << pLayer->GetLayerMinSlopeAngle()
		    << ", \"slopeMaxAngle\": " << pLayer->GetLayerMaxSlopeAngle() << "}";
		return oss.str();
	}

	// bridge.terrain.export_texture [resolution] [colorMultiplier]
	// Compiles the baked terrain colour (the RGB layer) into terrain/cover.ctc, the
	// texture the engine actually draws. exporter.export_to_engine does NOT do this
	// (it exports with eExp_AI_All only), and a level made by general.create_level
	// is left with a stub cover.ctc compiled at colour multiplier 0 -- which renders
	// the whole terrain black however bright the layers are. Measured: Ashvale's
	// stub was 131 KB with 5 texture indices; the farm level's is 11 MB, multiplier 8.
	// Same call the terrain dialog's "Export Terrain Texture" makes, minus the dialog.
	//
	// SGameExporterSettings is not SANDBOX_API (its constructor lives in Sandbox.exe),
	// so it is filled field by field in raw storage instead of being constructed.
	if (verb == "bridge.terrain.export_texture")
	{
		const int resolution = tokens.size() > 1 ? std::atoi(tokens[1].c_str()) : 4096;
		const float multiplier = tokens.size() > 2 ? (float)std::atof(tokens[2].c_str()) : 8.0f;
		if (resolution < 512 || resolution > 16384 || (resolution & (resolution - 1)) != 0)
			throw std::runtime_error("resolution must be a power of two in 512..16384");
		if (multiplier <= 0.0f)
			throw std::runtime_error("colorMultiplier must be > 0");

		alignas(SGameExporterSettings) unsigned char storage[sizeof(SGameExporterSettings)] = {};
		SGameExporterSettings& settings = *reinterpret_cast<SGameExporterSettings*>(storage);
		settings.bHiQualityCompression = true;
		settings.bUpdateIndirectLighting = false;
		settings.iExportTexWidth = (UINT)resolution;
		settings.nApplySS = 1;
		settings.fBrMultiplier = multiplier;
		settings.eExportEndian = eLittleEndian;
		settings.exportBinaryXml = false;

		// NOT SetAutoExportMode(true): CGameExporter::Export answers that with SetHiQuality(), which overwrites
		// every field above (16384 px, indirect lighting ON), so the resolution argument did nothing and the
		// indirect-lighting pass ran. That pass spawns SHLighting_<n> threads (SHHeightmapAccessibility.cpp)
		// that the engine never ends and that stay registered by the address of their task object; the next
		// export in the same session can get a task at a reused address, SpawnThread answers "already exists"
		// and the engine's CryFatalError("Error spawning SHLighting__0 thread") stops the editor (measured
		// 2026-10-13, the crash report lists SHLighting_0..5 alive). The terrain dialog's own call is this one,
		// non-automatic, so the settings above are what the export uses.
		CScopedLogCapture capture;
		bool ok = false;
		{
			CGameExporter exporter(settings);
			ok = exporter.Export(eExp_SurfaceTexture | eExp_ReloadTerrain, ".");
		}
		std::ostringstream oss;
		oss << "{\"ok\": " << (ok ? "true" : "false") << ", \"resolution\": " << resolution
		    << ", \"colorMultiplier\": " << multiplier
		    << ", \"log\": \"" << JsonEscape(capture.TakeOutput(true)) << "\"}";
		return oss.str();
	}

	// bridge.terrain.generate_surface_texture [repeat]
	// Bakes every textured layer into the terrain's base colour texture (the
	// CRGBLayer), and writes the matching surface-type id into the heightmap so
	// detail materials and physics agree with what is drawn.
	//
	// This exists because neither of the two paths Sandbox exposes actually
	// works from a script:
	//   * terrain.flood_layer -> CTerrainTexturePainter::Action_Flood, which
	//     returns SILENTLY (no Warning) when CheckSectorPalette fails, and whose
	//     PaintLayer then silently no-ops again if the engine has no terrain
	//     texture yet -- confirmed empirically: it reported success and changed
	//     nothing.
	//   * CTerrainManager::ConvertLayersToRGBLayer, which is interactive (two
	//     modal CQuestionDialogs) and so unusable headlessly.
	// CTerrainTexGen/CTerrainLayerTexGen (the real generator) are the only
	// terrain classes NOT marked SANDBOX_API, so they cannot be linked from a
	// plugin at all -- hence compositing directly here against CRGBLayer,
	// CLayer and CHeightmap, which all are exported.
	//
	// Layer order follows GenerateSectorTexture's own rule: the first textured
	// layer is the unmasked base, later layers blend over it wherever their
	// altitude/slope rules (see set_layer_params) admit them.
	//
	// Pixel formats differ between the two sides and this is easy to get wrong:
	// a CLayer's texture is packed R-in-low-byte, while an RGB layer tile is
	// packed (R<<16)|(G<<8)|B -- see CImagePainter::PaintBrushWithPattern. So R
	// and B are swapped on the way across.
	//
	// Not visible until the level is saved and reloaded (same as
	// generate_height): the engine reads the baked texture from
	// terraintexture.pak at load time.
	if (verb == "bridge.terrain.generate_surface_texture")
	{
		const float repeat = tokens.size() > 1 ? (float)std::atof(tokens[1].c_str()) : 1.0f;
		if (repeat <= 0.0f)
			throw std::runtime_error("repeat must be > 0");

		// Feather widths, in the natural units of each quantity: metres of
		// elevation, and slope RATIO (rise/run, so 0.30 is about 15 degrees
		// around a 30-degree threshold). Exposed rather than hardcoded because
		// the right value depends entirely on the terrain -- a soft landscape
		// wants a wide blend, a cliff face wants a narrow one -- and finding it
		// should not cost a rebuild each time.
		const float altFeather = tokens.size() > 2 ? (float)std::atof(tokens[2].c_str()) : 8.0f;
		const float slopeFeather = tokens.size() > 3 ? (float)std::atof(tokens[3].c_str()) : 0.30f;

		CEditorImpl* pImpl = EditorImpl();
		if (!pImpl)
			throw std::runtime_error("the editor is not available");
		CTerrainManager* pTerrainManager = pImpl->GetTerrainManager();
		if (!pTerrainManager)
			throw std::runtime_error("GetTerrainManager() not available");
		CHeightmap* pHeightmap = pTerrainManager->GetHeightmap();
		if (!pHeightmap)
			throw std::runtime_error("GetHeightmap() not available");
		CRGBLayer* pRGB = pTerrainManager->GetRGBLayer();
		if (!pRGB)
			throw std::runtime_error("GetRGBLayer() not available");

		CScopedLogCapture capture;

		// Gather every layer that actually has pixels to contribute.
		std::vector<CLayer*> layers;
		const int layerCount = pTerrainManager->GetLayerCount();
		for (int i = 0; i < layerCount; ++i)
		{
			CLayer* pLayer = pTerrainManager->GetLayer(i);
			if (!pLayer)
				continue;
			pLayer->PrecacheTexture();
			if (!pLayer->HasTexture())
				continue;
			// A layer skipped by IsInUse() would silently contribute nothing;
			// having a texture is taken as the intent to use it.
			pLayer->SetInUse(true);
			layers.push_back(pLayer);
		}
		if (layers.empty())
			throw std::runtime_error("no terrain layer has a texture -- set one first with bridge.terrain.set_layer_texture");

		if (!pRGB->IsAllocated())
			pRGB->AllocateTiles(4, 4, 512);

		const uint32 tileCountX = pRGB->GetTileCountX();
		const uint32 tileCountY = pRGB->GetTileCountY();
		if (tileCountX == 0 || tileCountY == 0)
			throw std::runtime_error("RGB layer has no tiles");

		const int hmW = (int)pHeightmap->GetWidth();
		const int hmH = (int)pHeightmap->GetHeight();
		if (hmW <= 0 || hmH <= 0)
			throw std::runtime_error("heightmap is empty");
		const float unitSize = pHeightmap->GetUnitSize() > 0.0f ? pHeightmap->GetUnitSize() : 1.0f;

		// Precompute slope per heightmap cell. Doing this per surface-texture
		// pixel via GetAccurateSlope would mean ~8 bilinear samples for each of
		// several million pixels and would blow the bridge's command timeout;
		// this is the same quantity (max-min over the 8 neighbours, halved, per
		// metre) read straight out of the array instead.
		std::vector<float> slopeGrid((size_t)hmW * (size_t)hmH, 0.0f);
		for (int y = 0; y < hmH; ++y)
		{
			for (int x = 0; x < hmW; ++x)
			{
				float mn = 3.0e38f, mx = -3.0e38f;
				for (int dy = -1; dy <= 1; ++dy)
				{
					for (int dx = -1; dx <= 1; ++dx)
					{
						if (dx == 0 && dy == 0)
							continue;
						// Clamp rather than CHeightmap::GetSafeXY, which returns 0
						// (not the nearest value) outside the map -- that would read
						// as a cliff all the way round the border and paint a false
						// steep-slope stripe along every edge of the terrain.
						int nx = x + dx;
						int ny = y + dy;
						if (nx < 0) nx = 0;
						if (nx >= hmW) nx = hmW - 1;
						if (ny < 0) ny = 0;
						if (ny >= hmH) ny = hmH - 1;
						const float h = pHeightmap->GetXY((uint32)nx, (uint32)ny);
						if (h < mn) mn = h;
						if (h > mx) mx = h;
					}
				}
				slopeGrid[(size_t)x + (size_t)y * (size_t)hmW] = (mx - mn) * 0.5f / unitSize;
			}
		}

		// Per-layer admission rules, resolved once up front.
		const size_t numLayers = layers.size();
		std::vector<float> altStart(numLayers), altEnd(numLayers);
		std::vector<float> slopeMin(numLayers), slopeMax(numLayers);
		std::vector<uint32> layerIds(numLayers);
		std::vector<const uint32*> texData(numLayers);
		std::vector<uint32> texW(numLayers), texH(numLayers);
		for (size_t k = 0; k < numLayers; ++k)
		{
			CLayer* pLayer = layers[k];
			altStart[k] = pLayer->GetLayerStart();
			altEnd[k] = pLayer->GetLayerEnd();
			// CLayer::AutogenLayerMask's own 0..90deg -> slope-ratio mapping.
			const float minAng = pLayer->GetLayerMinSlopeAngle();
			const float maxAng = pLayer->GetLayerMaxSlopeAngle();
			slopeMin[k] = (float)tan((double)minAng / 90.1 * 3.14159265358979 / 2.0);
			slopeMax[k] = (float)tan((double)maxAng / 90.1 * 3.14159265358979 / 2.0);
			layerIds[k] = pLayer->GetOrRequestLayerId();
			texData[k] = pLayer->GetTexture().GetData();
			texW[k] = (uint32)pLayer->GetTextureWidth();
			texH[k] = (uint32)pLayer->GetTextureHeight();
			if (!texData[k] || texW[k] == 0 || texH[k] == 0)
				throw std::runtime_error("layer texture became invalid while compositing");
		}

		// Dominant layer per heightmap cell, for the surface-type pass.
		std::vector<uint8> dominant((size_t)hmW * (size_t)hmH, 0);

		uint64 pixelsWritten = 0;
		// Measured coverage, not claimed coverage. "layersUsed" only said which
		// layers were considered; a layer whose mask evaluates to ~0 everywhere
		// still appeared in that list while contributing nothing visible. These
		// counters are what makes that failure impossible to miss.
		std::vector<uint64> weightedPixels(numLayers, 0);
		std::vector<uint64> dominantPixels(numLayers, 0);
		double sumR = 0.0, sumG = 0.0, sumB = 0.0;
		for (uint32 ty = 0; ty < tileCountY; ++ty)
		{
			for (uint32 tx = 0; tx < tileCountX; ++tx)
			{
				CImageEx* pTile = pRGB->GetTileImage((int)tx, (int)ty, true);
				if (!pTile || !pTile->IsValid())
					continue;
				const int tw = pTile->GetWidth();
				const int th = pTile->GetHeight();
				if (tw <= 1 || th <= 1)
					continue;
				// Tiles overlap by one pixel -- CRGBLayer's own convention
				// (dwTileSizeReduced = dwTileSize - 1 in PaintBrushWithPatternTiled),
				// there for bilinear filtering across tile seams.
				const uint32 reducedX = (uint32)(tw - 1);
				const uint32 reducedY = (uint32)(th - 1);
				const float fullX = (float)(reducedX * tileCountX);
				const float fullY = (float)(reducedY * tileCountY);

				for (int py = 0; py < th; ++py)
				{
					const uint32 gy = ty * reducedY + (uint32)py;
					const float v = fullY > 0.0f ? (float)gy / fullY : 0.0f;
					int hy = (int)(v * (float)hmH + 0.5f);
					if (hy < 0) hy = 0;
					if (hy >= hmH) hy = hmH - 1;

					for (int px = 0; px < tw; ++px)
					{
						const uint32 gx = tx * reducedX + (uint32)px;
						const float u = fullX > 0.0f ? (float)gx / fullX : 0.0f;
						int hx = (int)(u * (float)hmW + 0.5f);
						if (hx < 0) hx = 0;
						if (hx >= hmW) hx = hmW - 1;

						const size_t hIdx = (size_t)hx + (size_t)hy * (size_t)hmW;
						const float elev = pHeightmap->GetXY((uint32)hx, (uint32)hy);
						const float slope = slopeGrid[hIdx];

						float accR = 0.0f, accG = 0.0f, accB = 0.0f;
						float bestWeight = -1.0f;
						uint8 bestLayer = 0;

						for (size_t k = 0; k < numLayers; ++k)
						{
							float w;
							if (k == 0)
							{
								// Base layer: unmasked, exactly as
								// GenerateSectorTexture draws its first layer.
								w = 1.0f;
							}
							else
							{
								w = SmoothBand(elev, altStart[k], altEnd[k], altFeather)
								    * SmoothBand(slope, slopeMin[k], slopeMax[k], slopeFeather);
							}
							if (w <= 0.0f)
								continue;

							// Modulo rather than an &(size-1) mask: CLayer only
							// accepts power-of-2 textures today, but a mask would
							// silently read garbage if that ever stops holding.
							const uint32 sx = (uint32)((float)gx * repeat) % texW[k];
							const uint32 sy = (uint32)((float)gy * repeat) % texH[k];
							const uint32 texel = texData[k][sx + sy * texW[k]];
							// CLayer texture is R-in-low-byte.
							const float r = (float)(texel & 0xFF);
							const float g = (float)((texel >> 8) & 0xFF);
							const float b = (float)((texel >> 16) & 0xFF);

							accR = accR * (1.0f - w) + r * w;
							accG = accG * (1.0f - w) + g * w;
							accB = accB * (1.0f - w) + b * w;

							if (w >= 0.05f)
								++weightedPixels[k];

							// Which layer owns this cell's SURFACE TYPE (detail
							// material / physics), as opposed to its colour. The
							// base layer can't simply win on weight -- it is always
							// 1.0 by definition -- so a layer painted over the top
							// takes ownership once it is the one actually being
							// seen there.
							if (k == 0)
							{
								bestWeight = w;
								bestLayer = 0;
							}
							else if (w >= 0.5f)
							{
								bestWeight = w;
								bestLayer = (uint8)k;
							}
						}

						uint32 iR = (uint32)(accR + 0.5f);
						uint32 iG = (uint32)(accG + 0.5f);
						uint32 iB = (uint32)(accB + 0.5f);
						if (iR > 255) iR = 255;
						if (iG > 255) iG = 255;
						if (iB > 255) iB = 255;
						// RGB layer tile is (R<<16)|(G<<8)|B.
						pTile->ValueAt(px, py) = (iR << 16) | (iG << 8) | iB;
						++pixelsWritten;
						sumR += iR;
						sumG += iG;
						sumB += iB;

						if (bestWeight > 0.0f)
						{
							dominant[hIdx] = bestLayer;
							++dominantPixels[bestLayer];
						}
					}
				}
			}
		}

		// Surface types, so detail materials / physics match the painted colour.
		for (int y = 0; y < hmH; ++y)
		{
			for (int x = 0; x < hmW; ++x)
			{
				const size_t hIdx = (size_t)x + (size_t)y * (size_t)hmW;
				SSurfaceTypeItem st;
				st = layerIds[dominant[hIdx]];
				pHeightmap->SetLayerIdAt(x, y, st);
			}
		}

		pTerrainManager->InvalidateLayers();
		pHeightmap->UpdateEngineTerrain(false, true);
		pTerrainManager->SetModified();

		std::ostringstream oss;
		oss << "{\"ok\": true, \"layersUsed\": [";
		for (size_t k = 0; k < numLayers; ++k)
		{
			if (k > 0)
				oss << ", ";
			const double pct = pixelsWritten ? (100.0 * (double)weightedPixels[k] / (double)pixelsWritten) : 0.0;
			const double dom = pixelsWritten ? (100.0 * (double)dominantPixels[k] / (double)pixelsWritten) : 0.0;
			oss << "{\"name\": \"" << JsonEscape(std::string((const char*)layers[k]->GetLayerName()))
			    << "\", \"layerId\": " << layerIds[k]
			    << ", \"role\": \"" << (k == 0 ? "base" : "blended") << "\""
			    << ", \"altitudeBand\": [" << altStart[k] << ", " << altEnd[k] << "]"
			    << ", \"slopeBandRatio\": [" << slopeMin[k] << ", " << slopeMax[k] << "]"
			    // coveragePercent near zero on a non-base layer means its mask
			    // never opened -- the layer is configured, listed, and invisible.
			    << ", \"coveragePercent\": " << pct
			    << ", \"dominantPercent\": " << dom << "}";
		}
		oss << "], \"tiles\": " << (tileCountX * tileCountY)
		    << ", \"pixelsWritten\": " << pixelsWritten
		    << ", \"meanRGB\": ["
		    << (pixelsWritten ? sumR / (double)pixelsWritten : 0.0) << ", "
		    << (pixelsWritten ? sumG / (double)pixelsWritten : 0.0) << ", "
		    << (pixelsWritten ? sumB / (double)pixelsWritten : 0.0) << "]"
		    << ", \"repeat\": " << repeat
		    << ", \"altFeather\": " << altFeather
		    << ", \"slopeFeather\": " << slopeFeather
		    << ", \"note\": \"save the level and reload it to see this\""
		    << ", \"log\": \"" << JsonEscape(capture.TakeOutput()) << "\"}";
		return oss.str();
	}

	// bridge.camera.get
	// Editor viewport camera as position + the three basis vectors, plus the
	// yaw/pitch a human would recognise. CryEngine's convention is +Y forward,
	// +Z up, +X right, which is why "forward" is column 1 and not column 2.
	if (verb == "bridge.camera.get")
	{
		if (!GetIEditor())
			throw std::runtime_error("GetIEditor() not available");
		CViewport* pView = GetIEditor()->GetActiveView();
		if (!pView)
			throw std::runtime_error("no active viewport -- is a level open and a viewport focused?");

		const Matrix34& tm = pView->GetViewTM();
		const Vec3 pos = tm.GetTranslation();
		const Vec3 right = tm.GetColumn0();
		const Vec3 fwd = tm.GetColumn1();
		const Vec3 up = tm.GetColumn2();

		const float yaw = RAD2DEG(atan2_tpl(-fwd.x, fwd.y));
		const float pitch = RAD2DEG(asin_tpl(clamp_tpl(fwd.z, -1.0f, 1.0f)));

		int vw = 0, vh = 0;
		pView->GetDimensions(&vw, &vh);

		std::ostringstream oss;
		oss << "{\"pos\": [" << pos.x << ", " << pos.y << ", " << pos.z << "]"
		    << ", \"forward\": [" << fwd.x << ", " << fwd.y << ", " << fwd.z << "]"
		    << ", \"right\": [" << right.x << ", " << right.y << ", " << right.z << "]"
		    << ", \"up\": [" << up.x << ", " << up.y << ", " << up.z << "]"
		    << ", \"yawDeg\": " << yaw << ", \"pitchDeg\": " << pitch
		    << ", \"viewport\": [" << vw << ", " << vh << "]}";
		return oss.str();
	}

	// bridge.camera.look_at <eyeX> <eyeY> <eyeZ> <targetX> <targetY> <targetZ>
	// Put the camera at eye and aim it at target. Far easier to reason about
	// than yaw/pitch when the goal is "show me that thing" -- which is almost
	// always the goal when inspecting your own work.
	//
	// bridge.camera.set <x> <y> <z> [yawDeg] [pitchDeg]
	// yaw 0 looks along +Y, increasing yaw turns right; pitch is positive
	// looking up.
	if (verb == "bridge.camera.look_at" || verb == "bridge.camera.set")
	{
		const bool bLookAt = (verb == "bridge.camera.look_at");
		if (bLookAt && tokens.size() < 7)
			throw std::runtime_error("bridge.camera.look_at requires <eyeX> <eyeY> <eyeZ> <targetX> <targetY> <targetZ>");
		if (!bLookAt && tokens.size() < 4)
			throw std::runtime_error("bridge.camera.set requires <x> <y> <z> [yawDeg] [pitchDeg]");

		if (!GetIEditor())
			throw std::runtime_error("GetIEditor() not available");
		CViewport* pView = GetIEditor()->GetActiveView();
		if (!pView)
			throw std::runtime_error("no active viewport -- is a level open and a viewport focused?");

		const Vec3 eye((float)std::atof(tokens[1].c_str()),
		               (float)std::atof(tokens[2].c_str()),
		               (float)std::atof(tokens[3].c_str()));

		Vec3 fwd;
		if (bLookAt)
		{
			const Vec3 target((float)std::atof(tokens[4].c_str()),
			                  (float)std::atof(tokens[5].c_str()),
			                  (float)std::atof(tokens[6].c_str()));
			fwd = target - eye;
			if (fwd.GetLength() < 0.0001f)
				throw std::runtime_error("eye and target are the same point");
			fwd.Normalize();
		}
		else
		{
			const float yaw = DEG2RAD(tokens.size() > 4 ? (float)std::atof(tokens[4].c_str()) : 0.0f);
			const float pitch = DEG2RAD(tokens.size() > 5 ? (float)std::atof(tokens[5].c_str()) : 0.0f);
			const float cp = cos_tpl(pitch);
			fwd = Vec3(-sin_tpl(yaw) * cp, cos_tpl(yaw) * cp, sin_tpl(pitch));
			fwd.Normalize();
		}

		// Guard the degenerate straight-up/straight-down case, where the world
		// up axis and the view direction are parallel and the cross product
		// collapses to zero.
		Vec3 worldUp(0.0f, 0.0f, 1.0f);
		if (fabs_tpl(fwd.z) > 0.9999f)
			worldUp = Vec3(0.0f, 1.0f, 0.0f);

		Vec3 right = fwd.Cross(worldUp);
		if (right.GetLength() < 0.0001f)
			right = Vec3(1.0f, 0.0f, 0.0f);
		right.Normalize();
		// Right-handed with +Y forward: up = right x forward.
		Vec3 up = right.Cross(fwd);
		up.Normalize();

		Matrix34 tm;
		tm.SetFromVectors(right, fwd, up, eye);
		pView->SetViewTM(tm);

		std::ostringstream oss;
		oss << "{\"ok\": true, \"pos\": [" << eye.x << ", " << eye.y << ", " << eye.z << "]"
		    << ", \"forward\": [" << fwd.x << ", " << fwd.y << ", " << fwd.z << "]}";
		return oss.str();
	}

	// bridge.env.list [filter]
	// Every time-of-day variable -- sun colour and intensity, fog, HDR
	// exposure, and the rest -- with its current value. This is the Environment
	// Editor's entire surface, enumerated from ITimeOfDay rather than clicked
	// through, which is what makes lighting adjustable from a script at all.
	// Values are 1 float, or 3 for a colour.
	if (verb == "bridge.env.list")
	{
		if (!gEnv || !gEnv->p3DEngine)
			throw std::runtime_error("3DEngine not available");
		ITimeOfDay* pTod = gEnv->p3DEngine->GetTimeOfDay();
		if (!pTod)
			throw std::runtime_error("GetTimeOfDay() not available");

		std::string filter = tokens.size() > 1 ? tokens[1] : std::string();
		for (char& c : filter) c = (char)tolower((unsigned char)c);

		ITimeOfDay::IVariables& vars = pTod->GetCurrentPreset().GetVariables();
		const int count = vars.GetVariableCount();

		std::ostringstream oss;
		oss << "{\"time\": " << pTod->GetTime()
		    << ", \"preset\": \"" << JsonEscape(pTod->GetCurrentPresetName() ? pTod->GetCurrentPresetName() : "")
		    << "\", \"variables\": [";
		bool first = true;
		for (int i = 0; i < count; ++i)
		{
			ITimeOfDay::SVariableInfo info;
			ZeroStruct(info);
			if (!vars.GetVariableInfo(i, info))
				continue;

			const std::string name = info.szName ? info.szName : "";
			const std::string group = info.szGroup ? info.szGroup : "";
			if (!filter.empty())
			{
				std::string hay = name + " " + group;
				for (char& c : hay) c = (char)tolower((unsigned char)c);
				if (hay.find(filter) == std::string::npos)
					continue;
			}

			if (!first)
				oss << ", ";
			first = false;
			const bool bColor = (info.type == ITimeOfDay::TYPE_COLOR);
			oss << "{\"index\": " << i
			    << ", \"name\": \"" << JsonEscape(name) << "\""
			    << ", \"group\": \"" << JsonEscape(group) << "\""
			    << ", \"type\": \"" << (bColor ? "color" : "float") << "\""
			    << ", \"value\": ";
			if (bColor)
				oss << "[" << info.fValue[0] << ", " << info.fValue[1] << ", " << info.fValue[2] << "]";
			else
				oss << info.fValue[0];
			oss << "}";
		}
		oss << "]}";
		return oss.str();
	}

	// bridge.env.set <name-or-index> <v0> [v1] [v2]
	// Sets a time-of-day variable and forces the update, so the change is
	// visible immediately -- no level reload, unlike terrain edits.
	if (verb == "bridge.env.set")
	{
		if (tokens.size() < 3)
			throw std::runtime_error("bridge.env.set requires <name-or-index> <v0> [v1] [v2]");
		if (!gEnv || !gEnv->p3DEngine)
			throw std::runtime_error("3DEngine not available");
		ITimeOfDay* pTod = gEnv->p3DEngine->GetTimeOfDay();
		if (!pTod)
			throw std::runtime_error("GetTimeOfDay() not available");

		ITimeOfDay::IVariables& vars = pTod->GetCurrentPreset().GetVariables();
		const int count = vars.GetVariableCount();

		// Accept an index or an exact (case-insensitive) variable name.
		int index = -1;
		char* endptr = nullptr;
		const long parsed = std::strtol(tokens[1].c_str(), &endptr, 10);
		if (endptr != tokens[1].c_str() && *endptr == '\0')
		{
			index = (int)parsed;
		}
		else
		{
			std::string want = tokens[1];
			for (char& c : want) c = (char)tolower((unsigned char)c);
			for (int i = 0; i < count; ++i)
			{
				ITimeOfDay::SVariableInfo info;
				ZeroStruct(info);
				if (!vars.GetVariableInfo(i, info) || !info.szName)
					continue;
				std::string have = info.szName;
				for (char& c : have) c = (char)tolower((unsigned char)c);
				if (have == want)
				{
					index = i;
					break;
				}
			}
		}
		if (index < 0 || index >= count)
			throw std::runtime_error("no such time-of-day variable: " + tokens[1]);

		ITimeOfDay::SVariableInfo before;
		ZeroStruct(before);
		vars.GetVariableInfo(index, before);

		float v[3];
		v[0] = (float)std::atof(tokens[2].c_str());
		// A single value given for a colour sets all three channels, which is
		// what you almost always want for brightness-style adjustments.
		v[1] = tokens.size() > 3 ? (float)std::atof(tokens[3].c_str()) : v[0];
		v[2] = tokens.size() > 4 ? (float)std::atof(tokens[4].c_str()) : v[1];

		// Time-of-day variables are SPLINES over the day, not plain values.
		// SetVariableValue writes only the instantaneous value, and the very
		// next Update(bInterpolate=true) re-evaluates the spline for the
		// current time and overwrites it -- so setting a variable that way
		// reports success and changes nothing (confirmed empirically; see
		// CTimeOfDay::Update -> m_pCurrentPreset->Update(normalizedTime)).
		//
		// Editing every key on the spline is what actually sticks. It also
		// makes the value constant across the whole day, which is the
		// predictable thing to want when setting a look from a script rather
		// than authoring a day cycle. A spline with no keys yet gets one
		// inserted at the current time instead.
		const bool bColor = (before.type == ITimeOfDay::TYPE_COLOR);
		const int splineCount = bColor ? 3 : 1;
		// Curves are keyed in hours (EnvironmentPreset.cpp:18), not 0..1. It was GetTime()/24 here, which would
		// have keyed 12:00 at 00:30 -- never hit in practice, since every variable has at least two keys.
		// NOTE: this verb flattens the whole curve to one value; to author a day cycle use bridge.env.keys.
		const float normalizedTime = pTod->GetTime();
		int keysWritten = 0;

		for (int s = 0; s < splineCount; ++s)
		{
			const uint n = vars.GetSplineKeysCount(index, s);
			if (n > 0)
			{
				std::vector<SBezierKey> keys((size_t)n);
				if (vars.GetSplineKeysForVar(index, s, keys.data(), n))
				{
					for (SBezierKey& k : keys)
						k.m_controlPoint.m_value = v[s];
					if (vars.SetSplineKeysForVar(index, s, keys.data(), n))
						keysWritten += (int)n;
					continue;
				}
			}
			if (vars.UpdateSplineKeyForVar(index, s, normalizedTime, v[s]))
				++keysWritten;
		}

		pTod->Update(true, true);

		// Read back rather than echoing what was asked for -- the whole reason
		// this handler had to be rewritten is that the previous version
		// reported the requested value and the engine had ignored it.
		ITimeOfDay::SVariableInfo after;
		ZeroStruct(after);
		vars.GetVariableInfo(index, after);

		std::ostringstream oss;
		oss << "{\"ok\": true, \"index\": " << index
		    << ", \"name\": \"" << JsonEscape(before.szName ? before.szName : "") << "\""
		    << ", \"type\": \"" << (bColor ? "color" : "float") << "\""
		    << ", \"requested\": ";
		if (bColor)
			oss << "[" << v[0] << ", " << v[1] << ", " << v[2] << "]";
		else
			oss << v[0];
		oss << ", \"was\": ";
		if (bColor)
			oss << "[" << before.fValue[0] << ", " << before.fValue[1] << ", " << before.fValue[2] << "]";
		else
			oss << before.fValue[0];
		oss << ", \"now\": ";
		if (bColor)
			oss << "[" << after.fValue[0] << ", " << after.fValue[1] << ", " << after.fValue[2] << "]";
		else
			oss << after.fValue[0];
		// If "now" does not match "requested", the engine rejected or clamped
		// it -- surfaced rather than hidden behind an unconditional "ok".
		oss << ", \"keysWritten\": " << keysWritten
		    << ", \"applied\": " << ((fabs_tpl(after.fValue[0] - v[0]) < 0.001f) ? "true" : "false")
		    << "}";
		return oss.str();
	}

	// bridge.env.time [hour]
	// Reads or sets the hour of day (0-24, fractional allowed).
	if (verb == "bridge.env.time")
	{
		if (!gEnv || !gEnv->p3DEngine)
			throw std::runtime_error("3DEngine not available");
		ITimeOfDay* pTod = gEnv->p3DEngine->GetTimeOfDay();
		if (!pTod)
			throw std::runtime_error("GetTimeOfDay() not available");

		if (tokens.size() > 1)
		{
			pTod->SetTime((float)std::atof(tokens[1].c_str()), true);
			pTod->Update(true, true);
		}

		std::ostringstream oss;
		oss << "{\"ok\": true, \"time\": " << pTod->GetTime() << "}";
		return oss.str();
	}

	// Environment presets, the way the editor's own tools handle them (2026-09-26). Each verb is one
	// ITimeOfDay / asset-type call that the Asset Browser, Level Settings or the Environment Editor makes:
	//   bridge.env.presets                          the level's preset list (current, default)
	//   bridge.env.new_asset <path.env>             Asset Browser > New > Environment (a copy of default.env + .cryasset)
	//   bridge.env.load <path.env>                  add a preset to the level (Level Settings / "Set as Default" first step)
	//   bridge.env.default <name>                   "Set as Default for this Level"
	//   bridge.env.current <name>                   switch the current preset (what Environment:PresetSwitch does)
	//   bridge.env.remove <name>                    remove a preset from the level's list
	//   bridge.env.getkeys <var>                    a variable's curve: keys per spline, times in HOURS
	//   bridge.env.keys <var> <h:v[/v/v],...> [smooth|linear|step|zero]
	//                                               replace the whole curve (the curve editor's keys; drops stray ones)
	//   bridge.env.save [name]                      the Environment Editor's Save (ITimeOfDay::SavePreset)
	// Curves are keyed in hours (SAnimTime seconds, 24 per day: EnvironmentPreset.cpp:18).
	if (verb.compare(0, 11, "bridge.env.") == 0)
	{
		if (!gEnv || !gEnv->p3DEngine || !gEnv->p3DEngine->GetTimeOfDay())
			throw std::runtime_error("time of day not available");
		ITimeOfDay* pTod = gEnv->p3DEngine->GetTimeOfDay();
		const std::string what = verb.substr(11);
		auto Arg = [&](size_t i) -> std::string
		{
			if (tokens.size() <= i)
				throw std::runtime_error(verb + ": missing argument " + std::to_string(i));
			return tokens[i];
		};
		auto PresetsJson = [&]()
		{
			std::vector<ITimeOfDay::SPresetInfo> infos((size_t)max(0, pTod->GetPresetCount()));
			std::ostringstream oss;
			oss << "[";
			if (!infos.empty() && pTod->GetPresetsInfos(infos.data(), (unsigned int)infos.size()))
			{
				for (size_t i = 0; i < infos.size(); ++i)
					oss << (i ? ", " : "") << "{\"name\": \"" << JsonEscape(infos[i].szName ? infos[i].szName : "") << "\""
					    << ", \"current\": " << (infos[i].isCurrent ? "true" : "false")
					    << ", \"default\": " << (infos[i].isDefault ? "true" : "false") << "}";
			}
			oss << "]";
			return oss.str();
		};
		auto Reply = [&](const std::string& action, bool ok)
		{
			if (!ok)
				throw std::runtime_error(verb + " failed (the engine returned false)");
			std::ostringstream oss;
			oss << "{\"ok\": true, \"verb\": \"" << action << "\", \"current\": \"" << JsonEscape(pTod->GetCurrentPresetName() ? pTod->GetCurrentPresetName() : "")
			    << "\", \"default\": \"" << JsonEscape(pTod->GetDefaultPresetName() ? pTod->GetDefaultPresetName() : "")
			    << "\", \"presets\": " << PresetsJson() << "}";
			return oss.str();
		};
		auto FindVar = [&](ITimeOfDay::IVariables& vars, const std::string& token) -> int
		{
			char* endptr = nullptr;
			const long parsed = std::strtol(token.c_str(), &endptr, 10);
			if (endptr != token.c_str() && *endptr == '\0')
				return (parsed >= 0 && parsed < vars.GetVariableCount()) ? (int)parsed : -1;
			std::string want = token;
			for (char& ch : want) ch = (char)tolower((unsigned char)ch);
			for (char& ch : want) if (ch == '_') ch = ' ';     // names have spaces; the command line cannot
			for (int i = 0; i < vars.GetVariableCount(); ++i)
			{
				ITimeOfDay::SVariableInfo info;
				ZeroStruct(info);
				if (!vars.GetVariableInfo(i, info) || !info.szName)
					continue;
				std::string have = info.szName;
				for (char& ch : have) ch = (char)tolower((unsigned char)ch);
				if (have == want)
					return i;
			}
			return -1;
		};

		if (what == "presets")
			return Reply("presets", true);
		if (what == "load")
			return Reply("load", pTod->LoadPreset(Arg(1).c_str()));
		if (what == "default")
			return Reply("default", pTod->SetDefaultPreset(Arg(1).c_str()));
		if (what == "current")
			return Reply("current", pTod->SetCurrentPreset(Arg(1).c_str()));
		if (what == "remove")
			return Reply("remove", pTod->RemovePreset(Arg(1).c_str()));
		if (what == "save")
		{
			const std::string name = tokens.size() > 1 ? tokens[1] : std::string(pTod->GetCurrentPresetName() ? pTod->GetCurrentPresetName() : "");
			return Reply("save " + JsonEscape(name), pTod->SavePreset(name.c_str()));
		}
		if (what == "new_asset")
		{
			CAssetManager* pAssets = CAssetManager::GetInstance();
			CAssetType* pType = pAssets ? pAssets->FindAssetType("Environment") : nullptr;
			if (!pType)
				throw std::runtime_error("the Environment asset type is not registered");
			const std::string cryasset = Arg(1) + ".cryasset";
			return Reply("new_asset " + JsonEscape(cryasset), pType->Create(cryasset.c_str()));
		}

		ITimeOfDay::IVariables& vars = pTod->GetCurrentPreset().GetVariables();
		if (what == "getkeys" || what == "keys")
		{
			const int index = FindVar(vars, Arg(1));
			if (index < 0)
				throw std::runtime_error("no such time-of-day variable: " + tokens[1]);
			ITimeOfDay::SVariableInfo info;
			ZeroStruct(info);
			vars.GetVariableInfo(index, info);
			const int splines = (info.type == ITimeOfDay::TYPE_COLOR) ? 3 : 1;

			if (what == "keys")
			{
				// "h:v,h:v" (or "h:r/g/b,..." for a colour); an optional tangent type for every key.
				SBezierControlPoint::ETangentType tangent = SBezierControlPoint::ETangentType::Smooth;
				if (tokens.size() > 3)
				{
					const std::string t = tokens[3];
					if (t == "linear") tangent = SBezierControlPoint::ETangentType::Linear;
					else if (t == "step") tangent = SBezierControlPoint::ETangentType::Step;
					else if (t == "zero") tangent = SBezierControlPoint::ETangentType::Zero;
					else if (t != "smooth") throw std::runtime_error("tangent must be smooth, linear, step or zero");
				}
				std::vector<SBezierKey> keys[3];
				std::stringstream list(Arg(2));
				std::string item;
				while (std::getline(list, item, ','))
				{
					const size_t colon = item.find(':');
					if (colon == std::string::npos)
						throw std::runtime_error("key '" + item + "' is not hour:value");
					const float hour = (float)std::atof(item.substr(0, colon).c_str());
					if (hour < 0.0f || hour > 24.0f)
						throw std::runtime_error("key hour out of 0..24: " + item);
					float v[3];
					std::stringstream vals(item.substr(colon + 1));
					std::string part;
					int n = 0;
					while (n < 3 && std::getline(vals, part, '/'))
						v[n++] = (float)std::atof(part.c_str());
					if (n == 0)
						throw std::runtime_error("key '" + item + "' has no value");
					for (int s = n; s < 3; ++s)
						v[s] = v[n - 1];
					for (int s = 0; s < splines; ++s)
					{
						SBezierKey key;
						key.m_time = SAnimTime(hour);
						// A float's info carries its min and max (EnvironmentPreset.cpp:449-453); keep keys inside them,
						// as the Environment Editor's spin boxes do. Colours carry r/g/b there instead.
						key.m_controlPoint.m_value = (splines == 1) ? clamp_tpl(v[s], info.fValue[1], info.fValue[2]) : v[s];
						key.m_controlPoint.m_inTangentType = tangent;
						key.m_controlPoint.m_outTangentType = tangent;
						keys[s].push_back(key);
					}
				}
				for (int s = 0; s < splines; ++s)
				{
					std::sort(keys[s].begin(), keys[s].end(), [](const SBezierKey& a, const SBezierKey& b) { return a.m_time < b.m_time; });
					if (keys[s].size() < 2 || !vars.SetSplineKeysForVar(index, s, keys[s].data(), (unsigned int)keys[s].size()))
						throw std::runtime_error("the engine refused the keys (need at least two)");
				}
				pTod->Update(true, true);
			}

			std::ostringstream oss;
			oss << "{\"ok\": true, \"index\": " << index << ", \"name\": \"" << JsonEscape(info.szName ? info.szName : "")
			    << "\", \"preset\": \"" << JsonEscape(pTod->GetCurrentPresetName() ? pTod->GetCurrentPresetName() : "")
			    << "\", \"time\": " << pTod->GetTime() << ", \"splines\": [";
			for (int s = 0; s < splines; ++s)
			{
				const uint n = vars.GetSplineKeysCount(index, s);
				std::vector<SBezierKey> keys((size_t)n);
				if (n)
					vars.GetSplineKeysForVar(index, s, keys.data(), n);
				oss << (s ? ", " : "") << "[";
				for (uint k = 0; k < n; ++k)
					oss << (k ? ", " : "") << "[" << keys[k].m_time.ToFloat() << ", " << keys[k].m_controlPoint.m_value << ", "
					    << (int)keys[k].m_controlPoint.m_outTangentType << "]";
				oss << "]";
			}
			ITimeOfDay::SVariableInfo now;
			ZeroStruct(now);
			vars.GetVariableInfo(index, now);
			oss << "], \"valueNow\": [" << now.fValue[0];
			if (splines == 3)
				oss << ", " << now.fValue[1] << ", " << now.fValue[2];
			oss << "]}";
			return oss.str();
		}
		// bridge.env.list / set / time are handled above; anything else is a typo.
		throw std::runtime_error("unknown env verb: " + verb + " (list, set, time, presets, new_asset, load, default, current, "
		                         "remove, getkeys, keys, save)");
	}

	// bridge.object.list [nameFilter]
	// Every object in the level with its transform and state. The Python
	// object module can do some of this, but it answers with names only --
	// this gives the transform in the same call, which is what makes it
	// possible to reason about a level without a round-trip per object.
	if (verb == "bridge.object.list")
	{
		if (!GetIEditor())
			throw std::runtime_error("GetIEditor() not available");
		IObjectManager* pObjMan = GetIEditor()->GetObjectManager();
		if (!pObjMan)
			throw std::runtime_error("GetObjectManager() not available");

		std::string filter = tokens.size() > 1 ? tokens[1] : std::string();
		for (char& c : filter) c = (char)tolower((unsigned char)c);

		CBaseObjectsArray objects;
		pObjMan->GetObjects(objects);

		std::ostringstream oss;
		oss << "[";
		bool first = true;
		int emitted = 0;
		for (CBaseObject* pObj : objects)
		{
			if (!pObj)
				continue;
			const std::string name = (const char*)pObj->GetName();
			if (!filter.empty())
			{
				std::string hay = name;
				for (char& c : hay) c = (char)tolower((unsigned char)c);
				if (hay.find(filter) == std::string::npos)
					continue;
			}
			if (emitted >= 500)   // keep the reply bounded on a big level
				break;
			++emitted;

			const Vec3 pos = pObj->GetPos();
			const Vec3 scale = pObj->GetScale();
			const Quat rot = pObj->GetRotation();
			const Ang3 ang(rot);

			if (!first)
				oss << ", ";
			first = false;
			oss << "{\"name\": \"" << JsonEscape(name) << "\""
			    << ", \"type\": \"" << JsonEscape(std::string((const char*)pObj->GetTypeName())) << "\""
			    << ", \"pos\": [" << pos.x << ", " << pos.y << ", " << pos.z << "]"
			    << ", \"rotDeg\": [" << RAD2DEG(ang.x) << ", " << RAD2DEG(ang.y) << ", " << RAD2DEG(ang.z) << "]"
			    << ", \"scale\": [" << scale.x << ", " << scale.y << ", " << scale.z << "]"
			    << ", \"material\": \"" << JsonEscape(std::string((const char*)pObj->GetMaterialName())) << "\""
			    << ", \"hidden\": " << (pObj->IsHidden() ? "true" : "false")
			    << ", \"frozen\": " << (pObj->IsFrozen() ? "true" : "false")
			    << ", \"children\": " << (int)pObj->GetChildCount() << "}";
		}
		oss << "]";
		return oss.str();
	}

	// bridge.object.create <typeName> <x> <y> <z> [name] [geometryFile]
	// Direct IObjectManager::NewObject. Unlike general.new_object this
	// reports back what was actually created, so a silently-refused type
	// (an unknown typeName only logs a warning and no-ops) is visible.
	if (verb == "bridge.object.create")
	{
		if (tokens.size() < 5)
			throw std::runtime_error("bridge.object.create requires <typeName> <x> <y> <z> [name] [geometryFile]");
		if (!GetIEditor())
			throw std::runtime_error("GetIEditor() not available");
		IObjectManager* pObjMan = GetIEditor()->GetObjectManager();
		if (!pObjMan)
			throw std::runtime_error("GetObjectManager() not available");

		const string typeName = tokens[1].c_str();
		const Vec3 pos((float)std::atof(tokens[2].c_str()),
		               (float)std::atof(tokens[3].c_str()),
		               (float)std::atof(tokens[4].c_str()));
		const string file = tokens.size() > 6 ? string(tokens[6].c_str()) : string("");

		std::unique_ptr<CUndo> pUndo;
		if (s_claudeUndo)
			pUndo.reset(new CUndo((std::string("Claude: create ") + tokens[1]).c_str()));
		CScopedLogCapture capture;
		CBaseObject* pObj = pObjMan->NewObject(typeName, nullptr, file);
		if (!pObj)
			throw std::runtime_error("could not create object of type '" + tokens[1]
			                         + "' -- check the type name (log: " + capture.TakeOutput() + ")");

		pObj->SetPos(pos);
		if (tokens.size() > 5)
			pObj->SetName(string(tokens[5].c_str()));

		std::ostringstream oss;
		oss << "{\"ok\": true, \"name\": \"" << JsonEscape(std::string((const char*)pObj->GetName())) << "\""
		    << ", \"type\": \"" << JsonEscape(std::string((const char*)pObj->GetTypeName())) << "\""
		    << ", \"pos\": [" << pos.x << ", " << pos.y << ", " << pos.z << "]"
		    << ", \"log\": \"" << JsonEscape(capture.TakeOutput()) << "\"}";
		return oss.str();
	}

	// bridge.object.transform <name> <x> <y> <z> [rotXDeg rotYDeg rotZDeg] [scaleX scaleY scaleZ]
	if (verb == "bridge.object.transform")
	{
		if (tokens.size() < 5)
			throw std::runtime_error("bridge.object.transform requires <name> <x> <y> <z> [rx ry rz] [sx sy sz]");
		if (!GetIEditor())
			throw std::runtime_error("GetIEditor() not available");
		IObjectManager* pObjMan = GetIEditor()->GetObjectManager();
		if (!pObjMan)
			throw std::runtime_error("GetObjectManager() not available");

		CBaseObject* pObj = pObjMan->FindObject(tokens[1].c_str());
		if (!pObj)
			throw std::runtime_error("no object named '" + tokens[1] + "' -- see bridge.object.list");

		std::unique_ptr<CUndo> pUndo;
		if (s_claudeUndo)
		{
			pUndo.reset(new CUndo((std::string("Claude: move ") + tokens[1]).c_str()));
			pObj->StoreUndo("Claude: move");
		}
		pObj->SetPos(Vec3((float)std::atof(tokens[2].c_str()),
		                  (float)std::atof(tokens[3].c_str()),
		                  (float)std::atof(tokens[4].c_str())));

		if (tokens.size() >= 8)
		{
			const Ang3 ang(DEG2RAD((float)std::atof(tokens[5].c_str())),
			               DEG2RAD((float)std::atof(tokens[6].c_str())),
			               DEG2RAD((float)std::atof(tokens[7].c_str())));
			pObj->SetRotation(Quat(ang));
		}
		if (tokens.size() >= 11)
		{
			pObj->SetScale(Vec3((float)std::atof(tokens[8].c_str()),
			                    (float)std::atof(tokens[9].c_str()),
			                    (float)std::atof(tokens[10].c_str())));
		}

		const Vec3 p = pObj->GetPos();
		const Vec3 s = pObj->GetScale();
		const Ang3 a(pObj->GetRotation());
		std::ostringstream oss;
		oss << "{\"ok\": true, \"name\": \"" << JsonEscape(std::string((const char*)pObj->GetName())) << "\""
		    << ", \"pos\": [" << p.x << ", " << p.y << ", " << p.z << "]"
		    << ", \"rotDeg\": [" << RAD2DEG(a.x) << ", " << RAD2DEG(a.y) << ", " << RAD2DEG(a.z) << "]"
		    << ", \"scale\": [" << s.x << ", " << s.y << ", " << s.z << "]}";
		return oss.str();
	}

	// bridge.object.material <name> <materialName>
	// bridge.object.delete <name>
	// bridge.object.select <name>
	if (verb == "bridge.object.material" || verb == "bridge.object.delete" || verb == "bridge.object.select")
	{
		if (tokens.size() < 2)
			throw std::runtime_error(verb + " requires an object name");
		if (!GetIEditor())
			throw std::runtime_error("GetIEditor() not available");
		IObjectManager* pObjMan = GetIEditor()->GetObjectManager();
		if (!pObjMan)
			throw std::runtime_error("GetObjectManager() not available");

		CBaseObject* pObj = pObjMan->FindObject(tokens[1].c_str());
		if (!pObj)
			throw std::runtime_error("no object named '" + tokens[1] + "' -- see bridge.object.list");

		const std::string name = (const char*)pObj->GetName();
		std::unique_ptr<CUndo> pUndo;
		if (s_claudeUndo && verb != "bridge.object.select")
		{
			pUndo.reset(new CUndo((std::string("Claude: ") + (verb == "bridge.object.delete" ? "delete " : "material of ") + name).c_str()));
			if (verb == "bridge.object.material")
				pObj->StoreUndo("Claude: material");
		}
		CScopedLogCapture capture;
		std::ostringstream oss;

		if (verb == "bridge.object.delete")
		{
			pObjMan->DeleteObject(pObj);
			oss << "{\"ok\": true, \"deleted\": \"" << JsonEscape(name) << "\"";
		}
		else if (verb == "bridge.object.select")
		{
			pObjMan->ClearSelection();
			pObjMan->SelectObject(pObj);
			oss << "{\"ok\": true, \"selected\": \"" << JsonEscape(name) << "\"";
		}
		else
		{
			if (tokens.size() < 3)
				throw std::runtime_error("bridge.object.material requires <name> <materialName>");
			std::string mtl = tokens[2];
			for (size_t i = 3; i < tokens.size(); ++i)
				mtl += " " + tokens[i];
			pObj->SetMaterial(string(mtl.c_str()));
			oss << "{\"ok\": true, \"object\": \"" << JsonEscape(name) << "\""
			    << ", \"material\": \"" << JsonEscape(std::string((const char*)pObj->GetMaterialName())) << "\"";
		}

		oss << ", \"log\": \"" << JsonEscape(capture.TakeOutput()) << "\"}";
		return oss.str();
	}

	// bridge.object.types [categoryFilter]
	// Every creatable object class, by category. Without this,
	// bridge.object.create is guesswork: an unknown typeName only logs a
	// warning and no-ops, so you cannot tell a typo from an unsupported type.
	if (verb == "bridge.object.types")
	{
		if (!GetIEditor())
			throw std::runtime_error("GetIEditor() not available");
		IObjectManager* pObjMan = GetIEditor()->GetObjectManager();
		if (!pObjMan)
			throw std::runtime_error("GetObjectManager() not available");

		std::string filter = tokens.size() > 1 ? tokens[1] : std::string();
		for (char& c : filter) c = (char)tolower((unsigned char)c);

		std::vector<string> categories;
		pObjMan->GetClassCategories(categories);

		std::ostringstream oss;
		oss << "[";
		bool first = true;
		for (const string& cat : categories)
		{
			const std::string catStr = (const char*)cat;
			if (!filter.empty())
			{
				std::string hay = catStr;
				for (char& c : hay) c = (char)tolower((unsigned char)c);
				if (hay.find(filter) == std::string::npos)
					continue;
			}

			std::vector<CObjectClassDesc*> types;
			pObjMan->GetCreatableClassTypes(cat, types);
			for (CObjectClassDesc* pDesc : types)
			{
				if (!pDesc)
					continue;
				if (!first)
					oss << ", ";
				first = false;
				const char* cls = pDesc->ClassName();
				const char* spec = pDesc->GetFileSpec();
				oss << "{\"category\": \"" << JsonEscape(catStr) << "\""
				    << ", \"type\": \"" << JsonEscape(std::string(cls ? cls : "")) << "\""
				    // A non-empty fileSpec means create REQUIRES a file argument
				    // (e.g. Objects\\*.cgf) -- the usual reason a create silently
				    // does nothing.
				    << ", \"fileSpec\": \"" << JsonEscape(std::string(spec ? spec : "")) << "\"}";
			}
		}
		oss << "]";
		return oss.str();
	}

	// bridge.vegetation.list
	// Vegetation object types (the "brushes") with their placement rules and
	// how many instances of each are currently on the map.
	if (verb == "bridge.vegetation.list")
	{
		CEditorImpl* pImpl = EditorImpl();
		if (!pImpl)
			throw std::runtime_error("the editor is not available");
		CVegetationMap* pVegMap = pImpl->GetVegetationMap();
		if (!pVegMap)
			throw std::runtime_error("GetVegetationMap() not available -- is a level loaded?");

		std::ostringstream oss;
		oss << "{\"objects\": [";
		const int count = pVegMap->GetObjectCount();
		for (int i = 0; i < count; ++i)
		{
			CVegetationObject* pObj = pVegMap->GetObject(i);
			if (i > 0)
				oss << ", ";
			if (!pObj)
			{
				oss << "{\"index\": " << i << ", \"error\": \"null\"}";
				continue;
			}
			const char* file = pObj->GetFileName();
			const char* group = pObj->GetGroup();
			oss << "{\"index\": " << i
			    << ", \"id\": " << pObj->GetId()
			    << ", \"file\": \"" << JsonEscape(std::string(file ? file : "")) << "\""
			    << ", \"group\": \"" << JsonEscape(std::string(group ? group : "")) << "\""
			    << ", \"density\": " << pObj->GetDensity()
			    << ", \"instances\": " << pObj->GetNumInstances()
			    << ", \"hidden\": " << (pObj->IsHidden() ? "true" : "false") << "}";
		}
		oss << "], \"totalInstances\": " << pVegMap->GetNumInstances() << "}";
		return oss.str();
	}

	// bridge.vegetation.add <cgfPath> [density] [size] [sizeVar] [minAlt] [maxAlt] [minSlopeDeg] [maxSlopeDeg]
	// Creates a vegetation brush. density is the average SPACING in metres
	// (PaintBrush places area/(density*density) instances), so smaller means
	// denser. Slope is in degrees here -- note that is a different convention
	// from the terrain layer rules, which the engine maps through /90.1.
	if (verb == "bridge.vegetation.add")
	{
		if (tokens.size() < 2)
			throw std::runtime_error("bridge.vegetation.add requires <cgfPath> [density] [size] [sizeVar] [minAlt] [maxAlt] [minSlopeDeg] [maxSlopeDeg]");

		CEditorImpl* pImpl = EditorImpl();
		if (!pImpl)
			throw std::runtime_error("the editor is not available");
		CVegetationMap* pVegMap = pImpl->GetVegetationMap();
		if (!pVegMap)
			throw std::runtime_error("GetVegetationMap() not available -- is a level loaded?");

		CScopedLogCapture capture;
		CVegetationObject* pObj = pVegMap->CreateObject();
		if (!pObj)
			throw std::runtime_error("CreateObject() failed (log: " + capture.TakeOutput() + ")");

		pObj->SetFileName(string(tokens[1].c_str()));
		if (tokens.size() > 2) pObj->SetDensity((float)std::atof(tokens[2].c_str()));
		if (tokens.size() > 3) pObj->SetSize((float)std::atof(tokens[3].c_str()));
		if (tokens.size() > 4) pObj->SetSizeVar((float)std::atof(tokens[4].c_str()));
		if (tokens.size() > 6)
			pObj->SetElevation((float)std::atof(tokens[5].c_str()), (float)std::atof(tokens[6].c_str()));
		if (tokens.size() > 8)
			pObj->SetSlope((float)std::atof(tokens[7].c_str()), (float)std::atof(tokens[8].c_str()));
		pObj->SetInUse(true);
		pVegMap->SetEngineObjectsParams();

		std::ostringstream oss;
		oss << "{\"ok\": true, \"index\": " << (pVegMap->GetObjectCount() - 1)
		    << ", \"id\": " << pObj->GetId()
		    << ", \"file\": \"" << JsonEscape(std::string(pObj->GetFileName() ? pObj->GetFileName() : "")) << "\""
		    << ", \"density\": " << pObj->GetDensity()
		    << ", \"log\": \"" << JsonEscape(capture.TakeOutput()) << "\"}";
		return oss.str();
	}

	// bridge.vegetation.paint <index> <x1> <y1> <x2> <y2>
	// bridge.vegetation.clear <index> <x1> <y1> <x2> <y2>
	// Scatters (or removes) instances across a world-space rectangle in metres,
	// honouring the brush's density/elevation/slope rules. This is what the
	// paint tool does under the mouse, driven directly.
	if (verb == "bridge.vegetation.paint" || verb == "bridge.vegetation.clear")
	{
		if (tokens.size() < 6)
			throw std::runtime_error(verb + " requires <index> <x1> <y1> <x2> <y2> (world metres)");

		CEditorImpl* pImpl = EditorImpl();
		if (!pImpl)
			throw std::runtime_error("the editor is not available");
		CVegetationMap* pVegMap = pImpl->GetVegetationMap();
		if (!pVegMap)
			throw std::runtime_error("GetVegetationMap() not available -- is a level loaded?");

		const int index = std::atoi(tokens[1].c_str());
		if (index < 0 || index >= pVegMap->GetObjectCount())
			throw std::runtime_error("no vegetation object at index " + tokens[1] + " -- see bridge.vegetation.list");
		CVegetationObject* pObj = pVegMap->GetObject(index);
		if (!pObj)
			throw std::runtime_error("vegetation object is null");

		CRect rc(std::atoi(tokens[2].c_str()), std::atoi(tokens[3].c_str()),
		         std::atoi(tokens[4].c_str()), std::atoi(tokens[5].c_str()));

		const int before = pObj->GetNumInstances();
		CScopedLogCapture capture;
		if (verb == "bridge.vegetation.clear")
			pVegMap->ClearBrush(rc, false, pObj);
		else
			pVegMap->PaintBrush(rc, false, pObj);
		const int after = pObj->GetNumInstances();

		std::ostringstream oss;
		oss << "{\"ok\": true, \"index\": " << index
		    << ", \"rect\": [" << rc.left << ", " << rc.top << ", " << rc.right << ", " << rc.bottom << "]"
		    << ", \"instancesBefore\": " << before
		    << ", \"instancesAfter\": " << after
		    // Zero delta means the brush's elevation/slope filter rejected the
		    // whole area, or the .cgf failed to load -- reported rather than
		    // returning a bare ok.
		    << ", \"placed\": " << (after - before)
		    << ", \"log\": \"" << JsonEscape(capture.TakeOutput()) << "\"}";
		return oss.str();
	}

	// bridge.vegetation.place <index> <x> <y> <z>
	// A single instance at an exact position, for deliberate placement rather
	// than scattering.
	if (verb == "bridge.vegetation.place")
	{
		if (tokens.size() < 5)
			throw std::runtime_error("bridge.vegetation.place requires <index> <x> <y> <z>");

		CEditorImpl* pImpl = EditorImpl();
		if (!pImpl)
			throw std::runtime_error("the editor is not available");
		CVegetationMap* pVegMap = pImpl->GetVegetationMap();
		if (!pVegMap)
			throw std::runtime_error("GetVegetationMap() not available -- is a level loaded?");

		const int index = std::atoi(tokens[1].c_str());
		if (index < 0 || index >= pVegMap->GetObjectCount())
			throw std::runtime_error("no vegetation object at index " + tokens[1]);
		CVegetationObject* pObj = pVegMap->GetObject(index);
		if (!pObj)
			throw std::runtime_error("vegetation object is null");

		const Vec3 pos((float)std::atof(tokens[2].c_str()),
		               (float)std::atof(tokens[3].c_str()),
		               (float)std::atof(tokens[4].c_str()));

		CScopedLogCapture capture;
		CVegetationInstance* pInst = pVegMap->PlaceObjectInstance(pos, pObj);

		std::ostringstream oss;
		oss << "{\"ok\": " << (pInst ? "true" : "false")
		    << ", \"index\": " << index
		    << ", \"pos\": [" << pos.x << ", " << pos.y << ", " << pos.z << "]"
		    << ", \"instances\": " << pObj->GetNumInstances()
		    << ", \"log\": \"" << JsonEscape(capture.TakeOutput()) << "\"}";
		return oss.str();
	}

	// bridge.vegetation.instances [index] [max]
	// Where the instances actually are. "60 instances" in the list tells you
	// nothing about whether they landed anywhere the player will ever walk,
	// which is exactly the question when someone says they cannot see them.
	if (verb == "bridge.vegetation.instances")
	{
		CEditorImpl* pImpl = EditorImpl();
		if (!pImpl)
			throw std::runtime_error("the editor is not available");
		CVegetationMap* pVegMap = pImpl->GetVegetationMap();
		if (!pVegMap)
			throw std::runtime_error("GetVegetationMap() not available -- is a level loaded?");

		const int wantIndex = (tokens.size() > 1) ? std::atoi(tokens[1].c_str()) : -1;
		const int maxOut = (tokens.size() > 2) ? std::atoi(tokens[2].c_str()) : 200;

		std::vector<CVegetationInstance*> instances;
		pVegMap->GetAllInstances(instances);

		int matched = 0;
		int written = 0;
		Vec3 mins(1e9f, 1e9f, 1e9f), maxs(-1e9f, -1e9f, -1e9f);
		std::ostringstream items;
		for (size_t i = 0; i < instances.size(); ++i)
		{
			CVegetationInstance* pInst = instances[i];
			if (!pInst || !pInst->object)
				continue;
			const int objIndex = pInst->object->GetIndex();
			if (wantIndex >= 0 && objIndex != wantIndex)
				continue;
			++matched;
			mins.CheckMin(pInst->pos);
			maxs.CheckMax(pInst->pos);
			if (written < maxOut)
			{
				if (written > 0)
					items << ", ";
				items << "{\"object\": " << objIndex
				      << ", \"pos\": [" << pInst->pos.x << ", " << pInst->pos.y << ", " << pInst->pos.z << "]"
				      << ", \"scale\": " << pInst->scale
				      << ", \"rendered\": " << (pInst->pRenderNode ? "true" : "false") << "}";
				++written;
			}
		}

		std::ostringstream oss;
		oss << "{\"ok\": true, \"matched\": " << matched << ", \"listed\": " << written;
		if (matched > 0)
		{
			oss << ", \"bboxMin\": [" << mins.x << ", " << mins.y << ", " << mins.z << "]"
			    << ", \"bboxMax\": [" << maxs.x << ", " << maxs.y << ", " << maxs.z << "]";
		}
		oss << ", \"instances\": [" << items.str() << "]}";
		return oss.str();
	}

	// bridge.vegetation.params <index> <key> <value> [<key> <value> ...]
	// keys: size sizevar density bending airadius hideable castshadow
	//       elevmin elevmax slopemin slopemax hidden
	// airadius is the one that matters for AI: < 0 lets the engine work the
	// navigation radius out from the object, >= 0 forces it.
	if (verb == "bridge.vegetation.params")
	{
		if (tokens.size() < 4 || ((tokens.size() - 2) % 2) != 0)
			throw std::runtime_error("bridge.vegetation.params requires <index> <key> <value> [<key> <value> ...]");

		CEditorImpl* pImpl = EditorImpl();
		if (!pImpl)
			throw std::runtime_error("the editor is not available");
		CVegetationMap* pVegMap = pImpl->GetVegetationMap();
		if (!pVegMap)
			throw std::runtime_error("GetVegetationMap() not available -- is a level loaded?");

		const int index = std::atoi(tokens[1].c_str());
		if (index < 0 || index >= pVegMap->GetObjectCount())
			throw std::runtime_error("no vegetation object at index " + tokens[1]);
		CVegetationObject* pObj = pVegMap->GetObject(index);
		if (!pObj)
			throw std::runtime_error("vegetation object is null");

		std::ostringstream applied;
		int count = 0;
		for (size_t i = 2; i + 1 < tokens.size(); i += 2)
		{
			std::string key = tokens[i];
			for (size_t c = 0; c < key.size(); ++c)
				key[c] = (char)tolower((unsigned char)key[c]);
			const float value = (float)std::atof(tokens[i + 1].c_str());

			if (key == "size")            pObj->SetSize(value);
			else if (key == "sizevar")    pObj->SetSizeVar(value);
			else if (key == "density")    pObj->SetDensity(value);
			else if (key == "bending")    pObj->SetBending(value);
			else if (key == "airadius")   pObj->SetAIRadius(value);
			else if (key == "hideable")   pObj->SetHideable(value != 0.0f);
			else if (key == "castshadow") pObj->SetCastShadow(value != 0.0f);
			else if (key == "hidden")     pObj->SetHidden(value != 0.0f);
			else if (key == "elevmin")    pObj->SetElevation(value, pObj->GetElevationMax());
			else if (key == "elevmax")    pObj->SetElevation(pObj->GetElevationMin(), value);
			else if (key == "slopemin")   pObj->SetSlope(value, pObj->GetSlopeMax());
			else if (key == "slopemax")   pObj->SetSlope(pObj->GetSlopeMin(), value);
			else
			{
				// Anything else is looked up by its variable name, so every property the
				// Vegetation Editor shows is reachable -- AutoMerged (the merged-mesh
				// switch the manual asks for) has a getter but no setter, and the
				// merged-mesh behaviour parameters (Stiffness, Damping, Variance,
				// AirResistance) have neither. The names are the ones passed to
				// CVarObject::AddVariable in VegetationObject.cpp, matched case-insensitively.
				IVariable* pVar = nullptr;
				if (CVarObject* pVars = pObj->GetVarObject())
				{
					for (int v = 0; v < pVars->GetNumVariables() && !pVar; ++v)
					{
						IVariable* pCandidate = pVars->GetVariable(v);
						if (!pCandidate)
							continue;
						std::string name = (const char*)pCandidate->GetName();
						for (char& c : name)
							c = (char)tolower((unsigned char)c);
						if (name == key)
							pVar = pCandidate;
					}
				}
				if (!pVar)
					throw std::runtime_error("unknown vegetation param: " + tokens[i]);

				if (pVar->GetType() == IVariable::BOOL)
					pVar->Set(value != 0.0f);
				else if (pVar->GetType() == IVariable::INT)
					pVar->Set((int)value);
				else
					pVar->Set(value);
			}

			if (count > 0)
				applied << ", ";
			applied << "\"" << JsonEscape(key) << "\": " << value;
			++count;
		}

		CScopedLogCapture capture;
		pVegMap->SetEngineObjectsParams();
		pVegMap->RepositionObject(pObj);

		std::ostringstream oss;
		oss << "{\"ok\": true, \"index\": " << index
		    << ", \"applied\": {" << applied.str() << "}"
		    << ", \"size\": " << pObj->GetSize()
		    << ", \"aiRadius\": " << pObj->GetAIRadius()
		    << ", \"hideable\": " << (pObj->IsHideable() ? "true" : "false")
		    << ", \"autoMerged\": " << (pObj->IsAutoMerged() ? "true" : "false")
		    << ", \"instances\": " << pObj->GetNumInstances()
		    << ", \"log\": \"" << JsonEscape(capture.TakeOutput()) << "\"}";
		return oss.str();
	}

	// bridge.db.* -- the editor's item databases.
	//
	// Lens Flare, Particle Editor, entity archetypes and gametokens have no
	// Python module whatsoever, which is what made them "GUI-only". They are
	// all IDataBaseManager databases underneath, reachable from the abstract
	// IEditor interface, so one generic verb family covers all of them rather
	// than a bespoke binding each.
	//
	// XML moves through FILES, not the command line: the bridge splits
	// commands on whitespace, so passing a document inline would be mangled.
	if (verb.compare(0, 10, "bridge.db.") == 0)
	{
		if (!GetIEditor())
			throw std::runtime_error("GetIEditor() not available");
		const std::string action = verb.substr(10);

		if (action == "types")
		{
			return std::string("{\"types\": [\"material\", \"particle\", \"archetype\", \"gametoken\", \"flare\"]"
			                   ", \"actions\": [\"types\", \"libraries\", \"items\", \"add_library\", \"create\""
			                   ", \"export\", \"import\", \"delete\", \"export_library\", \"import_library\"]"
			                   ", \"note\": \"flare and particle have no Python module at all. For flares use "
			                   "export_library/import_library, not export/import: the per-item serializer omits the "
			                   "optics tree entirely and hands back an empty shell.\"}");
		}

		if (tokens.size() < 2)
			throw std::runtime_error(verb + " requires a database type (material, particle, archetype, gametoken, flare)");

		std::string typeName = tokens[1];
		for (char& c : typeName) c = (char)tolower((unsigned char)c);

		IDataBaseManager* pMgr = nullptr;
		if (typeName == "flare")
		{
			// EDB_TYPE_FLARE is NOT handled by CEditorImpl::GetDBItemManager --
			// its switch covers only material/archetype/gametoken/particle and
			// falls through to return 0, so the interface route yields null for
			// flares no matter what. CLensFlareManager is SANDBOX_API and
			// derives from CBaseLibraryManager (an IDataBaseManager), so go at
			// it directly. Confirmed empirically: the interface path returned
			// null even with the Lens Flare editor open.
			CEditorImpl* pImpl = EditorImpl();
			if (!pImpl)
				throw std::runtime_error("the editor is not available");
			pMgr = pImpl->GetLensFlareManager();
		}
		else
		{
			EDataBaseItemType dbType;
			if (typeName == "material")       dbType = EDB_TYPE_MATERIAL;
			else if (typeName == "particle")  dbType = EDB_TYPE_PARTICLE;
			else if (typeName == "archetype") dbType = EDB_TYPE_ENTITY_ARCHETYPE;
			else if (typeName == "gametoken") dbType = EDB_TYPE_GAMETOKEN;
			else
				throw std::runtime_error("unknown database type: " + tokens[1] + " (see bridge.db.types)");
			pMgr = GetIEditor()->GetDBItemManager(dbType);
		}
		if (!pMgr)
			throw std::runtime_error("no database manager for type " + typeName);

		if (action == "libraries")
		{
			std::ostringstream oss;
			oss << "{\"type\": \"" << JsonEscape(typeName) << "\", \"libraries\": [";
			const int n = pMgr->GetLibraryCount();
			for (int i = 0; i < n; ++i)
			{
				IDataBaseLibrary* pLib = pMgr->GetLibrary(i);
				if (i > 0)
					oss << ", ";
				if (!pLib)
				{
					oss << "{\"index\": " << i << ", \"error\": \"null\"}";
					continue;
				}
				oss << "{\"index\": " << i
				    << ", \"name\": \"" << JsonEscape(std::string((const char*)pLib->GetName())) << "\""
				    << ", \"file\": \"" << JsonEscape(std::string((const char*)pLib->GetFilename())) << "\""
				    << ", \"items\": " << pLib->GetItemCount()
				    << ", \"levelLibrary\": " << (pLib->IsLevelLibrary() ? "true" : "false")
				    << ", \"modified\": " << (pLib->IsModified() ? "true" : "false") << "}";
			}
			oss << "]}";
			return oss.str();
		}

		if (action == "items")
		{
			std::string filter = tokens.size() > 2 ? tokens[2] : std::string();
			for (char& c : filter) c = (char)tolower((unsigned char)c);

			std::ostringstream oss;
			oss << "[";
			bool first = true;
			const int n = pMgr->GetLibraryCount();
			for (int i = 0; i < n; ++i)
			{
				IDataBaseLibrary* pLib = pMgr->GetLibrary(i);
				if (!pLib)
					continue;
				const int m = pLib->GetItemCount();
				for (int j = 0; j < m; ++j)
				{
					IDataBaseItem* pItem = pLib->GetItem(j);
					if (!pItem)
						continue;
					const std::string full = (const char*)pItem->GetFullName();
					if (!filter.empty())
					{
						std::string hay = full;
						for (char& c : hay) c = (char)tolower((unsigned char)c);
						if (hay.find(filter) == std::string::npos)
							continue;
					}
					if (!first)
						oss << ", ";
					first = false;
					oss << "{\"fullName\": \"" << JsonEscape(full) << "\""
					    << ", \"name\": \"" << JsonEscape(std::string((const char*)pItem->GetName())) << "\""
					    << ", \"library\": \"" << JsonEscape(std::string((const char*)pLib->GetName())) << "\"}";
				}
			}
			oss << "]";
			return oss.str();
		}

		// bridge.db.load_library <type> <file>   (e.g. particle libs/particles/weather.xml)
		// bridge.db.save_library <type> <name>   IDataBaseLibrary::Save -- the library editor's Save
		// Libraries load on demand (the particle editor opens them); an item of an unloaded library is not
		// found by export/import until its library is loaded.
		if (action == "load_library" || action == "save_library")
		{
			if (tokens.size() < 3)
				throw std::runtime_error(verb + " requires <type> <file-or-name>");
			CScopedLogCapture capture;
			IDataBaseLibrary* pLib = nullptr;
			bool saved = false;
			if (action == "load_library")
			{
				pLib = pMgr->LoadLibrary(string(tokens[2].c_str()), false);
			}
			else
			{
				pLib = pMgr->FindLibrary(string(tokens[2].c_str()));
				saved = pLib && pLib->Save();
			}
			if (!pLib || (action == "save_library" && !saved))
				throw std::runtime_error(verb + " failed for '" + tokens[2] + "' (log: " + capture.TakeOutput() + ")");
			std::ostringstream oss;
			oss << "{\"ok\": true, \"library\": \"" << JsonEscape(std::string((const char*)pLib->GetName())) << "\""
			    << ", \"file\": \"" << JsonEscape(std::string((const char*)pLib->GetFilename())) << "\""
			    << ", \"items\": " << pLib->GetItemCount() << ", \"saved\": " << (saved ? "true" : "false") << "}";
			return oss.str();
		}

		if (action == "add_library")
		{
			if (tokens.size() < 3)
				throw std::runtime_error("bridge.db.add_library requires <type> <libraryName>");
			CScopedLogCapture capture;
			IDataBaseLibrary* pLib = pMgr->AddLibrary(string(tokens[2].c_str()));
			if (!pLib)
				throw std::runtime_error("AddLibrary failed (log: " + capture.TakeOutput() + ")");
			std::ostringstream oss;
			oss << "{\"ok\": true, \"library\": \"" << JsonEscape(std::string((const char*)pLib->GetName())) << "\""
			    << ", \"file\": \"" << JsonEscape(std::string((const char*)pLib->GetFilename())) << "\"}";
			return oss.str();
		}

		if (action == "create")
		{
			if (tokens.size() < 4)
				throw std::runtime_error("bridge.db.create requires <type> <libraryName> <itemName>");
			IDataBaseLibrary* pLib = pMgr->FindLibrary(string(tokens[2].c_str()));
			if (!pLib)
				throw std::runtime_error("no library '" + tokens[2] + "' -- create it with bridge.db.add_library");

			CScopedLogCapture capture;
			IDataBaseItem* pItem = pMgr->CreateItem(pLib);
			if (!pItem)
				throw std::runtime_error("CreateItem failed (log: " + capture.TakeOutput() + ")");
			pItem->SetName(string(tokens[3].c_str()));

			std::ostringstream oss;
			oss << "{\"ok\": true, \"fullName\": \"" << JsonEscape(std::string((const char*)pItem->GetFullName())) << "\""
			    << ", \"library\": \"" << JsonEscape(std::string((const char*)pLib->GetName())) << "\""
			    << ", \"log\": \"" << JsonEscape(capture.TakeOutput()) << "\"}";
			return oss.str();
		}

		// bridge.db.export_library <type> <libraryName> <path.xml>
		// bridge.db.import_library <type> <libraryName> <path.xml>
		//
		// Whole-library serialization, and for lens flares it is the ONLY way to
		// read one back. CLensFlareItem::Serialize writes nothing but Name/Id/
		// Library on the way out -- the optics tree is produced by a separate
		// CreateXmlData() that the per-item path never calls, so exporting a
		// flare item yields an empty shell. CLensFlareLibrary::Serialize does
		// call it, and IDataBaseLibrary::Serialize is pure virtual on the
		// abstract interface, so this reaches the real implementation without
		// linking against Sandbox.exe (CLensFlareItem itself is not exported, so
		// calling CreateXmlData directly would not link at all).
		//
		// Importing REPLACES the library: CBaseLibrary::Serialize(loading) calls
		// RemoveAllItems() first. Export before you import if you want the
		// existing items back.
		if (action == "export_library" || action == "import_library")
		{
			if (tokens.size() < 4)
				throw std::runtime_error(verb + " requires <type> <libraryName> <path.xml>");

			IDataBaseLibrary* pLib = nullptr;
			const int libCount = pMgr->GetLibraryCount();
			for (int i = 0; i < libCount; ++i)
			{
				IDataBaseLibrary* pCandidate = pMgr->GetLibrary(i);
				if (pCandidate && tokens[2] == std::string((const char*)pCandidate->GetName()))
				{
					pLib = pCandidate;
					break;
				}
			}
			if (!pLib)
				throw std::runtime_error("no library '" + tokens[2] + "' for type " + typeName + " -- see bridge.db.libraries " + typeName);

			std::string path = tokens[3];
			for (size_t i = 4; i < tokens.size(); ++i)
				path += " " + tokens[i];

			if (!gEnv || !gEnv->pSystem)
				throw std::runtime_error("ISystem not available");

			CScopedLogCapture capture;
			if (action == "export_library")
			{
				XmlNodeRef node = gEnv->pSystem->CreateXmlNode("DataBaseLibrary");
				pLib->Serialize(node, false);
				if (!node->saveToFile(path.c_str()))
					throw std::runtime_error("could not write " + path);

				std::ostringstream oss;
				oss << "{\"ok\": true, \"exported\": \"" << JsonEscape(std::string((const char*)pLib->GetName())) << "\""
				    << ", \"type\": \"" << JsonEscape(typeName) << "\""
				    << ", \"path\": \"" << JsonEscape(path) << "\""
				    << ", \"items\": " << pLib->GetItemCount()
				    << ", \"childNodes\": " << node->getChildCount() << "}";
				return oss.str();
			}

			XmlNodeRef node = gEnv->pSystem->LoadXmlFromFile(path.c_str());
			if (!node)
				throw std::runtime_error("could not read XML from " + path);
			const int before = pLib->GetItemCount();
			pLib->Serialize(node, true);
			pLib->SetModified(true);

			std::ostringstream oss;
			oss << "{\"ok\": true, \"imported\": \"" << JsonEscape(std::string((const char*)pLib->GetName())) << "\""
			    << ", \"type\": \"" << JsonEscape(typeName) << "\""
			    << ", \"path\": \"" << JsonEscape(path) << "\""
			    << ", \"itemsBefore\": " << before
			    << ", \"itemsNow\": " << pLib->GetItemCount()
			    << ", \"log\": \"" << JsonEscape(capture.TakeOutput(true)) << "\"}";
			return oss.str();
		}

		if (action == "export" || action == "import" || action == "delete")
		{
			if (tokens.size() < 3)
				throw std::runtime_error(verb + " requires <type> <itemFullName> [path]");
			IDataBaseItem* pItem = pMgr->FindItemByName(string(tokens[2].c_str()));
			if (!pItem)
				throw std::runtime_error("no item '" + tokens[2] + "' -- see bridge.db.items " + typeName);

			const std::string full = (const char*)pItem->GetFullName();

			if (action == "delete")
			{
				CScopedLogCapture capture;
				pMgr->DeleteItem(pItem);
				std::ostringstream oss;
				oss << "{\"ok\": true, \"deleted\": \"" << JsonEscape(full) << "\""
				    << ", \"log\": \"" << JsonEscape(capture.TakeOutput()) << "\"}";
				return oss.str();
			}

			if (tokens.size() < 4)
				throw std::runtime_error(verb + " requires <type> <itemFullName> <path.xml>");
			std::string path = tokens[3];
			for (size_t i = 4; i < tokens.size(); ++i)
				path += " " + tokens[i];

			if (!gEnv || !gEnv->pSystem)
				throw std::runtime_error("ISystem not available");

			CScopedLogCapture capture;
			if (action == "export")
			{
				XmlNodeRef node = gEnv->pSystem->CreateXmlNode("DataBaseItem");
				IDataBaseItem::SerializeContext ctx;
				ctx.node = node;
				ctx.bLoading = false;
				ctx.bUndo = false;
				ctx.bCopyPaste = false;
				pItem->Serialize(ctx);
				if (!node->saveToFile(path.c_str()))
					throw std::runtime_error("could not write " + path);

				std::ostringstream oss;
				oss << "{\"ok\": true, \"exported\": \"" << JsonEscape(full) << "\""
				    << ", \"path\": \"" << JsonEscape(path) << "\""
				    << ", \"childNodes\": " << node->getChildCount()
				    << ", \"attributes\": " << node->getNumAttributes() << "}";
				return oss.str();
			}

			XmlNodeRef node = gEnv->pSystem->LoadXmlFromFile(path.c_str());
			if (!node)
				throw std::runtime_error("could not read XML from " + path);
			IDataBaseItem::SerializeContext ctx;
			ctx.node = node;
			ctx.bLoading = true;
			ctx.bUndo = false;
			ctx.bCopyPaste = false;
			pItem->Serialize(ctx);

			std::ostringstream oss;
			oss << "{\"ok\": true, \"imported\": \"" << JsonEscape(full) << "\""
			    << ", \"path\": \"" << JsonEscape(path) << "\""
			    // Read the name back: Serialize may rename the item, and that
			    // is exactly the sort of thing worth seeing rather than assuming.
			    << ", \"nameNow\": \"" << JsonEscape(std::string((const char*)pItem->GetFullName())) << "\""
			    << ", \"log\": \"" << JsonEscape(capture.TakeOutput()) << "\"}";
			return oss.str();
		}

		throw std::runtime_error("unknown db action: " + action
		                         + " (types, libraries, items, add_library, load_library, save_library, create, export, import, delete, "
		                           "export_library, import_library)");
	}

	// bridge.viewport.screenshot and bridge.input.* are handled out-of-band
	// (see TryDispatchOutOfBand) so they still work while a modal dialog has
	// the idle tick blocked. Reaching them here too keeps behaviour identical
	// whichever route a caller takes.
	{
		std::string oob;
		if (TryDispatchOutOfBand(cmd, oob))
			return oob;
	}

	// A "bridge.*" command that reached this far is a verb that does not
	// exist. Falling through to the console would "succeed": IConsole logs
	// "Unknown command" and reports nothing, so a mistyped verb came back
	// ok:true with an empty result -- the same silent-success shape that has
	// cost this project hours more than once. Refuse it instead.
	if (cmd.compare(0, 7, "bridge.") == 0)
	{
		const std::vector<std::string> t = Tokenize(cmd);
		throw std::runtime_error("unknown bridge verb: " + (t.empty() ? cmd : t[0])
		                         + " (families: bridge.camera.*, bridge.db.*, bridge.dialog.*, bridge.entities.*, bridge.env.*, "
		                           "bridge.file.*, bridge.input.*, bridge.object.*, bridge.terrain.*, "
		                           "bridge.vegetation.*, bridge.viewport.*, bridge.window.*, "
		                           "bridge.execute_command, bridge.execute_python[.b64|.raw], "
		                           "bridge.cvar_get, bridge.cvar_set)");
	}

	// Anything else: run it as a raw console command, exactly like the
	// Game.dll bridge's original path.
	CScopedLogCapture capture;
	gEnv->pConsole->ExecuteString(cmd.c_str(), false, false);
	return capture.TakeOutput();
}
