import json
import os
from typing import Dict, Any
from PIL import Image
from clawvision.vision.detector import YOLOUIDetector

class AnchorGroup:
    """A collection of bounding boxes matched by an anchor rule."""
    def __init__(self, elements):
        self.elements = elements
        if elements:
            self.min_x = min(e.x for e in elements)
            self.min_y = min(e.y for e in elements)
            self.max_x = max(e.x + e.width for e in elements)
            self.max_y = max(e.y + e.height for e in elements)
            self.exists = True
        else:
            self.min_x = self.min_y = self.max_x = self.max_y = 0
            self.exists = False

class RegionProxy:
    """A proxy object exposing the bounds of an already computed region."""
    def __init__(self, bounds):
        self.left = bounds['left']
        self.top = bounds['top']
        self.right = bounds['right']
        self.bottom = bounds['bottom']

class JSONLayoutPrior:
    """
    Dynamically maps the UI into logical semantic Regions of Interest (ROIs).
    Loads extraction rules from a declarative JSON schema to evaluate boundaries.
    """
    
    def __init__(self, schema_path: str, detector=None):
        if not os.path.exists(schema_path):
            raise FileNotFoundError(f"Layout schema not found: {schema_path}")
            
        with open(schema_path, "r", encoding="utf-8") as f:
            self.config = json.load(f)
            
        self.detector = detector or YOLOUIDetector()
        
    def extract_regions(self, img: Image.Image) -> Dict[str, Image.Image]:
        w, h = img.size
        # YOLO elements detect UI
        elements = self.detector.detect(img)
        
        # 1. Resolve Anchors from Schema
        anchor_groups = {}
        for anchor_name, condition in self.config.get("anchors", {}).items():
            filtered = []
            for e in elements:
                # Provide a safe local context for evaluating the condition
                context = {"e": e, "w": w, "h": h}
                try:
                    if eval(condition, {}, context):
                        filtered.append(e)
                except Exception:
                    pass
            anchor_groups[anchor_name] = AnchorGroup(filtered)
            
        # 2. Compute Regions iteratively
        region_defs = self.config.get("regions", {})
        crops = {}
        computed_regions = {}
        
        for region_name, borders in region_defs.items():
            # Build context with image dims, anchors, and previously computed regions
            ctx = {"w": w, "h": h}
            ctx.update(anchor_groups)
            ctx.update({k: RegionProxy(v) for k, v in computed_regions.items()})
            
            try:
                left = eval(str(borders["left"]), {}, ctx)
                top = eval(str(borders["top"]), {}, ctx)
                right = eval(str(borders["right"]), {}, ctx)
                bottom = eval(str(borders["bottom"]), {}, ctx)
                
                computed_regions[region_name] = {
                    "left": left, "top": top, "right": right, "bottom": bottom
                }
                
                # Ensure box is within image bounds
                x1 = max(0, min(w, left))
                y1 = max(0, min(h, top))
                x2 = max(0, min(w, right))
                y2 = max(0, min(h, bottom))
                
                if x2 > x1 and y2 > y1:
                    crops[region_name] = img.crop((int(x1), int(y1), int(x2), int(y2)))
            except Exception as ex:
                print(f"Error computing region '{region_name}': {ex}")
                
        # Fallback raw image if everything failed
        if not crops:
            crops["full"] = img
            
        return crops
