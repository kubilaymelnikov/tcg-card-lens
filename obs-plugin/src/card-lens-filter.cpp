// OBS-Videofilter „TCG Card Lens“: liest das Bild der Quelle zurück, schickt es an die Engine und zeichnet Rahmen +
// Info-Panels der erkannten Karten direkt ins gefilterte Videobild.

#include <obs-module.h>
#include <graphics/vec4.h>
#include <util/platform.h>
#include <plugin-support.h>

#include <algorithm>
#include <cmath>
#include <cstring>
#include <memory>
#include <string>

#include "engine-client.hpp"
#include "json.hpp"
#include "overlay-draw.hpp"

using json = nlohmann::json;

namespace {

const char *kLanguages[][2] = {{"de", "Deutsch"},      {"en", "Englisch"},      {"fr", "Französisch"},
			       {"it", "Italienisch"},  {"es", "Spanisch"},      {"pt", "Portugiesisch"},
			       {"ja", "Japanisch"}};

// Kartenspiele: je eine Checkbox (Einstellung "game_<kürzel>"), an die Engine als Kommaliste "pokemon,mtg,ygo"
const char *kGames[][2] = {{"pokemon", "Pokémon"}, {"mtg", "Magic: The Gathering"}, {"ygo", "Yu-Gi-Oh!"}};

struct Filter {
	obs_source_t *source = nullptr;
	std::unique_ptr<EngineClient> client;
	OverlayDrawer drawer;
	OverlayStyle style;
	bool enabled = true;
	double analysis_fps = 15.0;

	gs_texrender_t *texrender = nullptr;
	gs_stagesurf_t *stage = nullptr;
	uint32_t stage_w = 0, stage_h = 0;
	int logged_maps = 0;
	bool named = false;
	uint64_t last_capture_ns = 0;
	uint64_t last_seq = 0;
	ResultFrame result;
	uint32_t src_w = 0, src_h = 0, ana_w = 0, ana_h = 0;
};

uint32_t color_from_settings(obs_data_t *s, const char *name)
{
	return (uint32_t)obs_data_get_int(s, name);
}

json rgb_json(uint32_t abgr)
{
	// OBS speichert Farben als 0xAABBGGRR
	return json::array({int(abgr & 0xFF), int((abgr >> 8) & 0xFF), int((abgr >> 16) & 0xFF)});
}

// Ältere Einstellungen (Auswahlliste "games": both | pokemon | mtg) einmalig in die Checkboxen übernehmen
void migrate_games(obs_data_t *s)
{
	if (!obs_data_has_user_value(s, "games"))
		return;
	std::string old = obs_data_get_string(s, "games");
	if (old == "pokemon" || old == "mtg") {
		obs_data_set_bool(s, "game_pokemon", old == "pokemon");
		obs_data_set_bool(s, "game_mtg", old == "mtg");
	}
	obs_data_erase(s, "games");
}

std::string games_setting(obs_data_t *s)
{
	std::string out;
	for (auto &g : kGames)
		if (obs_data_get_bool(s, (std::string("game_") + g[0]).c_str()))
			out += (out.empty() ? "" : ",") + std::string(g[0]);
	return out.empty() ? "both" : out; // nichts gewählt → wie bisher Pokémon + Magic
}

std::string settings_to_json(obs_data_t *s)
{
	json langs = json::array();
	for (auto &l : kLanguages)
		if (obs_data_get_bool(s, (std::string("lang_") + l[0]).c_str()))
			langs.push_back(l[0]);
	if (langs.empty())
		langs.push_back("en");
	json j = {
		{"enabled", obs_data_get_bool(s, "enabled")},
		{"games", games_setting(s)},
		{"mode", obs_data_get_string(s, "mode")},
		{"sensitivity", obs_data_get_double(s, "sensitivity")},
		{"hold_time", obs_data_get_double(s, "hold_time")},
		{"smoothing", obs_data_get_double(s, "smoothing")},
		{"languages", langs},
		{"display_language", obs_data_get_string(s, "display_language")},
		{"show_name", obs_data_get_bool(s, "show_name")},
		{"show_price", obs_data_get_bool(s, "show_price")},
		{"show_history", obs_data_get_bool(s, "show_history")},
		{"history_days", obs_data_get_int(s, "history_days")},
		{"scale", obs_data_get_double(s, "scale")},
		{"thickness", obs_data_get_double(s, "thickness")},
		{"color_pokemon", rgb_json(color_from_settings(s, "color_pokemon"))},
		{"color_mtg", rgb_json(color_from_settings(s, "color_mtg"))},
		{"color_ygo", rgb_json(color_from_settings(s, "color_ygo"))},
		{"debug", obs_data_get_bool(s, "debug")},
		{"auto_snapshots", obs_data_get_bool(s, "auto_snapshots")},
		{"snapshot_limit_mb", obs_data_get_int(s, "snapshot_limit_mb")},
	};
	return j.dump();
}

std::string default_engine_command()
{
	const char *local = getenv("LOCALAPPDATA");
	if (!local)
		return "";
	std::string path = std::string(local) + "\\TCGCardLens\\engine\\start-engine.cmd";
	return os_file_exists(path.c_str()) ? path : "";
}

// ------------------------------------------------------------------ Lebenszyklus

const char *filter_name(void *)
{
	return obs_module_text("CardLens");
}

void filter_update(void *data, obs_data_t *s)
{
	auto *f = static_cast<Filter *>(data);
	migrate_games(s);
	f->enabled = obs_data_get_bool(s, "enabled");
	f->analysis_fps = obs_data_get_double(s, "analysis_fps");
	f->style.debug = obs_data_get_bool(s, "debug");
	f->style.smoothing = (float)obs_data_get_double(s, "render_smoothing");
	f->client->set_endpoint(obs_data_get_string(s, "engine_host"), (int)obs_data_get_int(s, "engine_port"));
	f->client->set_autostart(obs_data_get_bool(s, "engine_autostart"), obs_data_get_string(s, "engine_command"));
	f->client->set_settings(settings_to_json(s));
}

void *filter_create(obs_data_t *settings, obs_source_t *source)
{
	auto *f = new Filter();
	f->source = source;
	obs_source_t *parent = obs_filter_get_parent(source);
	const char *name = parent ? obs_source_get_name(parent) : obs_source_get_name(source);
	f->client = std::make_unique<EngineClient>(name ? name : "quelle");
	obs_enter_graphics();
	f->texrender = gs_texrender_create(GS_BGRA, GS_ZS_NONE);
	obs_leave_graphics();
	filter_update(f, settings);
	return f;
}

void filter_destroy(void *data)
{
	auto *f = static_cast<Filter *>(data);
	f->client.reset(); // Netzwerk-Threads beenden, bevor Grafikressourcen freigegeben werden
	f->drawer.clear();
	obs_enter_graphics();
	if (f->stage)
		gs_stagesurface_destroy(f->stage);
	if (f->texrender)
		gs_texrender_destroy(f->texrender);
	obs_leave_graphics();
	delete f;
}

void filter_defaults(obs_data_t *s)
{
	obs_data_set_default_bool(s, "enabled", true);
	obs_data_set_default_bool(s, "game_pokemon", true);
	obs_data_set_default_bool(s, "game_mtg", true);
	obs_data_set_default_bool(s, "game_ygo", true); // ohne installierte Yu-Gi-Oh-Daten sucht die Engine dort einfach nichts
	obs_data_set_default_string(s, "mode", "facecam");
	obs_data_set_default_double(s, "sensitivity", 0.4);
	obs_data_set_default_double(s, "hold_time", 1.2);
	obs_data_set_default_double(s, "smoothing", 0.5);
	obs_data_set_default_double(s, "render_smoothing", 0.45);
	obs_data_set_default_double(s, "analysis_fps", 15.0);
	obs_data_set_default_bool(s, "lang_de", true);
	obs_data_set_default_bool(s, "lang_en", true);
	obs_data_set_default_string(s, "display_language", "de");
	obs_data_set_default_bool(s, "show_name", true);
	obs_data_set_default_bool(s, "show_price", true);
	obs_data_set_default_bool(s, "show_history", true);
	obs_data_set_default_int(s, "history_days", 90);
	obs_data_set_default_double(s, "scale", 1.0);
	obs_data_set_default_double(s, "thickness", 3.0);
	obs_data_set_default_int(s, "color_pokemon", 0xFF3FD2FF); // 0xAABBGGRR → RGB(255,210,63)
	obs_data_set_default_int(s, "color_mtg", 0xFF3D8AFF);     // RGB(255,138,61)
	obs_data_set_default_int(s, "color_ygo", 0xFFFF6EB0);     // RGB(176,110,255)
	obs_data_set_default_bool(s, "debug", false);
	obs_data_set_default_bool(s, "auto_snapshots", true);
	obs_data_set_default_int(s, "snapshot_limit_mb", 500);
	obs_data_set_default_string(s, "engine_host", "127.0.0.1");
	obs_data_set_default_int(s, "engine_port", 47820);
	// Autostart standardmäßig an: Der Installer legt die Engine unter %LOCALAPPDATA%\TCGCardLens\engine ab
	obs_data_set_default_bool(s, "engine_autostart", true);
	obs_data_set_default_string(s, "engine_command", default_engine_command().c_str());
}

bool snapshot_clicked(obs_properties_t *, obs_property_t *, void *data)
{
	static_cast<Filter *>(data)->client->request_snapshot();
	return false;
}

bool refresh_status(obs_properties_t *props, obs_property_t *, void *data)
{
	auto *f = static_cast<Filter *>(data);
	obs_property_t *p = obs_properties_get(props, "status");
	obs_property_set_description(p, (std::string(obs_module_text("Status")) + ": " + f->client->status()).c_str());
	return true;
}

obs_properties_t *filter_properties(void *data)
{
	auto *f = static_cast<Filter *>(data);
	obs_properties_t *props = obs_properties_create();

	std::string status = std::string(obs_module_text("Status")) + ": " + (f ? f->client->status() : "-");
	obs_properties_add_text(props, "status", status.c_str(), OBS_TEXT_INFO);
	obs_properties_add_button(props, "refresh_status", obs_module_text("RefreshStatus"), refresh_status);

	// Allgemein
	obs_properties_t *g = obs_properties_create();
	obs_properties_add_bool(g, "enabled", obs_module_text("Enabled"));
	obs_properties_add_text(g, "games_label", obs_module_text("Games"), OBS_TEXT_INFO);
	for (auto &game : kGames)
		obs_properties_add_bool(g, (std::string("game_") + game[0]).c_str(), game[1]);
	obs_property_t *mode = obs_properties_add_list(g, "mode", obs_module_text("Mode"), OBS_COMBO_TYPE_LIST,
						       OBS_COMBO_FORMAT_STRING);
	obs_property_list_add_string(mode, obs_module_text("Mode.Facecam"), "facecam");
	obs_property_list_add_string(mode, obs_module_text("Mode.Table"), "table");
	obs_properties_add_float_slider(g, "sensitivity", obs_module_text("Sensitivity"), 0.1, 0.9, 0.05);
	obs_properties_add_float_slider(g, "hold_time", obs_module_text("HoldTime"), 0.2, 5.0, 0.1);
	obs_properties_add_float_slider(g, "smoothing", obs_module_text("Smoothing"), 0.0, 0.9, 0.05);
	obs_properties_add_float_slider(g, "render_smoothing", obs_module_text("RenderSmoothing"), 0.0, 0.9, 0.05);
	obs_properties_add_float_slider(g, "analysis_fps", obs_module_text("AnalysisFps"), 5.0, 30.0, 1.0);
	obs_properties_add_group(props, "grp_general", obs_module_text("Group.General"), OBS_GROUP_NORMAL, g);

	// Sprachen
	obs_properties_t *l = obs_properties_create();
	for (auto &lang : kLanguages)
		obs_properties_add_bool(l, (std::string("lang_") + lang[0]).c_str(), lang[1]);
	obs_property_t *disp = obs_properties_add_list(l, "display_language", obs_module_text("DisplayLanguage"),
						       OBS_COMBO_TYPE_LIST, OBS_COMBO_FORMAT_STRING);
	for (auto &lang : kLanguages)
		obs_property_list_add_string(disp, lang[1], lang[0]);
	obs_properties_add_group(props, "grp_lang", obs_module_text("Group.Languages"), OBS_GROUP_NORMAL, l);

	// Overlay
	obs_properties_t *o = obs_properties_create();
	obs_properties_add_bool(o, "show_name", obs_module_text("ShowName"));
	obs_properties_add_bool(o, "show_price", obs_module_text("ShowPrice"));
	obs_properties_add_bool(o, "show_history", obs_module_text("ShowHistory"));
	obs_property_t *days = obs_properties_add_list(o, "history_days", obs_module_text("HistoryDays"),
						       OBS_COMBO_TYPE_LIST, OBS_COMBO_FORMAT_INT);
	obs_property_list_add_int(days, obs_module_text("Days30"), 30);
	obs_property_list_add_int(days, obs_module_text("Days90"), 90);
	obs_property_list_add_int(days, obs_module_text("Days180"), 180);
	obs_properties_add_float_slider(o, "scale", obs_module_text("Scale"), 0.5, 2.5, 0.05);
	obs_properties_add_float_slider(o, "thickness", obs_module_text("Thickness"), 1.0, 12.0, 0.5);
	obs_properties_add_color(o, "color_pokemon", obs_module_text("ColorPokemon"));
	obs_properties_add_color(o, "color_mtg", obs_module_text("ColorMtg"));
	obs_properties_add_color(o, "color_ygo", obs_module_text("ColorYgo"));
	obs_properties_add_group(props, "grp_overlay", obs_module_text("Group.Overlay"), OBS_GROUP_NORMAL, o);

	// Debug
	obs_properties_t *d = obs_properties_create();
	obs_properties_add_bool(d, "debug", obs_module_text("Debug"));
	obs_properties_add_button(d, "snapshot", obs_module_text("Snapshot"), snapshot_clicked);
	obs_properties_add_bool(d, "auto_snapshots", obs_module_text("AutoSnapshots"));
	obs_properties_add_int(d, "snapshot_limit_mb", obs_module_text("SnapshotLimit"), 50, 20000, 50);
	obs_properties_add_group(props, "grp_debug", obs_module_text("Group.Debug"), OBS_GROUP_NORMAL, d);

	// Engine
	obs_properties_t *e = obs_properties_create();
	obs_properties_add_text(e, "engine_host", obs_module_text("EngineHost"), OBS_TEXT_DEFAULT);
	obs_properties_add_int(e, "engine_port", obs_module_text("EnginePort"), 1024, 65535, 1);
	obs_properties_add_bool(e, "engine_autostart", obs_module_text("EngineAutostart"));
	obs_properties_add_path(e, "engine_command", obs_module_text("EngineCommand"), OBS_PATH_FILE,
				"Startskript (*.cmd *.bat *.exe)", nullptr);
	obs_properties_add_group(props, "grp_engine", obs_module_text("Group.Engine"), OBS_GROUP_NORMAL, e);
	return props;
}

// ------------------------------------------------------------------ Rendern

void capture(Filter *f, uint32_t w, uint32_t h)
{
	// Gedrosselt: Quelle in Analyse-Auflösung rendern, auf CPU-Seite kopieren, in den Shared-Memory-Slot legen.
	// Vorgehen wie in obs-backgroundremoval (render → stage → map im selben Frame).
	const uint64_t now = os_gettime_ns();
	if (!obs_source_enabled(f->source) || !f->client->connected() ||
	    now - f->last_capture_ns < uint64_t(1e9 / f->analysis_fps))
		return;
	const uint32_t aw = std::min<uint32_t>(w, EngineClient::kMaxWidth);
	const uint32_t ah = (uint32_t)std::lround(double(h) * aw / w);
	if (ah > EngineClient::kMaxHeight)
		return;
	f->last_capture_ns = now;

	gs_texrender_reset(f->texrender);
	if (!gs_texrender_begin(f->texrender, aw, ah)) {
		obs_log(LOG_WARNING, "gs_texrender_begin fehlgeschlagen (%ux%u)", aw, ah);
		return;
	}
	struct vec4 background;
	vec4_zero(&background);
	gs_clear(GS_CLEAR_COLOR, &background, 0.0f, 0);
	gs_ortho(0.0f, (float)w, 0.0f, (float)h, -100.0f, 100.0f);
	gs_blend_state_push();
	gs_blend_function(GS_BLEND_ONE, GS_BLEND_ZERO);
	// Wie OBS selbst den nächsten Schritt der Filterkette zeichnet. obs_source_video_render(target) zeichnet bei
	// synchronen Quellen (Bild, Spielaufnahme …) innerhalb eines Filters ohne aktiven Effekt nichts (libobs
	// obs_source_main_render) – das Rücklesen wäre schwarz. skip_video_filter behandelt alle Quelltypen.
	obs_source_skip_video_filter(f->source);
	gs_blend_state_pop();
	gs_texrender_end(f->texrender);

	if (!f->stage || f->stage_w != aw || f->stage_h != ah) {
		if (f->stage)
			gs_stagesurface_destroy(f->stage);
		f->stage = gs_stagesurface_create(aw, ah, GS_BGRA);
		f->stage_w = aw;
		f->stage_h = ah;
	}
	gs_stage_texture(f->stage, gs_texrender_get_texture(f->texrender));
	uint8_t *data = nullptr;
	uint32_t linesize = 0;
	if (!gs_stagesurface_map(f->stage, &data, &linesize))
		return;
	if (f->logged_maps < 3) {
		// Diagnose: mittlere Helligkeit einer Bildzeile (0 = schwarzes Bild)
		uint64_t sum = 0;
		const uint8_t *row = data + size_t(ah / 2) * linesize;
		for (uint32_t x = 0; x < aw * 4; x++)
			sum += row[x];
		obs_log(LOG_INFO, "Rücklesen %ux%u, Zeilenlänge %u, mittlerer Wert Bildmitte %.1f", aw, ah, linesize,
			double(sum) / (aw * 4));
		f->logged_maps++;
	}
	int slot = 0, stride = 0;
	uint8_t *dst = f->client->acquire_slot((int)aw, (int)ah, slot, stride);
	if (dst) {
		for (uint32_t y = 0; y < ah; y++)
			memcpy(dst + size_t(y) * stride, data + size_t(y) * linesize, size_t(aw) * 4);
		f->client->commit_frame(slot, (int)aw, (int)ah, stride);
	}
	gs_stagesurface_unmap(f->stage);
	f->ana_w = aw;
	f->ana_h = ah;
}

void filter_render(void *data, gs_effect_t *)
{
	auto *f = static_cast<Filter *>(data);
	obs_source_t *target = obs_filter_get_target(f->source);
	obs_source_t *parent = obs_filter_get_parent(f->source);
	const uint32_t w = target ? obs_source_get_base_width(target) : 0;
	const uint32_t h = target ? obs_source_get_base_height(target) : 0;
	if (!target || !parent || !w || !h) {
		obs_source_skip_video_filter(f->source);
		return;
	}
	if (!f->named) {
		const char *pname = obs_source_get_name(parent);
		if (pname && *pname) {
			f->client->set_source_name(pname);
			f->named = true;
		}
	}
	if (f->enabled)
		capture(f, w, h);

	obs_source_skip_video_filter(f->source); // Quelle unverändert zeichnen

	if (!f->enabled)
		return;
	f->drawer.upload(f->client->take_panel_updates());
	if (f->client->latest_result(f->result, f->last_seq) && f->ana_w)
		f->drawer.set_result(f->result, float(w) / f->ana_w, float(h) / f->ana_h);
	if (!f->client->connected())
		f->drawer.set_result(ResultFrame{}, 1.f, 1.f);
	f->drawer.draw(w, h, f->style);
}

} // namespace

struct obs_source_info card_lens_filter_info = [] {
	obs_source_info info{};
	info.id = "tcg_card_lens_filter";
	info.type = OBS_SOURCE_TYPE_FILTER;
	info.output_flags = OBS_SOURCE_VIDEO;
	info.get_name = filter_name;
	info.create = filter_create;
	info.destroy = filter_destroy;
	info.update = filter_update;
	info.get_defaults = filter_defaults;
	info.get_properties = filter_properties;
	info.video_render = filter_render;
	return info;
}();
