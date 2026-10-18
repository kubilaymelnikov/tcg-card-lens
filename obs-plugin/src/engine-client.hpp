#pragma once
// Verbindung zur Engine (Protokoll: docs/PLUGIN.md). Netzwerk in eigenen Threads; der OBS-Grafik-Thread ruft nur
// nicht blockierende Methoden auf (acquire_slot/commit_frame/latest_result/take_panel_updates).

#include <array>
#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

struct PanelRef {
	std::string key;
	uint32_t ver = 0;
	std::string anchor; // "top" | "bottom" | "corner" | "screen"
	std::string pos;    // nur "screen": top_left | top_center | top_right | bottom_left | bottom_right (leer = bottom_left)
	int w = 0, h = 0;
};

// Pack-Opening: sicher erkannte Karte über der Hit-Schwelle (Engine meldet jeden Hit genau einmal)
struct HitEvent {
	int track = 0;
	std::string key, name, currency;
	double price = 0.0;
};

struct TrackResult {
	int id = 0;
	std::array<float, 8> corners{}; // x0,y0 … x3,y3 in Analyse-Koordinaten
	std::array<float, 3> color{1.f, 1.f, 1.f};
	float thickness = 3.f;
	std::string label;      // Kennzeichen (optional; ältere Engine: leer)
	bool highlight = false; // Rahmen pulsierend mit Außenschein (optional)
	bool hit = false;       // Pack-Opening: goldener, pulsierender Hit-Rahmen (optional)
	std::vector<PanelRef> panels;
};

struct ResultFrame {
	uint64_t seq = 0;
	int analysis_w = 0, analysis_h = 0;
	std::vector<TrackResult> tracks;
	std::vector<std::array<float, 8>> detections; // nur im Debug-Modus
	std::vector<PanelRef> screen_panels;          // z. B. Debug-Statuszeile
};

struct PanelImage {
	std::string key;
	uint32_t ver = 0;
	int w = 0, h = 0;
	std::vector<uint8_t> rgba;
};

class EngineClient {
public:
	explicit EngineClient(std::string source_name);
	~EngineClient();

	void set_settings(const std::string &settings_json);
	void set_source_name(const std::string &name); // Name der OBS-Quelle (für Logs/Snapshots); verbindet neu
	void set_endpoint(const std::string &host, int port);
	void set_autostart(bool enabled, const std::string &command);
	void request_snapshot();
	void request_pack_reset(); // Pack-Opening: neue Session

	// Grafik-Thread: freien Slot holen (nullptr, wenn nicht verbunden), dann Frame melden
	uint8_t *acquire_slot(int width, int height, int &slot, int &stride);
	void commit_frame(int slot, int width, int height, int stride);

	bool latest_result(ResultFrame &out, uint64_t &last_seq);
	std::vector<PanelImage> take_panel_updates();
	std::vector<HitEvent> take_events(); // Hit-Ereignisse seit dem letzten Aufruf (gehen nicht verloren, wenn
					     // ein Ergebnis vom nächsten überholt wird)
	std::string status();
	// Einstellungsänderung aus dem Dock (JSON-Objekt, z. B. {"auto_detect": false}); leer, wenn keine anliegt
	std::string take_settings_patch();
	bool connected() const { return connected_.load(); }

	static constexpr int kMaxWidth = 1920;
	static constexpr int kMaxHeight = 1080;

private:
	void net_loop();
	void send_loop();
	bool connect_once();
	void disconnect();
	bool send_json(const std::string &json);
	void handle_message(const std::string &json);
	void maybe_autostart();

	std::string source_name_;
	std::string instance_id_;
	std::string host_ = "127.0.0.1";
	int port_ = 47820;

	std::atomic<bool> running_{true};
	std::atomic<bool> connected_{false};
	std::thread net_thread_, send_thread_;

	std::mutex mutex_; // schützt alles darunter
	std::condition_variable send_cv_;
	std::string settings_json_ = "{}";
	bool settings_dirty_ = true;
	bool snapshot_requested_ = false;
	bool pack_reset_requested_ = false;
	bool frame_pending_ = false;
	int pending_slot_ = 0, pending_w_ = 0, pending_h_ = 0, pending_stride_ = 0;
	uint64_t seq_ = 0;
	int next_slot_ = 0;
	ResultFrame result_;
	std::vector<PanelImage> panel_updates_;
	std::vector<HitEvent> events_;
	std::string settings_patch_; // noch nicht übernommene Einstellungsänderung aus dem Dock
	std::string status_ = "Engine nicht verbunden";
	bool autostart_ = false;
	std::string autostart_cmd_;
	uint64_t last_autostart_ns_ = 0;

	// Socket/Shared Memory (nur Netzwerk-Threads)
	uintptr_t sock_ = ~uintptr_t(0);
	std::mutex send_mutex_;
	void *mapping_ = nullptr;
	uint8_t *shm_ = nullptr;
	size_t slot_size_ = 0;
	std::string shm_name_;
};
