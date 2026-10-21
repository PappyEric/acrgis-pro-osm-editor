# ArcGIS Pro OSM Editor

A lightweight, no-nonsense Python Toolbox for getting OpenStreetMap (OSM) vector data in and out of ArcGIS Pro without the headache overhead.

If you've been around GIS for a while, you know the old ArcObjects OSM plugins were bloated, required massive back-end setups, and broke on every other update. This tool skips all that. We leverage vanilla Python and OSM’s REST API (Overpass/OAuth 2.0) to directly sync your local File Geodatabase with the live map.

You get four tools:

1. **Download OSM Data To Current View**: Grabs whatever's in your active map extent. It rips the JSON apart, dumps it cleanly into native Points, Lines, and Polygons, and builds a hidden topology node backbone so things actually snap together properly.
2. **Download OSM Cemeteries by County**: *(Niche tool for a cemetery location project)* Dedicated county-scale cemetery extractor (`landuse=cemetery`, `amenity=grave_yard`, `cemetery=*`, `historic=cemetery`). Operates as a pure, lightweight reference layer with clean dataset overwriting, live OSM user/timestamp attribution, multi-endpoint Overpass failover with bounding box fallback, and identifier filters (Wikidata / Find A Grave).
3. **Tag OSM Buildings from Address & Name Points**: Performs an in-memory point-in-polygon spatial join between your GIS address / structure name points and the OSM building footprint polygons. Maps addresses into standard OSM `addr:*` tags and structure names into `name`, ready for upload.
4. **Upload Edits to OSM**: Scans your local edits, converts them back to `osmChange` XML packets, and pushes them to your OSM account.

## Installation & Adding to ArcGIS Pro

### 1. Download & Placement
1. Click the green **Code** button on GitHub and select **Download ZIP** (or clone via `git clone https://github.com/PappyEric/acrgis-pro-osm-editor.git`).
2. Extract the folder to a stable location on your drive (e.g., `C:\GIS\Tools\acrgis-pro-osm-editor` or inside your project workspace).

### 2. Connect the Toolbox in ArcGIS Pro
1. Open your project in **ArcGIS Pro**.
2. Open the **Catalog Pane** (**View** tab &rarr; **Catalog Pane**, or press `Ctrl + Alt + G`).
3. In the Catalog tree:
   - **Method A (Add to current project):** Right-click **Toolboxes** &rarr; select **Add Toolbox**. Browse to the extracted folder, select **`OSM_Editor.pyt`**, and click **OK**.
   - **Method B (Folder connection):** Right-click **Folders** &rarr; select **Add Folder Connection**, select the repository folder, and expand it to see `OSM_Editor.pyt`.
4. *(Recommended)* **Add to Favorites:** Right-click **`OSM_Editor.pyt`** in the Catalog pane and click **Add To Favorites**. Then right-click it under Favorites and check **Add To New Projects** so it is permanently available in every ArcGIS Pro project you create.

### 3. Where to Find and Run the Tools
Once added, you can access the tools from two places:
- **Catalog Pane:** Expand **Toolboxes** &rarr; **ArcGIS Pro OSM Editor** (`OSM_Editor.pyt`).
- **Geoprocessing Pane:** Go to the **Analysis** tab &rarr; click **Tools** (to open Geoprocessing) &rarr; switch to the **Toolboxes** tab &rarr; find **ArcGIS Pro OSM Editor**.

Inside you will find all four tools ready to run:
- **Download OSM Data To Current View**
- **Download OSM Cemeteries by County**
- **Tag OSM Buildings from Address & Name Points**
- **Upload Edits to OSM**

---

## Setup & Authentication (For Uploading Edits)

Downloading and spatial tagging work immediately with zero setup. If you plan to **upload edits back to OpenStreetMap**, OSM requires OAuth 2.0 authentication:

1. Log in to OpenStreetMap and go to [My Settings &rarr; OAuth 2 Applications &rarr; Register new application](https://www.openstreetmap.org/user/-/oauth2_applications).
2. Set **Name** to `ArcGIS Pro OSM Editor`.
3. Set **Redirect URIs** to `urn:ietf:wg:oauth:2.0:oob`.
4. **Uncheck** *"Confidential application"*, and check the box for permissions: **Modify the map** (`write_api`).
5. Click **Register**, then copy your generated **Client ID**.
6. Open a terminal or command prompt in the repository folder and run:
   ```bash
   python get_osm_token.py
   ```
   Paste your Client ID, authorize in the browser that opens, and paste the authorization code. The script automatically saves your permanent Bearer Token into a local `osm_token.txt` file (which is git-ignored).
7. The toolbox automatically detects `osm_token.txt`. You are ready to upload! *(Alternatively, you can paste the token directly into the tool parameter when running Upload Edits to OSM, or set the `OSM_BEARER_TOKEN` environment variable).*


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

- ✅ **Good for:** Mass attribute tagging (fixing speed limits, trail names) via the Attribute table.
- ✅ **Good for:** Drawing hundreds of brand new buildings or POIs from scratch.
- ✅ **Good for:** Enriching building footprints with authoritative local address and name points.
- ✅ **Good for:** Pulling 100% fresh, authoritative county cemetery reference layers with live mapper attribution.
- ✅ **Good for:** Straight-up deleting bad features.
- ❌ **Avoid:** Bulldozing or doing heavy geometric realignment of massive, interconnected highways. If you're doing complex road networking, boot up JOSM so you don't crater the topological network.
- ❌ **Note:** The Cemetery tool is strictly a reference/audit layer; it does not build topology nodes and is not intended for pushing geometry edits back via `UploadOSMData`.
