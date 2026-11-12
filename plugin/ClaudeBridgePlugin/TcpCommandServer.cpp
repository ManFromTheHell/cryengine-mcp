// This module's .cpp files use a precompiled header (StdAfx.h), so it has
// to be the first include here even though everything else in this file is
// plain Winsock and has nothing to do with CryEngine.
//
// Straight copy of My Project/Code/TcpCommandServer.cpp -- proven working
// there (confirmed via live PowerShell round-trips against the Game.dll
// bridge). Only StdAfx.h differs per-module; the transport logic below is
// unchanged.
#include "StdAfx.h"
#include "TcpCommandServer.h"

#include <sstream>
#include <vector>

#ifdef _WIN32
	#include <winsock2.h>
	#include <ws2tcpip.h>
	#pragma comment(lib, "Ws2_32.lib")
#endif

namespace
{
	// Minimal helpers for our one-field-in, one-field-out JSON protocol.
	// Deliberately not a general JSON parser -- CryEngine ships a real JSON
	// library that'd be worth switching to once confirmed where it lives.

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

	// Extracts "id" (raw text, may be a number) and "cmd" from a flat
	// single-line JSON object like {"id": 1, "cmd": "map mylevel"}. Good
	// enough for our fixed protocol; not a general parser.
	bool ParseRequest(const std::string& line, std::string& outId, std::string& outCmd)
	{
		auto extractString = [&](const std::string& key, std::string& out) -> bool
		{
			std::string needle = "\"" + key + "\"";
			size_t keyPos = line.find(needle);
			if (keyPos == std::string::npos) return false;
			size_t colon = line.find(':', keyPos + needle.size());
			if (colon == std::string::npos) return false;
			size_t quoteStart = line.find('"', colon + 1);
			if (quoteStart == std::string::npos) return false;
			size_t i = quoteStart + 1;
			std::string result;
			while (i < line.size() && line[i] != '"')
			{
				if (line[i] == '\\' && i + 1 < line.size())
				{
					char next = line[i + 1];
					switch (next)
					{
					case 'n': result += '\n'; break;
					case 't': result += '\t'; break;
					case 'r': result += '\r'; break;
					case '"': result += '"'; break;
					case '\\': result += '\\'; break;
					default: result += next;
					}
					i += 2;
				}
				else
				{
					result += line[i];
					++i;
				}
			}
			out = result;
			return true;
		};

		auto extractRawNumber = [&](const std::string& key, std::string& out) -> bool
		{
			std::string needle = "\"" + key + "\"";
			size_t keyPos = line.find(needle);
			if (keyPos == std::string::npos) return false;
			size_t colon = line.find(':', keyPos + needle.size());
			if (colon == std::string::npos) return false;
			size_t i = colon + 1;
			while (i < line.size() && isspace(static_cast<unsigned char>(line[i]))) ++i;
			size_t start = i;
			while (i < line.size() && (isdigit(static_cast<unsigned char>(line[i])) || line[i] == '-')) ++i;
			out = line.substr(start, i - start);
			return !out.empty();
		};

		if (!extractString("cmd", outCmd)) return false;
		if (!extractRawNumber("id", outId)) outId = "null";
		return true;
	}
}

TcpCommandServer::TcpCommandServer(unsigned short port, CommandHandler handler)
	: m_port(port)
	, m_handler(std::move(handler))
{
}

TcpCommandServer::~TcpCommandServer()
{
	Stop();
}

bool TcpCommandServer::Start()
{
#ifdef _WIN32
	WSADATA wsaData;
	if (WSAStartup(MAKEWORD(2, 2), &wsaData) != 0)
		return false;

	SOCKET listenSocket = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
	if (listenSocket == INVALID_SOCKET)
	{
		WSACleanup();
		return false;
	}

	sockaddr_in addr{};
	addr.sin_family = AF_INET;
	addr.sin_port = htons(m_port);
	inet_pton(AF_INET, "127.0.0.1", &addr.sin_addr); // localhost only, deliberately

	if (bind(listenSocket, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) == SOCKET_ERROR)
	{
		closesocket(listenSocket);
		WSACleanup();
		return false;
	}

	if (listen(listenSocket, SOMAXCONN) == SOCKET_ERROR)
	{
		closesocket(listenSocket);
		WSACleanup();
		return false;
	}

	m_listenSocket = listenSocket;
	m_stopRequested = false;
	m_running = true;
	m_acceptThread = std::thread(&TcpCommandServer::AcceptLoop, this);
	return true;
#else
	return false; // Sandbox/this project target Windows only
#endif
}

void TcpCommandServer::Stop()
{
#ifdef _WIN32
	if (!m_running) return;
	m_stopRequested = true;
	// Nudge the accept() call to return by closing the listen socket.
	closesocket(static_cast<SOCKET>(m_listenSocket));
	if (m_acceptThread.joinable())
		m_acceptThread.join();
	WSACleanup();
	m_running = false;
#endif
}

void TcpCommandServer::AcceptLoop()
{
#ifdef _WIN32
	while (!m_stopRequested)
	{
		SOCKET client = accept(static_cast<SOCKET>(m_listenSocket), nullptr, nullptr);
		if (client == INVALID_SOCKET)
		{
			if (m_stopRequested) break;
			continue;
		}
		// Handle synchronously on a fresh thread per connection -- traffic
		// here is Claude issuing one command at a time, not a real server
		// workload, so simplicity wins over a thread pool.
		std::thread(&TcpCommandServer::HandleClient, this, static_cast<unsigned long long>(client)).detach();
	}
#endif
}

void TcpCommandServer::HandleClient(unsigned long long clientSocketRaw)
{
#ifdef _WIN32
	SOCKET client = static_cast<SOCKET>(clientSocketRaw);
	std::string buffer;
	char chunk[4096];
	bool firstLine = true;
	constexpr size_t kMaxLineBytes = 256u * 1024u * 1024u;   // one request line; the biggest legitimate ones are base64 file writes

	while (true)
	{
		int received = recv(client, chunk, sizeof(chunk), 0);
		if (received <= 0) break;
		buffer.append(chunk, received);
		if (buffer.size() > kMaxLineBytes && buffer.find('\n') == std::string::npos)
			break;

		// A web page can make the browser open a TCP connection to 127.0.0.1 and POST text to it. Every request of this protocol is a JSON
		// object, so the first byte of the first line must be '{'. An HTTP request ("POST /...", "GET /...", "OPTIONS ...") starts with a
		// letter: the connection is dropped before anything is parsed or executed.
		if (firstLine && !buffer.empty())
		{
			size_t i = 0;
			while (i < buffer.size() && (buffer[i] == ' ' || buffer[i] == '\t' || buffer[i] == '\r' || buffer[i] == '\n'))
				++i;
			if (i < buffer.size())
			{
				if (buffer[i] != '{')
				{
					static const char reject[] = "{\"id\": null, \"ok\": false, \"error\": \"not a bridge request (expected a JSON object per line)\"}\n";
					send(client, reject, static_cast<int>(sizeof(reject) - 1), 0);
					break;
				}
				firstLine = false;
			}
		}

		size_t newlinePos;
		while ((newlinePos = buffer.find('\n')) != std::string::npos)
		{
			std::string line = buffer.substr(0, newlinePos);
			buffer.erase(0, newlinePos + 1);
			if (line.empty()) continue;

			std::string id, cmd;
			std::string response;
			if (!ParseRequest(line, id, cmd))
			{
				response = "{\"id\": null, \"ok\": false, \"error\": \"could not parse request\"}\n";
			}
			else
			{
				try
				{
					std::string output = m_handler(cmd);
					std::ostringstream oss;
					oss << "{\"id\": " << id << ", \"ok\": true, \"output\": \"" << JsonEscape(output) << "\"}\n";
					response = oss.str();
				}
				catch (const std::exception& e)
				{
					std::ostringstream oss;
					oss << "{\"id\": " << id << ", \"ok\": false, \"error\": \"" << JsonEscape(e.what()) << "\"}\n";
					response = oss.str();
				}
			}
			send(client, response.c_str(), static_cast<int>(response.size()), 0);
		}
	}

	closesocket(client);
#endif
}
