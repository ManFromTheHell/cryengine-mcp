// Claude MCP bridge -- Sandbox editor plugin.
//
// This is the "genuine" editor-side counterpart to the Game.dll bridge in
// "My Project"/Code/GamePlugin.cpp. That bridge only pumps queued commands
// from IGameFrameworkListener::OnPostUpdate, which CryAction only calls
// while a Play session is running -- so it's useless for driving the
// editor itself (placing objects, editing levels, etc.) and, empirically,
// unreliable even in Play mode whenever the Sandbox window loses OS focus.
//
// This plugin instead hooks eNotify_OnIdleUpdate -- "Sent every frame
// while editor is idle" (see EEditorNotifyEvent in
// Code/Sandbox/EditorInterface/IEditor.h) -- which fires continuously
// whenever Sandbox.exe is running, in edit mode as well as Play mode, tied
// to Qt's own idle-event pump rather than the game update loop.
//
// That open question about OS-focus throttling now has an answer, and it is
// not the comfortable one: the idle tick only fires while the editor is
// DRAWING. Minimise the window, open a menu, or put up a modal dialog and it
// stops entirely -- every queued command then times out, including whichever
// one would undo the situation. So the plugin also runs a message-only
// window with a WM_TIMER on the main thread; WM_TIMER is dispatched by every
// message loop Windows runs, nested modal loops included, so the queue
// drains regardless of what the editor is doing. See the pump comment in the
// .cpp. The idle tick is kept as the fast path, not as the only path.
//
// Runs on its own TCP port (37778, vs. the Game.dll bridge's 37777) so
// both can be loaded into the same Sandbox.exe process (the Game.dll
// bridge loads when a level's Game.dll is loaded, e.g. during Play) without
// a port conflict.
//
// Threading model is identical to GamePlugin.cpp: TcpCommandServer's
// accept/client threads only enqueue a command and block on a
// std::promise/future; OnEditorNotifyEvent, called on the main/UI thread,
// is the only place that actually touches gEnv (pConsole, pRenderer,
// pEntitySystem, ...), because those are not thread-safe to call from an
// arbitrary background thread.
#pragma once

#include "IPlugin.h"
#include "IEditor.h"

#include <deque>
#include <future>
#include <memory>
#include <mutex>
#include <string>

#include "TcpCommandServer.h"

#if defined(ClaudeBridgePlugin_EXPORTS)
	#define CLAUDE_BRIDGE_PLUGIN_API DLL_EXPORT
#else
	#define CLAUDE_BRIDGE_PLUGIN_API DLL_IMPORT
#endif

struct SPendingBridgeCommand
{
	std::string cmd;
	std::promise<std::string> result;
};

class CLAUDE_BRIDGE_PLUGIN_API CClaudeBridgePlugin
	: public IPlugin
	, public IAutoEditorNotifyListener
{
public:
	CClaudeBridgePlugin();
	~CClaudeBridgePlugin();

	// IPlugin
	int32       GetPluginVersion() override { return 1; }
	const char* GetPluginName() override { return "Claude Bridge Plugin"; }
	const char* GetPluginDescription() override
	{
		return "TCP command bridge for Claude, driven by the editor idle tick. "
		       "Unlike the Game.dll bridge, works in edit mode as well as Play mode.";
	}

	// IEditorNotifyListener
	void OnEditorNotifyEvent(EEditorNotifyEvent event) override;

	// Drains the pending-command queue. Called from the main-thread WM_TIMER
	// pump as well as from the idle tick -- see the pump comment in the .cpp
	// for why the idle tick alone is not enough. Public only because the
	// pump's window procedure has to be a free function; not part of IPlugin.
	void PumpTick();

	// Drains the dialog-only queue (bridge.dialog.*). Called from the WM_TIMER
	// pump even while a queued command sits inside a modal dialog's loop --
	// which is exactly when the ordinary queue cannot move.
	void PumpDialogTick();

private:
	// Runs on the main thread; reads and clicks the editor's dialogs through
	// Qt (and Win32 for the few #32770 ones). Never runs engine commands.
	std::string DispatchDialogCommand(const std::string& cmd);

	// Called from a TcpCommandServer client thread. Queues the command and
	// blocks (via the future) until OnEditorNotifyEvent has run it on the
	// main thread and posted a result.
	std::string ExecuteAndCaptureOutput(const std::string& cmd);

	// Runs entirely on the main thread (called from OnEditorNotifyEvent).
	// Recognizes the same structured "bridge.*" convenience commands as
	// GamePlugin.cpp, falling back to raw console-command execution for
	// anything else.
	std::string DispatchBridgeCommand(const std::string& cmd);

	void DrainQueue();

	static constexpr unsigned short kBridgePort = 37778;

	std::unique_ptr<TcpCommandServer> m_bridgeServer;
	std::mutex m_bridgeQueueMutex;
	std::deque<std::unique_ptr<SPendingBridgeCommand>> m_bridgeQueue;

	// How long ExecuteAndCaptureOutput waits for the idle tick to drain the
	// queue before giving up. eNotify_OnIdleUpdate should fire every frame
	// Sandbox is running, so this is generous headroom, not a normal wait.
	static constexpr int kCommandTimeoutSeconds = 20;

	// The dialog queue: separate from the one above so that a command which
	// opened a modal dialog (and so holds the main queue until the dialog
	// closes) cannot also block the verb that answers it.
	std::mutex m_dialogQueueMutex;
	std::deque<std::unique_ptr<SPendingBridgeCommand>> m_dialogQueue;

	// The pump ticks every 25 ms whenever the main thread runs ANY message
	// loop, so a dialog verb that waits this long means the thread is not
	// pumping at all: busy in a long command, or hung.
	static constexpr int kDialogTimeoutSeconds = 10;
};
