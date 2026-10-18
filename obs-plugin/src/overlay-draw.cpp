#include "overlay-draw.hpp"

#include <obs-module.h>
#include <graphics/vec4.h>
#include <util/platform.h>

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
		s.label = t.label;
		s.highlight = t.highlight;
		s.hit = t.hit;
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

void OverlayDrawer::draw_quad_outline(const std::array<float, 8> &q, float thickness, const std::array<float, 3> &rgb,
				      float alpha)
{
	gs_effect_t *solid = obs_get_base_effect(OBS_EFFECT_SOLID);
	gs_eparam_t *color = gs_effect_get_param_by_name(solid, "color");
	struct vec4 c;
	const uint32_t a8 = uint32_t(std::clamp(alpha, 0.f, 1.f) * 255.f + 0.5f);
	const uint32_t packed = (a8 << 24) | (uint32_t(rgb[2] * 255) << 16) | (uint32_t(rgb[1] * 255) << 8) |
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
	// Umrisse aller Karten (Achsen-Box): Tafeln sollen nicht in fremde Karten ragen (Sammelmappe, Kartenraster)
	std::vector<std::pair<int, Rect>> cards;
	for (const auto &kv : tracks_) {
		Rect r{1e9f, 1e9f, -1e9f, -1e9f};
		for (int i = 0; i < 4; i++) {
			r.x0 = std::min(r.x0, kv.second.display[i * 2]);
			r.x1 = std::max(r.x1, kv.second.display[i * 2]);
			r.y0 = std::min(r.y0, kv.second.display[i * 2 + 1]);
			r.y1 = std::max(r.y1, kv.second.display[i * 2 + 1]);
		}
		cards.push_back({kv.first, r});
	}
	// dir > 0: Tafel unter der Karte (Preis), dir < 0: über der Karte (Name). `own` = eigene Karte.
	// Ragt die Tafel in eine fremde Karte, rutscht sie auf die eigene Karte (Preis nach oben, Name nach unten) –
	// wie am Bildrand; weiter als bis zur gegenüberliegenden Kante der eigenen Karte aber nicht.
	auto place = [&](float x, float y, float pw, float ph, int dir, int own_id, const Rect &own) {
		// Kandidaten statt Hin-und-her-Schieben: gewünschte Stelle sowie direkt über/unter jedem Hindernis.
		// Kosten: fremde Karte verdecken (sehr teuer) > andere Tafel überdecken (teuer) > Abstand zur Wunschstelle.
		// Die eigene Karte darf verdeckt werden (Preis rutscht auf die Karte wie am Bildrand).
		x = std::clamp(x, 0.f, std::max(0.f, float(w) - pw));
		const float lo = dir > 0 ? std::max(0.f, own.y0) : 0.f;                       // Preis: höchstens bis Kartenoberkante
		const float hi = std::max(0.f, dir < 0 ? std::min(own.y1 - ph, float(h) - ph) : float(h) - ph); // Name: bis Unterkante
		const float pref = y;
		auto overlap = [&](const Rect &r, float yy) {
			const float ox = std::min(x + pw, r.x1) - std::max(x, r.x0);
			const float oy = std::min(yy + ph, r.y1) - std::max(yy, r.y0);
			return (ox > 0.f && oy > 0.f) ? ox * oy : 0.f;
		};
		auto cost = [&](float yy) {
			float c = std::abs(yy - pref);
			for (const auto &cd : cards)
				if (cd.first != own_id)
					c += 1000.f * overlap(cd.second, yy);
			for (const auto &r : placed)
				c += 50.f * overlap(r, yy);
			return c;
		};
		std::vector<float> cand{pref};
		auto add_around = [&](const Rect &r) {
			if (x < r.x1 && x + pw > r.x0) {
				cand.push_back(r.y0 - ph - 4.f);
				cand.push_back(r.y1 + 4.f);
			}
		};
		for (const auto &cd : cards)
			if (cd.first != own_id)
				add_around(cd.second);
		for (const auto &r : placed)
			add_around(r);
		cand.push_back(lo);
		cand.push_back(hi);
		float best = std::clamp(pref, std::min(lo, hi), hi), best_cost = 1e30f;
		for (float c : cand) {
			c = std::clamp(c, std::min(lo, hi), hi);
			const float k = cost(c);
			if (k < best_cost) {
				best = c;
				best_cost = k;
			}
		}
		y = best;
		placed.push_back({x, y, x + pw, y + ph});
		return std::pair<float, float>(x, y);
	};

	// Hervorhebung: Dickenfaktor pulsiert mit ~2 Hz zwischen 1,5 und 2,5
	constexpr float kPi = 3.14159265f;
	const double t_sec = double(os_gettime_ns() % 1000000000000ull) * 1e-9;
	const float pulse = 2.f + 0.5f * float(std::sin(2.0 * kPi * 2.0 * t_sec));

	// "corner"-Panels: nach allen Rahmen zeichnen, ohne Überlappungsvermeidung
	struct CornerPanel {
		const Tex *tex;
		float x, y;
	};
	std::vector<CornerPanel> corner_panels;

	for (auto &kv : tracks_) {
		Shown &s = kv.second;
		for (int i = 0; i < 8; i++)
			s.display[i] += (s.target[i] - s.display[i]) * a; // Glättung bei voller Bildrate
		const float card_w = std::hypot(s.display[2] - s.display[0], s.display[3] - s.display[1]);
		const float base = std::max(2.f, s.thickness * card_w / 250.f);
		if (s.hit) {
			// Pack-Opening-Hit: goldener Rahmen, pulsiert schneller (~3 Hz) und breiter als die Hervorhebung,
			// mit weichem goldenem Schein (zwei halbtransparente Lagen)
			const float hp = 2.5f + 1.f * float(std::sin(2.0 * kPi * 3.0 * t_sec));
			const float th = base * hp;
			const std::array<float, 3> gold{1.f, 200.f / 255.f, 40.f / 255.f};
			draw_quad_outline(s.display, th + std::max(18.f, base * 6.f), gold, 0.18f);
			draw_quad_outline(s.display, th + std::max(8.f, base * 3.f), gold, 0.35f);
			draw_quad_outline(s.display, th, gold);
		} else if (s.highlight) {
			const float th = base * pulse;
			// heller, halbtransparenter Außenschein unter dem Rahmen
			draw_quad_outline(s.display, th + std::max(6.f, base * 2.f), {1.f, 1.f, 1.f}, 0.35f);
			draw_quad_outline(s.display, th, s.color);
		} else {
			draw_quad_outline(s.display, base, s.color);
		}
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
			if (p.anchor == "corner") {
				// Mittelpunkt auf die angezeigte (geglättete) erste Ecke, auf den Bildschirm geklemmt
				const float x = std::clamp(s.display[0] - pw / 2, 0.f, std::max(0.f, float(w) - pw));
				const float y = std::clamp(s.display[1] - ph / 2, 0.f, std::max(0.f, float(h) - ph));
				corner_panels.push_back({&it->second, x, y});
				continue;
			}
			const bool is_top = p.anchor == "top";
			const Rect own{cx - pw / 2, top, cx + pw / 2, bottom};
			auto pos = place(cx - pw / 2, is_top ? top - ph - 6 : bottom + 8, pw, ph, is_top ? -1 : 1, kv.first, own);
			draw_texture(it->second, pos.first, pos.second, panel_scale_);
		}
	}
	for (const auto &cp : corner_panels)
		draw_texture(*cp.tex, cp.x, cp.y, panel_scale_);
	// Bildschirm-Panels: je Position (pos) in Reihenfolge gestapelt – oben nach unten, unten nach oben
	constexpr float kMargin = 10.f;
	std::map<std::string, float> stack; // pos -> bereits belegte Höhe
	for (const auto &p : screen_panels_) {
		auto it = textures_.find(p.key);
		if (it == textures_.end() || !it->second.tex)
			continue;
		const std::string pos = p.pos.empty() ? "bottom_left" : p.pos;
		const float pw = it->second.w * panel_scale_, ph = it->second.h * panel_scale_;
		float &used = stack[pos];
		const bool top = pos.rfind("top", 0) == 0;
		float x = kMargin;
		if (pos == "top_center")
			x = (float(w) - pw) / 2.f;
		else if (pos == "top_right" || pos == "bottom_right")
			x = float(w) - pw - kMargin;
		const float y = top ? kMargin + used : float(h) - kMargin - used - ph;
		used += ph + 8.f;
		draw_texture(it->second, std::max(0.f, x), std::max(0.f, y), panel_scale_);
	}
	gs_blend_state_pop();
}
