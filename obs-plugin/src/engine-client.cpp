#include "engine-client.hpp"

#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>

#include <obs-module.h>
#include <util/platform.h>
#include <plugin-support.h>

#include <chrono>
#include <cstring>
#include <random>

#include "json.hpp"

using json = nlohmann::json;

namespace {
constexpr int kHeader = 64;
constexpr int kSlots = 2;

std::string random_id()
{
	static const char *hex = "0123456789abcdef";
	std::random_device rd;
	std::string s;
	for (int i = 0; i < 12; i++)
		s += hex[rd() % 16];
	return s;
}

std::vector<uint8_t> base64_decode(const std::string &in)
{
	static int table[256];
	static bool init = false;
	if (!init) {
		for (int &v : table)
			v = -1;
		const char *chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
		for (int i = 0; i < 64; i++)
			table[(unsigned char)chars[i]] = i;
		init = true;
	}
	std::vector<uint8_t> out;
	out.reserve(in.size() * 3 / 4);
	int val = 0, bits = -8;
	for (unsigned char c : in) {
		if (table[c] < 0)
			continue;
		val = (val << 6) + table[c];
		bits += 6;
		if (bits >= 0) {
			out.push_back(uint8_t((val >> bits) & 0xFF));
			bits -= 8;
		}
	}
	return out;
}

std::wstring widen(const std::string &s)
{
	int n = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), -1, nullptr, 0);
	std::wstring w(n > 0 ? n - 1 : 0, L'\0');
	if (n > 0)
		MultiByteToWideChar(CP_UTF8, 0, s.c_str(), -1, w.data(), n);
	return w;
}

void parse_panels(const json &arr, std::vector<PanelRef> &out, std::vector<PanelImage> &updates)
{
	for (const auto &p : arr) {
		PanelRef ref;
		ref.key = p.value("key", "");
		ref.ver = p.value("ver", 0u);
		ref.anchor = p.value("anchor", "bottom");
		ref.w = p.value("w", 0);
		ref.h = p.value("h", 0);
		if (p.contains("data")) {
			PanelImage img{ref.key, ref.ver, ref.w, ref.h, base64_decode(p["data"].get<std::string>())};
			if (img.rgba.size() == size_t(img.w) * img.h * 4)
				updates.push_back(std::move(img));
		}
		out.push_back(std::move(ref));
	}
}
} // namespace

EngineClient::EngineClient(std::string source_name) : source_name_(std::move(source_name)), instance_id_(random_id())
{
	WSADATA wsa;
	WSAStartup(MAKEWORD(2, 2), &wsa);
	slot_size_ = size_t(kMaxWidth) * kMaxHeight * 4;
	net_thread_ = std::thread(&EngineClient::net_loop, this);
	send_thread_ = std::thread(&EngineClient::send_loop, this);
}

EngineClient::~EngineClient()
{
	running_ = false;
	send_cv_.notify_all();
	disconnect();
	if (net_thread_.joinable())
		net_thread_.join();
	if (send_thread_.joinable())
		send_thread_.join();
	if (shm_)
		UnmapViewOfFile(shm_);
	if (mapping_)
		CloseHandle(mapping_);
	WSACleanup();
}

void EngineClient::set_settings(const std::string &settings_json)
{
	std::lock_guard<std::mutex> lock(mutex_);
	if (settings_json == settings_json_)
		return;
	settings_json_ = settings_json;
	settings_dirty_ = true;
	send_cv_.notify_all();
}

void EngineClient::set_source_name(const std::string &name)
{
	{
		std::lock_guard<std::mutex> lock(mutex_);
		if (name.empty() || name == source_name_)
			return;
		source_name_ = name;
	}
	disconnect(); // neues hello mit richtigem Namen
}

void EngineClient::set_endpoint(const std::string &host, int port)
{
	bool changed;
	{
		std::lock_guard<std::mutex> lock(mutex_);
		changed = host != host_ || port != port_;
		host_ = host;
		port_ = port;
	}
	if (changed)
		disconnect();
}

void EngineClient::set_autostart(bool enabled, const std::string &command)
{
	std::lock_guard<std::mutex> lock(mutex_);
	autostart_ = enabled;
	autostart_cmd_ = command;
}

void EngineClient::request_snapshot()
{
	std::lock_guard<std::mutex> lock(mutex_);
	snapshot_requested_ = true;
	send_cv_.notify_all();
}

uint8_t *EngineClient::acquire_slot(int width, int height, int &slot, int &stride)
{
	if (!connected_ || !shm_ || width > kMaxWidth || height > kMaxHeight)
		return nullptr;
	std::lock_guard<std::mutex> lock(mutex_);
	slot = next_slot_;
	next_slot_ = (next_slot_ + 1) % kSlots;
	stride = width * 4;
	return shm_ + kHeader + size_t(slot) * slot_size_;
}

void EngineClient::commit_frame(int slot, int width, int height, int stride)
{
	std::lock_guard<std::mutex> lock(mutex_);
	frame_pending_ = true;
	pending_slot_ = slot;
	pending_w_ = width;
	pending_h_ = height;
	pending_stride_ = stride;
	send_cv_.notify_all();
}

bool EngineClient::latest_result(ResultFrame &out, uint64_t &last_seq)
{
	std::lock_guard<std::mutex> lock(mutex_);
	if (result_.seq == last_seq)
		return false;
	out = result_;
	last_seq = result_.seq;
	return true;
}

std::vector<PanelImage> EngineClient::take_panel_updates()
{
	std::lock_guard<std::mutex> lock(mutex_);
	std::vector<PanelImage> out;
	out.swap(panel_updates_);
	return out;
}

std::string EngineClient::status()
{
	std::lock_guard<std::mutex> lock(mutex_);
	return status_;
}

// ------------------------------------------------------------------ Netzwerk

bool EngineClient::connect_once()
{
	std::string host;
	int port;
	{
		std::lock_guard<std::mutex> lock(mutex_);
		host = host_;
		port = port_;
	}
	addrinfo hints{}, *res = nullptr;
	hints.ai_family = AF_INET;
	hints.ai_socktype = SOCK_STREAM;
	if (getaddrinfo(host.c_str(), std::to_string(port).c_str(), &hints, &res) != 0)
		return false;
	SOCKET s = socket(res->ai_family, res->ai_socktype, res->ai_protocol);
	bool ok = s != INVALID_SOCKET && ::connect(s, res->ai_addr, (int)res->ai_addrlen) == 0;
	freeaddrinfo(res);
	if (!ok) {
		if (s != INVALID_SOCKET)
			closesocket(s);
		return false;
	}
	BOOL nodelay = TRUE;
	setsockopt(s, IPPROTO_TCP, TCP_NODELAY, (const char *)&nodelay, sizeof(nodelay));

	if (!shm_) {
		shm_name_ = "Local\\tcglens_" + instance_id_;
		const size_t size = kHeader + kSlots * slot_size_;
		mapping_ = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, DWORD(uint64_t(size) >> 32),
					      DWORD(size & 0xFFFFFFFF), widen(shm_name_).c_str());
		if (!mapping_) {
			closesocket(s);
			return false;
		}
		shm_ = (uint8_t *)MapViewOfFile(mapping_, FILE_MAP_ALL_ACCESS, 0, 0, size);
		memcpy(shm_, "TCGL", 4);
		uint32_t hdr[3] = {1, kSlots, (uint32_t)slot_size_};
		memcpy(shm_ + 4, hdr, sizeof(hdr));
	}
	sock_ = (uintptr_t)s;
	std::string current_name;
	{
		std::lock_guard<std::mutex> lock(mutex_);
		current_name = source_name_;
	}
	json hello = {{"type", "hello"},
		      {"proto", 1},
		      {"instance", instance_id_},
		      {"source", current_name},
		      {"shm", shm_name_},
		      {"shm_size", kHeader + kSlots * slot_size_},
		      {"slot_size", slot_size_}};
	{
		std::lock_guard<std::mutex> lock(mutex_);
		hello["settings"] = json::parse(settings_json_, nullptr, false);
		settings_dirty_ = false;
	}
	if (!send_json(hello.dump())) {
		disconnect();
		return false;
	}
	connected_ = true;
	obs_log(LOG_INFO, "[%s] mit Engine verbunden (%s:%d)", source_name_.c_str(), host.c_str(), port);
	return true;
}

void EngineClient::disconnect()
{
	std::lock_guard<std::mutex> lock(send_mutex_);
	if (sock_ != ~uintptr_t(0)) {
		shutdown((SOCKET)sock_, SD_BOTH);
		closesocket((SOCKET)sock_);
		sock_ = ~uintptr_t(0);
	}
	connected_ = false;
}

bool EngineClient::send_json(const std::string &payload)
{
	std::lock_guard<std::mutex> lock(send_mutex_);
	if (sock_ == ~uintptr_t(0))
		return false;
	uint32_t n = (uint32_t)payload.size();
	std::string buf(reinterpret_cast<const char *>(&n), 4);
	buf += payload;
	size_t sent = 0;
	while (sent < buf.size()) {
		int r = ::send((SOCKET)sock_, buf.data() + sent, int(buf.size() - sent), 0);
		if (r <= 0)
			return false;
		sent += size_t(r);
	}
	return true;
}

void EngineClient::maybe_autostart()
{
	std::string cmd;
	{
		std::lock_guard<std::mutex> lock(mutex_);
		const uint64_t now = os_gettime_ns();
		if (!autostart_ || autostart_cmd_.empty() || now - last_autostart_ns_ < 60ull * 1000000000ull)
			return;
		last_autostart_ns_ = now;
		cmd = autostart_cmd_;
	}
	// Mehrere Filterinstanzen sollen die Engine nur einmal starten
	HANDLE mutex = CreateMutexW(nullptr, FALSE, L"Local\\tcglens_engine_start");
	if (mutex && GetLastError() != ERROR_ALREADY_EXISTS) {
		// Ohne Konsolenfenster; Ausgabe landet in engine\data\engine.log (siehe start-engine.cmd --hidden)
		std::wstring wcmd = L"cmd.exe /c \"\"" + widen(cmd) + L"\" --hidden\"";
		STARTUPINFOW si{sizeof(si)};
		PROCESS_INFORMATION pi{};
		if (CreateProcessW(nullptr, wcmd.data(), nullptr, nullptr, FALSE, CREATE_NO_WINDOW,
				   nullptr, nullptr, &si, &pi)) {
			CloseHandle(pi.hThread);
			CloseHandle(pi.hProcess);
			obs_log(LOG_INFO, "Engine gestartet: %s", cmd.c_str());
		} else {
			obs_log(LOG_WARNING, "Engine konnte nicht gestartet werden: %s", cmd.c_str());
		}
	}
	// Mutex bleibt 60 s bestehen, dann kann eine andere Instanz erneut starten
	if (mutex) {
		std::thread([mutex] {
			std::this_thread::sleep_for(std::chrono::seconds(60));
			CloseHandle(mutex);
		}).detach();
	}
}

void EngineClient::net_loop()
{
	std::string buf;
	int failures = 0;
	while (running_) {
		if (!connected_) {
			if (!connect_once()) {
				{
					std::lock_guard<std::mutex> lock(mutex_);
					status_ = "Engine nicht erreichbar – wird gesucht …";
				}
				if (++failures >= 2)
					maybe_autostart();
				for (int i = 0; i < 20 && running_; i++)
					std::this_thread::sleep_for(std::chrono::milliseconds(100));
				continue;
			}
			failures = 0;
		}
		// Länge + Nachricht lesen
		uint32_t n = 0;
		char *p = reinterpret_cast<char *>(&n);
		int got = 0;
		while (got < 4 && running_) {
			int r = recv((SOCKET)sock_, p + got, 4 - got, 0);
			if (r <= 0)
				break;
			got += r;
		}
		if (got < 4 || n > 64u * 1024 * 1024) {
			disconnect();
			continue;
		}
		buf.resize(n);
		size_t read = 0;
		while (read < n && running_) {
			int r = recv((SOCKET)sock_, buf.data() + read, int(n - read), 0);
			if (r <= 0)
				break;
			read += size_t(r);
		}
		if (read < n) {
			disconnect();
			continue;
		}
		handle_message(buf);
	}
}

void EngineClient::handle_message(const std::string &text)
{
	json msg = json::parse(text, nullptr, false);
	if (msg.is_discarded())
		return;
	const std::string type = msg.value("type", "");
	if (type == "welcome" || type == "status") {
		std::lock_guard<std::mutex> lock(mutex_);
		status_ = type == "welcome" ? "Verbunden – " + msg.value("status", "") : msg.value("text", "");
		return;
	}
	if (type != "result")
		return;
	ResultFrame r;
	std::vector<PanelImage> updates;
	r.seq = msg.value("seq", 0ull);
	for (const auto &t : msg["tracks"]) {
		TrackResult tr;
		tr.id = t.value("id", 0);
		const auto &c = t["corners"];
		for (int i = 0; i < 4 && i < (int)c.size(); i++) {
			tr.corners[i * 2] = c[i][0].get<float>();
			tr.corners[i * 2 + 1] = c[i][1].get<float>();
		}
		const auto &col = t["color"];
		for (int i = 0; i < 3 && i < (int)col.size(); i++)
			tr.color[i] = col[i].get<float>() / 255.f;
		tr.thickness = t.value("thickness", 3.f);
		// Optional (neuere Engine): Kennzeichen und Hervorhebung; fehlend/falscher Typ → Standardwerte
		if (t.contains("label") && t["label"].is_string())
			tr.label = t["label"].get<std::string>();
		if (t.contains("highlight") && t["highlight"].is_boolean())
			tr.highlight = t["highlight"].get<bool>();
		parse_panels(t["panels"], tr.panels, updates);
		r.tracks.push_back(std::move(tr));
	}
	if (msg.contains("debug")) {
		for (const auto &d : msg["debug"]["detections"]) {
			std::array<float, 8> q{};
			for (int i = 0; i < 4 && i < (int)d.size(); i++) {
				q[i * 2] = d[i][0].get<float>();
				q[i * 2 + 1] = d[i][1].get<float>();
			}
			r.detections.push_back(q);
		}
	}
	if (msg.contains("screen_panels"))
		parse_panels(msg["screen_panels"], r.screen_panels, updates);
	std::lock_guard<std::mutex> lock(mutex_);
	result_ = std::move(r);
	for (auto &u : updates)
		panel_updates_.push_back(std::move(u));
	status_ = "Verbunden – " + msg.value("status", std::string("bereit"));
}

void EngineClient::send_loop()
{
	while (running_) {
		std::string payload;
		{
			std::unique_lock<std::mutex> lock(mutex_);
			send_cv_.wait_for(lock, std::chrono::milliseconds(500), [this] {
				return !running_ || (connected_ && (settings_dirty_ || snapshot_requested_ || frame_pending_));
			});
			if (!running_)
				break;
			if (!connected_)
				continue;
			if (settings_dirty_) {
				json m = {{"type", "settings"}, {"settings", json::parse(settings_json_, nullptr, false)}};
				payload = m.dump();
				settings_dirty_ = false;
			} else if (snapshot_requested_) {
				payload = R"({"type":"snapshot"})";
				snapshot_requested_ = false;
			} else if (frame_pending_) {
				json m = {{"type", "frame"},  {"seq", ++seq_},          {"slot", pending_slot_},
					  {"width", pending_w_}, {"height", pending_h_}, {"stride", pending_stride_},
					  {"ts", os_gettime_ns() / 1000000000.0}};
				payload = m.dump();
				frame_pending_ = false;
			}
		}
		if (!payload.empty() && !send_json(payload))
			disconnect();
	}
}
