# ArcGIS Pro OSM Editor

A lightweight Python Toolbox for downloading, editing, and uploading OpenStreetMap (OSM) vector data directly inside ArcGIS Pro.

## Overview
Unlike legacy ArcObjects plugins which require complex installations and massive backend databases, this tool leverages modern Python scripting and the official OpenStreetMap REST API (via Overpass and OAuth 2.0) to quickly and securely synchronize your local Geodatabases with global map data.

This toolbox consists of two primary tools:
1. **Download OSM Data To Current View**: Pulls active OSM mapping data for your screen extent, auto-categorizes them into Points, Lines, and Polygons natively in your local Geodatabase, and saves an invisible node topology backbone for accurate geometric routing.
2. **Upload Edits to OSM**: Seamlessly extracts modified attributes, new features, and geometry shape changes. It parses your local shapes back into standard XML `osmChange` packets and officially publishes them back directly to your OpenStreetMap contributor account.

## Setup & Authentication
OpenStreetMap heavily enforces secure OAuth 2.0 Personal Access Tokens for mapping scripts.

1. Go to your [OpenStreetMap Account Settings](https://www.openstreetmap.org/user/-/oauth2_applications) -> **OAuth 2 Applications**.
2. Register an application (Make sure to Uncheck "Confidential Application?") and ensure the `Modify the map` permission is checked. 
3. Run the included `get_osm_token.py` helper script in your terminal to easily generate your permanent Bearer Token.
4. Edit the `OSM_Editor.pyt` file and paste your token into the `OSM_BEARER_TOKEN` variable at the top of the file!

## How To Edit Data (Native ArcGIS Structure)
Every downloaded OSM grouping doesn't just pull data; it meticulously prepares your ArcGIS workspace for rapid, standards-compliant editing.

### 1. The Semantic Dropdowns
Instead of sifting through massive JSON blocks, the downloader physically extracts the 7 most globally mapped OSM tag keys (`highway`, `building`, `natural`, `waterway`, `amenity`, `landuse`, `leisure`) precisely into their own top-level Geodatabase text columns!
- These columns are actively fortified using massive **ArcGIS Coded Value Domains**.
- Click the `amenity` column on a node, and easily scroll over 50+ officially supported OpenStreetMap dictionary values (like `restaurant`, `bench`, `parking`) ensuring your datasets always perfectly adhere to global styling protocols without spelling mistakes.
- Modify *any* value in these columns natively, and the Upload Tool elegantly recompiles your selection straight back into the XML upload stream transparently.

### 2. Auto-Detecting Modifications
You no longer need to manually flag features! The Downloader tool heavily leans into native **ArcGIS Editor Tracking** logic.
- **Modifying:** The second you reposition a vertex or select a new Dropdown Domain value on a pre-existing trail, ArcGIS perfectly timestamps your `last_edited_date`. Upon compiling the upload changeset, the tool calculates this timestamp gap behind closed doors, instantly recognizing your input, and completely overrides the system to package it as an OSM `<modify>` payload. You don't have to flag anything manually!
- **Digitizing New Features:** Because we bound `"Create"` directly to the Feature Class *Default Values*, whenever you utilize the standard ArcGIS "Create Features" sketching ribbon to digitize a brand new line, the tool inherently locks its internal tagging natively to `<create>` right out of the box!
- **Deleting Flags:** To permanently obliterate a feature globally from the OpenStreetMap network, explicitly click its `osm_action` dropdown and select `Delete (Remove Feature)`.

## Limitations & Best Practices: The Topology Gap
GIS Applications like ArcGIS process spatial lines fundamentally differently than OpenStreetMap. ArcGIS defines a polyline as an independent array of geographic coordinates. OpenStreetMap defines a road as a specific, mathematically-shared sequence of numerical `Node IDs`.

While this plugin includes a fast background "Topology Index" allowing newly-drawn trails to successfully "snap" to the Node IDs of existing trails, **aggressive vertex-editing of pre-downloaded roads can leave "orphaned nodes" behind.**
When you pull a downloaded vertex and drag it to a new location, the plugin mathematically generates a completely new Node ID for the new corner and abandons the old one. The trail reshapes perfectly, but this leaves harmless (yet messy) floating dots scattered across the OSM database.

**The Golden Rules for using this Toolbox:**
✅ **Perfect for:** Massive bulk-updating of Names/Attributes on existing features (Speed limits, Trail names, Lanes).
✅ **Perfect for:** Digitizing hundreds of brand new Buildings, Trails, Parks, or POIs from scratch.
✅ **Perfect for:** Deleting existing bad features entirely.
❌ **Avoid:** Heavy physical reshaping or bulldozing of highly interconnected pre-existing highways (For aggressive street network realignment, use JOSM or the native OSM web editor to keep the topological Node network immaculately clean).
