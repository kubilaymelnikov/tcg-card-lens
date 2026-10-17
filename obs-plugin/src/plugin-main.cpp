/*
TCG Card Lens – OBS-Filter zur Erkennung von Pokémon- und Magic-Karten mit Cardmarket-Preisen.
Copyright (C) 2026

This program is free software: you can redistribute it and/or modify it under the terms of the GNU Affero General
Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option)
any later version. See LICENSE.
*/

#include <obs-module.h>
#include <plugin-support.h>

OBS_DECLARE_MODULE()
OBS_MODULE_USE_DEFAULT_LOCALE(PLUGIN_NAME, "de-DE")

extern struct obs_source_info card_lens_filter_info;

MODULE_EXPORT const char *obs_module_description(void)
{
	return "Erkennt Pokémon- und Magic-Karten und blendet Cardmarket-Preise ein.";
}

bool obs_module_load(void)
{
	obs_register_source(&card_lens_filter_info);
	obs_log(LOG_INFO, "TCG Card Lens geladen (Version %s)", PLUGIN_VERSION);
	return true;
}

void obs_module_unload(void)
{
	obs_log(LOG_INFO, "TCG Card Lens entladen");
}
