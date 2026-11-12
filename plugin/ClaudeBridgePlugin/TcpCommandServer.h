#pragma once
// Small, dependency-free (aside from Winsock) newline-delimited JSON TCP
// server. Deliberately kept free of any CryEngine headers -- the only
// CryEngine-specific code (starting/stopping this, and running commands
// through the console) lives in ClaudeBridgePlugin.cpp.
//
// This is a straight copy of My Project/Code/TcpCommandServer.h/.cpp (the
// Game.dll bridge's transport), reused as-is for the editor-side bridge --
// the wire protocol and threading model are identical, only the port and
// the caller differ.
//
// Wire protocol: each request is one line of JSON, e.g.
//   {"id": 1, "cmd": "map mylevel"}
// and gets exactly one line of JSON back:
//   {"id": 1, "ok": true, "output": "..."}
//
// Not hardened for anything beyond localhost use: no auth, no TLS, no
// concurrency limits. It only binds to 127.0.0.1, so it's only reachable
// from processes on the same machine.

#include <atomic>
#include <functional>
#include <string>
#include <thread>

class TcpCommandServer
{
public:
	// Called once per request with the raw "cmd" string from the JSON
	// request. Return value becomes the "output" field of the response.
	// Throw std::exception to have it reported back as {"ok": false, "error": ...}.
	using CommandHandler = std::function<std::string(const std::string& cmd)>;

	TcpCommandServer(unsigned short port, CommandHandler handler);
	~TcpCommandServer();

	// Starts the accept loop on a background thread. Safe to call once.
	bool Start();

	// Stops the server and joins the background thread.
	void Stop();

	bool IsRunning() const { return m_running; }

private:
	void AcceptLoop();
	void HandleClient(unsigned long long clientSocket);

	unsigned short m_port;
	CommandHandler m_handler;
	std::atomic<bool> m_running{ false };
	std::atomic<bool> m_stopRequested{ false };
	std::thread m_acceptThread;
	unsigned long long m_listenSocket = 0; // SOCKET, typed loosely to avoid pulling winsock2.h into this header
};
