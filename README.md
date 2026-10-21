# ArcGIS Pro OSM Editor

A lightweight, no-nonsense Python Toolbox for getting OpenStreetMap (OSM) vector data in and out of ArcGIS Pro without the headache overhead.

## The Deal

If you've been around GIS for a while, you know the old ArcObjects OSM plugins were bloated, required massive back-end setups, and broke on every other update. This tool skips all that. We leverage vanilla Python and OSM’s REST API (Overpass/OAuth 2.0) to directly sync your local File Geodatabase with the live map.

You get four tools:

1. **Download OSM Data To Current View**: Grabs whatever's in your active map extent. It rips the JSON apart, dumps it cleanly into native Points, Lines, and Polygons, and builds a hidden topology node backbone so things actually snap together properly.
2. **Download OSM Cemeteries by County**: (This is a niche tool for a cememtery location project)Dedicated county-scale cemetery extractor (`landuse=cemetery`, `amenity=grave_yard`, `cemetery=*`, `historic=cemetery`). Operates as a pure, lightweight reference layer with clean dataset overwriting, live OSM user/timestamp attribution, multi-endpoint Overpass failover with bounding box fallback, and identifier filters (Wikidata / Find A Grave).
3. **Tag OSM Buildings from Address & Name Points**: Performs an in-memory point-in-polygon spatial join between your GIS address / structure name points and the OSM building footprint polygons. Maps addresses into standard OSM `addr:*` tags and structure names into `name`, ready for upload.
4. **Upload Edits to OSM**: Scans your local edits, converts them back to `osmChange` XML packets, and pushes them to your OSM account.

## Setup & Auth (Don't Skip This)

OSM requires strict OAuth 2.0 Personal Access Tokens now.

1. Head over to your [OSM Account Settings](https://www.openstreetmap.org/user/-/oauth2_applications) -> **OAuth 2 Applications**.
2. Register a new app (Uncheck "Confidential Application") and check the box for `Modify the map`.
3. Run the `get_osm_token.py` script we included to get your permanent Bearer Token.
4. Hardcode that token into the `OSM_BEARER_TOKEN` variable at the very top of `OSM_Editor.pyt`. _(Note: Don't push this token to a public GitHub repo unless you want script-kiddies nuking the map under your name)._

## Editing & Reference Workflows

We set this up so you can edit the data just like standard native ArcGIS layers. No more dealing with raw JSON blocks or clunky interfaces.

### 1. Schema Dropdowns

When you download data, the script automatically parses out the core OSM tags (`highway`, `building`, `natural`, `waterway`, `amenity`, `landuse`, `leisure`, `addr_housenumber`, `addr_street`, `addr_unit`, `addr_city`, `addr_postcode`, `addr_full`) into standard Top-Level text columns.

- It wires up native **ArcGIS Coded Value Domains** for you. Click the `highway` or `building` field and pick the value from the dropdown.
- You change these attributes natively in Pro, and the Upload tool handles packing it back into OSM format on the backend. Simple as that.

### 2. Spatial Address & Name Enrichment

Have an authoritative local SDE/FileGDB layer of site address points or building names?

- Run **Tag OSM Buildings from Address & Name Points**.
- Select your `OSM_Polygons` layer and your point layers directly from your active map.
- Map the fields (House Number, Street Name, Unit, City, ZIP, Common Name).
- The tool uses a high-performance spatial grid index to match points inside footprints and writes both the dedicated GIS columns and the background `osm_tags` JSON.
- It automatically tags matched footprints with `osm_action = "modify"` (for existing OSM buildings) or `"create"` (for newly digitized footprints).

### 3. Dedicated Cemetery Reference Layers

Need up-to-date cemetery polygons and markers for local ground-truthing, research, or audit work?

- Run **Download OSM Cemeteries by County**.
- **County Geocoding or Map Extent:** Choose a county name (e.g. `Cabell County, West Virginia`) or an active map/layer extent.
- **Fresh Dataset Overwrite:** Enabled by default. Wipes existing cemetery layers (`OSM_Cemeteries_Points` and `OSM_Cemeteries_Polygons`) and pulls a clean, authoritative snapshot directly from OSM—preventing stale ghost records or append duplicates.
- **Pure Reference Architecture:** Unlike the general download tool, cemeteries are treated strictly as a reference layer: no `OSM_Topology_Nodes` bloat, no `osm_action` domains, and no editor tracking overhead.
- **OSM Metadata Tracking:** Extracts live `osm_timestamp` (last edit date/time) and `osm_user` (OSM mapper username) alongside `wikidata`, `ref_find_a_grave`, `gnis_feature_id`, denomination, and addresses.
- **Identifier Filtering:** Filter on the fly for missing or existing IDs:
  - `All Cemeteries (No Filter)`
  - `Missing Wikidata ID ([!wikidata])`
  - `Missing Find a Grave ID ([!ref:find_a_grave])`
  - `Missing Either Wikidata OR Find a Grave ID`
  - `Missing Both Wikidata AND Find a Grave IDs`
  - `Has Find a Grave ID ([ref:find_a_grave])`
  - `Has Wikidata ID ([wikidata])`
  - `Has Both Wikidata AND Find a Grave IDs`
- **High-Availability Overpass Failover & Bounding Box Fallback:** Queries across multiple fast Overpass endpoints (`lz4`, `kumi`, `de`, `z`). If an administrative boundary polygon query times out on Overpass servers, the tool automatically falls back to Nominatim's geocoded county bounding box to guarantee the extraction succeeds.
- **Pro Display Cache Busting:** Automatically invalidates and refreshes the active map view so newly downloaded polygons draw immediately without manual TOC layer cycling.

### 4. Editor Tracking & Auto-Detect

We removed the need for you to manually flag every row you touch in editable OSM layers (`DownloadOSMData`). The downloader automatically turns on native **ArcGIS Editor Tracking**.

- **Modifying:** When you move a vertex or change a dropdown value, ArcGIS natively logs the `last_edited_date`. The Upload script checks that timestamp against the creation date. If you touched it, the script knows, and auto-tags it as an OSM `<modify>` under the hood.
- **Digitizing:** Creating a new trail or building? We bound the Default Value of the `osm_action` column to `"create"`. Sketch a new feature and it's flagged as brand-new out of the gate.
- **Deleting:** If a building needs to be nuked, hit the `osm_action` dropdown and set it to `delete`. Don't delete it from the GIS layer: OSM needs to see the 'delete' flag on upload.

## Limitations & Best Practices

ArcGIS and OSM handle lines completely differently. Pro uses independent geography coordinate arrays; OSM uses mathematically-shared numbered Node IDs.

We built a backend topology cache that tries to snap your new vertices into the existing OSM node network, but **if you heavily rip apart pre-existing roads, you're going to leave orphaned nodes floating around the server.**

**The Golden Rules:**
✅ **Good for:** Mass attribute tagging (fixing speed limits, trail names) via the Attribute table.
✅ **Good for:** Drawing hundreds of brand new buildings or POIs from scratch.
✅ **Good for:** Enriching building footprints with authoritative local address and name points.
✅ **Good for:** Pulling 100% fresh, authoritative county cemetery reference layers with live mapper attribution.
✅ **Good for:** Straight-up deleting bad features.
❌ **Avoid:** Bulldozing or doing heavy geometric realignment of massive, interconnected highways. If you're doing complex road networking, boot up JOSM so you don't crater the topological network.
❌ **Note:** The Cemetery tool is strictly a reference/audit layer; it does not build topology nodes and is not intended for pushing geometry edits back via `UploadOSMData`.
