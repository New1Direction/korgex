"""
KorgKode Multimodal Vision — Screenshot & UI analysis.

Integrates with browser automation for visual testing and screenshot capture.
"""

import os
import json
import base64
import tempfile
from pathlib import Path
from typing import Optional

# Vision backends
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False


class VisionEngine:
    """Multimodal vision for analyzing screenshots, UIs, and images."""
    
    @staticmethod
    def analyze_image(image_path: str, question: str = None) -> dict:
        """Analyze an image using the configured vision backend.
        
        Falls back to:
        1. Direct file read (for LLM vision support)
        2. Base64 encoding
        """
        path = Path(image_path)
        if not path.exists():
            return {"error": f"Image not found: {image_path}"}
        
        with open(path, "rb") as f:
            data = f.read()
        
        b64 = base64.b64encode(data).decode()
        ext = path.suffix.lower()
        
        return {
            "image": b64,
            "format": ext.replace(".", ""),
            "size": len(data),
            "analysis_prompt": question or "Describe what you see in this image.",
            "path": str(path),
        }
    
    @staticmethod
    def take_screenshot(url: str, output_path: str = None) -> dict:
        """Take a browser screenshot of a URL using headless Chrome."""
        if not SELENIUM_AVAILABLE:
            return {"error": "Selenium not installed. Run: pip install selenium"}
        
        if output_path is None:
            output_path = tempfile.mktemp(suffix=".png")
        
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
        
        try:
            driver = webdriver.Chrome(options=options)
            driver.get(url)
            driver.save_screenshot(output_path)
            driver.quit()
            
            return {
                "screenshot_path": output_path,
                "url": url,
                "status": "success",
            }
        except Exception as e:
            return {"error": str(e), "url": url}
    
    @staticmethod
    def analyze_local_file(filepath: str) -> dict:
        """Read and describe a local image/video file."""
        path = Path(filepath)
        if not path.exists():
            return {"error": f"File not found: {filepath}"}
        
        ext = path.suffix.lower()
        size = path.stat().st_size
        
        if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            return VisionEngine.analyze_image(filepath)
        elif ext in (".webm", ".mp4", ".mov"):
            return {
                "filepath": filepath,
                "format": "video",
                "size": size,
                "message": "Video file detected. Use a vision-capable model to analyze frames.",
            }
        else:
            return {"error": f"Unsupported media format: {ext}"}


def init_browser_automation():
    """Initialize browser automation for frontend testing."""
    if SELENIUM_AVAILABLE:
        return True
    return False