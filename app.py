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
