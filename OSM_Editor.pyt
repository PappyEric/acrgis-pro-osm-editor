# -*- coding: utf-8 -*-

import arcpy
import urllib.request
import urllib.parse
import urllib.error
import base64
import os
import json
import re
import time
from xml.sax.saxutils import escape

def xml_escape_attr(val):
    """Escapes XML special characters including quotes for attribute values."""
    if val is None:
        return ""
    return escape(str(val), entities={'"': "&quot;", "'": "&apos;"})

def fetch_osm_element_version(elem_type, elem_id, token=None):
    """Queries the OSM API to retrieve the latest live version number of a node/way/relation."""
    if not elem_type or not elem_id or str(elem_id).startswith("-"):
        return None
    try:
        url = f"{OSM_API_URL}{elem_type}/{elem_id}"
        headers = {'User-Agent': 'ArcGIS-Pro-OSM-Editor/1.0'}
        if token:
            headers['Authorization'] = f"Bearer {token}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                xml_str = resp.read().decode('utf-8')
                import xml.etree.ElementTree as ET
                root = ET.fromstring(xml_str)
                elem = root.find(elem_type)
                if elem is not None and "version" in elem.attrib:
                    return int(elem.attrib["version"])
    except Exception:
        pass
    return None

def get_default_gdb():
    """Dynamically resolves the default geodatabase from the active ArcGIS Pro project."""
    try:
        aprx = arcpy.mp.ArcGISProject("CURRENT")
        if aprx and aprx.defaultGeodatabase and arcpy.Exists(aprx.defaultGeodatabase):
            return aprx.defaultGeodatabase
    except Exception:
        pass
    if arcpy.env.workspace and arcpy.Exists(arcpy.env.workspace):
        return arcpy.env.workspace
    if os.path.exists(r"E:\Working_OSM\MetroToOSM.gdb"):
        return r"E:\Working_OSM\MetroToOSM.gdb"
    return ""

def get_workspace_gdb(layer_or_path):
    """
    Robustly resolves the underlying Geodatabase or workspace root path,
    correctly handling feature datasets, enterprise SDE paths, and layer objects.
    """
    if not layer_or_path:
        return ""
    try:
        desc = arcpy.Describe(layer_or_path)
        cat_path = getattr(desc, 'catalogPath', '') or getattr(desc, 'path', '') or str(layer_or_path)
    except Exception:
        cat_path = str(layer_or_path)

    cat_lower = cat_path.lower()
    for ext in ['.gdb', '.sde']:
        pos = cat_lower.find(ext)
        if pos != -1:
            return cat_path[:pos + len(ext)]

    try:
        p = cat_path
        while p and p != os.path.dirname(p):
            if p.lower().endswith('.gdb') or p.lower().endswith('.sde'):
                return p
            p = os.path.dirname(p)
    except Exception:
        pass
    return os.path.dirname(cat_path) if cat_path else ""

def resolve_nominatim_area(query):
    """
    Queries Nominatim to resolve a county/administrative area to its OSM relation and Overpass Area ID.
    Returns (area_id, boundingbox) or (None, None).
    """
    if not query:
        return None, None
    try:
        clean_q = str(query).strip()
        params = urllib.parse.urlencode({
            'q': clean_q,
            'format': 'json',
            'limit': 1,
            'polygon_geojson': 0
        })
        url = f"https://nominatim.openstreetmap.org/search?{params}"
        req = urllib.request.Request(url, headers={'User-Agent': 'ArcGIS-Pro-OSM-Editor/1.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                results = json.loads(resp.read().decode('utf-8'))
                if results:
                    item = results[0]
                    osm_type = item.get('osm_type')
                    osm_id = item.get('osm_id')
                    bbox = item.get('boundingbox') # [min_lat, max_lat, min_lon, max_lon]
                    if osm_type == 'relation' and osm_id:
                        return 3600000000 + int(osm_id), bbox
                    elif osm_type == 'way' and osm_id:
                        return 2400000000 + int(osm_id), bbox
    except Exception:
        pass
    return None, None


# --- High-Performance Spatial Indexing ---
class SpatialHashGrid(object):
    """
    In-memory spatial hash grid index for sub-millisecond point-in-polygon candidate retrieval.
    Replaces O(N * M) nested loops with O(N + M) cell-bounded lookups.
    """
    def __init__(self, cell_size=100.0):
        self.cell_size = float(cell_size) if cell_size > 0 else 100.0
        self.grid = {}

    def _key(self, x, y):
        return (int(x // self.cell_size), int(y // self.cell_size))

    def insert(self, pt_geom, payload):
        p = pt_geom.firstPoint if hasattr(pt_geom, 'firstPoint') else pt_geom
        x, y = p.X, p.Y
        k = self._key(x, y)
        self.grid.setdefault(k, []).append((x, y, pt_geom, payload))

    def query_envelope(self, xmin, ymin, xmax, ymax):
        """Yields candidates matching cell keys whose coordinates fall within [xmin, ymin, xmax, ymax]."""
        min_cx = int(xmin // self.cell_size)
        max_cx = int(xmax // self.cell_size)
        min_cy = int(ymin // self.cell_size)
        max_cy = int(ymax // self.cell_size)

        candidates = []
        for cx in range(min_cx, max_cx + 1):
            for cy in range(min_cy, max_cy + 1):
                cell = self.grid.get((cx, cy))
                if cell:
                    for item in cell:
                        if xmin <= item[0] <= xmax and ymin <= item[1] <= ymax:
                            candidates.append((item[2], item[3]))
        return candidates


# --- OSM Address & Name Normalization Standards ---
STREET_SUFFIX_EXPANSIONS = {
    "ST": "Street", "STR": "Street", "ST.": "Street",
    "RD": "Road", "RD.": "Road",
    "AVE": "Avenue", "AV": "Avenue", "AVE.": "Avenue", "AV.": "Avenue",
    "BLVD": "Boulevard", "BLV": "Boulevard", "BLVD.": "Boulevard",
    "DR": "Drive", "DR.": "Drive",
    "LN": "Lane", "LN.": "Lane",
    "CT": "Court", "CT.": "Court",
    "PL": "Place", "PL.": "Place",
    "CIR": "Circle", "CR": "Circle", "CIR.": "Circle",
    "PKWY": "Parkway", "PKY": "Parkway", "PWY": "Parkway", "PKWY.": "Parkway",
    "HWY": "Highway", "HWY.": "Highway",
    "TRL": "Trail", "TR": "Trail", "TRL.": "Trail",
    "TER": "Terrace", "TERR": "Terrace", "TER.": "Terrace",
    "WAY": "Way", "PASS": "Pass", "PATH": "Path", "RUN": "Run", "LOOP": "Loop",
    "ALY": "Alley", "ALLEY": "Alley", "TRCE": "Trace", "TRACE": "Trace",
    "XING": "Crossing", "CROSSING": "Crossing", "ROW": "Row",
    "PLZ": "Plaza", "PLAZA": "Plaza", "SQ": "Square", "SQR": "Square", "SQ.": "Square",
    "EXT": "Extension", "EXT.": "Extension", "BYP": "Bypass", "BYPASS": "Bypass",
    "BND": "Bend", "BEND": "Bend", "CV": "Cove", "COVE": "Cove",
    "VW": "View", "VIEW": "View", "PT": "Point", "POINT": "Point",
    "RDG": "Ridge", "RIDGE": "Ridge", "VLG": "Village", "VILLAGE": "Village",
    "HL": "Hill", "HLS": "Hills", "IS": "Island", "MDW": "Meadow", "MDWS": "Meadows",
    "MNR": "Manor", "MANOR": "Manor", "EST": "Estates", "ESTS": "Estates", "ESTATES": "Estates",
    "CRES": "Crescent", "CRST": "Crescent", "CRESCENT": "Crescent",
    "CONN": "Connector", "CONNECTOR": "Connector", "SPUR": "Spur", "WALK": "Walk",
    "EXPY": "Expressway", "EXPWY": "Expressway", "EXPRESSWAY": "Expressway",
    "FWY": "Freeway", "FREEWAY": "Freeway", "TPKE": "Turnpike", "TRNPK": "Turnpike", "TURNPIKE": "Turnpike",
    "COR": "Corner", "CORS": "Corners", "BRG": "Bridge", "GLN": "Glen", "GRV": "Grove",
    "HBR": "Harbor", "HTS": "Heights", "LNDG": "Landing", "MNT": "Mountain", "MTN": "Mountain",
    "MT": "Mount", "OVAL": "Oval", "SHR": "Shores", "STN": "Station", "VLY": "Valley"
}

DIRECTIONAL_EXPANSIONS = {
    "N": "North", "N.": "North",
    "S": "South", "S.": "South",
    "E": "East", "E.": "East",
    "W": "West", "W.": "West",
    "NE": "Northeast", "N.E.": "Northeast", "N.E": "Northeast",
    "NW": "Northwest", "N.W.": "Northwest", "N.W": "Northwest",
    "SE": "Southeast", "S.E.": "Southeast", "S.E": "Southeast",
    "SW": "Southwest", "S.W.": "Southwest", "S.W": "Southwest"
}

UNIT_EXPANSIONS = {
    "APT": "Apartment", "APT.": "Apartment",
    "STE": "Suite", "STE.": "Suite",
    "UNIT": "Unit", "UNIT.": "Unit",
    "BLDG": "Building", "BLDG.": "Building",
    "FL": "Floor", "FLR": "Floor", "FL.": "Floor",
    "RM": "Room", "RM.": "Room",
    "DEPT": "Department", "LOT": "Lot", "BSMT": "Basement",
    "OFC": "Office", "TRLR": "Trailer", "HNGR": "Hangar"
}

NAME_EXPANSIONS = {
    "BLDG": "Building", "BLDG.": "Building",
    "CTR": "Center", "CTR.": "Center", "CNTR": "Center",
    "DEPT": "Department", "DEPT.": "Department",
    "ACAD": "Academy", "ACAD.": "Academy",
    "UNIV": "University", "UNIV.": "University",
    "SCH": "School", "SCH.": "School",
    "HOSP": "Hospital", "HOSP.": "Hospital",
    "LIB": "Library", "LIB.": "Library",
    "CH": "Church", "CH.": "Church",
    "TWR": "Tower", "TWRS": "Towers", "TWR.": "Tower",
    "LAB": "Laboratory", "LABS": "Laboratories", "LAB.": "Laboratory",
    "INST": "Institute", "INST.": "Institute",
    "COMM": "Community", "COMM.": "Community",
    "ASSOC": "Association", "ASSOC.": "Association",
    "CORP": "Corporation", "CORP.": "Corporation",
    "FAC": "Facility", "FAC.": "Facility",
    "PAV": "Pavilion", "PAV.": "Pavilion",
    "ANNX": "Annex", "ANNX.": "Annex", "ANX": "Annex"
}

ROMAN_NUMERALS = {"II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"}
LOWERCASE_WORDS = {"of", "and", "the", "at", "in", "for", "on", "by", "de", "la", "van", "von", "to"}

def format_word_title_case(word, is_first=False):
    """Formats a single word handling ordinals (1st, 2nd), Roman numerals, and lowercased particles."""
    clean = word.strip()
    if not clean:
        return ""

    upper = clean.upper().rstrip(".,;:")
    punctuation = clean[len(upper):] if len(clean) > len(upper) else ""

    # Roman Numerals
    if upper in ROMAN_NUMERALS:
        return upper + punctuation

    # Ordinal Numbers: 1ST -> 1st, 2ND -> 2nd, 23RD -> 23rd, 4TH -> 4th
    ord_match = re.match(r'^(\d+)(ST|ND|RD|TH)$', upper)
    if ord_match:
        return f"{ord_match.group(1)}{ord_match.group(2).lower()}{punctuation}"

    # Lowercase prepositions/articles if not first word
    if not is_first and upper.lower() in LOWERCASE_WORDS:
        return upper.lower() + punctuation

    # Letter-number combinations (e.g. 3B, 4A)
    if re.match(r'^\d+[A-Z]$', upper):
        return upper + punctuation

    # Standard Title Case (handling internal apostrophes or hyphens)
    if "-" in clean:
        return "-".join([format_word_title_case(sub, is_first and i == 0) for i, sub in enumerate(clean.split("-"))])
    if "'" in clean:
        parts = clean.split("'")
        return "'".join([p.capitalize() for p in parts])

    return clean.capitalize()

def normalize_osm_street(raw_street):
    """
    Expands street abbreviations (RD -> Road, ST -> Street, N -> North) 
    and applies OSM-standard Title Casing to all-caps GIS street names.
    """
    if not raw_street:
        return ""
    text = str(raw_street).strip().strip("'\"")
    if not text:
        return ""

    words = text.split()
    if not words:
        return ""

    normalized_words = []
    num_words = len(words)

    for i, w in enumerate(words):
        upper_w = w.upper().rstrip(".,;:")

        if (i == 0 or i == num_words - 1 or (i == 1 and num_words > 2)) and upper_w in DIRECTIONAL_EXPANSIONS:
            normalized_words.append(DIRECTIONAL_EXPANSIONS[upper_w])
        elif upper_w in STREET_SUFFIX_EXPANSIONS:
            normalized_words.append(STREET_SUFFIX_EXPANSIONS[upper_w])
        else:
            normalized_words.append(format_word_title_case(w, is_first=(i == 0)))

    return " ".join(normalized_words)

def normalize_osm_unit(raw_unit):
    """Expands unit abbreviations (STE -> Suite, APT -> Apartment) and title cases."""
    if not raw_unit:
        return ""
    text = str(raw_unit).strip().strip("'\"")
    if not text:
        return ""
    words = text.split()
    normalized_words = []
    for i, w in enumerate(words):
        upper_w = w.upper().rstrip(".,;:")
        if upper_w in UNIT_EXPANSIONS:
            normalized_words.append(UNIT_EXPANSIONS[upper_w])
        else:
            normalized_words.append(format_word_title_case(w, is_first=(i == 0)))
    return " ".join(normalized_words)

def normalize_osm_name(raw_name):
    """Converts building/POI names to Title Case with proper expansions and Roman numerals."""
    if not raw_name:
        return ""
    text = str(raw_name).strip().strip("'\"")
    if not text:
        return ""
    words = text.split()
    normalized_words = []
    for i, w in enumerate(words):
        upper_w = w.upper().rstrip(".,;:")
        if upper_w in NAME_EXPANSIONS:
            normalized_words.append(NAME_EXPANSIONS[upper_w])
        else:
            normalized_words.append(format_word_title_case(w, is_first=(i == 0)))
    return " ".join(normalized_words)

def normalize_osm_city(raw_city):
    """Converts ALL CAPS city names to Title Case."""
    if not raw_city:
        return ""
    text = str(raw_city).strip().strip("'\"")
    if not text:
        return ""
    words = text.split()
    return " ".join([format_word_title_case(w, is_first=(i == 0)) for i, w in enumerate(words)])


# --- Configuration ---
# Hardcoded fallback Bearer Token (can be overridden via tool parameter or OSM_BEARER_TOKEN env var)
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
        """Define the toolbox (the name of the toolbox is the name of the .pyt file)."""
        self.label = "ArcGIS Pro OSM Editor"
        self.alias = "osm_editor"
        self.tools = [DownloadOSMData, DownloadCemeteries, UploadOSMData, TagBuildingsFromPoints]


class DownloadOSMData(object):
    def __init__(self):
        self.label = "Download OSM Data To Current View"
        self.description = "Downloads OpenStreetMap data matching the current view extent into a local Geodatabase."
        self.canRunInBackground = False

    def getParameterInfo(self):
        param_extent = arcpy.Parameter(
            displayName="Download Extent",
            name="download_extent",
            datatype="GPExtent",
            parameterType="Required",
            direction="Input"
        )
        param_extent.value = "DEFAULT"

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
            "Buildings/Footprints",
            "Address Points (addr:*)",
            "Roads",
            "Trails/Paths",
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
        param_data_types.value = ["Buildings/Footprints", "Address Points (addr:*)"]

        param_out_gdb = arcpy.Parameter(
            displayName="Target Geodatabase",
            name="out_gdb",
            datatype="DEWorkspace",
            parameterType="Required",
            direction="Input"
        )
        def_gdb = get_default_gdb()
        param_out_gdb.value = def_gdb if def_gdb else r"E:\Working_OSM\MetroToOSM.gdb"

        params = [param_extent, param_data_types, param_out_gdb]
        return params

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        if not parameters[2].altered and not parameters[2].value:
            def_gdb = get_default_gdb()
            if def_gdb:
                parameters[2].value = def_gdb
        return

    def updateMessages(self, parameters):
        return

    def execute(self, parameters, messages):
        download_extent = parameters[0].value
        raw_data_types = parameters[1].valueAsText
        data_types = [dt.strip("'") for dt in raw_data_types.split(';')] if raw_data_types else []
        out_gdb = parameters[2].valueAsText

        arcpy.AddMessage(f"Extent requested: {download_extent.XMin}, {download_extent.YMin}, {download_extent.XMax}, {download_extent.YMax}")
        arcpy.AddMessage(f"Features selected: {', '.join(data_types)}")
        arcpy.AddMessage(f"Target workspace: {out_gdb}")
        arcpy.AddMessage("Preparing to download from OSM using Overpass API...")

        if not download_extent.spatialReference or download_extent.spatialReference.name == "Unknown":
            arcpy.AddWarning("Extent has unknown spatial reference. Assuming WGS84 for Overpass query.")
            extent_wgs84 = download_extent
            sr_wgs84 = arcpy.SpatialReference(4326)
        else:
            try:
                sr_wgs84 = arcpy.SpatialReference(4326)
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

        bbox = f"{extent_wgs84.YMin},{extent_wgs84.XMin},{extent_wgs84.YMax},{extent_wgs84.XMax}"

        query_elements = []
        if "Buildings/Footprints" in data_types:
            query_elements.append(f'way["building"]({bbox});\n  relation["building"]({bbox});')
        if any(a in str(data_types) for a in ["Address Points", "addr:"]):
            query_elements.append(f'node["addr:housenumber"]({bbox});\n  node["addr:street"]({bbox});\n  node["addr:full"]({bbox});')
        if "Roads" in data_types:
            query_elements.append(f'way["highway"][!"area"]({bbox});')
        if "Trails/Paths" in data_types:
            query_elements.append(f'way["highway"~"path|footway|cycleway|pedestrian"]({bbox});')
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
                        break
                    else:
                        arcpy.AddMessage(f"  -> Server busy (Status {response.status}), failing over to backup...")
            except Exception as e:
                arcpy.AddMessage(f"  -> Server busy ({str(e)}), failing over to backup...")

        if not osm_data:
            arcpy.AddError("All Overpass API endpoints failed (Server too busy). Please try again in a few minutes or zoom in a bit closer.")
            return

        elements = osm_data.get("elements", [])
        if not elements:
            arcpy.AddWarning("No features found for this extent and selection criteria.")
            return

        arcpy.AddMessage(f"Downloaded {len(elements)} elements from OSM. Processing geometry...")

        nodes = {}
        ways = []
        tagged_nodes = []

        for el in elements:
            if el["type"] == "node":
                nodes[el["id"]] = (el["lon"], el["lat"])
                if "tags" in el:
                    tagged_nodes.append(el)
            elif el["type"] == "way":
                ways.append(el)

        pt_fc = os.path.join(out_gdb, "OSM_Points")
        ln_fc = os.path.join(out_gdb, "OSM_Lines")
        py_fc = os.path.join(out_gdb, "OSM_Polygons")
        top_fc = os.path.join(out_gdb, "OSM_Topology_Nodes")

        domain_name = "OSM_Action_Domain"
        osm_semantics = {
            "OSM_Highway_Domain": ["motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link", "living_street", "service", "pedestrian", "track", "bus_guideway", "escape", "raceway", "road", "busway", "footway", "bridleway", "steps", "corridor", "path", "cycleway", "proposed", "construction"],
            "OSM_Building_Domain": ["yes", "commercial", "office", "industrial", "retail", "supermarket", "warehouse", "residential", "apartments", "house", "detached", "terrace", "dormitory", "hotel", "school", "university", "college", "hospital", "clinic", "civic", "government", "public", "parking", "garage", "garages", "roof", "church", "barn", "shed", "greenhouse", "construction"],
            "OSM_Natural_Domain": ["wood", "tree_row", "tree", "scrub", "heath", "moor", "grassland", "fell", "bare_rock", "scree", "shingle", "sand", "mud", "water", "wetland", "glacier", "bay", "strait", "cape", "beach", "coastline", "spring", "hot_spring", "geyser", "peak", "volcano", "valley", "ridge", "cliff", "saddle", "dune", "rock", "stone", "sinkhole", "cave_entrance"],
            "OSM_Waterway_Domain": ["river", "riverbank", "stream", "tidal_channel", "canal", "drain", "ditch", "wadi", "fairway", "dock", "boatyard", "dam", "weir", "waterfall", "lock_gate"],
            "OSM_Amenity_Domain": ["bar", "cafe", "fast_food", "food_court", "ice_cream", "pub", "restaurant", "college", "kindergarten", "library", "school", "university", "car_wash", "fuel", "parking", "parking_space", "taxi", "bank", "atm", "clinic", "dentist", "doctors", "hospital", "pharmacy", "veterinary", "arts_centre", "cinema", "community_centre", "nightclub", "theatre", "courthouse", "fire_station", "police", "post_box", "post_office", "prison", "townhall", "bench", "drinking_water", "fountain", "place_of_worship", "shelter", "toilets", "waste_basket"],
            "OSM_Landuse_Domain": ["commercial", "construction", "education", "industrial", "residential", "retail", "institutional", "aquaculture", "allotments", "farmland", "farmyard", "forest", "meadow", "orchard", "plant_nursery", "vineyard", "basin", "reservoir", "salt_pond", "brownfield", "cemetery", "conservation", "depot", "garages", "grass", "greenfield", "military", "port", "quarry", "railway", "recreation_ground", "religious", "village_green", "winter_sports"],
            "OSM_Leisure_Domain": ["adult_gaming_centre", "amusement_arcade", "beach_resort", "bandstand", "bird_hide", "common", "dance", "dog_park", "firepit", "fishing", "fitness_centre", "fitness_station", "garden", "golf_course", "horse_riding", "ice_rink", "marina", "miniature_golf", "nature_reserve", "park", "picnic_table", "pitch", "playground", "slipway", "sports_centre", "stadium", "summer_camp", "swimming_area", "swimming_pool", "track", "water_park"]
        }

        try:
            existing_domains = [d.name for d in arcpy.da.ListDomains(out_gdb)]
            if domain_name not in existing_domains:
                arcpy.management.CreateDomain(out_gdb, domain_name, "OpenStreetMap Upload Action", "TEXT", "CODED")
                arcpy.management.AddCodedValueToDomain(out_gdb, domain_name, "none", "None (Do not upload)")
                arcpy.management.AddCodedValueToDomain(out_gdb, domain_name, "create", "Create (New Feature)")
                arcpy.management.AddCodedValueToDomain(out_gdb, domain_name, "modify", "Modify (Edit Feature)")
                arcpy.management.AddCodedValueToDomain(out_gdb, domain_name, "delete", "Delete (Remove Feature)")

            for d_name, d_values in osm_semantics.items():
                if d_name not in existing_domains:
                    arcpy.management.CreateDomain(out_gdb, d_name, f"OSM {d_name.split('_')[1]} Tags", "TEXT", "CODED")
                    for val in d_values:
                        arcpy.management.AddCodedValueToDomain(out_gdb, d_name, val, val.replace("_", " ").title())
        except Exception as e:
            arcpy.AddWarning(f"Notice: Could not build all Dropdown Domains in {out_gdb}. (Error: {str(e)})")

        def create_fc(fc, geom_type):
            if not arcpy.Exists(fc):
                arcpy.management.CreateFeatureclass(out_gdb, os.path.basename(fc), geom_type, spatial_reference=sr_wgs84)

            existing_fields = [f.name for f in arcpy.ListFields(fc)]
            def add_field_safely(fname, ftype, flen=None):
                if fname not in existing_fields:
                    arcpy.management.AddField(fc, fname, ftype, field_length=flen)

            add_field_safely("osm_id", "TEXT", 25)
            add_field_safely("name", "TEXT", 255)
            add_field_safely("osm_type", "TEXT", 50)
            add_field_safely("osm_tags", "TEXT", 2000)
            add_field_safely("osm_version", "LONG")
            add_field_safely("osm_action", "TEXT", 20)

            try:
                arcpy.management.AssignDomainToField(fc, "osm_action", domain_name)
                arcpy.management.AssignDefaultToField(fc, "osm_action", "create")
            except Exception:
                pass

            for sem_field, dom_binding in [("highway", "OSM_Highway_Domain"), ("building", "OSM_Building_Domain"), 
                                           ("natural", "OSM_Natural_Domain"), ("waterway", "OSM_Waterway_Domain"), 
                                           ("amenity", "OSM_Amenity_Domain"), ("landuse", "OSM_Landuse_Domain"), 
                                           ("leisure", "OSM_Leisure_Domain"),
                                           ("addr_housenumber", None), ("addr_street", None),
                                           ("addr_unit", None), ("addr_city", None),
                                           ("addr_state", None), ("addr_postcode", None), ("addr_full", None)]:
                flen = 255 if sem_field in ["name", "addr_street", "addr_full"] else (50 if sem_field in ["addr_housenumber", "addr_unit", "addr_state", "addr_postcode"] else 150)
                add_field_safely(sem_field, "TEXT", flen)
                if dom_binding:
                    try:
                        arcpy.management.AssignDomainToField(fc, sem_field, dom_binding)
                    except Exception:
                        pass

            if geom_type == "POLYGON":
                try:
                    arcpy.management.AssignDefaultToField(fc, "building", "yes")
                except Exception:
                    pass

            try:
                arcpy.management.EnableEditorTracking(
                    fc, 
                    creator_field="created_user",
                    creation_date_field="created_date",
                    last_editor_field="last_edited_user",
                    last_edit_date_field="last_edited_date",
                    add_fields="ADD_FIELDS",
                    record_dates_in="UTC"
                )
            except Exception:
                pass

        create_fc(pt_fc, "POINT")
        create_fc(ln_fc, "POLYLINE")
        create_fc(py_fc, "POLYGON")

        if not arcpy.Exists(top_fc):
            arcpy.management.CreateFeatureclass(out_gdb, "OSM_Topology_Nodes", "POINT", spatial_reference=sr_wgs84)
            arcpy.management.AddField(top_fc, "osm_id", "TEXT", field_length=25)

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

        fields = ["SHAPE@", "osm_id", "name", "osm_type", "osm_tags", "osm_version", "osm_action",
                  "highway", "building", "natural", "waterway", "amenity", "landuse", "leisure",
                  "addr_housenumber", "addr_street", "addr_unit", "addr_city", "addr_state", "addr_postcode", "addr_full"]

        # Optimized Topology Nodes Stream Insertion
        arcpy.AddMessage(f"Building background Topology Index ({len(nodes)} total structural node(s))...")
        nodes_to_insert = [(nid, lon, lat) for nid, (lon, lat) in nodes.items() if str(nid) not in existing_top]
        if nodes_to_insert:
            arcpy.SetProgressor("step", "Writing background topology nodes...", 0, len(nodes_to_insert), max(1, len(nodes_to_insert) // 20))
            with arcpy.da.InsertCursor(top_fc, ["SHAPE@", "osm_id"]) as cur:
                for idx_node, (nid, lon, lat) in enumerate(nodes_to_insert):
                    cur.insertRow([arcpy.Point(lon, lat), str(nid)])
                    if (idx_node + 1) % 5000 == 0:
                        arcpy.SetProgressorPosition(idx_node + 1)
            arcpy.ResetProgressor()

        def is_address_node(tags_dict):
            address_keys = {"addr:housenumber", "addr:street", "addr:full", "addr:unit", "addr:flats", "addr:suite", "addr:housename", "addr:conscriptionnumber", "addr:postcode", "addr:city", "addr:state"}
            return any(k in address_keys or k.startswith("addr:") for k in tags_dict)

        def is_requested_poi_node(tags_dict, selected_data_types):
            if "Businesses/Amenities" in selected_data_types and "amenity" in tags_dict:
                return True
            if "Shops & Retail" in selected_data_types and "shop" in tags_dict:
                return True
            if "Tourism & Historic" in selected_data_types and ("tourism" in tags_dict or "historic" in tags_dict):
                return True
            if "Power Infrastructure" in selected_data_types and "power" in tags_dict:
                return True
            if "Natural (Trees, Forests, Scrub)" in selected_data_types and "natural" in tags_dict:
                return True
            if "Emergency Services" in selected_data_types and "emergency" in tags_dict:
                return True
            if "Public Transport (Railway, Bus)" in selected_data_types and ("public_transport" in tags_dict or "railway" in tags_dict):
                return True
            return False

        if tagged_nodes:
            filtered_nodes = [n for n in tagged_nodes if is_address_node(n.get("tags", {})) or is_requested_poi_node(n.get("tags", {}), data_types)]
            arcpy.AddMessage(f"Inserting {len(filtered_nodes)} Point node(s) into OSM_Points (filtered from {len(tagged_nodes)} total tagged nodes)...")
            with arcpy.da.InsertCursor(pt_fc, fields) as cursor:
                for n in filtered_nodes:
                    str_id = str(n["id"])
                    if str_id in existing_pts:
                        continue

                    pt = arcpy.PointGeometry(arcpy.Point(n["lon"], n["lat"]), sr_wgs84)
                    tags_dict_val = n.get("tags", {})
                    tags = json.dumps(tags_dict_val)
                    name = tags_dict_val.get("name", "")
                    osm_type = "amenity" if "amenity" in tags_dict_val else str(list(tags_dict_val.keys())[0] if tags_dict_val else "node")
                    version = n.get("version", 1)

                    hw = tags_dict_val.get("highway", "")
                    bd = tags_dict_val.get("building", "")
                    nt = tags_dict_val.get("natural", "")
                    ww = tags_dict_val.get("waterway", "")
                    am = tags_dict_val.get("amenity", "")
                    lu = tags_dict_val.get("landuse", "")
                    ls = tags_dict_val.get("leisure", "")

                    addr_hn = tags_dict_val.get("addr:housenumber", "")
                    addr_st = tags_dict_val.get("addr:street", "")
                    addr_un = tags_dict_val.get("addr:unit", tags_dict_val.get("addr:flats", tags_dict_val.get("addr:suite", "")))
                    addr_ct = tags_dict_val.get("addr:city", "")
                    addr_state_val = tags_dict_val.get("addr:state", "")
                    addr_pc = tags_dict_val.get("addr:postcode", "")
                    addr_fl = tags_dict_val.get("addr:full", "")

                    cursor.insertRow([pt, str_id, name[:254], osm_type[:49], tags[:1999], version, "none",
                                      hw[:149], bd[:149], nt[:149], ww[:149], am[:149], lu[:149], ls[:149],
                                      addr_hn[:49], addr_st[:149], addr_un[:49], addr_ct[:99], addr_state_val[:49], addr_pc[:19], addr_fl[:254]])

        if ways:
            arcpy.AddMessage(f"Processing {len(ways)} Lines and Polygons...")
            line_rows = []
            poly_rows = []

            for w in ways:
                node_refs = w.get("nodes", [])
                if len(node_refs) < 2:
                    continue

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

                hw = tags_dict.get("highway", "")
                bd = tags_dict.get("building", "")
                nt = tags_dict.get("natural", "")
                ww = tags_dict.get("waterway", "")
                am = tags_dict.get("amenity", "")
                lu = tags_dict.get("landuse", "")
                ls = tags_dict.get("leisure", "")

                addr_hn = tags_dict.get("addr:housenumber", "")
                addr_st = tags_dict.get("addr:street", "")
                addr_un = tags_dict.get("addr:unit", tags_dict.get("addr:flats", tags_dict.get("addr:suite", "")))
                addr_ct = tags_dict.get("addr:city", "")
                addr_state_val = tags_dict.get("addr:state", "")
                addr_pc = tags_dict.get("addr:postcode", "")
                addr_fl = tags_dict.get("addr:full", "")

                if is_closed and (force_poly or tags_dict.get("area") == "yes") and not force_line:
                    if str_id in existing_pys:
                        continue
                    geom = arcpy.Polygon(arr, sr_wgs84)
                    if geom and geom.area > 0:
                        poly_rows.append([geom, str_id, name[:254], osm_type[:49], tags_str[:1999], version, "none",
                                          hw[:149], bd[:149], nt[:149], ww[:149], am[:149], lu[:149], ls[:149],
                                          addr_hn[:49], addr_st[:149], addr_un[:49], addr_ct[:99], addr_state_val[:49], addr_pc[:19], addr_fl[:254]])
                else:
                    if str_id in existing_lns:
                        continue
                    geom = arcpy.Polyline(arr, sr_wgs84)
                    if geom and geom.length > 0:
                        line_rows.append([geom, str_id, name[:254], osm_type[:49], tags_str[:1999], version, "none",
                                          hw[:149], bd[:149], nt[:149], ww[:149], am[:149], lu[:149], ls[:149],
                                          addr_hn[:49], addr_st[:149], addr_un[:49], addr_ct[:99], addr_state_val[:49], addr_pc[:19], addr_fl[:254]])

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

        try:
            aprx = arcpy.mp.ArcGISProject("CURRENT")
            active_map = aprx.activeMap
            if active_map:
                existing_layers = [lyr.name for lyr in active_map.listLayers()]

                added_count = 0
                if "OSM_Points" not in existing_layers and arcpy.Exists(pt_fc):
                    pt_count = int(arcpy.management.GetCount(pt_fc).getOutput(0)) if arcpy.Exists(pt_fc) else 0
                    if any(a in str(data_types) for a in ["Address Points", "addr:", "Businesses"]) or pt_count > 0:
                        active_map.addDataFromPath(pt_fc)
                        added_count += 1
                if "OSM_Lines" not in existing_layers and arcpy.Exists(ln_fc):
                    ln_count = int(arcpy.management.GetCount(ln_fc).getOutput(0)) if arcpy.Exists(ln_fc) else 0
                    if any(l in data_types for l in ["Roads", "Trails/Paths", "Waterways/Water Bodies", "Boundaries (Administrative)", "Power Infrastructure", "Barriers & Fences"]) and ln_count > 0:
                        active_map.addDataFromPath(ln_fc)
                        added_count += 1
                if "OSM_Polygons" not in existing_layers and arcpy.Exists(py_fc):
                    active_map.addDataFromPath(py_fc)
                    added_count += 1

                if added_count > 0:
                    arcpy.AddMessage("Successfully added the OSM layers to your current map view.")
                    try:
                        arcpy.RefreshActiveView()
                    except Exception:
                        pass
        except Exception as e:
            arcpy.AddWarning(f"Could not automatically add layers to the map view. You can manually drag them from {out_gdb}. Error: {e}")

        arcpy.AddMessage("Download and geodatabase import complete!")
        return


class DownloadCemeteries(object):
    def __init__(self):
        self.label = "Download OSM Cemeteries by County"
        self.description = (
            "Downloads OpenStreetMap cemetery and graveyard features (landuse=cemetery, amenity=grave_yard) "
            "for a specific county or extent, with options to filter by missing Wikidata or Find A Grave identifiers."
        )
        self.canRunInBackground = False

    def getParameterInfo(self):
        param_method = arcpy.Parameter(
            displayName="County / Extent Selection Method",
            name="selection_method",
            datatype="GPString",
            parameterType="Required",
            direction="Input"
        )
        param_method.filter.type = "ValueList"
        param_method.filter.list = [
            "County Name (Geocoded Boundary)",
            "Map / Layer Extent (Bounding Box)",
            "Both (County Boundary clipped to Extent)"
        ]
        param_method.value = "County Name (Geocoded Boundary)"

        param_county = arcpy.Parameter(
            displayName="County & State Name",
            name="county_name",
            datatype="GPString",
            parameterType="Optional",
            direction="Input"
        )
        param_county.value = "Cabell County, West Virginia"

        param_extent = arcpy.Parameter(
            displayName="Search Extent (Map View / Layer Extent)",
            name="search_extent",
            datatype="GPExtent",
            parameterType="Optional",
            direction="Input"
        )
        param_extent.value = "DEFAULT"

        param_filter = arcpy.Parameter(
            displayName="Identifier Filter (Wikidata / Find A Grave)",
            name="id_filter",
            datatype="GPString",
            parameterType="Required",
            direction="Input"
        )
        param_filter.filter.type = "ValueList"
        param_filter.filter.list = [
            "All Cemeteries (No Filter)",
            "Missing Wikidata ID ([!wikidata])",
            "Missing Find a Grave ID ([!ref:find_a_grave])",
            "Missing Either Wikidata OR Find a Grave ID",
            "Missing Both Wikidata AND Find a Grave IDs"
        ]
        param_filter.value = "All Cemeteries (No Filter)"

        param_out_gdb = arcpy.Parameter(
            displayName="Target Geodatabase",
            name="out_gdb",
            datatype="DEWorkspace",
            parameterType="Required",
            direction="Input"
        )
        def_gdb = get_default_gdb()
        param_out_gdb.value = def_gdb if def_gdb else r"E:\Working_OSM\MetroToOSM.gdb"

        param_prefix = arcpy.Parameter(
            displayName="Output Feature Class Prefix",
            name="out_prefix",
            datatype="GPString",
            parameterType="Required",
            direction="Input"
        )
        param_prefix.value = "OSM_Cemeteries"

        return [param_method, param_county, param_extent, param_filter, param_out_gdb, param_prefix]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        if not parameters[4].altered and not parameters[4].value:
            def_gdb = get_default_gdb()
            if def_gdb:
                parameters[4].value = def_gdb
        return

    def updateMessages(self, parameters):
        return

    def execute(self, parameters, messages):
        method = parameters[0].valueAsText
        county_name = parameters[1].valueAsText.strip() if parameters[1].value else ""
        raw_extent = parameters[2].value
        id_filter = parameters[3].valueAsText
        out_gdb = parameters[4].valueAsText
        prefix = parameters[5].valueAsText.strip() if parameters[5].value else "OSM_Cemeteries"

        sr_wgs84 = arcpy.SpatialReference(4326)

        # 1. Resolve Bounding Box if extent method used
        bbox_str = None
        if method in ["Map / Layer Extent (Bounding Box)", "Both (County Boundary clipped to Extent)"] and raw_extent:
            if not raw_extent.spatialReference or raw_extent.spatialReference.name == "Unknown":
                extent_wgs84 = raw_extent
            else:
                try:
                    polygon = arcpy.Polygon(arcpy.Array([
                        arcpy.Point(raw_extent.XMin, raw_extent.YMin),
                        arcpy.Point(raw_extent.XMin, raw_extent.YMax),
                        arcpy.Point(raw_extent.XMax, raw_extent.YMax),
                        arcpy.Point(raw_extent.XMax, raw_extent.YMin)
                    ]), raw_extent.spatialReference)
                    extent_wgs84 = polygon.projectAs(sr_wgs84).extent
                except Exception as e:
                    arcpy.AddWarning(f"Notice: Could not project extent to WGS84: {e}")
                    extent_wgs84 = raw_extent
            bbox_str = f"{extent_wgs84.YMin},{extent_wgs84.XMin},{extent_wgs84.YMax},{extent_wgs84.XMax}"

        # 2. Resolve County Area ID if county name method used
        area_id = None
        if method in ["County Name (Geocoded Boundary)", "Both (County Boundary clipped to Extent)"] and county_name:
            arcpy.AddMessage(f"Resolving county boundary for '{county_name}' via Nominatim...")
            area_id, nom_bbox = resolve_nominatim_area(county_name)
            if area_id:
                arcpy.AddMessage(f"-> Resolved to OSM Area ID: {area_id}")
            else:
                arcpy.AddMessage("-> Nominatim area resolution fallback to Overpass name search...")

        # 3. Construct Overpass QL Query
        bbox_clause = f"({bbox_str})" if bbox_str else ""
        if area_id:
            overpass_query = f"""[out:json][timeout:180];
area({area_id})->.searchArea;
(
  nwr["landuse"="cemetery"](area.searchArea){bbox_clause};
  nwr["amenity"="grave_yard"](area.searchArea){bbox_clause};
  nwr["cemetery"](area.searchArea){bbox_clause};
);
out meta;
>;
out meta qt;"""
        elif method == "Map / Layer Extent (Bounding Box)" and bbox_str:
            overpass_query = f"""[out:json][timeout:180];
(
  nwr["landuse"="cemetery"]({bbox_str});
  nwr["amenity"="grave_yard"]({bbox_str});
  nwr["cemetery"]({bbox_str});
);
out meta;
>;
out meta qt;"""
        elif county_name:
            clean_county = county_name.split(',')[0].strip()
            overpass_query = f"""[out:json][timeout:180];
area["name"="{clean_county}"]["admin_level"="6"]->.searchArea;
(
  nwr["landuse"="cemetery"](area.searchArea){bbox_clause};
  nwr["amenity"="grave_yard"](area.searchArea){bbox_clause};
  nwr["cemetery"](area.searchArea){bbox_clause};
);
out meta;
>;
out meta qt;"""
        else:
            arcpy.AddError("You must provide either a County Name or a valid Map/Layer Extent.")
            return

        arcpy.AddMessage("Connecting to Overpass API to query cemeteries...")
        osm_data = None
        for endpoint in OVERPASS_ENDPOINTS:
            try:
                arcpy.AddMessage(f"Querying endpoint: {endpoint}")
                req = urllib.request.Request(endpoint, data=overpass_query.encode('utf-8'))
                req.add_header('User-Agent', 'ArcGIS-Pro-OSM-Editor/1.0')
                with urllib.request.urlopen(req, timeout=180) as response:
                    if response.status == 200:
                        osm_data = json.loads(response.read().decode('utf-8'))
                        break
                    else:
                        arcpy.AddMessage(f"  -> Server returned status {response.status}, failing over...")
            except Exception as e:
                arcpy.AddMessage(f"  -> Endpoint notice ({str(e)}), failing over...")

        if not osm_data:
            arcpy.AddError("All Overpass API endpoints failed or timed out. Please try again in a few moments.")
            return

        elements = osm_data.get("elements", [])
        if not elements:
            arcpy.AddWarning("No cemetery features found for this selection.")
            return

        arcpy.AddMessage(f"Downloaded {len(elements)} raw elements from OSM. Filtering and parsing geometries...")

        # Parse nodes, ways, relations
        nodes = {}
        tagged_nodes = []
        ways = []
        way_dict = {}
        relations = []

        for el in elements:
            e_type = el.get("type")
            if e_type == "node":
                nodes[el["id"]] = (el["lon"], el["lat"])
                if "tags" in el:
                    tagged_nodes.append(el)
            elif e_type == "way":
                ways.append(el)
                way_dict[el["id"]] = el
            elif e_type == "relation":
                relations.append(el)

        def matches_filter(tags_dict):
            has_wikidata = bool(tags_dict.get("wikidata", "").strip())
            has_fag = bool(tags_dict.get("ref:find_a_grave", "").strip() or tags_dict.get("findagrave", "").strip())

            if id_filter == "Missing Wikidata ID ([!wikidata])":
                return not has_wikidata
            elif id_filter == "Missing Find a Grave ID ([!ref:find_a_grave])":
                return not has_fag
            elif id_filter == "Missing Either Wikidata OR Find a Grave ID":
                return (not has_wikidata) or (not has_fag)
            elif id_filter == "Missing Both Wikidata AND Find a Grave IDs":
                return (not has_wikidata) and (not has_fag)
            return True

        # Prepare target feature classes
        pt_fc = os.path.join(out_gdb, f"{prefix}_Points")
        py_fc = os.path.join(out_gdb, f"{prefix}_Polygons")
        top_fc = os.path.join(out_gdb, "OSM_Topology_Nodes")

        # Action domain
        domain_name = "OSM_Action_Domain"
        try:
            existing_domains = [d.name for d in arcpy.da.ListDomains(out_gdb)]
            if domain_name not in existing_domains:
                arcpy.management.CreateDomain(out_gdb, domain_name, "OpenStreetMap Upload Action", "TEXT", "CODED")
                arcpy.management.AddCodedValueToDomain(out_gdb, domain_name, "none", "None (Do not upload)")
                arcpy.management.AddCodedValueToDomain(out_gdb, domain_name, "create", "Create (New Feature)")
                arcpy.management.AddCodedValueToDomain(out_gdb, domain_name, "modify", "Modify (Edit Feature)")
                arcpy.management.AddCodedValueToDomain(out_gdb, domain_name, "delete", "Delete (Remove Feature)")
        except Exception:
            pass

        def create_cem_fc(fc, geom_type):
            if not arcpy.Exists(fc):
                arcpy.management.CreateFeatureclass(out_gdb, os.path.basename(fc), geom_type, spatial_reference=sr_wgs84)

            existing_fields = [f.name for f in arcpy.ListFields(fc)]
            def add_field_safely(fname, ftype, flen=None):
                if fname not in existing_fields:
                    arcpy.management.AddField(fc, fname, ftype, field_length=flen)

            add_field_safely("osm_id", "TEXT", 25)
            add_field_safely("name", "TEXT", 255)
            add_field_safely("cemetery_type", "TEXT", 50)
            add_field_safely("wikidata", "TEXT", 50)
            add_field_safely("ref_find_a_grave", "TEXT", 50)
            add_field_safely("gnis_feature_id", "TEXT", 50)
            add_field_safely("religion", "TEXT", 50)
            add_field_safely("denomination", "TEXT", 50)
            add_field_safely("addr_housenumber", "TEXT", 50)
            add_field_safely("addr_street", "TEXT", 150)
            add_field_safely("addr_city", "TEXT", 100)
            add_field_safely("addr_state", "TEXT", 50)
            add_field_safely("addr_postcode", "TEXT", 20)
            add_field_safely("addr_full", "TEXT", 255)
            add_field_safely("osm_version", "LONG")
            add_field_safely("osm_action", "TEXT", 20)
            add_field_safely("osm_tags", "TEXT", 2000)

            try:
                arcpy.management.AssignDomainToField(fc, "osm_action", domain_name)
                arcpy.management.AssignDefaultToField(fc, "osm_action", "create")
            except Exception:
                pass

            try:
                arcpy.management.EnableEditorTracking(
                    fc,
                    creator_field="created_user",
                    creation_date_field="created_date",
                    last_editor_field="last_edited_user",
                    last_edit_date_field="last_edited_date",
                    add_fields="ADD_FIELDS",
                    record_dates_in="UTC"
                )
            except Exception:
                pass

        create_cem_fc(pt_fc, "POINT")
        create_cem_fc(py_fc, "POLYGON")

        if not arcpy.Exists(top_fc):
            arcpy.management.CreateFeatureclass(out_gdb, "OSM_Topology_Nodes", "POINT", spatial_reference=sr_wgs84)
            arcpy.management.AddField(top_fc, "osm_id", "TEXT", field_length=25)

        def get_existing_ids(fc):
            ids = set()
            if arcpy.Exists(fc) and int(arcpy.management.GetCount(fc).getOutput(0)) > 0:
                with arcpy.da.SearchCursor(fc, ["osm_id"]) as cur:
                    for row in cur:
                        if row[0]:
                            ids.add(str(row[0]).strip())
            return ids

        existing_pts = get_existing_ids(pt_fc)
        existing_pys = get_existing_ids(py_fc)
        existing_top = get_existing_ids(top_fc)

        # Store topology nodes for snapping
        nodes_to_insert = [(nid, lon, lat) for nid, (lon, lat) in nodes.items() if str(nid) not in existing_top]
        if nodes_to_insert:
            with arcpy.da.InsertCursor(top_fc, ["SHAPE@", "osm_id"]) as cur:
                for nid, lon, lat in nodes_to_insert:
                    cur.insertRow([arcpy.Point(lon, lat), str(nid)])

        cem_fields = [
            "SHAPE@", "osm_id", "name", "cemetery_type", "wikidata", "ref_find_a_grave",
            "gnis_feature_id", "religion", "denomination", "addr_housenumber",
            "addr_street", "addr_city", "addr_state", "addr_postcode", "addr_full",
            "osm_version", "osm_action", "osm_tags"
        ]

        def extract_cem_row(geom, osm_id, tags_dict, version):
            name = tags_dict.get("name", "")
            c_type = "grave_yard" if tags_dict.get("amenity") == "grave_yard" else "cemetery"
            wikidata = tags_dict.get("wikidata", "")
            fag = tags_dict.get("ref:find_a_grave", tags_dict.get("findagrave", ""))
            gnis = tags_dict.get("gnis:feature_id", "")
            religion = tags_dict.get("religion", "")
            denom = tags_dict.get("denomination", "")
            hn = tags_dict.get("addr:housenumber", "")
            st = tags_dict.get("addr:street", "")
            ct = tags_dict.get("addr:city", "")
            state = tags_dict.get("addr:state", "")
            pc = tags_dict.get("addr:postcode", "")
            full = tags_dict.get("addr:full", "")
            tags_str = json.dumps(tags_dict)

            return [
                geom, str(osm_id), name[:254], c_type[:49], wikidata[:49], fag[:49],
                gnis[:49], religion[:49], denom[:49], hn[:49], st[:149], ct[:99],
                state[:49], pc[:19], full[:254], int(version) if version else 1, "none",
                tags_str[:1999]
            ]

        # 1. Process Points
        cemetery_points = []
        for n in tagged_nodes:
            t = n.get("tags", {})
            if t.get("landuse") == "cemetery" or t.get("amenity") == "grave_yard" or "cemetery" in t:
                if matches_filter(t):
                    s_id = str(n["id"])
                    if s_id not in existing_pts:
                        pt_g = arcpy.PointGeometry(arcpy.Point(n["lon"], n["lat"]), sr_wgs84)
                        cemetery_points.append(extract_cem_row(pt_g, s_id, t, n.get("version", 1)))

        if cemetery_points:
            arcpy.AddMessage(f"Writing {len(cemetery_points)} cemetery point marker(s) into '{os.path.basename(pt_fc)}'...")
            with arcpy.da.InsertCursor(pt_fc, cem_fields) as cur_pt:
                for row in cemetery_points:
                    cur_pt.insertRow(row)

        # 2. Process Polygons (Ways and Relations)
        cemetery_polygons = []
        processed_way_ids = set()

        for w in ways:
            t = w.get("tags", {})
            if t.get("landuse") == "cemetery" or t.get("amenity") == "grave_yard" or "cemetery" in t:
                if matches_filter(t):
                    s_id = str(w["id"])
                    processed_way_ids.add(w["id"])
                    if s_id not in existing_pys:
                        node_refs = w.get("nodes", [])
                        if len(node_refs) >= 3:
                            arr = arcpy.Array([arcpy.Point(nodes[nid][0], nodes[nid][1]) for nid in node_refs if nid in nodes])
                            if len(arr) >= 3:
                                poly_g = arcpy.Polygon(arr, sr_wgs84)
                                if poly_g and poly_g.area > 0:
                                    cemetery_polygons.append(extract_cem_row(poly_g, s_id, t, w.get("version", 1)))

        # Process Relations (multipolygons)
        for rel in relations:
            t = rel.get("tags", {})
            if t.get("landuse") == "cemetery" or t.get("amenity") == "grave_yard" or "cemetery" in t:
                if matches_filter(t):
                    s_id = str(rel["id"])
                    if s_id not in existing_pys:
                        members = rel.get("members", [])
                        outer_rings = []
                        for m in members:
                            if m.get("type") == "way" and m.get("role") in ["outer", ""]:
                                w_id = m.get("ref")
                                if w_id in way_dict:
                                    w_elem = way_dict[w_id]
                                    w_nodes = w_elem.get("nodes", [])
                                    if len(w_nodes) >= 3:
                                        w_arr = arcpy.Array([arcpy.Point(nodes[nid][0], nodes[nid][1]) for nid in w_nodes if nid in nodes])
                                        if len(w_arr) >= 3:
                                            outer_rings.append(w_arr)
                        if outer_rings:
                            poly_g = arcpy.Polygon(outer_rings[0], sr_wgs84)
                            if poly_g and poly_g.area > 0:
                                cemetery_polygons.append(extract_cem_row(poly_g, s_id, t, rel.get("version", 1)))

        if cemetery_polygons:
            arcpy.AddMessage(f"Writing {len(cemetery_polygons)} cemetery boundary polygon(s) into '{os.path.basename(py_fc)}'...")
            with arcpy.da.InsertCursor(py_fc, cem_fields) as cur_py:
                for row in cemetery_polygons:
                    cur_py.insertRow(row)

        # 3. Add to Active Map
        try:
            aprx = arcpy.mp.ArcGISProject("CURRENT")
            active_map = aprx.activeMap
            if active_map:
                existing_lyrs = [lyr.name for lyr in active_map.listLayers()]
                py_name = os.path.basename(py_fc)
                pt_name = os.path.basename(pt_fc)

                if py_name not in existing_lyrs and arcpy.Exists(py_fc) and int(arcpy.management.GetCount(py_fc).getOutput(0)) > 0:
                    active_map.addDataFromPath(py_fc)
                    arcpy.AddMessage(f"Added '{py_name}' to active map.")
                if pt_name not in existing_lyrs and arcpy.Exists(pt_fc) and int(arcpy.management.GetCount(pt_fc).getOutput(0)) > 0:
                    active_map.addDataFromPath(pt_fc)
                    arcpy.AddMessage(f"Added '{pt_name}' to active map.")

                try:
                    arcpy.RefreshActiveView()
                except Exception:
                    pass
        except Exception as e:
            arcpy.AddWarning(f"Notice: Could not automatically add layer to map: {e}")

        arcpy.AddMessage(f"SUCCESS! Downloaded and indexed {len(cemetery_polygons)} polygon(s) and {len(cemetery_points)} point marker(s).")
        return


class UploadOSMData(object):
    def __init__(self):
        self.label = "Upload Edits to OSM"
        self.description = "Finds modifications in the local data and uploads a changeset back to OpenStreetMap."
        self.canRunInBackground = False

    def getParameterInfo(self):
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

        param_token = arcpy.Parameter(
            displayName="OSM Personal Access Token (Bearer Token)",
            name="bearer_token",
            datatype="GPStringHidden",
            parameterType="Optional",
            direction="Input"
        )
        token_env = os.environ.get("OSM_BEARER_TOKEN", "").strip()
        if token_env:
            param_token.value = token_env
        elif OSM_BEARER_TOKEN and OSM_BEARER_TOKEN != "your_personal_access_token_here":
            param_token.value = OSM_BEARER_TOKEN

        params = [param_in_layers, param_changeset_comment, param_token]
        return params

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        return

    def updateMessages(self, parameters):
        return

    def execute(self, parameters, messages):
        raw_layers = parameters[0].valueAsText
        in_layers = [lyr.strip("'") for lyr in raw_layers.split(';')] if raw_layers else []
        comment = parameters[1].valueAsText
        user_token = parameters[2].valueAsText.strip() if len(parameters) > 2 and parameters[2].value else None
        active_token = user_token or os.environ.get("OSM_BEARER_TOKEN", "").strip() or OSM_BEARER_TOKEN

        arcpy.AddMessage("Validating credentials...")
        if not active_token or active_token == "your_personal_access_token_here":
            arcpy.AddError("OSM Personal Access Token is missing. Provide it in the tool parameter, set the OSM_BEARER_TOKEN environment variable, or update OSM_Editor.pyt.")
            return

        edited_features_count = 0
        arcpy.AddMessage("Scanning selected features for manual OSM edits and auto-detecting spatial modifications...")

        for layer in in_layers:
            if arcpy.Exists(layer):
                desc = arcpy.Describe(layer)
                has_editor_tracking = getattr(desc, 'editorTrackingEnabled', False)
                c_field = getattr(desc, 'creationDateField', "")
                e_field = getattr(desc, 'editDateField', "")

                s_fields = ["osm_action"]
                if has_editor_tracking and c_field and e_field:
                    s_fields.extend([c_field, e_field])

                with arcpy.da.SearchCursor(layer, s_fields) as cur:
                    for row in cur:
                        action = row[0]
                        if action in ['create', 'modify', 'delete']:
                            edited_features_count += 1
                        elif has_editor_tracking and len(row) > 1:
                            c_date, e_date = row[1], row[2]
                            if c_date and e_date and (e_date - c_date).total_seconds() > 2.0:
                                edited_features_count += 1

        if edited_features_count == 0:
            arcpy.AddWarning("No edited features found in your selection.")
            arcpy.AddMessage("Remember to change the 'osm_action' field (to 'create', 'modify', or 'delete') in the Attribute Table on the precise features you want to upload!")
            return

        arcpy.AddMessage("Authenticating with standard OAuth 2.0 Bearer Token...")
        auth_header = f"Bearer {active_token}"

        arcpy.AddMessage(f"Opening OSM Changeset with comment: '{comment}'")
        changeset_xml = f"""<osm>
  <changeset>
    <tag k="created_by" v="ArcGIS Pro OSM Editor"/>
    <tag k="comment" v="{xml_escape_attr(comment)}"/>
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
            arcpy.AddError(f"Failed to open Changeset: {e.read().decode('utf-8') if hasattr(e, 'read') else str(e)}")
            return

        topology_cache = {}
        sr_wgs84 = arcpy.SpatialReference(4326)

        if in_layers:
            workspace = get_workspace_gdb(in_layers[0])
            top_fc = os.path.join(workspace, "OSM_Topology_Nodes") if workspace else ""

            if top_fc and arcpy.Exists(top_fc):
                arcpy.AddMessage("Loading topology backbone to ensure proper path snapping...")
                with arcpy.da.SearchCursor(top_fc, ["SHAPE@XY", "osm_id"], spatial_reference=sr_wgs84) as cur:
                    for (x, y), osm_id in cur:
                        if x is not None and y is not None:
                            topology_cache[(round(x, 6), round(y, 6))] = str(osm_id)

        xml_create = []
        xml_modify = []
        xml_delete = []
        negative_id_counter = -1

        arcpy.AddMessage("Extracting geometries and translating to OSM XML...")
        feature_records = []

        for layer in in_layers:
            if not arcpy.Exists(layer): continue

            desc = arcpy.Describe(layer)
            geom_type = desc.shapeType
            all_fields = [f.name for f in arcpy.ListFields(layer)]
            has_editor_tracking = getattr(desc, 'editorTrackingEnabled', False)
            c_field = getattr(desc, 'creationDateField', "")
            e_field = getattr(desc, 'editDateField', "")

            semantic_mapping = {
                "name": "name",
                "highway": "highway",
                "building": "building",
                "natural": "natural",
                "waterway": "waterway",
                "amenity": "amenity",
                "landuse": "landuse",
                "leisure": "leisure",
                "addr_housenumber": "addr:housenumber",
                "addr_street": "addr:street",
                "addr_unit": "addr:unit",
                "addr_city": "addr:city",
                "addr_state": "addr:state",
                "addr_postcode": "addr:postcode",
                "addr_full": "addr:full"
            }
            active_semantics = [f for f in semantic_mapping.keys() if f in all_fields]
            search_fields = ["OID@", "SHAPE@", "osm_id", "osm_version", "osm_action", "osm_tags"] + active_semantics

            offset = len(search_fields)
            if has_editor_tracking and c_field and e_field:
                search_fields.extend([c_field, e_field])

            processed_count = 0
            with arcpy.da.SearchCursor(layer, search_fields, spatial_reference=sr_wgs84) as cur:
                for row in cur:
                    is_modified = False
                    if has_editor_tracking and len(row) > offset:
                        c_date, e_date = row[offset], row[offset+1]
                        if c_date and e_date and (e_date - c_date).total_seconds() > 2.0:
                            is_modified = True

                    oid = row[0]
                    geom = row[1]
                    o_id = row[2]
                    o_version = row[3]
                    o_action = row[4]

                    if o_action in ['create', 'modify', 'delete'] or is_modified:
                        processed_count += 1
                        if processed_count % 100 == 0:
                            arcpy.AddMessage(f"  -> Translated {processed_count} edited features from '{os.path.basename(layer)}' to OSM XML...")

                    o_tags = row[5]

                    if o_action not in ['create', 'modify', 'delete'] and not is_modified:
                        continue

                    if is_modified and o_action == 'none':
                        o_action = 'modify'

                    if not geom and o_action != "delete":
                        continue

                    if not o_id or str(o_id).strip() == "":
                        if o_action == "delete":
                            continue
                        o_action = "create"
                    else:
                        if not o_version or (isinstance(o_version, int) and o_version <= 0):
                            live_v = fetch_osm_element_version("node" if geom_type == "Point" else "way", o_id, token=active_token)
                            o_version = live_v if live_v else 1

                    try:
                        tags_dict = json.loads(o_tags) if o_tags else {}
                    except Exception:
                        tags_dict = {}

                    for idx, field_name in enumerate(active_semantics):
                        val = row[6 + idx]
                        tag_key = semantic_mapping[field_name]
                        if val is not None and str(val).strip() != "":
                            str_val = str(val).strip()
                            if tag_key == "addr:street":
                                str_val = normalize_osm_street(str_val)
                            elif tag_key == "addr:unit":
                                str_val = normalize_osm_unit(str_val)
                            elif tag_key == "addr:city":
                                str_val = normalize_osm_city(str_val)
                            elif tag_key == "addr:state":
                                str_val = str_val.upper() if len(str_val) == 2 else normalize_osm_name(str_val)
                            elif tag_key == "name":
                                str_val = normalize_osm_name(str_val)
                            tags_dict[tag_key] = str_val
                        else:
                            if tag_key in tags_dict:
                                del tags_dict[tag_key]

                    if "addr:street" in tags_dict:
                        tags_dict["addr:street"] = normalize_osm_street(tags_dict["addr:street"])
                    if "addr:unit" in tags_dict:
                        tags_dict["addr:unit"] = normalize_osm_unit(tags_dict["addr:unit"])
                    if "addr:city" in tags_dict:
                        tags_dict["addr:city"] = normalize_osm_city(tags_dict["addr:city"])
                    if "addr:state" in tags_dict:
                        st_v = str(tags_dict["addr:state"]).strip()
                        tags_dict["addr:state"] = st_v.upper() if len(st_v) == 2 else normalize_osm_name(st_v)
                    if "name" in tags_dict:
                        tags_dict["name"] = normalize_osm_name(tags_dict["name"])

                    if geom_type == "Polygon":
                        area_keys = ["building", "landuse", "leisure", "amenity", "natural", "shop", "tourism", "historic", "barrier", "area"]
                        if not any(k in tags_dict for k in area_keys):
                            tags_dict["building"] = "yes"

                    tags_xml = "".join([f'<tag k="{xml_escape_attr(k)}" v="{xml_escape_attr(v)}"/>' for k, v in tags_dict.items()])

                    if geom_type == "Point":
                        p = geom.firstPoint if geom else None
                        node_id = str(o_id) if o_id and o_action != "create" else str(negative_id_counter)
                        node_version = f' version="{o_version}"' if o_action != "create" and o_version else ""

                        feature_records.append((layer, oid, node_id, geom_type, o_action))

                        if o_action == "create":
                            node_xml = f'<node id="{node_id}" lat="{p.Y:.7f}" lon="{p.X:.7f}" changeset="{changeset_id}">{tags_xml}</node>'
                            xml_create.append(node_xml)
                            negative_id_counter -= 1
                        elif o_action == "modify":
                            node_xml = f'<node id="{node_id}" lat="{p.Y:.7f}" lon="{p.X:.7f}" changeset="{changeset_id}"{node_version}>{tags_xml}</node>'
                            xml_modify.append(node_xml)
                        elif o_action == "delete":
                            node_xml = f'<node id="{node_id}" changeset="{changeset_id}" version="{o_version}"/>'
                            xml_delete.append(node_xml)

                    elif geom_type in ["Polyline", "Polygon"]:
                        way_id = str(o_id) if o_id and o_action != "create" else str(negative_id_counter)
                        way_version = f' version="{o_version}"' if o_action != "create" and o_version else ""
                        feature_records.append((layer, oid, way_id, geom_type, o_action))

                        if o_action == "delete":
                            way_xml = f'<way id="{way_id}" changeset="{changeset_id}" version="{o_version}"/>'
                            xml_delete.append(way_xml)
                            continue

                        if o_action == "create":
                            negative_id_counter -= 1

                        nd_refs = []
                        # Outer ring only for Polygons to ensure topology validity in OSM
                        parts_to_process = [geom.getPart(0)] if geom_type == "Polygon" else geom

                        for part in parts_to_process:
                            for p in part:
                                if p:
                                    x_r, y_r = round(p.X, 6), round(p.Y, 6)
                                    snap_id = topology_cache.get((x_r, y_r))
                                    if snap_id:
                                        node_ref_id = str(snap_id)
                                    else:
                                        node_ref_id = str(negative_id_counter)
                                        negative_id_counter -= 1
                                        xml_create.append(f'<node id="{node_ref_id}" lat="{p.Y:.7f}" lon="{p.X:.7f}" changeset="{changeset_id}"/>')
                                        topology_cache[(x_r, y_r)] = node_ref_id

                                    # Prevent consecutive duplicate nodes which violate OSM API specifications
                                    ref_tag = f'<nd ref="{node_ref_id}"/>'
                                    if not nd_refs or nd_refs[-1] != ref_tag:
                                        nd_refs.append(ref_tag)

                        # Enforce polygon closure
                        if geom_type == "Polygon" and len(nd_refs) >= 3:
                            if nd_refs[0] != nd_refs[-1]:
                                nd_refs.append(nd_refs[0])

                        min_refs = 4 if geom_type == "Polygon" else 2
                        if len(nd_refs) < min_refs:
                            arcpy.AddWarning(f"Skipping feature OID {oid} on '{layer}': Insufficient distinct vertices ({len(nd_refs)}).")
                            continue

                        way_xml = f'<way id="{way_id}" changeset="{changeset_id}"{way_version}>' + "".join(nd_refs) + tags_xml + "</way>"

                        if o_action == "create": xml_create.append(way_xml)
                        elif o_action == "modify": xml_modify.append(way_xml)

        arcpy.AddMessage("Transmitting XML packets to the OSM API...")
        osm_change_payload = f"""<osmChange version="0.6" generator="ArcGIS Pro OSM Editor">
  <create>{"".join(xml_create)}</create>
  <modify>{"".join(xml_modify)}</modify>
  <delete>{"".join(xml_delete)}</delete>
</osmChange>"""

        result = ""
        current_payload = osm_change_payload
        max_conflict_retries = 5
        retry_count = 0

        while retry_count <= max_conflict_retries:
            req = urllib.request.Request(f"{OSM_API_URL}changeset/{changeset_id}/upload", data=current_payload.encode('utf-8'), method='POST')
            req.add_header('Authorization', auth_header)
            req.add_header('Content-Type', 'text/xml')

            try:
                with urllib.request.urlopen(req) as response:
                    result = response.read().decode('utf-8')
                    if retry_count > 0:
                        arcpy.AddMessage(f"-> Edits accepted successfully by OpenStreetMap after conflict auto-resolution!")
                    else:
                        arcpy.AddMessage("-> Edits accepted successfully by OpenStreetMap!")
                    break
            except urllib.error.URLError as e:
                err_msg = e.read().decode('utf-8') if hasattr(e, 'read') else str(e)
                vm_matches = re.findall(r'Version mismatch:\s*Provided\s*\d+,\s*server\s*had:\s*(\d+)\s*of\s*(Node|Way)\s*(\d+)', err_msg, re.IGNORECASE)
                if vm_matches and retry_count < max_conflict_retries:
                    retry_count += 1
                    arcpy.AddMessage(f"-> Auto-resolving {len(vm_matches)} version conflict(s) with OSM server (Attempt {retry_count}/{max_conflict_retries})...")
                    for s_ver_str, e_kind, e_id in vm_matches:
                        s_ver = int(s_ver_str)
                        for lyr in in_layers:
                            if arcpy.Exists(lyr):
                                lyr_fields = [f.name for f in arcpy.ListFields(lyr)]
                                if "osm_id" in lyr_fields and "osm_version" in lyr_fields:
                                    with arcpy.da.UpdateCursor(lyr, ["osm_id", "osm_version"]) as ucur:
                                        for urow in ucur:
                                            if urow[0] and str(urow[0]).strip() == str(e_id):
                                                urow[1] = s_ver
                                                ucur.updateRow(urow)

                        current_payload = re.sub(
                            rf'(<(?:node|way)\s+[^>]*id="{e_id}"[^>]*\s+version=")\d+(")',
                            rf'\g<1>{s_ver}\g<2>',
                            current_payload
                        )
                        if rf'id="{e_id}"' in current_payload and rf'id="{e_id}" version="{s_ver}"' not in current_payload:
                            current_payload = re.sub(
                                rf'id="{e_id}"',
                                rf'id="{e_id}" version="{s_ver}"',
                                current_payload
                            )
                else:
                    arcpy.AddError(f"OSM Rejected the upload due to server conflicts: {err_msg}")
                    return

        import xml.etree.ElementTree as ET
        id_map = {}
        if result:
            try:
                diff_root = ET.fromstring(result)
                for elem in diff_root:
                    e_type = elem.tag
                    old_id = elem.attrib.get("old_id")
                    new_id = elem.attrib.get("new_id")
                    new_ver = elem.attrib.get("new_version")
                    if old_id and new_id:
                        id_map[(e_type, str(old_id))] = (str(new_id), int(new_ver) if new_ver else 1)
            except Exception as e:
                arcpy.AddWarning(f"Notice: Could not parse diffResult XML: {e}")

        arcpy.AddMessage("Syncing local layer attributes with OpenStreetMap...")
        layer_records = {}
        for lyr, oid, sent_id, g_type, act in feature_records:
            layer_records.setdefault(lyr, []).append((oid, sent_id, g_type, act))

        for lyr, records in layer_records.items():
            rec_by_oid = {r[0]: (r[1], r[2], r[3]) for r in records}
            fields_to_update = ["OID@", "osm_id", "osm_version", "osm_action"]
            try:
                with arcpy.da.UpdateCursor(lyr, fields_to_update) as u_cur:
                    for u_row in u_cur:
                        u_oid = u_row[0]
                        if u_oid in rec_by_oid:
                            sent_id, g_type, act = rec_by_oid[u_oid]
                            if act == "delete":
                                u_cur.deleteRow()
                                continue

                            e_type = "node" if g_type == "Point" else "way"
                            key = (e_type, str(sent_id))
                            if key in id_map:
                                new_id, new_ver = id_map[key]
                                u_row[1] = str(new_id)
                                u_row[2] = int(new_ver)
                                u_row[3] = "none"
                                u_cur.updateRow(u_row)
                            else:
                                u_row[3] = "none"
                                u_cur.updateRow(u_row)
            except Exception as e:
                arcpy.AddWarning(f"Notice: Could not update status on {os.path.basename(lyr)}: {e}")

        arcpy.AddMessage("Closing mapping changeset...")
        req = urllib.request.Request(f"{OSM_API_URL}changeset/{changeset_id}/close", method='PUT')
        req.add_header('Authorization', auth_header)
        try:
            urllib.request.urlopen(req)
        except: pass

        arcpy.AddMessage(f"SUCCESS! Your edits are officially live on the map: https://www.openstreetmap.org/changeset/{changeset_id}")
        return


class TagBuildingsFromPoints(object):
    def __init__(self):
        self.label = "Tag OSM Buildings from Address & Name Points"
        self.description = (
            "Performs an ultra-fast spatial join from site address points and structure common name points into "
            "the OSM building footprints layer (OSM_Polygons), mapping address details (addr:*) "
            "and building names directly into OSM-ready attributes and tags."
        )
        self.canRunInBackground = False

    def getParameterInfo(self):
        param_buildings = arcpy.Parameter(
            displayName="OSM Building Footprints Layer (e.g. OSM_Polygons)",
            name="in_buildings",
            datatype="GPFeatureLayer",
            parameterType="Required",
            direction="Input"
        )
        param_buildings.filter.list = ["Polygon"]
        param_buildings.value = "OSM_Polygons"

        param_addr_layer = arcpy.Parameter(
            displayName="Site Address Points Layer",
            name="in_address_points",
            datatype="GPFeatureLayer",
            parameterType="Optional",
            direction="Input"
        )
        param_addr_layer.filter.list = ["Point"]
        param_addr_layer.value = "Sites"

        param_housenumber = arcpy.Parameter(
            displayName="House / Street Number Field (addr:housenumber)",
            name="field_housenumber",
            datatype="Field",
            parameterType="Optional",
            direction="Input"
        )
        param_housenumber.parameterDependencies = [param_addr_layer.name]
        param_housenumber.value = "ADDNUM_NEW"

        param_street = arcpy.Parameter(
            displayName="Street Name Field (addr:street)",
            name="field_street",
            datatype="Field",
            parameterType="Optional",
            direction="Input"
        )
        param_street.parameterDependencies = [param_addr_layer.name]
        param_street.value = "Full Roadname"

        param_unit_type = arcpy.Parameter(
            displayName="Unit / Apt Type Field (e.g. Apt Type, Suite, Unit)",
            name="field_unit_type",
            datatype="Field",
            parameterType="Optional",
            direction="Input"
        )
        param_unit_type.parameterDependencies = [param_addr_layer.name]
        param_unit_type.value = "Apt Type"

        param_unit_num = arcpy.Parameter(
            displayName="Unit / Apt Number Field (e.g. Apt Num, Unit ID, Suite #)",
            name="field_unit_num",
            datatype="Field",
            parameterType="Optional",
            direction="Input"
        )
        param_unit_num.parameterDependencies = [param_addr_layer.name]
        param_unit_num.value = "Apt"

        param_city = arcpy.Parameter(
            displayName="City Field (addr:city)",
            name="field_city",
            datatype="Field",
            parameterType="Optional",
            direction="Input"
        )
        param_city.parameterDependencies = [param_addr_layer.name]
        param_city.value = "City"

        param_state = arcpy.Parameter(
            displayName="State Field (addr:state)",
            name="field_state",
            datatype="Field",
            parameterType="Optional",
            direction="Input"
        )
        param_state.parameterDependencies = [param_addr_layer.name]

        param_default_state = arcpy.Parameter(
            displayName="Default State (if unmapped or blank)",
            name="default_state",
            datatype="GPString",
            parameterType="Optional",
            direction="Input"
        )
        param_default_state.value = "WV"

        param_postcode = arcpy.Parameter(
            displayName="Postal / Zip Code Field (addr:postcode)",
            name="field_postcode",
            datatype="Field",
            parameterType="Optional",
            direction="Input"
        )
        param_postcode.parameterDependencies = [param_addr_layer.name]
        param_postcode.value = "Zip Code"

        param_full_addr = arcpy.Parameter(
            displayName="Full Address Field (Alternative to parsed fields)",
            name="field_full_address",
            datatype="Field",
            parameterType="Optional",
            direction="Input"
        )
        param_full_addr.parameterDependencies = [param_addr_layer.name]
        param_full_addr.value = "Full Address"

        param_name_layer = arcpy.Parameter(
            displayName="Structure Common Name Points Layer",
            name="in_name_points",
            datatype="GPFeatureLayer",
            parameterType="Optional",
            direction="Input"
        )
        param_name_layer.filter.list = ["Point"]
        param_name_layer.value = "Common Name"

        param_building_name = arcpy.Parameter(
            displayName="Building / Structure Name Field (name)",
            name="field_building_name",
            datatype="Field",
            parameterType="Optional",
            direction="Input"
        )
        param_building_name.parameterDependencies = [param_name_layer.name]
        param_building_name.value = "COMMON_NAME"

        param_out_points = arcpy.Parameter(
            displayName="OSM Points Layer (for Multi-Unit / Apartment address nodes)",
            name="out_points_layer",
            datatype="GPFeatureLayer",
            parameterType="Optional",
            direction="Input"
        )
        param_out_points.filter.list = ["Point"]
        param_out_points.value = "OSM_Points"

        param_multiunit_mode = arcpy.Parameter(
            displayName="Multi-Unit / Apartment Handling",
            name="multiunit_handling",
            datatype="GPString",
            parameterType="Required",
            direction="Input"
        )
        param_multiunit_mode.filter.type = "ValueList"
        param_multiunit_mode.filter.list = [
            "Base Address on Building + Export Unit Points to OSM_Points (Recommended)",
            "Combine all units onto Building Polygon (e.g. Apt 1, Apt 2)",
            "Ignore multiple units (Use first address on Building)"
        ]
        param_multiunit_mode.value = "Base Address on Building + Export Unit Points to OSM_Points (Recommended)"

        param_search_distance = arcpy.Parameter(
            displayName="Search Distance / Tolerance (Meters)",
            name="search_distance",
            datatype="GPDouble",
            parameterType="Optional",
            direction="Input"
        )
        param_search_distance.value = 0.0

        param_action = arcpy.Parameter(
            displayName="Upload Action Flag (osm_action)",
            name="action_handling",
            datatype="GPString",
            parameterType="Required",
            direction="Input"
        )
        param_action.filter.type = "ValueList"
        param_action.filter.list = [
            "Mark for Upload ('modify' existing, 'create' new)",
            "Set to 'modify'",
            "Set to 'create'",
            "Leave osm_action unchanged"
        ]
        param_action.value = "Mark for Upload ('modify' existing, 'create' new)"

        param_overwrite = arcpy.Parameter(
            displayName="Overwrite Existing Address / Name Values",
            name="overwrite_existing",
            datatype="GPBoolean",
            parameterType="Required",
            direction="Input"
        )
        param_overwrite.value = True

        param_normalize = arcpy.Parameter(
            displayName="Format to OSM Standards (Title Case & Expand Abbreviations)",
            name="normalize_osm",
            datatype="GPBoolean",
            parameterType="Required",
            direction="Input"
        )
        param_normalize.value = True

        return [
            param_buildings,
            param_addr_layer,
            param_housenumber,
            param_street,
            param_unit_type,
            param_unit_num,
            param_city,
            param_state,
            param_default_state,
            param_postcode,
            param_full_addr,
            param_name_layer,
            param_building_name,
            param_out_points,
            param_multiunit_mode,
            param_search_distance,
            param_action,
            param_overwrite,
            param_normalize
        ]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        if not parameters[0].altered and not parameters[0].value:
            parameters[0].value = "OSM_Polygons"
        if not parameters[1].altered and not parameters[1].value:
            parameters[1].value = "Sites"
        if not parameters[11].altered and not parameters[11].value:
            parameters[11].value = "Common Name"
        if not parameters[13].altered and not parameters[13].value:
            parameters[13].value = "OSM_Points"

        if parameters[1].value:
            try:
                addr_layer_fields = [f.name for f in arcpy.ListFields(parameters[1].valueAsText)]
                if not parameters[2].altered and not parameters[2].value:
                    for candidate in ["ADDNUM_NEW", "ADDNUM", "HOUSENUMBER", "HOUSE_NUM", "ADDR_NUM"]:
                        if candidate in addr_layer_fields:
                            parameters[2].value = candidate
                            break
                if not parameters[3].altered and not parameters[3].value:
                    for candidate in ["Full Roadname", "FULL_ROADNAME", "STREET_NAME", "ROAD_NAME", "STREET", "ST_NAME"]:
                        if candidate in addr_layer_fields:
                            parameters[3].value = candidate
                            break
                if not parameters[4].altered and not parameters[4].value:
                    for candidate in ["Apt Type", "APT_TYPE", "UNIT_TYPE", "SUB_TYPE"]:
                        if candidate in addr_layer_fields:
                            parameters[4].value = candidate
                            break
                if not parameters[5].altered and not parameters[5].value:
                    for candidate in ["Apt", "APT", "UNIT", "UNIT_NUM", "SUB_NUM", "APT_NUM"]:
                        if candidate in addr_layer_fields:
                            parameters[5].value = candidate
                            break
                if not parameters[6].altered and not parameters[6].value:
                    for candidate in ["City", "CITY", "MUNICIPALITY", "POST_COMMUNITY"]:
                        if candidate in addr_layer_fields:
                            parameters[6].value = candidate
                            break
                if not parameters[9].altered and not parameters[9].value:
                    for candidate in ["Zip Code", "ZIP_CODE", "ZIP", "POSTCODE", "POSTAL_CODE"]:
                        if candidate in addr_layer_fields:
                            parameters[9].value = candidate
                            break
                if not parameters[10].altered and not parameters[10].value:
                    for candidate in ["Full Address", "FULL_ADDRESS", "FULL_ADDR", "ADDRESS"]:
                        if candidate in addr_layer_fields:
                            parameters[10].value = candidate
                            break
            except Exception:
                pass

        if parameters[11].value:
            try:
                name_layer_fields = [f.name for f in arcpy.ListFields(parameters[11].valueAsText)]
                if not parameters[12].altered and not parameters[12].value:
                    for candidate in ["COMMON_NAME", "Common Name", "NAME", "STR_NAME", "BUILDING_NAME"]:
                        if candidate in name_layer_fields:
                            parameters[12].value = candidate
                            break
            except Exception:
                pass
        return

    def updateMessages(self, parameters):
        return

    def execute(self, parameters, messages):
        in_buildings = parameters[0].valueAsText
        in_address_points = parameters[1].valueAsText if parameters[1].value else None
        f_housenumber = parameters[2].valueAsText if parameters[2].value else None
        f_street = parameters[3].valueAsText if parameters[3].value else None
        f_unit_type = parameters[4].valueAsText if parameters[4].value else None
        f_unit_num = parameters[5].valueAsText if parameters[5].value else None
        f_city = parameters[6].valueAsText if parameters[6].value else None
        f_state = parameters[7].valueAsText if parameters[7].value else None
        default_state = parameters[8].valueAsText.strip() if parameters[8].value else "WV"
        f_postcode = parameters[9].valueAsText if parameters[9].value else None
        f_full_address = parameters[10].valueAsText if parameters[10].value else None

        in_name_points = parameters[11].valueAsText if parameters[11].value else None
        f_building_name = parameters[12].valueAsText if parameters[12].value else None

        out_points_layer = parameters[13].valueAsText if parameters[13].value else None
        multiunit_mode = parameters[14].valueAsText

        search_dist = float(parameters[15].value) if parameters[15].value else 0.0
        action_mode = parameters[16].valueAsText
        overwrite = bool(parameters[17].value)
        normalize_osm = bool(parameters[18].value) if len(parameters) > 18 and parameters[18].value is not None else True

        if not in_address_points and not in_name_points:
            arcpy.AddError("You must provide at least one point layer (Address Points or Name Points).")
            return

        arcpy.AddMessage("Checking building footprint schema and preparing fields...")
        b_fields = [f.name for f in arcpy.ListFields(in_buildings)]

        def add_b_field(fname, ftype, flen=None):
            if fname not in b_fields:
                try:
                    arcpy.management.AddField(in_buildings, fname, ftype, field_length=flen)
                    b_fields.append(fname)
                except Exception as e:
                    arcpy.AddWarning(f"Could not add field {fname}: {e}")

        add_b_field("name", "TEXT", 255)
        add_b_field("addr_housenumber", "TEXT", 50)
        add_b_field("addr_street", "TEXT", 150)
        add_b_field("addr_unit", "TEXT", 50)
        add_b_field("addr_city", "TEXT", 100)
        add_b_field("addr_state", "TEXT", 50)
        add_b_field("addr_postcode", "TEXT", 20)
        add_b_field("addr_full", "TEXT", 255)
        add_b_field("osm_tags", "TEXT", 2000)
        add_b_field("osm_action", "TEXT", 20)
        add_b_field("osm_id", "TEXT", 25)

        desc_b = arcpy.Describe(in_buildings)
        sr_buildings = desc_b.spatialReference
        gdb_path = get_workspace_gdb(in_buildings)

        # Resolve target OSM_Points path
        if not out_points_layer or out_points_layer == "OSM_Points":
            out_points_layer = os.path.join(gdb_path, "OSM_Points") if gdb_path else "OSM_Points"

        export_multiunit_points = multiunit_mode.startswith("Base Address on Building")

        if export_multiunit_points:
            if not arcpy.Exists(out_points_layer) and gdb_path:
                try:
                    arcpy.AddMessage(f"Creating '{os.path.basename(out_points_layer)}' feature class in {gdb_path}...")
                    arcpy.management.CreateFeatureclass(gdb_path, os.path.basename(out_points_layer), "POINT", spatial_reference=sr_buildings)
                except Exception as e:
                    arcpy.AddWarning(f"Notice: Could not auto-create point feature class: {e}")

            if arcpy.Exists(out_points_layer):
                pt_fields = [f.name for f in arcpy.ListFields(out_points_layer)]
                def add_pt_field(fname, ftype, flen=None):
                    if fname not in pt_fields:
                        try:
                            arcpy.management.AddField(out_points_layer, fname, ftype, field_length=flen)
                            pt_fields.append(fname)
                        except Exception:
                            pass
                add_pt_field("name", "TEXT", 255)
                add_pt_field("osm_type", "TEXT", 50)
                add_pt_field("osm_tags", "TEXT", 2000)
                add_pt_field("osm_action", "TEXT", 20)
                add_pt_field("osm_id", "TEXT", 25)
                add_pt_field("osm_version", "LONG")
                add_pt_field("addr_housenumber", "TEXT", 50)
                add_pt_field("addr_street", "TEXT", 150)
                add_pt_field("addr_unit", "TEXT", 50)
                add_pt_field("addr_city", "TEXT", 100)
                add_pt_field("addr_state", "TEXT", 50)
                add_pt_field("addr_postcode", "TEXT", 20)
                add_pt_field("addr_full", "TEXT", 255)

        def resolve_layer_field(layer, requested_name):
            if not requested_name or not layer:
                return None
            req_clean = requested_name.strip()
            if "." in req_clean:
                req_clean = req_clean.split(".")[-1]
            try:
                fields = arcpy.ListFields(layer)
                for f in fields:
                    if f.name == req_clean or f.name == requested_name:
                        return f.name
                for f in fields:
                    if f.name.lower() == req_clean.lower():
                        return f.name
                for f in fields:
                    if f.aliasName and (f.aliasName == req_clean or f.aliasName == requested_name):
                        return f.name
                for f in fields:
                    if f.aliasName and f.aliasName.lower() == req_clean.lower():
                        return f.name
                norm_req = req_clean.lower().replace(" ", "_")
                for f in fields:
                    if f.name.lower() == norm_req or (f.aliasName and f.aliasName.lower().replace(" ", "_") == norm_req):
                        return f.name
                    if f.name.lower().replace("_", " ") == req_clean.lower():
                        return f.name
            except Exception:
                pass
            return None

        # Determine cell size for spatial hash grid index
        is_geographic = getattr(sr_buildings, 'type', '') == "Geographic"
        grid_cell_size = 0.001 if is_geographic else 150.0 # ~110m or 150ft/m

        # 1. Load Address Points into Fast Spatial Hash Grid
        addr_grid = SpatialHashGrid(grid_cell_size)
        total_addr_loaded = 0
        if in_address_points:
            addr_cursor_fields = ["SHAPE@"]
            addr_field_map = {}
            for tag_key, fld_candidate in [("housenumber", f_housenumber), ("street", f_street),
                                           ("unit_type", f_unit_type), ("unit_num", f_unit_num),
                                           ("city", f_city), ("state", f_state),
                                           ("postcode", f_postcode), ("full", f_full_address)]:
                real_fld = resolve_layer_field(in_address_points, fld_candidate)
                if real_fld:
                    addr_field_map[tag_key] = real_fld
                    addr_cursor_fields.append(real_fld)

            real_osm_id_fld = resolve_layer_field(in_address_points, "osm_id")
            if real_osm_id_fld:
                addr_field_map["osm_id"] = real_osm_id_fld
                addr_cursor_fields.append(real_osm_id_fld)

            real_osm_ver_fld = resolve_layer_field(in_address_points, "osm_version")
            if real_osm_ver_fld:
                addr_field_map["osm_version"] = real_osm_ver_fld
                addr_cursor_fields.append(real_osm_ver_fld)

            arcpy.AddMessage(f"Mapped address fields: {addr_field_map}")
            arcpy.AddMessage("Fast spatial filtering address points to building footprints...")

            temp_addr_fl = f"in_memory_addr_fl_{int(time.time()*1000)%100000}"
            try:
                arcpy.management.MakeFeatureLayer(in_address_points, temp_addr_fl)
                dist_str = f"{max(50.0, search_dist * 3.28 + 20.0)} Feet"
                arcpy.management.SelectLayerByLocation(
                    in_layer=temp_addr_fl,
                    overlap_type="WITHIN_A_DISTANCE",
                    select_features=in_buildings,
                    search_distance=dist_str,
                    selection_type="NEW_SELECTION"
                )
                with arcpy.da.SearchCursor(temp_addr_fl, addr_cursor_fields, spatial_reference=sr_buildings) as cur:
                    for row in cur:
                        pt_geom = row[0]
                        if not pt_geom: continue
                        data = {}
                        for idx, tag_key in enumerate(addr_field_map.keys()):
                            val = row[1 + idx]
                            if val is not None and str(val).strip() != "":
                                data[tag_key] = str(val).strip()
                        if data:
                            addr_grid.insert(pt_geom, data)
                            total_addr_loaded += 1
            except Exception as e:
                arcpy.AddWarning(f"Spatial index query notice: {e}")
            finally:
                if arcpy.Exists(temp_addr_fl):
                    try: arcpy.management.Delete(temp_addr_fl)
                    except: pass

            arcpy.AddMessage(f"-> Indexed {total_addr_loaded} address point(s) into memory spatial hash grid.")

        # 2. Load Common Name Points into Fast Spatial Hash Grid
        name_grid = SpatialHashGrid(grid_cell_size)
        total_names_loaded = 0
        real_name_fld = resolve_layer_field(in_name_points, f_building_name) if in_name_points and f_building_name else None
        if in_name_points and real_name_fld:
            arcpy.AddMessage(f"Mapped common name field: {real_name_fld}")
            arcpy.AddMessage("Fast spatial filtering common name points to building footprints...")

            temp_name_fl = f"in_memory_name_fl_{int(time.time()*1000)%100000}"
            try:
                arcpy.management.MakeFeatureLayer(in_name_points, temp_name_fl)
                dist_str = f"{max(50.0, search_dist * 3.28 + 20.0)} Feet"
                arcpy.management.SelectLayerByLocation(
                    in_layer=temp_name_fl,
                    overlap_type="WITHIN_A_DISTANCE",
                    select_features=in_buildings,
                    search_distance=dist_str,
                    selection_type="NEW_SELECTION"
                )
                with arcpy.da.SearchCursor(temp_name_fl, ["SHAPE@", real_name_fld], spatial_reference=sr_buildings) as cur:
                    for row in cur:
                        pt_geom = row[0]
                        val = row[1]
                        if pt_geom and val is not None and str(val).strip() != "":
                            name_grid.insert(pt_geom, str(val).strip())
                            total_names_loaded += 1
            except Exception as e:
                arcpy.AddWarning(f"Spatial index query notice: {e}")
            finally:
                if arcpy.Exists(temp_name_fl):
                    try: arcpy.management.Delete(temp_name_fl)
                    except: pass

            arcpy.AddMessage(f"-> Indexed {total_names_loaded} common name point(s) into memory spatial hash grid.")

        # 3. Iterate Buildings & Match Using High-Performance Spatial Grid
        arcpy.AddMessage("Matching footprints to address & name points via Spatial Hash Grid...")
        update_fields = [
            "SHAPE@", "osm_id", "osm_tags", "osm_action", "name",
            "addr_housenumber", "addr_street", "addr_unit", "addr_city",
            "addr_state", "addr_postcode", "addr_full"
        ]

        matched_buildings = 0
        total_buildings = 0
        multiunit_points_to_insert = []

        def format_single_unit(data_item):
            utype = data_item.get("unit_type", "").strip()
            unum = data_item.get("unit_num", "").strip()
            if utype and unum:
                return f"{utype} {unum}"
            return unum if unum else utype

        building_count = int(arcpy.management.GetCount(in_buildings).getOutput(0)) if arcpy.Exists(in_buildings) else 0
        if building_count > 0:
            arcpy.SetProgressor("step", "Matching building footprints...", 0, building_count, max(1, building_count // 50))

        with arcpy.da.UpdateCursor(in_buildings, update_fields, spatial_reference=sr_buildings) as cur:
            for row in cur:
                total_buildings += 1
                if total_buildings % 500 == 0 and building_count > 0:
                    arcpy.SetProgressorPosition(total_buildings)

                poly_geom = row[0]
                if not poly_geom:
                    continue

                poly_ext = poly_geom.extent
                buf = search_dist if search_dist > 0.0 else 0.0

                # Query Spatial Hash Grid for points strictly inside envelope + search buffer
                candidate_addrs = addr_grid.query_envelope(
                    poly_ext.XMin - buf, poly_ext.YMin - buf,
                    poly_ext.XMax + buf, poly_ext.YMax + buf
                ) if total_addr_loaded > 0 else []

                candidate_names = name_grid.query_envelope(
                    poly_ext.XMin - buf, poly_ext.YMin - buf,
                    poly_ext.XMax + buf, poly_ext.YMax + buf
                ) if total_names_loaded > 0 else []

                matched_addr_list = []
                matched_name_list = []

                for pt_geom, data in candidate_addrs:
                    if poly_geom.contains(pt_geom) or poly_geom.touches(pt_geom):
                        matched_addr_list.append((pt_geom, data))
                    elif search_dist > 0.0 and poly_geom.distanceTo(pt_geom) <= search_dist:
                        matched_addr_list.append((pt_geom, data))

                for pt_geom, name_str in candidate_names:
                    if poly_geom.contains(pt_geom) or poly_geom.touches(pt_geom):
                        matched_name_list.append((pt_geom, name_str))
                    elif search_dist > 0.0 and poly_geom.distanceTo(pt_geom) <= search_dist:
                        matched_name_list.append((pt_geom, name_str))

                if not matched_addr_list and not matched_name_list:
                    continue

                matched_buildings += 1
                o_id = row[1]
                o_tags_str = row[2]

                try:
                    tags_dict = json.loads(o_tags_str) if o_tags_str else {}
                except Exception:
                    tags_dict = {}

                if matched_addr_list:
                    is_multi_unit = len(matched_addr_list) > 1
                    primary_geom, primary_addr = matched_addr_list[0]
                    hn = primary_addr.get("housenumber", "").strip()
                    st = primary_addr.get("street", "").strip()
                    ct = primary_addr.get("city", "").strip()
                    state_raw = primary_addr.get("state", "").strip() or default_state.strip()
                    pc = primary_addr.get("postcode", "").strip()
                    full = primary_addr.get("full", "").strip()

                    if is_multi_unit and multiunit_mode.startswith("Base Address on Building"):
                        un = ""
                        if export_multiunit_points:
                            for pt_geom, addr_item in matched_addr_list:
                                pt_hn = addr_item.get("housenumber", "").strip()
                                pt_st = addr_item.get("street", "").strip()
                                pt_ct = addr_item.get("city", "").strip()
                                pt_state_raw = addr_item.get("state", "").strip() or default_state.strip()
                                pt_pc = addr_item.get("postcode", "").strip()
                                pt_full = addr_item.get("full", "").strip()
                                pt_un = format_single_unit(addr_item)

                                suite_name = ""
                                if matched_name_list:
                                    min_d = 1.0
                                    for n_geom, n_str in matched_name_list:
                                        d = pt_geom.distanceTo(n_geom)
                                        if d <= min_d:
                                            min_d = d
                                            suite_name = n_str

                                if normalize_osm:
                                    if pt_st: pt_st = normalize_osm_street(pt_st)
                                    if pt_un: pt_un = normalize_osm_unit(pt_un)
                                    if pt_ct: pt_ct = normalize_osm_city(pt_ct)
                                    if suite_name: suite_name = normalize_osm_name(suite_name)
                                    pt_state = pt_state_raw.upper() if len(pt_state_raw) == 2 else normalize_osm_name(pt_state_raw)
                                    if pt_full: pt_full = normalize_osm_street(pt_full)
                                else:
                                    pt_state = pt_state_raw

                                pt_node_tags = {}
                                if suite_name: pt_node_tags["name"] = suite_name
                                if pt_hn: pt_node_tags["addr:housenumber"] = pt_hn
                                if pt_st: pt_node_tags["addr:street"] = pt_st
                                if pt_un: pt_node_tags["addr:unit"] = pt_un
                                if pt_ct: pt_node_tags["addr:city"] = pt_ct
                                if pt_state: pt_node_tags["addr:state"] = pt_state
                                if pt_pc: pt_node_tags["addr:postcode"] = pt_pc
                                if pt_full: pt_node_tags["addr:full"] = pt_full

                                pt_osm_id = addr_item.get("osm_id", "").strip()
                                pt_osm_ver_raw = addr_item.get("osm_version", 1)
                                try: pt_osm_ver = int(pt_osm_ver_raw)
                                except: pt_osm_ver = 1

                                multiunit_points_to_insert.append([
                                    pt_geom, pt_osm_id, suite_name[:254], "node", json.dumps(pt_node_tags), pt_osm_ver, "create",
                                    pt_hn[:49], pt_st[:149], pt_un[:49], pt_ct[:99], pt_state[:49], pt_pc[:19], pt_full[:254]
                                ])
                    elif is_multi_unit and multiunit_mode.startswith("Combine all units"):
                        all_units = [format_single_unit(a[1]) for a in matched_addr_list if format_single_unit(a[1])]
                        un = ", ".join(dict.fromkeys(all_units)) if all_units else format_single_unit(primary_addr)
                    else:
                        un = format_single_unit(primary_addr)

                    if normalize_osm:
                        if st: st = normalize_osm_street(st)
                        if un: un = normalize_osm_unit(un)
                        if ct: ct = normalize_osm_city(ct)
                        state = state_raw.upper() if len(state_raw) == 2 else normalize_osm_name(state_raw)
                        if full: full = normalize_osm_street(full)
                    else:
                        state = state_raw

                    if overwrite or not row[5]: row[5] = hn[:49]
                    if overwrite or not row[6]: row[6] = st[:149]
                    if overwrite or not row[7]: row[7] = un[:49]
                    if overwrite or not row[8]: row[8] = ct[:99]
                    if overwrite or not row[9]: row[9] = state[:49]
                    if overwrite or not row[10]: row[10] = pc[:19]
                    if overwrite or not row[11]: row[11] = full[:254]

                    if hn: tags_dict["addr:housenumber"] = hn
                    if st: tags_dict["addr:street"] = st
                    if un: tags_dict["addr:unit"] = un
                    elif "addr:unit" in tags_dict and is_multi_unit: del tags_dict["addr:unit"]
                    if ct: tags_dict["addr:city"] = ct
                    if state: tags_dict["addr:state"] = state
                    if pc: tags_dict["addr:postcode"] = pc
                    if full: tags_dict["addr:full"] = full

                if matched_name_list:
                    primary_name = matched_name_list[0][1]
                    b_name = normalize_osm_name(primary_name) if normalize_osm else primary_name
                    if overwrite or not row[4]: row[4] = b_name[:254]
                    tags_dict["name"] = b_name

                if "building" not in tags_dict:
                    tags_dict["building"] = "yes"

                row[2] = json.dumps(tags_dict)

                if action_mode.startswith("Mark for Upload"):
                    if o_id and str(o_id).strip() != "":
                        row[3] = "modify"
                    else:
                        row[3] = "create"
                elif action_mode == "Set to 'modify'":
                    row[3] = "modify"
                elif action_mode == "Set to 'create'":
                    row[3] = "create"

                cur.updateRow(row)

        if building_count > 0:
            arcpy.ResetProgressor()

        # 4. Insert / Update Multi-Unit Address Points in OSM_Points with O(1) & Spatial Hash Deduplication
        if multiunit_points_to_insert and export_multiunit_points and arcpy.Exists(out_points_layer):
            arcpy.AddMessage(f"Processing {len(multiunit_points_to_insert)} address node(s) for '{out_points_layer}' with deduplication check...")

            existing_pt_map = {} # osm_id -> OID
            existing_addr_map = {} # (hn_l, st_l, un_l) -> OID
            existing_spatial_grid = SpatialHashGrid(grid_cell_size)

            read_fields = ["OID@", "SHAPE@", "osm_id", "addr_housenumber", "addr_street", "addr_unit"]
            try:
                with arcpy.da.SearchCursor(out_points_layer, read_fields, spatial_reference=sr_buildings) as s_cur:
                    for s_row in s_cur:
                        oid, shape_g, o_id, o_hn, o_st, o_un = s_row
                        if o_id and str(o_id).strip():
                            existing_pt_map[str(o_id).strip()] = oid
                        hn_l = (o_hn or "").strip().lower()
                        st_l = (o_st or "").strip().lower()
                        un_l = (o_un or "").strip().lower()
                        if hn_l and st_l:
                            existing_addr_map[(hn_l, st_l, un_l)] = oid
                        if shape_g:
                            existing_spatial_grid.insert(shape_g, (oid, hn_l, st_l, un_l))
            except Exception as e:
                arcpy.AddWarning(f"Notice during point deduplication index build: {e}")

            points_to_insert = []
            points_to_update = {} # OID -> pt_row_data

            for pt_row in multiunit_points_to_insert:
                p_geom, p_osmid, p_name, p_type, p_tags, p_ver, p_action, p_hn, p_st, p_un, p_ct, p_state, p_pc, p_full = pt_row

                matched_oid = None
                # 1. Match by osm_id
                if p_osmid and str(p_osmid).strip() in existing_pt_map:
                    matched_oid = existing_pt_map[str(p_osmid).strip()]

                p_hn_l = (p_hn or "").strip().lower()
                p_st_l = (p_st or "").strip().lower()
                p_un_l = (p_un or "").strip().lower()

                # 2. Match by exact normalized address key
                if not matched_oid and p_hn_l and p_st_l:
                    addr_key = (p_hn_l, p_st_l, p_un_l)
                    if addr_key in existing_addr_map:
                        matched_oid = existing_addr_map[addr_key]

                # 3. Match spatially within 2 meters if address matches
                if not matched_oid and p_geom:
                    p_pt = p_geom.firstPoint if hasattr(p_geom, 'firstPoint') else p_geom
                    spatial_candidates = existing_spatial_grid.query_envelope(
                        p_pt.X - 5.0, p_pt.Y - 5.0, p_pt.X + 5.0, p_pt.Y + 5.0
                    )
                    for ex_geom, (ex_oid, ex_hn, ex_st, ex_un) in spatial_candidates:
                        if p_geom.distanceTo(ex_geom) <= 2.0:
                            if p_hn_l == ex_hn and p_st_l == ex_st and (not p_un_l or p_un_l == ex_un):
                                matched_oid = ex_oid
                                break

                if matched_oid:
                    points_to_update[matched_oid] = pt_row
                else:
                    points_to_insert.append(pt_row)
                    if p_hn_l and p_st_l:
                        existing_addr_map[(p_hn_l, p_st_l, p_un_l)] = -1
                    if p_geom:
                        existing_spatial_grid.insert(p_geom, (-1, p_hn_l, p_st_l, p_un_l))

            # Perform Updates on Existing Points
            if points_to_update:
                arcpy.AddMessage(f"Updating {len(points_to_update)} existing address point(s) in '{out_points_layer}' (preventing duplicates)...")
                update_fields_pt = ["OID@", "SHAPE@", "osm_id", "name", "osm_type", "osm_tags", "osm_version", "osm_action", "addr_housenumber", "addr_street", "addr_unit", "addr_city", "addr_state", "addr_postcode", "addr_full"]
                with arcpy.da.UpdateCursor(out_points_layer, update_fields_pt, spatial_reference=sr_buildings) as u_cur:
                    for u_row in u_cur:
                        cur_oid = u_row[0]
                        if cur_oid in points_to_update:
                            up_data = points_to_update[cur_oid]
                            if up_data[1]: u_row[2] = up_data[1] # osm_id
                            u_row[3] = up_data[2] # name
                            u_row[4] = up_data[3] # osm_type
                            u_row[5] = up_data[4] # osm_tags
                            if not u_row[6] or u_row[6] <= 1:
                                if up_data[5] and up_data[5] > 1:
                                    u_row[6] = up_data[5]
                            u_row[7] = "modify" if u_row[2] else "create" # osm_action
                            u_row[8] = up_data[7] # addr_housenumber
                            u_row[9] = up_data[8] # addr_street
                            u_row[10] = up_data[9] # addr_unit
                            u_row[11] = up_data[10] # addr_city
                            u_row[12] = up_data[11] # addr_state
                            u_row[13] = up_data[12] # addr_postcode
                            u_row[14] = up_data[13] # addr_full
                            u_cur.updateRow(u_row)

            # Perform Inserts for New Points
            if points_to_insert:
                arcpy.AddMessage(f"Writing {len(points_to_insert)} new address node(s) into '{out_points_layer}'...")
                pt_insert_fields = [
                    "SHAPE@", "osm_id", "name", "osm_type", "osm_tags", "osm_version", "osm_action",
                    "addr_housenumber", "addr_street", "addr_unit", "addr_city", "addr_state",
                    "addr_postcode", "addr_full"
                ]
                with arcpy.da.InsertCursor(out_points_layer, pt_insert_fields) as pt_cur:
                    for pt_row in points_to_insert:
                        pt_cur.insertRow(pt_row)

            arcpy.AddMessage(f"-> Successfully processed address node(s) in '{out_points_layer}' ({len(points_to_update)} updated, {len(points_to_insert)} created).")

            try:
                aprx = arcpy.mp.ArcGISProject("CURRENT")
                active_map = aprx.activeMap
                if active_map:
                    existing_layers = [lyr.name for lyr in active_map.listLayers()]
                    pt_layer_name = os.path.basename(out_points_layer)
                    if pt_layer_name not in existing_layers and "OSM_Points" not in existing_layers:
                        active_map.addDataFromPath(out_points_layer)
                        arcpy.AddMessage(f"Added '{pt_layer_name}' to the active map.")
                        try:
                            arcpy.RefreshActiveView()
                        except Exception:
                            pass
            except Exception:
                pass

        arcpy.AddMessage(f"Spatial join complete! Enriched {matched_buildings} of {total_buildings} building footprint(s).")
        arcpy.AddMessage("Address and name tags are now updated in the table and packaged in 'osm_tags' ready for upload.")
        return
