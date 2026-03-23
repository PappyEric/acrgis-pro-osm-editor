# -*- coding: utf-8 -*-

import arcpy
import urllib.request
import urllib.parse
import urllib.error
import base64
import os
import json
from xml.sax.saxutils import escape

# --- Configuration ---
# OpenStreetMap now requires OAuth 2.0 Personal Access Tokens for API edits.
# You can generate one from your OSM Account Settings -> OAuth 2 applications -> Personal Access Tokens.
# Check the boxes for "modify the map" and paste the generated string here:
OSM_BEARER_TOKEN = "your_personal_access_token_here"

# OSM API Endpoints
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://z.overpass-api.de/api/interpreter"
]
OSM_API_URL = "https://api.openstreetmap.org/api/0.6/"


class Toolbox(object):
    def __init__(self):
        """Define the toolbox (the name of the toolbox is the name of the
        .pyt file)."""
        self.label = "ArcGIS Pro OSM Editor"
        self.alias = "osm_editor"

        # List of tool classes associated with this toolbox
        self.tools = [DownloadOSMData, UploadOSMData]


class DownloadOSMData(object):
    def __init__(self):
        """Define the tool (tool name is the name of the class)."""
        self.label = "Download OSM Data To Current View"
        self.description = "Downloads OpenStreetMap data matching the current view extent into a local Geodatabase."
        self.canRunInBackground = False

    def getParameterInfo(self):
        """Define parameter definitions"""
        # Parameter 0: Extent
        param_extent = arcpy.Parameter(
            displayName="Download Extent",
            name="download_extent",
            datatype="GPExtent",
            parameterType="Required",
            direction="Input"
        )
        # Default to current extent
        param_extent.value = "DEFAULT"

        # Parameter 1: Data Types
        param_data_types = arcpy.Parameter(
            displayName="Features to Download",
            name="data_types",
            datatype="GPString",
            parameterType="Required",
            direction="Input",
            multiValue=True
        )
        param_data_types.filter.type = "ValueList"
        param_data_types.filter.list = [
            "Roads",
            "Trails/Paths",
            "Buildings/Footprints",
            "Businesses/Amenities",
            "Waterways/Water Bodies",
            "Parks/Landuse",
            "Public Transport (Railway, Bus)",
            "Boundaries (Administrative)",
            "Shops & Retail",
            "Tourism & Historic",
            "Power Infrastructure",
            "Natural (Trees, Forests, Scrub)",
            "Barriers & Fences",
            "Emergency Services"
        ]
        param_data_types.value = ["Roads", "Buildings/Footprints"]

        # Parameter 2: Output Workspace/Geodatabase
        param_out_gdb = arcpy.Parameter(
            displayName="Target Geodatabase",
            name="out_gdb",
            datatype="DEWorkspace",
            parameterType="Required",
            direction="Input"
        )

        params = [param_extent, param_data_types, param_out_gdb]
        return params

    def isLicensed(self):
        """Set whether tool is licensed to execute."""
        return True

    def updateParameters(self, parameters):
        """Modify the values and properties of parameters before internal
        validation is performed.  This method is called whenever a parameter
        has been changed."""
        return

    def updateMessages(self, parameters):
        """Modify the messages created by internal validation for each tool
        parameter.  This method is called after internal validation."""
        return

    def execute(self, parameters, messages):
        """The source code of the tool."""
        download_extent = parameters[0].value
        # Multi-value parameters arrive as semi-colon separated strings
        raw_data_types = parameters[1].valueAsText
        data_types = [dt.strip("'") for dt in raw_data_types.split(';')] if raw_data_types else []
        out_gdb = parameters[2].valueAsText

        arcpy.AddMessage(f"Extent requested: {download_extent.XMin}, {download_extent.YMin}, {download_extent.XMax}, {download_extent.YMax}")
        arcpy.AddMessage(f"Features selected: {', '.join(data_types)}")
        arcpy.AddMessage(f"Target workspace: {out_gdb}")
        arcpy.AddMessage("Preparing to download from OSM using Overpass API...")
        
        # 1. Project Extent to WGS84 for Overpass (EPSG 4326)
        if not download_extent.spatialReference or download_extent.spatialReference.name == "Unknown":
            arcpy.AddWarning("Extent has unknown spatial reference. Assuming WGS84 for Overpass query.")
            extent_wgs84 = download_extent
        else:
            try:
                sr_wgs84 = arcpy.SpatialReference(4326)
                # Safest projection: Create polygon from Extent, project, get new Extent
                polygon = arcpy.Polygon(arcpy.Array([
                    arcpy.Point(download_extent.XMin, download_extent.YMin),
                    arcpy.Point(download_extent.XMin, download_extent.YMax),
                    arcpy.Point(download_extent.XMax, download_extent.YMax),
                    arcpy.Point(download_extent.XMax, download_extent.YMin)
                ]), download_extent.spatialReference)
                
                polygon_wgs84 = polygon.projectAs(sr_wgs84)
                extent_wgs84 = polygon_wgs84.extent
            except Exception as e:
                arcpy.AddError(f"Failed to project the extent: {str(e)}")
                return
            
        # Overpass expects bbox as: south,west,north,east (YMin, XMin, YMax, XMax)
        bbox = f"{extent_wgs84.YMin},{extent_wgs84.XMin},{extent_wgs84.YMax},{extent_wgs84.XMax}"
        
        # 2. Build Overpass Query based on selected data types
        query_elements = []
        if "Roads" in data_types:
            query_elements.append(f'way["highway"][!"area"]({bbox});')
        if "Trails/Paths" in data_types:
            query_elements.append(f'way["highway"~"path|footway|cycleway|pedestrian"]({bbox});')
        if "Buildings/Footprints" in data_types:
            query_elements.append(f'way["building"]({bbox});\n  relation["building"]({bbox});')
        if "Businesses/Amenities" in data_types:
            query_elements.append(f'node["amenity"]({bbox});\n  way["amenity"]({bbox});')
        if "Waterways/Water Bodies" in data_types:
            query_elements.append(f'way["waterway"]({bbox});\n  way["natural"="water"]({bbox});\n  relation["natural"="water"]({bbox});')
        if "Parks/Landuse" in data_types:
            query_elements.append(f'way["leisure"~"park|nature_reserve"]({bbox});\n  relation["leisure"~"park|nature_reserve"]({bbox});\n  way["landuse"]({bbox});\n  relation["landuse"]({bbox});')
        if "Public Transport (Railway, Bus)" in data_types:
            query_elements.append(f'way["railway"]({bbox});\n  node["public_transport"]({bbox});\n  way["public_transport"]({bbox});')
        if "Boundaries (Administrative)" in data_types:
            query_elements.append(f'relation["boundary"="administrative"]({bbox});\n  way["boundary"="administrative"]({bbox});')
        if "Shops & Retail" in data_types:
            query_elements.append(f'node["shop"]({bbox});\n  way["shop"]({bbox});')
        if "Tourism & Historic" in data_types:
            query_elements.append(f'node["tourism"]({bbox});\n  way["tourism"]({bbox});\n  node["historic"]({bbox});\n  way["historic"]({bbox});')
        if "Power Infrastructure" in data_types:
            query_elements.append(f'node["power"]({bbox});\n  way["power"]({bbox});')
        if "Natural (Trees, Forests, Scrub)" in data_types:
            query_elements.append(f'node["natural"~"tree"]({bbox});\n  way["natural"~"wood|scrub|heath"]({bbox});\n  relation["natural"~"wood|scrub|heath"]({bbox});')
        if "Barriers & Fences" in data_types:
            query_elements.append(f'way["barrier"]({bbox});')
        if "Emergency Services" in data_types:
            query_elements.append(f'node["emergency"]({bbox});\n  way["emergency"]({bbox});')
            
        nodes_ways_rels = "\n  ".join(query_elements)
        
        overpass_query = f"""[out:json][timeout:180];
(
  {nodes_ways_rels}
);
out meta;
>;
out meta qt;"""

        arcpy.AddMessage("Constructed Overpass Query. Executing...")
        
        osm_data = None
        for endpoint in OVERPASS_ENDPOINTS:
            try:
                arcpy.AddMessage(f"Connecting to Overpass endpoint: {endpoint}")
                req = urllib.request.Request(endpoint, data=overpass_query.encode('utf-8'))
                req.add_header('User-Agent', 'ArcGIS-Pro-OSM-Editor/1.0')
                with urllib.request.urlopen(req, timeout=180) as response:
                    if response.status == 200:
                        osm_data = json.loads(response.read().decode('utf-8'))
                        break  # Stop trying other endpoints if successful
                    else:
                        arcpy.AddWarning(f"Endpoint {endpoint} returned status {response.status}")
            except Exception as e:
                arcpy.AddWarning(f"Endpoint failed: {e}")
                
        if not osm_data:
            arcpy.AddError("All Overpass API endpoints failed (Server too busy). Please try again in a few minutes or zoom in a bit closer.")
            return
            
        elements = osm_data.get("elements", [])
        if not elements:
            arcpy.AddWarning("No features found for this extent and selection criteria.")
            return

        arcpy.AddMessage(f"Downloaded {len(elements)} elements from OSM. Processing geometry...")
        
        nodes = {} # node_id -> (lon, lat)
        ways = [] # list of way dicts
        tagged_nodes = [] # nodes with tags
        
        for el in elements:
            if el["type"] == "node":
                nodes[el["id"]] = (el["lon"], el["lat"])
                if "tags" in el:
                    tagged_nodes.append(el)
            elif el["type"] == "way":
                ways.append(el)
                
        # Define output feature classes
        pt_fc = os.path.join(out_gdb, "OSM_Points")
        ln_fc = os.path.join(out_gdb, "OSM_Lines")
        py_fc = os.path.join(out_gdb, "OSM_Polygons")
        top_fc = os.path.join(out_gdb, "OSM_Topology_Nodes")
        
        def create_fc(fc, geom_type):
            if not arcpy.Exists(fc):
                arcpy.management.CreateFeatureclass(out_gdb, os.path.basename(fc), geom_type, spatial_reference=sr_wgs84)
                arcpy.management.AddField(fc, "osm_id", "TEXT", field_length=25)
                arcpy.management.AddField(fc, "name", "TEXT", field_length=255)
                arcpy.management.AddField(fc, "osm_type", "TEXT", field_length=50)
                arcpy.management.AddField(fc, "osm_tags", "TEXT", field_length=2000)
                arcpy.management.AddField(fc, "osm_version", "LONG")
                arcpy.management.AddField(fc, "osm_action", "TEXT", field_length=20)

        create_fc(pt_fc, "POINT")
        create_fc(ln_fc, "POLYLINE")
        create_fc(py_fc, "POLYGON")
        
        if not arcpy.Exists(top_fc):
            arcpy.management.CreateFeatureclass(out_gdb, "OSM_Topology_Nodes", "POINT", spatial_reference=sr_wgs84)
            arcpy.management.AddField(top_fc, "osm_id", "TEXT", field_length=25)
        
        # Anti-Duplication: Get existing osm_ids already in the geodatabase
        def get_existing_ids(fc):
            ids = set()
            if arcpy.Exists(fc) and int(arcpy.management.GetCount(fc).getOutput(0)) > 0:
                with arcpy.da.SearchCursor(fc, ["osm_id"]) as cur:
                    for row in cur:
                        if row[0]:
                            ids.add(row[0])
            return ids
            
        existing_pts = get_existing_ids(pt_fc)
        existing_lns = get_existing_ids(ln_fc)
        existing_pys = get_existing_ids(py_fc)
        existing_top = get_existing_ids(top_fc)
        
        fields = ["SHAPE@", "osm_id", "name", "osm_type", "osm_tags", "osm_version", "osm_action"]
        
        # 0. Build Topology Index
        arcpy.AddMessage(f"Building background Topology Index ({len(nodes)} total structural node(s))...")
        top_rows = []
        for nid, (lon, lat) in nodes.items():
            str_nid = str(nid)
            if str_nid not in existing_top:
                pt = arcpy.PointGeometry(arcpy.Point(lon, lat), sr_wgs84)
                top_rows.append([pt, str_nid])
                
        if top_rows:
            with arcpy.da.InsertCursor(top_fc, ["SHAPE@", "osm_id"]) as cur:
                for row in top_rows:
                    cur.insertRow(row)
        
        # 1. Insert Points
        if tagged_nodes:
            arcpy.AddMessage(f"Inserting {len(tagged_nodes)} Points...")
            with arcpy.da.InsertCursor(pt_fc, fields) as cursor:
                for n in tagged_nodes:
                    str_id = str(n["id"])
                    if str_id in existing_pts:
                        continue
                        
                    pt = arcpy.PointGeometry(arcpy.Point(n["lon"], n["lat"]), sr_wgs84)
                    tags = json.dumps(n.get("tags", {}))
                    name = n.get("tags", {}).get("name", "")
                    osm_type = "amenity" if "amenity" in n.get("tags", {}) else str(list(n.get("tags", {}).keys())[0] if n.get("tags") else "node")
                    version = n.get("version", 1)
                    
                    cursor.insertRow([pt, str_id, name[:254], osm_type[:49], tags[:1999], version, "none"])
                    
        # 2. Insert Lines & Polygons
        if ways:
            arcpy.AddMessage(f"Processing {len(ways)} Lines and Polygons...")
            line_rows = []
            poly_rows = []
            
            for w in ways:
                node_refs = w.get("nodes", [])
                if len(node_refs) < 2:
                    continue
                    
                # Build geometry array
                arr = arcpy.Array([arcpy.Point(nodes[nid][0], nodes[nid][1]) for nid in node_refs if nid in nodes])
                if len(arr) < 2:
                    continue
                    
                str_id = str(w["id"])
                tags_dict = w.get("tags", {})
                tags_str = json.dumps(tags_dict)
                name = tags_dict.get("name", "")
                osm_type = "unknown"
                if "highway" in tags_dict: osm_type = "highway"
                elif "building" in tags_dict: osm_type = "building"
                elif "amenity" in tags_dict: osm_type = "amenity"
                elif "shop" in tags_dict: osm_type = "shop"
                elif "waterway" in tags_dict or tags_dict.get("natural") == "water": osm_type = "water"
                elif "landuse" in tags_dict: osm_type = "landuse"
                elif "railway" in tags_dict: osm_type = "railway"
                elif "boundary" in tags_dict: osm_type = "boundary"
                elif "power" in tags_dict: osm_type = "power"
                elif "tourism" in tags_dict or "historic" in tags_dict: osm_type = "tourism"
                elif "natural" in tags_dict: osm_type = "natural"
                elif "barrier" in tags_dict: osm_type = "barrier"
                elif "emergency" in tags_dict: osm_type = "emergency"
                elif tags_dict: osm_type = str(list(tags_dict.keys())[0])

                version = w.get("version", 1)
                
                is_closed = (node_refs[0] == node_refs[-1])
                line_keys = ["highway", "barrier", "waterway", "railway", "power", "boundary"]
                force_line = any(k in tags_dict for k in line_keys)
                
                poly_keys = ["building", "leisure", "landuse", "amenity", "natural", "shop", "tourism", "historic"]
                force_poly = any(k in tags_dict for k in poly_keys)
                
                if is_closed and (force_poly or tags_dict.get("area") == "yes") and not force_line:
                    if str_id in existing_pys:
                        continue
                    geom = arcpy.Polygon(arr, sr_wgs84)
                    if geom and geom.area > 0:
                        poly_rows.append([geom, str_id, name[:254], osm_type[:49], tags_str[:1999], version, "none"])
                else:
                    if str_id in existing_lns:
                        continue
                    geom = arcpy.Polyline(arr, sr_wgs84)
                    if geom and geom.length > 0:
                        line_rows.append([geom, str_id, name[:254], osm_type[:49], tags_str[:1999], version, "none"])

            if line_rows:
                arcpy.AddMessage(f"Writing {len(line_rows)} Lines...")
                with arcpy.da.InsertCursor(ln_fc, fields) as cursor_ln:
                    for row in line_rows:
                        cursor_ln.insertRow(row)
                        
            if poly_rows:
                arcpy.AddMessage(f"Writing {len(poly_rows)} Polygons...")
                with arcpy.da.InsertCursor(py_fc, fields) as cursor_py:
                    for row in poly_rows:
                        cursor_py.insertRow(row)

        # 3. Add Layers to Current Map
        try:
            aprx = arcpy.mp.ArcGISProject("CURRENT")
            active_map = aprx.activeMap
            if active_map:
                existing_layers = [lyr.name for lyr in active_map.listLayers()]
                
                added_count = 0
                if "OSM_Points" not in existing_layers and arcpy.Exists(pt_fc):
                    active_map.addDataFromPath(pt_fc)
                    added_count += 1
                if "OSM_Lines" not in existing_layers and arcpy.Exists(ln_fc):
                    active_map.addDataFromPath(ln_fc)
                    added_count += 1
                if "OSM_Polygons" not in existing_layers and arcpy.Exists(py_fc):
                    active_map.addDataFromPath(py_fc)
                    added_count += 1
                    
                if added_count > 0:
                    arcpy.AddMessage("Successfully added the OSM layers to your current map view.")
        except Exception as e:
            arcpy.AddWarning(f"Could not automatically add layers to the map view. You can manually drag them from {out_gdb}. Error: {e}")

        arcpy.AddMessage("Download and geodatabase import complete!")
        return


class UploadOSMData(object):
    def __init__(self):
        """Define the tool (tool name is the name of the class)."""
        self.label = "Upload Edits to OSM"
        self.description = "Finds modifications in the local data and uploads a changeset back to OpenStreetMap."
        self.canRunInBackground = False

    def getParameterInfo(self):
        """Define parameter definitions"""
        param_in_layers = arcpy.Parameter(
            displayName="OSM Layers to Upload (Points, Lines, Polygons)",
            name="in_layers",
            datatype="GPFeatureLayer",
            parameterType="Required",
            direction="Input",
            multiValue=True
        )
        
        param_changeset_comment = arcpy.Parameter(
            displayName="Changeset Comment",
            name="changeset_comment",
            datatype="GPString",
            parameterType="Required",
            direction="Input"
        )
        param_changeset_comment.value = "ArcGIS Pro OSM Edits"

        params = [param_in_layers, param_changeset_comment]
        return params

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        return

    def updateMessages(self, parameters):
        return

    def execute(self, parameters, messages):
        """The source code of the tool."""
        # Convert the raw semi-colon string of layer names into an iterable list
        raw_layers = parameters[0].valueAsText
        in_layers = [lyr.strip("'") for lyr in raw_layers.split(';')] if raw_layers else []
        comment = parameters[1].valueAsText

        arcpy.AddMessage("Validating credentials...")
        if not OSM_BEARER_TOKEN or OSM_BEARER_TOKEN == "your_personal_access_token_here":
            arcpy.AddError("OSM Personal Access Token is missing from the script. Open OSM_Editor.pyt to paste your token.")
            return
            
        # 1. First, check all selected layers for any edited features
        edited_features_count = 0
        where_clause = "osm_action IN ('create', 'modify', 'delete')"
        
        arcpy.AddMessage("Scanning selected features for OSM edits...")
        
        for layer in in_layers:
            if arcpy.Exists(layer):
                # Using arcpy.da.SearchCursor automatically honors the user's manual active map selection!
                with arcpy.da.SearchCursor(layer, ["osm_id", "osm_action"], where_clause=where_clause) as cur:
                    for row in cur:
                        edited_features_count += 1
                        
        if edited_features_count == 0:
            arcpy.AddWarning("No edited features found in your selection.")
            arcpy.AddMessage("Remember to change the 'osm_action' field (to 'create', 'modify', or 'delete') in the Attribute Table on the precise features you want to upload!")
            return

        arcpy.AddMessage("Authenticating with standard OAuth 2.0 Bearer Token...")
        auth_header = f"Bearer {OSM_BEARER_TOKEN}"
        
        # 1. Open Changeset
        arcpy.AddMessage(f"Opening OSM Changeset with comment: '{comment}'")
        changeset_xml = f"""<osm>
          <changeset>
            <tag k="created_by" v="ArcGIS Pro OSM Editor"/>
            <tag k="comment" v="{escape(comment)}"/>
          </changeset>
        </osm>"""
        
        req = urllib.request.Request(f"{OSM_API_URL}changeset/create", data=changeset_xml.encode('utf-8'), method='PUT')
        req.add_header('Authorization', auth_header)
        req.add_header('Content-Type', 'text/xml')
        
        try:
            with urllib.request.urlopen(req) as response:
                changeset_id = response.read().decode('utf-8').strip()
            arcpy.AddMessage(f"-> Successfully opened Changeset #{changeset_id}")
        except urllib.error.URLError as e:
            arcpy.AddError(f"Failed to open Changeset (Check Password!): {e.read().decode('utf-8') if hasattr(e, 'read') else str(e)}")
            return
            
        # 2. Build Topology Cache (for snap-to-existing-trail logic)
        topology_cache = {}
        if in_layers:
            cat_path = arcpy.Describe(in_layers[0]).catalogPath
            workspace = os.path.dirname(cat_path) if cat_path else ""
            top_fc = os.path.join(workspace, "OSM_Topology_Nodes")
            sr_wgs84 = arcpy.SpatialReference(4326)
            
            if arcpy.Exists(top_fc):
                arcpy.AddMessage("Loading topology backbone to ensure proper path snapping...")
                with arcpy.da.SearchCursor(top_fc, ["SHAPE@XY", "osm_id"], spatial_reference=sr_wgs84) as cur:
                    for (x, y), osm_id in cur:
                        if x is not None and y is not None:
                            topology_cache[(round(x, 6), round(y, 6))] = osm_id
                            
        # 3. Process the Layers into OsmChange XML
        xml_create = []
        xml_modify = []
        xml_delete = []
        
        # New geometries generate negative sequence IDs immediately
        negative_id_counter = -1
        
        arcpy.AddMessage("Extracting geometries and translating to OSM XML...")
        
        for layer in in_layers:
            if not arcpy.Exists(layer): continue
            
            desc = arcpy.Describe(layer)
            geom_type = desc.shapeType
            
            with arcpy.da.SearchCursor(layer, ["SHAPE@", "osm_id", "osm_version", "osm_action", "osm_tags"], where_clause=where_clause, spatial_reference=sr_wgs84) as cur:
                for row in cur:
                    geom, o_id, o_version, o_action, o_tags = row
                    if not geom: continue
                    
                    # Safety check: If a user drew a brand NEW feature but accidentally typed 'modify' 
                    # in the Attribute Table instead of 'create', OSM will crash trying to modify 
                    # a placeholder. Force it cleanly to 'create' if the feature lacks an OSM ID.
                    if not o_id or str(o_id).strip() == "":
                        if o_action == "delete":
                            continue
                        o_action = "create"
                    
                    tags_dict = json.loads(o_tags) if o_tags else {}
                    tags_xml = "".join([f'<tag k="{escape(str(k))}" v="{escape(str(v))}"/>' for k, v in tags_dict.items()])
                    
                    if geom_type == "Point":
                        p = geom.firstPoint
                        node_id = str(o_id) if o_id and o_action != "create" else str(negative_id_counter)
                        node_version = f' version="{o_version}"' if o_action != "create" and o_version else ""
                        
                        node_xml = f'<node id="{node_id}" lat="{p.Y}" lon="{p.X}" changeset="{changeset_id}"{node_version}>{tags_xml}</node>'
                        
                        if o_action == "create":
                            xml_create.append(node_xml)
                            negative_id_counter -= 1
                        elif o_action == "modify": xml_modify.append(node_xml)
                        elif o_action == "delete": xml_delete.append(node_xml)
                            
                    elif geom_type in ["Polyline", "Polygon"]:
                        way_id = str(o_id) if o_id and o_action != "create" else str(negative_id_counter)
                        way_version = f' version="{o_version}"' if o_action != "create" and o_version else ""
                        if o_action == "create":
                            negative_id_counter -= 1
                            
                        nd_refs = []
                        # Extract all vertices to map as structural nodes
                        for part in geom:
                            for p in part:
                                if p:
                                    x_r, y_r = round(p.X, 6), round(p.Y, 6)
                                    snap_id = topology_cache.get((x_r, y_r))
                                    if snap_id:
                                        nd_refs.append(f'<nd ref="{snap_id}"/>')
                                    else:
                                        # It's a brand new corner/bend! It gets a temporary Node ID injected
                                        new_node_id = negative_id_counter
                                        negative_id_counter -= 1
                                        xml_create.append(f'<node id="{new_node_id}" lat="{p.Y}" lon="{p.X}" changeset="{changeset_id}"/>')
                                        nd_refs.append(f'<nd ref="{new_node_id}"/>')
                                        topology_cache[(x_r, y_r)] = str(new_node_id)
                                        
                        way_xml = f'<way id="{way_id}" changeset="{changeset_id}"{way_version}>' + "".join(nd_refs) + tags_xml + "</way>"
                        
                        if o_action == "create": xml_create.append(way_xml)
                        elif o_action == "modify": xml_modify.append(way_xml)
                        elif o_action == "delete": xml_delete.append(way_xml)

        # 4. Transmit OsmChange file automatically to OpenStreetMap
        arcpy.AddMessage("Transmitting XML packets to the OSM API...")
        osm_change_payload = f"""<osmChange version="0.6" generator="ArcGIS Pro OSM Editor">
  <create>{"".join(xml_create)}</create>
  <modify>{"".join(xml_modify)}</modify>
  <delete>{"".join(xml_delete)}</delete>
</osmChange>"""

        req = urllib.request.Request(f"{OSM_API_URL}changeset/{changeset_id}/upload", data=osm_change_payload.encode('utf-8'), method='POST')
        req.add_header('Authorization', auth_header)
        req.add_header('Content-Type', 'text/xml')
        
        try:
            with urllib.request.urlopen(req) as response:
                result = response.read().decode('utf-8')
                arcpy.AddMessage("-> Edits accepted successfully by OpenStreetMap!")
        except urllib.error.URLError as e:
            err_msg = e.read().decode('utf-8') if hasattr(e, 'read') else str(e)
            arcpy.AddError(f"OSM Rejected the upload due to server conflicts: {err_msg}")
            
        # 5. Cleanly shut the changeset door behind us
        arcpy.AddMessage("Closing mapping changeset...")
        req = urllib.request.Request(f"{OSM_API_URL}changeset/{changeset_id}/close", method='PUT')
        req.add_header('Authorization', auth_header)
        try:
            urllib.request.urlopen(req)
        except: pass
        
        arcpy.AddMessage(f"SUCCESS! Your edits are officially live on the map: https://www.openstreetmap.org/changeset/{changeset_id}")
        return
