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

## How To Edit Data
Every feature pulled down natively includes specialized backend attribute fields like `osm_id`, `osm_tags`, and most importantly: `osm_action`. 

- **Modifying Attributes:** Change the name or tags of a road, then physically type `modify` into its `osm_action` column in the Attribute Table.
- **Digitizing New Features:** Draw a brand new forest footprint or highway, and type `create` into the `osm_action` column (or just leave it blank and the script will autocorrect it for you!).
- **Deleting Flags:** Type `delete` to permanently flag a feature for deletion on the server.

*If you do not flag a pre-existing feature with an action phrase in the `osm_action` field, the Upload script will beautifully ignore the feature as a safety lock to prevent accidental map destruction!*

## Limitations & Best Practices: The Topology Gap
GIS Applications like ArcGIS process spatial lines fundamentally differently than OpenStreetMap. ArcGIS defines a polyline as an independent array of geographic coordinates. OpenStreetMap defines a road as a specific, mathematically-shared sequence of numerical `Node IDs`.

While this plugin includes a fast background "Topology Index" allowing newly-drawn trails to successfully "snap" to the Node IDs of existing trails, **aggressive vertex-editing of pre-downloaded roads can leave "orphaned nodes" behind.**
When you pull a downloaded vertex and drag it to a new location, the plugin mathematically generates a completely new Node ID for the new corner and abandons the old one. The trail reshapes perfectly, but this leaves harmless (yet messy) floating dots scattered across the OSM database.

**The Golden Rules for using this Toolbox:**
✅ **Perfect for:** Massive bulk-updating of Names/Attributes on existing features (Speed limits, Trail names, Lanes).
✅ **Perfect for:** Digitizing hundreds of brand new Buildings, Trails, Parks, or POIs from scratch.
✅ **Perfect for:** Deleting existing bad features entirely.
❌ **Avoid:** Heavy physical reshaping or bulldozing of highly interconnected pre-existing highways (For aggressive street network realignment, use JOSM or the native OSM web editor to keep the topological Node network immaculately clean).
