import json
from html import escape
import subprocess
import sys
import os

import queue

import re

import threading

import time

from pathlib import Path

from typing import Any, Optional



import streamlit as st


from playwright.sync_api import sync_playwright

from groq import Groq

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    KeepTogether,
)
from reportlab.graphics.shapes import Drawing, Rect, String, Line






# ============================================================

# CONFIGURATION

# ============================================================






BASE_DIR = Path(__file__).resolve().parent

MAPPING_JSON_FILE = BASE_DIR / "informatica_mapping.json"



INFORMATICA_URL = os.getenv(

    "INFORMATICA_URL",

    "https://usw1.dm2-us.informaticacloud.com/diUI/products/integrationDesign/main",

)



# ============================================================
# GROQ CONFIGURATION
# ============================================================
# Paste your NEW Groq API key directly between the quotes below.
# Keep the key only in this file, as requested.
GROQ_API_KEY = st.secrets.get("GROQ_API_KEY", "")
GROQ_MODEL = "openai/gpt-oss-20b"



st.set_page_config(

    page_title="Informatica Mapping Intelligence",

    page_icon="🔎",

    layout="wide",

)


def ensure_playwright_browser():
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"Failed to install Playwright Chromium: {e}"
        )


# ============================================================

# SMALL COMMAND OBJECT FOR THE PLAYWRIGHT WORKER

# ============================================================



class BrowserCommand:

    def __init__(self, method_name: str, *args, **kwargs):

        self.method_name = method_name

        self.args = args

        self.kwargs = kwargs

        self.result = None

        self.error = None

        self.done = threading.Event()





# ============================================================

# PLAYWRIGHT / INFORMATICA WORKER

# ============================================================



class InformaticaBrowser:

    """

    All synchronous Playwright calls happen inside one dedicated worker

    thread. This avoids Streamlit rerun/thread conflicts.

    """



    def __init__(self):

        self.commands = queue.Queue()

        self.stop_event = threading.Event()

        self.ready_event = threading.Event()

        self.startup_error = None



        self.worker = threading.Thread(

            target=self._worker_main,

            name="informatica-playwright-worker",

            daemon=True,

        )

        self.worker.start()



        if not self.ready_event.wait(timeout=30):

            raise RuntimeError("Playwright worker did not start within 30 seconds.")



        if self.startup_error:

            raise RuntimeError(f"Playwright startup failed: {self.startup_error}")



    # --------------------------------------------------------

    # Worker thread

    # --------------------------------------------------------



    def _worker_main(self):

        playwright = None

        browser = None

        context = None



        try:

            # Streamlit Cloud does not have Playwright's browser binary
            # preinstalled. Install the matching Chromium before startup.
            ensure_playwright_browser()

            playwright = sync_playwright().start()

            browser = playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )



            context = browser.new_context(

                viewport=None,

            )



            page = context.new_page()

            page.set_default_timeout(10000)



            self.state = {

                "playwright": playwright,

                "browser": browser,

                "context": context,

                "page": page,

                "base_entities": [],

                "mapping_definition": None,

                "mapping_response_url": None,

                "current_area": "home",

                "status": "Browser started",

                "last_error": "",

            }



            context.on("response", self._capture_response)

            context.on("page", self._handle_new_page)



            self.ready_event.set()



            while not self.stop_event.is_set():

                try:

                    command = self.commands.get(timeout=0.25)

                except queue.Empty:

                    continue



                if command.method_name == "__stop__":

                    break



                try:

                    method = getattr(self, command.method_name)

                    command.result = method(*command.args, **command.kwargs)

                except Exception as exc:

                    command.error = exc

                    self.state["last_error"] = str(exc)

                finally:

                    command.done.set()



        except Exception as exc:

            self.startup_error = exc

            self.ready_event.set()



        finally:

            try:

                if context:

                    context.close()

            except Exception:

                pass



            try:

                if browser:

                    browser.close()

            except Exception:

                pass



            try:

                if playwright:

                    playwright.stop()

            except Exception:

                pass



    # --------------------------------------------------------

    # New popup/page handler

    # --------------------------------------------------------



    def _handle_new_page(self, new_page):

        try:

            new_page.set_default_timeout(10000)

            new_page.on("response", self._capture_response)

            self.state["page"] = new_page

        except Exception:

            pass



    # --------------------------------------------------------

    # Network response capture

    # --------------------------------------------------------



    def _capture_response(self, response):

        try:

            url = response.url



            # Explorer data

            if "BaseEntities" in url and response.status == 200:

                try:

                    data = response.json()

                except Exception:

                    return



                if isinstance(data, dict) and isinstance(data.get("value"), list):

                    self.state["base_entities"].append(data)

                    self.state["status"] = "Explorer data captured"

                return



            # Mapping designer metadata

            if "stringIdentity" in url and response.status == 200:

                try:

                    data = response.json()

                except Exception:

                    return



                if isinstance(data, str):

                    try:

                        data = json.loads(data)

                    except Exception:

                        return



                if self._is_mapping_json(data):

                    self.state["mapping_definition"] = data

                    self.state["mapping_response_url"] = url

                    self.state["status"] = "Mapping metadata captured"



                    try:

                        MAPPING_JSON_FILE.write_text(

                            json.dumps(data, indent=2, ensure_ascii=False),

                            encoding="utf-8",

                        )

                    except Exception:

                        pass



        except Exception:

            # Network handlers should never break the browser.

            pass



    @staticmethod

    def _is_mapping_json(data):

        return isinstance(data, dict) and (

            data.get("documentType") == "MAPPING"

            or (

                "transformations" in data

                and "links" in data

                and "name" in data

            )

        )



    # --------------------------------------------------------

    # Page/context helpers

    # --------------------------------------------------------



    def _ensure_page(self):

        browser = self.state["browser"]

        context = self.state["context"]



        if not browser.is_connected():

            raise RuntimeError("Chromium is disconnected. Click Open / Connect again.")



        if context.is_closed():

            raise RuntimeError("Browser context is closed. Click Open / Connect again.")



        page = self.state["page"]



        if page.is_closed():

            page = context.new_page()

            page.set_default_timeout(10000)

            page.on("response", self._capture_response)

            self.state["page"] = page



        return page



    def _all_frames(self, page):

        try:

            return page.frames

        except Exception:

            return [page]



    def _body_text(self, page, limit=8000):

        try:

            return page.locator("body").inner_text(timeout=3000)[:limit]

        except Exception:

            return ""



    # --------------------------------------------------------

    # Locate Explore

    # --------------------------------------------------------



    def _find_explore_locator(self, page):

        """

        Try several user-facing selectors. Playwright recommends role/text

        locators because they are less brittle than generated CSS selectors.

        """

        patterns = [

            re.compile(r"^Explore$", re.IGNORECASE),

            re.compile(r"Explore", re.IGNORECASE),

        ]



        for frame in self._all_frames(page):

            for pattern in patterns:

                candidates = [

                    frame.get_by_role("button", name=pattern),

                    frame.get_by_role("link", name=pattern),

                    frame.get_by_text(pattern),

                ]



                for locator in candidates:

                    try:

                        if locator.count() > 0:

                            first = locator.first

                            if first.is_visible(timeout=500):

                                return first

                    except Exception:

                        continue



        return None



    # --------------------------------------------------------

    # Open Informatica

    # --------------------------------------------------------



    def _open_informatica(self):

        page = self._ensure_page()



        self.state["base_entities"] = []

        self.state["mapping_definition"] = None

        self.state["mapping_response_url"] = None

        self.state["current_area"] = "home"

        self.state["status"] = "Opening Informatica..."



        page.goto(

            INFORMATICA_URL,

            wait_until="commit",

            timeout=120000,

        )



        page.wait_for_timeout(2000)



        self.state["status"] = "Informatica opened. Complete login if required."



        return {

            "url": page.url,

            "status": self.state["status"],

        }



    # --------------------------------------------------------

    # Click Explore

    # --------------------------------------------------------



    def _click_explore(self, wait_seconds=45):

        page = self._ensure_page()

        deadline = time.time() + wait_seconds



        self.state["status"] = "Waiting for the Informatica Explore option..."



        while time.time() < deadline:

            if page.is_closed():

                raise RuntimeError("The Informatica browser page was closed.")



            locator = self._find_explore_locator(page)



            if locator is not None:

                try:

                    self.state["status"] = "Opening Explore..."

                    locator.click(timeout=10000)

                    page.wait_for_timeout(2000)

                    self.state["current_area"] = "explore"

                    self.state["status"] = "Explore opened. Reading projects and folders..."

                    return {

                        "success": True,

                        "url": page.url,

                        "status": self.state["status"],

                    }

                except Exception:

                    # Try again while the SPA is rendering.

                    pass



            page.wait_for_timeout(500)



        text = self._body_text(page)

        self.state["status"] = "Could not find Explore."



        return {

            "success": False,

            "url": page.url,

            "status": self.state["status"],

            "visible_text": text,

        }



    # --------------------------------------------------------

    # Discover Project -> Folder -> Mapping

    # --------------------------------------------------------



    def _discover_mappings(self):

        page = self._ensure_page()



        self.state["base_entities"] = []

        self.state["status"] = "Starting automatic discovery..."



        # If the browser is not currently in Explorer, click Explore.

        if self.state["current_area"] != "explore":

            result = self._click_explore(wait_seconds=45)

            if not result.get("success"):

                return {

                    "mappings": [],

                    "error": result.get("status", "Could not open Explore."),

                    "url": result.get("url", page.url),

                    "visible_text": result.get("visible_text", ""),

                }



        # BaseEntities normally arrives after Explorer renders.

        self.state["status"] = "Waiting for Explorer data..."

        deadline = time.time() + 30



        while time.time() < deadline:

            if self.state["base_entities"]:

                break

            page.wait_for_timeout(500)



        # If the response did not arrive, reload the current Explorer page once.

        if not self.state["base_entities"]:

            self.state["status"] = "Refreshing Explorer to capture its data..."

            try:

                page.reload(wait_until="commit", timeout=120000)

                page.wait_for_timeout(2000)

            except Exception:

                pass



            deadline = time.time() + 20

            while time.time() < deadline:

                if self.state["base_entities"]:

                    break

                page.wait_for_timeout(500)



        mappings = {}



        for payload in self.state["base_entities"]:

            for item in payload.get("value", []):

                if item.get("documentType") != "MAPPING":

                    continue



                parents = item.get("parentInfo") or []



                project = next(

                    (

                        p.get("parentName", "")

                        for p in parents

                        if p.get("parentType") == "Project"

                    ),

                    "",

                )



                folder = next(

                    (

                        p.get("parentName", "")

                        for p in parents

                        if p.get("parentType") == "Folder"

                    ),

                    "",

                )



                mapping_id = item.get("id")

                if not mapping_id:

                    continue



                mappings[mapping_id] = {

                    "id": mapping_id,

                    "name": item.get("name", ""),

                    "description": item.get("description", ""),

                    "project": project,

                    "folder": folder,

                    "state": item.get("documentState", ""),

                }



        result = sorted(

            mappings.values(),

            key=lambda x: (x["project"], x["folder"], x["name"]),

        )



        self.state["status"] = f"Found {len(result)} mappings."



        return {

            "mappings": result,

            "base_entity_responses": len(self.state["base_entities"]),

            "status": self.state["status"],

            "url": page.url,

        }



    # --------------------------------------------------------

    # Open a selected mapping

    # --------------------------------------------------------



    def _find_mapping_locator(self, page, mapping_name):

        pattern = re.compile(rf"^{re.escape(mapping_name)}$", re.IGNORECASE)



        for frame in self._all_frames(page):

            candidates = [

                frame.get_by_text(pattern),

                frame.get_by_role("link", name=pattern),

                frame.get_by_role("button", name=pattern),

            ]



            for locator in candidates:

                try:

                    if locator.count() > 0:

                        first = locator.first

                        if first.is_visible(timeout=500):

                            return first

                except Exception:

                    continue



        return None



    def _wait_for_mapping_metadata(self, page, seconds=30):

        deadline = time.time() + seconds

        while time.time() < deadline:

            if self.state["mapping_definition"] is not None:

                return True

            page.wait_for_timeout(500)

        return False



    def _open_mapping_from_explore(self, mapping_name, mapping_id):

        page = self._ensure_page()



        self.state["mapping_definition"] = None

        self.state["mapping_response_url"] = None

        self.state["status"] = f"Opening mapping: {mapping_name}"



        # First attempt: open it exactly as a user would from Explorer.

        locator = self._find_mapping_locator(page, mapping_name)



        if locator is not None:

            try:

                locator.dblclick(timeout=10000)

            except Exception:

                try:

                    locator.click(timeout=10000)

                except Exception:

                    pass



            if self._wait_for_mapping_metadata(page, seconds=30):

                self.state["current_area"] = "mapping"

                return {

                    "mapping": self.state["mapping_definition"],

                    "response_url": self.state["mapping_response_url"],

                    "page_url": page.url,

                    "opened_by": "explore_ui",

                }



        # Second attempt: if UI click did not open the designer, try the known

        # mapping route after Explorer has already established application state.

        self.state["status"] = "Trying the mapping route after Explorer navigation..."



        mapping_url = (

            "https://usw1.dm2-us.informaticacloud.com/"

            "diUI/products/integrationDesign/main/"

            f"mapping/{mapping_id}/edit"

        )



        try:

            page.goto(mapping_url, wait_until="commit", timeout=120000)

            page.wait_for_timeout(2500)

        except Exception:

            pass



        if self._wait_for_mapping_metadata(page, seconds=30):

            self.state["current_area"] = "mapping"

            return {

                "mapping": self.state["mapping_definition"],

                "response_url": self.state["mapping_response_url"],

                "page_url": page.url,

                "opened_by": "direct_route_after_explore",

            }



        return {

            "mapping": None,

            "response_url": None,

            "page_url": page.url,

            "opened_by": "failed",

            "status": self.state["status"],

        }



    # --------------------------------------------------------

    # Browser status

    # --------------------------------------------------------



    def _status(self):

        page = self._ensure_page()



        return {

            "browser_connected": self.state["browser"].is_connected(),

            "context_closed": self.state["context"].is_closed(),

            "page_closed": page.is_closed(),

            "page_url": page.url,

            "current_area": self.state["current_area"],

            "status": self.state["status"],

            "base_entity_responses": len(self.state["base_entities"]),

            "mapping_captured": self.state["mapping_definition"] is not None,

            "last_error": self.state.get("last_error", ""),

        }



    # --------------------------------------------------------

    # Public worker-call interface

    # --------------------------------------------------------



    def call(self, method_name, *args, timeout=180, **kwargs):

        if self.startup_error:

            raise RuntimeError(f"Playwright failed: {self.startup_error}")



        command = BrowserCommand(method_name, *args, **kwargs)

        self.commands.put(command)



        if not command.done.wait(timeout=timeout):

            raise TimeoutError(f"Operation '{method_name}' timed out.")



        if command.error:

            raise command.error



        return command.result



    def open_informatica(self):

        return self.call("_open_informatica", timeout=180)



    def discover_mappings(self):

        return self.call("_discover_mappings", timeout=180)



    def open_mapping_from_explore(self, mapping_name, mapping_id):

        return self.call(

            "_open_mapping_from_explore",

            mapping_name,

            mapping_id,

            timeout=180,

        )



    def status(self):

        return self.call("_status", timeout=30)



    def stop(self):

        self.stop_event.set()

        try:

            self.commands.put_nowait(BrowserCommand("__stop__"))

        except Exception:

            pass





@st.cache_resource(show_spinner=False)

def get_browser():

    return InformaticaBrowser()





# ============================================================


# ============================================================
# MAPPING NORMALIZATION
# ============================================================

TRANSFORMATION_TYPES = {
    270: "Source",
    16: "Target",
    63: "Filter",
    17: "Field Mapping",
    18: "Field Mappings",
    292: "Link",
}


def walk(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from walk(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from walk(value)


def object_summary(transformation):
    """Extract a clean source/target object summary without inventing metadata."""
    adapter = transformation.get("dataAdapter") or {}
    obj = adapter.get("object") or {}

    if not isinstance(obj, dict):
        return None

    fields = []
    for field in obj.get("fields", []) or []:
        if isinstance(field, dict):
            fields.append({
                "name": field.get("name") or field.get("nativeName") or "",
                "nativeName": field.get("nativeName", ""),
                "type": field.get("nativeType", ""),
                "javaType": field.get("javaType", ""),
            })

    return {
        "name": obj.get("name") or obj.get("objectName") or obj.get("label", ""),
        "path": obj.get("path", ""),
        "objectType": obj.get("objectType", ""),
        "schema": obj.get("dbSchema", ""),
        "connection_id": adapter.get("connectionId", ""),
        "connection_name": (
            adapter.get("connectionName")
            or adapter.get("connection", "")
            or ""
        ),
        "fields": fields,
    }


def _compact_logic_value(value, max_length=1200):
    """Turn captured Informatica logic into short human-readable text."""
    if value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
    elif isinstance(value, (int, float, bool)):
        text = str(value)
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
        except Exception:
            text = str(value)
    text = re.sub(r"\\s+", " ", text).strip()
    if len(text) > max_length:
        text = text[: max_length - 3] + "..."
    return text


def infer_transformation_type(transformation, class_type="Transformation"):
    """Prefer captured metadata; use common Informatica naming only as a fallback."""
    for key in ("transformationType", "type", "className", "transformationName"):
        value = transformation.get(key)
        if isinstance(value, str) and value.strip() and value.strip() != "Transformation":
            return value.strip()

    name = str(transformation.get("name", "")).upper()
    prefixes = [
        ("LKP_", "Lookup"),
        ("EXP_", "Expression"),
        ("RTR_", "Router"),
        ("JNR_", "Joiner"),
        ("SEQ_", "Sequence Generator"),
        ("SRT_", "Sorter"),
        ("RNK_", "Rank"),
        ("AGG_", "Aggregator"),
        ("FIL_", "Filter"),
        ("UPD_", "Update Strategy"),
        ("SQ_", "Source Qualifier"),
    ]
    for prefix, label in prefixes:
        if name.startswith(prefix):
            return label

    return class_type


def extract_transformation_logic(transformation, transformation_type):
    """Extract the actual configured logic, including field-to-field conditions.

    Informatica stores transformation settings differently by transformation
    type. In particular, Lookup and Joiner conditions are often represented as
    small objects such as {field, operator, incomingField}. We preserve those
    relationships instead of reducing them to just ``operator: =``.
    """
    candidates = {
        "expression", "expressionvalue", "expressionstring", "formula",
        "condition", "joincondition", "lookupcondition", "rankcondition",
        "routercondition", "filtercondition", "advancedfiltercondition",
        "sortfields", "sortfield", "groupby", "groupbyfields",
        "aggregateexpression", "aggregatefunction", "function",
        "operation", "operator", "defaultvalue", "lookupobject",
        "lookupfield", "returnfield", "returnfields", "sequencevalue",
        "startvalue", "incrementby", "portexpression", "ports",
        "rankfield", "rankorder", "groupbyfield", "joinercondition",
    }
    found = []
    seen = set()

    def add(label, value):
        text = _compact_logic_value(value)
        if text and text not in seen:
            seen.add(text)
            found.append((label, text))

    def key_value(node, names):
        if not isinstance(node, dict):
            return None
        normalized = {re.sub(r"[^a-z0-9]", "", str(k).lower()): v for k, v in node.items()}
        for name in names:
            if name in normalized:
                value = normalized[name]
                if value not in (None, "", [], {}):
                    return value
        return None

    def relation_label(node, label):
        """Capture lookup/join field relationships as one readable condition."""
        lookup_field = key_value(node, {
            "lookupfield", "lookupfieldname", "leftfield", "leftfieldname",
            "masterfield", "masterfieldname", "fieldname", "field",
        })
        incoming_field = key_value(node, {
            "incomingfield", "incomingfieldname", "rightfield", "rightfieldname",
            "slavefield", "slavefieldname", "sourcefield", "sourcefieldname",
        })
        operator = key_value(node, {"operator", "comparisonoperator", "op"})

        if lookup_field is not None and incoming_field is not None:
            op = str(operator).strip() if operator is not None else "="
            add(
                f"{label} condition",
                f"{label} field {lookup_field} {op} incoming field {incoming_field}",
            )
            return True

        # Some responses wrap the two sides in objects.
        left = key_value(node, {"left", "leftoperand", "master", "lookup"})
        right = key_value(node, {"right", "rightoperand", "slave", "incoming"})
        if left is not None and right is not None and operator is not None:
            add(
                f"{label} condition",
                f"{_compact_logic_value(left)} {operator} {_compact_logic_value(right)}",
            )
            return True
        return False

    def visit(node, parent_key=""):
        if isinstance(node, dict):
            relation_found = relation_label(
                node, "Lookup" if transformation_type == "Lookup" else "Join"
            )
            relationship_keys = {
                "lookupfield", "lookupfieldname", "incomingfield", "incomingfieldname",
                "leftfield", "leftfieldname", "rightfield", "rightfieldname",
                "masterfield", "masterfieldname", "slavefield", "slavefieldname",
                "operator", "comparisonoperator", "op",
            }
            for key, value in node.items():
                key_norm = re.sub(r"[^a-z0-9]", "", str(key).lower())
                if key_norm in candidates:
                    # Once we have a complete field-to-field relationship,
                    # don't separately display its raw lookupField/operator
                    # keys; the complete condition is much clearer.
                    if relation_found and key_norm in relationship_keys:
                        continue
                    add(str(key), value)
                if isinstance(value, (dict, list)):
                    visit(value, str(key))
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, (dict, list)):
                    visit(item, parent_key)

    visit(transformation)

    # Transformation-specific fallbacks from the visible Informatica property
    # names. These make the explanation useful even when a vendor version uses
    # slightly different nesting.
    if transformation_type == "Lookup":
        for key in ("lookupConditions", "lookupCondition", "lookupcondition", "conditions"):
            value = transformation.get(key)
            if value:
                for node in value if isinstance(value, list) else [value]:
                    if isinstance(node, dict):
                        relation_label(node, "Lookup")

    if transformation_type == "Joiner":
        for key in ("joinConditions", "joinCondition", "joincondition", "conditions"):
            value = transformation.get(key)
            if value:
                for node in value if isinstance(value, list) else [value]:
                    if isinstance(node, dict):
                        relation_label(node, "Join")

    priority = {
        "expression": 1, "expressionvalue": 1, "expressionstring": 1,
        "formula": 1, "condition": 1, "joincondition": 1,
        "lookupcondition": 1, "rankcondition": 1, "routercondition": 1,
        "filtercondition": 1, "advancedfiltercondition": 1,
        "lookup condition": 1, "join condition": 1,
        "aggregateexpression": 1, "aggregatefunction": 1,
        "sortfields": 2, "sortfield": 2, "groupby": 2,
        "groupbyfields": 2, "lookupobject": 2, "lookupfield": 2,
        "returnfield": 2, "returnfields": 2, "sequencevalue": 2,
        "startvalue": 2, "incrementby": 2, "operation": 2,
        "rankfield": 2, "rankorder": 2,
        "defaultvalue": 3, "operator": 5, "function": 3,
        "portexpression": 3, "ports": 4,
    }

    found.sort(key=lambda item: priority.get(
        re.sub(r"[^a-z0-9]", "", item[0].lower()), 5
    ))

    details = []
    relation_details = []
    generic_details = []
    for key, value in found:
        normalized_key = re.sub(r"[^a-z0-9]", "", key.lower())
        if normalized_key in {"lookupcondition", "joincondition", "lookupconditioncondition", "joinconditioncondition"} or key in {"Lookup condition", "Join condition"}:
            relation_details.append(f"{key}: {value}")
        else:
            generic_details.append(f"{key}: {value}")

    # Rich relationships first. Do not hide them behind a generic operator.
    details.extend(relation_details)
    details.extend(generic_details)

    # Deduplicate while preserving order.
    unique_details = []
    seen_details = set()
    for detail in details:
        if detail not in seen_details:
            seen_details.add(detail)
            unique_details.append(detail)

    return " | ".join(unique_details[:30])


def transformation_logic_items(transformation):
    """Return configured logic as separate human-readable items."""
    raw = str(transformation.get("logic") or "").strip()
    if not raw:
        return []

    # extract_transformation_logic joins independent metadata values with |.
    return [item.strip() for item in raw.split(" | ") if item.strip()]


def transformation_explanation(transformation):
    """Return a concise purpose when no concrete configured logic was captured."""
    ttype = transformation.get("type") or "Transformation"
    items = transformation_logic_items(transformation)
    if items:
        return items

    purpose = {
        "Lookup": "Looks up related data using the configured lookup logic.",
        "Expression": "Calculates or derives values using configured expressions.",
        "Router": "Routes records into groups using configured routing conditions.",
        "Joiner": "Combines data streams using the configured join condition.",
        "Sequence Generator": "Generates sequence values using the configured sequence settings.",
        "Sorter": "Sorts records using the configured sort fields and order.",
        "Rank": "Ranks records using the configured ranking criteria.",
        "Aggregator": "Groups and calculates data using configured aggregation logic.",
        "Filter": "Keeps only records that satisfy the configured filter condition.",
        "Update Strategy": "Determines how incoming records are handled by the target.",
        "Source Qualifier": "Prepares source data using the configured source-query logic.",
    }
    return [purpose.get(
        ttype,
        "The captured Informatica metadata does not expose a more specific expression or condition.",
    )]


def transformation_upstream(mapping, transformation_name):
    """Return the immediate upstream transformations for a step."""
    names = []
    for link in mapping.get("links", []):
        if link.get("to") == transformation_name and link.get("from"):
            if link["from"] not in names:
                names.append(link["from"])
    return names


def sources_for_transformation(mapping, transformation):
    """Identify source objects whose captured fields are referenced by a step's logic."""
    text = " ".join(transformation_logic_items(transformation)).lower()
    matches = []
    for source_obj in mapping.get("sources", []):
        fields = source_obj.get("fields", []) or []
        matched_fields = []
        for field in fields:
            field_name = field.get("name") or field.get("nativeName") or ""
            if field_name and re.search(r"\b" + re.escape(field_name.lower()) + r"\b", text):
                matched_fields.append(field_name)
        if matched_fields:
            matches.append({
                "name": object_label(source_obj, "Source"),
                "fields": matched_fields,
            })
    return matches



def extract_transformation_fields(transformation):
    """Collect field/port names exposed anywhere inside a transformation.

    Informatica can expose fields under different nested structures depending
    on transformation type. We keep this conservative and only collect names
    from field/port-like containers or explicit field-name keys.
    """
    found = []
    seen = set()
    container_keys = {
        "fields", "ports", "inputfields", "outputfields",
        "incomingfields", "outgoingfields", "inputports", "outputports",
        "fieldlist", "portlist", "fieldmappings", "mappinglist",
    }
    explicit_keys = {
        "fieldname", "portname", "fromfieldname", "tofieldname",
        "lookupfield", "returnfield", "groupbyfield", "sortfield",
        "rankfield", "sourcefield", "targetfield",
    }

    def add(value):
        if value is None:
            return
        value = str(value).strip()
        if not value:
            return
        # Avoid storing obvious IDs/objects as field names.
        if value.startswith("$$") or value.startswith("##"):
            return
        if value not in seen:
            seen.add(value)
            found.append(value)

    def visit(node, field_context=False):
        if isinstance(node, dict):
            for key, value in node.items():
                norm = re.sub(r"[^a-z0-9]", "", str(key).lower())
                child_context = field_context or norm in container_keys

                if norm in explicit_keys and isinstance(value, (str, int, float)):
                    add(value)
                elif child_context and norm in {"name", "nativename", "field", "fieldname", "portname"}:
                    if isinstance(value, (str, int, float)):
                        add(value)

                if isinstance(value, (dict, list)):
                    visit(value, child_context)

        elif isinstance(node, list):
            for item in node:
                if isinstance(item, (dict, list)):
                    visit(item, field_context)

    visit(transformation)
    return found


def transformation_all_logic(transformation):
    """Return individual configured logic items, preserving captured values."""
    items = transformation_logic_items(transformation)
    if items:
        return items
    return transformation_explanation(transformation)


def upstream_chain(mapping, transformation_name):
    """Return all upstream transformation names, nearest first."""
    result = []
    queue_names = list(transformation_upstream(mapping, transformation_name))
    visited = set()

    while queue_names:
        current = queue_names.pop(0)
        if current in visited:
            continue
        visited.add(current)
        result.append(current)
        for parent in transformation_upstream(mapping, current):
            if parent not in visited:
                queue_names.append(parent)

    return result


def transformation_by_name(mapping, name):
    for transformation in mapping.get("transformations", []):
        if transformation.get("name") == name:
            return transformation
    return None


def field_candidates_for_transformation(mapping, transformation_name):
    """Return fields exposed by the step's upstream chain and source objects."""
    candidates = []
    seen = set()

    names = [transformation_name] + upstream_chain(mapping, transformation_name)

    for name in names:
        transformation = transformation_by_name(mapping, name)
        if transformation:
            for field in transformation.get("fields", []) or []:
                if field not in seen:
                    seen.add(field)
                    candidates.append((name, field))

    for source in mapping.get("sources", []):
        source_name = object_label(source, "Source")
        for field in source.get("fields", []) or []:
            field_name = field.get("name") or field.get("nativeName") or ""
            if field_name and field_name not in seen:
                seen.add(field_name)
                candidates.append((source_name, field_name))

    return candidates


def find_field_provenance(mapping, field_name, target_transformation=None):
    """Find likely provenance without pretending uncertain lineage is confirmed."""
    if not field_name:
        return []

    candidates = field_candidates_for_transformation(
        mapping,
        target_transformation,
    ) if target_transformation else []

    exact = [x for x in candidates if x[1].lower() == field_name.lower()]
    if exact:
        return exact

    # If the field is present anywhere in the mapping, report it as a review
    # candidate rather than claiming that it is definitely the source.
    all_candidates = []
    seen = set()
    for transformation in mapping.get("transformations", []):
        for field in transformation.get("fields", []) or []:
            key = (transformation.get("name", ""), field)
            if key not in seen and field.lower() == field_name.lower():
                seen.add(key)
                all_candidates.append(key)

    for source in mapping.get("sources", []):
        source_name = object_label(source, "Source")
        for field in source.get("fields", []) or []:
            field_value = field.get("name") or field.get("nativeName") or ""
            key = (source_name, field_value)
            if key not in seen and field_value.lower() == field_name.lower():
                seen.add(key)
                all_candidates.append(key)

    return all_candidates


def mapping_location_for_field(mapping, item):
    """Describe exactly where a field-mapping finding originates."""
    target_transformation = item.get("target_transformation") or "Target"
    src = item.get("source_field") or "<missing>"
    tgt = item.get("target_field") or "<unresolved>"
    upstream = upstream_chain(mapping, target_transformation)
    return {
        "target_transformation": target_transformation,
        "source_field": src,
        "target_field": tgt,
        "upstream": upstream,
    }


def normalize_mapping(data: dict[str, Any]) -> dict[str, Any]:
    result = {
        "name": data.get("name", ""),
        "description": data.get("description", ""),
        "documentType": data.get("documentType", "MAPPING"),
        "sources": [],
        "targets": [],
        "transformations": [],
        "filters": [],
        "field_mappings": [],
        "links": [],
        "raw": data,
    }

    transformations = data.get("transformations") or []
    oid_to_name = {}
    oid_to_type = {}

    # First pass: identify transformation names and types.
    for transformation in transformations:
        if not isinstance(transformation, dict):
            continue

        oid = transformation.get("$$OID")
        name = transformation.get("name", "")
        class_id = transformation.get("$$class")
        ttype = infer_transformation_type(transformation, TRANSFORMATION_TYPES.get(class_id, "Transformation"))

        if oid is not None:
            oid_to_name[oid] = name
            oid_to_type[oid] = ttype

    # Second pass: extract useful information.
    for transformation in transformations:
        if not isinstance(transformation, dict):
            continue

        oid = transformation.get("$$OID")
        name = transformation.get("name", "")
        class_id = transformation.get("$$class")
        ttype = infer_transformation_type(transformation, TRANSFORMATION_TYPES.get(class_id, "Transformation"))

        logic = extract_transformation_logic(transformation, ttype)
        entry = {
            "name": name,
            "type": ttype,
            "class": class_id,
            "oid": oid,
            "logic": logic,
            "fields": extract_transformation_fields(transformation),
        }

        if ttype == "Source":
            obj = object_summary(transformation)
            if obj:
                entry["object"] = obj
                result["sources"].append(obj)

        elif ttype == "Target":
            obj = object_summary(transformation)
            if obj:
                entry["object"] = obj
                result["targets"].append(obj)

        elif ttype == "Filter":
            conditions = []

            for condition in transformation.get("filterConditions") or []:
                if isinstance(condition, dict):
                    conditions.append({
                        "field": condition.get("fieldName", ""),
                        "operator": condition.get("operator", ""),
                        "value": condition.get("filterValue", ""),
                        "transformation": name,
                    })

            if transformation.get("advancedFilterCondition"):
                conditions.append({
                    "field": "advancedFilterCondition",
                    "operator": "",
                    "value": transformation.get("advancedFilterCondition", ""),
                    "transformation": name,
                })

            # Some Informatica versions store the condition deeper in the
            # transformation object. Look for exact filter-condition objects
            # without guessing business meaning.
            if not conditions:
                for node in walk(transformation):
                    if not isinstance(node, dict):
                        continue
                    if (
                        node.get("fieldName") is not None
                        and node.get("filterValue") is not None
                    ):
                        conditions.append({
                            "field": node.get("fieldName", ""),
                            "operator": node.get("operator", ""),
                            "value": node.get("filterValue", ""),
                            "transformation": name,
                        })

            # Deduplicate.
            seen = set()
            clean_conditions = []
            for condition in conditions:
                key = (
                    condition.get("field", ""),
                    condition.get("operator", ""),
                    condition.get("value", ""),
                    condition.get("transformation", ""),
                )
                if key not in seen:
                    seen.add(key)
                    clean_conditions.append(condition)

            entry["conditions"] = clean_conditions
            result["filters"].extend(clean_conditions)

        # Target manual field mappings.
        manual = transformation.get("manualMappings") or {}
        mapping_list = manual.get("mappingList") or []

        for item in mapping_list:
            if isinstance(item, dict):
                result["field_mappings"].append({
                    "source_field": item.get("fromFieldName", ""),
                    "target_oid": (item.get("$toField") or {}).get("##OID"),
                    "target_field": "",
                    "target_transformation": name,
                })

        result["transformations"].append(entry)

    # Resolve target OIDs to target field names.
    target_field_by_oid = {}

    for transformation in transformations:
        if not isinstance(transformation, dict):
            continue

        if TRANSFORMATION_TYPES.get(transformation.get("$$class")) != "Target":
            continue

        # Depending on the Informatica response shape, fields can be directly
        # on the transformation or under dataAdapter.object.
        possible_field_lists = [
            transformation.get("fields") or [],
            ((transformation.get("dataAdapter") or {}).get("object") or {}).get(
                "fields", []
            ),
        ]

        for field_list in possible_field_lists:
            for field in field_list:
                if isinstance(field, dict) and field.get("$$OID") is not None:
                    target_field_by_oid[field["$$OID"]] = (
                        field.get("name")
                        or field.get("nativeName")
                        or ""
                    )

    for mapping in result["field_mappings"]:
        mapping["target_field"] = target_field_by_oid.get(
            mapping.get("target_oid"), ""
        )

    # Resolve links into readable data flow.
    for link in data.get("links") or []:
        if not isinstance(link, dict):
            continue

        from_oid = (link.get("$fromTransformation") or {}).get("##OID")
        to_oid = (link.get("$toTransformation") or {}).get("##OID")

        result["links"].append({
            "from": oid_to_name.get(from_oid, str(from_oid or "")),
            "to": oid_to_name.get(to_oid, str(to_oid or "")),
            "from_type": oid_to_type.get(from_oid, ""),
            "to_type": oid_to_type.get(to_oid, ""),
            "name": link.get("name", ""),
        })

    def unique_objects(items):
        seen = set()
        output = []
        for item in items:
            key = json.dumps(item, sort_keys=True, ensure_ascii=False)
            if key not in seen:
                seen.add(key)
                output.append(item)
        return output

    result["sources"] = unique_objects(result["sources"])
    result["targets"] = unique_objects(result["targets"])

    return result


# ============================================================
# HUMAN-READABLE HELPERS
# ============================================================

def source_field_names(mapping):
    names = set()
    for source in mapping.get("sources", []):
        for field in source.get("fields", []):
            name = field.get("name") or field.get("nativeName")
            if name:
                names.add(name)
    return names


def target_field_names(mapping):
    names = set()
    for target in mapping.get("targets", []):
        for field in target.get("fields", []):
            name = field.get("name") or field.get("nativeName")
            if name:
                names.add(name)
    return names


def format_filter(condition):
    field = condition.get("field", "")
    operator = condition.get("operator", "")
    value = condition.get("value", "")

    if field == "advancedFilterCondition":
        return value or "Advanced filter condition captured."

    # Avoid awkward spacing for operators that already contain their own syntax.
    if operator:
        return f"{field} {operator} {value}".strip()

    return f"{field} {value}".strip()


def object_label(obj, fallback):
    return (
        obj.get("path")
        or obj.get("name")
        or obj.get("objectName")
        or fallback
    )


def connection_display(obj):
    name = obj.get("connection_name")
    if name:
        return name
    # A technical connection ID is not useful to a BA and can be misleading
    # when displayed as if it were a connection/server name.
    if obj.get("connection_id"):
        return "Connection name not exposed in captured metadata"
    return "Connection details not captured"


def database_display(obj):
    schema = obj.get("schema")
    if schema:
        return schema
    return "Database / schema not exposed in captured metadata"


def primary_source(mapping):
    return mapping.get("sources", [None])[0] if mapping.get("sources") else None


def primary_target(mapping):
    return mapping.get("targets", [None])[0] if mapping.get("targets") else None


def human_title(mapping):
    """Use a business-friendly title while keeping the real mapping name visible."""
    filters = mapping.get("filters", [])
    if filters:
        first = filters[0]
        field = first.get("field", "")
        value = first.get("value", "")
        if field and value and field != "advancedFilterCondition":
            clean_value = str(value).strip('"').strip("'")
            return f"{clean_value} {field} Data Flow"
    return mapping.get("name") or "Data Flow"


def business_summary(mapping):
    """Create a concise, human-readable functional explanation of the mapping.

    This summary is intentionally written for business users. It describes
    what happens to the data and why, without exposing Informatica
    transformation names, technical conditions, field counts, or connection IDs.
    """
    source = primary_source(mapping)
    target = primary_target(mapping)

    source_name = (
        object_label(source or {}, "the source data")
        if source
        else "the source data"
    )
    target_name = (
        object_label(target or {}, "the destination")
        if target
        else "the destination"
    )

    transformations = [
        t
        for t in mapping.get("transformations", [])
        if t.get("type") not in {"Source", "Target"}
    ]

    # Translate technical transformation types into business-friendly
    # descriptions. The order follows the captured mapping metadata.
    type_explanations = {
        "Source Qualifier": "prepares the incoming data for processing",
        "Lookup": "finds related information to enrich the records",
        "Expression": "calculates and derives useful order information",
        "Router": "separates records into the appropriate processing paths",
        "Filter": "keeps only the records that meet the required criteria",
        "Joiner": "combines related information from different data streams",
        "Sequence Generator": "creates a tracking value for the processed records",
        "Sorter": "organizes the records in the required order",
        "Rank": "identifies the highest-priority records",
        "Aggregator": "summarizes the processed information",
    }

    explanations = []
    seen = set()

    for transformation in transformations:
        transformation_type = transformation.get("type", "")
        explanation = type_explanations.get(
            transformation_type,
            "processes the information according to the configured logic",
        )

        # Avoid repeating the same generic sentence when several
        # transformations of the same type are present.
        if explanation not in seen:
            explanations.append(explanation)
            seen.add(explanation)

    if explanations:
        if len(explanations) == 1:
            processing_sentence = explanations[0].capitalize() + "."
        elif len(explanations) == 2:
            processing_sentence = (
                explanations[0].capitalize()
                + " and "
                + explanations[1]
                + "."
            )
        else:
            processing_sentence = (
                ", ".join(explanations[:-1]).capitalize()
                + ", and "
                + explanations[-1]
                + "."
            )
    else:
        processing_sentence = (
            "The information is processed according to the configured mapping logic."
        )

    # Keep business rules in plain language. The exact Informatica expression
    # remains available in the Logic Applied and Technical Specification views.
    business_rules = []

    for condition in mapping.get("filters", []):
        condition_text = format_filter(condition)
        if not condition_text:
            continue

        normalized = condition_text.lower().replace(" ", "")

        if "to_decimal(ordervalue)>0" in normalized:
            business_rules.append(
                "only orders with a positive order value continue"
            )
        elif "rankindex<=3" in normalized:
            business_rules.append(
                "only the top three ranked records continue"
            )
        else:
            # Do not pretend to understand an unfamiliar rule. Keep it
            # business-friendly while directing the user to the detailed logic.
            business_rules.append(
                "records are retained according to the configured business criteria"
            )

    if business_rules:
        if len(business_rules) == 1:
            rule_sentence = (
                " The process also ensures that "
                + business_rules[0]
                + "."
            )
        else:
            rule_sentence = (
                " The process also ensures that "
                + ", and ".join(business_rules)
                + "."
            )
    else:
        rule_sentence = ""

    return (
        f"The process takes information from {source_name} and prepares it "
        f"for the final business result. During processing, related information "
        f"is added, important order details are calculated, records are organized "
        f"and checked against the required criteria, and the relevant information "
        f"is summarized. {processing_sentence}"
        f"{rule_sentence} The final result is loaded into {target_name}."
    )


# ============================================================
# STATIC MAPPING VALIDATION / ERROR HANDLING
# ============================================================

def validate_mapping(mapping):
    """Validate what can actually be proven from captured metadata.

    Important: missing lineage is NOT treated as a mapping error. Informatica
    may have a valid design even when the network response does not expose all
    intermediate ports. Such cases are reported as NEEDS REVIEW with a precise
    location so the user knows where to look.
    """
    checks = []

    sources = mapping.get("sources", [])
    targets = mapping.get("targets", [])
    filters = mapping.get("filters", [])
    field_maps = mapping.get("field_mappings", [])
    links = mapping.get("links", [])

    def add(status, title, detail, suggestion="", location=""):
        checks.append({
            "status": status,
            "title": title,
            "detail": detail,
            "suggestion": suggestion,
            "location": location,
        })

    if sources:
        add(
            "PASS",
            "Source objects captured",
            f"{len(sources)} source object(s) are present in the captured mapping metadata.",
        )
    else:
        add(
            "REVIEW",
            "Source object could not be confirmed",
            "The captured response does not expose a source object. This is a metadata-capture issue unless Informatica itself shows otherwise.",
            "Recapture the mapping while the Mapping Designer is open.",
            "Mapping metadata → Source section",
        )

    if targets:
        add(
            "PASS",
            "Target objects captured",
            f"{len(targets)} target object(s) are present in the captured mapping metadata.",
        )
    else:
        add(
            "REVIEW",
            "Target object could not be confirmed",
            "The captured response does not expose a target object.",
            "Recapture the mapping while the Mapping Designer is open.",
            "Mapping metadata → Target section",
        )

    if links:
        add(
            "PASS",
            "Data-flow links captured",
            f"{len(links)} Source → Transformation → Target link(s) were captured.",
        )
    else:
        add(
            "REVIEW",
            "Data-flow links were not captured",
            "The app cannot reconstruct the complete flow without links. This does not prove that the Informatica mapping is invalid.",
            "Recapture the mapping metadata from the Mapping Designer.",
            "Data Flow → Exact flow",
        )

    # Filter validation is lineage-aware.
    for condition in filters:
        field = condition.get("field", "")
        transformation = condition.get("transformation", "Filter")
        rule_text = format_filter(condition)

        if field == "advancedFilterCondition":
            add(
                "PASS",
                f"Filter rule captured in {transformation}",
                f"Exact advanced filter: {rule_text}",
                location=f"Data Flow → {transformation}",
            )
            continue

        provenance = find_field_provenance(
            mapping,
            field,
            transformation,
        )

        if provenance:
            where = ", ".join(f"{owner}.{name}" for owner, name in provenance[:4])
            add(
                "PASS",
                f"Filter field '{field}' is traceable",
                f"Rule: {rule_text}. Captured metadata can trace the field to {where}.",
                location=f"Data Flow → {transformation}",
            )
        else:
            add(
                "REVIEW",
                f"Filter field '{field}' needs lineage review",
                f"Rule: {rule_text}. The field is not visible in the captured upstream field metadata, so the app cannot prove where it originates.",
                "Open the filter transformation and inspect its incoming ports/fields in Informatica. Do not treat this as a confirmed mapping error.",
                f"Data Flow → {transformation} → Filter condition",
            )

    # Field mapping validation is also lineage-aware.
    if field_maps:
        for item in field_maps:
            src = item.get("source_field", "")
            tgt = item.get("target_field", "")
            location = mapping_location_for_field(mapping, item)
            target_transformation = location["target_transformation"]
            provenance = find_field_provenance(
                mapping,
                src,
                target_transformation,
            )

            if not src:
                add(
                    "REVIEW",
                    "Source field is unresolved",
                    f"The target mapping {target_transformation} does not expose a source field name in the captured metadata.",
                    "Open the target transformation's field mapping and inspect the incoming port.",
                    f"Data Flow → {target_transformation} → field mapping",
                )
            elif provenance:
                where = ", ".join(f"{owner}.{name}" for owner, name in provenance[:4])
                add(
                    "PASS",
                    f"Field mapping '{src}' is traceable",
                    f"{src} → {tgt or '<target unresolved>'}. Upstream metadata contains the field at {where}.",
                    location=f"Data Flow → {target_transformation} → field mapping",
                )
            else:
                upstream = location["upstream"]
                upstream_text = " → ".join(upstream[:6]) if upstream else "No upstream transformation was captured"
                add(
                    "REVIEW",
                    f"Field '{src}' needs lineage review",
                    f"This finding comes from the field mapping into '{target_transformation}': {src} → {tgt or '<target unresolved>'}. The field is not visible in the captured upstream field metadata. Upstream chain captured: {upstream_text}.",
                    f"Open '{target_transformation}' in Informatica and trace the incoming port '{src}' backward through the upstream transformations. This is NOT a confirmed mapping error.",
                    f"Data Flow → {target_transformation} → field mapping for '{src}'",
                )

            if not tgt:
                add(
                    "REVIEW",
                    f"Target field for '{src or 'this mapping'}' is unresolved",
                    f"The target field ID was captured, but its display name was not resolved in the available target metadata.",
                    "Open the target transformation and inspect the target port/field mapping.",
                    f"Data Flow → {target_transformation} → target field",
                )

    else:
        add(
            "REVIEW",
            "No field-level mappings captured",
            "The mapping may still be valid, but field-level movement could not be verified from the captured response.",
            "Open Rules & Data or recapture the mapping metadata.",
            "Rules & Data → Data Movement",
        )

    # Link endpoints that cannot be resolved are capture reviews, not mapping errors.
    known_names = {
        t.get("name") for t in mapping.get("transformations", []) if t.get("name")
    }
    for link in links:
        source_name = link.get("from")
        target_name = link.get("to")
        if source_name not in known_names or target_name not in known_names:
            add(
                "REVIEW",
                "Link endpoint needs review",
                f"Captured link: {source_name} → {target_name}. One endpoint is not present in the normalized transformation list.",
                "Recapture the mapping metadata before treating this as a real Informatica design problem.",
                "Data Flow → Exact flow",
            )

    error_count = sum(1 for c in checks if c["status"] == "ERROR")
    review_count = sum(1 for c in checks if c["status"] == "REVIEW")
    warning_count = sum(1 for c in checks if c["status"] == "WARNING")

    # There should be no false INVALID state merely because lineage was not
    # exposed by the network response.
    overall = "NO CONFIRMED ERRORS" if error_count == 0 else "CONFIRMED ERROR"

    return {
        "overall": overall,
        "errors": error_count,
        "reviews": review_count,
        "warnings": warning_count,
        "checks": checks,
    }


# ============================================================
# DETERMINISTIC ANSWERS FOR SIMPLE QUESTIONS
# ============================================================

def answer_from_metadata(mapping, question):
    """Answer simple factual questions locally without spending an LLM request."""
    q = question.lower().strip()

    source = primary_source(mapping)
    target = primary_target(mapping)
    filters = mapping.get("filters", [])
    fields = mapping.get("field_mappings", [])
    links = mapping.get("links", [])

    if any(word in q for word in ["filter", "filters", "condition", "conditions", "rule", "rules"]):
        if filters:
            lines = ["### Business rules\n"]
            for condition in filters:
                rule = format_filter(condition)
                if rule:
                    lines.append(f"- **{rule}**")
            return "\n".join(lines)
        return "No filter or business rule was captured in the mapping metadata."

    if any(phrase in q for phrase in ["source", "where does", "comes from", "coming from"]):
        if source:
            return (
                f"### Source\n\n"
                f"**Object:** `{object_label(source, 'Source')}`\n\n"
                f"**Connection:** `{connection_display(source)}`\n\n"
                f"**Database / schema:** {database_display(source)}"
            )
        return "The source object could not be determined from the captured metadata."

    if any(phrase in q for phrase in ["target", "destination", "where does it go", "goes to"]):
        if target:
            return (
                f"### Destination\n\n"
                f"**Object:** `{object_label(target, 'Target')}`\n\n"
                f"**Connection:** `{connection_display(target)}`\n\n"
                f"**Database / schema:** {database_display(target)}"
            )
        return "The target object could not be determined from the captured metadata."

    if any(phrase in q for phrase in ["field mapping", "field mappings", "fields mapped", "what fields", "which fields"]):
        if fields:
            rows = ["### Data movement\n", "| Source | Target |", "|---|---|"]
            for item in fields:
                rows.append(
                    f"| `{item.get('source_field', '')}` | `{item.get('target_field', '') or 'Unresolved'}` |"
                )
            return "\n".join(rows)
        return "No field-level mappings were captured."

    if any(phrase in q for phrase in ["flow", "data flow", "mapping flow", "how does it flow"]):
        if links:
            return "\n".join(
                f"- `{x.get('from', '')}` → `{x.get('to', '')}`"
                for x in links
            )
        return business_summary(mapping)

    if any(phrase in q for phrase in ["valid", "invalid", "error", "errors", "issue", "issues"]):
        validation = validate_mapping(mapping)
        if validation["errors"] == 0:
            return (
                "### Mapping validation: No confirmed errors\n\n"
                f"No confirmed structural errors were detected. "
                f"{validation.get('reviews', 0)} item(s) need lineage review. "
                "Open the **Error Handling** tab to see exactly where to look."
            )
        return (
            "### Mapping validation: Confirmed error(s)\n\n"
            f"{validation['errors']} confirmed error(s) were detected. "
            "Open the **Error Handling** tab to see the exact location and suggested fix."
        )

    # "what mappings are used" / "what mapping is used"
    if "mapping" in q and any(word in q for word in ["used", "use", "mappings"]):
        if fields:
            return (
                f"### Field mappings used\n\n"
                + "\n".join(
                    f"- `{x.get('source_field', '')}` → `{x.get('target_field', '') or 'Unresolved'}`"
                    for x in fields
                )
            )
        return business_summary(mapping)

    return None


# ============================================================
# GROQ AI
# ============================================================

def compact_json(data):
    return json.dumps(data, indent=2, ensure_ascii=False)


def build_system_instruction(mapping):
    ai_mapping = dict(mapping)
    ai_mapping.pop("raw", None)

    return f"""
You are an Informatica Mapping Intelligence Assistant.

Use ONLY the supplied Informatica mapping metadata.

Rules:
1. Never invent source data, database names, row counts, business results, values, or logic.
2. If a database/server/schema name is not captured, explicitly say it is not available.
3. Distinguish mapping design from runtime results.
4. Explain technical concepts simply for a BA or business user.
5. When discussing a filter, give the exact field, operator, and value when available.
6. When discussing field mappings, show WHERE FROM and WHERE TO, including source/target object names.
7. When discussing Lookup or Joiner logic, show the actual field-to-field condition when it exists in the metadata, not just the operator.
8. When discussing errors, distinguish static mapping validation from runtime execution errors.
9. Never claim that a runtime error occurred unless runtime logs were supplied.
10. Use the exact captured mapping metadata as the source of truth.

Mapping metadata:
{compact_json(ai_mapping)}
"""


def ask_groq(mapping, user_prompt, history=None):
    if not GROQ_API_KEY or GROQ_API_KEY == "PASTE_YOUR_NEW_GROQ_API_KEY_HERE":
        raise RuntimeError(
            "Groq API key is missing. Replace "
            "PASTE_YOUR_NEW_GROQ_API_KEY_HERE in app.py with your Groq API key."
        )

    history = history or []
    messages = [
        {
            "role": "system",
            "content": build_system_instruction(mapping),
        }
    ]

    for message in history:
        role = message.get("role", "user")
        content = message.get("content", "")
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": user_prompt})

    client = Groq(api_key=GROQ_API_KEY)
    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=messages,
        temperature=0.2,
        max_tokens=2048,
    )

    content = response.choices[0].message.content
    return content or "Groq returned an empty response."



# ============================================================
# CHANGE IMPACT ANALYSIS / TROUBLESHOOTING HELPERS
# ============================================================

def mapping_graph(mapping):
    """Build a readable directed graph from the captured Informatica links."""
    adjacency = {}
    reverse = {}
    for link in mapping.get("links", []):
        a = link.get("from")
        b = link.get("to")
        if not a or not b:
            continue
        adjacency.setdefault(a, []).append(b)
        reverse.setdefault(b, []).append(a)
    return adjacency, reverse


def downstream_chain(mapping, start_name):
    """Return all downstream steps from a transformation, nearest first."""
    adjacency, _ = mapping_graph(mapping)
    result = []
    queue_names = list(adjacency.get(start_name, []))
    visited = set()
    while queue_names:
        current = queue_names.pop(0)
        if current in visited:
            continue
        visited.add(current)
        result.append(current)
        queue_names.extend(adjacency.get(current, []))
    return result


def upstream_chain_including(mapping, start_name):
    """Return all upstream steps from a transformation, nearest first."""
    _, reverse = mapping_graph(mapping)
    result = []
    queue_names = list(reverse.get(start_name, []))
    visited = set()
    while queue_names:
        current = queue_names.pop(0)
        if current in visited:
            continue
        visited.add(current)
        result.append(current)
        queue_names.extend(reverse.get(current, []))
    return result


def impact_target_reached(mapping, names):
    target_names = {
        object_label(x, "Target") for x in mapping.get("targets", [])
    }
    target_transformation_names = {
        t.get("name") for t in mapping.get("transformations", [])
        if t.get("type") == "Target" and t.get("name")
    }
    return any(name in target_names or name in target_transformation_names for name in names)


def analyze_change_impact(mapping, change_type, selected_name, new_type="Filter", insert_after="", configuration=""):
    """Analyze a proposed add/delete without changing the real Informatica mapping."""
    adjacency, reverse = mapping_graph(mapping)
    steps = ordered_processing_steps(mapping)
    step_names = [x.get("name") for x in steps if x.get("name")]
    source_name = object_label(primary_source(mapping) or {}, "Source")
    target_name = object_label(primary_target(mapping) or {}, "Target")

    if change_type == "Add":
        anchor = insert_after or source_name
        affected = downstream_chain(mapping, anchor)
        proposed = {
            "change": "ADD",
            "item": new_type,
            "anchor": anchor,
            "configuration": configuration.strip(),
            "affected": affected,
            "target_reached": impact_target_reached(mapping, affected),
        }
        if anchor in step_names:
            proposed["proposed_position"] = f"After {anchor}"
        else:
            proposed["proposed_position"] = "At the beginning of the captured processing flow"
        if not affected:
            proposed["severity"] = "LOW"
        elif proposed["target_reached"]:
            proposed["severity"] = "HIGH"
        else:
            proposed["severity"] = "MEDIUM"
        return proposed

    # DELETE
    selected = selected_name
    affected = downstream_chain(mapping, selected)
    upstream = upstream_chain_including(mapping, selected)
    proposed = {
        "change": "DELETE",
        "item": selected,
        "upstream": upstream,
        "affected": affected,
        "target_reached": impact_target_reached(mapping, affected),
    }
    if not affected:
        proposed["severity"] = "LOW"
    elif proposed["target_reached"]:
        proposed["severity"] = "HIGH"
    else:
        proposed["severity"] = "MEDIUM"
    return proposed


def troubleshooting_guidance(step):
    """Static troubleshooting guidance; never claims that a runtime error occurred."""
    t = (step.get("type") or "Transformation").lower()
    guidance = {
        "source qualifier": [
            "Check the source/query configuration and incoming source availability.",
            "Confirm the expected source fields are exposed to the next step.",
        ],
        "lookup": [
            "Check the lookup object and the actual field-to-field lookup condition.",
            "Verify that incoming lookup keys are populated and compatible with the lookup fields.",
        ],
        "expression": [
            "Check each expression for datatype, null-handling, date conversion, and function syntax.",
            "Verify that every referenced input field is available upstream.",
        ],
        "router": [
            "Check each routing group condition and the default group behavior.",
            "Verify that the fields referenced by the conditions are available at the router input.",
        ],
        "filter": [
            "Check the exact filter condition and confirm that the incoming field has the expected value and datatype.",
            "If records are unexpectedly rejected, test the condition against representative input values.",
        ],
        "joiner": [
            "Check the actual field-to-field join condition and join type when captured.",
            "Verify both input streams contain the expected join keys and compatible datatypes.",
        ],
        "sequence generator": [
            "Check sequence start value, increment and whether the generated value is connected downstream.",
        ],
        "sorter": [
            "Check the configured sort fields and sort directions.",
            "Verify that the sorted fields are available at the sorter input.",
        ],
        "rank": [
            "Check the rank field, rank direction, top/bottom setting, rank count and grouping configuration.",
            "Verify that the rank field is populated before ranking.",
        ],
        "aggregator": [
            "Check group-by fields and aggregate expressions.",
            "Verify that the fields used in the aggregation are available and have compatible datatypes.",
        ],
    }
    for key, items in guidance.items():
        if key in t:
            return items
    return [
        "Check the transformation configuration shown above and verify that its required input fields are available upstream.",
        "If Informatica reports a runtime failure, use the execution/session log to identify the exact error before treating it as a confirmed runtime issue.",
    ]

# ============================================================
# PDF DOCUMENTATION
# ============================================================

def ordered_processing_steps(mapping):
    """Return captured processing steps in connected execution order."""
    steps = [
        t for t in mapping.get("transformations", [])
        if t.get("name") and t.get("type") not in {"Source", "Target"}
    ]
    by_name = {t.get("name"): t for t in steps}
    adjacency = {}
    incoming = {}
    for link in mapping.get("links", []):
        a, b = link.get("from"), link.get("to")
        if a and b:
            adjacency.setdefault(a, []).append(b)
            incoming.setdefault(b, []).append(a)

    ordered = []
    queue_names = [t.get("name") for t in steps if not incoming.get(t.get("name"))]
    visited = set()
    while queue_names:
        current = queue_names.pop(0)
        if current in visited:
            continue
        visited.add(current)
        if current in by_name:
            ordered.append(by_name[current])
        queue_names.extend(adjacency.get(current, []))

    for step in steps:
        if step.get("name") not in {x.get("name") for x in ordered}:
            ordered.append(step)
    return ordered


def functional_step_text(step):
    """Business-friendly description; deliberately avoids Informatica jargon."""
    t = (step.get("type") or "").lower()
    logic = " ".join(transformation_all_logic(step)).lower()
    if "source qualifier" in t:
        return "Prepares the incoming order data for processing."
    if "lookup" in t:
        return "Finds the related customer information needed for the order."
    if "expression" in t:
        return "Calculates useful order information such as the order year, delivery timing and delivery status."
    if "router" in t:
        return "Directs records to the appropriate processing path based on the configured conditions."
    if "filter" in t:
        if "orderValue".lower() in logic and "> 0" in logic:
            return "Keeps only orders with a positive order value."
        if "rankindex" in logic and "<= 3" in logic:
            return "Keeps only the top three records after ranking."
        return "Keeps only records that meet the required business condition."
    if "joiner" in t:
        return "Combines the order information with the related reference information."
    if "sequence" in t:
        return "Creates a unique sequence value used for processing or tracking."
    if "sorter" in t:
        return "Arranges the records in the required order before the next step."
    if "rank" in t:
        return "Identifies the highest-priority orders according to the configured ranking."
    if "aggregator" in t:
        return "Summarizes the processed information for the final result."
    if "update strategy" in t:
        return "Determines how processed records should be handled at the destination."
    return "Processes the incoming information according to the configured mapping logic."


def functional_overview(mapping):
    source = primary_source(mapping)
    target = primary_target(mapping)
    source_name = object_label(source or {}, "the source")
    target_name = object_label(target or {}, "the destination")
    steps = ordered_processing_steps(mapping)
    filters = [format_filter(x) for x in mapping.get("filters", []) if format_filter(x)]
    parts = [f"The process takes information from {source_name} and prepares it for {target_name}."]
    if steps:
        parts.append(f"The information passes through {len(steps)} processing stages before the final result is stored.")
    if filters:
        friendly = []
        for f in filters:
            if "TO_DECIMAL(orderValue) > 0" in f:
                friendly.append("only orders with a positive value are kept")
            elif "RANKINDEX" in f and "<= 3" in f:
                friendly.append("only the top three ranked records are kept")
            else:
                friendly.append(f"records must satisfy {f}")
        parts.append("The key selection rules are " + " and ".join(friendly) + ".")
    if mapping.get("field_mappings"):
        parts.append(f"The resulting information is then written to {target_name}.")
    return " ".join(parts)


def technical_overview(mapping):
    source = primary_source(mapping)
    target = primary_target(mapping)
    steps = ordered_processing_steps(mapping)
    source_name = object_label(source or {}, "Source")
    target_name = object_label(target or {}, "Target")
    return (
        f"The mapping reads from {source_name}, processes the data through "
        f"{len(steps)} captured transformation step(s), and writes the resulting "
        f"field mappings to {target_name}. The technical specification below preserves "
        f"the captured transformation types, configured expressions/conditions, "
        f"connections and field-level mappings."
    )


def flow_drawing(mapping, functional=False):
    """Create a compact image-like execution-order diagram for the PDF."""
    steps = ordered_processing_steps(mapping)
    source = primary_source(mapping)
    target = primary_target(mapping)
    labels = [(object_label(source or {}, "Source"), "Source")]
    for step in steps:
        label = functional_step_text(step) if functional else step.get("name", "Step")
        if functional:
            short = {
                "Source Qualifier": "Prepare order data",
                "Lookup": "Find customer information",
                "Expression": "Calculate order details",
                "Router": "Route records",
                "Filter": "Keep qualifying records",
                "Joiner": "Combine related information",
                "Sequence Generator": "Create tracking value",
                "Sorter": "Arrange records",
                "Rank": "Identify highest-priority orders",
                "Aggregator": "Summarize information",
            }.get(step.get("type"), label)
            label = short
        labels.append((label, step.get("type", "Step")))
    labels.append((object_label(target or {}, "Target"), "Target"))

    cols = 3
    box_w, box_h = 155, 42
    gap_x, gap_y = 18, 28
    rows = (len(labels) + cols - 1) // cols
    width = cols * box_w + (cols - 1) * gap_x
    height = rows * (box_h + gap_y) + 8
    d = Drawing(width, height)
    palette = {
        "Source": ("#163B5C", "#5BB8FF"),
        "Target": ("#174A35", "#55E89A"),
        "Lookup": ("#3F315F", "#C8A7FF"),
        "Expression": ("#573D22", "#FFCA7A"),
        "Router": ("#49345B", "#E0A7FF"),
        "Joiner": ("#214D4A", "#74E0D5"),
        "Filter": ("#4D4A1D", "#E5DF72"),
        "Sequence Generator": ("#304D3B", "#9DE3B0"),
        "Sorter": ("#263E52", "#8FCFFF"),
        "Rank": ("#543B26", "#FFC477"),
        "Aggregator": ("#3C3155", "#C6A7FF"),
    }
    for i, (label, typ) in enumerate(labels):
        row, col = divmod(i, cols)
        x = col * (box_w + gap_x)
        y = height - (row + 1) * (box_h + gap_y) + gap_y
        bg, accent = palette.get(typ, ("#20242D", "#AEB7C5"))
        d.add(Rect(x, y, box_w, box_h, rx=7, ry=7, fillColor=colors.HexColor(bg), strokeColor=colors.HexColor(accent), strokeWidth=1))
        d.add(String(x + 8, y + 26, f"{i+1}. {typ}", fontName="Helvetica-Bold", fontSize=7.5, fillColor=colors.HexColor(accent)))
        shown = str(label)
        if len(shown) > 29:
            shown = shown[:26] + "..."
        d.add(String(x + 8, y + 12, shown, fontName="Helvetica-Bold", fontSize=8.5, fillColor=colors.white))
        if i < len(labels) - 1:
            ni = i + 1
            nrow, ncol = divmod(ni, cols)
            nx = ncol * (box_w + gap_x)
            ny = height - (nrow + 1) * (box_h + gap_y) + gap_y
            if nrow == row:
                d.add(Line(x + box_w, y + box_h/2, nx, ny + box_h/2, strokeColor=colors.HexColor("#8C96A3"), strokeWidth=1.2))
            else:
                d.add(Line(x + box_w/2, y, x + box_w/2, ny + box_h, strokeColor=colors.HexColor("#8C96A3"), strokeWidth=1.2))
    return d


def generate_pdf(mapping, documentation_type="Functional Specification"):
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", mapping.get("name", "mapping"))
    suffix = "functional_specification" if documentation_type == "Functional Specification" else "technical_specification"
    output = BASE_DIR / f"{safe_name}_{suffix}.pdf"

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("PDFTitle", parent=styles["Title"], fontSize=20, leading=24, alignment=TA_CENTER, spaceAfter=10)
    subtitle_style = ParagraphStyle("PDFSubtitle", parent=styles["Normal"], fontSize=10, alignment=TA_CENTER, spaceAfter=16)
    heading_style = ParagraphStyle("PDFHeading", parent=styles["Heading2"], fontSize=14, leading=18, spaceBefore=12, spaceAfter=8)
    normal_style = ParagraphStyle("PDFNormal", parent=styles["Normal"], fontSize=9.5, leading=14, spaceAfter=6)
    small_style = ParagraphStyle("PDFSmall", parent=styles["Normal"], fontSize=8, leading=11, spaceAfter=4)
    code_style = ParagraphStyle("PDFCode", parent=styles["Code"], fontSize=7.5, leading=10, backColor=colors.HexColor("#F2F4F7"), borderPadding=6, spaceAfter=5)

    doc = SimpleDocTemplate(str(output), pagesize=A4, rightMargin=15*mm, leftMargin=15*mm, topMargin=15*mm, bottomMargin=15*mm, title=f"{documentation_type} - {mapping.get('name','')}", author="Informatica Mapping Intelligence")
    story = []
    story.append(Paragraph(documentation_type, title_style))
    story.append(Paragraph(f"Mapping: <b>{escape(str(mapping.get('name','')))}</b>", subtitle_style))

    if documentation_type == "Functional Specification":
        story.append(Paragraph("1. Purpose and Business Outcome", heading_style))
        story.append(Paragraph(escape(functional_overview(mapping)), normal_style))

        story.append(Paragraph("2. Process Flow", heading_style))
        story.append(Paragraph("The diagram below shows the order in which information is processed. The labels are intentionally business-friendly.", normal_style))
        story.append(flow_drawing(mapping, functional=True))
        story.append(Spacer(1, 8))

        story.append(Paragraph("3. What Happens at Each Stage", heading_style))
        for i, step in enumerate(ordered_processing_steps(mapping), 1):
            story.append(Paragraph(f"<b>{i}. {escape(functional_step_text(step))}</b>", normal_style))

        story.append(Paragraph("4. Logic Applied", heading_style))
        filters = [format_filter(x) for x in mapping.get("filters", []) if format_filter(x)]
        if filters:
            for f in filters:
                if "TO_DECIMAL(orderValue) > 0" in f:
                    text = "Only orders with a positive order value continue."
                elif "RANKINDEX" in f and "<= 3" in f:
                    text = "Only the top three ranked records continue to the final result."
                else:
                    text = f"Records continue only when the configured condition is satisfied: {f}"
                story.append(Paragraph(f"• {escape(text)}", normal_style))
        else:
            story.append(Paragraph("No selection condition was captured.", normal_style))

        story.append(Paragraph("5. Final Result", heading_style))
        target = primary_target(mapping)
        story.append(Paragraph(f"The processed information is delivered to {escape(object_label(target or {}, 'the destination'))}.", normal_style))
        story.append(Paragraph("This document is intended to explain the mapping in business-friendly terms; technical field names and implementation details are intentionally minimized.", small_style))

    else:
        source = primary_source(mapping)
        target = primary_target(mapping)
        story.append(Paragraph("1. Technical Overview", heading_style))
        story.append(Paragraph(escape(technical_overview(mapping)), normal_style))

        story.append(Paragraph("2. Execution Order", heading_style))
        story.append(flow_drawing(mapping, functional=False))
        story.append(Spacer(1, 8))

        story.append(Paragraph("3. Source and Target", heading_style))
        rows = [["Role", "Object", "Connection", "Database / Schema"]]
        for role, obj in [("Source", source), ("Target", target)]:
            rows.append([role, object_label(obj or {}, role), connection_display(obj or {}), database_display(obj or {})])
        table = Table(rows, colWidths=[25*mm, 50*mm, 55*mm, 45*mm], repeatRows=1)
        table.setStyle(TableStyle([("BACKGROUND", (0,0),(-1,0), colors.HexColor("#E8EEF7")), ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"), ("GRID",(0,0),(-1,-1),0.5,colors.grey), ("VALIGN",(0,0),(-1,-1),"TOP"), ("FONTSIZE",(0,0),(-1,-1),7.5), ("BOTTOMPADDING",(0,0),(-1,-1),5), ("TOPPADDING",(0,0),(-1,-1),5)]))
        story.append(table)

        story.append(Paragraph("4. Logic Applied by Transformation", heading_style))
        for i, step in enumerate(ordered_processing_steps(mapping), 1):
            story.append(Paragraph(f"<b>{i}. {escape(str(step.get('name','')))} — {escape(str(step.get('type','')))}</b>", normal_style))
            for logic in transformation_all_logic(step):
                story.append(Paragraph(escape(str(logic)), code_style))

        story.append(Paragraph("5. Lookup and Join Conditions", heading_style))
        relation_steps = [s for s in ordered_processing_steps(mapping) if s.get("type") in {"Lookup", "Joiner"}]
        if relation_steps:
            for step in relation_steps:
                story.append(Paragraph(f"<b>{escape(str(step.get('name','')))}</b>", normal_style))
                items = transformation_all_logic(step)
                for item in items:
                    story.append(Paragraph(escape(str(item)), code_style))
        else:
            story.append(Paragraph("No lookup or join transformation was captured.", normal_style))

        story.append(Paragraph("6. Field Mappings", heading_style))
        if mapping.get("field_mappings"):
            rows = [["Source field", "Applied at", "Target field"]]
            for item in mapping["field_mappings"]:
                loc = mapping_location_for_field(mapping, item)
                rows.append([item.get("source_field", "Unresolved"), loc.get("target_transformation", "Unresolved"), item.get("target_field", "Unresolved")])
            table = Table(rows, colWidths=[60*mm, 60*mm, 55*mm], repeatRows=1)
            table.setStyle(TableStyle([("BACKGROUND", (0,0),(-1,0), colors.HexColor("#E8EEF7")), ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"), ("GRID",(0,0),(-1,-1),0.5,colors.grey), ("VALIGN",(0,0),(-1,-1),"TOP"), ("FONTSIZE",(0,0),(-1,-1),7.5), ("BOTTOMPADDING",(0,0),(-1,-1),4), ("TOPPADDING",(0,0),(-1,-1),4)]))
            story.append(table)
        else:
            story.append(Paragraph("No field mappings were captured.", normal_style))

        story.append(Paragraph("7. Connections and Technical Notes", heading_style))
        story.append(Paragraph("Connection and database/schema values below are shown only when exposed by the captured Informatica metadata. Missing values are not inferred.", normal_style))
        story.append(Paragraph(f"Source connection: {escape(connection_display(source or {}))}", small_style))
        story.append(Paragraph(f"Target connection: {escape(connection_display(target or {}))}", small_style))

    def add_page_number(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.drawCentredString(A4[0]/2, 8*mm, f"Page {document.page}")
        canvas.restoreState()
    doc.build(story, onFirstPage=add_page_number, onLaterPages=add_page_number)
    return output

# ============================================================
# STREAMLIT STATE
# ============================================================

if "browser" not in st.session_state:
    st.session_state.browser = None

if "mappings" not in st.session_state:
    st.session_state.mappings = []

if "selected_mapping" not in st.session_state:
    st.session_state.selected_mapping = None

if "mapping" not in st.session_state:
    st.session_state.mapping = None

if "messages" not in st.session_state:
    st.session_state.messages = []

if "documentation_pdf" not in st.session_state:
    st.session_state.documentation_pdf = None

if "documentation_overview" not in st.session_state:
    st.session_state.documentation_overview = ""


# ============================================================
# LIGHT UI STYLING
# ============================================================

st.markdown(
    """
    <style>
    .flow-card {
        padding: 18px;
        border-radius: 14px;
        border: 1px solid rgba(128,128,128,0.25);
        margin-bottom: 12px;
    }
    .flow-arrow {
        text-align: center;
        font-size: 28px;
        padding: 10px;
    }
    .small-muted {
        color: #8b8b8b;
        font-size: 0.9rem;
    }
    .pipeline-arrow {
        text-align: center;
        font-size: 24px;
        color: #8b8b8b;
        margin: 4px 0;
    }
    .pipeline-end {
        border: 1px solid rgba(70, 200, 130, 0.35);
        background: rgba(30, 100, 70, 0.25);
        border-radius: 12px;
        padding: 14px 18px;
        text-align: center;
        font-weight: 700;
        margin-top: 8px;
    }

    .transformation-type {
        text-align: center;
        padding: 7px 10px;
        border-radius: 999px;
        border: 1px solid rgba(128,128,128,0.35);
        font-size: 0.82rem;
        font-weight: 700;
        white-space: nowrap;
        margin-top: 8px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# HEADER
# ============================================================

st.title("🔎 Informatica Mapping Intelligence")
st.caption(
    "Understand, validate, debug and document Informatica data flows"
)


# ============================================================
# SIDEBAR — CONNECTION AND MAPPING SELECTION
# ============================================================

with st.sidebar:
    st.header("Informatica")

    if st.button("Open / Connect", use_container_width=True):
        try:
            st.session_state.browser = get_browser()
            result = st.session_state.browser.open_informatica()
            st.success("Informatica browser opened.")
            st.info("Complete Informatica login in the browser if required.")
            st.caption(result.get("url", ""))
        except Exception as exc:
            st.error(f"Could not open Informatica: {exc}")

    if st.session_state.browser:
        if st.button(
            "Discover Projects / Folders / Mappings",
            use_container_width=True,
        ):
            try:
                with st.spinner("Discovering Informatica mappings..."):
                    result = st.session_state.browser.discover_mappings()

                st.session_state.mappings = result.get("mappings", [])

                if st.session_state.mappings:
                    st.success(
                        f"Found {len(st.session_state.mappings)} mappings."
                    )
                else:
                    st.warning("No mappings were found.")
                    st.write("Status:", result.get("status", ""))
            except Exception as exc:
                st.error(f"Discovery failed: {exc}")

        with st.expander("Browser Status"):
            try:
                st.json(st.session_state.browser.status())
            except Exception as exc:
                st.error(str(exc))

    st.divider()
    st.header("Select Data Flow")

    if st.session_state.mappings:
        projects = sorted(
            {
                m.get("project", "")
                for m in st.session_state.mappings
                if m.get("project")
            }
        )

        project = st.selectbox("Project", projects)

        folders = sorted(
            {
                m.get("folder", "")
                for m in st.session_state.mappings
                if m.get("project") == project
                and m.get("folder")
            }
        )

        folder = st.selectbox("Folder", folders)

        candidates = [
            m
            for m in st.session_state.mappings
            if m.get("project") == project
            and m.get("folder") == folder
        ]

        mapping_names = [m.get("name", "") for m in candidates]
        mapping_name = st.selectbox("Mapping", mapping_names)

        selected = next(
            m for m in candidates if m.get("name") == mapping_name
        )

        if st.button(
            "Load Data Flow",
            type="primary",
            use_container_width=True,
        ):
            try:
                with st.spinner(
                    f"Opening {mapping_name} and capturing metadata..."
                ):
                    result = st.session_state.browser.open_mapping_from_explore(
                        selected["name"],
                        selected["id"],
                    )

                data = result.get("mapping")

                if data:
                    st.session_state.selected_mapping = selected
                    st.session_state.mapping = normalize_mapping(data)
                    st.session_state.messages = []
                    st.session_state.impact_result = None
                    st.success(f"Loaded {mapping_name}")
                else:
                    st.error(
                        "Mapping metadata was not captured. "
                        "Keep Informatica open and try again."
                    )
            except Exception as exc:
                st.error(f"Mapping load failed: {exc}")


# ============================================================
# MAIN CONTENT
# ============================================================

if st.session_state.mapping:
    mapping = st.session_state.mapping
    source = primary_source(mapping)
    target = primary_target(mapping)
    validation = validate_mapping(mapping)

    # --------------------------------------------------------
    # MAPPING HEADER
    # --------------------------------------------------------

    st.success(
        f"Data flow loaded: **{human_title(mapping)}**"
    )

    st.caption(
        f"Informatica mapping: `{mapping.get('name', 'Unknown')}`"
        + (
            f"  •  Project: `{st.session_state.selected_mapping.get('project', '')}`"
            if st.session_state.selected_mapping
            else ""
        )
        + (
            f"  •  Folder: `{st.session_state.selected_mapping.get('folder', '')}`"
            if st.session_state.selected_mapping
            else ""
        )
    )

    # IMPORTANT: keep the application focused on the mapping itself.
    # The separate Understand tab has been removed; its useful content now
    # lives in Data Flow and Logic Applied.
    tabs = st.tabs(
        [
            "🔄 Data Flow",
            "⚙️ Logic Applied",
            "🔗 Impact Analysis",
            "🐞 Debugger",
            "🚨 Error Handling",
            "💬 Ask AI",
            "📄 Documentation",
            "⚙️ Technical Details",
        ]
    )

    # --------------------------------------------------------
    # 1. DATA FLOW
    # --------------------------------------------------------

    with tabs[0]:
        st.header("Data Flow")
        st.caption(
            "A complete, human-readable view of how data moves through every transformation before reaching the target."
        )

        processing_steps = [
            t for t in mapping.get("transformations", [])
            if t.get("type") not in {"Source", "Target"}
        ]

        # Build the actual pipeline from Informatica links.
        ordered_names = []
        if mapping.get("links"):
            adjacency = {}
            incoming = {}
            for link in mapping["links"]:
                a, b = link.get("from"), link.get("to")
                if a and b:
                    adjacency.setdefault(a, []).append(b)
                    incoming.setdefault(b, []).append(a)

            starts = [
                t.get("name") for t in mapping.get("transformations", [])
                if t.get("name") and not incoming.get(t.get("name"))
            ]
            queue_names = list(starts)
            visited = set()
            while queue_names:
                current = queue_names.pop(0)
                if current in visited:
                    continue
                visited.add(current)
                if current:
                    ordered_names.append(current)
                queue_names.extend(adjacency.get(current, []))

        for step in processing_steps:
            name = step.get("name")
            if name and name not in ordered_names:
                ordered_names.append(name)

        step_by_name = {t.get("name"): t for t in processing_steps if t.get("name")}

        # ----------------------------------------------------
        # BUSINESS SUMMARY
        # ----------------------------------------------------
        st.subheader("What this mapping does")
        st.info(business_summary(mapping))

        st.markdown("### Execution order")
        st.caption(
            "The mapping is shown as a compact flow. Each box is one step in the order captured from Informatica."
        )

        if ordered_names:
            flow_labels = [
                (object_label(source, "SOURCE"), "SOURCE")
            ]
            flow_labels.extend(
                (name, step_by_name.get(name, {}).get("type", "Transformation"))
                for name in ordered_names
            )
            flow_labels.append(
                (object_label(target, "TARGET"), "TARGET")
            )

            type_colors = {
                "SOURCE": ("#163b5c", "#5bb8ff"),
                "TARGET": ("#174a35", "#55e89a"),
                "Source Qualifier": ("#203c66", "#7db8ff"),
                "Lookup": ("#3f315f", "#c8a7ff"),
                "Expression": ("#573d22", "#ffca7a"),
                "Router": ("#49345b", "#e0a7ff"),
                "Joiner": ("#214d4a", "#74e0d5"),
                "Filter": ("#4d4a1d", "#e5df72"),
                "Sequence Generator": ("#304d3b", "#9de3b0"),
                "Sorter": ("#263e52", "#8fcfff"),
                "Rank": ("#543b26", "#ffc477"),
                "Aggregator": ("#3c3155", "#c6a7ff"),
                "Update Strategy": ("#4c3030", "#ff9d9d"),
            }

            cards = []
            for idx, (label, step_type) in enumerate(flow_labels, start=1):
                bg, accent = type_colors.get(step_type, ("#20242d", "#aeb7c5"))
                card = f"""<div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">
                    <div style="width:205px;min-height:70px;background:{bg};border:1px solid {accent};border-radius:12px;padding:10px 12px;box-sizing:border-box;">
                        <div style="font-size:11px;color:{accent};font-weight:700;text-transform:uppercase;letter-spacing:.5px;">Step {idx} · {escape(str(step_type))}</div>
                        <div style="font-size:15px;color:#f4f6f8;font-weight:700;margin-top:6px;line-height:1.25;word-break:break-word;">{escape(str(label))}</div>
                    </div>"""
                if idx < len(flow_labels):
                    card += '<div style="font-size:22px;color:#aeb7c5;">→</div>'
                card += '</div>'
                cards.append(card)

            st.markdown(
                '<div style="display:flex;flex-wrap:wrap;align-items:center;gap:2px 4px;padding:8px 0 2px;">'
                + ''.join(cards)
                + '</div>',
                unsafe_allow_html=True,
            )
        else:
            st.info("The complete transformation order was not captured.")

        st.divider()
        st.markdown("### Complete transformation journey")
        st.caption(
            "Every transformation captured from Informatica is shown below. "
            "Open a step to understand what it receives, what it does, which fields it uses, and the exact configuration."
        )

        # Source
        if source:
            with st.container(border=True):
                st.markdown(f"### 📥 START — {object_label(source, 'Source')}")
                st.write("This is where the mapping begins.")
                if source.get("fields"):
                    names = [f.get("name") or f.get("nativeName") for f in source.get("fields", [])]
                    st.caption("Captured source fields")
                    st.write(", ".join(f"`{x}`" for x in names if x))

        if ordered_names:
            for index, step_name in enumerate(ordered_names, start=1):
                step = step_by_name.get(step_name)
                if not step:
                    continue

                step_type = step.get("type") or "Transformation"
                logic_items = transformation_all_logic(step)
                upstream = transformation_upstream(mapping, step_name)
                source_matches = sources_for_transformation(mapping, step)

                icon_by_type = {
                    "Source Qualifier": "📥", "Lookup": "🔎", "Expression": "🧮",
                    "Router": "🔀", "Joiner": "🔗", "Filter": "🔍",
                    "Sequence Generator": "🔢", "Sorter": "↕️", "Rank": "🏆",
                    "Aggregator": "📊", "Update Strategy": "✏️",
                }
                icon = icon_by_type.get(step_type, "⚙️")

                st.markdown('<div style="text-align:center;font-size:28px;line-height:1.0">↓</div>', unsafe_allow_html=True)
                with st.container(border=True):
                    st.markdown(f"### {index}. {icon} {step_name}")
                    st.caption(f"Informatica type: **{step_type}**")

                    if upstream:
                        st.markdown("**Receives data from:** " + " → ".join(f"`{x}`" for x in upstream))
                    else:
                        st.markdown("**Receives data from:** Start of captured flow")

                    st.markdown("**What happens here:**")
                    purpose = transformation_explanation(step)
                    st.write(purpose[0] if purpose else "Processes incoming data using captured Informatica configuration.")

                    if source_matches:
                        st.markdown("**Fields referenced from captured source data:**")
                        for match in source_matches:
                            st.write(
                                f"`{match['name']}` → " + ", ".join(f"`{x}`" for x in match["fields"])
                            )

                    if logic_items:
                        st.markdown("**Configured logic / condition:**")
                        for logic in logic_items:
                            st.code(logic, language="text")
                    else:
                        st.info("No specific expression or condition was exposed in the captured metadata.")

        # Target
        st.markdown('<div style="text-align:center;font-size:28px;line-height:1.0">↓</div>', unsafe_allow_html=True)
        if target:
            with st.container(border=True):
                st.markdown(f"### 📤 END — {object_label(target, 'Target')}")
                st.write("This is where the final processed data is loaded.")
                if target.get("fields"):
                    names = [f.get("name") or f.get("nativeName") for f in target.get("fields", [])]
                    st.caption("Captured target fields")
                    st.write(", ".join(f"`{x}`" for x in names if x))

        st.divider()
        st.subheader("What does the complete flow mean?")
        if ordered_names:
            flow_text = " → ".join(
                [object_label(source, "Source")] + ordered_names + [object_label(target, "Target")]
            )
            st.code(flow_text, language="text")
        else:
            st.info("The complete transformation chain was not captured.")

        st.subheader("Exact captured links")
        if mapping.get("links"):
            for link in mapping["links"]:
                st.write(
                    f"`{link.get('from', '')}` → `{link.get('to', '')}`"
                )
        else:
            st.info("No links were captured.")

    # 2. LOGIC APPLIED
    # --------------------------------------------------------

    with tabs[1]:
        st.header("⚙️ Logic Applied")
        st.caption(
            "All processing logic in the order it is applied — filters, lookups, expressions, joins, routing, ranking, aggregation, and other transformation logic are shown together."
        )

        # Use the same execution order as Data Flow.
        logic_steps = [
            t for t in mapping.get("transformations", [])
            if t.get("type") not in {"Source", "Target"}
        ]

        ordered_names = []
        adjacency = {}
        incoming = {}
        for link in mapping.get("links", []):
            a, b = link.get("from"), link.get("to")
            if a and b:
                adjacency.setdefault(a, []).append(b)
                incoming.setdefault(b, []).append(a)

        starts = [
            t.get("name") for t in mapping.get("transformations", [])
            if t.get("name") and not incoming.get(t.get("name"))
        ]
        queue_names = list(starts)
        visited = set()
        while queue_names:
            current = queue_names.pop(0)
            if current in visited:
                continue
            visited.add(current)
            if current:
                ordered_names.append(current)
            queue_names.extend(adjacency.get(current, []))

        for step in logic_steps:
            name = step.get("name")
            if name and name not in ordered_names:
                ordered_names.append(name)

        step_by_name = {t.get("name"): t for t in logic_steps if t.get("name")}

        st.subheader("Complete logic applied")
        st.write(
            "This is the single place to understand what the mapping does. "
            "A filter condition is shown together with the transformation where it is applied, "
            "rather than being separated into a different 'business rules' section."
        )

        if ordered_names:
            for index, step_name in enumerate(ordered_names, start=1):
                step = step_by_name.get(step_name)
                if not step:
                    continue

                step_type = step.get("type") or "Transformation"
                logic_items = transformation_all_logic(step)
                upstream = transformation_upstream(mapping, step_name)

                icon_by_type = {
                    "Source Qualifier": "📥", "Lookup": "🔎", "Expression": "🧮",
                    "Router": "🔀", "Joiner": "🔗", "Filter": "🔍",
                    "Sequence Generator": "🔢", "Sorter": "↕️", "Rank": "🏆",
                    "Aggregator": "📊", "Update Strategy": "✏️",
                }
                icon = icon_by_type.get(step_type, "⚙️")

                with st.expander(
                    f"{index}. {icon} {step_name} — {step_type}",
                    expanded=False,
                ):
                    if upstream:
                        st.markdown(
                            "**Receives data from:** "
                            + " → ".join(f"`{x}`" for x in upstream)
                        )
                    else:
                        st.markdown("**Receives data from:** Start of captured flow")

                    purpose = transformation_explanation(step)
                    st.markdown("**What happens here:**")
                    st.write(
                        purpose[0]
                        if purpose
                        else "Processes incoming data using the captured Informatica configuration."
                    )

                    if logic_items:
                        st.markdown("**Logic applied:**")
                        for logic in logic_items:
                            st.code(logic, language="text")
                    else:
                        st.info(
                            "No specific expression or condition was exposed in the captured metadata."
                        )
        else:
            st.info("No transformation logic was captured.")

        st.divider()
        st.subheader("Field movement")
        if mapping.get("field_mappings"):
            movement_rows = []
            for item in mapping["field_mappings"]:
                location = mapping_location_for_field(mapping, item)
                provenance = find_field_provenance(
                    mapping,
                    item.get("source_field", ""),
                    location["target_transformation"],
                )
                origin = (
                    ", ".join(f"{owner}.{field}" for owner, field in provenance[:3])
                    if provenance
                    else "Needs review — upstream lineage not fully captured"
                )
                movement_rows.append({
                    "Source field": item.get("source_field", "") or "Unresolved",
                    "Origin": origin,
                    "Applied at": location["target_transformation"],
                    "Target field": item.get("target_field", "") or "Unresolved",
                })
            st.dataframe(movement_rows, use_container_width=True, hide_index=True)
        else:
            st.info("No field-level mappings were captured.")

    # --------------------------------------------------------
    # 3. IMPACT ANALYSIS
    # --------------------------------------------------------

    with tabs[2]:
        st.header("🔗 Impact Analysis")
        st.caption(
            "Simulate a proposed addition or deletion and see which downstream parts of the mapping could be affected. "
            "This analysis does not change the real Informatica mapping."
        )

        change_type = st.radio(
            "What change do you want to analyze?",
            ["Add", "Delete"],
            horizontal=True,
            key="impact_change_type",
        )

        transformation_names = [
            t.get("name") for t in mapping.get("transformations", [])
            if t.get("name") and t.get("type") not in {"Source", "Target"}
        ]

        if change_type == "Add":
            add_type = st.selectbox(
                "What are you adding?",
                [
                    "Filter", "Expression", "Lookup", "Joiner", "Router",
                    "Sequence Generator", "Sorter", "Rank", "Aggregator", "Source Qualifier",
                ],
                key="impact_add_type",
            )

            insertion_options = [
                object_label(primary_source(mapping) or {}, "Source")
            ] + transformation_names
            insert_after = st.selectbox(
                "Add it after",
                insertion_options,
                key="impact_insert_after",
            )

            configuration = st.text_area(
                "Optional configuration / condition",
                placeholder="Example: TO_DECIMAL(orderValue) > 1000",
                key="impact_add_configuration",
            )

            analyze = st.button(
                "🔍 Analyze Add Impact",
                type="primary",
                use_container_width=True,
                key="analyze_add_impact",
            )

            if analyze:
                result = analyze_change_impact(
                    mapping,
                    "Add",
                    selected_name="",
                    new_type=add_type,
                    insert_after=insert_after,
                    configuration=configuration,
                )
                st.session_state.impact_result = result

        else:
            if transformation_names:
                selected_delete = st.selectbox(
                    "Transformation to delete",
                    transformation_names,
                    key="impact_delete_transformation",
                )

                analyze = st.button(
                    "🔍 Analyze Delete Impact",
                    type="primary",
                    use_container_width=True,
                    key="analyze_delete_impact",
                )

                if analyze:
                    result = analyze_change_impact(
                        mapping,
                        "Delete",
                        selected_name=selected_delete,
                    )
                    st.session_state.impact_result = result
            else:
                st.info("No processing transformations were captured, so deletion impact cannot be analyzed.")

        result = st.session_state.get("impact_result")
        if result:
            st.divider()
            severity = result.get("severity", "MEDIUM")
            severity_icon = {"HIGH": "🔴", "MEDIUM": "🟠", "LOW": "🟢"}.get(severity, "ℹ️")
            st.subheader(f"{severity_icon} Potential impact: {severity}")

            if result.get("change") == "ADD":
                st.markdown(
                    f"**Proposed change:** Add **{result.get('item')}** after **{result.get('anchor')}**."
                )
                if result.get("configuration"):
                    st.markdown(f"**Proposed configuration:** `{result['configuration']}`")
                st.markdown(f"**Position:** {result.get('proposed_position', '')}")
                affected = result.get("affected", [])
                if affected:
                    st.markdown("### Downstream components that may be affected")
                    for index, name in enumerate(affected, 1):
                        st.write(f"{index}. `{name}`")
                else:
                    st.info("No downstream transformation was captured from the proposed insertion point.")
                if result.get("target_reached"):
                    st.warning(
                        "The affected downstream path reaches the target. The proposed addition can therefore change the final target result."
                    )
                else:
                    st.info("The captured downstream path does not reach the target, or target linkage was not exposed.")
            else:
                st.markdown(f"**Proposed deletion:** **{result.get('item')}**")
                affected = result.get("affected", [])
                if affected:
                    st.markdown("### Downstream components that may be affected")
                    for index, name in enumerate(affected, 1):
                        st.write(f"{index}. `{name}`")
                else:
                    st.info("No downstream transformation was captured after the selected step.")
                upstream = result.get("upstream", [])
                if upstream:
                    st.markdown("### Upstream dependencies")
                    st.write(" → ".join(f"`{x}`" for x in reversed(upstream)))
                if result.get("target_reached"):
                    st.error(
                        "The downstream path reaches the target. Deleting this transformation can change or break the final target result and should be reviewed before implementation."
                    )
                else:
                    st.info("No confirmed target dependency was exposed in the captured graph.")

            st.markdown("### What this means")
            if severity == "HIGH":
                st.write(
                    "The proposed change is on a path that reaches the target. Review downstream field dependencies and transformation logic before making the change in Informatica."
                )
            elif severity == "MEDIUM":
                st.write(
                    "The proposed change has downstream consequences in the captured graph. Review the affected transformations before implementing it."
                )
            else:
                st.write(
                    "The captured metadata shows limited downstream impact. This does not prove that there is no business impact if additional dependencies are outside the captured metadata."
                )

            st.caption(
                "Impact Analysis is a design-time simulation. It does not modify Informatica and does not claim a runtime failure."
            )

    # --------------------------------------------------------
    # 4. DEBUGGER
    # --------------------------------------------------------

    with tabs[3]:
        st.header("🐞 Debugger")
        st.caption(
            "A complete readable representation of the mapping logic. It is not executable code. "
            "Every captured transformation is included so you can trace the mapping from source to target. "
            "No runtime error is claimed unless Informatica execution logs are available."
        )

        source_name = object_label(source or {}, "SOURCE")
        target_name = object_label(target or {}, "TARGET")

        processing_steps = [
            t for t in mapping.get("transformations", [])
            if t.get("type") not in {"Source", "Target"}
        ]

        # Reconstruct the same linked order used by Data Flow.
        ordered_names = []
        adjacency = {}
        incoming = {}
        for link in mapping.get("links", []):
            a, b = link.get("from"), link.get("to")
            if a and b:
                adjacency.setdefault(a, []).append(b)
                incoming.setdefault(b, []).append(a)
        starts = [
            t.get("name") for t in mapping.get("transformations", [])
            if t.get("name") and not incoming.get(t.get("name"))
        ]
        queue_names = list(starts)
        visited = set()
        while queue_names:
            current = queue_names.pop(0)
            if current in visited:
                continue
            visited.add(current)
            if current:
                ordered_names.append(current)
            queue_names.extend(adjacency.get(current, []))
        for step in processing_steps:
            if step.get("name") and step.get("name") not in ordered_names:
                ordered_names.append(step.get("name"))

        step_by_name = {t.get("name"): t for t in processing_steps if t.get("name")}

        pseudo_lines = [
            f"# Mapping: {mapping.get('name', 'Unknown')}",
            "",
            f"SOURCE = {source_name}",
            "",
        ]

        for index, step_name in enumerate(ordered_names, start=1):
            step = step_by_name.get(step_name)
            if not step:
                continue
            step_type = step.get("type") or "Transformation"
            upstream = transformation_upstream(mapping, step_name)
            logic_items = transformation_all_logic(step)

            pseudo_lines.append(f"# STEP {index}: {step_name} ({step_type})")
            if upstream:
                pseudo_lines.append(f"input = {', '.join(upstream)}")
            else:
                pseudo_lines.append("input = START_OF_FLOW")

            for logic in logic_items:
                pseudo_lines.append(f"# configured: {logic}")

            # Show field mappings belonging to this transformation.
            step_maps = [
                item for item in mapping.get("field_mappings", [])
                if item.get("target_transformation") == step_name
            ]
            for item in step_maps:
                pseudo_lines.append(
                    f"{step_name}.{item.get('target_field') or 'UNRESOLVED'} "
                    f"<- {item.get('source_field') or 'UNRESOLVED'}"
                )
            pseudo_lines.append("")

        pseudo_lines.append(f"TARGET = {target_name}")

        st.code("\n".join(pseudo_lines), language="python")

        st.subheader("Step-by-step execution")
        if ordered_names:
            for index, step_name in enumerate(ordered_names, start=1):
                step = step_by_name.get(step_name)
                if not step:
                    continue
                step_type = step.get("type") or "Transformation"
                upstream = transformation_upstream(mapping, step_name)
                logic_items = transformation_all_logic(step)
                with st.expander(f"{index}. {step_name} — {step_type}", expanded=False):
                    if upstream:
                        st.markdown("**Input:** " + " → ".join(f"`{x}`" for x in upstream))
                    else:
                        st.markdown("**Input:** Start of flow")
                    st.markdown("**What it does:**")
                    purpose = transformation_explanation(step)
                    st.write(purpose[0] if purpose else "Processes incoming data using captured Informatica configuration.")
                    if logic_items:
                        st.markdown("**Exact configured logic:**")
                        for logic in logic_items:
                            st.code(logic, language="text")
                    step_maps = [
                        item for item in mapping.get("field_mappings", [])
                        if item.get("target_transformation") == step_name
                    ]
                    if step_maps:
                        st.markdown("**Field movement at this step:**")
                        for item in step_maps:
                            st.write(
                                f"`{item.get('source_field') or 'UNRESOLVED'}` → "
                                f"`{item.get('target_field') or 'UNRESOLVED'}`"
                            )

                    st.markdown("**🛠 Troubleshooting guide:**")
                    st.success("🟢 No confirmed runtime error for this step is available from the captured metadata.")
                    for tip in troubleshooting_guidance(step):
                        st.write(f"• {tip}")
        else:
            st.info("No transformation sequence was captured.")

        with st.expander("Raw link/debug information"):
            st.json(mapping.get("links", []))

    # 5. ERROR HANDLING
    # --------------------------------------------------------

    with tabs[4]:
        st.header("🚨 Error Handling")

        st.caption(
            "This tab tells you what the app can prove from captured metadata, what needs review, and exactly where to look. It does NOT replace Informatica's own mapping validation or runtime logs."
        )

        if validation["errors"] == 0:
            st.success(
                "🟢 No confirmed mapping errors were found in the captured metadata."
            )
            st.caption(
                "If Informatica shows the mapping as Valid, this tab will not create a separate error just because some lineage details could not be captured."
            )
        else:
            st.error(
                f"🔴 {validation['errors']} confirmed mapping error(s) found."
            )

            st.subheader("Confirmed errors")
            for check in validation["checks"]:
                if check.get("status") != "ERROR":
                    continue

                with st.container(border=True):
                    st.markdown(f"### 🔴 {check['title']}")
                    st.write(check["detail"])

                    if check.get("location"):
                        st.markdown(
                            f"**📍 Where this comes from:** `{check['location']}`"
                        )

                    if check.get("suggestion"):
                        st.markdown(
                            f"**🔎 What to check:** {check['suggestion']}"
                        )

            st.info(
                "These are confirmed structural findings from the captured mapping metadata. "
                "Runtime failures still require Informatica execution/session logs."
            )

    # --------------------------------------------------------
    # 6. ASK AI
    # --------------------------------------------------------

    with tabs[5]:
        st.header("💬 Ask about this data flow")

        st.caption(
            "Ask normal mapping questions or use this as an emergency troubleshooting assistant. "
            "Groq reasons over the captured mapping metadata and can suggest what to check and how to fix a likely configuration issue. "
            "It never treats a suspected problem as a confirmed runtime error without execution logs."
        )

        suggestion_cols = st.columns(3)

        suggested_questions = [
            "Where does the data come from?",
            "What rules are applied?",
            "Show me the field mappings.",
            "What happens to records that do not match?",
            "Explain this data flow simply.",
            "What could cause this mapping to be invalid?",
            "🚨 Emergency: this transformation is failing. What should I check?",
            "How can I fix this expression or filter?",
            "Why are records not reaching the target?",
        ]

        for index, suggestion in enumerate(suggested_questions):
            with suggestion_cols[index % 3]:
                if st.button(
                    suggestion,
                    key=f"suggested_question_{index}",
                    use_container_width=True,
                ):
                    st.session_state.pending_question = suggestion

        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

        pending = st.session_state.pop("pending_question", None)

        question = st.chat_input(
            "Ask anything about this data flow..."
        )

        if pending:
            question = pending

        if question:
            previous_messages = list(st.session_state.messages)

            st.session_state.messages.append(
                {"role": "user", "content": question}
            )

            with st.chat_message("user"):
                st.markdown(question)

            with st.chat_message("assistant"):
                local_answer = answer_from_metadata(mapping, question)

                if local_answer is not None:
                    answer = local_answer
                else:
                    try:
                        answer = ask_groq(
                            mapping,
                            question,
                            history=previous_messages,
                        )
                    except Exception as exc:
                        answer = f"Groq error: {exc}"

                st.markdown(answer)

            st.session_state.messages.append(
                {"role": "assistant", "content": answer}
            )

    # --------------------------------------------------------
    # 7. DOCUMENTATION
    # --------------------------------------------------------

    with tabs[6]:
        st.header("📄 Documentation")
        st.write("Create one of two focused documents. Each document has different content and a compact execution-order flow diagram.")

        documentation_type = st.radio(
            "Choose documentation",
            ["Functional Specification", "Technical Specification"],
            horizontal=True,
        )

        if documentation_type == "Functional Specification":
            st.caption("For business users, BAs and stakeholders: what the process does, why it exists, what happens at each stage and the final outcome.")
        else:
            st.caption("For developers and support teams: exact transformation types, expressions, lookup/join conditions, field mappings and connection details.")

        if st.button("📄 Generate PDF", type="primary", use_container_width=True):
            try:
                with st.spinner(f"Preparing {documentation_type}..."):
                    path = generate_pdf(mapping, documentation_type)
                    st.session_state.documentation_pdf = str(path)
                    st.session_state.documentation_type = documentation_type
                st.success(f"{documentation_type} generated successfully.")
            except Exception as exc:
                st.error(f"Documentation generation failed: {exc}")

        pdf_path_value = st.session_state.get("documentation_pdf")
        if pdf_path_value and Path(pdf_path_value).exists():
            pdf_path = Path(pdf_path_value)
            st.divider()
            st.download_button(
                "⬇️ Download PDF",
                data=pdf_path.read_bytes(),
                file_name=pdf_path.name,
                mime="application/pdf",
                type="primary",
                use_container_width=True,
            )

            st.divider()
            st.header("📖 Preview")
            st.caption("This preview follows the same content structure as the PDF.")
            current_doc_type = st.session_state.get("documentation_type", documentation_type)
            st.markdown(f"## {current_doc_type}")
            st.markdown(f"**Mapping:** `{mapping.get('name','')}`")

            if current_doc_type == "Functional Specification":
                st.subheader("1. Purpose and Business Outcome")
                st.info(functional_overview(mapping))
                st.subheader("2. Process Flow")
                steps = ordered_processing_steps(mapping)
                friendly = [object_label(primary_source(mapping) or {}, "Source")]
                friendly.extend({
                    "Source Qualifier": "Prepare order data",
                    "Lookup": "Find customer information",
                    "Expression": "Calculate order details",
                    "Router": "Route records",
                    "Filter": "Keep qualifying records",
                    "Joiner": "Combine related information",
                    "Sequence Generator": "Create tracking value",
                    "Sorter": "Arrange records",
                    "Rank": "Identify highest-priority orders",
                    "Aggregator": "Summarize information",
                }.get(s.get("type"), functional_step_text(s)) for s in steps)
                friendly.append(object_label(primary_target(mapping) or {}, "Target"))
                flow_html = '<div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;">'
                for i, label in enumerate(friendly, 1):
                    flow_html += f'<div style="background:#18212d;border:1px solid #59677a;border-radius:10px;padding:9px 12px;min-width:150px;text-align:center;"><b>{i}.</b> {escape(str(label))}</div>'
                    if i < len(friendly): flow_html += '<div style="font-size:20px;">→</div>'
                flow_html += '</div>'
                st.markdown(flow_html, unsafe_allow_html=True)
                st.subheader("3. What Happens at Each Stage")
                for i, step in enumerate(steps, 1):
                    with st.expander(f"{i}. {functional_step_text(step)}", expanded=False):
                        st.write(f"This stage is responsible for: {functional_step_text(step)}")
                st.subheader("4. Logic Applied")
                filters = [format_filter(x) for x in mapping.get("filters", []) if format_filter(x)]
                if filters:
                    for f in filters:
                        if "TO_DECIMAL(orderValue) > 0" in f:
                            st.write("• Only orders with a positive order value continue.")
                        elif "RANKINDEX" in f and "<= 3" in f:
                            st.write("• Only the top three ranked records continue.")
                        else:
                            st.write(f"• Records continue only when the configured condition is satisfied: {f}")
                else:
                    st.info("No selection condition was captured.")
                st.subheader("5. Final Result")
                st.success(f"The processed information is delivered to **{object_label(primary_target(mapping) or {}, 'the destination')}**.")
            else:
                st.subheader("1. Technical Overview")
                st.info(technical_overview(mapping))
                st.subheader("2. Execution Order")
                tech_steps = ordered_processing_steps(mapping)
                labels = [object_label(primary_source(mapping) or {}, "Source")] + [s.get("name", "Step") for s in tech_steps] + [object_label(primary_target(mapping) or {}, "Target")]
                flow_html = '<div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;">'
                for i, label in enumerate(labels, 1):
                    flow_html += f'<div style="background:#18212d;border:1px solid #59677a;border-radius:10px;padding:9px 12px;min-width:150px;text-align:center;"><b>{i}.</b> {escape(str(label))}</div>'
                    if i < len(labels): flow_html += '<div style="font-size:20px;">→</div>'
                flow_html += '</div>'
                st.markdown(flow_html, unsafe_allow_html=True)
                st.subheader("3. Source and Target")
                st.table({
                    "Role": ["Source", "Target"],
                    "Object": [object_label(primary_source(mapping) or {}, "Source"), object_label(primary_target(mapping) or {}, "Target")],
                    "Connection": [connection_display(primary_source(mapping) or {}), connection_display(primary_target(mapping) or {})],
                    "Database / Schema": [database_display(primary_source(mapping) or {}), database_display(primary_target(mapping) or {})],
                })
                st.subheader("4. Logic Applied by Transformation")
                for i, step in enumerate(tech_steps, 1):
                    with st.expander(f"{i}. {step.get('name','')} — {step.get('type','Transformation')}", expanded=False):
                        for logic in transformation_all_logic(step):
                            st.code(logic, language="text")
                st.subheader("5. Lookup and Join Conditions")
                relation_steps = [s for s in tech_steps if s.get("type") in {"Lookup", "Joiner"}]
                if relation_steps:
                    for step in relation_steps:
                        st.markdown(f"**{step.get('name','')}**")
                        for logic in transformation_all_logic(step):
                            st.code(logic, language="text")
                else:
                    st.info("No lookup or join transformation was captured.")
                st.subheader("6. Field Mappings")
                if mapping.get("field_mappings"):
                    rows=[]
                    for item in mapping["field_mappings"]:
                        loc=mapping_location_for_field(mapping,item)
                        rows.append({"Source field":item.get("source_field", "Unresolved"),"Applied at":loc.get("target_transformation","Unresolved"),"Target field":item.get("target_field", "Unresolved")})
                    st.dataframe(rows,use_container_width=True,hide_index=True)
                else:
                    st.info("No field mappings were captured.")

    # --------------------------------------------------------
    # 8. TECHNICAL DETAILS
    # --------------------------------------------------------

    with tabs[7]:
        st.header("⚙️ Technical Details")

        st.caption(
            "Developer-oriented information. The business views above "
            "are intentionally kept free of raw Informatica JSON."
        )

        with st.expander("Normalized Metadata"):
            st.json(
                {
                    k: v
                    for k, v in mapping.items()
                    if k != "raw"
                }
            )

        with st.expander("Raw Informatica Metadata"):
            st.json(mapping.get("raw", {}))

else:
    st.info(
        "Start by clicking **Open / Connect**, complete Informatica login "
        "in the browser, then click **Discover Projects / Folders / Mappings**."
    )

    st.markdown(
        """
        ### What this version is designed to do

        1. Discover Informatica Projects → Folders → Mappings.
        2. Load a selected mapping.
        3. Explain the mapping in business-friendly language.
        4. Show exactly where each field comes from and where it goes.
        5. Show the source/connection and target/connection when captured.
        6. Validate the mapping and explain structural errors.
        7. Provide a complete developer Debugger and troubleshooting view.
        8. Simulate Add/Delete change impact without modifying Informatica.
        9. Let users ask normal questions or get emergency AI troubleshooting help.
        10. Generate functional or technical PDF documentation.
        """
    )
