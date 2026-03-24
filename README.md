# ArcGIS Pro OSM Editor

A lightweight, no-nonsense Python Toolbox for getting OpenStreetMap (OSM) vector data in and out of ArcGIS Pro without the headache overhead.

## The Deal
If you've been around GIS for a while, you know the old ArcObjects OSM plugins were bloated, required massive back-end setups, and broke on every other update. This tool skips all that. We leverage vanilla Python and OSM’s REST API (Overpass/OAuth 2.0) to directly sync your local File Geodatabase with the live map. 

You get two tools:
1. **Download OSM Data To Current View**: Grabs whatever's in your active map extent. It rips the JSON apart, dumps it cleanly into native Points, Lines, and Polygons, and builds a hidden topology node backbone so things actually snap together properly.
2. **Upload Edits to OSM**: Scans your local edits, converts them back to `osmChange` XML packets, and pushes them to your OSM account. 

## Setup & Auth (Don't Skip This)
OSM requires strict OAuth 2.0 Personal Access Tokens now. 

1. Head over to your [OSM Account Settings](https://www.openstreetmap.org/user/-/oauth2_applications) -> **OAuth 2 Applications**.
2. Register a new app (Uncheck "Confidential Application") and check the box for `Modify the map`. 
3. Run the `get_osm_token.py` script we included to get your permanent Bearer Token.
4. Hardcode that token into the `OSM_BEARER_TOKEN` variable at the very top of `OSM_Editor.pyt`. *(Note: Don't push this token to a public GitHub repo unless you want script-kiddies nuking the map under your name).*

## Editing Workflows
We set this up so you can edit the data just like standard native ArcGIS layers. No more dealing with raw JSON blocks or clunky interfaces.

### 1. Schema Dropdowns
When you download data, the script automatically parses out the core OSM tags (`highway`, `building`, `natural`, `waterway`, `amenity`, `landuse`, `leisure`) into standard Top-Level text columns. 
- It wires up native **ArcGIS Coded Value Domains** for you. Click the `highway` field and just pick `residential` or `path` from the dropdown. 
- You change these attributes natively in Pro, and the Upload tool handles packing it back into OSM format on the backend. Simple as that.

### 2. Editor Tracking & Auto-Detect
We removed the need for you to manually flag every row you touch. The downloader automatically turns on native **ArcGIS Editor Tracking**.
- **Modifying:** When you move a vertex or change a dropdown value, ArcGIS natively logs the `last_edited_date`. The Upload script checks that timestamp against the creation date. If you touched it, the script knows, and auto-tags it as an OSM `<modify>` under the hood.
- **Digitizing:** Creating a new trail? We bound the Default Value of the `osm_action` column to `"create"`. Sketch a new feature and it's flagged as brand-new out of the gate.
- **Deleting:** If a building needs to be nuked, hit the `osm_action` dropdown and set it to `delete`.

## Limitations & Best Practices
ArcGIS and OSM handle lines completely differently. Pro uses independent geography coordinate arrays; OSM uses mathematically-shared numbered Node IDs. 

We built a backend topology cache that tries to snap your new vertices into the existing OSM node network, but **if you heavily rip apart pre-existing roads, you're going to leave orphaned nodes floating around the server.** 

**The Golden Rules:**
✅ **Good for:** Mass attribute tagging (fixing speed limits, trail names) via the Attribute table.
✅ **Good for:** Drawing hundreds of brand new buildings or POIs from scratch.
✅ **Good for:** Straight-up deleting bad features.
❌ **Avoid:** Bulldozing or doing heavy geometric realignment of massive, interconnected highways. If you're doing complex road networking, boot up JOSM so you don't crater the topological network.
