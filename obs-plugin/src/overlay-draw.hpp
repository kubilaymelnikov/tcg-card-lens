#pragma once
// Zeichnen im OBS-Grafik-Thread: Rahmen (dicke Linien), Panel-Texturen, geglättete Positionen.

#include <array>
#include <map>
#include <string>
#include <vector>

#include <graphics/graphics.h>

#include "engine-client.hpp"

struct OverlayStyle {
	float smoothing = 0.45f; // 0 = sofort, höher = ruhiger
	bool debug = false;
};

class OverlayDrawer {
public:
	~OverlayDrawer();
	// Grafik-Thread: neue Panel-Bilder in Texturen übernehmen
	void upload(std::vector<PanelImage> &&images);
	// Neues Ergebnis übernehmen (Analyse-Koordinaten → Quellkoordinaten mit sx/sy)
	void set_result(const ResultFrame &r, float sx, float sy);
	// Jeden Frame zeichnen (Quellgröße w×h)
	void draw(uint32_t w, uint32_t h, const OverlayStyle &style);
	void clear();

private:
	struct Shown {
		std::array<float, 8> target{};
		std::array<float, 8> display{};
		std::array<float, 3> color{};
		float thickness = 3.f;
		std::vector<PanelRef> panels;
		bool fresh = true;
	};
	struct Tex {
		gs_texture_t *tex = nullptr;
		uint32_t ver = 0;
		int w = 0, h = 0;
	};
	void draw_quad_outline(const std::array<float, 8> &q, float thickness, const std::array<float, 3> &rgb);
	void draw_texture(const Tex &t, float x, float y, float scale);

	std::map<int, Shown> tracks_;
	std::map<std::string, Tex> textures_;
	std::vector<std::array<float, 8>> detections_;
	std::vector<PanelRef> screen_panels_;
	float panel_scale_ = 1.f;
};
