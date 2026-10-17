#include "overlay-draw.hpp"

#include <obs-module.h>
#include <graphics/vec4.h>

#include <algorithm>
#include <cmath>
#include <set>

OverlayDrawer::~OverlayDrawer()
{
	clear();
}

void OverlayDrawer::clear()
{
	obs_enter_graphics();
	for (auto &kv : textures_)
		if (kv.second.tex)
			gs_texture_destroy(kv.second.tex);
	obs_leave_graphics();
	textures_.clear();
	tracks_.clear();
}

void OverlayDrawer::upload(std::vector<PanelImage> &&images)
{
	for (auto &img : images) {
		Tex &t = textures_[img.key];
		if (t.tex && (t.w != img.w || t.h != img.h)) {
			gs_texture_destroy(t.tex);
			t.tex = nullptr;
		}
		const uint8_t *data = img.rgba.data();
		if (!t.tex)
			t.tex = gs_texture_create(img.w, img.h, GS_RGBA, 1, &data, GS_DYNAMIC);
		else
			gs_texture_set_image(t.tex, data, img.w * 4, false);
		t.ver = img.ver;
		t.w = img.w;
		t.h = img.h;
	}
}

void OverlayDrawer::set_result(const ResultFrame &r, float sx, float sy)
{
	panel_scale_ = sx;
	std::set<int> alive;
	for (const auto &t : r.tracks) {
		alive.insert(t.id);
		Shown &s = tracks_[t.id];
		for (int i = 0; i < 4; i++) {
			s.target[i * 2] = t.corners[i * 2] * sx;
			s.target[i * 2 + 1] = t.corners[i * 2 + 1] * sy;
		}
		if (s.fresh) {
			s.display = s.target;
			s.fresh = false;
		}
		s.color = t.color;
		s.thickness = t.thickness;
		s.panels = t.panels;
	}
	for (auto it = tracks_.begin(); it != tracks_.end();)
		it = alive.count(it->first) ? std::next(it) : tracks_.erase(it);
	// Texturen verschwundener Karten freigeben
	std::set<std::string> used;
	for (auto &kv : tracks_)
		for (auto &p : kv.second.panels)
			used.insert(p.key);
	for (auto &p : r.screen_panels)
		used.insert(p.key);
	for (auto it = textures_.begin(); it != textures_.end();) {
		if (!used.count(it->first)) {
			if (it->second.tex)
				gs_texture_destroy(it->second.tex);
			it = textures_.erase(it);
		} else {
			++it;
		}
	}
	detections_.clear();
	for (auto d : r.detections) {
		for (int i = 0; i < 4; i++) {
			d[i * 2] *= sx;
			d[i * 2 + 1] *= sy;
		}
		detections_.push_back(d);
	}
	screen_panels_ = r.screen_panels;
}

void OverlayDrawer::draw_quad_outline(const std::array<float, 8> &q, float thickness, const std::array<float, 3> &rgb)
{
	gs_effect_t *solid = obs_get_base_effect(OBS_EFFECT_SOLID);
	gs_eparam_t *color = gs_effect_get_param_by_name(solid, "color");
	struct vec4 c;
	const uint32_t packed = 0xFF000000u | (uint32_t(rgb[2] * 255) << 16) | (uint32_t(rgb[1] * 255) << 8) |
				uint32_t(rgb[0] * 255);
	vec4_from_rgba_srgb(&c, packed);
	gs_effect_set_vec4(color, &c);
	const float half = thickness * 0.5f;
	while (gs_effect_loop(solid, "Solid")) {
		gs_render_start(true);
		for (int i = 0; i < 4; i++) {
			const float x0 = q[i * 2], y0 = q[i * 2 + 1];
			const float x1 = q[((i + 1) % 4) * 2], y1 = q[((i + 1) % 4) * 2 + 1];
			float dx = x1 - x0, dy = y1 - y0;
			const float len = std::sqrt(dx * dx + dy * dy);
			if (len < 1e-3f)
				continue;
			// Kante leicht verlängern, damit die Ecken geschlossen wirken
			const float ex = dx / len * half, ey = dy / len * half;
			const float nx = -dy / len * half, ny = dx / len * half;
			const float ax = x0 - ex, ay = y0 - ey, bx = x1 + ex, by = y1 + ey;
			gs_vertex2f(ax + nx, ay + ny);
			gs_vertex2f(ax - nx, ay - ny);
			gs_vertex2f(bx + nx, by + ny);
			gs_vertex2f(bx + nx, by + ny);
			gs_vertex2f(ax - nx, ay - ny);
			gs_vertex2f(bx - nx, by - ny);
		}
		gs_render_stop(GS_TRIS);
	}
}

void OverlayDrawer::draw_texture(const Tex &t, float x, float y, float scale)
{
	gs_effect_t *def = obs_get_base_effect(OBS_EFFECT_DEFAULT);
	gs_eparam_t *image = gs_effect_get_param_by_name(def, "image");
	// Panels sind sRGB-Bilder (wie Text-Quellen in OBS): sRGB-Framebuffer für korrektes Blending aktivieren
	const bool previous = gs_framebuffer_srgb_enabled();
	gs_enable_framebuffer_srgb(true);
	gs_effect_set_texture_srgb(image, t.tex);
	gs_matrix_push();
	gs_matrix_translate3f(std::floor(x), std::floor(y), 0.f);
	while (gs_effect_loop(def, "Draw"))
		gs_draw_sprite(t.tex, 0, uint32_t(t.w * scale), uint32_t(t.h * scale));
	gs_matrix_pop();
	gs_enable_framebuffer_srgb(previous);
}

void OverlayDrawer::draw(uint32_t w, uint32_t h, const OverlayStyle &style)
{
	if (tracks_.empty() && detections_.empty() && screen_panels_.empty())
		return;
	const float a = std::clamp(1.f - style.smoothing, 0.05f, 1.f);
	gs_blend_state_push();
	gs_enable_blending(true);
	gs_blend_function(GS_BLEND_SRCALPHA, GS_BLEND_INVSRCALPHA);

	if (style.debug)
		for (const auto &d : detections_)
			draw_quad_outline(d, 1.5f, {1.f, 1.f, 1.f});

	struct Rect {
		float x0, y0, x1, y1;
	};
	std::vector<Rect> placed;
	auto place = [&](float x, float y, float pw, float ph, int dir) {
		x = std::clamp(x, 0.f, std::max(0.f, float(w) - pw));
		y = std::clamp(y, 0.f, std::max(0.f, float(h) - ph));
		for (int i = 0; i < 20; i++) {
			auto hit = std::find_if(placed.begin(), placed.end(), [&](const Rect &r) {
				return x < r.x1 && x + pw > r.x0 && y < r.y1 && y + ph > r.y0;
			});
			if (hit == placed.end())
				break;
			y = dir > 0 ? hit->y1 + 4.f : hit->y0 - ph - 4.f;
			y = std::clamp(y, 0.f, std::max(0.f, float(h) - ph));
		}
		placed.push_back({x, y, x + pw, y + ph});
		return std::pair<float, float>(x, y);
	};

	for (auto &kv : tracks_) {
		Shown &s = kv.second;
		for (int i = 0; i < 8; i++)
			s.display[i] += (s.target[i] - s.display[i]) * a; // Glättung bei voller Bildrate
		const float card_w = std::hypot(s.display[2] - s.display[0], s.display[3] - s.display[1]);
		draw_quad_outline(s.display, std::max(2.f, s.thickness * card_w / 250.f), s.color);
		float cx = 0, top = 1e9f, bottom = -1e9f;
		for (int i = 0; i < 4; i++) {
			cx += s.display[i * 2] / 4.f;
			top = std::min(top, s.display[i * 2 + 1]);
			bottom = std::max(bottom, s.display[i * 2 + 1]);
		}
		for (const auto &p : s.panels) {
			auto it = textures_.find(p.key);
			if (it == textures_.end() || !it->second.tex)
				continue;
			const float pw = it->second.w * panel_scale_, ph = it->second.h * panel_scale_;
			const bool is_top = p.anchor == "top";
			auto pos = place(cx - pw / 2, is_top ? top - ph - 6 : bottom + 8, pw, ph, is_top ? -1 : 1);
			draw_texture(it->second, pos.first, pos.second, panel_scale_);
		}
	}
	for (const auto &p : screen_panels_) {
		auto it = textures_.find(p.key);
		if (it != textures_.end() && it->second.tex)
			draw_texture(it->second, 10.f, float(h) - it->second.h * panel_scale_ - 10.f, panel_scale_);
	}
	gs_blend_state_pop();
}
